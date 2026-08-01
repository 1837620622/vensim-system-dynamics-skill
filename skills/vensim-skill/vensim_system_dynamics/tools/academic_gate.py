#!/usr/bin/env python3
"""系统动力学论文级建模门禁。

这个脚本不替代 Vensim 的语法、单位和行为检验，而是把容易被“看起来能跑”
掩盖的研究设计问题提前拦截：边界和参考资料是否明确、存量是否有真实初值、
历史期是否把观测序列回填进内生方程、耦合协调度是否由模型输出连续生成。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vensim_autolayout import _read_text  # noqa: E402
from vensim_engine import extract_deps, parse_equations  # noqa: E402


CONTROL_NAMES = {"INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER"}
HISTORY_FUNCTIONS = re.compile(r"\b(GET\s+(?:XLS|DIRECT|DATA)|GET\s+DIRECT|DATA\s+ONLY)\b", re.I)
HISTORY_TERMS = re.compile(r"历史|观测|实际值|实际输出|回放|重构|预测输出|history|observed|replay", re.I)
TIME_SWITCH = re.compile(r"IF\s+THEN\s+ELSE\s*\([^)]*\bTIME\b", re.I)
COUPLING_HINTS = ("耦合", "协调", "U1", "U2", "coupling", "coordination")
STANDARDIZATION_HINT = re.compile(r"标准化|归一化|normaliz", re.I)
BARE_UNIT_SUBTRACTION = re.compile(r"-\s*\d+(?:\.\d+)?")
BOUNDARY_SWITCH_HINTS = ("情景", "policy", "scenario")


def _iter_reference_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.is_file() and child.suffix.lower() in {".pdf", ".doc", ".docx", ".md", ".txt", ".bib", ".ris"}:
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
        errors.append("检测到可能的历史路径注入：" + "；".join(history_hits[:4]))

    if require_coupling:
        outputs = coupling_outputs or ["班列综合发展指数", "区域经济综合发展指数", "子系统耦合度", "双系统综合发展指数", "系统耦合协调度"]
        missing = [name for name in outputs if name not in names]
        if missing:
            errors.append("耦合协调门禁缺少模型内生输出：" + "、".join(missing))
        else:
            formula_text = " ".join((equations[name].rhs or "") for name in outputs)
            if not any(hint.lower() in formula_text.lower() for hint in COUPLING_HINTS):
                errors.append("耦合协调输出存在，但方程中未看到U1/U2/C/T/D或耦合协调计算痕迹")
            for name in outputs:
                rhs = equations[name].rhs or ""
                if HISTORY_TERMS.search(rhs) or HISTORY_FUNCTIONS.search(rhs):
                    errors.append(f"耦合协调输出“{name}”疑似直接读取历史评价序列，不符合内生生成要求")

            # 量纲门禁：带单位的指标不能直接减裸数字做标准化。应把历史边界
            # 写成同单位的模型参数（例如“评价期末地区生产总值 ~ CNY100m”），
            # 这样 Vensim Units Check 才能验证归一化前后的量纲守恒。
            for name, equation in equations.items():
                if not STANDARDIZATION_HINT.search(name):
                    continue
                rhs = equation.rhs or ""
                if not BARE_UNIT_SUBTRACTION.search(rhs):
                    continue
                deps = extract_deps(rhs, names)
                dimensional = [
                    dep for dep in deps
                    if equations.get(dep) is not None
                    and equations[dep].unit
                    and equations[dep].unit.lower() not in {"dmnl", "dimensionless"}
                ]
                if dimensional:
                    errors.append(
                        f"标准化方程“{name}”对带单位变量 {','.join(dimensional[:3])} 使用裸数字边界；"
                        "请建立同单位的期初/期末锚点参数后再做归一化"
                    )
    else:
        if any(any(hint.lower() in name.lower() for hint in COUPLING_HINTS) for name in names):
            warnings.append("模型包含耦合/协调命名；若论文要报告D，请用 --require-coupling 明确检查U1/U2/C/T/D")

    if references is None:
        warnings.append("未提供 references 目录；论文模型应先核对领域文献和方法文献，再确定边界、方程与参数")
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
        "history_mode": "endogenous",
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
    parser.add_argument("--require-coupling", action="store_true", help="要求模型内生生成U1/U2/C/T/D")
    parser.add_argument("--coupling-output", action="append", default=[], help="耦合输出变量名，可重复")
    parser.add_argument("--report", type=Path, help="写入 JSON 审计报告")
    args = parser.parse_args(argv)
    if not args.model.exists():
        parser.error(f"模型不存在：{args.model}")
    report = check_model(args.model, args.references, args.spec, args.require_coupling, args.coupling_output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
