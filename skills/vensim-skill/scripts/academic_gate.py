#!/usr/bin/env python3
"""系统动力学论文级建模门禁。

这个脚本不替代 Vensim 的语法、单位和行为检验，而是把容易被“看起来能跑”
掩盖的研究设计问题提前拦截：边界和参考资料是否明确、存量是否有真实初值、
历史期是否把观测序列回填进内生方程、指定的派生指标是否依赖模型变量。
"""

from __future__ import annotations

import argparse
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
from vensim_engine import extract_deps, parse_equations  # noqa: E402

CONTROL_NAMES = {"INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER"}
HISTORY_FUNCTIONS = re.compile(r"\b(GET\s+(?:XLS|DIRECT|DATA)|GET\s+DIRECT|DATA\s+ONLY)\b", re.I)
HISTORY_TERMS = re.compile(
    r"历史|观测|实际值|实际输出|回放|重构|预测输出|history|observed|replay", re.I
)
TIME_SWITCH = re.compile(r"IF\s+THEN\s+ELSE\s*\([^)]*\bTIME\b", re.I)
STANDARDIZATION_HINT = re.compile(r"标准化|归一化|normaliz", re.I)
BARE_UNIT_SUBTRACTION = re.compile(r"-\s*\d+(?:\.\d+)?")
BOUNDARY_SWITCH_HINTS = ("情景", "policy", "scenario")


def _iter_reference_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.is_file() and child.suffix.lower() in {
                ".pdf",
                ".doc",
                ".docx",
                ".md",
                ".txt",
                ".bib",
                ".ris",
            }:
                yield child


def _load_spec(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"无法读取 spec：{exc}"]
    errors: list[str] = []
    project = data.get("project", {}) if isinstance(data, dict) else {}
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

    # 历史行为生成门禁：历史期的观测值只能作为外部边界驱动或比较序列，
    # 不应写成“历史状态/输出路径”注入同一组内生存量方程。
    history_hits = []
    for name, eq in equations.items():
        rhs = eq.integ_flow or eq.rhs
        if HISTORY_FUNCTIONS.search(rhs):
            history_hits.append(f"{name}: 使用外部数据函数")
        if eq.integ_flow is not None and HISTORY_TERMS.search(rhs):
            history_hits.append(f"{name}: 存量流率含历史/观测回填词")
        if TIME_SWITCH.search(rhs):
            # 情景/政策乘数是模型边界输入，按 TIME 在政策起始年切换属于
            # 正常实验设置；核心存量、流率和综合输出仍必须保持同一套方程。
            if any(hint.lower() in name.lower() for hint in BOUNDARY_SWITCH_HINTS):
                boundary_time_switches.append(name)
            else:
                warnings.append(f"{name}: 方程按 TIME 分段切换；请证明这是边界输入而非历史输出回放")
    if history_hits:
        message = "检测到需核对的历史/外部数据输入：" + "；".join(history_hits[:4])
        # 外部需求与政策数据可以是合理边界；严格内生检验必须由任务显式选择。
        if strict_endogenous and any("存量流率" in hit for hit in history_hits):
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
                if HISTORY_TERMS.search(rhs) or HISTORY_FUNCTIONS.search(rhs):
                    errors.append(f"所选派生输出“{name}”疑似直接读取历史评价序列，请核对计算来源")

            # 量纲门禁：带单位的指标不能直接减裸数字做标准化。应把历史边界
            # 写成同单位的模型参数，
            # 这样 Vensim Units Check 才能验证归一化前后的量纲守恒。
            for name, equation in equations.items():
                if not STANDARDIZATION_HINT.search(name):
                    continue
                rhs = equation.rhs or ""
                if not BARE_UNIT_SUBTRACTION.search(rhs):
                    continue
                deps = extract_deps(rhs, names)
                dimensional = [
                    dep
                    for dep in deps
                    if equations.get(dep) is not None
                    and equations[dep].unit
                    and equations[dep].unit.lower() not in {"dmnl", "dimensionless"}
                ]
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
