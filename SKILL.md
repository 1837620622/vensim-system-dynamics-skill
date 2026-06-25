---
name: vensim-skill
description: Vensim 系统动力学建模与 .mdl 草图审计、保守自动布局与仿真辅助技能。用于课程作业、政策分析与管理研究的因果回路图(CLD)、存量流量图(SFD)、方程、单位校验、仿真、情景对比、论文图表，以及在保留方程与对象 ID 的前提下对已有草图做分层布局与基础弧线化整理。当用户需要用 Vensim 建立或整理系统动力学模型、审计或修复 .mdl 草图、做人口/供应链/政策等 SFD 作业、或在不打开 Vensim 的情况下对常见结构做仿真与对比图时使用。
license: MIT
compatibility: Claude Code, Codex CLI, Cursor, Windsurf, Gemini CLI, GitHub Copilot, Amazon Q Developer, Aider, Cline, Continue.dev, OpenCode, Roo Code, Goose, Zed, Trae, Antigravity, Amp, Qwen Code, Kiro, OpenClaw, JetBrains AI Assistant, VS Code, any shell-capable AI coding agent
metadata:
  author: 传康kk
  version: "1.0"
---

# Vensim 系统动力学通用技能

本技能定位为 **Vensim `.mdl` Sketch 审计、保守自动布局与系统动力学建模辅助工具**，不是 Vensim 的全功能替代。

## 兼容性（全球 IDE / AI 编程助手）

本技能是**纯 CLI 工具**（`skill.sh` + Python 标准库），不依赖 MCP 协议、不绑定特定 IDE 插件。任何能执行 shell 命令、能读取项目文件的 AI 编程助手均可使用，跨 macOS / Windows / Linux。已验证与以下全球主流工具兼容：

- **云订阅型**：Claude Code、Cursor、Windsurf、Codex CLI、Antigravity、Amp、Mistral Vibe
- **免费 / 云托管型**：Gemini CLI、GitHub Copilot（CLI 与 VS Code Chat）、Amazon Q Developer、Kiro、Qwen Code
- **开源 BYOK 型**：OpenCode、Aider、Cline、Continue.dev、Goose、Roo Code、OpenClaw、Zed、iFlow、Kimi Code CLI、BLACKBOX
- **IDE 内置 / 插件型**：VS Code、JetBrains 全系（IntelliJ/PyCharm 等）AI Assistant、Trae、通义灵码、CodeGeeX、Baidu Comate、Replit AI
- **自主 Agent 型**：Devin、OpenHands、Bolt.new、v0、Lovable

> 调研依据：Agent Skills 规范（agentskills.io / agensi.io）确认 `name` + `description` + 纯 Markdown 指令的技能可跨 Claude Code、Codex CLI、Cursor、Windsurf、Gemini CLI、Copilot 等全部主流运行时加载；本技能仅用标准 frontmatter 字段，平台特定字段均被安全忽略。

**跨平台入口**：macOS/Linux 用 `./skill.sh`，Windows 用 `skill.cmd`（cmd/PowerShell）或 Git Bash/WSL 下的 `./skill.sh`；两入口命令一致，自动检测 `python3`/`python`/`py`。Python 脚本用 `pathlib`+`shutil.which`+`subprocess`（不依赖 shell），读文件兼容 UTF-8 BOM 与 GB 编码，CSV 用 `newline=""` 避免 Windows 双换行。

## 能力边界

**已支持：**
- Sketch 对象 ID 与 Arrow 引用审计；
- 方程区语义审计：重复定义、未定义引用、未使用变量、缺失单位；
- 普通变量节点的分层（`dot`）或力导向（`neato`）布局；
- 普通信息箭头（≤1 控制点）的基础弧线化，多控制点箭头保持原样；
- 保留原方程区、保留对象数量、保留 Arrow 起止对象；
- 纯 Python 仿真引擎对常见结构（INTEG / LOOKUP / IF THEN ELSE / SMOOTH / DELAY）的 Euler 积分仿真与 CSV 导出；
- matplotlib 折线图与多场景对比图；
- 缺失单位补齐与断裂草图箭头修复。

**暂不承诺：**
- 全部 Vensim 函数的解析与仿真；
- 原生 Vensim 语法检查与完整单位量纲推导；
- 无交叉、无穿框的完全自动布线；
- Control Panel、敏感性分析和论文图表的自动生成。

**最终质量门槛仍需回到 Vensim**：布局后请在 Vensim 中执行 `Check Model` 与 `Units Check`。

## 工具入口

- 布局与审计：`vensim_system_dynamics/tools/vensim_autolayout.py`（`inspect` / `audit` / `layout`）
- 仿真与绘图：`vensim_system_dynamics/tools/vensim_engine.py`（`simulate` / `graph` / `compare` / `units` / `check` / `fix`）
- 便捷封装：`skill.sh`（根目录）
- 建模规范与流程：`vensim_system_dynamics/OPERATIONS_GUIDE.md`
- 示例模型：`vensim_system_dynamics/examples/`

## 快速使用

```bash
./skill.sh doctor                                 # 检查环境
./skill.sh audit examples/population_demo.mdl     # 审计草图与方程语义
./skill.sh quick examples/population_demo.mdl     # 一键 inspect + audit + layout
./skill.sh simulate examples/population_demo.mdl --var Population --var Births
./skill.sh graph examples/population_demo.mdl --var Population --output pop.png
./skill.sh check examples/population_demo.mdl     # 全面检查
./skill.sh fix broken.mdl --output fixed.mdl      # 自动修复
```

详细说明见 `README.md` 与 `vensim_system_dynamics/OPERATIONS_GUIDE.md`。
