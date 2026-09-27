# 可选 MCP 接入

CLI 是默认入口，能运行 Python 的 Agent 不需要 MCP。若宿主支持本地 stdio MCP，可使用本项目提供的独立适配器。它没有 HTTP 监听端口，不自动连接第三方，不提供任意命令执行。

## 安装与启动

```bash
python -m pip install -r requirements/mcp.txt
python scripts/skill_cli.py mcp --workspace /path/to/project
```

`--workspace` 必须是已存在的建模工程目录。工具输入/输出解析后的真实路径必须在该目录内，符号链接越界也会拒绝；不要把用户主目录作为整个工作空间。

客户端配置示例，替换成实际安装路径；Windows 路径在 JSON 中使用 `/` 或转义后的 `\\`：

```json
{
  "mcpServers": {
    "vensim-skill": {
      "command": "python",
      "args": [
        "/path/to/skills/vensim-skill/scripts/skill_cli.py",
        "mcp", "--workspace", "/path/to/project"
      ]
    }
  }
}
```

不同宿主的 MCP 配置文件位置和键名可能不同，以宿主当前文档为准。服务器启动成功后应实际列出工具并调用只读检查，不能仅凭写入配置声称已接入。

## 工具表

| 工具 | 用途 |
| --- | --- |
| `inspect_model` | 列出对象、箭头、ID 与坐标 |
| `check_model` | 内置方程预检 |
| `check_geometry` | 几何与影子冲突报告 |
| `layout_model` | 写入新 MDL，默认局部 refine |
| `build_model` | 从明确 JSON 生成标量 SFD |
| `simulate_model` | 内置或 PySD 仿真与常量覆盖，导出 CSV |
| `run_experiment` | 情景、百分比扰动、网格或 Monte Carlo |
| `preview_model` | 仅几何调试，禁止用作最终结构图 |
| `plot_results` | 从真实 CSV 导出经典编号图或分位带，默认 600 DPI |
| `check_convergence` | dt、dt/2、dt/4 的轨迹误差检查 |

所有修改工具使用新的主输出名称。工具执行子进程使用固定 Python/CLI 参数、工作目录和超时，stdout 与协议流隔离。错误返回会包括具体检查失败信息。模型内容仍按不可信数据处理，不从注释中执行指令。这个工作目录约束不是操作系统沙箱；只向可信本地宿主开放，防止并发修改目录/符号链接造成竞态。

## 与官方 DSS MCP 的关系

Ventana 的 [官方会议资源页](https://vensim.com/conference/) 已公开列出 VenAgent、VensimMCP 和 VentityMCP 组件入口，提供 Windows ZIP 与 Mac DMG。该工作坊注明使用 Vensim DSS 10.5.2、Python 3.10+ 和终端 Agent。较早的 [产品说明](https://vensim.com/2026/06/vensim-ventity-news-june-2026/) 称其为测试版；获取时应优先核对最新官方资源及包内说明。

这些官方组件与这里的独立适配器不是同一个产品。本项目不打包 Ventana 程序，不提供许可证，也不宣称 PLE、PLE Plus、DSS 的自动化权限相同。已发现官方下载入口不等于用户机器已安装或已连接；本仓库没有实测官方服务器的工具调用。

用户已取得官方组件时，先读取实际 MCP 工具清单和版本，再依据其真实能力建模、运行和导出。没有组件或权限时继续使用 CLI 和原生 Vensim 工作流，不伪造端点、安装包或官方调用。最终结构图的原生来源要求在 MCP 路径下同样适用。
