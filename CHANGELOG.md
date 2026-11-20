# 更新记录

## 2.5.0

许可证
- 源码继续公开；个人学习、课程作业、学位论文和非商业学术研究免费使用。
- 商业使用（收费建模或咨询、付费客户交付物、收费产品或服务、出售副本）须事先购买商业授权，联系邮箱 2040168455@qq.com 或在 GitHub 1837620622 的仓库提交 issue。
- 以前版本的副本继续适用各自随附的许可证。

仿真引擎
- 新增 `SAMPLE IF TRUE`、`INITIAL`、`ACTIVE INITIAL`、`SMOOTH N`、`DELAY N`、`TREND`、`FORECAST`、`PULSE TRAIN`、`QUANTUM`、`POWER`、`LOG(x,base)`、`LOOKUP EXTRAPOLATE/FORWARD/BACKWARD`、反三角与双曲函数；与 PySD 3.14 逐点一致。
- 平滑、延迟族可以嵌在表达式里（如 `2 * SMOOTH(x, 3)`），以前会报语法错误。
- `NPV`、`RANDOM` 系列、`GET XLS` 数据函数、数组函数等不支持的函数，报错时写明函数名并提示改用 `crosscheck`（PySD）或 Vensim。
- 每个方程只解析一次，变量名替换的正则按模型缓存，查表改用二分查找。

速度
- `build` 的极性数值求偏导不再为每次求值重新编译全部变量名的正则。
- natural 排版的代价函数先用外接框排除不可能相交的线段和文字框，箭头几何在退火回退时复用；排版结果与 2.4.0 逐字节相同。
- 多视图模型按 CPU 数并行排版（`VENSIM_SKILL_WORKERS` 可指定进程数，设为 1 关闭），结果与顺序排版相同；进程池不可用时自动退回顺序执行。

因果回路图
- 新命令 `cld`：用建模 JSON 生成每个子系统一张 CLD 和一张总体 CLD；变量为无框文字，连线标 `+/-`，核对通过的回路画 R/B 标记。

结果图
- 默认样式改为 Vensim DSS 风格：白底黑框、曲线数字编号、Vensim 序列色、整数年份刻度，PNG 默认 300 dpi；新增 `x_label`、`integer_time_ticks` 配置。
- 新命令 `vensim-script`：生成 `.cin` 情景变更文件、`.vgd` 自定义图和 `.cmd` 命令脚本，在 Vensim DSS 中出原生结果图。
