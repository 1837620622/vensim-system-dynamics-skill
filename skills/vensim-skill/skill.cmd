@echo off
REM Vensim System Dynamics Skill - Windows CMD 便捷封装
REM 复杂参数处理放在 Python wrapper 中，避免 CMD 字符串拼接与 delayed expansion 破坏路径。
setlocal DisableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "WRAPPER=%SCRIPT_DIR%vensim_system_dynamics\tools\skill_cli.py"

REM 跨平台 Python 检测：Windows 常为 python，也可能为 python3 或 py
set "PY="
for %%c in (python3 python py) do (
  where %%c >nul 2>&1
  if not errorlevel 1 (
    set "PY=%%c"
    goto :found_py
  )
)
echo ERROR: 未找到 python3/python，请安装 Python 3.8+ 并加入 PATH >&2
exit /b 1

:found_py
%PY% "%WRAPPER%" %*
exit /b %ERRORLEVEL%
