# 仿真、实验与校准

内置引擎用纯 Python 做 Euler 积分，覆盖常见的标量模型；数组、宏、外部数据和其他高级函数交给 PySD 或 Vensim。参数、初值、时间设置和实验规模都来自当前任务资料。

## 内置引擎支持的函数

| 函数 | 实现 | 说明 |
| --- | --- | --- |
| `INTEG(rate, initial)` | 同一时刻先算全部流率，再同时做 Euler 更新 | 初值按依赖顺序递归求值，初值成环时报错 |
| `XIDZ(A,B,X)` / `ZIDZ(A,B)` | `ABS(B) < 1e-6` 时返回 X（ZIDZ 为 0），否则 A/B | 与 Vensim 的小分母阈值一致 |
| `PULSE(start,width)` | 用 `Time + TIME STEP/2` 比较，宽度 0 按一个步长 | 非网格起点在 Vensim 与 PySD 之间可能不同，切换后端时核对跳变处 |
| `STEP(height,start)` | `Time + TIME STEP/2 > start` 时取 height | |
| `RAMP(slope,start,end)` | `Time <= start` 为 0，之后线性上升到 end 并保持 | 反向区间与 PySD 不同 |
| `MODULO(A,B)` | 正除数用 C 风格余数（结果与被除数同号） | 非正除数直接报错，Vensim 与 PySD 定义不同 |
| `DELAY FIXED(input,duration,initial)` | 独立的离散状态队列，时长和初值在初始化时冻结 | 时长按步长取整（半步向上）；必须单独写在等号右侧 |
| `SMOOTH`、`SMOOTH3` 及 I 变体 | 一阶或三阶信息平滑 | |
| `DELAY1`、`DELAY3` 及 I 变体 | 各阶段管道存量逐级流出 | 物料延迟，不同于信息平滑 |
| 独立 Lookup、`WITH LOOKUP` | 数据点之间线性插值，范围外取首末值 | 至少两个点，x 严格递增 |
| `IF THEN ELSE`、`MIN`、`MAX`、`ABS`、`SQRT`、`EXP`、`LN` 等 | 标准数学与条件函数 | `IF THEN ELSE` 只求所选分支 |

方程通过受限的表达式树求值，不执行任意 Python 代码。遇到不支持的函数直接报错；`--keep-going` 用替代值继续，结果标记为诊断数据，不能出图或写进结论。变量名不区分大小写，空格与下划线等价。

## 时间设置

模型要写明 `INITIAL TIME`、`FINAL TIME`、`TIME STEP`、`SAVEPER`。内置引擎要求仿真区间和保存间隔都是步长的整数倍，保存间隔不小于步长。参数覆盖若会间接改变这些时间控制，会被拒绝。

Rahmandad 和 Sterman（2012）要求报告积分方法和步长，并说明结果对步长是否敏感。`convergence` 用 dt、dt/2、dt/4 各跑一次，误差除以最细步长轨迹的最大绝对值，超过容差（默认 0.01）判为不合格。

## 后端对照

```bash
./skill.sh simulate model.mdl --var 库存 --output results/base.csv
./skill.sh simulate model.mdl --var 库存 --backend pysd --output results/pysd.csv
./skill.sh crosscheck model.mdl --var 库存 --output results/crosscheck.json
```

`simulate` 不写 `--var` 时导出全部变量；`graph`、`crosscheck` 必须用 `--var` 指明变量。

PySD 在临时目录翻译模型，不在模型旁边留下 `.py` 文件；引用外部数据文件的模型请在原工程里直接用 PySD 或 Vensim。`crosscheck` 用相同的参数、时间网格和变量逐点比较内置 Euler 与 PySD，差值除以 `max(1, |取值|)` 后与容差（默认 1e-10）比较：取值小于 1 时相当于绝对误差，大于 1 时相当于相对误差，人口、资金这类大数值的浮点舍入不会被误判。报告同时给出最大绝对误差和最大缩放误差。收录的 7 个参考模型全部变量逐点一致。

两个 Python 实现一致，不代表与 Vensim 逐点相同，涉及特殊函数时仍要在 Vensim 里核对。PySD 解析不了部分老版本官方示例（报语法错误）和带数组的模型，这时只能用内置引擎或 Vensim。

每次仿真除 CSV 外还写一个 `.run.json`，记录模型哈希、后端与版本、积分方法、时间设置、参数覆盖和诊断状态。`plot-data` 读取 CSV 时会核对这个清单：哈希对不上、标记为诊断结果、时间单位冲突时停止出图。

## 批量实验

```bash
./skill.sh experiment model.mdl --spec experiment.json --output-dir results/scenarios --plot results/scenarios.png
```

| mode | 参数写法 | 含义 |
| --- | --- | --- |
| `scenarios` | `scenarios: [{name, params}]` | 每个命名情景独立运行 |
| `perturb` | `parameters: [参数名]`、`changes: [-0.2, 0.2]` | 一次只改一个参数，并加入基准 |
| `grid` | `parameters: {参数名: [取值列表]}` | 全组合 |
| `monte-carlo` | `parameters: {参数名: [下限, 上限]}`、`samples`、`seed` | 独立均匀抽样，种子必填 |

只能覆盖数值常量，不能替换方程。每次运行都从原始初值开始，实验期间模型文件被改动会中止。最多 200 次运行、200 万个保存值。输出 `series.csv`（长表）、`summary.csv`（初值、终值、最小、最大、峰值时刻）和 `experiment.json`。终值只在最后一个保存点正好是 FINAL TIME 时写出。

基准、策略、机制等情景只是名字，含义由当前研究决定。比较机制作用时，保持其他参数、时间网格和种子相同，分别运行去掉机制、基准和加入机制的情景。两个模型耦合时，写清接口的方向、单位换算和时间对齐，分目录保存独立运行与耦合运行的结果。

## 参数校准与政策搜索

需要 SciPy（`requirements/analysis.txt`）。两者都用差分进化，每个候选都真实跑一次模型，不改写 MDL。

```bash
./skill.sh calibrate model.mdl --spec calibration.json --data observations.csv --output-dir results/fit
./skill.sh optimize model.mdl --spec policy.json --output-dir results/policy
```

共同字段：

| 字段 | 含义 |
| --- | --- |
| `parameters` | `{常量名: [下限, 上限]}`，1–20 个 |
| `seed` | 整数，用于复现 |
| `max_evaluations` | 10–5000，实际仿真次数上限（含一次基准） |
| `population_size` / `tolerance` | SciPy 种群乘数（默认 10）与收敛阈值（默认 1e-6） |
| `time` | 可选 `{time_step, final_time, saveper}` |

校准用 `targets: [{variable, unit, weight, scale}]` 和 `time_unit`。观测文件是 `Time,变量,...` 宽表或单一情景的 `Scenario,Time,Variable,Value` 长表，观测时刻必须落在保存网格上，不插值。目标函数为各目标 `weight × mean(((模拟 − 观测)/scale)²)` 之和。拟合好不等于结构正确，应另留验证区间，检查残差和参数是否碰到边界。

政策搜索用 `objectives: [{variable, statistic, direction, weight, scale}]`，统计量可选 `initial`、`final`、`min`、`max`、`mean`、`integral`；可加 `constraints: [{variable, statistic, lower, upper}]`。多个目标按 `weight/scale` 加权合成，不生成 Pareto 前沿。`integral` 和 `mean` 在保存点上用梯形法计算，与 Vensim DSS 按 TIME STEP 累积的 payoff 不同。

输出：`optimization.json`（规范、哈希、依赖版本、最佳参数、实际次数、终止原因）、`evaluations.csv`、`baseline.csv`、`best.csv`（找到可行解时）、`comparison.csv`（可直接用 `plot-data` 出图）。找不到可行解时返回 1，不生成 `best.csv`。

## 研究报告要包含的内容

参照 Rahmandad 和 Sterman（2012）的报告要求：

- 完整方程、全部参数值与初值、每个变量的单位、数据来源；
- 积分方法、步长、保存间隔，以及步长敏感性检查结果；
- 每个情景的参数设置；蒙特卡洛实验的分布、抽样方法、样本数和种子；
- 校准与优化的参数范围、搜索算法、实际仿真次数；
- 结构图来自交付的 MDL，结果图能追溯到保存的 CSV 和运行清单。

验证至少包括：初值、极端条件、守恒关系、步长敏感性、历史行为对照、参数敏感性和政策作用方向（Sterman，*Business Dynamics* 第 21 章）。历史期与预测期用同一套内生方程连续运行，不能把观测轨迹写回存量冒充历史再现。
