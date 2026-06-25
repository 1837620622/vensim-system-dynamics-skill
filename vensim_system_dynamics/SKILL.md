---
name: vensim-system-dynamics
description: Vensim 系统动力学建模与 .mdl 草图自动排版技能。用于课程作业、政策分析与管理研究的因果回路图(CLD)、存量流量图(SFD)、方程、单位校验、仿真、情景/敏感性分析、控制面板、论文图表，以及在保留方程与对象 ID 的前提下对已有草图做"半固定 + Graphviz 自动布局 + 信息箭头平滑弧线"整理。当用户需要用 Vensim 建立或整理系统动力学模型、生成或修复 .mdl 草图、把乱箭头/重叠变量自动排版成论文级 SFD/CLD、做汽车销售/人口/供应链/政策等 SFD 作业时使用。
license: MIT
compatibility: Claude Code, Codex CLI, Cursor, Windsurf, Gemini CLI, GitHub Copilot, Amazon Q Developer, Aider, Cline, Continue.dev, OpenCode, Roo Code, Goose, Zed, Trae, Antigravity, Amp, Qwen Code, Kiro, OpenClaw, JetBrains AI Assistant, VS Code, any shell-capable AI coding agent
---

# Vensim 系统动力学通用技能

## 0. 兼容性（全球 IDE / AI 编程助手）

本技能是**纯 CLI 工具**（`skill.sh` + Python 标准库），不依赖 MCP 协议、不绑定特定 IDE 插件。任何能执行 shell 命令、能读取项目文件的 AI 编程助手均可使用，跨 macOS / Windows / Linux。已验证与以下全球主流工具兼容：

- **云订阅型**：Claude Code、Cursor、Windsurf、Codex CLI、Antigravity、Amp、Mistral Vibe
- **免费 / 云托管型**：Gemini CLI、GitHub Copilot（CLI 与 VS Code Chat）、Amazon Q Developer、Kiro、Qwen Code
- **开源 BYOK 型**：OpenCode、Aider、Cline、Continue.dev、Goose、Roo Code、OpenClaw、Zed、iFlow、Kimi Code CLI、BLACKBOX
- **IDE 内置 / 插件型**：VS Code、JetBrains 全系（IntelliJ/PyCharm 等）AI Assistant、Trae、通义灵码、CodeGeeX、Baidu Comate、Replit AI
- **自主 Agent 型**：Devin、OpenHands、Bolt.new、v0、Lovable

> 调研依据：Agent Skills 规范（agentskills.io / agensi.io）确认 `name` + `description` + 纯 Markdown 指令的技能可跨 Claude Code、Codex CLI、Cursor、Windsurf、Gemini CLI、Copilot 等全部主流运行时加载；本技能仅用标准 frontmatter 字段，平台特定字段均被安全忽略。

**跨平台入口**：macOS/Linux 用 `./skill.sh`，Windows 用 `skill.cmd`（cmd/PowerShell）或 Git Bash/WSL 下的 `./skill.sh`；两入口命令一致，自动检测 `python3`/`python`/`py`。Python 脚本用 `pathlib`+`shutil.which`+`subprocess`（不依赖 shell），读文件兼容 UTF-8 BOM 与 GB 编码，CSV 用 `newline=""` 避免 Windows 双换行。

## 1. 核心结论（必须先理解）

Vensim PLE **没有**一键全图自动布局或自动避让按钮。原生 Layout 菜单只做对齐、统一大小、等间距。但 Vensim 的普通 Arrow 在存在**一个中间控制点**时会形成平滑圆弧；按住 `Command/Ctrl` 绘制的是 Spline Arrow。因此"AI 自动排版 + 连线自动变平滑曲线"是可行的，但实现路径是：

```
Vensim 先建立正确模型结构（库存/流率/阀门/云/方程）
    ↓ Python 解析 .mdl 草图，提取真实对象 ID、坐标、箭头 from/to、控制点
    ↓ Graphviz 计算可移动辅助变量的坐标（库存/流率骨架锁定）
    ↓ Python 为每条信息箭头生成单控制点圆弧（平行边错开曲率）
    ↓ 回写新坐标与控制点到 *_autolayout.mdl（方程区不动）
    ↓ Vensim 打开 → Check Model → Units Check → 手工微调
```

**绝对不要**让 Python 从零凭空写 `.mdl` 的库存/阀门/云/物理流；**绝对不要**把 Graphviz 导出的 SVG/PDF 当图塞进 Vensim（那只是图片，无法仿真）。自动排版只整理**已正确建模**的草图。

## 2. .mdl 草图格式（写脚本/改图的依据）

`.mdl` = 方程区 + 草图区 + 设置区。草图区由 `\\\---///` 标记开始，每个 view 一段：

- `V300 ...` 版本码（Vensim 3/4/5 共用 300），其下内容会被忽略
- `*View Name` 视图名（≤30 字符）
- `$iniarrow,n2,face|size|attrs|color|shape|arrow|fill|background|ppix,ppiy,zoom,tf` 视图默认字体/颜色

对象行首字段为类型码：
- `10` = 变量(word)：`10,id,name,x,y,w,h,shape,bits,hid,hasf,tpos,thick,rest,...`
- `11` = 流率阀门(flow arrow)
- `12` = 源/汇/图/注释(source/sink/plot/comment)
- `1` = 箭头(arrow)：`1,id,from,to,shape,...,np|(x,y)|...`
- `30/31` = 其他对象

关键字段语义：
- `x,y` = 文字**中心**坐标，像素，x 向右增、y 向下增
- `w,h` = **半宽/半高**（x-w 为左边，x+w 为右边）
- `shape` 低 5 位 = 形状；bit6(32) = 附着到阀门；bit7 = 形状由类型决定
- `bits`：bit1=允许入箭头，bit2=允许出箭头，bit3=有注释续行，bit4=IO 对象，bit7=因果不穿透；**arrows_in_allowed 为偶数 = shadow variable**
- 箭头 `from,to` = 对象 ID（必须是本 view 内已存在的对象 ID）；`np` = 控制点个数；`|(x,y)|` 列表 = 控制点
- 普通 Arrow：1 个中间控制点 → 圆弧；0 个 → 直线；Polyline/Perpendicular 可多达 16 个中间点

只要对象 ID、from/to、控制点格式错一个，箭头就会漂浮、反向或穿过变量。

## 3. 强制建模原则

1. 先建模语义后画图：任何图形操作必须来自已确认的变量定义、因果方向、库存—流率关系、方程。
2. 严格区分物质流与信息流：库存与流率必须用 Vensim Rate 工具；普通蓝色箭头只表示信息/因果影响。
3. 每个库存：`库存 = INTEG(流入总量 - 流出总量, 初始值)`，单位满足 `库存单位 = 流率单位 × 时间单位`。
4. 每个变量有：定义、单位、来源、取值范围、初值/校准逻辑。
5. 先 `Model > Check Model`，再 `Model > Units Check`，两项通过后才分析仿真结果。
6. 情景结论表述为"在模型设定与参数范围内的模拟结果"，不得把未验证推断写成实证结论。
7. 复杂模型拆多 View：CLD、核心 SFD、子模块、控制面板、结果页。不要全堆一个视图。

## 4. 标准工作流

### 阶段 A：任务解析与设计
1. 提取作业要求、交付格式、软件限制、时间单位、需要的图和检验。
2. 明确系统边界：研究对象、时间跨度、外生/内生变量。
3. 写出研究问题、状态变量、政策杠杆、预期行为模式（增长/S 型/波动/超调/衰减）。
4. 在 `templates/model_spec_template.json` 整理变量清单与关系，未确认项标 `pending_validation`。

### 阶段 B：CLD
1. 变量用名词短语，避免在变量名中混入方向词。
2. 每条连线标极性：原因增→结果增为 `+`，减为 `-`。
3. 回路命名分类：正反馈(放大/累积/增长)、负反馈(调节/目标追赶/约束)、延迟标记。
4. 输出：变量定义表、回路表、两条关键回路机制分析、主导结构迁移。

### 阶段 C：SFD 与方程
1. 先在 Vensim 手工建立库存—流率骨架；不要由脚本凭空创建阀门、云、物理流。
2. 补充辅助变量与信息箭头；信息箭头只指向其确实影响的对象。
3. 方程、单位、初值、时间设置写入 Vensim；初始化可追溯。
4. 非线性关系优先用独立命名的 Lookup；`WITH LOOKUP` 仅用于简单关系。
5. 流率单位审计：人数库存=人，招募/流失=人/月；资金=元，收入/成本=元/月；比率=Dmnl 或 1/月，不混用。

### 阶段 D：检验与仿真
1. 结构检验：方程引用、符号、库存守恒、无双重计数。
2. 量纲检验：Units Check 必过；无法检查的外生数据变量说明原因。
3. 极端条件：关键参数置 0/上限/极端值，确认无负库存、除零、不合理爆炸。
4. 行为再现：基准情景方向、转折点、数量级与理论/历史一致。
5. 政策实验：每次只改一个杠杆，记录参数、机制链、结果。
6. 敏感性分析：识别高不确定参数，报告结论稳健性。

### 阶段 E：图形与论文交付
1. 输出：CLD、核心 SFD、参数表、方程表、基准结果、政策对比、敏感性、边界与局限。
2. 论文图不得出现编辑手柄、重叠文字、断裂箭头、未解释变量、混用字体、无单位坐标轴。
3. 表格用三线表；变量表含"符号/变量、含义、单位、类型、来源"。
4. 结论结构：发现 → 机制 → 政策含义 → 适用边界。

## 5. 草图自动排版：安全边界

任何自动整理必须：
- 从原文件复制 `*_backup.mdl`；
- 不改写方程区；
- 默认锁定库存、阀门(11)、云/源汇(12)、流率标签、控制面板对象；
- 只移动普通辅助变量(type=10 且非 shadow 且非附着阀门)，只重设**信息箭头**的中间控制点；
- 物理流率管道(thickness>阈值 或 from/to 涉及阀门/云)保持原样；
- 输出文件在 Vensim 重新打开，跑 Check Model 与 Units Check；
- 出现错位/浮动箭头/阀门脱离时立即恢复备份改用手动。

**不要让脚本新建库存、阀门、云或物理流。**

## 6. 自动排版工具用法

依赖：Graphviz（`dot`/`neato`）。macOS `brew install graphviz`；Windows 装 Graphviz 后把 `dot` 加入 PATH。Python 脚本只用标准库。

```bash
# 1. 备份并查看草图对象 ID、坐标、形状、箭头 from/to、控制点
python tools/vensim_autolayout.py inspect model.mdl

# 2. 审计箭头引用是否指向本 view 内有效对象
python tools/vensim_autolayout.py audit model.mdl

# 3. 复制 SFD 配置，填入要锁定的库存/流率名
cp templates/layout_config_sfd.json my_layout.json

# 4. 生成自动排版模型（自动建 .backup.mdl 与 .layout_report.json）
python tools/vensim_autolayout.py layout model.mdl \
  --output model_autolayout.mdl \
  --config my_layout.json \
  --engine dot \
  --route-information-arrows

# 5. 审计输出
python tools/vensim_autolayout.py audit model_autolayout.mdl
```

### 图形策略
- **CLD**：`rankdir=LR`/`TB`，同一回路集中相邻区域；同变量只放一个主节点，跨模块用 shadow variable。
- **SFD 半固定**：锁定库存—流率主骨架；辅助变量按模块分层；收入/成本/政策变量放下方或侧方；只对信息箭头生成弧线。
- **曲线**：普通 Arrow + 1 个中间控制点 = 圆弧。脚本为信息箭头设单控制点；需真正 spline 外观时先在 Vensim 用 `Cmd/Ctrl+Arrow` 建 spline，脚本只调节点位置不改箭头类型。
- **避交叉**：平行边赋相反曲率；远距离边增大弧度，近距离边小弧度；仍严重交叉则分 View，不继续堆曲线。

## 7. 输出质量门槛

- [ ] 每个库存有流入和/或流出
- [ ] 每个流率单位正确
- [ ] CLD 极性与方程方向一致
- [ ] 关键参数有依据
- [ ] Check Model 通过
- [ ] Units Check 通过
- [ ] 极端条件无负库存/无意义爆炸
- [ ] 图、表、方程、正文变量名一致
- [ ] 图中箭头吸附正确对象
- [ ] 自动排版后 .mdl 已在目标 Vensim 版本重新打开验证
- [ ] 最终 ZIP 含 Word/PDF、.mdl、参数/数据、运行说明、必要文献

## 8. 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| 蓝箭头代替流率管道 | 物理流与信息流混淆 | 用 Rate 工具重建流率 |
| 箭头漂浮/反向/穿变量 | from/to 对象 ID 错、控制点格式错 | 还原备份；inspect 查 ID；Vensim 重绘 |
| 移动后阀门与标签分离 | 移动了阀门/流率标签/库存骨架 | 配置锁定这些；只移普通辅助变量 |
| 能运行但结果不合理 | 单位/符号/初值/反馈方向/步长错 | Units Check + 极端条件 + 参数审计 |
| 控制面板失效 | Vensim 版本不支持 IO Controls | 改参数表 + 情景运行流程 |

## 9. 参考资料

详见 `docs/REFERENCES.md`（Vensim 官方 Sketch Format / Arrow Class / Sketch Object Detail / Layout Menu / Check Model，Graphviz splines，PySD 解析器）。实现依据与限制写在该文件。
