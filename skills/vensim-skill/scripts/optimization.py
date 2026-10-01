"""有界参数校准与政策搜索，使用真实仿真和明确的目标，不改写 MDL。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from bisect import bisect_left
from pathlib import Path

from experiments import csv_bytes
from mdl_document import atomic_write, preflight_outputs
from result_plotting import read_result_csv
from simulation_runner import CONTROL_NAMES, run_model
from vensim_engine import extract_deps, get_time_bounds, load_mdl_text, parse_equations

STATISTICS = {"initial", "final", "min", "max", "mean", "integral"}


class ModelIdentityError(RuntimeError):
    """搜索期间源模型或单次仿真身份不一致，必须中止而不能记为普通坏分数。"""


def _check_model_identity(model, fingerprint, result=None):
    if hashlib.sha256(model.read_bytes()).hexdigest() != fingerprint or (
        result is not None and result.metadata.get("model_sha256") != fingerprint
    ):
        raise ModelIdentityError("搜索期间源模型发生变化，结果已拒绝发布，请在稳定模型上重跑")


def number(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} 必须是有限数值")
    if positive and value <= 0:
        raise ValueError(f"{label} 必须大于零")
    return float(value)


def validate_spec(spec, model, mode):
    allowed = {
        "parameters",
        "seed",
        "max_evaluations",
        "population_size",
        "tolerance",
        "time",
        "work_limit",
    }
    allowed |= {"targets", "time_unit"} if mode == "calibrate" else {"objectives", "constraints"}
    if not isinstance(spec, dict) or set(spec) - allowed:
        raise ValueError("搜索规范含未知字段或模式不匹配")
    parameters = spec.get("parameters")
    if not isinstance(parameters, dict) or not 1 <= len(parameters) <= 20:
        raise ValueError("parameters 必须明确提供 1 到 20 个参数的 [下限, 上限]")
    equations = parse_equations(load_mdl_text(model), expand=False)
    bounds, initial = [], []
    for name, limits in parameters.items():
        if name not in equations or name in CONTROL_NAMES:
            raise ValueError(f"搜索参数不是模型业务常量: {name}")
        try:
            value = float(equations[name].rhs)
        except ValueError as exc:
            raise ValueError(f"只能搜索数值常量，不能替换反馈方程: {name}") from exc
        if not math.isfinite(value) or not isinstance(limits, list) or len(limits) != 2:
            raise ValueError(f"{name}: 需要有限常量和两个边界")
        low, high = (number(v, name) for v in limits)
        if low >= high:
            raise ValueError(f"{name}: 下限必须小于上限")
        bounds.append((low, high))
        initial.append(value)
    for key, low, high in (("seed", 0, 2**32 - 1), ("max_evaluations", 10, 5000)):
        if type(spec.get(key)) is not int or not low <= spec[key] <= high:
            raise ValueError(f"必须明确提供整数 {key}，范围 {low} 到 {high}")
    population = spec.get("population_size", 10)
    if type(population) is not int or not 2 <= population <= 50:
        raise ValueError("population_size 必须为 2 到 50 的整数乘数")
    number(spec.get("tolerance", 1e-6), "tolerance", True)
    time = spec.get("time", {})
    if not isinstance(time, dict) or set(time) - {"time_step", "final_time", "saveper"}:
        raise ValueError("time 只接受 time_step、final_time、saveper")
    for key, value in time.items():
        number(value, key, key != "final_time")
    if (
        type(spec.get("work_limit", 50_000_000)) is not int
        or spec.get("work_limit", 50_000_000) <= 0
    ):
        raise ValueError("work_limit 必须是正整数，用于限制预计的标量求值工作量")
    # 搜索必须比较同一时间区间；参数间接影响控制变量时也应拒绝。
    overridden = {name.upper().replace("_", " ") for name in time}
    pending, visited = list(CONTROL_NAMES - overridden), set()
    while pending:
        name = pending.pop()
        if name in visited or name in overridden or name not in equations:
            continue
        visited.add(name)
        pending.extend(extract_deps(equations[name].rhs, set(equations)))
    if set(parameters) & visited:
        raise ValueError("搜索参数影响时间控制；请先将实验时间区间与业务参数分离")
    fields = spec.get("targets" if mode == "calibrate" else "objectives")
    if not isinstance(fields, list) or not fields:
        raise ValueError("必须明确提供 targets 或 objectives")
    for item in fields:
        keys = {"variable", "weight", "scale"} | (
            {"unit"} if mode == "calibrate" else {"statistic", "direction"}
        )
        if (
            not isinstance(item, dict)
            or set(item) != keys
            or not isinstance(item.get("variable"), str)
            or item["variable"] not in equations
        ):
            raise ValueError(
                "目标必须提供存在的 variable、weight、scale；校准还需 unit，政策目标还需 statistic 和 direction"
            )
        number(item["weight"], "weight", True)
        number(item["scale"], "scale", True)
        if mode == "optimize" and (
            not isinstance(item["statistic"], str)
            or not isinstance(item["direction"], str)
            or item["statistic"] not in STATISTICS
            or item["direction"] not in {"minimize", "maximize"}
        ):
            raise ValueError("无效的目标 statistic 或 direction")
        if mode == "calibrate" and (
            not isinstance(item["unit"], str)
            or not item["unit"]
            or item["unit"] != equations[item["variable"]].unit
        ):
            raise ValueError("观测 unit 必须明确且与模型变量单位一致；单位换算应在导入前完成")
    if mode == "calibrate" and len({item["variable"] for item in fields}) != len(fields):
        raise ValueError("校准 targets 不得重复变量")
    constraints = spec.get("constraints", [])
    if not isinstance(constraints, list):
        raise ValueError("constraints 必须是列表")
    for item in constraints:
        if (
            not isinstance(item, dict)
            or set(item) - {"variable", "statistic", "lower", "upper"}
            or not isinstance(item.get("variable"), str)
            or item["variable"] not in equations
            or not isinstance(item.get("statistic"), str)
            or item["statistic"] not in STATISTICS
        ):
            raise ValueError("约束需要存在的 variable、statistic 和上下界")
        if not {"lower", "upper"} & set(item):
            raise ValueError("每条约束至少提供 lower 或 upper")
        for key in {"lower", "upper"} & set(item):
            number(item[key], key)
        if item.get("lower", -math.inf) > item.get("upper", math.inf):
            raise ValueError("约束下界不得高于上界")
    return bounds, initial, fields, constraints


def statistic(result, variable, method):
    times, values = result.times, result.series[variable]
    if method in {"final", "mean", "integral"} and not math.isclose(
        times[-1],
        result.metadata["final_time"],
        rel_tol=0,
        abs_tol=max(1e-9, 8 * math.ulp(times[-1])),
    ):
        raise ValueError("政策统计需要保存最终时刻；请调整 SAVEPER 或实验时间设置")
    if method == "initial":
        return values[0]
    if method == "final":
        return values[-1]
    if method in {"min", "max"}:
        return (min if method == "min" else max)(values)
    if len(times) < 2:
        raise ValueError("mean/integral 至少需要两个保存时刻")
    area = math.fsum(
        (b - a) * (left + right) / 2
        for a, b, left, right in zip(times, times[1:], values, values[1:], strict=False)
    )
    return area if method == "integral" else area / (times[-1] - times[0])


def observation_indices(times, observed_times):
    indices = []
    for timestamp in observed_times:
        index = bisect_left(times, timestamp)
        candidates = [i for i in (index - 1, index) if 0 <= i < len(times)]
        nearest = min(candidates, key=lambda i: abs(times[i] - timestamp))
        tolerance = max(1e-9, 8 * math.ulp(timestamp))
        if abs(times[nearest] - timestamp) > tolerance:
            raise ValueError(
                f"观测时刻 {timestamp} 不在仿真保存网格；请显式调整时间设置，不静默插值"
            )
        indices.append(nearest)
    return indices


class EvaluationBudgetReached(Exception):
    """到达用户指定的实际仿真次数上限。"""


def run_search(
    model, spec, output, mode, data=None, backend="builtin", protected=(), encoding="utf-8-sig"
):
    if mode not in {"calibrate", "optimize"}:
        raise ValueError("搜索模式必须为 calibrate 或 optimize")
    bounds, initial, targets, constraints = validate_spec(spec, model, mode)
    variables = list(dict.fromkeys(item["variable"] for item in [*targets, *constraints]))
    files = [
        output / name
        for name in (
            "optimization.json",
            "evaluations.csv",
            "baseline.csv",
            "best.csv",
            "comparison.csv",
        )
    ]
    preflight_outputs(files, [model, *protected, *([data] if data else [])])
    try:
        import numpy
        import scipy
        from scipy.optimize import NonlinearConstraint, differential_evolution
    except ImportError as exc:
        raise RuntimeError("搜索功能需要可选 scipy，请安装 requirements/analysis.txt") from exc
    fingerprint = hashlib.sha256(model.read_bytes()).hexdigest()
    names = list(spec["parameters"])
    equations = parse_equations(load_mdl_text(model))
    t0, tf, dt, _ = get_time_bounds(equations)
    time = spec.get("time", {})
    tf, dt = time.get("final_time", tf), time.get("time_step", dt)
    if not all(math.isfinite(value) for value in (t0, tf, dt)) or dt <= 0 or tf < t0:
        raise ValueError("模型时间设置必须有限，步长为正，终点不早于起点")
    steps = (tf - t0) / dt
    if not math.isfinite(steps):
        raise ValueError("模型时间设置导致过大的步数，请先检查区间与步长")
    work = (math.ceil(steps) + 1) * len(equations) * spec["max_evaluations"]
    if work > spec.get("work_limit", 50_000_000):
        raise ValueError(
            "预计仿真工作量超过 work_limit；请减少搜索次数，或评估计算资源后显式提高限制"
        )
    _check_model_identity(model, fingerprint)
    base = run_model(model, variables, backend=backend, **spec.get("time", {}))
    _check_model_identity(model, fingerprint, base)
    observed, indices = None, None
    if mode == "calibrate":
        if data is None:
            raise ValueError("校准必须提供真实观测 CSV")
        if (
            not isinstance(spec.get("time_unit"), str)
            or not spec["time_unit"]
            or spec["time_unit"] != base.metadata["time_unit"]
        ):
            raise ValueError("校准规范必须明确 time_unit，并与模型时间单位一致")
        dataset, _ = read_result_csv(
            data, variables, time_unit=spec["time_unit"], encoding=encoding
        )
        if len(dataset) != 1:
            raise ValueError("校准只接受一组观测序列，不能混合多个情景")
        observed = dataset[0][1]
        if len(observed.times) < 2:
            raise ValueError("校准至少需要两个观测时刻")
        indices = observation_indices(base.times, observed.times)
    elif data is not None:
        raise ValueError("政策优化不接受观测 CSV，请使用 calibrate")

    def assessment(result):
        if result.times != base.times or any(
            result.metadata[key] != base.metadata[key]
            for key in ("initial_time", "final_time", "time_step", "saveper")
        ):
            raise ValueError("搜索参数改变了仿真时间网格；不同时间区间的得分不可直接比较")
        components, residuals = [], []
        for item in targets:
            if observed is not None:
                errors = [
                    (result.series[item["variable"]][i] - value) / item["scale"]
                    for i, value in zip(indices, observed.series[item["variable"]], strict=False)
                ]
                value = math.fsum(error * error for error in errors) / len(errors)
            else:
                value = statistic(result, item["variable"], item["statistic"]) / item["scale"]
                if item["direction"] == "maximize":
                    value = -value
            components.append(item["weight"] * value)
        for item in constraints:
            value = statistic(result, item["variable"], item["statistic"])
            if "lower" in item:
                residuals.append(item["lower"] - value)
            if "upper" in item:
                residuals.append(value - item["upper"])
        score = math.fsum(components)
        if not math.isfinite(score) or any(not math.isfinite(v) for v in residuals):
            raise ValueError("目标或约束产生非有限值")
        return score, residuals

    base_score, base_residuals = assessment(base)
    trace, cache, best = [], {}, None
    residual_count = sum(int("lower" in item) + int("upper" in item) for item in constraints)

    def evaluate(values):
        nonlocal best
        key = tuple(float(value) for value in values)
        _check_model_identity(model, fingerprint)
        if key in cache:
            return cache[key]
        if len(trace) >= spec["max_evaluations"]:
            raise EvaluationBudgetReached()
        params = dict(zip(names, key, strict=False))
        result, error = None, ""
        try:
            result = (
                base
                if key == tuple(initial)
                else run_model(
                    model, variables, backend=backend, params=params, **spec.get("time", {})
                )
            )
            _check_model_identity(model, fingerprint, result)
            score, residuals = assessment(result)
            feasible = all(value <= 0 for value in residuals) and all(
                low <= value <= high for value, (low, high) in zip(key, bounds, strict=False)
            )
        except (ValueError, ArithmeticError) as exc:
            score, residuals, feasible, error = (
                math.inf,
                [math.inf] * residual_count,
                False,
                str(exc)[:400],
            )
        record = {
            "evaluation": len(trace) + 1,
            "parameters": params,
            "score": score if math.isfinite(score) else None,
            "feasible": feasible,
            "error": error,
        }
        trace.append(record)
        cache[key] = score, residuals
        if feasible and (best is None or score < best["score"]):
            best = {**record, "result": result}
        return cache[key]

    evaluate(initial)
    native_constraints = (
        (NonlinearConstraint(lambda values: evaluate(values)[1], -math.inf, 0),)
        if constraints
        else ()
    )
    termination, solver_success, message = "budget_exhausted", False, "已达到实际仿真次数上限"
    try:
        result = differential_evolution(
            lambda values: evaluate(values)[0],
            bounds,
            rng=spec["seed"],
            popsize=spec.get("population_size", 10),
            maxiter=spec["max_evaluations"],
            tol=spec.get("tolerance", 1e-6),
            polish=False,
            workers=1,
            constraints=native_constraints,
        )
        solver_success, message = bool(result.success), str(result.message)
        termination = "converged" if solver_success else "solver_stopped"
    except EvaluationBudgetReached:
        pass
    _check_model_identity(model, fingerprint)
    report = {
        "mode": mode,
        "spec": spec,
        "backend": backend,
        "scipy_version": scipy.__version__,
        "numpy_version": numpy.__version__,
        "model_sha256": fingerprint,
        "algorithm": "scipy.differential_evolution",
        "evaluation_count": len(trace),
        "termination": termination,
        "solver_success": solver_success,
        "message": message,
        "feasible_solution_found": best is not None,
        "global_optimum_proven": False,
        "native_verified": False,
        "baseline": {
            "parameters": dict(zip(names, initial, strict=False)),
            "score": base_score,
            "constraints_satisfied": all(v <= 0 for v in base_residuals),
        },
        "best": None,
        "score_definition": "weighted mean squared scaled residuals"
        if observed
        else "weighted signed scaled statistics",
        "integral_method": "trapezoids over saved output samples",
        "run": base.metadata,
        "estimated_work": work,
        "work_limit": spec.get("work_limit", 50_000_000),
    }
    if observed is not None:
        report["observations"] = {
            **observed.metadata,
            "encoding": encoding,
            "units": {item["variable"]: item["unit"] for item in targets},
        }
    runs = [("baseline", base)]
    if best is not None:
        report["best"] = {key: value for key, value in best.items() if key != "result"}
        report["best"]["run"] = best["result"].metadata
        runs.append(("best", best["result"]))
    if observed is not None:
        runs.append(("observed", observed))
    payload = {
        files[1]: csv_bytes(
            ["Evaluation", *[f"Parameter: {name}" for name in names], "Score", "Feasible", "Error"],
            [
                [
                    item["evaluation"],
                    *[item["parameters"][name] for name in names],
                    item["score"],
                    item["feasible"],
                    item["error"],
                ]
                for item in trace
            ],
        ),
        files[2]: csv_bytes(
            ["Time", *variables],
            [[t, *[base.series[v][i] for v in variables]] for i, t in enumerate(base.times)],
        ),
        files[4]: csv_bytes(
            ["Scenario", "Time", "Variable", "Value"],
            [
                [label, t, variable, values[i]]
                for label, result in runs
                for variable, values in result.series.items()
                for i, t in enumerate(result.times)
            ],
        ),
    }
    if best is not None:
        result = best["result"]
        payload[files[3]] = csv_bytes(
            ["Time", *variables],
            [[t, *[result.series[v][i] for v in variables]] for i, t in enumerate(result.times)],
        )
    report["result_files"] = {
        path.name: hashlib.sha256(content).hexdigest() for path, content in payload.items()
    }
    for path, content in payload.items():
        atomic_write(path, content)
    # 报告最后发布；中断留下的数据文件不应带有看似完成的运行报告。
    atomic_write(
        files[0], json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False).encode()
    )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["calibrate", "optimize"])
    parser.add_argument("model", type=Path)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--data", type=Path)
    parser.add_argument(
        "--encoding", default="utf-8-sig", help="观测 CSV 编码；旧中文数据可指定 gb18030"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--backend", choices=["builtin", "pysd"], default="builtin")
    args = parser.parse_args(argv)
    report = run_search(
        args.model,
        json.loads(args.spec.read_text(encoding="utf-8-sig")),
        args.output_dir,
        args.mode,
        args.data,
        args.backend,
        protected=[args.spec],
        encoding=args.encoding,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["feasible_solution_found"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
