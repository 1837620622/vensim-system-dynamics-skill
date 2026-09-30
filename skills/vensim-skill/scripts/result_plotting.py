"""从实际仿真序列生成独立的 Vensim 经典结果图，保留曲线与数据的对应关系。"""

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


def plot_options(parser, style=False):
    parser.add_argument(
        "--dpi", type=int, default=600, help="PNG 分辨率，默认 600，最高 1200；矢量图不受 DPI 限制"
    )
    parser.add_argument("--formats", help="额外导出格式，以逗号分隔，例如 png,pdf,svg")
    parser.add_argument(
        "--plot-config", type=Path, help="绘图样式 JSON；尺寸、字体、颜色、线宽与编号密度"
    )
    if style:
        parser.add_argument("--plot-style", choices=["auto", "classic", "band"], default="auto")


def load_plot_config(path=None):
    default = Path(__file__).resolve().parents[1] / "assets/templates/plot_config_classic.json"
    config = json.loads(default.read_text(encoding="utf-8"))
    if path:
        overrides = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(overrides, dict) or set(overrides) - set(config):
            raise ValueError("plot-config 含未知字段；请参考 plot_config_classic.json")
        config.update(overrides)
    limits = {
        "width_inches": (4, 14),
        "plot_height_inches": (2, 8),
        "legend_row_inches": (0.18, 0.6),
        "font_size": (6, 24),
        "legend_font_size": (6, 20),
        "line_width": (0.2, 3),
        "marker_size": (3, 15),
    }
    for key, (low, high) in limits.items():
        value = config[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not low <= value <= high
        ):
            raise ValueError(f"{key} 必须在 {low} 到 {high} 之间")
    for key, low, high in (
        ("marker_repeats", 1, 20),
        ("legend_repeats", 1, 12),
        ("max_classic_curves", 1, 30),
    ):
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f"{key} 必须为 {low} 到 {high} 的整数")
    if not isinstance(config["font_family"], list) or any(
        not isinstance(name, str) or not name for name in config["font_family"]
    ):
        raise ValueError("font_family 必须为字体名列表；空列表表示按系统选择")
    if not isinstance(config["number_lines"], bool):
        raise ValueError("number_lines 必须为布尔值")
    if not isinstance(config["show_grid"], bool):
        raise ValueError("show_grid 必须为布尔值")
    if any(
        isinstance(config[key], bool)
        or not isinstance(config[key], (int, float))
        or not 0 < config[key] <= 1
        for key in ("band_outer_alpha", "band_inner_alpha")
    ):
        raise ValueError("分位带透明度必须大于 0 且不超过 1")
    if (
        not isinstance(config["line_styles"], list)
        or not config["line_styles"]
        or any(style not in ("-", "--", "-.", ":") for style in config["line_styles"])
    ):
        raise ValueError("line_styles 必须是非空线型列表，仅支持 -、--、-.、:")
    colors = config["colors"]
    if (
        not isinstance(colors, list)
        or not colors
        or any(
            not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color)
            for color in [*colors, config["grid_color"], config["border_color"]]
        )
    ):
        raise ValueError("颜色必须为 #RRGGBB，colors 至少包含一种颜色")
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
            raise ValueError("诊断结果包含求值失败，不能导出正式结果图")
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
    from matplotlib import font_manager
    from matplotlib.ft2font import FT2Font

    available = {font.name for font in font_manager.fontManager.ttflist}
    cjk = next(
        (
            name
            for name in (
                "SimSun",
                "Songti SC",
                "Noto Serif CJK SC",
                "Source Han Serif SC",
                "Microsoft YaHei",
                "PingFang SC",
                "STHeiti",
                "Noto Sans CJK SC",
                "Arial Unicode MS",
            )
            if name in available
        ),
        None,
    )
    latin = "Times New Roman" if "Times New Roman" in available else "DejaVu Serif"
    custom = (config or {}).get("font_family", [])
    if any(name not in available for name in custom):
        raise ValueError(
            "指定字体未安装: " + ", ".join(name for name in custom if name not in available)
        )
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
            "字体缺少实际图件字形: "
            + "".join(missing[:30])
            + "；请安装合适字体并设置 font_family，不能交付缺字图片"
        )
    return {
        "font.family": families,
        "font.size": (config or {}).get("font_size", 11),
        "axes.unicode_minus": False,
        "pdf.fonttype": 42,
        "svg.fonttype": "path",
        "mathtext.fontset": "stix",
        "savefig.facecolor": "white",
    }


def separate_number_markers(axis):
    """只调整实际采样点上的编号位置；密集处宁可少标，不移动或插值曲线。"""
    from matplotlib.markers import MarkerStyle
    from matplotlib.transforms import Bbox

    occupied = []
    for line in axis.lines:
        if not line.get_marker():
            continue
        marker = MarkerStyle(line.get_marker())
        glyph = marker.get_path().transformed(marker.get_transform()).get_extents()
        scale = line.get_markersize() * axis.figure.dpi / 72
        pixels = axis.transData.transform(
            list(zip(line.get_xdata(), line.get_ydata(), strict=False))
        )
        chosen = []
        targets = line.get_markevery()
        # 每个重复位置只在附近寻找，保护长序列的排版时间，不能为了编号改采样网格。
        radius = min(64, max(1, len(pixels) // max(1, 2 * len(targets))))
        for target in targets:
            candidates = sorted(
                range(max(0, target - radius), min(len(pixels), target + radius + 1)),
                key=lambda index: (abs(index - target), index),
            )
            for index in candidates:
                x, y = pixels[index]
                rect = Bbox.from_extents(
                    x + glyph.x0 * scale,
                    y + glyph.y0 * scale,
                    x + glyph.x1 * scale,
                    y + glyph.y1 * scale,
                ).expanded(1.35, 1.35)
                if (
                    index not in chosen
                    and axis.bbox.contains(rect.x0, rect.y0)
                    and axis.bbox.contains(rect.x1, rect.y1)
                    and not any(rect.overlaps(other) for other in occupied)
                ):
                    chosen.append(index)
                    occupied.append(rect)
                    break
        line.set_markevery(sorted(chosen))


def classic_figure(results, variable, xlabel, title="", config=None):
    import matplotlib.pyplot as plt
    import numpy as np

    config = config or load_plot_config()
    colors = config["colors"]
    count = len(results)
    if count > config["max_classic_curves"]:
        raise ValueError("曲线数超过 max_classic_curves；请分组、调整配置或使用 --plot-style band")
    legend_height = config["legend_row_inches"] * count
    height = config["plot_height_inches"] + 0.95 + legend_height
    figure = plt.figure(figsize=(config["width_inches"], height))
    axis = figure.add_axes(
        [0.11, (legend_height + 0.78) / height, 0.87, config["plot_height_inches"] / height]
    )
    legend = figure.add_axes([0.015, 0.10 / height, 0.965, legend_height / height])
    legend.set(xlim=(0, 1), ylim=(0, count))
    legend.set_axis_off()
    texts = []
    for i, (label, result) in enumerate(results):
        color = colors[i % len(colors)]
        values = result.series[variable]
        line_style = config["line_styles"][i % len(config["line_styles"])]
        (line,) = axis.plot(
            result.times,
            values,
            color=color,
            linewidth=config["line_width"],
            linestyle=line_style,
            label=label,
        )
        # 在实际采样点上交错编号；不平滑、不改变数据，也不人为错开曲线。
        if len(values) > 1:
            fractions = np.linspace(0.035, 0.88, config["marker_repeats"]) + 0.09 * i / max(
                1, count
            )
            indices = sorted(set(int(round(f * (len(values) - 1))) for f in fractions))
        else:
            indices = [0]
        line.set_marker(f"${i + 1}$" if config["number_lines"] else "")
        line.set_markevery(indices)
        line.set_markersize(config["marker_size"])
        line.set_markeredgewidth(0.35)
        texts.append(
            legend.text(
                0,
                count - i - 0.5,
                f"{variable} : {label}",
                va="center",
                fontsize=config["legend_font_size"],
            )
        )
    style_axis(axis, xlabel, title, config)
    figure.canvas.draw()
    if config["number_lines"]:
        separate_number_markers(axis)
    renderer = figure.canvas.get_renderer()
    text_width = max(text.get_window_extent(renderer).width for text in texts)
    legend_width = legend.get_window_extent(renderer).width
    start = text_width / legend_width + 0.025
    if start > 0.74:
        plt.close(figure)
        raise ValueError("图例文字过长，请缩短情景名称，使文字与线段各有足够空间")
    for i in range(count):
        y = count - i - 0.5
        color = colors[i % len(colors)]
        legend.plot(
            [start, 0.995],
            [y, y],
            color=color,
            linewidth=config["line_width"],
            linestyle=config["line_styles"][i % len(config["line_styles"])],
        )
        if config["number_lines"]:
            for x in np.linspace(start + 0.018, 0.98, config["legend_repeats"]):
                legend.text(
                    x,
                    y,
                    str(i + 1),
                    color=color,
                    ha="center",
                    va="center",
                    fontsize=config["legend_font_size"],
                )
    return figure


def style_axis(axis, xlabel, title="", config=None):
    config = config or load_plot_config()
    axis.set_xlabel(xlabel, labelpad=5)
    axis.set_axisbelow(True)
    if config["show_grid"]:
        axis.grid(True, color=config["grid_color"], linewidth=0.5, linestyle="-")
    else:
        axis.grid(False)
    axis.tick_params(direction="out", length=0, pad=6)
    for spine in axis.spines.values():
        spine.set_color(config["border_color"])
        spine.set_linewidth(0.65)
    axis.margins(x=0, y=0.06)
    if title:
        axis.set_title(title, fontsize=12, pad=10)


def band_figure(results, variable, xlabel, title="", config=None):
    import matplotlib.pyplot as plt
    import numpy as np

    config = config or load_plot_config()
    times = results[0][1].times
    if any(result.times != times for _, result in results):
        raise ValueError("分位带要求全部情景使用相同时间网格；不能静默插值")
    data = np.asarray([result.series[variable] for _, result in results])
    q05, q25, q50, q75, q95 = np.quantile(data, [0.05, 0.25, 0.5, 0.75, 0.95], axis=0)
    figure, axis = plt.subplots(
        figsize=(config["width_inches"], config["plot_height_inches"] + 1.4)
    )
    color = config["colors"][0]
    axis.fill_between(
        times, q05, q95, color=color, alpha=config["band_outer_alpha"], label="5–95% sample range"
    )
    axis.fill_between(
        times, q25, q75, color=color, alpha=config["band_inner_alpha"], label="25–75% sample range"
    )
    axis.plot(times, q50, color=color, linewidth=config["line_width"], label=f"{variable} : median")
    style_axis(axis, xlabel, title, config)
    axis.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.20),
        frameon=False,
        fontsize=config["legend_font_size"],
        ncol=1,
    )
    figure.subplots_adjust(left=0.11, right=0.98, top=0.96, bottom=0.32)
    return figure


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
    if style not in {"auto", "classic", "band"}:
        raise ValueError("未知结果图样式")
    validate_results(results, variables)
    config = load_plot_config(plot_config)
    paths, manifest_path = output_paths(output, variables, formats)
    all_paths = [path for _, path in paths] + [manifest_path]
    preflight_outputs(
        all_paths, [*inputs, *([plot_config] if plot_config else [])], overwrite=overwrite
    )
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if style == "auto":
        style = "classic"
        if len(results) > config["max_classic_curves"]:
            if not all(
                getattr(result, "metadata", {}).get("experiment_mode") == "monte-carlo"
                for _, result in results
            ):
                raise ValueError(
                    "情景曲线过多，请分组或明确选择 band；不能把命名政策情景自动合并成抽样分位带"
                )
            style = "band"
    height = (
        config["plot_height_inches"]
        + 0.95
        + config["legend_row_inches"] * (len(results) if style == "classic" else 3)
    )
    if config["width_inches"] * height * dpi**2 > 100_000_000:
        raise ValueError("图件像素规模过大，请减小尺寸/DPI，或分组出图")
    units = {getattr(result, "metadata", {}).get("time_unit", "") for _, result in results}
    if len(units) != 1:
        raise ValueError("情景的时间单位不同，不能直接比较")
    unit = next(iter(units))
    xlabel = f"Time ({unit})" if unit else "Time"
    report = {
        "artifact_kind": "simulation_result",
        "style": style,
        "dpi": dpi,
        "title": title,
        "time_unit": unit,
        "native_verified": False,
        "sample_bands_are_confidence_intervals": False,
        "figures": [],
        "plot_config": config,
        "runs": [{"name": label, **getattr(result, "metadata", {})} for label, result in results],
    }
    # 先完成所有渲染，再写文件；绘图失败时不留下半套图件。
    rendered = []
    fonts = font_settings(
        "".join(variables)
        + "".join(label for label, _ in results)
        + title
        + xlabel
        + "0123456789−–%",
        config,
    )
    report["resolved_font_families"] = fonts["font.family"]
    with plt.rc_context(fonts):
        for variable in variables:
            figure = (classic_figure if style == "classic" else band_figure)(
                results, variable, xlabel, title, config
            )
            try:
                for _, path in (item for item in paths if item[0] == variable):
                    stream = io.BytesIO()
                    extension = path.suffix[1:].lower()
                    metadata = (
                        {"CreationDate": None, "ModDate": None}
                        if extension == "pdf"
                        else {"Date": None}
                        if extension == "svg"
                        else None
                    )
                    figure.savefig(stream, format=extension, dpi=dpi, metadata=metadata)
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
    """恢复相邻运行清单；CSV 导入不能丢掉诊断状态或沿用不匹配的仿真身份。"""
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
            raise ValueError("CSV 与运行清单哈希不一致；请核对数据来源，不能沿用原仿真身份")
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
            raise ValueError("运行清单标记为诊断结果，不能导出正式结果图")
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
            raise ValueError("指定时间单位与运行清单不一致；请先明确换算，不能只改标签")
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
    parser.add_argument("--time-unit", default="", help="填写真实的时间单位，不猜测为 Year")
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
