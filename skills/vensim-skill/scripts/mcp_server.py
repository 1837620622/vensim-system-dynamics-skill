"""可选的本地 stdio MCP 适配器；与 Ventana 官方 DSS MCP 是独立实现。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess  # nosec B404
import sys


def create_server(workspace):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace 必须是目录")
    server = FastMCP("vensim-skill", instructions="本项目为独立、非商业的 Vensim 工具。输出位于 workspace；native_verified=false 不代表原生软件已验证。")
    cli = Path(__file__).with_name("skill_cli.py")

    def path(value, suffixes, output=False):
        candidate = (root / value).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("路径必须位于指定 workspace 内")
        if suffixes and candidate.suffix.lower() not in suffixes:
            raise ValueError("文件扩展名不符合工具要求")
        if output and candidate.exists():
            raise ValueError("输出已存在，请使用新的文件名")
        if not output and not candidate.is_file():
            raise ValueError("输入文件不存在")
        return str(candidate)

    def invoke(arguments):
        # 不接收任意命令，不通过 shell；子进程隔离 stdout，保护 MCP 协议流。
        completed = subprocess.run(  # nosec B603
            [sys.executable, str(cli), *arguments], cwd=root,
            capture_output=True, text=True, encoding="utf-8", timeout=180,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        if completed.returncode:
            raise ValueError((completed.stderr + completed.stdout)[-12000:])
        return {"stdout": completed.stdout[-16000:], "native_verified": False}

    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write_new = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    @server.tool(annotations=read_only)
    def inspect_model(model: str) -> dict:
        """读取 MDL 的对象、箭头端点和控制点。"""
        return invoke(["inspect", path(model, {".mdl"})])

    @server.tool(annotations=read_only)
    def check_model(model: str) -> dict:
        """使用内置引擎预检模型；完整语法和量纲仍由原生 Vensim 核对。"""
        return invoke(["check", path(model, {".mdl"})])

    @server.tool(annotations=read_only)
    def check_geometry(model: str) -> dict:
        """读取圆弧穿字、交叉、重叠和未覆盖形状的 JSON 报告。"""
        return invoke(["visual", path(model, {".mdl"})])

    @server.tool(annotations=write_new)
    def layout_model(model: str, output: str, mode: str = "refine", style: str = "preserve") -> dict:
        """保护方程和拓扑，整理信息线；preserve 模式只改圆弧。"""
        if mode not in {"auto", "preserve", "refine", "graphviz"}:
            raise ValueError("无效布局模式")
        if style not in {"preserve", "monochrome", "native-blue"}:
            raise ValueError("无效箭头样式")
        return invoke(["layout", path(model, {".mdl"}), "--output", path(output, {".mdl"}, True), "--mode", mode, "--style", style])

    @server.tool(annotations=write_new)
    def build_model(spec: str, output: str) -> dict:
        """从用户已明确的方程、单位、初值和流向 JSON 生成 SFD。"""
        return invoke(["build", path(spec, {".json"}), "--output", path(output, {".mdl"}, True)])

    @server.tool(annotations=write_new)
    def simulate_model(model: str, output: str, variables: list[str], parameters: dict[str, float] | None = None, backend: str = "builtin") -> dict:
        """以内置 Euler 或可选 PySD 运行模型并导出 CSV；参数覆盖不改源模型。"""
        if backend not in {"builtin", "pysd"}:
            raise ValueError("无效仿真后端")
        arguments = ["simulate", path(model, {".mdl"}), "--output", path(output, {".csv"}, True), "--backend", backend]
        for variable in variables:
            arguments.extend(["--var", variable])
        for name, value in (parameters or {}).items():
            arguments.extend(["--set", f"{name}={value}"])
        return invoke(arguments)

    @server.tool(annotations=write_new)
    def run_experiment(model: str, spec: str, output_directory: str) -> dict:
        """运行情景、百分比扰动、网格或固定种子 Monte Carlo，保存数据及元数据。"""
        return invoke(["experiment", path(model, {".mdl"}), "--spec", path(spec, {".json"}),
                       "--output-dir", path(output_directory, set(), True)])

    @server.tool(annotations=write_new)
    def preview_model(model: str, output: str) -> dict:
        """仅生成几何调试预览。禁止作为最终结构图，最终图必须在原生 Vensim 导出。"""
        return invoke(["preview", path(model, {".mdl"}), "--output", path(output, {".html", ".svg"}, True)])

    @server.tool(annotations=write_new)
    def plot_results(csv_file: str, output: str, variables: list[str], time_unit: str = "", dpi: int = 600, style: str = "auto") -> dict:
        """从真实 CSV 出单变量图件，默认无标题、编号曲线和底部图例；多变量分别保存。"""
        if style not in {"auto", "classic", "band"}:
            raise ValueError("无效结果图样式")
        from result_plotting import output_paths
        target = path(output, {".png", ".svg", ".pdf"}, True)
        plots, manifest = output_paths(Path(target), variables)
        for extra in [p for _, p in plots] + [manifest]:
            path(str(extra), set(), True)
        arguments = ["plot-data", path(csv_file, {".csv"}), "--output", target, "--time-unit", time_unit,
                     "--dpi", str(dpi), "--plot-style", style]
        for variable in variables:
            arguments.extend(["--var", variable])
        return invoke(arguments)

    @server.tool(annotations=write_new)
    def check_convergence(model: str, output: str, variables: list[str], tolerance: float = 0.01) -> dict:
        """在同一输出网格比较 dt、dt/2、dt/4 的轨迹误差，不能替代原生单位检查。"""
        arguments = ["convergence", path(model, {".mdl"}), "--output", path(output, {".json"}, True), "--tolerance", str(tolerance)]
        for variable in variables:
            arguments.extend(["--var", variable])
        return invoke(arguments)

    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    create_server(args.workspace).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
