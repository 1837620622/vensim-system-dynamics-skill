"""仿真后端与可复现参数覆盖；所有运行都从模型原始初值重新开始。"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
import tempfile

from vensim_engine import (
    SimResult, get_time_bounds, load_mdl_text, parse_equations, simulate,
)

CONTROL_NAMES = {"INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER"}


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


def run_model(path: Path, variables=None, backend="builtin", params=None,
              time_step=None, final_time=None, saveper=None, strict=True):
    text = load_mdl_text(path)
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
            raise ValueError(f"{name}: 只能覆盖数值常量参数，不能替换反馈方程或延迟状态") from exc
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"参数 {name} 必须是有限数")
    selected = variables or [name for name in originals if name not in CONTROL_NAMES]
    if isinstance(selected, str) or any(not isinstance(name, str) for name in selected) or len(set(selected)) != len(selected):
        raise ValueError("变量必须为不重复的名称列表")
    if not selected:
        raise ValueError("模型没有可导出的变量")
    if any(name not in originals for name in selected):
        raise ValueError("请求的变量不存在: " + ", ".join(name for name in selected if name not in originals))
    for name, value in params.items():
        originals[name].rhs = str(value)
    t0, tf, dt, sp = get_time_bounds(originals)
    tf = tf if final_time is None else final_time
    dt = dt if time_step is None else time_step
    sp = sp if saveper is None else saveper
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (t0, tf, dt, sp)) or tf < t0 or dt <= 0 or sp <= 0:
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
        try:
            import pysd
        except ImportError as exc:
            raise RuntimeError("需要可选依赖 pysd；或改用 --backend builtin") from exc
        # 只翻译自包含模型，避免悄悄改变外部数据文件的相对路径。
        if re.search(r"\b(GET (?:XLS|DIRECT|VDF|123)|GET DATA|FILE|TABBED ARRAY)\b", text.split(r"\\\---///", 1)[0], re.I):
            raise ValueError("PySD 临时翻译仅支持自包含模型；外部数据模型请在原项目中使用 PySD 或原生 Vensim")
        with tempfile.TemporaryDirectory(prefix="vensim-pysd-") as folder:
            copied = Path(folder) / "model.mdl"
            copied.write_text(text, encoding="utf-8")
            model = pysd.read_vensim(str(copied))
            options = {key: value for key, value in {"time_step": time_step, "final_time": final_time, "saveper": saveper}.items() if value is not None}
            frame = model.run(params=params, return_columns=selected, initial_condition="original", **options)
            result = SimResult(frame.index.astype(float).tolist(), {name: frame[name].astype(float).tolist() for name in selected})
        if not result.times or any(not math.isfinite(value) for values in result.series.values() for value in values):
            raise ValueError("PySD 返回空数据或非有限数值")
    else:
        raise ValueError(f"不支持的仿真后端: {backend}")
    result.metadata = {"backend": backend, "model_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "parameters": params, "initial_time": t0, "time_step": dt, "final_time": tf,
                       "time_unit": originals["INITIAL TIME"].unit if "INITIAL TIME" in originals else "",
                       "saveper": sp, "native_verified": False, "variables": selected,
                       "numerical_method": "Euler" if backend == "builtin" else "PySD",
                       "status": "diagnostic_only" if result.eval_warnings else "completed",
                       "warnings": result.eval_warnings}
    return result
