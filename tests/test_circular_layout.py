from pathlib import Path
import json
import math
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/vensim-skill/scripts"))

from model_builder import build_model, command_build  # noqa: E402
from sketch_geometry import measure_view, quality_pass  # noqa: E402
from vensim_autolayout import command_layout, load_mdl, parse_views, validate_config  # noqa: E402


def diagram(nodes, links):
    return "\n".join([r"\\\---/// Sketch information", "V300", "*反馈",
                      "$192-192-192,0,Vensim Sans SC|12||0-0-0|0-0-0|0-0-0|-1--1--1|-1--1--1|96,96,100,0",
                      *nodes, *links, ""])


def word(oid, name, x=120, y=120, bits=3):
    return f"10,{oid},{name},{x},{y},45,14,0,{bits},0,0,0,0,0,0"


def link(oid, source, target, thick=0):
    return f"1,{oid},{source},{target},0,0,43,{thick},1,65,0,0-0-0,,1|(0,0)|"


def water_spec():
    return {"time": {"initial": 0, "final": 4, "step": 0.25, "saveper": 1, "unit": "Hour"},
            "variables": [
                {"name": "水量", "kind": "stock", "initial": 10, "unit": "Litre"},
                {"name": "补水", "kind": "flow", "from": None, "to": "水量", "equation": "补水需求", "unit": "Litre/Hour"},
                {"name": "补水需求", "kind": "aux", "equation": "缺水量/调节周期", "unit": "Litre/Hour"},
                {"name": "缺水量", "kind": "aux", "equation": "MAX(0, 目标水量 - 水量)", "unit": "Litre"},
                {"name": "目标水量", "kind": "constant", "equation": 30, "unit": "Litre"},
                {"name": "调节周期", "kind": "constant", "equation": 2, "unit": "Hour"}]}


def test_circular_orders_real_cycle_without_graphviz(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    source, target = tmp_path / "loop.mdl", tmp_path / "circle.mdl"
    # 记录顺序与反馈循线顺序不同，不应直接按输入顺序把边交错在圆内。
    cycle = [1, 3, 5, 2, 4, 6]
    source.write_text(diagram([word(i, f"变量{i}") for i in range(1, 7)],
                             [link(10+i, a, b) for i, (a, b) in enumerate(zip(cycle, cycle[1:]+cycle[:1]))]))
    command_layout(source, target, mode="circular")
    view = load_mdl(target)[1][0]
    assert quality_pass(measure_view(view))
    assert len({obj.y for obj in view.objects.values()}) > 2
    assert len({obj.x for obj in view.objects.values()}) > 2
    assert [(a.from_id, a.to_id, a.fields[6], a.fields[9]) for a in view.arrows] == [(a, b, "43", "65") for a, b in zip(cycle, cycle[1:]+cycle[:1])]
    again = tmp_path / "again.mdl"
    command_layout(source, again, mode="circular")
    assert again.read_bytes() == target.read_bytes()


def test_new_models_default_to_circular_and_allow_local_refinement(tmp_path):
    spec = water_spec()
    path = tmp_path / "model.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "new.mdl"
    command_build(path, output)
    report = json.loads(output.with_suffix(".mdl.build_report.json").read_text())
    assert report["requested_mode"] == report["strategy"] == "circular"
    assert not report["native_verified"]
    assert output.read_text() == build_model(spec)
    spec["sketch"] = {"layout_mode": "refine"}
    assert build_model(spec) != output.read_text()


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
    assert all((obj.x, obj.y) == (500, 500) for obj in view.objects.values() if obj.name in {"目标水量", "调节周期"})
    report = json.loads(output.with_suffix(".mdl.build_report.json").read_text())
    assert not report["pass"] and report["after"]["node_overlaps"]


def test_anchor_is_an_obstacle_before_other_nodes_are_placed(tmp_path):
    source, target, config = (tmp_path / name for name in ("a.mdl", "b.mdl", "layout.json"))
    source.write_text(diagram([word(1, "锚点", 100, 100), word(2, "其他节点", 400, 300)], [link(3, 1, 2)]))
    config.write_text(json.dumps({"node_positions": {"锚点": [400, 300]}}))
    command_layout(source, target, config)
    view = load_mdl(target)[1][0]
    assert (view.objects[1].x, view.objects[1].y) == (400, 300)
    assert not measure_view(view)["node_overlaps"]


def test_disconnected_modules_and_shadows_do_not_merge(tmp_path):
    source, target = tmp_path / "a.mdl", tmp_path / "b.mdl"
    source.write_text(diagram([word(1, "共享", bits=2), word(2, "共享", bits=2),
                              word(3, "甲"), word(4, "乙"), word(5, "共享")],
                             [link(6, 1, 3), link(7, 2, 4)]))
    command_layout(source, target, mode="circular")
    view = load_mdl(target)[1][0]
    assert not measure_view(view)["node_overlaps"]
    assert [(obj.obj_id, obj.name, obj.bits) for obj in view.objects.values()] == [(1, "共享", 2), (2, "共享", 2), (3, "甲", 3), (4, "乙", 3), (5, "共享", 3)]


def test_pipe_hitting_another_label_is_not_reported_as_pass():
    view = parse_views(diagram([word(1, "起点", 100, 100), word(2, "终点", 500, 100), word(3, "被穿过的字", 300, 100)], [link(4, 1, 2, 22)]).splitlines(True))[0]
    metrics = measure_view(view)
    assert metrics["physical_node_collisions"] == [{"arrow": 4, "object": 3}]
    assert not quality_pass(metrics)


def test_ring_keeps_flow_assembly_and_simulation_unchanged(tmp_path):
    from mdl_document import MdlDocument
    from simulation_runner import run_model
    source, target = tmp_path / "source.mdl", tmp_path / "circular.mdl"
    spec = water_spec()
    spec["sketch"] = {"layout_mode": "refine"}
    source.write_text(build_model(spec))
    command_layout(source, target, mode="circular")
    before, after = load_mdl(source)[1][0], load_mdl(target)[1][0]
    assert MdlDocument.read(source).equation_bytes == MdlDocument.read(target).equation_bytes
    assert [(a.fields, a.points) for a in before.arrows if a.is_physical_flow] == [(a.fields, a.points) for a in after.arrows if a.is_physical_flow]
    for oid, obj in before.objects.items():
        if obj.stock_like or obj.kind in (11, 12) or obj.attached_to_valve:
            assert (obj.x, obj.y) == (after.objects[oid].x, after.objects[oid].y)
    assert run_model(source).series == run_model(target).series
    assert quality_pass(measure_view(after))


def test_native_example_is_reproducible_and_parameters_stay_near_targets():
    import hashlib
    root = ROOT / "skills/vensim-skill/assets"
    text = build_model(json.loads((root / "templates/circular_feedback_zh.json").read_text()))
    assert text == (root / "examples/circular_feedback_zh.mdl").read_text()
    verification = json.loads((ROOT / "docs/circular_example_verification.json").read_text())
    assert hashlib.sha256(text.encode()).hexdigest() == verification["model_sha256"]
    assert hashlib.sha256((ROOT / verification["figure"]).read_bytes()).hexdigest() == verification["figure_sha256"]
    view = parse_views(text.splitlines(True), {"水量"})[0]
    objects = {obj.name: obj for obj in view.objects.values()}
    for parameter, target in (("目标水量", "缺水量"), ("调节周期", "补水需求")):
        a, b = objects[parameter], objects[target]
        c = objects["水量"]
        assert math.dist((a.x, a.y), (b.x, b.y)) < math.dist((a.x, a.y), (c.x, c.y))


@pytest.mark.parametrize("config", [
    {"node_spacing": True}, {"routing_passes": 1.0}, {"routing_passes": True},
    {"node_positions": {"A": [True, 200]}}, {"move_stocks": "false"},
    {"max_allowed_crossings": -1}, {"max_allowed_crossings": False},
    {"circular_gap": math.inf}, {"circular_aspect": 0},
])
def test_layout_rejects_ambiguous_or_invalid_configuration(config):
    with pytest.raises(ValueError):
        validate_config(config)
