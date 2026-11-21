#!/usr/bin/env python3
"""由建模 JSON 生成因果回路图（CLD）的 Vensim 文件。

    cld spec.json --output model_cld.mdl [--include-constants] [--no-overall]

与 `build` 读同一份 JSON，写两个文件：

- ``model_cld.mdl``：每个子系统（变量的 ``view``）一个视图。变量画成不带框的文字，
  存量没有方框，流量没有阀门和管道；连线标 ``+``/``-`` 极性，核对通过的回路画
  R/B 标记。来自其他子系统的原因变量按 Vensim 习惯以影子引用出现。
- ``model_cld_总体.mdl``（英文模型为 ``_overall``）：全模型一个视图，所有变量都是
  正常变量，没有影子，用作总体因果回路图。

方程写成 ``A FUNCTION OF(原因变量, ...)``，这是 Vensim 定性模型的写法，只用于出图，
不用于仿真。极性的来源与 `build` 相同：``links`` 中声明的优先，没写的由方程推导，
推不出的按基准运行数值求偏导；流量对流入存量记 ``+``、对流出存量记 ``-``。
默认不画常量（参数是外生输入，不在反馈结构里），``--include-constants`` 可以加上。
排版沿用 natural 引擎和回路标记规则。
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from mdl_document import atomic_write, preflight_outputs, separate_output
from vensim_engine import canonical_name, extract_deps, parse_equations

SIGN_CODES = {"+": ord("+"), "-": ord("-")}


def _text_width(name: str, scale: float) -> int:
    wide = sum(1 for ch in name if ord(ch) > 0x2E80)
    return round(max(32, wide * 12 + (len(name) - wide) * 6 + 8) * scale / 2)


def cld_links(spec, include_constants=False):
    """返回 (links, loops, notes)。links 为 [(原因, 结果, 极性, 延迟)]。"""
    from feedback_audit import check_loops, infer_polarity, numeric_link_signs
    from model_builder import _model_text

    text, notes = _model_text(spec)
    equations = parse_equations(text, expand=False)
    items = {item["name"]: item for item in spec["variables"]}
    stocks = {name for name, item in items.items() if item["kind"] == "stock"}
    constants = {name for name, item in items.items() if item["kind"] == "constant"}
    numeric = numeric_link_signs(text)
    declared = {}
    delays = set()
    for link in spec.get("links", []):
        key = (canonical_name(link["from"]), canonical_name(link["to"]))
        if link.get("polarity"):
            # S/s 与 O/o 是 + / - 的同义写法，统一成 +/-，不能在下面被当成“推不出”
            declared[key] = {"S": "+", "s": "+", "O": "-", "o": "-"}.get(
                link["polarity"], link["polarity"]
            )
        if link.get("delay"):
            delays.add((link["from"], link["to"]))
    known = set(items)
    links, unsigned = [], []
    for target in items:
        if target in constants:
            continue
        if target in stocks:
            for flow, flow_item in items.items():
                if flow_item["kind"] != "flow":
                    continue
                if flow_item.get("to") == target:
                    links.append((flow, target, "+", (flow, target) in delays))
                if flow_item.get("from") == target:
                    links.append((flow, target, "-", (flow, target) in delays))
            continue
        expression = equations[target].rhs
        for dep in extract_deps(expression, known):
            if dep == target or (dep in constants and not include_constants):
                continue
            key = (canonical_name(dep), canonical_name(target))
            polarity = declared.get(key) or infer_polarity(expression, dep, equations)
            if polarity not in {"+", "-"}:
                polarity = numeric.get(key, "")
            if polarity not in {"+", "-"}:
                polarity = ""
                unsigned.append([dep, target])
            links.append((dep, target, polarity, (dep, target) in delays))
    loops = [
        row
        for row in check_loops(equations, spec.get("feedback_loops", []), declared, numeric)
        if row["status"] in {"verified", "declared", "numeric"}
    ]
    # 回路编号全模型统一：同一条回路在子系统图和总体图里是同一个 R/B 编号
    counters = {"R": 0, "B": 0}
    for row in loops:
        counters[row["expected"]] += 1
        row["label"] = f"{row['expected']}{counters[row['expected']]}"
    view_of = {
        name: str(item.get("view", spec.get("name", "系统动力学模型"))).strip()
        for name, item in items.items()
    }
    for move in notes.get("moved_constants", []):
        view_of[move["variable"]] = move["to"]
    return (
        links,
        loops,
        view_of,
        {"unsigned_links": unsigned, "units": {n: i.get("unit", "") for n, i in items.items()}},
    )


def _equation_block(spec, members, links, units):
    causes = {name: [] for name in members}
    for source, target, _polarity, _delay in links:
        if target in causes and source not in causes[target]:
            causes[target].append(source)
    lines = ["{UTF-8}\n"]
    for name in members:
        args = ", ".join(causes[name])
        rhs = f"A FUNCTION OF({args})" if args else "A FUNCTION OF()"
        lines.append(f"{name} = {rhs}\n\t~ {units.get(name, '')}\n\t~ 因果回路图变量\n\t|\n")
    time = spec["time"]
    unit = time.get("unit", "Year")
    for control, value in (
        ("INITIAL TIME", time["initial"]),
        ("FINAL TIME", time["final"]),
        ("TIME STEP", time["step"]),
        ("SAVEPER", time.get("saveper", time["step"])),
    ):
        lines.append(f"{control} = {value}\n\t~ {unit}\n\t~\n\t|\n")
    return "\n".join(lines)


def _view_records(view_name, members, defined, links, loops, font, font_size):
    """一个 CLD 视图：变量为无框文字（形状 8），其他视图的原因变量用影子（灰色）。"""
    scale = font_size / 12
    records = [
        r"\\\---/// Sketch information - do not modify anything except names",
        "V300  Do not put anything below this section - it will be ignored",
        "*" + view_name,
        f"$192-192-192,0,{font}|{font_size}||0-0-0|0-0-0|0-0-0|-1--1--1|-1--1--1|96,96,100,0",
    ]
    ids, positions = {}, {}
    order = list(members)
    radius = max(160.0, 26.0 * len(order))
    for index, name in enumerate(order):
        angle = -math.pi / 2 + math.tau * index / max(1, len(order))
        x = round(radius + 120 + radius * math.cos(angle))
        y = round(radius * 0.75 + 100 + radius * 0.75 * math.sin(angle))
        oid = len(ids) + 1
        ids[name] = oid
        positions[oid] = (x, y)
        bits = 131 if name in defined else 130
        records.append(
            f"10,{oid},{name},{x},{y},{_text_width(name, scale)},{round(12 * scale)},8,{bits},0,0,0,0,0,0,0,0,0,0,0,0"
        )
    next_id = len(ids) + 1
    markers = []
    for row in loops:
        if not set(row["variables"]) <= set(members):
            continue
        label = row["label"]
        points = [positions[ids[name]] for name in row["variables"]]
        x = round(sum(p[0] for p in points) / len(points))
        y = round(sum(p[1] for p in points) / len(points))
        size = round(12 * scale)
        records.append(
            f"12,{next_id},0,{x},{y},{size},{size},5,135,0,8,-1,0,0,0,"
            f"-1--1--1,0-0-0,|{max(9, font_size - 2)}||0-0-0"
        )
        records.append(label)
        positions[next_id] = (x, y)
        markers.append({"label": label, "loop": row["name"], "variables": row["variables"]})
        next_id += 1
    for source, target, polarity, delay in links:
        if source not in ids or target not in ids or target not in defined:
            # 影子变量只作原因出现，不接收箭头，免得 Vensim 里出现指向影子的连线
            continue
        s, t = ids[source], ids[target]
        x = round((positions[s][0] + positions[t][0]) / 2)
        y = round((positions[s][1] + positions[t][1]) / 2)
        code = SIGN_CODES.get(polarity, 0)
        records.append(
            f"1,{next_id},{s},{t},1,0,{code},0,1,{64 | (1 if delay else 0)},0,0-0-0,,1|({x},{y})|"
        )
        next_id += 1
    return records, markers


def _layout(text, markers_by_view):
    """沿用 build 的 natural 排版、回路标记归位和几何验收。"""
    from model_builder import _marker_nodes, _natural_views
    from natural_layout import _set_fields, loop_marker_shape, natural_view
    from sketch_geometry import measure_view, quality_pass
    from vensim_autolayout import parse_views

    lines = text.splitlines(keepends=True)
    views = parse_views(lines, set(), set())
    markers = {
        view.name: _marker_nodes(view, lines, markers_by_view.get(view.name, [])) for view in views
    }
    jobs = [
        (
            view,
            {
                "layout_mode": "natural",
                "_marker_loops": markers[view.name],
                "_free_row_order": True,
            },
        )
        for view in views
    ]
    results = _natural_views(lines, jobs, natural_view)
    insertions = [row for _item, extra in results for row in extra]
    for position, block in sorted(insertions, key=lambda row: row[0], reverse=True):
        lines[position:position] = block.splitlines(keepends=True)
    views = parse_views(lines, set(), set())
    for view in views:
        for oid, nodes in markers.get(view.name, {}).items():
            index = view.objects[oid].line_index
            lines[index] = _set_fields(lines[index], {7: loop_marker_shape(view, nodes)})
    views = parse_views(lines, set(), set())
    report = []
    for (item, _extra), view in zip(results, views, strict=True):
        after = measure_view(view)
        report.append(
            {
                "view": view.name,
                "pass": quality_pass(after),
                "after": after,
                **{key: item[key] for key in ("shadow_splits",) if key in item},
            }
        )
    return "".join(lines), report


def build_cld(spec, include_constants=False, overall=True):
    """返回 {"subsystems": (text, report), "overall": (text, report) 或 None, "notes": …}。"""
    links, loops, view_of, notes = cld_links(spec, include_constants)
    sketch = spec.get("sketch", {})
    font = sketch.get("font_family", "Vensim Sans SC")
    font_size = sketch.get("font_size", 12)
    linked = {name for link in links for name in link[:2]}
    shown = [name for name in view_of if name in linked]
    notes["isolated"] = sorted(
        name
        for name in view_of
        if name not in linked
        and (
            include_constants
            or next(i for i in spec["variables"] if i["name"] == name)["kind"] != "constant"
        )
    )
    units = notes.pop("units")

    def file_text(view_members):
        records, markers_by_view = [], {}
        for view_name, members, defined in view_members:
            body, markers = _view_records(
                view_name, members, defined, links, loops, font, font_size
            )
            records += body
            markers_by_view[view_name] = markers
        records += ["///---" + chr(92) * 3, ""]
        text = _equation_block(spec, shown, links, units) + "\n" + "\n".join(records)
        laid, report = _layout(text, markers_by_view)
        return laid, report, markers_by_view

    subsystem_views = []
    for view_name in dict.fromkeys(view_of[name] for name in shown):
        defined = [name for name in shown if view_of[name] == view_name]
        sources = [
            source
            for source, target, _p, _d in links
            if target in defined and source not in defined and source in shown
        ]
        members = defined + list(dict.fromkeys(sources))
        subsystem_views.append((view_name, members, set(defined)))
    sub_text, sub_report, sub_markers = file_text(subsystem_views)
    result = {
        "subsystems": (sub_text, {"views": sub_report, "loop_markers": sub_markers}),
        "overall": None,
        "notes": notes,
    }
    if overall:
        title = "总体因果回路图" if spec.get("language", "zh") == "zh" else "Overall CLD"
        all_text, all_report, all_markers = file_text([(title, shown, set(shown))])
        result["overall"] = (all_text, {"views": all_report, "loop_markers": all_markers})
    return result


def overall_path(output: Path, language: str) -> Path:
    suffix = "_总体" if language == "zh" else "_overall"
    return output.with_name(output.stem + suffix + output.suffix)


def command_cld(
    spec_path: Path, output: Path, include_constants=False, overall=True, allow_unsigned=False
) -> int:
    separate_output(output, [spec_path])
    if output.suffix.lower() != ".mdl":
        raise ValueError("CLD 输出必须是 .mdl")
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    if not isinstance(spec, dict) or not isinstance(spec.get("variables"), list):
        raise ValueError("模型规范必须为含 variables 的 JSON 对象")
    second = overall_path(output, spec.get("language", "zh"))
    report_path = output.with_suffix(".mdl.cld_report.json")
    targets = [output, report_path] + ([second] if overall else [])
    for path in targets:
        if path.exists():
            raise ValueError(f"输出已存在，请使用新文件名: {path}")
    preflight_outputs(targets, [spec_path])
    result = build_cld(spec, include_constants, overall)
    atomic_write(output, result["subsystems"][0].encode("utf-8"))
    report = {"subsystems": result["subsystems"][1], "notes": result["notes"]}
    if overall:
        atomic_write(second, result["overall"][0].encode("utf-8"))
        report["overall"] = result["overall"][1]
    atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    print(f"已生成子系统因果回路图: {output}")
    if overall:
        print(f"已生成总体因果回路图: {second}")
    if result["notes"]["unsigned_links"]:
        print(
            f"有 {len(result['notes']['unsigned_links'])} 条连线推不出极性。每条连线都要标正负号，"
            f"交付前必须在 links 中写明 polarity，逐条见 {report_path}"
        )
    failed = [
        row["view"]
        for part in ("subsystems", "overall")
        if part in report
        for row in report[part]["views"]
        if not row["pass"]
    ]
    if failed:
        print("以下视图仍有几何问题，请在 Vensim 中微调: " + "，".join(failed))
    if result["notes"]["unsigned_links"] and not allow_unsigned:
        print("FAIL: 有连线缺极性，退出码 1；草稿阶段可加 --allow-unsigned")
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("spec", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-constants", action="store_true", help="同时画出常量（参数）")
    parser.add_argument("--no-overall", action="store_true", help="不生成总体因果回路图")
    parser.add_argument(
        "--allow-unsigned",
        action="store_true",
        help="草稿阶段允许连线缺极性，退出码仍为 0（交付前不要用）",
    )
    args = parser.parse_args(argv)
    return command_cld(
        args.spec, args.output, args.include_constants, not args.no_overall, args.allow_unsigned
    )


if __name__ == "__main__":
    raise SystemExit(main())
