"""把 MDL 草图画成 SVG/HTML 预览，用于检查几何；最终结构图仍以 Vensim 导出为准。"""

from __future__ import annotations

import math
from html import escape
from pathlib import Path

from mdl_document import atomic_write, separate_output
from sketch_geometry import (
    LINE_HEIGHT,
    NATIVE_CLOUD_CODE,
    arrow_path,
    bounds,
    box,
    is_cloud_candidate,
    measure_view,
    pipe_endpoint_ids,
    shown,
    time_label_box,
    time_label_owners,
    visible,
    wrap_label,
)


def _color(value, default="#000000"):
    parts = value.split("-")
    if len(parts) == 3 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return "#" + "".join(f"{int(p):02x}" for p in parts)
    return default


def _polarity_spot(path, boxes, back=16.0, offset=9.0, pipes=()):
    """极性符号的位置：从箭头末端沿线往回约 16px，再向一侧偏 9px。

    两侧都试，选不压文字框、离管道至少 7px 的一侧；都不行时取左侧。
    """
    walked, i = 0.0, len(path) - 1
    while i > 0 and walked + math.dist(path[i], path[i - 1]) < back:
        walked += math.dist(path[i], path[i - 1])
        i -= 1
    if i == 0:
        ax, ay = path[0]
    else:
        (x1, y1), (x2, y2) = path[i - 1], path[i]
        t = (back - walked) / (math.dist(path[i], path[i - 1]) or 1.0)
        ax, ay = x2 + (x1 - x2) * t, y2 + (y1 - y2) * t
    ex, ey = path[-1]
    length = math.hypot(ex - ax, ey - ay) or 1.0
    nx, ny = -(ey - ay) / length, (ex - ax) / length
    sides = [(ax + nx * offset, ay + ny * offset), (ax - nx * offset, ay - ny * offset)]
    for x, y in sides:
        if not any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in boxes) and all(
            _point_segment_distance((x, y), a, b) >= 7 for a, b in pipes
        ):
            return x, y
    return sides[0]


def _point_segment_distance(point, a, b):
    (px, py), (ax, ay), (bx, by) = point, a, b
    dx, dy = bx - ax, by - ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1.0)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _cloud_points(x, y, w, h):
    """近似原生云图标的波浪轮廓。"""
    return " ".join(
        f"{x + max(w, 12) * (1 + 0.15 * math.sin(6 * t)) * math.cos(t):.2f},"
        f"{y + max(h, 9) * (1 + 0.15 * math.sin(6 * t)) * math.sin(t):.2f}"
        for t in [i * math.tau / 48 for i in range(49)]
    )


def _loop_symbol(x, y, radius, clockwise):
    """Vensim 回路标记的圆形箭头：圆周留约 50° 缺口，箭头指向回路走向。"""
    gap = math.radians(50)
    start = -math.pi / 2 + (gap / 2 if clockwise else -gap / 2)
    end = start + (math.tau - gap) * (1 if clockwise else -1)
    sx, sy = x + radius * math.cos(start), y + radius * math.sin(start)
    ex, ey = x + radius * math.cos(end), y + radius * math.sin(end)
    return (
        f'<path d="M{sx:.2f},{sy:.2f} A{radius:g},{radius:g} 0 1 {1 if clockwise else 0} '
        f'{ex:.2f},{ey:.2f}" fill="none" stroke="#222" stroke-width="1.1" '
        'marker-end="url(#head)"/>'
    )


def _pipe_is_downstream(view, arrow):
    """管道段是否通向下游端（箭头头部所在端）；规则与 natural 骨架识别一致。"""
    source, target = view.objects.get(arrow.from_id), view.objects.get(arrow.to_id)
    if source is None or target is None:
        return True
    if source.kind == 11 and target.kind != 11:
        return not bool(arrow.shape & 64)
    return False


def view_svg(view, lines, show_ids=False):
    """把一个视图画成调试 SVG：柔和圆弧、原生双线管道、云、阀门与 <Time> 预留区。"""
    paths = {arrow.obj_id: arrow_path(view, arrow) for arrow in view.arrows if shown(view, arrow)}
    points = [point for path in paths.values() for point in path]
    owners = time_label_owners(view)
    for obj in view.objects.values():
        if visible(obj):
            points.extend(((obj.x - obj.w, obj.y - obj.h), (obj.x + obj.w, obj.y + obj.h)))
    for owner in owners:
        rect = time_label_box(owner)
        points.extend(((rect[0], rect[1]), (rect[2], rect[3])))
    xmin, ymin, xmax, ymax = bounds(points)
    width, height = max(300, xmax - xmin + 80), max(160, ymax - ymin + 80)
    pipe_ends = pipe_endpoint_ids(view)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{xmin - 40:g} {ymin - 40:g} {width:g} {height:g}" role="img" aria-label="{escape(view.name, quote=True)}">',
        '<defs><marker id="head" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" fill="context-stroke"/></marker>'
        '<marker id="pipehead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="3.2" markerHeight="3.2" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#222"/></marker></defs>',
        '<style>text{font-family:Arial,"PingFang SC","Microsoft YaHei",sans-serif;fill:#161616} .id{font-size:9px;fill:#777} .time{font-size:10px;fill:#8a8a8a}</style>',
    ]
    # ---- 管道：双线，只在下游端画箭头头部 ----
    for arrow in view.arrows:
        path = paths.get(arrow.obj_id, [])
        if not path or not arrow.is_physical_flow:
            continue
        data = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in path)
        head = ' marker-end="url(#pipehead)"' if _pipe_is_downstream(view, arrow) else ""
        parts.append(f'<path d="{data}" fill="none" stroke="#222222" stroke-width="5"{head}/>')
        parts.append(f'<path d="{data}" fill="none" stroke="white" stroke-width="2.4"/>')
    # ---- 信息箭头：原生单控制点圆弧 ----
    label_boxes = [box(obj, 2) for obj in view.objects.values() if visible(obj)]
    pipe_segments = [
        (path[i], path[i + 1])
        for arrow in view.arrows
        if arrow.is_physical_flow
        for path in [paths.get(arrow.obj_id, [])]
        for i in range(len(path) - 1)
    ]
    for arrow in view.arrows:
        path = paths.get(arrow.obj_id, [])
        if not path or arrow.is_physical_flow:
            continue
        data = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in path)
        color = _color(arrow.fields[11])
        parts.append(
            f'<path d="{data}" fill="none" stroke="{color}" stroke-width="1.3" marker-end="url(#head)"/>'
        )
        polarity = int(arrow.fields[6] or 0)
        if 32 <= polarity <= 126:
            px, py = _polarity_spot(path, label_boxes, pipes=pipe_segments)
            parts.append(
                f'<text x="{px:.2f}" y="{py:.2f}" font-size="13" text-anchor="middle" dominant-baseline="central">{escape(chr(polarity))}</text>'
            )
        # dtype 的原值保留在模型中；预览只标注有意义的低位延迟标记。
        if int(arrow.fields[9] or 0) & 3:
            mid = path[len(path) // 2]
            parts.append(f'<text x="{mid[0]:.2f}" y="{mid[1] - 5:.2f}" font-size="12">||</text>')
    # ---- 原生自动 <Time> 标签的预留区（虚线框，仅供调试） ----
    for owner in owners:
        x0, y0, x1, y1 = time_label_box(owner)
        parts.append(
            f'<rect x="{x0:g}" y="{y0:g}" width="{x1 - x0:g}" height="{y1 - y0:g}" fill="none" stroke="#b0b0b0" stroke-dasharray="3 2"/>'
        )
        parts.append(
            f'<text class="time" x="{(x0 + x1) / 2:g}" y="{(y0 + y1) / 2:g}" text-anchor="middle" dominant-baseline="central">&lt;Time&gt;</text>'
        )
    # ---- 对象 ----
    for obj in view.objects.values():
        if not visible(obj):
            continue
        x, y, w, h = obj.x, obj.y, abs(obj.w), abs(obj.h)
        label = ""
        if obj.kind == 10:
            if obj.shape & 31 == 3 and not obj.is_shadow:
                parts.append(
                    f'<rect x="{x - w:g}" y="{y - h:g}" width="{2 * w:g}" height="{2 * h:g}" fill="white" stroke="#222" stroke-width="1.4"/>'
                )
            label = f"<{obj.name}>" if obj.is_shadow else obj.name
        elif obj.kind == 11:
            parts.append(
                f'<path d="M{x - w:g},{y - h:g} L{x + w:g},{y + h:g} L{x + w:g},{y - h:g} L{x - w:g},{y + h:g} Z" fill="white" stroke="#222"/>'
            )
        elif is_cloud_candidate(obj, pipe_ends):
            if obj.name.strip() == NATIVE_CLOUD_CODE:
                parts.append(
                    f'<polygon points="{_cloud_points(x, y, w, h)}" fill="white" stroke="#222"/>'
                )
            else:
                # 非 48 代码：原生 Vensim 不显示云，预览用红色虚线提示需要修复。
                parts.append(
                    f'<polygon points="{_cloud_points(x, y, w, h)}" fill="none" stroke="#c0392b" stroke-dasharray="3 2"><title>非原生云代码 {escape(obj.name)}：Vensim 不会显示云图标</title></polygon>'
                )
        elif obj.kind == 12 and obj.bits & 4:
            label = lines[obj.line_index + 1].strip() if obj.line_index + 1 < len(lines) else ""
            if obj.shape & 31 in (4, 5):
                parts.append(_loop_symbol(x, y, max(w, h, 10), obj.shape & 31 == 4))
        else:
            label = f"[object {obj.obj_id}]"
        if label:
            font = obj.font
            # 影子的尖括号另占约 6px 半宽，与 sketch_geometry.box 的留白一致；存量框内
            # 左右各留 8px（按字号缩放），与排版时的存量尺寸口径一致
            boxed = obj.kind == 10 and obj.shape & 31 == 3 and not obj.is_shadow
            wrap_width = w + (6 if obj.is_shadow else 0) - (8 * font / 12 if boxed else 0)
            rows = wrap_label(
                label, wrap_width if obj.kind == 10 and not obj.attached_to_valve else 0, font
            )
            first = -(len(rows) - 1) / 2 * font * LINE_HEIGHT
            spans = "".join(
                f'<tspan x="{x:g}" dy="{(first if i == 0 else font * LINE_HEIGHT):g}">{escape(row)}</tspan>'
                for i, row in enumerate(rows)
            )
            parts.append(
                f'<text class="label" x="{x:g}" y="{y:g}" font-size="{font}" text-anchor="middle" dominant-baseline="central">{spans}</text>'
            )
        if show_ids:
            parts.append(f'<text class="id" x="{x + w + 4:g}" y="{y - h:g}">{obj.obj_id}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def render_preview(path: Path, output: Path, compare_with: Path | None = None, show_ids=False):
    from vensim_autolayout import load_mdl

    separate_output(output, [path, *([compare_with] if compare_with else [])])
    models = [compare_with, path] if compare_with else [path]
    documents = [load_mdl(model) for model in models]
    if output.suffix.lower() == ".svg":
        if compare_with or len(documents[0][1]) != 1:
            raise ValueError("多视图或前后比较请输出 .html；单视图可输出 .svg")
        result = view_svg(documents[0][1][0], documents[0][0], show_ids)
    elif output.suffix.lower() == ".html":
        sections = []
        for column, (model, (lines, views)) in enumerate(zip(models, documents, strict=False)):
            title = ("Before" if column == 0 else "After") if compare_with else model.stem
            sections.append(f"<section><h1>{escape(title)}</h1>")
            for view in views:
                metrics = measure_view(view)
                sections.append(
                    f"<h2>{escape(view.name)}</h2><p>Node overlaps: {len(metrics['node_overlaps'])} · "
                    f"Line–label collisions: {len(metrics['arrow_node_collisions'])} · "
                    f"Crossings: {len(metrics['arrow_crossings']) + len(metrics['flow_crossings'])} · "
                    f"Short arrows: {len(metrics['short_arrows'])} · "
                    f"&lt;Time&gt; conflicts: {len(metrics['time_label_conflicts'])} · "
                    f"Non-native clouds: {len(metrics['invisible_clouds'])}</p>"
                )
                svg = (
                    view_svg(view, lines, show_ids)
                    .replace('id="head"', f'id="head-{column}-{view.index}"')
                    .replace("url(#head)", f"url(#head-{column}-{view.index})")
                )
                sections.append(svg)
            sections.append("</section>")
        result = (
            '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Vensim sketch review</title><style>body{margin:24px;color:#222;background:white;font:14px Arial,sans-serif}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:32px}h1{font-size:20px}h2{font-size:15px;margin-top:28px}p{font-size:12px;color:#666}svg{width:100%;border-top:1px solid #ddd}footer{margin-top:24px;font-size:12px;color:#555}</style><main>'
            + "".join(sections)
            + "</main><footer>Geometry preview only. Check fonts and shapes in Vensim.</footer></html>"
        )
    else:
        raise ValueError("预览输出必须是 .html 或 .svg")
    atomic_write(output, result.encode("utf-8"))
    return {"preview": str(output)}
