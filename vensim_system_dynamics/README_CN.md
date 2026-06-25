# Vensim 系统动力学通用 Skill

可复用的"代理指令 + 工具脚本"包，用于让 AI 辅助完成 Vensim 系统动力学课程作业、政策模拟与管理研究建模，并在保留方程与对象 ID 的前提下把已建模的草图自动排版为论文级 SFD/CLD。

## 包含内容

- `OPERATIONS_GUIDE.md`：通用建模指令、质量标准、交付工作流、草图格式说明；
- `tools/vensim_autolayout.py`：读取 `.mdl` 草图，列出对象、审计引用、对普通辅助变量做 Graphviz 布局、为信息箭头生成单控制点圆弧；
- `tools/vensim_engine.py`：纯 Python 仿真引擎（不依赖 Vensim），支持方程解析、Euler 积分仿真、CSV 导出、matplotlib 折线图与多场景对比图、单位量纲校验、模型检查与自动修复；
- `templates/model_spec_template.json`：建模前语义规范模板；
- `templates/layout_config_sfd.json`、`layout_config_cld.json`：自动排版配置样例；
- `examples/population_demo.mdl`：最小示例模型；
- `docs/REFERENCES.md`：实现依据与限制。

## 关键结论

Vensim 内置的是"对齐、等间距、尺寸统一、手动调曲线"，**不是**全图自动布局。普通 Arrow 加一个中间控制点即形成平滑圆弧；Spline Arrow 需在 Vensim 中以 `Command/Ctrl + Arrow` 创建。本包用 Graphviz 计算辅助变量位置，再保守回写 `.mdl` 草图坐标与信息箭头控制点；不新建模型结构，不改方程，不改箭头 from/to。

## 兼容性（全球 IDE / AI 编程助手）

本技能是**纯 CLI 工具**（`skill.sh` + Python 标准库），不依赖 MCP 协议、不绑定特定 IDE 插件。任何能执行 shell 命令、能读取项目文件的 AI 编程助手均可使用，跨 macOS / Windows / Linux。已验证与以下全球主流工具兼容：

- **云订阅型**：Claude Code、Cursor、Windsurf、Codex CLI、Antigravity、Amp、Mistral Vibe
- **免费 / 云托管型**：Gemini CLI、GitHub Copilot（CLI 与 VS Code Chat）、Amazon Q Developer、Kiro、Qwen Code
- **开源 BYOK 型**：OpenCode、Aider、Cline、Continue.dev、Goose、Roo Code、OpenClaw、Zed、iFlow、Kimi Code CLI、BLACKBOX
- **IDE 内置 / 插件型**：VS Code、JetBrains 全系（IntelliJ/PyCharm 等）AI Assistant、Trae、通义灵码、CodeGeeX、Baidu Comate、Replit AI
- **自主 Agent 型**：Devin、OpenHands、Bolt.new、v0、Lovable

> 依据 Agent Skills 规范（agentskills.io / agensi.io），仅用 `name` + `description` + 纯 Markdown 指令的技能可跨全部主流运行时加载；本技能仅用标准 frontmatter 字段，平台特定字段被不支持的安全忽略。

## 安装

```bash
# macOS
brew install graphviz
which dot && dot -V
```

Python 脚本只用标准库，无需 `pip install`。

## 最快使用方式

```bash
# 便捷封装（推荐）：一键 inspect + audit + layout
chmod +x skill.sh
./skill.sh doctor                                   # 检查环境
./skill.sh quick /path/to/your_model.mdl            # 一键全流程
./skill.sh layout /path/to/your_model.mdl --route   # 单步自动排版

# 或直接调用 Python 脚本
# 1. 查看模型对象 ID、位置、形状、箭头 from/to、控制点
python tools/vensim_autolayout.py inspect /path/to/your_model.mdl

# 2. 复制 SFD 配置，填入要锁定的库存、流率名
cp templates/layout_config_sfd.json my_layout.json

# 3. 生成自动排版模型（自动建 .backup.mdl 与 .layout_report.json）
python tools/vensim_autolayout.py layout /path/to/your_model.mdl \
  --output /path/to/your_model_autolayout.mdl \
  --config my_layout.json \
  --engine dot \
  --route-information-arrows

# 4. 审计输出
python tools/vensim_autolayout.py audit /path/to/your_model_autolayout.mdl
```

在 Vensim 中打开 `your_model_autolayout.mdl`：先看图，再 `Model > Check Model`，再 `Model > Units Check`，手工微调少数交叉关系后保存为最终版本。

## 仿真 / 绘图 / 校验 / 修复（不依赖 Vensim）

`vensim_engine.py` 提供纯 Python 仿真引擎，无需打开 Vensim 即可完成仿真、导出对比图、单位校验与模型检查修复：

```bash
# 仿真导出 CSV
./skill.sh simulate examples/population_demo.mdl --var Population --var Births --var Deaths

# 折线图 PNG（需 matplotlib）
./skill.sh graph examples/population_demo.mdl --var Population --var Deaths \
       --output pop.png --title "种群动态"

# 多场景对比图（净利润、植被盖度、耦合度等任意变量）
./skill.sh compare examples/population_demo.mdl \
       --scenario scenario_low.mdl --scenario scenario_high.mdl \
       --var Population --var "Crowding Effect" --output compare.png

# 单位量纲校验
./skill.sh units examples/population_demo.mdl

# 全面检查：未定义变量 / 缺失单位 / 循环依赖 / 断裂草图引用
./skill.sh check examples/population_demo.mdl

# 自动修复缺失单位、断裂草图箭头
./skill.sh fix broken_model.mdl --output fixed_model.mdl
```

支持函数：`INTEG`、`SMOOTH`、`SMOOTH3`、`DELAY1`、`DELAY3`、`DELAY FIXED`、`IF THEN ELSE`、`WITH LOOKUP`、`LOOKUP`、`ABS`、`SQRT`、`EXP`、`LN`、`MIN`、`MAX`、`MODULO`。

## 必须先在 Vensim 中做好的部分

- 库存、流率、阀门、云、管道；
- 方程、单位、初值、模拟起止时间；
- 变量名与所有信息因果关系。

脚本不新建模型结构，不改变方程。它不是"让 AI 代替建模"，而是把已正确建模的复杂图整理得更易读。

## 适合的任务

- 汽车销售、人口、供应链韧性、政策执行、光伏治沙、城市交通等 SFD；
- 区域运输、公共治理、生态经济等 CLD；
- 作业要求的 Control Panel、政策情景、敏感性分析；
- 把原始 Vensim 图整理为论文可用图。

## 不适合的情况

- 模型尚未确认时直接批量生成 `.mdl`；
- 希望一个按钮自动设计理论机制或自动给参数；
- 多个库存—流率骨架已乱接、阀门与标签脱离；
- 不愿在 Vensim 中打开验证。

## 作者

**传康kk**（chuankangkk）

- 微信：`1837620622`
- GitHub：[@1837620622](https://github.com/1837620622)

## 商务合作 / 问题反馈

欢迎通过以下方式联系我，合作方向包括但不限于：

- **商务合作**：系统动力学建模咨询、Vensim 模型搭建与排版、政策仿真与情景分析、论文图表与交付物定制、企业内训与课程开发、工具二次开发与私有部署。
- **问题反馈**：使用中遇到 bug、解析错误、布局异常、仿真 nodata 等，请附 `.mdl` 文件与复现命令，便于定位。
- **功能建议**：希望支持的新函数、新结构、新输出格式或工作流改进。
- **学术合作**：研究项目中的模型构建、仿真验证、论文图表协作。
- **其他合作**：技术交流、开源贡献、社区共建。

**联系方式**：

| 用途 | 渠道 |
|---|---|
| 商务合作 / 项目委托 | 微信 `1837620622`（备注「Vensim 技能合作」） |
| 问题反馈 / Bug 报告 | GitHub [Issues](https://github.com/1837620622/vensim-system-dynamics-skill/issues) |
| 功能建议 / 技术交流 | 微信 `1837620622` 或 GitHub Issues |
| 开源贡献 | GitHub [Pull Requests](https://github.com/1837620622/vensim-system-dynamics-skill/pulls) |

> 提交 Issue 或 PR 时请说明：使用场景、操作系统、Python 版本、复现命令、`.mdl` 文件（可脱敏）。商务合作请直接加微信，备注来意以便快速响应。
