"""natural 排版、柔和圆弧、原生云与 <Time> 预留区的回归测试。"""

import hashlib
import json
import math
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "skills/vensim-skill/scripts"
sys.path.insert(0, str(TOOLS))

from arc_routing import MAX_ARC_RATIO, arc_ratio  # noqa: E402
from mdl_document import MdlDocument  # noqa: E402
from model_builder import command_build  # noqa: E402
from natural_layout import is_loop_marker  # noqa: E402
from sketch_geometry import MIN_VISIBLE_ARROW, measure_view, quality_pass  # noqa: E402
from vensim_autolayout import command_layout, load_mdl  # noqa: E402

# ---------------------------------------------------------------------------
# 测试模型构造
# ---------------------------------------------------------------------------

HEADER = "$192-192-192,0,SimSun|12||0-0-0|0-0-0|0-0-0|-1--1--1|-1--1--1|96,96,100,0"


def controls(unit="Year"):
    return "".join(
        f"{name} = {value}\n\t~ {unit}\n\t~\n\t|\n\n"
        for name, value in (
            ("INITIAL TIME", 0),
            ("FINAL TIME", 10),
            ("TIME STEP", 0.25),
            ("SAVEPER", 1),
        )
    )


def view_block(name, records):
    return "\n".join(
        [
            r"\\\---/// Sketch information - do not modify anything except names",
            "V300",
            f"*{name}",
            HEADER,
            *records,
        ]
    )


def two_chain_model(time_on_flow=False):
    """两条“云—阀门—存量—阀门—云”链，云写成非原生的 0，含 Time 引用与跨链连线。

    time_on_flow=True 时流入乙也直接引用 Time：它的流量名只能放在管道下方，
    又被另一条链的存量驱动，属于需要报告原因或显式影子拆分的受限结构。
    """
    equations = (
        "{UTF-8}\n"
        "库存甲 = INTEG(流入甲 - 流出甲, 初值甲)\n\t~ 件\n\t~\n\t|\n\n"
        "流入甲 = 系数甲 * 10\n\t~ 件/Year\n\t~\n\t|\n\n"
        "流出甲 = 库存甲 / 周期\n\t~ 件/Year\n\t~\n\t|\n\n"
        "库存乙 = INTEG(流入乙 - 流出乙, 初值乙)\n\t~ 件\n\t~\n\t|\n\n"
        + (
            "流入乙 = IF THEN ELSE(Time > 冲击时点, 系数乙, 0) * 库存甲\n\t~ 件/Year\n\t~\n\t|\n\n"
            if time_on_flow
            else "流入乙 = 系数乙 * 库存甲\n\t~ 件/Year\n\t~\n\t|\n\n"
        )
        + "流出乙 = 库存乙 / 周期\n\t~ 件/Year\n\t~\n\t|\n\n"
        "开关 = IF THEN ELSE(Time > 冲击时点, 1, 0)\n\t~ Dmnl\n\t~\n\t|\n\n"
        "初值甲 = 100\n\t~ 件\n\t~\n\t|\n\n"
        "初值乙 = 50\n\t~ 件\n\t~\n\t|\n\n"
        "系数甲 = 0.1\n\t~ 1/Year\n\t~\n\t|\n\n"
        "系数乙 = 0.2\n\t~ 1/Year\n\t~\n\t|\n\n"
        "周期 = 5\n\t~ Year\n\t~\n\t|\n\n"
        "冲击时点 = 3\n\t~ Year\n\t~\n\t|\n\n"
        "效果 = 开关 * 系数甲\n\t~ 1/Year\n\t~\n\t|\n\n"
    ) + controls()
    records = []
    oid = 1

    def rec(text):
        records.append(text)

    # 链 1
    rec("10,1,库存甲,700,300,40,20,3,3,0,0,0,0,0,0,0,0,0,0,0,0")
    rec("12,2,0,300,300,22,16,0,3,0,0,0,0,0,0,0,0,0,0,0,0")
    rec("11,3,0,500,300,6,8,34,3,0,0,1,0,0,0,0,0,0,0,0,0")
    rec("10,4,流入甲,500,340,40,16,40,3,0,0,-1,0,0,0,0,0,0,0,0,0")
    rec("1,5,3,2,100,0,0,22,0,0,0,-1--1--1,,1|(400,300)|")
    rec("1,6,3,1,4,0,0,22,0,0,0,-1--1--1,,1|(600,300)|")
    rec("12,7,0,1100,300,22,16,0,3,0,0,0,0,0,0,0,0,0,0,0,0")
    rec("11,8,0,900,300,6,8,34,3,0,0,1,0,0,0,0,0,0,0,0,0")
    rec("10,9,流出甲,900,340,40,16,40,3,0,0,-1,0,0,0,0,0,0,0,0,0")
    rec("1,10,8,1,100,0,0,22,0,0,0,-1--1--1,,1|(800,300)|")
    rec("1,11,8,7,4,0,0,22,0,0,0,-1--1--1,,1|(1000,300)|")
    # 链 2
    rec("10,12,库存乙,700,900,40,20,3,3,0,0,0,0,0,0,0,0,0,0,0,0")
    rec("12,13,0,300,900,22,16,0,3,0,0,0,0,0,0,0,0,0,0,0,0")
    rec("11,14,0,500,900,6,8,34,3,0,0,1,0,0,0,0,0,0,0,0,0")
    rec("10,15,流入乙,500,940,40,16,40,3,0,0,-1,0,0,0,0,0,0,0,0,0")
    rec("1,16,14,13,100,0,0,22,0,0,0,-1--1--1,,1|(400,900)|")
    rec("1,17,14,12,4,0,0,22,0,0,0,-1--1--1,,1|(600,900)|")
    rec("12,18,0,1100,900,22,16,0,3,0,0,0,0,0,0,0,0,0,0,0,0")
    rec("11,19,0,900,900,6,8,34,3,0,0,1,0,0,0,0,0,0,0,0,0")
    rec("10,20,流出乙,900,940,40,16,40,3,0,0,-1,0,0,0,0,0,0,0,0,0")
    rec("1,21,19,12,100,0,0,22,0,0,0,-1--1--1,,1|(800,900)|")
    rec("1,22,19,18,4,0,0,22,0,0,0,-1--1--1,,1|(1000,900)|")
    # 辅助变量（故意放在远处、彼此重叠）
    for oid, name in (
        (23, "初值甲"),
        (24, "初值乙"),
        (25, "系数甲"),
        (26, "系数乙"),
        (27, "周期"),
        (28, "冲击时点"),
        (29, "开关"),
        (30, "效果"),
    ):
        rec(f"10,{oid},{name},1500,{100 + oid * 4},40,16,8,3,0,0,0,0,0,0,0,0,0,0,0,0")
    links = [
        (23, 1),
        (24, 12),
        (25, 4),
        (1, 9),
        (27, 9),
        (1, 15),
        (26, 15),
        (12, 20),
        (27, 20),
        (28, 29),
        (29, 30),
        (25, 30),
    ]
    if time_on_flow:
        links.append((28, 15))
    for index, (a, b) in enumerate(links):
        rec(f"1,{40 + index},{a},{b},0,0,0,0,0,64,0,-1--1--1,,1|(0,0)|")
    return equations + view_block("双链", records) + "\n///---\\\\\\\n"


def k33_model():
    """K3,3：三个参数各自影响三个辅助量，不可平面，至少一处交叉。"""
    names_a = ["甲参数", "乙参数", "丙参数"]
    names_b = ["子量一", "子量二", "子量三"]
    eq = "{UTF-8}\n"
    for name in names_a:
        eq += f"{name} = 1\n\t~ Dmnl\n\t~\n\t|\n\n"
    for name in names_b:
        eq += f"{name} = {' + '.join(names_a)}\n\t~ Dmnl\n\t~\n\t|\n\n"
    eq += controls()
    records = []
    for i, name in enumerate(names_a + names_b, start=1):
        records.append(
            f"10,{i},{name},{100 + i * 90},{100 + (i % 2) * 60},40,16,8,3,0,0,0,0,0,0,0,0,0,0,0,0"
        )
    aid = 10
    for a in range(1, 4):
        for b in range(4, 7):
            records.append(f"1,{aid},{a},{b},0,0,0,0,0,64,0,-1--1--1,,1|(0,0)|")
            aid += 1
    return eq + view_block("不可平面", records) + "\n///---\\\\\\\n"


def write(tmp_path, name, text, encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def equation_bytes(path):
    return MdlDocument.read(path).equation_bytes


# ---------------------------------------------------------------------------
# natural 排版
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def chains(tmp_path_factory):
    # 同一份排版结果供多项断言复用，避免重复运行多起点退火。
    tmp_path = tmp_path_factory.mktemp("chains")
    source = write(tmp_path, "chains.mdl", two_chain_model())
    target = tmp_path / "chains_natural.mdl"
    command_layout(source, target, mode="natural")
    return source, target


def test_natural_passes_visual_and_keeps_equations(chains):
    source, target = chains
    assert equation_bytes(source) == equation_bytes(target)
    view = load_mdl(target)[1][0]
    metrics = measure_view(view)
    assert quality_pass(metrics), {k: v for k, v in metrics.items() if isinstance(v, list) and v}
    report = json.loads(target.with_suffix(".mdl.layout_report.json").read_text(encoding="utf-8"))
    assert report["pass"] and report["equations_preserved"] and report["topology_preserved"]
    assert report["layout_mode"] == "natural"


def test_natural_keeps_topology_and_semantic_fields(chains):
    source, target = chains
    before, after = load_mdl(source)[1][0], load_mdl(target)[1][0]
    assert set(before.objects) == set(after.objects)
    assert [(a.obj_id, a.from_id, a.to_id) for a in before.arrows] == [
        (a.obj_id, a.from_id, a.to_id) for a in after.arrows
    ]
    for left, right in zip(before.arrows, after.arrows, strict=True):
        assert [left.fields[i] for i in (6, 7, 9, 12)] == [right.fields[i] for i in (6, 7, 9, 12)]


def test_clouds_are_written_as_native_48(chains):
    source, target = chains
    assert load_mdl(source)[1][0] and measure_view(load_mdl(source)[1][0])["invisible_clouds"]
    view = load_mdl(target)[1][0]
    clouds = [obj for obj in view.objects.values() if obj.kind == 12]
    assert len(clouds) == 4
    for obj in clouds:
        fields = obj.raw_fields
        assert fields[2] == "48" and fields[5:9] == ["10", "8", "0", "3"]
    assert not measure_view(view)["invisible_clouds"]


def test_refine_mode_also_repairs_clouds(tmp_path):
    source = write(tmp_path, "chains.mdl", two_chain_model())
    target = tmp_path / "refined.mdl"
    command_layout(source, target, mode="refine")
    view = load_mdl(target)[1][0]
    assert all(obj.raw_fields[2] == "48" for obj in view.objects.values() if obj.kind == 12)


def test_skeleton_is_horizontal_with_native_spacing(chains):
    _source, target = chains
    view = load_mdl(target)[1][0]
    objects = view.objects
    for cloud, valve, stock in ((2, 3, 1), (13, 14, 12)):
        assert objects[cloud].y == objects[valve].y == objects[stock].y
        assert abs(objects[valve].x - objects[cloud].x) == 80
        assert abs(objects[stock].x - objects[valve].x) >= 120
    for valve, label in ((3, 4), (8, 9), (14, 15), (19, 20)):
        assert objects[label].x == objects[valve].x
        assert abs(objects[label].y - objects[valve].y) == 30


def test_soft_arcs_follow_the_k_rule(chains):
    _source, target = chains
    view = load_mdl(target)[1][0]
    ratios = []
    for arrow in view.arrows:
        if arrow.is_physical_flow:
            continue
        assert arrow.shape == 1 and len(arrow.points) == 1
        k = arc_ratio(view.objects[arrow.from_id], view.objects[arrow.to_id], arrow.points[0])
        ratios.append(abs(k))
        assert arrow.fields[11] == "0-0-0"
    assert max(ratios) <= MAX_ARC_RATIO + 0.01
    curved = [k for k in ratios if k > 0.01]
    assert len(curved) >= len(ratios) * 0.8
    # Vensim 官方示例的弧度中位数约 0.14；目标 0.12
    assert 0.08 <= sum(curved) / len(curved) <= 0.16


def test_time_reserved_area_and_initial_values(chains):
    _source, target = chains
    view = load_mdl(target)[1][0]
    metrics = measure_view(view)
    names = {view.objects[i].name for i in metrics["time_label_owners"]}
    # 开关直接引用 Time；控制变量 TIME STEP 等不算。
    assert names == {"开关"}
    assert not metrics["time_label_conflicts"]
    objects = {obj.name: obj for obj in view.objects.values()}
    for initial, stock in (("初值甲", "库存甲"), ("初值乙", "库存乙")):
        assert abs(objects[initial].x - objects[stock].x) <= 40


def test_natural_is_deterministic(tmp_path, chains):
    source, target = chains
    again = tmp_path / "again.mdl"
    command_layout(source, again, mode="natural")
    assert again.read_bytes() == target.read_bytes()


@pytest.mark.parametrize(
    ("encoding", "bom", "newline"),
    [("utf-8", False, "\n"), ("utf-8", True, "\r\n"), ("gb18030", False, "\r\n")],
)
def test_natural_preserves_equation_bytes_across_encodings(tmp_path, encoding, bom, newline):
    text = two_chain_model().replace("{UTF-8}\n", "" if encoding != "utf-8" else "{UTF-8}\n")
    data = text.replace("\n", newline).encode(encoding)
    if bom:
        data = b"\xef\xbb\xbf" + data
    source = tmp_path / "encoded.mdl"
    source.write_bytes(data)
    target = tmp_path / "encoded_natural.mdl"
    command_layout(source, target, mode="natural")
    assert equation_bytes(source) == equation_bytes(target)
    assert target.read_bytes().startswith(b"\xef\xbb\xbf") == bom
    assert (b"\r\n" in target.read_bytes()) == (newline == "\r\n")


def test_natural_refuses_to_overwrite_and_leaves_no_partial_output(tmp_path):
    source = write(tmp_path, "chains.mdl", two_chain_model())
    with pytest.raises(ValueError):
        command_layout(source, source, mode="natural")
    target = tmp_path / "out.mdl"
    target.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError):
        command_layout(source, target, mode="natural")
    assert target.read_text(encoding="utf-8") == "keep"
    blocked = tmp_path / "blocked.mdl"
    blocked.with_suffix(".mdl.layout_report.json").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError):
        command_layout(source, blocked, mode="natural")
    assert not blocked.exists()


# ---------------------------------------------------------------------------
# 不可平面结构与显式影子拆分
# ---------------------------------------------------------------------------


def test_time_owning_flow_label_needs_reported_reason_or_explicit_split(tmp_path):
    source = write(tmp_path, "time_flow.mdl", two_chain_model(time_on_flow=True))
    plain, split = tmp_path / "plain.mdl", tmp_path / "split.mdl"
    command_layout(source, plain, mode="natural")
    report = json.loads(plain.with_suffix(".mdl.layout_report.json").read_text(encoding="utf-8"))
    view = report["views"][0]
    after = view["after"]
    # 不允许出现文字重叠、穿字、过短箭头或压住 <Time>；若剩交叉必须给出原因。
    for key in ("node_overlaps", "short_arrows", "invisible_clouds"):
        assert not after[key]
    if not view["pass"]:
        assert view["remaining_crossings"] and "advice" in view
        assert all(
            row["reason"] in {"label_side", "non_planar", "unresolved"}
            for row in view["remaining_crossings"]
        )
    command_layout(source, split, split_shadows=True, mode="natural")
    assert quality_pass(measure_view(load_mdl(split)[1][0]))
    assert equation_bytes(source) == equation_bytes(split)


def test_non_planar_view_reports_reason_without_split(tmp_path):
    pytest.importorskip("networkx")
    source = write(tmp_path, "k33.mdl", k33_model())
    target = tmp_path / "k33_natural.mdl"
    command_layout(source, target, split_shadows=False, mode="natural")
    report = json.loads(target.with_suffix(".mdl.layout_report.json").read_text(encoding="utf-8"))
    view = report["views"][0]
    assert view["planarity"]["planar"] is False
    assert view["remaining_crossings"]
    assert {row["reason"] for row in view["remaining_crossings"]} <= {"non_planar", "unresolved"}
    assert "advice" in view and not view["shadow_splits"]
    assert report["topology_preserved"]


def test_explicit_shadow_split_removes_crossing_and_keeps_causality(tmp_path):
    source = write(tmp_path, "k33.mdl", k33_model())
    target = tmp_path / "k33_split.mdl"
    command_layout(source, target, split_shadows=True, mode="natural")
    assert equation_bytes(source) == equation_bytes(target)
    before, after = load_mdl(source)[1][0], load_mdl(target)[1][0]
    metrics = measure_view(after)
    assert quality_pass(metrics)
    report = json.loads(target.with_suffix(".mdl.layout_report.json").read_text(encoding="utf-8"))
    splits = report["views"][0]["shadow_splits"]
    assert splits and not report["topology_preserved"]
    for item in splits:
        shadow = after.objects[item["new_shadow_id"]]
        assert shadow.is_shadow and shadow.name == item["variable"]
        moved = next(a for a in after.arrows if a.obj_id == item["arrow"])
        original = next(a for a in before.arrows if a.obj_id == item["arrow"])
        assert moved.from_id == shadow.obj_id and moved.to_id == original.to_id
        assert before.objects[original.from_id].name == shadow.name


# ---------------------------------------------------------------------------
# 检查器：过短箭头与 <Time> 冲突
# ---------------------------------------------------------------------------


def test_visual_flags_short_arrows_and_time_conflicts(tmp_path):
    equations = (
        "{UTF-8}\n甲 = 1\n\t~ Dmnl\n\t~\n\t|\n\n乙 = 甲 * Time\n\t~ Dmnl\n\t~\n\t|\n\n"
        "丙 = 1\n\t~ Dmnl\n\t~\n\t|\n\n"
    ) + controls()
    records = [
        "10,1,甲,100,100,30,12,8,3,0,0,0,0,0,0,0,0,0,0,0,0",
        "10,2,乙,170,100,30,12,8,3,0,0,0,0,0,0,0,0,0,0,0,0",
        "10,3,丙,400,100,30,12,8,3,0,0,0,0,0,0,0,0,0,0,0,0",
        "1,4,1,2,1,0,0,0,0,64,0,-1--1--1,,1|(135,100)|",
        "1,5,3,2,1,0,0,0,0,64,0,-1--1--1,,1|(300,150)|",
    ]
    path = write(
        tmp_path, "short.mdl", equations + view_block("短线", records) + "\n///---\\\\\\\n"
    )
    metrics = measure_view(load_mdl(path)[1][0])
    assert [row["arrow"] for row in metrics["short_arrows"]] == [4]
    assert metrics["short_arrows"][0]["length"] < MIN_VISIBLE_ARROW
    assert metrics["time_label_owners"] == [2]
    assert {"owner": 2, "arrow": 5} in metrics["time_label_conflicts"]
    assert not quality_pass(metrics)


def test_attached_shadow_flow_label_is_not_hidden_as_orphan(tmp_path):
    """附着在阀门上的影子是流量名的显示实例，orphan 策略不能把它隐藏。"""
    text = two_chain_model().replace(
        "10,20,流出乙,900,940,40,16,40,3,", "10,20,流出乙,900,940,40,16,40,2,"
    )
    text = text.replace("1,49,12,20,", "1,49,12,9,").replace("1,50,27,20,", "1,50,27,9,")
    source = write(tmp_path, "attached.mdl", text)
    target = tmp_path / "attached_out.mdl"
    command_layout(source, target, mode="refine")
    view = load_mdl(target)[1][0]
    assert view.objects[20].raw_fields[9] == "0"


# ---------------------------------------------------------------------------
# 预览与建模
# ---------------------------------------------------------------------------


def test_preview_draws_arcs_clouds_and_time_area(tmp_path, chains):
    from sketch_preview import render_preview

    source, target = chains
    svg = tmp_path / "preview.html"
    render_preview(target, svg, source)
    html = svg.read_text(encoding="utf-8")
    assert html.count("<polygon") >= 8  # 前后各 4 朵云
    assert "stroke-dasharray" in html  # 原图非 48 云与 <Time> 预留区
    assert "&lt;Time&gt;" in html
    assert "Short arrows" in html and "Non-native clouds" in html
    after_view = html.split("<h1>After</h1>", 1)[1]
    arcs = [part for part in after_view.split('<path d="M')[1:] if 'marker-end="url(#head' in part]
    # 圆弧按 0.5px 容差离散为折线：绝大多数信息线应有多于两个点。
    curved = [part for part in arcs if part.split('"', 1)[0].count(" L") >= 3]
    assert arcs and len(curved) >= 0.8 * len(arcs)


def test_build_defaults_to_natural_with_native_clouds(tmp_path):
    template = ROOT / "skills/vensim-skill/assets/templates/inventory_zh.json"
    output = tmp_path / "inventory.mdl"
    command_build(template, output)
    report = json.loads(output.with_suffix(".mdl.build_report.json").read_text(encoding="utf-8"))
    assert report["requested_mode"] == "natural" and report["pass"]
    view = load_mdl(output)[1][0]
    # 12 类对象既有云也有回路标记；云的字段 2 固定为 48，标记数量等于模板声明的回路数
    clouds = [obj for obj in view.objects.values() if obj.kind == 12 and not is_loop_marker(obj)]
    assert len(clouds) == 2 and all(obj.raw_fields[2] == "48" for obj in clouds)
    assert sum(is_loop_marker(obj) for obj in view.objects.values()) == 2


# ---------------------------------------------------------------------------
# 可选：真实研究模型回归（通过环境变量提供路径，默认跳过）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("VENSIM_SKILL_REGRESSION_MODEL"),
    reason="设置 VENSIM_SKILL_REGRESSION_MODEL 指向多视图模型后运行（耗时约 2 分钟）",
)
def test_external_multiview_model_passes_with_explicit_shadow_split(tmp_path):
    source = Path(os.environ["VENSIM_SKILL_REGRESSION_MODEL"])
    target = tmp_path / "regression_natural.mdl"
    command_layout(source, target, split_shadows=True, mode="natural")
    assert (
        hashlib.sha256(equation_bytes(source)).hexdigest()
        == hashlib.sha256(equation_bytes(target)).hexdigest()
    )
    for view in load_mdl(target)[1]:
        metrics = measure_view(view)
        assert quality_pass(metrics), view.name
        assert metrics["layout_span"] < 3000
    assert math.isfinite(1.0)
