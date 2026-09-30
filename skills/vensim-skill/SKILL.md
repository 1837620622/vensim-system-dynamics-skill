---
name: vensim-skill
description: "Use for Vensim modeling with circular feedback layouts by default, native MDL and shadow-variable repair, polarity and loop checks, Chinese diagrams, reproducible simulation, calibration, policy optimization, sensitivity analysis, and colored Python publication figures on Windows, macOS, or Linux."
license: "Non-commercial; see LICENSE"
---

# Vensim 建模、排版与仿真

从模型结构和方程出发，整理真实 `.mdl`，运行可复现的仿真，再回到 Vensim 检查。工具是独立项目，不代表 Ventana 官方产品、认证或完整求解器。

## 不可跳过的约束

1. **最终结构图只能来自交付的真实 MDL 在 Vensim 原生打开后的导出或截图。** 不得用 Graphviz 图、另画的 SVG/HTML、图片生成、虚构示范图替代。Graphviz 只能提议节点位置，不能把它的边样条当成 Vensim 圆弧。`preview` 是几何调试材料，永远不算最终模型图。没有原生环境时交付 MDL 和待检报告，明确说明尚未原生核验。
2. **新建模型默认中文业务变量。** 用户指定英文时使用英文；Vensim 函数、控制变量保持原生英文。修改已有模型时保留变量名，不能为了中文化破坏公式、CSV、脚本与变量之间的对应关系。
3. **新图默认统一黑色信息箭头 `0-0-0`；可选原生纯蓝 `0-0-255`。** 禁止把深蓝、墨色、渐变、透明度或多种装饰色当作“学术风格”。已有图默认保留用户样式；用户要求统一颜色时明确选 `monochrome` 或 `native-blue`。原生实体流量用黑色双线管道，不能画成普通信息线。
4. **图内只放解释模型必需的内容。** 不新增工具名称、AI说明、版本字样、调试 ID、图注段落、作者水印、无依据的 R/B 圈或装饰边框。来源、单位表、假设、审计结果、作者和许可证写在图外的文档中。已有必要的正负极性、时滞标记、业务注释不能为了整洁而抹掉；删除已有文字前先确认其用途。
5. **美化不能改模型含义。** 不改方程、变量类型、箭头端点、因果极性、延迟标记和影子身份；不能靠删边、隐藏冲突、加零系数或伪造反馈来消除警告。布局输出必须使用新文件，与输入方程区逐字节一致，并检查拓扑不变量。
6. **软件检查通过不等于图面合格。** 必须看原生图；仿真成功不等于研究有效。几何报告的 `pass` 与 `native_verified` 分开处理，禁止把未做的检查填写为通过。
7. **通用流程不固定业务参数和布局模板。** 时间范围、时间单位、初值、参数、情景幅度、抽样次数、种子和坐标依据当前任务确定。示例只用于演示与回归，不能复制其中的变量、公式或数值来填补资料空缺；旧案例不参与新任务的规则选择。缺少必要资料时列明缺项，不能自动补成示例值。局部排版、模块位置、圆弧和图件尺寸按实际内容调整，禁止随机抖动或机械套图。可配置的显示默认值与资源保护上限不代表业务假设。
8. **仿真结果图默认使用 Python。** 默认无标题、图号、图标与水印，优先清晰的独立图件。少量情景可用经典编号曲线与底部图例；复杂实验按分析目的分组或使用分位带。样式可以配置，数据不能为了美观改写。结构图继续遵守第 1 条的真实 MDL 原生来源约束。
9. **字体必须实际可用。** `doctor` 先检查 Python 绘图环境，出图时按真实文字检查字形；缺字必须处理。MDL 在原生 Vensim 核对中文、字号、长名称与影子括号，字体替换后重新检查碰撞。Python 字体正常不能当作原生字体正常的证据。
10. **新建 MDL 默认采用环形布局。** 主要反馈链按实际连接沿环展开，外围参数靠近作用对象，独立模块分别组织；SFD 的存量、阀门和管道保留可读的骨架，在其外侧展开反馈弧段。不得为凑圆形增删关系、画装饰圆圈或把所有参数塞进同一个大圆。留白、环的宽高和局部位置按内容调整；用户指定布局或人工坐标时优先遵守。现有图的局部修复保留 `refine`，明确重排为环形时使用 `circular`。

11. **逐条核对正负极性与整条回路性质。** 箭头 `+/-` 与回路 `R/B` 分开判断；依据动态方程和适用域，不能用名称、相关性、曲线上升或顺逆时针代替。正负号靠近所属箭头的目标端空白侧，R/B 仅放在已核对回路的内部留白，不压字、不遮线。初值引用不构成动态反馈。未知非线性报告待核对，不硬填符号。原生符号位置必须实际检查。

12. **仿真结果图默认彩色。** 单曲线、情景比较和样本分位带都使用统一的科研配色；图例、编号、线型与曲线同步，不为“高级感”添加渐变背景或装饰。模型结构图仍执行黑色／纯蓝原生箭头规则。数据保持真实，密集编号通过采样点上的位置选择避让。

## 先看哪些文件

- 日常建模和命令：[操作手册](references/OPERATIONS_GUIDE.md)。
- 外观返工、弧线、影子重叠：[图面规则](references/APPEARANCE.md)，执行排版任务前必须阅读。
- 箭头正负、增强/平衡回路与符号位置：[反馈核对](references/FEEDBACK.md)。
- 论文、政策或学术结论：[研究与交付检查](references/RESEARCH_WORKFLOW.md)。单纯整理已有图不强行要求重做整份研究。
- Python 结果图、编号图例、DPI 和敏感性：[结果图手册](references/RESULT_PLOTS.md)。
- 对照文献完善流图、方程与仿真图：[学术表达与文献依据](references/ACADEMIC_PRESENTATION.md)。按当前模型选择，不照抄文献参数或把一种版式写死。
- PLE 外的参数校准、有约束政策搜索：[Python 高级分析](references/ADVANCED_ANALYSIS.md)。只有明确观测数据、目标或约束时使用；搜索结果不能称为已证明的全局最优。
- 准备建模或实验 JSON：[输入规范](references/SPECIFICATIONS.md)。
- 官方格式、版本、Graphviz 和 PySD 边界：[实现依据](references/REFERENCES.md)。
- 仿真函数、名称、Lookup、时间网格与结果清单：[仿真语义边界](references/SIMULATION_SEMANTICS.md)，改变求解器或涉及延迟、脉冲、查表时阅读。
- Agent 接入：[MCP 使用边界](references/MCP.md)。MCP 可选，CLI 本身不依赖 MCP。

## 工作顺序

1. 读取实际目录、原模型、所有 View、方程和用户给出的示意或截图。截图只用来定位问题，不能据此猜测缺失方程。运行 `doctor`、`inspect`、`audit`、`check`，记录当前可用的原生 Vensim、Python、Graphviz 和可选后端。
2. 将问题分为模型语义、文字/影子重叠、圆弧/穿线、样式、仿真行为。先处理错误引用、无依据的关系与缺失初值，再美化。对不支持的格式保留原文并报告。
3. 新建模型先按环形组织主要反馈链，`build` 默认执行 `circular`。存量和流量是骨架，外围参数靠近受影响的流率或辅助量；不同模块分别展开。不要把全图机械铺满固定网格，也不要加入随机抖动伪装手绘。
4. 修改已有图时，从 `--mode preserve`（只整理圆弧）或默认 `refine`（局部避让）开始；明确需要环形重排时选 `circular`。`graphviz` 或 `auto` 是其他位置建议方式。使用 `node_positions` 固定人工审图后确定的辅助节点位置，其他节点会避开锚点；新建 JSON 的 `position` 不会被排版阶段移动。管道、阀门和流量文字保持锁定。
5. 运行 `visual --strict --max-crossings 0`，逐项看报告中的对象 ID。重叠和穿字必须解决。交叉优先通过就近移动、调整圆弧两侧、拆分子系统视图来消除；非平面关系确实不可避免时在报告中解释，不能虚报零交叉。
6. 打开输出 MDL，在原生 Vensim 核对每个 View，执行 `Check Model`、`Units Check`。查看是否有软件新补的影子或箭头、文字框变化、管道反向、阀门标签脱离。必要时局部修订，重新保存再打开检查。
7. 按任务运行基准、情景、敏感性或步长收敛检查；保存 CSV、参数、种子、模型哈希、后端和实际时间设置。使用原生求解器时记录实际版本和运行结果；不能将 PySD 或内置引擎的结果称为原生运行。
8. 从同一已检查 MDL 原生导出结构图；Python 可以画时间序列与实验结果。交付对应 MDL、数据、必要图件和检查记录，排除几何调试预览和临时仿真缓存。

## 影子变量专项规则

- Shadow 是同一变量在图中的引用，不能按名称合并所有图形对象。以 `(View, object ID)` 跟踪每个实例，保留它的原变量名、引用身份和独立坐标。
- 影子只允许出线，不能接收入线。错误入线先回查方程和定义对象；不直接删线来让检查通过。
- 默认 `refine` 会对可移动影子实例避让，保留其 `bits`、隐藏层级和端点。库存的影子可移动，真正接管道的库存仍锁定。`move_shadows: false` 或显式锁定可保护人工位置。
- 检查影子与定义变量、其他影子、流量标签和管道的碰撞；同名不等于同一个对象。为 `<名称>` 留出实际宽度，字体变化后重新检查。
- 保留合法的影子引用。灰色 `<名称>` 本身不是错误；自动补出的影子常说明 Defined 变量缺少可见原因，需要调查。不得强写 `27:64`、隐藏全部影子、截图遮挡或删除真实关系来获得干净图面。
- 同一变量在同一 View 只能有一个 Defined 实例，其他重复实例应核实是否为 Shadow；跨 View 的定义与引用要与同一份方程一致。

## 常用入口

以下命令在 skill 目录执行；Windows 将 `./skill.sh` 换为 `.\skill.cmd`。所有平台也可直接运行 `python scripts/skill_cli.py`。需要 Python 3.10+。`model.json`、`experiment.json` 和“目标变量”都要替换为当前工程实际内容；示例文件不会自动成为建模输入。

```bash
./skill.sh doctor
./skill.sh build model.json --output work/model.mdl
./skill.sh audit work/model.mdl
./skill.sh check work/model.mdl
./skill.sh feedback work/model.mdl --spec model.json --strict
./skill.sh layout work/model.mdl --output work/model_layout.mdl --mode refine --style monochrome
./skill.sh visual work/model_layout.mdl --strict --max-crossings 0
./skill.sh simulate work/model_layout.mdl --var 目标变量 --output results/base.csv
./skill.sh simulate work/model_layout.mdl --backend pysd --var 目标变量 --output results/pysd.csv
./skill.sh experiment work/model_layout.mdl --spec experiment.json --output-dir results/scenarios
./skill.sh convergence work/model_layout.mdl --var 目标变量 --output results/convergence.json
./skill.sh calibrate work/model_layout.mdl --spec calibration.json --data observations.csv --output-dir results/calibration
./skill.sh optimize work/model_layout.mdl --spec policy.json --output-dir results/policy
```

建模 JSON 必须明确单位、流向、方程与存量初值。未知参数不能编造为研究事实。内置引擎支持常见标量子集及 Euler 积分；复杂数组、宏、外部数据、高级函数交给支持它们的 PySD 或原生 Vensim。`units` 只查缺失单位，不是量纲推导。`--keep-going` 产生诊断结果，不能用于论文结论。

建模入口必须显式提供全部时间设置。结果图用 `--plot-config` 读取可调整的字体、尺寸、线宽和编号密度；不能把某篇论文的版式当作所有任务的标准。用户指定期刊或版面时，先核对其当前要求。

重新读取 CSV 时保留相邻运行清单；哈希不符、诊断状态或时间单位冲突须处理。无清单或旧清单缺少哈希时明确记录来源未验证。删除清单不能作为把诊断数据变成正式数据的方式。离散脉冲与延迟的非网格边界应按实际后端复核，不以另一后端运行成功证明原生等价。

`fix` 必须明确选择修复项：`--units-map units.json` 使用有依据的单位，`--drop-broken-arrows` 仅在确认断裂记录不应保留后使用；禁止把所有缺失单位补成 `Dmnl`。

## 工程与发布要求

- 路径以工程目录为基准；输入支持 UTF-8/BOM、GB18030 等，布局保留原编码、换行和方程字节。遇到未知格式停止猜测。
- 不通过 shell 拼接模型内容，不对方程执行 Python `eval`；可选 MCP 只在指定工作目录提供固定工具，不允许任意命令执行。
- 主输出、运行报告和图件清单一起预检，拒绝覆盖源文件、已有文件或断裂符号链接。重复运行使用新名称或新目录；不得为了重跑而自动删除用户已有结果。
- 修改解析器、路由或引擎后运行对应回归及跨平台测试；重点覆盖真实圆弧、反向信息线、影子重叠、重复 ID、续行编码、状态初始化、物料延迟守恒与路径越界。
- 提交前检查文档与代码的一致性。不要承诺“任意 AI 完全兼容”“自动零交叉”“全函数支持”或“官方 MCP 已接入”，除非有相应实测证据。
- 作者署名及非商业许可保留在 README/LICENSE 和分发包内；不把授权声明压在模型图上。禁止商业使用，详见 [LICENSE](LICENSE)。
