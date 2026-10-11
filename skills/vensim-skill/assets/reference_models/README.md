# 参考模型

7 个真实 Vensim 模型，都按 MIT 许可收录，文件内容与上游逐字节相同，只改了文件名。

- 5 个来自 Jim Duggan 的教材 *System Dynamics Modeling with R*（Springer, 2016）配套仓库 [JimDuggan/SDMR](https://github.com/JimDuggan/SDMR)，许可证见 [LICENSE-SDMR.txt](LICENSE-SDMR.txt)。
- 2 个来自 PySD 等工具共用的测试模型库 [SDXorg/test-models](https://github.com/SDXorg/test-models)，许可证见 [LICENSE-SDXorg.txt](LICENSE-SDXorg.txt)。

用途有两个：排版遇到同类结构时，先看作者是怎么画的；测试用它们检查排版和仿真没有退化。

| 文件 | 来源 | 上游路径 | 结构 | 排版要点 |
| --- | --- | --- | --- | --- |
| `teacup.mdl` | SDXorg | `samples/teacup/teacup.mdl` | 一个存量、一条流出 | 最小的一阶负反馈；长存量名在 80×40 的框里折成两行 |
| `sir_aggregate.mdl` | SDMR | `models/05 Chapter/Vensim/01 SIR Aggregate.mdl` | S→I→R 单链 | 水平存量链；R1、B1、B2 回路标记画在各自回路内部 |
| `overshoot.mdl` | SDMR | `models/03 Chapter/Vensim/03 Overshoot.mdl` | 资本、资源两条链，4 个平衡回路和 1 个增强回路 | 作者版面合理，`auto` 只挪开压字的个别变量 |
| `sir_policy.mdl` | SDMR | `models/05 Chapter/Vensim/02 SIR Policy.mdl` | SIR 加接种和隔离 | 接种流跨过整条主干，画成上方的直角折线管道；隔离画成主干下方的绕行 |
| `workforce.mdl` | SDXorg | `samples/Workforce/workforce.mdl` | 新手、熟手两级人员链加任务积压 | 两条存量链上下排列，工时计算链放在两行之间 |
| `student_flows.mdl` | SDMR | `archive 2015/04 Lecture/Vensim/Student Flows.mdl` | 四个年级的升级链，每段带留级存量 | 留级存量交替画在主干上方和下方，拐角阀门的名字放在夹角内侧；原图有隐藏变量 |
| `health_model.mdl` | SDMR | `models/04 Chapter/Vensim/Health Model.mdl` | 人口年龄链、门诊需求、医生供给 | 按子系统分成 3 个视图，跨视图引用用影子变量 |

内置引擎与 PySD 对这 7 个模型的全部标量变量逐点比较（图形函数不能单独导出，由调用它的变量间接检验），按 `max(1, |值|)` 缩放后的最大差值不超过 1e-15（`crosscheck`）。

Vensim 官方示例（Ventana Systems 随软件发布）也用来统计过排版间距，但许可不允许再分发，所以没有收录。
