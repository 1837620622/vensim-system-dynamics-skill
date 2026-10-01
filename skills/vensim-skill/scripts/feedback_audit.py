"""核对箭头极性和明确指定的反馈回路；不按名称、相关性或图形方向猜符号。"""

from __future__ import annotations

import argparse
import ast
import json
import math
from pathlib import Path

from mdl_document import MdlDocument, atomic_write, preflight_outputs
from vensim_engine import _to_python_expr, canonical_name, extract_deps, parse_equations

SIGNS = {"+": 1, "S": 1, "s": 1, "-": -1, "O": -1, "o": -1}


def infer_polarity(expression, source, equations):
    """保守分析偏导的符号区间。其他变量保持不变，字面常量按当前 MDL 取值。

    只覆盖线性组合、常量乘除和 MIN/MAX；未知非线性或作用相消时不猜。
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


def check_loops(equations, loops):
    if not isinstance(loops, list):
        raise ValueError("feedback_loops 必须是列表")
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
            raise ValueError("回路 polarity 仅支持 R 或 B；不能用顺逆时针代替回路正负")
        signs = []
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
            signs.append(infer_polarity(expression, source, equations))
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
                else "verified",
                "native_symbol_placement_verified": False,
            }
        )
    return result


def audit_feedback(text, loops=None):
    from sketch_geometry import visible
    from vensim_autolayout import parse_views

    equations = parse_equations(text, expand=False)
    aliases = {canonical_name(name): name for name in equations}
    views = parse_views(
        text.splitlines(True), {name for name, eq in equations.items() if eq.integ_flow is not None}
    )
    rows, unresolved = [], []
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
                # 信息箭头指向存量时不能无条件拿初值关系判断。若 source
                # 只出现在净流率中，它仍是动态作用；只有初值中存在时才标为
                # initial。两者同时出现则保守地要求人工核对。
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
    loop_rows = check_loops(equations, [] if loops is None else loops)
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
        "native_verified": False,
        "symbol_placement_requires_native_review": True,
        "scope": "只证明支持表达式在当前字面常量下的直接作用；未标注不等于已验证。重跑改变参数后须重查；回路性质不表示动态稳定性。",
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
