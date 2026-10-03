#!/usr/bin/env python3
"""跨平台 skill 包装入口，主要供 Windows skill.cmd 使用。"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import plistlib
import re
import sys
from collections.abc import Callable
from pathlib import Path

if sys.version_info < (3, 10):  # noqa: UP036 - 保留入口的明确版本诊断。
    raise SystemExit("ERROR: Vensim skill 需要 Python 3.10 或更新版本")
sys.dont_write_bytecode = True

TOOLS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parent
EXAMPLES_DIR = ROOT_DIR / "assets" / "examples"
TEMPLATES_DIR = ROOT_DIR / "assets" / "templates"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import academic_gate  # noqa: E402
import experiments  # noqa: E402
import model_builder  # noqa: E402
import vensim_autolayout  # noqa: E402
import vensim_engine  # noqa: E402


def _default_output(model: str, suffix: str) -> str:
    path = Path(model)
    return str(path.with_name(f"{path.stem}{suffix}"))


def _has_option(args: list[str], option: str) -> bool:
    return any(arg.lower().split("=", 1)[0] == option for arg in args)


def _invoke(module_main: Callable[[], int], argv: list[str]) -> int:
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


def _invoke_layout(argv: list[str]) -> int:
    return _invoke(vensim_autolayout.main, argv)


def _invoke_engine(argv: list[str]) -> int:
    return _invoke(vensim_engine.main, argv)


def dependency_constraints():
    """逐项检查当前解释器；约束匹配不代表实时漏洞扫描或完整安全证明。"""
    rows = []
    for line in (
        (ROOT_DIR / "requirements/constraints.txt").read_text(encoding="utf-8").splitlines()
    ):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([\w.-]+)>=(\d+(?:\.\d+)*)(?:,<(\d+(?:\.\d+)*))?", line.strip())
        if not match:
            raise ValueError(f"依赖约束格式未覆盖: {line}")
        name, minimum, maximum = match.groups()
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        row = {"package": name, "required": line.strip(), "installed": installed}
        if installed is None:
            row["status"] = "not_installed"
        else:
            # 只判定稳定 release/post/local 版本；预发布等形式保留待核对。
            release = re.fullmatch(r"(\d+(?:\.\d+)*)(?:\.post\d+)?(?:\+[\w.-]+)?", installed)
            if release is None:
                row["status"] = "version_needs_review"
            else:
                value = tuple(map(int, release[1].split(".")))
                lower = tuple(map(int, minimum.split(".")))
                upper = tuple(map(int, maximum.split("."))) if maximum else None
                width = max(len(value), len(lower), len(upper or ()))
                value += (0,) * (width - len(value))
                lower += (0,) * (width - len(lower))
                if upper is not None:
                    upper += (0,) * (width - len(upper))
                valid = value >= lower and (upper is None or value < upper)
                row["status"] = "satisfies_constraints" if valid else "upgrade_required"
        rows.append(row)
    return rows


def doctor_report():
    packages = {}
    for name in ("matplotlib", "pysd", "scipy", "mcp"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    native = []
    if sys.platform == "darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            for app in root.glob("*Vensim*.app"):
                info = app / "Contents/Info.plist"
                if info.is_file():
                    metadata = plistlib.loads(info.read_bytes())
                    native.append(
                        {
                            "path": str(app),
                            "bundle_version_raw": metadata.get("CFBundleShortVersionString"),
                            "version_note": "原始 bundle 字段；显示版本请在原生 About 界面确认",
                        }
                    )
    elif os.name == "nt":
        for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if root:
                native.extend(
                    {"path": str(path)} for path in Path(root).glob("Vensim*/vensim*.exe")
                )
    fonts = {"status": "需要可选 matplotlib 才能检查 Python 绘图字体"}
    if packages["matplotlib"]:
        from result_plotting import font_settings

        try:
            fonts = {
                "status": "基础中文与符号字形可用，实际图件导出时继续逐字检查",
                "families": font_settings("中文变量存量流率影子引用时间结果−–±%0123456789")[
                    "font.family"
                ],
            }
        except (RuntimeError, ValueError) as exc:
            fonts = {"status": "字体需配置", "detail": str(exc)}
    return {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "layout_engine": "native-python (骨架优先、局部邻域与原生圆弧)",
        "optional_packages": packages,
        "dependency_constraints": dependency_constraints(),
        "native_vensim": native,
        "plot_fonts": fonts,
        "official_dss_mcp": "需核对官方组件、许可与实际工具列表，未自动连接",
    }


def _doctor() -> int:
    print(json.dumps(doctor_report(), ensure_ascii=False, indent=2))
    return 0


def _quick(args: list[str]) -> int:
    if not args:
        print("ERROR: 用法: skill.cmd quick <model.mdl>", file=sys.stderr)
        return 2
    model = args[0]
    out = _default_output(model, "_autolayout.mdl")
    print("=== inspect ===")
    rc = _invoke_layout(["inspect", model])
    if rc:
        return rc
    print("=== audit ===")
    rc = _invoke_layout(["audit", model])
    if rc:
        return rc
    print("=== layout ===")
    rc = _invoke_layout(
        [
            "layout",
            model,
            "--output",
            out,
            "--route-information-arrows",
            "--preview",
            _default_output(model, "_layout.html"),
        ]
    )
    if rc:
        return rc
    print(f"完成: {out}  (请在 Vensim 打开并运行 Check Model 与 Units Check)")
    return 0


def _examples() -> int:
    rc = 0
    for model in sorted(EXAMPLES_DIR.glob("*.mdl")):
        print(f"========== {model.name} ==========")
        rc = _invoke_layout(["audit", str(model)]) or rc
    return rc


def _auto(args: list[str]) -> int:
    if not args:
        print("ERROR: 用法: skill.cmd auto <model.mdl> [--var V] [--keep-going]", file=sys.stderr)
        return 2
    model = args[0]
    passthrough = args[1:]
    sim_out = _default_output(model, "_sim.csv")
    graph_out = _default_output(model, "_graph.png")
    print("=== check ===")
    rc = _invoke_engine(["check", model])
    if rc:
        return rc
    print("=== simulate ===")
    rc = _invoke_engine(["simulate", model, "--output", sim_out, *passthrough])
    if rc:
        return rc
    print("=== graph ===")
    rc = _invoke_engine(["graph", model, "--output", graph_out, *passthrough])
    if rc:
        return rc
    print(f"完成: {sim_out}")
    print(f"完成: {graph_out}")
    return 0


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
    params = parse_overrides(args.overrides)
    preflight_outputs([args.output], [args.model])
    report = compare_backends(
        args.model,
        args.variables,
        params=params,
        time_step=args.time_step,
        final_time=args.final_time,
        saveper=args.saveper,
        tolerance=args.tolerance,
    )
    atomic_write(
        args.output,
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


def _help() -> int:
    print("""Vensim System Dynamics Skill
用法: ./skill.sh <命令> [参数]  /  skill.cmd <命令> [参数]

  doctor                           检查 Python、可选包、原生 Vensim 与字体
  build spec.json --output m.mdl    从明确的存量、流向和方程生成 MDL
  inspect model.mdl                列出对象与箭头
  audit model.mdl                  结构与语义预检
  check model.mdl                  方程、初值和时间设置预检
  feedback model.mdl [--spec model.json] [--strict] [--output feedback.json]
         核对箭头正负与指定回路的 R/B，复杂关系报告待核对
  layout model.mdl --output out.mdl [--mode circular|auto|preserve|refine]
         [--style monochrome|native-blue|preserve]
         [--config layout.json] [--preview review.html]
  preview model.mdl --output sketch.html [--compare-with before.mdl]
  visual model.mdl [--strict] [--output geometry.json]
  quick model.mdl                  检查、布局和前后预览
  simulate model.mdl --var 变量 [--output result.csv] [--plot result.png]
  crosscheck model.mdl --var 变量 --output backend-check.json
         逐点比较内置 Euler 与 PySD；需安装 requirements/pysd.txt
  graph model.mdl --var 变量 --output result.svg
  plot-data series.csv --output result.png --time-unit 单位
         绘图选项: --dpi 600 --formats png,pdf,svg（默认无标题，每个变量单独出图）
  compare base.mdl --scenario policy.mdl --var 变量 --output comparison.png
         仿真共用选项: --backend builtin|pysd --set '参数=数值'
                       --time-step 步长 --final-time 终点 --saveper 保存间隔
  experiment model.mdl --spec experiment.json --output-dir results [--plot out.png]
  convergence model.mdl --var 变量 --output convergence.json [--tolerance 0.01]
  calibrate model.mdl --spec calibration.json --data observed.csv --output-dir fit
  optimize model.mdl --spec policy.json --output-dir policy
         搜索需可选 SciPy；边界、目标、种子与仿真次数必须明确
  academic model.mdl --references refs --spec model_spec.json
  units model.mdl                  缺失单位预检，不能替代原生量纲检查
  fix model.mdl --output fixed.mdl 显式选择修复项，详见 fix --help
  auto model.mdl --var 变量        检查、仿真与绘图
  examples                        检查示例模型
  mcp --workspace <目录>          可选 stdio MCP，需安装 mcp 包

运行具体命令加 --help 查看参数。布局不会改方程或因果端点。
默认中文业务变量；按用户要求使用英文；既有模型保持原变量名。
非商业使用许可，作者传康KK（万能程序员）。""")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0].lower() in {"help", "-h", "--help"}:
        return _help()

    cmd = args[0].lower()
    rest = args[1:]

    if cmd == "doctor":
        return _doctor()
    if cmd in {"inspect", "audit", "visual", "preview"}:
        return _invoke_layout(args)
    if cmd == "build":
        return _invoke(model_builder.main, rest)
    if cmd == "feedback":
        from feedback_audit import main as feedback_main

        return _invoke(feedback_main, rest)
    if cmd in {"experiment", "convergence"}:
        return _invoke(experiments.main, args)
    if cmd in {"calibrate", "optimize"}:
        from optimization import main as optimization_main

        return _invoke(optimization_main, args)
    if cmd == "plot-data":
        from result_plotting import main as plot_main

        return _invoke(plot_main, rest)
    if cmd == "mcp":
        from mcp_server import main as mcp_main

        return _invoke(mcp_main, rest)
    if cmd == "layout":
        if not rest:
            print("ERROR: 用法: skill.cmd layout <model.mdl>", file=sys.stderr)
            return 2
        forwarded = args[:]
        if not _has_option(forwarded, "--output"):
            forwarded.extend(["--output", _default_output(rest[0], "_autolayout.mdl")])
        return _invoke_layout(forwarded)
    if cmd == "quick":
        return _quick(rest)
    if cmd == "examples":
        return _examples()
    if cmd in {"simulate", "graph"}:
        if not rest:
            print(f"ERROR: 用法: skill.cmd {cmd} <model.mdl>", file=sys.stderr)
            return 2
        forwarded = args[:]
        if not _has_option(forwarded, "--output"):
            suffix = "_sim.csv" if cmd == "simulate" else "_graph.png"
            forwarded.extend(["--output", _default_output(rest[0], suffix)])
        return _invoke_engine(forwarded)
    if cmd in {"compare", "units", "check", "fix"}:
        return _invoke_engine(args)
    if cmd == "academic":
        return _invoke(academic_gate.main, rest)
    if cmd == "auto":
        return _auto(rest)
    if cmd == "crosscheck":
        return _invoke(_crosscheck, rest)
    print(f"ERROR: 未知命令 {cmd}；使用 --help 查看用法", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
