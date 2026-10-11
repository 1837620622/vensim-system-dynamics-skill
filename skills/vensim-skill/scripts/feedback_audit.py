"""核对箭头极性和明确指定的反馈回路；不按名称、相关性或图形方向猜符号。"""

from __future__ import annotations

import argparse
import ast
import json
import math
import re
from pathlib import Path

from mdl_document import MdlDocument, atomic_write, preflight_outputs
from vensim_engine import _to_python_expr, canonical_name, extract_deps, parse_equations

# ---------------------------------------------------------------------------
# 极性推断：按方程求偏导符号
# ---------------------------------------------------------------------------


SIGNS = {"+": 1, "S": 1, "s": 1, "-": -1, "O": -1, "o": -1}


def infer_polarity(expression, source, equations):
    """保守分析偏导的符号区间。其他变量保持不变，字面常量按当前 MDL 取值。

    只覆盖线性组合、常量乘除和 MIN/MAX；其他非线性或作用相消时返回 unknown。
    返回 + / - / 0 / unknown；非严格单调的饱和区允许导数为零。
    """
    names = {name: f"v{i}" for i, name in enumerate(equations)}
    constants = {}
    for name, eq in equations.items():
        if name == source:
            continue
        try:
            value = float(eq.rhs)
            if math.isfinite(value):
                constants[names[name]] = value
        except ValueError:
            pass

    def walk(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return float(node.value), (0.0, 0.0)
        if isinstance(node, ast.Name):
            if node.id == names.get(source):
                return None, (1.0, 1.0)
            if node.id in names.values():
                return constants.get(node.id), (0.0, 0.0)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value, derivative = walk(node.operand)
            if isinstance(node.op, ast.USub):
                return -value if value is not None else None, (-derivative[1], -derivative[0])
            return value, derivative
        if isinstance(node, ast.BinOp):
            a, da = walk(node.left)
            b, db = walk(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                if isinstance(node.op, ast.Sub):
                    b, db = -b if b is not None else None, (-db[1], -db[0])
                return a + b if a is not None and b is not None else None, (
                    da[0] + db[0],
                    da[1] + db[1],
                )
            if isinstance(node.op, ast.Mult):
                if a is not None:
                    return a * b if b is not None else None, tuple(sorted((a * db[0], a * db[1])))
                if b is not None:
                    return None, tuple(sorted((b * da[0], b * da[1])))
            if isinstance(node.op, ast.Div) and b is not None and b != 0:
                return a / b if a is not None else None, tuple(sorted((da[0] / b, da[1] / b)))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.upper()
            in {
                "SMOOTH",
                "SMOOTH3",
                "SMOOTHI",
                "SMOOTH3I",
                "DELAY1",
                "DELAY3",
                "DELAY1I",
                "DELAY3I",
            }
            and node.args
            and not node.keywords
        ):
            # 平滑/延迟对输入保持正向单调；延迟时间等其他参数若含有
            # source，则作用路径不再能由这一条规则证明。
            rows = [walk(arg) for arg in node.args]
            if any(row[1] != (0.0, 0.0) for row in rows[1:]):
                raise ValueError("延迟时间也依赖 source")
            return rows[0]
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"_sd_min", "_sd_max"}
            and node.args
            and not node.keywords
        ):
            rows = [walk(arg) for arg in node.args]
            function = min if node.func.id == "_sd_min" else max
            value = (
                function(row[0] for row in rows)
                if all(row[0] is not None for row in rows)
                else None
            )
            return value, (min(row[1][0] for row in rows), max(row[1][1] for row in rows))
        raise ValueError("极性证明未覆盖的表达式")

    try:
        node = ast.parse(_to_python_expr(expression, names), mode="eval")
        _, (low, high) = walk(node.body)
        if not all(math.isfinite(value) for value in (low, high)):
            return "unknown"
        if low == high == 0:
            return "0"
        if low >= 0 and high > 0:
            return "+"
        if high <= 0 and low < 0:
            return "-"
    except (ValueError, SyntaxError, RecursionError, OverflowError, ZeroDivisionError):
        pass
    return "unknown"


NUMERIC_POINTS = 60  # 数值求偏导最多取的保存时刻数，均匀抽取


def numeric_link_signs(text):
    """方程推不出符号的链接，沿基准运行数值求偏导。

    在均匀抽取的保存时刻，把来源变量加一个小增量、其他变量保持当时的取值，
    重新计算目标方程；整个运行中差值始终不为负（且至少一次为正）记为 +，始终
    不为正记为 -，否则不给结论。结论只在基准运行覆盖的取值范围内成立。
    返回 {(来源规范名, 目标规范名): "+" 或 "-"}；模型无法用内置引擎运行时返回空。
    """
    from vensim_engine import LookupTable, evaluate, get_time_bounds, simulate

    try:
        expanded = parse_equations(text)
        t0, tf, dt, saveper = get_time_bounds(expanded)
        result = simulate(expanded, t0, tf, dt, saveper)
    except (ValueError, RecursionError, ZeroDivisionError, OverflowError):
        return {}
    equations = parse_equations(text, expand=False)
    names = set(equations)
    name_map = {name: f"_v{i}" for i, name in enumerate(expanded)}
    lookups = {name: LookupTable(eq.lookup_pairs) for name, eq in expanded.items() if eq.is_lookup}
    step = max(1, math.ceil(len(result.times) / NUMERIC_POINTS))
    samples = range(0, len(result.times), step)
    signs = {}
    for target, eq in equations.items():
        if eq.integ_flow is not None or eq.is_lookup or eq.delay_fixed_args is not None:
            continue
        if re.search(r"\b(SMOOTH|DELAY)", eq.rhs, re.I):
            continue
        for source in extract_deps(eq.rhs, names):
            if source == target or infer_polarity(eq.rhs, source, equations) in {"+", "-"}:
                continue
            seen = set()
            try:
                for index in samples:
                    ctx = {name: values[index] for name, values in result.series.items()}
                    ctx.update(Time=result.times[index], __time_step__=dt)
                    base = evaluate(eq.rhs, ctx, lookups, name_map)
                    value = ctx[source]
                    ctx[source] = value + 1e-6 * max(1.0, abs(value))
                    change = evaluate(eq.rhs, ctx, lookups, name_map) - base
                    seen.add(0 if abs(change) <= 1e-12 * (1 + abs(base)) else (change > 0) * 2 - 1)
            except (ValueError, KeyError, ZeroDivisionError, OverflowError):
                continue
            if seen in ({1}, {0, 1}):
                signs[(canonical_name(source), canonical_name(target))] = "+"
            elif seen in ({-1}, {0, -1}):
                signs[(canonical_name(source), canonical_name(target))] = "-"
    return signs


# ---------------------------------------------------------------------------
# 回路核对
# ---------------------------------------------------------------------------


def check_loops(equations, loops, declared_links=None, numeric_links=None):
    """核对指定回路的 R/B。

    方程推不出符号的链接（如两个变量相乘），先用建模者在 links 或图上标的极性，
    回路状态记为 declared；仍没有的用基准运行的数值偏导（numeric_link_signs），
    状态记为 numeric。完全由方程推出的记为 verified。两个字典的键都是
    (来源, 目标) 的规范名。
    """
    if not isinstance(loops, list):
        raise ValueError("feedback_loops 必须是列表")
    declared_links = declared_links or {}
    numeric_links = numeric_links or {}
    aliases = {canonical_name(name): name for name in equations}
    result = []
    for loop in loops:
        if not isinstance(loop, dict) or set(loop) - {"name", "variables", "polarity"}:
            raise ValueError("回路只接受 name、variables、polarity")
        path = loop.get("variables")
        resolved_path = (
            [aliases.get(canonical_name(name)) if isinstance(name, str) else None for name in path]
            if isinstance(path, list)
            else None
        )
        if (
            not isinstance(path, list)
            or len(path) < 1
            or any(
                not isinstance(name, str) or resolved is None
                for name, resolved in zip(path, resolved_path or [], strict=False)
            )
            or resolved_path is None
            or len(resolved_path) != len(path)
            or len({canonical_name(name) for name in path}) != len(path)
        ):
            raise ValueError(
                "回路 variables 至少包含一个真实变量且不能重复；最后一个自动连回第一个"
            )
        path = resolved_path
        declared = loop.get("polarity")
        if declared is not None and (not isinstance(declared, str) or declared not in {"R", "B"}):
            raise ValueError("回路 polarity 只能是 R 或 B")
        signs, missing, used_declared, used_numeric = [], [], False, False
        for source, target in zip(path, path[1:] + path[:1], strict=False):
            eq = equations[target]
            expression = (
                eq.integ_flow
                if eq.integ_flow is not None
                else eq.delay_fixed_args[0]
                if eq.delay_fixed_args is not None
                else eq.rhs
            )
            if source not in extract_deps(expression, set(equations)):
                raise ValueError(
                    f"反馈路径不存在动态依赖: {source} → {target}；初值关系不构成动态回路"
                )
            sign = infer_polarity(expression, source, equations)
            if sign not in {"+", "-"}:
                key = (canonical_name(source), canonical_name(target))
                marked = declared_links.get(key)
                if SIGNS.get(marked):
                    sign, used_declared = "+" if SIGNS[marked] > 0 else "-", True
                elif key in numeric_links:
                    sign, used_numeric = numeric_links[key], True
                else:
                    missing.append([source, target])
            signs.append(sign)
        expected = (
            ("B" if signs.count("-") % 2 else "R")
            if all(sign in {"+", "-"} for sign in signs)
            else "unknown"
        )
        result.append(
            {
                "name": loop.get("name", ""),
                "variables": path,
                "link_signs": signs,
                "declared": declared,
                "expected": expected,
                "status": "needs_review"
                if expected == "unknown"
                else "conflict"
                if declared and declared != expected
                else "numeric"
                if used_numeric
                else "declared"
                if used_declared
                else "verified",
                # 方程推不出、也没有标注极性的链接，补上 links 后回路才能核对
                "unsigned_links": missing,
                "native_symbol_placement_verified": False,
            }
        )
    return result


# ---------------------------------------------------------------------------
# 整体审计与命令行
# ---------------------------------------------------------------------------


def audit_feedback(text, loops=None):
    from sketch_geometry import visible
    from vensim_autolayout import parse_views

    equations = parse_equations(text, expand=False)
    aliases = {canonical_name(name): name for name in equations}
    views = parse_views(
        text.splitlines(True), {name for name, eq in equations.items() if eq.integ_flow is not None}
    )
    rows, unresolved = [], []
    numeric = None  # 基准运行的数值偏导，第一次用到时才仿真
    for view in views:
        labels = {
            oid: aliases.get(canonical_name(obj.name), obj.name)
            for oid, obj in view.objects.items()
            if obj.kind == 10
        }
        ordered = sorted(view.objects.values(), key=lambda obj: obj.line_index)
        for previous, obj in zip(ordered, ordered[1:], strict=False):
            if previous.kind == 11 and obj.kind == 10 and obj.attached_to_valve:
                labels[previous.obj_id] = aliases.get(canonical_name(obj.name), obj.name)
        for arrow in view.arrows:
            if not visible(arrow):
                continue
            source, target = labels.get(arrow.from_id), labels.get(arrow.to_id)
            raw_polarity = arrow.fields[6] if len(arrow.fields) > 6 else "0"
            if source not in equations or target not in equations:
                if raw_polarity not in ("0", ""):
                    unresolved.append(
                        {
                            "view": view.name,
                            "arrow": arrow.obj_id,
                            "reason": "已标符号无法映射到已解析方程；需要原生核对",
                        }
                    )
                continue
            eq = equations[target]
            init_expression = eq.integ_init_expr or ""
            flow_expression = eq.integ_flow or ""
            ambiguous_initial_dynamic = False
            if eq.integ_flow is None:
                expression = eq.rhs
                kind = "information"
            elif arrow.is_physical_flow:
                expression = flow_expression
                kind = "flow"
            else:
                # 指向存量的箭头：source 只在净流率中出现是动态作用，只在初值中
                # 出现记为 initial，两处都出现时交给人工判断
                init_deps = extract_deps(init_expression, set(equations))
                flow_deps = extract_deps(flow_expression, set(equations))
                in_init = source in init_deps
                in_flow = source in flow_deps
                if in_init and in_flow:
                    expression = flow_expression
                    kind = "information"
                    ambiguous_initial_dynamic = True
                elif in_flow:
                    expression = flow_expression
                    kind = "information"
                else:
                    expression = init_expression
                    kind = "initial"
            expression = expression or ""
            try:
                code = int(raw_polarity or 0)
            except (TypeError, ValueError):
                code = -1
                unresolved.append(
                    {
                        "view": view.name,
                        "arrow": arrow.obj_id,
                        "reason": "箭头极性字段不是有效字符编码；需要原生核对",
                    }
                )
            displayed = chr(code) if 0 < code < 128 else "" if code == 0 else "unsupported"
            exists = source in extract_deps(expression, set(equations))
            expected = infer_polarity(expression, source, equations) if exists else "unknown"
            sign = SIGNS.get(displayed)
            status = (
                "no_dependency" if not exists else "unmarked" if not displayed else "needs_review"
            )
            if exists and expected == "0":
                status = "needs_review"
            elif ambiguous_initial_dynamic:
                status = "needs_review"
            elif exists and sign and expected != "unknown":
                status = "verified" if sign == SIGNS.get(expected) else "conflict"
            elif exists and sign and kind == "information":
                # 方程推不出：与基准运行的数值偏导一致记为 numeric，只在运行范围内成立
                if numeric is None:
                    numeric = numeric_link_signs(text)
                found = numeric.get((canonical_name(source), canonical_name(target)))
                if found and SIGNS[found] == sign:
                    status = "numeric"
            rows.append(
                {
                    "view": view.name,
                    "arrow": arrow.obj_id,
                    "source": source,
                    "target": target,
                    "kind": kind,
                    "displayed": displayed,
                    "expected": expected,
                    "status": status,
                    "native_dtype": arrow.fields[9] if len(arrow.fields) > 9 else "",
                }
            )
    marked = {
        (canonical_name(row["source"]), canonical_name(row["target"])): row["displayed"]
        for row in rows
        # 与数值偏导一致的符号不算建模者声明，回路由 numeric 字典给出依据
        if row["kind"] != "flow" and row["displayed"] in SIGNS and row["status"] != "numeric"
    }
    if loops and numeric is None:
        numeric = numeric_link_signs(text)
    loop_rows = check_loops(equations, [] if loops is None else loops, marked, numeric)
    conflicts = [row for row in rows if row["status"] in {"conflict", "no_dependency"}]
    conflicts.extend(row for row in loop_rows if row["status"] == "conflict")
    needs_review = bool(unresolved) or any(
        row["status"] == "needs_review" for row in [*rows, *loop_rows]
    )
    return {
        "links": rows,
        "loops": loop_rows,
        "conflicts": conflicts,
        "unresolved": unresolved,
        "status": "conflict" if conflicts else "needs_review" if needs_review else "checked_subset",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--spec", type=Path, help="可选建模 JSON，从 feedback_loops 读取待核对回路")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--strict", action="store_true", help="存在冲突或已标符号尚待核对时返回失败"
    )
    args = parser.parse_args(argv)
    spec = json.loads(args.spec.read_text(encoding="utf-8-sig")) if args.spec else {}
    if not isinstance(spec, dict) or not isinstance(spec.get("feedback_loops", []), list):
        raise ValueError("spec 必须是对象，feedback_loops 必须是列表")
    loops = spec.get("feedback_loops", [])
    report = audit_feedback(MdlDocument.read(args.model).semantic_text, loops)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        preflight_outputs([args.output], [args.model, *([args.spec] if args.spec else [])])
        atomic_write(args.output, (encoded + "\n").encode("utf-8"))
    print(encoded)
    return 2 if args.strict and report["status"] in {"conflict", "needs_review"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
