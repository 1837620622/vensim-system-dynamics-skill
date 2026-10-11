# 示例

两个中文示例都由 `build` 从 `../templates/` 下的同名 JSON 生成，用当前代码重建会得到逐字节相同的文件。示例里的数值只用来演示命令，不代表任何研究的真实参数。

| 文件 | 结构 | 建模 JSON |
| --- | --- | --- |
| `inventory_zh.mdl` | 库存一条存量链：补货流入、销售流出，补货按目标库存缺口调整 | [inventory_zh.json](../templates/inventory_zh.json) |
| `water_feedback_zh.mdl` | 水量的一阶负反馈：水量 → 缺水量 → 补水需求 → 补水 → 水量 | [water_feedback_zh.json](../templates/water_feedback_zh.json) |

在 skill 目录下运行：

```bash
./skill.sh build assets/templates/inventory_zh.json --output work/inventory.mdl
./skill.sh visual work/inventory.mdl --strict
./skill.sh simulate work/inventory.mdl --var 库存 --output results/base.csv
./skill.sh experiment work/inventory.mdl --spec assets/templates/inventory_scenarios.json --output-dir results/scenarios --plot figures/scenarios.png
./skill.sh experiment work/inventory.mdl --spec assets/templates/inventory_sensitivity.json --output-dir results/monte_carlo --plot figures/monte_carlo.png
```

三个实验模板分别演示命名情景（`inventory_scenarios.json`）、单因素扰动（`inventory_perturb.json`）和蒙特卡洛抽样（`inventory_sensitivity.json`）。

真实教材模型在 [reference_models](../reference_models/README.md)，排版遇到老化链、绕行支路、多视图等结构时可以参考。
