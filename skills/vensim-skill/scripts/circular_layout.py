"""按真实连接组织环形位置；只提议坐标，不生成或改变因果关系。"""
from __future__ import annotations

import math

from sketch_geometry import box, visible


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
    for previous, obj in zip(ordered, ordered[1:]):
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
            linked = sorted(neighbors[oid] & remaining,
                            key=lambda other: (other not in outgoing[oid], -len(neighbors[other]), other))
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
            positions[oid] = (round(px + distance * math.cos(angle + spread)),
                              round(py + distance * math.sin(angle + spread)))


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
        sizes = {oid: math.hypot(box(nodes[oid])[2] - box(nodes[oid])[0], 2 * abs(nodes[oid].h))
                 for oid in order}
        largest = max(sizes.values(), default=gap)
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
            # 用相邻节点最大对角线约束最短弦长，长中文名和影子括号均计入。
            rx = (largest + gap) / (2 * math.sin(math.pi / max(2, len(order))) * min(1, aspect))
            ry = rx * aspect
            cx, cy = cursor_x + rx, margin + ry + largest / 2
            angles = [-math.pi / 2 + math.tau * i / len(order) for i in range(len(order))]
            cursor_x += 2 * rx + largest + margin
        for proposal, traversal in zip(proposals, (order, list(reversed(order)))):
            positions = {oid: (nodes[oid].x, nodes[oid].y) for oid in component if oid not in movable}
            for oid, angle in zip(traversal, angles):
                if len(order) == 1 and not fixed:
                    positions[oid] = (round(cx), round(cy))
                else:
                    positions[oid] = (round(cx + rx * math.cos(angle)), round(cy + ry * math.sin(angle)))
            place_branches(component, core, nodes, neighbors, positions, (cx, cy), gap)
            if not any(oid not in movable for oid in component):
                shift = max(0, margin - min(x - abs(nodes[oid].w) for oid, (x, y) in positions.items()))
                down = max(0, margin - min(y - abs(nodes[oid].h) for oid, (x, y) in positions.items()))
                positions = {oid: (x + shift, y + down) for oid, (x, y) in positions.items()}
                cursor_x = max(cursor_x, max(x + abs(nodes[oid].w) for oid, (x, y) in positions.items()) + margin)
            proposal.update({oid: position for oid, position in positions.items() if oid in movable})
    return proposals
