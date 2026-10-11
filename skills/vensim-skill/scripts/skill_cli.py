#!/usr/bin/env python3
"""Vensim skill 命令行入口，skill.sh 与 skill.cmd 都转发到这里。"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import plistlib
import re
import sys
from collections.abc import Callable
from pathlib import Path

if sys.version_info < (3, 10):  # noqa: UP036 - 旧解释器上给出可读的报错
    raise SystemExit("ERROR: Vensim skill 需要 Python 3.10 或更新版本")
sys.dont_write_bytecode = True

TOOLS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import model_builder  # noqa: E402
import vensim_autolayout  # noqa: E402
import vensim_engine  # noqa: E402

# ---------------------------------------------------------------------------
# 子命令转发
# ---------------------------------------------------------------------------


def _default_output(model: str, suffix: str) -> str:
    path = Path(model)
    return str(path.with_name(f"{path.stem}{suffix}"))


def _has_option(args: list[str], option: str) -> bool:
    return any(arg.lower().split("=", 1)[0] == option for arg in args)


def _invoke(module_main: Callable[[], int], argv: list[str]) -> int:
    """以给定参数调用各模块的 main，把常见异常转成一行错误信息。"""
    old_argv = sys.argv[:]
    sys.argv = [old_argv[0], *argv]
    try:
        return int(module_main() or 0)
    except SystemExit as exc:
        return int(exc.code or 0)
    except (
        ValueError,
        RuntimeError,
        OSError,
        ImportError,
        TypeError,
        AttributeError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        sys.argv = old_argv


# ---------------------------------------------------------------------------
# doctor：解释器、可选包、原生 Vensim 与绘图字体
# ---------------------------------------------------------------------------


def dependency_constraints():
    """把已安装版本与 requirements/constraints.txt 的下限逐项比较。"""
    rows = []
    text = (ROOT_DIR / "requirements/constraints.txt").read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([\w.-]+)>=(\d+(?:\.\d+)*)(?:,<(\d+(?:\.\d+)*))?", line.strip())
        if not match:
            raise ValueError(f"无法识别的依赖约束: {line}")
        name, minimum, maximum = match.groups()
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        row = {"package": name, "required": line.strip(), "installed": installed}
        if installed is None:
            row["status"] = "not_installed"
            rows.append(row)
            continue
        # 只比较纯数字版本；rc、dev 等预发布版本标记为需人工查看
        release = re.fullmatch(r"(\d+(?:\.\d+)*)(?:\.post\d+)?(?:\+[\w.-]+)?", installed)
        if release is None:
            row["status"] = "version_needs_review"
            rows.append(row)
            continue
        parts = [tuple(map(int, v.split("."))) for v in (release[1], minimum, maximum or "") if v]
        width = max(len(p) for p in parts)
        value, lower, *upper = (p + (0,) * (width - len(p)) for p in parts)
        valid = value >= lower and (not upper or value < upper[0])
        row["status"] = "satisfies_constraints" if valid else "upgrade_required"
        rows.append(row)
    return rows


def _native_vensim():
    found = []
    if sys.platform == "darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            for app in root.glob("*Vensim*.app"):
                info = app / "Contents/Info.plist"
                if info.is_file():
                    meta = plistlib.loads(info.read_bytes())
                    found.append(
                        {"path": str(app), "version": meta.get("CFBundleShortVersionString")}
                    )
    elif os.name == "nt":
        for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if root:
                found.extend({"path": str(p)} for p in Path(root).glob("Vensim*/vensim*.exe"))
    return found


def doctor_report():
    packages = {}
    for name in ("matplotlib", "pysd", "scipy", "networkx"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    fonts = {"status": "未安装 matplotlib，跳过字体检查"}
    if packages["matplotlib"]:
        from result_plotting import font_settings

        try:
            families = font_settings("中文变量存量流率时间−–±%0123456789")["font.family"]
            fonts = {"status": "ok", "families": families}
        except (RuntimeError, ValueError) as exc:
            fonts = {"status": "缺字", "detail": str(exc)}
    return {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "optional_packages": packages,
        "dependency_constraints": dependency_constraints(),
        "native_vensim": _native_vensim(),
        "plot_fonts": fonts,
    }


# ---------------------------------------------------------------------------
# crosscheck：内置 Euler 与 PySD 逐点比较
# ---------------------------------------------------------------------------


def _crosscheck() -> int:
    from mdl_document import atomic_write, preflight_outputs
    from simulation_runner import compare_backends, parse_overrides

    parser = argparse.ArgumentParser(description="逐点比较内置 Euler 与 PySD")
    parser.add_argument("model", type=Path)
    parser.add_argument("--var", action="append", required=True, dest="variables")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--set", action="append", default=[], dest="overrides")
    parser.add_argument("--time-step", type=float)
    parser.add_argument("--final-time", type=float)
    parser.add_argument("--saveper", type=float)
    parser.add_argument("--tolerance", type=float, default=1e-10)
    args = parser.parse_args()
    preflight_outputs([args.output], [args.model])
    report = compare_backends(
        args.model,
        args.variables,
        params=parse_overrides(args.overrides),
        time_step=args.time_step,
        final_time=args.final_time,
        saveper=args.saveper,
        tolerance=args.tolerance,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    atomic_write(args.output, (text + "\n").encode("utf-8"))
    print(text)
    return 0 if report["pass"] else 1


# ---------------------------------------------------------------------------
# 帮助与分发
# ---------------------------------------------------------------------------

HELP = """Vensim System Dynamics Skill
用法: ./skill.sh <命令> [参数]   Windows: skill.cmd <命令> [参数]

建模与排版
  doctor                              检查 Python、可选包、原生 Vensim 与字体
  build spec.json --output m.mdl      按存量、流向和方程生成 MDL（默认 natural 排版）
  inspect | audit | check m.mdl       列对象、查草图引用、查方程与时间设置
  units m.mdl                         列出缺失单位（不做量纲推导）
  fix m.mdl --output fixed.mdl        按显式选项修复，见 fix --help
  feedback m.mdl [--spec model.json]  核对箭头正负与指定回路的 R/B
  layout m.mdl --output out.mdl [--mode natural|refine|preserve]
         [--style monochrome|native-blue|preserve] [--split-shadows] [--preview p.html]
  visual m.mdl [--strict]             交叉、穿字、重叠、过短箭头与 <Time> 预留区检查
  preview m.mdl --output p.html       几何调试预览，不能替代原生导出图

仿真与图表
  simulate m.mdl --var 变量 [--output r.csv] [--backend builtin|pysd]
  crosscheck m.mdl --var 变量 --output check.json   内置 Euler 与 PySD 逐点比较
  graph m.mdl --var 变量 --output r.png
  compare base.mdl --scenario policy.mdl --var 变量 --output c.png
  plot-data series.csv --output r.png --time-unit 单位
  experiment m.mdl --spec experiment.json --output-dir results
  convergence m.mdl --var 变量 --output convergence.json
  calibrate m.mdl --spec calibration.json --data observed.csv --output-dir fit
  optimize m.mdl --spec policy.json --output-dir policy

各命令加 --help 查看完整参数。排版只改草图坐标与连线形状，不改方程。
非商业使用许可，作者 传康KK（万能程序员）。"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0].lower() in {"help", "-h", "--help"}:
        print(HELP)
        return 0
    cmd, rest = args[0].lower(), args[1:]
    args[0] = cmd

    if cmd == "doctor":
        print(json.dumps(doctor_report(), ensure_ascii=False, indent=2))
        return 0
    if cmd == "build":
        return _invoke(model_builder.main, rest)
    if cmd in {"inspect", "audit", "visual", "preview", "layout"}:
        if cmd == "layout" and rest and not _has_option(args, "--output"):
            args.extend(["--output", _default_output(rest[0], "_layout.mdl")])
        return _invoke(vensim_autolayout.main, args)
    if cmd in {"simulate", "graph"} and rest and not _has_option(args, "--output"):
        args.extend(
            [
                "--output",
                _default_output(rest[0], "_sim.csv" if cmd == "simulate" else "_graph.png"),
            ]
        )
    if cmd in {"simulate", "graph", "compare", "units", "check", "fix"}:
        return _invoke(vensim_engine.main, args)
    if cmd == "feedback":
        from feedback_audit import main as feedback_main

        return _invoke(feedback_main, rest)
    if cmd in {"experiment", "convergence"}:
        import experiments

        return _invoke(experiments.main, args)
    if cmd in {"calibrate", "optimize"}:
        from optimization import main as optimization_main

        return _invoke(optimization_main, args)
    if cmd == "plot-data":
        from result_plotting import main as plot_main

        return _invoke(plot_main, rest)
    if cmd == "crosscheck":
        return _invoke(_crosscheck, rest)
    print(f"ERROR: 未知命令 {cmd}，运行 --help 查看用法", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
