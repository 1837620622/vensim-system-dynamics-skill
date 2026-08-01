---
name: vensim-skill
description: "Use when the user needs Vensim/system-dynamics CLD/SFD modeling, .mdl sketch audit/repair/layout, simulation, nodata diagnosis, scenario comparison, or charts for coursework, policy, management, population, or supply-chain models."
license: MIT
---

# Vensim 系统动力学通用技能

本技能定位为 **Vensim `.mdl` Sketch 审计、保守自动布局、仿真诊断与论文图表辅助工具**。目标是形成“文献与边界 → 结构与方程 → 检查 → 修复 → 仿真 → 出图 → 行为验证 → 回到 Vensim 复核”的闭环流程。技能不能替代研究者确定系统边界、反馈假设、参数依据或因果极性。

中文课程论文、政策仿真和管理研究模型默认使用**中文业务变量**。Vensim 控制变量和函数名保持原生英文；业务变量、图题、坐标轴、图例、CSV 表头、变量表和说明文档优先使用中文。留学生作业或用户明确要求英文时才切换英文变量。

## 兼容性（全球 IDE / AI 编程助手）

本技能是**纯 CLI 工具**（`skill.sh` + Python 标准库），不依赖 MCP 协议、不绑定特定 IDE 插件。任何能执行 shell 命令、能读取项目文件的 AI 编程助手均可使用，跨 macOS / Windows / Linux。设计目标兼容以下主流工具：

- **云订阅型**：Claude Code、Cursor、Windsurf、Codex CLI、Antigravity、Amp、Mistral Vibe
- **免费 / 云托管型**：Gemini CLI、GitHub Copilot（CLI 与 VS Code Chat）、Amazon Q Developer、Kiro、Qwen Code
- **开源 BYOK 型**：OpenCode、Aider、Cline、Continue.dev、Goose、Roo Code、OpenClaw、Zed、iFlow、Kimi Code CLI、BLACKBOX
- **IDE 内置 / 插件型**：VS Code、JetBrains 全系（IntelliJ/PyCharm 等）AI Assistant、Trae、通义灵码、CodeGeeX、Baidu Comate、Replit AI
- **自主 Agent 型**：Devin、OpenHands、Bolt.new、v0、Lovable

> 兼容性说明放在正文中，frontmatter 仅保留技能发布与加载需要的标准字段。

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
- 缺失单位补齐与断裂草图箭头修复；
- `academic` 研究设计门禁：检查文献目录、研究问题/边界说明、存量初值、单位、历史期内生性和耦合输出；
- `visual` 图面门禁：检查灰色影子设置、变量框重叠、直接箭头度数、信息线交叉、弧线控制点、实线与深蓝色样式；
- nodata 诊断：默认严格模式下遇到不支持函数、变量缺失或求值失败会中止并指出根因；明确传入 `--keep-going` 时才按兼容模式继续输出。

**暂不承诺：**
- 全部 Vensim 函数的解析与仿真；
- 原生 Vensim 语法检查与完整单位量纲推导；
- 无交叉、无穿框的完全自动布线；
- Control Panel、敏感性分析和论文图表的自动生成。

**最终质量门槛仍需回到 Vensim**：布局后请在 Vensim 中执行 `Check Model` 与 `Units Check`。

## 学术建模门禁（先研究，后建模）

任何论文、学位论文或政策研究模型必须先完成以下材料，再允许脚本改方程或重画图：

1. **参考文献与边界**：先检索并阅读领域论文、系统动力学方法论文和数据来源，记录研究问题、系统边界、时间边界、外生输入、内生反馈与不纳入机制。不能依据变量名或一张截图凭空补方程。
2. **动态假设**：列出状态变量、流率、辅助变量、关键反馈回路、预期行为模式和政策杠杆；每条因果边至少有数据、文献、单位关系或明确结构假设支持。
3. **存量—流率优先**：先建立可运行的 Vensim 存量—流率骨架，再添加辅助变量和信息箭头。每个存量必须有真实研究初值、流入/流出和单位守恒关系。
4. **参数可追溯**：参数表同时记录数值、单位、来源类型（统计初值、历史量级、文献、比例校准、过程时滞、边界设定或敏感性范围）和取值理由；禁止用“AI估计”作为依据。
5. **检验计划**：至少包含结构/方程检查、Units Check、极端条件、历史行为再现、时间步长稳定性、敏感性和政策传导方向；结论必须写成“在模型边界与参数范围内”。报告型终端输出（例如 D、相对发展度）可保留 Vensim USE FLAG，但必须在审计记录中说明其是有意输出，不得用零系数或无意义反馈消除提示。

运行 `./skill.sh academic model.mdl --references <文献目录> --spec <已填写的model_spec.json>` 可执行机器门禁。门禁通过不等于模型已经有效，但门禁失败时不得直接进入论文结论。

### 历史验证的硬规则

标准系统动力学历史验证要求：2015 年（或研究起点）真实初值 + 同一套内生方程连续运行整个历史期和预测期。历史观测值只能作为真正位于系统边界之外的外生驱动，或作为模拟值与观测值的外部比较序列。禁止把六个核心存量、年度产出、耦合协调度或“历史路径/预测标志”写回模型以拼接 2025 年起点；这属于回放/拟合输入，不是历史行为生成检验。若确有外生历史驱动，必须在边界表、数据字典和方程注释中说明其不由模型生成的理由。

### 耦合协调度的硬规则

若研究讨论两个子系统的耦合协调，U1、U2、C、T、D 必须由系统动力学模型中的内生变量连续计算，并随基准与政策情景延伸到预测期。历史熵权评价或其他统计 D 只能用于外部行为对照、参数锚点或附录重构，不能作为 D 的输入路径，也不能与系统动力学完全割裂成“历史表格 + 另一个模型”。耦合评价的指标、归一化锚点、权重、公式、单位和边界必须写入变量表/方程表。对 GDP、开行列数、TEU、货值、网络规模、运输时间等带单位指标做标准化时，期初/期末边界必须是同单位模型参数；禁止直接写成“带单位变量 − 裸数字”，否则必须在 Vensim Units Check 前修复。

## 图面质量规则

- 同一模块变量尽量放近，不跨半个页面连线；
- 长距离来源引用只有在拆分 View 后仍需要时才使用 Shadow Variable；Shadow 只能发出箭头，不能作为结果接收入箭头，最终主图必须隐藏模型级影子提示，不得出现灰色 `<…>`；
- 一个变量最多保留 3-5 条直接可见箭头，超过就拆 View 或移入参数表；
- 主路径从左到右，反馈路径从右下绕回左上；
- 信息箭头交叉超过 3 条就拆子系统或建立反馈/结果辅助 View，不能用更大的弧度继续把线堆在主图中央；
- 普通信息箭头必须使用 `shape=1` 加至少一个控制点，才能保证 Vensim 原生弧线；弧度不明显时优先增加控制点偏移、拆 View 或移动节点，不能把线改成点线；
- 信息箭头使用深蓝色（建议 `0-0-150`），存量—流率实体管道使用黑色；禁止灰色、点线和半透明“装饰线”；
- 图中存在影子变量时，模型级 Sketch Appearance 必须写入 `27:64`，并在全新 Vensim 进程截图确认没有灰色 `<原因变量>`；
- 变量框不能重叠；主图直接可见箭头一般不超过 5 条；超过阈值必须拆分视图并在论文中说明各视图的边界；
- 箭头不能压在变量文字上；
- 图中变量名用精炼中文，完整解释放变量表；
- 常量参数默认放“参数表”，不要全部画到图里；
- 截图前统一字体、字号、线宽和缩放比例；
- 最终以能解释模型机制为准，不以把所有变量画出来为准。

## 论文成稿与最终交付门禁

模型通过结构、量纲和行为检验后，才进入论文成稿。论文正文必须呈现研究问题、边界与假设、指标和方程、参数依据、历史行为检验、情景结果、稳健性检验、结论与局限性的闭环；不得把建模软件操作、脚本调用、人工修改过程或 AI 生成过程写成研究方法，也不得使用教师修订版、前一版、根据意见修改等草稿措辞。除确有定义需要外，统一采用学术术语，删除不必要的引号和口语化表达，并使正文、图题、变量表、方程表和 CSV 表头的名称、单位和符号完全一致。

论文与交付检查至少包括：

- 历史期和预测期使用同一套内生状态方程；观测值仅作为边界输入或外部对照，不能在正文中被包装成模型输出；
- 耦合协调指标的定义、权重、归一化锚点、单位和模型内生来源均可追溯，指标表不能与系统动力学模型割裂；
- 表格采用黑色标准三线表，表头、单位、有效数字和脚注统一；不把原始截图、临时调参表或未经核验的数字直接放入正文；
- 每个结论都能回指模型变量、方程、检验表或图件；政策效果使用在模型边界与参数范围内的模拟结果等限定语；
- 论文 DOCX 为可编辑源稿，PDF 为版式交付稿；最终 PDF 必须完成文本抽取扫描、页数/字体/嵌入检查和逐页视觉抽查；
- 只保留一个最终交付目录，目录内按模型、数据、检验情景、图件、论文、复现脚本和审计材料分层；旧版本、临时目录、渲染缓存和 `__pycache__` 采用可恢复移动方式归档，不得混入最终目录；
- 发布技能或模型前，先检查 Git 跟踪树、差异、归档内容和发布包，确认没有缓存、临时文件、个人路径、旧版本或未验证的生成物。

Vensim 中灰色 `<变量名>` 是 Defined 变量未展开 causes 的提示，通常不是仿真错误，但论文主图不能留下这种阅读负担。处理方式是补充必要的子系统 View、把长距离来源放入辅助 View、将常量参数移入参数表，并在模型级设置隐藏所有影子提示；不能靠截图裁剪或手工遮挡。

## 工具入口

- 布局与审计：`vensim_system_dynamics/tools/vensim_autolayout.py`（`inspect` / `audit` / `layout`）
- 仿真与绘图：`vensim_system_dynamics/tools/vensim_engine.py`（`simulate` / `graph` / `compare` / `units` / `check` / `fix`）
- 便捷封装：`skill.sh`（根目录）
- 建模规范与流程：`vensim_system_dynamics/OPERATIONS_GUIDE.md`
- 示例模型：`vensim_system_dynamics/examples/`

## 快速使用

```bash
./skill.sh doctor                                 # 检查环境
./skill.sh audit vensim_system_dynamics/examples/population_demo.mdl     # 审计草图与方程语义
./skill.sh quick vensim_system_dynamics/examples/population_demo.mdl     # 一键 inspect + audit + layout
./skill.sh simulate vensim_system_dynamics/examples/population_demo.mdl --var Population --var Births
./skill.sh graph vensim_system_dynamics/examples/population_demo.mdl --var Population --output pop.png
./skill.sh check vensim_system_dynamics/examples/population_demo.mdl     # 全面检查
./skill.sh academic vensim_system_dynamics/examples/population_demo.mdl --references ./references --spec ./model_spec.json
./skill.sh visual vensim_system_dynamics/examples/population_demo.mdl     # 图面预检＋原生 Vensim 复核提示
./skill.sh fix broken.mdl --output fixed.mdl      # 自动修复
```

详细说明见 `README.md` 与 `vensim_system_dynamics/OPERATIONS_GUIDE.md`。
