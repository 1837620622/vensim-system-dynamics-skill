"""仿真后端与可复现参数覆盖；所有运行都从模型原始初值重新开始。"""

from __future__ import annotations

import hashlib
import math
import platform
import re
import tempfile
from pathlib import Path

from mdl_document import MdlDocument
from vensim_engine import (
    SimResult,
    canonical_name,
    extract_deps,
    get_time_bounds,
    parse_equations,
    simulate,
)

# ---------------------------------------------------------------------------
# 时间设置校验
# ---------------------------------------------------------------------------


CONTROL_NAMES = {"INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER"}
TIME_OPTION_NAMES = {
    "time_step": "TIME STEP",
    "final_time": "FINAL TIME",
    "saveper": "SAVEPER",
}


def validate_parameter_time_dependencies(equations, params, overridden=()):
    """检查参数覆盖是否会间接改变 FINAL TIME、TIME STEP 等时间控制。

    直接覆盖控制变量本来就会被 ``run_model`` 拒绝，但 ``FINAL TIME = 周期``
    这类间接依赖同样会改变输出规模和比较口径。只检查本次没有显式覆盖的
    控制变量；显式时间选项已经由调用方承担其资源上限和时间网格责任。
    """
    if not params:
        return
    overridden_names = {
        canonical_name(TIME_OPTION_NAMES[key]) for key in overridden if key in TIME_OPTION_NAMES
    }
    names = set(equations)
    pending = [
        name
        for name in CONTROL_NAMES
        if name in equations and canonical_name(name) not in overridden_names
    ]
    visited = set()
    dependencies = set()
    while pending:
        name = pending.pop()
        key = canonical_name(name)
        if key in visited or name not in equations:
            continue
        visited.add(key)
        for dependency in extract_deps(equations[name].rhs, names):
            dependencies.add(canonical_name(dependency))
            if canonical_name(dependency) not in visited:
                pending.append(dependency)
    affected = [name for name in params if canonical_name(name) in dependencies]
    if affected:
        raise ValueError(
            "参数影响时间控制（"
            + "、".join(affected)
            + "）；请使用 time_step/final_time/saveper 显式设置仿真时间"
        )


def _validate_pysd_time_grid(t0, tf, dt, saveper):
    """PySD 也必须遵守内置 Euler 使用的保存网格约束。"""
    for label, ratio in (
        ("仿真区间/TIME STEP", (tf - t0) / dt),
        ("SAVEPER/TIME STEP", saveper / dt),
    ):
        if not math.isfinite(ratio) or not math.isclose(
            ratio, round(ratio), rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError(f"PySD 要求 {label} 为整数，请调整步长或保存间隔")
    if saveper < dt:
        raise ValueError("SAVEPER 不能小于 TIME STEP")


def _expected_saved_times(t0, tf, saveper):
    count = int(math.floor((tf - t0) / saveper + 1e-9)) + 1
    return [t0 + index * saveper for index in range(count)]


# ---------------------------------------------------------------------------
# 内置 Euler 与 PySD 对照
# ---------------------------------------------------------------------------


def compare_backends(
    path: Path,
    variables,
    params=None,
    time_step=None,
    final_time=None,
    saveper=None,
    tolerance=1e-10,
):
    """用同一 MDL、参数和保存网格逐点比较内置 Euler 与 PySD。

    两个 Python 实现互相印证，不代表与原生 Vensim 逐点一致。时间网格不一致
    或差值超过容差时报告 pass=False。差值按 max(1, |取值|) 缩放后与容差比较：
    取值小于 1 时相当于绝对误差，大于 1 时相当于相对误差，人口、资金这类
    大数值变量的浮点舍入不会被误判为不一致。
    """
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
        raise ValueError("tolerance 必须是有限非负数")
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance 必须是有限非负数")
    options = {
        key: value
        for key, value in {
            "params": params or {},
            "time_step": time_step,
            "final_time": final_time,
            "saveper": saveper,
        }.items()
        if value is not None or key == "params"
    }
    builtin = run_model(path, variables, backend="builtin", **options)
    pysd = run_model(path, variables, backend="pysd", **options)
    if len(builtin.times) != len(pysd.times) or any(
        not math.isclose(left, right, rel_tol=0, abs_tol=tolerance)
        for left, right in zip(builtin.times, pysd.times, strict=False)
    ):
        raise ValueError("内置 Euler 与 PySD 的保存时刻不一致，无法逐点比较")
    differences, scaled = {}, {}
    for name in variables:
        pairs = list(zip(builtin.series[name], pysd.series[name], strict=False))
        differences[name] = max((abs(left - right) for left, right in pairs), default=0.0)
        scaled[name] = max(
            (abs(left - right) / max(1.0, abs(left), abs(right)) for left, right in pairs),
            default=0.0,
        )
    maximum = max(differences.values(), default=0.0)
    worst = max(scaled.values(), default=0.0)
    return {
        "pass": worst <= tolerance,
        "tolerance": tolerance,
        "max_absolute_error": maximum,
        "max_scaled_error": worst,
        "differences": differences,
        "variables": list(variables),
        "saved_points": len(builtin.times),
        "time_grid": builtin.times,
        "model_sha256": builtin.metadata["model_sha256"],
        "builtin": builtin.metadata,
        "pysd": pysd.metadata,
    }


# ---------------------------------------------------------------------------
# 参数覆盖与统一运行入口
# ---------------------------------------------------------------------------


def parse_overrides(items):
    params = {}
    for item in items:
        name, sep, value = item.rpartition("=")
        if not sep or not name.strip():
            raise ValueError("参数格式应为 --set '变量名=数值'")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"参数 {name} 必须为有限数")
        if name.strip() in params:
            raise ValueError(f"参数重复: {name.strip()}")
        params[name.strip()] = number
    return params


def run_model(
    path: Path,
    variables=None,
    backend="builtin",
    params=None,
    time_step=None,
    final_time=None,
    saveper=None,
    strict=True,
):
    document = MdlDocument.read(path)
    text = document.semantic_text
    fingerprint = hashlib.sha256(document.raw).hexdigest()
    originals = parse_equations(text, expand=False)
    missing_controls = CONTROL_NAMES - set(originals)
    if missing_controls:
        raise ValueError("模型缺少明确的时间控制变量: " + ", ".join(sorted(missing_controls)))
    params = params or {}
    for name, value in params.items():
        if name not in originals:
            raise ValueError(f"覆盖参数不存在: {name}")
        if name in CONTROL_NAMES or originals[name].integ_flow is not None:
            raise ValueError(f"{name}: 请使用时间设置选项；存量初值请通过独立初值参数调整")
        try:
            float(originals[name].rhs)
        except ValueError as exc:
            raise ValueError(f"{name}: 只能覆盖数值常量") from exc
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"参数 {name} 必须是有限数")
    selected = (
        variables
        if variables is not None
        else [
            name for name, eq in originals.items() if name not in CONTROL_NAMES and not eq.is_lookup
        ]
    )
    if (
        isinstance(selected, str)
        or any(not isinstance(name, str) for name in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("变量必须为不重复的名称列表")
    if not selected:
        raise ValueError("模型没有可导出的变量")
    if any(name not in originals for name in selected):
        raise ValueError(
            "请求的变量不存在: " + ", ".join(name for name in selected if name not in originals)
        )
    if any(originals[name].is_lookup for name in selected):
        raise ValueError("Lookup 是函数，不能作为标量轨迹导出；请选择调用该表的变量")
    validate_parameter_time_dependencies(
        originals,
        params,
        overridden=[
            key
            for key, value in {
                "time_step": time_step,
                "final_time": final_time,
                "saveper": saveper,
            }.items()
            if value is not None
        ],
    )
    for name, value in params.items():
        originals[name].rhs = str(value)
    t0, tf, dt, sp = get_time_bounds(originals)
    tf = tf if final_time is None else final_time
    dt = dt if time_step is None else time_step
    sp = sp if saveper is None else saveper
    if (
        any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in (t0, tf, dt, sp)
        )
        or tf < t0
        or dt <= 0
        or sp <= 0
    ):
        raise ValueError("仿真时间必须有限，终点不能早于起点，时间步长与保存间隔必须为正数")
    if (tf - t0) / dt > 1_000_000 or ((tf - t0) / sp + 1) * len(originals) > 5_000_000:
        raise ValueError("仿真规模超限，请缩短区间或增大保存间隔")
    if backend == "builtin":
        equations = parse_equations(text)
        for name, value in params.items():
            equations[name].rhs = str(value)
        for name, value in (("FINAL TIME", tf), ("TIME STEP", dt), ("SAVEPER", sp)):
            if name in equations:
                equations[name].rhs = str(value)
        result = simulate(equations, t0, tf, dt, sp, strict=strict)
        result.series = {name: result.series[name] for name in selected}
    elif backend == "pysd":
        if not strict:
            raise ValueError("PySD 后端不支持 --keep-going")
        _validate_pysd_time_grid(t0, tf, dt, sp)
        try:
            import pysd
        except ImportError as exc:
            raise RuntimeError("需要可选依赖 pysd；或改用 --backend builtin") from exc
        # 只翻译不引用外部数据文件的模型，临时目录里找不到相对路径的数据
        if re.search(
            r"\b(GET (?:XLS|DIRECT|VDF|123)|GET DATA|FILE|TABBED ARRAY)\b",
            text.split(r"\\\---///", 1)[0],
            re.I,
        ):
            raise ValueError(
                "PySD 临时翻译仅支持自包含模型；外部数据模型请在原项目中使用 PySD 或原生 Vensim"
            )
        with tempfile.TemporaryDirectory(prefix="vensim-pysd-") as folder:
            copied = Path(folder) / "model.mdl"
            copied.write_text(text, encoding="utf-8")
            model = pysd.read_vensim(str(copied))
            # PySD 保留名字两侧的引号（如 "Population Aged 0-14"），按 Vensim 名称规则对应
            native = {canonical_name(key): key for key in model.namespace}

            def pysd_name(name):
                if canonical_name(name) not in native:
                    raise ValueError(f"PySD 翻译结果中找不到变量: {name}")
                return native[canonical_name(name)]

            columns = [pysd_name(name) for name in selected]
            frame = model.run(
                params={pysd_name(name): value for name, value in params.items()},
                return_columns=columns,
                initial_condition="original",
                time_step=dt,
                final_time=tf,
                saveper=sp,
            )
            result = SimResult(
                frame.index.astype(float).tolist(),
                {
                    name: frame[column].astype(float).tolist()
                    for name, column in zip(selected, columns, strict=True)
                },
            )
        if not result.times or any(
            not math.isfinite(value) for values in result.series.values() for value in values
        ):
            raise ValueError("PySD 返回空数据或非有限数值")
        expected_times = _expected_saved_times(t0, tf, sp)
        if len(result.times) != len(expected_times) or any(
            not math.isclose(actual, expected, rel_tol=0, abs_tol=1e-9)
            for actual, expected in zip(result.times, expected_times, strict=False)
        ):
            raise ValueError("PySD 返回的保存网格与请求的 TIME STEP/SAVEPER 不一致")
    else:
        raise ValueError(f"不支持的仿真后端: {backend}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != fingerprint:
        raise ValueError("仿真期间源模型发生变化，结果已拒绝发布")
    result.metadata = {
        "backend": backend,
        "model_sha256": fingerprint,
        "parameters": params,
        "initial_time": t0,
        "time_step": dt,
        "final_time": tf,
        "time_unit": originals["INITIAL TIME"].unit if "INITIAL TIME" in originals else "",
        "saveper": sp,
        "saved_points": len(result.times),
        "actual_last_time": result.times[-1] if result.times else None,
        "variables": selected,
        "variable_units": {name: originals[name].unit for name in selected},
        "numerical_method": "Euler",
        "python_version": platform.python_version(),
        "backend_version": pysd.__version__ if backend == "pysd" else None,
        "status": "diagnostic_only" if result.eval_warnings else "completed",
        "warnings": result.eval_warnings,
    }
    return result
