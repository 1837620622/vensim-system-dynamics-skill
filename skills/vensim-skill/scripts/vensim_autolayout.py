#!/usr/bin/env python3
"""Vensim .mdl 草图检查、审计与保守自动排版工具。

依据 Vensim 官方 Sketch Format / Sketch Object Detail / Arrow Class 文档实现：
  - 解析草图对象(10 变量 / 11 阀门 / 12 源汇云图注释 / 1 箭头)的真实字段；
  - 按 thick 区分物理流率管道与信息箭头，不把接入阀门的信息线错判为管道；
  - 默认局部避让辅助量和影子，库存/阀门/云/流率标签锁定，Graphviz 可选；
  - 只为信息箭头生成单个中间控制点，使普通 Arrow 显示为平滑圆弧；
  - 按真实圆弧检查穿字与交叉，优先局部调整与短路径；
  - 不改方程区，不新建/删除对象，不改箭头 from/to，不覆盖输入文件。

输出必须在 Vensim 中重新打开并运行 Check Model 与 Units Check 后方可使用。
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import re
import shlex
import shutil

# 仅调用 argparse choices 限定的 Graphviz 可执行文件。
import subprocess  # nosec B404
import sys
from collections.abc import Iterable
from pathlib import Path

from mdl_document import MdlDocument, atomic_write, preflight_outputs, separate_output
from sketch_geometry import arrow_path, measure_view, quality_pass

# 草图起始标记（.mdl 中写作 \\\---///）
SKETCH_MARKER = r"\\\---///"


def parse_stock_names(mdl_text: str) -> set:
    """从方程区解析所有库存变量名（含 INTEG 的方程左侧变量）。

    用于替代单纯依赖图形形状识别库存：先从方程语义确定库存名，
    再在 Sketch 中按名定位对象并锁定。
    """
    sketch_pos = mdl_text.find(SKETCH_MARKER)
    body = mdl_text[:sketch_pos] if sketch_pos >= 0 else mdl_text
    stocks: set = set()
    body = re.sub(r"\\\r?\n[ \t]*", "", body).lstrip("\ufeff")
    for match in re.finditer(r"(?m)^\s*([^=~|\\\n]+?)\s*=\s*INTEG\s*\(", body, re.I):
        stocks.add(match.group(1).strip().strip('"'))
    return stocks


# 对象类型码
T_ARROW = 1
T_VARIABLE = 10
T_VALVE = 11
T_SOURCE_SINK = 12
T_OTHER = (30, 31)
OBJECT_TYPES = {T_VARIABLE, T_VALVE, T_SOURCE_SINK, *T_OTHER}

# 箭头字段顺序(官方 Sketch Objects 文档)：
#   1,id,from,to,shape,hid,pol,thick,hasf,dtype,res,color,font,np|plist
# field[7] = thick(线宽)。物理流率管道 thick=22，信息箭头 thick=0。
# 官方格式中 thick 大于 20 才是双线；整数阈值从 21 开始。
FLOW_THICK_THRESHOLD = 21
MAX_GRAPHVIZ_NODES = 1_000
MAX_GRAPHVIZ_EDGES = 5_000
MAX_DOT_LABEL_CHARS = 256
GRAPHVIZ_TIMEOUT_SECONDS = 20
ALLOWED_RANKDIR = {"TB", "BT", "LR", "RL"}

# shape 字段位标志：低 5 位为形状码；bit6(1<<5=32)=附着到阀门；bit7=形状由类型决定
SHAPE_ATTACHED_TO_VALVE = 32
SHAPE_MASK = 31

# bits 字段(field[8])，官方称为 arrows_in_allowed：
#   bit1(1)=允许入箭头, bit2(2)=允许出箭头, bit3(4)=有注释续行,
#   bit4(8)=IO 对象, bit7(64)=因果不穿透, bit8(128)=用户设定尺寸
# PySD 的 sketch.peg 用 arrows_in_allowed 偶数判定 shadow variable：
#   偶数 → shadow variable(跨视图引用，无入箭头)，可独立调整显示位置；
#   奇数 → defined variable(本视图定义，有入箭头)。
# 依据 PySD SketchVisitor.visit_var_definition 与官方 Defined & Shadow Variables。


@dataclasses.dataclass
class Obj:
    """草图对象（变量/阀门/源汇等）。"""

    view_index: int
    line_index: int
    kind: int
    obj_id: int
    name: str
    x: float
    y: float
    w: float  # 半宽
    h: float  # 半高
    shape: int
    bits: int
    raw_fields: list[str]
    stock_names: set = dataclasses.field(default_factory=set)

    @property
    def attached_to_valve(self) -> bool:
        # bit6 置位表示该 word 附着到阀门（或阀门附着到另一 word）
        return bool(self.shape & SHAPE_ATTACHED_TO_VALVE)

    @property
    def is_shadow(self) -> bool:
        # bits 为偶数 → shadow variable，位置独立但方程仍引用同一变量。
        return self.kind == T_VARIABLE and self.bits % 2 == 0

    @property
    def stock_like(self) -> bool:
        from vensim_engine import canonical_name

        # 优先用方程语义：变量名出现在 INTEG 方程左侧则为库存。
        # 退化为图形形状启发式：boxed 形状码 3 作为保守兜底。
        if self.kind != T_VARIABLE:
            return False
        if self.name and canonical_name(self.name) in {
            canonical_name(name) for name in self.stock_names
        }:
            return True
        return (self.shape & SHAPE_MASK) == 3


@dataclasses.dataclass
class Arrow:
    """箭头（信息箭头或物理流管道段）。"""

    view_index: int
    line_index: int
    obj_id: int
    from_id: int
    to_id: int
    shape: int
    thick: int  # field[7] thick：物理管道(>20) vs 信息箭头(<=20)
    fields: list[str]
    points: list[tuple[float, float]]

    @property
    def is_physical_flow(self) -> bool:
        return self.thick >= FLOW_THICK_THRESHOLD


@dataclasses.dataclass
class View:
    index: int
    name: str
    start: int
    end: int
    objects: dict[int, Obj]
    arrows: list[Arrow]


# ---------------------------------------------------------------------------
# 文本读写
# ---------------------------------------------------------------------------


def _safe_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_text(path: Path) -> str:
    return MdlDocument.read(path).semantic_text


def _line_body(line: str) -> str:
    return line.rstrip("\r\n")


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


# ---------------------------------------------------------------------------
# 控制点解析与格式化
# ---------------------------------------------------------------------------

_POINT_RE = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")


def split_arrow_record(body: str):
    # font 可以含竖线；不能用第一个 | 分割箭头记录。
    match = re.search(r",(\d+)\|(?=\(|$)", body)
    if match is None:
        raise ValueError("箭头记录缺少有效 np|pointlist")
    return body[: match.end() - 1].split(","), body[match.end() :]


def parse_points(text: str) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in _POINT_RE.findall(text)]


def format_points(points: Iterable[tuple[float, float]]) -> str:
    # Vensim 控制点格式：(x,y)|  每个点一对括号加一个竖线
    return "".join(f"({int(round(x))},{int(round(y))})|" for x, y in points)


# ---------------------------------------------------------------------------
# 草图解析
# ---------------------------------------------------------------------------


def parse_views(lines: list[str], stock_names: set | None = None) -> list[View]:
    marker_positions = [
        i for i, line in enumerate(lines) if _line_body(line).startswith(SKETCH_MARKER)
    ]
    if not marker_positions:
        raise ValueError(r"No Vensim Sketch Information marker found. Expected \\---///.")
    if stock_names is None:
        stock_names = set()

    views: list[View] = []
    for view_index, marker in enumerate(marker_positions):
        end = (
            marker_positions[view_index + 1]
            if view_index + 1 < len(marker_positions)
            else len(lines)
        )
        name = f"View_{view_index + 1}"
        for i in range(marker + 1, min(marker + 5, end)):
            body = _line_body(lines[i])
            if body.startswith("*"):
                name = body[1:].strip() or name
                break

        objects: dict[int, Obj] = {}
        arrows: list[Arrow] = []
        seen_ids = set()
        continuation = False
        for line_index in range(marker + 1, end):
            body = _line_body(lines[line_index])
            if continuation:
                continuation = False
                continue
            if body.startswith("///---"):
                break
            if not body or body.startswith(("V300", "*", "$", "}")):
                continue
            first = body.split(",", 1)[0]
            if not first.lstrip("-").isdigit():
                continue
            kind = _safe_int(first, -1)
            if kind in OBJECT_TYPES | {T_ARROW}:
                record_id = _safe_int(body.split(",", 2)[1], -1)
                if record_id in seen_ids:
                    raise ValueError(f"视图 {name}: 对象 ID {record_id} 重复")
                seen_ids.add(record_id)

            if kind == T_ARROW:
                # 1,id,from,to,shape,...,np|(x,y)|...
                fields, point_tail = split_arrow_record(body)
                if len(fields) < 14:
                    raise ValueError(f"视图 {name}: 箭头 {record_id} 字段不足")
                if len(parse_points(point_tail)) < _safe_int(fields[-1]):
                    raise ValueError(f"视图 {name}: 箭头 {record_id} 控制点数量不符")
                arrows.append(
                    Arrow(
                        view_index=view_index,
                        line_index=line_index,
                        obj_id=_safe_int(fields[1]),
                        from_id=_safe_int(fields[2]),
                        to_id=_safe_int(fields[3]),
                        shape=_safe_int(fields[4]),
                        thick=_safe_int(fields[7]) if len(fields) > 7 else 0,
                        fields=fields,
                        points=parse_points(point_tail)[: _safe_int(fields[-1])],
                    )
                )
            elif kind in OBJECT_TYPES:
                # 10/11/12,id,name,x,y,w,h,shape,bits,...
                fields = re.split(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", body)
                if len(fields) < 9:
                    continue
                objects[_safe_int(fields[1])] = Obj(
                    view_index=view_index,
                    line_index=line_index,
                    kind=kind,
                    obj_id=_safe_int(fields[1]),
                    name=fields[2].strip('"'),
                    x=_safe_float(fields[3]),
                    y=_safe_float(fields[4]),
                    w=_safe_float(fields[5]),
                    h=_safe_float(fields[6]),
                    shape=_safe_int(fields[7]),
                    bits=_safe_int(fields[8]),
                    raw_fields=fields,
                    stock_names=stock_names,
                )
                continuation = bool(_safe_int(fields[8]) & (4 | 8)) or kind in T_OTHER
        views.append(View(view_index, name, marker, end, objects, arrows))
    return views


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _looks_like_business_variable(name: str) -> bool:
    if name in {"INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER", "Time"}:
        return False
    return bool(name.strip())


def _segments_intersect(a, b, c, d) -> bool:
    """判断两条线段是否相交；共享端点的相邻箭头不计为交叉。"""

    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    if a in (c, d) or b in (c, d):
        return False
    o1 = orient(a, b, c)
    o2 = orient(a, b, d)
    o3 = orient(c, d, a)
    o4 = orient(c, d, b)
    return o1 * o2 < 0 and o3 * o4 < 0


def _boxes_overlap(left: Obj, right: Obj, padding: float = 8.0) -> bool:
    """按 Vensim 的半宽/半高字段检查变量文字框是否互相覆盖。"""
    return (
        abs(left.x - right.x) < left.w + right.w + padding
        and abs(left.y - right.y) < left.h + right.h + padding
    )


def _arrow_polyline(view: View, arrow: Arrow) -> list[tuple[float, float]]:
    return arrow_path(view, arrow)


def load_mdl(path: Path) -> tuple[list[str], list[View]]:
    document = MdlDocument.read(path)
    lines = document.text.splitlines(keepends=True)
    stock_names = parse_stock_names(document.semantic_text)
    return lines, parse_views(lines, stock_names=stock_names)


# ---------------------------------------------------------------------------
# 节点选择与 Graphviz 布局
# ---------------------------------------------------------------------------


def eligible_movable_nodes(view: View, config: dict) -> dict[int, Obj]:
    """选出可被自动布局移动的普通辅助变量。"""
    lock_names = {str(n) for n in config.get("lock_node_names", [])}
    lock_ids = {int(x) for x in config.get("lock_object_ids", [])}
    move_stocks = bool(config.get("move_stocks", False))
    # 任何实体管道、未知路由或附着注释的端点保持原位。
    for arrow in view.arrows:
        if not _is_information_arrow(arrow, view.objects):
            lock_ids.update((arrow.from_id, arrow.to_id))
    selected: dict[int, Obj] = {}
    for obj in view.objects.values():
        if obj.kind != T_VARIABLE:
            # 阀门(11)、源汇云(12)、其他(30/31)一律不动
            continue
        if obj.obj_id in lock_ids or obj.name in lock_names:
            continue
        if (obj.is_shadow and not config.get("move_shadows", True)) or (
            len(obj.raw_fields) > 9 and _safe_int(obj.raw_fields[9]) != 0
        ):
            continue
        if obj.attached_to_valve:
            # 流率标签附着在阀门上，移动会脱离管道
            continue
        if obj.stock_like and not obj.is_shadow and not move_stocks:
            # 库存默认锁定
            continue
        selected[obj.obj_id] = obj
    return selected


def graph_nodes_for_layout(view: View, movable: dict[int, Obj]) -> dict[int, Obj]:
    """参与 Graphviz 布局计算的节点：所有变量+阀门，边只用信息箭头。

    物理流率管道会主导图结构并压扁辅助变量层级，因此布局只用信息箭头作为约束，
    但阀门节点仍参与计算以便辅助变量相对阀门定位。
    """
    nodes: dict[int, Obj] = {}
    for obj_id, obj in view.objects.items():
        if obj.kind in (T_VARIABLE, T_VALVE):
            nodes[obj_id] = obj
    return nodes


def _quote_dot(identifier: str) -> str:
    escaped = (
        identifier.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", " ")
        .replace("\n", "\\n")
        .replace("\t", " ")
    )
    return '"' + escaped + '"'


def _dot_label(label: str) -> str:
    cleaned = "".join(ch if ch >= " " else " " for ch in label)
    if len(cleaned) > MAX_DOT_LABEL_CHARS:
        cleaned = cleaned[:MAX_DOT_LABEL_CHARS] + "..."
    return cleaned


def graphviz_positions(
    view: View,
    movable: dict[int, Obj],
    config: dict,
    engine: str,
) -> dict[int, tuple[float, float]]:
    if engine not in {"dot", "neato", "fdp", "sfdp"}:
        raise ValueError("不支持的 Graphviz 引擎")
    if shutil.which(engine) is None:
        raise RuntimeError(
            f"Graphviz 可执行文件 '{engine}' 未找到。请安装 Graphviz 并将其加入 PATH。"
        )

    nodes = graph_nodes_for_layout(view, movable)
    if not nodes:
        return {}

    rankdir = str(config.get("rankdir", "LR")).upper()
    if rankdir not in ALLOWED_RANKDIR:
        raise ValueError(f"rankdir 仅支持: {', '.join(sorted(ALLOWED_RANKDIR))}")
    nodesep = float(config.get("nodesep", 0.65))
    ranksep = float(config.get("ranksep", 1.05))
    if len(nodes) > MAX_GRAPHVIZ_NODES:
        raise RuntimeError(f"Graphviz 节点过多: {len(nodes)} > {MAX_GRAPHVIZ_NODES}")
    edge_count = sum(
        1
        for arrow in view.arrows
        if not arrow.is_physical_flow and arrow.from_id in nodes and arrow.to_id in nodes
    )
    if edge_count > MAX_GRAPHVIZ_EDGES:
        raise RuntimeError(f"Graphviz 边过多: {edge_count} > {MAX_GRAPHVIZ_EDGES}")

    dot_lines = [
        "digraph G {",
        f"graph [rankdir={rankdir}, nodesep={nodesep}, ranksep={ranksep}, "
        "splines=true, overlap=false, start=42];",
        "node [shape=box, width=1.1, height=0.4, fixedsize=false];",
    ]
    for obj_id, obj in nodes.items():
        if obj.kind == T_VARIABLE:
            label = _dot_label(obj.name) or f"var{obj_id}"
        else:
            label = f"valve_{obj_id}"
        dot_lines.append(
            f"{_quote_dot(f'n{obj_id}')} [label={_quote_dot(label)}, "
            f"width={max(0.4, 2 * obj.w / 72):.3f}, height={max(0.3, 2 * obj.h / 72):.3f}];"
        )

    # 只用信息箭头作为布局约束，避免物理管道压扁层级
    for arrow in view.arrows:
        if arrow.is_physical_flow:
            continue
        if arrow.from_id in nodes and arrow.to_id in nodes:
            dot_lines.append(
                f"{_quote_dot(f'n{arrow.from_id}')} -> {_quote_dot(f'n{arrow.to_id}')} [weight=4];"
            )
    dot_lines.append("}")

    # engine 由 argparse choices 限定为 dot/neato/fdp/sfdp，且 shell=False。
    try:
        result = subprocess.run(  # nosec B603
            [engine, "-Tplain"],
            input="\n".join(dot_lines),
            capture_output=True,
            text=True,
            check=False,
            timeout=GRAPHVIZ_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Graphviz 运行超时: {GRAPHVIZ_TIMEOUT_SECONDS} 秒") from exc
    if result.returncode != 0:
        raise RuntimeError(f"Graphviz 运行失败: {result.stderr.strip()}")

    positions: dict[int, tuple[float, float]] = {}
    for line in result.stdout.splitlines():
        if not line.startswith("node "):
            continue
        parts = shlex.split(line)
        if len(parts) < 4 or not parts[1].startswith("n"):
            continue
        obj_id = _safe_int(parts[1][1:], -1)
        positions[obj_id] = (_safe_float(parts[2]), _safe_float(parts[3]))
    return positions


# ---------------------------------------------------------------------------
# 回写
# ---------------------------------------------------------------------------


def update_obj_line(line: str, x: float, y: float) -> str:
    ending = _line_ending(line)
    fields = re.split(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", _line_body(line))
    fields[3] = str(int(round(x)))
    fields[4] = str(int(round(y)))
    return ",".join(fields) + ending


def restyle_arrow_line(line: str, config: dict) -> str:
    """颜色与几何分别处理，未知路由只统一显式颜色而不改变形状。"""
    if config.get("style", "preserve") == "preserve":
        return line
    fields, tail = split_arrow_record(_line_body(line))
    if len(fields) < 14:
        return line
    fields[8] = str(_safe_int(fields[8]) | 1)
    fields[11] = config.get(
        "information_arrow_color", "0-0-255" if config.get("style") == "native-blue" else "0-0-0"
    )
    return ",".join(fields) + "|" + tail + _line_ending(line)


def update_arrow_line(
    line: str,
    point: tuple[float, float],
    config: dict | None = None,
) -> str:
    """只改圆弧几何与明确选择的颜色；极性、延迟、字体和隐藏级别原样保留。"""
    ending = _line_ending(line)
    body = _line_body(line)
    try:
        fields, tail = split_arrow_record(body)
    except ValueError:
        return line
    if len(fields) < 14:
        # 极旧版本记录字段不足时不猜测其余字段，避免破坏未知格式。
        return line
    # 官方字段：shape,hid,pol,thick,hasf,dtype,res,color,font,np。
    fields[4] = "1"  # 普通 Arrow + 控制点 = 原生圆弧
    preset = (config or {}).get("style", "preserve")
    # academic 保留为旧配置的兼容别名，新方案统一使用黑色。
    arrow_color = (config or {}).get(
        "information_arrow_color", "0-0-255" if preset == "native-blue" else "0-0-0"
    )
    if preset != "preserve":
        fields[8] = str(_safe_int(fields[8]) | 1)  # 官方 hasf 第一位启用箭头自定义颜色。
        fields[11] = str(arrow_color)
    # 末字段为 np（控制点个数），普通 Arrow 圆弧 = 1 个中间点
    old_count = _safe_int(fields[-1])
    suffix_offset = 0
    for match in list(_POINT_RE.finditer(tail))[:old_count]:
        suffix_offset = match.end()
        if tail[suffix_offset : suffix_offset + 1] == "|":
            suffix_offset += 1
    fields[-1] = "1"
    return ",".join(fields) + "|" + format_points([point]) + tail[suffix_offset:] + ending


# ---------------------------------------------------------------------------
# 视图选择与箭头布线
# ---------------------------------------------------------------------------


def choose_views(views: list[View], config: dict) -> list[View]:
    requested = str(config.get("view", "*")).strip()
    skip = {str(v) for v in config.get("skip_views", [])}
    chosen = []
    for view in views:
        if view.name in skip:
            continue
        if requested == "*" or requested == view.name:
            chosen.append(view)
    return chosen


def _is_information_arrow(arrow: Arrow, objects: dict[int, Obj]) -> bool:
    """判定是否为可重布线的信息箭头：细线、两端均为变量节点、且原本为普通 Arrow。

    保守判据：仅当原箭头控制点个数 <= 1 时才视为普通 Arrow，可写入单控制点圆弧。
    Polyline(多控制点)、Spline、Perpendicular 等多控制点箭头保持原样不动，
    避免破坏其原有路由类型（Vensim Arrow Class 文档：不同箭头类型行为不同）。
    """
    if arrow.is_physical_flow or arrow.shape not in (0, 1):
        return False
    src = objects.get(arrow.from_id)
    dst = objects.get(arrow.to_id)
    if src is None or dst is None:
        return False
    # 只处理 变量→变量 或 变量→阀门 的信息影响；阀门→阀门/云 不动
    if src.kind not in (T_VARIABLE, T_VALVE):
        return False
    if dst.kind not in (T_VARIABLE, T_VALVE):
        return False
    # 仅普通 Arrow(0 或 1 个控制点)可重写为单控制点圆弧；
    # 多控制点箭头(Polyline/Spline/Perpendicular)保持原样
    if len(arrow.points) > 1 or arrow.from_id == arrow.to_id or _safe_int(arrow.fields[5]) != 0:
        return False
    return True


def route_arrows(
    lines: list[str],
    view: View,
    new_positions: dict[int, tuple[float, float]],
    config: dict,
) -> int:
    from sketch_layout import route_view

    return route_view(lines, view, new_positions, config)


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------


def command_inspect(path: Path) -> int:
    _, views = load_mdl(path)
    print(f"MODEL: {path}")
    for view in views:
        print(f"\nVIEW {view.index + 1}: {view.name}")
        print("Objects:")
        for obj in sorted(view.objects.values(), key=lambda o: o.obj_id):
            kind_name = {10: "var", 11: "valve", 12: "src/sink", 30: "other", 31: "other"}.get(
                obj.kind, str(obj.kind)
            )
            print(
                f"  id={obj.obj_id:<4} {kind_name:<8} name={obj.name!r:<32} "
                f"xy=({int(obj.x)},{int(obj.y)}) shape={obj.shape} bits={obj.bits} "
                f"attached={obj.attached_to_valve} shadow={obj.is_shadow} stock_like={obj.stock_like}"
            )
        print("Arrows:")
        for arrow in sorted(view.arrows, key=lambda a: a.obj_id):
            flow = "FLOW" if arrow.is_physical_flow else "info"
            print(
                f"  id={arrow.obj_id:<4} {arrow.from_id} -> {arrow.to_id} "
                f"{flow:<4} shape={arrow.shape} thick={arrow.thick} points={len(arrow.points)}"
            )
    return 0


def command_audit(path: Path) -> int:
    from vensim_engine import extract_deps, parse_equations

    _, views = load_mdl(path)
    equations = parse_equations(_read_text(path), expand=False)
    errors = 0
    print(f"AUDIT: {path}")
    for view in views:
        metrics = measure_view(view)
        errors += (
            len(metrics["broken_arrows"])
            + len(metrics["shadow_inputs"])
            + len(metrics["duplicate_defined"])
        )
        print(
            f"VIEW {view.name}: 断链 {len(metrics['broken_arrows'])}，入影子变量 {len(metrics['shadow_inputs'])}，"
            f"文字重叠 {len(metrics['node_overlaps'])}，穿字 {len(metrics['arrow_node_collisions'])}，"
            f"交叉 {len(metrics['arrow_crossings']) + len(metrics['flow_crossings'])}"
        )
        for arrow in view.arrows:
            if arrow.is_physical_flow:
                continue
            source, target = view.objects.get(arrow.from_id), view.objects.get(arrow.to_id)
            if not source or not target or source.kind != T_VARIABLE or target.kind != T_VARIABLE:
                continue
            if source.name in equations and target.name in equations:
                equation = equations[target.name]
                expression = (
                    (equation.integ_flow or equation.rhs) + " " + (equation.integ_init_expr or "")
                )
                if source.name not in extract_deps(expression, set(equations)):
                    print(
                        f"  WARNING arrow {arrow.obj_id}: {source.name} -> {target.name} 未在目标方程中找到直接依赖，请核对因果含义"
                    )
        for collision in metrics["arrow_node_collisions"]:
            print(
                f"  WARNING arrow {collision['arrow']}: 穿过 object {collision['object']} 的文字/形状范围"
            )
        if metrics["unsupported_arrows"]:
            print(
                f"  WARNING 未完整覆盖的箭头类型: {metrics['unsupported_arrows']}，保留原记录并在 Vensim 核对"
            )
        for item in metrics["duplicate_defined"]:
            print(f"  ERROR 同一 View 重复 Defined: {item['variable']}，对象 {item['ids']}")
    print(
        "FAIL: 草图结构错误"
        if errors
        else "PASS: 已解析草图引用；几何警告请用 visual --strict 复核"
    )
    print("布局只整理图形，因果边的增删与正负极性需要方程或领域依据。")
    return 1 if errors else 0


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("布局配置必须是 JSON 对象")
    if config.get("layout_mode", "refine") not in {
        "auto",
        "preserve",
        "refine",
        "graphviz",
        "circular",
    }:
        raise ValueError("layout_mode 必须是 auto/preserve/refine/graphviz/circular")
    if config.get("style", "preserve") not in {"academic", "monochrome", "native-blue", "preserve"}:
        raise ValueError("style 必须是 monochrome/native-blue/preserve")
    for key in (
        "clearance",
        "node_spacing",
        "minimum_curve_pixels",
        "maximum_curve_pixels",
        "curve_strength",
        "graphviz_scale",
        "nodesep",
        "ranksep",
        "circular_gap",
        "circular_aspect",
    ):
        if key in config and (
            isinstance(config[key], bool)
            or not isinstance(config[key], (int, float))
            or not math.isfinite(config[key])
            or config[key] <= 0
        ):
            raise ValueError(f"{key} 必须是有限正数")
    if type(config.get("routing_passes", 2)) is not int or config.get(
        "routing_passes", 2
    ) not in range(1, 6):
        raise ValueError("routing_passes 必须为 1 到 5")
    for key in ("move_stocks", "move_shadows"):
        if key in config and not isinstance(config[key], bool):
            raise ValueError(f"{key} 必须是布尔值")
    if (
        type(config.get("max_allowed_crossings", 0)) is not int
        or config.get("max_allowed_crossings", 0) < 0
    ):
        raise ValueError("max_allowed_crossings 必须是非负整数")
    anchors = config.get("node_positions", {})
    if not isinstance(anchors, dict) or any(
        not isinstance(p, list)
        or len(p) != 2
        or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
            for v in p
        )
        for p in anchors.values()
    ):
        raise ValueError("node_positions 必须为变量名到 [x, y] 非负有限坐标的映射")
    if "information_arrow_color" in config:
        expected = {"monochrome": "0-0-0", "academic": "0-0-0", "native-blue": "0-0-255"}.get(
            config.get("style")
        )
        if expected is None or config["information_arrow_color"] != expected:
            raise ValueError(
                "信息箭头颜色必须与明确的黑色/纯蓝 style 一致；preserve 保留原生设置，不接收颜色覆盖"
            )
    return config


def command_layout(
    path: Path,
    output: Path,
    config_path: Path | None = None,
    engine: str = "dot",
    route_information_arrows: bool = True,
    mode=None,
    style=None,
    preview=None,
) -> int:
    from sketch_layout import optimize_view

    separate_output(output, [path, *([config_path] if config_path else [])])
    if output.suffix.lower() != ".mdl":
        raise ValueError("布局输出必须是 .mdl")
    config = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path else {}
    if mode:
        config["layout_mode"] = mode
    if style:
        config["style"] = style
    validate_config(config)
    document = MdlDocument.read(path)
    lines, views = load_mdl(path)
    selected = choose_views(views, config)
    if not selected:
        raise ValueError("未匹配到视图，请检查 view/skip_views")
    missing = set(config.get("node_positions", {})) - {
        obj.name for view in selected for obj in view.objects.values()
    }
    if missing:
        raise ValueError("锚点变量未出现在所选视图: " + ", ".join(sorted(missing)))
    if any(
        len(v.objects) > MAX_GRAPHVIZ_NODES or len(v.arrows) > MAX_GRAPHVIZ_EDGES for v in selected
    ):
        raise ValueError("草图超过布局规模上限，请先按子系统拆分视图")
    report_path = output.with_suffix(output.suffix + ".layout_report.json")
    preflight_outputs(
        [output, report_path, *([preview] if preview else [])],
        [path, *([config_path] if config_path else [])],
    )
    if preview:
        separate_output(
            preview, [path, output, report_path, *([config_path] if config_path else [])]
        )
        if preview.suffix.lower() not in {".svg", ".html"}:
            raise ValueError("预览输出必须是 .html 或 .svg")
    report = {
        "input": str(path),
        "output": str(output),
        "encoding": document.encoding,
        "equation_sha256": hashlib.sha256(document.equation_bytes).hexdigest(),
        "native_verified": False,
        "views": [],
    }
    for view in selected:
        item = optimize_view(lines, view, config, engine, route_information_arrows)
        item["view"] = view.name
        item["pass"] = quality_pass(item["after"], config.get("max_allowed_crossings", 0))
        report["views"].append(item)
    data = document.encode(lines)
    updated = parse_views(lines, parse_stock_names(document.semantic_text))
    for old, new in zip(views, updated, strict=False):
        if set(old.objects) != set(new.objects) or [
            (a.obj_id, a.from_id, a.to_id) for a in old.arrows
        ] != [(a.obj_id, a.from_id, a.to_id) for a in new.arrows]:
            raise ValueError("草图结构不变量校验失败，已拒绝写入")
        for left, right in zip(old.arrows, new.arrows, strict=False):
            if any(left.fields[i] != right.fields[i] for i in (5, 6, 9, 12)):
                raise ValueError("箭头极性、延迟、隐藏或字体改变，已拒绝写入")
    report["equations_preserved"] = True
    report["topology_preserved"] = True
    report["pass"] = all(item["pass"] for item in report["views"])
    atomic_write(output, data)
    atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    if preview:
        from sketch_preview import render_preview

        render_preview(output, preview, path if preview.suffix.lower() == ".html" else None)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("NEXT: 在 Vensim 中检查草图、Check Model 和 Units Check。")
    return 0


def command_visual(path, output=None, strict=False, max_crossings=0):
    _, views = load_mdl(path)
    records = [{"view": view.name, **measure_view(view)} for view in views]
    report = {
        "model": str(path),
        "views": records,
        "pass": all(quality_pass(record, max_crossings) for record in records),
        "native_verified": False,
    }
    data = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        separate_output(output, [path])
        atomic_write(output, data.encode("utf-8"))
    print(data)
    return 1 if strict and not report["pass"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="列出草图对象与箭头。")
    p_inspect.add_argument("model", type=Path)

    p_audit = sub.add_parser("audit", help="审计箭头对象引用。")
    p_audit.add_argument("model", type=Path)

    p_layout = sub.add_parser("layout", help="应用保守的 Graphviz 辅助布局与信息箭头弧线。")
    p_layout.add_argument("model", type=Path)
    p_layout.add_argument("--output", required=True, type=Path)
    p_layout.add_argument("--config", type=Path)
    p_layout.add_argument("--engine", default="dot", choices=["dot", "neato", "fdp", "sfdp"])
    p_layout.add_argument(
        "--route-information-arrows",
        "--route",
        action="store_true",
        default=True,
        help="为可重布线的信息箭头设置单个圆弧控制点。",
    )
    p_layout.add_argument("--mode", choices=["auto", "preserve", "refine", "graphviz", "circular"])
    p_layout.add_argument("--style", choices=["academic", "monochrome", "native-blue", "preserve"])
    p_layout.add_argument("--preview", type=Path)
    p_visual = sub.add_parser("visual", help="圆弧碰撞报告，可作为严格质量检查")
    p_visual.add_argument("model", type=Path)
    p_visual.add_argument("--output", type=Path)
    p_visual.add_argument("--strict", action="store_true")
    p_visual.add_argument("--max-crossings", type=int, default=0)
    p_preview = sub.add_parser("preview", help="导出草图 SVG 或前后比较 HTML")
    p_preview.add_argument("model", type=Path)
    p_preview.add_argument("--output", type=Path, required=True)
    p_preview.add_argument("--compare-with", type=Path)
    p_preview.add_argument("--show-ids", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.model.exists():
        parser.error(f"模型文件未找到: {args.model}")
    if args.command == "preview":
        from sketch_preview import render_preview

        print(json.dumps(render_preview(args.model, args.output, args.compare_with, args.show_ids)))
        return 0
    if args.command == "visual":
        return command_visual(args.model, args.output, args.strict, args.max_crossings)
    if args.command == "inspect":
        return command_inspect(args.model)
    if args.command == "audit":
        return command_audit(args.model)
    if args.command == "layout":
        if args.config and not args.config.exists():
            parser.error(f"配置文件未找到: {args.config}")
        return command_layout(
            args.model,
            args.output,
            args.config,
            args.engine,
            args.route_information_arrows,
            args.mode,
            args.style,
            args.preview,
        )
    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
