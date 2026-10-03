# 建模与实验规范

规范让 Agent 先表达模型意图，再调用确定性工具。公式、单位、参数和边界由任务资料确定，不能用示例值替代研究证据。

## 建模 JSON

入口：`build spec.json --output work/model.mdl`。完整可运行示例见 [inventory_zh.json](../assets/templates/inventory_zh.json)。

| 字段 | 含义 |
| --- | --- |
| `language` | 新建业务变量的语言，默认 `zh`；用户指定英文时用 `en` |
| `name` | 模型的简短名称，用于视图命名 |
| `time.initial / final` | 起止时刻，数值有限，终点不早于起点 |
| `time.step / saveper` | 积分步长与保存间隔，均为正数 |
| `time.unit` | 实际时间单位，不猜测为 Year；所有 time 字段必须显式提供 |
| `variables` | 变量对象列表，变量名唯一 |
| `links` | 可选的直接依赖极性与时滞标记，不能增加方程中不存在的关系 |
| `feedback_loops` | 可选回路列表，每项为 `name`、有向路径 `variables` 与可选 `polarity: R/B`，不含重复首尾节点 |
| `sketch.font_family / font_size` | 可选原生字体设置；默认 Vensim Sans SC、12 pt，按实际平台和长变量名调整 |
| `sketch.layout_mode` | 新建默认 `circular`；可选 `refine` 局部避让或 `preserve` 保留初始位置，只整理圆弧 |
| `sketch.circular_gap / circular_aspect` | 可选环形留白和高宽比，有限正数；默认 64 草图单位和 0.8，半径从实际文字框计算 |
| `sketch.node_spacing` | 可选节点避让间距，有限正数，默认 24 草图单位 |

变量的公共字段为 `name`、`kind`、`unit`。生成器使用无引号的简单名称：以字母或中文等国际文字起头，后续支持字母、数字、空格、下划线和美元符号；不能含运算符、控制分隔符或需要原生引号的特殊字符。已有模型的带引号名称仍按读取支持范围处理，不为通过新建校验而擅自改名。

| kind | 必需内容 | 语义 |
| --- | --- | --- |
| `stock` | `initial` | 初值表达式；流向生成 `INTEG` 的净流入 |
| `flow` | `equation`、`from`、`to` | `from/to` 指向存量，单端 `null` 表示边界源或汇 |
| `aux` | `equation` | 辅助方程 |
| `constant` | `equation` | 明确的有限数值字面常量；派生表达式使用 aux |

存量、辅助量和常量可以指定 `position: [x, y]`。坐标是原生 MDL 草图坐标，不是网页像素坐标；存量坐标用于确定骨架，流量阀门和附着文字由管道生成。不要给 `flow` 指定独立位置使文字与阀门脱离。

指定位置优先于环形和局部排版。相互冲突的人工坐标会保留并导致几何报告 `pass: false`，不会静默移动。所有坐标和数值样式拒绝布尔值；`routing_passes` 必须为整数，不能使用浮点数伪装次数。

`links` 每项使用 `from`、`to`、可选 `polarity`（`+`、`-`、`S`、`O`，兼容小写）和 `delay`。极性应来自公式与领域解释，非单调作用不能凭经验标正负。`links` 与 `feedback_loops` 都必须为列表；空白规范是空列表。新建会拒绝可确定的符号冲突，未覆盖的表达式在报告中列为待核对。初值引用也需要原生草图关系检查，但不能用来组成动态回路。算法和符号位置见 [反馈核对](FEEDBACK.md)。

生成器面向明确、简单的标量 SFD，不负责推断因果、自动校准、共享复杂管道或自动创建多 View。结构过复杂时使用已有 MDL 加局部修订，或在原生 Vensim 中组织视图。

[model_template.json](../assets/templates/model_template.json) 是不含业务数值的空白入口；需要先填写，空值会被验证器拒绝。[model_spec_template.json](../assets/templates/model_spec_template.json) 是研究论证清单，供 `academic` 使用，不是 `build` 的输入格式。

## 实验 JSON

公共字段：`mode`、`variables`，可选 `time: {time_step, final_time, saveper}`。变量必须存在且不能重复，时间设置和参数必须有限。

| mode | 参数字段 | 运行含义 |
| --- | --- | --- |
| `scenarios` | `scenarios: [{name, params}]` | 每个命名情景独立运行 |
| `perturb` | `parameters: [参数名]`、`changes: [相对变化]` | 一个参数一次变化，并加入原基准 |
| `grid` | `parameters: {参数名: [离散值]}` | 笛卡尔积 |
| `monte-carlo` | `parameters: {参数名: [下限, 上限]}`、`samples`、`seed` | 有界独立均匀抽样 |

`changes`、`samples`、`seed` 必须在相应模式中显式提供，不默认沿用参考图的百分比或其他实验设置。最多 200 次运行，输出最多 200 万个变量值；这些是资源保护上限，不是推荐的实验规模。实验不修改模型，每次都重新初始化。未知常量、覆盖反馈方程、不合理保存网格和输出冲突会报错。

## 校准与政策 JSON

独立入口为 `calibrate` 与 `optimize`，不属于 `experiment` 的 mode。规范、字段与统计定义见 [Python 高级分析](ADVANCED_ANALYSIS.md)，空白文件分别为 [calibration_template.json](../assets/templates/calibration_template.json) 与 [policy_template.json](../assets/templates/policy_template.json)。输入必须来自当前模型与资料；空模板不能自动补成演示值。

## 布局配置

从 [SFD 配置](../assets/templates/layout_config_sfd.json) 或 [CLD 配置](../assets/templates/layout_config_cld.json) 开始，按实际图面修改；不要机械复制示例中的变量名或坐标。

- `lock_object_ids` 适合同名影子实例，`lock_node_names` 适合锁定该名字的全部实例。
- `node_positions` 只用于指定名字在选定 View 中唯一且可移动的实例。歧义必须明确，不能猜测用户希望移动哪个影子。
- `move_shadows` 默认开启独立避让，不能改变影子身份；关闭时将冲突留给人工处理并报告。
- `layout_mode` 取 `circular`、`refine`、`preserve`、`auto`。对已有 MDL 默认 `refine`；新建 `build` 默认 `circular`。旧配置中的 `graphviz` 只作为迁移别名，不调用外部布局器。`style` 默认保留，黑色用 `monochrome`，纯蓝用 `native-blue`。
- `circular_gap` 与 `circular_aspect` 分别调节环形留白和高宽比，默认值同建模 JSON；环的实际半径依据当前文字框和节点数计算。
- `shadow_visibility` 取 `preserve`、`orphan` 或 `all`。前两者不隐藏有合法出线的影子；`all` 只在用户明确要求减少当前 View 杂乱时使用，并按照 Vensim Hide Depth 同时隐藏该影子的出入箭头。隐藏不改变方程、变量身份或拓扑。
- `clearance`、`node_spacing`、圆弧强度和上下限按字体、长变量名、反馈跨度调整，不是美观的固定公式。

未知路由保持几何并报告；明确选择颜色时只改颜色字段。圆弧与原生文字边界之间仍可能存在渲染差异，最终以原生图面为准。
