# 开发与发布说明

本文件是仓库可分发的工程说明，不包含开发者机器的绝对路径、凭据或本地缓存规则。贡献者从仓库根目录执行命令；Skill 目录内的入口会按自身位置解析脚本，因此 Windows、macOS 和 Linux 不需要修改源码路径。

## 目录边界

- `skills/vensim-skill/SKILL.md` 是 Skill 入口，目录名必须与 frontmatter 的 `name` 一致。
- `skills/vensim-skill/scripts` 保存确定性解析、布局、仿真、审计和绘图代码。
- `skills/vensim-skill/assets` 只放不含当前项目业务假设的模板与回归示例。
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

需要可选集成时，再安装对应的 `requirements/pysd.txt`、`analysis.txt`、`plots.txt` 或 `mcp.txt`。这些文件共享 `requirements/constraints.txt` 的安全下限；MCP 低于声明下限时应由 `doctor` 拒绝启动，而不是降级运行。

Python 内置 Euler 是默认、可复现的仿真通道。支持的标量模型可以用 PySD 做逐点对照；比较前必须使用相同的时间网格、变量、初值、参数和保存间隔。原生 Vensim 仍是 MDL 语义、单位、结构图和未覆盖函数的最终核对来源，跨 Python 后端一致不等于已经证明与所有原生模型逐点等价。

## 发布前检查

1. 确认 `pyproject.toml` 与 `skills/vensim-skill/SKILL.md` 的版本一致。
2. 确认 `git status --short --ignored` 没有缓存、构建产物、临时仿真输出或凭据。
3. 运行 Skill 格式校验与 `skills add . --list`，确认入口可发现。
4. 运行 `gh auth status`，确认发布账号和 Git 提交邮箱符合仓库贡献规则；核对最后一个提交作者。
5. 推送后检查 GitHub Actions、发布标签、下载包内容和本机安装目录。远程任务未启动时不能把矩阵配置写成 CI 已通过。

## 许可证

仓库与 Skill 均为非商业许可。商业使用、付费再分发、商业服务集成和商业客户交付均不允许；复制和非商业修改必须保留作者署名与许可证。
