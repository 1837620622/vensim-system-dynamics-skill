# 依据与边界

本 Skill 的规则来自下列资料，代码常量旁的注释写明了具体出处。具体坐标、权重和退火参数是工程实现，不是 Ventana 的官方算法。

## Vensim 官方文档

| 资料 | 用在哪里 |
| --- | --- |
| [MDL 文件](https://www.vensim.com/documentation/_mdl_model_files.html)、[Sketch Format](https://www.vensim.com/documentation/ref_sketch_format.html) | 方程区和草图区分开处理，排版只改草图 |
| [Sketch Object Detail](https://www.vensim.com/documentation/24305.html) | 坐标是中心点，尺寸是半宽半高；字段 9 是隐藏层级；字体字段“字体名\|字号\|样式\|颜色” |
| [Arrow Class](https://www.vensim.com/documentation/22925.html) | 普通箭头的中间点在圆弧上，排版只改这个控制点，不改端点 |
| [Vensim Conventions](https://www.vensim.com/documentation/20390.html) | 存量画方框，流量画阀门，其余变量只写名字不加框 |
| [Defined & Shadow Variables](https://www.vensim.com/documentation/22890.html)、[Existing Variable Class](https://www.vensim.com/documentation/22945.html) | 影子只引出连线，同一变量在一个视图里只定义一次 |
| [Hide/unhide](https://vensim.com/documentation/22955.html) | 隐藏变量时，进出它的箭头一起隐藏；检查和排版按同样规则跳过这些箭头 |
| [How Views Work](https://vensim.com/documentation/how_views_work.html) | 大模型按子系统分视图，视图之间用变量或影子连接 |
| [物料与信息延迟](https://www.vensim.com/documentation/mgu09_material_and_information_delays.html)、[DELAY3](https://www.vensim.com/documentation/fn_delay3.html) | 物料延迟按阶段管道存量实现，与信息平滑分开 |
| [Payoff Computation](https://www.vensim.com/documentation/payoffcomputation.html) | Vensim 的 payoff 按 TIME STEP 累积，本工具在保存点上用梯形法，两者不同 |
| [Number lines](https://www.vensim.com/documentation/20736.html) | 结果图的数字编号曲线样式 |

## 系统动力学文献

- Sterman, J. D. (2000). *Business Dynamics: Systems Thinking and Modeling for a Complex World*. McGraw-Hill. 存量—流量画法、因果回路与极性；模型检验的测试清单见其中“验证与模型检验”部分。
- Sterman, J. D. *Fine-Tuning Your Causal Loop Diagrams*, [Part I](https://thesystemsthinker.com/fine-tuning-your-causal-loop-diagrams-part-i/)、[Part II](https://thesystemsthinker.com/fine-tuning-your-causal-loop-diagrams-part-ii/). The Systems Thinker. 重要回路用圆形或椭圆路径、减少交叉、变量周围不加圆圈或六边形（Part II）；一次呈现复杂图会难以看清回路，应拆开画（Part II）。R/B 标在回路内部是本工具的排版约定。
- Richardson, G. P. (1997). Problems in causal loop diagrams revisited. *System Dynamics Review*, 13(3), 247–252. 极性用 `+`/`-`，不用 `s`/`o`。
- Rahmandad, H., & Sterman, J. D. (2012). Reporting guidelines for simulation-based research in social sciences. *System Dynamics Review*, 28(4), 396–411. 结果图的样式，以及研究报告要交代的方程、参数、积分方法和实验设置。
- Martinez-Moyano, I. J. (2012). Documentation for model transparency. *System Dynamics Review*, 28(2), 199–208. 交付时附带模型、数据和运行记录，让结果能复现。

## 真实模型语料

排版间距从 256 个公开 MDL 统计得出：Vensim 官方手册示例 25 个、Duggan 教材 59 个、SDXorg 测试模型 149 个、研究模型 23 个。收录的是其中 7 个代表模型（SDMR 5 个、SDXorg 2 个，均为 MIT 许可），见 [reference_models](../assets/reference_models/README.md)；其余模型不收录，Vensim 官方示例只用于本地统计。

- Duggan, J. (2016). *System Dynamics Modeling with R*. Springer. 配套仓库 [JimDuggan/SDMR](https://github.com/JimDuggan/SDMR)，MIT 许可。
- [SDXorg/test-models](https://github.com/SDXorg/test-models)：PySD 等工具共用的测试模型库，MIT 许可。
- Vensim 官方示例随软件发布，只用于本地统计，不收录。

## Python 工具

- [PySD](https://github.com/SDXorg/pysd)（[运行 API](https://pysd.readthedocs.io/en/master/python_api/model.html)）：第二个仿真后端，用于和内置 Euler 逐点对照。PySD 解析不了部分老版本官方示例（语法报错）和带数组的模型，这时只能用内置引擎或 Vensim。
- [SciPy differential_evolution](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.differential_evolution.html)：参数校准和政策搜索。本工具另外限制实际仿真次数。
- [Matplotlib savefig](https://matplotlib.org/stable/api/_as_gen/matplotlib.figure.Figure.savefig.html)：结果图的 DPI、矢量格式和去掉生成时间的元数据。

## 已知边界

- 内置引擎只覆盖常见标量函数（见 [SIMULATION.md](SIMULATION.md)）；数组、宏、外部数据交给 PySD 或 Vensim。
- 两个 Python 后端一致，不代表与 Vensim 逐点相同。
- 字宽、折行和箭头端点是估算值，最终结构图要在 Vensim 里打开核对后导出。
- 排版不调用 Graphviz 等外部布局器：它们不认识存量、阀门、附着流量名和单控制点圆弧，生成的坐标不能直接写回 MDL。
