import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/vensim-skill/scripts"))

from model_builder import build_model, command_build  # noqa: E402
from sketch_geometry import measure_view, quality_pass  # noqa: E402
from vensim_autolayout import (  # noqa: E402
    command_layout,
    load_mdl,
    parse_views,
    validate_config,
)


def diagram(nodes, links):
    return "\n".join(
        [
            r"\\\---/// Sketch information",
            "V300",
            "*反馈",
            "$192-192-192,0,Vensim Sans SC|12||0-0-0|0-0-0|0-0-0|-1--1--1|-1--1--1|96,96,100,0",
            *nodes,
            *links,
            "",
        ]
    )


def word(oid, name, x=120, y=120, bits=3):
    return f"10,{oid},{name},{x},{y},45,14,0,{bits},0,0,0,0,0,0"


def link(oid, source, target, thick=0):
    return f"1,{oid},{source},{target},0,0,43,{thick},1,65,0,0-0-0,,1|(0,0)|"


def water_spec():
    return {
        "time": {"initial": 0, "final": 4, "step": 0.25, "saveper": 1, "unit": "Hour"},
        "variables": [
            {"name": "水量", "kind": "stock", "initial": 10, "unit": "Litre"},
            {
                "name": "补水",
                "kind": "flow",
                "from": None,
                "to": "水量",
                "equation": "补水需求",
                "unit": "Litre/Hour",
            },
            {
                "name": "补水需求",
                "kind": "aux",
                "equation": "缺水量/调节周期",
                "unit": "Litre/Hour",
            },
            {
                "name": "缺水量",
                "kind": "aux",
                "equation": "MAX(0, 目标水量 - 水量)",
                "unit": "Litre",
            },
            {"name": "目标水量", "kind": "constant", "equation": 30, "unit": "Litre"},
            {"name": "调节周期", "kind": "constant", "equation": 2, "unit": "Hour"},
        ],
    }


def test_natural_lays_out_a_pure_feedback_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    source, target = tmp_path / "loop.mdl", tmp_path / "loop_natural.mdl"
    # 记录顺序与反馈循线顺序不同，不应直接按输入顺序把边交错在圆内。
    cycle = [1, 3, 5, 2, 4, 6]
    source.write_text(
        diagram(
            [word(i, f"变量{i}") for i in range(1, 7)],
            [
                link(10 + i, a, b)
                for i, (a, b) in enumerate(zip(cycle, cycle[1:] + cycle[:1], strict=False))
            ],
        )
    )
    command_layout(source, target, mode="natural")
    view = load_mdl(target)[1][0]
    assert quality_pass(measure_view(view))
    assert len({obj.y for obj in view.objects.values()}) > 1
    assert len({obj.x for obj in view.objects.values()}) > 2
    assert [(a.from_id, a.to_id, a.fields[6], a.fields[9]) for a in view.arrows] == [
        (a, b, "43", "65") for a, b in zip(cycle, cycle[1:] + cycle[:1], strict=False)
    ]
    again = tmp_path / "again.mdl"
    command_layout(source, again, mode="natural")
    assert again.read_bytes() == target.read_bytes()


def test_new_models_default_to_natural_and_allow_local_refinement(tmp_path):
    spec = water_spec()
    path = tmp_path / "model.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "new.mdl"
    command_build(path, output)
    report = json.loads(output.with_suffix(".mdl.build_report.json").read_text())
    assert report["requested_mode"] == report["views"][0]["strategy"] == "natural"
    assert report["pass"]
    assert output.read_text() == build_model(spec)
    spec["sketch"] = {"layout_mode": "refine"}
    assert build_model(spec) != output.read_text()


def test_large_chinese_stock_backbone_stays_compact_and_connected():
    stocks = [f"存量{i}" for i in range(1, 10)]
    variables = [{"name": name, "kind": "stock", "initial": 10, "unit": "Item"} for name in stocks]
    for index, source in enumerate(stocks):
        target = stocks[(index + 1) % len(stocks)]
        variables.append(
            {
                "name": f"流量{index + 1}",
                "kind": "flow",
                "from": source,
                "to": target,
                "equation": f"{source} * 0.1",
                "unit": "Item/Month",
            }
        )
    spec = {
        "language": "zh",
        "name": "大存量骨架",
        "time": {"initial": 0, "final": 4, "step": 0.25, "saveper": 1, "unit": "Month"},
        "variables": variables,
        "links": [],
    }
    text = build_model(spec)
    view = parse_views(text.splitlines(True), set(stocks))[0]
    stock_objects = [obj for obj in view.objects.values() if obj.stock_like]
    metrics = measure_view(view)
    # 存量环排成上下两行的矩形回路，不需要影子拆分
    assert len(stock_objects) == len(stocks)
    assert len({obj.y for obj in stock_objects}) == 2
    assert quality_pass(metrics)
    assert metrics["layout_span"] < 1800


def test_repeat_path_is_drawn_as_detour_above_main_chain():
    # 年级链 + 留级通路：一年级 → 留级 → 二年级 与 一年级 → 二年级 并存
    def flow(name, source, target, equation):
        return {
            "name": name,
            "kind": "flow",
            "from": source,
            "to": target,
            "equation": equation,
            "unit": "Student/Year",
        }

    stocks = ["一年级", "二年级", "三年级", "留级一年级"]
    variables = [{"name": s, "kind": "stock", "initial": 100, "unit": "Student"} for s in stocks]
    variables += [
        flow("入学", None, "一年级", "100"),
        flow("升入二年级", "一年级", "二年级", "一年级 * (1 - 留级比例)"),
        flow("升入三年级", "二年级", "三年级", "二年级"),
        flow("毕业", "三年级", None, "三年级"),
        flow("留级", "一年级", "留级一年级", "一年级 * 留级比例"),
        flow("重读完成", "留级一年级", "二年级", "留级一年级"),
        {"name": "留级比例", "kind": "constant", "equation": 0.1, "unit": "Dmnl"},
    ]
    spec = {
        "name": "学生流动",
        "time": {"initial": 0, "final": 4, "step": 0.25, "saveper": 1, "unit": "Year"},
        "variables": variables,
    }
    view = parse_views(build_model(spec).splitlines(True), set(stocks))[0]
    by_name = {obj.name: obj for obj in view.objects.values() if obj.kind == 10}
    main = [by_name[s] for s in ("一年级", "二年级", "三年级")]
    repeat = by_name["留级一年级"]
    assert len({obj.y for obj in main}) == 1
    assert repeat.y != main[0].y
    assert main[0].x < repeat.x < main[1].x
    assert quality_pass(measure_view(view))


@pytest.mark.parametrize("removed", ["graphviz", "circular"])
def test_removed_modes_fail_with_migration_hint(tmp_path, removed):
    """已删除的旧模式直接报错并提示改用 natural。"""
    source, target = tmp_path / "source.mdl", tmp_path / "target.mdl"
    source.write_text(diagram([word(1, "甲"), word(2, "乙", 300)], [link(3, 1, 2)]))
    with pytest.raises(ValueError, match="natural"):
        command_layout(source, target, mode=removed)
    assert not target.exists()


def test_natural_layout_has_a_finite_native_display_span(tmp_path, monkeypatch):
    """natural 不启动外部程序，长链也给出有限画布。"""
    monkeypatch.setenv("PATH", "")
    source, target = tmp_path / "source.mdl", tmp_path / "target.mdl"
    source.write_text(
        diagram(
            [word(index, f"变量{index}", index * 100, 100) for index in range(1, 9)],
            [link(20 + index, index, index + 1) for index in range(1, 8)],
        )
    )
    command_layout(source, target, mode="natural")
    after = load_mdl(target)[1][0]
    metrics = measure_view(after)
    assert quality_pass(metrics)
    assert metrics["layout_span"] < 1800


def test_build_preserves_explicit_positions_even_when_they_overlap(tmp_path):
    spec = water_spec()
    for item in spec["variables"]:
        if item["kind"] == "constant":
            item["position"] = [500, 500]
    path = tmp_path / "pinned.json"
    path.write_text(json.dumps(spec))
    output = tmp_path / "pinned.mdl"
    command_build(path, output)
    view = load_mdl(output)[1][0]
    assert all(
        (obj.x, obj.y) == (500, 500)
        for obj in view.objects.values()
        if obj.name in {"目标水量", "调节周期"}
    )
    report = json.loads(output.with_suffix(".mdl.build_report.json").read_text())
    assert not report["pass"] and report["views"][0]["after"]["node_overlaps"]


def test_anchor_is_an_obstacle_before_other_nodes_are_placed(tmp_path):
    source, target, config = (tmp_path / name for name in ("a.mdl", "b.mdl", "layout.json"))
    source.write_text(
        diagram([word(1, "锚点", 100, 100), word(2, "其他节点", 400, 300)], [link(3, 1, 2)])
    )
    config.write_text(json.dumps({"node_positions": {"锚点": [400, 300]}}))
    command_layout(source, target, config)
    view = load_mdl(target)[1][0]
    assert (view.objects[1].x, view.objects[1].y) == (400, 300)
    assert not measure_view(view)["node_overlaps"]


def test_disconnected_modules_and_shadows_do_not_merge(tmp_path):
    source, target = tmp_path / "a.mdl", tmp_path / "b.mdl"
    source.write_text(
        diagram(
            [
                word(1, "共享", bits=2),
                word(2, "共享", bits=2),
                word(3, "甲"),
                word(4, "乙"),
                word(5, "共享"),
            ],
            [link(6, 1, 3), link(7, 2, 4)],
        )
    )
    command_layout(source, target)
    view = load_mdl(target)[1][0]
    assert not measure_view(view)["node_overlaps"]
    assert [(obj.obj_id, obj.name, obj.bits) for obj in view.objects.values()] == [
        (1, "共享", 2),
        (2, "共享", 2),
        (3, "甲", 3),
        (4, "乙", 3),
        (5, "共享", 3),
    ]


def test_pipe_hitting_another_label_is_not_reported_as_pass():
    view = parse_views(
        diagram(
            [word(1, "起点", 100, 100), word(2, "终点", 500, 100), word(3, "被穿过的字", 300, 100)],
            [link(4, 1, 2, 22)],
        ).splitlines(True)
    )[0]
    metrics = measure_view(view)
    assert metrics["physical_node_collisions"] == [{"arrow": 4, "object": 3}]
    assert not quality_pass(metrics)


def test_refine_keeps_flow_assembly_and_simulation_unchanged(tmp_path):
    from mdl_document import MdlDocument
    from simulation_runner import run_model

    source, target = tmp_path / "source.mdl", tmp_path / "refined.mdl"
    spec = water_spec()
    spec["sketch"] = {"layout_mode": "preserve"}
    source.write_text(build_model(spec))
    command_layout(source, target, mode="refine")
    before, after = load_mdl(source)[1][0], load_mdl(target)[1][0]
    assert MdlDocument.read(source).equation_bytes == MdlDocument.read(target).equation_bytes
    assert [(a.fields, a.points) for a in before.arrows if a.is_physical_flow] == [
        (a.fields, a.points) for a in after.arrows if a.is_physical_flow
    ]
    for oid, obj in before.objects.items():
        if obj.stock_like or obj.kind in (11, 12) or obj.attached_to_valve:
            assert (obj.x, obj.y) == (after.objects[oid].x, after.objects[oid].y)
    assert run_model(source).series == run_model(target).series
    assert quality_pass(measure_view(after))


def test_example_matches_its_template_and_keeps_parameters_near_targets():
    """示例 MDL 与模板重建结果方程一致；natural 排版合格，参数靠近直接作用对象。"""
    root = ROOT / "skills/vensim-skill/assets"
    example = root / "examples/water_feedback_zh.mdl"
    text = build_model(json.loads((root / "templates/water_feedback_zh.json").read_text()))
    marker = "\\\\\\---///"
    assert text.split(marker, 1)[0] == example.read_text().split(marker, 1)[0]
    view = parse_views(text.splitlines(True), {"水量"})[0]
    assert quality_pass(measure_view(view))
    objects = {obj.name: obj for obj in view.objects.values()}
    for parameter, target in (("目标水量", "缺水量"), ("调节周期", "补水需求")):
        a, b = objects[parameter], objects[target]
        c = objects["水量"]
        assert math.dist((a.x, a.y), (b.x, b.y)) < math.dist((a.x, a.y), (c.x, c.y))


@pytest.mark.parametrize(
    "config",
    [
        {"node_spacing": True},
        {"routing_passes": 1.0},
        {"routing_passes": True},
        {"node_positions": {"A": [True, 200]}},
        {"move_stocks": "false"},
        {"shadow_visibility": "always"},
        {"max_allowed_crossings": -1},
        {"max_allowed_crossings": False},
        {"circular_gap": 40},
        {"layout_mode": "circular"},
        {"style": "academic"},
        {"graphviz_max_span": 0},
        {"layout_mode": "graphviz"},
        {"curve_strength": 0.12},
        {"maximum_curve_pixels": 180},
        {"split_shadows": "yes"},
        {"seed": -1},
        {"anneal_iterations": 10},
        {"stock_size": "huge"},
        {"lock_node_names": "甲"},
    ],
)
def test_layout_rejects_ambiguous_or_invalid_configuration(config):
    with pytest.raises(ValueError):
        validate_config(config)


def test_build_splits_subsystems_into_views_with_shadow_references(tmp_path):
    from simulation_runner import run_model

    spec = water_spec()
    for item in spec["variables"]:
        if item["name"] in {"目标水量", "缺水量", "调节周期", "补水需求"}:
            item["view"] = "补水决策"
    path = tmp_path / "views.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "views.mdl"
    command_build(path, output)
    report = json.loads(output.with_suffix(".mdl.build_report.json").read_text())
    assert [item["view"] for item in report["views"]] == ["系统动力学模型", "补水决策"]
    assert report["pass"]
    first, second = load_mdl(output)[1]
    # 补水（流量）引用其他视图的补水需求，缺水量引用其他视图的水量：各出现一个影子
    shadows = {
        obj.name for view in (first, second) for obj in view.objects.values() if obj.is_shadow
    }
    assert shadows == {"补水需求", "水量"}
    for view in (first, second):
        assert not measure_view(view)["shadow_inputs"]
    single = tmp_path / "single.mdl"
    single.write_text(build_model(water_spec()), encoding="utf-8")
    assert run_model(output).series == run_model(single).series


def test_flow_and_its_stocks_must_share_a_view():
    spec = water_spec()
    spec["variables"][1]["view"] = "其他"
    with pytest.raises(ValueError, match="同一视图"):
        build_model(spec)


def test_view_advice_flags_fragmented_and_crowded_views():
    from vensim_autolayout import view_advice

    def view(name, count, start=1):
        return diagram(
            [word(start + i, f"{name}{i}", 100 + 90 * i) for i in range(count)], []
        ).replace("*反馈", "*" + name)

    tiny = parse_views((view("甲", 3) + view("乙", 20, 10)).splitlines(True))
    advice = view_advice(tiny)
    assert any("甲" in line and "并入" in line for line in advice)
    crowded = parse_views(view("丙", 45).splitlines(True))
    assert any("拆分" in line for line in view_advice(crowded))
    many = parse_views("".join(view(f"V{i}", 6, i * 10) for i in range(6)).splitlines(True))
    assert any("视图偏多" in line for line in view_advice(many))
