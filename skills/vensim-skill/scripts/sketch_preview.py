"""直接从 MDL 几何生成可检查的 SVG，不把 Graphviz 图冒充原生草图。"""
from __future__ import annotations

from html import escape
import math
from pathlib import Path

from mdl_document import atomic_write, separate_output
from sketch_geometry import arrow_path, bounds, measure_view, visible


def _color(value, default="#000000"):
    parts = value.split("-")
    if len(parts) == 3 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return "#" + "".join(f"{int(p):02x}" for p in parts)
    return default


def view_svg(view, lines, show_ids=False):
    paths = {arrow.obj_id: arrow_path(view, arrow) for arrow in view.arrows if visible(arrow)}
    points = [point for path in paths.values() for point in path]
    for obj in view.objects.values():
        if visible(obj):
            points.extend(((obj.x - obj.w, obj.y - obj.h), (obj.x + obj.w, obj.y + obj.h)))
    xmin, ymin, xmax, ymax = bounds(points)
    width, height = max(300, xmax - xmin + 80), max(160, ymax - ymin + 80)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{xmin-40:g} {ymin-40:g} {width:g} {height:g}" role="img" aria-label="{escape(view.name, quote=True)}">',
             '<metadata>geometry_debug_preview; eligible_as_final_model_figure=false; native_verified=false</metadata>',
             '<desc>Geometry debugging only. Final model structure figures must be exported from the delivered MDL in native Vensim.</desc>',
             '<defs><marker id="head" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" fill="context-stroke"/></marker></defs>',
             '<style>text{font-family:Arial,"PingFang SC","Microsoft YaHei",sans-serif;fill:#161616} .label{font-size:12px} .id{font-size:9px;fill:#777}</style>']
    for arrow in view.arrows:
        path = paths.get(arrow.obj_id, [])
        if not path:
            continue
        data = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in path)
        color = "#222222" if arrow.is_physical_flow else _color(arrow.fields[11])
        weight = 4 if arrow.is_physical_flow else 1.3
        parts.append(f'<path d="{data}" fill="none" stroke="{color}" stroke-width="{weight}" marker-end="url(#head)"/>')
        if arrow.is_physical_flow:
            parts.append(f'<path d="{data}" fill="none" stroke="white" stroke-width="1.5"/>')
        polarity = int(arrow.fields[6] or 0)
        if 32 <= polarity <= 126:
            anchor = path[max(0, len(path) - 3)]
            parts.append(f'<text x="{anchor[0]+8:.2f}" y="{anchor[1]-8:.2f}" font-size="13">{escape(chr(polarity))}</text>')
        # dtype 的原值保留在模型中；预览只标注有意义的低位延迟标记。
        if int(arrow.fields[9] or 0) & 3:
            mid = path[len(path) // 2]
            parts.append(f'<text x="{mid[0]:.2f}" y="{mid[1]-5:.2f}" font-size="12">||</text>')
    for obj in view.objects.values():
        if not visible(obj):
            continue
        x, y, w, h = obj.x, obj.y, abs(obj.w), abs(obj.h)
        if obj.kind == 10:
            if obj.shape & 31 == 3:
                parts.append(f'<rect x="{x-w:g}" y="{y-h:g}" width="{2*w:g}" height="{2*h:g}" fill="white" stroke="#222" stroke-width="1.4"/>')
            label = f"<{obj.name}>" if obj.is_shadow else obj.name
        elif obj.kind == 11:
            parts.append(f'<path d="M{x-w:g},{y-h:g} L{x+w:g},{y+h:g} L{x+w:g},{y-h:g} L{x-w:g},{y+h:g} Z" fill="white" stroke="#222"/>')
            label = ""
        elif obj.kind == 12 and obj.name == "48":
            cloud = " ".join(f'{x + max(w, 14) * (1 + 0.15 * math.sin(6*t)) * math.cos(t):.2f},{y + max(h, 10) * (1 + 0.15 * math.sin(6*t)) * math.sin(t):.2f}' for t in [i * math.tau / 48 for i in range(49)])
            parts.append(f'<polygon points="{cloud}" fill="white" stroke="#222"/>')
            label = ""
        elif obj.kind == 12 and obj.bits & 4:
            label = lines[obj.line_index + 1].strip() if obj.line_index + 1 < len(lines) else ""
        else:
            label = f"[object {obj.obj_id}]"
        if label:
            parts.append(f'<text class="label" x="{x:g}" y="{y:g}" text-anchor="middle" dominant-baseline="central">{escape(label)}</text>')
        if show_ids:
            parts.append(f'<text class="id" x="{x+w+4:g}" y="{y-h:g}">{obj.obj_id}</text>')
    parts.append('</svg>')
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
        for column, (model, (lines, views)) in enumerate(zip(models, documents)):
            title = ("Before" if column == 0 else "After") if compare_with else model.stem
            sections.append(f'<section><h1>{escape(title)}</h1>')
            for view in views:
                metrics = measure_view(view)
                sections.append(f'<h2>{escape(view.name)}</h2><p>Node overlaps: {len(metrics["node_overlaps"])} · Line–label collisions: {len(metrics["arrow_node_collisions"])} · Crossings: {len(metrics["arrow_crossings"]) + len(metrics["flow_crossings"])}</p>')
                svg = view_svg(view, lines, show_ids).replace('id="head"', f'id="head-{column}-{view.index}"').replace('url(#head)', f'url(#head-{column}-{view.index})')
                sections.append(svg)
            sections.append('</section>')
        result = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Vensim sketch review</title><style>body{margin:24px;color:#222;background:white;font:14px Arial,sans-serif}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:32px}h1{font-size:20px}h2{font-size:15px;margin-top:28px}p{font-size:12px;color:#666}svg{width:100%;border-top:1px solid #ddd}footer{margin-top:24px;font-size:12px;color:#555}</style><main>' + ''.join(sections) + '</main><footer>Geometry preview; fonts, comments and unsupported shapes require final review in Vensim.</footer></html>'
    else:
        raise ValueError("预览输出必须是 .html 或 .svg")
    atomic_write(output, result.encode("utf-8"))
    return {"preview": str(output), "artifact_kind": "geometry_debug_preview",
            "eligible_as_final_model_figure": False, "native_verified": False,
            "notice": "调试预览，不能用作最终模型结构图；最终图必须由同一 MDL 在 Vensim 原生导出"}
