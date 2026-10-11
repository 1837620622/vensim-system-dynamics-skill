"""Vensim 圆弧几何与碰撞检查；使用二维行列式，不依赖 NumPy。"""

from __future__ import annotations

import functools
import math

EPS = 1e-8

# ---------------------------------------------------------------------------
# 原生显示约束（由 Vensim 10.5 导出 SVG 实测，坐标即模型坐标）
# ---------------------------------------------------------------------------
# 信息箭头在两端文字框之间的可见长度。两框几乎贴住时原生会把箭头画成近乎
# 整圆的大弧，检查只拦这种情况：Vensim 官方示例中最短的 5% 约 12px，教材
# 模型约 30px，下限取 20px。自动排版另按 PREFERRED_ARROW 留出更舒展的长度。
MIN_VISIBLE_ARROW = 20.0
PREFERRED_ARROW = 38.0
# 检查时信息线与文字框、文字框之间的净距（像素）。官方示例中约 10% 的连线
# 离其他文字不到 5px，检查取 3px；自动排版生成时用 6px
CHECK_CLEARANCE = 3.0
LAYOUT_CLEARANCE = 6.0
# 定义实例的方程直接引用 Time、且视图中没有从 Time 画出的箭头时，原生
# 会在变量正下方自动绘制灰色 "<Time>"。实测中心约在 +28~30px，字框约
# 40x16；这里取半宽 24、半高 10 作为保守预留区。
TIME_LABEL_OFFSET = 29.0
TIME_LABEL_HALF_WIDTH = 24.0
TIME_LABEL_HALF_HEIGHT = 10.0
# 原生云图标在对象记录中写作 12,id,48,...；写成 0 的源汇不会显示云。
NATIVE_CLOUD_CODE = "48"
# 多行变量名的行高与字号之比，预览按同一比例排字。
LINE_HEIGHT = 1.15


def bounds(points):
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    xs, ys = zip(*points, strict=True)
    return (min(xs), min(ys), max(xs), max(ys))


def half_height(obj):
    """对象的显示半高。

    普通变量名按记录半宽折行，折成两三行后的文字比记录里的半高（通常是 8）
    高出不少。原生 Vensim 按实际文字范围画箭头端点，检查和排版也按折行后的
    高度算，否则箭头会压到第二、三行字上。存量框内的文字和附着在阀门上的
    流量名不按这个规则处理。
    """
    h = abs(obj.h)
    if obj.kind != 10 or not obj.name or obj.shape & 32 or obj.shape & 31 == 3:
        return h
    shadow = getattr(obj, "is_shadow", False)
    label = f"<{obj.name}>" if shadow else obj.name
    font = getattr(obj, "font", 12)
    rows = _row_count(label, abs(obj.w) + (6 if shadow else 0), font)
    return max(h, rows * font * LINE_HEIGHT / 2)


def box(obj, padding=0.0):
    # 影子的尖括号需要额外留白；原生字体可能改变实际宽度，仍需原生审图。
    shadow_margin = 6.0 if getattr(obj, "is_shadow", False) else 0.0
    h = half_height(obj)
    return (
        obj.x - abs(obj.w) - padding - shadow_margin,
        obj.y - h - padding,
        obj.x + abs(obj.w) + padding + shadow_margin,
        obj.y + h + padding,
    )


def text_width(text, font=12):
    """估计单行文字宽度：中日韩字符约一个字号宽，其余约 0.55 个。"""
    return sum(font if ord(c) >= 0x2E80 else 0.55 * font for c in text)


def wrap_label(text, half_width, font=12):
    """按 Vensim 的方式在文字框宽度内折行：西文按空格断词，中文按字断开。"""
    if half_width <= 0:
        return [text]
    limit = 2 * half_width
    tokens = []
    for word in text.split(" "):
        if any(ord(c) >= 0x2E80 for c in word):
            tokens.extend(word)
        else:
            tokens.append(word)
    rows, current = [], ""
    for token in tokens:
        cjk = len(token) == 1 and ord(token) >= 0x2E80
        joined = current + token if (cjk or not current) else current + " " + token
        if current and text_width(joined, font) > limit:
            rows.append(current)
            current = token
        else:
            current = joined
    rows.append(current)
    return rows


@functools.lru_cache(maxsize=4096)
def _row_count(text, half_width, font):
    return len(wrap_label(text, half_width, font))


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
    path = [
        (
            center[0] + radius * math.cos(first + sweep * i / count),
            center[1] + radius * math.sin(first + sweep * i / count),
        )
        for i in range(count + 1)
    ]
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


def segment_intersects(a, b, c, d, ends=None):
    """两线段是否相交。ends 给出两条路径的首尾点：只有在这些首尾点附近
    的相接才不算交叉（两条箭头指向同一变量时终点会落在同一处）。"""

    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    if not (
        max(a[0], b[0]) + EPS >= min(c[0], d[0])
        and max(c[0], d[0]) + EPS >= min(a[0], b[0])
        and max(a[1], b[1]) + EPS >= min(c[1], d[1])
        and max(c[1], d[1]) + EPS >= min(a[1], b[1])
    ):
        return False
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if max(abs(o1), abs(o2), abs(o3), abs(o4)) < EPS:
        # 共线重叠也会导致双向信息线看起来只有一根。
        axis = 0 if abs(b[0] - a[0]) >= abs(b[1] - a[1]) else 1
        overlap = min(max(a[axis], b[axis]), max(c[axis], d[axis])) - max(
            min(a[axis], b[axis]), min(c[axis], d[axis])
        )
        return overlap > 1.0
    for p in (a, b):
        for q in (c, d):
            if math.dist(p, q) < 0.1 and (ends is None or any(math.dist(p, e) < 0.1 for e in ends)):
                return False
    return o1 * o2 <= EPS and o3 * o4 <= EPS


def paths_cross(left, right, left_bounds=None, right_bounds=None):
    """两条折线是否相交；可传入预先算好的外接矩形以省去重复计算。"""
    if not left or not right:
        return False
    a = left_bounds or bounds(left)
    b = right_bounds or bounds(right)
    if a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]:
        return False

    # 只保留落在对方外接矩形内的线段，再做逐段判断
    def near(path, rect):
        return [
            (p, q)
            for p, q in zip(path, path[1:], strict=False)
            if max(p[0], q[0]) + EPS >= rect[0]
            and min(p[0], q[0]) - EPS <= rect[2]
            and max(p[1], q[1]) + EPS >= rect[1]
            and min(p[1], q[1]) - EPS <= rect[3]
        ]

    first, second = near(left, b), near(right, a)
    ends = (left[0], left[-1], right[0], right[-1])
    return any(segment_intersects(p, q, r, s, ends) for p, q in first for r, s in second)


def path_hits_box(path, rect, path_bounds=None):
    if not path:
        return False
    a = path_bounds or bounds(path)
    if a[2] < rect[0] or a[0] > rect[2] or a[3] < rect[1] or a[1] > rect[3]:
        return False
    return any(segment_box(a, b, rect) is not None for a, b in zip(path, path[1:], strict=False))


def path_length(path):
    return sum(math.dist(a, b) for a, b in zip(path, path[1:], strict=False))


def time_label_box(obj, padding=0.0):
    """返回原生自动 "<Time>" 标签的预留矩形。"""
    cx, cy = obj.x, obj.y + TIME_LABEL_OFFSET
    return (
        cx - TIME_LABEL_HALF_WIDTH - padding,
        cy - TIME_LABEL_HALF_HEIGHT - padding,
        cx + TIME_LABEL_HALF_WIDTH + padding,
        cy + TIME_LABEL_HALF_HEIGHT + padding,
    )


def time_label_owners(view):
    """列出原生会自动附加 "<Time>" 的定义实例。

    条件：本视图中可见的定义实例（非影子）、方程直接引用 Time、且视图中
    没有可见箭头从名为 Time 的对象指向它。名称集合由读取模型时从方程区
    提取（view.time_dependents，已规范化为 canonical_name）。
    """
    names = getattr(view, "time_dependents", None)
    if not names:
        return []
    from vensim_engine import canonical_name

    time_ids = {
        oid
        for oid, obj in view.objects.items()
        if obj.kind == 10 and canonical_name(obj.name) == "time"
    }
    fed = {a.to_id for a in view.arrows if visible(a) and a.from_id in time_ids}
    return [
        obj
        for obj in view.objects.values()
        if obj.kind == 10
        and visible(obj)
        and not obj.is_shadow
        and obj.obj_id not in fed
        and canonical_name(obj.name) in names
    ]


def pipe_endpoint_ids(view):
    """管道（原生双线）连接的全部对象 ID。"""
    ids = set()
    for arrow in view.arrows:
        if arrow.is_physical_flow:
            ids.update((arrow.from_id, arrow.to_id))
    return ids


def is_cloud_candidate(obj, pipe_ends):
    """判断 12 类对象是否为管道端点的源/汇（而不是带文字的注释）。

    注释对象的 bits 含续行位（4）或形状非 0；源汇对象形状为 0、无续行文字。
    """
    return (
        obj.kind == 12 and obj.obj_id in pipe_ends and (obj.shape & 31) == 0 and not (obj.bits & 4)
    )


def invisible_clouds(view):
    """管道端点的源汇写成非 48 代码时，原生 Vensim 不显示云图标。"""
    ends = pipe_endpoint_ids(view)
    return [
        obj.obj_id
        for obj in view.objects.values()
        if is_cloud_candidate(obj, ends) and obj.name.strip() != NATIVE_CLOUD_CODE and visible(obj)
    ]


def visible(obj):
    index = 5 if hasattr(obj, "from_id") else 9
    fields = obj.fields if hasattr(obj, "fields") else obj.raw_fields
    return len(fields) <= index or fields[index] in ("", "0")


def shown(view, arrow):
    """箭头在图上可见：自身未隐藏，两端对象也未隐藏。

    Vensim 隐藏一个变量时，进出它的箭头一起隐藏（Hide/unhide 工具说明），
    即使箭头记录自己的隐藏深度仍是 0。
    """
    if not visible(arrow):
        return False
    return all(
        view.objects.get(oid) is None or visible(view.objects[oid])
        for oid in (arrow.from_id, arrow.to_id)
    )


def measure_view(view, clearance=CHECK_CLEARANCE, min_arrow=MIN_VISIBLE_ARROW):
    from vensim_engine import canonical_name

    objects = [obj for obj in view.objects.values() if visible(obj)]
    variables = [obj for obj in objects if obj.kind == 10]
    arrows = [arrow for arrow in view.arrows if shown(view, arrow)]
    paths = {arrow.obj_id: arrow_path(view, arrow) for arrow in arrows}
    information = [arrow for arrow in arrows if not arrow.is_physical_flow]
    physical = [arrow for arrow in arrows if arrow.is_physical_flow]
    overlaps = [
        [a.obj_id, b.obj_id]
        for i, a in enumerate(variables)
        for b in variables[i + 1 :]
        if boxes_intersect(box(a, clearance / 2), box(b, clearance / 2))
    ]
    ordered_objects = sorted(objects, key=lambda item: item.line_index)
    attached_parents = {
        current.obj_id: previous.obj_id
        for previous, current in zip(ordered_objects, ordered_objects[1:], strict=False)
        if previous.kind == 11 and current.kind == 10 and current.attached_to_valve
    }
    # 阀门与它的流量名紧挨着：连到其中一个的箭头经过另一个不算穿字
    partner = {**attached_parents, **{v: k for k, v in attached_parents.items()}}
    collisions = []
    for arrow in information:
        ends = {arrow.from_id, arrow.to_id}
        ends |= {partner[e] for e in ends if e in partner}
        for obj in objects:
            if obj.obj_id not in ends and path_hits_box(paths[arrow.obj_id], box(obj, clearance)):
                collisions.append({"arrow": arrow.obj_id, "object": obj.obj_id})
    physical_collisions = [
        {"arrow": arrow.obj_id, "object": obj.obj_id}
        for arrow in physical
        for obj in objects
        if obj.obj_id not in (arrow.from_id, arrow.to_id)
        and attached_parents.get(obj.obj_id) != arrow.from_id
        and path_hits_box(paths[arrow.obj_id], box(obj, clearance))
    ]
    crossings = [
        [a.obj_id, b.obj_id]
        for i, a in enumerate(information)
        for b in information[i + 1 :]
        if paths_cross(paths[a.obj_id], paths[b.obj_id])
    ]
    flow_crossings = [
        [a.obj_id, b.obj_id]
        for a in information
        for b in physical
        if paths_cross(paths[a.obj_id], paths[b.obj_id])
    ]
    unsupported = [
        a.obj_id
        for a in information
        if a.shape not in (0, 1, 4) or (a.shape == 1 and len(a.points) != 1)
    ]
    broken = [
        a.obj_id
        for a in view.arrows
        if a.from_id not in view.objects or a.to_id not in view.objects
    ]
    shadow_inputs = [
        a.obj_id for a in information if a.to_id in view.objects and view.objects[a.to_id].is_shadow
    ]
    # 过短箭头：两端裁剪后可见长度不足时，原生可能画成近乎整圆的大弧。
    short_arrows = [
        {"arrow": a.obj_id, "length": round(path_length(paths[a.obj_id]), 1)}
        for a in information
        if a.from_id in view.objects
        and a.to_id in view.objects
        and path_length(paths[a.obj_id]) < min_arrow
    ]
    # 原生自动 "<Time>" 标签的预留区：任何文字框、信息线或管道都不得进入。
    owners = time_label_owners(view)
    time_conflicts = []
    for owner in owners:
        rect = time_label_box(owner)
        for obj in objects:
            if obj.obj_id != owner.obj_id and boxes_intersect(rect, box(obj, clearance / 2)):
                time_conflicts.append({"owner": owner.obj_id, "object": obj.obj_id})
        for arrow in arrows:
            if path_hits_box(paths[arrow.obj_id], rect):
                time_conflicts.append({"owner": owner.obj_id, "arrow": arrow.obj_id})
    all_shadows = [obj for obj in view.objects.values() if obj.kind == 10 and obj.is_shadow]
    shadow_ids = {obj.obj_id for obj in variables if obj.is_shadow}
    shadow_overlaps = [pair for pair in overlaps if shadow_ids.intersection(pair)]
    # 记录可见对象的实际包围盒。长度和交叉数相同的候选布局中，优先选择
    # 更紧凑的版面，避免把少量节点拉到画布两端后留下大块空白。
    object_bounds = bounds(
        [
            corner
            for obj in objects
            for corner in (
                (box(obj)[0], box(obj)[1]),
                (box(obj)[2], box(obj)[3]),
            )
        ]
    )
    layout_width = max(0.0, object_bounds[2] - object_bounds[0])
    layout_height = max(0.0, object_bounds[3] - object_bounds[1])
    defined = {}
    names = {}
    for obj in view.objects.values():
        if obj.kind == 10 and not obj.is_shadow:
            key = canonical_name(obj.name)
            names.setdefault(key, obj.name)
            defined.setdefault(key, []).append(obj.obj_id)
    duplicate_defined = [
        {"variable": names[name], "ids": ids} for name, ids in defined.items() if len(ids) > 1
    ]
    return {
        "node_overlaps": overlaps,
        "arrow_node_collisions": collisions,
        "physical_node_collisions": physical_collisions,
        "arrow_crossings": crossings,
        "flow_crossings": flow_crossings,
        "unsupported_arrows": unsupported,
        "short_arrows": short_arrows,
        "time_label_owners": [obj.obj_id for obj in owners],
        "time_label_conflicts": time_conflicts,
        "invisible_clouds": invisible_clouds(view),
        "broken_arrows": broken,
        "shadow_inputs": shadow_inputs,
        "shadow_overlaps": shadow_overlaps,
        "duplicate_defined": duplicate_defined,
        "shadow_objects": [
            {"id": obj.obj_id, "variable": obj.name, "hidden": not visible(obj)}
            for obj in all_shadows
        ],
        "hidden_shadow_objects": [
            {"id": obj.obj_id, "variable": obj.name} for obj in all_shadows if not visible(obj)
        ],
        "total_information_length": round(
            sum(path_length(paths[a.obj_id]) for a in information), 2
        ),
        "layout_bounds": [round(value, 2) for value in object_bounds],
        "layout_width": round(layout_width, 2),
        "layout_height": round(layout_height, 2),
        "layout_span": round(layout_width + layout_height, 2),
        "approximation_tolerance_px": 0.5,
    }


def quality_key(metrics):
    """排序依据：结构错误 > 文字重叠 > 连线穿字或过短 > 连线交叉 > 总长 > 版面尺寸。

    连线穿过文字比交叉更难读，所以排在交叉之前；K3,3 这类必有交叉的结构
    不会为了消除交叉而让连线穿过变量名。
    """
    return (
        len(metrics["broken_arrows"])
        + len(metrics["shadow_inputs"])
        + len(metrics.get("duplicate_defined", [])),
        len(metrics["node_overlaps"]),
        len(metrics["arrow_node_collisions"])
        + len(metrics.get("physical_node_collisions", []))
        + len(metrics.get("time_label_conflicts", []))
        + len(metrics.get("short_arrows", [])),
        len(metrics["arrow_crossings"]) + len(metrics["flow_crossings"]),
        metrics["total_information_length"],
        metrics.get("layout_span", 0.0),
    )


def quality_pass(metrics, max_crossings=0):
    return (
        not any(
            metrics.get(key)
            for key in (
                "broken_arrows",
                "shadow_inputs",
                "duplicate_defined",
                "node_overlaps",
                "arrow_node_collisions",
                "physical_node_collisions",
                "unsupported_arrows",
                "short_arrows",
                "time_label_conflicts",
                "invisible_clouds",
            )
        )
        and len(metrics["arrow_crossings"]) + len(metrics["flow_crossings"]) <= max_crossings
    )
