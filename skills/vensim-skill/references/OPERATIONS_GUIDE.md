# 操作手册

所有命令从 skill 目录执行。macOS/Linux 用 `./skill.sh`，Windows 用 `.\skill.cmd`，也可直接调用 `python scripts/skill_cli.py`。`<命令> --help` 显示准确参数。核心 Python 要求 3.10+。

## 检查与整理

```bash
./skill.sh doctor
./skill.sh inspect model.mdl
./skill.sh audit model.mdl
./skill.sh check model.mdl
./skill.sh feedback model.mdl --strict --output work/feedback.json
./skill.sh layout model.mdl --output work/model_layout.mdl --mode refine --style monochrome
./skill.sh visual work/model_layout.mdl --strict --max-crossings 0 --output work/geometry.json
```

`audit` 检查对象引用与模型依赖的明显不一致；`check` 检查内置解析器支持的方程、初值与时间设置。两者不能代替 Vensim 的完整语法/单位检查。CLD 没有方程时只做草图检查，不能把它当作可仿真的 SFD。

默认 `refine` 就近移动可移动辅助量和影子，保留管道、阀门、附着文字、隐藏对象、未知路由端点与用户锁定对象。`preserve` 只路由圆弧；`auto` 比较本地骨架候选，不需要外部布局器。旧配置中的 `graphviz` 仅作为迁移别名，映射到本地 `auto`。`style` 取 `preserve`、`monochrome` 或 `native-blue`。

需要将已有图重排为环形时：

```bash
./skill.sh layout model.mdl --output work/model_circular.mdl --mode circular --style monochrome
```

`circular` 按真实连接和文字尺寸组织各模块，外围参数靠近作用对象；保留 SFD 骨架、影子身份和所有箭头端点。小型闭环使用椭圆，大型模块使用局部锚点或圆角边界，避免规则大圆和“云团”。`circular_gap` 与 `circular_aspect` 可调留白和高宽比。自动排版后仍须逐项看碰撞报告，不能仅凭外观接受结果。

配置支持 `view`、`skip_views`、`lock_node_names`、`lock_object_ids`、`move_shadows`、`shadow_visibility`、`node_positions`、`clearance`、`node_spacing`、`curve_strength`、`minimum_curve_pixels`、`maximum_curve_pixels`、`routing_passes` 和 `max_allowed_crossings`。`shadow_visibility` 取 `preserve`、`orphan`（默认）或显式的 `all`；通常先使用默认值。按 [图面规则](APPEARANCE.md) 处理具体冲突。锁定位置与无法自动消除的冲突会保留在报告中。

布局输出保留源模型的编码、换行、方程字节和因果端点。源文件不可作为输出；符号链接、硬链接别名也会检查。不要用 `--move-stocks` 或手改管道字段绕过锁定；需要改结构时在原生 Vensim 中确认。

调试预览：

```bash
./skill.sh preview work/model_layout.mdl --compare-with model.mdl --output debug/review.html
```

仅供几何排查，不能充当最终结构图。多 View 或前后对比输出 HTML；单 View 可输出调试 SVG。`--show-ids` 为显式调试选项，正式图禁止增加编号。

## 从规范生成 SFD

```bash
./skill.sh build assets/templates/inventory_zh.json --output work/inventory.mdl
```

`variables` 每项有 `name`、`kind`、`unit`。`stock` 给 `initial`；`flow` 给 `equation`、`from`、`to`，一端可为 `null` 代表边界；`aux` 和 `constant` 给 `equation`。工具按流向生成 INTEG 方程。存量和辅助变量可用 `position: [x, y]` 指定位置；流量阀门和文字由管道结构生成。

新建 `build` 默认环形布局，无需额外参数；`sketch.layout_mode` 可显式改为 `refine` 或 `preserve`。CLI、Python `build_model(spec)` 与 MCP `build_model` 共用该默认值。显式 `position` 固定不动；冲突会写入 `.mdl.build_report.json`，其中 `pass` 是几何检查，`native_verified` 仍为 `false`，必须在 Vensim 单独验收。

`time` 必须明确设置 `initial`、`final`、`step`、`saveper`、`unit`。`links` 只补充真实依赖的 `polarity` 和 `delay`，不是凭空增加因果关系的通道。业务默认中文，英文规范应显式写 `language: "en"`。不对已有名字进行自动翻译。

建模器面向自包含标量模型，复杂共享管道、多个同向流率、多 View 和高级 Vensim 结构仍需原生编辑。新模型必须执行原生 Check Model、Units Check 并审查每个真实依赖，不能仅凭预检判定可交付。

`feedback` 核对箭头极性，`--spec model.json` 还会核对其中明确指定的 `feedback_loops`；不会自动添加符号或改写方程。新建拒绝可确定的极性冲突，未覆盖语义保留待核对状态。符号放置与完整边界见 [反馈核对](FEEDBACK.md)。

## 仿真与结果图

```bash
./skill.sh simulate work/inventory.mdl --var 库存 --output results/base.csv
./skill.sh simulate work/inventory.mdl --var 库存 --set '调整时间=2' --time-step 0.125 --saveper 1 --output results/policy.csv
./skill.sh simulate work/inventory.mdl --backend pysd --var 库存 --output results/pysd.csv
./skill.sh crosscheck work/inventory.mdl --var 库存 --output results/python_crosscheck.json
./skill.sh graph work/inventory.mdl --var 库存 --output results/stock.svg
./skill.sh compare base.mdl --scenario policy.mdl --var 库存 --output results/comparison.png
```

内置引擎用 Euler，支持 INTEG、常见数学/条件函数、内联 Lookup、STEP/RAMP/PULSE、SMOOTH/SMOOTH3 及显式初值形式、DELAY1/DELAY3 及显式初值形式、DELAY FIXED 的已测试子集。独立命名 Lookup、复杂数组、宏、外部数据及完整函数语义不做全覆盖承诺。

数值常量可用 `--set` 覆盖。存量初值由独立参数控制；不允许覆盖流率或状态方程，从而避免悄悄切断反馈。时间设置单独通过对应选项传入。内置后端要求终止区间与保存间隔能由时间步长整除，避免不明示地截断时间轴。

`simulate` 生成 CSV 和 `.csv.run.json`；元数据记录后端、模型哈希、实际时间设置、参数覆盖和诊断警告。`--keep-going` 遇到错误可能使用替代值，元数据会标记 `diagnostic_only`，不得用于结论。CSV 使用 UTF-8 BOM，便于 Windows 表格软件读取中文。

PySD 是可选后端，临时翻译自包含 MDL，不在输入目录留下 Python 文件。`crosscheck` 使用同一组变量、参数和时间设置逐点比较内置 Euler 与 PySD，并核对保存网格；差值超过容差或网格不同就返回失败。含相对外部数据引用的模型明确拒绝，以免静默改变数据路径；这类模型在其原工程中调用 PySD 或原生 Vensim。Python 后端一致不等于原生 Vensim 逐点证明，未覆盖函数仍需原生核对。

结果图默认使用 Python，600 DPI PNG、PDF/SVG 矢量版，无标题/图号/水印，按变量分别输出。使用 `--formats png,pdf,svg` 同时导出，`--plot-config my_plot.json` 调整样式。编号曲线、下方长线图例、CSV 重绘和 Monte Carlo 分位带见 [结果图手册](RESULT_PLOTS.md)。

## 批量实验

```bash
./skill.sh experiment work/inventory.mdl --spec assets/templates/inventory_scenarios.json --output-dir results/scenarios --plot results/scenarios.png
./skill.sh experiment work/inventory.mdl --spec assets/templates/inventory_sensitivity.json --output-dir results/sensitivity
```

实验必须选择 `variables`。支持四种规范：

```json
{
  "mode": "grid",
  "variables": ["库存"],
  "parameters": {"调整时间": [2, 4, 8], "需求": [15, 20, 30]},
  "time": {"time_step": 0.25, "final_time": 60, "saveper": 1}
}
```

- `scenarios`：使用 `scenarios: [{"name": "基准", "params": {}}]`。
- `perturb`：使用参数名列表和相对变化列表，按原始常量逐个扰动并加入基准。
- `grid`：每个参数给离散数值列表，执行笛卡尔积。
- `monte-carlo`：每个参数给 `[下限, 上限]`，明确 `samples` 和 `seed`；采用独立均匀分布。

输出 `series.csv`、`summary.csv` 和 `experiment.json`。所有运行成功后才写入结果数据，已有同名输出会拒绝覆盖。不同情景均从原始初值开始；敏感性样本不是校准数据，也不是概率置信区间。

## 参数校准与政策优化

```bash
python -m pip install -r requirements/analysis.txt
./skill.sh calibrate work/model.mdl --spec calibration.json --data observations.csv --output-dir results/calibration
./skill.sh optimize work/model.mdl --spec policy.json --output-dir results/policy
```

参数边界、观测数据、目标、约束、种子和仿真次数依据当前任务填写。每个候选方案重新初始化，源模型不修改；同名结果与附属报告不可覆盖。输入规范、加权误差、保存点统计、预算与收敛的区别见 [Python 高级分析](ADVANCED_ANALYSIS.md)。

## 步长检查

```bash
./skill.sh convergence work/inventory.mdl --var 库存 --output results/convergence.json --tolerance 0.01
```

使用 dt、dt/2、dt/4，保持原保存间隔。误差除以最细步长轨迹的最大绝对值；接近零的轨迹使用极小正数保护。这个检查用于发现 Euler 时间离散敏感性，不是模型正确性的证明，也不意味着自动换用更高级积分器。

## 明确选择修复项

```bash
./skill.sh fix model.mdl --units-map units.json --output work/fixed.mdl
```

单位 JSON 示例为 `{"库存":"件","补货":"件/Month"}`。只补指定且缺失的单位，不猜测量纲。`--drop-broken-arrows` 删除引用不存在对象的草图记录，使用前必须核实应修复端点还是移除旧记录；工具不会替你判断研究语义。

## 原生验收和交付

在 Vensim 打开输出、检查全部 View、运行 Check Model 与 Units Check，并实际运行仿真。记录是否成功、所用版本与输出；截图仅证明看到的内容。没有原生环境就明确待验收，不能自行把 `native_verified` 改成真。

最终结构图由这个 MDL 原生导出。Python 可生成仿真曲线；调试 SVG/HTML 不进入最终结构图目录。文档写作者和非商业许可，模型图内不叠加水印。研究类任务还需要 [研究工作流](RESEARCH_WORKFLOW.md)。
