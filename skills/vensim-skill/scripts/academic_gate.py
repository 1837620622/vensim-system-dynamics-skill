#!/usr/bin/env python3
"""系统动力学论文级建模门禁。

这个脚本不替代 Vensim 的语法、单位和行为检验，而是把容易被“看起来能跑”
掩盖的研究设计问题提前拦截：边界和参考资料是否明确、存量是否有真实初值、
历史期是否把观测序列回填进内生方程、指定的派生指标是否依赖模型变量。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mdl_document import atomic_write, separate_output  # noqa: E402
from vensim_autolayout import _read_text  # noqa: E402
from vensim_engine import (  # noqa: E402
    _matching_paren,
    _name_pattern,
    _split_top_level_args,
    _to_python_expr,
    canonical_name,
    extract_deps,
    parse_equations,
)

CONTROL_NAMES = {"INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER"}
STANDARDIZATION_HINT = re.compile(r"标准化|归一化|normaliz", re.I)
TIME_TOKEN = re.compile(r"(?<![\w$])TIME(?![\w$])", re.I)
HISTORY_FUNCTIONS = re.compile(r"\b(GET\s+(?:XLS|DIRECT|DATA)|DATA\s+ONLY)\b", re.I)


def _iter_if_conditions(expression):
    """遍历 IF THEN ELSE 的条件参数，支持嵌套括号与查表表达式。"""
    lower = expression.lower()
    needle = "if then else"
    cursor = 0
    while True:
        start = lower.find(needle, cursor)
        if start < 0:
            return
        before = expression[start - 1] if start else ""
        after_index = start + len(needle)
        if before and (before.isalnum() or before in "_$"):
            cursor = after_index
            continue
        open_index = after_index
        while open_index < len(expression) and expression[open_index].isspace():
            open_index += 1
        if open_index >= len(expression) or expression[open_index] != "(":
            cursor = after_index
            continue
        try:
            close_index = _matching_paren(expression, open_index)
            args = _split_top_level_args(expression[open_index + 1 : close_index])
        except (ValueError, IndexError):
            cursor = after_index
            continue
        if args:
            yield args[0]
        cursor = close_index + 1


def _contains_time_condition(expression, names):
    """只识别条件中的内置 TIME，忽略名为“adjustment time”的业务变量。"""
    for condition in _iter_if_conditions(expression):
        cleaned = condition
        for name in sorted(names, key=len, reverse=True):
            if canonical_name(name) == "time":
                continue
            cleaned = re.sub(_name_pattern(name), " ", cleaned, flags=re.I)
        if TIME_TOKEN.search(cleaned):
            return True
    return False


def _standardization_subtractions(rhs, equations):
    """返回带单位表达式减裸数字的变量名；比值减 1 不应被误报。

    使用受限 AST 判断减号左侧是否已经是无量纲比值，而不是在原始字符串
    中搜索 ``- 1``。这样也不会把科学计数法 ``1e-6`` 当作减法。
    """
    name_map = {name: f"v{index}" for index, name in enumerate(equations)}
    reverse = {alias: name for name, alias in name_map.items()}
    dimensionless = {"", "dmnl", "dimensionless"}

    def unit_of(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return "dmnl"
        if isinstance(node, ast.Name):
            name = reverse.get(node.id)
            if name is None:
                return None
            return equations[name].unit.strip().lower()
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return unit_of(node.operand)
        if isinstance(node, ast.BinOp):
            left, right = unit_of(node.left), unit_of(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                return left if left == right else None
            if isinstance(node.op, ast.Mult):
                if left in dimensionless:
                    return right
                if right in dimensionless:
                    return left
                return None
            if isinstance(node.op, ast.Div):
                if left == right or (left in dimensionless and right in dimensionless):
                    return "dmnl"
                if right in dimensionless:
                    return left
                return None
            return None
        if isinstance(node, ast.IfExp):
            left, right = unit_of(node.body), unit_of(node.orelse)
            return left if left == right else None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"_sd_abs", "_sd_min", "_sd_max"}:
                units = [unit_of(arg) for arg in node.args]
                return units[0] if units and all(item == units[0] for item in units) else None
        return None

    def dimensional_dependencies(node):
        result = []
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in reverse:
                name = reverse[child.id]
                if equations[name].unit.strip().lower() not in dimensionless:
                    result.append(name)
        return list(dict.fromkeys(result))

    def is_numeric(node):
        return isinstance(node, ast.Constant) and type(node.value) in (int, float)

    try:
        translated = _to_python_expr(rhs, name_map)
        tree = ast.parse(translated, mode="eval")
    except (ValueError, SyntaxError, RecursionError):
        return []
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Sub):
            continue
        right = node.right.operand if isinstance(node.right, ast.UnaryOp) else node.right
        if is_numeric(right) and unit_of(node.left) not in dimensionless:
            violations.extend(dimensional_dependencies(node.left))
    return list(dict.fromkeys(violations))


def _iter_reference_files(path: Path) -> Iterable[Path]:
    allowed = {".pdf", ".doc", ".docx", ".md", ".txt", ".bib", ".ris"}
    if path.is_file():
        if path.suffix.lower() in allowed:
            yield path
        return
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.is_file() and child.suffix.lower() in allowed:
                yield child


def _load_spec(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"无法读取 spec：{exc}"]
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["spec 顶层必须是 JSON 对象"]
    project = data.get("project", {})
    if not isinstance(project, dict):
        errors.append("spec.project 必须是对象，包含 research_question 和 system_boundary")
        project = {}
    for key in ("research_question", "system_boundary"):
        value = project.get(key)
        if not value or str(value).strip().startswith("填写"):
            errors.append(f"spec.project.{key} 尚未填写研究问题或系统边界")
    pending = []

    def walk(value, prefix=""):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{prefix}.{k}" if prefix else str(k))
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{prefix}[{i}]")
        elif value == "pending_validation":
            pending.append(prefix)
        elif isinstance(value, str) and value.strip().startswith("填写"):
            pending.append(prefix)

    walk(data)
    if pending:
        errors.append(f"spec 仍有待验证字段（示例：{pending[0]}）")
    return errors


def check_model(
    model: Path,
    references: Path | None,
    spec: Path | None,
    require_coupling: bool,
    coupling_outputs: list[str],
    strict_endogenous: bool = False,
) -> dict:
    text = _read_text(model)
    equations = parse_equations(text)
    errors: list[str] = []
    warnings: list[str] = []
    boundary_time_switches: list[str] = []
    names = set(equations)

    if not equations:
        errors.append("未解析到方程区；请确认输入是 Vensim .mdl 而不是图片或导出表格")

    # 结构门禁：每个 INTEG 必须有初值，且每个业务方程应有单位。
    stocks = []
    for name, eq in equations.items():
        if name in CONTROL_NAMES:
            continue
        if eq.integ_flow is not None:
            stocks.append(name)
            if eq.integ_init_expr is None and eq.integ_init is None:
                errors.append(f"存量“{name}”没有可追溯的初始值")
        if not eq.unit:
            errors.append(f"变量“{name}”缺少单位；先补单位再做行为检验")

    if not stocks:
        warnings.append("模型没有检测到 INTEG 存量；如果这是 CLD 或纯代数模型，请在报告中说明")

    # 历史行为生成门禁：只有真实外部数据函数才构成“回填”证据。
    # 变量名含“历史/观测”本身不说明它来自外部序列，不能据此否定内生存量。
    history_hits = []
    endogenous_history_hits = []
    stock_flow_dependencies = {
        dependency
        for equation in equations.values()
        if equation.integ_flow is not None
        for dependency in extract_deps(equation.integ_flow, names)
    }
    for name, eq in equations.items():
        rhs = eq.integ_flow or eq.rhs
        if HISTORY_FUNCTIONS.search(rhs):
            history_hits.append(f"{name}: 使用外部数据函数")
            if eq.integ_flow is not None:
                endogenous_history_hits.append(name)
        if _contains_time_condition(rhs, names):
            # 变量名不再提供豁免；被存量流率使用的分段方程必须人工说明其
            # 边界含义，避免把 scenario output 一类名称当成结构证明。
            if name in stock_flow_dependencies or eq.integ_flow is not None:
                warnings.append(
                    f"{name}: 方程按 TIME 分段且位于存量/流率路径；请证明这是边界输入而非历史输出回放"
                )
            else:
                boundary_time_switches.append(name)
    if history_hits:
        message = "检测到需核对的历史/外部数据输入：" + "；".join(history_hits[:4])
        # 外部需求与政策数据可以是合理边界；严格内生检验必须由任务显式选择。
        if strict_endogenous and endogenous_history_hits:
            errors.append("严格内生检查发现可能的历史路径注入：" + "；".join(history_hits[:4]))
        else:
            warnings.append(message + "；请区分外生边界驱动、数据比较和输出路径回填")

    if require_coupling:
        outputs = coupling_outputs
        if not outputs:
            errors.append(
                "派生指标检查必须通过 --derived-output 明确本项目的输出变量，不能套用其他案例名称"
            )
        missing = [name for name in outputs if name not in names]
        if missing:
            errors.append("派生指标检查缺少所选输出：" + "、".join(missing))
        elif outputs:
            for name in outputs:
                rhs = equations[name].rhs or ""
                if not extract_deps(rhs, names):
                    errors.append(f"所选派生输出“{name}”没有来自模型变量的计算依赖")
                if HISTORY_FUNCTIONS.search(rhs):
                    errors.append(f"所选派生输出“{name}”疑似直接读取历史评价序列，请核对计算来源")

            # 量纲门禁：带单位的指标不能直接减裸数字做标准化。应把历史边界
            # 写成同单位的模型参数，
            # 这样 Vensim Units Check 才能验证归一化前后的量纲守恒。
            for name, equation in equations.items():
                if not STANDARDIZATION_HINT.search(name):
                    continue
                rhs = equation.rhs or ""
                dimensional = _standardization_subtractions(rhs, equations)
                if dimensional:
                    errors.append(
                        f"标准化方程“{name}”对带单位变量 {','.join(dimensional[:3])} 使用裸数字边界；"
                        "请核对同单位的边界参数，并在原生 Vensim 验证量纲"
                    )

    if references is None:
        warnings.append(
            "未提供 references 目录；论文模型应先核对领域文献和方法文献，再确定边界、方程与参数"
        )
    else:
        refs = list(_iter_reference_files(references))
        if not refs:
            errors.append(f"references 路径没有可读文献文件：{references}")

    if spec is not None:
        errors.extend(_load_spec(spec))
    else:
        warnings.append("未提供 model_spec；建议在建模前填写研究问题、系统边界、变量来源和验证计划")

    return {
        "model": str(model),
        "equation_count": len(equations),
        "stock_count": len(stocks),
        "stocks": stocks,
        "history_mode": "strict_endogenous" if strict_endogenous else "review_external_inputs",
        "boundary_time_switches": boundary_time_switches,
        "require_coupling": require_coupling,
        "errors": errors,
        "warnings": warnings,
        "pass": not errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--references", type=Path, help="参考文献目录或文件")
    parser.add_argument("--spec", type=Path, help="已填写的 model_spec JSON")
    parser.add_argument(
        "--require-derived",
        "--require-coupling",
        dest="require_coupling",
        action="store_true",
        help="检查明确指定的派生指标；旧耦合检查参数保留为别名",
    )
    parser.add_argument(
        "--derived-output",
        "--coupling-output",
        dest="coupling_output",
        action="append",
        default=[],
        help="本项目要求检查的派生输出变量名，可重复",
    )
    parser.add_argument(
        "--strict-endogenous", action="store_true", help="明确要求严格检查存量流率中的历史路径回填"
    )
    parser.add_argument("--report", type=Path, help="写入 JSON 审计报告")
    args = parser.parse_args(argv)
    if not args.model.exists():
        parser.error(f"模型不存在：{args.model}")
    if args.report:
        separate_output(
            args.report,
            [
                args.model,
                *([args.spec] if args.spec else []),
                *(list(_iter_reference_files(args.references)) if args.references else []),
            ],
        )
    report = check_model(
        args.model,
        args.references,
        args.spec,
        args.require_coupling,
        args.coupling_output,
        args.strict_endogenous,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        atomic_write(
            args.report, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        )
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
