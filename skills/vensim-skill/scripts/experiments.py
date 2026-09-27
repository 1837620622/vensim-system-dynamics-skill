"""批量情景、参数敏感性和时间步长检查，保留每次运行的参数与完整序列。"""
from __future__ import annotations

import argparse
import csv
import io
import itertools
import json
import math
from pathlib import Path
import random

from mdl_document import atomic_write, preflight_outputs, separate_output
from simulation_runner import run_model
from vensim_engine import get_time_bounds, load_mdl_text, parse_equations

MAX_RUNS = 200
MAX_EXPERIMENT_VALUES = 2_000_000


def csv_bytes(header, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(header)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def experiment_runs(spec, baseline=None):
    if not isinstance(spec, dict):
        raise ValueError("实验规范必须为对象")
    mode = spec.get("mode", "scenarios")
    if mode == "scenarios":
        runs = spec.get("scenarios", [])
        if not isinstance(runs, list) or not runs or any(not isinstance(item, dict) or "name" not in item for item in runs):
            raise ValueError("scenarios 不能为空")
        runs = [{"name": item["name"], "params": item.get("params", {})} for item in runs]
    elif mode == "perturb":
        names = spec.get("parameters", [])
        changes = spec.get("changes")
        if not isinstance(names, list) or not names or any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
            raise ValueError("perturb 的 parameters 必须为不重复的参数名列表")
        if not isinstance(changes, list) or not changes or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v == 0 for v in changes) or len(set(changes)) != len(changes):
            raise ValueError("changes 必须为非零、不重复的有限相对变化列表，例如 [-0.2, -0.1, 0.2, 0.1]")
        if baseline is None or any(name not in baseline for name in names):
            raise ValueError("扰动参数必须是模型中的数值常量，不能覆盖反馈方程")
        if any(baseline[name] == 0 for name in names):
            raise ValueError("零基准值不能做百分比扰动，请改用 scenarios 指定绝对参数值")
        runs = [{"name": f"{name} {change:+.8g}%", "params": {name: baseline[name] * (1 + change / 100)}}
                for name in names for change in (fraction * 100 for fraction in changes)]
        runs.append({"name": "current", "params": {}})
    elif mode in {"grid", "monte-carlo"}:
        parameters = spec.get("parameters", {})
        if not isinstance(parameters, dict) or not parameters:
            raise ValueError("parameters 不能为空")
        names = list(parameters)
        if mode == "grid":
            if any(not isinstance(values, list) or not values for values in parameters.values()):
                raise ValueError("grid 的每个参数需要非空数值列表")
            if math.prod(len(values) for values in parameters.values()) > MAX_RUNS:
                raise ValueError(f"参数组合超过 {MAX_RUNS} 次")
            rows = itertools.product(*parameters.values())
        else:
            count = spec.get("samples")
            if type(count) is not int or not 1 <= count <= MAX_RUNS:
                raise ValueError(f"必须显式提供 samples，取 1 到 {MAX_RUNS} 的整数")
            seed = spec.get("seed")
            if type(seed) is not int:
                raise ValueError("必须显式提供整数 seed，不能沿用其他实验的随机种子")
            # 固定种子的统计抽样，不用于口令或任何密码学用途。
            rng = random.Random(seed)  # nosec B311
            for limits in parameters.values():
                if not isinstance(limits, list) or len(limits) != 2 or not all(not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) for v in limits) or limits[0] > limits[1]:
                    raise ValueError("Monte Carlo 参数必须是 [下限, 上限]，使用独立均匀分布")
            rows = [[rng.uniform(*parameters[name]) for name in names] for _ in range(count)]
        runs = [{"name": f"run_{index + 1:03d}", "params": dict(zip(names, row))} for index, row in enumerate(rows)]
    else:
        raise ValueError("mode 必须是 scenarios、perturb、grid 或 monte-carlo")
    if not 1 <= len(runs) <= MAX_RUNS:
        raise ValueError(f"运行次数必须为 1 到 {MAX_RUNS}")
    labels = [run["name"] for run in runs]
    if any(not isinstance(label, str) or not label.strip() for label in labels) or len(set(labels)) != len(labels):
        raise ValueError("情景名必须非空且不重复")
    for run in runs:
        if not isinstance(run["params"], dict) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in run["params"].values()):
            raise ValueError("参数必须为变量名到有限数值的映射")
    return runs


def execute_experiment(model, spec, output, backend="builtin", plot=None, dpi=600, formats=None, plot_style="auto", protected=(), plot_config=None):
    from result_plotting import output_paths
    protected = [*protected, *([plot_config] if plot_config else [])]
    equations = parse_equations(load_mdl_text(model), expand=False)
    baseline = {}
    for name, equation in equations.items():
        try:
            baseline[name] = float(equation.rhs)
        except ValueError:
            continue
    runs = experiment_runs(spec, baseline)
    variables = spec.get("variables", [])
    if not isinstance(variables, list) or not variables or any(not isinstance(v, str) for v in variables) or len(set(variables)) != len(variables):
        raise ValueError("必须指定 variables，避免把内部参数当作结果")
    outputs = [output / "series.csv", output / "summary.csv", output / "experiment.json"]
    plot_paths, plot_manifest = output_paths(plot, variables, formats) if plot else ([], None)
    plots = [path for _, path in plot_paths] + ([plot_manifest] if plot_manifest else [])
    preflight_outputs(outputs + plots, [model, *protected])
    time = spec.get("time", {})
    if not isinstance(time, dict) or set(time) - {"time_step", "final_time", "saveper"}:
        raise ValueError("time 只接受 time_step、final_time、saveper")
    for name, value in time.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (name != "final_time" and value <= 0):
            raise ValueError(f"无效的时间设置: {name}")
    settings = get_time_bounds(parse_equations(load_mdl_text(model)))
    points = (time.get("final_time", settings[1]) - settings[0]) / time.get("saveper", settings[3]) + 1
    if points * len(runs) * len(variables) > MAX_EXPERIMENT_VALUES:
        raise ValueError("实验输出过大，请缩短仿真区间、增大保存间隔或减少参数组合")
    series, summary, manifest, results = [], [], [], []
    for run in runs:
        result = run_model(model, variables, backend=backend, params=run["params"], **time)
        manifest.append({"name": run["name"], **result.metadata})
        results.append((run["name"], result))
        for name in variables:
            values = result.series[name]
            peak = max(range(len(values)), key=lambda index: values[index])
            summary.append([run["name"], name, values[0], values[-1], min(values), max(values), result.times[peak]])
            series.extend([run["name"], timestamp, name, value] for timestamp, value in zip(result.times, values))
    report = {"spec": spec, "runs": manifest, "run_count": len(runs), "native_verified": False}
    # 所有求解及图形渲染成功后才保存实验清单，不伪报完整实验。
    figure_report = plot_experiment(results, variables, plot, dpi=dpi, formats=formats, style=plot_style,
                                    inputs=[model, *protected, *outputs], overwrite=False, plot_config=plot_config) if plot else None
    atomic_write(outputs[0], csv_bytes(["Scenario", "Time", "Variable", "Value"], series))
    atomic_write(outputs[1], csv_bytes(["Scenario", "Variable", "Initial", "Final", "Min", "Max", "Peak time"], summary))
    atomic_write(outputs[2], json.dumps(report, ensure_ascii=False, indent=2).encode())
    return {"run_count": len(runs), "output": str(output), "plot": figure_report}


def plot_experiment(results, variables, output, title=None, **options):
    from result_plotting import export_figures
    return export_figures(results, variables, output, title=title or "", **options)


def convergence(model, variables, output, tolerance=0.01):
    preflight_outputs([output], [model])
    if isinstance(tolerance, bool) or not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("tolerance 必须为有限正数")
    _, _, dt, sp = get_time_bounds(parse_equations(load_mdl_text(model)))
    runs = [run_model(model, variables, time_step=dt / factor, saveper=sp) for factor in (1, 2, 4)]
    records = []
    for variable in variables:
        refined = runs[-1].series[variable]
        scale = max(max(abs(value) for value in refined), 1e-12)
        coarse_error = max(abs(a - b) for a, b in zip(runs[0].series[variable], refined))
        fine_error = max(abs(a - b) for a, b in zip(runs[1].series[variable], refined))
        records.append({"variable": variable, "max_absolute_error": coarse_error,
                        "normalized_error": coarse_error / scale,
                        "half_step_error": fine_error / scale,
                        "pass": coarse_error / scale <= tolerance})
    report = {"time_steps": [dt, dt / 2, dt / 4], "saveper": sp, "tolerance": tolerance,
              "normalization": "maximum absolute value of the finest trajectory",
              "variables": records, "pass": all(item["pass"] for item in records),
              "model_sha256": runs[0].metadata["model_sha256"], "native_verified": False}
    atomic_write(output, json.dumps(report, ensure_ascii=False, indent=2).encode())
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    experiment = sub.add_parser("experiment")
    experiment.add_argument("model", type=Path)
    experiment.add_argument("--spec", type=Path, required=True)
    experiment.add_argument("--output-dir", type=Path, required=True)
    experiment.add_argument("--backend", choices=["builtin", "pysd"], default="builtin")
    experiment.add_argument("--plot", type=Path)
    from result_plotting import plot_options
    plot_options(experiment, style=True)
    check = sub.add_parser("convergence")
    check.add_argument("model", type=Path)
    check.add_argument("--var", action="append", required=True)
    check.add_argument("--output", type=Path, required=True)
    check.add_argument("--tolerance", type=float, default=0.01)
    args = parser.parse_args(argv)
    if args.command == "experiment":
        spec = json.loads(args.spec.read_text(encoding="utf-8-sig"))
        for target in [args.output_dir / name for name in ("series.csv", "summary.csv", "experiment.json")]:
            separate_output(target, [args.model, args.spec])
        if args.plot:
            separate_output(args.plot, [args.spec, *(args.output_dir / name for name in ("series.csv", "summary.csv", "experiment.json"))])
        report = execute_experiment(args.model, spec, args.output_dir, args.backend, args.plot,
                                    args.dpi, args.formats, args.plot_style, protected=[args.spec], plot_config=args.plot_config)
    else:
        report = convergence(args.model, args.var, args.output, args.tolerance)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report.get("pass") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
