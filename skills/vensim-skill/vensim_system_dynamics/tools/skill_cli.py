#!/usr/bin/env python3
"""跨平台 skill 包装入口，主要供 Windows skill.cmd 使用。"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Callable, List

TOOLS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parents[1]
EXAMPLES_DIR = ROOT_DIR / "vensim_system_dynamics" / "examples"
TEMPLATES_DIR = ROOT_DIR / "vensim_system_dynamics" / "templates"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import vensim_autolayout  # noqa: E402
import vensim_engine  # noqa: E402


def _default_output(model: str, suffix: str) -> str:
    path = Path(model)
    return str(path.with_name(f"{path.stem}{suffix}"))


def _has_option(args: List[str], option: str) -> bool:
    return any(arg.lower() == option for arg in args)


def _invoke(module_main: Callable[[], int], argv: List[str]) -> int:
    old_argv = sys.argv[:]
    sys.argv = [old_argv[0], *argv]
    try:
        return int(module_main() or 0)
    except SystemExit as exc:
        return int(exc.code or 0)
    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        sys.argv = old_argv


def _invoke_layout(argv: List[str]) -> int:
    return _invoke(vensim_autolayout.main, argv)


def _invoke_engine(argv: List[str]) -> int:
    return _invoke(vensim_engine.main, argv)


def _doctor() -> int:
    print(f"python: {sys.version.split()[0]}")
    dot = shutil.which("dot")
    print(f"graphviz: {dot if dot else '未安装'}")
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("matplotlib: 未安装（绘图命令需要）")
    else:
        print("matplotlib: 可用")
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
        "--config", str(TEMPLATES_DIR / "layout_config_sfd.json"),
        "--engine", engine,
        "--route-information-arrows",
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
    print(
        """Vensim System Dynamics Skill (Windows)
用法:
  草图与布局:
    skill.cmd inspect  <model.mdl>                 列出草图对象与箭头
    skill.cmd audit    <model.mdl>                 审计箭头对象引用
    skill.cmd layout   <model.mdl> [选项]          保守自动排版
        选项: --output out.mdl --config cfg.json --engine dot|neato|fdp|sfdp --route
    skill.cmd quick    <model.mdl>                 一键 inspect+audit+layout
    skill.cmd examples                             审计全部示例
  仿真与分析 (不依赖 Vensim):
    skill.cmd simulate <model.mdl> [--output out.csv] [--var V] [--keep-going]
    skill.cmd graph    <model.mdl> --var V [--output out.png] [--keep-going]
    skill.cmd compare  <base.mdl> --scenario s.mdl --var V --output out.png
    skill.cmd auto     <model.mdl> [--var V] [--keep-going]  一键 check+simulate+graph
    skill.cmd units    <model.mdl>                 单位缺失预检
    skill.cmd check    <model.mdl>                 全面检查
    skill.cmd fix      <model.mdl> --output f.mdl  自动修复
  环境:
    skill.cmd doctor                               检查 python 与 graphviz

macOS/Linux 用户请使用 ./skill.sh"""
    )
    return 0


def main(argv: List[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0].lower() in {"help", "-h", "--help"}:
        return _help()

    cmd = args[0].lower()
    rest = args[1:]

    if cmd == "doctor":
        return _doctor()
    if cmd in {"inspect", "audit"}:
        return _invoke_layout(args)
    if cmd == "layout":
        if not rest:
            print("ERROR: 用法: skill.cmd layout <model.mdl>", file=sys.stderr)
            return 2
        forwarded = args[:]
        if not _has_option(forwarded, "--output"):
            forwarded.extend(["--output", _default_output(rest[0], "_autolayout.mdl")])
        if not _has_option(forwarded, "--config"):
            forwarded.extend(["--config", str(TEMPLATES_DIR / "layout_config_sfd.json")])
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
    if cmd == "auto":
        return _auto(rest)
    return _help()


if __name__ == "__main__":
    raise SystemExit(main())
