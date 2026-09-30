"""官方函数边界与 PySD 交叉核对；测试数值不参与产品默认参数。"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/vensim-skill/scripts"))

import experiments  # noqa: E402
from experiments import execute_experiment  # noqa: E402
from model_builder import build_model  # noqa: E402
from result_plotting import read_result_csv, validate_results  # noqa: E402
from simulation_runner import run_model  # noqa: E402
from vensim_engine import command_simulate, parse_equations  # noqa: E402

CONTROLS = "INITIAL TIME=0~Hour~|FINAL TIME=4~Hour~|TIME STEP=1~Hour~|SAVEPER=1~Hour~|"


def model_file(tmp_path, equations):
    model = tmp_path / "model.mdl"
    model.write_text("{UTF-8}\n" + equations + CONTROLS, encoding="utf-8")
    return model


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("ZIDZ(4,2)", [2] * 5),
        ("ZIDZ(4,1e-9)", [0] * 5),
        ("XIDZ(4,-1e-9,7)", [7] * 5),
        ("XIDZ(4,1e-6,7)", [4e6] * 5),
        ("PULSE(1,0)", [0, 1, 0, 0, 0]),
        ("PULSE(0.25,1)", [1, 0, 0, 0, 0]),
        ("PULSE(0.5,1)", [0, 0, 0, 0, 0]),
        ("STEP(2,0.25)", [2] * 5),
        ("STEP(2,0.5)", [0, 2, 2, 2, 2]),
        ("STEP(-2,1)", [0, -2, -2, -2, -2]),
        ("STEP(2,-1)", [2] * 5),
        ("MODULO(-5,3)", [-2] * 5),
        ("MODULO(-7.5,2)", [-1.5] * 5),
        ("MODULO(7.5,2)", [1.5] * 5),
        ("RAMP(2,1,3)", [0, 0, 2, 4, 4]),
        ("RAMP(2,1,0)", [0, 0, -2, -2, -2]),
        ("IF THEN ELSE(Time=1 :OR: Time<>2 :AND: :NOT: Time>3,2,0)", [2, 2, 0, 2, 0]),
    ],
)
def test_official_scalar_function_boundaries(tmp_path, expression, expected):
    model = model_file(tmp_path, f"Output={expression}~Dmnl~|\n")
    assert run_model(model, ["Output"]).series["Output"] == expected


@pytest.mark.parametrize("divisor", [0, -3])
def test_nonpositive_modulo_divisor_does_not_guess_native_semantics(tmp_path, divisor):
    model = model_file(tmp_path, f"Output=MODULO(-5,{divisor})~Dmnl~|\n")
    with pytest.raises(ValueError, match="非正除数"):
        run_model(model, ["Output"])


@pytest.mark.parametrize("expression", ["STEP(2,0.25)", "STEP(2,0.5)", "MODULO(-7.5,2)"])
def test_step_and_signed_modulo_match_actual_pysd(tmp_path, expression):
    pytest.importorskip("pysd")
    model = model_file(tmp_path, f"Output={expression}~Dmnl~|\n")
    expected = run_model(model, ["Output"])
    actual = run_model(model, ["Output"], backend="pysd")
    assert actual.times == expected.times
    assert actual.series["Output"] == expected.series["Output"]


def test_step_uses_effective_overridden_time_step(tmp_path):
    model = model_file(tmp_path, "Output=STEP(2,0.25)~Dmnl~|\n")
    assert run_model(model, ["Output"], time_step=0.5, saveper=0.5).series["Output"] == [
        0,
        2,
        2,
        2,
        2,
        2,
        2,
        2,
        2,
    ]


@pytest.mark.parametrize("duration", [0, 1, 1.5, 2.5])
def test_fixed_delay_frozen_state_and_step_rounding_match_pysd(tmp_path, duration):
    model = model_file(
        tmp_path,
        f"Input=Time~Hour~|\nDuration={duration}+Time~Hour~|\n"
        "Output=DELAY FIXED(Input,Duration,9)~Hour~|\n",
    )
    count = max(1, int(duration + 0.5))
    expected = [9 if step < count else step - count for step in range(5)]
    assert run_model(model, ["Output"]).series["Output"] == expected
    pytest.importorskip("pysd")
    assert run_model(model, ["Output"], backend="pysd").series["Output"] == expected


def test_fixed_delay_feedback_and_initial_value_are_stateful(tmp_path):
    model = model_file(
        tmp_path, "Input=Output+1~Dmnl~|\nOutput=DELAY FIXED(Input,1,5+Time)~Dmnl~|\n"
    )
    result = run_model(model, ["Output"])
    assert result.series["Output"] == [5, 6, 7, 8, 9]
    pytest.importorskip("pysd")
    assert run_model(model, ["Output"], backend="pysd").series["Output"] == result.series["Output"]


def test_delay_fixed_restrictions_and_bad_inputs_are_checked(tmp_path):
    with pytest.raises(ValueError, match="独立"):
        parse_equations("Output=1+DELAY FIXED(2,1,0)~Dmnl~|")
    model = model_file(tmp_path, "Output=DELAY FIXED(Missing,1,0)~Dmnl~|\n")
    with pytest.raises(ValueError, match="固定延迟"):
        run_model(model, ["Output"], final_time=0)
    with pytest.raises(ValueError, match="参数数量"):
        run_model(model_file(tmp_path, "Output=ZIDZ(1,2,0)~Dmnl~|\n"), ["Output"])


def test_native_names_quoted_names_and_lookup_scaling(tmp_path):
    model = model_file(
        tmp_path,
        'Total_Cost=5~Dmnl~|\n"投入/产出"=2~Dmnl~|\n'
        "Curve([(0,-100)-(100,100)],(0,3e0),(1,7e0))~Dmnl~|\n"
        'Output=Curve(0.5)+total cost+"投入/产出"+TIME~Dmnl~|\n',
    )
    assert run_model(model, ["Output"]).series["Output"] == [12, 13, 14, 15, 16]
    assert "Curve" not in run_model(model).series
    with pytest.raises(ValueError, match="Lookup"):
        run_model(model, ["Curve"])
    with pytest.raises(ValueError, match="重复定义"):
        parse_equations("Total_Cost=1~Dmnl~|total cost=2~Dmnl~|")


@pytest.mark.parametrize("table", ["((1,2),(0,3))", "((0,2),(0,3))", "((0,2),(1,1e309))"])
def test_invalid_lookup_points_are_not_silently_repaired(tmp_path, table):
    with pytest.raises(ValueError, match="LOOKUP"):
        run_model(model_file(tmp_path, f"Output=WITH LOOKUP(Time,{table})~Dmnl~|\n"), ["Output"])


def test_diagnostic_csv_import_cannot_erase_failure_status(tmp_path):
    model = model_file(tmp_path, "Output=Missing~Dmnl~|\n")
    csv = tmp_path / "diagnostic.csv"
    command_simulate(model, csv, ["Output"], strict=False)
    with pytest.raises(ValueError, match="诊断"):
        read_result_csv(csv)


def test_run_csv_hash_units_and_legacy_provenance(tmp_path):
    model = model_file(tmp_path, "Output=2~Item~|\n")
    csv = tmp_path / "result.csv"
    command_simulate(model, csv, ["Output"])
    results, variables = read_result_csv(csv)
    metadata = results[0][1].metadata
    assert variables == ["Output"] and metadata["time_unit"] == "Hour"
    assert metadata["provenance_verified"] and metadata["variable_units"] == {"Output": "Item"}
    with pytest.raises(ValueError, match="时间单位"):
        read_result_csv(csv, time_unit="Year")
    csv.write_bytes(csv.read_bytes().replace(b",2.0", b",3.0"))
    with pytest.raises(ValueError, match="哈希不一致"):
        read_result_csv(csv)
    csv.with_suffix(".csv.run.json").unlink()
    assert not read_result_csv(csv)[0][0][1].metadata["provenance_verified"]


def test_experiment_csv_keeps_sampling_metadata_and_hash(tmp_path):
    model = model_file(tmp_path, "Parameter=2~Item~|\nOutput=Parameter~Item~|\n")
    spec = {
        "mode": "monte-carlo",
        "parameters": {"Parameter": [1, 3]},
        "seed": 41,
        "samples": 3,
        "variables": ["Output"],
    }
    folder = tmp_path / "experiment"
    execute_experiment(model, spec, folder)
    csv = folder / "series.csv"
    metadata = json.loads((folder / "experiment.json").read_text())
    assert metadata["model_sha256"] == hashlib.sha256(model.read_bytes()).hexdigest()
    assert metadata["result_files"]["series.csv"] == hashlib.sha256(csv.read_bytes()).hexdigest()
    results, _ = read_result_csv(csv)
    assert all(
        result.metadata["experiment_mode"] == "monte-carlo"
        and result.metadata["provenance_verified"]
        for _, result in results
    )


@pytest.mark.parametrize("workflow", ["experiment", "convergence"])
@pytest.mark.parametrize("changed_run", [1, 2])
def test_batch_source_changes_fail_before_publishing(tmp_path, monkeypatch, workflow, changed_run):
    model = model_file(tmp_path, "Parameter=2~Item~|\nOutput=Parameter~Item~|\n")
    original = experiments.run_model
    calls = 0

    def change_between_runs(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == changed_run:
            model.write_bytes(model.read_bytes().replace(b"Parameter=2", b"Parameter=3"))
        return result

    monkeypatch.setattr(experiments, "run_model", change_between_runs)
    output = tmp_path / "results"
    with pytest.raises(ValueError, match="源模型发生变化"):
        if workflow == "experiment":
            execute_experiment(
                model,
                {"variables": ["Output"], "scenarios": [{"name": "first"}, {"name": "second"}]},
                output,
            )
        else:
            experiments.convergence(model, ["Output"], output / "convergence.json")
    assert not output.exists()
    assert calls == changed_run


def test_experiment_checks_result_model_hash_even_when_source_is_unchanged(tmp_path, monkeypatch):
    model = model_file(tmp_path, "Output=2~Item~|\n")
    original = experiments.run_model

    def mismatched_hash(*args, **kwargs):
        result = original(*args, **kwargs)
        result.metadata["model_sha256"] = "different-model"
        return result

    monkeypatch.setattr(experiments, "run_model", mismatched_hash)
    with pytest.raises(ValueError, match="源模型发生变化"):
        execute_experiment(
            model, {"variables": ["Output"], "scenarios": [{"name": "first"}]}, tmp_path / "out"
        )
    assert not (tmp_path / "out").exists()


def test_experiment_rechecks_source_after_rendering_before_publishing(tmp_path, monkeypatch):
    model = model_file(tmp_path, "Output=2~Item~|\n")

    def change_during_rendering(*args, **kwargs):
        model.write_bytes(model.read_bytes().replace(b"Output=2", b"Output=3"))
        return {"status": "completed"}

    monkeypatch.setattr(experiments, "plot_experiment", change_during_rendering)
    with pytest.raises(ValueError, match="源模型发生变化"):
        execute_experiment(
            model,
            {"variables": ["Output"], "scenarios": [{"name": "first"}]},
            tmp_path / "out",
            plot=tmp_path / "figure.png",
        )
    assert not (tmp_path / "out").exists()


def test_convergence_checks_identical_saved_times(tmp_path, monkeypatch):
    model = model_file(tmp_path, "Output=2~Item~|\n")
    original = experiments.run_model
    calls = 0

    def mismatched_time_grid(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            result.times[1] += 0.1
        return result

    monkeypatch.setattr(experiments, "run_model", mismatched_time_grid)
    with pytest.raises(ValueError, match="保存时刻不一致"):
        experiments.convergence(model, ["Output"], tmp_path / "convergence.json")
    assert not (tmp_path / "convergence.json").exists()


def test_variable_units_and_nonfinite_constants_are_rejected(tmp_path):
    model = model_file(tmp_path, "Output=2~Item~|\n")
    first = run_model(model, ["Output"])
    second = run_model(model, ["Output"])
    second.metadata["variable_units"]["Output"] = "Kg"
    with pytest.raises(ValueError, match="变量单位"):
        validate_results([("first", first), ("second", second)], ["Output"])
    with pytest.raises(ValueError, match="有限数"):
        run_model(model_file(tmp_path, "Output=1e309~Dmnl~|\n"), ["Output"])


@pytest.mark.parametrize("expression", ["MIN(1e309,1)", "1e309 :AND: 1", "IF THEN ELSE(1e309,1,0)"])
def test_nonfinite_inputs_cannot_be_hidden_by_finite_function_results(tmp_path, expression):
    with pytest.raises(ValueError, match="有限数"):
        run_model(model_file(tmp_path, f"Output={expression}~Dmnl~|\n"), ["Output"])


def test_implicit_state_names_use_native_equivalence():
    with pytest.raises(ValueError, match="隐式状态名"):
        parse_equations("Output=SMOOTH(2,1)~Dmnl~|output_stage1=3~Dmnl~|")


@pytest.mark.parametrize("name", ["2库存", "利率%", "A&B", "投入;产出", "_amount"])
def test_builder_rejects_names_requiring_native_quotes(name):
    spec = {
        "time": {"initial": 0, "final": 1, "step": 1, "saveper": 1, "unit": "Hour"},
        "variables": [{"name": name, "kind": "stock", "unit": "Item", "initial": 1}],
    }
    with pytest.raises(ValueError, match="业务变量名"):
        build_model(spec)


def test_nested_functions_do_not_rewrite_internal_aliases(tmp_path):
    model = model_file(
        tmp_path,
        "Input=3~Dmnl~|\n_v0=7~Dmnl~|\nvar0=11~Dmnl~|\n_sd_max=5~Dmnl~|\n"
        "Output=IF THEN ELSE(Input=3,MAX(Input,_sd_max)+INTEGER(_v0)+var0,0)~Dmnl~|\n",
    )
    assert run_model(model, ["Output"]).series["Output"] == [23] * 5


def test_official_inline_lookup_matches_pysd(tmp_path):
    model = model_file(
        tmp_path, "Output=WITH LOOKUP(Time,([(0,-100)-(10,100)],(0,3),(2,7)))+1~Dmnl~|\n"
    )
    expected = [4, 6, 8, 8, 8]
    assert run_model(model, ["Output"]).series["Output"] == expected
    pytest.importorskip("pysd")
    assert run_model(model, ["Output"], backend="pysd").series["Output"] == expected


def test_time_precision_and_explicit_empty_selection(tmp_path):
    model = model_file(tmp_path, "Output=2~Dmnl~|\n")
    with pytest.raises(ValueError, match="可导出"):
        run_model(model, [])
    model.write_text(
        "Output=2~Dmnl~|INITIAL TIME=1e20~Hour~|FINAL TIME=1.000000000000001e20~Hour~|TIME STEP=1~Hour~|SAVEPER=1~Hour~|"
    )
    with pytest.raises(ValueError, match="浮点精度"):
        run_model(model, ["Output"])


def test_neighboring_manifest_cannot_follow_link_outside_result_directory(tmp_path):
    folder = tmp_path / "results"
    folder.mkdir()
    csv = folder / "result.csv"
    csv.write_text("Time,Output\n0,1\n1,2\n")
    outside = tmp_path / "outside.json"
    outside.write_text('{"status":"completed"}')
    sidecar = csv.with_suffix(".csv.run.json")
    try:
        sidecar.symlink_to(outside)
    except OSError:
        pytest.skip("系统未允许创建符号链接")
    with pytest.raises(ValueError, match="结果目录之外"):
        read_result_csv(csv)
