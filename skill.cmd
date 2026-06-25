@echo off
REM Vensim System Dynamics Skill - Windows CMD 便捷封装
REM 跨平台：macOS/Linux 用 skill.sh，Windows 用 skill.cmd
REM 用法与 skill.sh 完全一致：skill.cmd doctor / audit / simulate / graph ...
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "TOOL=%SCRIPT_DIR%vensim_system_dynamics\tools\vensim_autolayout.py"
set "ENGINE=%SCRIPT_DIR%vensim_system_dynamics\tools\vensim_engine.py"
set "EXAMPLES_DIR=%SCRIPT_DIR%vensim_system_dynamics\examples"
set "TEMPLATES_DIR=%SCRIPT_DIR%vensim_system_dynamics\templates"

REM 跨平台 Python 检测：Windows 常为 python，也可能为 python3
set "PY="
for %%c in (python3 python py) do (
  where %%c >nul 2>&1 && ( set "PY=%%c" & goto :found_py )
)
echo ERROR: 未找到 python3/python，请安装 Python 3.8+ 并加入 PATH >&2
exit /b 1
:found_py

set "CMD=%~1"
if "%CMD%"=="" set "CMD=help"

if /i "%CMD%"=="doctor" (
  echo python: 
  %PY% --version
  where dot >nul 2>&1 && ( for /f "delims=" %%v in ('dot -V 2^>^&1') do echo graphviz: %%v ) || echo graphviz: 未安装
  %PY% -c "import matplotlib" 2>nul && echo matplotlib: 可用 || echo matplotlib: 未安装（绘图命令需要）
  goto :eof
)

if /i "%CMD%"=="inspect" (
  %PY% "%TOOL%" inspect %2
  goto :eof
)

if /i "%CMD%"=="audit" (
  %PY% "%TOOL%" audit %2
  goto :eof
)

if /i "%CMD%"=="layout" (
  where dot >nul 2>&1 || ( echo ERROR: 未找到 graphviz dot，请安装 Graphviz 并加入 PATH >&2 & exit /b 1 )
  %PY% "%TOOL%" layout %*
  goto :eof
)

if /i "%CMD%"=="quick" (
  where dot >nul 2>&1 || ( echo ERROR: 未找到 graphviz dot，请安装 Graphviz 并加入 PATH >&2 & exit /b 1 )
  set "MODEL=%~2"
  set "OUT=!MODEL:.mdl=_autolayout.mdl!"
  echo === inspect ===
  %PY% "%TOOL%" inspect "!MODEL!"
  echo === audit ===
  %PY% "%TOOL%" audit "!MODEL!"
  echo === layout ===
  %PY% "%TOOL%" layout "!MODEL!" --output "!OUT!" --config "%TEMPLATES_DIR%\layout_config_sfd.json" --engine dot --route-information-arrows
  echo 完成: !OUT!  (请在 Vensim 打开并运行 Check Model 与 Units Check)
  goto :eof
)

if /i "%CMD%"=="examples" (
  for %%f in ("%EXAMPLES_DIR%\*.mdl") do (
    echo ========== %%~nxf ==========
    %PY% "%TOOL%" audit "%%f" 2>&1 | findstr /n "." | findstr "^[0-9]*:" >nul && %PY% "%TOOL%" audit "%%f" 2>&1 | more +0
  )
  goto :eof
)

if /i "%CMD%"=="simulate" (
  %PY% "%ENGINE%" simulate %*
  goto :eof
)

if /i "%CMD%"=="graph" (
  %PY% "%ENGINE%" graph %*
  goto :eof
)

if /i "%CMD%"=="compare" (
  %PY% "%ENGINE%" compare %*
  goto :eof
)

if /i "%CMD%"=="units" (
  %PY% "%ENGINE%" units %2
  goto :eof
)

if /i "%CMD%"=="check" (
  %PY% "%ENGINE%" check %2
  goto :eof
)

if /i "%CMD%"=="fix" (
  %PY% "%ENGINE%" fix %*
  goto :eof
)

if /i "%CMD%"=="help" goto :help
if /i "%CMD%"=="-h" goto :help
if /i "%CMD%"=="--help" goto :help
goto :help

:help
echo Vensim System Dynamics Skill (Windows)
echo 用法:
echo   草图与布局:
echo     skill.cmd inspect  ^<model.mdl^>                 列出草图对象与箭头
echo     skill.cmd audit    ^<model.mdl^>                 审计箭头对象引用
echo     skill.cmd layout   ^<model.mdl^> [选项]          保守自动排版
echo         选项: --output out.mdl --config cfg.json --engine dot^|neato^|fdp^|sfdp --route
echo     skill.cmd quick    ^<model.mdl^>                 一键 inspect+audit+layout
echo     skill.cmd examples                               审计全部示例
echo   仿真与分析 (不依赖 Vensim):
echo     skill.cmd simulate ^<model.mdl^> [--output out.csv] [--var V]   纯 Python 仿真导出 CSV
echo     skill.cmd graph    ^<model.mdl^> --var V --output out.png       折线图
echo     skill.cmd compare  ^<base.mdl^> --scenario s.mdl --var V --output out.png  多场景对比图
echo     skill.cmd units    ^<model.mdl^>                 单位量纲校验
echo     skill.cmd check    ^<model.mdl^>                 全面检查
echo     skill.cmd fix      ^<model.mdl^> --output f.mdl  自动修复
echo   环境:
echo     skill.cmd doctor                                 检查 python 与 graphviz
echo.
echo macOS/Linux 用户请使用 ./skill.sh
goto :eof
