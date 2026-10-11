"""骨架优先的自然排版（natural 模式）。

流程：
  1. 识别存量—流量骨架：阀门、上下游存量或云、附着流量名。简单链条按水平
     骨架重建（云—阀门 80px、阀门—存量中心不少于 120px），跳过中间存量的
     流量画成绕行支路或直角折线管道，首尾相连的存量环排成矩形，其余整体保留；
  2. 多条链按原图纵向顺序成行，行距不小于 200px，横向按跨行连接对齐；
  3. 辅助变量靠近直接作用对象逐个落位，初值放在存量正上/下方，无骨架模块
     按计算顺序分层并紧凑装箱；
  4. 模拟退火在初始拓扑附近做局部调整（移动、交换、流量名翻面、整行平移），
     代价包含交叉、穿管、穿字、重叠、小夹角、过短箭头、<Time> 预留区与偏离；
  5. 信息箭头统一柔和圆弧（见 arc_routing），回路标记放回所在回路中间，
     最后用 visual 同口径几何验收。

只改坐标、存量/云的显示尺寸、云图标代码和箭头控制点；方程区逐字节保留。
split_shadows（默认开启）时，结构上不可平面的交叉用影子消除：复制已有变量
的影子引用，把个别箭头起点改到影子上，因果关系不变，报告中逐条列出。
"""

from __future__ import annotations

import dataclasses
import math
import random
import re
from collections import Counter, defaultdict

from arc_routing import arc_ratio, route_arcs
from sketch_geometry import (
    LAYOUT_CLEARANCE,
    NATIVE_CLOUD_CODE,
    PREFERRED_ARROW,
    TIME_LABEL_HALF_HEIGHT,
    TIME_LABEL_HALF_WIDTH,
    TIME_LABEL_OFFSET,
    arrow_path,
    box,
    boxes_intersect,
    half_height,
    is_cloud_candidate,
    measure_view,
    path_hits_box,
    pipe_endpoint_ids,
    quality_key,
    shown,
    text_width,
    time_label_box,
    time_label_owners,
    visible,
    wrap_label,
)

# ---------------------------------------------------------------------------
# 排版常量（像素；只描述版面，不代表任何业务数据）
# ---------------------------------------------------------------------------

GRID = 10
CLOUD_TO_VALVE = 80  # 云中心到阀门中心（Vensim 官方示例中位数约 76）
VALVE_TO_STOCK = 120  # 阀门中心到存量中心的下限（官方示例中位数约 124）
LABEL_OFFSET = 30  # 水平管道上流量名中心到阀门中心的纵向距离
BRANCH_VALVE_GAP = 76  # 竖向支流：存量边缘到阀门中心
DETOUR_INNER_GAP = 90  # 绕行支路存量与主干存量之间的净空（Duggan 教材原图约 93）
ROW_SPACING = 200  # 相邻存量链中心线的最小间距（教材模型中位数约 180）
ROW_CLEAR = 100  # 相邻两行流量名外缘之间至少留出的净空，放一行辅助变量
ROW_MARGIN = 20  # 重建链条的上下外扩量，给 <Time> 与流量名留位置
STOCK_MIN_HALF = (48, 24)  # 存量框最小半宽、半高（官方示例中位数约 52×28）
CLOUD_HALF = (10, 8)
SHADOW_MARGIN = 6  # 影子尖括号额外宽度，与 sketch_geometry.box 一致

# 退火代价权重：先保证零交叉、零穿字、零重叠，再追求短而均匀的连线。
W_CROSS = 1000.0
W_PIPE_CROSS = 1200.0
W_HIT = 1500.0
W_TIME = 1500.0
W_OVER = 3000.0
W_SHORT = 1500.0
W_NARROW = 300.0
W_LEN = 0.3
W_DIR = 0.15
W_BOUND = 20.0
W_SNAP = 0.6
TIME_SIDE_OFFSET = 140  # 跨行连线指向 <Time> 所有者时的横向错开量
SNAP_RANGE = 30.0
BOUND_MARGIN = 90.0
ARROW_CLEAR = 7.0  # 直线近似比检查器（6px）多留 1px，给圆弧留余量
GAP_X, GAP_Y = 14.0, 10.0  # 文字框之间的最小净距
NARROW_ANGLE = math.radians(20)
# 过短判断按目标弧度 k=0.12 估计可见长度：两端切线偏离弦约 26°，宽而扁的
# 文字框会从上下边离开，可见部分比直线裁剪更长
SHORT_LIMIT = PREFERRED_ARROW
ARC_END_TURN = math.atan(4 * 0.12)
SEED_ATTEMPTS = 3  # 仍有缺陷时换种子重跑的总次数
LARGE_VIEW = 80  # 对象数超过此值的视图减少重跑、打磨与拆分试验，控制耗时
ROW_ORDER_PENALTY = 400.0  # 存量链上下顺序与原图相反时的代价，缺陷相同时保留作者的行序
POLISH_ROUNDS = 4  # 收尾阶段温热退火的最多轮数
# 影子拆分每轮的搜索预算：候选连线数、局部退火迭代、整体复核的试验数与迭代
SPLIT_CANDIDATES = 8
SPLIT_LOCAL_ITERS = 2500
SPLIT_SETTLE = 2
SPLIT_SETTLE_ITERS = 9000


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def snap(value: float, grid: int = GRID) -> int:
    return int(round(value / grid) * grid)


def view_font_size(lines, view) -> int:
    """读取视图默认字号（$...,字体|字号|...），缺省 12。"""
    for index in range(view.start + 1, min(view.start + 5, view.end)):
        line = lines[index]
        if line.startswith("$"):
            match = re.search(r"\|(\d+)\|", line)
            if match:
                return int(match.group(1))
    return 12


def text_half_width(name: str, font_size: int = 12) -> float:
    """单行文字半宽的估计值。"""
    return text_width(name.strip('"'), font_size) / 2


def stock_half_size(name: str, font_size: int = 12, scale: float = 1.0) -> tuple[int, int]:
    """存量框半宽、半高。

    Vensim 在存量框内自动折行，教材和官方示例的长名存量都是框宽不变、名字折成
    两行（如 Teacup Temperature 用 80×40 的框）。这里从最小框起逐步加宽，直到
    名字在左右各留 8px 后最多两行、每行都放得下，不为一行写完长名把框拉成长条。
    """
    pad = 8 * scale
    label = name.strip('"')
    half = STOCK_MIN_HALF[0] * scale
    while True:
        rows = wrap_label(label, half - pad, font_size)
        if len(rows) <= 2 and max(text_width(r, font_size) for r in rows) <= 2 * (half - pad):
            return int(math.ceil(half)), int(STOCK_MIN_HALF[1] * scale)
        half += GRID / 2


def attached_label_map(view) -> dict[int, int]:
    """阀门 → 附着流量名（原生记录中流量名紧跟在阀门之后，形状位 32）。"""
    ordered = sorted(view.objects.values(), key=lambda obj: obj.line_index)
    return {
        previous.obj_id: current.obj_id
        for previous, current in zip(ordered, ordered[1:], strict=False)
        if previous.kind == 11 and current.kind == 10 and current.attached_to_valve
    }


# ---------------------------------------------------------------------------
# 骨架识别
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Flow:
    valve: int
    label: int | None
    upstream: int | None = None
    downstream: int | None = None
    pipes: list = dataclasses.field(default_factory=list)
    regular: bool = True


@dataclasses.dataclass
class Chain:
    """一条存量—流量链（或无法重建时整体保留的骨架块）。"""

    members: list[int]
    local: dict[int, tuple[float, float]]
    label_valve: dict[int, int]
    label_options: dict[int, list[tuple[float, float]]]
    rebuilt: bool
    sort_key: tuple
    stocks: list[int] = dataclasses.field(default_factory=list)
    # 流量名默认侧别（label_options 的下标）；存量环的下排默认放在管道下方
    label_default: dict[int, int] = dataclasses.field(default_factory=dict)
    # 画成直角折线的跨越流量（阀门编号）：两段管道各在端点正上（下）方拐一次
    bends: set = dataclasses.field(default_factory=set)


def extract_flows(view, hidden=frozenset()):
    """按管道识别流量：阀门、上游端、下游端与附着流量名。

    兼容两种原生写法：两段管道都从阀门出发（流向源头的一段带 64 位，常见值
    100），或按流向书写（端点→阀门为上游）。同侧出现两段管道的阀门视为
    不规则，整块保留原几何。
    """
    labels = attached_label_map(view)
    flows: dict[int, Flow] = {}
    for arrow in view.arrows:
        if not arrow.is_physical_flow or arrow.obj_id in hidden:
            continue
        src, dst = view.objects.get(arrow.from_id), view.objects.get(arrow.to_id)
        if src is None or dst is None:
            continue
        if src.kind == 11 and dst.kind != 11:
            valve, end, upstream = src, dst, bool(arrow.shape & 64)
        elif dst.kind == 11 and src.kind != 11:
            valve, end, upstream = dst, src, True
        else:
            continue
        flow = flows.setdefault(valve.obj_id, Flow(valve.obj_id, labels.get(valve.obj_id)))
        flow.pipes.append(arrow.obj_id)
        endpoint_ok = end.kind == 12 or (end.kind == 10 and end.stock_like and not end.is_shadow)
        if not endpoint_ok:
            flow.regular = False
        slot = "upstream" if upstream else "downstream"
        if getattr(flow, slot) is not None:
            flow.regular = False
        else:
            setattr(flow, slot, end.obj_id)
    return flows


def _longest_path(flows):
    """在端点有向图上找最长简单路径（按流量数），作为水平主干。"""
    out = defaultdict(list)
    indegree = Counter()
    nodes = set()
    for flow in flows:
        up = flow.upstream if flow.upstream is not None else ("src", flow.valve)
        down = flow.downstream if flow.downstream is not None else ("dst", flow.valve)
        out[up].append((flow, down))
        indegree[down] += 1
        nodes.update((up, down))
    starts = sorted((n for n in nodes if indegree[n] == 0), key=str) or sorted(nodes, key=str)
    best: list = []
    budget = [20000]

    def visit(node, seen, path):
        nonlocal best
        budget[0] -= 1
        if budget[0] < 0:
            return
        if len(path) > len(best):
            best = list(path)
        for flow, nxt in out[node]:
            if nxt in seen:
                continue
            seen.add(nxt)
            path.append(flow)
            visit(nxt, seen, path)
            path.pop()
            seen.remove(nxt)

    for start in starts:
        visit(start, {start}, [])
    return best


def build_chains(view, flows, half, label_half, config):
    """把骨架组件转换为链：简单链条水平重建，复杂结构整体保留原相对几何。"""
    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for flow in flows.values():
        find(flow.valve)
        for end in (flow.upstream, flow.downstream):
            if end is not None:
                union(flow.valve, end)
    groups = defaultdict(list)
    for flow in flows.values():
        groups[find(flow.valve)].append(flow)

    locked = set(config.get("_locked_ids", ()))
    chains = []
    for comp_flows in groups.values():
        comp_flows.sort(key=lambda f: f.valve)
        members = []
        for flow in comp_flows:
            members.append(flow.valve)
            if flow.label is not None:
                members.append(flow.label)
            members.extend(e for e in (flow.upstream, flow.downstream) if e is not None)
        members = list(dict.fromkeys(members))
        objs = [view.objects[m] for m in members]
        sort_key = (
            sum(o.y for o in objs) / len(objs),
            sum(o.x for o in objs) / len(objs),
        )
        stocks = [
            m
            for m in members
            if view.objects[m].kind == 10
            and view.objects[m].stock_like
            and m not in {f.label for f in comp_flows}
        ]
        chain = None
        if (
            all(f.regular for f in comp_flows)
            and len(comp_flows) <= 40
            and not locked.intersection(members)
            and not config.get("_preserve_skeleton")
        ):
            chain = _rebuild_chain(view, comp_flows, members, half, label_half, sort_key, stocks)
            if chain is None:
                chain = _rebuild_ring(view, comp_flows, members, half, label_half, sort_key, stocks)
        if chain is None:
            # 不规则或被锁定：保持原相对几何，整体参与成行排列。
            base = view.objects[members[0]]
            chain = Chain(
                members=members,
                local={
                    m: (view.objects[m].x - base.x, view.objects[m].y - base.y) for m in members
                },
                label_valve={f.label: f.valve for f in comp_flows if f.label is not None},
                label_options={},
                rebuilt=False,
                sort_key=sort_key,
                stocks=stocks,
            )
        chains.append(chain)
    chains.sort(key=lambda c: c.sort_key)
    return chains


def _gap(view, end, flow, half, label_half):
    """端点中心到阀门中心的距离：云 100；存量至少 150，并为流量名和过短箭头留白。"""
    if view.objects[end].kind == 12:
        return CLOUD_TO_VALVE
    stock_w = half[end][0]
    label_w = label_half.get(flow.label, (0, 0))[0]
    return max(VALVE_TO_STOCK, int(math.ceil((stock_w + label_w + 44) / GRID)) * GRID)


def _plan_skips(view, path, comp_flows, half):
    """处理一端在主干上游、一端在主干下游的跨越流量。

    例如 一年级 →(留级)→ 留级存量 →(返回)→ 二年级 与 一年级 →(升级)→ 二年级 并存。
    两种画法都按原图决定：
    - 绕行：直接流量留在主干上，中间存量画到主干上方或下方（Duggan 学生流动模型）；
    - 跨越：中间存量留在主干上，直接流量画成从上方或下方绕过的直角折线管道
      （Duggan SIR 政策模型的接种流）。原图里该流量阀门不在主干行上、
      被跨过的存量都在行上时用跨越，其余情况（含新建模型）用绕行。
    返回 (主干, 绕行列表, 跨越列表)；无法用直管道或单拐角表达时返回 None。
    """
    objects = view.objects
    path = list(path)
    detours, bypasses = [], []
    while True:
        nodes = [path[0].upstream] + [f.downstream for f in path]
        index = {n: i for i, n in enumerate(nodes) if n is not None}
        handled = {f.valve for f in path} | {f.valve for f, _, _ in bypasses}
        pick = None
        for flow in comp_flows:
            if flow.valve in handled or flow.upstream not in index or flow.downstream not in index:
                continue
            i, j = index[flow.upstream], index[flow.downstream]
            if j - i < 2:
                # 相邻存量之间的并行流量或回流，无法用直管道表达
                return None
            pick = (flow, i, j)
            break
        if pick is None:
            break
        flow, i, j = pick
        row_y = objects[nodes[i]].y
        reach = max(half[n][1] for n in nodes[i : j + 1])
        inline = all(abs(objects[n].y - row_y) <= reach for n in nodes[i + 1 : j + 1])
        if inline and abs(objects[flow.valve].y - row_y) > reach:
            bypasses.append((flow, nodes[i], nodes[j]))
        else:
            detours.append((nodes[i], nodes[j], path[i:j]))
            path = path[:i] + [flow] + path[j:]
    main = {path[0].upstream} | {f.downstream for f in path}
    for start, end, _ in detours:
        if start not in main or end not in main:
            return None
    for _, start, end in bypasses:
        if start not in main or end not in main:
            return None
    return path, detours, bypasses


def _rebuild_chain(view, comp_flows, members, half, label_half, sort_key, stocks):
    path = _longest_path(comp_flows)
    if not path:
        return None
    plan = _plan_skips(view, path, comp_flows, half)
    if plan is None:
        return None
    path, detours, bypasses = plan
    original = view.objects
    # 绕行段需要的横向跨度：首尾阀门分别在起点、终点正上（下）方，中间存量依次排开
    need_span = {}
    for start, end, segment in detours:
        span = 0.0
        for k, flow in enumerate(segment):
            if k > 0:
                span += _gap(view, flow.upstream, flow, half, {})
            if k < len(segment) - 1:
                span += _gap(view, flow.downstream, flow, half, {})
        need_span[(start, end)] = span
    local: dict[int, tuple[float, float]] = {}
    label_valve, label_options, label_default = {}, {}, {}
    x = 0.0
    first = path[0]
    if first.upstream is not None:
        local[first.upstream] = (0.0, 0.0)
    for flow in path:
        if flow.upstream is not None and flow.upstream not in local:
            return None
        gap_up = (
            _gap(view, flow.upstream, flow, half, label_half) if flow.upstream is not None else 0
        )
        gap_down = (
            _gap(view, flow.downstream, flow, half, label_half)
            if flow.downstream is not None
            else 0
        )
        extra = max(0.0, need_span.get((flow.upstream, flow.downstream), 0.0) - gap_up - gap_down)
        gap_up += snap(extra / 2)
        gap_down += snap(extra / 2)
        vx = (local[flow.upstream][0] if flow.upstream is not None else x) + gap_up
        local[flow.valve] = (vx, 0.0)
        x = vx
        if flow.downstream is not None:
            if flow.downstream in local:
                return None
            x = vx + gap_down
            local[flow.downstream] = (x, 0.0)
        if flow.label is not None:
            label_valve[flow.label] = flow.valve
            label_options[flow.label] = [(0.0, -LABEL_OFFSET), (0.0, LABEL_OFFSET)]
            local[flow.label] = (vx, -LABEL_OFFSET)
    slots: dict[int, set] = defaultdict(set)
    spans = []  # 已占用的横向区间 (左, 右, 侧别)，绕行与跨越不能在同一侧重叠
    for start, end, segment in detours:
        # 绕行放在原图中间存量所在一侧，该侧已被占用时换到另一侧
        inner = [f.downstream for f in segment[:-1]]
        mean_y = sum(original[s].y for s in inner) / len(inner)
        prefer = -1 if mean_y <= original[start].y else 1
        side = prefer if prefer not in slots[start] | slots[end] else -prefer
        if side in slots[start] or side in slots[end]:
            return None
        slots[start].add(side)
        slots[end].add(side)
        (sx, _), (ex, _) = local[start], local[end]
        spans.append((sx, ex, side))
        # 绕行与主干之间留出一行变量的高度，影响两条通路的参数可以放在中间
        reach = max(half[n][1] for n in (start, end, *inner))
        y = side * snap(2 * reach + DETOUR_INNER_GAP)
        # 中间存量按需要的间距排开，富余宽度平均分给各段
        steps = []
        for k, flow in enumerate(segment):
            before = _gap(view, flow.upstream, flow, half, {}) if k > 0 else 0
            after = _gap(view, flow.downstream, flow, half, {}) if k < len(segment) - 1 else 0
            steps.append((before, after))
        slack = (ex - sx - need_span[(start, end)]) / (2 * len(segment) - 2)
        cursor = sx
        for k, flow in enumerate(segment):
            before, after = steps[k]
            if k > 0:
                cursor += before + slack
            local[flow.valve] = (cursor, y)
            if flow.label is not None:
                # 首尾阀门在拐角处：名字可放外侧、横向外侧或两段管道夹角内侧；
                # 中间阀门的名字放在管道上方或下方
                label_w = label_half.get(flow.label, (40, 16))[0]
                outward = (0.0, side * LABEL_OFFSET)
                inner = (label_w + 10, -side * (reach + 16))
                if k == 0:
                    options = [outward, (-(label_w + 18), 0.0), inner]
                elif k == len(segment) - 1:
                    options = [outward, (label_w + 18, 0.0), (-inner[0], inner[1])]
                else:
                    options = [outward, (0.0, -side * LABEL_OFFSET)]
                # 拐角阀门默认放在夹角内侧，靠近两条通路共用的参数（与 Duggan 原图一致）
                default = 2 if len(options) == 3 else 0
                label_valve[flow.label] = flow.valve
                label_options[flow.label] = options
                label_default[flow.label] = default
                local[flow.label] = (cursor + options[default][0], y + options[default][1])
            if k < len(segment) - 1:
                cursor += after + slack
                local[flow.downstream] = (cursor, y)
        # 主干上被绕行跨过的流量名放到另一侧，不夹在绕行里面
        for flow in path:
            if (flow.upstream, flow.downstream) == (start, end) and flow.label is not None:
                label_default[flow.label] = 0 if side > 0 else 1
                dx, dy = label_options[flow.label][label_default[flow.label]]
                local[flow.label] = (local[flow.valve][0] + dx, dy)
    order = {n: i for i, n in enumerate([path[0].upstream] + [f.downstream for f in path])}
    bends = set()
    for flow, start, end in bypasses:
        # 跨越流量：阀门在两端中点的上方或下方，两段管道各拐一次进入端点
        inner = [n for n, i in order.items() if order[start] < i < order[end] and n is not None]
        prefer = -1 if original[flow.valve].y < original[start].y else 1
        (sx, _), (ex, _) = local[start], local[end]

        around = (start, end, *inner)

        def blocked(side, around=around, sx=sx, ex=ex):
            return any(side in slots[n] for n in around) or any(
                sd == side and x0 < ex and x1 > sx for x0, x1, sd in spans
            )

        side = prefer if not blocked(prefer) else -prefer
        if blocked(side):
            return None
        for n in (start, end, *inner):
            slots[n].add(side)
        spans.append((sx, ex, side))
        reach = max(half[n][1] for n in (start, end, *inner))
        vx, vy = snap((sx + ex) / 2), side * snap(2 * reach + DETOUR_INNER_GAP)
        local[flow.valve] = (vx, vy)
        bends.add(flow.valve)
        if flow.label is not None:
            options = [(0.0, side * LABEL_OFFSET), (0.0, -side * LABEL_OFFSET)]
            label_valve[flow.label] = flow.valve
            label_options[flow.label] = options
            label_default[flow.label] = 0
            local[flow.label] = (vx + options[0][0], vy + options[0][1])
    in_chain = (
        {f.valve for f in path}
        | {f.valve for _, _, seg in detours for f in seg}
        | {f.valve for f, _, _ in bypasses}
    )
    for flow in comp_flows:
        if flow.valve in in_chain:
            continue
        # 支流：一端在主干存量上，另一端为云或缺省；竖直放置，入流优先在上方。
        if flow.downstream in local and flow.upstream not in local:
            anchor, other, prefer = flow.downstream, flow.upstream, -1
        elif flow.upstream in local and flow.downstream not in local:
            anchor, other, prefer = flow.upstream, flow.downstream, 1
        else:
            return None
        if (
            local[anchor][1] != 0
            or view.objects[anchor].kind == 12
            or (other is not None and view.objects[other].kind != 12)
        ):
            return None
        side = prefer if prefer not in slots[anchor] else -prefer
        if side in slots[anchor]:
            return None
        slots[anchor].add(side)
        ax, ay = local[anchor]
        ah = half[anchor][1]
        vy = ay + side * (ah + BRANCH_VALVE_GAP)
        local[flow.valve] = (ax, vy)
        if other is not None:
            local[other] = (ax, vy + side * CLOUD_TO_VALVE)
        if flow.label is not None:
            lw = label_half.get(flow.label, (40, 16))[0]
            offset = lw + 18
            label_valve[flow.label] = flow.valve
            label_options[flow.label] = [(offset, 0.0), (-offset, 0.0)]
            local[flow.label] = (ax + offset, vy)
    if set(local) != set(members):
        return None
    return Chain(
        members, local, label_valve, label_options, True, sort_key, stocks, label_default, bends
    )


def _rebuild_ring(view, comp_flows, members, half, label_half, sort_key, stocks):
    """首尾相连的存量环排成矩形回路：上排自左向右，下排自右向左，两端竖直连接。

    要求环上至少 4 个存量；挂在环上存量的源/汇支流竖直朝外放置。
    """
    stock_set = set(stocks)
    ring = [f for f in comp_flows if f.upstream in stock_set and f.downstream in stock_set]
    branches = [f for f in comp_flows if f not in ring]
    n = len(stocks)
    out_of = {f.upstream: f for f in ring}
    if n < 4 or len(ring) != n or len(out_of) != n or {f.downstream for f in ring} != stock_set:
        return None
    # 从原图左上角的存量出发，沿流向排出环的顺序
    order = [min(stocks, key=lambda s: (view.objects[s].y, view.objects[s].x))]
    while len(order) < n:
        nxt = out_of[order[-1]].downstream
        if nxt in order:
            return None
        order.append(nxt)
    if out_of[order[-1]].downstream != order[0]:
        return None
    top_count = (n + 1) // 2
    top, bottom = order[:top_count], order[top_count:]
    step = max(
        _gap(view, f.upstream, f, half, label_half) + _gap(view, f.downstream, f, half, label_half)
        for f in ring
    )
    width = step * (top_count - 1)
    tall = max(half[s][1] for s in stocks)
    height = 2 * tall + 2 * BRANCH_VALVE_GAP + 40
    local, label_valve, label_options, label_default = {}, {}, {}, {}
    for i, s in enumerate(top):
        local[s] = (i * step, 0.0)
    for j, s in enumerate(bottom):
        # 下排从最右端退回最左端，存量个数少时间距相应拉宽
        x = width - j * width / (len(bottom) - 1) if len(bottom) > 1 else width / 2
        local[s] = (x, height)
    for f in ring:
        (x1, y1), (x2, y2) = local[f.upstream], local[f.downstream]
        local[f.valve] = ((x1 + x2) / 2, (y1 + y2) / 2)
        if f.label is None:
            continue
        label_valve[f.label] = f.valve
        if y1 == y2:
            # 横向管道：流量名默认放在环外侧（上排在上，下排在下）
            label_options[f.label] = [(0.0, -LABEL_OFFSET), (0.0, LABEL_OFFSET)]
            label_default[f.label] = 0 if y1 == 0 else 1
        else:
            offset = label_half.get(f.label, (40, 16))[0] + 18
            outward = offset if x1 >= width / 2 else -offset
            label_options[f.label] = [(outward, 0.0), (-outward, 0.0)]
            label_default[f.label] = 0
        dx, dy = label_options[f.label][label_default[f.label]]
        local[f.label] = (local[f.valve][0] + dx, local[f.valve][1] + dy)
    used = defaultdict(set)
    for f in branches:
        # 支流：一端是环上的存量，另一端是云或缺省，竖直朝环外
        anchor = f.downstream if f.downstream in stock_set else f.upstream
        other = f.upstream if anchor == f.downstream else f.downstream
        if anchor not in stock_set or (other is not None and view.objects[other].kind != 12):
            return None
        side = -1 if local[anchor][1] == 0 else 1
        if side in used[anchor]:
            return None
        used[anchor].add(side)
        ax, ay = local[anchor]
        vy = ay + side * (half[anchor][1] + BRANCH_VALVE_GAP)
        local[f.valve] = (ax, vy)
        if other is not None:
            local[other] = (ax, vy + side * CLOUD_TO_VALVE)
        if f.label is not None:
            offset = label_half.get(f.label, (40, 16))[0] + 18
            label_valve[f.label] = f.valve
            label_options[f.label] = [(offset, 0.0), (-offset, 0.0)]
            label_default[f.label] = 0
            local[f.label] = (ax + offset, vy)
    if set(local) != set(members):
        return None
    return Chain(members, local, label_valve, label_options, True, sort_key, stocks, label_default)


# ---------------------------------------------------------------------------
# 快速几何场景：直线近似 + 增量代价
# ---------------------------------------------------------------------------


def _clip(cx, cy, hw, hh, tx, ty):
    dx, dy = tx - cx, ty - cy
    adx, ady = abs(dx), abs(dy)
    if adx < 1e-9 and ady < 1e-9:
        return cx, cy
    t = min(hw / adx if adx > 1e-9 else 1e18, hh / ady if ady > 1e-9 else 1e18)
    if t > 1.0:
        t = 1.0
    return cx + dx * t, cy + dy * t


def _segment(x1, y1, w1, h1, x2, y2, w2, h2):
    """两文字框中心连线在两框之间的可见部分：(px,py,qx,qy,minx,miny,maxx,maxy,len)。"""
    px, py = _clip(x1, y1, w1, h1, x2, y2)
    qx, qy = _clip(x2, y2, w2, h2, x1, y1)
    if (qx - px) * (x2 - x1) + (qy - py) * (y2 - y1) <= 0:
        # 文字框互相覆盖：长度记为 0，交叉判断退化为中心连线。
        return (x1, y1, x2, y2, min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), 0.0)
    return (
        px,
        py,
        qx,
        qy,
        min(px, qx),
        min(py, qy),
        max(px, qx),
        max(py, qy),
        math.hypot(qx - px, qy - py),
    )


def _exit(hw, hh, angle):
    """从文字框中心沿 angle 方向射出时离开框的距离。"""
    c, s = abs(math.cos(angle)), abs(math.sin(angle))
    return min(hw / c if c > 1e-9 else 1e18, hh / s if s > 1e-9 else 1e18)


def arc_visible_length(x1, y1, w1, h1, x2, y2, w2, h2):
    """估计柔和圆弧（两侧任选其一）或直线在两框之间的最大可见长度。"""
    dx, dy = x2 - x1, y2 - y1
    chord = math.hypot(dx, dy)
    if chord < 1e-9:
        return 0.0
    theta = math.atan2(dy, dx)
    best = chord - _exit(w1, h1, theta) - _exit(w2, h2, theta)
    for sign in (1, -1):
        turn = sign * ARC_END_TURN
        # 弧长 ≈ 弦长 × (1 + 8k²/3)，k=0.12 时约 1.038
        value = chord * 1.038 - _exit(w1, h1, theta + turn) - _exit(w2, h2, theta - turn)
        best = max(best, value)
    return best


def _cross(a, b):
    if a[6] < b[4] or b[6] < a[4] or a[7] < b[5] or b[7] < a[5]:
        return False
    ax, ay, bx, by = a[0], a[1], a[2], a[3]
    cx, cy, dx, dy = b[0], b[1], b[2], b[3]
    d1 = (dx - cx) * (ay - cy) - (dy - cy) * (ax - cx)
    d2 = (dx - cx) * (by - cy) - (dy - cy) * (bx - cx)
    if d1 * d2 >= 0:
        return False
    d3 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    d4 = (bx - ax) * (dy - ay) - (by - ay) * (dx - ax)
    return d3 * d4 < 0


def _hits(s, cx, cy, hw, hh):
    """Liang–Barsky：线段是否进入以 (cx,cy) 为中心、半宽高 (hw,hh) 的矩形。"""
    x0, x1, y0, y1 = cx - hw, cx + hw, cy - hh, cy + hh
    if s[6] < x0 or s[4] > x1 or s[7] < y0 or s[5] > y1:
        return False
    px, py = s[0], s[1]
    dx, dy = s[2] - px, s[3] - py
    lo, hi = 0.0, 1.0
    if abs(dx) < 1e-12:
        if px < x0 or px > x1:
            return False
    else:
        t1, t2 = (x0 - px) / dx, (x1 - px) / dx
        if t1 > t2:
            t1, t2 = t2, t1
        lo, hi = max(lo, t1), min(hi, t2)
        if lo > hi:
            return False
    if abs(dy) < 1e-12:
        if py < y0 or py > y1:
            return False
    else:
        t1, t2 = (y0 - py) / dy, (y1 - py) / dy
        if t1 > t2:
            t1, t2 = t2, t1
        lo, hi = max(lo, t1), min(hi, t2)
        if lo > hi:
            return False
    return hi - lo > 1e-9


def _overlap(x1, y1, w1, h1, x2, y2, w2, h2):
    ox = w1 + w2 + GAP_X - abs(x1 - x2)
    if ox <= 0:
        return 0.0
    oy = h1 + h2 + GAP_Y - abs(y1 - y2)
    if oy <= 0:
        return 0.0
    return W_OVER + 4.0 * min(ox, oy)


class Scene:
    """以节点下标表示的快速代价模型；只为退火服务，最终仍用 visual 精确验收。"""

    def __init__(self, ids, half, info, pipes, own_pairs, time_owners, direction_weight=0.0):
        self.ids = list(ids)
        self.index = {oid: i for i, oid in enumerate(self.ids)}
        n = len(self.ids)
        self.HW = [float(half[oid][0]) for oid in self.ids]
        self.HH = [float(half[oid][1]) for oid in self.ids]
        self.X = [0.0] * n
        self.Y = [0.0] * n
        self.active = [False] * n
        self.active_list: list[int] = []
        self.info = [(self.index[s], self.index[d]) for s, d in info]
        self.info_ids = [None] * len(self.info)
        # 管道记录 (阀门, 端点[, 段别])：段别 1 为折线管道阀门到拐角的一段，2 为拐角到端点
        self.pipes = [(self.index[p[0]], self.index[p[1]]) for p in pipes]
        self.pipe_mode = [p[2] if len(p) > 2 else 0 for p in pipes]
        self.node_arrows = [[] for _ in range(n)]
        self.node_pipes = [[] for _ in range(n)]
        for a, (s, d) in enumerate(self.info):
            self.node_arrows[s].append(a)
            self.node_arrows[d].append(a)
        for p, (v, e) in enumerate(self.pipes):
            self.node_pipes[v].append(p)
            self.node_pipes[e].append(p)
        self.own = {
            (min(self.index[a], self.index[b]), max(self.index[a], self.index[b]))
            for a, b in own_pairs
        }
        self.pipe_exempt = defaultdict(set)
        for a, b in own_pairs:
            self.pipe_exempt[self.index[a]].add(self.index[b])
            self.pipe_exempt[self.index[b]].add(self.index[a])
        self.time_owner = [oid in time_owners for oid in self.ids]
        self.init = [None] * n
        self.drift_w = [0.0] * n
        self.align: dict[int, int] = {}
        self.bounds = None  # (x0, y0, x1, y1)：初始拓扑外扩后的软边界，防止节点漂散
        self.direction_weight = direction_weight
        self.seg = [None] * len(self.info)
        self.visible_len = [0.0] * len(self.info)
        self.ang = [(0.0, 0.0)] * len(self.info)
        self.pseg = [None] * len(self.pipes)

    # -- 节点与线段维护 -------------------------------------------------

    def add_node(self, oid, hw, hh, time_owner=False):
        self.ids.append(oid)
        self.index[oid] = len(self.ids) - 1
        for array, value in (
            (self.HW, float(hw)),
            (self.HH, float(hh)),
            (self.X, 0.0),
            (self.Y, 0.0),
            (self.active, False),
            (self.time_owner, time_owner),
            (self.init, None),
            (self.drift_w, 0.0),
        ):
            array.append(value)
        self.node_arrows.append([])
        self.node_pipes.append([])
        return len(self.ids) - 1

    def repoint(self, arrow_index, new_source):
        s, d = self.info[arrow_index]
        self.node_arrows[s].remove(arrow_index)
        self.node_arrows[new_source].append(arrow_index)
        self.info[arrow_index] = (new_source, d)
        self._update_arrow(arrow_index)

    def place(self, i, x, y):
        self.X[i], self.Y[i] = float(x), float(y)
        if not self.active[i]:
            self.active[i] = True
            self.active_list.append(i)
        self.refresh(i)

    def refresh(self, i):
        for a in self.node_arrows[i]:
            self._update_arrow(a)
        for p in self.node_pipes[i]:
            self._update_pipe(p)

    def refresh_all(self):
        for a in range(len(self.info)):
            self._update_arrow(a)
        for p in range(len(self.pipes)):
            self._update_pipe(p)

    def _update_arrow(self, a):
        s, d = self.info[a]
        X, Y, HW, HH = self.X, self.Y, self.HW, self.HH
        self.seg[a] = _segment(X[s], Y[s], HW[s], HH[s], X[d], Y[d], HW[d], HH[d])
        self.visible_len[a] = arc_visible_length(X[s], Y[s], HW[s], HH[s], X[d], Y[d], HW[d], HH[d])
        angle = math.atan2(Y[d] - Y[s], X[d] - X[s])
        self.ang[a] = (angle, angle + math.pi if angle <= 0 else angle - math.pi)

    def _update_pipe(self, p):
        v, e = self.pipes[p]
        X, Y, HW, HH = self.X, self.Y, self.HW, self.HH
        mode = self.pipe_mode[p]
        if mode == 1:
            self.pseg[p] = _segment(X[v], Y[v], HW[v], HH[v], X[e], Y[v], 0.0, 0.0)
        elif mode == 2:
            self.pseg[p] = _segment(X[e], Y[v], 0.0, 0.0, X[e], Y[e], HW[e], HH[e])
        else:
            self.pseg[p] = _segment(X[v], Y[v], HW[v], HH[v], X[e], Y[e], HW[e], HH[e])

    def clone(self):
        """复制场景（列表逐层复制），供影子拆分试验使用。"""
        other = Scene.__new__(Scene)
        other.__dict__ = dict(self.__dict__)
        for key, value in self.__dict__.items():
            if isinstance(value, list):
                setattr(other, key, list(value))
            elif isinstance(value, dict) and key != "pipe_exempt":
                setattr(other, key, dict(value))
        other.node_arrows = [list(v) for v in self.node_arrows]
        other.node_pipes = [list(v) for v in self.node_pipes]
        return other

    def neighbor_sets(self):
        out = [set() for _ in self.ids]
        for s, d in self.info:
            out[s].add(d)
            out[d].add(s)
        return out

    def snapshot(self):
        return list(self.X), list(self.Y)

    def restore(self, state):
        self.X, self.Y = list(state[0]), list(state[1])
        self.refresh_all()

    # -- 代价 ----------------------------------------------------------

    def total(self):
        return self.partial(set(self.active_list))

    def partial(self, S, detail=None):
        """计算所有涉及 S 中节点的代价项，每一项只计一次；增量 = 移动后 − 移动前。"""
        act = self.active
        X, Y, HW, HH = self.X, self.Y, self.HW, self.HH
        info, seg, pipes, pseg = self.info, self.seg, self.pipes, self.pseg
        time_owner = self.time_owner
        A, P = set(), set()
        for n in S:
            for a in self.node_arrows[n]:
                s, d = info[a]
                if act[s] and act[d]:
                    A.add(a)
            for p in self.node_pipes[n]:
                v, e = pipes[p]
                if act[v] and act[e]:
                    P.add(p)
        active_arrows = [a for a, (s, d) in enumerate(info) if act[s] and act[d]]
        active_pipes = [p for p, (v, e) in enumerate(pipes) if act[v] and act[e]]
        nodes = self.active_list
        cost = 0.0
        cross = pipe_cross = hits = over = short = narrow = 0
        tw, th, toff = TIME_LABEL_HALF_WIDTH, TIME_LABEL_HALF_HEIGHT, TIME_LABEL_OFFSET
        cl = ARROW_CLEAR
        # 信息线交叉与穿管
        for a in A:
            sa = seg[a]
            a0, a1 = info[a]
            for b in active_arrows:
                if b == a or (b in A and b < a):
                    continue
                b0, b1 = info[b]
                if a0 == b0 or a0 == b1 or a1 == b0 or a1 == b1:
                    continue
                if _cross(sa, seg[b]):
                    cross += 1
            for p in active_pipes:
                if _cross(sa, pseg[p]):
                    pipe_cross += 1
        for p in P:
            sp = pseg[p]
            p0, p1 = pipes[p]
            for b in active_arrows:
                if b in A:
                    continue
                if _cross(sp, seg[b]):
                    pipe_cross += 1
            for q in active_pipes:
                if q == p or (q in P and q < p):
                    continue
                q0, q1 = pipes[q]
                if p0 == q0 or p0 == q1 or p1 == q0 or p1 == q1:
                    continue
                if _cross(sp, pseg[q]):
                    cross += 1
        # 信息线、管道穿字与 <Time> 预留区；先用外接框排除，再做精确裁剪
        exempt = self.pipe_exempt
        owners = [o for o in nodes if time_owner[o]]

        def crossings(s, ends, skip, pad, candidates):
            count = 0
            x0, y0, x1, y1 = s[4], s[5], s[6], s[7]
            for o in candidates:
                if o in ends or o in skip:
                    continue
                w, h = HW[o] + pad, HH[o] + pad
                xo, yo = X[o], Y[o]
                if x1 < xo - w or x0 > xo + w or y1 < yo - h or y0 > yo + h:
                    continue
                if _hits(s, xo, yo, w, h):
                    count += 1
            return count

        def time_hits(s, candidates):
            count = 0
            x0, y0, x1, y1 = s[4], s[5], s[6], s[7]
            for o in candidates:
                xo, yo = X[o], Y[o] + toff
                if x1 < xo - tw or x0 > xo + tw or y1 < yo - th or y0 > yo + th:
                    continue
                if _hits(s, xo, yo, tw, th):
                    count += 1
            return count

        moved_active = [o for o in S if act[o]]
        moved_owners = [o for o in moved_active if time_owner[o]]
        for a in A:
            hits += crossings(seg[a], info[a], (), cl, nodes) + time_hits(seg[a], owners)
        for b in active_arrows:
            if b not in A:
                hits += crossings(seg[b], info[b], (), cl, moved_active)
                hits += time_hits(seg[b], moved_owners)
        for p in P:
            ends = pipes[p]
            hits += crossings(pseg[p], ends, exempt.get(ends[0], ()), 4, nodes)
            hits += time_hits(pseg[p], owners)
        for p in active_pipes:
            if p not in P:
                ends = pipes[p]
                hits += crossings(pseg[p], ends, exempt.get(ends[0], ()), 4, moved_active)
                hits += time_hits(pseg[p], moved_owners)
        # 文字框重叠（含 <Time> 预留区）
        own = self.own
        for o in S:
            if not act[o]:
                continue
            xo, yo, wo, ho = X[o], Y[o], HW[o], HH[o]
            to = time_owner[o]
            for q in nodes:
                if q == o or (q in S and q < o):
                    continue
                if (min(o, q), max(o, q)) in own:
                    continue
                value = _overlap(xo, yo, wo, ho, X[q], Y[q], HW[q], HH[q])
                if to:
                    value += _overlap(xo, yo + toff, tw, th, X[q], Y[q], HW[q], HH[q])
                if time_owner[q]:
                    value += _overlap(xo, yo, wo, ho, X[q], Y[q] + toff, tw, th)
                if value:
                    over += 1
                    cost += value
        # 长度、过短与方向
        dw = self.direction_weight
        visible_len = self.visible_len
        for a in A:
            cost += W_LEN * seg[a][8]
            length = visible_len[a]
            if length < SHORT_LIMIT:
                short += 1
                cost += W_SHORT * (1.0 + (SHORT_LIMIT - length) / SHORT_LIMIT)
            s, d = info[a]
            if dw and X[d] < X[s]:
                cost += dw * (X[s] - X[d])
            # 近乎水平/竖直却差几像素的连线显得随意：鼓励对齐到同一行或同一列。
            ddx, ddy = abs(X[d] - X[s]), abs(Y[d] - Y[s])
            if 0.5 < ddy <= SNAP_RANGE and ddx > 2 * ddy:
                cost += W_SNAP * ddy
            elif 0.5 < ddx <= SNAP_RANGE and ddy > 2 * ddx:
                cost += W_SNAP * ddx
        # 同一节点上的小夹角
        touched = set(S)
        for a in A:
            touched.update(info[a])
        for t in touched:
            if not act[t]:
                continue
            here = [a for a in self.node_arrows[t] if act[info[a][0]] and act[info[a][1]]]
            if len(here) < 2:
                continue
            angles = [(a, self.ang[a][0] if info[a][0] == t else self.ang[a][1]) for a in here]
            for i in range(len(angles)):
                ai, gi = angles[i]
                for j in range(i + 1, len(angles)):
                    aj, gj = angles[j]
                    if ai not in A and aj not in A:
                        continue
                    gap = abs((gi - gj + math.pi) % (2 * math.pi) - math.pi)
                    if gap < NARROW_ANGLE:
                        narrow += 1
                        cost += W_NARROW * (0.5 + (NARROW_ANGLE - gap) / NARROW_ANGLE)
        # 偏离初始拓扑、越出软边界与初值对齐
        bounds = self.bounds
        for o in S:
            if self.init[o] is not None and self.drift_w[o]:
                cost += self.drift_w[o] * math.hypot(X[o] - self.init[o][0], Y[o] - self.init[o][1])
            if bounds is not None and act[o]:
                excess = (
                    max(0.0, bounds[0] - (X[o] - HW[o]))
                    + max(0.0, (X[o] + HW[o]) - bounds[2])
                    + max(0.0, bounds[1] - (Y[o] - HH[o]))
                    + max(0.0, (Y[o] + HH[o]) - bounds[3])
                )
                if excess:
                    cost += W_BOUND * excess
            target = self.align.get(o)
            if target is not None and act[target]:
                cost += 0.8 * abs(X[o] - X[target])
        cost += W_CROSS * cross + W_PIPE_CROSS * pipe_cross + W_HIT * hits
        if detail is not None:
            detail.update(
                cross=cross,
                pipe_cross=pipe_cross,
                hits=hits,
                overlaps=over,
                short=short,
                narrow=narrow,
            )
        return cost


# ---------------------------------------------------------------------------
# 初始拓扑
# ---------------------------------------------------------------------------


def _ring(anchor_x, anchor_y, w1, h1, w2, h2, extras, steps=16):
    """围绕锚点的候选位置；半径按两个文字框在该方向上恰好分开再加留白。"""
    out = []
    for k in range(steps):
        theta = 2 * math.pi * k / steps
        c, s = math.cos(theta), math.sin(theta)
        need_x = (w1 + w2 + 30) / abs(c) if abs(c) > 1e-6 else 1e9
        need_y = (h1 + h2 + 26) / abs(s) if abs(s) > 1e-6 else 1e9
        base = min(need_x, need_y)
        for extra in extras:
            r = base + extra
            out.append((snap(anchor_x + r * c), snap(anchor_y + r * s)))
    return out


def greedy_place(scene, order_ids, neighbors, roles, rng=None, priority=frozenset()):
    """逐个落位：优先已落位邻居最多的节点；候选围绕邻居重心，代价最低者胜出。

    priority 中的节点（直接连到流量名的变量）同等条件下先落位：流量名只能在
    管道一侧，它们的位置决定了相邻辅助量应该处在管道哪一侧。rng 给定时对
    并列节点随机打散，作为多起点的一种。
    """
    pending = [scene.index[oid] for oid in order_ids]
    placed_count = {i: 0 for i in pending}
    for i in pending:
        placed_count[i] = sum(1 for j in neighbors[i] if scene.active[j])
    remaining = set(pending)
    jitter = {i: (rng.random() if rng else 0.0) for i in pending}
    while remaining:
        i = max(
            remaining,
            key=lambda k: (
                placed_count[k] > 0,
                k in priority,
                placed_count[k],
                len(neighbors[k]),
                jitter[k],
                -k,
            ),
        )
        if placed_count[i] == 0:
            break
        remaining.discard(i)
        known = [j for j in neighbors[i] if scene.active[j]]
        ax = sum(scene.X[j] for j in known) / len(known)
        ay = sum(scene.Y[j] for j in known) / len(known)
        ref = known[0]
        wn, hn = scene.HW[i], scene.HH[i]
        cands = []
        if len(known) == 1:
            cands += _ring(ax, ay, scene.HW[ref], scene.HH[ref], wn, hn, (0, 40, 90, 150, 220))
        else:
            cands.append((snap(ax), snap(ay)))
            cands += _ring(ax, ay, 20, 10, wn, hn, (0, 40, 90, 160))
            for j in known:
                cands += _ring(
                    scene.X[j], scene.Y[j], scene.HW[j], scene.HH[j], wn, hn, (0, 60), 12
                )
        stock = roles.get(i)
        if stock is not None and scene.active[stock]:
            sx, sy, sh = scene.X[stock], scene.Y[stock], scene.HH[stock]
            for gap in (36, 56, 80, 110):
                for sign in (-1, 1):
                    cands.append((snap(sx), snap(sy + sign * (sh + hn + gap))))
        best, best_cost = None, None
        for x, y in dict.fromkeys(cands):
            scene.place(i, x, y)
            cost = scene.partial({i}) + 0.6 * math.hypot(x - ax, y - ay)
            if best_cost is None or cost < best_cost:
                best, best_cost = (x, y), cost
        scene.place(i, *best)
        for j in neighbors[i]:
            if j in placed_count:
                placed_count[j] += 1
    return [scene.ids[i] for i in remaining]


def layered_positions(component, succ, pred, half, original):
    """无骨架模块：按计算顺序自左向右分层，层内按重心排序，返回相对坐标。"""
    comp = set(component)
    layer = {}
    # 打破回路：按原坐标 x 稳定排序的 DFS，忽略回边。
    order = sorted(comp, key=lambda n: (original[n][0], original[n][1], n))
    state = {}
    back = set()

    def dfs(n):
        state[n] = 1
        for m in sorted(succ[n] & comp, key=lambda k: (original[k][0], k)):
            if state.get(m) == 1:
                back.add((n, m))
            elif m not in state:
                dfs(m)
        state[n] = 2

    for n in order:
        if n not in state:
            dfs(n)

    def preds(n):
        return [m for m in pred[n] & comp if (m, n) not in back]

    memo = {}

    def depth(n, trail=()):
        if n in memo:
            return memo[n]
        ps = [m for m in preds(n) if m not in trail]
        memo[n] = 0 if not ps else 1 + max(depth(m, trail + (n,)) for m in ps)
        return memo[n]

    for n in comp:
        layer[n] = depth(n)
    # 汇点尽量靠近其后继：源参数放在紧邻目标的上一层。
    for n in comp:
        outs = [m for m in succ[n] & comp if (n, m) not in back]
        if not preds(n) and outs:
            layer[n] = max(0, min(layer[m] for m in outs) - 1)
    layers = defaultdict(list)
    for n in comp:
        layers[layer[n]].append(n)
    for key in layers:
        layers[key].sort(key=lambda n: (original[n][1], n))
    keys = sorted(layers)
    for _ in range(8):
        for key in keys[1:]:
            pos = {n: i for k in keys for i, n in enumerate(layers[k])}
            layers[key].sort(
                key=lambda n: (
                    sum(pos[m] for m in pred[n] & comp) / max(1, len(pred[n] & comp))
                    if pred[n] & comp
                    else pos[n],
                    n,
                )
            )
        for key in reversed(keys[:-1]):
            pos = {n: i for k in keys for i, n in enumerate(layers[k])}
            layers[key].sort(
                key=lambda n: (
                    sum(pos[m] for m in succ[n] & comp) / max(1, len(succ[n] & comp))
                    if succ[n] & comp
                    else pos[n],
                    n,
                )
            )
    result = {}
    x = 0.0
    xs = {}
    for idx, key in enumerate(keys):
        members = layers[key]
        width = max(half[n][0] for n in members)
        if idx:
            prev_width = max(half[n][0] for n in layers[keys[idx - 1]])
            x += prev_width + width + 70
        xs[key] = x
        heights = [half[n][1] * 2 + 46 for n in members]
        total = sum(heights)
        y = -total / 2
        for n, h in zip(members, heights, strict=False):
            result[n] = (x, y + h / 2)
            y += h
    # 纵向对齐：节点向相邻层邻居的中位纵坐标靠拢，层内保持顺序与最小间距，
    # 让计算链尽量水平，读起来像人工按计算顺序摆放。
    for _ in range(4):
        for key in keys:
            members = layers[key]
            wanted = []
            for n in members:
                ys = sorted(result[m][1] for m in (succ[n] | pred[n]) & comp if layer[m] != key)
                wanted.append(ys[len(ys) // 2] if ys else result[n][1])
            placed = []
            for n, target in zip(members, wanted, strict=False):
                if placed:
                    prev = placed[-1]
                    gap = half[prev][1] + half[n][1] + 46
                    target = max(target, result[prev][1] + gap)
                result[n] = (xs[key], target)
                placed.append(n)
            # 整层平移，使层的平均偏移量与期望一致，避免整体向下漂移。
            shift = sum(w - result[n][1] for n, w in zip(members, wanted, strict=False)) / len(
                members
            )
            if shift < 0:
                for n in members:
                    result[n] = (result[n][0], result[n][1] + shift)
    return result


def pack_blocks(blocks, half, origin, max_width):
    """把若干相对坐标块按货架方式紧凑排列，返回绝对坐标。"""
    out = {}
    cursor_x, cursor_y, shelf_h = origin[0], origin[1], 0.0
    for block in blocks:
        xs0 = min(x - half[n][0] for n, (x, y) in block.items())
        xs1 = max(x + half[n][0] for n, (x, y) in block.items())
        ys0 = min(y - half[n][1] for n, (x, y) in block.items())
        ys1 = max(y + half[n][1] + TIME_LABEL_OFFSET for n, (x, y) in block.items())
        w, h = xs1 - xs0, ys1 - ys0
        if cursor_x > origin[0] and cursor_x + w > origin[0] + max_width:
            cursor_x = origin[0]
            cursor_y += shelf_h + 60
            shelf_h = 0.0
        for n, (x, y) in block.items():
            out[n] = (snap(cursor_x + x - xs0), snap(cursor_y + y - ys0))
        cursor_x += w + 70
        shelf_h = max(shelf_h, h)
    return out


# ---------------------------------------------------------------------------
# 退火
# ---------------------------------------------------------------------------


def anneal(
    scene, movable, groups, flips, neighbors, rng, iterations, t0=200.0, t1=1.0, step=50.0, axes=()
):
    """在当前拓扑附近做局部调整；返回最优代价。

    动作：单点移动、两点交换、流量名翻面、整条存量链平移、跳到邻居附近，
    以及“簇平移/簇镜像”——把一个辅助变量连同其可移动邻居整体平移，或按
    最近的存量链中心线翻到管道另一侧，用于整体越过管道而不必逐个穿越。
    """
    movable = [i for i in movable if scene.active[i]]
    movable_set = set(movable)
    if not movable and not flips and not groups:
        return scene.total()
    current = scene.total()
    best, best_state = current, scene.snapshot()
    for it in range(iterations):
        temperature = t0 * (t1 / t0) ** (it / max(1, iterations))
        r = rng.random()
        saved = {}
        if r < 0.10 and flips:
            label = rng.choice(list(flips))
            valve, options, side = flips[label]
            moved = {label}
            saved[label] = (scene.X[label], scene.Y[label])
            before = scene.partial(moved)
            new_side = (side + 1) % len(options)
            ox, oy = options[new_side]
            scene.X[label], scene.Y[label] = scene.X[valve] + ox, scene.Y[valve] + oy
            scene.refresh(label)
            flips[label] = (valve, options, new_side)
        elif r < 0.12 and len(groups) > 1:
            # 交换两条存量链的纵向位置（横向保持），用于调整行序。
            first, second = rng.sample(range(len(groups)), 2)
            ga, gb = groups[first], groups[second]
            moved = set(ga) | set(gb)
            for i in moved:
                saved[i] = (scene.X[i], scene.Y[i])
            before = scene.partial(moved)
            ya = sum(scene.Y[i] for i in ga) / len(ga)
            yb = sum(scene.Y[i] for i in gb) / len(gb)
            shift = snap(yb - ya)
            for i in ga:
                scene.Y[i] += shift
            for i in gb:
                scene.Y[i] -= shift
            for i in moved:
                scene.refresh(i)
        elif r < 0.17 and groups:
            group = rng.choice(groups)
            moved = set(group)
            for i in group:
                saved[i] = (scene.X[i], scene.Y[i])
            before = scene.partial(moved)
            if rng.random() < 0.3:
                # 大跨度平移：让整条链越过另一条链的端点，给跨行连线让出通道。
                size = rng.choice((100, 160, 240, 320))
            else:
                size = max(GRID, step * (temperature / t0) ** 0.5 * 1.5)
            dx = snap(rng.uniform(-size, size)) if rng.random() < 0.5 else 0
            dy = snap(rng.uniform(-size, size)) if dx == 0 else 0
            for i in group:
                scene.X[i] += dx
                scene.Y[i] += dy
            for i in group:
                scene.refresh(i)
        elif r < 0.24 and movable:
            i = rng.choice(movable)
            cluster = _cluster(i, neighbors, movable_set, rng)
            moved = set(cluster)
            saved = {j: (scene.X[j], scene.Y[j]) for j in cluster}
            before = scene.partial(moved)
            row_axes = [scene.Y[k] for k in axes]
            if row_axes and rng.random() < 0.5:
                axis = min(row_axes, key=lambda y: abs(y - scene.Y[i]))
                for j in cluster:
                    scene.Y[j] = snap(2 * axis - scene.Y[j])
            else:
                size = max(GRID, step * (temperature / t0) ** 0.5 * 2.5)
                dx, dy = snap(rng.uniform(-size, size)), snap(rng.uniform(-size, size))
                for j in cluster:
                    scene.X[j] += dx
                    scene.Y[j] += dy
            for j in cluster:
                scene.refresh(j)
        elif r < 0.34 and len(movable) > 1:
            i, j = rng.sample(movable, 2)
            moved = {i, j}
            saved = {i: (scene.X[i], scene.Y[i]), j: (scene.X[j], scene.Y[j])}
            before = scene.partial(moved)
            scene.X[i], scene.X[j] = scene.X[j], scene.X[i]
            scene.Y[i], scene.Y[j] = scene.Y[j], scene.Y[i]
            scene.refresh(i)
            scene.refresh(j)
        elif movable:
            i = rng.choice(movable)
            moved = {i}
            saved = {i: (scene.X[i], scene.Y[i])}
            before = scene.partial(moved)
            if r < 0.42 and neighbors[i]:
                known = [j for j in neighbors[i] if scene.active[j]]
                if known:
                    ax = sum(scene.X[j] for j in known) / len(known)
                    ay = sum(scene.Y[j] for j in known) / len(known)
                    theta = rng.uniform(0, 2 * math.pi)
                    radius = rng.uniform(60, 180)
                    scene.X[i] = snap(ax + radius * math.cos(theta))
                    scene.Y[i] = snap(ay + radius * math.sin(theta))
            else:
                size = max(GRID, step * (temperature / t0) ** 0.5 * 3)
                scene.X[i] = snap(scene.X[i] + rng.uniform(-size, size))
                scene.Y[i] = snap(scene.Y[i] + rng.uniform(-size, size))
            scene.refresh(i)
        else:
            continue
        after = scene.partial(moved)
        delta = after - before
        if delta <= 0 or rng.random() < math.exp(-delta / temperature):
            current += delta
            if current < best - 1e-9:
                best, best_state = current, scene.snapshot()
        else:
            for i, (x, y) in saved.items():
                scene.X[i], scene.Y[i] = x, y
            for i in saved:
                scene.refresh(i)
            if r < 0.10 and flips and len(saved) == 1:
                label = next(iter(saved))
                if label in flips:
                    valve, options, side = flips[label]
                    flips[label] = (valve, options, (side - 1) % len(options))
    scene.restore(best_state)
    # 恢复后按实际坐标重新识别流量名侧别。
    for label, (valve, options, _side) in list(flips.items()):
        ox, oy = scene.X[label] - scene.X[valve], scene.Y[label] - scene.Y[valve]
        nearest = min(
            range(len(options)), key=lambda k: math.hypot(options[k][0] - ox, options[k][1] - oy)
        )
        flips[label] = (valve, options, nearest)
    return best


def _cluster(start, neighbors, movable_set, rng, depth=2, limit=8):
    """以 start 为中心、沿可移动辅助变量扩展至多两跳的小簇（随机截断深度）。"""
    depth = rng.choice((1, depth))
    cluster, frontier = {start}, [start]
    for _ in range(depth):
        nxt = []
        for node in frontier:
            for other in neighbors[node]:
                if other in movable_set and other not in cluster and len(cluster) < limit:
                    cluster.add(other)
                    nxt.append(other)
        frontier = nxt
    return cluster


# ---------------------------------------------------------------------------
# 平面性检查
# ---------------------------------------------------------------------------


def planarity_report(view, hidden=frozenset()):
    """把流量名并入阀门后检查平面性；不可平面时给出 Kuratowski 子图。"""
    try:
        import networkx as nx
    except ImportError:
        return {
            "available": False,
            "note": "未安装 networkx，跳过平面性检查（requirements/layout.txt）",
        }
    labels = attached_label_map(view)
    merge = {label: valve for valve, label in labels.items()}
    graph = nx.Graph()
    for oid, obj in view.objects.items():
        if visible(obj) and oid not in hidden:
            graph.add_node(merge.get(oid, oid))
    for arrow in view.arrows:
        if not shown(view, arrow) or arrow.obj_id in hidden:
            continue
        a, b = merge.get(arrow.from_id, arrow.from_id), merge.get(arrow.to_id, arrow.to_id)
        if a != b and a in graph and b in graph:
            graph.add_edge(a, b)
    planar, certificate = nx.check_planarity(graph, counterexample=True)
    report = {"available": True, "planar": bool(planar)}
    if not planar:

        def label(oid):
            obj = view.objects[oid]
            if obj.kind == 11 and oid in labels:
                return view.objects[labels[oid]].name
            return obj.name

        report["kuratowski_edges"] = sorted(
            [sorted([a, b]) + [label(a), label(b)] for a, b in certificate.edges()]
        )
        report["note"] = "图结构不可平面：不增加影子引用时至少保留一处交叉"
    return report


# ---------------------------------------------------------------------------
# 视图入口
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Plan:
    """一个视图的完整排版结果（尚未写回文本）。"""

    positions: dict[int, tuple[int, int]]
    sizes: dict[int, tuple[int, int]]
    clouds: list[int]
    controls: dict[int, tuple[tuple[int, int], float]]
    pipes: dict[int, tuple[int, int]]
    splits: list[dict]
    metrics: dict
    label_sides: dict[int, str]
    chains: list[dict]


def _positioned(
    view,
    positions,
    sizes,
    clouds,
    new_objects=None,
    repoint=None,
    controls=None,
    pipes=None,
    shifted=None,
):
    """生成带新坐标、尺寸与控制点的视图副本，供精确几何检查使用。"""
    objects = {}
    for oid, obj in view.objects.items():
        x, y = positions.get(oid, (obj.x, obj.y))
        w, h = sizes.get(oid, (obj.w, obj.h))
        name = NATIVE_CLOUD_CODE if oid in clouds else obj.name
        objects[oid] = dataclasses.replace(obj, x=x, y=y, w=w, h=h, name=name)
    for oid, obj in (new_objects or {}).items():
        x, y = positions[oid]
        objects[oid] = dataclasses.replace(obj, x=x, y=y)
    arrows = []
    for arrow in view.arrows:
        updated = arrow
        if repoint and arrow.obj_id in repoint:
            updated = dataclasses.replace(updated, from_id=repoint[arrow.obj_id])
        if controls and arrow.obj_id in controls:
            updated = dataclasses.replace(updated, shape=1, points=[controls[arrow.obj_id][0]])
        if pipes and arrow.obj_id in pipes:
            updated = dataclasses.replace(updated, points=[pipes[arrow.obj_id]])
        if shifted and arrow.obj_id in shifted:
            updated = dataclasses.replace(updated, points=list(shifted[arrow.obj_id]))
        arrows.append(updated)
    return dataclasses.replace(view, objects=objects, arrows=arrows)


def natural_plan(lines, view, config, exclude=frozenset()):
    """计算一个视图的 natural 排版方案；不修改 lines。

    多起点退火在极少数情况下会停在只差一处缺陷的局部最优；此时按固定的
    种子序列整体重试（默认最多 3 次），保留缺陷最少、最紧凑的方案。种子序列
    固定，因此同一输入的输出可复现。
    """
    base_seed = int(config.get("seed", 1))
    best = None
    big = len(view.objects) > LARGE_VIEW
    for attempt in range(1 if big else SEED_ATTEMPTS):
        plan = _natural_plan_once(lines, view, {**config, "seed": base_seed + attempt}, exclude)
        if plan is None:
            return None
        key = selection_key(plan.metrics)
        if best is None or key < best[0]:
            best = (key, plan)
        if key[:2] == (0, 0):
            break
    return best[1]


def _natural_plan_once(lines, view, config, exclude=frozenset()):
    """单个种子的完整排版流程。"""
    from vensim_autolayout import _is_information_arrow

    rng_seed = int(config.get("seed", 1))
    font = view_font_size(lines, view)
    scale = font / 12
    hidden = set(exclude)
    for oid, obj in view.objects.items():
        if not visible(obj):
            hidden.add(oid)
    hidden_arrows = {
        a.obj_id for a in view.arrows if not visible(a) or a.from_id in hidden or a.to_id in hidden
    }
    objects = {oid: obj for oid, obj in view.objects.items() if oid not in hidden}
    if not objects:
        return None

    # ---- 尺寸：存量按文字归一化，云改为原生 10×8 ----
    pipe_ends = pipe_endpoint_ids(view)
    sizes: dict[int, tuple[int, int]] = {}
    clouds = []
    for oid, obj in view.objects.items():
        if is_cloud_candidate(obj, pipe_ends):
            if obj.name.strip() != NATIVE_CLOUD_CODE or (abs(obj.w), abs(obj.h)) != CLOUD_HALF:
                clouds.append(oid)
                sizes[oid] = CLOUD_HALF
        elif (
            obj.kind == 10
            and obj.stock_like
            and not obj.is_shadow
            and not obj.attached_to_valve
            and config.get("stock_size", "normalize") == "normalize"
        ):
            sizes[oid] = stock_half_size(obj.name, font, scale)

    def half_of(oid, obj=None):
        obj = obj or view.objects[oid]
        w, h = sizes.get(oid, (abs(obj.w), half_height(obj)))
        if obj.kind == 10 and obj.is_shadow:
            w += SHADOW_MARGIN
        return (float(w), float(h))

    # 尺寸覆盖全部对象：隐藏的链成员仍需参与骨架间距计算，但不进入代价场景。
    half = {oid: half_of(oid) for oid in view.objects}

    # ---- 骨架 ----
    locked_ids = {int(x) for x in config.get("lock_object_ids", [])}
    locked_names = {str(n) for n in config.get("lock_node_names", [])}
    anchors_by_name = config.get("node_positions", {})
    anchors = {}
    for oid, obj in objects.items():
        if obj.name in locked_names:
            locked_ids.add(oid)
        if obj.name in anchors_by_name and obj.kind == 10:
            anchors[oid] = tuple(anchors_by_name[obj.name])
    if not config.get("move_shadows", True):
        locked_ids.update(oid for oid, obj in objects.items() if obj.kind == 10 and obj.is_shadow)
    # 折线、样条等无法改写为单控制点圆弧的信息线：两端保持原位，箭头记录不动。
    for arrow in view.arrows:
        if (
            visible(arrow)
            and not arrow.is_physical_flow
            and arrow.from_id in objects
            and arrow.to_id in objects
            and not _is_information_arrow(arrow, view.objects)
        ):
            locked_ids.update((arrow.from_id, arrow.to_id))
    flows = extract_flows(view, hidden_arrows)
    flows = {v: f for v, f in flows.items() if v in objects}
    label_half = {f.label: half[f.label] for f in flows.values() if f.label in half}
    chain_config = dict(config)
    chain_config["_locked_ids"] = locked_ids
    skeleton_locked = bool(
        locked_ids
        & {
            m
            for f in flows.values()
            for m in (f.valve, f.label, f.upstream, f.downstream)
            if m is not None
        }
    )
    chain_config["_preserve_skeleton"] = skeleton_locked
    chains = build_chains(view, flows, half, label_half, chain_config)
    for chain in chains:
        if not chain.rebuilt:
            # 保留原几何的骨架块也保留原存量尺寸，放大后会压住相邻阀门
            for m in chain.stocks:
                if m in sizes and m not in clouds:
                    del sizes[m]
                    half[m] = half_of(m)
    skeleton = {m for c in chains for m in c.members}
    rebuilt_valves = {
        m for c in chains if c.rebuilt for m in c.members if view.objects[m].kind == 11
    }
    straight_pipes = {
        pid for valve, flow in flows.items() if valve in rebuilt_valves for pid in flow.pipes
    }

    # ---- 连接关系 ----
    routeable = [
        a
        for a in view.arrows
        if a.obj_id not in hidden_arrows and _is_information_arrow(a, view.objects)
    ]
    routeable_ids = {a.obj_id for a in routeable}
    info_pairs = [
        (a.from_id, a.to_id)
        for a in view.arrows
        if a.obj_id not in hidden_arrows
        and not a.is_physical_flow
        and a.from_id in objects
        and a.to_id in objects
        and a.from_id != a.to_id
    ]
    info_ids = [
        a.obj_id
        for a in view.arrows
        if a.obj_id not in hidden_arrows
        and not a.is_physical_flow
        and a.from_id in objects
        and a.to_id in objects
        and a.from_id != a.to_id
    ]
    bent_valves = {v for c in chains for v in c.bends}
    pipe_pairs, scene_pipes = [], []
    for flow in flows.values():
        for end in (flow.upstream, flow.downstream):
            if end is not None and end in objects:
                pipe_pairs.append((flow.valve, end))
                if flow.valve in bent_valves:
                    scene_pipes += [(flow.valve, end, 1), (flow.valve, end, 2)]
                else:
                    scene_pipes.append((flow.valve, end))
    own_pairs = [
        (f.valve, f.label) for f in flows.values() if f.label is not None and f.label in objects
    ]
    owners = {obj.obj_id for obj in time_label_owners(view) if obj.obj_id in objects}
    no_skeleton = not chains
    scene = Scene(
        list(objects),
        half,
        info_pairs,
        scene_pipes,
        own_pairs,
        owners,
        W_DIR if no_skeleton else 0.0,
    )
    scene.info_ids = info_ids
    idx = scene.index
    succ = defaultdict(set)
    pred = defaultdict(set)
    neighbors = [set() for _ in scene.ids]
    for s, d in info_pairs:
        succ[s].add(d)
        pred[d].add(s)
        neighbors[idx[s]].add(idx[d])
        neighbors[idx[d]].add(idx[s])

    original = {oid: (obj.x, obj.y) for oid, obj in objects.items()}
    free_kinds = {
        oid for oid, obj in objects.items() if obj.kind not in (10, 11) and oid not in skeleton
    }
    aux = [
        oid
        for oid, obj in objects.items()
        if oid not in skeleton
        and oid not in free_kinds
        and oid not in locked_ids
        and oid not in anchors
    ]
    # 初值参数：唯一出线指向存量，且方程中只出现在该存量 INTEG 的初值表达式里。
    # 没有管道的累计量（INTEG(输入, 0) 直接画箭头）中，指向存量的线是速率输入。
    init_links = _initial_value_links(lines)
    from vensim_engine import canonical_name

    roles = {}
    for oid in aux:
        outs = succ[oid]
        if len(outs) == 1 and not pred[oid]:
            target = next(iter(outs))
            obj = objects[target]
            if (
                obj.kind == 10
                and obj.stock_like
                and not obj.is_shadow
                and (canonical_name(objects[oid].name), canonical_name(obj.name)) in init_links
            ):
                roles[idx[oid]] = idx[target]

    # ---- 链条成行 ----
    def chain_positions(sides=None, reverse=False):
        if skeleton_locked:
            # 用户锁定了骨架对象：全部存量链保持原绝对坐标，只排布其余变量。
            return {m: original[m] for chain in chains for m in chain.members if m in original}
        positions = {}
        placed_rows = []
        prev_bottom = None
        prev_oy = 0.0
        for c_index, chain in enumerate(reversed(chains) if reverse else chains):
            local = dict(chain.local)
            if chain.rebuilt:
                for label, options in chain.label_options.items():
                    side = (sides or {}).get(label, chain.label_default.get(label, 0))
                    vx, vy = chain.local[chain.label_valve[label]]
                    local[label] = (vx + options[side][0], vy + options[side][1])
            margin = ROW_MARGIN if chain.rebuilt else 0
            ys0 = min(y - half.get(m, (0, 0))[1] - margin for m, (x, y) in local.items())
            ys1 = max(y + half.get(m, (0, 0))[1] + margin for m, (x, y) in local.items())
            if c_index == 0:
                oy = 0.0
            else:
                oy = max(prev_oy + ROW_SPACING, prev_bottom - ys0 + ROW_CLEAR)
            # 横向：按与已排链条的直接连接对齐，取中位数；否则按中心对齐。
            deltas = []
            for s, d in info_pairs:
                for u, v in ((s, d), (d, s)):
                    if u in local and v in positions:
                        delta = positions[v][0] - local[u][0]
                        if d in owners:
                            # 目标下方有原生 <Time>：错开一段，让输入从侧上方进入。
                            delta += TIME_SIDE_OFFSET if u == s else -TIME_SIDE_OFFSET
                        deltas.append(delta)
            if not deltas:
                for oid in aux:
                    nb = succ[oid] | pred[oid]
                    mine = [u for u in nb if u in local]
                    theirs = [v for v in nb if v in positions]
                    if mine and theirs:
                        deltas.append(
                            sum(positions[v][0] for v in theirs) / len(theirs)
                            - sum(local[u][0] for u in mine) / len(mine)
                        )
            if deltas:
                deltas.sort()
                ox = deltas[len(deltas) // 2]
            elif placed_rows:
                prev_center = placed_rows[-1]
                xs = [x for x, _ in local.values()]
                ox = prev_center - (min(xs) + max(xs)) / 2
            else:
                ox = 0.0
            ox = snap(ox)
            oy = snap(oy)
            for m, (x, y) in local.items():
                positions[m] = (ox + x, oy + y)
            xs = [positions[m][0] for m in local]
            placed_rows.append((min(xs) + max(xs)) / 2)
            prev_bottom, prev_oy = oy + ys1, oy
        return positions

    flips_template = {}
    for chain in chains:
        if chain.rebuilt:
            for label, options in chain.label_options.items():
                if label in objects:
                    flips_template[idx[label]] = (idx[chain.label_valve[label]], options)

    def heuristic_sides(skeleton_pos, invert=False):
        """流量名放在输入所在一侧：比较跨行骨架节点在管道上方还是下方。"""
        sides = {}
        for chain in chains:
            for label, options in chain.label_options.items():
                if len(options) != 2 or options[0][0] != 0 or options[1][0] != 0:
                    sides[label] = chain.label_default.get(label, 0)
                    continue
                above, below = (0, 1) if options[0][1] < 0 else (1, 0)
                if label in owners:
                    # 引用 Time 的流量名：原生在其正下方自动画 <Time>，放在管道上方
                    # 会让 <Time> 压在管道上，因此优先放在管道下方。
                    sides[label] = below
                    continue
                valve_y = skeleton_pos[chain.label_valve[label]][1]
                up = down = 0
                for other in pred[label] | succ[label]:
                    if other in skeleton_pos and other not in chain.members:
                        if skeleton_pos[other][1] < valve_y - 5:
                            up += 1
                        elif skeleton_pos[other][1] > valve_y + 5:
                            down += 1
                if up == down:
                    side = chain.label_default.get(label, 0)
                    if invert:
                        side = 1 - side
                else:
                    side = below if down > up else above
                sides[label] = side
        return sides

    # ---- 候选初始布局 ----
    def build_start(kind):
        sc = Scene(
            list(objects),
            half,
            info_pairs,
            scene_pipes,
            own_pairs,
            owners,
            W_DIR if no_skeleton else 0.0,
        )
        sc.info_ids = info_ids
        base_sides = {}
        reverse = kind == "greedy-rev"
        skeleton_pos = chain_positions(reverse=reverse)
        if kind in ("greedy", "greedy-alt", "greedy-shuffle", "greedy-rev"):
            base_sides = heuristic_sides(skeleton_pos, invert=(kind == "greedy-alt"))
        elif kind == "mapped":
            base_sides = {}
            for chain in chains:
                for label, options in chain.label_options.items():
                    valve = chain.label_valve[label]
                    dy = original[label][1] - original[valve][1]
                    dx = original[label][0] - original[valve][0]
                    sides_cost = [math.hypot(dx - ox, dy - oy) for ox, oy in options]
                    base_sides[label] = min(range(len(options)), key=sides_cost.__getitem__)
        skeleton_pos = chain_positions(base_sides, reverse=reverse)
        for oid, (x, y) in skeleton_pos.items():
            if oid in objects:
                sc.place(idx[oid], x, y)
        for oid in locked_ids:
            if oid in objects and oid not in skeleton:
                sc.place(idx[oid], *original[oid])
        for oid, (x, y) in anchors.items():
            if oid not in skeleton:
                sc.place(idx[oid], x, y)
        movable_aux = [oid for oid in aux]
        if kind == "mapped" or (kind == "layered" and not no_skeleton):
            # 沿用原图心智地图：按骨架的仿射对应关系映射原坐标。
            mapped = _map_original(objects, original, skeleton_pos, movable_aux)
            for oid in movable_aux:
                sc.place(idx[oid], *mapped[oid])
            _relieve_overlaps(sc, [idx[o] for o in movable_aux])
        elif kind == "layered" and no_skeleton:
            _layered_start(sc, movable_aux, succ, pred, half, original, objects, neighbors, roles)
        else:
            order_rng = (
                random.Random(rng_seed * 104729 + len(kind)) if kind == "greedy-shuffle" else None  # nosec B311 排版退火的可复现随机数，与安全无关
            )
            leftover = greedy_place(sc, movable_aux, neighbors, roles, order_rng, label_feeders)
            if leftover:
                _place_components(sc, leftover, succ, pred, half, original, objects)
        # 注释等无连线对象跟随原图中最近的已落位对象。
        for oid in free_kinds:
            ref = min(
                (o for o in objects if o not in free_kinds),
                key=lambda o: math.dist(original[o], original[oid]),
                default=None,
            )
            if ref is None or not sc.active[idx[ref]]:
                sc.place(idx[oid], *original[oid])
            else:
                rx, ry = sc.X[idx[ref]], sc.Y[idx[ref]]
                sc.place(
                    idx[oid],
                    snap(rx + original[oid][0] - original[ref][0]),
                    snap(ry + original[oid][1] - original[ref][1]),
                )
        for i in range(len(sc.ids)):
            if not sc.active[i]:
                sc.place(i, *original[sc.ids[i]])
        if locked_ids or anchors:
            # 存在锁定或锚点：新布局整体平移到原图坐标系，与固定对象保持相对关系。
            fixed = set(locked_ids) | set(anchors)
            free = [i for i in range(len(sc.ids)) if sc.ids[i] not in fixed]
            if free:
                cur_x = min(sc.X[i] - sc.HW[i] for i in free)
                cur_y = min(sc.Y[i] - sc.HH[i] for i in free)
                ref_x = min(original[sc.ids[i]][0] - sc.HW[i] for i in free)
                ref_y = min(original[sc.ids[i]][1] - sc.HH[i] for i in free)
                dx, dy = snap(ref_x - cur_x), snap(ref_y - cur_y)
                for i in free:
                    sc.place(i, sc.X[i] + dx, sc.Y[i] + dy)
        flips = {}
        for li, (vi, options) in flips_template.items():
            ox, oy = sc.X[li] - sc.X[vi], sc.Y[li] - sc.Y[vi]
            side = min(
                range(len(options)),
                key=lambda k: math.hypot(options[k][0] - ox, options[k][1] - oy),
            )
            flips[li] = (vi, options, side)
        return sc, flips

    groups_ids = (
        [
            [idx[m] for m in chain.members if m in objects]
            for chain in chains
            if not (set(chain.members) & (locked_ids | set(anchors)))
        ]
        if len(chains) > 1 and not skeleton_locked
        else []
    )
    # 镜像轴：每条重建链取一个存量（或阀门）作为中心线的代表。
    axis_nodes = []
    for chain in chains:
        if chain.rebuilt:
            ref = next(
                (m for m in chain.members if objects.get(m) is not None and objects[m].kind == 11),
                None,
            )
            if ref is not None:
                axis_nodes.append(idx[ref])
    # 回路标记等注释不参与退火。收尾时回路标记放回所在回路的中间（见
    # loop_marker_cycles），其余注释按周围对象的新位置映射（见 _follow_neighbors）
    valve_labels = {f.valve: f.label for f in flows.values() if f.label is not None}
    marker_cycles = loop_marker_cycles(
        view,
        objects,
        flows,
        [oid for oid in free_kinds if is_loop_marker(objects[oid]) and oid not in locked_ids],
    )
    movable_idx = [idx[oid] for oid in aux]
    fixed_refs = [oid for oid in objects if oid not in free_kinds]
    # 直接连到流量名的辅助变量（按场景下标）。
    label_ids = {f.label for f in flows.values() if f.label is not None}
    label_feeders = {idx[oid] for oid in aux if (succ[oid] | pred[oid]) & label_ids}
    starts = (
        ["greedy", "greedy-alt", "mapped", "greedy-shuffle"]
        if not no_skeleton
        else ["layered", "greedy", "mapped", "greedy-shuffle"]
    )
    if 2 <= len(chains) <= 4 and not skeleton_locked:
        # 行序是结构性选择，退火很难整行互换；少量存量链时再试一次反向行序。
        starts.append("greedy-rev")
    iterations = max(3000, min(16000, 320 * max(1, len(movable_idx) + len(flips_template))))

    def finish(sc, flips, extra_objects=None, repoint=None):
        positions = {
            sc.ids[i]: (int(round(sc.X[i])), int(round(sc.Y[i]))) for i in range(len(sc.ids))
        }
        notes = {}
        for oid in free_kinds:
            if oid not in locked_ids:
                region = None
                if oid in marker_cycles:
                    spot, region = _loop_spot(
                        marker_cycles[oid], positions, original[oid], original, valve_labels
                    )
                else:
                    spot = _follow_neighbors(original[oid], original, positions, fixed_refs)
                positions[oid] = _free_spot(oid, spot, positions, half, pipe_pairs, region)
                notes[oid] = (spot, region)
        # 平移到正坐标并留出页边（含 <Time> 预留区）。
        min_x = min(
            positions[o][0] - sc.HW[idx_any]
            for o, idx_any in ((sc.ids[i], i) for i in range(len(sc.ids)))
        )
        min_y = min(
            positions[o][1] - sc.HH[idx_any]
            for o, idx_any in ((sc.ids[i], i) for i in range(len(sc.ids)))
        )
        shift_x = snap(30 - min_x) if not locked_ids and not anchors else 0
        shift_y = snap(30 - min_y) if not locked_ids and not anchors else 0
        positions = {o: (x + shift_x, y + shift_y) for o, (x, y) in positions.items()}
        notes = {
            oid: (
                (spot[0] + shift_x, spot[1] + shift_y),
                [(x + shift_x, y + shift_y) for x, y in region] if region else None,
            )
            for oid, (spot, region) in notes.items()
        }
        pipe_points = {}
        shifted = {}
        for arrow in view.arrows:
            if arrow.from_id not in positions or arrow.to_id not in positions:
                continue
            if arrow.is_physical_flow and arrow.obj_id in straight_pipes:
                (x1, y1), (x2, y2) = positions[arrow.from_id], positions[arrow.to_id]
                if arrow.from_id in bent_valves or arrow.to_id in bent_valves:
                    # 折线管道的控制点就是拐角：端点正上（下）方、与阀门同高
                    valve = arrow.from_id if arrow.from_id in bent_valves else arrow.to_id
                    end = arrow.to_id if valve == arrow.from_id else arrow.from_id
                    pipe_points[arrow.obj_id] = (positions[end][0], positions[valve][1])
                else:
                    pipe_points[arrow.obj_id] = (
                        int(round((x1 + x2) / 2)),
                        int(round((y1 + y2) / 2)),
                    )
            elif arrow.points and arrow.obj_id not in routeable_ids:
                # 整体保留的管道与多控制点箭头：控制点随两端平均位移平移。
                moves = [
                    (
                        positions[end][0] - view.objects[end].x,
                        positions[end][1] - view.objects[end].y,
                    )
                    for end in (arrow.from_id, arrow.to_id)
                ]
                dx = sum(m[0] for m in moves) / 2
                dy = sum(m[1] for m in moves) / 2
                shifted[arrow.obj_id] = [
                    (int(round(x + dx)), int(round(y + dy))) for x, y in arrow.points
                ]
        trial = _positioned(
            view, positions, sizes, clouds, extra_objects, repoint, None, pipe_points, shifted
        )
        routeable_now = [a for a in trial.arrows if a.obj_id in routeable_ids]
        # 注释不是连线端点，先不当障碍布弧线，弧线定下来后再给注释找空位
        bare = dataclasses.replace(
            trial, objects={k: v for k, v in trial.objects.items() if k not in notes}
        )
        controls = route_arcs(bare, routeable_now)
        final_view = _positioned(
            view, positions, sizes, clouds, extra_objects, repoint, controls, pipe_points, shifted
        )
        settled = _settle_notes(final_view, notes)
        if settled:
            positions.update(settled)
            final_view = _positioned(
                view,
                positions,
                sizes,
                clouds,
                extra_objects,
                repoint,
                controls,
                pipe_points,
                shifted,
            )
        # 生成阶段按更宽的净距与箭头长度验收，交付时再用标准检查复核
        metrics = measure_view(final_view, LAYOUT_CLEARANCE, PREFERRED_ARROW)
        metrics["row_inversions"] = _row_inversions(chains, positions)
        metrics["alignment_lines"] = alignment_lines(
            positions, [oid for oid in positions if oid in objects and objects[oid].kind == 10]
        )
        sides = {}
        for li, (_vi, options, side) in flips.items():
            ox, oy = options[side]
            sides[sc.ids[li]] = (
                ("above" if oy < 0 else "below") if ox == 0 else ("right" if ox > 0 else "left")
            )
        return positions, controls, {**pipe_points, **shifted}, metrics, sides, final_view

    candidates = []
    # 开启影子拆分时，追加重启的作用被拆分取代，少做几轮以节省时间。
    split_enabled = config.get("split_shadows", True)
    # 允许影子拆分时，剩余缺陷交给拆分处理，追加重启次数相应减少
    big = len(objects) > LARGE_VIEW
    extra = (1 if big else 2) if split_enabled else (3 if big else 6)
    hot_t0 = 1500.0
    base_t0 = 40.0 if no_skeleton else 200.0
    schedule = [(kind, base_t0, iterations) for kind in starts]
    retry_kinds = ["greedy-shuffle", "greedy-alt", "greedy", "mapped"]
    attempt = -1
    while schedule:
        attempt += 1
        kind, t0_value, iters_value = schedule.pop(0)
        sc, flips = build_start(kind)
        for i in range(len(sc.ids)):
            sc.init[i] = (sc.X[i], sc.Y[i])
            oid = sc.ids[i]
            if oid in free_kinds:
                sc.drift_w[i] = 0.5
            elif oid in skeleton:
                sc.drift_w[i] = 0.1
            else:
                # 无骨架视图的分层/装箱起点已经整齐，偏离惩罚更强，退火只做局部修补。
                sc.drift_w[i] = 0.3 if no_skeleton else 0.08
        sc.align = {i: t for i, t in roles.items()}
        sc.bounds = _initial_bounds(sc)
        rng = random.Random(rng_seed * 7919 + attempt)  # nosec B311 排版退火的可复现随机数，与安全无关
        anneal(
            sc,
            movable_idx,
            groups_ids,
            flips,
            neighbors,
            rng,
            iters_value,
            t0=t0_value,
            axes=axis_nodes,
        )
        result = finish(sc, flips)
        candidates.append((kind, sc, flips, result))
        # 默认起点全部跑完后仍有缺陷：用更高初温、更多迭代追加确定性的重启。
        if (
            not schedule
            and extra > 0
            and min(selection_key(c[3][3])[:2] for c in candidates) != (0, 0)
        ):
            extra -= 1
            schedule.append((retry_kinds[extra % len(retry_kinds)], hot_t0, int(iterations * 1.5)))

    evaluated = []
    for kind, sc, flips, result in candidates:
        evaluated.append((selection_key(result[3]), kind, sc, flips, result))
    evaluated.sort(key=lambda row: row[0])
    best_key, best_kind, best_scene, best_flips, best_result = evaluated[0]

    # ---- 改进循环：仍有缺陷时交替进行“显式影子拆分”（若开启）与“温热打磨” ----
    state = {
        "scene": best_scene,
        "flips": best_flips,
        "result": best_result,
        "objects": {},
        "repoint": {},
        "splits": [],
        "movable": list(movable_idx),
        "next_id": max([*view.objects, *(a.obj_id for a in view.arrows)]) + 1,
    }
    split_limit = int(config.get("max_shadow_splits", 6)) if split_enabled else 0
    polish_left = POLISH_ROUNDS // 2 if big else POLISH_ROUNDS
    polish_seed = 0
    split_stale = False  # 上一轮拆分失败且状态未变：跳过，避免重复同样的试验
    for _ in range(split_limit + polish_left + 8):
        if selection_key(state["result"][3])[:2] == (0, 0):
            break
        improved = False
        if len(state["splits"]) < split_limit and not split_stale:
            improved = _split_round(view, lines, config, state, finish, groups_ids, axis_nodes)
            split_stale = not improved
        if not improved:
            improved = _repair_round(state, finish)
        if not improved:
            if polish_left <= 0:
                break
            polish_left -= 1
            polish_seed += 1
            improved = _polish_round(
                state, finish, groups_ids, axis_nodes, rng_seed * 15485863 + polish_seed, iterations
            )
        if improved:
            split_stale = False
    # ---- 收尾：行间紧凑化与局部精修（只接受精确检查不变差的结果） ----
    _tidy(state, finish, groups_ids, neighbors_of=lambda sc: sc.neighbor_sets())
    best_scene, best_flips, best_result = state["scene"], state["flips"], state["result"]
    splits = state["splits"]
    for item in splits:
        item["shadow_object"] = state["objects"][item["new_shadow_id"]]

    positions, controls, pipe_points, metrics, sides, final_view = best_result
    chains_report = [
        {
            "members": chain.members,
            "stocks": chain.stocks,
            "rebuilt": chain.rebuilt,
        }
        for chain in chains
    ]
    plan = Plan(
        positions, sizes, clouds, controls, pipe_points, splits, metrics, sides, chains_report
    )
    plan.strategy = best_kind
    plan.candidates = [
        {"start": row[1], "defects": row[0][1], "quality": list(row[0])} for row in evaluated
    ]
    plan.final_view = final_view
    plan.hidden = hidden
    plan.scene = best_scene
    plan.seed_used = rng_seed
    return plan


def _initial_value_links(lines):
    """返回 (源变量, 存量) 的规范名对：源只出现在该存量 INTEG 的初值表达式中。"""
    from vensim_engine import canonical_name, extract_deps, parse_equations

    text = "".join(lines)
    try:
        equations = parse_equations(re.sub(r"\\\r?\n[ \t]*", "", text), expand=False)
    except ValueError:
        return set()
    names = set(equations)
    links = set()
    for name, equation in equations.items():
        if equation.integ_flow is None:
            continue
        rate = set(extract_deps(equation.integ_flow, names))
        for dep in extract_deps(equation.integ_init_expr or "", names):
            if dep not in rate:
                links.add((canonical_name(dep), canonical_name(name)))
    return links


# ---------------------------------------------------------------------------
# 回路标记：重建后放回原来所在回路的内部
# ---------------------------------------------------------------------------


def is_loop_marker(obj) -> bool:
    """Vensim 回路标记：12 类注释，形状 4/5 是顺/逆时针回路圈，下一行写 R1、B2 等。"""
    return obj.kind == 12 and obj.shape & 31 in (4, 5)


def _feedback_cycles(view, objects, flows, max_len=12, max_count=3000):
    """因果图中的简单回路（节点序列）。流量名并入阀门，影子并入同名的定义变量，
    流量指向两端存量。"""
    merge = {f.label: f.valve for f in flows.values() if f.label is not None}
    defined = {
        obj.name: oid for oid, obj in objects.items() if obj.kind == 10 and not obj.is_shadow
    }
    for oid, obj in objects.items():
        if obj.kind == 10 and obj.is_shadow and obj.name in defined:
            target = defined[obj.name]
            merge[oid] = merge.get(target, target)
    adj = defaultdict(set)
    for arrow in view.arrows:
        if arrow.is_physical_flow:
            continue
        a, b = merge.get(arrow.from_id, arrow.from_id), merge.get(arrow.to_id, arrow.to_id)
        if a != b and a in objects and b in objects:
            adj[a].add(b)
    for flow in flows.values():
        for end in (flow.upstream, flow.downstream):
            if end in objects and objects[end].kind == 10:
                adj[flow.valve].add(end)
    cycles = []
    for start in sorted(adj):
        stack = [(start, (start,))]
        while stack and len(cycles) < max_count:
            node, path = stack.pop()
            for nxt in sorted(adj[node]):
                if nxt == start:
                    cycles.append(path)
                elif nxt > start and nxt not in path and len(path) < max_len:
                    stack.append((nxt, (*path, nxt)))
    return cycles, merge


def _polygon(cycle, coords, controls):
    """回路在图上围成的多边形：节点中心加上连线的弧线控制点。"""
    points = []
    for i, node in enumerate(cycle):
        points.append(coords[node])
        nxt = cycle[(i + 1) % len(cycle)]
        if (node, nxt) in controls:
            points.append(controls[(node, nxt)])
    return points


def _area(points):
    return (
        abs(
            sum(
                x1 * y2 - x2 * y1
                for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1], strict=True)
            )
        )
        / 2
    )


def _contains(point, points):
    """射线法判断点是否在多边形内。"""
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def loop_marker_cycles(view, objects, flows, markers):
    """给每个回路标记找到它在原图中所在的回路：包住它的回路里面积最小的一条。

    Sterman 和 Vensim 示例都把 R/B 画在所标回路的内部；按这个约定反推标记属于
    哪条回路，重建后放回该回路中间。多个标记落在同一条最小回路里时，后面的
    标记顺延到下一条包住它的回路。找不到时返回结果里没有这个标记。
    """
    cycles, merge = _feedback_cycles(view, objects, flows)
    if not cycles:
        return {}
    coords = {oid: (obj.x, obj.y) for oid, obj in objects.items()}
    controls = {}
    for arrow in view.arrows:
        if not arrow.is_physical_flow and len(arrow.points) == 1:
            key = (merge.get(arrow.from_id, arrow.from_id), merge.get(arrow.to_id, arrow.to_id))
            controls[key] = arrow.points[0]
    polygons = [_polygon(cycle, coords, controls) for cycle in cycles]
    ranked = {
        marker: [
            cycles[i]
            for i in sorted(
                (i for i, poly in enumerate(polygons) if _contains(coords[marker], poly)),
                key=lambda i: _area(polygons[i]),
            )
        ]
        for marker in markers
    }
    chosen, used = {}, set()
    for marker in sorted(markers, key=lambda m: (len(ranked[m]), m)):
        options = ranked[marker]
        if options:
            pick = next((c for c in options if c not in used), options[0])
            chosen[marker] = pick
            used.add(pick)
    return chosen


def _loop_spot(cycle, positions, original_marker, original, labels):
    """回路新位置的中心，以及判断“在回路内部”的多边形（两节点回路没有多边形）。

    两节点回路（存量与自己的流量）的中心落在管道上：信息线连到流量名时，回路
    围在管道和流量名之间，标记偏向流量名一侧；否则沿用原图标记所在的一侧。
    """
    points = [positions[n] for n in cycle]
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    if len(points) > 2:
        return (snap(cx), snap(cy)), points
    (a, b), (c, d) = points
    label = next((labels[n] for n in cycle if labels.get(n) in positions), None)
    if label is not None:
        ref, (x1, y1), (x2, y2) = positions[label], (a, b), (c, d)
    else:
        ref, (x1, y1), (x2, y2) = original_marker, *(original[n] for n in cycle)
    side = (x2 - x1) * (ref[1] - y1) - (y2 - y1) * (ref[0] - x1)
    length = math.hypot(c - a, d - b) or 1.0
    offset = 30 if side >= 0 else -30
    return (snap(cx - (d - b) / length * offset), snap(cy + (c - a) / length * offset)), None


def _follow_neighbors(point, original, positions, refs, count=4):
    """用原图中最近的几个对象拟合仿射映射，把注释从原坐标映射到新坐标。

    回路标记 R/B 画在回路内部，跟着周围变量一起变换才能留在原来的回路里。
    点数不足或共线时退化为按最近对象的平移。
    """
    near = sorted(
        (oid for oid in refs if oid in positions and oid in original),
        key=lambda oid: math.dist(original[oid], point),
    )[:count]
    if not near:
        return snap(point[0]), snap(point[1])
    if len(near) >= 3:
        # 最小二乘求 x' = a·x + b·y + c、y' = d·x + e·y + f
        rows = [(original[o][0], original[o][1], 1.0) for o in near]
        normal = [[sum(r[i] * r[j] for r in rows) for j in range(3)] for i in range(3)]

        def solve(rhs):
            m = [
                row[:] + [sum(r[i] * v for r, v in zip(rows, rhs, strict=True))]
                for i, row in enumerate(normal)
            ]
            for col in range(3):
                pivot = max(range(col, 3), key=lambda r: abs(m[r][col]))
                if abs(m[pivot][col]) < 1e-6:
                    return None
                m[col], m[pivot] = m[pivot], m[col]
                for r in range(3):
                    if r != col:
                        factor = m[r][col] / m[col][col]
                        m[r] = [a - factor * b for a, b in zip(m[r], m[col], strict=True)]
            return [m[i][3] / m[i][i] for i in range(3)]

        ax = solve([positions[o][0] for o in near])
        ay = solve([positions[o][1] for o in near])
        if ax and ay:
            x = ax[0] * point[0] + ax[1] * point[1] + ax[2]
            y = ay[0] * point[0] + ay[1] * point[1] + ay[2]
            # 重建不是整体仿射，外推可能把注释甩到远处：超出参考对象新位置的
            # 范围（按注释到最近对象的原距离外扩）时改用平移
            reach = math.dist(original[near[0]], point) + 40
            xs = [positions[o][0] for o in near]
            ys = [positions[o][1] for o in near]
            if min(xs) - reach <= x <= max(xs) + reach and min(ys) - reach <= y <= max(ys) + reach:
                return snap(x), snap(y)
    ref = near[0]
    return (
        snap(point[0] + positions[ref][0] - original[ref][0]),
        snap(point[1] + positions[ref][1] - original[ref][1]),
    )


def _free_spot(oid, spot, positions, half, pipes=(), region=None):
    """注释映射后若压住其他文字框或管道，在周围由近到远找第一个空位。

    给出 region（回路多边形）时先在多边形内部找，找不到再放宽到任意空位。
    """
    w, h = half[oid]
    segments = [
        _segment(*positions[a], *half[a], *positions[b], *half[b])
        for a, b in pipes
        if a in positions and b in positions
    ]

    def clear(x, y):
        return all(
            abs(x - px) >= w + half[other][0] + GAP_X or abs(y - py) >= h + half[other][1] + GAP_Y
            for other, (px, py) in positions.items()
            if other != oid and other in half
        ) and not any(_hits(s, x, y, w + 4, h + 4) for s in segments)

    rings = [(spot[0], spot[1])] + [
        (
            snap(spot[0] + radius * math.cos(2 * math.pi * k / 12)),
            snap(spot[1] + radius * math.sin(2 * math.pi * k / 12)),
        )
        for radius in (20, 40, 60, 80, 110, 140)
        for k in range(12)
    ]
    for inside in [True, False] if region else [False]:
        for x, y in rings:
            if (not inside or _contains((x, y), region)) and clear(x, y):
                return x, y
    return spot


def _settle_notes(view, notes):
    """弧线定下来以后复查注释：压到连线、文字框或 <Time> 预留区的，在原定位置
    附近由近到远另找空位，有回路多边形时先在多边形内部找。返回挪动过的坐标。"""
    if not notes:
        return {}
    paths = [arrow_path(view, a) for a in view.arrows if shown(view, a)]
    blocked = [time_label_box(owner) for owner in time_label_owners(view)]
    moved = {}
    for oid, (spot, region) in notes.items():
        obj = view.objects.get(oid)
        if obj is None or not visible(obj):
            continue
        others = [
            box(other, LAYOUT_CLEARANCE / 2)
            for other in view.objects.values()
            if other.obj_id != oid and visible(other)
        ] + blocked

        def clear(x, y, obj=obj, others=others):
            # 与 measure_view 同口径：框之间各留半个净距，线与框留一个净距
            moved_obj = dataclasses.replace(obj, x=x, y=y)
            rect = box(moved_obj, LAYOUT_CLEARANCE / 2)
            line_rect = box(moved_obj, LAYOUT_CLEARANCE)
            return not any(boxes_intersect(rect, other) for other in others) and not any(
                path_hits_box(path, line_rect) for path in paths
            )

        if clear(obj.x, obj.y):
            continue
        rings = [spot] + [
            (
                snap(spot[0] + radius * math.cos(2 * math.pi * k / 16)),
                snap(spot[1] + radius * math.sin(2 * math.pi * k / 16)),
            )
            for radius in range(10, 161, 10)
            for k in range(16)
        ]
        for inside in [True, False] if region else [False]:
            found = next(
                (p for p in rings if (not inside or _contains(p, region)) and clear(*p)),
                None,
            )
            if found is not None:
                moved[oid] = found
                break
    return moved


def _row_inversions(chains, positions):
    """存量链的上下顺序与原图顺序相反的对数。"""
    centers = []
    for chain in chains:
        ys = [positions[m][1] for m in chain.members if m in positions]
        if ys:
            centers.append(sum(ys) / len(ys))
    return sum(
        1
        for i in range(len(centers))
        for j in range(i + 1, len(centers))
        if centers[i] > centers[j] + 20
    )


def _initial_bounds(scene):
    active = [i for i in range(len(scene.ids)) if scene.active[i]]
    if not active:
        return None
    return (
        min(scene.X[i] - scene.HW[i] for i in active) - BOUND_MARGIN,
        min(scene.Y[i] - scene.HH[i] for i in active) - BOUND_MARGIN,
        max(scene.X[i] + scene.HW[i] for i in active) + BOUND_MARGIN,
        max(scene.Y[i] + scene.HH[i] + TIME_LABEL_OFFSET for i in active) + BOUND_MARGIN,
    )


def selection_key(metrics):
    """先比较与 visual 同口径的缺陷数，再合并比较连线总长、版面宽高与整齐度。

    整齐度用节点中心落在多少条不同的横/竖对齐线上衡量（10px 取整）：对齐线
    越少，人工摆放的“成行成列”感越强。
    """
    key = quality_key(metrics)
    total = key[1] + key[2] + key[3]
    return (
        key[0],
        total,
        *key[1:4],
        round(
            key[4]
            + 0.6 * key[5]
            + 6.0 * metrics.get("alignment_lines", 0)
            + ROW_ORDER_PENALTY * metrics.get("row_inversions", 0),
            1,
        ),
    )


def alignment_lines(positions, ids):
    xs = {int(round(positions[i][0] / GRID)) for i in ids if i in positions}
    ys = {int(round(positions[i][1] / GRID)) for i in ids if i in positions}
    return len(xs) + len(ys)


def _map_original(objects, original, skeleton_pos, movable):
    """按骨架原坐标与新坐标的轴向线性拟合，映射其余对象的原坐标。"""
    pairs = [(original[o], skeleton_pos[o]) for o in skeleton_pos if o in original]
    out = {}

    def fit(axis):
        if len(pairs) < 2:
            return 1.0, 0.0
        xs = [p[0][axis] for p in pairs]
        ys = [p[1][axis] for p in pairs]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        var = sum((x - mx) ** 2 for x in xs)
        if var < 1e-6:
            return 1.0, my - mx
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False)) / var
        slope = min(1.2, max(0.35, slope))
        return slope, my - slope * mx

    if pairs:
        (ax, bx), (ay, by) = fit(0), fit(1)
    else:
        # 无骨架：按最近邻中位距离压缩到约 130px，保持原图相对位置。
        pts = [original[o] for o in movable]
        dists = []
        for i, p in enumerate(pts):
            best = min((math.dist(p, q) for j, q in enumerate(pts) if j != i), default=130)
            dists.append(best)
        dists.sort()
        median = dists[len(dists) // 2] if dists else 130
        factor = min(1.0, max(0.4, 130 / max(median, 1)))
        ax = ay = factor
        bx = by = 0.0
    for oid in movable:
        x, y = original[oid]
        out[oid] = (snap(ax * x + bx), snap(ay * y + by))
    return out


def _relieve_overlaps(scene, nodes):
    """把映射后互相覆盖的节点就近挪到留白处。"""
    for i in nodes:
        detail = {}
        scene.partial({i}, detail)
        if not detail.get("overlaps"):
            continue
        x0, y0 = scene.X[i], scene.Y[i]
        best, best_cost = (x0, y0), None
        for radius in (20, 40, 70, 110, 160):
            for k in range(12):
                theta = 2 * math.pi * k / 12
                x, y = snap(x0 + radius * math.cos(theta)), snap(y0 + radius * math.sin(theta))
                scene.place(i, x, y)
                cost = scene.partial({i})
                if best_cost is None or cost < best_cost:
                    best, best_cost = (x, y), cost
        scene.place(i, *best)


def _layered_start(scene, movable, succ, pred, half, original, objects, neighbors, roles):
    """无骨架视图：计算主干分层装箱，纯参数再围绕其作用对象落位。

    纯参数（无输入、只作用于一个对象）若也参与分层，会在第一列堆成高柱，
    把整张图拉长；人工绘图通常把它们放在作用对象旁边。
    """
    movable_set = set(movable)
    candidates = {
        n
        for n in movable
        if not pred[n] and len(succ[n]) == 1 and next(iter(succ[n])) in movable_set
    }
    core_set = movable_set - candidates
    # 主干连通块：只在“主干至少两个节点”的模块里把纯参数拿出来；单节点主干
    # （如 <输入> → 累计量 的成对结构、小扇入）保留在分层中，装箱后成行成列。
    component = {}
    for start in sorted(core_set):
        if start in component:
            continue
        stack, members = [start], set()
        while stack:
            n = stack.pop()
            if n in members:
                continue
            members.add(n)
            stack.extend((succ[n] | pred[n]) & core_set - members)
        for n in members:
            component[n] = len(members)
    satellites = [n for n in sorted(candidates) if component.get(next(iter(succ[n])), 1) >= 2]
    core = [n for n in movable if n not in set(satellites)]
    _place_components(scene, core, succ, pred, half, original, objects, origin=(0.0, 0.0))
    leftover = greedy_place(scene, satellites, neighbors, roles)
    if leftover:
        _place_components(scene, leftover, succ, pred, half, original, objects)


def _place_components(scene, nodes, succ, pred, half, original, objects, origin=None):
    nodes = [n for n in nodes if not scene.active[scene.index[n]]]
    if not nodes:
        return
    remaining = set(nodes)
    comps = []
    while remaining:
        start = min(remaining, key=lambda n: (original[n][1], original[n][0], n))
        comp, stack = set(), [start]
        while stack:
            n = stack.pop()
            if n in comp:
                continue
            comp.add(n)
            stack.extend((succ[n] | pred[n]) & remaining - comp)
        remaining -= comp
        comps.append(comp)
    blocks = [layered_positions(sorted(c), succ, pred, half, original) for c in comps]
    blocks.sort(key=lambda b: (-len(b), min(original[n][1] for n in b)))
    area = sum(
        (
            max(x + half[n][0] for n, (x, y) in b.items())
            - min(x - half[n][0] for n, (x, y) in b.items())
            + 70
        )
        * (
            max(y + half[n][1] for n, (x, y) in b.items())
            - min(y - half[n][1] for n, (x, y) in b.items())
            + 90
        )
        for b in blocks
    )
    widest = max(
        max(x + half[n][0] for n, (x, y) in b.items())
        - min(x - half[n][0] for n, (x, y) in b.items())
        for b in blocks
    )
    max_width = max(widest, math.sqrt(area * 1.7))
    if origin is None:
        active = [i for i in range(len(scene.ids)) if scene.active[i]]
        if active:
            min_x = min(scene.X[i] - scene.HW[i] for i in active)
            max_y = max(scene.Y[i] + scene.HH[i] for i in active)
            origin = (min_x, max_y + 90)
            max_width = max(max_width, max(scene.X[i] + scene.HW[i] for i in active) - min_x)
        else:
            origin = (0.0, 0.0)
    placed = pack_blocks(blocks, half, origin, max_width)
    for n, (x, y) in placed.items():
        scene.place(scene.index[n], x, y)


# ---------------------------------------------------------------------------
# 显式影子拆分
# ---------------------------------------------------------------------------


def _defect_arrows(metrics):
    """参与缺陷的信息线及其次数：交叉、穿管优先，其次穿字、过短与 <Time> 冲突。"""
    involvement = Counter()
    for a, b in metrics["arrow_crossings"]:
        involvement[a] += 3
        involvement[b] += 3
    for a, _pipe in metrics["flow_crossings"]:
        involvement[a] += 3
    for row in metrics["arrow_node_collisions"]:
        involvement[row["arrow"]] += 1
    for row in metrics.get("short_arrows", []):
        involvement[row["arrow"]] += 1
    for row in metrics.get("time_label_conflicts", []):
        if "arrow" in row:
            involvement[row["arrow"]] += 1
    return involvement


def _split_round(view, lines, config, state, finish, groups, axes):
    """尝试一处影子拆分：复制同名影子并改指一条箭头的起点，因果关系不变。

    候选为参与缺陷最多的信息线（源对象至少连着两条线，否则直接移动源对象即可）。
    影子先落在目标附近并做局部退火，排名前两位的试验再整体退火一轮；只有
    与 visual 同口径的缺陷总数下降时才接受。
    """
    from vensim_autolayout import _is_information_arrow

    scene, flips, result = state["scene"], state["flips"], state["result"]
    font = view_font_size(lines, view)
    arrows_by_id = {a.obj_id: a for a in view.arrows}
    involvement = _defect_arrows(result[3])
    big = len(view.objects) > LARGE_VIEW
    limit = SPLIT_CANDIDATES - 3 if big else SPLIT_CANDIDATES
    ranked = sorted(involvement, key=lambda aid: (-involvement[aid], aid))[:limit]
    trials = []
    for arrow_id in ranked:
        arrow = arrows_by_id.get(arrow_id)
        if (
            arrow is None
            or arrow_id in state["repoint"]
            or not _is_information_arrow(arrow, view.objects)
        ):
            continue
        source = view.objects.get(arrow.from_id)
        if source is None or source.kind != 10:
            continue
        degree = sum(1 for a in view.arrows if arrow.from_id in (a.from_id, a.to_id))
        if degree < 2 or arrow_id not in scene.info_ids:
            continue
        trial = scene.clone()
        new_id = state["next_id"]
        # 影子显示为 <名称>，框宽要算上两侧尖括号
        half_width = text_half_width(f"<{source.name}>", font) + 6
        new_index = trial.add_node(
            new_id,
            half_width + SHADOW_MARGIN,
            16.0 if source.stock_like else max(12.0, abs(source.h)),
        )
        trial.repoint(trial.info_ids.index(arrow_id), new_index)
        nb = trial.neighbor_sets()
        greedy_place(trial, [new_id], nb, {})
        trial.init[new_index] = (trial.X[new_index], trial.Y[new_index])
        trial.drift_w[new_index] = 0.08
        local_flips = dict(flips)
        rng = random.Random(int(config.get("seed", 1)) * 31 + new_id + arrow_id)  # nosec B311 排版退火的可复现随机数，与安全无关
        anneal(
            trial,
            [*state["movable"], new_index],
            [],
            local_flips,
            nb,
            rng,
            SPLIT_LOCAL_ITERS,
            t0=120.0,
            axes=axes,
        )
        objects = {**state["objects"], new_id: _shadow_object(view, source, new_id, half_width)}
        repoint = {**state["repoint"], arrow_id: new_id}
        trial_result = finish(trial, local_flips, objects, repoint)
        key = selection_key(trial_result[3])
        if key[1]:
            found = _relocate(trial, local_flips, new_index, finish, objects, repoint, key, keep=4)
            if found is not None:
                key, trial, trial_result = found
        trials.append(
            [
                key,
                trial,
                local_flips,
                trial_result,
                objects,
                repoint,
                arrow,
                source,
                new_id,
                new_index,
            ]
        )
    if not trials:
        return False
    trials.sort(key=lambda row: row[0])
    for row in trials[: 1 if big else SPLIT_SETTLE]:
        (
            key,
            trial,
            local_flips,
            _trial_result,
            objects,
            repoint,
            arrow,
            _source,
            new_id,
            new_index,
        ) = row
        settled = trial.clone()
        settled_flips = dict(local_flips)
        for i in range(len(settled.ids)):
            if settled.active[i]:
                settled.init[i] = (settled.X[i], settled.Y[i])
        rng = random.Random(int(config.get("seed", 1)) * 37 + new_id + arrow.obj_id)  # nosec B311 排版退火的可复现随机数，与安全无关
        anneal(
            settled,
            [*state["movable"], new_index],
            list(groups),
            settled_flips,
            settled.neighbor_sets(),
            rng,
            SPLIT_SETTLE_ITERS,
            t0=800.0,
            axes=axes,
        )
        settled_result = finish(settled, settled_flips, objects, repoint)
        settled_key = selection_key(settled_result[3])
        if settled_key < key:
            row[0:4] = [settled_key, settled, settled_flips, settled_result]
    best = min(trials, key=lambda row: row[0])
    if best[0] >= selection_key(result[3]) or best[0][:2] >= selection_key(result[3])[:2]:
        return False
    key, scene, flips, result, objects, repoint, arrow, source, new_id, new_index = best
    state.update(scene=scene, flips=flips, result=result, objects=objects, repoint=repoint)
    state["movable"].append(new_index)
    state["splits"].append(
        {
            "arrow": arrow.obj_id,
            "variable": source.name,
            "original_source": arrow.from_id,
            "new_shadow_id": new_id,
            "target": arrow.to_id,
        }
    )
    state["next_id"] += 1
    return True


def _defects(scene, nodes):
    detail = {}
    scene.partial(set(nodes), detail)
    return (
        detail["cross"]
        + detail["pipe_cross"]
        + detail["hits"]
        + detail["overlaps"]
        + detail["short"]
    )


def _tidy(state, finish, groups, neighbors_of):
    """行间紧凑化 + 单/双邻居节点的局部精修。

    1. 每条存量链连同其“专属”辅助变量（所有连线都在该块内）向上一行靠拢，
       步长 20px，直到快速模型出现新缺陷；用于消除拆分影子后留下的大块空白。
    2. 只有一两个邻居的辅助变量，在其邻居周围的环形候选中寻找代价更低的位置。
    两步都在副本上进行，精确几何检查的选择键变好才接受。
    """
    scene = state["scene"].clone()
    nb = neighbors_of(scene)
    movable = set(state["movable"])
    changed = False
    ordered = sorted(groups, key=lambda g: sum(scene.Y[i] for i in g) / len(g))
    for g_index, group in enumerate(ordered[1:], start=1):
        block = set(group)
        grow = True
        while grow:
            grow = False
            for i in list(movable - block):
                if nb[i] and nb[i] <= block | {i}:
                    block.add(i)
                    grow = True
        above = ordered[g_index - 1]
        gap_now = min(scene.Y[i] for i in group) - max(scene.Y[i] for i in above)
        baseline = _defects(scene, block)
        moved = 0
        while gap_now - moved - 20 >= ROW_SPACING * 0.6:
            for i in block:
                scene.Y[i] -= 20
            for i in block:
                scene.refresh(i)
            if _defects(scene, block) > baseline:
                for i in block:
                    scene.Y[i] += 20
                for i in block:
                    scene.refresh(i)
                break
            moved += 20
        changed |= moved > 0
    for i in sorted(movable):
        if not scene.active[i] or not 1 <= len(nb[i]) <= 2:
            continue
        x0, y0 = scene.X[i], scene.Y[i]
        best_cost = scene.partial({i})
        best = (x0, y0)
        for j in nb[i]:
            for x, y in _ring(
                scene.X[j],
                scene.Y[j],
                scene.HW[j],
                scene.HH[j],
                scene.HW[i],
                scene.HH[i],
                (0, 30, 70),
                16,
            ):
                scene.X[i], scene.Y[i] = x, y
                scene.refresh(i)
                cost = scene.partial({i})
                if cost < best_cost - 1e-6:
                    best_cost, best = cost, (x, y)
        scene.X[i], scene.Y[i] = best
        scene.refresh(i)
        changed |= best != (x0, y0)
    if not changed:
        return False
    result = finish(scene, state["flips"], state["objects"] or None, state["repoint"] or None)
    if selection_key(result[3]) < selection_key(state["result"][3]):
        state.update(scene=scene, result=result)
        return True
    return False


def _relocate(scene, flips, node, finish, objects, repoint, current_key, keep=8):
    """单个节点的精确局部修补：快速模型筛选环形候选，再用精确检查比较前几名。"""
    nb = scene.neighbor_sets()
    if not nb[node]:
        return None
    x0, y0 = scene.X[node], scene.Y[node]
    screened = []
    for j in nb[node]:
        for x, y in _ring(
            scene.X[j],
            scene.Y[j],
            scene.HW[j],
            scene.HH[j],
            scene.HW[node],
            scene.HH[node],
            (0, 40, 90, 150, 220),
            16,
        ):
            scene.X[node], scene.Y[node] = x, y
            scene.refresh(node)
            screened.append((scene.partial({node}), x, y))
    scene.X[node], scene.Y[node] = x0, y0
    scene.refresh(node)
    best = None
    for _cost, x, y in sorted(dict.fromkeys(screened))[:keep]:
        trial = scene.clone()
        trial.X[node], trial.Y[node] = x, y
        trial.refresh(node)
        result = finish(trial, flips, objects or None, repoint or None)
        key = selection_key(result[3])
        if key < current_key and (best is None or key < best[0]):
            best = (key, trial, result)
    return best


def _repair_round(state, finish):
    """对参与剩余缺陷的可移动节点逐个做精确局部修补，接受第一处改进。"""
    involvement = _defect_arrows(state["result"][3])
    final_view = state["result"][5]
    scene = state["scene"]
    movable = set(state["movable"])
    nodes = []
    for arrow_id, _count in sorted(involvement.items(), key=lambda row: (-row[1], row[0])):
        arrow = next((a for a in final_view.arrows if a.obj_id == arrow_id), None)
        if arrow is None:
            continue
        for oid in (arrow.from_id, arrow.to_id):
            index = scene.index.get(oid)
            if index is not None and index in movable and index not in nodes:
                nodes.append(index)
    current = selection_key(state["result"][3])
    for node in nodes[:6]:
        found = _relocate(
            scene, state["flips"], node, finish, state["objects"], state["repoint"], current
        )
        if found is not None:
            key, trial, result = found
            state.update(scene=trial, result=result)
            return True
    return False


def _polish_round(state, finish, groups, axes, seed, iterations):
    """从当前最优场景出发的一轮温热退火；只有缺陷或美观代价下降才接受。"""
    trial = state["scene"].clone()
    trial_flips = dict(state["flips"])
    for i in range(len(trial.ids)):
        if trial.active[i]:
            trial.init[i] = (trial.X[i], trial.Y[i])
    anneal(
        trial,
        state["movable"],
        list(groups),
        trial_flips,
        trial.neighbor_sets(),
        random.Random(seed),  # nosec B311 排版退火的可复现随机数，与安全无关
        max(6000, iterations),
        t0=600.0,
        axes=axes,
    )
    result = finish(trial, trial_flips, state["objects"] or None, state["repoint"] or None)
    if selection_key(result[3]) < selection_key(state["result"][3]):
        state.update(scene=trial, flips=trial_flips, result=result)
        return True
    return False


def _shadow_object(view, source, new_id, half_width):
    """以本视图已有影子为模板（否则以源对象为模板）构造新的影子实例。"""
    template = next(
        (
            obj
            for obj in view.objects.values()
            if obj.kind == 10 and obj.is_shadow and not obj.attached_to_valve
        ),
        None,
    )
    if template is not None:
        fields = list(template.raw_fields)
        fields[1] = str(new_id)
        fields[2] = source.raw_fields[2]
        fields[5] = str(int(round(half_width)))
        fields[6] = str(int(abs(template.h)))
    else:
        fields = list(source.raw_fields)
        fields[1] = str(new_id)
        fields[5] = str(int(round(half_width)))
        fields[6] = str(int(max(11, min(abs(source.h), 16))))
        fields[7] = str(_safe_shape(source))
        # 偶数 bits 表示影子：清除“允许入线”位，保留“允许出线”位。
        bits = int(fields[8]) if fields[8].lstrip("-").isdigit() else 3
        fields[8] = str((bits | 2) & ~1)
    if len(fields) > 9:
        fields[9] = "0"
    return dataclasses.replace(
        source,
        obj_id=new_id,
        name=source.name,
        w=float(fields[5]),
        h=float(fields[6]),
        shape=int(fields[7]) if fields[7].lstrip("-").isdigit() else 8,
        bits=int(fields[8]) if fields[8].lstrip("-").isdigit() else 2,
        raw_fields=fields,
        line_index=-1,
    )


def _safe_shape(source):
    # 影子不画存量方框，也不附着阀门：形状码改为普通文字 8。
    return 8


# ---------------------------------------------------------------------------
# 报告辅助
# ---------------------------------------------------------------------------


def explain_crossings(view, metrics, planarity, label_sides):
    """为剩余交叉给出位置与原因，便于人工判断是否需要影子拆分。"""
    labels = attached_label_map(view)
    label_ids = set(labels.values())
    valve_of_label = {label: valve for valve, label in labels.items()}
    kuratowski = set()
    for row in planarity.get("kuratowski_edges", []):
        kuratowski.add((row[0], row[1]))
    merge = {label: valve for valve, label in labels.items()}

    def edge(arrow_id):
        arrow = next((a for a in view.arrows if a.obj_id == arrow_id), None)
        if arrow is None:
            return None, None
        return arrow, tuple(
            sorted((merge.get(arrow.from_id, arrow.from_id), merge.get(arrow.to_id, arrow.to_id)))
        )

    def name(oid):
        obj = view.objects.get(oid)
        return obj.name if obj else str(oid)

    rows = []
    for a, b in metrics["arrow_crossings"]:
        arrow_a, edge_a = edge(a)
        arrow_b, edge_b = edge(b)
        reason = "non_planar" if edge_a in kuratowski and edge_b in kuratowski else "unresolved"
        rows.append(
            {
                "type": "arrow_crossing",
                "arrows": [a, b],
                "between": [
                    f"{name(arrow_a.from_id)}→{name(arrow_a.to_id)}",
                    f"{name(arrow_b.from_id)}→{name(arrow_b.to_id)}",
                ],
                "reason": reason,
            }
        )
    for a, pipe in metrics["flow_crossings"]:
        arrow_a, _ = edge(a)
        pipe_arrow = next((x for x in view.arrows if x.obj_id == pipe), None)
        valve = None
        if pipe_arrow is not None:
            valve = (
                pipe_arrow.from_id
                if view.objects[pipe_arrow.from_id].kind == 11
                else pipe_arrow.to_id
            )
        reason = "unresolved"
        if (
            arrow_a.to_id in label_ids
            and valve is not None
            and valve_of_label.get(arrow_a.to_id) == valve
        ):
            reason = "label_side"
        elif arrow_a.to_id in label_ids or arrow_a.from_id in label_ids:
            reason = "label_side"
        rows.append(
            {
                "type": "flow_crossing",
                "arrow": a,
                "pipe": pipe,
                "between": [f"{name(arrow_a.from_id)}→{name(arrow_a.to_id)}", f"管道 {pipe}"],
                "reason": reason,
            }
        )
    return rows


def arc_summary(plan, view):
    ratios = []
    for arrow_id, (control, _k) in plan.controls.items():
        arrow = next(a for a in plan.final_view.arrows if a.obj_id == arrow_id)
        source, target = (
            plan.final_view.objects[arrow.from_id],
            plan.final_view.objects[arrow.to_id],
        )
        ratios.append(abs(arc_ratio(source, target, control)))
    curved = [r for r in ratios if r > 0.005]
    return {
        "arrows": len(ratios),
        "curved": len(curved),
        "mean_abs_ratio": round(sum(curved) / len(curved), 3) if curved else 0.0,
        "max_abs_ratio": round(max(ratios), 3) if ratios else 0.0,
    }


# ---------------------------------------------------------------------------
# 回写 MDL 文本
# ---------------------------------------------------------------------------

_FIELD_SPLIT = re.compile(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)")


def _set_fields(line, updates):
    """按字段序号改写对象记录；引号内的逗号不参与分割，换行符原样保留。"""
    from vensim_autolayout import _line_body, _line_ending

    fields = _FIELD_SPLIT.split(_line_body(line))
    for index, value in updates.items():
        if index < len(fields):
            fields[index] = str(value)
    return ",".join(fields) + _line_ending(line)


def _set_points(line, points):
    """只替换箭头记录的控制点列表与点数，形状、颜色、极性等字段不变。"""
    from vensim_autolayout import _line_body, _line_ending, format_points, split_arrow_record

    fields, tail = split_arrow_record(_line_body(line))
    old_count = int(fields[-1]) if fields[-1].isdigit() else 0
    matches = list(re.finditer(r"\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\)\|?", tail))[
        :old_count
    ]
    rest = tail[matches[-1].end() :] if matches else tail
    fields[-1] = str(len(points))
    return ",".join(fields) + "|" + format_points(points) + rest + _line_ending(line)


def _set_arrow_source(line, source_id):
    from vensim_autolayout import _line_body, _line_ending, split_arrow_record

    fields, tail = split_arrow_record(_line_body(line))
    fields[2] = str(source_id)
    return ",".join(fields) + "|" + tail + _line_ending(line)


def orphan_shadow_ids(view):
    """与 shadow_visibility=orphan 相同口径：没有任何可见出入线的可见影子。"""
    shadows = {
        oid
        for oid, obj in view.objects.items()
        if obj.kind == 10 and obj.is_shadow and visible(obj) and not obj.attached_to_valve
    }
    used = set()
    for arrow in view.arrows:
        if shown(view, arrow):
            used.update((arrow.from_id, arrow.to_id))
    return shadows - used


def natural_view(lines, view, config, exclude=frozenset()):
    """对一个视图执行 natural 排版并改写 lines（新影子行另行返回，由调用方插入）。

    返回 (报告, 待插入行列表)。待插入行为 (插入位置, 文本)，插入位置是视图
    最后一条草图记录之后，调用方按位置倒序插入，避免改变其他视图的行号。
    """
    from vensim_autolayout import _line_ending, update_arrow_line

    plan = natural_plan(lines, view, config, exclude)
    if plan is None:
        return {"strategy": "natural", "skipped": "视图中没有可见对象"}, []
    # ---- 对象：坐标、存量/云尺寸、云图标代码 ----
    for oid, obj in view.objects.items():
        updates = {}
        if oid in plan.positions:
            x, y = plan.positions[oid]
            updates[3], updates[4] = x, y
        if oid in plan.sizes:
            updates[5], updates[6] = plan.sizes[oid]
        if oid in plan.clouds:
            updates[2] = NATIVE_CLOUD_CODE
        if updates:
            lines[obj.line_index] = _set_fields(lines[obj.line_index], updates)
    # ---- 箭头：管道控制点、信息线圆弧、影子改指 ----
    repoint = {item["arrow"]: item["new_shadow_id"] for item in plan.splits}
    for arrow in view.arrows:
        line = lines[arrow.line_index]
        if arrow.obj_id in plan.controls:
            line = update_arrow_line(line, plan.controls[arrow.obj_id][0], config)
        elif arrow.obj_id in plan.pipes:
            # 重建链的直管道是单个中点；整体保留的折线按原点数平移。
            value = plan.pipes[arrow.obj_id]
            line = _set_points(line, value if isinstance(value, list) else [value])
        if arrow.obj_id in repoint:
            line = _set_arrow_source(line, repoint[arrow.obj_id])
        if not arrow.is_physical_flow and arrow.obj_id not in plan.controls:
            from vensim_autolayout import restyle_arrow_line

            line = restyle_arrow_line(line, config)
        lines[arrow.line_index] = line
    # ---- 新影子行：插在本视图最后一条记录之后 ----
    insertions = []
    if plan.splits:
        # 带文字的注释（12 类且 bits 含 4）把文字写在下一行，插入点要跳过这一行
        last = max(
            [
                obj.line_index + (1 if obj.kind == 12 and obj.bits & 4 else 0)
                for obj in view.objects.values()
            ]
            + [a.line_index for a in view.arrows]
        )
        ending = _line_ending(lines[last]) or "\n"
        block = []
        for item in sorted(plan.splits, key=lambda row: row["new_shadow_id"]):
            shadow = item["shadow_object"]
            fields = list(shadow.raw_fields)
            x, y = plan.positions[shadow.obj_id]
            fields[3], fields[4] = str(x), str(y)
            block.append(",".join(fields) + ending)
        # 同一视图的新影子合并为一段文本，保持对象编号递增的书写顺序。
        insertions.append((last + 1, "".join(block)))
    planarity = planarity_report(view, frozenset(plan.hidden))
    report = {
        "strategy": "natural",
        "start": plan.strategy,
        "seed": plan.seed_used,
        "candidates": plan.candidates,
        "chains": plan.chains,
        "label_sides": {str(k): v for k, v in plan.label_sides.items()},
        "clouds_repaired": sorted(plan.clouds),
        "resized_stocks": sorted(oid for oid in plan.sizes if oid not in plan.clouds),
        "time_label_owners": plan.metrics.get("time_label_owners", []),
        "planarity": planarity,
        "shadow_splits": [
            {k: v for k, v in item.items() if k != "shadow_object"} for item in plan.splits
        ],
        "remaining_crossings": explain_crossings(
            plan.final_view, plan.metrics, planarity, plan.label_sides
        ),
        "arcs": arc_summary(plan, view),
        "quality": list(quality_key(plan.metrics)),
        "moved_objects": sum(
            1
            for oid, (x, y) in plan.positions.items()
            if oid in view.objects and (view.objects[oid].x, view.objects[oid].y) != (x, y)
        ),
    }
    if report["remaining_crossings"] and not config.get("split_shadows", True):
        report["advice"] = (
            "仍有交叉：原因为 non_planar（结构不可平面）或 label_side（流量名只能在管道一侧）时，"
            "去掉 --no-split-shadows，用影子变量引出连线即可消除。"
        )
    return report, insertions
