"""仿真结果曲线图：折线或分位带，数据原样绘制，不加标题和水印。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
from pathlib import Path
from types import SimpleNamespace

from mdl_document import atomic_write, preflight_outputs

FORMATS = {"png", "pdf", "svg"}
CONFIG_TEMPLATE = Path(__file__).resolve().parents[1] / "assets/templates/plot_config.json"

# ---------------------------------------------------------------------------
# 命令行选项与样式配置
# ---------------------------------------------------------------------------


def plot_options(parser, style=False):
    parser.add_argument("--dpi", type=int, default=600, help="PNG 分辨率，72–1200，默认 600")
    parser.add_argument("--formats", help="额外导出格式，逗号分隔，如 png,pdf,svg")
    parser.add_argument("--plot-config", type=Path, help="样式 JSON，字段见 plot_config.json")
    if style:
        parser.add_argument("--plot-style", choices=["auto", "lines", "band"], default="auto")


def _number(value, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)):
        return False
    return math.isfinite(value) and low <= value <= high


def load_plot_config(path=None):
    """读取默认样式并按用户 JSON 覆盖；未知字段和越界数值直接报错。"""
    config = json.loads(CONFIG_TEMPLATE.read_text(encoding="utf-8"))
    if path:
        overrides = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(overrides, dict) or set(overrides) - set(config):
            raise ValueError("plot-config 含未知字段，可用字段见 plot_config.json")
        config.update(overrides)
    ranges = {
        "width_inches": (3, 14),
        "height_inches": (2, 10),
        "font_size": (6, 24),
        "legend_font_size": (6, 20),
        "line_width": (0.2, 4),
        "marker_size": (3, 15),
    }
    for key, (low, high) in ranges.items():
        if not _number(config[key], low, high):
            raise ValueError(f"{key} 必须在 {low} 到 {high} 之间")
    for key, low, high in (("marker_repeats", 1, 20), ("max_lines", 1, 30)):
        if not _number(config[key], low, high, integer=True):
            raise ValueError(f"{key} 必须为 {low} 到 {high} 的整数")
    for key in ("band_outer_alpha", "band_inner_alpha"):
        if not _number(config[key], 0.01, 1):
            raise ValueError(f"{key} 必须在 0.01 到 1 之间")
    for key in ("number_lines", "show_grid"):
        if not isinstance(config[key], bool):
            raise ValueError(f"{key} 必须为布尔值")
    if not isinstance(config["font_family"], list) or not all(
        isinstance(name, str) and name for name in config["font_family"]
    ):
        raise ValueError("font_family 必须为字体名列表，空列表表示自动选择")
    styles = config["line_styles"]
    if (
        not isinstance(styles, list)
        or not styles
        or any(s not in ("-", "--", "-.", ":") for s in styles)
    ):
        raise ValueError("line_styles 必须是非空列表，只能取 -、--、-.、:")
    colors = config["colors"]
    hex_color = re.compile(r"#[0-9a-fA-F]{6}")
    if (
        not isinstance(colors, list)
        or not colors
        or not all(
            isinstance(c, str) and hex_color.fullmatch(c)
            for c in [*colors, config["grid_color"], config["border_color"]]
        )
    ):
        raise ValueError("颜色必须写成 #RRGGBB，colors 至少一种")
    return config


def output_paths(output, variables, formats=None):
    output = Path(output)
    extension = output.suffix.lower().lstrip(".")
    extensions = list(dict.fromkeys([extension, *(formats.split(",") if formats else [])]))
    if any(ext not in FORMATS for ext in extensions):
        raise ValueError("结果图只接受 .png、.pdf、.svg；--formats 使用 png,pdf,svg")
    if not variables or len(set(variables)) != len(variables):
        raise ValueError("绘图变量必须非空且不重复")
    paths = []
    for index, variable in enumerate(variables):
        # 多变量独立出图；变量名不参与文件路径，兼容 Windows 保留字符。
        stem = output.stem if len(variables) == 1 else f"{output.stem}_{index + 1:02d}"
        paths.extend(
            (variable, output.with_name(f"{stem}.{extension}")) for extension in extensions
        )
    return paths, output.with_suffix(".plot.json")


def validate_results(results, variables):
    if not results or len(results) > 200:
        raise ValueError("绘图需要 1 到 200 个完整情景")
    labels = [label for label, _ in results]
    if len(set(labels)) != len(labels) or any(
        not isinstance(label, str) or not label.strip() for label in labels
    ):
        raise ValueError("情景标签必须非空且不重复")
    for _, result in results:
        if not result.times or any(not math.isfinite(t) for t in result.times):
            raise ValueError("Time 必须为有限数值且非空")
        if any(b <= a for a, b in zip(result.times, result.times[1:], strict=False)):
            raise ValueError("Time 必须严格递增，不能重复或乱序")
        if getattr(result, "metadata", {}).get("status") == "diagnostic_only":
            raise ValueError("该结果是 --keep-going 生成的诊断结果，含求值失败，不能出图")
        for variable in variables:
            values = result.series.get(variable, [])
            if len(values) != len(result.times) or any(
                not math.isfinite(value) for value in values
            ):
                raise ValueError(f"{variable}: 序列缺失、长度不匹配或含非有限数值")
    for variable in variables:
        units = {
            getattr(result, "metadata", {}).get("variable_units", {}).get(variable)
            for _, result in results
        }
        if len(units - {None, ""}) > 1:
            raise ValueError(f"{variable}: 情景的变量单位不同，不能直接比较")


def font_settings(text, config=None):
    """选择西文 + 中文字体组合，并逐字检查实际字形，缺字时报错。"""
    from matplotlib import font_manager
    from matplotlib.ft2font import FT2Font

    available = {font.name for font in font_manager.fontManager.ttflist}

    def regular(font):
        weight = font.weight if isinstance(font.weight, int) else 400
        return weight <= 500 and font.style == "normal"

    # TTC 字体只登记第一个字面，macOS 的 Songti.ttc 首个字面是粗体，跳过粗体字面
    upright = {font.name for font in font_manager.fontManager.ttflist if regular(font)}
    cjk = next(
        (
            name
            for name in (
                "SimSun",
                "SimSong",
                "Songti SC",
                "Noto Serif CJK SC",
                "Source Han Serif SC",
                "Microsoft YaHei",
                "PingFang SC",
                "STHeiti",
                "Noto Sans CJK SC",
                "Arial Unicode MS",
            )
            if name in upright
        ),
        None,
    )
    latin = "Times New Roman" if "Times New Roman" in available else "DejaVu Serif"
    custom = (config or {}).get("font_family", [])
    missing_fonts = [name for name in custom if name not in available]
    if missing_fonts:
        raise ValueError("指定字体未安装: " + ", ".join(missing_fonts))
    families = list(dict.fromkeys([*(custom or [latin]), *([cjk] if cjk else [])]))
    coverage = set()
    for family in families:
        path = font_manager.findfont(
            font_manager.FontProperties(family=[family]), fallback_to_default=False
        )
        coverage.update(FT2Font(path).get_charmap())
    missing = sorted({char for char in text if not char.isspace() and ord(char) not in coverage})
    if missing:
        raise RuntimeError(
            "字体缺少以下字形: " + "".join(missing[:30]) + "，请安装字体并在 font_family 中指定"
        )
    return {
        "font.family": families,
        "font.size": (config or {}).get("font_size", 10.5),
        "axes.unicode_minus": False,
        "pdf.fonttype": 42,
        "svg.fonttype": "path",
        "mathtext.fontset": "stix",
        "savefig.facecolor": "white",
    }


# ---------------------------------------------------------------------------
# 图形
# ---------------------------------------------------------------------------


def _ylabel(results, variable):
    units = {
        getattr(result, "metadata", {}).get("variable_units", {}).get(variable)
        for _, result in results
    } - {None, ""}
    return f"{variable} ({units.pop()})" if len(units) == 1 else variable


def style_axis(axis, xlabel, ylabel="", title="", config=None):
    """四边细框、刻度朝内、默认无网格，接近常见期刊插图。"""
    config = config or load_plot_config()
    axis.set_xlabel(xlabel)
    if ylabel:
        axis.set_ylabel(ylabel)
    if config["show_grid"]:
        axis.grid(True, color=config["grid_color"], linewidth=0.5)
    else:
        axis.grid(False)
    axis.set_axisbelow(True)
    for spine in axis.spines.values():
        spine.set_color(config["border_color"])
        spine.set_linewidth(0.8)
    axis.tick_params(direction="in", length=3.5, width=0.8, top=True, right=True, pad=4)
    axis.margins(x=0, y=0.06)
    if title:
        axis.set_title(title, fontsize=config["font_size"] + 1)


def _legend(axis, count, config):
    if count < 2:
        return
    options = {"frameon": False, "fontsize": config["legend_font_size"], "handlelength": 2.8}
    if count <= 6:
        axis.legend(loc="best", **options)
    else:
        # 曲线较多时图例放到坐标区下方，避免压住曲线
        axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2, **options)


def lines_figure(results, variable, xlabel, title="", config=None):
    """每个情景一条曲线；颜色与线型同时区分，黑白打印也能分辨。"""
    import matplotlib.pyplot as plt
    import numpy as np

    config = config or load_plot_config()
    count = len(results)
    if count > config["max_lines"]:
        raise ValueError("曲线数超过 max_lines，请分组出图或改用 --plot-style band")
    colors, styles = config["colors"], config["line_styles"]
    extra = 0.25 * math.ceil(count / 2) if count > 6 else 0.0
    figure, axis = plt.subplots(figsize=(config["width_inches"], config["height_inches"] + extra))
    for i, (label, result) in enumerate(results):
        values = result.series[variable]
        (line,) = axis.plot(
            result.times,
            values,
            color=colors[i % len(colors)],
            linestyle=styles[i % len(styles)],
            linewidth=config["line_width"],
            label=label,
        )
        if config["number_lines"] and len(values) > 1:
            # Vensim 式编号：只标在真实采样点上，各曲线按间隔的 i/count 错开
            last = len(values) - 1
            gap = 0.92 * last / config["marker_repeats"]
            spots = 0.03 * last + gap * (np.arange(config["marker_repeats"]) + i / count)
            line.set_marker(f"${i + 1}$")
            line.set_markersize(config["marker_size"])
            line.set_markeredgewidth(0.3)
            line.set_markevery(sorted({int(round(s)) for s in spots}))
    style_axis(axis, xlabel, _ylabel(results, variable), title, config)
    _legend(axis, count, config)
    figure.tight_layout()
    return figure


def band_figure(results, variable, xlabel, title="", config=None):
    """蒙特卡洛样本的 5–95% 与 25–75% 分位带加中位数线。"""
    import matplotlib.pyplot as plt
    import numpy as np

    config = config or load_plot_config()
    times = results[0][1].times
    if any(result.times != times for _, result in results):
        raise ValueError("分位带要求所有样本使用相同的时间网格")
    data = np.asarray([result.series[variable] for _, result in results])
    q05, q25, q50, q75, q95 = np.quantile(data, [0.05, 0.25, 0.5, 0.75, 0.95], axis=0)
    figure, axis = plt.subplots(figsize=(config["width_inches"], config["height_inches"]))
    color = config["colors"][0]
    axis.fill_between(
        times, q05, q95, color=color, alpha=config["band_outer_alpha"], lw=0, label="5–95%"
    )
    axis.fill_between(
        times, q25, q75, color=color, alpha=config["band_inner_alpha"], lw=0, label="25–75%"
    )
    axis.plot(times, q50, color=color, linewidth=config["line_width"], label="Median")
    style_axis(axis, xlabel, _ylabel(results, variable), title, config)
    axis.legend(loc="best", frameon=False, fontsize=config["legend_font_size"])
    figure.tight_layout()
    return figure


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def export_figures(
    results,
    variables,
    output,
    dpi=600,
    formats=None,
    style="auto",
    title="",
    inputs=(),
    overwrite=False,
    plot_config=None,
):
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 72 <= dpi <= 1200:
        raise ValueError("DPI 必须为 72 到 1200 的整数")
    if style not in {"auto", "lines", "band"}:
        raise ValueError("plot-style 只能是 auto、lines 或 band")
    validate_results(results, variables)
    config = load_plot_config(plot_config)
    paths, manifest_path = output_paths(output, variables, formats)
    preflight_outputs(
        [path for _, path in paths] + [manifest_path],
        [*inputs, *([plot_config] if plot_config else [])],
        overwrite=overwrite,
    )
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if style == "auto":
        style = "lines"
        if len(results) > config["max_lines"]:
            # 只有明确标记为蒙特卡洛抽样的结果才自动合并为分位带
            if not all(
                getattr(result, "metadata", {}).get("experiment_mode") == "monte-carlo"
                for _, result in results
            ):
                raise ValueError("命名情景过多，请分组出图；分位带只用于蒙特卡洛抽样结果")
            style = "band"
    if config["width_inches"] * (config["height_inches"] + 0.5) * dpi**2 > 100_000_000:
        raise ValueError("图片像素过大，请减小尺寸或 DPI")
    units = {getattr(result, "metadata", {}).get("time_unit", "") for _, result in results}
    if len(units) != 1:
        raise ValueError("各情景的时间单位不同，不能画在同一张图上")
    unit = next(iter(units))
    xlabel = f"Time ({unit})" if unit else "Time"
    report = {
        "style": style,
        "dpi": dpi,
        "title": title,
        "time_unit": unit,
        "figures": [],
        "plot_config": config,
        "runs": [{"name": label, **getattr(result, "metadata", {})} for label, result in results],
    }
    fonts = font_settings(
        "".join(variables)
        + "".join(label for label, _ in results)
        + title
        + xlabel
        + "0123456789−–%",
        config,
    )
    report["resolved_font_families"] = fonts["font.family"]
    # 全部渲染成功后再写文件，中途出错不会留下半套图片
    rendered = []
    draw = lines_figure if style == "lines" else band_figure
    with plt.rc_context(fonts):
        for variable in variables:
            figure = draw(results, variable, xlabel, title, config)
            try:
                for _, path in (item for item in paths if item[0] == variable):
                    stream = io.BytesIO()
                    extension = path.suffix[1:].lower()
                    # 去掉 PDF/SVG 内的时间戳，同一数据重复导出得到相同文件
                    metadata = {
                        "pdf": {"CreationDate": None, "ModDate": None},
                        "svg": {"Date": None},
                    }
                    figure.savefig(
                        stream, format=extension, dpi=dpi, metadata=metadata.get(extension)
                    )
                    content = stream.getvalue()
                    rendered.append((path, content))
                    report["figures"].append(
                        {
                            "variable": variable,
                            "file": path.name,
                            "sha256": hashlib.sha256(content).hexdigest(),
                        }
                    )
            finally:
                plt.close(figure)
    for path, content in rendered:
        atomic_write(path, content, overwrite=overwrite)
    atomic_write(
        manifest_path,
        json.dumps(report, ensure_ascii=False, indent=2).encode(),
        overwrite=overwrite,
    )
    return {"figures": [str(path) for path, _ in rendered], "manifest": str(manifest_path)}


def csv_provenance(path, fingerprint):
    """读取 CSV 旁边的运行清单，带回模型哈希、时间单位与诊断状态。"""
    candidates = [(path.with_suffix(path.suffix + ".run.json"), "run")]
    if path.name == "series.csv":
        candidates.append((path.parent / "experiment.json", "experiment"))
    if path.name in {"baseline.csv", "best.csv", "comparison.csv"}:
        candidates.append((path.parent / "optimization.json", "optimization"))
    for sidecar, kind in candidates:
        if not sidecar.exists() and not sidecar.is_symlink():
            continue
        if not sidecar.resolve().is_relative_to(path.parent.resolve()):
            raise ValueError("相邻运行清单指向结果目录之外，请将真实清单放在结果目录内")
        report = json.loads(sidecar.read_text(encoding="utf-8-sig"))
        if not isinstance(report, dict) or not isinstance(report.get("result_files", {}), dict):
            raise ValueError("运行清单及 result_files 必须是 JSON 对象")
        recorded = (
            report.get("source_csv_sha256")
            if kind == "run"
            else report.get("result_files", {}).get(path.name)
        )
        if recorded is not None and recorded != fingerprint:
            raise ValueError("CSV 内容与运行清单记录的哈希不一致，文件可能被改动过")
        if kind == "run":
            rows = [("current", report)]
        elif kind == "experiment":
            runs = report.get("runs", [])
            if not isinstance(runs, list) or any(not isinstance(row, dict) for row in runs):
                raise ValueError("实验清单 runs 必须是对象列表")
            rows = [(row.get("name"), row) for row in runs]
        else:
            rows = [("baseline", report.get("run", {}))]
            best = report.get("best")
            if best is not None and not isinstance(best, dict):
                raise ValueError("搜索清单 best 必须是对象或 null")
            if best:
                rows.append(("best", best.get("run", {})))
            if report.get("observations"):
                rows.append(("observed", report["observations"]))
            if path.name != "comparison.csv":
                selected = "baseline" if path.name == "baseline.csv" else "best"
                rows = [("current", row) for label, row in rows if label == selected]
        if not rows or any(
            not isinstance(label, str) or not isinstance(row, dict) for label, row in rows
        ):
            raise ValueError("运行清单缺少有效的情景名称或运行对象")
        if any(row.get("status") == "diagnostic_only" or row.get("warnings") for _, row in rows):
            raise ValueError("运行清单标记为诊断结果，不能出图")
        return {
            label: {
                **row,
                "provenance_manifest": sidecar.name,
                "provenance_verified": recorded is not None,
            }
            for label, row in rows
        }
    return {}


def read_result_csv(path, variables=None, time_unit="", encoding="utf-8-sig"):
    path = Path(path)
    raw = path.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    provenance = csv_provenance(path, fingerprint)
    reader = csv.DictReader(io.StringIO(raw.decode(encoding), newline=""))
    columns = reader.fieldnames or []
    if len(columns) != len(set(columns)) or "Time" not in columns:
        raise ValueError("CSV 需要唯一列名和 Time 列；支持宽表或 Scenario,Time,Variable,Value 长表")
    long_form = set(columns) == {"Scenario", "Time", "Variable", "Value"}
    rows = list(reader)
    if (
        not rows
        or len(rows) > 2_000_000
        or any(None in row or any(value is None for value in row.values()) for row in rows)
    ):
        raise ValueError("CSV 为空、行数超限或字段数量不一致")
    available = (
        list(dict.fromkeys(row["Variable"] for row in rows))
        if long_form
        else [name for name in columns if name != "Time"]
    )
    variables = variables or available
    if any(name not in available for name in variables):
        raise ValueError("请求的变量不在 CSV 中")
    groups = {}
    if long_form:
        for row in rows:
            if row["Variable"] in variables:
                key = (row["Scenario"], row["Variable"])
                groups.setdefault(key, []).append((float(row["Time"]), float(row["Value"])))
    else:
        for variable in variables:
            groups[("current", variable)] = [
                (float(row["Time"]), float(row[variable])) for row in rows
            ]
    labels = list(dict.fromkeys(label for label, _ in groups))
    results = []
    for label in labels:
        series, times = {}, None
        for variable in variables:
            pairs = groups.get((label, variable))
            if not pairs:
                raise ValueError(f"{label}: 缺少变量 {variable}")
            current_times, values = map(list, zip(*pairs, strict=False))
            if times is not None and current_times != times:
                raise ValueError("同一情景的变量时间网格不一致")
            times, series[variable] = current_times, values
        metadata = provenance.get(label, {})
        recorded_unit = metadata.get("time_unit", "")
        if time_unit and recorded_unit and time_unit != recorded_unit:
            raise ValueError("--time-unit 与运行清单记录的时间单位不一致")
        results.append(
            (
                label,
                SimpleNamespace(
                    times=times,
                    series=series,
                    metadata={
                        **metadata,
                        "source_csv_sha256": fingerprint,
                        "time_unit": time_unit or recorded_unit,
                        "status": metadata.get("status", "imported"),
                        "provenance_verified": metadata.get("provenance_verified", False),
                    },
                ),
            )
        )
    validate_results(results, variables)
    return results, variables


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--var", action="append")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--time-unit", default="", help="时间单位，如 Year、Month")
    parser.add_argument(
        "--encoding", default="utf-8-sig", help="默认 UTF-8/BOM；旧中文 CSV 可用 gb18030"
    )
    plot_options(parser, style=True)
    args = parser.parse_args(argv)
    results, variables = read_result_csv(args.csv, args.var, args.time_unit, args.encoding)
    report = export_figures(
        results,
        variables,
        args.output,
        args.dpi,
        args.formats,
        args.plot_style,
        inputs=[args.csv],
        plot_config=args.plot_config,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
