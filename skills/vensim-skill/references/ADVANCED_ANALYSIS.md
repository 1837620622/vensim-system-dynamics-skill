# Python 参数校准与政策优化

适用于已经完成方程、单位、初值和基准行为检查的自包含标量 MDL。Python 在 PLE 外提供参数搜索；使用 SciPy 差分进化，每个候选方案都实际运行模型。源 MDL、图面和原始参数保持不变，最佳参数在报告中单独给出。

## 安装与入口

```bash
python -m pip install -r requirements/analysis.txt
./skill.sh calibrate work/model.mdl --spec calibration.json --data observations.csv --output-dir results/calibration
./skill.sh optimize work/model.mdl --spec policy.json --output-dir results/policy
```

Windows 使用 `.\skill.cmd`。两条命令都支持 `--backend builtin|pysd`；PySD 需要单独安装。输出目录中的同名文件不可覆盖。MCP 对应 `calibrate_model` 和 `optimize_policy`，仍受工作目录和执行超时限制。

从 [校准空白规范](../assets/templates/calibration_template.json) 或 [政策空白规范](../assets/templates/policy_template.json) 开始，先填写任务资料。空对象、空列表和 `null` 表示待提供的资料，不能直接运行。工具不把参考图片、旧样例或其他项目的参数当作观测数据。

## 共同字段

| 字段 | 要求与含义 |
| --- | --- |
| `parameters` | `{模型常量名: [下限, 上限]}`；1–20 个有限数值常量，下限小于上限。范围来自研究或业务依据 |
| `seed` | 必填整数，范围 0–4294967295；同一后端及依赖版本下用于复算 |
| `max_evaluations` | 必填，10–5000；实际独立仿真次数上限，包含一次原始基准，不是代数 |
| `population_size` | 可选，默认 10，范围 2–50；SciPy 的种群乘数，种群规模随参数维数变化 |
| `tolerance` | 可选正数，默认 `1e-6`；SciPy 种群得分的相对收敛阈值，不是参数误差保证 |
| `time` | 可选 `{time_step, final_time, saveper}`；省略时使用 MDL 自身的明确设置 |
| `work_limit` | 可选正整数，默认 50000000；预计步数 × 展开方程数 × 最大仿真次数的资源保护。评估资源后可显式提高 |

不能搜索时间控制变量、存量方程、流率方程或辅助反馈关系。存量初值若需校准，使用模型中已有的独立数值初值参数。影响时间控制的间接依赖也会检查，所有方案必须使用相同的时间区间和输出网格。

固定种子不是跨所有库版本得到完全相同结果的承诺；报告保存 NumPy、SciPy 版本、模型 SHA-256、有效时间设置、后端与参数。默认顺序计算、关闭自动局部精修，目标和约束共享缓存，避免约束重复评估突破次数限制。

## 观测数据校准

规范使用 `targets` 和 `time_unit`：

| 字段 | 含义 |
| --- | --- |
| `time_unit` | 明确的观测时间单位，须与 MDL 的 INITIAL TIME 单位字符串一致 |
| `targets[].variable` | 模型中存在的观测变量名，不能重复 |
| `targets[].unit` | 观测值的单位，须与模型变量单位字符串一致 |
| `targets[].weight` | 正数，相对重要性；由研究问题或测量依据决定 |
| `targets[].scale` | 正数，与观测变量同量纲，用于归一化不同量级的误差 |

观测文件可以是 `Time,变量一,变量二,...` 宽表，或只有一组观测情景的 `Scenario,Time,Variable,Value` 长表。至少两个时刻，各目标使用同一时间网格；拒绝重复时间、NaN/Inf、缺失列和混合情景。默认 UTF-8/BOM，旧中文 CSV 可显式设置 `--encoding gb18030`。

观测时刻必须落在实际保存网格上。不悄悄插值、不丢弃不匹配观测、不把年份自动换为 Month，也不自动换算数值单位。单位声明只验证映射，数据本身是否采用该单位仍须核对来源。

最小化的得分为：对每个目标，计算 `mean(((模拟值 - 观测值) / scale)^2)`，乘以该目标的 `weight`，再对所有目标求和。每个目标内部对观测时刻等权。权重与尺度不自动估计，结果不包含参数置信区间、显著性检验或可识别性证明。

拟合后检查残差的方向和时序、边界命中、独立验证区间、参数可解释性与步长敏感性。误差小不意味着结构正确；不能用同一组拟合数据声称已完成独立预测验证。

## 有约束的政策搜索

规范使用 `objectives`，可附加 `constraints`：

| 字段 | 含义 |
| --- | --- |
| `objectives[].variable` | 模型变量名 |
| `objectives[].statistic` | `initial`、`final`、`min`、`max`、`mean` 或 `integral` |
| `objectives[].direction` | `minimize` 或 `maximize` |
| `objectives[].weight / scale` | 必填正数；尺度采用相应统计量的单位 |
| `constraints[].variable / statistic` | 同样从真实保存轨迹提取的受约束统计量 |
| `constraints[].lower / upper` | 至少一项；均为有限数值，下界不能高于上界 |

目标采用加权标量化：最小化目标取正号，最大化目标取负号，再按 `weight / scale` 相加。不宣称生成 Pareto 前沿。约束通过 SciPy 的非线性约束接口处理；不把违反约束的高分方案列为最佳可行解。

`min/max` 检查保存采样点，不能保证采样点之间没有峰值。`integral` 在保存时间点上做梯形积分，`mean` 为该积分除以区间长度。`final/mean/integral` 要求实际保存终止时刻；必要时显式调整 SAVEPER。关心短暂超限时，应保存每个积分步并做步长检查。

本实现的梯形统计与 Vensim 原生政策 payoff 的 TIME STEP 累积规则不同，不能把分数直接等同于 DSS 的 payoff。若需要与原生得分逐项对照，应先明确积分规则、权重和时间设置。

## 输出与解释

| 文件 | 内容 |
| --- | --- |
| `optimization.json` | 输入规范、源文件哈希、后端、依赖版本、基准与最佳参数、得分、实际次数、终止原因 |
| `evaluations.csv` | 每个实际候选的参数、得分、是否可行与错误信息；参数列以 `Parameter: ` 区分元数据列 |
| `baseline.csv` | 原始 MDL 参数的轨迹；即使原参数超出搜索范围，也保留为比较依据 |
| `best.csv` | 找到可行解时才生成的最佳轨迹，不改写 MDL |
| `comparison.csv` | 可直接绘图的长表，包含 baseline、best；校准时还包含 observed |

报告中的 `termination` 区分 `converged`、`budget_exhausted` 和 `solver_stopped`。`solver_success` 仅反映求解器是否报告收敛，`feasible_solution_found` 只表明至少找到了一个可行点。`global_optimum_proven` 始终为 false；达到次数上限不等于证明最优，也不等于结果文件丢失。

无可行解时保留基准、候选记录和报告，不生成虚构的 `best.csv`，CLI 返回 1。输入错误返回 2。模型在搜索中被修改时拒绝发布结果。已知输出冲突在运行前检查；数据成功写入后才发布报告。进程被强制中断或存储发生故障仍可能留下不完整文件，应使用新目录重跑。

结果图继续使用既有 Python 样式：

```bash
./skill.sh plot-data results/calibration/comparison.csv --var 目标变量 --time-unit 实际单位 --output figures/calibration.png --formats png,pdf,svg
./skill.sh plot-data results/policy/comparison.csv --var 目标变量 --time-unit 实际单位 --output figures/policy.pdf
```

替换“目标变量”和“实际单位”为当前模型的值。默认独立单变量、无标题、无图号、无水印；图例区分基准、候选与观测，不在图中打印参数审计说明。

## 与 DSS 的边界

这两项功能对应校准和政策优化的使用场景，是独立 Python 实现。没有复制 DSS 原生优化算法、payoff 文件、Kalman 滤波、MCMC、编译模型、外部函数或分布似然选项，也不改变任何 Vensim 产品许可。

依据：[Vensim 优化用途](https://www.vensim.com/documentation/usr18.html)、[原生 payoff 计算规则](https://www.vensim.com/documentation/payoffcomputation.html)、[SciPy differential_evolution](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.differential_evolution.html)。
