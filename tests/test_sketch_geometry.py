from pathlib import Path
import json
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "skills/vensim-skill/scripts"
sys.path.insert(0, str(TOOLS))

from mdl_document import MdlDocument  # noqa: E402
from sketch_geometry import circle_path, measure_view, path_hits_box  # noqa: E402
from vensim_autolayout import command_layout, load_mdl, parse_stock_names, parse_views, update_arrow_line  # noqa: E402


def sketch(nodes, arrows):
    return "\n".join([r"\\\---/// Sketch information", "V300", "*View", "$192-192-192,0,Arial|12||0-0-0|0-0-0|0-0-150|-1--1--1|-1--1--1|96,96,100,0", *nodes, *arrows, ""])


def node(oid, name, x, y, bits=3):
    return f"10,{oid},{name},{x},{y},35,14,0,{bits},0,0,0,0,0,0"


def arrow(oid, source, target, control="(0,0)", shape=0):
    return f"1,{oid},{source},{target},{shape},0,43,0,0,65,0,-1--1--1,,1|{control}|"


def test_circle_uses_arc_instead_of_control_polygon():
    path = circle_path((0, 0), (50, 50), (100, 0))
    assert path[0] == (0, 0) and path[-1] == (100, 0)
    assert path_hits_box(path, (23, 41, 27, 45))
    assert not path_hits_box([(0, 0), (50, 50), (100, 0)], (23, 41, 27, 45))


def test_straight_arrow_ignores_dummy_handle():
    text = sketch([node(1, "A", 100, 100), node(2, "B", 400, 100), node(3, "C", 250, 100)], [arrow(4, 1, 2)])
    report = measure_view(parse_views(text.splitlines(keepends=True))[0])
    assert report["arrow_node_collisions"] == [{"arrow": 4, "object": 3}]


def test_router_avoids_label_and_preserves_semantics(tmp_path):
    text = sketch([node(1, "A", 100, 100), node(2, "B", 400, 100), node(3, "C", 250, 100)], [arrow(4, 1, 2)])
    source, target = tmp_path / "source.mdl", tmp_path / "target.mdl"
    source.write_text(text)
    command_layout(source, target, mode="preserve")
    before, after = load_mdl(source)[1][0], load_mdl(target)[1][0]
    assert measure_view(after)["arrow_node_collisions"] == []
    assert after.arrows[0].fields[6] == "43"
    assert after.arrows[0].fields[9] == "65"
    assert before.objects == after.objects
    assert source.read_text() == text


def test_opposite_directions_do_not_share_same_arc(tmp_path):
    source, target = tmp_path / "source.mdl", tmp_path / "target.mdl"
    source.write_text(sketch([node(1, "A", 100, 100), node(2, "B", 400, 100)], [arrow(3, 1, 2), arrow(4, 2, 1)]))
    command_layout(source, target, mode="preserve")
    arrows = load_mdl(target)[1][0].arrows
    assert arrows[0].points != arrows[1].points
    assert not measure_view(load_mdl(target)[1][0])["arrow_crossings"]


@pytest.mark.parametrize("encoding,bom,newline", [("utf-8", b"", "\n"), ("utf-8", b"\xef\xbb\xbf", "\r\n"), ("gb18030", b"", "\r\n")])
def test_layout_preserves_equation_bytes_and_encoding(tmp_path, encoding, bom, newline):
    text = '库存 = INTEG(\n  流量, 10)\n~ 件\n~ 注释\n|\n' + sketch([node(1, "库存", 100, 100), node(2, "流量", 400, 100)], [arrow(3, 1, 2)])
    raw = bom + text.replace("\n", newline).encode(encoding)
    source, target = tmp_path / "source.mdl", tmp_path / "target.mdl"
    source.write_bytes(raw)
    command_layout(source, target, mode="preserve")
    assert MdlDocument.read(target).equation_bytes == MdlDocument.read(source).equation_bytes
    assert MdlDocument.read(target).encoding == encoding
    assert b"\xef\xbf\xbd" not in target.read_bytes()


def test_continuation_inside_utf8_character_round_trips(tmp_path):
    raw = '库存 = INTEG(0, 10)\n~ 件\n|\n'.encode()
    raw = raw[:1] + b"\\\r\n\t" + raw[1:]
    raw += sketch([node(1, "库存", 100, 100)], []).encode()
    source, target = tmp_path / "source.mdl", tmp_path / "target.mdl"
    source.write_bytes(raw)
    command_layout(source, target, mode="preserve")
    assert target.read_bytes() == raw
    assert parse_stock_names(MdlDocument.read(source).semantic_text) == {"库存"}


def test_font_pipes_and_tooltip_suffix_survive():
    line = "1,3,1,2,1,2,45,2,63,65,0,0-0-0,Arial|12|B|0-0-0,1|(200,50)|tooltip\r\n"
    result = update_arrow_line(line, (220, 80))
    assert "1,2,45,2,63,65" in result
    assert "Arial|12|B|0-0-0,1|(220,80)|tooltip\r\n" in result


def test_duplicate_ids_fail_and_numeric_comment_is_not_parsed():
    with pytest.raises(ValueError, match="重复"):
        parse_views(sketch([node(1, "A", 100, 100), node(1, "B", 200, 100)], []).splitlines(True))
    text = sketch(["12,1,0,100,100,10,10,0,4,0,0,0", node(1, "comment text", 0, 0)], [])
    assert len(parse_views(text.splitlines(True))[0].objects) == 1


def test_unsupported_arrows_and_endpoints_are_unchanged(tmp_path):
    source, target = tmp_path / "source.mdl", tmp_path / "target.mdl"
    raw = sketch([node(1, "A", 100, 100), node(2, "B", 400, 100)], [arrow(3, 1, 2, shape=4)])
    source.write_text(raw)
    command_layout(source, target)
    assert source.read_bytes() == target.read_bytes()
    report = json.loads(target.with_suffix(".mdl.layout_report.json").read_text())
    assert report["equations_preserved"] and report["topology_preserved"]


def test_overlapping_shadow_instances_are_separated_without_merging(tmp_path):
    source, target = tmp_path / "shadow.mdl", tmp_path / "fixed.mdl"
    source.write_text(sketch([node(1, "Same", 120, 100, bits=2), node(2, "Same", 120, 100, bits=2),
                             node(3, "Same", 120, 100), node(4, "Target A", 400, 60), node(5, "Target B", 400, 260)],
                            [arrow(6, 1, 4), arrow(7, 2, 5)]))
    before = load_mdl(source)[1][0]
    assert measure_view(before)["shadow_overlaps"]
    command_layout(source, target, mode="refine")
    after = load_mdl(target)[1][0]
    assert not measure_view(after)["shadow_overlaps"]
    assert [(obj.obj_id, obj.name, obj.bits) for obj in before.objects.values()] == [(obj.obj_id, obj.name, obj.bits) for obj in after.objects.values()]
    assert [(a.from_id, a.to_id) for a in before.arrows] == [(a.from_id, a.to_id) for a in after.arrows]


def test_shadow_of_stock_can_move_but_stock_stays_locked(tmp_path):
    source, target = tmp_path / "shadow.mdl", tmp_path / "fixed.mdl"
    source.write_text("Stock=INTEG(0,1)~Unit~|\n" + sketch([node(1, "Stock", 150, 150), node(2, "Stock", 150, 150, bits=2)], []))
    command_layout(source, target, mode="refine")
    objects = load_mdl(target)[1][0].objects
    assert (objects[1].x, objects[1].y) == (150, 150)
    assert (objects[2].x, objects[2].y) != (150, 150)
    assert objects[2].is_shadow


def test_style_defaults_and_pure_blue():
    from vensim_autolayout import split_arrow_record
    line = arrow(3, 1, 2)
    assert split_arrow_record(update_arrow_line(line, (100, 30)))[0][11] == "-1--1--1"
    black = split_arrow_record(update_arrow_line(line, (100, 30), {"style": "monochrome"}))[0]
    blue = split_arrow_record(update_arrow_line(line, (100, 30), {"style": "native-blue"}))[0]
    assert black[11] == "0-0-0" and blue[11] == "0-0-255"
    assert int(black[8]) & 1 and int(blue[8]) & 1


def test_duplicate_defined_is_reported_without_merging():
    from sketch_geometry import quality_pass
    view = parse_views(sketch([node(1, "Same", 100, 100), node(2, "Same", 300, 100)], []).splitlines(True))[0]
    metrics = measure_view(view)
    assert metrics["duplicate_defined"] == [{"variable": "Same", "ids": [1, 2]}]
    assert not quality_pass(metrics)
    assert len(view.objects) == 2


def test_color_only_change_preserves_unknown_arrow_geometry(tmp_path):
    source, output = tmp_path / "source.mdl", tmp_path / "styled.mdl"
    source.write_text(sketch([node(1, "A", 100, 100), node(2, "B", 400, 100)], [arrow(3, 1, 2, shape=4)]))
    before = load_mdl(source)[1][0].arrows[0]
    command_layout(source, output, style="native-blue")
    after = load_mdl(output)[1][0].arrows[0]
    assert after.shape == before.shape
    assert after.points == before.points
    assert after.fields[11] == "0-0-255"


def test_output_cannot_alias_input_by_symlink(tmp_path):
    source, target = tmp_path / "source.mdl", tmp_path / "alias.mdl"
    source.write_text(sketch([node(1, "A", 100, 100)], []))
    try:
        target.symlink_to(source)
    except OSError:
        pytest.skip("此环境不允许创建符号链接")
    with pytest.raises(ValueError):
        command_layout(source, target)


def test_explicit_anchor_is_used_and_unknown_anchor_rejected(tmp_path):
    source, target, config = tmp_path / "source.mdl", tmp_path / "target.mdl", tmp_path / "layout.json"
    source.write_text(sketch([node(1, "A", 100, 100), node(2, "B", 400, 100)], [arrow(3, 1, 2)]))
    config.write_text(json.dumps({"node_positions": {"A": [150, 200]}}))
    command_layout(source, target, config)
    obj = load_mdl(target)[1][0].objects[1]
    assert (obj.x, obj.y) == (150, 200)
    config.write_text(json.dumps({"node_positions": {"Typo": [150, 200]}}))
    with pytest.raises(ValueError, match="锚点"):
        command_layout(source, target, config)
