# 仿真结果图

这里只画仿真曲线。模型结构图要从 Vensim 原生导出，不能用本模块重画。

## 默认样式

样式按 Rahmandad 与 Sterman（2012）对仿真结果图的建议设定：图里只放要讨论的内容，不要默认网格线和装饰；同一张图里的曲线在彩色和黑白打印下都能分清。

| 项目 | 默认 |
| --- | --- |
| 尺寸 | 6.3 × 3.8 英寸（约 16 cm 宽，适合单栏论文） |
| 坐标框 | 四边细框，刻度朝内，无网格 |
| 标题 | 无。图题和图号写在正文或题注里，只有明确要求时才用 `--title` |
| 曲线 | 每个情景一条线，颜色和线型同时区分（实线、虚线、点划线、点线循环） |
| 配色 | `#0072B2 #D55E00 #009E73 #E69F00 #CC79A7 #56B4E9`，色觉障碍可分 |
| 图例 | 不超过 6 条曲线时放在图内空白处，更多时放在图下两列；单条曲线不加图例 |
| 坐标轴 | 横轴 `Time (单位)`，单位取自模型；纵轴是变量名，有单位时写成 `变量 (单位)` |
| 字体 | 西文 Times New Roman，中文宋体（Windows 的 SimSun，macOS 的 Songti SC）；逐字检查字形，缺字时报错 |
| 输出 | PNG 600 dpi，可加 `--formats png,pdf,svg` 同时导出矢量版 |

`number_lines: true` 可切换成 Vensim 风格的数字编号曲线，编号只标在真实采样点上，各曲线错开位置。曲线超过 8 条时，命名情景请分组出图；蒙特卡洛样本会自动改画 5–95% 和 25–75% 分位带加中位线。分位带描述的是抽样分布，不是置信区间。

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

把 [plot_config.json](../assets/templates/plot_config.json) 里需要改的字段写进自己的 JSON，用 `--plot-config` 传入。可调：`width_inches`、`height_inches`、`font_family`、`font_size`、`legend_font_size`、`line_width`、`line_styles`、`colors`、`max_lines`、`number_lines`、`marker_size`、`marker_repeats`、`show_grid`、`grid_color`、`border_color`、分位带透明度。未知字段和越界数值会报错。

期刊有明确的图件要求时以期刊为准，例如 Nature 要求矢量线稿和统一字体。

## 数据来源

只画求解器的真实输出或用户提供的 CSV，不平滑、不删点、不改坐标轴来制造结论。CSV 旁边的 `.run.json` 或 `experiment.json` 会被一起读取：哈希不符、诊断结果、时间单位冲突时停止出图。
