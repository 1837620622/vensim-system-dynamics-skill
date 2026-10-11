"""按 JSON 中给出的变量、方程、单位与流向生成 MDL，并做 natural 排版。"""

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

# ---------------------------------------------------------------------------
# 建模 JSON 校验与方程生成
# ---------------------------------------------------------------------------


# 只在建模阶段使用、不传给排版配置的 sketch 键
FONT_KEYS = {"font_family", "font_size", "polarity"}
POLARITY_MODES = {"dynamic", "declared", "all"}


def _field(value, label):
    if value is None or isinstance(value, (bool, list, dict)):
        raise ValueError(f"{label} 缺失")
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
    # 生成过程中做出的草图决定（常量换视图、画了哪些回路标记等），写进报告
    notes = {
        "moved_constants": [],
        "supplementary": [],
        "numeric_links": [],
        "suggested_loops": [],
        "loop_markers": [],
        "unmarked_loops": [],
    }
    sketch_options = spec.get("sketch", {})
    if not isinstance(sketch_options, dict) or set(sketch_options) - {
        "font_family",
        "font_size",
        "layout_mode",
        "node_spacing",
        "shadow_visibility",
        "split_shadows",
        "seed",
        "polarity",
    }:
        raise ValueError(
            "sketch 只接受 font_family、font_size、layout_mode、node_spacing、"
            "shadow_visibility、split_shadows、seed、polarity"
        )
    # 箭头极性：dynamic 给变量之间能由方程推出符号的链接标 +/-，常量引出的链接不标；
    # declared 只标 links 里写明的；all 连常量链接也标。links 里写明的总会标出
    polarity_mode = sketch_options.get("polarity", "dynamic")
    if polarity_mode not in POLARITY_MODES:
        raise ValueError("sketch.polarity 只能是 dynamic、declared 或 all")
    if sketch_options.get("layout_mode", "natural") not in {"natural", "refine", "preserve"}:
        raise ValueError("新建 sketch.layout_mode 必须是 natural/refine/preserve")
    from vensim_autolayout import validate_config

    validate_config({k: v for k, v in sketch_options.items() if k not in FONT_KEYS})
    font = _field(sketch_options.get("font_family", "Vensim Sans SC"), "MDL 字体")
    if "," in font:
        raise ValueError("MDL 字体名不能含逗号")
    font_size = sketch_options.get("font_size", 12)
    if type(font_size) is not int or not 8 <= font_size <= 24:
        raise ValueError("MDL font_size 必须是 8 到 24 的整数")
    font_scale = font_size / 12
    variables = spec.get("variables", [])
    if not isinstance(variables, list) or not variables or len(variables) > 400:
        raise ValueError("variables 必须包含 1 到 400 个变量")
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
        raise ValueError("time 需要 initial、final、step、saveper、unit 五项")
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
    rhs_of = {}
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
        rhs_of[name] = rhs
    # 使用检查与 Vensim Check Model 同口径：没有被任何方程引用的变量会报 USE FLAG。
    # 没人用的常量是多余参数，直接报错；没人用的辅助变量、存量是输出指标，
    # 写上 :SUPPLEMENTARY 字段；标了 supplementary 却被引用的同样会报 USE FLAG
    used = {
        dep for name, rhs in rhs_of.items() for dep in extract_deps(rhs, set(names)) if dep != name
    }
    supplementary = set()
    for name, item in names.items():
        flagged = item.get("supplementary", False)
        if not isinstance(flagged, bool):
            raise ValueError(f"{name}: supplementary 必须是 true 或 false")
        if flagged and name in used:
            raise ValueError(
                f"{name}: 被其他方程使用，不能标 supplementary（Vensim 会报 USE FLAG）"
            )
        if name in used:
            continue
        if item["kind"] == "constant":
            raise ValueError(
                f"常量「{name}」没有被任何方程使用：删掉它，或在需要它的方程里引用；"
                "否则打开 Vensim 会报 USE FLAG"
            )
        supplementary.add(name)
    notes["supplementary"] = sorted(supplementary)
    equations = ["{UTF-8}\n"]
    for name, item in names.items():
        flag = "\t~\t:SUPPLEMENTARY\n" if name in supplementary else ""
        equations.append(f"{name} = {rhs_of[name]}\n\t~ {item['unit']}\n\t~\n{flag}\t|\n")
    equations.extend(f"{name} = {value}\n\t~ {unit}\n\t~\n\t|\n" for name, value in values.items())
    equation_text = "\n".join(equations)
    parsed = parse_equations(equation_text)
    # 建模入口只生成内置引擎可预检的标量方程；高级模型可在原生软件中扩展。
    t0, tf, dt, sp = get_time_bounds(parsed)
    _validate_time_settings(t0, tf, dt, sp)
    simulate(parsed, t0, t0, dt, sp)

    def _view_records(members):
        """生成一个视图的对象与连线记录；其他视图的变量以影子引用出现。"""
        view_stocks = {name: item for name, item in stocks.items() if name in members}
        view_flows = {name: item for name, item in flows.items() if name in members}
        records, links, ids, positions = [], [], {}, {}
        next_id = 1

        def object_record(kind, name, x, y, w, h, shape=8, tpos=0, shadow=False):
            nonlocal next_id
            oid = next_id
            next_id += 1
            # 131 = 定义实例并指定文字框尺寸；130 = 影子引用（灰色 <名称>，只能引出连线）
            bits = (130 if shadow else 131) if kind == 10 else 3
            records.append(
                f"{kind},{oid},{name},{round(x)},{round(y)},{round(w)},{round(h)},{shape},{bits},0,0,{tpos},0,0,0,0,0,0,0,0,0"
            )
            positions[oid] = (round(x), round(y))
            return oid

        # 初始坐标只是排版起点：存量按流向成行，参数围在作用对象旁边，
        # 随后由 natural 排版整体重排；显式 position 的变量保持不动
        stock_items = list(view_stocks.items())
        stock_widths = {name: max(40, len(name) * 6 + 12) * font_scale for name, _ in stock_items}
        layout_gap = max(96.0, float(sketch_options.get("node_spacing", 24)) * 4)
        stock_neighbors = {name: set() for name in view_stocks}
        stock_outgoing = {name: set() for name in view_stocks}
        for flow in view_flows.values():
            source, target = flow.get("from"), flow.get("to")
            if source in view_stocks and target in view_stocks:
                stock_neighbors[source].add(target)
                stock_neighbors[target].add(source)
                stock_outgoing[source].add(target)
        remaining = set(view_stocks)
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
        first_width = stock_widths[stock_items[0][0]] if stock_items else 0
        cursor_x, cursor_y = 340.0 - first_width / 2, 220.0
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
            tuple(
                sum(positions[ids[name]][axis] for name in view_stocks) / len(view_stocks)
                for axis in (0, 1)
            )
            if view_stocks
            else (340.0, 220.0)
        )
        for name, flow in view_flows.items():
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
            if flow.get("from") in view_stocks and flow.get("to") in view_stocks:
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
        auxiliary = [(name, names[name]) for name in names if name in members and name not in ids]
        grouped = {}
        for name, item in auxiliary:
            # 参数围绕直接受影响的结构分组；显式 position 提供人工审图后的稳定锚点。
            destinations = [
                target for target in ids if name in extract_deps(originals[target].rhs, set(names))
            ]
            center = (
                tuple(
                    round(
                        sum(positions[ids[target]][axis] for target in destinations)
                        / len(destinations)
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
                for target in view_stocks
            ):
                default = [center[0], max(50, center[1] - 125)]
            x, y = item.get("position", default)
            ids[name] = object_record(
                10, name, x, y, max(32, len(name) * 6 + 6) * font_scale, 12 * font_scale
            )
        shadows = {}
        for name, eq in originals.items():
            if name not in members:
                continue
            expression = eq.integ_init_expr if name in stocks else eq.rhs
            for dep in sorted(extract_deps(expression, set(names))):
                if dep not in ids and dep not in shadows:
                    # 其他视图定义的变量：在本视图放一个影子引用，靠近作用对象
                    tx, ty = positions[ids[name]]
                    shadows[dep] = object_record(
                        10,
                        dep,
                        tx - 150,
                        ty - 60,
                        max(32, len(dep) * 6 + 6) * font_scale,
                        12 * font_scale,
                        shadow=True,
                    )
                annotation = signed_links.get((dep, name), {})
                polarity = annotation.get("polarity", "")
                if not isinstance(polarity, str) or polarity not in {
                    "",
                    "+",
                    "-",
                    "S",
                    "O",
                    "s",
                    "o",
                }:
                    raise ValueError("polarity 仅支持 +、-、S、O；无依据时留空")
                if (
                    not polarity
                    and name not in stocks
                    and polarity_mode != "declared"
                    and (polarity_mode == "all" or names[dep]["kind"] != "constant")
                ):
                    inferred = infer_polarity(expression, dep, originals)
                    if inferred not in {"+", "-"}:
                        # 方程推不出（如两个变量相乘）时用基准运行的数值偏导
                        inferred = numeric.get((canonical_name(dep), canonical_name(name)), "")
                        if inferred:
                            notes["numeric_links"].append([dep, name, inferred])
                    polarity = inferred
                links.append(
                    (
                        ids.get(dep, shadows.get(dep)),
                        ids[name],
                        False,
                        ord(polarity) if polarity else 0,
                        1 if annotation.get("delay") else 0,
                    )
                )
        # 回路标记：12 类注释，形状 5/4 是逆/顺时针回路圈，文字写在下一行。先放在回路
        # 节点中间，排版时按回路归属放回回路内部，圈的走向在排版后按实际图形确定
        for marker in notes["loop_markers"]:
            if not set(marker["variables"]) <= members:
                continue
            points = [positions[ids[name]] for name in marker["variables"]]
            x = round(sum(p[0] for p in points) / len(points))
            y = round(sum(p[1] for p in points) / len(points))
            size = round(12 * font_scale)
            records.append(
                f"12,{next_id},0,{x},{y},{size},{size},5,135,0,8,-1,0,0,0,"
                f"-1--1--1,0-0-0,|{max(9, font_size - 2)}||0-0-0"
            )
            records.append(marker["label"])
            positions[next_id] = (x, y)
            next_id += 1
        for source, target, physical, polarity, delay in links:
            x = round((positions[source][0] + positions[target][0]) / 2)
            y = round((positions[source][1] + positions[target][1]) / 2)
            records.append(
                f"1,{next_id},{source},{target},{physical or 1},0,{polarity},{22 if physical else 0},1,{64 | delay},0,0-0-0,,1|({x},{y})|"
            )
            next_id += 1
        return records

    originals = parse_equations(equation_text, expand=False)
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
    # 视图按变量的 view 字段划分；未写 view 的变量都在第一个视图
    view_of = {name: str(item.get("view", title)).strip() for name, item in names.items()}
    if any(not view or any(c in view for c in ",|~\n\r") for view in view_of.values()):
        raise ValueError("view 名称不能为空，也不能包含逗号、| 或 ~")
    consumers = {name: set() for name in names}
    for target in names:
        eq = originals[target]
        # 存量通过净流率引用流量、通过初值引用参数，两处都算使用
        expression = (
            f"{eq.integ_flow or ''} {eq.integ_init_expr or ''}" if target in stocks else eq.rhs
        )
        for dep in extract_deps(expression, set(names)):
            consumers[dep].add(target)
    # 只被另一个视图使用的常量移到使用它的视图：Vensim 习惯把参数画在作用对象旁边，
    # 留在原视图会多出一个影子，原视图里还剩一个没有连线的孤立变量
    for name, item in names.items():
        homes = {view_of[user] for user in consumers[name]}
        if (
            item["kind"] == "constant"
            and "position" not in item
            and len(homes) == 1
            and view_of[name] not in homes
        ):
            notes["moved_constants"].append(
                {"variable": name, "from": view_of[name], "to": next(iter(homes))}
            )
            view_of[name] = next(iter(homes))
    # 回路标记只画核对一致、且整条回路在同一视图里的回路；编号按 R、B 分别递增
    from feedback_audit import check_loops, infer_polarity, numeric_link_signs

    numeric = numeric_link_signs(equation_text)

    declared = {
        (canonical_name(source), canonical_name(target)): link.get("polarity", "")
        for (source, target), link in signed_links.items()
    }
    counters = {"R": 0, "B": 0}
    for row in check_loops(originals, spec.get("feedback_loops", []), declared, numeric):
        views_on_loop = {view_of[name] for name in row["variables"]}
        if row["status"] in {"verified", "declared", "numeric"} and len(views_on_loop) == 1:
            counters[row["expected"]] += 1
            notes["loop_markers"].append(
                {
                    "label": f"{row['expected']}{counters[row['expected']]}",
                    "loop": row["name"],
                    "variables": row["variables"],
                    "view": next(iter(views_on_loop)),
                    "status": row["status"],
                }
            )
        else:
            notes["unmarked_loops"].append(
                {
                    "loop": row["name"],
                    "status": row["status"],
                    "unsigned_links": row["unsigned_links"],
                    "views": sorted(views_on_loop),
                }
            )
    if not spec.get("feedback_loops"):
        notes["suggested_loops"] = _suggest_loops(
            originals, names, stocks, view_of, declared, numeric
        )
    for name, flow in flows.items():
        for end in (flow.get("from"), flow.get("to")):
            if end is not None and view_of[end] != view_of[name]:
                raise ValueError(f"{name}: 流量与其两端存量必须在同一视图")
    sketch_lines = []
    for view_name in dict.fromkeys(view_of.values()):
        members = {name for name in names if view_of[name] == view_name}
        sketch_lines += [
            r"\\\---/// Sketch information - do not modify anything except names",
            "V300  Do not put anything below this section - it will be ignored",
            "*" + view_name,
            f"$192-192-192,0,{font}|{font_size}||0-0-0|0-0-0|0-0-0|-1--1--1|-1--1--1|96,96,100,0",
            *_view_records(members),
        ]
    sketch_lines += ["///---" + chr(92) * 3, ""]
    return equation_text + "\n" + "\n".join(sketch_lines), notes


# ---------------------------------------------------------------------------
# 草图生成、排版与反馈核对
# ---------------------------------------------------------------------------


def _prepare_model(spec):
    from feedback_audit import audit_feedback
    from natural_layout import _set_fields, loop_marker_shape, natural_view
    from sketch_geometry import measure_view, quality_pass
    from sketch_layout import optimize_view
    from vensim_autolayout import parse_views, time_dependent_names, view_advice

    text, notes = _model_text(spec)
    feedback = audit_feedback(text, spec.get("feedback_loops", []))
    if feedback["conflicts"]:
        raise ValueError(
            "反馈极性与方程冲突: " + json.dumps(feedback["conflicts"], ensure_ascii=False)
        )
    lines = text.splitlines(keepends=True)
    stock_names = {item["name"] for item in spec["variables"] if item["kind"] == "stock"}
    time_names = time_dependent_names(text)
    sketch = {k: v for k, v in spec.get("sketch", {}).items() if k not in FONT_KEYS}
    config = {
        "layout_mode": "natural",
        **sketch,
        "lock_node_names": [item["name"] for item in spec["variables"] if "position" in item],
    }
    reports, insertions, markers = [], [], {}
    for view in parse_views(lines, stock_names, time_names):
        markers[view.name] = _marker_nodes(view, lines, notes["loop_markers"])
        if config["layout_mode"] == "natural":
            item, extra = natural_view(
                lines,
                view,
                {**config, "_marker_loops": markers[view.name], "_free_row_order": True},
            )
            insertions.extend(extra)
        else:
            item = optimize_view(lines, view, config)
        reports.append({"view": view.name, **item})
    # 新影子行按位置倒序插入，保证其余视图的行号不受影响
    for position, block in sorted(insertions, key=lambda row: row[0], reverse=True):
        lines[position:position] = block.splitlines(keepends=True)
    views = parse_views(lines, stock_names, time_names)
    for view in views:
        for oid, nodes in markers.get(view.name, {}).items():
            line_index = view.objects[oid].line_index
            lines[line_index] = _set_fields(lines[line_index], {7: loop_marker_shape(view, nodes)})
    views = parse_views(lines, stock_names, time_names)
    marker_check = []
    for item, view in zip(reports, views, strict=True):
        item["after"] = measure_view(view)
        item["pass"] = quality_pass(item["after"])
        marker_check += _marker_check(view, lines, markers.get(view.name, {}))
    notes["marker_check"] = marker_check
    report = {
        "requested_mode": config["layout_mode"],
        "pass": all(item["pass"] for item in reports),
        "views": reports,
        "fixed_positions": {
            item["name"]: item["position"] for item in spec["variables"] if "position" in item
        },
        "feedback": feedback,
        "sketch_decisions": notes,
        "advice": _advice(spec, notes, views, view_advice),
    }
    return "".join(lines), report


SUGGEST_LOOPS = 6  # 建议声明的回路最多几条
LOOP_SEARCH_LIMIT = 200_000  # 找回路时最多展开的路径数，防止稠密模型耗时失控


def _suggest_loops(originals, names, stocks, view_of, declared, numeric):
    """未声明 feedback_loops 时，找出能被方程核对（含声明和数值偏导）的最短反馈回路。

    只给建议，不替建模者画标记；回路必须经过存量，且整条回路在同一视图里。
    返回可以直接粘进 feedback_loops 的条目。
    """
    from feedback_audit import check_loops

    known = set(names)
    graph = {name: [] for name in names}
    for target, eq in originals.items():
        expression = eq.integ_flow if target in stocks else eq.rhs
        for dep in extract_deps(expression or "", known):
            if dep != target:
                graph[dep].append(target)
    cycles, seen, steps = [], set(), 0
    for start in sorted(names):
        stack = [(start, (start,))]
        while stack and steps < LOOP_SEARCH_LIMIT:
            steps += 1
            node, path = stack.pop()
            for nxt in sorted(graph[node]):
                if nxt == start:
                    key = frozenset(zip(path, path[1:] + path[:1], strict=True))
                    if key not in seen and any(n in stocks for n in path):
                        seen.add(key)
                        cycles.append((path, key))
                elif nxt > start and nxt not in path and len(path) < 10:
                    stack.append((nxt, (*path, nxt)))
    suggestions, covered = [], set()
    for cycle, key in sorted(cycles, key=lambda c: (len(c[0]), c[0])):
        # 只推荐带来新连线的回路，短回路不会把长回路挤掉
        if key <= covered or len({view_of[n] for n in cycle}) > 1:
            continue
        row = check_loops(originals, [{"variables": list(cycle)}], declared, numeric)[0]
        if row["status"] in {"verified", "declared", "numeric"}:
            covered |= key
            suggestions.append(
                {
                    "name": f"回路{len(suggestions) + 1}",
                    "variables": list(cycle),
                    "polarity": row["expected"],
                }
            )
        if len(suggestions) == SUGGEST_LOOPS:
            break
    return suggestions


def _marker_check(view, lines, nodes_by_marker):
    """每个回路标记是否落在自己的回路里：三个以上节点看是否在多边形内，两个节点看
    离管道中点的距离。与排版时的口径一致，位置不对的写进报告和建议。"""
    from natural_layout import MARKER_REACH, _contains

    rows = []
    for oid, nodes in nodes_by_marker.items():
        marker = view.objects[oid]
        points = [(view.objects[n].x, view.objects[n].y) for n in nodes]
        if len(points) > 2:
            inside = _contains((marker.x, marker.y), points)
        else:
            cx = sum(x for x, _ in points) / len(points)
            cy = sum(y for _, y in points) / len(points)
            inside = math.hypot(marker.x - cx, marker.y - cy) <= MARKER_REACH
        rows.append({"label": lines[marker.line_index + 1].strip(), "inside": inside})
    return rows


def _marker_nodes(view, lines, loop_markers):
    """回路标记与回路变量换成本视图的对象编号；流量用阀门，与回路识别的口径一致。"""
    from natural_layout import extract_flows, is_loop_marker

    valves = {f.label: f.valve for f in extract_flows(view).values() if f.label is not None}
    defined = {
        canonical_name(obj.name): oid
        for oid, obj in view.objects.items()
        if obj.kind == 10 and not obj.is_shadow
    }
    texts = {
        lines[obj.line_index + 1].strip(): oid
        for oid, obj in view.objects.items()
        if is_loop_marker(obj) and obj.line_index + 1 < len(lines)
    }
    result = {}
    for marker in loop_markers:
        oid = texts.get(marker["label"])
        nodes = [defined.get(canonical_name(name)) for name in marker["variables"]]
        if oid is not None and None not in nodes:
            result[oid] = tuple(valves.get(node, node) for node in nodes)
    return result


# 名字超过这个宽度时提示精简：存量 8 个汉字，其他变量 10 个汉字（按 12 号字估算）
STOCK_NAME_ADVICE = 96
NAME_ADVICE = 120


def _advice(spec, notes, views, view_advice):
    """把生成时的决定和可以改进的地方写成能直接照做的提示。"""
    from sketch_geometry import text_width

    advice = [
        f"常量「{row['variable']}」只在视图「{row['to']}」中使用，已从「{row['from']}」移过去，"
        "避免多出影子和孤立变量；以后请直接写在使用它的视图里"
        for row in notes["moved_constants"]
    ]
    advice += [
        f"「{name}」没有被其他方程使用，已标为 Supplementary（输出指标），打开 Vensim 不会报"
        " USE FLAG；如果它本该影响模型，请检查引用它的方程"
        for name in notes["supplementary"]
    ]
    advice += [
        f"回路标记 {row['label']} 没能放进所属回路：回路周围太挤，试着删掉不必要的变量或把相关变量放进同一视图"
        for row in notes.get("marker_check", [])
        if not row["inside"]
    ]
    advice += [
        "未声明 feedback_loops，可以核对的回路有：把这一条加进 feedback_loops，build 会画出 "
        f"{row['polarity']} 标记 " + json.dumps(row, ensure_ascii=False)
        for row in notes["suggested_loops"]
    ]
    if notes["numeric_links"]:
        advice.append(
            f"{len(notes['numeric_links'])} 条箭头的极性方程推不出（多为变量相乘），按基准运行"
            "逐时刻数值求偏导确定，只在这次运行的取值范围内成立；需要时在 links 里写明"
        )
    for row in notes["unmarked_loops"]:
        if len(row["views"]) > 1:
            advice.append(f"回路「{row['loop']}」跨视图 {'、'.join(row['views'])}，不画 R/B 标记")
        for source, target in row["unsigned_links"]:
            advice.append(
                f"回路「{row['loop']}」中 {source} → {target} 的符号无法从方程判断；在 links 里写 "
                f'{{"from": "{source}", "to": "{target}", "polarity": "+ 或 -"}} 后会画出 R/B 标记'
            )
    for item in spec["variables"]:
        limit = STOCK_NAME_ADVICE if item["kind"] == "stock" else NAME_ADVICE
        if text_width(item["name"]) > limit:
            advice.append(
                f"名字「{item['name']}」偏长，建议精简到"
                f"{limit // 12} 个字以内（去掉“的”等修饰），图上更紧凑"
            )
    return advice + view_advice(views)


def build_model(spec):
    return _prepare_model(spec)[0]


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


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
        print(f"排版仍有几何问题，详见 {report_path}")
    if report["feedback"]["status"] == "needs_review":
        print(f"部分已标极性或回路需要领域与取值范围核对，见 {report_path}")
    drawn = report["sketch_decisions"]["loop_markers"]
    if drawn:
        print("回路标记: " + "，".join(f"{row['label']} {row['loop']}".strip() for row in drawn))
    for index, line in enumerate(report["advice"], 1):
        print(f"建议 {index}: {line}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    return command_build(args.spec, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
