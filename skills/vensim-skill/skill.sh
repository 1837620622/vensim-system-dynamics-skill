#!/usr/bin/env bash
# Vensim System Dynamics Skill 便捷封装
# 用法:
#   ./skill.sh inspect  <model.mdl>
#   ./skill.sh audit    <model.mdl>
#   ./skill.sh layout   <model.mdl> [--output out.mdl] [--config cfg.json] [--engine dot] [--route]
#   ./skill.sh quick     <model.mdl> [--engine dot]   一键 inspect+audit+layout(自动锁库存状变量)
#   ./skill.sh examples                       对 examples/ 下所有 .mdl 跑 audit
#   ./skill.sh doctor                         检查 python3 与 graphviz 是否可用
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$SCRIPT_DIR/vensim_system_dynamics/tools/vensim_autolayout.py"
ENGINE="$SCRIPT_DIR/vensim_system_dynamics/tools/vensim_engine.py"
EXAMPLES_DIR="$SCRIPT_DIR/vensim_system_dynamics/examples"
TEMPLATES_DIR="$SCRIPT_DIR/vensim_system_dynamics/templates"

die() { echo "ERROR: $*" >&2; exit 1; }

# 跨平台 Python 检测：macOS/Linux 通常为 python3，Windows 常为 python
PY=""
for c in python3 python py; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
need_py() { [ -n "$PY" ] || die "未找到 python3/python，请安装 Python 3.8+ 并加入 PATH"; }
need_dot() { command -v dot >/dev/null 2>&1 || die "未找到 graphviz dot，请安装 Graphviz 并加入 PATH（macOS: brew install graphviz；Windows: 官网安装后加 bin 到 PATH）"; }

cmd="${1:-help}"; shift || true

case "$cmd" in
  doctor)
    need_py
    echo "python: $($PY --version)"
    if command -v dot >/dev/null 2>&1; then echo "graphviz: $(dot -V 2>&1)"; else echo "graphviz: 未安装"; fi
    if $PY -c "import matplotlib" 2>/dev/null; then echo "matplotlib: 可用"; else echo "matplotlib: 未安装（绘图命令需要）"; fi
    ;;
  inspect)
    [ $# -ge 1 ] || die "用法: $0 inspect <model.mdl>"
    need_py; "$PY" "$TOOL" inspect "$1"
    ;;
  audit)
    [ $# -ge 1 ] || die "用法: $0 audit <model.mdl>"
    need_py; "$PY" "$TOOL" audit "$1"
    ;;
  layout)
    [ $# -ge 1 ] || die "用法: $0 layout <model.mdl> [--output out.mdl] [--config cfg.json] [--engine dot] [--route]"
    need_py; need_dot
    model="$1"; shift
    out=""; cfg=""; engine="dot"; route=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --output) out="$2"; shift 2;;
        --config) cfg="$2"; shift 2;;
        --engine) engine="$2"; shift 2;;
        --route) route="--route-information-arrows"; shift;;
        *) die "未知参数: $1";;
      esac
    done
    [ -n "$out" ] || out="${model%.mdl}_autolayout.mdl"
    [ -n "$cfg" ] || cfg="$TEMPLATES_DIR/layout_config_sfd.json"
    "$PY" "$TOOL" layout "$model" --output "$out" --config "$cfg" --engine "$engine" $route
    echo "完成: $out"
    ;;
  quick)
    [ $# -ge 1 ] || die "用法: $0 quick <model.mdl> [--engine dot]"
    need_py; need_dot
    model="$1"; shift
    engine="dot"
    [ "${1:-}" = "--engine" ] && engine="$2"
    echo "=== inspect ==="; "$PY" "$TOOL" inspect "$model"
    echo "=== audit ==="; "$PY" "$TOOL" audit "$model"
    out="${model%.mdl}_autolayout.mdl"
    echo "=== layout ==="; "$PY" "$TOOL" layout "$model" --output "$out" --config "$TEMPLATES_DIR/layout_config_sfd.json" --engine "$engine" --route-information-arrows
    echo "完成: $out  (请在 Vensim 打开并运行 Check Model 与 Units Check)"
    ;;
  examples)
    need_py
    for f in "$EXAMPLES_DIR"/*.mdl; do
      echo "========== $(basename "$f") =========="
      "$PY" "$TOOL" audit "$f" | tail -3
    done
    ;;
  simulate)
    [ $# -ge 1 ] || die "用法: $0 simulate <model.mdl> [--output out.csv] [--var V1 --var V2 ...] [--plot out.png]"
    need_py
    model="$1"; shift
    cmd_args=("$model")
    # 检查是否已带 --output，未带则自动补默认输出路径
    has_out=0
    for a in "$@"; do [ "$a" = "--output" ] && has_out=1; done
    if [ "$has_out" -eq 0 ]; then cmd_args+=(--output "${model%.mdl}_sim.csv"); fi
    cmd_args+=("$@")
    "$PY" "$ENGINE" simulate "${cmd_args[@]}"
    ;;
  graph)
    [ $# -ge 1 ] || die "用法: $0 graph <model.mdl> [--var V1 --var V2] --output out.png [--title T]  (缺省 --var 画全部变量)"
    need_py
    model="$1"; shift
    cmd_args=("$model")
    has_out=0
    for a in "$@"; do [ "$a" = "--output" ] && has_out=1; done
    if [ "$has_out" -eq 0 ]; then cmd_args+=(--output "${model%.mdl}_graph.png"); fi
    cmd_args+=("$@")
    "$PY" "$ENGINE" graph "${cmd_args[@]}"
    ;;
  compare)
    [ $# -ge 1 ] || die "用法: $0 compare <base.mdl> --scenario s1.mdl --var V --output out.png"
    need_py
    "$PY" "$ENGINE" compare "$@"
    ;;
  auto)
    [ $# -ge 1 ] || die "用法: $0 auto <model.mdl> [--var V1 --var V2 ...] [--keep-going]"
    need_py
    model="$1"; shift
    sim_out="${model%.mdl}_sim.csv"
    graph_out="${model%.mdl}_graph.png"
    echo "=== check ==="; "$PY" "$ENGINE" check "$model"
    echo "=== simulate ==="; "$PY" "$ENGINE" simulate "$model" --output "$sim_out" "$@"
    echo "=== graph ==="; "$PY" "$ENGINE" graph "$model" --output "$graph_out" "$@"
    echo "完成: $sim_out"
    echo "完成: $graph_out"
    ;;
  units)
    [ $# -ge 1 ] || die "用法: $0 units <model.mdl>"
    need_py; "$PY" "$ENGINE" units "$1"
    ;;
  check)
    [ $# -ge 1 ] || die "用法: $0 check <model.mdl>"
    need_py; "$PY" "$ENGINE" check "$1"
    ;;
  fix)
    [ $# -ge 1 ] || die "用法: $0 fix <model.mdl> --output fixed.mdl"
    need_py; "$PY" "$ENGINE" fix "$@"
    ;;
  help|-h|--help|*)
    cat <<EOF
Vensim System Dynamics Skill
用法:
  草图与布局:
    $0 inspect  <model.mdl>                 列出草图对象与箭头
    $0 audit    <model.mdl>                 审计箭头对象引用
    $0 layout   <model.mdl> [选项]          保守自动排版
        选项: --output out.mdl --config cfg.json --engine dot|neato|fdp|sfdp --route
    $0 quick    <model.mdl> [--engine dot]  一键 inspect+audit+layout
    $0 examples                               审计全部示例
  仿真与分析 (不依赖 Vensim):
    $0 simulate <model.mdl> [--output out.csv] [--var V] [--keep-going]  纯 Python 仿真导出 CSV
    $0 graph    <model.mdl> --var V [--var V2] [--output out.png] [--title T] [--keep-going]  折线图
    $0 compare  <base.mdl> --scenario s.mdl --var V --output out.png [--keep-going]  多场景对比图
    $0 auto     <model.mdl> [--var V] [--keep-going]  一键 check+simulate+graph
    $0 units    <model.mdl>                 单位缺失预检
    $0 check    <model.mdl>                 全面检查(未定义/缺单位/循环/断裂引用)
    $0 fix      <model.mdl> --output f.mdl  自动修复缺失单位与断裂引用
  环境:
    $0 doctor                                 检查 python3 与 graphviz
EOF
    ;;
esac
