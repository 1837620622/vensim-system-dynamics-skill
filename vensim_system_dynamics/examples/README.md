# 示例模型集合

本目录提供覆盖 Vensim 仿真全部主要功能的完整图例。每个 `.mdl` 都是闭环、头格式正确、无注释文字、不折叠，可直接在 Vensim PLE 打开运行 `Check Model` 与 `Units Check`。

## 图例清单

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

## 使用方法

```bash
# 查看每个模型的对象 ID、类型、坐标、箭头属性
python ../tools/vensim_autolayout.py inspect <model.mdl>

# 审计箭头引用完整性
python ../tools/vensim_autolayout.py audit <model.mdl>

# 自动排版（以 population_demo 为例）
cp ../templates/layout_config_sfd.json my_layout.json
# 编辑 my_layout.json，在 lock_node_names 填入该模型的库存与流率标签名
python ../tools/vensim_autolayout.py layout <model.mdl> \
  --output <model>_autolayout.mdl \
  --config my_layout.json \
  --engine dot \
  --route-information-arrows
```

## 各模型的锁定建议

运行 `layout` 时，在配置 `lock_node_names` 中填入对应库存与流率标签，避免移动骨架：

- `population_demo`：`Population`、`Births`、`Deaths`
- `delay_structure`：`Order Pipeline`、`Inventory`、`Order Rate`、`Delivery Rate`、`Sales Rate`
- `smooth_structure`：`Level`
- `coflow_structure`：`Workforce`、`Experience`、`Hiring`、`Quitting`、`Experience Gain`、`Experience Loss`
- `lookup_structure`：`Price`、`Price Change`
- `s_shaped_growth`：`Potential Adopters`、`Adopters`、`Adoption Rate`、`Abandonment Rate`
- `control_panel`：`Population`、`Net Growth` 及全部 IO 对象
- `cld_customer_loop`：CLD 可设 `move_stocks: true`，不锁定，让 Graphviz 自由排布

## 草图格式要点

- 草图头为 `\\\---///`（三个反斜杠加三个横杠三个斜杠），其后 `V300` 版本码、`*View Name` 视图名、`$...` 默认字体颜色。
- 对象类型码：`10`=变量，`11`=阀门，`12`=源/汇/IO/注释，`1`=箭头，`30/31`=其他。
- 箭头 `from/to` 必须引用本视图已存在的对象 ID；物理流率管道 `weight≥20`，信息箭头 `weight<20`。
- 普通箭头加一个中间控制点即显示为平滑圆弧。
