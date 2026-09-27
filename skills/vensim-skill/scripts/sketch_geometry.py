"""Vensim 圆弧几何与碰撞检查；使用二维行列式，不依赖 NumPy。"""
from __future__ import annotations

import math

EPS = 1e-8


def bounds(points):
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def box(obj, padding=0.0):
    # 影子的尖括号需要额外留白；原生字体可能改变实际宽度，仍需原生审图。
    shadow_margin = 6.0 if getattr(obj, "is_shadow", False) else 0.0
    return (obj.x - abs(obj.w) - padding - shadow_margin, obj.y - abs(obj.h) - padding,
            obj.x + abs(obj.w) + padding + shadow_margin, obj.y + abs(obj.h) + padding)


def boxes_intersect(a, b):
    return a[0] < b[2] - EPS and b[0] < a[2] - EPS and a[1] < b[3] - EPS and b[1] < a[3] - EPS


def inside(point, rect):
    return rect[0] - EPS <= point[0] <= rect[2] + EPS and rect[1] - EPS <= point[1] <= rect[3] + EPS


def segment_box(a, b, rect):
    """Liang–Barsky 裁剪，返回进入和离开矩形的参数。"""
    lo, hi = 0.0, 1.0
    for origin, delta, lower, upper in (
        (a[0], b[0] - a[0], rect[0], rect[2]),
        (a[1], b[1] - a[1], rect[1], rect[3]),
    ):
        if abs(delta) < EPS:
            if origin < lower or origin > upper:
                return None
        else:
            t1, t2 = sorted(((lower - origin) / delta, (upper - origin) / delta))
            lo, hi = max(lo, t1), min(hi, t2)
            if lo > hi + EPS:
                return None
    return lo, hi


def lerp(a, b, t):
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def circle_path(start, control, end, tolerance=0.5):
    """经过三个点的圆弧，选取包含控制点的有向弧；共线退化为直线。"""
    bx, by = control[0] - start[0], control[1] - start[1]
    cx, cy = end[0] - start[0], end[1] - start[1]
    determinant = 2 * (bx * cy - by * cx)
    if abs(determinant) < EPS * max(1, math.hypot(bx, by) * math.hypot(cx, cy)):
        return [start, end]
    ux = (cy * (bx * bx + by * by) - by * (cx * cx + cy * cy)) / determinant
    uy = (bx * (cx * cx + cy * cy) - cx * (bx * bx + by * by)) / determinant
    center = start[0] + ux, start[1] + uy
    radius = math.hypot(ux, uy)
    first = math.atan2(start[1] - center[1], start[0] - center[0])
    middle = math.atan2(control[1] - center[1], control[0] - center[0])
    last = math.atan2(end[1] - center[1], end[0] - center[0])
    sweep = (last - first) % math.tau
    if (middle - first) % math.tau > sweep + EPS:
        sweep -= math.tau
    step = 2 * math.acos(max(-1.0, min(1.0, 1 - tolerance / radius)))
    count = max(8, min(512, math.ceil(abs(sweep) / max(step, 0.001))))
    path = [(center[0] + radius * math.cos(first + sweep * i / count),
             center[1] + radius * math.sin(first + sweep * i / count)) for i in range(count + 1)]
    path[0], path[-1] = start, end
    return path


def trim_path(points, source, target):
    """把中心到中心的路径裁剪到文字框边界，避免预览箭头指到字中间。"""
    points = list(points)
    for rect, reverse in ((box(source), False), (box(target), True)):
        if reverse:
            points.reverse()
        index = 0
        while index + 1 < len(points) and inside(points[index + 1], rect):
            index += 1
        points = points[index:]
        if len(points) >= 2 and inside(points[0], rect):
            crossing = segment_box(points[0], points[1], rect)
            if crossing is not None:
                points[0] = lerp(points[0], points[1], crossing[1])
        if reverse:
            points.reverse()
    return points if len(points) >= 2 else []


def arrow_path(view, arrow, control=None):
    source, target = view.objects.get(arrow.from_id), view.objects.get(arrow.to_id)
    if source is None or target is None:
        return []
    start, end = (source.x, source.y), (target.x, target.y)
    points = [control] if control is not None else arrow.points
    shape = 1 if control is not None else arrow.shape
    if shape == 1 and len(points) == 1:
        path = circle_path(start, points[0], end)
    elif shape == 0:
        path = [start, end]
    else:
        # 未识别的样条只提供近似预览，质量报告会标注未覆盖对象。
        path = [start, *points, end]
    return trim_path(path, source, target)


def segment_intersects(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    if not (max(a[0], b[0]) + EPS >= min(c[0], d[0]) and
            max(c[0], d[0]) + EPS >= min(a[0], b[0]) and
            max(a[1], b[1]) + EPS >= min(c[1], d[1]) and
            max(c[1], d[1]) + EPS >= min(a[1], b[1])):
        return False
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if max(abs(o1), abs(o2), abs(o3), abs(o4)) < EPS:
        # 共线重叠也会导致双向信息线看起来只有一根。
        axis = 0 if abs(b[0] - a[0]) >= abs(b[1] - a[1]) else 1
        overlap = min(max(a[axis], b[axis]), max(c[axis], d[axis])) - max(min(a[axis], b[axis]), min(c[axis], d[axis]))
        return overlap > 1.0
    if any(math.dist(p, q) < 0.1 for p in (a, b) for q in (c, d)):
        return False
    return o1 * o2 <= EPS and o3 * o4 <= EPS


def paths_cross(left, right):
    if not left or not right:
        return False
    a, b = bounds(left), bounds(right)
    if a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]:
        return False
    return any(segment_intersects(p, q, r, s) for p, q in zip(left, left[1:]) for r, s in zip(right, right[1:]))


def path_hits_box(path, rect):
    if not path:
        return False
    a = bounds(path)
    if a[2] < rect[0] or a[0] > rect[2] or a[3] < rect[1] or a[1] > rect[3]:
        return False
    return any(segment_box(a, b, rect) is not None for a, b in zip(path, path[1:]))


def path_length(path):
    return sum(math.dist(a, b) for a, b in zip(path, path[1:]))


def visible(obj):
    index = 5 if hasattr(obj, "from_id") else 9
    fields = obj.fields if hasattr(obj, "fields") else obj.raw_fields
    return len(fields) <= index or fields[index] in ("", "0")


def measure_view(view, clearance=6.0):
    objects = [obj for obj in view.objects.values() if visible(obj)]
    variables = [obj for obj in objects if obj.kind == 10]
    arrows = [arrow for arrow in view.arrows if visible(arrow)]
    paths = {arrow.obj_id: arrow_path(view, arrow) for arrow in arrows}
    information = [arrow for arrow in arrows if not arrow.is_physical_flow]
    physical = [arrow for arrow in arrows if arrow.is_physical_flow]
    overlaps = [[a.obj_id, b.obj_id] for i, a in enumerate(variables) for b in variables[i + 1:]
                if boxes_intersect(box(a, clearance / 2), box(b, clearance / 2))]
    collisions = []
    for arrow in information:
        for obj in objects:
            if obj.obj_id not in (arrow.from_id, arrow.to_id) and path_hits_box(paths[arrow.obj_id], box(obj, clearance)):
                collisions.append({"arrow": arrow.obj_id, "object": obj.obj_id})
    crossings = [[a.obj_id, b.obj_id] for i, a in enumerate(information) for b in information[i + 1:]
                 if paths_cross(paths[a.obj_id], paths[b.obj_id])]
    flow_crossings = [[a.obj_id, b.obj_id] for a in information for b in physical
                      if paths_cross(paths[a.obj_id], paths[b.obj_id])]
    unsupported = [a.obj_id for a in information if a.shape not in (0, 1, 4) or (a.shape == 1 and len(a.points) != 1)]
    broken = [a.obj_id for a in view.arrows if a.from_id not in view.objects or a.to_id not in view.objects]
    shadow_inputs = [a.obj_id for a in information if a.to_id in view.objects and view.objects[a.to_id].is_shadow]
    shadow_ids = {obj.obj_id for obj in variables if obj.is_shadow}
    shadow_overlaps = [pair for pair in overlaps if shadow_ids.intersection(pair)]
    defined = {}
    for obj in view.objects.values():
        if obj.kind == 10 and not obj.is_shadow:
            defined.setdefault(obj.name, []).append(obj.obj_id)
    duplicate_defined = [{"variable": name, "ids": ids} for name, ids in defined.items() if len(ids) > 1]
    return {
        "node_overlaps": overlaps, "arrow_node_collisions": collisions,
        "arrow_crossings": crossings, "flow_crossings": flow_crossings,
        "unsupported_arrows": unsupported, "broken_arrows": broken,
        "shadow_inputs": shadow_inputs,
        "shadow_overlaps": shadow_overlaps,
        "duplicate_defined": duplicate_defined,
        "shadow_objects": [{"id": obj.obj_id, "variable": obj.name} for obj in variables if obj.is_shadow],
        "total_information_length": round(sum(path_length(paths[a.obj_id]) for a in information), 2),
        "approximation_tolerance_px": 0.5,
    }


def quality_key(metrics):
    """优先避开文字与断链，再减少交叉，最后缩短路径。"""
    return (len(metrics["broken_arrows"]) + len(metrics["shadow_inputs"]) + len(metrics.get("duplicate_defined", [])),
            len(metrics["node_overlaps"]), len(metrics["arrow_node_collisions"]),
            len(metrics["arrow_crossings"]) + len(metrics["flow_crossings"]),
            metrics["total_information_length"])


def quality_pass(metrics, max_crossings=0):
    return (not any(metrics.get(key) for key in ("broken_arrows", "shadow_inputs", "duplicate_defined", "node_overlaps", "arrow_node_collisions", "unsupported_arrows"))
            and len(metrics["arrow_crossings"]) + len(metrics["flow_crossings"]) <= max_crossings)
