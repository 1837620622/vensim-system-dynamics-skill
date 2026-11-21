#!/usr/bin/env python3
"""Vensim .mdl 的纯 Python 仿真引擎，以及方程检查和修复命令。

子命令：
  - simulate：Euler 积分，导出 CSV（不写 --var 时导出全部变量）
  - graph / compare：单模型或多情景的折线图
  - units：列出缺失单位，完整量纲检查仍要在 Vensim 里做
  - check：未定义变量、缺失单位、断裂引用、初值循环
  - fix：按明确给出的单位映射或修复项输出新文件

支持的函数见 references/SIMULATION.md；遇到不支持的函数直接报错，不猜测语义。
方程经受限的表达式树求值，不执行任意 Python 代码。
"""

from __future__ import annotations

import argparse
import ast
import bisect
import dataclasses
import functools
import hashlib
import json
import math
import re
import sys
from collections import OrderedDict, deque
from pathlib import Path

from mdl_document import MdlDocument, atomic_write, preflight_outputs, separate_output

# ---------------------------------------------------------------------------
# 方程区解析
# ---------------------------------------------------------------------------

MAX_SIM_STEPS = 1_000_000
MAX_OUTPUT_POINTS = 1_000_000
MAX_EXPR_CHARS = 20_000
MAX_AST_NODES = 2_000
MAX_AST_DEPTH = 80
MAX_POWER_EXPONENT = 1_000
HELPER_TOKENS = {
    "_wl": "@0@",
    "_sd_abs": "@1@",
    "_sd_min": "@2@",
    "_sd_max": "@3@",
    "_sd_sqrt": "@4@",
    "_sd_exp": "@5@",
    "_sd_log": "@6@",
    "_sd_sin": "@7@",
    "_sd_cos": "@8@",
    "_sd_tan": "@9@",
    "_sd_int": "@10@",
    "_sd_float": "@11@",
    "_pulse": "@12@",
    "_ramp": "@13@",
    "_step": "@14@",
    "_delay_fixed": "@15@",
    "_sd_modulo": "@16@",
    "_sample_if_true": "@17@",
    "_sd_initial": "@18@",
    "_sd_initializing": "@19@",
    "_sd_quantum": "@20@",
    "_sd_power": "@21@",
    "_sd_logb": "@22@",
    "_pulse_train": "@23@",
    "_lookup_extrapolate": "@24@",
    "_lookup_forward": "@25@",
    "_lookup_backward": "@26@",
    "_sd_asin": "@27@",
    "_sd_acos": "@28@",
    "_sd_atan": "@29@",
    "_sd_sinh": "@30@",
    "_sd_cosh": "@31@",
    "_sd_tanh": "@32@",
}

# 内置引擎明确不支持的 Vensim 函数：遇到时直接报中文错误，不猜语义。
UNSUPPORTED_FUNCTIONS = (
    "NPV",
    "NPVE",
    "RANDOM UNIFORM",
    "RANDOM NORMAL",
    "RANDOM POISSON",
    "RANDOM EXPONENTIAL",
    "RANDOM BINOMIAL",
    "RANDOM GAMMA",
    "RANDOM BETA",
    "RANDOM LOGNORMAL",
    "RANDOM TRIANGULAR",
    "RANDOM WEIBULL",
    "RANDOM PINK NOISE",
    "RANDOM 0 1",
    "RANDOM LOOKUP",
    "GET XLS DATA",
    "GET XLS CONSTANTS",
    "GET XLS LOOKUPS",
    "GET DIRECT DATA",
    "GET DIRECT CONSTANTS",
    "GET DIRECT LOOKUPS",
    "GET DATA AT TIME",
    "GET DATA BETWEEN TIMES",
    "GET TIME VALUE",
    "TIME BASE",
    "VECTOR SELECT",
    "VECTOR ELM MAP",
    "VECTOR SORT ORDER",
    "VECTOR RANK",
    "VMIN",
    "VMAX",
    "SUM",
    "PROD",
    "ELMCOUNT",
    "ALLOCATE BY PRIORITY",
    "ALLOCATE AVAILABLE",
    "QUEUE FIFO",
    "QUEUE ATTRIBUTE",
    "DELAY CONVEYOR",
    "DELAY PROFILE",
    "DELAY BATCH",
    "DELAY MATERIAL",
    "SHIFT IF TRUE",
    "LOOKUP INVERT",
    "LOOKUP AREA",
    "TABBED ARRAY",
    "GAME",
    "INVERT MATRIX",
)
_UNSUPPORTED_PATTERN = re.compile(
    r"(?<![\w$])("
    + "|".join(
        re.escape(name).replace(r"\ ", r"\s+")
        for name in sorted(UNSUPPORTED_FUNCTIONS, key=len, reverse=True)
    )
    + r")\s*\(",
    re.I,
)

# 方程块以 "变量名 = ..." 开头，后续 ~ 单位 ~ 注释 | 结束。
# 变量名可以是中文、含空格或 $，不能只按英文标识符匹配
_LOOKUP_DEF = re.compile(r"\[(.*?)\]\s*$")


@dataclasses.dataclass
class Equation:
    name: str
    rhs: str  # 等号右侧表达式
    unit: str  # 单位
    comment: str  # 注释
    integ_init: float | None = None  # INTEG 初值
    integ_init_expr: str | None = None  # INTEG 初值表达式
    integ_flow: str | None = None  # INTEG 内部流表达式
    is_lookup: bool = False
    lookup_pairs: list[tuple[float, float]] = dataclasses.field(default_factory=list)
    line_index: int = 0
    smooth_init_ref: str | None = None  # SMOOTH 隐式库存初值引用的输入变量名
    delay_fixed_args: list[str] | None = None  # 固定延迟有自己的状态队列
    supplementary: bool = False  # 注释后的 ~ :SUP 字段：只作输出、不参与其他方程


@functools.lru_cache(maxsize=65536)
def canonical_name(name: str) -> str:
    """原生名称不区分大小写，连续空格与下划线等价；返回用于比对的键。"""
    return re.sub(r"[\s_]+", " ", name.strip('"')).strip().casefold()


def _matching_paren(text: str, open_index: int) -> int:
    """返回 open_index 对应右括号位置，支持括号/方括号与字符串跳过。"""
    depth = 0
    bracket_depth = 0
    quote = ""
    escape = False
    for i in range(open_index, len(text)):
        ch = text[i]
        if quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch == "[":
            bracket_depth += 1
        elif ch == "]" and bracket_depth:
            bracket_depth -= 1
        elif bracket_depth == 0:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
    raise ValueError(f"括号未闭合: {text[open_index : open_index + 80]}")


def _split_top_level_args(text: str) -> list[str]:
    """按顶层逗号切分函数参数，忽略括号、方括号与字符串内部逗号。"""
    args: list[str] = []
    start = 0
    paren_depth = 0
    bracket_depth = 0
    quote = ""
    escape = False
    for i, ch in enumerate(text):
        if quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch == "(":
            paren_depth += 1
        elif ch == ")" and paren_depth:
            paren_depth -= 1
        elif ch == "[":
            bracket_depth += 1
        elif ch == "]" and bracket_depth:
            bracket_depth -= 1
        elif ch == "," and paren_depth == 0 and bracket_depth == 0:
            args.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        args.append(tail)
    return args


def _replace_calls(expr: str, function_name: str, handler) -> str:
    """替换函数调用，参数解析支持嵌套括号；函数名前一个字符必须不是变量字符。"""
    out: list[str] = []
    lower = expr.lower()
    needle = function_name.lower()
    i = 0
    while i < len(expr):
        j = lower.find(needle, i)
        if j < 0:
            out.append(expr[i:])
            break
        before = expr[j - 1] if j > 0 else ""
        if before and (before.isalnum() or before in "_$"):
            out.append(expr[i : j + 1])
            i = j + 1
            continue
        k = j + len(function_name)
        while k < len(expr) and expr[k].isspace():
            k += 1
        if k >= len(expr) or expr[k] != "(":
            out.append(expr[i : j + 1])
            i = j + 1
            continue
        end = _matching_paren(expr, k)
        args = _split_top_level_args(expr[k + 1 : end])
        out.append(expr[i:j])
        out.append(handler(args, expr[j : end + 1]))
        i = end + 1
    return "".join(out)


def init_dependency_text(expression: str) -> str:
    """初始化阶段 ACTIVE INITIAL 只取初值参数，其余依赖照常。"""

    def handler(args: list[str], raw: str) -> str:
        if len(args) != 2:
            raise ValueError(f"ACTIVE INITIAL 需要 2 个参数: {raw}")
        return f"({init_dependency_text(args[1])})"

    return _replace_calls(expression, "ACTIVE INITIAL", handler)


def _function_args(text: str, function_name: str) -> list[str] | None:
    """解析完整函数调用的参数；不是该函数调用时返回 None。"""
    s = text.strip()
    pattern = re.compile(rf"^{re.escape(function_name)}\s*\(", re.I)
    m = pattern.match(s)
    if not m:
        return None
    open_index = m.end() - 1
    close_index = _matching_paren(s, open_index)
    if s[close_index + 1 :].strip():
        return None
    return _split_top_level_args(s[open_index + 1 : close_index])


_PLACEHOLDER = re.compile(r"A\s+FUNCTION\s+OF\s*\(", re.I)


def parse_equations(mdl_text: str, expand: bool = True) -> OrderedDict[str, Equation]:
    """按原生 ~ 和 | 分隔符解析方程，允许内联字段与跨行表达式。"""
    body = mdl_text.split(r"\\\---///", 1)[0].lstrip("\ufeff")
    body = re.sub(r"\\\r?\n[ \t]*", "", body)
    equations = OrderedDict()
    defined = set()
    offset = 0
    for block in body.split("|"):
        fields = block.split("~", 2)
        match = re.search(r"(?m)^[ \t]*([^=~|\n]+?)\s*=\s*([\s\S]*)", fields[0])
        lookup_match = None
        if match is None:
            lookup_match = re.search(
                r'(?m)^[ \t]*("[^"\n]+"|[^()=~|\n]+?)\s*(\([\s\S]*\))\s*$', fields[0]
            )
            if lookup_match is None:
                offset += block.count("\n")
                continue
            match = lookup_match
        name = match.group(1).strip().strip('"')
        key = canonical_name(name)
        controls = {
            canonical_name(item): item
            for item in ("INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER")
        }
        name = controls.get(key, name)
        rhs = " ".join(match.group(2).split())
        # A FUNCTION OF(...) 是 Vensim 给未写方程的变量留的占位，同名有真实方程时以真实方程为准
        placeholder = bool(_PLACEHOLDER.match(rhs))
        if key in defined:
            previous = next(n for n in equations if canonical_name(n) == key)
            if placeholder:
                offset += block.count("\n")
                continue
            if not _PLACEHOLDER.match(equations[previous].rhs):
                raise ValueError(f"变量重复定义: {name}")
            del equations[previous]
        defined.add(key)
        # 注释之后可以再跟 ~ :SUP（或 :SUPPLEMENTARY）字段，把变量标为输出指标
        extra = fields[2].split("~") if len(fields) > 2 else [""]
        eq = Equation(
            name=name,
            rhs=rhs,
            unit=fields[1].strip() if len(fields) > 1 else "",
            comment=extra[0].strip(),
            line_index=offset + block[: match.start()].count("\n"),
            supplementary=any(
                re.fullmatch(r":SUP(?:PLEMENTARY)?", part.strip(), re.I) for part in extra[1:]
            ),
        )
        integ_args = _function_args(rhs, "INTEG")
        if integ_args is not None:
            if len(integ_args) != 2:
                raise ValueError(f"{name}: INTEG 需要流率和初值")
            eq.integ_flow, eq.integ_init_expr = integ_args
            try:
                eq.integ_init = float(eq.integ_init_expr)
            except ValueError:
                pass
        fixed_args = _function_args(rhs, "DELAY FIXED")
        if fixed_args is not None:
            if len(fixed_args) != 3:
                raise ValueError(f"{name}: DELAY FIXED 需要输入、延迟时间和初值")
            eq.delay_fixed_args = fixed_args
        elif re.search(r"\bDELAY\s+FIXED\s*\(", rhs, re.I):
            raise ValueError(f"{name}: DELAY FIXED 必须独立位于等号右侧，不能嵌入其他表达式")
        if lookup_match is not None or (rhs.startswith("[") and rhs.endswith("]")):
            eq.is_lookup = True
            eq.lookup_pairs = _parse_lookup_pairs(rhs)
        equations[name] = eq
        offset += block.count("\n")
    if expand:
        _expand_smooth_delay(equations)
    return equations


# 平滑与延迟族：名称 -> (阶数, 是否平滑, 参数个数)。阶数为 None 时取第 4 个参数。
_STATE_FUNCTIONS = {
    "SMOOTH N": (None, True, 4),
    "DELAY N": (None, False, 4),
    "SMOOTH3I": (3, True, 3),
    "SMOOTHI": (1, True, 3),
    "SMOOTH3": (3, True, 2),
    "SMOOTH": (1, True, 2),
    "DELAY3I": (3, False, 3),
    "DELAY1I": (1, False, 3),
    "DELAY3": (3, False, 2),
    "DELAY1": (1, False, 2),
    "TREND": (1, True, 3),
    "FORECAST": (1, True, 3),
}
_STATE_CALL = re.compile(
    r"(?<![\w$])("
    + "|".join(
        re.escape(name).replace(r"\ ", r"\s+")
        for name in sorted(_STATE_FUNCTIONS, key=len, reverse=True)
    )
    + r")\s*\(",
    re.I,
)


def _find_state_call(rhs: str):
    """返回第一个平滑/延迟族调用 (函数名, 起点, 终点, 参数)。"""
    for match in _STATE_CALL.finditer(rhs):
        function = " ".join(match.group(1).upper().split())
        open_index = match.end() - 1
        close_index = _matching_paren(rhs, open_index)
        args = _split_top_level_args(rhs[open_index + 1 : close_index])
        return function, match.start(), close_index + 1, args
    return None


def _expand_smooth_delay(equations):
    """平滑、延迟、TREND 和 FORECAST 展开成隐式库存；调用可以嵌在表达式中。

    物料延迟保存管道存量，默认初值等于初始输入；TREND 和 FORECAST 按 Vensim
    手册的定义展开：平均值是输入的一阶平滑，趋势为 ZIDZ(输入-平均, 平均时间*|平均|)。
    """
    used = {canonical_name(name) for name in equations}
    queue = list(equations.items())
    additions = []
    while queue:
        name, eq = queue.pop(0)
        if eq.is_lookup:
            continue
        calls = 0
        while True:
            found = _find_state_call(eq.rhs)
            if found is None:
                break
            function, begin, finish, args = found
            order, smooth, expected = _STATE_FUNCTIONS[function]
            if len(args) != expected:
                raise ValueError(f"{name}: {function} 需要 {expected} 个参数")
            if order is None:
                try:
                    order = int(float(args[3]))
                except ValueError as exc:
                    raise ValueError(
                        f"{name}: {function} 的阶数必须是正整数常数，当前为 {args[3]}"
                    ) from exc
                if order < 1 or order != float(args[3]) or order > 100:
                    raise ValueError(f"{name}: {function} 的阶数必须是 1 到 100 的整数")
            value, delay = args[0], args[1]
            if function in ("TREND", "FORECAST"):
                trend0 = args[2] if function == "TREND" else "0"
                initial = f"({value}) / (1 + ({trend0}) * ({delay}))"
            else:
                initial = args[2] if expected >= 3 else value
            duration = f"(({delay}) / {order})"
            previous = f"({value})"
            prefix = name if calls == 0 else f"{name}__{calls + 1}"
            calls += 1
            for stage in range(order):
                stock = f"{prefix}__stage{stage + 1}"
                if canonical_name(stock) in used:
                    raise ValueError(f"隐式状态名与模型变量冲突: {stock}")
                used.add(canonical_name(stock))
                outflow = stock if smooth else f"({stock} / {duration})"
                flow = (
                    f"({previous} - {outflow}) / {duration}"
                    if smooth
                    else f"{previous} - {outflow}"
                )
                init = f"({initial})" if smooth else f"({initial}) * {duration}"
                state = Equation(
                    stock,
                    f"INTEG({flow}, {init})",
                    eq.unit,
                    f"{function} 隐式状态",
                    integ_init_expr=init,
                    integ_flow=flow,
                    line_index=eq.line_index,
                )
                additions.append((stock, state))
                queue.append((stock, state))
                previous = outflow
            if function == "TREND":
                previous = f"ZIDZ(({value}) - {previous}, ({delay}) * ABS({previous}))"
            elif function == "FORECAST":
                previous = (
                    f"(({value}) * (1 + ZIDZ(({value}) - {previous}, ({delay}) * ABS({previous}))"
                    f" * ({args[2]})))"
                )
            eq.rhs = eq.rhs[:begin] + previous + eq.rhs[finish:]
        if calls:
            integ_args = _function_args(eq.rhs, "INTEG")
            if integ_args is not None and len(integ_args) == 2:
                eq.integ_flow, eq.integ_init_expr = integ_args
            else:
                eq.integ_flow, eq.integ_init, eq.integ_init_expr = None, None, None
            fixed_args = _function_args(eq.rhs, "DELAY FIXED")
            if fixed_args is not None:
                eq.delay_fixed_args = fixed_args
    equations.update(additions)


def _parse_lookup_pairs(text: str) -> list[tuple[float, float]]:
    """读取真实数据点；方括号中的显示范围和参考点不参与插值。"""
    body = text.strip()
    if body.startswith("(") and _matching_paren(body, 0) == len(body) - 1:
        body = body[1:-1].strip()
    # 旧格式把显示范围与数据点放在同一个方括号中，继续支持其回归样例。
    if body.startswith("[") and body.endswith("]") and "]" not in body[:-1]:
        body = body[1:-1].strip()
        body = re.sub(r"^\s*\([^()]+\)\s*-\s*\([^()]+\)\s*,?", "", body)
    else:
        body = re.sub(r"\[[^\]]*\]", "", body).strip().lstrip(",").strip()
    tokens = _split_top_level_args(body)
    if not tokens:
        raise ValueError("LOOKUP 表无有效坐标点")
    if all(token.startswith("(") and token.endswith(")") for token in tokens):
        pairs = []
        for token in tokens:
            values = _split_top_level_args(token[1:-1])
            if len(values) != 2:
                raise ValueError("LOOKUP 坐标必须是 (x,y)")
            pairs.append(tuple(float(value) for value in values))
    elif all("(" not in token and ")" not in token for token in tokens) and len(tokens) % 2 == 0:
        numbers = [float(token) for token in tokens]
        middle = len(numbers) // 2
        pairs = list(zip(numbers[:middle], numbers[middle:], strict=False))
    else:
        raise ValueError("LOOKUP 数据点格式错误")
    if len(pairs) < 2 or any(not math.isfinite(value) for pair in pairs for value in pair):
        raise ValueError("LOOKUP 至少需要两个有限坐标点")
    if any(right[0] <= left[0] for left, right in zip(pairs, pairs[1:], strict=False)):
        raise ValueError("LOOKUP 的 x 坐标必须严格递增，不能重复或乱序")
    return pairs


# ---------------------------------------------------------------------------
# 依赖分析
# ---------------------------------------------------------------------------

# 排除函数名与关键字
_KEYWORDS = {
    "INTEG",
    "SMOOTH",
    "SMOOTH3",
    "SAMPLE IF TRUE",
    "ACTIVE INITIAL",
    "TREND",
    "FORECAST",
    "QUANTUM",
    "POWER",
    "PULSE TRAIN",
    "DELAY1",
    "DELAY3",
    "DELAY",
    "DELAY FIXED",
    "IF",
    "THEN",
    "ELSE",
    "WITH",
    "LOOKUP",
    "ABS",
    "SQRT",
    "EXP",
    "LN",
    "MIN",
    "MAX",
    "MODULO",
    "PULSE",
    "RAMP",
    "STEP",
    "TIME",
    "TRUE",
    "FALSE",
    "INITIAL",
    "FINAL",
    "SAVEPER",
}


@functools.lru_cache(maxsize=65536)
def _name_pattern(name: str) -> str:
    """返回变量名匹配模式，兼容中文、空格和美元符号变量名。

    Python 的 ``\b`` 对中文和带空格变量名不稳定，因此使用显式的
    “变量字符”负向边界，避免短变量名误匹配到长变量名内部。
    """
    parts = re.split(r"([\s_]+)", name)
    pattern = "".join(
        r"[\s_]+" if re.fullmatch(r"[\s_]+", part) else re.escape(part) for part in parts
    )
    return rf'(?<![\w$])(?:"{pattern}"|{pattern})(?![\w$])'


def extract_deps(rhs: str, known_names: set) -> list[str]:
    """从表达式提取依赖的变量名（已知名集合内，支持带空格变量名）。"""
    deps: list[str] = []
    # 按长度降序匹配，避免短名前缀误匹配（如 "Birth" 匹配 "Birth Fraction"）
    sorted_names = sorted(known_names, key=lambda name: (-len(name), name))
    # 先移除函数名
    function_words = (
        r"INTEG|SMOOTH3?I?|SMOOTH N|DELAY[13]?I?|DELAY FIXED|DELAY N|IF THEN ELSE|WITH LOOKUP|"
        r"SAMPLE IF TRUE|ACTIVE INITIAL|INITIAL|TREND|FORECAST|QUANTUM|POWER|LOG|"
        r"PULSE TRAIN|LOOKUP EXTRAPOLATE|LOOKUP FORWARD|LOOKUP BACKWARD|ZIDZ|XIDZ|INTEGER|"
        r"ABS|SQRT|EXP|LN|MIN|MAX|MODULO|PULSE|RAMP|STEP"
    )
    cleaned = re.sub(rf"\b({function_words})\b(?=\s*\()", " ", rhs, flags=re.I)
    # 用占位符逐个替换已知名，避免重叠匹配
    remaining = cleaned
    for name in sorted_names:
        pattern = _name_pattern(name)
        if re.search(pattern, remaining, re.I):
            deps.append(name)
            remaining = re.sub(pattern, " ", remaining, flags=re.I)
    return deps


def _is_number(text: str) -> bool:
    try:
        return math.isfinite(float(text))
    except ValueError:
        return False


def use_flags(equations: OrderedDict[str, Equation]) -> tuple[list[str], list[str]]:
    """按 Vensim Check Model 的使用检查找出两类变量（USE FLAG）。

    一是没有被任何其他方程引用的变量（不在任何反馈回路上），二是标了
    Supplementary 却被引用的变量。存量的 INTEG 写在等号右侧，流量和初值参数
    都算被使用；时间控制变量由仿真器使用，不在此列。
    """
    names = set(equations)
    used = set()
    for name, eq in equations.items():
        used.update(dep for dep in extract_deps(eq.rhs, names) if dep != name)
    controls = {"INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER"}
    not_used = [
        name
        for name, eq in equations.items()
        if name not in used and name not in controls and not eq.supplementary
    ]
    misused = [name for name, eq in equations.items() if eq.supplementary and name in used]
    return not_used, misused


def topological_sort(equations: OrderedDict[str, Equation]) -> list[str]:
    """拓扑排序辅助变量；库存(INTEG)用上一时间步值，其流率依赖不参与环检测。"""
    names = set(equations.keys())
    stocks = [
        n
        for n, e in equations.items()
        if e.integ_flow is not None or e.delay_fixed_args is not None
    ]
    auxs = [
        n
        for n, e in equations.items()
        if e.integ_flow is None and e.delay_fixed_args is None and not e.is_lookup
    ]

    order: list[str] = []
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
                if (
                    d in equations
                    and equations[d].integ_flow is None
                    and equations[d].delay_fixed_args is None
                ):
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


def stock_initialization_order(equations: OrderedDict[str, Equation]) -> list[str]:
    """按 INTEG 初值表达式中的库存依赖排序，避免使用临时 0 初值。"""
    all_names = set(equations.keys())
    stocks = {
        name
        for name, eq in equations.items()
        if eq.integ_flow is not None and eq.integ_init is None and eq.integ_init_expr
    }
    order: list[str] = []
    visited: set = set()
    temp: set = set()

    def visit(node: str) -> None:
        if node in visited:
            return
        if node in temp:
            raise ValueError(f"检测到库存初值循环依赖: {node}")
        temp.add(node)
        eq = equations[node]
        for dep in extract_deps(eq.integ_init_expr or "", all_names):
            if dep in stocks:
                visit(dep)
        temp.discard(node)
        visited.add(node)
        order.append(node)

    for stock in stocks:
        visit(stock)
    return order


# ---------------------------------------------------------------------------
# 表达式求值
# ---------------------------------------------------------------------------


class LookupTable:
    """线性插值查表。支持传入 (x,y) 对列表或 Vensim 原始表字符串。"""

    def __init__(self, table):
        if isinstance(table, str):
            pairs = _parse_lookup_pairs(table)
        else:
            pairs = list(table)
        if len(pairs) < 2 or any(
            len(pair) != 2 or any(not math.isfinite(value) for value in pair) for pair in pairs
        ):
            raise ValueError("LOOKUP 至少需要两个有限坐标点")
        if any(right[0] <= left[0] for left, right in zip(pairs, pairs[1:], strict=False)):
            raise ValueError("LOOKUP 的 x 坐标必须严格递增")
        self.xs = [p[0] for p in pairs]
        self.ys = [p[1] for p in pairs]

    def __call__(self, x: float) -> float:
        if x <= self.xs[0]:
            return self.ys[0]
        if x >= self.xs[-1]:
            return self.ys[-1]
        i = max(bisect.bisect_right(self.xs, x) - 1, 0)
        x0, y0 = self.xs[i], self.ys[i]
        x1, y1 = self.xs[i + 1], self.ys[i + 1]
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

    def extrapolate(self, x: float) -> float:
        """LOOKUP EXTRAPOLATE：表外沿首尾两段直线外推。"""
        if x < self.xs[0]:
            x0, x1, y0, y1 = self.xs[0], self.xs[1], self.ys[0], self.ys[1]
        elif x > self.xs[-1]:
            x0, x1, y0, y1 = self.xs[-2], self.xs[-1], self.ys[-2], self.ys[-1]
        else:
            return self(x)
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

    def forward(self, x: float) -> float:
        """LOOKUP FORWARD：取不小于 x 的下一个数据点的值。"""
        i = bisect.bisect_left(self.xs, x)
        return self.ys[min(i, len(self.ys) - 1)]

    def backward(self, x: float) -> float:
        """LOOKUP BACKWARD：取不大于 x 的上一个数据点的值。"""
        i = bisect.bisect_right(self.xs, x) - 1
        return self.ys[max(i, 0)]


class StatefulStore:
    """SAMPLE IF TRUE 与 INITIAL 的状态；仿真器在每个时间步结束时提交。"""

    def __init__(self):
        self.values: dict[str, float] = {}
        self.pending: dict[str, float] = {}
        self.initializing = True

    def commit(self) -> None:
        self.values.update(self.pending)
        self.pending.clear()


_PROTECTED_TOKEN = re.compile(r"@(?:var\d+|\d+)@")


@functools.lru_cache(maxsize=64)
def _known_names(names: tuple[str, ...]) -> frozenset[str]:
    return frozenset(canonical_name(name) for name in names)


@functools.lru_cache(maxsize=64)
def _names_regex(names: tuple[str, ...]):
    """同一组变量名只编译一次替换正则；大模型上这是转换的主要开销。"""
    pattern = "|".join(_name_pattern(name) for name in sorted(names, key=len, reverse=True))
    if not pattern:
        return None
    aliases = {canonical_name(name): f"@var{index}@" for index, name in enumerate(names)}
    return re.compile(f"{_PROTECTED_TOKEN.pattern}|(?:{pattern})", re.I), aliases


def _to_python_expr(rhs: str, name_map: dict[str, str], _nested=False) -> str:
    """把 Vensim 表达式转成 Python 可求值字符串，变量名映射为合法标识符。"""
    s = rhs

    def _helper(name: str) -> str:
        return HELPER_TOKENS[name]

    def _replace_with_lookup_calls(expr: str) -> str:
        def handler(args, raw):
            if len(args) != 2:
                raise ValueError(f"WITH LOOKUP 参数数量错误: {raw}")
            _parse_lookup_pairs(args[1])
            return f"{_helper('_wl')}({_expr_arg(args[0])}, {args[1]!r})"

        return _replace_calls(expr, "WITH LOOKUP", handler)

    def _expr_arg(arg: str) -> str:
        return _to_python_expr(arg, name_map, _nested=True)

    def _if_handler(args: list[str], raw: str) -> str:
        if len(args) != 3:
            raise ValueError(f"IF THEN ELSE 参数数量错误: {raw}")
        return f"(({_expr_arg(args[1])}) if ({_expr_arg(args[0])}) else ({_expr_arg(args[2])}))"

    def _modulo_handler(args: list[str], raw: str) -> str:
        if len(args) != 2:
            raise ValueError(f"MODULO 参数数量错误: {raw}")
        return f"{_helper('_sd_modulo')}({_expr_arg(args[0])}, {_expr_arg(args[1])})"

    def _division_guard_handler(kind: str):
        def handler(args: list[str], raw: str) -> str:
            if len(args) != (2 if kind == "ZIDZ" else 3):
                raise ValueError(f"{kind} 参数数量错误: {raw}")
            num = _expr_arg(args[0])
            den = _expr_arg(args[1])
            fallback = "0.0" if kind == "ZIDZ" else _expr_arg(args[2])
            return f"(({fallback}) if {_helper('_sd_abs')}({den}) < 1e-6 else ({num})/({den}))"

        return handler

    def _time_func_handler(name: str):
        def handler(args: list[str], raw: str) -> str:
            if len(args) != 2:
                raise ValueError(f"{name} 参数数量错误: {raw}")
            return f"{_helper('_' + name.lower())}({_expr_arg(args[0])}, {_expr_arg(args[1])})"

        return handler

    def _ramp_handler(args: list[str], raw: str) -> str:
        if len(args) not in (2, 3):
            raise ValueError(f"RAMP 参数数量错误: {raw}")
        converted = [_expr_arg(arg) for arg in args]
        return f"{_helper('_ramp')}({', '.join(converted)})"

    def _delay_fixed_handler(args: list[str], raw: str) -> str:
        if len(args) != 3:
            raise ValueError(f"DELAY FIXED 参数数量错误: {raw}")
        key = hashlib.blake2s(raw.encode("utf-8"), digest_size=6).hexdigest()
        return f"{_helper('_delay_fixed')}('{key}', {_expr_arg(args[0])}, {_expr_arg(args[1])}, {_expr_arg(args[2])})"

    names_key = tuple(name_map)
    if not _nested:
        known = _known_names(names_key)
        for match in _UNSUPPORTED_PATTERN.finditer(s):
            function = " ".join(match.group(1).upper().split())
            if canonical_name(function) in known:
                continue  # 同名的模型变量（如查表函数），不是 Vensim 内置函数
            raise ValueError(
                f"内置引擎不支持 Vensim 函数 {function}，"
                "请用 `crosscheck`（PySD）或在 Vensim 中运行该模型"
            )

    def _call_handler(helper: str, counts: tuple[int, ...], label: str):
        def handler(args: list[str], raw: str) -> str:
            if len(args) not in counts:
                raise ValueError(f"{label} 参数数量错误: {raw}")
            return f"{_helper(helper)}({', '.join(_expr_arg(arg) for arg in args)})"

        return handler

    def _stateful_handler(helper: str, count: int, label: str):
        def handler(args: list[str], raw: str) -> str:
            if len(args) != count:
                raise ValueError(f"{label} 需要 {count} 个参数: {raw}")
            key = hashlib.blake2s(raw.encode("utf-8"), digest_size=6).hexdigest()
            converted = ", ".join(_expr_arg(arg) for arg in args)
            return f"{_helper(helper)}('{key}', {converted})"

        return handler

    def _active_initial_handler(args: list[str], raw: str) -> str:
        if len(args) != 2:
            raise ValueError(f"ACTIVE INITIAL 需要 2 个参数: {raw}")
        # 条件表达式只求值选中的一支，初始化时不会碰到尚未就绪的 active 依赖
        return (
            f"(({_expr_arg(args[1])}) if {_helper('_sd_initializing')}() "
            f"else ({_expr_arg(args[0])}))"
        )

    s = _replace_calls(
        s, "SAMPLE IF TRUE", _stateful_handler("_sample_if_true", 3, "SAMPLE IF TRUE")
    )
    s = _replace_calls(s, "ACTIVE INITIAL", _active_initial_handler)
    s = _replace_calls(s, "INITIAL", _stateful_handler("_sd_initial", 1, "INITIAL"))
    s = _replace_calls(s, "IF THEN ELSE", _if_handler)
    s = _replace_with_lookup_calls(s)
    s = _replace_calls(s, "DELAY FIXED", _delay_fixed_handler)
    s = _replace_calls(s, "MODULO", _modulo_handler)
    s = _replace_calls(s, "XIDZ", _division_guard_handler("XIDZ"))
    s = _replace_calls(s, "ZIDZ", _division_guard_handler("ZIDZ"))
    s = _replace_calls(s, "PULSE TRAIN", _call_handler("_pulse_train", (4,), "PULSE TRAIN"))
    s = _replace_calls(s, "PULSE", _time_func_handler("pulse"))
    s = _replace_calls(s, "QUANTUM", _call_handler("_sd_quantum", (2,), "QUANTUM"))
    s = _replace_calls(s, "POWER", _call_handler("_sd_power", (2,), "POWER"))
    s = _replace_calls(s, "LOG", _call_handler("_sd_logb", (2,), "LOG"))
    for vensim_name, helper in (
        ("LOOKUP EXTRAPOLATE", "_lookup_extrapolate"),
        ("LOOKUP FORWARD", "_lookup_forward"),
        ("LOOKUP BACKWARD", "_lookup_backward"),
    ):
        s = _replace_calls(s, vensim_name, _call_handler(helper, (2,), vensim_name))
    for vensim_name, helper in (
        ("ARCSIN", "_sd_asin"),
        ("ARCCOS", "_sd_acos"),
        ("ARCTAN", "_sd_atan"),
        ("SINH", "_sd_sinh"),
        ("COSH", "_sd_cosh"),
        ("TANH", "_sd_tanh"),
    ):
        s = _replace_calls(s, vensim_name, _call_handler(helper, (1,), vensim_name))
    s = _replace_calls(s, "RAMP", _ramp_handler)
    s = _replace_calls(s, "STEP", _time_func_handler("step"))
    # 数学函数（Vensim 函数名与左括号之间允许空白，如 MAX ( 0, ... )）
    s = re.sub(r"\bABS\s*\(", f"{_helper('_sd_abs')}(", s, flags=re.I)
    s = re.sub(r"\bSQRT\s*\(", f"{_helper('_sd_sqrt')}(", s, flags=re.I)
    s = re.sub(r"\bEXP\s*\(", f"{_helper('_sd_exp')}(", s, flags=re.I)
    s = re.sub(r"\bLN\s*\(", f"{_helper('_sd_log')}(", s, flags=re.I)
    s = re.sub(r"\bMIN\s*\(", f"{_helper('_sd_min')}(", s, flags=re.I)
    s = re.sub(r"\bMAX\s*\(", f"{_helper('_sd_max')}(", s, flags=re.I)
    s = re.sub(r"\bSIN\s*\(", f"{_helper('_sd_sin')}(", s, flags=re.I)
    s = re.sub(r"\bCOS\s*\(", f"{_helper('_sd_cos')}(", s, flags=re.I)
    s = re.sub(r"\bTAN\s*\(", f"{_helper('_sd_tan')}(", s, flags=re.I)
    s = re.sub(r"\bINTEGER\s*\(", f"{_helper('_sd_int')}(", s, flags=re.I)
    # 幂运算 ^ -> **
    s = s.replace("^", "**")
    # 变量名替换：按长度降序，中文/带空格变量名映射为合法 Python 标识符。
    # 单次替换，防止刚生成的内部别名又被另一个真实变量名匹配。
    compiled_names = _names_regex(names_key)
    if compiled_names is not None:
        # 内部别名保持占位符直到最外层转换结束，避免递归函数重新匹配真实同名变量。
        regex, aliases = compiled_names
        s = regex.sub(
            lambda match: (
                match.group(0)
                if _PROTECTED_TOKEN.fullmatch(match.group(0))
                else aliases[canonical_name(match.group(0))]
            ),
            s,
        )
    # 先处理真实变量名，再处理原生逻辑运算符，避免破坏带特殊字符的名称。
    s = re.sub(r":AND:", " and ", s, flags=re.I)
    s = re.sub(r":OR:", " or ", s, flags=re.I)
    s = re.sub(r":NOT:", " not ", s, flags=re.I)
    s = s.replace("<>", "!=")
    s = re.sub(r"(?<![<>=!])=(?!=)", "==", s)
    s = re.sub(r"\bTIME\b", "Time", s, flags=re.I)
    if not _nested:
        for helper_name, token in HELPER_TOKENS.items():
            s = s.replace(token, helper_name)
        if "@var" in s:
            values = list(name_map.values())
            s = re.sub(r"@var(\d+)@", lambda match: values[int(match.group(1))], s)
    return s.strip()


_ALLOWED_MATH_ATTRS = {
    "sqrt",
    "exp",
    "log",
    "sin",
    "cos",
    "tan",
}


def _safe_eval_node(node: ast.AST, namespace: dict) -> float:
    """求值受限 Python 表达式 AST，仅允许数学表达式和白名单函数。"""
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body, namespace)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            if len(node.value) > MAX_EXPR_CHARS:
                raise ValueError("字符串常量过长")
            return node.value
        if isinstance(node.value, (int, float, bool)):
            return _ensure_number(node.value, "数值常量")
        raise ValueError(f"不支持的常量: {node.value!r}")
    if isinstance(node, ast.Name):
        if node.id in namespace:
            return namespace[node.id]
        raise ValueError(f"变量未定义: {node.id}")
    if isinstance(node, ast.UnaryOp):
        value = _safe_eval_node(node.operand, namespace)
        if isinstance(node.op, ast.UAdd):
            return +value
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.Not):
            return not value
        raise ValueError("不支持的一元运算")
    if isinstance(node, ast.BinOp):
        left = _ensure_number(_safe_eval_node(node.left, namespace), "二元运算左值")
        right = _ensure_number(_safe_eval_node(node.right, namespace), "二元运算右值")
        if isinstance(node.op, ast.Add):
            return _ensure_number(left + right, "加法结果")
        if isinstance(node.op, ast.Sub):
            return _ensure_number(left - right, "减法结果")
        if isinstance(node.op, ast.Mult):
            return _ensure_number(left * right, "乘法结果")
        if isinstance(node.op, ast.Div):
            return _ensure_number(left / right, "除法结果")
        if isinstance(node.op, ast.Mod):
            return _ensure_number(left % right, "取模结果")
        if isinstance(node.op, ast.Pow):
            if abs(float(right)) > MAX_POWER_EXPONENT:
                raise ValueError("指数过大")
            return _ensure_number(left**right, "幂运算结果")
        raise ValueError("不支持的二元运算")
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_safe_eval_node(value, namespace) for value in node.values)
        if isinstance(node.op, ast.Or):
            return any(_safe_eval_node(value, namespace) for value in node.values)
        raise ValueError("不支持的布尔运算")
    if isinstance(node, ast.Compare):
        left = _ensure_number(_safe_eval_node(node.left, namespace), "比较左值")
        for op, comparator in zip(node.ops, node.comparators, strict=False):
            right = _ensure_number(_safe_eval_node(comparator, namespace), "比较右值")
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            else:
                raise ValueError("不支持的比较运算")
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _safe_eval_node(
            node.body if _safe_eval_node(node.test, namespace) else node.orelse, namespace
        )
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name not in namespace or not callable(namespace[func_name]):
                raise ValueError(f"函数未允许: {func_name}")
            func = namespace[func_name]
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id != "math" or node.func.attr not in _ALLOWED_MATH_ATTRS:
                raise ValueError(f"math 函数未允许: {node.func.attr}")
            func = getattr(math, node.func.attr)
        else:
            raise ValueError("不支持的函数调用")
        if node.keywords:
            raise ValueError("不支持关键字参数")
        args = [_safe_eval_node(a, namespace) for a in node.args]
        return _ensure_number(func(*args), "函数结果")
    raise ValueError(f"不支持的表达式节点: {type(node).__name__}")


def _ensure_number(value, context: str) -> float:
    if isinstance(value, bool):
        return float(value)
    if not isinstance(value, (int, float)):
        raise ValueError(f"{context} 不是数值")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{context} 超出浮点范围") from exc
    if not math.isfinite(number):
        raise ValueError(f"{context} 不是有限数")
    return number


def _ast_depth(node: ast.AST) -> int:
    children = list(ast.iter_child_nodes(node))
    if not children:
        return 1
    return 1 + max(_ast_depth(child) for child in children)


def _safe_eval_expr(py_expr: str, namespace: dict) -> float:
    if len(py_expr) > MAX_EXPR_CHARS:
        raise ValueError("表达式过长")
    tree = ast.parse(py_expr, mode="eval")
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        raise ValueError("表达式节点过多")
    if _ast_depth(tree) > MAX_AST_DEPTH:
        raise ValueError("表达式嵌套过深")
    return _safe_eval_node(tree, namespace)


def _compile_expr(rhs: str, name_map: dict[str, str]):
    """把 rhs 转成经过资源检查的表达式树。"""
    integ_args = _function_args(rhs, "INTEG")
    if integ_args:
        expr = integ_args[0]
    elif rhs.strip().startswith("["):
        raise ValueError("LOOKUP 表不能直接作为标量求值")
    else:
        expr = rhs
    py_expr = _to_python_expr(expr, name_map)
    if len(py_expr) > MAX_EXPR_CHARS:
        raise ValueError("表达式过长")
    try:
        tree = ast.parse(py_expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(
            f"求值失败 [{py_expr[:80]}]: 无法解析，可能用了内置引擎不认识的函数或写法；"
            "请核对方程，或用 `crosscheck`（PySD）运行"
        ) from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        raise ValueError("表达式节点过多")
    if _ast_depth(tree) > MAX_AST_DEPTH:
        raise ValueError("表达式嵌套过深")
    # 只把表达式用到的名字放进求值命名空间，大模型上省去每次复制全部变量
    reverse = {alias: name for name, alias in name_map.items()}
    idents = tuple(
        sorted(
            {
                (node.id, reverse.get(node.id, node.id))
                for node in ast.walk(tree)
                # 与模型变量同名的辅助函数名（如 _sd_max）不能被变量值覆盖
                if isinstance(node, ast.Name) and (node.id in reverse or node.id not in name_map)
            }
        )
    )
    return py_expr, tree, idents


def evaluate(
    rhs: str,
    ctx: dict,
    lookups: dict,
    name_map: dict[str, str],
    cache: dict | None = None,
) -> float:
    """在上下文 ctx 中求值 rhs；name_map 把带空格变量名映射为合法标识符。

    cache 由仿真器传入，同一方程只转换和解析一次；不传时每次重新编译。
    """
    compiled = cache.get(rhs) if cache is not None else None
    if compiled is None:
        compiled = _compile_expr(rhs, name_map)
        if cache is not None:
            cache[rhs] = compiled
    py_expr, tree, idents = compiled
    store = ctx.get("__stateful__")

    def _wl(x, table):
        return LookupTable(table)(x)

    def _pulse(start, duration):
        t = ctx.get("Time", 0.0)
        dt = ctx.get("__time_step__")
        if dt is None:
            raise ValueError("PULSE 需要有效 TIME STEP 上下文")
        width = dt if duration == 0 else duration
        time_plus = t + dt / 2
        return 1.0 if start < time_plus < start + width else 0.0

    def _ramp(slope, start, end=None):
        t = ctx.get("Time", 0.0)
        if t <= start:
            return 0.0
        if end is not None and t > end:
            return slope * (end - start)
        return slope * (t - start)

    def _step(value, time):
        t = ctx.get("Time", 0.0)
        dt = ctx.get("__time_step__")
        if dt is None:
            raise ValueError("STEP 需要有效 TIME STEP 上下文")
        return value if t + dt / 2 > time else 0.0

    def _modulo(dividend, divisor):
        # 非正除数时 Vensim 与 PySD 的定义不同，这里直接报错
        if divisor <= 0:
            raise ValueError("MODULO 的非正除数须在原生 Vensim 核对，内置后端仅支持正除数")
        # Python 的 % 随除数定号；C 余数随被除数定号，浮点输入也必须保留。
        return math.fmod(dividend, divisor)

    def _delay_fixed(key, value, delay_time, initial_value):
        raise ValueError("DELAY FIXED 必须由仿真器作为独立状态运行")

    def _sample_if_true(key, condition, value, initial):
        # Vensim 语义：条件为真时取当前输入，否则保持上一时刻的输出；起点为初值。
        if store is None:
            return value if condition else initial
        held = store.values.setdefault(key, _ensure_number(initial, "SAMPLE IF TRUE 初值"))
        out = value if condition else held
        store.pending[key] = out
        return out

    def _initial(key, value):
        if store is None:
            return value
        return store.values.setdefault(key, _ensure_number(value, "INITIAL 参数"))

    def _initializing():
        return True if store is None else store.initializing

    def _quantum(value, quantum):
        if quantum <= 0:
            return value
        return quantum * int(value / quantum)

    def _power(base, exponent):
        if abs(float(exponent)) > MAX_POWER_EXPONENT:
            raise ValueError("指数过大")
        return base**exponent

    def _logb(value, base):
        return math.log(value) / math.log(base)

    def _pulse_train(start, duration, repeat, end):
        # 与 PySD 定义一致：start <= t < end 内每隔 repeat 出现宽 duration 的脉冲
        t = ctx.get("Time", 0.0)
        dt = ctx.get("__time_step__") or 0.0
        eps = dt * 1e-6
        if t + eps < start or t >= end:
            return 0.0
        if repeat <= 0:
            raise ValueError("PULSE TRAIN 的重复间隔必须大于 0")
        width = dt if duration == 0 else duration
        return 1.0 if math.fmod(t - start + eps, repeat) < width else 0.0

    def _lookup_method(method):
        def call(table, x):
            if not isinstance(table, LookupTable):
                raise ValueError("第一个参数必须是查表函数名")
            return getattr(table, method)(x)

        return call

    namespace = {
        "math": math,
        "_wl": _wl,
        "_sd_abs": abs,
        "_sd_min": min,
        "_sd_max": max,
        "_sd_sqrt": math.sqrt,
        "_sd_exp": math.exp,
        "_sd_log": math.log,
        "_sd_sin": math.sin,
        "_sd_cos": math.cos,
        "_sd_tan": math.tan,
        "_sd_int": int,
        "_sd_float": float,
        "_sd_modulo": _modulo,
        "_pulse": _pulse,
        "_ramp": _ramp,
        "_step": _step,
        "_delay_fixed": _delay_fixed,
        "_sample_if_true": _sample_if_true,
        "_sd_initial": _initial,
        "_sd_initializing": _initializing,
        "_sd_quantum": _quantum,
        "_sd_power": _power,
        "_sd_logb": _logb,
        "_pulse_train": _pulse_train,
        "_lookup_extrapolate": _lookup_method("extrapolate"),
        "_lookup_forward": _lookup_method("forward"),
        "_lookup_backward": _lookup_method("backward"),
        "_sd_asin": math.asin,
        "_sd_acos": math.acos,
        "_sd_atan": math.atan,
        "_sd_sinh": math.sinh,
        "_sd_cosh": math.cosh,
        "_sd_tanh": math.tanh,
    }
    # 注入表达式用到的查表函数和变量值（用别名）；变量值优先于同名查表
    for ident, key in idents:
        if key in ctx:
            namespace[ident] = ctx[key]
        elif key in lookups:
            namespace[ident] = lookups[key]
    try:
        return _ensure_number(_safe_eval_node(tree, namespace), "方程结果")
    except Exception as exc:
        raise ValueError(f"求值失败 [{py_expr[:80]}]: {exc}") from exc


# ---------------------------------------------------------------------------
# 仿真器
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SimResult:
    times: list[float]
    series: dict[str, list[float]]
    eval_warnings: list[str] = dataclasses.field(default_factory=list)


def _validate_time_settings(t0: float, tf: float, dt: float, saveper: float) -> None:
    values = {
        "INITIAL TIME": t0,
        "FINAL TIME": tf,
        "TIME STEP": dt,
        "SAVEPER": saveper,
    }
    for label, value in values.items():
        if not math.isfinite(float(value)):
            raise ValueError(f"{label} 必须是有限数")
    if dt <= 0:
        raise ValueError("TIME STEP 必须大于 0")
    if saveper <= 0:
        raise ValueError("SAVEPER 必须大于 0")
    if tf < t0:
        raise ValueError("FINAL TIME 必须大于或等于 INITIAL TIME")
    if tf > t0 and (t0 + dt <= t0 or tf - dt >= tf):
        raise ValueError("TIME STEP 小于当前时间量级的浮点精度，不能生成可靠时间网格")
    for label, ratio in (
        ("仿真区间/TIME STEP", (tf - t0) / dt),
        ("SAVEPER/TIME STEP", saveper / dt),
    ):
        if not math.isfinite(ratio) or ratio > MAX_SIM_STEPS:
            raise ValueError(f"{label} 超过资源上限")
        if not math.isclose(ratio, round(ratio), rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(f"内置 Euler 要求 {label} 为整数，请调整步长或使用其他后端")
    if saveper < dt:
        raise ValueError("SAVEPER 不能小于 TIME STEP")
    sim_steps = int(math.floor((tf - t0) / dt)) + 1
    output_points = int(math.floor((tf - t0) / saveper)) + 1
    if sim_steps > MAX_SIM_STEPS:
        raise ValueError(f"仿真步数过多: {sim_steps} > {MAX_SIM_STEPS}")
    if output_points > MAX_OUTPUT_POINTS:
        raise ValueError(f"输出点数过多: {output_points} > {MAX_OUTPUT_POINTS}")


def simulate(
    equations: OrderedDict[str, Equation],
    t0: float,
    tf: float,
    dt: float,
    saveper: float | None = None,
    strict: bool = True,
) -> SimResult:
    """Euler 积分仿真。"""
    saveper = dt if saveper is None else saveper
    _validate_time_settings(t0, tf, dt, saveper)
    order = topological_sort(equations)

    # 构建变量名到合法 Python 标识符的映射（带空格/特殊字符的变量名）
    name_map: dict[str, str] = {}
    for i, name in enumerate(equations.keys()):
        name_map[name] = f"_v{i}"

    # 递归求初值依赖；库存的流率依赖不参与初始化，避免用临时零值污染辅助量。
    store = StatefulStore()
    cache: dict = {}
    ctx: dict = {"Time": t0, "__time_step__": dt, "__stateful__": store}
    lookups = {name: LookupTable(eq.lookup_pairs) for name, eq in equations.items() if eq.is_lookup}
    initializing = set()
    names = set(equations)
    eval_warnings = []
    fixed_delays = {}

    def initial_value(name):
        if name in ctx:
            return ctx[name]
        if name in initializing:
            raise ValueError(f"检测到初值循环依赖: {name}")
        initializing.add(name)
        eq = equations[name]
        if eq.is_lookup:
            initializing.remove(name)
            return lookups[name]
        else:
            expression = (
                eq.delay_fixed_args[2]
                if eq.delay_fixed_args is not None
                else (eq.integ_init_expr or str(eq.integ_init or 0))
                if eq.integ_flow is not None
                else eq.rhs
            )
            dependencies = (
                expression + " " + eq.delay_fixed_args[1]
                if eq.delay_fixed_args is not None
                else expression
            )
            for dependency in extract_deps(init_dependency_text(dependencies), names):
                initial_value(dependency)
            try:
                ctx[name] = evaluate(expression, ctx, lookups, name_map, cache)
                if eq.delay_fixed_args is not None:
                    duration = _ensure_number(
                        evaluate(eq.delay_fixed_args[1], ctx, lookups, name_map, cache),
                        f"{name} 的固定延迟时间",
                    )
                    ratio = max(duration / dt, 1)
                    if not math.isfinite(ratio) or ratio > MAX_SIM_STEPS:
                        raise ValueError("固定延迟状态超过资源上限")
                    # 原生固定延迟只在步长边界变化，时长冻结于初始值；半步向上取整。
                    count = math.floor(ratio + 0.5)
                    if (
                        sum(len(state) for state in fixed_delays.values()) + count
                        > MAX_OUTPUT_POINTS
                    ):
                        raise ValueError("固定延迟总状态超过资源上限")
                    fixed_delays[name] = deque([ctx[name]] * count, maxlen=count)
            except ValueError as exc:
                if strict:
                    raise ValueError(f"初始化 {name} 失败: {exc}") from exc
                ctx[name] = 0.0
                eval_warnings.append(f"初始化 {name}: {exc}")
        initializing.remove(name)
        return ctx[name]

    for name in equations:
        initial_value(name)
    store.initializing = False

    times: list[float] = []
    series: dict[str, list[float]] = {n: [] for n, eq in equations.items() if not eq.is_lookup}
    steps = round((tf - t0) / dt)
    save_steps = round(saveper / dt)
    if (steps // save_steps + 1) * len(series) > 5_000_000:
        raise ValueError("包含隐式状态的仿真输出超过资源上限，请减少变量或保存点")
    for step in range(steps + 1):
        t = t0 + step * dt
        # 注入当前时间，供 STEP / PULSE / RAMP 等时间函数使用
        ctx["Time"] = t
        # 计算所有辅助变量（当前时间步）
        for name in order:
            eq = equations[name]
            if eq.integ_flow is not None or eq.delay_fixed_args is not None or eq.is_lookup:
                continue
            try:
                ctx[name] = evaluate(eq.rhs, ctx, lookups, name_map, cache)
            except ValueError as exc:
                message = f"{name} @t={t:.2f}: {exc}"
                if strict:
                    raise ValueError(message) from exc
                ctx[name] = 0.0
                if not eval_warnings:
                    eval_warnings.append(message)
        # 计算库存的流率（用当前辅助值）
        flows: dict[str, float] = {}
        for name, eq in equations.items():
            if eq.integ_flow is not None:
                try:
                    flows[name] = evaluate(eq.integ_flow, ctx, lookups, name_map, cache)
                except ValueError as exc:
                    message = f"flow {name} @t={t:.2f}: {exc}"
                    if strict:
                        raise ValueError(message) from exc
                    flows[name] = 0.0
                    if not eval_warnings:
                        eval_warnings.append(message)

        # 记录
        if step % save_steps == 0:
            times.append(t)
            for n in series:
                series[n].append(ctx.get(n, 0.0))

        # 同时捕获当前输入，再更新所有延迟，避免声明顺序影响反馈状态。
        delayed_inputs = {}
        for name in fixed_delays:
            try:
                delayed_inputs[name] = _ensure_number(
                    evaluate(equations[name].delay_fixed_args[0], ctx, lookups, name_map, cache),
                    f"{name} 的延迟输入",
                )
            except ValueError as exc:
                if strict:
                    raise ValueError(f"固定延迟 {name} @t={t}: {exc}") from exc
                delayed_inputs[name] = 0.0
                eval_warnings.append(f"固定延迟 {name} @t={t}: {exc}")
        if step < steps:
            for name, value in delayed_inputs.items():
                fixed_delays[name].append(value)
                ctx[name] = fixed_delays[name][0]

        store.commit()

        # Euler 更新库存
        for name, eq in equations.items():
            if eq.integ_flow is not None and step < steps:
                ctx[name] = _ensure_number(ctx[name] + flows[name] * dt, f"库存 {name} @t={t + dt}")

    if eval_warnings:
        sys.stderr.write(
            "警告: 部分变量求值失败（已按兼容模式置零），首条: " + eval_warnings[0] + "\n"
        )
        sys.stderr.write(
            f"      共 {len(eval_warnings)} 类求值失败，可能导致 nodata 或曲线为 0。\n"
        )

    return SimResult(times=times, series=series, eval_warnings=eval_warnings)


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------


def load_mdl_text(path: Path) -> str:
    return MdlDocument.read(path).semantic_text


def _resolve_number(rhs, equations, visited=None):
    visited = set() if visited is None else set(visited)
    try:
        return float(rhs.strip())
    except ValueError:
        pass
    if rhs in visited:
        raise ValueError(f"时间设置循环引用: {rhs}")
    visited.add(rhs)
    names = set(equations)
    dependencies = extract_deps(rhs, names)
    context = {
        name: _resolve_number(equations[name].rhs, equations, visited) for name in dependencies
    }
    mapping = {name: f"_control_{index}" for index, name in enumerate(names)}
    return evaluate(rhs, context, {}, mapping)


def get_time_bounds(equations: OrderedDict[str, Equation]):
    controls = ("INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER")
    missing = [name for name in controls if name not in equations]
    if missing:
        raise ValueError("模型必须明确时间设置，缺少: " + ", ".join(missing))
    return tuple(_resolve_number(equations[name].rhs, equations) for name in controls)


def _ensure_output_separate(output: Path, inputs: list[Path]) -> None:
    separate_output(output, inputs)


def command_simulate(
    path: Path,
    output: Path,
    variables: list[str],
    plot: Path | None = None,
    strict: bool = True,
    dpi=300,
    formats=None,
    plot_config=None,
    **options,
) -> int:
    from simulation_runner import run_model

    _ensure_output_separate(output, [path, *([plot_config] if plot_config else [])])
    metadata = output.with_suffix(output.suffix + ".run.json")
    protected = [path, *([plot_config] if plot_config else [])]
    preflight_outputs([output, metadata], protected)
    if plot:
        _ensure_output_separate(plot, [path, output, metadata])
    result = run_model(path, variables, strict=strict, **options)
    variables = list(result.series)
    from experiments import csv_bytes

    rows = [
        [t, *[result.series[name][index] for name in variables]]
        for index, t in enumerate(result.times)
    ]
    if plot:
        _render_plot(
            result,
            variables,
            plot,
            dpi=dpi,
            formats=formats,
            inputs=[path, output, metadata],
            plot_config=plot_config,
        )
    content = csv_bytes(["Time", *variables], rows)
    result.metadata["source_csv_sha256"] = hashlib.sha256(content).hexdigest()
    atomic_write(output, content)
    atomic_write(metadata, json.dumps(result.metadata, ensure_ascii=False, indent=2).encode())
    print(f"仿真完成: {len(result.times)} 个时间点 -> {output}")
    return 0


def _render_plot(result, variables: list[str], output: Path, title: str = "", **options):
    from result_plotting import export_figures

    return export_figures([("current", result)], variables, output, title=title, **options)


def command_graph(
    path: Path,
    variables: list[str],
    output: Path,
    title: str,
    strict: bool = True,
    dpi=300,
    formats=None,
    plot_config=None,
    **options,
) -> int:
    from simulation_runner import run_model

    _ensure_output_separate(output, [path])
    result = run_model(path, variables, strict=strict, **options)
    _render_plot(
        result,
        list(result.series),
        output,
        title=title,
        dpi=dpi,
        formats=formats,
        inputs=[path],
        plot_config=plot_config,
    )
    print(f"折线图已导出: {output}")
    return 0


def command_compare(
    base: Path,
    scenarios: list[Path],
    variables: list[str],
    output: Path,
    title: str,
    strict: bool = True,
    dpi=300,
    formats=None,
    plot_config=None,
    **options,
) -> int:
    from experiments import plot_experiment
    from simulation_runner import run_model

    _ensure_output_separate(output, [base, *scenarios])
    if not variables:
        raise ValueError("至少指定一个变量 --var")
    results = [
        (model.stem, run_model(model, variables, strict=strict, **options))
        for model in [base, *scenarios]
    ]
    plot_experiment(
        results,
        variables,
        output,
        title=title,
        dpi=dpi,
        formats=formats,
        inputs=[base, *scenarios],
        plot_config=plot_config,
    )
    print(f"对比图已导出: {output}")
    return 0


# ---------------------------------------------------------------------------
# 单位校验
# ---------------------------------------------------------------------------


def command_units(path: Path) -> int:
    text = load_mdl_text(path)
    equations = parse_equations(text)
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
        # 完整量纲推导后续应基于表达式 AST；这里保留缺失单位检查作为轻量预检。
    print(f"\nWarnings: {warnings}  Errors: {errors}")
    return 1 if errors else 0


# ---------------------------------------------------------------------------
# 检查与修复
# ---------------------------------------------------------------------------


def command_check(path: Path, strict: bool = False) -> int:
    text = load_mdl_text(path)
    equations = parse_equations(text)
    errors = 0
    warnings = 0
    print(f"CHECK: {path}")

    # 1. 未定义变量引用：整条方程转 AST 逐个名字核对，不依赖初始时刻走到哪个分支
    name_map = {name: f"_v{i}" for i, name in enumerate(equations)}
    allowed = set(name_map.values()) | set(HELPER_TOKENS) | {"Time", "True", "False"}
    for name, eq in equations.items():
        if eq.is_lookup:
            continue
        parts = (eq.integ_flow, eq.integ_init_expr) if eq.integ_flow is not None else (eq.rhs,)
        for expr in parts:
            if not expr:
                continue
            try:
                tree = ast.parse(_to_python_expr(expr, name_map), mode="eval")
            except (SyntaxError, ValueError):
                continue  # 语法问题由第 5 步引擎预检报告
            funcs = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
            for bad in sorted(
                {
                    node.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Name)
                    and id(node) not in funcs
                    and node.id not in allowed
                }
            ):
                errors += 1
                print(f"  ERROR {name}: 引用未定义变量 '{bad}'")

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
        from sketch_geometry import shown
        from vensim_autolayout import load_mdl as _load_mdl

        _, views = _load_mdl(path)
        for view in views:
            ids = set(view.objects)
            ordered = sorted(view.objects.values(), key=lambda obj: obj.line_index)
            valve_label = {
                prev.obj_id: obj
                for prev, obj in zip(ordered, ordered[1:], strict=False)
                if prev.kind == 11 and obj.kind == 10 and obj.attached_to_valve
            }
            for arrow in view.arrows:
                if arrow.from_id not in ids or arrow.to_id not in ids:
                    errors += 1
                    print(f"  ERROR 草图 {view.name}: 箭头 {arrow.obj_id} 断裂引用")
                    continue
                # 每条信息箭头都要标正负号（原则性要求）；物理管道不算。
                # 指向/来自阀门的箭头按其附着的流量名算（Vensim 原生模型里输入流率的箭头指向阀门），
                # 隐藏的箭头与隐藏对象上的箭头不算（与 feedback 审计口径一致）
                if arrow.is_physical_flow or not shown(view, arrow):
                    continue
                ends = [view.objects[arrow.from_id], view.objects[arrow.to_id]]
                ends = [valve_label.get(obj.obj_id, obj) for obj in ends]
                if all(obj.kind == 10 for obj in ends) and (
                    arrow.fields[6].strip() if len(arrow.fields) > 6 else "0"
                ) not in {"43", "45", "83", "79", "115", "111"}:
                    warnings += 1
                    print(
                        f"  WARNING 草图 {view.name}: 箭头 {arrow.obj_id} "
                        f"{ends[0].name} → {ends[1].name} 没有标极性：JSON 建模在 links 里写明 + 或 -，"
                        "已有 MDL 用 fix --apply-polarity 补写"
                    )

    # 5. 用内置引擎实际求一次初值，暴露表达式里的未知名称和语法问题
    try:
        bounds = get_time_bounds(equations)
        _validate_time_settings(*bounds)
        simulate(equations, bounds[0], bounds[0], bounds[2], bounds[3])
    except (ValueError, RecursionError) as exc:
        errors += 1
        print(f"  ERROR 内置引擎预检: {exc}")

    # 6. 时间设置
    for req in ("INITIAL TIME", "FINAL TIME", "TIME STEP"):
        if req not in equations:
            errors += 1
            print(f"  ERROR: 缺失控制变量 {req}")

    # 7. 使用检查：与 Vensim Check Model 的 USE FLAG 同口径，打开模型时就会看到
    not_used, misused = use_flags(parse_equations(text, expand=False))
    for name in not_used:
        warnings += 1
        print(
            f"  WARNING {name}: 没有被任何方程使用（Vensim 报 USE FLAG）；"
            "是输出指标就用 fix --mark-supplementary 标记，否则删掉"
        )
    for name in misused:
        warnings += 1
        print(f"  WARNING {name}: 标了 Supplementary 却被其他方程使用，请去掉该标记")

    if errors == 0:
        print("\nPASS: 未发现错误。")
    else:
        print(f"\nFAIL: {errors} 个错误。")
    print(f"Warnings: {warnings}")
    if strict and warnings:
        print("FAIL(--strict): 警告也要清零（缺单位、USE FLAG、箭头缺极性）。")
    return 1 if errors or (strict and warnings) else 0


POLARITY_CODES = {"+": "43", "-": "45"}


def apply_polarity(document: MdlDocument) -> tuple[str, list[str], list[dict]]:
    """把 feedback 推出的确定符号写进未标号箭头的极性字段（草图第 7 个字段）。

    只写方程推导或基准运行数值偏导给出的 + / -；推不出的原样保留并列出。
    方程区逐字节不变（MdlDocument.encode 校验）。
    """
    from feedback_audit import audit_feedback
    from mdl_document import split_sketch_fields
    from vensim_autolayout import parse_views

    report = audit_feedback(document.semantic_text)
    lines = document.text.splitlines(keepends=True)
    views = parse_views(lines)
    applied, remaining = [], []
    for row in report["links"]:
        if row["displayed"] or row["kind"] == "flow" or row["status"] == "no_dependency":
            continue
        sign = row.get("suggested")
        if sign not in POLARITY_CODES:
            remaining.append(
                {
                    "view": row["view"],
                    "arrow": row["arrow"],
                    "source": row["source"],
                    "target": row["target"],
                    "reason": "方程和基准运行数值偏导都推不出确定符号，需建模者判断",
                }
            )
            continue
        arrow = next(a for a in views[row["view_index"]].arrows if a.obj_id == row["arrow"])
        line = lines[arrow.line_index]
        body = line.rstrip("\r\n")
        fields = split_sketch_fields(body)
        if len(fields) < 7 or fields[0] != "1" or fields[1].strip() != str(arrow.obj_id):
            raise ValueError(f"无法安全定位箭头 {arrow.obj_id} 的极性字段")
        fields[6] = POLARITY_CODES[sign]
        lines[arrow.line_index] = ",".join(fields) + line[len(body) :]
        applied.append(
            f"箭头 {row['arrow']} {row['source']} → {row['target']}: {sign}"
            f"（{'方程推导' if row['suggested_source'] == 'equation' else '基准运行数值偏导'}）"
        )
    remaining.extend(report.get("unsigned_unmapped", []))
    return document.encode(lines).decode(document.encoding, "surrogateescape"), applied, remaining


def command_fix(
    path: Path,
    output: Path,
    units_map=None,
    drop_broken=False,
    mark_supplementary=False,
    apply_polarity_signs=False,
) -> int:
    _ensure_output_separate(output, [path, *([units_map] if units_map else [])])
    document = MdlDocument.read(path)
    text = document.text
    equations = parse_equations(document.semantic_text, expand=False)
    fixes = []
    remaining = []
    if apply_polarity_signs:
        # 先补极性：只改草图字段，后面的选项在此基础上继续
        text, applied, remaining = apply_polarity(document)
        document = MdlDocument(text.encode(document.encoding, "surrogateescape"), document.encoding)
        fixes.extend(f"补写极性 {item}" for item in applied)
    if not units_map and not drop_broken and not mark_supplementary and not apply_polarity_signs:
        raise ValueError(
            "请指定 --units-map、--drop-broken-arrows、--mark-supplementary 或 --apply-polarity；"
            "缺失的单位不会自动补成 Dmnl"
        )
    if units_map:
        mapping = json.loads(units_map.read_text(encoding="utf-8-sig"))
        for name, unit in mapping.items():
            if (
                name not in equations
                or not isinstance(unit, str)
                or not unit.strip()
                or any(c in unit for c in "~|\n\r")
            ):
                raise ValueError(f"无效单位映射: {name}")
            if equations[name].unit:
                raise ValueError(f"{name} 已有单位，units-map 只补缺失单位")
            pattern = rf'(?m)(^[ \t]*"?{re.escape(name)}"?\s*=[^~|]*~)[ \t\r\n]*(?=[~|])'
            text, count = re.subn(
                pattern,
                lambda match, unit=unit: match[1] + " " + unit.strip() + "\n\t",
                text,
                count=1,
            )
            if count != 1:
                raise ValueError(f"无法安全定位单位字段: {name}")
            fixes.append(f"补充用户提供的单位: {name} = {unit}")
    if drop_broken:
        from vensim_autolayout import parse_views

        lines = text.splitlines(keepends=True)
        broken = {
            arrow.line_index
            for view in parse_views(lines)
            for arrow in view.arrows
            if arrow.from_id not in view.objects or arrow.to_id not in view.objects
        }
        text = "".join(line for index, line in enumerate(lines) if index not in broken)
        fixes.append(f"按显式选项删除 {len(broken)} 条断裂草图箭头")
    if mark_supplementary:
        # 没有被使用的输出指标加上 ~ :SUPPLEMENTARY 字段，打开 Vensim 不再报 USE FLAG。
        # 没有被使用的常量多半是遗留参数，不替它掩盖，只列出来建议删除
        not_used, _ = use_flags(equations)
        constants = [name for name in not_used if _is_number(equations[name].rhs)]
        for name in not_used:
            if name in constants:
                continue
            start = re.search(rf'(?m)^[ \t]*"?{re.escape(name)}"?[ \t]*(?:=|\()', text)
            end = text.find("|", start.end()) if start else -1
            if start is None or end < 0:
                raise ValueError(f"无法安全定位方程: {name}")
            missing = max(0, 2 - text.count("~", start.end(), end))
            text = text[:end] + "~\n\t" * missing + "~\t:SUPPLEMENTARY\n\t" + text[end:]
            fixes.append(f"标为 Supplementary（输出指标）: {name}")
        fixes.extend(f"常量 {name} 没有被任何方程使用，未标记，建议删除" for name in constants)
    atomic_write(output, text.encode(document.encoding, errors="surrogateescape"))
    report = {"output": str(output), "fixes": fixes}
    if apply_polarity_signs:
        report["unsigned_remaining"] = remaining
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
    p_sim.add_argument("--var", action="append", help="要导出的变量，可多次；不写时导出全部")
    p_sim.add_argument("--plot", type=Path, default=None, help="同时导出折线图 PNG")
    p_sim.add_argument(
        "--keep-going", action="store_true", help="求值失败时继续输出，并把失败变量置零。"
    )

    p_graph = sub.add_parser("graph", help="仿真并导出折线图 PNG")
    p_graph.add_argument("model", type=Path)
    p_graph.add_argument("--var", action="append", required=True, help="绘图变量，可多次")
    p_graph.add_argument("--output", required=True, type=Path)
    p_graph.add_argument("--title", default="")
    p_graph.add_argument(
        "--keep-going", action="store_true", help="求值失败时继续输出，并把失败变量置零。"
    )

    p_cmp = sub.add_parser("compare", help="多场景对比图")
    p_cmp.add_argument("base", type=Path)
    p_cmp.add_argument("--scenario", action="append", default=[], type=Path)
    p_cmp.add_argument("--var", action="append", required=True)
    p_cmp.add_argument("--output", required=True, type=Path)
    p_cmp.add_argument("--title", default="")
    p_cmp.add_argument(
        "--keep-going", action="store_true", help="求值失败时继续输出，并把失败变量置零。"
    )

    p_units = sub.add_parser("units", help="单位校验")
    p_units.add_argument("model", type=Path)

    p_check = sub.add_parser("check", help="检查方程引用、时间设置与草图记录")
    p_check.add_argument("model", type=Path)
    p_check.add_argument(
        "--strict", action="store_true", help="警告（缺单位、未使用变量、箭头缺极性）也按失败处理"
    )

    p_fix = sub.add_parser("fix", help="自动修复")
    p_fix.add_argument("model", type=Path)
    p_fix.add_argument("--output", required=True, type=Path)
    p_fix.add_argument("--units-map", type=Path)
    p_fix.add_argument("--drop-broken-arrows", action="store_true")
    p_fix.add_argument(
        "--mark-supplementary",
        action="store_true",
        help="把没有被使用的输出指标标为 Supplementary，消除 Vensim 的 USE FLAG",
    )
    p_fix.add_argument(
        "--apply-polarity",
        action="store_true",
        help="给未标号的箭头写入方程推导或基准运行数值偏导得出的 +/-，推不出的列在 "
        "unsigned_remaining 里",
    )

    from result_plotting import plot_options

    for target in (p_sim, p_graph, p_cmp):
        plot_options(target)
        target.add_argument("--backend", choices=["builtin", "pysd"], default="builtin")
        target.add_argument("--set", action="append", default=[], dest="parameters")
        target.add_argument("--time-step", type=float)
        target.add_argument("--final-time", type=float)
        target.add_argument("--saveper", type=float)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    options = {}
    if args.command in {"simulate", "graph", "compare"}:
        from simulation_runner import parse_overrides

        options = {
            "backend": args.backend,
            "params": parse_overrides(args.parameters),
            "time_step": args.time_step,
            "final_time": args.final_time,
            "saveper": args.saveper,
            "dpi": args.dpi,
            "formats": args.formats,
            "plot_config": args.plot_config,
        }
    if args.command == "simulate":
        return command_simulate(
            args.model,
            args.output,
            args.var,
            getattr(args, "plot", None),
            not args.keep_going,
            **options,
        )
    if args.command == "graph":
        return command_graph(
            args.model, args.var, args.output, args.title, not args.keep_going, **options
        )
    if args.command == "compare":
        return command_compare(
            args.base,
            args.scenario,
            args.var,
            args.output,
            args.title,
            not args.keep_going,
            **options,
        )
    if args.command == "units":
        return command_units(args.model)
    if args.command == "check":
        return command_check(args.model, args.strict)
    if args.command == "fix":
        return command_fix(
            args.model,
            args.output,
            args.units_map,
            args.drop_broken_arrows,
            args.mark_supplementary,
            args.apply_polarity,
        )
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
