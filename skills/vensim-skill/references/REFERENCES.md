# 实现依据与已知边界

## 结果图与出版要求

- [Vensim Number lines](https://www.vensim.com/documentation/20736.html)：编号是沿曲线放置的字符，PLE 可开关；不是 DSS 独有的出图能力。
- [Vensim Graph 工具](https://www.vensim.com/documentation/ref_graph_tool.html)：图例、字体、标记与数据集比较；曲线太多时需要重组。
- [Matplotlib savefig](https://matplotlib.org/stable/api/_as_gen/matplotlib.figure.Figure.savefig.html)：位图 DPI、矢量格式与元数据接口。
- [Nature 最终图件要求](https://www.nature.com/nature/for-authors/final-submission)：优先矢量线稿、统一字体；这是该出版物要求，不当作所有中文论文的统一规则。

本项目采用可配置的经典结果图样式；参考图只决定外观，不提供可挪用的仿真数据。

优先使用官方文档与原始实现。公开版本线为 Vensim 10.5，官方会议资源注明 DSS 10.5.2；本机原生验收使用 PLE 10.5.0。不同页面可能更新不同步，不能把旧下载页或单一版本号当作所有产品通道的最新状态。具体版本、权限与 MCP 工具以实际安装环境为准。

## Vensim

- [产品版本及 DSS MCP 说明](https://vensim.com/2026/06/vensim-ventity-news-june-2026/)：桌面 DSS MCP 需要取得官方组件；不能假设任意 PLE 安装都有 MCP。
- [官方会议资源](https://vensim.com/conference/)：DSS 10.5.2 工作坊与 VenAgent/VensimMCP/VentityMCP 的 Windows、Mac 分发入口；已核对入口，未安装或实测官方服务器。
- [原生版本说明](https://www.vensim.com/documentation/vensim-10_4_x.html)：核对 10.4–10.5 系列的实际功能变化，不能仅根据页面 URL 判断版本。
- [MDL 文件](https://www.vensim.com/documentation/_mdl_model_files.html) 与 [Sketch Format](https://www.vensim.com/documentation/ref_sketch_format.html)：方程、草图与设置是不同区段，布局不改方程。
- [Sketch Object Detail](https://www.vensim.com/documentation/24305.html)：坐标为中心，尺寸为半宽/半高；对象 ID 在各 View 内解释。颜色是否生效由 hasf 决定，极性、延迟、隐藏字段不能清零；字体内部可能含 `|`。bits 的第七位是 64，第八位是 128，不能混淆位序和数值。注释、IO 和图片对象可能有续行。
- [Arrow Class](https://www.vensim.com/documentation/22925.html)：普通 Arrow 的中间点位于圆弧上；Polyline、Perpendicular、Spline 不能统一当作单圆弧。改变几何不应改变因果端点。
- [Defined & Shadow Variables](https://www.vensim.com/documentation/22890.html) 与 [Existing Variable Class](https://www.vensim.com/documentation/22945.html)：影子允许多实例、只能出线。Defined 的原因会由软件补齐，缺少可见输入可能造成自动补影子。影子本身不是方程错误。
- [视图菜单](https://www.vensim.com/documentation/viewmenu.html)：初始原因箭头、影子及字体显示有独立设置；不应靠全局隐藏掩盖结构问题。
- [DELAY3](https://www.vensim.com/documentation/fn_delay3.html) 与 [物料和信息延迟](https://www.vensim.com/documentation/mgu09_material_and_information_delays.html)：物料延迟使用管道存量和阶段流出，信息平滑使用信息状态，两者不能混用。
- [模型语法与单位检查](https://www.vensim.com/documentation/20405.html)：完整验证回到原生 Check Model 和 Units Check。

## 官方示例与视觉方法

- John Sterman：[Fine-Tuning Your Causal Loop Diagrams, Part II](https://thesystemsthinker.com/fine-tuning-your-causal-loop-diagrams-part-ii/)：圆弧、反馈环的可读形状、减少交叉、拆分复杂图、保留有意义延迟。这里据此使用局部位置和短圆弧，不把规则简化为统一网格或统一大弯。
- MIT：[Business Dynamics](https://mitmgmtfaculty.mit.edu/jsterman/business-dynamics/)：动态假设、结构、边界与行为验证应早于研究结论。
- SDXorg 原生模型样本：[teacup.mdl](https://github.com/SDXorg/test-models/blob/master/samples/teacup/teacup.mdl)：用于交叉核对流量阀门与存量连接的记录形态。构造流出管道时，因果记录仍从阀门到来源存量，`shape=100` 表达反向的管道图形；这不是将普通信息箭头反向。该样本属于 SDXorg 测试材料，不冒称 Ventana 官方发布。

官方教程决定基本语义；本项目的具体位置、启发式权重和样式选择是工程实现，不是官方推荐算法或视觉认证。库存示例为本项目构造，并在原生 Vensim 核对，不是临摹图代替模型。

## 参数校准与政策搜索

- [Vensim 优化功能](https://www.vensim.com/documentation/usr18.html) 与 [Optimization](https://www.vensim.com/documentation/ref_optimization.html)：区分数据校准、政策搜索、常量参数与目标。
- [Payoff Computation](https://www.vensim.com/documentation/payoffcomputation.html)：原生政策 payoff 按 TIME STEP 累积，不能将保存点上的梯形统计声称为相同分数。
- [SciPy differential_evolution](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.differential_evolution.html)：有界差分进化、`rng`、种群乘数与非线性约束。本工具另加实际仿真次数上限，不直接把 SciPy 代数当作运行次数。

实现与统计边界见 [Python 高级分析](ADVANCED_ANALYSIS.md)。

## Graphviz

[属性参考](https://graphviz.org/doc/info/attrs.html) 和 [FAQ](https://graphviz.org/faq/) 说明坐标、pin 和布局后处理的边界。Graphviz 的位置单位需换算，固定节点仍可能受坐标变换影响。本工具只取节点位置建议，再依据原生圆弧路由；不直接写入 Graphviz 样条，不把 Graphviz 的图作为最终结构图。

新建默认 circular 与已有图的局部 refine 都不依赖 Graphviz。[Graphviz circo](https://graphviz.org/docs/layouts/circo/) 可作为多循环结构的布局参考，`oneblock` 可以控制是否强制同圆；本项目的 circular 为独立标准库实现，保留 SFD 骨架并把外围参数放在作用对象附近，不调用 circo，也不宣称复现其算法。任何全局模式都不保证消除所有交叉。超复杂视图应拆分，工具报告不能替代原生视觉验收。

## PySD 与 MCP

- [PySD 源码](https://github.com/SDXorg/pysd) 和 [运行 API](https://pysd.readthedocs.io/en/master/python_api/model.html)：模型参数覆盖、原始初值与时间设置。对照实现必须使用相同方程、参数、初值与保存时间点。
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)：本项目使用 1.x FastMCP 的 stdio 传输、工具注解和客户端集成测试。SDK 主分支文档可能先于稳定版变化，因此依赖显式限制 `<2`。

PySD 翻译成功不证明与所有 Vensim 函数语义一致。本封装只承诺已测试的自包含标量路径。独立 MCP 适配器不能冒充官方 DSS MCP，且本地目录限制不能替代操作系统沙箱。
