# 更新记录

## 2.7.0

安全
- PySD 后端翻译前清空方程注释字段，方程区含三引号时拒绝翻译。以前恶意 MDL 的注释会被 PySD 写进生成代码的 docstring，可借此执行任意代码。
- `vensim-script` 写进 `.vgd` 和 `run_all.cmd` 的情景名、标题前缀、变量名、单位和模型路径遇到换行等控制字符直接报错，防止注入额外的 Vensim 命令行。
- `skill.cmd` 设置 `NoDefaultCurrentDirectoryInExePath=1`，不再执行当前目录里的同名 `py`/`python`。
- CI：每个作业加 `timeout-minutes`，`actions/checkout` 设 `persist-credentials: false`，动作仍按 SHA 固定。

已有模型补极性
- `fix --apply-polarity` 给已有 MDL 的未标号箭头写入 `+`/`-`：只写方程推导或基准运行数值偏导推得出的符号，推不出的原样保留并列在 `unsigned_remaining`；方程区逐字节不变。随附的 teacup、overshoot 补写后 `check --strict` 通过。
- `feedback` 对未标号箭头给出 `suggested`（建议符号）和 `suggested_source`（`equation` 或 `numeric`）；端点推不出符号的箭头（如 `Time`）列在 `unsigned_unmapped`。
- `check` 的无极性提示同时指向 `links` 和 `fix --apply-polarity`。

检查与极性
- `check` 把整条方程转成语法树逐个核对名字，未执行分支里的未定义变量也会报错。
- `check` 的极性检查按阀门所附流量名核对指向阀门的箭头，隐藏箭头和隐藏对象上的箭头不再误报。
- `SMOOTHI`、`DELAY1I`、`DELAY3I` 等带初值的变体，输出不再被当成输入值参与乘除。
- 数值偏导逐个保存时刻计算，不再抽样漏掉只在部分时刻翻号的作用；标注符号与数值偏导全程相反时记为冲突。
- `DELAY FIXED` 的输入箭头按正向推导，回路核对不再出现符号缺失却记为 verified 的情况。
- `cld` 接受 `S`/`O` 作为 `+`/`-` 的同义写法。

退出码与命令行
- `build`、`cld` 有箭头缺极性时退出码为 1（文件照常写出），与 `check --strict` 一致；草稿阶段可加 `--allow-unsigned`。
- 新增 `--version`。
- 顶层帮助补上 `layout` 的默认 `auto` 模式和 `--config`，`--var` 标为可选；各子命令帮助的 usage 显示 `skill.sh 命令`；`calibrate` 与 `optimize` 帮助分开说明。
- `vensim-script` 帮助与实际输出一致：参数为空的情景就是基准、不出 `.cin`，没有这样的情景时另加名为 `base` 的基准运行；README.txt 文案修正。

排版
- 单个视图排版有时间上限，默认 180 秒，环境变量 `VENSIM_SKILL_LAYOUT_SECONDS` 可调（0 表示不限），到时返回当前最好的版面。
- 草图字段切分改为线性时间，长引号字段不再出现二次方耗时。
- 新增 `requirements/layout.txt`（networkx，平面性检查用）。

许可与打包
- LICENSE 标识改为 `LicenseRef-Vensim-Skill-SourceAvailable`，标题和 README 徽章改为“源码公开，商业使用须付费授权”，条款内容不变。联系方式 2040168455@qq.com、GitHub 1837620622。
- `pyproject.toml` 改用 PEP 639 许可字段并加 hatchling 构建配置：`pip wheel .` 打出的包含整个技能目录（`vensim_skill`），并提供 `vensim-skill` 命令。

测试
- 新增 `tests/test_release_270.py`，覆盖补极性、退出码、`--version`、帮助、`vensim-script` 输入清洗、发布物检查和 2.6.x 审计补丁的回归。

## 2.6.0

极性（原则性要求）
- 每条信息箭头都要标 `+` 或 `-`。`build` 的 `sketch.polarity` 默认改为 `all`，常量引出的箭头和存量初值箭头也标号；`dynamic`、`declared` 只用于草稿。
- `build` 把方程和数值偏导都推不出的箭头列进报告 `unsigned_arrows`，逐条给出可粘贴的 `links` 条目；`cld` 同样提示必须补写。
- `check` 新增无极性箭头警告，`--strict` 下直接失败。
- 方程推导新增 `IF THEN ELSE`（条件不含来源变量时取两个分支的并集）和 `ZIDZ/XIDZ`（分母为常量）。
- 示例模板 `inventory_zh` 补写 `发货时间 → 销售` 为 `-`，三个随附示例按新规则重新生成。

因果回路图
- 子系统 CLD 里的影子变量只作原因，不再接收箭头。

其他
- 命令行里的许可说明改为与 LICENSE 一致：源码公开，个人学习与非商业研究免费，商业使用须付费授权。

## 2.5.0

- Windows 上模型和输出目录不在同一盘符时，vensim-script 改写绝对路径，不再报 ValueError。

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

## 2.4.0

排版
- `build` 把核对过的 R/B 回路标记作为排版节点，拉进所属回路；圆圈方向随回路走向，报告 `marker_check` 记录每个标记是否在回路内。
- 存量链行序按连线长度选择，次优行序也参与改进。
- 中文存量名十个字以内排一行，更长的分成长度相近的两行。
- 只被一个视图使用的常量移到那个视图。

极性
- 自动写入箭头极性；方程推不出的（如乘积）用基准运行数值偏导（`sketch.polarity`: `dynamic`、`declared` 或 `all`）。
- `build` 给出可核对的回路建议和可粘贴的条目。

未使用变量
- `build` 遇到未使用的常量直接报错，未使用的输出标 `:SUPPLEMENTARY`，Vensim 不再提示 USE FLAG。
- `check` 报告同样的情况，`--strict` 下警告也算失败；`fix --mark-supplementary` 标记已有模型里的输出指标，不替常量掩盖。

技能
- 新增 `ev_adoption_zh` 模板（3 个存量、4 条回路、23 个变量）及示例 MDL 和 README 图；inventory、water 模板补写回路。
- SPECIFICATIONS 新增“照模板写模型”一节；`tests/test_build_polish.py` 新增 21 个测试。
