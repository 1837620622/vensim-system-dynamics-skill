# Vensim Skill 项目记忆

## 项目范围

- 项目根目录：`/Users/chuankangkk/Downloads/vensim-skill`
- GitHub 仓库：`1837620622/vensim-system-dynamics-skill`
- Skill 发布目录：`skills/vensim-skill`
- Skill 入口文件：`skills/vensim-skill/SKILL.md`
- 主要工具目录：`skills/vensim-skill/vensim_system_dynamics/tools`
- 示例模型目录：`skills/vensim-skill/vensim_system_dynamics/examples`

## 本地运行

从 skill 目录运行 CLI：

```bash
cd skills/vensim-skill
./skill.sh doctor
./skill.sh check vensim_system_dynamics/examples/population_demo.mdl
./skill.sh simulate vensim_system_dynamics/examples/population_demo.mdl --var Population
```

Windows 入口为：

```cmd
skills\vensim-skill\skill.cmd doctor
```

## 测试与校验

从项目根目录运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider
python3 -m ruff check .
python3 -m bandit -r skills/vensim-skill/vensim_system_dynamics/tools -q
python3 -m py_compile skills/vensim-skill/vensim_system_dynamics/tools/vensim_autolayout.py skills/vensim-skill/vensim_system_dynamics/tools/vensim_engine.py skills/vensim-skill/vensim_system_dynamics/tools/skill_cli.py
bash -n skills/vensim-skill/skill.sh
python3 /Users/chuankangkk/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/vensim-skill
gh skill publish --dry-run
skills add . --list
```

## 发布流程

发布前必须确认：

- `gh auth status` 当前账号为 `1837620622`
- `gh --version` 支持 `gh skill publish`
- `skills --version` 可用
- `git status --short --ignored` 中没有缓存、构建产物或临时仿真输出

发布顺序：

```bash
git add README.md AGENTS.md tests skills/vensim-skill
git commit -m "Prepare Vensim skill for publishing"
git push origin main
gh repo edit 1837620622/vensim-system-dynamics-skill --add-topic agent-skills,skills-sh,vensim,system-dynamics,simulation,graphviz,pysd,mdl,cld,sfd,stock-flow-diagram,causal-loop-diagram,scenario-analysis,policy-analysis,data-visualization,ai-skill,python
gh skill publish --tag v1.0.0
```

skills.sh 安装验证：

```bash
skills add 1837620622/vensim-system-dynamics-skill --list
```

## 缓存与清理边界

可以清理：

- 项目内 `.pytest_cache`、`.ruff_cache`、`__pycache__`
- 自动排版或仿真产生的 `*.backup.mdl`、`*.layout_report.json`、`*.3vmfx`、`*.vdf`、`*.vdfx`
- Homebrew 下载缓存：`brew cleanup -s --prune=all`

不要清理：

- Android SDK、Gradle、Kotlin、AVD、Android Studio 索引缓存
- 用户全局 Python、Node、npm、Homebrew 已安装包目录
- Vensim 原始 `.mdl` 模型文件

## 已知风险

- 纯 Python 仿真引擎只覆盖 Vensim 常见子集，复杂数组、宏、外部数据和高级函数仍需回到 Vensim 或 PySD。
- 自动布局是保守回写，不保证完全无交叉；输出后必须在 Vensim 执行 `Check Model` 与 `Units Check`。
- `gh skill publish` 要求 skill 目录名和 `SKILL.md` 的 `name` 一致，因此不要把发布入口放回仓库根目录。
