"""cld：子系统与总体因果回路图。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "vensim-skill"
sys.path.insert(0, str(SKILL / "scripts"))

from cld_builder import command_cld, overall_path  # noqa: E402
from vensim_autolayout import parse_views  # noqa: E402

SPEC = SKILL / "assets" / "templates" / "inventory_zh.json"


def _objects(path):
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    return lines, parse_views(lines, set(), set())


def test_cld_files_have_plain_variables_signed_links_and_no_valves(tmp_path):
    output = tmp_path / "inventory_cld.mdl"
    assert command_cld(SPEC, output) == 0
    overall = overall_path(output, "zh")
    assert overall.exists()
    for path in (output, overall):
        lines, views = _objects(path)
        for view in views:
            kinds = {obj.kind for obj in view.objects.values()}
            assert 11 not in kinds  # 没有阀门
            variables = [obj for obj in view.objects.values() if obj.kind == 10]
            assert variables
            for obj in variables:
                fields = lines[obj.line_index].split(",")
                assert fields[7] == "8"  # 无框文字
            signs = (
                {arrow.polarity for arrow in view.arrows}
                if hasattr(view.arrows[0], "polarity")
                else {lines[a.line_index].split(",")[6] for a in view.arrows}
            )
            assert signs & {"+", "-", "43", "45"}
    _, views = _objects(overall)
    assert len(views) == 1
    assert not any(obj.is_shadow for obj in views[0].objects.values() if obj.kind == 10)
    text = output.read_text(encoding="utf-8")
    assert "A FUNCTION OF(" in text
    assert "INTEG" not in text


def test_multi_view_cld_uses_shadows_and_shared_loop_numbers(tmp_path):
    import json

    spec = json.loads((SKILL / "assets" / "templates" / "ev_adoption_zh.json").read_text("utf-8"))
    for item in spec["variables"]:
        item["view"] = "充电设施" if "充电" in item["name"] else "汽车市场"
    path = tmp_path / "ev2.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "ev2_cld.mdl"
    assert command_cld(path, output) == 0
    _, views = _objects(output)
    assert [view.name for view in views] == ["汽车市场", "充电设施"]
    assert any(obj.is_shadow for view in views for obj in view.objects.values())
    report = json.loads(output.with_suffix(".mdl.cld_report.json").read_text("utf-8"))
    overall = {m["loop"]: m["label"] for v in report["overall"]["loop_markers"].values() for m in v}
    for markers in report["subsystems"]["loop_markers"].values():
        for marker in markers:
            assert overall[marker["loop"]] == marker["label"]


def test_parallel_view_layout_matches_sequential(tmp_path, monkeypatch):
    import json

    from model_builder import command_build

    spec = json.loads((SKILL / "assets" / "templates" / "ev_adoption_zh.json").read_text("utf-8"))
    for item in spec["variables"]:
        item["view"] = "充电设施" if "充电" in item["name"] else "汽车市场"
    path = tmp_path / "ev2.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    outputs = []
    for workers in ("1", "2"):
        monkeypatch.setenv("VENSIM_SKILL_WORKERS", workers)
        output = tmp_path / f"ev2_{workers}.mdl"
        command_build(path, output)
        outputs.append(output.read_bytes())
    assert outputs[0] == outputs[1]
