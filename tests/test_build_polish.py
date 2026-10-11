"""新建模型的收尾质量：未使用变量、回路标记、极性、视图归位与示例模板。"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/vensim-skill/scripts"))

from feedback_audit import audit_feedback, numeric_link_signs  # noqa: E402
from model_builder import _model_text, _prepare_model, build_model  # noqa: E402
from natural_layout import (  # noqa: E402
    extract_flows,
    is_loop_marker,
    loop_marker_cycles,
    loop_marker_shape,
    stock_half_size,
)
from sketch_geometry import measure_view, quality_pass, wrap_label  # noqa: E402
from vensim_autolayout import load_mdl, parse_views  # noqa: E402
from vensim_engine import (  # noqa: E402
    canonical_name,
    command_check,
    command_fix,
    parse_equations,
    use_flags,
)

ASSETS = ROOT / "skills/vensim-skill/assets"
MARKER = "\\\\\\---///"


def spec(*extra, loops=None, **options):
    """一个库存、一条流入、一个反馈参数的最小模型，extra 追加变量。"""
    variables = [
        {"name": "库存", "kind": "stock", "initial": "100", "unit": "件"},
        {
            "name": "补货",
            "kind": "flow",
            "from": None,
            "to": "库存",
            "equation": "MAX(0, (目标库存 - 库存) / 调整时间)",
            "unit": "件/Month",
        },
        {"name": "目标库存", "kind": "constant", "equation": "200", "unit": "件"},
        {"name": "调整时间", "kind": "constant", "equation": "4", "unit": "Month"},
        *extra,
    ]
    return {
        "name": "测试",
        "time": {"initial": 0, "final": 12, "step": 0.25, "saveper": 1, "unit": "Month"},
        "variables": variables,
        "feedback_loops": loops or [],
        **({"sketch": options} if options else {}),
    }


# ---------------------------------------------------------------------------
# 未使用变量：与 Vensim Check Model 的 USE FLAG 同口径
# ---------------------------------------------------------------------------


def test_parse_equations_reads_the_supplementary_flag():
    text = (
        "{UTF-8}\n甲 = 1\n\t~ 件\n\t~\n\t~\t:SUP\n\t|\n"
        "乙 = 甲 * 2\n\t~ 件\n\t~ 说明\n\t~\t:SUPPLEMENTARY\n\t|\n"
        "丙 = 甲\n\t~ 件\n\t~ 注释\n\t|\n"
    )
    equations = parse_equations(text, expand=False)
    assert [eq.supplementary for eq in equations.values()] == [True, True, False]
    assert equations["乙"].comment == "说明"
    # 乙已标 SUP 且没人用，不报；丙没标且没人用，报；甲标了 SUP 却被引用，报
    assert use_flags(equations) == (["丙"], ["甲"])


def test_build_marks_unused_outputs_as_supplementary_and_check_is_clean(tmp_path):
    text = build_model(
        spec({"name": "缺口", "kind": "aux", "equation": "目标库存 - 库存", "unit": "件"})
    )
    assert "缺口 = 目标库存 - 库存\n\t~ 件\n\t~\n\t~\t:SUPPLEMENTARY\n" in text
    assert use_flags(parse_equations(text, expand=False)) == ([], [])
    path = tmp_path / "m.mdl"
    path.write_text(text, encoding="utf-8")
    assert command_check(path, strict=True) == 0


def test_build_rejects_an_unused_constant():
    extra = {"name": "电价", "kind": "constant", "equation": "0.6", "unit": "元/度"}
    with pytest.raises(ValueError, match="电价.*没有被任何方程使用"):
        build_model(spec(extra))


def test_build_rejects_supplementary_on_a_used_variable():
    extra = {"name": "缺口", "kind": "aux", "equation": "目标库存 - 库存", "unit": "件"}
    used = {**extra, "supplementary": True}
    follower = {"name": "观察", "kind": "aux", "equation": "缺口 * 2", "unit": "件"}
    with pytest.raises(ValueError, match="不能标 supplementary"):
        build_model(spec(used, follower))


def test_check_strict_turns_warnings_into_failure():
    path = ROOT / "skills/vensim-skill/assets/reference_models/sir_aggregate.mdl"
    assert command_check(path) == 0
    assert command_check(path, strict=True) == 1


def test_fix_marks_outputs_but_never_hides_unused_constants(tmp_path):
    source = ROOT / "skills/vensim-skill/assets/reference_models/student_flows.mdl"
    assert use_flags(parse_equations(source.read_text(encoding="utf-8"), expand=False))[0]
    fixed = tmp_path / "fixed.mdl"
    assert command_fix(source, fixed, mark_supplementary=True) == 0
    equations = parse_equations(fixed.read_text(encoding="utf-8"), expand=False)
    assert use_flags(equations) == ([], [])
    assert any(eq.supplementary for eq in equations.values())
    legacy = (
        "{UTF-8}\n库存 = INTEG(入, 0)\n\t~ 件\n\t~\n\t|\n"
        "入 = 参数A\n\t~ 件/Year\n\t~\n\t|\n参数A = 2\n\t~ 件/Year\n\t~\n\t|\n"
        "遗留 = 5\n\t~ 件\n\t~\n\t|\n指标 = 库存 * 2\n\t~ 件\n\t~\n\t|\n"
        "INITIAL TIME = 0\n\t~ Year\n\t~\n\t|\nFINAL TIME = 3\n\t~ Year\n\t~\n\t|\n"
        "TIME STEP = 1\n\t~ Year\n\t~\n\t|\nSAVEPER = 1\n\t~ Year\n\t~\n\t|\n"
    )
    constant_model = tmp_path / "constant.mdl"
    constant_model.write_text(legacy, encoding="utf-8")
    again = tmp_path / "again.mdl"
    command_fix(constant_model, again, mark_supplementary=True)
    equations = parse_equations(again.read_text(encoding="utf-8"), expand=False)
    assert equations["指标"].supplementary and not equations["遗留"].supplementary
    assert use_flags(equations) == (["遗留"], [])


# ---------------------------------------------------------------------------
# 极性、回路与视图
# ---------------------------------------------------------------------------


COUPLED = [
    {"name": "效率", "kind": "aux", "equation": "1 + 库存 / 100", "unit": "Dmnl"},
    {"name": "投入", "kind": "aux", "equation": "2 + 库存 / 50", "unit": "Dmnl"},
    {
        "name": "产出",
        "kind": "flow",
        "from": "库存",
        "to": None,
        "equation": "效率 * 投入",
        "unit": "件/Month",
    },
]


def test_numeric_signs_cover_products_of_two_variables():
    text, _ = _model_text(spec(*COUPLED))
    signs = numeric_link_signs(text)
    assert signs[(canonical_name("效率"), canonical_name("产出"))] == "+"
    assert signs[(canonical_name("投入"), canonical_name("产出"))] == "+"


def test_loop_through_a_product_is_checked_numerically_and_gets_a_marker():
    loop = {"name": "产出消耗", "variables": ["库存", "效率", "产出"], "polarity": "B"}
    text, notes = _model_text(spec(*COUPLED, loops=[loop]))
    audit = audit_feedback(text, [loop])
    assert [row["status"] for row in audit["loops"]] == ["numeric"]
    assert [row["label"] for row in notes["loop_markers"]] == ["B1"]


def test_default_polarity_marks_dynamic_links_and_leaves_parameter_links_bare():
    text, _ = _model_text(spec(*COUPLED))
    rows = {(r["source"], r["target"]): r for r in audit_feedback(text)["links"]}
    assert rows[("库存", "效率")]["displayed"] == "+"
    assert rows[("目标库存", "补货")]["displayed"] == ""
    text_all, _ = _model_text(spec(*COUPLED, polarity="all"))
    rows = {(r["source"], r["target"]): r for r in audit_feedback(text_all)["links"]}
    assert rows[("目标库存", "补货")]["displayed"] == "+"


def test_constants_move_to_the_only_view_that_uses_them():
    variables = [
        {"name": "甲存量", "kind": "stock", "initial": "1", "unit": "件", "view": "甲"},
        {
            "name": "甲流入",
            "kind": "flow",
            "from": None,
            "to": "甲存量",
            "equation": "甲系数",
            "unit": "件/Year",
            "view": "甲",
        },
        {
            "name": "甲系数",
            "kind": "aux",
            "equation": "乙参数 * 2",
            "unit": "件/Year",
            "view": "甲",
        },
        {"name": "乙存量", "kind": "stock", "initial": "1", "unit": "件", "view": "乙"},
        {
            "name": "乙流入",
            "kind": "flow",
            "from": None,
            "to": "乙存量",
            "equation": "乙存量 / 10",
            "unit": "件/Year",
            "view": "乙",
        },
        {"name": "乙参数", "kind": "constant", "equation": "3", "unit": "件/Year", "view": "乙"},
    ]
    model = {
        "name": "两视图",
        "time": {"initial": 0, "final": 5, "step": 1, "saveper": 1, "unit": "Year"},
        "variables": variables,
    }
    text, notes = _model_text(model)
    assert notes["moved_constants"] == [{"variable": "乙参数", "from": "乙", "to": "甲"}]
    views = {view.name: view for view in parse_views(text.splitlines(True), {"甲存量", "乙存量"})}
    defined = {
        name: sorted(o.name for o in view.objects.values() if o.kind == 10 and not o.is_shadow)
        for name, view in views.items()
    }
    assert "乙参数" in defined["甲"] and "乙参数" not in defined["乙"]


def test_undeclared_loops_are_suggested_as_ready_to_paste_entries():
    text, notes = _model_text(spec(*COUPLED))
    del text
    suggested = notes["suggested_loops"]
    assert suggested
    assert {"库存", "补货"} in [set(row["variables"]) for row in suggested]
    assert all(row["polarity"] in {"R", "B"} for row in suggested)
    declared = _model_text(spec(*COUPLED, loops=[{"variables": ["库存", "补货"], "polarity": "B"}]))
    assert declared[1]["suggested_loops"] == []


# ---------------------------------------------------------------------------
# 存量框与回路标记形状
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "one_row"),
    [("充电桩数量", True), ("新能源汽车保有量", True), ("中国居民人均可支配收入水平", False)],
)
def test_chinese_stock_names_fit_one_row_up_to_ten_characters(name, one_row):
    half, _ = stock_half_size(name)
    rows = wrap_label(name, half - 8)
    assert len(rows) <= 2
    assert (len(rows) == 1) is one_row
    if not one_row:
        assert abs(len(rows[0]) - len(rows[1])) <= 1


@pytest.mark.parametrize("name", ["sir_aggregate", "overshoot", "health_model", "sir_policy"])
def test_marker_shape_matches_the_loop_direction_in_authors_models(name):
    """作者画的回路标记，圈的方向都与回路在屏幕上的走向一致。"""
    _, views = load_mdl(ASSETS / f"reference_models/{name}.mdl")
    checked = 0
    for view in views:
        markers = [oid for oid, obj in view.objects.items() if is_loop_marker(obj)]
        cycles = loop_marker_cycles(view, dict(view.objects), extract_flows(view), markers)
        for marker, cycle in cycles.items():
            assert loop_marker_shape(view, cycle) == view.objects[marker].shape & 31
            checked += 1
    assert checked


# ---------------------------------------------------------------------------
# 示例模板：方程与随附示例一致，几何合格，标记在回路里，check 无警告
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["inventory_zh", "water_feedback_zh", "ev_adoption_zh"])
def test_templates_build_clean_and_match_the_shipped_examples(name, tmp_path):
    template = json.loads((ASSETS / f"templates/{name}.json").read_text(encoding="utf-8"))
    text, report = _prepare_model(template)
    example = (ASSETS / f"examples/{name}.mdl").read_text(encoding="utf-8")
    assert text.split(MARKER, 1)[0] == example.split(MARKER, 1)[0]
    assert report["pass"]
    assert report["sketch_decisions"]["loop_markers"]
    assert all(row["inside"] for row in report["sketch_decisions"]["marker_check"])
    path = tmp_path / f"{name}.mdl"
    path.write_text(text, encoding="utf-8")
    assert command_check(path, strict=True) == 0
    stocks = {item["name"] for item in template["variables"] if item["kind"] == "stock"}
    views = parse_views(text.splitlines(True), stocks)
    assert all(quality_pass(measure_view(view)) for view in views)
