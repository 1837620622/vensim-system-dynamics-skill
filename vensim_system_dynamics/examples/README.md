# 示例模型集合

本目录提供覆盖系统动力学经典结构的**通用模板**，每个 `.mdl` 都是一个闭环、单位正确、可直接套用到自己作业/研究的范式模型。可在 Vensim PLE 打开运行 `Check Model` 与 `Units Check`，也可用纯 Python 引擎仿真。

模板分两类：**经典结构范式**（通用积木，适合学习与套用）和**应用场景示范**（具体业务，适合参考建模思路）。

## 经典结构范式（通用模板）

| 文件 | 范式 | 覆盖功能 | 动态行为 | 适用场景 |
|---|---|---|---|---|
| `first_order_negative_feedback.mdl` | 一阶负反馈 | 目标追赶、调节回路 | 指数趋近目标 | 恒温器、库存补货、目标管理 |
| `first_order_positive_feedback.mdl` | 一阶正反馈 | 复利、指数增长 | 指数增长/衰减 | 资本积累、人口增长、复利 |
| `second_order_oscillation.mdl` | 二阶振荡 | 带延迟的负反馈、STEP 冲击 | 衰减振荡 | 库存-劳动力调整、供应链振荡 |
| `aging_chain.mdl` | 老化链/多级流水 | 多级 INTEG 串联、物料分批流动 | 分布滞后、年龄结构演化 | 人口年龄结构、产品批次、在制品 |
| `sir_epidemic.mdl` | SIR 传染病 | 正反馈转负反馈、存量转移 | 爆发-峰值-消退 | 流行病、谣言扩散、创新采纳 |
| `depreciation.mdl` | 折旧/衰减稳态 | 一阶负反馈收敛 | 收敛到稳态 | 设备折旧、资产评估、库存衰减 |

## 应用场景示范

| 文件 | 类型 | 覆盖功能 | 闭环结构 |
|---|---|---|---|
| `population_demo.mdl` | SFD | 基础库存—流率 + 承载力 + 拥挤效应 | Population → Crowding → Birth Fraction → Births → Population（负反馈） |
| `cld_customer_loop.mdl` | CLD | 纯因果回路、正负反馈、信息箭头 | Customers ↔ Word of Mouth（正）、Customers ↔ Churn（负）、Satisfaction 调节 |
| `delay_structure.mdl` | SFD | `DELAY1` 物料延迟、管道库存 | Order → Pipeline → Delivery → Inventory → Sales（链式闭环） |
| `smooth_structure.mdl` | SFD | `SMOOTH` 信息平滑、一阶跟踪 | Input → Smoothed → Level（平滑闭环） |
| `coflow_structure.mdl` | SFD | 共流(coflow)、属性随物料流动 | Workforce ↔ Experience，Hiring/Quitting 同步驱动两条库存 |
| `lookup_structure.mdl` | SFD | `WITH LOOKUP` 查表、供需价格调节 | Price → Demand/Supply → Price Change → Price（负反馈寻价） |
| `s_shaped_growth.mdl` | SFD | S 形增长、采纳扩散、超调 | Potential Adopters → Adoption → Adopters → Abandonment（正反馈转负反馈） |
| `control_panel.mdl` | SFD | Input/Output Controls、滑块、图形输出 | Population → Growth Multiplier → Effective Birth Rate → Net Growth（含 3 个滑块 + 1 个图形） |
| `production_chain.mdl` | SFD | 供应链、库存补货、覆盖时间 | Demand → Ordering → Inventory → Sales（多库存闭环） |
| `multiview_shadow.mdl` | SFD | 多视图、shadow variable、跨视图引用 | 多视图分模块，shadow 连接同名变量 |

## 使用方法

```bash
# 查看每个模型的对象 ID、类型、坐标、箭头属性
python ../tools/vensim_autolayout.py inspect <model.mdl>

# 审计箭头引用完整性 + 方程语义
python ../tools/vensim_autolayout.py audit <model.mdl>

# 纯 Python 仿真导出 CSV（不依赖 Vensim）
python ../tools/vensim_engine.py simulate <model.mdl> --output out.csv --var Stock Level

# 自动排版（以 population_demo 为例）
cp ../templates/layout_config_sfd.json my_layout.json
# 编辑 my_layout.json，在 lock_node_names 填入该模型的库存与流率标签名
python ../tools/vensim_autolayout.py layout <model.mdl> \
  --output <model>_autolayout.mdl \
  --config my_layout.json \
  --engine dot \
  --route-information-arrows
```

## 各模板的锁定建议

运行 `layout` 时，在配置 `lock_node_names` 中填入对应库存与流率标签，避免移动骨架：

**经典结构范式：**
- `first_order_negative_feedback`：`Stock Level`、`Adjustment Rate`
- `first_order_positive_feedback`：`Capital`、`Investment`、`Depreciation`
- `second_order_oscillation`：`Inventory`、`Workforce`、`Production`、`Sales`、`Net Hiring`
- `aging_chain`：`Children`、`Youth`、`Adults`、`Elderly` 及全部成熟流阀门
- `sir_epidemic`：`Susceptible`、`Infected`、`Recovered`、`Infection Rate`、`Recovery Rate`
- `depreciation`：`Asset Value`、`Investment`、`Depreciation`

**应用场景示范：**
- `population_demo`：`Population`、`Births`、`Deaths`
- `delay_structure`：`Order Pipeline`、`Inventory`、`Order Rate`、`Delivery Rate`、`Sales Rate`
- `smooth_structure`：`Level`
- `coflow_structure`：`Workforce`、`Experience`、`Hiring`、`Quitting`、`Experience Gain`、`Experience Loss`
- `lookup_structure`：`Price`、`Price Change`
- `s_shaped_growth`：`Potential Adopters`、`Adopters`、`Adoption Rate`、`Abandonment Rate`
- `control_panel`：`Population`、`Net Growth` 及全部 IO 对象
- `production_chain`：`Inventory`、`Order Pipeline` 及各流率阀门
- `multiview_shadow`：各视图库存与流率
- `cld_customer_loop`：CLD 可设 `move_stocks: true`，不锁定，让 Graphviz 自由排布

## 关于 audit 的"未使用变量"提示

部分模板含**诊断/观察变量**（如 `Net Growth Rate`、`Steady State Value`、`Recovered Fraction`），它们不参与反馈回路，仅用于输出观察，因此会被 audit 标记"已定义但从未被引用"。这是 SD 模型的正常做法，非错误——这些变量在 `simulate`/`graph` 时作为输出指标导出。

## 草图格式要点

- 草图头为 `\\\---///`（三个反斜杠加三个横杠三个斜杠），其后 `V300` 版本码、`*View Name` 视图名、`$...` 默认字体颜色。
- 对象类型码：`10`=变量，`11`=阀门，`12`=源/汇/IO/注释，`1`=箭头，`30/31`=其他。
- 箭头 `from/to` 必须引用本视图已存在的对象 ID；物理流率管道 `weight≥20`，信息箭头 `weight<20`。
- 普通箭头加一个中间控制点即显示为平滑圆弧。
