#!/usr/bin/env python3
"""跨平台 skill 包装入口，主要供 Windows skill.cmd 使用。"""
from __future__ import annotations

import json
import importlib.metadata
import importlib.util
import os
import plistlib
import shutil
import sys
from pathlib import Path
from typing import Callable, List

if sys.version_info < (3, 10):
    raise SystemExit("ERROR: Vensim skill 需要 Python 3.10 或更新版本")
sys.dont_write_bytecode = True

TOOLS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parent
EXAMPLES_DIR = ROOT_DIR / "assets" / "examples"
TEMPLATES_DIR = ROOT_DIR / "assets" / "templates"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import vensim_autolayout  # noqa: E402
import vensim_engine  # noqa: E402
import academic_gate  # noqa: E402
import model_builder  # noqa: E402
import experiments  # noqa: E402


def _default_output(model: str, suffix: str) -> str:
    path = Path(model)
    return str(path.with_name(f"{path.stem}{suffix}"))


def _has_option(args: List[str], option: str) -> bool:
    return any(arg.lower().split("=", 1)[0] == option for arg in args)


def _invoke(module_main: Callable[[], int], argv: List[str]) -> int:
    old_argv = sys.argv[:]
    sys.argv = [old_argv[0], *argv]
    try:
        return int(module_main() or 0)
    except SystemExit as exc:
        return int(exc.code or 0)
    except (ValueError, RuntimeError, OSError, ImportError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        sys.argv = old_argv


def _invoke_layout(argv: List[str]) -> int:
    return _invoke(vensim_autolayout.main, argv)


def _invoke_engine(argv: List[str]) -> int:
    return _invoke(vensim_engine.main, argv)


def doctor_report():
    packages = {}
    for name in ("matplotlib", "pysd", "mcp"):
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
                    native.append({"path": str(app), "bundle_version_raw": metadata.get("CFBundleShortVersionString"),
                                   "version_note": "原始 bundle 字段；显示版本请在原生 About 界面确认"})
    elif os.name == "nt":
        for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if root:
                native.extend({"path": str(path)} for path in Path(root).glob("Vensim*/vensim*.exe"))
    fonts = {"status": "需要可选 matplotlib 才能检查 Python 绘图字体"}
    if packages["matplotlib"]:
        from result_plotting import font_settings
        try:
            fonts = {"status": "基础中文与符号字形可用，实际图件导出时继续逐字检查",
                     "families": font_settings("中文变量存量流率影子引用时间结果−–±%0123456789")["font.family"]}
        except (RuntimeError, ValueError) as exc:
            fonts = {"status": "字体需配置", "detail": str(exc)}
    return {"python": sys.version.split()[0], "executable": sys.executable,
            "graphviz": {engine: shutil.which(engine) for engine in ("dot", "neato", "fdp", "sfdp")},
            "optional_packages": packages, "native_vensim": native,
            "plot_fonts": fonts,
            "official_dss_mcp": "需核对官方组件、许可与实际工具列表，未自动连接"}


def _doctor() -> int:
    print(json.dumps(doctor_report(), ensure_ascii=False, indent=2))
    return 0


def _quick(args: List[str]) -> int:
    if not args:
        print("ERROR: 用法: skill.cmd quick <model.mdl>", file=sys.stderr)
        return 2
    model = args[0]
    engine = "dot"
    if len(args) >= 3 and args[1].lower() == "--engine":
        engine = args[2]
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
    rc = _invoke_layout([
        "layout", model,
        "--output", out,
        "--engine", engine,
        "--route-information-arrows",
        "--preview", _default_output(model, "_layout.html"),
    ])
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


def _auto(args: List[str]) -> int:
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


def _help() -> int:
    print("""Vensim System Dynamics Skill
用法: ./skill.sh <命令> [参数]  /  skill.cmd <命令> [参数]

  doctor                           检查 Python、Graphviz、可选包、原生 Vensim
  build spec.json --output m.mdl    从明确的存量、流向和方程生成 MDL
  inspect model.mdl                列出对象与箭头
  audit model.mdl                  结构与语义预检
  check model.mdl                  方程、初值和时间设置预检
  layout model.mdl --output out.mdl [--mode auto|preserve|refine|graphviz]
         [--engine dot|neato] [--style monochrome|native-blue|preserve]
         [--config layout.json] [--preview review.html]
  preview model.mdl --output sketch.html [--compare-with before.mdl]
  visual model.mdl [--strict] [--output geometry.json]
  quick model.mdl                  检查、布局和前后预览
  simulate model.mdl --var 变量 [--output result.csv] [--plot result.png]
  graph model.mdl --var 变量 --output result.svg
  plot-data series.csv --output result.png --time-unit 单位
         绘图选项: --dpi 600 --formats png,pdf,svg（默认无标题，每个变量单独出图）
  compare base.mdl --scenario policy.mdl --var 变量 --output comparison.png
         仿真共用选项: --backend builtin|pysd --set '参数=数值'
                       --time-step 步长 --final-time 终点 --saveper 保存间隔
  experiment model.mdl --spec experiment.json --output-dir results [--plot out.png]
  convergence model.mdl --var 变量 --output convergence.json [--tolerance 0.01]
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


def main(argv: List[str] | None = None) -> int:
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
    if cmd in {"experiment", "convergence"}:
        return _invoke(experiments.main, args)
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
    print(f"ERROR: 未知命令 {cmd}；使用 --help 查看用法", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
