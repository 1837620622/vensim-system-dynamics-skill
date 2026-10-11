"""用收录的真实教材模型检查排版与仿真没有退化。"""

import hashlib
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/vensim-skill/scripts"))

from natural_layout import extract_flows, is_loop_marker, loop_marker_cycles  # noqa: E402
from vensim_autolayout import command_layout, load_mdl  # noqa: E402

MODELS = ROOT / "skills/vensim-skill/assets/reference_models"

# 与上游仓库中同一文件的 git blob 哈希一致，说明收录时没有改动内容
UPSTREAM_BLOBS = {
    "health_model.mdl": "a3d4438c736ca91e087b26d85ede3d776dceb946",
    "overshoot.mdl": "baf305ea1a045d000224cf02f24b117600e02da8",
    "sir_aggregate.mdl": "d702ad9d3474b1ef14b9f88c9949ef3cd6f3ff17",
    "sir_policy.mdl": "f7cfbcabeb956c9a249582abd1169712aab8b101",
    "student_flows.mdl": "00f812454d2b48bc8fb25737601a0fcad3000a4c",
    "teacup.mdl": "0c692de1deaa83cb0d636d39b64584f1e80c59a6",
    "workforce.mdl": "77370937ebc3da202314ccbc253aa233070f12e0",
}


def layout(tmp_path, name, mode):
    output = tmp_path / f"{mode}_{name}"
    assert command_layout(MODELS / name, output, mode=mode) == 0
    report = json.loads(Path(f"{output}.layout_report.json").read_text(encoding="utf-8"))
    return output, report


def test_reference_models_are_byte_identical_to_upstream():
    found = {path.name for path in MODELS.glob("*.mdl")}
    assert found == set(UPSTREAM_BLOBS)
    for name, blob in UPSTREAM_BLOBS.items():
        data = (MODELS / name).read_bytes()
        digest = hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False)
        assert digest.hexdigest() == blob, name


@pytest.mark.parametrize("name", ["overshoot.mdl", "workforce.mdl", "teacup.mdl"])
def test_auto_keeps_the_authors_layout(tmp_path, name):
    """作者版面没有交叉时，auto 最多挪开个别压字的变量，存量和阀门一律不动。"""
    output, report = layout(tmp_path, name, "auto")
    view = report["views"][0]
    assert view["strategy"] in {"keep", "nudge", "preserve"}
    assert view.get("moved_auxiliary_nodes", 0) <= 3
    assert not view["after"]["arrow_crossings"] and not view["after"]["flow_crossings"]
    before = load_mdl(MODELS / name)[1][0]
    after = load_mdl(output)[1][0]
    for oid, obj in before.objects.items():
        if obj.kind != 10 or obj.stock_like:
            assert (after.objects[oid].x, after.objects[oid].y) == (obj.x, obj.y)


def test_natural_wraps_long_stock_names_instead_of_widening_the_box(tmp_path):
    output, report = layout(tmp_path, "teacup.mdl", "natural")
    assert report["pass"]
    stock = next(
        o for o in load_mdl(output)[1][0].objects.values() if o.name == "Teacup Temperature"
    )
    assert (stock.w, stock.h) == (48, 24)


@pytest.mark.parametrize("name", ["sir_policy.mdl", "student_flows.mdl"])
def test_natural_rebuilds_branching_chains_without_defects(tmp_path, name):
    """接种流画成折线管道、留级画成绕行支路后，检查全部通过。"""
    _, report = layout(tmp_path, name, "natural")
    assert report["pass"] and report["equations_preserved"]


def test_loop_markers_follow_their_loops(tmp_path):
    """R/B 标记重建后仍在所标回路的节点附近。"""
    source = load_mdl(MODELS / "sir_aggregate.mdl")[1][0]
    objects = dict(source.objects)
    markers = [oid for oid, obj in objects.items() if is_loop_marker(obj)]
    cycles = loop_marker_cycles(source, objects, extract_flows(source), markers)
    assert set(cycles) == set(markers)
    output, report = layout(tmp_path, "sir_aggregate.mdl", "natural")
    assert report["pass"]
    after = load_mdl(output)[1][0].objects
    for marker, cycle in cycles.items():
        cx = sum(after[n].x for n in cycle) / len(cycle)
        cy = sum(after[n].y for n in cycle) / len(cycle)
        others = [math.dist((after[m].x, after[m].y), (cx, cy)) for m in markers if m != marker]
        mine = math.dist((after[marker].x, after[marker].y), (cx, cy))
        assert mine <= 120 and all(mine <= other for other in others)


@pytest.mark.parametrize("name", sorted(UPSTREAM_BLOBS))
def test_builtin_engine_matches_pysd_on_reference_models(name):
    pytest.importorskip("pysd")
    from mdl_document import MdlDocument
    from simulation_runner import CONTROL_NAMES, compare_backends
    from vensim_engine import parse_equations

    path = MODELS / name
    equations = parse_equations(MdlDocument.read(path).semantic_text, expand=False)
    names = [n for n, e in equations.items() if n not in CONTROL_NAMES and not e.is_lookup]
    report = compare_backends(path, names)
    assert report["pass"], report["max_scaled_error"]
