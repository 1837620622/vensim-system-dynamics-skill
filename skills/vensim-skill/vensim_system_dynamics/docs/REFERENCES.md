# 实现依据与限制

本技能依据以下官方/原始文档设计与实现。访问日期：2026-06-25。

## 1. Vensim 官方文档

### .mdl Model Files
https://www.vensim.com/documentation/_mdl_model_files.html
- `.mdl` 由方程组、宏、方程、草图信息、设置组成；草图信息附加在末尾。

### Sketch Format
https://www.vensim.com/documentation/ref_sketch_format.html
- 草图以 `\\\---///` 开始，每 view 一段；`V300` 版本码；`*View Name` 视图名；`$...` 视图默认字体/颜色。
- 对象行首为类型码：`10`=变量(word)，`11`=流率阀门(flow arrow)，`12`=源/汇/图/注释，`1`=箭头，`30/31`=其他。
- 变量字段：`10,id,name,x,y,w,h,shape,bits,hid,hasf,tpos,thick,...`；`x,y` 为中心坐标(像素，x右y下)；`w,h` 为**半宽/半高**。
- `shape` 低 5 位为形状；bit6(32)=附着到阀门；bit7=形状由类型决定。
- `bits`(官方称 arrows_in_allowed)：bit1(1)=允许入箭头，bit2(2)=允许出箭头，bit3(4)=有注释续行，bit4(8)=IO 对象，bit7(128)=因果不穿透，bit8(256)=用户设定尺寸；**偶数 = shadow variable**。
- 箭头字段顺序：`1,id,from,to,shape,hid,pol,thick,hasf,dtype,res,color,font,np|plist`；`from,to` 为对象 ID；`thick` 为线宽(物理流=22，信息箭头=0)；`np` 为控制点个数；`|(x,y)|` 为控制点列表。

### Sketch Object Detail
https://www.vensim.com/documentation/24305.html
- 对象坐标为中心点；箭头 from/to 必须是本 view 内已存在对象 ID；控制点格式错一个箭头就会漂浮/反向/穿变量。
- IO 对象：bits 的 bit4(8) 置位；`tpos` 0=slider、1=custom graph、2=variable+workbench tool；下一行为定义 `variable,min,max,increment`(滑块)或图形名。
- 注释续行：bits 的 bit3(4) 置位时，注释文字出现在对象行的下一行。

### Arrow Class
https://www.vensim.com/documentation/22925.html
- 普通 Arrow：中间一个控制点 → 平滑圆弧；拖动中点手柄改弧度。
- Spline Arrow：按住 `Command/Ctrl` 绘制。
- Perpendicular Arrow：按住 `Shift`。
- Polyline Arrow：最多 16 个中间点。
- 重塑已有箭头只影响外观，不改变因果结构；新建/删除箭头会改变结构。

### Layout Menu
https://www.vensim.com/documentation/layoutmenu.html
- Vensim 原生 Layout 只做对齐、统一大小、水平/垂直等间距，**没有**全图自动布局或自动避让。

### Checking Model Syntax and Units
https://www.vensim.com/documentation/20405.html
- 模型运行前必须执行 `Check Model` 与 `Units Check`。

## 2. Graphviz
https://graphviz.org/docs/attrs/splines/
- `splines=true` 让边以样条路由并绕开节点。本技能只用 `dot`/`neato` 求节点坐标，**不**把 Graphviz 的 SVG/PDF 边路径直接写入 Vensim（那只是图片，无法仿真编辑）。
- `dot` 适合分层系统图(SFD)；`neato`/`fdp` 更适合关系网图(CLD)。

## 3. PySD（辅助交叉验证，不替代 Vensim）
https://pysd.readthedocs.io/en/master/structure/vensim_translation.html
- PySD 用 PEG 语法(`sketch.peg`)解析 `.mdl` 草图。源码：https://github.com/SDXorg/pysd
- 交叉验证结论：
  - `var_code = ^10(?=,)`、`arrow_code = ^1(?=,)`、`flow_arrow_code = ^11(?=,)`，与官方类型码一致。
  - `SketchVisitor.visit_var_definition` 用 `arrows_in_allowed`(bits 字段) 偶数判定 shadow variable，与官方 "Defined & Shadow Variables" 一致。
  - 物理流箭头以 `11` 开头(flow_arrow_code)，信息箭头以 `1` 开头(arrow_code)；本技能进一步用 `thick` 字段区分管道段与信息线。
- 最终编辑与验证仍须在 Vensim 中完成；不用 PySD 输出替代 Vensim 原始模型文件。

### Defined & Shadow Variables
https://www.vensim.com/documentation/22890.html
- Defined 变量：本视图定义，有入箭头(显示全部 causes)；Shadow 变量：别处定义，无入箭头，显示为 `<Name>`，默认灰色。
- 一个变量在一个视图只能 Defined 一次，可多次 Shadow；Shadow 不可接入箭头，只可发出箭头。

## 4. 重要限制

Vensim 官方明确指出 Sketch Information 并非面向用户直接修改。`vensim_autolayout.py` 是保守工具：
- 不改写方程区；
- 不新建/删除对象；
- 不改变箭头 from/to；
- 默认锁定库存、阀门(11)、源汇云(12)、流率标签、shadow variable、控制面板对象；
- 只移动普通辅助变量，只重设信息箭头中间控制点；
- 物理流率管道(thick≥20 或端点为阀门/云)保持原样。

任何输出必须在目标 Vensim 版本中重新打开并运行 Check Model 与 Units Check 后方可使用。出现错位/浮动箭头/阀门脱离时立即恢复 `*_backup.mdl` 改用手动调整。
