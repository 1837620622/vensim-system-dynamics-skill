import csv
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "skills/vensim-skill/scripts"
sys.path.insert(0, str(TOOLS))

from optimization import observation_indices, run_search, statistic, validate_spec  # noqa: E402
from simulation_runner import run_model  # noqa: E402


@pytest.fixture
def model(tmp_path):
    target = tmp_path / "独立领域.mdl"
    target.write_text("累计量=INTEG(投放速度, 初始量)~Litre~|\n投放速度=1~Litre/Hour~|\n初始量=2~Litre~|\n"
                      "INITIAL TIME=0~Hour~|\nFINAL TIME=4~Hour~|\nTIME STEP=0.25~Hour~|\nSAVEPER=1~Hour~|\n", encoding="utf-8")
    return target


def calibration_spec():
    return {"parameters": {"投放速度": [0.1, 6]}, "seed": 41, "max_evaluations": 180,
            "time_unit": "Hour", "targets": [{"variable": "累计量", "unit": "Litre", "weight": 1, "scale": 1}]}


def policy_spec():
    return {"parameters": {"投放速度": [0, 10]}, "seed": 29, "max_evaluations": 160,
            "objectives": [{"variable": "累计量", "statistic": "final", "direction": "maximize", "weight": 1, "scale": 1}],
            "constraints": [{"variable": "累计量", "statistic": "max", "upper": 12}]}


def test_calibration_recovers_known_parameter_and_is_reproducible(model, tmp_path):
    pytest.importorskip("scipy")
    observed = tmp_path / "真实列格式.csv"
    observed.write_text("Time,累计量\n0,2\n1,5.25\n2,8.5\n3,11.75\n4,15\n", encoding="utf-8-sig")
    source = model.read_bytes()
    reports = [run_search(model, calibration_spec(), tmp_path / name, "calibrate", observed) for name in ("fit-a", "fit-b")]
    first, second = reports
    assert first["best"]["parameters"]["投放速度"] == pytest.approx(3.25, abs=1e-4)
    assert first["best"]["score"] < first["baseline"]["score"] * 1e-8
    assert first["best"]["parameters"] == second["best"]["parameters"]
    assert first["evaluation_count"] <= 180
    assert not first["global_optimum_proven"] and not first["native_verified"]
    assert first["observations"]["source_csv_sha256"] == hashlib.sha256(observed.read_bytes()).hexdigest()
    assert model.read_bytes() == source
    with (tmp_path / "fit-a/comparison.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert {row["Scenario"] for row in csv.DictReader(stream)} == {"baseline", "best", "observed"}
    assert (tmp_path / "fit-a/evaluations.csv").read_bytes() == (tmp_path / "fit-b/evaluations.csv").read_bytes()


def test_policy_honors_constraint_and_records_budget_exhaustion(model, tmp_path):
    pytest.importorskip("scipy")
    report = run_search(model, policy_spec(), tmp_path / "policy", "optimize")
    assert report["best"]["parameters"]["投放速度"] == pytest.approx(2.5, abs=0.02)
    assert report["best"]["feasible"]
    with (tmp_path / "policy/best.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert max(float(row["累计量"]) for row in csv.DictReader(stream)) <= 12
    assert report["best"]["score"] < report["baseline"]["score"]
    short = {**policy_spec(), "max_evaluations": 10}
    limited = run_search(model, short, tmp_path / "limited", "optimize")
    assert limited["evaluation_count"] == 10
    assert limited["termination"] == "budget_exhausted"
    assert not limited["solver_success"]


def test_infeasible_policy_retains_trace_without_inventing_best(model, tmp_path):
    pytest.importorskip("scipy")
    spec = {**policy_spec(), "max_evaluations": 20, "constraints": [{"variable": "累计量", "statistic": "initial", "lower": 3}]}
    report = run_search(model, spec, tmp_path / "infeasible", "optimize")
    assert not report["feasible_solution_found"]
    assert report["best"] is None
    assert not (tmp_path / "infeasible/best.csv").exists()
    assert (tmp_path / "infeasible/evaluations.csv").is_file()


@pytest.mark.parametrize("change, message", [
    ({"parameters": {"投放速度": [False, 6]}}, "有限"),
    ({"seed": True}, "seed"),
    ({"max_evaluations": 0}, "max_evaluations"),
    ({"targets": [{"variable": "累计量", "unit": "Kilogram", "weight": 1, "scale": 1}]}, "单位"),
    ({"parameters": {"累计量": [0, 1]}}, "常量"),
    ({"targets": [{"variable": [], "unit": "Litre", "weight": 1, "scale": 1}]}, "目标"),
    ({"unrecognized": 2}, "未知"),
])
def test_invalid_search_spec_fails_early(model, change, message):
    with pytest.raises(ValueError, match=message):
        validate_spec({**calibration_spec(), **change}, model, "calibrate")


def test_search_cannot_optimize_experiment_time(model):
    model.write_text(model.read_text(encoding="utf-8").replace("FINAL TIME=4", "FINAL TIME=投放速度"), encoding="utf-8")
    with pytest.raises(ValueError, match="影响时间控制"):
        validate_spec(policy_spec(), model, "optimize")


def test_invalid_model_time_is_reported_before_search(model, tmp_path):
    pytest.importorskip("scipy")
    model.write_text(model.read_text(encoding="utf-8").replace("TIME STEP=0.25", "TIME STEP=0"), encoding="utf-8")
    with pytest.raises(ValueError, match="时间设置"):
        run_search(model, policy_spec(), tmp_path / "bad-time", "optimize")
    assert not (tmp_path / "bad-time").exists()


def test_observations_must_match_output_grid(model, tmp_path):
    pytest.importorskip("scipy")
    data = tmp_path / "off-grid.csv"
    data.write_text("Time,累计量\n0,2\n0.3,4\n", encoding="utf-8")
    with pytest.raises(ValueError, match="保存网格"):
        run_search(model, calibration_spec(), tmp_path / "invalid", "calibrate", data)
    assert not (tmp_path / "invalid").exists()
    assert observation_indices([0, 0.1, 0.2], [0.1 + 1e-16]) == [1]


def test_statistics_use_saved_times_and_require_final_value(model):
    result = run_model(model, ["累计量"])
    assert statistic(result, "累计量", "mean") == 4
    assert statistic(result, "累计量", "integral") == 16
    result.metadata["final_time"] = 4.25
    with pytest.raises(ValueError, match="最终时刻"):
        statistic(result, "累计量", "final")


def test_search_refuses_existing_files_and_resource_overrun(model, tmp_path):
    pytest.importorskip("scipy")
    output = tmp_path / "protected"
    output.mkdir()
    existing = output / "evaluations.csv"
    existing.write_bytes(b"keep original")
    with pytest.raises(ValueError, match="已存在"):
        run_search(model, policy_spec(), output, "optimize")
    assert list(output.iterdir()) == [existing]
    assert existing.read_bytes() == b"keep original"
    with pytest.raises(ValueError, match="work_limit"):
        run_search(model, {**policy_spec(), "work_limit": 1}, tmp_path / "overrun", "optimize")
    assert not (tmp_path / "overrun").exists()


def test_optimize_cli_and_pysd_backend(model, tmp_path):
    pytest.importorskip("scipy")
    pytest.importorskip("pysd")
    spec = tmp_path / "policy.json"
    spec.write_text(json.dumps({**policy_spec(), "max_evaluations": 10}), encoding="utf-8")
    result = subprocess.run([sys.executable, str(TOOLS / "skill_cli.py"), "optimize", str(model), "--spec", str(spec),
                             "--output-dir", str(tmp_path / "pysd-search"), "--backend", "pysd"],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["backend"] == "pysd" and report["evaluation_count"] == 10
    assert not model.with_suffix(".py").exists()


def test_mcp_search_tools_execute_real_models(model, tmp_path):
    pytest.importorskip("scipy")
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    fit = tmp_path / "fit.json"
    fit.write_text(json.dumps({**calibration_spec(), "max_evaluations": 10}), encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({**policy_spec(), "max_evaluations": 10}), encoding="utf-8")
    observed = tmp_path / "observed.csv"
    observed.write_text("Time,累计量\n0,2\n1,3\n2,4\n", encoding="utf-8")

    async def exercise():
        params = StdioServerParameters(command=sys.executable,
            args=[str(TOOLS / "skill_cli.py"), "mcp", "--workspace", str(tmp_path)], env=dict(os.environ))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                fitted = await session.call_tool("calibrate_model", {"model": model.name, "spec": fit.name,
                    "data": observed.name, "output_directory": "mcp-fit"})
                assert not fitted.isError, fitted
                optimized = await session.call_tool("optimize_policy", {"model": model.name, "spec": policy.name,
                    "output_directory": "mcp-policy"})
                assert not optimized.isError, optimized
        for directory in ("mcp-fit", "mcp-policy"):
            report = json.loads((tmp_path / directory / "optimization.json").read_text(encoding="utf-8"))
            assert report["evaluation_count"] <= 10
            assert report["feasible_solution_found"]

    asyncio.run(exercise())
