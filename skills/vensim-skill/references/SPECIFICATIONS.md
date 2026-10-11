# JSON 输入格式

## 建模 JSON（`build`）

完整示例见 [inventory_zh.json](../assets/templates/inventory_zh.json)，空白模板见 [model_template.json](../assets/templates/model_template.json)。

| 字段 | 含义 |
| --- | --- |
| `language` | 新建变量的语言，默认 `zh`；用户要求英文时写 `en` |
| `name` | 模型名，也是默认视图名 |
| `time` | `initial`、`final`、`step`、`saveper`、`unit` 五项都要写 |
| `variables` | 变量列表，名字不能重复 |
| `links` | 可选，给已有依赖标极性 `+`/`-` 和延迟 `delay: true`；不能增加方程里没有的关系 |
| `feedback_loops` | 可选，`[{name, variables: [按因果顺序], polarity: "R"或"B"}]`，最后一个变量自动连回第一个 |
| `sketch` | 可选：`font_family`、`font_size`（默认 Vensim Sans SC、12）、`layout_mode`（默认 natural）、`split_shadows`、`seed` |

变量：

| kind | 必填 | 说明 |
| --- | --- | --- |
| `stock` | `initial` | 由流向自动生成 `INTEG(流入 - 流出, 初值)` |
| `flow` | `equation`、`from`、`to` | `from`/`to` 写存量名，边界一端写 `null` |
| `aux` | `equation` | 辅助方程 |
| `constant` | `equation` | 必须是有限数值；派生表达式用 aux |

每个变量都要写 `unit`。可选 `view` 指定所在视图（子系统名），不写的都在第一个视图；流量与它两端的存量必须在同一个视图，引用其他视图的变量时自动生成影子。

`position: [x, y]` 会把变量固定在该坐标，不参与排版。一般不要写，只在审图后需要固定个别变量时使用。

名字以字母或汉字开头，可含字母、数字、空格、下划线和 `$`；不含运算符和需要引号的特殊字符。

## 实验 JSON（`experiment`）

公共字段：`mode`、`variables`，可选 `time: {time_step, final_time, saveper}`。各 mode 见 [SIMULATION.md](SIMULATION.md#批量实验)，示例见 `inventory_scenarios.json`、`inventory_perturb.json`、`inventory_sensitivity.json`。

## 校准与政策 JSON

`calibrate` 与 `optimize` 的字段见 [SIMULATION.md](SIMULATION.md#参数校准与政策搜索)，空白模板为 [calibration_template.json](../assets/templates/calibration_template.json) 和 [policy_template.json](../assets/templates/policy_template.json)。

## 排版配置

见 [APPEARANCE.md](APPEARANCE.md#配置) 与 [layout_config.json](../assets/templates/layout_config.json)。

## 结果图配置

见 [RESULT_PLOTS.md](RESULT_PLOTS.md#调整样式) 与 [plot_config.json](../assets/templates/plot_config.json)。
