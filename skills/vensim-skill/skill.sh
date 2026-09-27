#!/usr/bin/env bash
# 两个平台共用 Python 参数解析，避免功能与错误处理分叉。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for interpreter in python3 python py; do
  if command -v "$interpreter" >/dev/null 2>&1 && "$interpreter" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    exec "$interpreter" "$SCRIPT_DIR/scripts/skill_cli.py" "$@"
  fi
done
printf '%s\n' 'ERROR: 需要 Python 3.10+，请将解释器加入 PATH。' >&2
exit 2
