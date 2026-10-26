"""Port (anchor) assignment.

Draw.io has no first-class ports: an edge endpoint is described by an
``exitX/exitY`` or ``entryX/entryY`` fraction on a node side.  We therefore
assign one anchor per edge endpoint, but do it node-by-node so the result is a
coherent port order rather than a pile of independent choices.

Goals, in priority order:
1. never change which nodes an edge connects;
2. use the port that matches the dominant flow direction;
3. make singleton connections straight (co-linear anchors);
4. order anchors by the neighbour position to reduce crossings;
5. keep a minimum pitch so arrows and labels do not pile up.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import Edge, Graph, Node, OPPOSITE, Port


@dataclass
class PortConfig:
    direction: str = "LR"
    strategy: str = "nearest"       # axis | nearest
    min_pitch: float = 16.0
    margin: float = 10.0
    align_straight: bool = True
    max_side_ports: int = 10
    control_side_bias: bool = False


PREFERRED = {
    "LR": ("R", "L"),
    "RL": ("L", "R"),
    "TB": ("B", "T"),
    "BT": ("T", "B"),
}

FLOW_AXIS = {"LR": "x", "RL": "x", "TB": "y", "BT": "y"}


def assign_ports(graph: Graph, config: PortConfig | None = None) -> None:
    cfg = config or PortConfig(direction=graph.direction)
    graph.direction = cfg.direction
    for e in graph.edges:
        if not e.src or not e.tgt or e.src not in graph.nodes or e.tgt not in graph.nodes:
            continue
        a, b = graph.nodes[e.src], graph.nodes[e.tgt]
        e.src_port = _choose_port(e, a, b, cfg, outgoing=True, existing=e.src_port)
        e.tgt_port = _choose_port(e, a, b, cfg, outgoing=False, existing=e.tgt_port)
        e.src_port.signal = e.signal
        e.tgt_port.signal = e.signal

    # collect anchors per node/side
    groups: dict[tuple[str, str], list[tuple[Edge, str]]] = {}
    for e in graph.edges:
        if not e.src or not e.tgt or src_port_side(e) is None:
            continue
        if e.src in graph.nodes and graph.nodes[e.src].container is False:
            groups.setdefault((e.src, e.src_port.side), []).append((e, "src"))
        elif e.src in graph.nodes:
            groups.setdefault((e.src, e.src_port.side), []).append((e, "src"))
        if e.tgt in graph.nodes:
            groups.setdefault((e.tgt, e.tgt_port.side), []).append((e, "tgt"))

    for (nid, side), items in groups.items():
        _distribute(graph, graph.nodes[nid], side, items, cfg)

    if cfg.align_straight:
        _snap_straight(graph, cfg)
        _align_parallel(graph, cfg)


def src_port_side(e: Edge) -> str | None:
    return e.src_port.side if e.src_port else None


def _choose_port(
    e: Edge,
    a: Node,
    b: Node,
    cfg: PortConfig,
    outgoing: bool,
    existing: Port | None,
) -> Port:
    node, other = (a, b) if outgoing else (b, a)
    if existing and existing.locked:
        return Port(existing.side, existing.frac, existing.role, e.signal, existing.name, True)
    if existing and existing.name and cfg.strategy == "axis":
        # a named port from a spec preserves its requested side
        return Port(existing.side, existing.frac, existing.role, e.signal, existing.name, True)
    side = _auto_side(node, other, cfg, outgoing, _is_backward(a, b, cfg.direction))
    role = "out" if outgoing else "in"
    return Port(side=side, frac=0.5, role=role, signal=e.signal)


def _is_backward(a: Node, b: Node, direction: str, tol: float = 8.0) -> bool:
    if direction == "LR":
        return b.rect.cx < a.rect.cx - tol
    if direction == "RL":
        return b.rect.cx > a.rect.cx + tol
    if direction == "TB":
        return b.rect.cy < a.rect.cy - tol
    return b.rect.cy > a.rect.cy + tol


def _auto_side(node: Node, other: Node, cfg: PortConfig, outgoing: bool, backward: bool) -> str:
    d = cfg.direction
    out_side, in_side = PREFERRED[d]
    side = out_side if outgoing else in_side
    # backward edge: flip to the opposite side on the flow axis
    if backward:
        side = OPPOSITE[side]

    if cfg.strategy == "nearest":
        # use a perpendicular side only when the neighbour is clearly off-axis
        r = node.rect
        dx = other.rect.cx - r.cx
        dy = other.rect.cy - r.cy
        if d in ("LR", "RL"):
            if abs(dy) > 1.8 * max(20.0, abs(dx)) and abs(dy) > r.h:
                side = "B" if dy > 0 else "T"
        else:
            if abs(dx) > 1.8 * max(20.0, abs(dy)) and abs(dx) > r.w:
                side = "R" if dx > 0 else "L"
    return side


def _side_length(node: Node, side: str) -> float:
    return node.h if side in ("L", "R") else node.w


def _cross_pos(node: Node, other: Node, side: str) -> float:
    return other.rect.cy if side in ("L", "R") else other.rect.cx


def _distribute(graph: Graph, node: Node, side: str, items: list[tuple[Edge, str]], cfg: PortConfig) -> None:
    length = _side_length(node, side)
    usable_lo = min(cfg.margin, length / 3.0)
    usable_hi = max(length - cfg.margin, length * 2.0 / 3.0)
    # order: R/B ascending, L/T descending so lines do not braid
    reverse = side in ("L", "T")

    def key(item: tuple[Edge, str]) -> tuple[float, str]:
        e, which = item
        other_id = e.tgt if which == "src" else e.src
        other = graph.nodes.get(other_id or "")
        if not other:
            return (0.0, e.id)
        return (_cross_pos(node, other, side), e.id)

    items = sorted(items, key=key, reverse=reverse)
    n = len(items)
    if n == 0:
        return
    if n == 1:
        fracs = [0.5]
    else:
        span = max(1.0, usable_hi - usable_lo)
        pitch = span / (n - 1)
        if pitch < cfg.min_pitch:
            # congested side: use the whole edge and keep the order
            usable_lo, usable_hi = 2.0, max(3.0, length - 2.0)
            span = usable_hi - usable_lo
            pitch = span / max(1, n - 1)
        fracs = [(usable_lo + i * pitch) / length for i in range(n)]
    for (e, which), frac in zip(items, fracs):
        port = e.src_port if which == "src" else e.tgt_port
        if port:
            edge_px = 8.0 / max(1.0, length)
            port.frac = max(edge_px, min(1.0 - edge_px, frac))



def _snap_straight(graph: Graph, cfg: PortConfig) -> None:
    """Greedy anchor snapping: an edge whose endpoints' spans overlap should be
    straight when the shared coordinate is free on both sides.  Most-aligned
    pairs are processed first, so the visually obvious connection wins."""
    axis_x = FLOW_AXIS[cfg.direction] == "x"
    candidates = []
    for e in graph.edges:
        if not e.src or not e.tgt or not e.src_port or not e.tgt_port:
            continue
        a, b = graph.nodes.get(e.src), graph.nodes.get(e.tgt)
        if not a or not b:
            continue
        if axis_x:
            if not _sides_horizontal(e.src_port.side, e.tgt_port.side):
                continue
            lo, hi = max(a.y, b.y) + 6, min(a.rect.y1, b.rect.y1) - 6
            if hi - lo < 6:
                continue
            shared = max(lo, min(hi, (a.rect.cy + b.rect.cy) / 2.0))
            delta = abs(a.rect.cy - b.rect.cy)
        else:
            if not _sides_vertical(e.src_port.side, e.tgt_port.side):
                continue
            lo, hi = max(a.x, b.x) + 6, min(a.rect.x1, b.rect.x1) - 6
            if hi - lo < 6:
                continue
            shared = max(lo, min(hi, (a.rect.cx + b.rect.cx) / 2.0))
            delta = abs(a.rect.cx - b.rect.cx)
        candidates.append((delta, e.id, e, shared))

    candidates.sort(key=lambda x: (x[0], x[1]))
    priority = {c[2].id: i for i, c in enumerate(candidates)}
    for _, _, e, shared in candidates:
        a, b = graph.nodes[e.src], graph.nodes[e.tgt]
        lower = {eid for eid, p in priority.items() if p > priority[e.id]}
        chosen = None
        for candidate in (shared, shared - cfg.min_pitch, shared + cfg.min_pitch,
                          shared - 2 * cfg.min_pitch, shared + 2 * cfg.min_pitch):
            if axis_x:
                lo, hi = a.y + 8, a.rect.y1 - 8
                lo2, hi2 = b.y + 8, b.rect.y1 - 8
            else:
                lo, hi = a.x + 8, a.rect.x1 - 8
                lo2, hi2 = b.x + 8, b.rect.x1 - 8
            if not (lo <= candidate <= hi and lo2 <= candidate <= hi2):
                continue
            if _anchor_conflict(graph, e, a, e.src_port.side, candidate, axis_x, cfg.min_pitch, lower):
                continue
            if _anchor_conflict(graph, e, b, e.tgt_port.side, candidate, axis_x, cfg.min_pitch, lower):
                continue
            chosen = candidate
            break
        if chosen is None:
            continue
        if axis_x:
            e.src_port.frac = (chosen - a.y) / a.h
            e.tgt_port.frac = (chosen - b.y) / b.h
        else:
            e.src_port.frac = (chosen - a.x) / a.w
            e.tgt_port.frac = (chosen - b.x) / b.w


def _anchor_conflict(graph: Graph, edge: Edge, node: Node, side: str, shared: float,
                     axis_x: bool, pitch: float, ignore: set[str] | None = None) -> bool:
    ignore = ignore or set()
    for other in graph.edges:
        if other.id == edge.id or other.id in ignore:
            continue
        p = other.src_port if other.src == node.id else (
            other.tgt_port if other.tgt == node.id else None
        )
        if not p or p.side != side:
            continue
        if axis_x:
            y = node.y + node.h * p.frac
            if abs(y - shared) < max(6.0, pitch * 0.75):
                return True
        else:
            x = node.x + node.w * p.frac
            if abs(x - shared) < max(6.0, pitch * 0.75):
                return True
    return False


def _align_parallel(graph: Graph, cfg: PortConfig) -> None:
    """Parallel edges between the same node pair: pair up anchors in order."""
    groups: dict[tuple[str, str, str, str], list[Edge]] = {}
    for e in graph.edges:
        if not e.src or not e.tgt or not e.src_port or not e.tgt_port:
            continue
        key = (e.src, e.src_port.side, e.tgt, e.tgt_port.side)
        groups.setdefault(key, []).append(e)
    for (sid, sside, tid, tside), edges in groups.items():
        if len(edges) < 2:
            continue
        a, b = graph.nodes[sid], graph.nodes[tid]
        axis = FLOW_AXIS[cfg.direction]
        if axis == "x" and (_sides_horizontal(sside, tside)):
            lo = max(a.y, b.y) + 8
            hi = min(a.rect.y1, b.rect.y1) - 8
            if hi - lo < 12:
                continue
            ordered = sorted(edges, key=lambda e: (_edge_cross(e, a, b), e.id))
            n = len(ordered)
            for i, e in enumerate(ordered):
                shared = lo + (hi - lo) * ((i + 1) / (n + 1))
                e.src_port.frac = (shared - a.y) / a.h
                e.tgt_port.frac = (shared - b.y) / b.h
        elif axis == "y" and (_sides_vertical(sside, tside)):
            lo = max(a.x, b.x) + 8
            hi = min(a.rect.x1, b.rect.x1) - 8
            if hi - lo < 12:
                continue
            ordered = sorted(edges, key=lambda e: (_edge_cross(e, a, b), e.id))
            n = len(ordered)
            for i, e in enumerate(ordered):
                shared = lo + (hi - lo) * ((i + 1) / (n + 1))
                e.src_port.frac = (shared - a.x) / a.w
                e.tgt_port.frac = (shared - b.x) / b.w


def _edge_cross(e: Edge, a: Node, b: Node) -> float:
    if not e.src_port or not a:
        return 0.0
    return e.src_port.frac * (a.h if e.src_port.side in ("L", "R") else a.w)


def _sides_horizontal(a: str, b: str) -> bool:
    return a in ("L", "R") and b in ("L", "R")


def _sides_vertical(a: str, b: str) -> bool:
    return a in ("T", "B") and b in ("T", "B")


def _only_port(graph: Graph, e: Edge, which: str) -> bool:
    nid = e.src if which == "src" else e.tgt
    port = e.src_port if which == "src" else e.tgt_port
    if not nid or not port:
        return False
    cnt = 0
    for other in graph.edges:
        p = other.src_port if which == "src" and other.src == nid else (
            other.tgt_port if which == "tgt" and other.tgt == nid else None
        )
        if p and p.side == port.side:
            cnt += 1
    return cnt == 1


def congestion_report(graph: Graph, config: PortConfig | None = None) -> list[dict]:
    cfg = config or PortConfig(direction=graph.direction)
    out = []
    counts: dict[tuple[str, str], int] = {}
    for e in graph.edges:
        if e.src_port and e.src:
            counts[(e.src, e.src_port.side)] = counts.get((e.src, e.src_port.side), 0) + 1
        if e.tgt_port and e.tgt:
            counts[(e.tgt, e.tgt_port.side)] = counts.get((e.tgt, e.tgt_port.side), 0) + 1
    for (nid, side), n in sorted(counts.items()):
        node = graph.nodes.get(nid)
        if not node:
            continue
        length = _side_length(node, side)
        pitch = length / max(1, n)
        if pitch < cfg.min_pitch:
            out.append(
                {
                    "node": nid,
                    "side": side,
                    "ports": n,
                    "pitch": round(pitch, 2),
                    "length": length,
                }
            )
    return out
