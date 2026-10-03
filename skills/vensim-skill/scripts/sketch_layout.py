"""在既有 SFD 骨架上优化节点位置和原生单控制点圆弧。"""

from __future__ import annotations

import dataclasses
import math

from sketch_geometry import (
    arrow_path,
    box,
    boxes_intersect,
    measure_view,
    path_hits_box,
    path_length,
    paths_cross,
    quality_key,
    visible,
)


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


def control_candidates(view, arrow, config):
    source, target = view.objects[arrow.from_id], view.objects[arrow.to_id]
    # 统一端点顺序，反向箭头不会因法向量同时反向而落在同一根线上。
    left, right = sorted((source, target), key=lambda obj: obj.obj_id)
    dx, dy = right.x - left.x, right.y - left.y
    distance = math.hypot(dx, dy)
    if distance < 1:
        return []
    nx, ny = -dy / distance, dx / distance
    minimum = min(float(config.get("minimum_curve_pixels", 14)), distance * 0.15)
    maximum = min(float(config.get("maximum_curve_pixels", 180)), distance * 0.48)
    amplitudes = {
        minimum,
        max(minimum, min(maximum, distance * float(config.get("curve_strength", 0.12)))),
    }
    amplitudes.update(
        max(minimum, min(maximum, distance * ratio)) for ratio in (0.07, 0.14, 0.24, 0.36, 0.48)
    )
    # 直线是原生 Arrow 的合法几何。大型视图先把中点加入候选，只有直线
    # 会穿过文字、管道或与其他箭头相交时才使用圆弧，避免所有关系被强行
    # 弯成同一种“AI 弧线”。
    candidates = []
    if len(view.objects) >= 12:
        candidates.append((round(left.x + dx * 0.5), round(left.y + dy * 0.5)))
    for amplitude in sorted(amplitudes):
        for direction in (1, -1):
            for fraction in (0.5, 0.35, 0.65):
                candidates.append(
                    (
                        round(left.x + dx * fraction + nx * amplitude * direction),
                        round(left.y + dy * fraction + ny * amplitude * direction),
                    )
                )
    # 现有的正常圆弧也参加比较，避免对已排好的模型反复扰动。
    if arrow.shape == 1 and len(arrow.points) == 1:
        point = arrow.points[0]
        sagitta = abs(dx * (point[1] - left.y) - dy * (point[0] - left.x)) / distance
        if minimum * 0.75 <= sagitta <= maximum:
            candidates.insert(0, point)
    return list(dict.fromkeys(candidates))


def route_view(lines, view, positions, config):
    from vensim_autolayout import (
        _is_information_arrow,
        restyle_arrow_line,
        update_arrow_line,
    )

    current = positioned_view(view, positions)
    routeable = [arrow for arrow in current.arrows if _is_information_arrow(arrow, current.objects)]
    paths = {a.obj_id: arrow_path(current, a) for a in current.arrows if visible(a)}
    obstacles = [obj for obj in current.objects.values() if visible(obj)]
    clearance = float(config.get("clearance", 6))
    routeable.sort(key=lambda a: (-path_length(paths.get(a.obj_id, [])), a.obj_id))
    candidates = {}
    for arrow in routeable:
        rows = []
        for control in control_candidates(current, arrow, config):
            path = arrow_path(current, arrow, control)
            if len(path) < 2:
                continue
            hits = sum(
                path_hits_box(path, box(obj, clearance))
                for obj in obstacles
                if obj.obj_id not in (arrow.from_id, arrow.to_id)
            )
            rows.append((control, path, hits, path_length(path)))
        candidates[arrow.obj_id] = rows
    chosen = {}
    for _ in range(int(config.get("routing_passes", 2))):
        for arrow in routeable:

            def cost(row, arrow=arrow):
                control, path, hits, length = row
                crossings = sum(
                    paths_cross(path, other) for oid, other in paths.items() if oid != arrow.obj_id
                )
                return hits, crossings, round(length, 1)

            if candidates[arrow.obj_id]:
                best = min(candidates[arrow.obj_id], key=cost)
                chosen[arrow.obj_id], paths[arrow.obj_id] = best[0], best[1]
    changed = 0
    for arrow in routeable:
        if arrow.obj_id in chosen:
            old = lines[arrow.line_index]
            lines[arrow.line_index] = update_arrow_line(old, chosen[arrow.obj_id], config)
            changed += old != lines[arrow.line_index]
    for arrow in current.arrows:
        if not arrow.is_physical_flow:
            lines[arrow.line_index] = restyle_arrow_line(lines[arrow.line_index], config)
    return changed


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


def optimize_view(lines, view, config, route=True):
    from vensim_autolayout import eligible_movable_nodes, parse_views, update_obj_line

    movable = eligible_movable_nodes(view, config)
    requested_mode = config.get("layout_mode", "refine")
    # 旧配置中的 graphviz 只保留为迁移别名；所有位置都由本地、可复现
    # 的骨架/邻域算法生成，避免外部样条和节点尺度把 MDL 拉成超宽长图。
    mode = "auto" if requested_mode == "graphviz" else requested_mode
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
    proposals = [("preserve", {})]
    if mode != "preserve":
        proposals.append(("refine", clear_positions(working, {}, free, config)))
        if mode == "circular":
            from circular_layout import circular_proposals

            proposals = [
                ("circular", clear_positions(working, proposed, free, config))
                for proposed in circular_proposals(working, free, config)
            ]
        if mode == "auto" and free:
            from circular_layout import circular_proposals

            proposals.extend(
                (
                    "native-circular",
                    clear_positions(working, proposed, free, config),
                )
                for proposed in circular_proposals(working, free, config)
            )
    if anchors:
        proposals = [(label, {**positions, **anchors}) for label, positions in proposals]
    if requested_mode == "graphviz" and free:
        # 明确使用旧别名时仍只接受本地候选，不再尝试外部布局器。
        proposals = [proposal for proposal in proposals if proposal[0] != "original"]
    evaluated = []
    stock_names = next(iter(view.objects.values())).stock_names if view.objects else set()
    # 原图也作为候选：在自动模式下不因美化而恶化碰撞指标。
    if mode in {"auto", "refine", "preserve"} and not anchors:
        evaluated.append(
            (quality_key(measure_view(view)), "original", lines[:], {}, 0, measure_view(view))
        )
    for label, positions in proposals:
        candidate = lines[:]
        for oid, (x, y) in positions.items():
            candidate[movable[oid].line_index] = update_obj_line(
                candidate[movable[oid].line_index], x, y
            )
        changed = route_view(candidate, view, positions, config) if route else 0
        updated = parse_views(candidate, stock_names)[view.index]
        metrics = measure_view(updated, float(config.get("clearance", 6)))
        evaluated.append((quality_key(metrics), label, candidate, positions, changed, metrics))
    # 同等几何质量时优先采用有明确弧线的方案，原图排在最后。
    # 既有直线不参加长度惩罚：先保证不增加碰撞，再选择自然圆弧。
    best = min(evaluated, key=lambda row: (row[0][:-1], row[1] == "original", row[0][-1]))
    lines[:] = best[2]
    if best[1] == "original":
        from vensim_autolayout import restyle_arrow_line

        for arrow in view.arrows:
            if not arrow.is_physical_flow:
                lines[arrow.line_index] = restyle_arrow_line(lines[arrow.line_index], config)
    return {
        "strategy": (
            "native-auto"
            if requested_mode == "graphviz" and best[1] == "native-circular"
            else best[1]
        ),
        "moved_auxiliary_nodes": sum(
            (view.objects[oid].x, view.objects[oid].y) != pos for oid, pos in best[3].items()
        ),
        "rerouted_information_arrows": best[4],
        "before": measure_view(view, float(config.get("clearance", 6))),
        "after": best[5],
        "candidates": [{"strategy": row[1], "quality": list(row[0])} for row in evaluated],
    }
