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
import ast
import dataclasses
import hashlib
import json
import math
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from mdl_document import MdlDocument, atomic_write, separate_output

# ---------------------------------------------------------------------------
# 方程区解析
# ---------------------------------------------------------------------------

MAX_SIM_STEPS = 1_000_000
MAX_OUTPUT_POINTS = 1_000_000
MAX_EXPR_CHARS = 20_000
MAX_AST_NODES = 2_000
MAX_AST_DEPTH = 80
MAX_POWER_EXPONENT = 1_000
RESERVED_EVAL_NAMES = {
    "math", "_wl",
    "_sd_abs", "_sd_min", "_sd_max", "_sd_sqrt", "_sd_exp", "_sd_log",
    "_sd_sin", "_sd_cos", "_sd_tan", "_sd_int", "_sd_float",
    "_pulse", "_ramp", "_step", "_delay_fixed",
    "__delay_fixed_history__",
}
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
}

# 方程块以 "变量名 = ..." 开头，后续 ~ 单位 ~ 注释 | 结束。
# 变量名不能只按英文标识符识别；课程和论文模型常用中文变量名。
_LOOKUP_DEF = re.compile(r"\[(.*?)\]\s*$")


@dataclasses.dataclass
class Equation:
    name: str
    rhs: str            # 等号右侧表达式
    unit: str           # 单位
    comment: str        # 注释
    integ_init: Optional[float] = None   # INTEG 初值
    integ_init_expr: Optional[str] = None  # INTEG 初值表达式
    integ_flow: Optional[str] = None     # INTEG 内部流表达式
    is_lookup: bool = False
    lookup_pairs: List[Tuple[float, float]] = dataclasses.field(default_factory=list)
    line_index: int = 0
    smooth_init_ref: Optional[str] = None  # SMOOTH 隐式库存初值引用的输入变量名


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
    raise ValueError(f"括号未闭合: {text[open_index:open_index + 80]}")


def _split_top_level_args(text: str) -> List[str]:
    """按顶层逗号切分函数参数，忽略括号、方括号与字符串内部逗号。"""
    args: List[str] = []
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


def _find_with_lookup_end(text: str, table_start: int) -> int:
    """定位 WITH LOOKUP 表字面量结尾，兼容 Vensim 的表参数写法。"""
    paren_depth = 0
    bracket_depth = 0
    for i in range(table_start, len(text)):
        ch = text[i]
        if ch == "[":
            bracket_depth += 1
        elif ch == "]" and bracket_depth:
            bracket_depth -= 1
        elif bracket_depth == 0:
            if ch == "(":
                paren_depth += 1
            elif ch == ")" and paren_depth:
                paren_depth -= 1
                if paren_depth == 0:
                    return i
    raise ValueError(f"WITH LOOKUP 表参数未闭合: {text[table_start:table_start + 80]}")


def _function_args(text: str, function_name: str) -> Optional[List[str]]:
    """解析完整函数调用的参数；不是该函数调用时返回 None。"""
    s = text.strip()
    pattern = re.compile(rf"^{re.escape(function_name)}\s*\(", re.I)
    m = pattern.match(s)
    if not m:
        return None
    open_index = m.end() - 1
    close_index = _matching_paren(s, open_index)
    if s[close_index + 1:].strip():
        return None
    return _split_top_level_args(s[open_index + 1:close_index])


def parse_equations(mdl_text: str, expand: bool = True) -> "OrderedDict[str, Equation]":
    """按原生 ~ 和 | 分隔符解析方程，允许内联字段与跨行表达式。"""
    body = mdl_text.split(r"\\\---///", 1)[0].lstrip("\ufeff")
    body = re.sub(r"\\\r?\n[ \t]*", "", body)
    equations = OrderedDict()
    offset = 0
    for block in body.split("|"):
        fields = block.split("~", 2)
        match = re.search(r'(?m)^[ \t]*([^=~|\n]+?)\s*=\s*([\s\S]*)', fields[0])
        if match is None:
            offset += block.count("\n")
            continue
        name = match.group(1).strip().strip('"')
        rhs = " ".join(match.group(2).split())
        if name in equations:
            raise ValueError(f"变量重复定义: {name}")
        eq = Equation(name=name, rhs=rhs, unit=fields[1].strip() if len(fields) > 1 else "",
                      comment=fields[2].strip() if len(fields) > 2 else "",
                      line_index=offset + block[:match.start()].count("\n"))
        integ_args = _function_args(rhs, "INTEG")
        if integ_args is not None:
            if len(integ_args) != 2:
                raise ValueError(f"{name}: INTEG 需要流率和初值")
            eq.integ_flow, eq.integ_init_expr = integ_args
            try:
                eq.integ_init = float(eq.integ_init_expr)
            except ValueError:
                pass
        if rhs.startswith("[") and rhs.endswith("]"):
            eq.is_lookup = True
            eq.lookup_pairs = _parse_lookup_pairs(rhs)
        equations[name] = eq
        offset += block.count("\n")
    if expand:
        _expand_smooth_delay(equations)
    return equations


def _expand_smooth_delay(equations):
    """平滑保存状态量，物料延迟保存管道存量；延迟默认初值等于初始输入。"""
    additions = []
    functions = {"SMOOTH": (1, True), "SMOOTH3": (3, True),
                 "SMOOTHI": (1, True), "SMOOTH3I": (3, True),
                 "DELAY1": (1, False), "DELAY3": (3, False),
                 "DELAY1I": (1, False), "DELAY3I": (3, False)}
    used = set(equations)
    for name, eq in list(equations.items()):
        for function, (order, smooth) in functions.items():
            args = _function_args(eq.rhs, function)
            if args is None:
                continue
            expected = 3 if function.endswith("I") else 2
            if len(args) != expected:
                raise ValueError(f"{name}: {function} 需要 {expected} 个参数")
            value, delay = args[:2]
            initial = args[2] if expected == 3 else value
            duration = f"(({delay}) / {order})"
            previous = f"({value})"
            for stage in range(order):
                stock = f"{name}__stage{stage + 1}"
                if stock in used:
                    raise ValueError(f"隐式状态名与模型变量冲突: {stock}")
                used.add(stock)
                outflow = stock if smooth else f"({stock} / {duration})"
                flow = f"({previous} - {outflow}) / {duration}" if smooth else f"{previous} - {outflow}"
                init = f"({initial})" if smooth else f"({initial}) * {duration}"
                state = Equation(stock, f"INTEG({flow}, {init})", eq.unit,
                                 f"{function} 隐式状态", integ_init_expr=init, integ_flow=flow,
                                 line_index=eq.line_index)
                additions.append((stock, state))
                previous = outflow
            eq.rhs, eq.integ_flow, eq.integ_init = previous, None, None
            break
    equations.update(additions)


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

# 排除函数名与关键字
_KEYWORDS = {
    "INTEG", "SMOOTH", "SMOOTH3", "DELAY1", "DELAY3", "DELAY", "DELAY FIXED",
    "IF", "THEN", "ELSE", "WITH", "LOOKUP", "ABS", "SQRT", "EXP", "LN",
    "MIN", "MAX", "MODULO", "PULSE", "RAMP", "STEP", "TIME", "TRUE", "FALSE",
    "INITIAL", "FINAL", "STEP", "SAVEPER",
}


def _name_pattern(name: str) -> str:
    """返回变量名匹配模式，兼容中文、空格和美元符号变量名。

    Python 的 ``\b`` 对中文和带空格变量名不稳定，因此使用显式的
    “变量字符”负向边界，避免短变量名误匹配到长变量名内部。
    """
    boundary_chars = r"A-Za-z0-9_\$\u4e00-\u9fff"
    return rf"(?<![{boundary_chars}]){re.escape(name)}(?![{boundary_chars}])"


def extract_deps(rhs: str, known_names: set) -> List[str]:
    """从表达式提取依赖的变量名（已知名集合内，支持带空格变量名）。"""
    deps: List[str] = []
    # 按长度降序匹配，避免短名前缀误匹配（如 "Birth" 匹配 "Birth Fraction"）
    sorted_names = sorted(known_names, key=lambda name: (-len(name), name))
    # 先移除函数名
    function_words = (
        r"INTEG|SMOOTH3?|DELAY[13]?|DELAY FIXED|IF THEN ELSE|WITH LOOKUP|"
        r"ABS|SQRT|EXP|LN|MIN|MAX|MODULO|PULSE|RAMP|STEP"
    )
    cleaned = re.sub(rf"\b({function_words})\b(?=\s*\()", " ", rhs, flags=re.I)
    # 用占位符逐个替换已知名，避免重叠匹配
    remaining = cleaned
    for name in sorted_names:
        pattern = _name_pattern(name)
        if re.search(pattern, remaining):
            deps.append(name)
            remaining = re.sub(pattern, " ", remaining)
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


def stock_initialization_order(equations: "OrderedDict[str, Equation]") -> List[str]:
    """按 INTEG 初值表达式中的库存依赖排序，避免使用临时 0 初值。"""
    all_names = set(equations.keys())
    stocks = {
        name for name, eq in equations.items()
        if eq.integ_flow is not None and eq.integ_init is None and eq.integ_init_expr
    }
    order: List[str] = []
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

    _PAIR_RE = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")

    def __init__(self, table):
        if isinstance(table, str):
            # 解析 Vensim 表字符串：( [0,0)-(100,1000)], (0,950), (20,800), ...
            # 先剥离范围声明 ( [x,y)-(x,y)]，再取 (x,y) 数值对
            body = re.sub(r"\(\s*\[.*?\)\s*\]", " ", table)
            pairs = [(float(x), float(y)) for x, y in self._PAIR_RE.findall(body)]
        else:
            pairs = list(table)
        if not pairs:
            raise ValueError("LOOKUP 表无有效坐标点")
        self.xs = [p[0] for p in pairs]
        self.ys = [p[1] for p in pairs]

    def __call__(self, x: float) -> float:
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

    def _helper(name: str) -> str:
        return HELPER_TOKENS[name]

    def _replace_calls(expr: str, function_name: str, handler) -> str:
        """替换函数调用，参数解析支持嵌套括号。"""
        out: List[str] = []
        lower = expr.lower()
        needle = function_name.lower()
        i = 0
        while i < len(expr):
            j = lower.find(needle, i)
            if j < 0:
                out.append(expr[i:])
                break
            before = expr[j - 1] if j > 0 else ""
            if before and (before.isalnum() or before == "_"):
                out.append(expr[i:j + 1])
                i = j + 1
                continue
            k = j + len(function_name)
            while k < len(expr) and expr[k].isspace():
                k += 1
            if k >= len(expr) or expr[k] != "(":
                out.append(expr[i:j + 1])
                i = j + 1
                continue
            end = _matching_paren(expr, k)
            args = _split_top_level_args(expr[k + 1:end])
            out.append(expr[i:j])
            out.append(handler(args, expr[j:end + 1]))
            i = end + 1
        return "".join(out)

    def _replace_with_lookup_calls(expr: str) -> str:
        out: List[str] = []
        lower = expr.lower()
        needle = "with lookup"
        i = 0
        while i < len(expr):
            j = lower.find(needle, i)
            if j < 0:
                out.append(expr[i:])
                break
            k = j + len(needle)
            while k < len(expr) and expr[k].isspace():
                k += 1
            if k >= len(expr) or expr[k] != "(":
                out.append(expr[i:j + 1])
                i = j + 1
                continue
            first_arg_end = None
            depth = 0
            for pos in range(k + 1, len(expr)):
                ch = expr[pos]
                if ch == "(":
                    depth += 1
                elif ch == ")" and depth:
                    depth -= 1
                elif ch == "," and depth == 0:
                    first_arg_end = pos
                    break
            if first_arg_end is None:
                raise ValueError(f"WITH LOOKUP 参数数量错误: {expr[j:j + 80]}")
            table_start = first_arg_end + 1
            while table_start < len(expr) and expr[table_start].isspace():
                table_start += 1
            end = _find_with_lookup_end(expr, table_start)
            x_arg = expr[k + 1:first_arg_end].strip()
            table = expr[table_start:end + 1].strip()
            out.append(expr[i:j])
            out.append(f"{_helper('_wl')}({_expr_arg(x_arg)}, {table!r})")
            i = end + 1
        return "".join(out)

    def _expr_arg(arg: str) -> str:
        return _to_python_expr(arg, name_map)

    def _if_handler(args: List[str], raw: str) -> str:
        if len(args) != 3:
            raise ValueError(f"IF THEN ELSE 参数数量错误: {raw}")
        return f"(({_expr_arg(args[1])}) if ({_expr_arg(args[0])}) else ({_expr_arg(args[2])}))"

    def _binary_handler(op: str):
        def handler(args: List[str], raw: str) -> str:
            if len(args) != 2:
                raise ValueError(f"{raw} 参数数量错误")
            return f"({_expr_arg(args[0])} {op} {_expr_arg(args[1])})"
        return handler

    def _three_arg_guard_handler(kind: str):
        def handler(args: List[str], raw: str) -> str:
            if len(args) != 3:
                raise ValueError(f"{kind} 参数数量错误: {raw}")
            num = _expr_arg(args[0])
            den = _expr_arg(args[1])
            fallback = _expr_arg(args[2])
            return f"(({num})/({den}) if {_helper('_sd_float')}({den})!=0 else ({fallback}))"
        return handler

    def _time_func_handler(name: str):
        def handler(args: List[str], raw: str) -> str:
            if len(args) != 2:
                raise ValueError(f"{name} 参数数量错误: {raw}")
            return f"{_helper('_' + name.lower())}({_expr_arg(args[0])}, {_expr_arg(args[1])})"
        return handler

    def _ramp_handler(args: List[str], raw: str) -> str:
        if len(args) not in (2, 3):
            raise ValueError(f"RAMP 参数数量错误: {raw}")
        converted = [_expr_arg(arg) for arg in args]
        return f"{_helper('_ramp')}({', '.join(converted)})"

    def _delay_fixed_handler(args: List[str], raw: str) -> str:
        if len(args) != 3:
            raise ValueError(f"DELAY FIXED 参数数量错误: {raw}")
        key = hashlib.blake2s(raw.encode("utf-8"), digest_size=6).hexdigest()
        return f"{_helper('_delay_fixed')}('{key}', {_expr_arg(args[0])}, {_expr_arg(args[1])}, {_expr_arg(args[2])})"

    s = _replace_calls(s, "IF THEN ELSE", _if_handler)
    s = _replace_with_lookup_calls(s)
    s = _replace_calls(s, "DELAY FIXED", _delay_fixed_handler)
    s = _replace_calls(s, "MODULO", _binary_handler("%"))
    s = _replace_calls(s, "XIDZ", _three_arg_guard_handler("XIDZ"))
    s = _replace_calls(s, "ZIDZ", _three_arg_guard_handler("ZIDZ"))
    s = _replace_calls(s, "PULSE", _time_func_handler("pulse"))
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
    for orig, alias in sorted(name_map.items(), key=lambda x: -len(x[0])):
        s = re.sub(_name_pattern(orig), alias, s)
    for helper_name, token in HELPER_TOKENS.items():
        s = s.replace(token, helper_name)
    return s


_ALLOWED_MATH_ATTRS = {
    "sqrt", "exp", "log", "sin", "cos", "tan",
}


def _safe_eval_node(node: ast.AST, namespace: Dict) -> float:
    """求值受限 Python 表达式 AST，仅允许数学表达式和白名单函数。"""
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body, namespace)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            if len(node.value) > 128:
                raise ValueError("字符串常量过长")
            return node.value
        if isinstance(node.value, (int, float, bool)):
            return node.value
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
            return _ensure_number(left ** right, "幂运算结果")
        raise ValueError("不支持的二元运算")
    if isinstance(node, ast.BoolOp):
        values = [_safe_eval_node(v, namespace) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        raise ValueError("不支持的布尔运算")
    if isinstance(node, ast.Compare):
        left = _ensure_number(_safe_eval_node(node.left, namespace), "比较左值")
        for op, comparator in zip(node.ops, node.comparators):
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
        return _safe_eval_node(node.body if _safe_eval_node(node.test, namespace) else node.orelse, namespace)
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
        return func(*args)
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


def _safe_eval_expr(py_expr: str, namespace: Dict) -> float:
    if len(py_expr) > MAX_EXPR_CHARS:
        raise ValueError("表达式过长")
    tree = ast.parse(py_expr, mode="eval")
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        raise ValueError("表达式节点过多")
    if _ast_depth(tree) > MAX_AST_DEPTH:
        raise ValueError("表达式嵌套过深")
    return _safe_eval_node(tree, namespace)


def evaluate(rhs: str, ctx: Dict, lookups: Dict, name_map: Dict[str, str]) -> float:
    """在上下文 ctx 中求值 rhs；name_map 把带空格变量名映射为合法标识符。"""
    integ_args = _function_args(rhs, "INTEG")
    if integ_args:
        expr = integ_args[0]
    elif rhs.strip().startswith("["):
        raise ValueError("LOOKUP 表不能直接作为标量求值")
    else:
        expr = rhs

    py_expr = _to_python_expr(expr, name_map)

    def _wl(x, table):
        return LookupTable(table)(x)

    def _pulse(start, duration):
        t = ctx.get("Time", 0.0)
        return 1.0 if start <= t < start + duration else 0.0

    def _ramp(slope, start, end=None):
        t = ctx.get("Time", 0.0)
        if t < start:
            return 0.0
        if end is not None and t > end:
            return slope * (end - start)
        return slope * (t - start)

    def _step(value, time):
        t = ctx.get("Time", 0.0)
        return 0.0 if t < time else value

    def _delay_fixed(key, value, delay_time, initial_value):
        t = float(ctx.get("Time", 0.0))
        delay = float(delay_time)
        histories = ctx.setdefault("__delay_fixed_history__", {})
        hist = histories.setdefault(key, [])
        if not hist or abs(hist[-1][0] - t) > 1e-9:
            hist.append((t, float(value)))
        target = t - delay
        if target < hist[0][0] - 1e-9:
            return float(initial_value)
        before = None
        after = None
        for point in hist:
            if point[0] <= target + 1e-9:
                before = point
            if point[0] >= target - 1e-9:
                after = point
                break
        if before is None:
            return float(initial_value)
        if after is None or abs(after[0] - before[0]) < 1e-9:
            return before[1]
        ratio = (target - before[0]) / (after[0] - before[0])
        return before[1] + (after[1] - before[1]) * ratio

    namespace = {
        "math": math, "_wl": _wl,
        "_sd_abs": abs, "_sd_min": min, "_sd_max": max,
        "_sd_sqrt": math.sqrt, "_sd_exp": math.exp, "_sd_log": math.log,
        "_sd_sin": math.sin, "_sd_cos": math.cos, "_sd_tan": math.tan,
        "_sd_int": int, "_sd_float": float,
        "_pulse": _pulse, "_ramp": _ramp, "_step": _step,
        "_delay_fixed": _delay_fixed,
    }
    # 注入 lookup 变量为可调用对象
    for k, v in lookups.items():
        namespace[name_map.get(k, k)] = v
    # 注入变量值（用别名）
    for k, v in ctx.items():
        namespace[name_map.get(k, k)] = v
    try:
        return float(_safe_eval_expr(py_expr, namespace))
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
    for label, ratio in (("仿真区间/TIME STEP", (tf - t0) / dt), ("SAVEPER/TIME STEP", saveper / dt)):
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
    equations: "OrderedDict[str, Equation]",
    t0: float,
    tf: float,
    dt: float,
    saveper: Optional[float] = None,
    strict: bool = True,
) -> SimResult:
    """Euler 积分仿真。"""
    saveper = dt if saveper is None else saveper
    _validate_time_settings(t0, tf, dt, saveper)
    order = topological_sort(equations)

    # 构建变量名到合法 Python 标识符的映射（带空格/特殊字符的变量名）
    name_map: Dict[str, str] = {}
    for i, name in enumerate(equations.keys()):
        if re.fullmatch(r"[A-Za-z_]\w*", name) and name not in RESERVED_EVAL_NAMES:
            name_map[name] = name
        else:
            name_map[name] = f"_v{i}"

    # 递归求初值依赖；库存的流率依赖不参与初始化，避免用临时零值污染辅助量。
    ctx: Dict = {"Time": t0}
    lookups = {name: LookupTable(eq.lookup_pairs) for name, eq in equations.items() if eq.is_lookup}
    initializing = set()
    eval_warnings = []

    def initial_value(name):
        if name in ctx:
            return ctx[name]
        if name in initializing:
            raise ValueError(f"检测到初值循环依赖: {name}")
        initializing.add(name)
        eq = equations[name]
        if eq.is_lookup:
            ctx[name] = 0.0
        else:
            expression = (eq.integ_init_expr or str(eq.integ_init or 0)) if eq.integ_flow is not None else eq.rhs
            for dependency in extract_deps(expression, set(equations)):
                initial_value(dependency)
            try:
                ctx[name] = evaluate(expression, ctx, lookups, name_map)
            except ValueError as exc:
                if strict:
                    raise ValueError(f"初始化 {name} 失败: {exc}") from exc
                ctx[name] = 0.0
                eval_warnings.append(f"初始化 {name}: {exc}")
        initializing.remove(name)
        return ctx[name]

    for name in equations:
        initial_value(name)

    times: List[float] = []
    series: Dict[str, List[float]] = {n: [] for n in equations}
    steps = round((tf - t0) / dt)
    save_steps = round(saveper / dt)
    for step in range(steps + 1):
        t = t0 + step * dt
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
                message = f"{name} @t={t:.2f}: {exc}"
                if strict:
                    raise ValueError(message) from exc
                ctx[name] = 0.0
                if not eval_warnings:
                    eval_warnings.append(message)
        # 计算库存的流率（用当前辅助值）
        flows: Dict[str, float] = {}
        for name, eq in equations.items():
            if eq.integ_flow is not None:
                try:
                    flows[name] = evaluate(eq.integ_flow, ctx, lookups, name_map)
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

        # Euler 更新库存
        for name, eq in equations.items():
            if eq.integ_flow is not None and step < steps:
                ctx[name] = _ensure_number(ctx[name] + flows[name] * dt, f"库存 {name} @t={t + dt}")

    if eval_warnings:
        sys.stderr.write("警告: 部分变量求值失败（已按兼容模式置零），首条: " + eval_warnings[0] + "\n")
        sys.stderr.write(f"      共 {len(eval_warnings)} 类求值失败，可能导致 nodata 或曲线为 0。\n")

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
    context = {name: _resolve_number(equations[name].rhs, equations, visited) for name in dependencies}
    mapping = {name: f"_control_{index}" for index, name in enumerate(names)}
    return evaluate(rhs, context, {}, mapping)


def get_time_bounds(equations: "OrderedDict[str, Equation]"):
    controls = ("INITIAL TIME", "FINAL TIME", "TIME STEP", "SAVEPER")
    missing = [name for name in controls if name not in equations]
    if missing:
        raise ValueError("模型必须明确时间设置，缺少: " + ", ".join(missing))
    return tuple(_resolve_number(equations[name].rhs, equations) for name in controls)


def _ensure_output_separate(output: Path, inputs: List[Path]) -> None:
    separate_output(output, inputs)


def command_simulate(path: Path, output: Path, variables: List[str],
                     plot: Optional[Path] = None, strict: bool = True, dpi=600, formats=None, plot_config=None, **options) -> int:
    from simulation_runner import run_model
    _ensure_output_separate(output, [path, *([plot_config] if plot_config else [])])
    metadata = output.with_suffix(output.suffix + ".run.json")
    _ensure_output_separate(metadata, [path])
    if plot:
        _ensure_output_separate(plot, [path, output, metadata])
    result = run_model(path, variables, strict=strict, **options)
    variables = list(result.series)
    from experiments import csv_bytes
    rows = [[t, *[result.series[name][index] for name in variables]] for index, t in enumerate(result.times)]
    atomic_write(output, csv_bytes(["Time", *variables], rows))
    atomic_write(metadata, json.dumps(result.metadata, ensure_ascii=False, indent=2).encode())
    if plot:
        _render_plot(result, variables, plot, dpi=dpi, formats=formats, inputs=[path, output, metadata], plot_config=plot_config)
    print(f"仿真完成: {len(result.times)} 个时间点 -> {output}")
    return 0


def _render_plot(result, variables: List[str], output: Path, title: str = "", **options):
    from result_plotting import export_figures
    return export_figures([("current", result)], variables, output, title=title, **options)


def command_graph(path: Path, variables: List[str], output: Path, title: str, strict: bool = True, dpi=600, formats=None, plot_config=None, **options) -> int:
    from simulation_runner import run_model
    _ensure_output_separate(output, [path])
    result = run_model(path, variables, strict=strict, **options)
    _render_plot(result, list(result.series), output, title=title, dpi=dpi, formats=formats, inputs=[path], plot_config=plot_config)
    print(f"折线图已导出: {output}")
    return 0


def command_compare(base: Path, scenarios: List[Path], variables: List[str],
                    output: Path, title: str, strict: bool = True, dpi=600, formats=None, plot_config=None, **options) -> int:
    from simulation_runner import run_model
    from experiments import plot_experiment
    _ensure_output_separate(output, [base, *scenarios])
    if not variables:
        raise ValueError("至少指定一个变量 --var")
    results = [(model.stem, run_model(model, variables, strict=strict, **options)) for model in [base, *scenarios]]
    plot_experiment(results, variables, output, title=title, dpi=dpi, formats=formats, inputs=[base, *scenarios], plot_config=plot_config)
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

    # 5. 内置引擎预检初值与实际表达式，不能只在已知名称集合中寻找未知名称。
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

    if errors == 0:
        print("\nPASS: 未发现错误。")
    else:
        print(f"\nFAIL: {errors} 个错误。")
    print(f"Warnings: {warnings}")
    return 1 if errors else 0


def command_fix(path: Path, output: Path, units_map=None, drop_broken=False) -> int:
    _ensure_output_separate(output, [path, *([units_map] if units_map else [])])
    document = MdlDocument.read(path)
    text = document.text
    equations = parse_equations(document.semantic_text, expand=False)
    fixes = []
    if not units_map and not drop_broken:
        raise ValueError("需要显式指定 --units-map 或 --drop-broken-arrows；缺失单位不能自动猜成 Dmnl")
    if units_map:
        mapping = json.loads(units_map.read_text(encoding="utf-8-sig"))
        for name, unit in mapping.items():
            if name not in equations or not isinstance(unit, str) or not unit.strip() or any(c in unit for c in "~|\n\r"):
                raise ValueError(f"无效单位映射: {name}")
            if equations[name].unit:
                raise ValueError(f"{name} 已有单位，不能通过缺失单位修复覆盖")
            pattern = rf'(?m)(^[ \t]*"?{re.escape(name)}"?\s*=[^~|]*~)[ \t\r\n]*(?=[~|])'
            text, count = re.subn(pattern, lambda match: match[1] + " " + unit.strip() + "\n\t", text, count=1)
            if count != 1:
                raise ValueError(f"无法安全定位单位字段: {name}")
            fixes.append(f"补充用户提供的单位: {name} = {unit}")
    if drop_broken:
        from vensim_autolayout import parse_views
        lines = text.splitlines(keepends=True)
        broken = {arrow.line_index for view in parse_views(lines) for arrow in view.arrows
                  if arrow.from_id not in view.objects or arrow.to_id not in view.objects}
        text = "".join(line for index, line in enumerate(lines) if index not in broken)
        fixes.append(f"按显式选项删除 {len(broken)} 条断裂草图箭头")
    atomic_write(output, text.encode(document.encoding, errors="surrogateescape"))
    print(json.dumps({"output": str(output), "fixes": fixes}, ensure_ascii=False, indent=2))
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
    p_sim.add_argument("--keep-going", action="store_true", help="求值失败时继续输出，并把失败变量置零。")

    p_graph = sub.add_parser("graph", help="仿真并导出折线图 PNG")
    p_graph.add_argument("model", type=Path)
    p_graph.add_argument("--var", action="append", default=[], help="绘图变量(可多次，缺省全部)")
    p_graph.add_argument("--output", required=True, type=Path)
    p_graph.add_argument("--title", default="")
    p_graph.add_argument("--keep-going", action="store_true", help="求值失败时继续输出，并把失败变量置零。")

    p_cmp = sub.add_parser("compare", help="多场景对比图")
    p_cmp.add_argument("base", type=Path)
    p_cmp.add_argument("--scenario", action="append", default=[], type=Path)
    p_cmp.add_argument("--var", action="append", required=True)
    p_cmp.add_argument("--output", required=True, type=Path)
    p_cmp.add_argument("--title", default="")
    p_cmp.add_argument("--keep-going", action="store_true", help="求值失败时继续输出，并把失败变量置零。")

    p_units = sub.add_parser("units", help="单位校验")
    p_units.add_argument("model", type=Path)

    p_check = sub.add_parser("check", help="全面检查")
    p_check.add_argument("model", type=Path)

    p_fix = sub.add_parser("fix", help="自动修复")
    p_fix.add_argument("model", type=Path)
    p_fix.add_argument("--output", required=True, type=Path)
    p_fix.add_argument("--units-map", type=Path)
    p_fix.add_argument("--drop-broken-arrows", action="store_true")

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
        options = {"backend": args.backend, "params": parse_overrides(args.parameters),
                   "time_step": args.time_step, "final_time": args.final_time, "saveper": args.saveper, "dpi": args.dpi, "formats": args.formats, "plot_config": args.plot_config}
    if args.command == "simulate":
        return command_simulate(args.model, args.output, args.var, getattr(args, "plot", None), not args.keep_going, **options)
    if args.command == "graph":
        return command_graph(args.model, args.var, args.output, args.title, not args.keep_going, **options)
    if args.command == "compare":
        return command_compare(args.base, args.scenario, args.var, args.output, args.title, not args.keep_going, **options)
    if args.command == "units":
        return command_units(args.model)
    if args.command == "check":
        return command_check(args.model)
    if args.command == "fix":
        return command_fix(args.model, args.output, args.units_map, args.drop_broken_arrows)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
