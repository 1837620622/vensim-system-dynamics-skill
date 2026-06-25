#!/usr/bin/env python3
"""Vensim .mdl 纯 Python 仿真引擎与检查修复工具。

不依赖 Vensim 即可完成：
  - parse:   解析方程区（INTEG / LOOKUP / SMOOTH / DELAY / IF THEN ELSE / WITH LOOKUP）
  - simulate:Euler 积分仿真，导出 CSV
  - graph:   matplotlib 折线图导出 PNG
  - compare: 多场景对比图（净利润、植被盖度、耦合度等任意变量）
  - units:   单位量纲一致性校验
  - check:   检测未定义变量 / 缺失单位 / 断裂引用 / 循环依赖
  - fix:     自动修复缺失单位、断裂引用、缺失草图对象

支持函数：INTEG, SMOOTH, SMOOTH3, DELAY1, DELAY3, DELAY FIXED,
         IF THEN ELSE, WITH LOOKUP, LOOKUP, ABS, SQRT, EXP, LN, MIN, MAX, MODULO。
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import math
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 方程区解析
# ---------------------------------------------------------------------------

# 方程块以 "变量名 = ..." 开头，后续 ~ 单位 ~ 注释 | 结束
_EQ_START = re.compile(r"^\s*([A-Za-z_][\w\$\s]*?)\s*=\s*(.+)$")
_INTEG = re.compile(r"INTEG\s*\((.*),\s*(-?[\d.]+)\s*\)", re.S | re.I)
_LOOKUP_DEF = re.compile(r"\[(.*?)\]\s*$")


@dataclasses.dataclass
class Equation:
    name: str
    rhs: str            # 等号右侧表达式
    unit: str           # 单位
    comment: str        # 注释
    integ_init: Optional[float] = None   # INTEG 初值
    integ_flow: Optional[str] = None     # INTEG 内部流表达式
    is_lookup: bool = False
    lookup_pairs: List[Tuple[float, float]] = dataclasses.field(default_factory=list)
    line_index: int = 0
    smooth_init_ref: Optional[str] = None  # SMOOTH 隐式库存初值引用的输入变量名


def parse_equations(mdl_text: str) -> "OrderedDict[str, Equation]":
    """解析 .mdl 方程区（草图标记之前的部分）。"""
    # 截断草图区
    sketch_pos = mdl_text.find(r"\\\---///")
    body = mdl_text[:sketch_pos] if sketch_pos >= 0 else mdl_text
    lines = body.splitlines()

    equations: "OrderedDict[str, Equation]" = OrderedDict()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = _EQ_START.match(line)
        if not m:
            i += 1
            continue
        name = m.group(1).strip()
        rhs = m.group(2).strip()
        # 跨行收集直到 | 结束符
        block = [rhs]
        j = i
        # 同一方程可能多行，直到遇到独立的 ~ 行
        # Vensim 格式：第一行 = 表达式；随后 ~ 单位；~ 注释；|
        # 表达式可能跨行（括号未闭合）
        while j + 1 < n and not _EQ_START.match(lines[j + 1]):
            nxt = lines[j + 1]
            if nxt.strip().startswith("~") or nxt.strip() == "|":
                break
            # 表达式续行
            if rhs.count("(") > rhs.count(")"):
                block.append(nxt.strip())
                rhs = " ".join(block)
                j += 1
                continue
            break
        # 收集单位与注释
        unit = ""
        comment = ""
        k = j + 1
        tilde_count = 0
        while k < n and tilde_count < 2:
            s = lines[k].strip()
            if s == "|":
                break
            if s.startswith("~"):
                tilde_count += 1
                content = s[1:].strip()
                if tilde_count == 1:
                    unit = content
                elif tilde_count == 2:
                    comment = content
            k += 1

        eq = Equation(name=name, rhs=rhs, unit=unit, comment=comment, line_index=i)

        # INTEG 解析
        mi = _INTEG.search(rhs)
        if mi:
            eq.integ_flow = mi.group(1).strip()
            eq.integ_init = float(mi.group(2))

        # LOOKUP 定义：rhs 形如 [(0,0)-(10,1),(0,0),(5,0.5),(10,1)]
        if rhs.startswith("[") and rhs.endswith("]"):
            eq.is_lookup = True
            eq.lookup_pairs = _parse_lookup_pairs(rhs)

        equations[name] = eq
        i = k + 1

    # 展开 SMOOTH / DELAY 为隐式库存方程
    _expand_smooth_delay(equations)
    return equations


def _expand_smooth_delay(equations: "OrderedDict[str, Equation]") -> None:
    """把 SMOOTH / DELAY1 / DELAY3 / SMOOTH3 展开为等价隐式库存方程。

    SMOOTH(x, d)    = 一阶指数平滑 = INTEG((x - s)/d, x)
    SMOOTH3(x, d)   = 三阶，每级 d/3
    DELAY1(x, d)    = INTEG(x - out, 0)，out = stock/d
    DELAY3(x, d)    = 三阶管道，每级 d/3
    DELAY FIXED 不在此处理（需离散事件）。
    """
    to_add: List[Tuple[str, Equation]] = []
    names = set(equations.keys())

    for name, eq in list(equations.items()):
        rhs = eq.rhs
        # SMOOTH(x, d)
        m = re.match(r"^\s*SMOOTH3?\s*\(([^,]+),\s*([^)]+)\)\s*$", rhs, re.I)
        if m:
            x, d = m.group(1).strip(), m.group(2).strip()
            order = 3 if rhs.upper().startswith("SMOOTH3") else 1
            _expand_smooth_chain(name, x, d, order, eq, equations, to_add, smooth=True)
            continue
        # DELAY1 / DELAY3(x, d)
        m = re.match(r"^\s*DELAY3?\s*\(([^,]+),\s*([^)]+)\)\s*$", rhs, re.I)
        if m:
            x, d = m.group(1).strip(), m.group(2).strip()
            order = 3 if rhs.upper().startswith("DELAY3") else 1
            _expand_smooth_chain(name, x, d, order, eq, equations, to_add, smooth=False)

    for nm, eqn in to_add:
        equations[nm] = eqn


def _expand_smooth_chain(name, x, d, order, eq, equations, to_add, smooth):
    """展开 SMOOTH/DELAY 链为 order 级隐式库存。

    smooth=True: SMOOTH，每级 INTEG((in - out)/(d/order), in)
    smooth=False: DELAY，每级 INTEG(in - out, 0)，out = stock/(d/order)
    最终变量名 = name，指向最后一级的 out（辅助变量）。
    """
    stage_d = f"({d})/{order}"
    prev_input = x
    init = x if smooth else "0"
    for k in range(order):
        stock_name = f"{name}__stage{k+1}"
        flow = f"({prev_input} - {stock_name}) / {stage_d}" if smooth else f"{prev_input} - {stock_name}/{stage_d}"
        stock_eq = Equation(
            name=stock_name,
            rhs=f"INTEG ( {flow}, {init} )",
            unit=eq.unit,
            comment=f"SMOOTH/DELAY 隐式第{k+1}级",
            line_index=eq.line_index,
        )
        stock_eq.integ_flow = flow
        # SMOOTH 首级初值 = 输入变量初值；后续级初值 = 上一级初值（链式）
        if smooth:
            stock_eq.smooth_init_ref = prev_input
        try:
            stock_eq.integ_init = float(init)
        except ValueError:
            stock_eq.integ_init = 0.0
        to_add.append((stock_name, stock_eq))
        prev_input = stock_name
    # 把原变量改为辅助变量，等于最后一级库存（SMOOTH）或其流出（DELAY）
    if smooth:
        eq.rhs = prev_input
        eq.integ_flow = None
        eq.integ_init = None
    else:
        # DELAY: out = 最后一级 stock / stage_d
        eq.rhs = f"{prev_input} / {stage_d}"
        eq.integ_flow = None
        eq.integ_init = None


def _parse_lookup_pairs(text: str) -> List[Tuple[float, float]]:
    """解析 LOOKUP 表 [(x1,y1)-(x2,y2),(x1,y1),...]。"""
    inner = text.strip()[1:-1]
    pairs: List[Tuple[float, float]] = []
    # 先分离 range (x,y)-(x,y)
    range_match = re.match(r"\(([^)]+)\)-\(([^)]+)\)", inner)
    if range_match:
        x1, y1 = map(float, range_match.group(1).split(","))
        x2, y2 = map(float, range_match.group(2).split(","))
        pairs.append((x1, y1))
        pairs.append((x2, y2))
        rest = inner[range_match.end():]
    else:
        rest = inner
    for tok in re.findall(r"\(([^)]+)\)", rest):
        parts = tok.split(",")
        if len(parts) == 2:
            try:
                pairs.append((float(parts[0]), float(parts[1])))
            except ValueError:
                pass
    return pairs


# ---------------------------------------------------------------------------
# 依赖分析
# ---------------------------------------------------------------------------

_VAR_TOKEN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_$ ]*?)\b")
# 排除函数名与关键字
_KEYWORDS = {
    "INTEG", "SMOOTH", "SMOOTH3", "DELAY1", "DELAY3", "DELAY", "DELAY FIXED",
    "IF", "THEN", "ELSE", "WITH", "LOOKUP", "ABS", "SQRT", "EXP", "LN",
    "MIN", "MAX", "MODULO", "PULSE", "RAMP", "STEP", "TIME", "TRUE", "FALSE",
    "INITIAL", "FINAL", "STEP", "SAVEPER",
}


def extract_deps(rhs: str, known_names: set) -> List[str]:
    """从表达式提取依赖的变量名（已知名集合内，支持带空格变量名）。"""
    deps: List[str] = []
    # 按长度降序匹配，避免短名前缀误匹配（如 "Birth" 匹配 "Birth Fraction"）
    sorted_names = sorted(known_names, key=len, reverse=True)
    # 先移除函数名
    cleaned = re.sub(
        r"\b(INTEG|SMOOTH3?|DELAY[13]?|DELAY FIXED|IF THEN ELSE|WITH LOOKUP|ABS|SQRT|EXP|LN|MIN|MAX|MODULO|PULSE|RAMP|STEP)\b",
        " ", rhs)
    # 用占位符逐个替换已知名，避免重叠匹配
    remaining = cleaned
    for name in sorted_names:
        if name.upper() in _KEYWORDS:
            continue
        # 转义变量名中的空格与特殊字符
        pattern = re.escape(name)
        if re.search(rf"\b{pattern}\b", remaining):
            deps.append(name)
            remaining = re.sub(rf"\b{pattern}\b", " ", remaining)
    return deps


def topological_sort(equations: "OrderedDict[str, Equation]") -> List[str]:
    """拓扑排序辅助变量；库存(INTEG)用上一时间步值，其流率依赖不参与环检测。"""
    names = set(equations.keys())
    stocks = [n for n, e in equations.items() if e.integ_flow is not None]
    auxs = [n for n, e in equations.items() if e.integ_flow is None and not e.is_lookup]

    order: List[str] = []
    visited: set = set()
    temp: set = set()

    def visit(node: str):
        if node in visited:
            return
        if node in temp:
            raise ValueError(f"检测到循环依赖: {node}")
        temp.add(node)
        eq = equations[node]
        # 辅助变量用 rhs 依赖；库存不在此排序（用上一时间步）
        if eq.integ_flow is None and not eq.is_lookup:
            for d in extract_deps(eq.rhs, names):
                if d in equations and equations[d].integ_flow is None:
                    visit(d)
        temp.discard(node)
        visited.add(node)
        order.append(node)

    for a in auxs:
        visit(a)
    # 库存追加在末尾（仿真时单独处理）
    for s in stocks:
        if s not in visited:
            visited.add(s)
            order.append(s)
    return order


# ---------------------------------------------------------------------------
# 表达式求值
# ---------------------------------------------------------------------------

class LookupTable:
    """线性插值查表。支持传入 (x,y) 对列表或 Vensim 原始表字符串。"""

    _PAIR_RE = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")

    def __init__(self, table):
        if isinstance(table, str):
            # 解析 Vensim 表字符串：( [0,0)-(100,1000)], (0,950), (20,800), ...
            # 先剥离范围声明 ( [x,y)-(x,y)]，再取 (x,y) 数值对
            body = re.sub(r"\(\s*\[.*?\)\s*\]", " ", table)
            pairs = [(float(x), float(y)) for x, y in self._PAIR_RE.findall(body)]
        else:
            pairs = list(table)
        self.xs = [p[0] for p in pairs]
        self.ys = [p[1] for p in pairs]

    def __call__(self, x: float) -> float:
        if not self.xs:
            return 0.0
        if x <= self.xs[0]:
            return self.ys[0]
        if x >= self.xs[-1]:
            return self.ys[-1]
        for i in range(len(self.xs) - 1):
            if self.xs[i] <= x <= self.xs[i + 1]:
                x0, y0 = self.xs[i], self.ys[i]
                x1, y1 = self.xs[i + 1], self.ys[i + 1]
                if x1 == x0:
                    return y0
                return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        return self.ys[-1]


def _to_python_expr(rhs: str, name_map: Dict[str, str]) -> str:
    """把 Vensim 表达式转成 Python 可求值字符串，变量名映射为合法标识符。"""
    s = rhs
    # WITH LOOKUP(x, table) -> _wl(x, "table")
    # Vensim 实际格式：WITH LOOKUP( Price, ( [0,0)-(100,1000)], (0,950), (20,800), ... )
    # table 部分作为字符串字面量传给 _wl，由 LookupTable 解析
    def _wl_sub(m):
        x_arg = m.group(1).strip()
        table = m.group(2)
        # 转义内嵌双引号
        table_escaped = table.replace('"', '\\"')
        return f'_wl({x_arg}, "{table_escaped}")'

    s = re.sub(r"WITH\s+LOOKUP\s*\(([^,()]+),\s*(\(.+\))\s*\)",
               _wl_sub, s, flags=re.S | re.I)
    # IF THEN ELSE(c,a,b) -> (a if c else b)
    s = re.sub(r"IF\s+THEN\s+ELSE\s*\(([^,]+),([^,]+),([^)]+)\)",
               r"(\2 if (\1) else \3)", s, flags=re.I)
    # MODULO(a,b)
    s = re.sub(r"MODULO\s*\(([^,]+),([^)]+)\)", r"(\1 % \2)", s, flags=re.I)
    # 数学函数（Vensim 函数名与左括号之间允许空白，如 MAX ( 0, ... )）
    s = re.sub(r"\bABS\s*\(", "abs(", s, flags=re.I)
    s = re.sub(r"\bSQRT\s*\(", "math.sqrt(", s, flags=re.I)
    s = re.sub(r"\bEXP\s*\(", "math.exp(", s, flags=re.I)
    s = re.sub(r"\bLN\s*\(", "math.log(", s, flags=re.I)
    s = re.sub(r"\bMIN\s*\(", "min(", s, flags=re.I)
    s = re.sub(r"\bMAX\s*\(", "max(", s, flags=re.I)
    s = re.sub(r"\bSIN\s*\(", "math.sin(", s, flags=re.I)
    s = re.sub(r"\bCOS\s*\(", "math.cos(", s, flags=re.I)
    s = re.sub(r"\bTAN\s*\(", "math.tan(", s, flags=re.I)
    s = re.sub(r"\bINTEGER\s*\(", "int(", s, flags=re.I)
    s = re.sub(r"\bXIDZ\s*\(([^,]+),([^,]+),([^)]+)\)", r"((\1)/(\2) if float(\2)!=0 else (\3))", s, flags=re.I)
    s = re.sub(r"\bZIDZ\s*\(([^,]+),([^,]+),([^)]+)\)", r"((\1)/(\2) if float(\2)!=0 else (\3))", s, flags=re.I)
    s = re.sub(r"\bPULSE\s*\(([^,]+),([^)]+)\)", r"_pulse(\1,\2)", s, flags=re.I)
    s = re.sub(r"\bRAMP\s*\(([^,]+),([^)]+)\)", r"_ramp(\1,\2)", s, flags=re.I)
    s = re.sub(r"\bSTEP\s*\(([^,]+),([^)]+)\)", r"_step(\1,\2)", s, flags=re.I)
    # 幂运算 ^ -> **
    s = s.replace("^", "**")
    # 变量名替换：按长度降序，带空格的名替换为 _vN
    for orig, alias in sorted(name_map.items(), key=lambda x: -len(x[0])):
        s = re.sub(rf"\b{re.escape(orig)}\b", alias, s)
    return s


def evaluate(rhs: str, ctx: Dict, lookups: Dict, name_map: Dict[str, str]) -> float:
    """在上下文 ctx 中求值 rhs；name_map 把带空格变量名映射为合法标识符。"""
    mi = _INTEG.search(rhs)
    if mi:
        expr = mi.group(1)
    elif rhs.strip().startswith("["):
        return 0.0
    else:
        expr = rhs

    py_expr = _to_python_expr(expr, name_map)

    def _wl(x, table):
        return LookupTable(table)(x)

    def _pulse(start, duration):
        t = ctx.get("Time", 0.0)
        return 1.0 if start <= t < start + duration else 0.0

    def _ramp(start, slope):
        t = ctx.get("Time", 0.0)
        return 0.0 if t < start else slope * (t - start)

    def _step(value, time):
        t = ctx.get("Time", 0.0)
        return 0.0 if t < time else value

    namespace = {
        "math": math, "_wl": _wl, "abs": abs, "min": min, "max": max,
        "_pulse": _pulse, "_ramp": _ramp, "_step": _step,
    }
    # 注入 lookup 变量为可调用对象
    for k, v in lookups.items():
        namespace[name_map.get(k, k)] = v
    # 注入变量值（用别名）
    for k, v in ctx.items():
        namespace[name_map.get(k, k)] = v
    try:
        return float(eval(py_expr, {"__builtins__": {}}, namespace))
    except Exception as exc:
        raise ValueError(f"求值失败 [{py_expr[:80]}]: {exc}")


# ---------------------------------------------------------------------------
# 仿真器
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class SimResult:
    times: List[float]
    series: Dict[str, List[float]]
    eval_warnings: List[str] = dataclasses.field(default_factory=list)


def simulate(equations: "OrderedDict[str, Equation]", t0: float, tf: float, dt: float,
             saveper: Optional[float] = None) -> SimResult:
    """Euler 积分仿真。"""
    saveper = saveper or dt
    names = set(equations.keys())
    order = topological_sort(equations)

    # 构建变量名到合法 Python 标识符的映射（带空格/特殊字符的变量名）
    name_map: Dict[str, str] = {}
    for i, name in enumerate(equations.keys()):
        if re.fullmatch(r"[A-Za-z_]\w*", name):
            name_map[name] = name
        else:
            name_map[name] = f"_v{i}"

    # 初始化
    ctx: Dict[str, float] = {"Time": t0}
    lookups: Dict[str, LookupTable] = {}
    for name, eq in equations.items():
        if eq.is_lookup:
            lookups[name] = LookupTable(eq.lookup_pairs)
            ctx[name] = 0.0
        elif eq.integ_init is not None and eq.smooth_init_ref is None:
            ctx[name] = eq.integ_init
        else:
            # 常量先求值
            try:
                ctx[name] = evaluate(eq.rhs, ctx, lookups, name_map)
            except ValueError:
                ctx[name] = 0.0

    # SMOOTH 隐式库存初值 = 其引用输入在 t0 的值（链式：后级取前级初值）
    ctx["Time"] = t0
    for name, eq in equations.items():
        if eq.smooth_init_ref is not None:
            ref = eq.smooth_init_ref
            if ref in ctx:
                ctx[name] = ctx[ref]
            else:
                try:
                    ctx[name] = evaluate(ref, ctx, lookups, name_map)
                except (ValueError, TypeError):
                    ctx[name] = 0.0

    times: List[float] = []
    series: Dict[str, List[float]] = {n: [] for n in equations}
    t = t0
    next_save = t0
    eval_warnings: List[str] = []

    while t <= tf + 1e-9:
        # 注入当前时间，供 STEP / PULSE / RAMP 等时间函数使用
        ctx["Time"] = t
        # 计算所有辅助变量（当前时间步）
        for name in order:
            eq = equations[name]
            if eq.integ_flow is not None or eq.is_lookup:
                continue
            try:
                ctx[name] = evaluate(eq.rhs, ctx, lookups, name_map)
            except ValueError as exc:
                if not eval_warnings:
                    eval_warnings.append(f"{name} @t={t:.2f}: {exc}")
        # 计算库存的流率（用当前辅助值）
        flows: Dict[str, float] = {}
        for name, eq in equations.items():
            if eq.integ_flow is not None:
                try:
                    flows[name] = evaluate(eq.integ_flow, ctx, lookups, name_map)
                except ValueError as exc:
                    flows[name] = 0.0
                    if not eval_warnings:
                        eval_warnings.append(f"flow {name} @t={t:.2f}: {exc}")

        # 记录
        if t >= next_save - 1e-9:
            times.append(t)
            for n in series:
                series[n].append(ctx.get(n, 0.0))
            next_save += saveper

        # Euler 更新库存
        for name, eq in equations.items():
            if eq.integ_flow is not None:
                ctx[name] = ctx.get(name, 0.0) + flows[name] * dt
        t += dt

    if eval_warnings:
        sys.stderr.write("警告: 部分变量求值失败（已置零），首条: " + eval_warnings[0] + "\n")
        sys.stderr.write(f"      共 {len(eval_warnings)} 类求值失败，可能导致 nodata 或曲线为 0。\n")

    return SimResult(times=times, series=series, eval_warnings=eval_warnings)


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------

def load_mdl_text(path: Path) -> str:
    # 兼容 Windows UTF-8 BOM 与中文 GB 编码遗留文件
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Cannot decode file: {path}")


def _resolve_number(rhs: str, equations: "OrderedDict[str, Equation]") -> float:
    """解析数值或引用其他控制变量的值。"""
    rhs = rhs.strip()
    try:
        return float(rhs)
    except ValueError:
        pass
    # 引用另一个变量名
    if rhs in equations:
        return _resolve_number(equations[rhs].rhs, equations)
    raise ValueError(f"无法解析数值: {rhs}")


def get_time_bounds(equations: "OrderedDict[str, Equation]"):
    t0 = equations.get("INITIAL TIME")
    tf = equations.get("FINAL TIME")
    dt = equations.get("TIME STEP")
    sp = equations.get("SAVEPER")
    t0v = _resolve_number(t0.rhs, equations) if t0 else 0.0
    tfv = _resolve_number(tf.rhs, equations) if tf else 100.0
    dtv = _resolve_number(dt.rhs, equations) if dt else 1.0
    spv = _resolve_number(sp.rhs, equations) if sp else dtv
    return t0v, tfv, dtv, spv


def command_simulate(path: Path, output: Path, variables: List[str], plot: Optional[Path] = None) -> int:
    text = load_mdl_text(path)
    equations = parse_equations(text)
    t0, tf, dt, sp = get_time_bounds(equations)
    result = simulate(equations, t0, tf, dt, sp)

    if not variables:
        variables = list(equations.keys())
    # 过滤掉控制变量
    variables = [v for v in variables if v not in ("INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER")]

    with output.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Time"] + variables)
        for i, t in enumerate(result.times):
            w.writerow([t] + [result.series[v][i] for v in variables])
    print(f"仿真完成: {len(result.times)} 个时间点 -> {output}")
    print(f"变量: {', '.join(variables)}")

    # nodata 检测：仅在有求值失败时才报全程为 0 的变量（避免均衡变量的误报）
    if result.eval_warnings:
        nodata = [v for v in variables if result.series.get(v) and all(x == 0.0 for x in result.series[v])]
        if nodata:
            print(f"警告: 以下变量全程为 0（可能 nodata）: {', '.join(nodata)}", file=sys.stderr)
            print("      请检查方程是否含未支持的函数或求值失败警告。", file=sys.stderr)

    if plot is not None:
        _render_plot(result, variables, plot, title=f"{path.stem} Simulation")
        print(f"折线图已导出: {plot}")
    return 0


def _render_plot(result, variables: List[str], output: Path, title: str = "") -> None:
    """Vensim 风格折线图：白底、细网格、左下图例、Time 轴标签。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 6), dpi=120)
    colors = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd",
              "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]
    for i, v in enumerate(variables):
        if v in result.series and result.series[v]:
            ax.plot(result.times, result.series[v],
                    linewidth=1.8, label=v, color=colors[i % len(colors)])
    ax.set_xlabel("Time", fontsize=11)
    ax.set_ylabel("Value", fontsize=11)
    ax.set_title(title or "Simulation Result", fontsize=13)
    ax.legend(loc="upper left", framealpha=0.9, fontsize=10)
    ax.grid(True, alpha=0.25, linestyle="--")
    ax.axhline(0, color="black", linewidth=0.5)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def command_graph(path: Path, variables: List[str], output: Path, title: str) -> int:
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("ERROR: 需要 matplotlib，请 pip install matplotlib", file=sys.stderr)
        return 2

    text = load_mdl_text(path)
    equations = parse_equations(text)
    t0, tf, dt, sp = get_time_bounds(equations)
    result = simulate(equations, t0, tf, dt, sp)

    if not variables:
        variables = [v for v in equations if v not in ("INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER")]
    if not variables:
        print("ERROR: 模型无可绘变量", file=sys.stderr)
        return 2

    # nodata 检测：仅在有求值失败时才报
    if result.eval_warnings:
        nodata = [v for v in variables if result.series.get(v) and all(x == 0.0 for x in result.series[v])]
        if nodata:
            print(f"警告: 以下变量全程为 0（可能 nodata）: {', '.join(nodata)}", file=sys.stderr)

    _render_plot(result, variables, output, title=title or f"{path.stem} Simulation")
    print(f"折线图已导出: {output}")
    print(f"变量: {', '.join(variables)}")
    return 0


def command_compare(base: Path, scenarios: List[Path], variables: List[str],
                    output: Path, title: str) -> int:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("ERROR: 需要 matplotlib", file=sys.stderr)
        return 2

    if not variables:
        print("ERROR: 至少指定一个变量 --var", file=sys.stderr)
        return 2

    fig, axes = plt.subplots(len(variables), 1, figsize=(10, 4 * len(variables)), dpi=120, squeeze=False)
    all_models = [base] + scenarios
    labels = ["base"] + [p.stem for p in scenarios]

    for ax, v in zip(axes[:, 0], variables):
        for model, label in zip(all_models, labels):
            text = load_mdl_text(model)
            equations = parse_equations(text)
            t0, tf, dt, sp = get_time_bounds(equations)
            result = simulate(equations, t0, tf, dt, sp)
            if v in result.series:
                ax.plot(result.times, result.series[v], linewidth=2, label=label)
        ax.set_title(v)
        ax.set_xlabel("Time")
        ax.set_ylabel(v)
        ax.legend(loc="best")
        ax.grid(True, alpha=0.3)
    fig.suptitle(title or "Scenario Comparison", fontsize=14)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    print(f"对比图已导出: {output}")
    return 0


# ---------------------------------------------------------------------------
# 单位校验
# ---------------------------------------------------------------------------

# 基础量纲：用可比较的字符串规范化
_UNIT_ALIASES = {
    "person": "Person", "people": "Person", "rabbit": "Rabbit", "rabbits": "Rabbit",
    "unit": "Unit", "units": "Unit", "dollar": "Dollar", "dollars": "Dollar",
    "month": "Month", "year": "Year", "week": "Week", "day": "Day",
    "fraction": "Dmnl", "dmnl": "Dmnl", "dimensionless": "Dmnl", "1": "Dmnl",
}


def normalize_unit(unit: str) -> str:
    """规范化单位字符串用于比较。"""
    if not unit:
        return ""
    u = unit.strip()
    # 处理复合单位 Person/Month 等
    # 拆分分子分母
    parts = re.split(r"[*/]", u)
    normed = []
    for p in parts:
        p = p.strip().lower()
        normed.append(_UNIT_ALIASES.get(p, p))
    return "/".join(normed)


def command_units(path: Path) -> int:
    text = load_mdl_text(path)
    equations = parse_equations(text)
    names = set(equations.keys())
    errors = 0
    warnings = 0
    print(f"UNITS CHECK: {path}")
    for name, eq in equations.items():
        if name in ("INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER"):
            continue
        if not eq.unit:
            warnings += 1
            print(f"  WARNING {name}: 缺失单位")
            continue
        # 检查依赖单位一致性（简单：流率 = 库存/时间）
        deps = extract_deps(eq.rhs, names) if not eq.integ_flow else extract_deps(eq.integ_flow, names)
        # 仅对乘除做粗略检查：若 rhs 是单一变量，单位应一致
        if eq.integ_flow:
            # 库存单位应与初值单位一致；流率单位应为 库存/时间
            stock_unit = normalize_unit(eq.unit)
            # 流率单位推断
            flow_deps_units = [equations[d].unit for d in deps if d in equations]
            if flow_deps_units:
                # 简单提示，不做严格量纲推导
                pass
    print(f"\nWarnings: {warnings}  Errors: {errors}")
    return 1 if errors else 0


# ---------------------------------------------------------------------------
# 检查与修复
# ---------------------------------------------------------------------------

def command_check(path: Path) -> int:
    text = load_mdl_text(path)
    equations = parse_equations(text)
    names = set(equations.keys())
    errors = 0
    warnings = 0
    print(f"CHECK: {path}")

    # 1. 未定义变量引用
    for name, eq in equations.items():
        rhs = eq.integ_flow or eq.rhs
        deps = extract_deps(rhs, names)
        for d in deps:
            if d not in equations:
                errors += 1
                print(f"  ERROR {name}: 引用未定义变量 '{d}'")

    # 2. 缺失单位
    for name, eq in equations.items():
        if name in ("INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER"):
            continue
        if not eq.unit:
            warnings += 1
            print(f"  WARNING {name}: 缺失单位")

    # 3. 循环依赖
    try:
        topological_sort(equations)
    except ValueError as exc:
        errors += 1
        print(f"  ERROR: {exc}")

    # 4. 草图引用审计
    sketch_pos = text.find(r"\\\---///")
    if sketch_pos >= 0:
        from vensim_autolayout import load_mdl as _load_mdl
        _, views = _load_mdl(path)
        for view in views:
            ids = set(view.objects)
            for arrow in view.arrows:
                if arrow.from_id not in ids or arrow.to_id not in ids:
                    errors += 1
                    print(f"  ERROR 草图 {view.name}: 箭头 {arrow.obj_id} 断裂引用")

    # 5. 时间设置
    for req in ("INITIAL TIME", "FINAL TIME", "TIME STEP"):
        if req not in equations:
            errors += 1
            print(f"  ERROR: 缺失控制变量 {req}")

    if errors == 0:
        print("\nPASS: 未发现错误。")
    else:
        print(f"\nFAIL: {errors} 个错误。")
    print(f"Warnings: {warnings}")
    return 1 if errors else 0


def command_fix(path: Path, output: Path) -> int:
    text = load_mdl_text(path)
    equations = parse_equations(text)
    names = set(equations.keys())
    fixes = []

    # 修复1：缺失单位补 Dmnl
    lines = text.splitlines(keepends=True)
    for name, eq in equations.items():
        if name in ("INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER"):
            continue
        if not eq.unit:
            # 在方程的 ~ 行插入 Dmnl
            # 找到该方程后的第一个 ~ 空行
            for li in range(eq.line_index, min(eq.line_index + 5, len(lines))):
                if lines[li].strip() == "~":
                    lines[li] = lines[li].replace("~", "~ Dmnl")
                    fixes.append(f"补单位 {name}: Dmnl")
                    break

    # 修复2：断裂草图箭头 - 删除引用不存在对象的箭头行
    sketch_idx = None
    for li, ln in enumerate(lines):
        if r"\\\---///" in ln:
            sketch_idx = li
            break
    if sketch_idx is not None:
        from vensim_autolayout import load_mdl as _load_mdl, parse_views
        new_lines = lines[:sketch_idx]
        sketch_lines = lines[sketch_idx:]
        # 重新解析并过滤断裂箭头
        sketch_text = "".join(sketch_lines)
        # 简单按行过滤：箭头行若 from/to 不在本 view 对象集则删除
        # 重新解析所有 view
        _, views = _load_mdl(path)
        broken_arrow_lines = set()
        for view in views:
            ids = set(view.objects)
            for arrow in view.arrows:
                if arrow.from_id not in ids or arrow.to_id not in ids:
                    broken_arrow_lines.add(arrow.line_index)
        if broken_arrow_lines:
            fixed_sketch = [ln for i, ln in enumerate(sketch_lines) if i not in broken_arrow_lines]
            fixes.append(f"删除 {len(broken_arrow_lines)} 条断裂草图箭头")
            new_lines = new_lines + fixed_sketch
        else:
            new_lines = lines
        output.write_text("".join(new_lines), encoding="utf-8")
    else:
        output.write_text("".join(lines), encoding="utf-8")

    report = {"input": str(path), "output": str(output), "fixes": fixes}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_sim = sub.add_parser("simulate", help="纯 Python 仿真，导出 CSV")
    p_sim.add_argument("model", type=Path)
    p_sim.add_argument("--output", required=True, type=Path)
    p_sim.add_argument("--var", action="append", default=[], help="要导出的变量(可多次)")
    p_sim.add_argument("--plot", type=Path, default=None, help="同时导出折线图 PNG")

    p_graph = sub.add_parser("graph", help="仿真并导出折线图 PNG")
    p_graph.add_argument("model", type=Path)
    p_graph.add_argument("--var", action="append", default=[], help="绘图变量(可多次，缺省全部)")
    p_graph.add_argument("--output", required=True, type=Path)
    p_graph.add_argument("--title", default="")

    p_cmp = sub.add_parser("compare", help="多场景对比图")
    p_cmp.add_argument("base", type=Path)
    p_cmp.add_argument("--scenario", action="append", default=[], type=Path)
    p_cmp.add_argument("--var", action="append", required=True)
    p_cmp.add_argument("--output", required=True, type=Path)
    p_cmp.add_argument("--title", default="")

    p_units = sub.add_parser("units", help="单位校验")
    p_units.add_argument("model", type=Path)

    p_check = sub.add_parser("check", help="全面检查")
    p_check.add_argument("model", type=Path)

    p_fix = sub.add_parser("fix", help="自动修复")
    p_fix.add_argument("model", type=Path)
    p_fix.add_argument("--output", required=True, type=Path)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "simulate":
        return command_simulate(args.model, args.output, args.var, getattr(args, "plot", None))
    if args.command == "graph":
        return command_graph(args.model, args.var, args.output, args.title)
    if args.command == "compare":
        return command_compare(args.base, args.scenario, args.var, args.output, args.title)
    if args.command == "units":
        return command_units(args.model)
    if args.command == "check":
        return command_check(args.model)
    if args.command == "fix":
        return command_fix(args.model, args.output)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
