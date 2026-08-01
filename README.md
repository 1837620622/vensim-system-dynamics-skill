# Vensim System Dynamics Skill

> 作者 **传康kk** · 微信 `1837620622` · GitHub [@1837620622](https://github.com/1837620622) · [商务合作见文末](#关于作者与商务合作)

> 一个可复用的 **AI 代理指令 + Python 工具脚本** 包，用于 Vensim 系统动力学建模、课程作业、政策分析与管理研究，并在 **保留方程与对象 ID** 的前提下把已建模的草图做保守的分层布局与基础弧线化整理。

### 一键安装（skills.sh）

技能主页：<https://skills.sh/1837620622/vensim-system-dynamics-skill>

```bash
gh skill install 1837620622/vensim-system-dynamics-skill
# 或
npx skills add 1837620622/vensim-system-dynamics-skill --skill vensim-skill --agent codex --yes
```

已发布到 [skills.sh](https://skills.sh)，可被 Claude Code、Codex CLI、Cursor、Windsurf 等所有兼容 Agent Skills 规范的运行时直接加载。完整安装方式见 [Agent Skill 安装](#agent-skill-安装skillssh)。

## 目录

- [当前能力边界](#当前能力边界请先阅读)
- [兼容性](#兼容性全球-ide--ai-编程助手) / [Agent Skill 安装](#agent-skill-安装skillssh)
- [这个项目解决什么问题](#这个项目解决什么问题) / [核心特性](#核心特性) / [已知限制](#已知限制)
- [目录结构](#目录结构) / [安装](#安装) / [快速开始](#快速开始)
- [命令参考](#命令参考) / [示例模型](#示例模型) / [配置文件说明](#配置文件说明)
- [SFD 推荐布局规范](#sfd-推荐布局规范) / [安全边界](#安全边界必须遵守)
- [适合的任务](#适合的任务) / [不适合的情况](#不适合的情况)
- [未来发展方向](#未来发展方向) / [实现依据](#实现依据)
- [许可证](#许可证) / [关于作者与商务合作](#关于作者与商务合作) / [致谢](#致谢)

## 当前能力边界（请先阅读）

本项目定位为 **Vensim `.mdl` Sketch 审计、保守自动布局与系统动力学建模辅助工具**，不是 Vensim 的全功能替代。

**已支持：**
- Sketch 对象 ID 与 Arrow 引用审计（`audit` / `check`）；
- 中文业务变量解析、仿真、审计与绘图；
- 普通变量节点的分层（`dot`）或力导向（`neato`）布局；
- 普通信息箭头的基础弧线化（单控制点圆弧）；
- 保留原方程区、保留对象数量、保留 Arrow 起止对象；
- 纯 Python 仿真引擎（`vensim_engine.py`）对常见结构（INTEG / LOOKUP / WITH LOOKUP / IF THEN ELSE / SMOOTH / DELAY1 / DELAY3 / DELAY FIXED）的 Euler 积分仿真与 CSV 导出；
- matplotlib 折线图与多场景对比图导出；
- 单位缺失预检、未定义变量检查、循环依赖检查、缺失单位自动补齐；
- nodata 诊断：默认严格模式下遇到不支持函数、变量缺失或求值失败会中止并给出根因；明确传入 `--keep-going` 时才继续输出兼容结果；
- Vensim 建模流程、单位检查和结果分析模板；
- 图面质量启发式审计：长距离信息箭头、长变量名、节点箭头过多、信息线交叉过多。

**论文级研究门禁（新增，必须先做）**：

- 先核对领域文献、系统动力学方法文献和数据来源，再确定研究问题、系统边界、外生输入、内生反馈、变量口径和参数依据；不能根据一张截图或变量名凭空构建模型。
- 历史验证采用“真实起点初值 + 同一套内生方程贯穿历史期和预测期”。历史观测只作真正的外生边界驱动或模拟值—观测值比较，禁止把历史路径、实际输出、预测标志或期末实际值写回存量/流量。
- 若研究含耦合协调，U1、U2、C、T、D 必须由模型内生变量生成并延伸到预测期；历史熵权 D 只能作外部对照或锚点，不能和系统动力学割裂。
- 耦合指标归一化时，GDP、开行、TEU、货值、网络和运输时间等带单位变量必须使用同单位期初/期末锚点；禁止“带单位变量−裸数字”。
- `academic` 命令会检查存量初值、单位、疑似历史回放、耦合输出、参考文献目录和已填写的模型规范；通过后仍必须回到 Vensim 做原生检查。

**论文级图面门禁（新增）**：

- 普通信息箭头强制使用 `shape=1` + 至少一个控制点，建议深蓝 `0-0-150` 实线；存量—流率实体管道保持黑色。
- 模型级 Sketch Appearance 使用 `27:64` 隐藏影子变量，关闭并全新打开 Vensim 后确认主图无灰色 `<…>`。
- 变量框不得重叠；直接可见箭头一般不超过 5 条；信息线交叉超过 3 处拆分子系统/反馈 View，不能继续把线堆在一张图里。
- `visual` 命令做机器预检，但最终必须在全新 Vensim 进程截图确认颜色、弧度、吸附、字体和布局。

**暂不承诺：**
- 全部 Vensim 函数的解析与仿真（数组下标、宏、部分特殊函数未实现）；
- 原生 Vensim 语法检查与完整单位量纲推导（单位一致性需回到 Vensim `Units Check` 确认）；
- 自动识别所有库存、流率、阀门和云（当前库存识别仍部分依赖图形形状，见下文"已知限制"）；
- 无交叉、无穿框的完全自动布线（当前未读取 Graphviz 边路由，仅做单控制点弧线）；
- Control Panel、敏感性分析和论文图表的自动生成；
- 对任意复杂 `.mdl` 文件的无损重写。

**最终质量门槛仍需回到 Vensim**：布局后请在 Vensim 中打开，执行 `Model > Check Model` 与 `Model > Units Check`，手工微调后保存。

[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![Graphviz](https://img.shields.io/badge/graphviz-required-orange.svg)](https://graphviz.org/)
[![Vensim](https://img.shields.io/badge/Vensim-PLE%2FPro-DSS.svg)](https://vensim.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

中文说明见 [skills/vensim-skill/vensim_system_dynamics/README_CN.md](skills/vensim-skill/vensim_system_dynamics/README_CN.md)。

---

## 兼容性（全球 IDE / AI 编程助手）

本技能是**纯 CLI 工具**（`skill.sh` + Python 标准库），不依赖 MCP 协议、不绑定特定 IDE 插件。任何能执行 shell 命令、能读取项目文件的 AI 编程助手均可使用，跨 macOS / Windows / Linux。设计目标兼容以下全球主流工具：

| 类别 | 兼容工具 |
|---|---|
| 云订阅型 | Claude Code、Cursor、Windsurf、Codex CLI、Antigravity、Amp、Mistral Vibe |
| 免费 / 云托管型 | Gemini CLI、GitHub Copilot（CLI 与 VS Code Chat）、Amazon Q Developer、Kiro、Qwen Code |
| 开源 BYOK 型 | OpenCode、Aider、Cline、Continue.dev、Goose、Roo Code、OpenClaw、Zed、iFlow、Kimi Code CLI、BLACKBOX |
| IDE 内置 / 插件型 | VS Code、JetBrains 全系（IntelliJ / PyCharm 等）AI Assistant、Trae、通义灵码、CodeGeeX、Baidu Comate、Replit AI |
| 自主 Agent 型 | Devin、OpenHands、Bolt.new、v0、Lovable |

**为什么便于跨工具使用**：本技能遵循 Agent Skills 的轻量结构，frontmatter 提供 `name` / `description` 等标准字段，正文为纯 Markdown 指令。工具调用通过各 agent 原生的 shell 执行能力完成，无需安装插件。

**运行前提**：Python 3.8+、可选 Graphviz（布局命令）、可选 matplotlib（绘图命令）。进入 `skills/vensim-skill` 后用 `./skill.sh doctor` 一键自检。

**跨平台入口**：
- macOS / Linux：`./skill.sh <命令>`（bash）
- Windows：`skill.cmd <命令>`（cmd / PowerShell），或在 Git Bash / WSL 下用 `./skill.sh`
- 两个入口命令与参数完全一致，均自动检测 `python3` / `python` / `py`，无需手动配置
- Python 脚本用 `pathlib` + `shutil.which` + `subprocess`（不依赖 shell），读文件兼容 UTF-8 BOM 与 GB 编码，写 CSV 用 `newline=""` 避免 Windows 双换行

## Agent Skill 安装（skills.sh）

本技能已发布到 [skills.sh](https://skills.sh) —— Agent Skills 公共目录，可被 Claude Code、Codex CLI、Cursor、Windsurf 等所有兼容 Agent Skills 规范的运行时直接发现和安装。

**技能主页**：<https://skills.sh/1837620622/vensim-system-dynamics-skill>

### 方式一：gh skill（GitHub CLI 扩展）

```bash
# 安装最新版
gh skill install 1837620622/vensim-system-dynamics-skill

# 固定到指定版本（可复现）
gh skill install 1837620622/vensim-system-dynamics-skill vensim-skill --pin v1.0.5
```

### 方式二：npx skills（无需全局安装）

```bash
# 查看仓库内可安装的技能
npx skills add 1837620622/vensim-system-dynamics-skill --list

# 安装到指定 Agent（如 Codex）
npx skills add 1837620622/vensim-system-dynamics-skill \
  --skill vensim-skill \
  --agent codex \
  --yes

# 全局安装到当前用户目录
npx skills add 1837620622/vensim-system-dynamics-skill --skill vensim-skill --global --yes
```

### 方式三：从 GitHub 克隆后本地使用

```bash
git clone https://github.com/1837620622/vensim-system-dynamics-skill.git
cd vensim-system-dynamics-skill/skills/vensim-skill
./skill.sh doctor          # 自检环境
./skill.sh examples        # 审计全部示例
```

> 安装后仍需在本机安装 Graphviz 才能使用自动布局命令；仿真、检查和修复命令只依赖 Python 标准库。各 Agent 的技能目录位置不同，`npx skills` 会自动写入对应目录，无需手动配置。

---

## 这个项目解决什么问题

Vensim PLE **没有**一键「全图自动布局」或「自动避让」按钮。原生 Layout 菜单只做对齐、统一大小、水平 / 垂直等间距。当模型变量变多时，箭头会漂浮、重叠、穿过变量，手工整理成论文可用图非常耗时。

但 Vensim 的普通 Arrow 在存在 **一个中间控制点** 时会形成平滑圆弧；按住 `Command/Ctrl` 绘制的是 Spline Arrow。因此「AI 自动排版 + 连线自动变平滑曲线」是可行的——实现路径是：

```
Vensim 先建立正确模型结构（库存 / 流率 / 阀门 / 云 / 方程）
        ↓  Python 解析 .mdl 草图，提取真实对象 ID、坐标、箭头 from/to、控制点
        ↓  Graphviz 计算可移动辅助变量的坐标（库存 / 流率骨架锁定）
        ↓  Python 为每条信息箭头生成单控制点圆弧（平行边自动错开曲率）
        ↓  回写新坐标与控制点到 *_autolayout.mdl（方程区不动）
        ↓  Vensim 打开 → Check Model → Units Check → 手工微调
```

**它不是「让 AI 代替建模」**，而是把 **已正确建模** 的复杂图整理得更易读、更符合论文规范。

---

## 核心特性

- **保守的草图解析**：严格依据 Vensim 官方 Sketch Format 文档，准确识别变量(10)、阀门(11)、源汇云(12)、箭头(1) 的真实字段。
- **物理流 vs 信息箭头自动区分**：通过箭头 `thick` 字段（≥20 为物理流率管道，<20 为信息箭头）和端点对象类型判定，物理流管道保持原样不被破坏。
- **半固定 + 自动布局**：默认锁定库存、阀门、云、流率标签、shadow variable、控制面板对象；只让 Graphviz 排布普通辅助变量。
- **基础弧线化**：为普通信息箭头生成单个中间控制点，使普通 Arrow 显示为圆弧；平行边自动分配对称曲率避免重叠。**注意：当前未读取 Graphviz 边路由控制点，layout 不会自动完成节点避障；audit 会提示明显长线、过密节点和交叉风险。**
- **安全回写**：自动生成 `*_backup.mdl` 与 `*.layout_report.json`；不改方程区，不新建 / 删除对象，不改箭头 `from/to`。
- **审计与检查**：`inspect` 列出全部对象与箭头属性；`audit` 检测断裂的箭头对象引用、重复定义、未定义引用、中文变量规范和图面质量风险；`check` 额外检测缺失单位、循环依赖、缺失控制变量。
- **纯 Python 仿真引擎**（`vensim_engine.py`，不依赖 Vensim）：对常见结构（INTEG / LOOKUP / WITH LOOKUP / IF THEN ELSE / SMOOTH / DELAY1 / DELAY3 / DELAY FIXED）做 Euler 积分仿真，导出 CSV；matplotlib 折线图与多场景对比图；缺失单位补齐与断裂草图箭头修复。**注意：这不是原生 Vensim 语法检查，复杂函数、数组结构、宏与外部数据尚未完整支持。**
- **纯标准库**：布局脚本只用标准库，无需 `pip install`；唯一外部依赖是 Graphviz。仿真与绘图需 matplotlib（绘图可选，仿真与校验无需）。

## 已知限制

1. **仿真引擎仍是 Vensim 子集**，不支持数组下标、宏、`GET DATA`、优化、敏感性分析等高级功能；复杂模型建议优先接入 PySD 作为可选后端。
2. **箭头类型未严格区分**：当前对信息箭头统一写入单控制点，未区分 Polyline / Perpendicular / Spline。未来将仅对普通 Arrow 写控制点，其他类型保持原样。
3. **未实现自动避障布线**：当前只读取 Graphviz 节点坐标，未读取边路由 spline；箭头控制点由中垂线法向量计算，复杂场景可能穿框或交叉。`audit` 会提示明显交叉风险，但不会自动重路由所有线段。
4. **`audit` 是预检工具**：会检查 Sketch 引用、方程区基础语义、中文业务变量和图面质量风险，但不替代完整单位检查、CLD 极性一致性判断和行为有效性检验。
5. **图面质量审计是启发式提醒**，能发现长线、交叉和过密节点，但不能替代 Vensim 中的人工排版判断。
6. **单位检查仍是轻量预检**，仅可靠覆盖缺失单位与部分结构性问题，完整量纲一致性仍需回到 Vensim `Units Check`。

## 中文变量与论文图规范

中文课程论文、政策仿真和管理研究模型默认使用中文业务变量。Vensim 控制变量 `INITIAL TIME`、`FINAL TIME`、`TIME STEP`、`SAVEPER` 与函数名 `INTEG`、`MIN`、`MAX` 等保持原生英文；业务变量、CSV 表头、图题、坐标轴、图例和变量表优先使用中文。

图面默认遵守以下规则：

- 同一模块变量尽量放近，不跨半个页面连线；
- 长距离关系优先拆分 View；只有必要时使用 Shadow Variable，且主图必须写入 `27:64` 隐藏灰色 `<…>`；Shadow 只能发出箭头，不能作为结果接收入箭头；
- 一个变量最多保留 3-5 条直接可见箭头，超过就拆 View 或移入参数表；
- 主路径从左到右，反馈路径从右下绕回左上；
- 交叉线超过 3 条就拆子系统或反馈/结果辅助 View；
- 普通信息箭头必须是 `shape=1` + 至少一个控制点的深蓝实线，存量—流率实体管道为黑色；变量框不得重叠，直接可见箭头一般不超过 5 条；
- 箭头不能压在变量文字上；
- 图中变量名用精炼中文，完整解释放变量表；
- 常量参数默认放“参数表”，不要全部画到图里；
- Python 图表图例采用动态策略：少量曲线可放图内空白角落，中等数量外置，很多曲线放底部多列或拆图，避免遮挡和过度留白；
- 最终以能解释模型机制为准，不以把所有变量画出来为准。

---

## 目录结构

```
vensim-skill/
├── README.md                         # 本文件
├── LICENSE                           # MIT
├── .gitignore
├── skills/
│   └── vensim-skill/                 # GitHub Skill 发布目录，目录名与 name 一致
│       ├── SKILL.md                  # Agent Skill 入口（name/description/license）
│       ├── skill.sh                  # macOS/Linux 便捷 CLI
│       ├── skill.cmd                 # Windows CMD/PowerShell 入口
│       └── vensim_system_dynamics/   # 工具、模板、示例与说明文档
│           ├── OPERATIONS_GUIDE.md   # 详细操作手册（建模原则/工作流/草图格式/安全边界）
│           ├── README_CN.md          # 中文说明
│           ├── requirements.txt      # 依赖说明（无强制 pip 包）
│           ├── docs/REFERENCES.md    # 实现依据与官方文档链接
│           ├── tools/
│           │   ├── vensim_autolayout.py  # 草图检查/审计/保守自动布局
│           │   ├── vensim_engine.py      # 纯 Python 仿真引擎（仿真/绘图/校验/修复）
│           │   └── skill_cli.py          # 跨平台 CLI 包装（供 skill.cmd 调用）
│           ├── templates/
│           │   ├── model_spec_template.json  # 建模前语义规范模板
│           │   ├── layout_config_sfd.json    # SFD 自动排版配置样例
│           │   └── layout_config_cld.json    # CLD 自动排版配置样例
│           └── examples/             # 16 个覆盖经典 SD 结构的示例模型
│               ├── population_demo.mdl              # SFD：库存流率 + 承载力负反馈
│               ├── s_shaped_growth.mdl              # S 型增长（正负反馈复合）
│               ├── first_order_positive_feedback.mdl  # 一阶正反馈（指数增长）
│               ├── first_order_negative_feedback.mdl  # 一阶负反馈（目标追赶）
│               ├── second_order_oscillation.mdl     # 二阶振荡
│               ├── aging_chain.mdl                  # 老化链（多库存串联）
│               ├── sir_epidemic.mdl                 # SIR 传染病模型
│               ├── depreciation.mdl                 # 折旧/衰减稳态
│               ├── cld_customer_loop.mdl            # CLD：客户增长因果回路
│               ├── coflow_structure.mdl             # 协流结构
│               ├── control_panel.mdl                # 控制面板示例
│               ├── delay_structure.mdl              # 延迟结构
│               ├── lookup_structure.mdl             # Lookup 表函数结构
│               ├── multiview_shadow.mdl             # 多视图与 shadow variable
│               ├── production_chain.mdl             # 生产链
│               └── smooth_structure.mdl             # SMOOTH 平滑结构
└── tests/                            # 仿真引擎回归测试（16 个用例）
```

---

## 安装

### 1. Python

Python 3.8+，无需额外包：

```bash
python3 --version
```

### 2. Graphviz

**macOS：**

```bash
brew install graphviz
dot -V
```

**Windows：** 从 https://graphviz.org/download/ 安装，并把 `dot` 所在目录（如 `C:\Program Files\Graphviz\bin`）加入 `PATH`。

**Linux：**

```bash
sudo apt-get install graphviz   # Debian/Ubuntu
sudo dnf install graphviz       # Fedora
```

---

## 快速开始

### 方式一：skill.sh 便捷封装

```bash
cd skills/vensim-skill
chmod +x skill.sh
./skill.sh doctor                                   # 检查 python3 与 graphviz
./skill.sh examples                                 # 审计全部示例
./skill.sh quick vensim_system_dynamics/examples/population_demo.mdl       # 一键 inspect + audit + layout
./skill.sh layout your_model.mdl --route            # 单步自动排版（默认 SFD 配置）
```

### 方式一·续：仿真 / 绘图 / 校验 / 修复（不依赖 Vensim）

```bash
# 纯 Python 仿真，导出 CSV
./skill.sh simulate vensim_system_dynamics/examples/population_demo.mdl --var Population --var Births --var Deaths

# 仿真并导出折线图 PNG（需 matplotlib）
./skill.sh graph vensim_system_dynamics/examples/population_demo.mdl --var Population --var Deaths \
       --output pop.png --title "Population Dynamics"

# 多场景对比图（修改 Carrying Capacity 等参数后对比）
./skill.sh compare vensim_system_dynamics/examples/population_demo.mdl \
       --scenario scenario_low.mdl --scenario scenario_high.mdl \
       --var Population --var "Crowding Effect" --output compare.png

# 单位量纲校验
./skill.sh units vensim_system_dynamics/examples/population_demo.mdl

# 全面检查：未定义变量 / 缺失单位 / 循环依赖 / 断裂草图引用
./skill.sh check vensim_system_dynamics/examples/population_demo.mdl

# 研究设计门禁：参考文献、边界、存量初值、历史内生性、耦合输出
./skill.sh academic vensim_system_dynamics/examples/population_demo.mdl \
       --references ./references --spec ./model_spec.json

# 图面门禁：弧线、深蓝/黑色样式、影子、重叠与交叉预检
./skill.sh visual vensim_system_dynamics/examples/population_demo.mdl

# 自动修复缺失单位、断裂草图箭头
./skill.sh fix broken_model.mdl --output fixed_model.mdl

# 一键 check + simulate + graph（适合快速跑通一个模型）
./skill.sh auto vensim_system_dynamics/examples/population_demo.mdl --var Population --var Births
```

> 仿真默认严格模式：遇到不支持的函数或求值失败会中止并指出根因。如需兼容输出（失败变量置零继续），加 `--keep-going`。

### 方式二：直接调用 Python 脚本

```bash
# 1. 查看模型内的对象 ID、坐标、形状、箭头 from/to、控制点
python3 vensim_system_dynamics/tools/vensim_autolayout.py inspect vensim_system_dynamics/examples/population_demo.mdl

# 2. 审计箭头引用是否指向本视图内有效对象
python3 vensim_system_dynamics/tools/vensim_autolayout.py audit vensim_system_dynamics/examples/population_demo.mdl

# 3. 复制 SFD 配置，填入要锁定的库存 / 流率名
cp vensim_system_dynamics/templates/layout_config_sfd.json my_layout.json
#   编辑 my_layout.json，把 lock_node_names 改成你模型里的库存与流率标签名

# 4. 生成自动排版模型（自动建 .backup.mdl 与 .layout_report.json）
python3 vensim_system_dynamics/tools/vensim_autolayout.py layout vensim_system_dynamics/examples/population_demo.mdl \
  --output vensim_system_dynamics/examples/population_demo_autolayout.mdl \
  --config my_layout.json \
  --engine dot \
  --route-information-arrows

# 5. 审计输出
python3 vensim_system_dynamics/tools/vensim_autolayout.py audit vensim_system_dynamics/examples/population_demo_autolayout.mdl
```

在 Vensim 中打开 `*_autolayout.mdl`：先看图，再 `Model > Check Model`，再 `Model > Units Check`，手工微调少数交叉关系后保存为最终版本。

---

## 命令参考

### `inspect` — 列出草图对象与箭头

```bash
python3 vensim_system_dynamics/tools/vensim_autolayout.py inspect <model.mdl>
```

输出每个对象的类型(`var`/`valve`/`src/sink`)、坐标、形状、是否附着阀门、是否 shadow variable、是否库存状；每条箭头标注 `FLOW`(物理流率管道) 或 `info`(信息箭头)、weight、控制点数。

### `audit` — 审计箭头对象引用

```bash
python3 vensim_system_dynamics/tools/vensim_autolayout.py audit <model.mdl>
```

检测箭头 `from/to` 是否引用了本视图不存在的对象 ID（会漂浮 / 反向 / 穿变量的根因），并警告无控制点、空名变量、中文模型中的非中文业务变量、长变量名、长距离信息箭头、节点箭头过多和信息线交叉过多。

### `academic` / `visual` — 论文级门禁

```bash
./skill.sh academic <model.mdl> --references <文献目录> --spec <已填写的model_spec.json> --require-coupling
./skill.sh visual <model.mdl>
```

`academic` 会阻止疑似历史路径/实际输出回填、缺少存量初值或单位、耦合协调输出未内生生成和缺少参考文献目录的交付。`visual` 会审计 `27:64` 隐藏影子设置、`shape=1` 控制点、深蓝实线、变量框重叠、直接箭头度数和交叉风险；最后仍需在全新 Vensim 进程人工截图确认。
对有意作为论文结果导出的 D、相对发展度等终端变量，原生 Vensim 可能显示 USE FLAG；技能将其作为需解释的报告输出，而不是要求用零系数伪造引用。

### `layout` — 应用保守自动排版

```bash
python3 vensim_system_dynamics/tools/vensim_autolayout.py layout <model.mdl> \
  --output <out.mdl> \
  --config <config.json> \
  --engine {dot|neato|fdp|sfdp} \
  --route-information-arrows
```

- `--engine dot`：分层布局，适合 SFD（库存—流率有明确层级）。
- `--engine neato`/`fdp`：弹簧模型，适合 CLD（关系网图）。
- `--route-information-arrows`：为可重布线的信息箭头设置单个圆弧控制点。

---

## 示例模型

`skills/vensim-skill/vensim_system_dynamics/examples/` 下提供 16 个覆盖经典系统动力学结构的示例，可直接用于学习、测试与作业参考：

| 分类 | 模型 | 说明 |
|---|---|---|
| 基础 SFD | `population_demo.mdl` | 库存流率 + 承载力负反馈闭环 |
| 增长范式 | `s_shaped_growth.mdl` | S 型增长（正负反馈复合） |
| 反馈结构 | `first_order_positive_feedback.mdl` | 一阶正反馈（指数增长） |
| 反馈结构 | `first_order_negative_feedback.mdl` | 一阶负反馈（目标追赶） |
| 反馈结构 | `second_order_oscillation.mdl` | 二阶振荡 |
| 链式结构 | `aging_chain.mdl` | 老化链（多库存串联） |
| 应用模型 | `sir_epidemic.mdl` | SIR 传染病模型 |
| 应用模型 | `depreciation.mdl` | 折旧/衰减稳态 |
| CLD | `cld_customer_loop.mdl` | 客户增长因果回路图 |
| 经典结构 | `coflow_structure.mdl` | 协流结构 |
| 经典结构 | `delay_structure.mdl` | 延迟结构 |
| 经典结构 | `lookup_structure.mdl` | Lookup 表函数结构 |
| 经典结构 | `smooth_structure.mdl` | SMOOTH 平滑结构 |
| 草图特性 | `control_panel.mdl` | 控制面板示例 |
| 草图特性 | `multiview_shadow.mdl` | 多视图与 shadow variable |
| 链式结构 | `production_chain.mdl` | 生产链 |

快速验证全部示例：

```bash
cd skills/vensim-skill
./skill.sh examples                    # 审计全部示例草图与方程
./skill.sh simulate vensim_system_dynamics/examples/sir_epidemic.mdl --var Susceptible --var Infected --var Recovered
```

> 部分模型含诊断变量（如康复比例、稳态指示量），审计时可能出现「未使用变量」警告，属正常现象，不影响仿真。详见 [examples/README.md](skills/vensim-skill/vensim_system_dynamics/examples/README.md)。

---

## 配置文件说明

```json
{
  "view": "*",
  "canvas": {"x_min": 120, "x_max": 1180, "y_min": 110, "y_max": 720},
  "rankdir": "LR",
  "nodesep": 0.65,
  "ranksep": 1.05,
  "move_stocks": false,
  "lock_node_names": ["Population", "Births", "Deaths"],
  "lock_object_ids": [],
  "route_information_arrows_only": true,
  "curve_strength": 0.18,
  "parallel_curve_spacing": 0.08,
  "minimum_curve_pixels": 26,
  "maximum_curve_pixels": 118,
  "skip_views": []
}
```

| 字段 | 说明 |
|---|---|
| `view` | 处理哪个视图，`*` 为全部 |
| `canvas` | 可移动节点的目标画布范围（像素） |
| `rankdir` | Graphviz 布局方向：`LR`/`TB`/`BT`/`RL` |
| `nodesep` / `ranksep` | 节点间距 / 层间距 |
| `move_stocks` | 是否允许移动库存状变量（SFD 应为 `false`） |
| `lock_node_names` | 按变量名锁定的对象（库存、流率标签） |
| `lock_object_ids` | 按对象 ID 锁定 |
| `route_information_arrows_only` | 只重布线信息箭头，不动物理流 |
| `curve_strength` | 弧线强度系数 |
| `parallel_curve_spacing` | 平行边错开曲率增量 |
| `minimum_curve_pixels` / `maximum_curve_pixels` | 弧度像素上下限 |
| `skip_views` | 跳过的视图名列表 |

---

## SFD 推荐布局规范

系统动力学存量流量图有明确规范，建议采用「半固定 + 自动布局」而非完全交给 Graphviz：

```
顶部：云 → 流入 → 库存 → 流出 → 云
中部：辅助变量、比率、效果变量
右侧 / 下方：参数、价格、比率
下部：收入、成本、利润
底部：留存收益 → 投资者回报
```

- 库存与两侧流率管道固定在顶部中央；
- 投资者回报等库存及其流率固定在左下；
- 其余辅助变量交给 Graphviz 按层级排布；
- 箭头按上下 / 左右关系自动选取弯曲方向；
- 同方向多条箭头自动分配不同弯曲轨道避免重叠；
- 远距离关系用大弧度，近距离用小弧度。

---

## 安全边界（必须遵守）

- 自动排版 **只整理已正确建模** 的草图；
- 脚本 **不新建** 库存、阀门、云或物理流；
- 脚本 **不改** 方程区、箭头 `from/to`、对象 ID；
- 默认 **锁定** 库存、阀门、源汇云、流率标签、shadow variable、控制面板对象；
- 输出 **必须** 在目标 Vensim 版本重新打开并运行 `Check Model` + `Units Check`；
- 出现错位 / 浮动箭头 / 阀门脱离时 **立即恢复** `*_backup.mdl` 改用手动。

---

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

---

## 未来发展方向

本项目后续将由"Vensim 草图审计与自动布局工具"逐步扩展为面向系统动力学建模全过程的智能辅助平台。总体目标是在不破坏原始模型方程、变量关系和 Vensim 文件兼容性的前提下，实现模型构建、逻辑审查、单位校验、仿真分析、可视化输出与论文材料生成的一体化支持。

1. **模型语义解析模块**：不再仅依赖 Sketch 图形形状识别库存、流率和辅助变量，而是从 `.mdl` 方程区解析变量定义、`INTEG`、`DELAY`、`SMOOTH`、`WITH LOOKUP`、数组下标和初始条件，建立统一的模型中间表示，识别库存—流率结构、变量依赖、反馈回路、变量单位和政策杠杆。
2. **模型质量审查模块**：覆盖重复定义、未定义变量引用、未使用变量、库存方程缺失、流率量纲不匹配、比例变量越界、概率变量越界、负库存风险、循环依赖、方程与因果箭头不一致等问题，输出变量名、位置、影响范围、严重等级和修复建议。
3. **自动布局与自动布线**：采用"语义锚点 + 图结构布局 + 几何避障"组合方法，引入节点边界检测、线段交叉检测、曲线碰撞检测、平行箭头分轨和跨层箭头重路由，降低箭头穿框与反馈回路混乱。
4. **Vensim 兼容的仿真与情景分析**：支持基准情景、政策情景、敏感性分析、参数组合实验和极端条件测试，自动导出净利润、植被盖度、耦合协调度、碳减排量、客户规模、库存水平等指标的折线图、对比图、统计表和 CSV，保存完整参数配置保证可复现。
5. **论文与课程作业交付模块**：自动生成变量定义表、方程表、参数表、政策情景表、模型检验表、仿真结果表和图表说明文字，提供因果回路分析、正负反馈识别、主导结构迁移分析、模型边界说明与局限性分析模板。
6. **多层质量门槛**：输出前依次完成方程语法检查、变量依赖检查、单位检查、结构一致性检查、图形重叠检查、仿真稳定性检查和结果合理性检查，全部通过后才生成最终文件。
7. **明确工具边界**：优先保证对常见 Vensim 建模结构的稳定支持，不将"完全替代 Vensim"作为短期目标，对复杂函数、特殊分析工具、原生 Control Panel、数组结构保留回到 Vensim 检查的流程。

---

## 实现依据

依据 Vensim 官方文档与开源解析器实现，完整链接见 [skills/vensim-skill/vensim_system_dynamics/docs/REFERENCES.md](skills/vensim-skill/vensim_system_dynamics/docs/REFERENCES.md)：

- Vensim Help — [Sketch Format](https://www.vensim.com/documentation/ref_sketch_format.html)
- Vensim Help — [Sketch Object Detail](https://www.vensim.com/documentation/24305.html)
- Vensim Help — [Arrow Class](https://www.vensim.com/documentation/22925.html)
- Vensim Help — [Layout Menu](https://www.vensim.com/documentation/layoutmenu.html)
- Graphviz — [splines](https://graphviz.org/docs/attrs/splines/)
- PySD — [Vensim Translation](https://pysd.readthedocs.io/en/master/structure/vensim_translation.html)（辅助验证）

---

## 许可证

MIT License。见 [LICENSE](LICENSE)。

## 关于作者与商务合作

**传康kk**（chuankangkk）—— 系统动力学建模与 AI Agent 工具开发者。

- 微信：`1837620622`（备注「Vensim 技能合作」）
- GitHub：[@1837620622](https://github.com/1837620622)
- 技能主页：<https://skills.sh/1837620622/vensim-system-dynamics-skill> · 安装 `gh skill install 1837620622/vensim-system-dynamics-skill`

**合作方向**：

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

## 致谢

- [Ventana Systems](https://www.vensim.com/) — Vensim 与官方文档
- [SDXorg/pysd](https://github.com/SDXorg/pysd) — Vensim `.mdl` 解析参考
- [Graphviz](https://graphviz.org/) — 自动布局引擎
