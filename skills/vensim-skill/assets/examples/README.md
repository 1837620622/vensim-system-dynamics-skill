# 示例模型集合

本目录提供中文库存示例和历史结构样例。示例值用于演示与回归，不代表任何研究中的真实参数。使用前必须检查方程、单位、时间设置及实际图面。历史样例保留以验证格式兼容性，不承诺全部已经完成最新原生验收，也不能直接当作论文成品。

`inventory_zh.mdl` 是已在 Vensim PLE 中检查模型、检查单位并原生导出结构图的示例；从 `../templates/inventory_zh.json` 可重建。批量实验分别参考 `inventory_scenarios.json`、`inventory_perturb.json` 和 `inventory_sensitivity.json`。通用任务应替换为有依据的业务定义与参数。

模板分两类：**经典结构范式**（用于学习结构原理）和**应用场景示范**（具体业务，适合参考建模思路）。

## 经典结构范式

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
| `cld_customer_loop.mdl` | CLD | 因果回路草图、正负反馈、信息箭头 | Customers ↔ Word of Mouth（正）、Customers ↔ Churn（负）、Satisfaction 调节 |
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
python ../../scripts/vensim_autolayout.py inspect <model.mdl>

# 审计箭头引用完整性 + 方程语义
python ../../scripts/vensim_autolayout.py audit <model.mdl>

# 纯 Python 仿真导出 CSV（不依赖 Vensim）
python ../../scripts/vensim_engine.py simulate <model.mdl> --output out.csv --var "Stock Level"

# 自动排版（输入当前需要整理的模型）
cp ../templates/layout_config_sfd.json my_layout.json
# 编辑 my_layout.json，在 lock_node_names 填入该模型的库存与流率标签名
python ../../scripts/vensim_autolayout.py layout <model.mdl> \
  --output <model>_autolayout.mdl \
  --config my_layout.json \
  --route-information-arrows
```

## 骨架与局部锁定

程序从当前模型的方程和草图识别存量、流量阀门、云及附着文字，默认保护管道骨架。需要额外保护人工确定的位置时，使用当前 View 的 `lock_object_ids` 或 `lock_node_names`；不要把本目录某个模型的变量清单抄到另一个任务。CLD 与多视图模型应按实际阅读顺序组织，不能只因示例类型相近而复用坐标。

## 关于 audit 的"未使用变量"提示

部分模板含**诊断/观察变量**（如 `Net Growth Rate`、`Steady State Value`、`Recovered Fraction`），它们不参与反馈回路，仅用于输出观察，因此会被 audit 标记"已定义但从未被引用"。这是 SD 模型的正常做法，非错误——这些变量在 `simulate`/`graph` 时作为输出指标导出。

## 草图格式要点

- 草图头为 `\\\---///`（三个反斜杠加三个横杠三个斜杠），其后 `V300` 版本码、`*View Name` 视图名、`$...` 默认字体颜色。
- 对象类型码：`10`=变量，`11`=阀门，`12`=源/汇/IO/注释，`1`=箭头，`30/31`=其他。
- 箭头 `from/to` 必须引用本视图已存在的对象 ID；本项目解析器结合管道字段与阀门识别物理流率；未知格式保持原文，不凭箭头外观猜测语义。
- 普通箭头加一个中间控制点即显示为平滑圆弧。
