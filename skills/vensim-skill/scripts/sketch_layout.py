"""在既有 SFD 骨架上优化节点位置和原生单控制点圆弧。"""

from __future__ import annotations

import dataclasses
import math

from sketch_geometry import (
    CHECK_CLEARANCE,
    arrow_path,
    box,
    boxes_intersect,
    measure_view,
    path_hits_box,
    quality_key,
    visible,
)

# ---------------------------------------------------------------------------
# 坐标替换与圆弧重排
# ---------------------------------------------------------------------------


def positioned_view(view, positions):
    return dataclasses.replace(
        view,
        objects={
            oid: dataclasses.replace(
                obj,
                x=positions.get(oid, (obj.x, obj.y))[0],
                y=positions.get(oid, (obj.x, obj.y))[1],
            )
            for oid, obj in view.objects.items()
        },
    )


def route_view(lines, view, positions, config):
    """为可重布线的信息箭头选择柔和圆弧（与 natural 相同的 k 规则与约束）。"""
    from arc_routing import route_arcs
    from vensim_autolayout import (
        _is_information_arrow,
        restyle_arrow_line,
        update_arrow_line,
    )

    current = positioned_view(view, positions)
    routeable = [arrow for arrow in current.arrows if _is_information_arrow(arrow, current.objects)]
    chosen = route_arcs(
        current,
        routeable,
        clearance=float(config.get("clearance", CHECK_CLEARANCE)),
        passes=int(config.get("routing_passes", 5)),
    )
    changed = 0
    for arrow in routeable:
        if arrow.obj_id in chosen:
            old = lines[arrow.line_index]
            lines[arrow.line_index] = update_arrow_line(old, chosen[arrow.obj_id][0], config)
            changed += old != lines[arrow.line_index]
    for arrow in current.arrows:
        if not arrow.is_physical_flow and arrow.obj_id not in chosen:
            lines[arrow.line_index] = restyle_arrow_line(lines[arrow.line_index], config)
    return changed


# ---------------------------------------------------------------------------
# 整体避让（refine）
# ---------------------------------------------------------------------------


def clear_positions(view, proposed, movable, config):
    """就近找留白，优先保留模块相对位置，不把少量节点铺满整个画布。"""
    clearance = float(config.get("node_spacing", 24))
    fixed = [obj for oid, obj in view.objects.items() if oid not in movable and visible(obj)]
    physical = [arrow_path(view, arrow) for arrow in view.arrows if arrow.is_physical_flow]
    placed = list(fixed)
    placed_positions = {obj.obj_id: (obj.x, obj.y) for obj in fixed}
    neighbors = {oid: set() for oid in view.objects}
    for arrow in view.arrows:
        if arrow.from_id in neighbors and arrow.to_id in neighbors:
            neighbors[arrow.from_id].add(arrow.to_id)
            neighbors[arrow.to_id].add(arrow.from_id)
    result = {}
    # 邻居多的节点先落位，参数节点围绕主要结构安排。
    degree = {oid: sum(oid in (a.from_id, a.to_id) for a in view.arrows) for oid in movable}
    for oid in sorted(movable, key=lambda item: (-degree[item], item)):
        obj = movable[oid]
        x, y = proposed.get(oid, (obj.x, obj.y))
        candidates = [(x, y)]
        for radius in (24, 48, 80, 120, 180, 260, 360):
            for angle in range(0, 360, 30):
                theta = math.radians(angle)
                candidates.append(
                    (round(x + radius * math.cos(theta)), round(y + radius * math.sin(theta)))
                )
        known = [
            placed_positions[other]
            for other in sorted(neighbors.get(oid, ()))
            if other in placed_positions
        ]
        if len(view.objects) >= 12 and known:
            # 旧模型给出的远距离坐标只作为候选；大型视图额外尝试围绕
            # 已落位的相邻骨架，避免辅助量被拉到画布另一端。
            center = (
                sum(point[0] for point in known) / len(known),
                sum(point[1] for point in known) / len(known),
            )
            local_radius = max(clearance * 2, math.hypot(obj.w, obj.h) + clearance * 1.5)
            candidates.append((round(center[0]), round(center[1])))
            for angle in range(0, 360, 45):
                theta = math.radians(angle)
                candidates.append(
                    (
                        round(center[0] + local_radius * math.cos(theta)),
                        round(center[1] + local_radius * math.sin(theta)),
                    )
                )

        def cost(position, obj=obj, origin=(x, y), known=tuple(known)):
            test = dataclasses.replace(obj, x=position[0], y=position[1])
            rect = box(test, clearance / 2)
            collisions = sum(boxes_intersect(rect, box(other, clearance / 2)) for other in placed)
            collisions += sum(path_hits_box(path, rect) for path in physical)
            outside = max(0, obj.w + 24 - position[0]) + max(0, obj.h + 24 - position[1])
            neighbor_distance = sum(math.dist(position, point) for point in known)
            if len(view.objects) >= 12 and known:
                return collisions, round(neighbor_distance, 1), outside, math.dist(position, origin)
            return collisions, outside, math.dist(position, origin)

        target = min(candidates, key=cost)
        result[oid] = (round(target[0]), round(target[1]))
        placed.append(dataclasses.replace(obj, x=result[oid][0], y=result[oid][1]))
        placed_positions[oid] = result[oid]
    return result


# ---------------------------------------------------------------------------
# 最小位移（nudge）
# ---------------------------------------------------------------------------


def _culprits(view, metrics, movable):
    """按检查结果列出该挪的节点：文字重叠的双方、被线穿过的对象、过短箭头的两端。"""
    arrows = {arrow.obj_id: arrow for arrow in view.arrows}
    found = [oid for pair in metrics["node_overlaps"] for oid in pair]
    found += [item["object"] for item in metrics["arrow_node_collisions"]]
    found += [item["object"] for item in metrics.get("physical_node_collisions", [])]
    for item in metrics.get("time_label_conflicts", []):
        found += [item["owner"], item.get("object")]
    for item in metrics["arrow_node_collisions"] + metrics["short_arrows"]:
        arrow = arrows.get(item["arrow"])
        if arrow is not None:
            found += [arrow.to_id, arrow.from_id]
    return list(dict.fromkeys(oid for oid in found if oid in movable))


def nudge_positions(view, movable, config, max_moves=6):
    """最小位移：只挪出问题的节点，其余坐标和全部弧线控制点保持原样。

    每轮从出问题的节点里找一个，在原位周围由近到远试探，取第一个让各项缺陷
    都不增加、且至少一项减少的位置。作者原图通常只有一两处文字相压，没必要
    为此重排整张图。
    """
    clearance = float(config.get("clearance", CHECK_CLEARANCE))
    positions: dict[int, tuple[int, int]] = {}
    current = measure_view(view, clearance)
    for _ in range(max_moves):
        key = quality_key(current)[:4]
        if not any(key):
            break
        trial_view = positioned_view(view, positions)
        best = None
        for oid in _culprits(trial_view, current, movable):
            x0, y0 = trial_view.objects[oid].x, trial_view.objects[oid].y
            for radius in (10, 20, 30, 40, 60, 80):
                for angle in range(0, 360, 30):
                    theta = math.radians(angle)
                    point = (
                        round(x0 + radius * math.cos(theta)),
                        round(y0 + radius * math.sin(theta)),
                    )
                    metrics = measure_view(
                        positioned_view(view, {**positions, oid: point}), clearance
                    )
                    new = quality_key(metrics)[:4]
                    if new != key and all(a <= b for a, b in zip(new, key, strict=True)):
                        if best is None or (new, radius) < (best[0], best[1]):
                            best = (new, radius, oid, point, metrics)
                if best is not None:
                    break
            if best is not None:
                break
        if best is None:
            break
        positions[best[2]] = best[3]
        current = best[4]
    return positions


# ---------------------------------------------------------------------------
# refine / preserve / nudge 方案比较
# ---------------------------------------------------------------------------


def optimize_view(lines, view, config, route=True):
    """refine 与 preserve：尽量保留人工坐标，preserve 只改圆弧，refine 就近避让。

    refine 同时比较最小位移（nudge）和整体避让两种方案；auto 单独用 nudge。
    """
    from vensim_autolayout import eligible_movable_nodes, parse_views, update_obj_line

    movable = eligible_movable_nodes(view, config)
    mode = config.get("layout_mode", "refine")
    anchors = {}
    for name, point in config.get("node_positions", {}).items():
        matches = [oid for oid, obj in view.objects.items() if obj.name == name]
        if not matches:
            continue  # 多视图配置中的锚点可属于其他视图。
        if len(matches) != 1 or matches[0] not in movable:
            raise ValueError(f"{name}: 锚点必须唯一且可移动；不能改动存量管道、阀门或影子变量")
        anchors[matches[0]] = tuple(point)
    # 锚点先进入障碍物集合，其他节点才能真正绕开其最终位置。
    working = positioned_view(view, anchors)
    free = {oid: obj for oid, obj in movable.items() if oid not in anchors}
    proposals = [] if mode == "nudge" else [("preserve", {})]
    if mode in {"refine", "nudge"} and not anchors:
        proposals.append(("nudge", nudge_positions(view, free, config)))
    if mode == "refine":
        proposals.append(("refine", clear_positions(working, {}, free, config)))
    if anchors:
        proposals = [(label, {**positions, **anchors}) for label, positions in proposals]
    evaluated = []
    stock_names = next(iter(view.objects.values())).stock_names if view.objects else set()
    # 原图也参与比较，保证排版后碰撞指标不比原图差。
    if not anchors:
        evaluated.append(
            (quality_key(measure_view(view)), "original", lines[:], {}, 0, measure_view(view))
        )
    for label, positions in proposals:
        candidate = lines[:]
        for oid, (x, y) in positions.items():
            candidate[movable[oid].line_index] = update_obj_line(
                candidate[movable[oid].line_index], x, y
            )
        # nudge 只挪个别节点，作者画的弧线控制点原样保留
        changed = (
            route_view(candidate, view, positions, config) if route and label != "nudge" else 0
        )
        updated = parse_views(candidate, stock_names, view.time_dependents)[view.index]
        metrics = measure_view(updated, float(config.get("clearance", CHECK_CLEARANCE)))
        evaluated.append((quality_key(metrics), label, candidate, positions, changed, metrics))
    # 缺陷数相同时优先最小位移；再比总长，同等时优先有明确弧线的方案，原图排在最后。
    best = min(
        evaluated,
        key=lambda row: (
            row[0][:4],
            row[1] != "nudge",
            row[0][4:-1],
            row[1] == "original",
            row[0][-1],
        ),
    )
    lines[:] = best[2]
    if best[1] in {"original", "nudge"}:
        from vensim_autolayout import restyle_arrow_line

        for arrow in view.arrows:
            if not arrow.is_physical_flow:
                lines[arrow.line_index] = restyle_arrow_line(lines[arrow.line_index], config)
    return {
        "strategy": best[1],
        "moved_auxiliary_nodes": sum(
            (view.objects[oid].x, view.objects[oid].y) != pos for oid, pos in best[3].items()
        ),
        "rerouted_information_arrows": best[4],
        "before": measure_view(view, float(config.get("clearance", CHECK_CLEARANCE))),
        "after": best[5],
        "candidates": [{"strategy": row[1], "quality": list(row[0])} for row in evaluated],
    }
