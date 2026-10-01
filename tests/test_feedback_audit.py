import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/vensim-skill/scripts"))

from feedback_audit import (  # noqa: E402
    audit_feedback,
    check_loops,
    infer_polarity,
    main,
)
from model_builder import build_model, command_build  # noqa: E402
from vensim_engine import parse_equations  # noqa: E402


def equations():
    return parse_equations("输入=1~Unit~|输出=输入~Unit~|系数=2~dmnl~|偏移=3~Unit~|", expand=False)


@pytest.mark.parametrize(
    "expression, expected",
    [
        ("输入", "+"),
        ("偏移 - 输入", "-"),
        ("MAX(0, 偏移 - 输入)", "-"),
        ("MIN(输入, 偏移)/系数", "+"),
        ("-2 * 输入", "-"),
        ("输入 - 输入", "0"),
        ("输入 ^ 2", "unknown"),
        ("ABS(输入)", "unknown"),
        ("系数/输入", "unknown"),
        ("IF THEN ELSE(输入 > 0, 输入, -输入)", "unknown"),
    ],
)
def test_direct_polarity_is_conservative(expression, expected):
    assert infer_polarity(expression, "输入", equations()) == expected


@pytest.mark.parametrize("expression", ["SMOOTH(输入, 3)", "DELAY3(输入, 3)"])
def test_smoothing_and_delay_preserve_positive_input_polarity(expression):
    assert infer_polarity(expression, "输入", equations()) == "+"


def test_changed_parameter_sign_requires_rechecking():
    eqs = equations()
    assert infer_polarity("输入*系数", "输入", eqs) == "+"
    eqs["系数"].rhs = "-2"
    assert infer_polarity("输入*系数", "输入", eqs) == "-"


def test_loop_parity_uses_dynamic_equations_and_rejects_initial_only_edges():
    eqs = parse_equations(
        "存量=INTEG(流入,初值)~Unit~|流入=目标-存量~Unit/Hour~|目标=2~Unit~|初值=存量~Unit~|",
        expand=False,
    )
    loop = {"variables": ["存量", "流入"], "polarity": "B"}
    assert check_loops(eqs, [loop])[0]["status"] == "verified"
    loop["polarity"] = "R"
    assert check_loops(eqs, [loop])[0]["status"] == "conflict"
    with pytest.raises(ValueError, match="初值关系"):
        check_loops(eqs, [{"variables": ["存量", "初值"]}])
    eqs["流入"].rhs = "存量 * 存量"
    assert check_loops(eqs, [loop])[0]["status"] == "needs_review"


def test_build_rejects_wrong_sign_before_creating_outputs(tmp_path):
    spec = json.loads(
        (ROOT / "skills/vensim-skill/assets/templates/circular_feedback_zh.json").read_text()
    )
    spec["links"] = [{"from": "水量", "to": "缺水量", "polarity": "+"}]
    source, output = tmp_path / "spec.json", tmp_path / "model.mdl"
    source.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="反馈极性与方程冲突"):
        command_build(source, output)
    assert not output.exists() and not output.with_suffix(".mdl.build_report.json").exists()
    spec["links"][0]["polarity"] = "-"
    spec["feedback_loops"] = [
        {"name": "状态调整", "variables": ["水量", "缺水量", "补水需求", "补水"], "polarity": "B"}
    ]
    report = audit_feedback(build_model(spec), spec["feedback_loops"])
    assert not report["conflicts"]
    assert report["loops"][0]["expected"] == "B"
    assert any(row["source"] == "水量" and row["status"] == "verified" for row in report["links"])


def test_blank_template_and_missing_business_input_never_use_demo_data(tmp_path):
    empty = ROOT / "skills/vensim-skill/assets/templates/model_template.json"
    with pytest.raises(ValueError):
        command_build(empty, tmp_path / "empty.mdl")
    assert not list(tmp_path.iterdir())
    spec = {
        "name": "独立项目",
        "time": {},
        "variables": [{"name": "自定状态", "kind": "stock", "initial": 7, "unit": "Item"}],
    }
    with pytest.raises(ValueError, match="不能套用示例参数"):
        build_model(spec)
    spec["time"] = {"initial": 2, "final": 3, "step": 0.5, "saveper": 0.5, "unit": "Week"}
    text = build_model(spec)
    assert "自定状态 = INTEG(0, 7)" in text and "水量" not in text and "库存" not in text
    del spec["variables"][0]["initial"]
    with pytest.raises(ValueError, match="必须提供 initial"):
        build_model(spec)


def test_feedback_cli_preserves_existing_output(tmp_path, capsys):
    model = ROOT / "skills/vensim-skill/assets/examples/circular_feedback_zh.mdl"
    output = tmp_path / "review.json"
    assert main([str(model), "--output", str(output)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["symbol_placement_requires_native_review"]
    assert not report["native_verified"]
    original = output.read_bytes()
    with pytest.raises(ValueError):
        main([str(model), "--output", str(output)])
    assert output.read_bytes() == original


def test_direct_self_feedback_and_fixed_delay_initial_edges():
    eqs = parse_equations("State=INTEG(-State,1)~Item~|", expand=False)
    assert check_loops(eqs, [{"variables": ["State"], "polarity": "B"}])[0]["status"] == "verified"
    eqs = parse_equations(
        "Output=DELAY FIXED(Input,1,Initial)~Item~|Input=Output+1~Item~|Initial=Output~Item~|",
        expand=False,
    )
    assert (
        check_loops(eqs, [{"variables": ["Input", "Output"], "polarity": "R"}])[0]["status"]
        == "verified"
    )
    with pytest.raises(ValueError, match="初值关系"):
        check_loops(eqs, [{"variables": ["Initial", "Output"]}])


def test_zero_effect_is_review_not_conflict():
    eqs = parse_equations("开关=0~Dmnl~|强度=1~1/Month~|效果=开关*强度~1/Month~|", expand=False)
    # A zero derivative at the current parameter point is not evidence of a
    # wrong displayed sign; it requires a range or native review.
    assert infer_polarity("0*强度", "强度", eqs) == "0"
