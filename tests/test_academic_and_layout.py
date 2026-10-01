import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "skills" / "vensim-skill" / "scripts"
sys.path.insert(0, str(TOOLS))

from academic_gate import check_model  # noqa: E402
from vensim_autolayout import update_arrow_line  # noqa: E402


def _model(stock_rhs: str, coupling: bool = True) -> str:
    text = f"""
INITIAL TIME = 0
    ~ Year
    |
FINAL TIME = 2
    ~ Year
    |
TIME STEP = 1
    ~ Year
    |
SAVEPER = TIME STEP
    ~ Year
    |
初值 = 10
    ~ Unit
    |
存量 = INTEG( {stock_rhs}, 初值 )
    ~ Unit
    |
流量 = 1
    ~ Unit/Year
    |
U1 = 存量 / 10
    ~ Dmnl
    |
U2 = 1
    ~ Dmnl
    |
C = 2 * SQRT( U1 * U2 ) / MAX( U1 + U2, 0.000001 )
    ~ Dmnl
    |
T = 0.5 * U1 + 0.5 * U2
    ~ Dmnl
    |
D = SQRT( C * T )
    ~ Dmnl
    |
"""
    if not coupling:
        text = text.replace("U1 = 存量 / 10\n    ~ Dmnl\n    |\n", "")
    return text


def test_academic_gate_accepts_endogenous_coupling_model(tmp_path):
    path = tmp_path / "pass.mdl"
    path.write_text(_model("流量"), encoding="utf-8")
    report = check_model(path, None, None, True, ["U1", "C", "T", "D"])
    assert report["pass"] is True
    assert report["stock_count"] == 1


def test_academic_gate_does_not_reject_history_named_endogenous_stock(tmp_path):
    path = tmp_path / "history_named_stock.mdl"
    path.write_text(
        _model("Observed Stock - 存量") + "Observed Stock = 10\n    ~ Unit\n    |\n",
        encoding="utf-8",
    )
    report = check_model(path, None, None, False, [], strict_endogenous=True)
    assert report["pass"] is True


def test_academic_gate_rejects_external_data_in_stock_flow(tmp_path):
    path = tmp_path / "replay.mdl"
    path.write_text(_model("GET DATA('observations', 'Observed Stock') - 存量"), encoding="utf-8")
    report = check_model(path, None, None, False, [], strict_endogenous=True)
    assert report["pass"] is False
    assert any("历史路径注入" in item for item in report["errors"])


def test_academic_gate_does_not_bind_general_model_to_one_case(tmp_path):
    path = tmp_path / "general.mdl"
    path.write_text(
        _model("Observed Demand - 存量") + "Observed Demand = 10\n    ~ Unit/Year\n    |\n",
        encoding="utf-8",
    )
    report = check_model(path, None, None, False, [])
    assert report["pass"]
    assert not any("历史路径注入" in warning for warning in report["warnings"])
    report = check_model(path, None, None, True, [])
    assert not report["pass"]
    assert any("明确本项目" in error for error in report["errors"])


def test_academic_gate_requires_coupling_outputs(tmp_path):
    path = tmp_path / "missing_coupling.mdl"
    path.write_text(_model("流量", coupling=False), encoding="utf-8")
    report = check_model(path, None, None, True, ["U1", "U2", "C", "T", "D"])
    assert report["pass"] is False
    assert any("缺少所选输出" in item for item in report["errors"])


def test_layout_route_preserves_polarity_and_enables_arrow_color():
    line = "1,9,1,2,0,0,43,2,1,64,0,31-41-55,,,1|(0,0)|\n"
    output = update_arrow_line(line, (80, 40), {"style": "monochrome"})
    fields = output.split("|", 1)[0].rstrip("\n").split(",")
    assert fields[4:9] == ["1", "0", "43", "2", "1"]
    assert fields[11] == "0-0-0"
    assert fields[-1] == "1"
    assert "|(80,40)|" in output


def test_academic_gate_rejects_bare_numeric_unit_anchor(tmp_path):
    path = tmp_path / "bare_anchor.mdl"
    text = _model("流量").replace(
        "D = SQRT( C * T )\n    ~ Dmnl\n    |\n",
        "发送TEU = 2\n    ~ TEU10k/Year\n    |\n"
        "发送TEU标准化指数 = MIN(1, MAX(0, (发送TEU - 1.0) / (34.0 - 1.0)))\n"
        "    ~ Dmnl\n    |\n"
        "D = SQRT( C * T )\n    ~ Dmnl\n    |\n",
    )
    path.write_text(text, encoding="utf-8")
    report = check_model(path, None, None, True, ["U1", "U2", "C", "T", "D"])
    assert report["pass"] is False
    assert any("裸数字边界" in item for item in report["errors"])


def test_academic_gate_treats_policy_time_switch_as_boundary_input(tmp_path):
    path = tmp_path / "scenario.mdl"
    text = _model("流量") + (
        "基础设施投入情景 = IF THEN ELSE( Time < 1, 1, 1.15 )\n    ~ Dmnl\n    |\n"
    )
    path.write_text(text, encoding="utf-8")
    report = check_model(path, None, None, True, ["U1", "C", "T", "D"])
    assert report["pass"] is True
    assert not any("按 TIME 分段切换" in warning for warning in report["warnings"])
    assert report["boundary_time_switches"] == ["基础设施投入情景"]


def test_academic_gate_time_detection_ignores_variable_name_and_catches_nested_time(tmp_path):
    path = tmp_path / "time_detection.mdl"
    path.write_text(
        _model("流量")
        + (
            "adjustment time = 2\n    ~ Year\n    |\n"
            "模型值 = IF THEN ELSE(MAX(存量, 0) > 0 :AND: Time <= 1, 3, 5)\n"
            "    ~ Unit/Year\n    |\n"
        ),
        encoding="utf-8",
    )
    report = check_model(path, None, None, False, [])
    assert report["boundary_time_switches"] == ["模型值"]
    assert not any("adjustment time" in warning for warning in report["warnings"])


def test_academic_gate_does_not_accept_model_file_as_reference(tmp_path):
    path = tmp_path / "model.mdl"
    path.write_text(_model("流量"), encoding="utf-8")
    report = check_model(path, path, None, False, [])
    assert report["pass"] is False
    assert any("没有可读文献文件" in error for error in report["errors"])


def test_derived_output_check_uses_project_names_and_dependencies(tmp_path):
    path = tmp_path / "different_domain.mdl"
    path.write_text(
        "病房需求=病人数/床位数~Dmnl~|\n病人数=70~Person~|\n床位数=100~Person~|\n", encoding="utf-8"
    )
    assert check_model(path, None, None, True, ["病房需求"])["pass"]
    report = check_model(path, None, None, True, ["病房需求", "床位数"])
    assert not report["pass"]
    assert any("床位数" in error and "计算依赖" in error for error in report["errors"])
