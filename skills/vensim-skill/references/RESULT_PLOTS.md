# 仿真结果图

这里只画仿真曲线。模型结构图要从 Vensim 原生导出，不能用本模块重画。需要 Vensim 自己画的结果图时，用 `vensim-script` 生成运行脚本和自定义图，见下文“Vensim DSS 原生结果图”。

## 默认样式

默认样式对齐 Vensim DSS 的输出图和中文系统动力学论文常见插图，同时遵循 Rahmandad 与 Sterman（2012）的建议：图里只放要讨论的内容，不要默认网格线和装饰；同一张图里的曲线在彩色和黑白打印下都能分清。

| 项目 | 默认 |
| --- | --- |
| 尺寸 | 6.3 × 3.8 英寸（约 16 cm 宽，适合单栏论文） |
| 坐标框 | 白底、四边黑色细框，刻度朝内，无网格 |
| 标题 | 无。图题和图号写在正文或题注里，只有明确要求时才用 `--title` |
| 曲线 | 每个情景一条线，Vensim 式数字编号（1、2、3 …）标在真实采样点上并错开位置，颜色和线型（实线、虚线、点划线、点线）同时区分 |
| 配色 | Vensim 默认序列色 `#0000FF #FF0000 #008000 #808080 #C000C0 #000000` |
| 图例 | 不超过 6 条曲线时放在图内空白处，更多时放在图下两列；单条曲线不加图例 |
| 坐标轴 | 横轴 `Time (单位)`，单位取自模型，`x_label` 可改成“时间（年）”之类；纵轴是变量名，有单位时写成 `变量 (单位)`；时间为整数（年份）时只用整数刻度，纵轴不用偏移量和科学计数 |
| 字体 | 西文 Times New Roman，中文宋体（Windows 的 SimSun，macOS 的 Songti SC）；逐字检查字形，缺字时报错 |
| 输出 | PNG 300 dpi（`--dpi` 可改为 600），可加 `--formats png,pdf,svg` 同时导出矢量版 |

不要编号时设 `number_lines: false`；要 2.4 以前的色觉友好配色，把 `colors` 写成 `["#0072B2", "#D55E00", "#009E73", "#E69F00", "#CC79A7", "#56B4E9"]`。曲线超过 8 条时，命名情景请分组出图；蒙特卡洛样本会自动改画 5–95% 和 25–75% 分位带加中位线。分位带描述的是抽样分布，不是置信区间。

## 命令

```bash
./skill.sh graph model.mdl --var 库存 --output figures/stock.png --formats png,pdf,svg
./skill.sh compare base.mdl --scenario policy.mdl --var 库存 --output figures/compare.png
./skill.sh experiment model.mdl --spec perturb.json --output-dir results/perturb --plot figures/perturb.png
./skill.sh plot-data results/perturb/series.csv --var 库存 --time-unit Month --output figures/perturb.pdf
```

多个 `--var` 时每个变量单独出一张图（`stock_01.png`、`stock_02.png`），对应关系写在同名 `.plot.json` 里，清单还记录每个文件的 SHA-256、使用的字体和全部运行参数。PDF 和 SVG 去掉了生成时间，同一份数据重复导出得到相同文件。

## 单因素敏感性示例

```json
{
  "mode": "perturb",
  "variables": ["库存"],
  "parameters": ["调整时间"],
  "changes": [-0.2, -0.1, 0.1, 0.2]
}
```

原始常量乘以 `1 + change`，一次只改一个参数，最后加入基准 `current`。基准为 0 的参数不能按百分比扰动，请用 `scenarios` 给出绝对值。

## 调整样式

把 [plot_config.json](../assets/templates/plot_config.json) 里需要改的字段写进自己的 JSON，用 `--plot-config` 传入。可调：`width_inches`、`height_inches`、`font_family`、`font_size`、`legend_font_size`、`line_width`、`line_styles`、`colors`、`max_lines`、`number_lines`、`marker_size`、`marker_repeats`、`show_grid`、`grid_color`、`border_color`、`x_label`、`integer_time_ticks`、分位带透明度。未知字段和越界数值会报错。

期刊有明确的图件要求时以期刊为准，例如 Nature 要求矢量线稿和统一字体。

## 数据来源

只画求解器的真实输出或用户提供的 CSV，不平滑、不删点、不改坐标轴来制造结论。CSV 旁边的 `.run.json` 或 `experiment.json` 会被一起读取：哈希不符、诊断结果、时间单位冲突时停止出图。

## Vensim DSS 原生结果图

Python 画的图只是近似 Vensim 的样子。论文要求“Vensim 仿真结果图”时，生成 Vensim 能直接执行的文件，在 Vensim DSS 里出图：

```bash
./skill.sh vensim-script model.mdl --spec assets/templates/inventory_scenarios.json --output-dir results/dss
```

情景沿用 `experiment` 的 JSON（`scenarios`、`perturb`、`grid`；蒙特卡洛请用 Vensim 的 Sensitivity 工具），出图变量取 `--var`，不写时用 JSON 里的 `variables`。输出目录里有：

| 文件 | 内容 |
| --- | --- |
| `scenario_XX.cin` | 每个情景的参数变更，一行一个 `常量 = 数值`；参数为空的情景当作基准，不生成文件 |
| `graphs.vgd` | 自定义图，每个变量一张（G01、G02 …），叠加全部运行，带单位 |
| `run_all.cmd` | 命令脚本：载入模型，逐个读入 `.cin`、设运行名并运行，最后载入 `graphs.vgd` |
| `README.txt` | 在 Vensim DSS 中的操作步骤和运行名对照 |

文件使用 CRLF 换行，路径相对输出目录写出；默认 UTF-8 编码，旧版 Vensim 在中文 Windows 上读不出中文名时加 `--encoding gbk`。命令脚本需要 Vensim DSS 或 Pro，PLE 不能执行。

未在原生 Vensim 中逐版本验证（本仓库的测试环境没有 Vensim）：命令 `SPECIAL>LOADMODEL`、`SIMULATE>READCIN`、`SIMULATE>RUNNAME`、`MENU>RUN|O`、`MENU>VGD` 和 `.vgd` 的 `:GRAPH`/`:VAR`/`:DATASET`/`:LABEL`/`:UNITS` 写法取自 Vensim 帮助文档，首次使用请在自己的 Vensim 版本里跑一遍，并核对每次运行之前上一情景的参数变更已经清除。
