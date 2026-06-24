# Vensim 系统动力学通用 Skill

可复用的"代理指令 + 工具脚本"包，用于让 AI 辅助完成 Vensim 系统动力学课程作业、政策模拟与管理研究建模，并在保留方程与对象 ID 的前提下把已建模的草图自动排版为论文级 SFD/CLD。

## 包含内容

- `SKILL.md`：通用建模指令、质量标准、交付工作流、草图格式说明；
- `tools/vensim_autolayout.py`：读取 `.mdl` 草图，列出对象、审计引用、对普通辅助变量做 Graphviz 布局、为信息箭头生成单控制点圆弧；
- `templates/model_spec_template.json`：建模前语义规范模板；
- `templates/layout_config_sfd.json`、`layout_config_cld.json`：自动排版配置样例；
- `examples/population_demo.mdl`：最小示例模型；
- `docs/REFERENCES.md`：实现依据与限制。

## 关键结论

Vensim 内置的是"对齐、等间距、尺寸统一、手动调曲线"，**不是**全图自动布局。普通 Arrow 加一个中间控制点即形成平滑圆弧；Spline Arrow 需在 Vensim 中以 `Command/Ctrl + Arrow` 创建。本包用 Graphviz 计算辅助变量位置，再保守回写 `.mdl` 草图坐标与信息箭头控制点；不新建模型结构，不改方程，不改箭头 from/to。

## 安装

```bash
# macOS
brew install graphviz
which dot && dot -V
```

Python 脚本只用标准库，无需 `pip install`。

## 最快使用方式

```bash
# 1. 查看模型对象 ID、位置、形状、箭头 from/to、控制点
python tools/vensim_autolayout.py inspect /path/to/your_model.mdl

# 2. 复制 SFD 配置，填入要锁定的库存、流率名
cp templates/layout_config_sfd.json my_layout.json

# 3. 生成自动排版模型（自动建 .backup.mdl 与 .layout_report.json）
python tools/vensim_autolayout.py layout /path/to/your_model.mdl \
  --output /path/to/your_model_autolayout.mdl \
  --config my_layout.json \
  --engine dot \
  --route-information-arrows

# 4. 审计输出
python tools/vensim_autolayout.py audit /path/to/your_model_autolayout.mdl
```

在 Vensim 中打开 `your_model_autolayout.mdl`：先看图，再 `Model > Check Model`，再 `Model > Units Check`，手工微调少数交叉关系后保存为最终版本。

## 必须先在 Vensim 中做好的部分

- 库存、流率、阀门、云、管道；
- 方程、单位、初值、模拟起止时间；
- 变量名与所有信息因果关系。

脚本不新建模型结构，不改变方程。它不是"让 AI 代替建模"，而是把已正确建模的复杂图整理得更易读。

## 适合的任务

- 汽车销售、人口、供应链韧性、政策执行、光伏治沙、城市交通等 SFD；
- 区域运输、公共治理、生态经济等 CLD；
- 作业要求的 Control Panel、政策情景、敏感性分析；
- 把原始 Vensim 图整理为论文可用图。

## 不适合的情况

- 模型尚未确认时直接批量生成 `.mdl`；
- 希望一个按钮自动设计理论机制或自动给参数；
- 多个库存—流率骨架已乱接、阀门与标签脱离；
- 不愿在 Vensim 中打开验证。
