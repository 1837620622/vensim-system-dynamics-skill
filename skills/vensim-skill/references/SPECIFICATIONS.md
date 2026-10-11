# JSON 输入格式

## 建模 JSON（`build`）

完整示例见 [inventory_zh.json](../assets/templates/inventory_zh.json)，空白模板见 [model_template.json](../assets/templates/model_template.json)。

| 字段 | 含义 |
| --- | --- |
| `language` | 新建变量的语言，默认 `zh`；用户要求英文时写 `en` |
| `name` | 模型名，也是默认视图名 |
| `time` | `initial`、`final`、`step`、`saveper`、`unit` 五项都要写 |
| `variables` | 变量列表，名字不能重复 |
| `links` | 可选，给已有依赖标极性 `+`/`-` 和延迟 `delay: true`；不能增加方程里没有的关系。不写时 `build` 按方程给变量之间的链接自动标极性，推不出的按基准运行数值求偏导 |
| `feedback_loops` | 可选，`[{name, variables: [按因果顺序], polarity: "R"或"B"}]`，最后一个变量自动连回第一个；核对一致且整条回路在同一视图里时，`build` 画出 R1、B1 … 标记。不写时 `build` 会找出能核对的回路，给出可粘贴的条目 |
| `sketch` | 可选：`font_family`、`font_size`（默认 Vensim Sans SC、12）、`layout_mode`（默认 natural）、`split_shadows`、`seed`、`polarity`（标哪些箭头的极性：`dynamic` 默认，只标变量之间的链接；`all` 连参数链接也标；`declared` 只标 `links` 里写明的） |

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

没有被任何方程使用的变量，Vensim 的 Check Model 会报 USE FLAG。`build` 的处理：没人用的常量直接报错（删掉它，或在需要它的方程里引用）；其他变量自动写上 `~ :SUPPLEMENTARY` 作为输出指标，并在建议里列出。变量上可选 `supplementary: true/false` 显式声明，被引用的变量不能标。只被另一个视图使用的常量，自动移到使用它的视图，报告的 `sketch_decisions.moved_constants` 里列出。

## 照模板写

[ev_adoption_zh.json](../assets/templates/ev_adoption_zh.json) 是完整示范，`build` 会得到零交叉、`check --strict` 零警告、R/B 标记都在回路里的图。照它写：

- 名字：存量 8 个字以内，其他变量 10 个字以内，去掉“的”“量”之类修饰（超出 `build` 会建议精简）。
- 每个存量至少一条流；流量写清 `from`/`to`，边界一端写 `null`；每个变量都有 `unit`。
- 常量写在使用它的视图里；一个常量被多个视图使用时才放在别处，对方视图会生成影子。
- 声明 3–6 条主要回路，`variables` 按因果顺序写；拿不准时先不写，看 `build` 给出的建议再补。
- `links` 只在需要强调或方程推不出符号时写，其余交给 `build` 自动标。
- 建完跑 `visual --strict` 和 `check --strict`；输出里的“建议”逐条处理。

## 实验 JSON（`experiment`）

公共字段：`mode`、`variables`，可选 `time: {time_step, final_time, saveper}`。各 mode 见 [SIMULATION.md](SIMULATION.md#批量实验)，示例见 `inventory_scenarios.json`、`inventory_perturb.json`、`inventory_sensitivity.json`。

## 校准与政策 JSON

`calibrate` 与 `optimize` 的字段见 [SIMULATION.md](SIMULATION.md#参数校准与政策搜索)，空白模板为 [calibration_template.json](../assets/templates/calibration_template.json) 和 [policy_template.json](../assets/templates/policy_template.json)。

## 排版配置

见 [APPEARANCE.md](APPEARANCE.md#配置) 与 [layout_config.json](../assets/templates/layout_config.json)。

## 结果图配置

见 [RESULT_PLOTS.md](RESULT_PLOTS.md#调整样式) 与 [plot_config.json](../assets/templates/plot_config.json)。
