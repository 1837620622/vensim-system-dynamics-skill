from pathlib import Path
import asyncio
import hashlib
import json
import math
import os
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/vensim-skill"
TOOLS = SKILL / "scripts"
sys.path.insert(0, str(TOOLS))

from experiments import convergence, execute_experiment, experiment_runs  # noqa: E402
from model_builder import build_model, command_build  # noqa: E402
from simulation_runner import run_model  # noqa: E402
from vensim_engine import get_time_bounds, parse_equations, simulate  # noqa: E402
from vensim_autolayout import load_mdl  # noqa: E402

TEMPLATE = SKILL / "assets/templates/inventory_zh.json"


@pytest.fixture
def inventory(tmp_path):
    target = tmp_path / "中文 folder" / "inventory.mdl"
    command_build(TEMPLATE, target)
    return target


def controls(final=10, dt=0.1, save=0.1):
    return f"INITIAL TIME=0~Month~|FINAL TIME={final}~Month~|TIME STEP={dt}~Month~|SAVEPER={save}~Month~|".replace("|", "|\n")


@pytest.mark.parametrize("function", ["SMOOTH", "SMOOTH3", "DELAY1", "DELAY3"])
def test_delay_initial_equilibrium(function):
    text = f"Input=7~Unit/Month~|\nOutput={function}(Input, 3)~Unit/Month~|\n" + controls()
    eq = parse_equations(text)
    result = simulate(eq, *get_time_bounds(eq))
    assert result.series["Output"] == pytest.approx([7] * len(result.times))


def test_material_delay_conserves_mass():
    text = "Input=STEP(10, 1)~Unit/Month~|\nOutput=DELAY3I(Input, 3, 0)~Unit/Month~|\n" + controls()
    eq = parse_equations(text)
    result = simulate(eq, *get_time_bounds(eq))
    stages = [name for name in result.series if "__stage" in name]
    assert len(stages) == 3
    for i in range(len(result.times) - 1):
        change = sum(result.series[name][i + 1] - result.series[name][i] for name in stages)
        expected = 0.1 * (result.series["Input"][i] - result.series["Output"][i])
        assert change == pytest.approx(expected, abs=1e-12)


def test_stock_dependency_initialization_does_not_cache_zero():
    eq = parse_equations("B=INTEG(0, Twice A)~Unit~|\nTwice A=2*A~Unit~|\nA=INTEG(0, 8)~Unit~|\n" + controls())
    result = simulate(eq, *get_time_bounds(eq))
    assert result.series["B"][0] == 16


def test_builder_has_native_causal_flow_direction_and_initial_link(inventory):
    view = load_mdl(inventory)[1][0]
    stock = next(obj for obj in view.objects.values() if obj.name == "库存")
    initial = next(obj for obj in view.objects.values() if obj.name == "初始库存")
    assert any(a.from_id == initial.obj_id and a.to_id == stock.obj_id for a in view.arrows)
    incoming = [a for a in view.arrows if a.is_physical_flow and a.to_id == stock.obj_id]
    assert sorted(a.shape for a in incoming) == [4, 100]
    assert all(view.objects[a.from_id].kind == 11 for a in incoming)
    assert all(a.fields[11] == "0-0-0" for a in view.arrows)
    report = json.loads(inventory.with_suffix(".mdl.build_report.json").read_text())
    assert not report["after"]["node_overlaps"]
    assert not report["after"]["arrow_node_collisions"]
    assert not report["after"]["arrow_crossings"]
    assert not report["after"]["flow_crossings"]


def test_overrides_are_independent_and_record_effective_time(inventory):
    raw = inventory.read_bytes()
    base = run_model(inventory, ["库存"])
    policy = run_model(inventory, ["库存"], params={"调整时间": 2})
    again = run_model(inventory, ["库存"])
    assert base.series == again.series
    assert policy.series["库存"][5] > base.series["库存"][5]
    assert base.metadata["time_step"] == 0.25
    assert base.metadata["final_time"] == 60
    assert base.metadata["model_sha256"] == hashlib.sha256(raw).hexdigest()
    assert inventory.read_bytes() == raw
    with pytest.raises(ValueError, match="常量"):
        run_model(inventory, params={"补货": 0})


def test_grid_and_monte_carlo_are_bounded_and_reproducible():
    grid = {"mode": "grid", "parameters": {"A": [1, 2], "B": [3, 4, 5]}}
    assert len(experiment_runs(grid)) == 6
    mc = {"mode": "monte-carlo", "samples": 20, "seed": 7, "parameters": {"A": [0, 1]}}
    assert experiment_runs(mc) == experiment_runs(mc)
    assert experiment_runs(mc) != experiment_runs({**mc, "seed": 8})
    with pytest.raises(ValueError, match="超过"):
        experiment_runs({"mode": "grid", "parameters": {"A": list(range(201))}})
    with pytest.raises(ValueError, match="有限"):
        experiment_runs({"mode": "grid", "parameters": {"A": [math.nan]}})


def test_experiment_assumptions_must_be_explicit():
    with pytest.raises(ValueError, match="changes"):
        experiment_runs({"mode": "perturb", "parameters": ["A"]}, {"A": 2})
    with pytest.raises(ValueError, match="samples"):
        experiment_runs({"mode": "monte-carlo", "parameters": {"A": [0, 1]}, "seed": 19})
    with pytest.raises(ValueError, match="seed"):
        experiment_runs({"mode": "monte-carlo", "parameters": {"A": [0, 1]}, "samples": 5})


def test_time_settings_are_not_inherited_from_old_example():
    eq = parse_equations("存量=INTEG(0, 7)~Person~|\n")
    with pytest.raises(ValueError, match="明确时间设置"):
        get_time_bounds(eq)


def test_builder_simulates_different_domain_without_example_values(tmp_path):
    spec = {"time": {"initial": 5, "final": 8, "step": 0.125, "saveper": 0.5, "unit": "Hour"},
            "variables": [{"name": "水体体积", "kind": "stock", "unit": "Litre", "initial": "初始体积"},
                          {"name": "注入流率", "kind": "flow", "unit": "Litre/Hour", "equation": "泵速", "from": None, "to": "水体体积"},
                          {"name": "泵速", "kind": "constant", "unit": "Litre/Hour", "equation": 3.5},
                          {"name": "初始体积", "kind": "constant", "unit": "Litre", "equation": 7}]}
    model = tmp_path / "reservoir.mdl"
    model.write_text(build_model(spec), encoding="utf-8")
    result = run_model(model, ["水体体积"])
    assert result.times == [5, 5.5, 6, 6.5, 7, 7.5, 8]
    assert result.series["水体体积"] == pytest.approx([7 + 3.5 * (t - 5) for t in result.times])
    assert result.metadata["time_unit"] == "Hour"
    assert "库存" not in model.read_text(encoding="utf-8")


def test_experiment_outputs_and_validation(inventory, tmp_path):
    spec = {"variables": ["库存"], "scenarios": [{"name": "基准"}, {"name": "政策", "params": {"需求": 30}}]}
    output = tmp_path / "results"
    result = execute_experiment(inventory, spec, output)
    assert result["run_count"] == 2
    assert (output / "series.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    assert json.loads((output / "experiment.json").read_text())["runs"][1]["parameters"] == {"需求": 30}
    with pytest.raises(ValueError, match="已存在"):
        execute_experiment(inventory, spec, output)
    with pytest.raises(ValueError, match="时间"):
        execute_experiment(inventory, {**spec, "time": {"saveper": 0}}, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_convergence_compares_same_time_grid(inventory, tmp_path):
    report = convergence(inventory, ["库存"], tmp_path / "convergence.json")
    assert report["time_steps"] == [0.25, 0.125, 0.0625]
    assert report["variables"][0]["half_step_error"] < report["variables"][0]["normalized_error"]
    assert report["pass"]


def test_invalid_model_spec_fails_before_writing(tmp_path):
    spec = json.loads(TEMPLATE.read_text())
    spec["variables"][1]["to"] = "不存在"
    with pytest.raises(ValueError, match="流向"):
        build_model(spec)
    spec = json.loads(TEMPLATE.read_text())
    spec["variables"][0]["position"] = [math.nan, 3]
    with pytest.raises(ValueError, match="position"):
        build_model(spec)
    spec = json.loads(TEMPLATE.read_text())
    del spec["time"]
    with pytest.raises(ValueError, match="明确提供"):
        build_model(spec)
    spec = json.loads(TEMPLATE.read_text())
    spec["time"]["final"] = -1
    with pytest.raises(ValueError, match="FINAL TIME"):
        build_model(spec)


def test_builtin_matches_pysd_inventory(inventory):
    pytest.importorskip("pysd")
    variables = ["库存", "补货", "销售"]
    left, right = (run_model(inventory, variables, backend=backend) for backend in ("builtin", "pysd"))
    assert left.times == pytest.approx(right.times)
    for name in variables:
        assert left.series[name] == pytest.approx(right.series[name], rel=1e-10, abs=1e-10)
    assert not inventory.with_suffix(".py").exists()


def test_mcp_protocol_and_workspace_boundary(inventory, tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def exercise():
        params = StdioServerParameters(command=sys.executable,
            args=[str(TOOLS / "skill_cli.py"), "mcp", "--workspace", str(tmp_path)],
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert len(listed.tools) == 10
                result = await session.call_tool("inspect_model", {"model": str(inventory.relative_to(tmp_path))})
                assert not result.isError
                escaped = await session.call_tool("inspect_model", {"model": "../outside.mdl"})
                assert escaped.isError
                overwrite = await session.call_tool("layout_model", {"model": str(inventory), "output": str(inventory)})
                assert overwrite.isError
                simulated = await session.call_tool("simulate_model", {"model": str(inventory), "output": "mcp.csv", "variables": ["库存"]})
                assert not simulated.isError
        assert (tmp_path / "mcp.csv").is_file()
    asyncio.run(exercise())
