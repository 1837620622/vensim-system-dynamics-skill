"""由明确的方程、初值、单位和流向生成可编辑 MDL，不猜测研究关系。"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from mdl_document import atomic_write, preflight_outputs, separate_output
from vensim_engine import (
    _validate_time_settings,
    canonical_name,
    extract_deps,
    get_time_bounds,
    parse_equations,
    simulate,
)


def _field(value, label):
    if value is None or isinstance(value, (bool, list, dict)):
        raise ValueError(f"{label} 必须由建模资料明确提供，不能用空值占位")
    text = str(value).strip()
    if not text or any(c in text for c in "\n\r~|{}"):
        raise ValueError(f"{label} 不能为空或包含 MDL 字段分隔符")
    return text


def _model_text(spec):
    if not isinstance(spec, dict):
        raise ValueError("模型规范必须为 JSON 对象")
    if spec.get("language", "zh") not in {"zh", "en"}:
        raise ValueError("language 必须为 zh 或 en；业务默认中文")
    for field in ("links", "feedback_loops"):
        if not isinstance(spec.get(field, []), list):
            raise ValueError(f"{field} 必须为列表，空列表表示未指定")
    title = _field(
        spec.get(
            "name", "System dynamics model" if spec.get("language") == "en" else "系统动力学模型"
        ),
        "模型名",
    )
    sketch_options = spec.get("sketch", {})
    if not isinstance(sketch_options, dict) or set(sketch_options) - {
        "font_family",
        "font_size",
        "layout_mode",
        "circular_gap",
        "circular_aspect",
        "node_spacing",
        "shadow_visibility",
    }:
        raise ValueError(
            "sketch 只接受字体、layout_mode、circular_gap、circular_aspect、node_spacing、shadow_visibility"
        )
    if sketch_options.get("layout_mode", "circular") not in {"circular", "refine", "preserve"}:
        raise ValueError("新建 sketch.layout_mode 必须是 circular/refine/preserve")
    from vensim_autolayout import validate_config

    validate_config(sketch_options)
    font = _field(sketch_options.get("font_family", "Vensim Sans SC"), "MDL 字体")
    if "," in font:
        raise ValueError("MDL 字体名不能含逗号")
    font_size = sketch_options.get("font_size", 12)
    if type(font_size) is not int or not 8 <= font_size <= 24:
        raise ValueError("MDL font_size 必须是 8 到 24 的整数")
    font_scale = font_size / 12
    variables = spec.get("variables", [])
    if not isinstance(variables, list) or not variables or len(variables) > 200:
        raise ValueError("variables 必须包含 1 到 200 个变量；大型模型请拆分视图")
    names = {}
    for variable in variables:
        if not isinstance(variable, dict):
            raise ValueError("variables 中每项必须是对象")
        name = _field(variable.get("name", ""), "变量名")
        # 生成器不为名称补引号，因此只接受原生无引号的简单名称。
        if (
            not name[0].isalpha()
            or any(not (char.isalnum() or char in " _$") for char in name)
            or canonical_name(name)
            in {
                canonical_name(value)
                for value in ("TIME", "INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER")
            }
        ):
            raise ValueError(f"不支持的业务变量名: {name}")
        if canonical_name(name) in {canonical_name(existing) for existing in names}:
            raise ValueError(f"变量名重复: {name}")
        if variable.get("kind") not in {"stock", "flow", "aux", "constant"}:
            raise ValueError(f"{name}: kind 必须是 stock/flow/aux/constant")
        _field(variable.get("unit", ""), f"{name} 的单位")
        names[name] = variable
        if "position" in variable:
            if variable["kind"] == "flow":
                raise ValueError(f"{name}: 流量位置由管道生成，请在原生软件调整阀门与附着文字")
            point = variable["position"]
            if (
                not isinstance(point, list)
                or len(point) != 2
                or any(
                    isinstance(v, bool)
                    or not isinstance(v, (int, float))
                    or not math.isfinite(v)
                    or v < 0
                    for v in point
                )
            ):
                raise ValueError(f"{name}: position 必须是两个非负有限坐标")
    stocks = {name: item for name, item in names.items() if item["kind"] == "stock"}
    flows = {name: item for name, item in names.items() if item["kind"] == "flow"}
    if not stocks:
        raise ValueError("SFD 建模至少需要一个 stock")
    for name, flow in flows.items():
        endpoints = flow.get("from"), flow.get("to")
        if endpoints == (None, None) or endpoints[0] == endpoints[1]:
            raise ValueError(f"{name}: 流率需要不同的来源与去向，边界一端可为 null")
        for endpoint in endpoints:
            if endpoint is not None and endpoint not in stocks:
                raise ValueError(f"{name}: 流向必须引用存量: {endpoint}")
    controls = spec.get("time", {})
    if not isinstance(controls, dict) or {"initial", "final", "step", "saveper", "unit"} - set(
        controls
    ):
        raise ValueError("time 必须明确提供 initial、final、step、saveper、unit，不能套用示例参数")
    unit = _field(controls["unit"], "时间单位")
    values = {
        "INITIAL TIME": controls["initial"],
        "FINAL TIME": controls["final"],
        "TIME STEP": controls["step"],
        "SAVEPER": controls["saveper"],
    }
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in values.values()
    ):
        raise ValueError("时间设置必须为有限数")
    equations = ["{UTF-8}\n"]
    for name, item in names.items():
        if item["kind"] == "stock":
            incoming = [flow for flow, definition in flows.items() if definition.get("to") == name]
            outgoing = [
                flow for flow, definition in flows.items() if definition.get("from") == name
            ]
            expression = " + ".join(incoming) or "0"
            expression += "".join(f" - {flow}" for flow in outgoing)
            if "initial" not in item:
                raise ValueError(f"{name}: 必须提供 initial")
            rhs = f"INTEG({expression}, {_field(item['initial'], name)})"
        else:
            rhs = _field(item.get("equation", ""), f"{name} 的方程")
            if item["kind"] == "constant":
                try:
                    value = float(rhs)
                except ValueError as exc:
                    raise ValueError(
                        f"{name}: constant 必须是明确的有限数值，派生方程请使用 aux"
                    ) from exc
                if not math.isfinite(value):
                    raise ValueError(f"{name}: constant 必须是有限数值")
        equations.append(f"{name} = {rhs}\n\t~ {item['unit']}\n\t~\n\t|\n")
    equations.extend(f"{name} = {value}\n\t~ {unit}\n\t~\n\t|\n" for name, value in values.items())
    equation_text = "\n".join(equations)
    parsed = parse_equations(equation_text)
    # 建模入口只生成内置引擎可预检的标量方程；高级模型可在原生软件中扩展。
    t0, tf, dt, sp = get_time_bounds(parsed)
    _validate_time_settings(t0, tf, dt, sp)
    simulate(parsed, t0, t0, dt, sp)
    records, links, ids, positions = [], [], {}, {}
    next_id = 1

    def object_record(kind, name, x, y, w, h, shape=8, tpos=0):
        nonlocal next_id
        oid = next_id
        next_id += 1
        bits = 131 if kind == 10 else 3  # 指定真实文字框，避免原生打开时恢复为统一大框。
        records.append(
            f"{kind},{oid},{name},{round(x)},{round(y)},{round(w)},{round(h)},{shape},{bits},0,0,{tpos},0,0,0,0,0,0,0,0,0"
        )
        positions[oid] = (round(x), round(y))
        return oid

    # 默认存量骨架使用有限宽度的横向分行布局。旧的固定 400 单位间隔
    # 对多存量模型会制造超宽画布，随后所有辅助量只能在骨架下方拉成长线。
    # 这里的数值只来自字体和排版留白，不代表任何业务数据；显式 position
    # 仍完全由用户控制并保持不动。
    stock_items = list(stocks.items())
    stock_widths = {name: max(40, len(name) * 6 + 12) * font_scale for name, _ in stock_items}
    layout_gap = max(96.0, float(sketch_options.get("node_spacing", 24)) * 4)
    stock_neighbors = {name: set() for name in stocks}
    stock_outgoing = {name: set() for name in stocks}
    for flow in flows.values():
        source, target = flow.get("from"), flow.get("to")
        if source in stocks and target in stocks:
            stock_neighbors[source].add(target)
            stock_neighbors[target].add(source)
            stock_outgoing[source].add(target)
    remaining = set(stocks)
    stock_components = []
    while remaining:
        start = min(remaining)
        component, pending = set(), [start]
        while pending:
            name = pending.pop()
            if name in component:
                continue
            component.add(name)
            pending.extend(stock_neighbors[name] - component)
        remaining -= component
        stock_components.append(component)

    def component_order(component):
        """稳定地沿存量流向排列链或反馈环，避免按字典顺序打乱管道。"""
        start = min(component, key=lambda name: (-len(stock_neighbors[name]), name))
        order, seen, current, previous = [], set(), start, None
        while current not in seen:
            order.append(current)
            seen.add(current)
            options = stock_neighbors[current] - seen
            if previous is not None and len(options) > 1:
                options -= {previous}
            if not options:
                break
            current = min(
                options,
                key=lambda name: (
                    name not in stock_outgoing[current],
                    -len(stock_neighbors[name]),
                    name,
                ),
            )
            previous = order[-1]
        return order + sorted(component - seen)

    def contains_cycle(component):
        """按有向存量流识别反馈环，覆盖两个存量互相作用的边界情况。"""
        visiting, visited = set(), set()

        def visit(name):
            if name in visiting:
                return True
            if name in visited:
                return False
            visiting.add(name)
            if any(visit(target) for target in stock_outgoing[name] & component):
                return True
            visiting.remove(name)
            visited.add(name)
            return False

        return any(visit(name) for name in component)

    default_positions = {}
    cursor_x, cursor_y = 340.0 - stock_widths[stock_items[0][0]] / 2, 220.0
    for component in stock_components:
        order = component_order(component)
        widths = [stock_widths[name] for name in order]
        has_cycle = len(component) > 1 and contains_cycle(component)
        if has_cycle:
            # 反馈环按实际存量连接围成紧凑椭圆；半径由文字框和环上留白决定。
            circumference = sum(width + layout_gap for width in widths)
            radius_x = max(
                (max(widths) + layout_gap) / (2 * math.sin(math.pi / len(order))),
                circumference / math.tau,
            )
            radius_y = max(radius_x * 0.8, max(widths) + layout_gap)
            center_x, center_y = cursor_x + radius_x, cursor_y + radius_y
            for index, name in enumerate(order):
                angle = -math.pi / 2 + math.tau * index / len(order)
                default_positions[name] = [
                    round(center_x + radius_x * math.cos(angle)),
                    round(center_y + radius_y * math.sin(angle)),
                ]
            cursor_x += 2 * radius_x + layout_gap
        else:
            # 无反馈环的存量链沿同一基线排列；组件之间只在需要时换行，
            # 不把不相干的模块挤到同一条长对角线上。
            x = cursor_x
            for name, width in zip(order, widths, strict=False):
                default_positions[name] = [round(x + width / 2), round(cursor_y)]
                x += width + layout_gap
            cursor_x = x + layout_gap
        if cursor_x > 1600:
            cursor_x = 340.0
            cursor_y += max(widths, default=40) + layout_gap * 2
    for name, item in stock_items:
        x, y = item.get("position", default_positions[name])
        ids[name] = object_record(
            10, name, x, y, max(40, len(name) * 6 + 12) * font_scale, 22 * font_scale, 3
        )
    stock_center = (
        sum(positions[ids[name]][0] for name in stocks) / len(stocks),
        sum(positions[ids[name]][1] for name in stocks) / len(stocks),
    )
    for name, flow in flows.items():
        source = ids.get(flow.get("from"))
        target = ids.get(flow.get("to"))
        if source is None:
            tx, ty = positions[target]
            source = object_record(12, "48", tx - 260, ty, 10, 8, 0, tpos=-1)
        if target is None:
            sx, sy = positions[source]
            target = object_record(12, "48", sx + 260, sy, 10, 8, 0, tpos=-1)
        sx, sy = positions[source]
        tx, ty = positions[target]
        x, y = (sx + tx) / 2, (sy + ty) / 2
        valve = object_record(11, "0", x, y, 6, 8, 34, 1)
        label_x, label_y = x, y + 30 * font_scale
        if flow.get("from") in stocks and flow.get("to") in stocks:
            # 环形存量骨架中，流量文字沿管道法向朝外放置，避免压住相邻
            # 存量。边界源/汇保留原生示例的下方附着位置。
            dx, dy = tx - sx, ty - sy
            distance = math.hypot(dx, dy)
            if distance > 1:
                nx, ny = -dy / distance, dx / distance
                radial = (x - stock_center[0], y - stock_center[1])
                if nx * radial[0] + ny * radial[1] < 0:
                    nx, ny = -nx, -ny
                offset = max(30 * font_scale, layout_gap * 0.8)
                label_x, label_y = x + nx * offset, y + ny * offset
        ids[name] = object_record(
            10,
            name,
            label_x,
            label_y,
            max(30, len(name) * 6) * font_scale,
            12 * font_scale,
            40,
            -1,
        )
        # 原生流量的因果方向是阀门到存量；100 将管道画成反向的流出段。
        # 若写成存量到阀门，Vensim 会额外补出流率文字到存量的错误视觉连接。
        links.extend([(valve, source, 100, 0, 0), (valve, target, 4, 0, 0)])
    auxiliary = [(name, item) for name, item in names.items() if name not in ids]
    originals = parse_equations(equation_text, expand=False)
    grouped = {}
    for name, item in auxiliary:
        # 参数围绕直接受影响的结构分组；显式 position 提供人工审图后的稳定锚点。
        destinations = [
            target for target in ids if name in extract_deps(originals[target].rhs, set(names))
        ]
        center = (
            tuple(
                round(
                    sum(positions[ids[target]][axis] for target in destinations) / len(destinations)
                )
                for axis in (0, 1)
            )
            if destinations
            else (340, 220)
        )
        slot = grouped.get(center, 0)
        grouped[center] = slot + 1
        angle = math.radians(45 + (slot % 5) * 35)
        radius = 150 + (slot // 5) * 110
        default = [
            max(70, round(center[0] + radius * math.cos(angle))),
            round(center[1] + radius * math.sin(angle)),
        ]
        if any(
            name in extract_deps(originals[target].integ_init_expr or "", set(names))
            for target in stocks
        ):
            default = [center[0], max(50, center[1] - 125)]
        x, y = item.get("position", default)
        ids[name] = object_record(
            10, name, x, y, max(32, len(name) * 6 + 6) * font_scale, 12 * font_scale
        )
    signed_links = {}
    for link in spec.get("links", []):
        if not isinstance(link, dict) or any(
            not isinstance(link.get(field), str) or link[field] not in names
            for field in ("from", "to")
        ):
            raise ValueError("links 的 from/to 必须引用已定义变量")
        key = link["from"], link["to"]
        target_eq = originals[key[1]]
        expression = target_eq.integ_init_expr if key[1] in stocks else target_eq.rhs
        if key in signed_links or key[0] not in extract_deps(expression, set(names)):
            raise ValueError(f"重复或缺乏方程依据的 links: {key}")
        if not isinstance(link.get("delay", False), bool):
            raise ValueError("delay 必须是布尔值")
        signed_links[key] = link
    for name, eq in originals.items():
        if name not in names:
            continue
        expression = eq.integ_init_expr if name in stocks else eq.rhs
        for dep in sorted(extract_deps(expression, set(names))):
            annotation = signed_links.get((dep, name), {})
            polarity = annotation.get("polarity", "")
            if not isinstance(polarity, str) or polarity not in {"", "+", "-", "S", "O", "s", "o"}:
                raise ValueError("polarity 仅支持 +、-、S、O；无依据时留空")
            links.append(
                (
                    ids[dep],
                    ids[name],
                    False,
                    ord(polarity) if polarity else 0,
                    1 if annotation.get("delay") else 0,
                )
            )
    for source, target, physical, polarity, delay in links:
        x = round((positions[source][0] + positions[target][0]) / 2)
        y = round((positions[source][1] + positions[target][1]) / 2)
        records.append(
            f"1,{next_id},{source},{target},{physical or 1},0,{polarity},{22 if physical else 0},1,{64 | delay},0,0-0-0,,1|({x},{y})|"
        )
        next_id += 1
    sketch = [
        r"\\\---/// Sketch information - do not modify anything except names",
        "V300  Do not put anything below this section - it will be ignored",
        "*" + title,
        f"$192-192-192,0,{font}|{font_size}||0-0-0|0-0-0|0-0-0|-1--1--1|-1--1--1|96,96,100,0",
        *records,
        "///---" + chr(92) * 3,
        "",
    ]
    return equation_text + "\n" + "\n".join(sketch)


def _prepare_model(spec):
    from sketch_layout import optimize_view
    from vensim_autolayout import parse_views

    text = _model_text(spec)
    from feedback_audit import audit_feedback

    feedback = audit_feedback(text, spec.get("feedback_loops", []))
    if feedback["conflicts"]:
        raise ValueError(
            "反馈极性与方程冲突: " + json.dumps(feedback["conflicts"], ensure_ascii=False)
        )
    lines = text.splitlines(keepends=True)
    view = parse_views(
        lines, {item["name"] for item in spec["variables"] if item["kind"] == "stock"}
    )[0]
    config = {
        "layout_mode": "circular",
        **spec.get("sketch", {}),
        "lock_node_names": [item["name"] for item in spec["variables"] if "position" in item],
    }
    report = optimize_view(lines, view, config)
    report["requested_mode"] = config["layout_mode"]
    report["fixed_positions"] = {
        item["name"]: item["position"] for item in spec["variables"] if "position" in item
    }
    from sketch_geometry import quality_pass

    report["pass"] = quality_pass(report["after"])
    report["native_verified"] = False
    report["feedback"] = feedback
    return "".join(lines), report


def build_model(spec):
    return _prepare_model(spec)[0]


def command_build(spec_path, output):
    separate_output(output, [spec_path])
    if output.exists():
        raise ValueError("建模输出已存在，请使用新文件名")
    if output.suffix.lower() != ".mdl":
        raise ValueError("建模输出必须是 .mdl")
    report_path = output.with_suffix(".mdl.build_report.json")
    preflight_outputs([output, report_path], [spec_path])
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    text, report = _prepare_model(spec)
    atomic_write(output, text.encode("utf-8"))
    atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode())
    print(f"已生成: {output}；请在 Vensim 执行 Check Model 和 Units Check")
    if not report["pass"]:
        print(f"几何预检尚有冲突或未覆盖对象，见 {report_path}；不能据此交付最终结构图")
    if report["feedback"]["status"] == "needs_review":
        print(f"部分已标极性或回路需要领域与取值范围核对，见 {report_path}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    return command_build(args.spec, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
