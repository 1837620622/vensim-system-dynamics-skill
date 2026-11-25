"""信息箭头的圆弧路由：Vensim 普通箭头加一个控制点即画成圆弧。

控制点 = 弦中点 + k·(dy, −dx)，(dx, dy) 为起点中心指向终点中心的向量，|k| 为
矢高与弦长之比。每条箭头在 0、±0.06、±0.12、±0.18 中选取：先满足与 visual
相同的硬约束（不与其他连线或管道交叉、不穿文字框、不压 <Time> 预留区、
可见长度不少于 PREFERRED_ARROW、控制点不落进两端文字框），再选接近 0.12、
离文字较远、与同一节点其他箭头张开足够角度的弧。
"""

from __future__ import annotations

import math

from sketch_geometry import (
    LAYOUT_CLEARANCE,
    PREFERRED_ARROW,
    arrow_path,
    bounds,
    box,
    inside,
    path_hits_box,
    path_length,
    paths_cross,
    shown,
    time_label_box,
    time_label_owners,
    visible,
)

# ---------------------------------------------------------------------------
# 圆弧参数
# ---------------------------------------------------------------------------

# 弧度 k = 矢高 / 弦长。Vensim 官方示例模型中信息线弧度的中位数约 0.14
# （四分位 0.08–0.22），各弦长区间基本一致；这里取略缓的 0.12 为目标
ARC_RATIOS = (0.06, 0.12, 0.18)
TARGET_RATIO = 0.12
MAX_ARC_RATIO = 0.18
SPREAD_ANGLE = math.radians(28)


def target_ratio(chord: float) -> float:
    """期望弧度；真实模型的弧度与弦长关系不大，统一取 TARGET_RATIO。"""
    return TARGET_RATIO


def control_point(source, target, ratio: float) -> tuple[int, int]:
    """控制点取整到像素，评价与回写使用同一个整数点。"""
    dx, dy = target.x - source.x, target.y - source.y
    return (
        int(round((source.x + target.x) / 2 + ratio * dy)),
        int(round((source.y + target.y) / 2 - ratio * dx)),
    )


def arc_ratio(source, target, control) -> float:
    """由已有控制点反求带符号的 k（矢高/弦长），用于报告与回归测试。"""
    dx, dy = target.x - source.x, target.y - source.y
    chord2 = dx * dx + dy * dy
    if chord2 < 1e-9:
        return 0.0
    mx, my = (source.x + target.x) / 2, (source.y + target.y) / 2
    # 控制点相对中点的位移投影到左法向量 (dy, −dx) 上。
    return ((control[0] - mx) * dy - (control[1] - my) * dx) / chord2


def _tangent(path, at_start: bool):
    if len(path) < 2:
        return None
    a, b = (path[0], path[1]) if at_start else (path[-1], path[-2])
    return math.atan2(b[1] - a[1], b[0] - a[0])


def _angle_gap(a, b):
    return abs((a - b + math.pi) % math.tau - math.pi)


# ---------------------------------------------------------------------------
# 逐条选择
# ---------------------------------------------------------------------------


def route_arcs(view, routeable, clearance: float = LAYOUT_CLEARANCE, passes: int = 5, ratios=None):
    """为 routeable 中的信息箭头选择控制点，返回 {arrow_id: (control, k)}。

    view 必须已经带有最终坐标与尺寸；不可重布线的箭头按原记录参与碰撞判断。
    """
    ratios = ratios or ARC_RATIOS
    candidates_k = [0.0] + [sign * value for value in ratios for sign in (1, -1)]
    routeable = [a for a in routeable if shown(view, a)]
    route_ids = {a.obj_id for a in routeable}
    objects = [obj for obj in view.objects.values() if visible(obj)]
    padded = {obj.obj_id: box(obj, clearance) for obj in objects}
    plain = {obj.obj_id: box(obj) for obj in objects}
    time_boxes = [time_label_box(owner) for owner in time_label_owners(view)]
    # 不参与重布线的信息线与管道是固定障碍，路径与外接矩形只算一次
    fixed = [
        (path, bounds(path))
        for path in (
            arrow_path(view, a)
            for a in view.arrows
            if shown(view, a) and (a.is_physical_flow or a.obj_id not in route_ids)
        )
        if path
    ]

    # 每条箭头的候选：路径与静态代价（与其他箭头无关的部分）。
    table = {}
    for arrow in routeable:
        source, target = view.objects[arrow.from_id], view.objects[arrow.to_id]
        chord = math.dist((source.x, source.y), (target.x, target.y))
        goal = target_ratio(chord)
        rows = []
        src_box, dst_box = box(source, 3), box(target, 3)
        for k in candidates_k:
            control = control_point(source, target, k)
            path = arrow_path(view, arrow, control)
            extent = bounds(path)
            ends = (arrow.from_id, arrow.to_id)
            bad = 0
            if len(path) < 2:
                bad += 1
            else:
                if path_length(path) < PREFERRED_ARROW:
                    bad += 1
                if k and (inside(control, src_box) or inside(control, dst_box)):
                    bad += 1
                bad += sum(
                    path_hits_box(path, rect, extent)
                    for oid, rect in padded.items()
                    if oid not in ends
                )
                bad += sum(path_hits_box(path, rect, extent) for rect in time_boxes)
                bad += sum(paths_cross(path, other, extent, ob) for other, ob in fixed)
            # 远离文字：路径中段到非端点文字框的最小距离，封顶 40px
            clear = 40.0
            near = [
                rect
                for oid, rect in plain.items()
                if oid not in ends
                and rect[0] - 40 < extent[2]
                and rect[2] + 40 > extent[0]
                and rect[1] - 40 < extent[3]
                and rect[3] + 40 > extent[1]
            ]
            for point in path[2:-2]:
                for rect in near:
                    gap = math.hypot(
                        max(rect[0] - point[0], 0, point[0] - rect[2]),
                        max(rect[1] - point[1], 0, point[1] - rect[3]),
                    )
                    clear = min(clear, gap)
            shape = 120 * abs(abs(k) - goal) / 0.03
            rows.append(
                {
                    "k": k,
                    "control": control,
                    "path": path,
                    "bounds": extent,
                    "static": 1e4 * bad + shape - 1.5 * clear,
                    "start": _tangent(path, True),
                    "end": _tangent(path, False),
                }
            )
        table[arrow.obj_id] = rows

    # 初始选择：静态代价最小者。
    choice = {
        aid: min(range(len(rows)), key=lambda i, r=rows: r[i]["static"])
        for aid, rows in table.items()
    }
    by_id = {a.obj_id: a for a in routeable}
    cross_cache: dict = {}

    def crosses(aid, i, bid, j):
        key = (aid, i, bid, j) if aid < bid else (bid, j, aid, i)
        if key not in cross_cache:
            left, right = table[aid][i], table[bid][j]
            cross_cache[key] = paths_cross(
                left["path"], right["path"], left["bounds"], right["bounds"]
            )
        return cross_cache[key]

    def dynamic(aid, i):
        arrow, row = by_id[aid], table[aid][i]
        bad = 0
        for bid, j in choice.items():
            if bid != aid and crosses(aid, i, bid, j):
                bad += 1
        spread = 0.0
        for node, angle in ((arrow.from_id, row["start"]), (arrow.to_id, row["end"])):
            if angle is None:
                continue
            for bid, j in choice.items():
                if bid == aid:
                    continue
                other = by_id[bid]
                if node == other.from_id:
                    other_angle = table[bid][j]["start"]
                elif node == other.to_id:
                    other_angle = table[bid][j]["end"]
                else:
                    continue
                if other_angle is None:
                    continue
                gap = _angle_gap(angle, other_angle)
                if gap < SPREAD_ANGLE:
                    spread += (SPREAD_ANGLE - gap) * 60
        return 1e4 * bad + spread

    order = sorted(
        routeable, key=lambda a: (-path_length(table[a.obj_id][choice[a.obj_id]]["path"]), a.obj_id)
    )
    for _ in range(passes):
        changed = False
        for arrow in order:
            aid = arrow.obj_id
            rows = table[aid]
            current = choice[aid]
            best_i, best_cost = current, rows[current]["static"] + dynamic(aid, current)
            for i, row in enumerate(rows):
                if i == current:
                    continue
                cost = row["static"] + dynamic(aid, i)
                if cost < best_cost - 1e-6:
                    best_i, best_cost = i, cost
            if best_i != current:
                choice[aid] = best_i
                changed = True
        if not changed:
            break
    return {aid: (table[aid][i]["control"], table[aid][i]["k"]) for aid, i in choice.items()}
