# 仿真语义、数据身份与后端边界

改变求解器，或模型包含脉冲、延迟、查表和名称别名时，先核对本页。业务时间、参数、初值和实验规模都来自当前任务；以下阈值和资源上限属于原生函数定义或运行保护。

## 名称与表达式

- Vensim 的名称不区分大小写，空格与下划线等价，连续空格/下划线合并。内置解析保留定义名作为结果列，匹配引用时使用原生等价规则，拒绝等价重复定义。
- 已有 MDL 支持读取加引号的简单名称；新建器只生成其支持的标量名称，特殊运算符名称必须在原生软件按正确引号语法处理。
- `IF THEN ELSE` 只求所选分支；支持 `=`、`<>`、`:AND:`、`:OR:`、`:NOT:`。真实变量名先映射，再处理运算符，内部别名不参与业务名称二次替换。
- 方程只能通过受限 AST 求值，不能调用任意 Python 代码。不支持的函数报错；`--keep-going` 是诊断模式，替代值不能进入正式研究图件。
- 非有限的常量、中间算术结果和状态更新都会失败，不以 Inf/NaN 作为成功轨迹发布。

## 常见函数的已实现边界

| 函数 | 内置行为 | 验证与限制 |
| --- | --- | --- |
| `INTEG(rate, initial)` | 同一时刻计算流率后同时进行 Euler 更新 | 不是 RK4；初值递归求依赖，初值循环失败 |
| `XIDZ(A,B,X)` | `ABS(B) < 1e-6` 时返回 X，否则 A/B | 原生小分母阈值；不只检测精确零 |
| `ZIDZ(A,B)` | 与 XIDZ(A,B,0) 相同 | 两个参数；旧实现接受的三个参数现在拒绝 |
| `PULSE(start,width)` | 用 `Time + TIME STEP/2` 比较；零宽度按一个步长处理 | 按官方严格不等号实现；非网格时刻与边界相等时须核对实际后端 |
| `STEP(height,start)` | `Time + TIME STEP/2 > start` 时返回 height，否则零 | 使用有效步长，半步相等仍为零；非网格时刻有实际 PySD 对照 |
| `MODULO(A,B)` | 正除数使用 C 浮点余数，负被除数保持负余数 | 不使用 Python `%`；非正除数拒绝猜值，要求原生核对 |
| `RAMP(slope,start,end)` | `Time <= start` 为零，之后上升到结束时刻并保持 | 开始时刻按官方严格不等号；反向区间与 PySD 的边界不同，须核对具体任务 |
| `DELAY FIXED(input,duration,initial)` | 独立离散状态，初始化冻结时长和初值；同时捕获所有当前输入，再更新状态 | 至少一时步；非整数时长/步长向最近整数取整，半步向上；反馈、冻结与取整有 PySD 对照 |
| `SMOOTH/SMOOTH3` 及 I 变体 | 一阶段或三阶段信息状态 | 只覆盖独立完整 RHS 形式，不承诺任意嵌套 |
| `DELAY1/DELAY3` 及 I 变体 | 按各阶段管道存量与流出传递 | 物料延迟不等同于信息平滑；高级时变边界须跨后端核对 |
| 原生独立 Lookup、`WITH LOOKUP` | 实际点间线性插值，范围外保持首/末值 | 显示范围与参考点不是数据点；至少两个有限点，x 严格递增 |

`DELAY FIXED` 必须直接位于等号右侧，不能写成 `a + DELAY FIXED(...)`。原生也将其视为 Level。延迟时间即使包含动态变量也只使用初始值，初值引用不构成动态反馈。

Lookup 接受坐标对和原生 x 序列/y 序列形式，可识别科学计数法。错误点、重复 x 和乱序会报错，不能为使其运行而自动排序或丢点。表是函数，不能导出一条虚构的零值标量轨迹；应导出调用表的业务量。

官方 PULSE 说明与 PySD 当前实现对非网格起点的处理有差别。切换后端时，重点测试跳变附近、半步边界和零宽度；不能因整数网格示范相同而承诺任意脉冲都一致。选择实际任务需要的后端，并保存其版本与积分方法。

MODULO 官方说明使用 C 余数，同时写为 `A-QUANTUM(A,B)`；QUANTUM 对非正除数又规定返回 A。这些表述在非正除数上不能直接统一，PySD 的 QUANTUM 还对小于 `1e-6` 的除数返回 A。内置仅承诺正除数的 C 余数实现，不把负数取余直接翻译成 Python `%`，非正除数会报错。极小正除数、非正除数和反向 RAMP 区间应按实际原生版本逐点核对，不能把普通边界的对照推广到全部输入。

## 时间网格与资源

模型必须明确 `INITIAL TIME`、`FINAL TIME`、`TIME STEP`、`SAVEPER`。控制字段使用原生名称规则解析，输出保持标准控制名。

内置 Euler 要求仿真区间/步长和保存间隔/步长为整数，保存间隔不小于步长。步长不能小到在当前时间量级无法形成不同浮点时刻。统计所需终点必须在保存网格中，不能把最后保存点误称为未保存的 FINAL TIME。

运行步数、结果单元数、表达式长度和延迟状态设有保护上限。隐式平滑/物料状态同样计入结果规模，多个固定延迟的总状态也受限制。这些限制不提供业务初值、样本数或研究假设。

PySD 使用临时副本翻译自包含标量模型，显式传入本封装解析的有效时间设置。需要外部数据、数组、宏或其他高级结构时，在原始工程使用已确认支持该模型的 PySD 或原生 Vensim，不能悄悄改变相对数据路径。

## CSV 与运行清单

单次仿真保存 `result.csv` 和 `result.csv.run.json`。实验保存 `series.csv`、`summary.csv` 与 `experiment.json`。搜索保存数据和 `optimization.json`。新输出清单记录数据文件 SHA-256，运行记录包含模型哈希、后端/版本、Python 版本、Euler 方法、参数、时间网格、单位及诊断状态。

批量实验与步长检查从同一份 MDL 字节快照解析基准参数和时间设置，每次运行前后及发布清单前核对源模型哈希，运行记录也须对应这一哈希。中途改动会停止整组发布；不能把两个版本的模型混成同一组情景或收敛结果。步长检查还核对实际保存时刻完全一致，不能只按数组下标拼接。文件哈希检测不锁定整个文件系统，不构成对可信本地进程并发修改的完全隔离。

`plot-data` 自动读取相邻的对应清单：

1. 验证已记录的 CSV 哈希；不一致时停止，不能沿用旧仿真身份。
2. 保留诊断状态与警告，拒绝正式出图。
3. 恢复真实时间单位；显式标签冲突时要求先进行有依据的单位换算。
4. 恢复实验类型，只有明确的 Monte Carlo 样本才自动汇总为分位带。
5. 拒绝已知的同名结果变量单位冲突。

旧清单没有哈希、或外部 CSV 没有清单时，仍可检查并绘图，`provenance_verified: false`。这个字段只表示清单与当前文件的哈希对应，**不是原生验证、真实数据审计或研究有效性的证明**。删除或伪造清单不能把失败仿真变成合格数据。清单的符号链接不得越出结果目录。

## 依据与验证范围

- [Vensim 名称规则](https://www.vensim.com/documentation/ref_variable_names.html)
- [XIDZ](https://www.vensim.com/documentation/fn_xidz.html) 与 [ZIDZ](https://www.vensim.com/documentation/fn_zidz.html)
- [PULSE](https://www.vensim.com/documentation/fn_pulse.html)
- [STEP](https://www.vensim.com/documentation/fn_step.html)、[MODULO](https://www.vensim.com/documentation/fn_modulo.html)、[QUANTUM](https://www.vensim.com/documentation/fn_quantum.html) 与 [RAMP](https://www.vensim.com/documentation/fn_ramp.html)
- [DELAY FIXED](https://www.vensim.com/documentation/fn_delay_fixed.html)
- [Lookups](https://www.vensim.com/documentation/lookups.html) 与 [范围外行为](https://www.vensim.com/documentation/22820.html)
- [PySD 固定延迟实现](https://pysd.readthedocs.io/en/master/_modules/pysd/py_backend/statefuls.html) 与 [函数实现](https://pysd.readthedocs.io/en/master/_modules/pysd/py_backend/functions.html)

测试包含官方定义的边界预期、实际 PySD 运行、延迟反馈、Lookup 范围/点验证、别名冲突、数据身份与输出保护。没有执行的原生函数边界检查应保持未验证；不能把 Python/PySD 对照改写为原生 Vensim 逐点验收。
