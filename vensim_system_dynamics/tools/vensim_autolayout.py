#!/usr/bin/env python3
"""Vensim .mdl 草图检查、审计与保守自动排版工具。

依据 Vensim 官方 Sketch Format / Sketch Object Detail / Arrow Class 文档实现：
  - 解析草图对象(10 变量 / 11 阀门 / 12 源汇云图注释 / 1 箭头)的真实字段；
  - 区分物理流率管道(field thick >= FLOW_THICK_THRESHOLD 或端点为阀门/云)与信息箭头；
  - 用 Graphviz 计算可移动辅助变量坐标，库存/阀门/云/流率标签默认锁定；
  - 只为信息箭头生成单个中间控制点，使普通 Arrow 显示为平滑圆弧；
  - 平行边自动错开曲率，远距离边增大弧度；
  - 不改方程区，不新建/删除对象，不改箭头 from/to，自动生成 *_backup.mdl。

输出必须在 Vensim 中重新打开并运行 Check Model 与 Units Check 后方可使用。
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import re
import shlex
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# 草图起始标记（.mdl 中写作 \\\---///）
SKETCH_MARKER = r"\\\---///"

# 方程区 INTEG 正则：匹配 "变量名 = INTEG ( ..., 初值 )"
_INTEG_EQ = re.compile(r"^\s*([A-Za-z_][\w\$\s]*?)\s*=\s*INTEG\s*\(", re.I)


def parse_stock_names(mdl_text: str) -> set:
    """从方程区解析所有库存变量名（含 INTEG 的方程左侧变量）。

    用于替代单纯依赖图形形状识别库存：先从方程语义确定库存名，
    再在 Sketch 中按名定位对象并锁定。
    """
    sketch_pos = mdl_text.find(SKETCH_MARKER)
    body = mdl_text[:sketch_pos] if sketch_pos >= 0 else mdl_text
    stocks: set = set()
    for line in body.splitlines():
        m = _INTEG_EQ.match(line)
        if m:
            stocks.add(m.group(1).strip())
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
# thick >= 此阈值视为物理流率管道(粗管)，< 视为信息箭头(细线)。
FLOW_THICK_THRESHOLD = 20

# shape 字段位标志：低 5 位为形状码；bit6(1<<5=32)=附着到阀门；bit7=形状由类型决定
SHAPE_ATTACHED_TO_VALVE = 32
SHAPE_MASK = 31

# bits 字段(field[8])，官方称为 arrows_in_allowed：
#   bit1(1)=允许入箭头, bit2(2)=允许出箭头, bit3(4)=有注释续行,
#   bit4(8)=IO 对象, bit7(128)=因果不穿透, bit8(256)=用户设定尺寸
# PySD 的 sketch.peg 用 arrows_in_allowed 偶数判定 shadow variable：
#   偶数 → shadow variable(跨视图引用，无入箭头)，不自动移动；
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
    w: float          # 半宽
    h: float          # 半高
    shape: int
    bits: int
    raw_fields: List[str]
    stock_names: set = dataclasses.field(default_factory=set)

    @property
    def attached_to_valve(self) -> bool:
        # bit6 置位表示该 word 附着到阀门（或阀门附着到另一 word）
        return bool(self.shape & SHAPE_ATTACHED_TO_VALVE)

    @property
    def is_shadow(self) -> bool:
        # bits 为偶数 → shadow variable，跨视图引用，不在本视图移动
        return self.kind == T_VARIABLE and self.bits % 2 == 0

    @property
    def stock_like(self) -> bool:
        # 优先用方程语义：变量名出现在 INTEG 方程左侧则为库存。
        # 退化为图形形状启发式：boxed 形状码 3 作为保守兜底。
        if self.kind != T_VARIABLE:
            return False
        if self.name and self.name in self.stock_names:
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
    thick: int          # field[7] thick：物理管道(>=20) vs 信息箭头(<20)
    fields: List[str]
    points: List[Tuple[float, float]]

    @property
    def is_physical_flow(self) -> bool:
        return self.thick >= FLOW_THICK_THRESHOLD


@dataclasses.dataclass
class View:
    index: int
    name: str
    start: int
    end: int
    objects: Dict[int, Obj]
    arrows: List[Arrow]


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
    # 兼容 Windows UTF-8 BOM 与中文 GB 编码遗留文件
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Cannot decode file: {path}")


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

_POINT_RE = re.compile(r"\((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\)")


def parse_points(text: str) -> List[Tuple[float, float]]:
    return [(float(x), float(y)) for x, y in _POINT_RE.findall(text)]


def format_points(points: Iterable[Tuple[float, float]]) -> str:
    # Vensim 控制点格式：(x,y)|  每个点一对括号加一个竖线
    return "".join(f"({int(round(x))},{int(round(y))})|" for x, y in points)


# ---------------------------------------------------------------------------
# 草图解析
# ---------------------------------------------------------------------------

def parse_views(lines: List[str], stock_names: Optional[set] = None) -> List[View]:
    marker_positions = [
        i for i, line in enumerate(lines)
        if _line_body(line).startswith(SKETCH_MARKER)
    ]
    if not marker_positions:
        raise ValueError(r"No Vensim Sketch Information marker found. Expected \\---///.")
    if stock_names is None:
        stock_names = set()

    views: List[View] = []
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

        objects: Dict[int, Obj] = {}
        arrows: List[Arrow] = []
        for line_index in range(marker + 1, end):
            body = _line_body(lines[line_index])
            if not body or body.startswith(("V300", "*", "$", "}")):
                continue
            first = body.split(",", 1)[0]
            if not first.lstrip("-").isdigit():
                continue
            kind = _safe_int(first, -1)

            if kind == T_ARROW:
                # 1,id,from,to,shape,...,np|(x,y)|...
                prefix, sep, point_tail = body.partition("|")
                if not sep:
                    continue
                fields = prefix.split(",")
                if len(fields) < 8:
                    continue
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
                        points=parse_points(point_tail),
                    )
                )
            elif kind in OBJECT_TYPES:
                # 10/11/12,id,name,x,y,w,h,shape,bits,...
                fields = body.split(",")
                if len(fields) < 9:
                    continue
                objects[_safe_int(fields[1])] = Obj(
                    view_index=view_index,
                    line_index=line_index,
                    kind=kind,
                    obj_id=_safe_int(fields[1]),
                    name=fields[2],
                    x=_safe_float(fields[3]),
                    y=_safe_float(fields[4]),
                    w=_safe_float(fields[5]),
                    h=_safe_float(fields[6]),
                    shape=_safe_int(fields[7]),
                    bits=_safe_int(fields[8]),
                    raw_fields=fields,
                    stock_names=stock_names,
                )
        views.append(View(view_index, name, marker, end, objects, arrows))
    return views


def load_mdl(path: Path) -> Tuple[List[str], List[View]]:
    text = _read_text(path)
    lines = text.splitlines(keepends=True)
    stock_names = parse_stock_names(text)
    return lines, parse_views(lines, stock_names=stock_names)


# ---------------------------------------------------------------------------
# 节点选择与 Graphviz 布局
# ---------------------------------------------------------------------------

def eligible_movable_nodes(view: View, config: dict) -> Dict[int, Obj]:
    """选出可被自动布局移动的普通辅助变量。"""
    lock_names = {str(n) for n in config.get("lock_node_names", [])}
    lock_ids = {int(x) for x in config.get("lock_object_ids", [])}
    move_stocks = bool(config.get("move_stocks", False))
    selected: Dict[int, Obj] = {}
    for obj in view.objects.values():
        if obj.kind != T_VARIABLE:
            # 阀门(11)、源汇云(12)、其他(30/31)一律不动
            continue
        if obj.obj_id in lock_ids or obj.name in lock_names:
            continue
        if obj.is_shadow:
            # shadow variable 是跨视图引用占位，不动
            continue
        if obj.attached_to_valve:
            # 流率标签附着在阀门上，移动会脱离管道
            continue
        if obj.stock_like and not move_stocks:
            # 库存默认锁定
            continue
        selected[obj.obj_id] = obj
    return selected


def graph_nodes_for_layout(view: View, movable: Dict[int, Obj]) -> Dict[int, Obj]:
    """参与 Graphviz 布局计算的节点：所有变量+阀门，边只用信息箭头。

    物理流率管道会主导图结构并压扁辅助变量层级，因此布局只用信息箭头作为约束，
    但阀门节点仍参与计算以便辅助变量相对阀门定位。
    """
    nodes: Dict[int, Obj] = {}
    for obj_id, obj in view.objects.items():
        if obj.kind in (T_VARIABLE, T_VALVE):
            nodes[obj_id] = obj
    return nodes


def _quote_dot(identifier: str) -> str:
    return '"' + identifier.replace('"', '\\"') + '"'


def graphviz_positions(
    view: View,
    movable: Dict[int, Obj],
    config: dict,
    engine: str,
) -> Dict[int, Tuple[float, float]]:
    if shutil.which(engine) is None:
        raise RuntimeError(
            f"Graphviz 可执行文件 '{engine}' 未找到。请安装 Graphviz 并将其加入 PATH。"
        )

    nodes = graph_nodes_for_layout(view, movable)
    if not nodes:
        return {}

    rankdir = str(config.get("rankdir", "LR"))
    nodesep = float(config.get("nodesep", 0.65))
    ranksep = float(config.get("ranksep", 1.05))

    dot_lines = [
        "digraph G {",
        f"graph [rankdir={rankdir}, nodesep={nodesep}, ranksep={ranksep}, "
        f"splines=true, overlap=false];",
        "node [shape=box, width=1.1, height=0.4, fixedsize=false];",
    ]
    for obj_id, obj in nodes.items():
        if obj.kind == T_VARIABLE:
            label = obj.name.replace('"', "'") or f"var{obj_id}"
        else:
            label = f"valve_{obj_id}"
        dot_lines.append(f"{_quote_dot(f'n{obj_id}')} [label={_quote_dot(label)}];")

    # 只用信息箭头作为布局约束，避免物理管道压扁层级
    for arrow in view.arrows:
        if arrow.is_physical_flow:
            continue
        if arrow.from_id in nodes and arrow.to_id in nodes:
            dot_lines.append(
                f"{_quote_dot(f'n{arrow.from_id}')} -> "
                f"{_quote_dot(f'n{arrow.to_id}')} [weight=4];"
            )
    dot_lines.append("}")

    result = subprocess.run(
        [engine, "-Tplain"],
        input="\n".join(dot_lines),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Graphviz 运行失败: {result.stderr.strip()}")

    positions: Dict[int, Tuple[float, float]] = {}
    for line in result.stdout.splitlines():
        if not line.startswith("node "):
            continue
        parts = shlex.split(line)
        if len(parts) < 4 or not parts[1].startswith("n"):
            continue
        obj_id = _safe_int(parts[1][1:], -1)
        positions[obj_id] = (_safe_float(parts[2]), _safe_float(parts[3]))
    return positions


def scale_positions(
    positions: Dict[int, Tuple[float, float]],
    movable: Dict[int, Obj],
    config: dict,
) -> Dict[int, Tuple[float, float]]:
    """把 Graphviz 坐标(左下原点,y向上)映射到 Vensim 画布(左上原点,y向下)。"""
    usable = {oid: pos for oid, pos in positions.items() if oid in movable}
    if not usable:
        return {}

    canvas = config.get("canvas", {})
    x_min = float(canvas.get("x_min", 120))
    x_max = float(canvas.get("x_max", 1180))
    y_min = float(canvas.get("y_min", 110))
    y_max = float(canvas.get("y_max", 720))

    px = [p[0] for p in usable.values()]
    py = [p[1] for p in usable.values()]
    sx_min, sx_max = min(px), max(px)
    sy_min, sy_max = min(py), max(py)
    dx = sx_max - sx_min
    dy = sy_max - sy_min

    out: Dict[int, Tuple[float, float]] = {}
    for obj_id, (x, y) in usable.items():
        nx = (x_min + x_max) / 2 if abs(dx) < 1e-9 else x_min + (x - sx_min) / dx * (x_max - x_min)
        # Graphviz y 向上，Vensim y 向下 → 翻转
        ny = (y_min + y_max) / 2 if abs(dy) < 1e-9 else y_max - (y - sy_min) / dy * (y_max - y_min)
        out[obj_id] = (nx, ny)
    return out


# ---------------------------------------------------------------------------
# 回写
# ---------------------------------------------------------------------------

def update_obj_line(line: str, x: float, y: float) -> str:
    ending = _line_ending(line)
    fields = _line_body(line).split(",")
    fields[3] = str(int(round(x)))
    fields[4] = str(int(round(y)))
    return ",".join(fields) + ending


def _endpoint_position(obj: Obj, new_positions: Dict[int, Tuple[float, float]]) -> Tuple[float, float]:
    return new_positions.get(obj.obj_id, (obj.x, obj.y))


def _curve_point(
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    signed_slot: float,
    config: dict,
) -> Tuple[float, float]:
    """计算一个中间控制点，使普通 Arrow 显示为圆弧。

    signed_slot 的符号决定弧线偏向哪一侧，绝对值决定平行边错开距离。
    """
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    distance = math.hypot(dx, dy)
    if distance < 1e-6:
        return ((x0 + x1) / 2, (y0 + y1) / 2)
    # 垂直于连线方向的单位法向量
    nx, ny = -dy / distance, dx / distance
    strength = float(config.get("curve_strength", 0.18))
    minimum = float(config.get("minimum_curve_pixels", 26))
    maximum = float(config.get("maximum_curve_pixels", 118))
    parallel = float(config.get("parallel_curve_spacing", 0.08))
    amplitude = max(minimum, min(maximum, distance * (strength + abs(signed_slot) * parallel)))
    direction = 1.0 if signed_slot >= 0 else -1.0
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    return mx + direction * nx * amplitude, my + direction * ny * amplitude


def update_arrow_line(line: str, point: Tuple[float, float]) -> str:
    """把箭头改写为单控制点圆弧：np=1，控制点列表只有一个点。"""
    ending = _line_ending(line)
    body = _line_body(line)
    prefix, sep, _ = body.partition("|")
    if not sep:
        return line
    fields = prefix.split(",")
    # 末字段为 np（控制点个数），普通 Arrow 圆弧 = 1 个中间点
    fields[-1] = "1"
    return ",".join(fields) + "|" + format_points([point]) + ending


# ---------------------------------------------------------------------------
# 视图选择与箭头布线
# ---------------------------------------------------------------------------

def choose_views(views: List[View], config: dict) -> List[View]:
    requested = str(config.get("view", "*")).strip()
    skip = {str(v) for v in config.get("skip_views", [])}
    chosen = []
    for view in views:
        if view.name in skip:
            continue
        if requested == "*" or requested == view.name:
            chosen.append(view)
    return chosen


def _is_information_arrow(arrow: Arrow, objects: Dict[int, Obj]) -> bool:
    """判定是否为可重布线的信息箭头：细线、两端均为变量节点、且原本为普通 Arrow。

    保守判据：仅当原箭头控制点个数 <= 1 时才视为普通 Arrow，可写入单控制点圆弧。
    Polyline(多控制点)、Spline、Perpendicular 等多控制点箭头保持原样不动，
    避免破坏其原有路由类型（Vensim Arrow Class 文档：不同箭头类型行为不同）。
    """
    if arrow.is_physical_flow:
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
    if len(arrow.points) > 1:
        return False
    return True


def route_arrows(
    lines: List[str],
    view: View,
    new_positions: Dict[int, Tuple[float, float]],
    config: dict,
) -> int:
    objects = view.objects
    routeable = [a for a in view.arrows if _is_information_arrow(a, objects)]

    # 同一对端点的多条边按 obj_id 排序后分配对称 slot，避免重叠
    by_pair: Dict[Tuple[int, int], List[Arrow]] = defaultdict(list)
    for arrow in routeable:
        by_pair[tuple(sorted((arrow.from_id, arrow.to_id)))].append(arrow)

    changed = 0
    for arrows in by_pair.values():
        arrows.sort(key=lambda a: a.obj_id)
        if len(arrows) == 1:
            slots = [1.0]
        else:
            slots = []
            for i in range(len(arrows)):
                magnitude = 1.0 + i // 2
                slots.append(magnitude if i % 2 == 0 else -magnitude)
        for arrow, slot in zip(arrows, slots):
            src = objects[arrow.from_id]
            dst = objects[arrow.to_id]
            point = _curve_point(
                _endpoint_position(src, new_positions),
                _endpoint_position(dst, new_positions),
                slot,
                config,
            )
            lines[arrow.line_index] = update_arrow_line(lines[arrow.line_index], point)
            changed += 1
    return changed


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


def _equation_semantics_audit(path: Path) -> Tuple[int, int]:
    """方程区语义审计：重复定义、未定义引用、未使用变量、缺失单位。

    复用 vensim_engine 的方程解析与依赖提取，返回 (errors, warnings)。
    """
    try:
        from vensim_engine import parse_equations, extract_deps
    except ImportError:
        # vensim_engine 不可用时跳过语义审计
        return 0, 0

    text = _read_text(path)
    equations = parse_equations(text)
    names = set(equations.keys())
    errors = warnings = 0

    # 1. 重复定义：parse_equations 用 OrderedDict，后定义覆盖前定义；
    #    通过重新扫描原始文本检测同名方程出现多次
    sketch_pos = text.find(SKETCH_MARKER)
    body = text[:sketch_pos] if sketch_pos >= 0 else text
    seen: Dict[str, int] = {}
    for line in body.splitlines():
        m = re.match(r"^\s*([A-Za-z_][\w\$\s]*?)\s*=\s*(.+)$", line)
        if not m:
            continue
        name = m.group(1).strip()
        # 排除控制变量与函数定义行
        if name in ("INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER"):
            continue
        seen[name] = seen.get(name, 0) + 1
    for name, count in seen.items():
        if count > 1:
            errors += 1
            print(f"  ERROR 变量 '{name}' 被重复定义 {count} 次。")

    # 2. 未定义变量引用
    for name, eq in equations.items():
        rhs = eq.integ_flow or eq.rhs
        for d in extract_deps(rhs, names):
            if d not in equations:
                errors += 1
                print(f"  ERROR {name}: 引用未定义变量 '{d}'。")

    # 3. 未使用变量（定义但从未被任何方程引用）
    referenced: set = set()
    for eq in equations.values():
        rhs = eq.integ_flow or eq.rhs
        for d in extract_deps(rhs, names):
            referenced.add(d)
    for name in equations:
        if name in ("INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER"):
            continue
        if name not in referenced:
            warnings += 1
            print(f"  WARNING 变量 '{name}' 已定义但从未被引用。")

    # 4. 缺失单位
    for name, eq in equations.items():
        if name in ("INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER"):
            continue
        if not eq.unit:
            warnings += 1
            print(f"  WARNING {name}: 缺失单位。")

    return errors, warnings


def command_audit(path: Path) -> int:
    _, views = load_mdl(path)
    errors = warnings = 0
    print(f"AUDIT: {path}")

    # 方程区语义审计
    print("\n[方程语义]")
    eq_errors, eq_warnings = _equation_semantics_audit(path)
    errors += eq_errors
    warnings += eq_warnings

    # Sketch 引用审计
    for view in views:
        print(f"\n[Sketch] VIEW: {view.name}")
        ids = set(view.objects)
        for arrow in view.arrows:
            if arrow.from_id not in ids or arrow.to_id not in ids:
                errors += 1
                print(
                    f"  ERROR arrow {arrow.obj_id}: {arrow.from_id} -> {arrow.to_id} "
                    "引用了本视图不存在的对象 ID。"
                )
            if not arrow.points:
                warnings += 1
                print(f"  WARNING arrow {arrow.obj_id}: 无中间控制点记录。")
        for obj in view.objects.values():
            if obj.kind == T_VARIABLE and not obj.name.strip():
                warnings += 1
                print(f"  WARNING object {obj.obj_id}: 变量名为空。")

    if errors == 0:
        print("\nPASS: 未发现错误。")
    else:
        print(f"\nFAIL: 发现 {errors} 处错误。")
    print(f"Warnings: {warnings}")
    print("\n注意：audit 仅检查 Sketch 对象 ID 断裂与方程区基础语义，")
    print("不替代 Vensim Check Model 与 Units Check。")
    return 1 if errors else 0


def command_layout(
    path: Path,
    output: Path,
    config_path: Path,
    engine: str,
    route_information_arrows: bool,
) -> int:
    if output.resolve() == path.resolve():
        raise ValueError("输出路径必须与输入路径不同。")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    lines, views = load_mdl(path)

    backup = path.with_suffix(path.suffix + ".backup.mdl")
    if not backup.exists():
        shutil.copy2(path, backup)

    report = {"input": str(path), "output": str(output), "backup": str(backup), "views": []}
    for view in choose_views(views, config):
        movable = eligible_movable_nodes(view, config)
        positions = graphviz_positions(view, movable, config, engine)
        scaled = scale_positions(positions, movable, config)
        for obj_id, (x, y) in scaled.items():
            lines[movable[obj_id].line_index] = update_obj_line(
                lines[movable[obj_id].line_index], x, y
            )
        rerouted = 0
        if route_information_arrows or bool(config.get("route_information_arrows_only", False)):
            rerouted = route_arrows(lines, view, scaled, config)
        report["views"].append(
            {
                "view": view.name,
                "moved_auxiliary_nodes": len(scaled),
                "rerouted_information_arrows": rerouted,
                "locked_node_names": config.get("lock_node_names", []),
            }
        )

    output.write_text("".join(lines), encoding="utf-8")
    report_path = output.with_suffix(output.suffix + ".layout_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("\nNEXT: 在 Vensim 中打开输出文件，检查草图，然后运行 Check Model 与 Units Check。")
    return 0


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
    p_layout.add_argument("--config", required=True, type=Path)
    p_layout.add_argument("--engine", default="dot", choices=["dot", "neato", "fdp", "sfdp"])
    p_layout.add_argument(
        "--route-information-arrows",
        action="store_true",
        help="为可重布线的信息箭头设置单个圆弧控制点。",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.model.exists():
        parser.error(f"模型文件未找到: {args.model}")
    if args.command == "inspect":
        return command_inspect(args.model)
    if args.command == "audit":
        return command_audit(args.model)
    if args.command == "layout":
        if not args.config.exists():
            parser.error(f"配置文件未找到: {args.config}")
        return command_layout(
            args.model, args.output, args.config, args.engine, args.route_information_arrows
        )
    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
