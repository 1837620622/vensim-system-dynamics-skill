# 仿真结果与论文图件

这里处理时间序列和实验结果。模型结构图仍必须从真实 MDL 在 Vensim 原生导出；不能把本模块用于重画 SFD/CLD。

## 默认成图规则

- 每个变量独立出图。多个情景比较同一个变量时画在同一坐标系中；不同量纲的变量不强行拼在一起。
- 默认无标题、图号、图注、图标、工具标记和作者水印。图注写入论文正文。只有用户明确要求时才使用 `graph/compare --title`。
- 白底、细实线网格、完整坐标边框。字体优先 Times New Roman 与宋体；macOS 可用 Songti SC，Windows 可用 SimSun，其他系统可安装 Noto Serif CJK SC。
- 经典图在曲线上重复标记 `1、2、3…`，图下逐行显示“变量 : 情景”与相同编号的长线段。编号落在真实采样点，密集曲线错开编号位置，不错开或改动数据。
- 结果曲线默认使用蓝、朱红、绿、紫、橙等可区分的彩色，单曲线与样本分位带也保留彩色。**结果曲线配色与模型信息箭头配色是两个设置**；黑色/纯蓝箭头约束针对模型结构图。
- 默认 PNG 为 **600 DPI**；可用 `--dpi 1200`。推荐同时保留 **PDF/SVG 矢量版**，矢量图可缩放，不能用“最高 DPI”衡量。SVG 字形转路径，PDF 嵌入字体，避免换电脑后中文缺字。
- 样式随任务调整。少量情景使用经典编号图；大量敏感性运行优先分组或分位带，不为了形式一致挤入超长图例。

绘图只处理实际求解器输出或用户提供的 CSV。不能从参考图片抄出一条“看起来相同”的趋势，也不能通过平滑、改点、删除异常或修改坐标轴制造结论。没有中文字体、数据缺失、时间重复或出现 NaN/Inf 时停止正式出图并说明问题。

## 单次仿真

在 skill 目录执行，Windows 把 `./skill.sh` 换为 `.\skill.cmd`：

```bash
./skill.sh graph work/inventory.mdl --var 库存 --output results/stock.png --formats png,pdf,svg
./skill.sh simulate work/inventory.mdl --var 库存 --output results/base.csv --plot results/base.png --dpi 1200
```

时间轴使用 MDL 的实际时间单位，例如 `Time (Month)`；不因为参考图片写着 Year 就改成 Year。多次传入 `--var` 会分别生成 `stock_01.png`、`stock_02.png` 等，变量与文件对应关系记录在 `stock.plot.json`。

## ±10% / ±20% 单因素敏感性

```bash
./skill.sh experiment work/inventory.mdl --spec assets/templates/inventory_perturb.json --output-dir results/perturb --plot results/perturb/stock.png --formats png,pdf,svg
```

规范示例：

```json
{
  "mode": "perturb",
  "variables": ["库存"],
  "parameters": ["调整时间"],
  "changes": [-0.2, -0.1, 0.2, 0.1]
}
```

模型中的原始常量乘以 `1 + changes`，每次只改变一个参数，最后加入 `current` 基准。多个参数会分别做单因素实验，不会误当成同时变化。零基准无法通过百分比改变，需用 `scenarios` 给绝对参数值。常量以外的反馈方程不能作为参数覆盖。

## 多因素方案比较

```json
{
  "mode": "scenarios",
  "variables": ["库存"],
  "scenarios": [
    {"name": "缩短调整时间", "params": {"调整时间": 2}},
    {"name": "提高目标库存", "params": {"目标库存": 240}},
    {"name": "组合方案", "params": {"调整时间": 2, "目标库存": 240}},
    {"name": "current", "params": {}}
  ]
}
```

使用相同初值、时间设置和后端，比较变量一致。模型文件不同的情景可用 `compare`，但 Agent 必须先核对单位与时间范围，不能仅凭同名变量认定可比。

## Monte Carlo 分位带

```bash
./skill.sh experiment work/inventory.mdl --spec assets/templates/inventory_sensitivity.json --output-dir results/mc --plot results/mc/stock.svg --plot-style band
```

输出中位数、25–75% 与 5–95% 样本区间。必须采用相同保存时间网格；不静默插值。样本来自规范中给定的独立均匀分布，固定种子可以复算。这些区间不是统计置信区间，不表示模型已校准，也不证明预测可靠。

默认 `auto` 在不超过 12 条曲线时用经典编号图；更多曲线只有在运行元数据明确为 `monte-carlo` 时自动用分位带。命名政策、网格或无抽样元数据的 CSV 会提示分组，不静默抹去情景身份。需要汇总已有样本 CSV 时，先确认样本含义，再明确使用 `--plot-style band`。`--plot-style classic` 超过配置上限会提示分组，不会继续挤压图例。

## 按版面调整样式

```bash
./skill.sh graph work/model.mdl --var 指标 --output figures/result.pdf --plot-config my_plot.json
```

以 [plot_config_classic.json](../assets/templates/plot_config_classic.json) 为参考，只提供需要覆盖的字段即可：

```json
{
  "width_inches": 8.2,
  "plot_height_inches": 3.8,
  "font_size": 10,
  "legend_font_size": 9,
  "line_width": 0.8,
  "marker_size": 6,
  "marker_repeats": 5,
  "legend_repeats": 4
}
```

还可调整 `font_family`、`legend_row_inches`、`colors`、`grid_color`、`border_color`、`number_lines` 和 `max_classic_curves`。空字体列表表示按平台选择；指定字体必须已安装。默认阈值用于保证可读性，具体版面应按变量名长度、曲线密度和目标文献规范调整。像素总量设有资源保护，不通过无限提高 DPI 获得清晰度。

经典编号线来自 [Vensim 的 Number lines 机制](https://www.vensim.com/documentation/20736.html)，PLE 也可开关编号。参考图属于一种可用的论文风格，并不是所有期刊的统一标准；例如 [Nature 的最终图件说明](https://www.nature.com/nature/for-authors/final-submission) 偏好一致的无衬线字体和矢量线稿。先遵守用户指定的参考样式与目标出版要求，再选择配置。

默认彩色；单条曲线也使用 `colors` 中的第一种颜色。分位带采用同色深浅层次，`band_outer_alpha`、`band_inner_alpha` 控制透明度。`show_grid` 控制网格，`line_styles` 在实线、虚线、点划线和点线之间逐情景循环，图例与曲线同步。编号按字形边界在实际采样点上避让，密集处可少标；不插值、不平移数据来制造差异。具体文献依据和方程配套要求见 [学术表达与文献依据](ACADEMIC_PRESENTATION.md)。

## 从现有结果 CSV 出图

```bash
./skill.sh plot-data results/perturb/series.csv --var 库存 --output figures/stock.png --time-unit Month --formats png,pdf,svg
```

支持两种明确的 CSV 结构：

| 形式 | 列 | 用途 |
| --- | --- | --- |
| 宽表 | `Time,库存,补货,...` | 单次仿真，每一行一个时间点 |
| 长表 | `Scenario,Time,Variable,Value` | 多情景实验，每一行一个变量、情景和时间点 |

默认 UTF-8/BOM；旧中文文件可用 `--encoding gb18030`。列名、数值类型、时间严格递增、各变量采样网格及缺失值都检查。CSV 本身没有时间单位时由 `--time-unit` 明确给出。Vensim 的转置表、TAB 或其他导出格式应先核对字段再转换，不能宣称支持所有原生导出格式。

## 可追溯与验收

每组图件有 `.plot.json`：记录变量、文件、图件哈希、原模型或 CSV 哈希、参数、后端、时间单位、DPI、样式与是否原生核验。实验另存完整 `series.csv`、摘要 `summary.csv` 和 `experiment.json`。元数据不印在图面上。

1. 对照方程、量纲、边界、守恒、初值和预期行为。
2. 使用 `convergence` 检查时间步长敏感性；支持范围内可与 PySD 或原生 Vensim 做同条件对照。
3. 查看真实导出文件，检查中文、图例、刻度、编号、线条和留白；不得仅凭导出成功宣称美观。
4. 文件与结论对应；不截断异常区间，不以局部放大掩盖差异，不把诊断替代值当作真实结果。

Python 在 PLE 外提供情景、网格、单因素扰动、Monte Carlo、[参数校准与政策优化](ADVANCED_ANALYSIS.md) 和结果绘制。校准及政策搜索输出的 `comparison.csv` 可以直接用 `plot-data` 绘图。它不解锁 DSS，不包含 DSS 全部函数、编译模型、外部函数接口或完整原生优化器。PLE、PySD 和各依赖仍使用各自许可。
