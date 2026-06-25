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

need_py() { command -v python3 >/dev/null 2>&1 || die "未找到 python3"; }
need_dot() { command -v dot >/dev/null 2>&1 || die "未找到 graphviz dot，请 brew install graphviz 或加入 PATH"; }

cmd="${1:-help}"; shift || true

case "$cmd" in
  doctor)
    need_py
    echo "python3: $(python3 --version)"
    if command -v dot >/dev/null 2>&1; then echo "graphviz: $(dot -V 2>&1)"; else echo "graphviz: 未安装"; fi
    ;;
  inspect)
    [ $# -ge 1 ] || die "用法: $0 inspect <model.mdl>"
    need_py; python3 "$TOOL" inspect "$1"
    ;;
  audit)
    [ $# -ge 1 ] || die "用法: $0 audit <model.mdl>"
    need_py; python3 "$TOOL" audit "$1"
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
    python3 "$TOOL" layout "$model" --output "$out" --config "$cfg" --engine "$engine" $route
    echo "完成: $out"
    ;;
  quick)
    [ $# -ge 1 ] || die "用法: $0 quick <model.mdl> [--engine dot]"
    need_py; need_dot
    model="$1"; shift
    engine="dot"
    [ "${1:-}" = "--engine" ] && engine="$2"
    echo "=== inspect ==="; python3 "$TOOL" inspect "$model"
    echo "=== audit ==="; python3 "$TOOL" audit "$model"
    out="${model%.mdl}_autolayout.mdl"
    echo "=== layout ==="; python3 "$TOOL" layout "$model" --output "$out" --config "$TEMPLATES_DIR/layout_config_sfd.json" --engine "$engine" --route-information-arrows
    echo "完成: $out  (请在 Vensim 打开并运行 Check Model 与 Units Check)"
    ;;
  examples)
    need_py
    for f in "$EXAMPLES_DIR"/*.mdl; do
      echo "========== $(basename "$f") =========="
      python3 "$TOOL" audit "$f" | tail -3
    done
    ;;
  simulate)
    [ $# -ge 1 ] || die "用法: $0 simulate <model.mdl> [--output out.csv] [--var V1 --var V2 ...]"
    need_py
    model="$1"; shift
    out=""; vars=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --output) out="$2"; shift 2;;
        --var) vars="$vars --var $2"; shift 2;;
        *) die "未知参数: $1";;
      esac
    done
    [ -n "$out" ] || out="${model%.mdl}_sim.csv"
    python3 "$ENGINE" simulate "$model" --output "$out" $vars
    ;;
  graph)
    [ $# -ge 1 ] || die "用法: $0 graph <model.mdl> --var V1 [--var V2] --output out.png [--title T]"
    need_py
    python3 "$ENGINE" graph "$@"
    ;;
  compare)
    [ $# -ge 1 ] || die "用法: $0 compare <base.mdl> --scenario s1.mdl --var V --output out.png"
    need_py
    python3 "$ENGINE" compare "$@"
    ;;
  units)
    [ $# -ge 1 ] || die "用法: $0 units <model.mdl>"
    need_py; python3 "$ENGINE" units "$1"
    ;;
  check)
    [ $# -ge 1 ] || die "用法: $0 check <model.mdl>"
    need_py; python3 "$ENGINE" check "$1"
    ;;
  fix)
    [ $# -ge 1 ] || die "用法: $0 fix <model.mdl> --output fixed.mdl"
    need_py; python3 "$ENGINE" fix "$@"
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
    $0 simulate <model.mdl> [--output out.csv] [--var V]   纯 Python 仿真导出 CSV
    $0 graph    <model.mdl> --var V [--var V2] --output out.png [--title T]  折线图
    $0 compare  <base.mdl> --scenario s.mdl --var V --output out.png         多场景对比图
    $0 units    <model.mdl>                 单位量纲校验
    $0 check    <model.mdl>                 全面检查(未定义/缺单位/循环/断裂引用)
    $0 fix      <model.mdl> --output f.mdl  自动修复缺失单位与断裂引用
  环境:
    $0 doctor                                 检查 python3 与 graphviz
EOF
    ;;
esac
