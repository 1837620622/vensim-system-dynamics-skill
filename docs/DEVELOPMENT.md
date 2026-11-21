# 开发与发布说明

本文件是仓库可分发的工程说明，不包含开发者机器的绝对路径、凭据或本地缓存规则。贡献者从仓库根目录执行命令；Skill 目录内的入口会按自身位置解析脚本，因此 Windows、macOS 和 Linux 不需要修改源码路径。

## 目录边界

- `skills/vensim-skill/SKILL.md` 是 Skill 入口，目录名必须与 frontmatter 的 `name` 一致。
- `skills/vensim-skill/scripts` 保存确定性解析、布局、仿真、审计和绘图代码。
- `skills/vensim-skill/assets` 放模板、三个中文示例和 `reference_models` 下的 7 个教材模型；新增参考模型必须是可再分发的许可，并附许可证全文。
- `skills/vensim-skill/references` 是按任务读取的规则与研究边界。
- `tests` 覆盖核心代码、可选后端、跨平台入口和输出安全。
- 本机专属的 `AGENTS.md`、缓存和临时仿真目录不属于发布内容。

## 本地验证

```bash
python -m pip install -r skills/vensim-skill/requirements/dev.txt
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
python -m ruff check .
python -m ruff format --check .
python -m bandit -r skills/vensim-skill/scripts -q
bash -n skills/vensim-skill/skill.sh
```

需要可选集成时，再安装对应的 `requirements/pysd.txt`、`analysis.txt` 或 `plots.txt`。这些文件共享 `requirements/constraints.txt` 的安全下限，`doctor` 会报告低于下限的已安装包。

改动排版代码后，先用旧版本构建 `assets/templates` 里的三个模板，再用新代码构建，MDL 应逐字节相同（性能改动不能改变排版结果）；除了 `pytest`，还要用 `reference_models` 里的模型看一遍图：`layout --mode auto` 不应重画作者版面合理的模型，`--mode natural` 的结果要零缺陷。`tests/test_reference_models.py` 覆盖其中较快的几项。

Python 内置 Euler 是默认、可复现的仿真通道。支持的标量模型可以用 PySD 做逐点对照；比较前必须使用相同的时间网格、变量、初值、参数和保存间隔。原生 Vensim 仍是 MDL 语义、单位、结构图和未覆盖函数的最终核对来源，跨 Python 后端一致不等于已经证明与所有原生模型逐点等价。

## 发布前检查

1. 确认 `pyproject.toml`、`skills/vensim-skill/SKILL.md`、两份 README 的版本徽章和 `CHANGELOG.md` 最新条目一致。
2. 确认 `git status --short --ignored` 没有缓存、构建产物、临时仿真输出或凭据。
3. 运行 `npx skills add . --list`，确认入口可发现。
4. 运行 `gh auth status`，确认发布账号和 Git 提交邮箱符合仓库贡献规则；核对最后一个提交作者。
5. 推送后检查 GitHub Actions、发布标签、下载包内容和本机安装目录。远程任务未启动时不能把矩阵配置写成 CI 已通过。

## 许可证

源码公开，个人学习和非商业研究免费；商业使用（收费建模或咨询、付费客户交付物、收费产品或服务、出售副本）须事先向作者购买商业授权，联系 2040168455@qq.com 或在 GitHub 1837620622 的仓库提交 issue。复制和非商业修改必须保留作者署名与许可证。详见 `LICENSE`。
