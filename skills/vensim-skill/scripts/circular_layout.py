"""按真实连接组织环形位置；只提议坐标，不生成或改变因果关系。"""

from __future__ import annotations

import math

from sketch_geometry import box, boxes_intersect, visible


def connection_graph(view):
    nodes = {oid: obj for oid, obj in view.objects.items() if obj.kind in (10, 11) and visible(obj)}
    outgoing = {oid: set() for oid in nodes}
    neighbors = {oid: set() for oid in nodes}

    def connect(source, target):
        if source != target and source in nodes and target in nodes:
            outgoing[source].add(target)
            neighbors[source].add(target)
            neighbors[target].add(source)

    for arrow in view.arrows:
        if visible(arrow):
            connect(arrow.from_id, arrow.to_id)
    # 原生附着文字位于被附着对象之后；这里只联通布局模块，不回写这条辅助关系。
    ordered = sorted(view.objects.values(), key=lambda obj: obj.line_index)
    for previous, obj in zip(ordered, ordered[1:], strict=False):
        if obj.kind == 10 and obj.attached_to_valve and previous.kind == 11:
            connect(obj.obj_id, previous.obj_id)
    return nodes, outgoing, neighbors


def component_order(component, outgoing, neighbors):
    """沿实际出线遍历，再接入支路；稳定排序使同一输入得到相同位置。"""
    remaining, order = set(component), []
    while remaining:
        start = min(remaining, key=lambda oid: (-len(neighbors[oid]), oid))
        stack = [start]
        while stack:
            oid = stack.pop()
            if oid not in remaining:
                continue
            remaining.remove(oid)
            order.append(oid)
            linked = sorted(
                neighbors[oid] & remaining,
                key=lambda other: (other not in outgoing[oid], -len(neighbors[other]), other),
            )
            stack.extend(reversed(linked))
    return order


def feedback_core(component, neighbors):
    """剥离外围树枝；参数不占反馈环的槽位，避免跨半个图连回作用对象。"""
    core = set(component)
    leaves = [oid for oid in core if len(neighbors[oid] & core) < 2]
    while leaves:
        core.difference_update(leaves)
        leaves = [oid for oid in core if len(neighbors[oid] & core) < 2]
    return core or set(component)


def place_branches(component, core, nodes, neighbors, positions, center, gap):
    pending = sorted(core)
    visited = set(core)
    while pending:
        parent = pending.pop(0)
        children = sorted((neighbors[parent] & component) - visited)
        visited.update(children)
        pending.extend(children)
        px, py = positions[parent]
        angle = math.atan2(py - center[1], px - center[0])
        for index, oid in enumerate(children):
            if oid in positions:
                continue
            obj, anchor = nodes[oid], nodes[parent]
            distance = math.hypot(anchor.w, anchor.h) + math.hypot(obj.w, obj.h) + gap
            spread = (index - (len(children) - 1) / 2) * math.pi / max(3, len(children))
            positions[oid] = (
                round(px + distance * math.cos(angle + spread)),
                round(py + distance * math.sin(angle + spread)),
            )


def _local_anchor_positions(component, movable, nodes, neighbors, outgoing, gap, spacing):
    """把大 SFD 的辅助量按相邻骨架就近排开。

    旧算法把固定存量的整个包围盒当成一个大椭圆，存量一多就会把所有
    辅助量推到椭圆底部，形成截图中的巨大空白和长对角线。这里保留固定
    存量、阀门和流量文字，只围绕已经放置的相邻节点逐个寻找最近的留白；
    没有固定骨架的 CLD 仍由外层完整环形算法处理。
    """
    fixed_ids = set(component) - set(movable)
    positions = {oid: (nodes[oid].x, nodes[oid].y) for oid in fixed_ids if oid in nodes}
    if not positions:
        return {}
    fixed_center = (
        sum(point[0] for point in positions.values()) / len(positions),
        sum(point[1] for point in positions.values()) / len(positions),
    )

    def positioned_box(obj, position, padding):
        """按 sketch_geometry.box 的规则返回临时位置的文字框。"""
        shadow_margin = 6.0 if obj.is_shadow else 0.0
        return (
            position[0] - abs(obj.w) - padding - shadow_margin,
            position[1] - abs(obj.h) - padding,
            position[0] + abs(obj.w) + padding + shadow_margin,
            position[1] + abs(obj.h) + padding,
        )

    ordered = [oid for oid in component_order(component, outgoing, neighbors) if oid in movable]
    placed = set(positions)

    # 同一局部锚点下的变量按不同角度展开，避免一束文字完全重叠。
    def candidate_points(oid, anchor, known):
        obj = nodes[oid]
        base_radius = max(
            gap + spacing / 2,
            math.hypot(obj.w, obj.h) + gap,
            max(
                math.hypot(nodes[other].w, nodes[other].h) + gap
                for other in known
                if other in nodes
            )
            if known
            else gap + spacing,
        )
        origin = (obj.x, obj.y)
        dx, dy = origin[0] - anchor[0], origin[1] - anchor[1]
        base_angle = math.atan2(dy, dx) if math.hypot(dx, dy) > 1 else (oid % 8) * math.tau / 8
        # 先尝试原有方向附近，再逐步扩大到一圈；这会保留人工布局的
        # 阅读方向，同时给密集影子和参数留出真实文字框的空间。
        offsets = [0.0, 0.55, -0.55, 1.1, -1.1, 1.65, -1.65, math.pi]
        for ring in range(1, 5):
            radius = base_radius * (1 + 0.72 * (ring - 1))
            for offset in offsets:
                theta = base_angle + offset
                yield (
                    round(anchor[0] + radius * math.cos(theta)),
                    round(anchor[1] + radius * math.sin(theta)),
                )

    for _index, oid in enumerate(ordered):
        known_neighbors = sorted(neighbors[oid] & placed)
        if known_neighbors:
            anchor = (
                sum(positions[other][0] for other in known_neighbors) / len(known_neighbors),
                sum(positions[other][1] for other in known_neighbors) / len(known_neighbors),
            )
        else:
            # 无固定邻居的外围支路沿已放置的最近父节点继续展开。
            parent_candidates = sorted(
                placed,
                key=lambda other: math.dist(
                    (nodes[other].x, nodes[other].y), (nodes[oid].x, nodes[oid].y)
                ),
            )
            anchor = positions[parent_candidates[0]] if parent_candidates else fixed_center
            known_neighbors = [parent_candidates[0]] if parent_candidates else []
        candidates = list(candidate_points(oid, anchor, known_neighbors))

        def cost(position, oid=oid, known_neighbors=tuple(known_neighbors)):
            rect = positioned_box(nodes[oid], position, spacing / 2)
            collisions = sum(
                boxes_intersect(rect, box(nodes[other], spacing / 2))
                for other in placed
                if other in nodes
            )
            edge_distance = sum(
                math.dist(position, positions[other])
                for other in known_neighbors
                if other in positions
            )
            displacement = math.dist(position, (nodes[oid].x, nodes[oid].y))
            return collisions, round(edge_distance, 3), round(displacement, 3), position

        if candidates:
            target = min(candidates, key=cost)
        else:
            target = (round(anchor[0]), round(anchor[1]))
        positions[oid] = target
        placed.add(oid)
    return positions


def _perimeter_positions(order, nodes, center, gap):
    """在圆角矩形的四条边上布置自由节点。

    大型 CLD 如果把所有节点放在同一个椭圆上，长名称会挤成一团，
    交叉边也会被迫绕过整张图。圆角矩形保留反馈的环绕阅读方向，
    同时给上下游关系留出明确的水平和垂直通道；它只用于坐标提案，
    不会写入 Graphviz 样条或增加模型关系。
    """
    if not order:
        return {}
    largest = max(
        math.hypot(box(nodes[oid])[2] - box(nodes[oid])[0], 2 * abs(nodes[oid].h)) for oid in order
    )
    step = max(largest + gap, gap * 1.6)
    # 四边的总长度按节点数和最大文字框确定，避免把短边压成一列。
    half_width = max(step * 1.5, step * max(2.0, len(order) / 4.0))
    half_height = max(step * 1.3, step * max(1.5, len(order) / 6.0))
    cx, cy = center
    perimeter = 4 * (half_width + half_height)

    def point_at(distance):
        distance %= perimeter
        if distance <= 2 * half_width:
            return cx - half_width + distance, cy - half_height
        distance -= 2 * half_width
        if distance <= 2 * half_height:
            return cx + half_width, cy - half_height + distance
        distance -= 2 * half_height
        if distance <= 2 * half_width:
            return cx + half_width - distance, cy + half_height
        distance -= 2 * half_width
        return cx - half_width, cy + half_height - distance

    return {
        oid: tuple(round(value) for value in point_at((index + 0.5) * perimeter / len(order)))
        for index, oid in enumerate(order)
    }


def circular_proposals(view, movable, config):
    """CLD 使用闭环位置；有固定 SFD 骨架时使用其外侧的环形弧段。

    半径由实际文字框与间距决定。各连通模块独立安排，保留固定对象，
    比较两个循线方向；最终碰撞和原生圆弧由统一布局评估器决定。
    """
    nodes, outgoing, neighbors = connection_graph(view)
    remaining, components = set(nodes), []
    while remaining:
        start = min(remaining)
        reached, pending = set(), [start]
        while pending:
            oid = pending.pop()
            if oid in reached:
                continue
            reached.add(oid)
            pending.extend(neighbors[oid] - reached)
        remaining -= reached
        components.append(reached)
    proposals = [{}, {}]
    spacing = float(config.get("node_spacing", 24))
    gap = float(config.get("circular_gap", 64))
    aspect = float(config.get("circular_aspect", 0.8))
    margin = spacing + gap
    fixed_all = [obj for oid, obj in view.objects.items() if oid not in movable and visible(obj)]
    cursor_x = max((box(obj)[2] for obj in fixed_all), default=0) + margin
    for component in components:
        if not component.intersection(movable):
            continue
        core = feedback_core(component, neighbors)
        order = [oid for oid in component_order(core, outgoing, neighbors) if oid in movable]
        fixed = [nodes[oid] for oid in sorted(core) if oid not in movable]
        sizes = {
            oid: math.hypot(box(nodes[oid])[2] - box(nodes[oid])[0], 2 * abs(nodes[oid].h))
            for oid in order
        }
        largest = max(sizes.values(), default=gap)
        fixed_width = (
            max(box(obj)[2] for obj in fixed) - min(box(obj)[0] for obj in fixed) if fixed else 0
        )
        fixed_height = (
            max(box(obj)[3] for obj in fixed) - min(box(obj)[1] for obj in fixed) if fixed else 0
        )
        # 有存量、阀门或流量标签时，骨架优先。只要自由节点已经达到
        # 一个小模块的规模，就按相邻锚点逐个展开；这样不会把参数全
        # 挤到骨架下方形成“云团”。小型旧示例仍保留原来的半环提案。
        use_local = bool(fixed) and (
            len(component) > 8 or max(fixed_width, fixed_height) > 720 or len(order) >= 5
        )
        if use_local:
            local = _local_anchor_positions(
                component,
                movable,
                nodes,
                neighbors,
                outgoing,
                gap,
                spacing,
            )
            for proposal in proposals:
                proposal.update({oid: point for oid, point in local.items() if oid in movable})
            continue
        if fixed:
            # 管道的阀门/附着标签不分离；反馈链与参数在骨架下方展开成环。
            left = min(box(obj)[0] for obj in fixed)
            right = max(box(obj)[2] for obj in fixed)
            cy = max(box(obj)[3] for obj in fixed) + gap
            rx = max((right - left) / 2 + gap, (sum(sizes.values()) + len(order) * gap) / math.pi)
            ry = max(rx * aspect, largest + spacing)
            cx = max((left + right) / 2, rx + margin)
            angles = [math.pi * (i + 0.5) / len(order) for i in range(len(order))]
        else:
            # 小型 CLD 保留椭圆；节点较多时使用圆角矩形周边，避免
            # 规则大圆造成“云团”与长对角线。两种方向仍交给质量评估器选择。
            rx = (largest + gap) / (2 * math.sin(math.pi / max(2, len(order))) * min(1, aspect))
            ry = rx * aspect
            cx, cy = cursor_x + rx, margin + ry + largest / 2
            angles = [-math.pi / 2 + math.tau * i / len(order) for i in range(len(order))]
            cursor_x += 2 * rx + largest + margin
        for proposal, traversal in zip(proposals, (order, list(reversed(order))), strict=False):
            positions = {
                oid: (nodes[oid].x, nodes[oid].y) for oid in component if oid not in movable
            }
            if not fixed and len(order) >= 8:
                positions.update(_perimeter_positions(traversal, nodes, (cx, cy), gap))
            else:
                for oid, angle in zip(traversal, angles, strict=False):
                    if len(order) == 1 and not fixed:
                        positions[oid] = (round(cx), round(cy))
                    else:
                        positions[oid] = (
                            round(cx + rx * math.cos(angle)),
                            round(cy + ry * math.sin(angle)),
                        )
            place_branches(component, core, nodes, neighbors, positions, (cx, cy), gap)
            if not any(oid not in movable for oid in component):
                shift = max(
                    0, margin - min(x - abs(nodes[oid].w) for oid, (x, y) in positions.items())
                )
                down = max(
                    0, margin - min(y - abs(nodes[oid].h) for oid, (x, y) in positions.items())
                )
                positions = {oid: (x + shift, y + down) for oid, (x, y) in positions.items()}
                cursor_x = max(
                    cursor_x,
                    max(x + abs(nodes[oid].w) for oid, (x, y) in positions.items()) + margin,
                )
            proposal.update(
                {oid: position for oid, position in positions.items() if oid in movable}
            )
    return proposals
