"""Design-rule checking for Draw.io diagrams.

Rules are deliberately explicit objects (id, name, severity, category, fixable,
description) so they can be listed, filtered and regression-tested.  Detection
uses the *routed* polyline; when a real Draw.io export is available the checks
R02/R04/R07/R12 additionally run on the exported SVG path (the path the user
actually sees), and a mismatch between model and render is itself reported.

Severity levels
---------------
error   violates a hard constraint (topology, overlap, penetration, invalid file)
warning should be fixed; the drawing is still usable
info    style/consistency observation, often only meaningful for generated layouts
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import pstdev
from typing import Any, Callable

from .geometry import (
    Rect,
    bend_count,
    dist,
    manhattan,
    polyline_crossings,
    polyline_length,
    polyline_overlap,
    segment_distance_to_rect,
    segment_rect_hit,
    simplify_points,
)
from .model import Edge, Graph, Node, OPPOSITE
from .ports import PREFERRED, PortConfig, congestion_report
from .textmetrics import measurer

SEVERITIES = ("error", "warning", "info")


@dataclass
class DrcConfig:
    direction: str = "LR"
    min_clearance: float = 8.0
    min_node_gap: float = 14.0
    alignment_tol: float = 6.0
    max_side_ports: int = 9
    min_port_pitch: float = 13.0
    max_label_offset: float = 40.0
    max_detour_ratio: float = 1.9
    arrow_zone: float = 10.0
    min_wire_pitch: float = 24.0
    check_ports: bool = True
    allow_bus_share: bool = False
    container_crossing: str = "info"     # info | warning | error
    disabled: tuple[str, ...] = ()
    severity_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class Violation:
    rule_id: str
    rule_name: str
    severity: str
    message: str
    objects: list[str] = field(default_factory=list)
    location: tuple[float, float] | None = None
    fixable: bool = False
    fixed: bool = False
    fix: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def key(self) -> tuple:
        return (self.rule_id, tuple(sorted(self.objects)), self.message)

    def to_dict(self) -> dict:
        return {
            "rule": self.rule_id,
            "name": self.rule_name,
            "severity": self.severity,
            "message": self.message,
            "objects": self.objects,
            "location": list(self.location) if self.location else None,
            "fixable": self.fixable,
            "fixed": self.fixed,
            "fix": self.fix,
            "evidence": self.evidence,
        }


@dataclass
class Rule:
    id: str
    name: str
    severity: str
    category: str
    fixable: bool
    description: str
    check: Callable[["DrcContext"], list[Violation]]
    fix_hint: str = ""


class DrcContext:
    def __init__(self, graph: Graph, config: DrcConfig | None = None, ranks: dict | None = None, render=None):
        self.g = graph
        self.cfg = config or DrcConfig(direction=graph.direction)
        self.ranks = ranks or {}
        self.render = render
        self._label_boxes = graph.all_label_boxes()
        self._paths: dict[str, list[tuple[float, float]]] = {}
        for e in graph.edges:
            if render is not None and getattr(render, "verified", False) and e.rendered_path:
                self._paths[e.id] = e.rendered_path
            else:
                self._paths[e.id] = e.path

    # -- helpers -----------------------------------------------------------
    def path(self, e: Edge):
        return self._paths.get(e.id) or e.path

    def obstacles(self, e: Edge, clearance: float = 0.0):
        return self.g.obstacles_for(e, clearance)

    def labels(self, kind: str | None = None):
        if kind:
            return [b for b in self._label_boxes if b["kind"] == kind]
        return self._label_boxes

    def edge_by_id(self, eid: str) -> Edge | None:
        return next((e for e in self.g.edges if e.id == eid), None)


RULES: dict[str, Rule] = {}


def rule(rid: str, name: str, severity: str, category: str, fixable: bool, description: str, fix_hint: str = ""):
    def deco(fn: Callable[[DrcContext], list[Violation]]):
        RULES[rid] = Rule(rid, name, severity, category, fixable, description, fn, fix_hint)
        return fn

    return deco


def _v(ctx: DrcContext, rid: str, message: str, objects: list[str], **kw) -> Violation:
    r = RULES[rid]
    sev = ctx.cfg.severity_overrides.get(rid, r.severity)
    return Violation(
        rid,
        r.name,
        sev,
        message,
        objects=objects,
        location=kw.get("location"),
        fixable=kw.get("fixable", r.fixable),
        fix=kw.get("fix", r.fix_hint),
        evidence=kw.get("evidence", {}),
    )


# ---------------------------------------------------------------------------
# structural
# ---------------------------------------------------------------------------

@rule("S01", "Invalid file structure", "error", "structural", False,
      "Duplicate ids, non-positive geometry, or a missing edge endpoint id.",
      "Report only; repairing structure requires a human decision.")
def s01(ctx: DrcContext) -> list[Violation]:
    out = []
    seen: set[str] = set()
    for n in ctx.g.nodes.values():
        if n.id in seen:
            out.append(_v(ctx, "S01", f"duplicate id {n.id}", [n.id]))
        seen.add(n.id)
        if n.w <= 0 or n.h <= 0:
            out.append(_v(ctx, "S01", f"node {n.id} has non-positive size", [n.id],
                          evidence={"w": n.w, "h": n.h}))
    ids = {n.id for n in ctx.g.nodes.values()}
    for e in ctx.g.edges:
        if e.id in seen:
            out.append(_v(ctx, "S01", f"duplicate id {e.id}", [e.id]))
        seen.add(e.id)
        if e.src and e.src not in ids:
            out.append(_v(ctx, "S01", f"edge {e.id} source {e.src} does not exist", [e.id, e.src]))
        if e.tgt and e.tgt not in ids:
            out.append(_v(ctx, "S01", f"edge {e.id} target {e.tgt} does not exist", [e.id, e.tgt]))
    return out


@rule("S02", "Topology completeness", "error", "structural", False,
      "Every edge must connect two existing nodes; no edge may be silently dropped.",
      "Report only; the skill never invents missing connections.")
def s02(ctx: DrcContext) -> list[Violation]:
    out = []
    for e in ctx.g.edges:
        if not e.src or not e.tgt:
            out.append(_v(ctx, "S02", f"edge {e.id} is floating (source/target missing)", [e.id]))
    return out


# ---------------------------------------------------------------------------
# routing rules
# ---------------------------------------------------------------------------

def _terminal_hook_shape(e, pts) -> float | None:
    """Return the outward bump size when the path ends in a U-shaped hook."""
    if len(pts) < 5 or e.tgt_port is None:
        return None
    h, a, b, q = pts[-4], pts[-3], pts[-2], pts[-1]
    side = e.tgt_port.side
    hook = False
    if side == "R":
        hook = (
            abs(a[0] - b[0]) <= 1.0 and abs(b[1] - q[1]) <= 1.0
            and a[0] > h[0] + 4.0 and a[0] > q[0] + 4.0
            and 4.0 <= abs(a[1] - b[1]) <= 40.0
            and abs(a[0] - h[0]) <= 60.0
        )
    elif side == "L":
        hook = (
            abs(a[0] - b[0]) <= 1.0 and abs(b[1] - q[1]) <= 1.0
            and a[0] < h[0] - 4.0 and a[0] < q[0] - 4.0
            and 4.0 <= abs(a[1] - b[1]) <= 40.0
            and abs(a[0] - h[0]) <= 60.0
        )
    elif side == "B":
        hook = (
            abs(a[1] - b[1]) <= 1.0 and abs(b[0] - q[0]) <= 1.0
            and a[1] > h[1] + 4.0 and a[1] > q[1] + 4.0
            and 4.0 <= abs(a[0] - b[0]) <= 40.0
            and abs(a[1] - h[1]) <= 60.0
        )
    elif side == "T":
        hook = (
            abs(a[1] - b[1]) <= 1.0 and abs(b[0] - q[0]) <= 1.0
            and a[1] < h[1] - 4.0 and a[1] < q[1] - 4.0
            and 4.0 <= abs(a[0] - b[0]) <= 40.0
            and abs(a[1] - h[1]) <= 60.0
        )
    if not hook:
        return None
    return abs(a[0] - h[0]) if side in ("L", "R") else abs(a[1] - h[1])


@rule("R01", "Redundant bend", "warning", "routing", True,
      "A bend is redundant when a straight or single-bend path between the same "
      "anchors is obstacle-free; includes terminal jogs and outward U-hooks near the arrow.",
      "Simplify or re-route the edge; never move the anchors.")
def r01(ctx: DrcContext) -> list[Violation]:
    out = []
    from .router import Router, RouterConfig

    router = Router(ctx.g, RouterConfig(clearance=max(0.0, ctx.cfg.min_clearance - 2)))
    for e in ctx.g.edges:
        p = ctx.path(e)
        if len(p) < 3:
            continue
        if not e.src_port or not e.tgt_port or e.src not in ctx.g.nodes or e.tgt not in ctx.g.nodes:
            continue
        actual = bend_count(p)
        if actual == 0:
            continue
        a, b = ctx.g.nodes[e.src], ctx.g.nodes[e.tgt]
        p0 = e.src_port.point(a.rect)
        q0 = e.tgt_port.point(b.rect)
        obstacles = ctx.obstacles(e, max(0.0, ctx.cfg.min_clearance - 2))
        ns, nt = e.src_port.normal(), e.tgt_port.normal()
        minimal = None
        if router._direct(p0, q0, ns, nt, obstacles, a, b):
            minimal = 0
        elif router._elbows(p0, q0, ns, nt, obstacles, a, b):
            minimal = 1
        if minimal is not None and minimal < actual:
            out.append(_v(ctx, "R01", f"edge {e.id} has {actual} bends; {minimal} suffices",
                          [e.id], location=p[0],
                          evidence={"bends": actual, "minimal": minimal}))
            continue
        # Terminal hooks have their own hard rule (R14); do not double-count.
        pts = simplify_points(p)
        if _terminal_hook_shape(e, pts) is not None:
            continue
        # terminal jog: a short final run followed by a sideways hop
        if len(pts) >= 5:
            last = dist(pts[-2], pts[-1])
            if last < 10 and dist(pts[-3], pts[-2]) > 24:
                out.append(_v(ctx, "R01", f"edge {e.id} has a terminal jog of {last:.0f}px",
                              [e.id], location=pts[-1],
                              evidence={"terminal_length": round(last, 1)}))
                continue
        # penultimate dogleg: the final stub is perpendicular and long enough,
        # but a short sideways step immediately before it shifts the line onto
        # the anchor.  This is the 30px "extra corner" seen just above an arrow.
        if len(pts) >= 4:
            a, b, q = pts[-3], pts[-2], pts[-1]
            pen = dist(a, b)
            last = dist(b, q)
            pen_h = abs(a[1] - b[1]) <= 1.0 and abs(a[0] - b[0]) >= 4.0
            pen_v = abs(a[0] - b[0]) <= 1.0 and abs(a[1] - b[1]) >= 4.0
            last_v = abs(b[0] - q[0]) <= 1.0 and last >= 10.0
            last_h = abs(b[1] - q[1]) <= 1.0 and last >= 10.0
            if pen < 40.0 and ((pen_h and last_v) or (pen_v and last_h)):
                out.append(
                    _v(
                        ctx,
                        "R01",
                        f"edge {e.id} has a {pen:.0f}px dogleg just before the arrow",
                        [e.id],
                        location=b,
                        evidence={"dogleg_px": round(pen, 1),
                                  "terminal_px": round(last, 1)},
                    )
                )
    return out


@rule("R02", "Wire through unrelated module", "error", "routing", True,
      "A connector must not cross the interior of a module that is not its "
      "source, target, ancestor or descendant.  Container boundaries are allowed.",
      "Re-route around the obstacle (or re-layout if no channel exists).")
def r02(ctx: DrcContext) -> list[Violation]:
    out = []
    for e in ctx.g.edges:
        p = ctx.path(e)
        if len(p) < 2:
            continue
        for nid, rect in ctx.obstacles(e, 0.0):
            for i in range(len(p) - 1):
                if segment_rect_hit(p[i], p[i + 1], rect, tol=0.6):
                    mid = ((p[i][0] + p[i + 1][0]) / 2, (p[i][1] + p[i + 1][1]) / 2)
                    out.append(
                        _v(ctx, "R02", f"edge {e.id} crosses module {nid}",
                           [e.id, nid], location=mid,
                           evidence={"segment": [list(p[i]), list(p[i + 1])]})
                    )
                    break
        if ctx.cfg.container_crossing in ("warning", "error"):
            for n in ctx.g.nodes.values():
                if not n.container:
                    continue
                if e.src and e.tgt and (ctx.g.is_ancestor(n.id, e.src) and ctx.g.is_ancestor(n.id, e.tgt)):
                    continue
                for i in range(len(p) - 1):
                    if segment_rect_hit(p[i], p[i + 1], n.rect, tol=0.6):
                        out.append(
                            _v(ctx, "R02", f"edge {e.id} crosses container {n.id}",
                               [e.id, n.id], location=p[i],
                               fixable=False,
                               evidence={"policy": ctx.cfg.container_crossing})
                        )
                        break
    return out


@rule("R03", "Avoidable crossing", "warning", "routing", True,
      "Two connectors cross where re-routing or re-ordering could remove the crossing. "
      "Absolute zero crossings is not required.",
      "Re-route the lower-priority edge through another channel.")
def r03(ctx: DrcContext) -> list[Violation]:
    out = []
    edges = [e for e in ctx.g.edges if len(ctx.path(e)) >= 2]
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            a, b = edges[i], edges[j]
            if ctx.g.related(a.src or "", b.src or "") or ctx.g.related(a.tgt or "", b.tgt or ""):
                continue
            if polyline_crossings(ctx.path(a), ctx.path(b)):
                out.append(
                    _v(ctx, "R03", f"edges {a.id} and {b.id} cross",
                       [a.id, b.id],
                       location=_cross_point(ctx.path(a), ctx.path(b)),
                       evidence={"signals": [a.signal, b.signal]})
                )
    return out


@rule("R04", "Clearance violation", "warning", "routing", True,
      "A connector runs closer than the configured minimum clearance to an "
      "unrelated module.",
      "Re-route with a larger clearance; report the achieved value.")
def r04(ctx: DrcContext) -> list[Violation]:
    out = []
    for e in ctx.g.edges:
        p = ctx.path(e)
        if len(p) < 2:
            continue
        for nid, _ in ctx.obstacles(e, 0.0):
            n = ctx.g.nodes.get(nid)
            if not n:
                continue
            worst = None
            for i in range(len(p) - 1):
                d = segment_distance_to_rect(p[i], p[i + 1], n.rect)
                if 0 < d < ctx.cfg.min_clearance:
                    if worst is None or d < worst[0]:
                        worst = (d, p[i])
            if worst:
                out.append(
                    _v(ctx, "R04", f"edge {e.id} is {worst[0]:.1f}px from module {nid}",
                       [e.id, nid], location=worst[1],
                       evidence={"distance": round(worst[0], 2),
                                 "required": ctx.cfg.min_clearance})
                )
    return out


@rule("R05", "Backtracking / 180-degree reversal", "error", "routing", True,
      "Consecutive segments reverse direction; hairpin turns with a tiny connector "
      "are also flagged.",
      "Re-route without reversing direction.")
def r05(ctx: DrcContext) -> list[Violation]:
    out = []
    for e in ctx.g.edges:
        pts = simplify_points(ctx.path(e))
        for i in range(len(pts) - 2):
            d1 = _qdir(pts[i], pts[i + 1])
            d2 = _qdir(pts[i + 1], pts[i + 2])
            if d1[0] * d2[0] + d1[1] * d2[1] < 0:
                out.append(
                    _v(ctx, "R05", f"edge {e.id} reverses direction at {pts[i + 1]}",
                       [e.id], location=pts[i + 1],
                       evidence={"segments": [list(d1), list(d2)]})
                )

    return out


@rule("R06", "Zig-zag routing", "warning", "routing", True,
      "Three or more alternating short bends form a staircase that is harder to "
      "follow than a single channel.",
      "Re-route; prefer one long segment per direction.")
def r06(ctx: DrcContext) -> list[Violation]:
    out = []
    for e in ctx.g.edges:
        pts = simplify_points(ctx.path(e))
        if len(pts) < 5:
            continue
        short = 0
        for i in range(len(pts) - 1):
            if dist(pts[i], pts[i + 1]) < 26:
                short += 1
        if short >= 3 and len(pts) - 2 >= 4:
            out.append(
                _v(ctx, "R06", f"edge {e.id} zig-zags ({short} short segments)",
                   [e.id], location=pts[0],
                   evidence={"bends": len(pts) - 2, "short_segments": short})
            )
    return out


@rule("R07", "Non-orthogonal segment", "error", "routing", True,
      "In orthogonal mode every segment must be horizontal or vertical.",
      "Re-route with the orthogonal router.")
def r07(ctx: DrcContext) -> list[Violation]:
    out = []
    for e in ctx.g.edges:
        p = ctx.path(e)
        for i in range(len(p) - 1):
            dx = abs(p[i + 1][0] - p[i][0])
            dy = abs(p[i + 1][1] - p[i][1])
            if dx > 0.6 and dy > 0.6:
                out.append(
                    _v(ctx, "R07", f"edge {e.id} has a diagonal segment",
                       [e.id], location=p[i],
                       evidence={"dx": round(dx, 2), "dy": round(dy, 2)})
                )
    return out


@rule("R08", "Overlapping wires", "warning", "routing", True,
      "Two different connections share a collinear run.  Shared buses and "
      "explicitly merged nets are exempt.",
      "Offset one edge to a parallel channel.")
def r08(ctx: DrcContext) -> list[Violation]:
    out = []
    edges = [e for e in ctx.g.edges if len(ctx.path(e)) >= 2]
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            a, b = edges[i], edges[j]
            if ctx.cfg.allow_bus_share and "bus" in (a.signal, b.signal):
                continue
            shared = _shared_nodes(ctx.g, a, b)
            overlap = polyline_overlap(ctx.path(a), ctx.path(b))
            if overlap <= 6:
                continue
            # a short overlap right at a shared node is unavoidable (ports)
            if shared and overlap <= 24:
                continue
            out.append(
                _v(ctx, "R08", f"edges {a.id} and {b.id} overlap for {overlap:.0f}px",
                   [a.id, b.id],
                   location=_overlap_point(ctx.path(a), ctx.path(b)),
                   evidence={"overlap_px": round(overlap, 1),
                             "shared_nodes": sorted(shared)})
            )
    return out


@rule("R09", "Dangling connector", "error", "routing", False,
      "The rendered path does not start/end on the declared node boundary, or the "
      "edge has no geometry at all.",
      "Report only when topology is missing; a present edge with a bad anchor is re-routed.")
def r09(ctx: DrcContext) -> list[Violation]:
    out = []
    for e in ctx.g.edges:
        p = ctx.path(e)
        if len(p) < 2:
            out.append(_v(ctx, "R09", f"edge {e.id} has no routed path", [e.id], fixable=True))
            continue
        if e.src in ctx.g.nodes:
            r = ctx.g.nodes[e.src].rect
            if r.distance_to_point(p[0]) > 4.0:
                out.append(_v(ctx, "R09", f"edge {e.id} is detached from {e.src}",
                              [e.id, e.src], location=p[0], fixable=True))
        if e.tgt in ctx.g.nodes:
            r = ctx.g.nodes[e.tgt].rect
            if r.distance_to_point(p[-1]) > 4.0:
                out.append(_v(ctx, "R09", f"edge {e.id} is detached from {e.tgt}",
                              [e.id, e.tgt], location=p[-1], fixable=True))
    return out


@rule("R10", "Port direction", "warning", "routing", True,
      "An endpoint uses a side that contradicts the declared flow direction "
      "(e.g. a left-to-right data path leaving from the left side).",
      "Reassign the anchor to the flow-consistent side.")
def r10(ctx: DrcContext) -> list[Violation]:
    if not ctx.cfg.check_ports:
        return []
    out = []
    d = ctx.cfg.direction
    for e in ctx.g.edges:
        if not e.src_port or not e.tgt_port or e.src not in ctx.g.nodes or e.tgt not in ctx.g.nodes:
            continue
        a, b = ctx.g.nodes[e.src], ctx.g.nodes[e.tgt]
        backward = _backward(a, b, d)
        want_out = OPPOSITE[PREFERRED[d][0]] if backward else PREFERRED[d][0]
        want_in = OPPOSITE[PREFERRED[d][1]] if backward else PREFERRED[d][1]
        # if the two modules are clearly stacked, vertical ports are the
        # flow-consistent choice even in a left-to-right diagram (and vice versa)
        dx = abs(a.rect.cx - b.rect.cx)
        dy = abs(a.rect.cy - b.rect.cy)
        if d in ("LR", "RL") and dy > 1.8 * max(20.0, dx) and dy > a.h:
            want_out = "B" if b.rect.cy > a.rect.cy else "T"
            want_in = OPPOSITE[want_out]
        elif d in ("TB", "BT") and dx > 1.8 * max(20.0, dy) and dx > a.w:
            want_out = "R" if b.rect.cx > a.rect.cx else "L"
            want_in = OPPOSITE[want_out]
        if e.src_port.side != want_out and not e.src_port.locked:
            out.append(_v(ctx, "R10", f"edge {e.id} leaves {e.src} on {e.src_port.side}, expected {want_out}",
                          [e.id, e.src], evidence={"actual": e.src_port.side, "expected": want_out}))
        if e.tgt_port.side != want_in and not e.tgt_port.locked:
            out.append(_v(ctx, "R10", f"edge {e.id} enters {e.tgt} on {e.tgt_port.side}, expected {want_in}",
                          [e.id, e.tgt], evidence={"actual": e.tgt_port.side, "expected": want_in}))
    return out


@rule("R11", "Long detour", "info", "routing", True,
      "Wirelength is far above the Manhattan lower bound with no obvious obstacle "
      "justification.",
      "Try a shorter channel; keep the detour if it is needed to avoid a module.")
def r11(ctx: DrcContext) -> list[Violation]:
    out = []
    for e in ctx.g.edges:
        p = ctx.path(e)
        if len(p) < 2 or not e.src_port or not e.tgt_port:
            continue
        a, b = ctx.g.nodes.get(e.src or ""), ctx.g.nodes.get(e.tgt or "")
        if not a or not b:
            continue
        lower = manhattan(e.src_port.point(a.rect), e.tgt_port.point(b.rect))
        length = polyline_length(p)
        if lower > 10 and length > ctx.cfg.max_detour_ratio * lower + 60:
            sev = "warning" if length > 2.5 * lower + 100 else "info"
            out.append(_v(ctx, "R11", f"edge {e.id} is {length:.0f}px vs {lower:.0f}px direct",
                          [e.id], location=p[0],
                          evidence={"length": round(length, 1), "manhattan": round(lower, 1)}))
    return out


@rule("R12", "Arrow occlusion / bad approach", "error", "routing", True,
      "The arrow approaches its node from a non-perpendicular direction, has no "
      "room for the head, or the head is covered by another edge or a label.",
      "Re-route so the final segment is perpendicular and at least one stub long.")
def r12(ctx: DrcContext) -> list[Violation]:
    out = []
    for e in ctx.g.edges:
        p = simplify_points(ctx.path(e))
        if len(p) < 2 or not e.tgt_port or e.tgt not in ctx.g.nodes:
            continue
        want = (-e.tgt_port.normal()[0], -e.tgt_port.normal()[1])
        d = (p[-1][0] - p[-2][0], p[-1][1] - p[-2][1])
        d = (0 if abs(d[0]) < 0.6 else (1 if d[0] > 0 else -1),
             0 if abs(d[1]) < 0.6 else (1 if d[1] > 0 else -1))
        if d != want:
            out.append(_v(ctx, "R12", f"edge {e.id} enters its target sideways", [e.id],
                          location=p[-1], evidence={"direction": list(d), "expected": list(want)}))
            continue
        if dist(p[-2], p[-1]) < ctx.cfg.arrow_zone:
            out.append(_v(ctx, "R12", f"edge {e.id} has only {dist(p[-2], p[-1]):.0f}px for the arrow",
                          [e.id], location=p[-1], fixable=True))
        head = _arrow_box(p, 12)
        for other in ctx.g.edges:
            if other.id == e.id:
                continue
            op = ctx.path(other)
            for i in range(len(op) - 1):
                if segment_rect_hit(op[i], op[i + 1], head, tol=0.2):
                    out.append(_v(ctx, "R12", f"arrow of {e.id} is crossed by {other.id}",
                                  [e.id, other.id], location=p[-1]))
                    break
    return out


@rule("R13", "Dense parallel wires", "warning", "routing", True,
      "Two unrelated connectors run parallel closer than the minimum readable "
      "pitch over a long overlap, making the bus hard to follow.",
      "Assign the wires to evenly spaced lanes (>=18px pitch); move only their "
      "intermediate waypoints, not nodes or endpoints.")
def r13(ctx: DrcContext) -> list[Violation]:
    segs: list[tuple[str, float, float, float, str]] = []
    for e in ctx.g.edges:
        p = simplify_points(ctx.path(e))
        for a, b in zip(p, p[1:]):
            if abs(a[0] - b[0]) <= 1.0 and abs(b[1] - a[1]) >= 40.0:
                y0, y1 = sorted((a[1], b[1]))
                segs.append(("V", a[0], y0, y1, e.id))
            elif abs(a[1] - b[1]) <= 1.0 and abs(b[0] - a[0]) >= 40.0:
                x0, x1 = sorted((a[0], b[0]))
                segs.append(("H", a[1], x0, x1, e.id))
    out: list[Violation] = []
    for i, s in enumerate(segs):
        for t in segs[i + 1:]:
            if s[0] != t[0] or s[4] == t[4]:
                continue
            gap = abs(s[1] - t[1])
            overlap = min(s[3], t[3]) - max(s[2], t[2])
            if gap < 0.5 or gap >= ctx.cfg.min_wire_pitch or overlap < 40.0:
                continue
            if s[4] > t[4]:
                s_id, t_id = t[4], s[4]
            else:
                s_id, t_id = s[4], t[4]
            mid = (max(s[2], t[2]) + min(s[3], t[3])) / 2.0
            loc = (s[1], mid) if s[0] == "V" else (mid, s[1])
            out.append(
                _v(
                    ctx, "R13",
                    f"edges {s_id} and {t_id} run parallel at {gap:.1f}px pitch "
                    f"over {overlap:.0f}px",
                    [s_id, t_id],
                    location=loc,
                    evidence={"pitch": round(gap, 2),
                              "overlap": round(overlap, 2),
                              "required": ctx.cfg.min_wire_pitch},
                )
            )
    return out


@rule("R14", "Terminal hook", "error", "routing", True,
      "A wire approaches an arrow through a short outward U-shaped bump: it "
      "overshoots, steps sideways, then comes back.  R12 may still pass, but the "
      "bump is a visible routing defect and must not be left in a repaired atlas.",
      "Move the whole last trunk to a clean lane with a full arrow stub; if needed "
      "snap the target anchor on its free side.  Never leave the hook visible.")
def r14(ctx: DrcContext) -> list[Violation]:
    out = []
    for e in ctx.g.edges:
        pts = simplify_points(ctx.path(e))
        bump = _terminal_hook_shape(e, pts)
        if bump is None:
            continue
        out.append(
            _v(
                ctx,
                "R14",
                f"edge {e.id} has a terminal hook: {bump:.0f}px outward bump before the arrow",
                [e.id],
                location=pts[-2],
                evidence={"bump_px": round(bump, 1)},
            )
        )
    return out


# ---------------------------------------------------------------------------
# placement rules
# ---------------------------------------------------------------------------

@rule("P01", "Node overlap", "error", "placement", True,
      "Two unrelated modules overlap.  A child inside its own container is legal.",
      "Re-layout or nudge the unlocked node; locked nodes are never moved.")
def p01(ctx: DrcContext) -> list[Violation]:
    out = []
    nodes = [n for n in ctx.g.nodes.values() if not n.container]
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[j]
            if ctx.g.related(a.id, b.id):
                continue
            if a.rect.overlaps(b.rect):
                out.append(
                    _v(ctx, "P01", f"modules {a.id} and {b.id} overlap",
                       [a.id, b.id],
                       location=(max(a.x, b.x), max(a.y, b.y)),
                       fixable=not (a.locked and b.locked))
                )
    for c in [n for n in ctx.g.nodes.values() if n.container]:
        for n in nodes:
            if ctx.g.related(c.id, n.id):
                continue
            if c.rect.overlaps(n.rect):
                out.append(_v(ctx, "P01", f"container {c.id} overlaps module {n.id}",
                              [c.id, n.id], location=(n.x, n.y), fixable=False))
    return out


@rule("P02", "Port anchor alignment", "warning", "placement", True,
      "Nodes may be centred while their anchors are not, leaving an avoidable "
      "terminal jog.  Checks node coordinates, anchors and the routed path together.",
      "Snap both anchors to a shared coordinate when their spans overlap.")
def p02(ctx: DrcContext) -> list[Violation]:
    out = []
    d = ctx.cfg.direction
    axis_x = d in ("LR", "RL")
    for e in ctx.g.edges:
        if not e.src_port or not e.tgt_port or e.src not in ctx.g.nodes or e.tgt not in ctx.g.nodes:
            continue
        a, b = ctx.g.nodes[e.src], ctx.g.nodes[e.tgt]
        if axis_x and abs(a.rect.cy - b.rect.cy) <= 2.0:
            if not _both_horizontal(e.src_port.side, e.tgt_port.side):
                continue
            p = ctx.path(e)
            if bend_count(p) > 0:
                out.append(
                    _v(ctx, "P02", f"edge {e.id} connects centred modules with a jog",
                       [e.id, a.id, b.id], location=p[0],
                       evidence={"anchor_delta": round(abs(e.src_port.frac - e.tgt_port.frac) * a.h, 1),
                                 "bends": bend_count(p)})
                )
        if (not axis_x) and abs(a.rect.cx - b.rect.cx) <= 2.0:
            if not _both_vertical(e.src_port.side, e.tgt_port.side):
                continue
            p = ctx.path(e)
            if bend_count(p) > 0:
                out.append(
                    _v(ctx, "P02", f"edge {e.id} connects centred modules with a jog",
                       [e.id, a.id, b.id], location=p[0],
                       evidence={"bends": bend_count(p)})
                )
    return out


@rule("P03", "Flow direction consistency", "warning", "placement", False,
      "Most connections run against the declared direction (e.g. right-to-left in "
      "an LR diagram).  Individual back-edges are legal.",
      "Re-layout with the requested direction; do not flip edges silently.")
def p03(ctx: DrcContext) -> list[Violation]:
    total = backward = 0
    for e in ctx.g.edges:
        if e.src not in ctx.g.nodes or e.tgt not in ctx.g.nodes:
            continue
        total += 1
        if _backward(ctx.g.nodes[e.src], ctx.g.nodes[e.tgt], ctx.cfg.direction):
            backward += 1
    if total >= 3 and backward / total > 0.4:
        return [_v(ctx, "P03", f"{backward}/{total} connections run backwards",
                   objects=[e.id for e in ctx.g.edges[:5]],
                   evidence={"backward": backward, "total": total, "direction": ctx.cfg.direction})]
    return []


@rule("P04", "Node spacing", "warning", "placement", True,
      "Adjacent modules are closer than the minimum gap, or spacing is visibly "
      "uneven within a rank.",
      "Re-pack the rank; keep locked nodes fixed.")
def p04(ctx: DrcContext) -> list[Violation]:
    out = []
    nodes = [n for n in ctx.g.nodes.values() if not n.container]
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[j]
            if ctx.g.related(a.id, b.id):
                continue
            if a.rect.overlaps(b.rect):
                continue
            gap_x = max(0.0, max(a.x, b.x) - min(a.rect.x1, b.rect.x1))
            gap_y = max(0.0, max(a.y, b.y) - min(a.rect.y1, b.rect.y1))
            overlap_x = min(a.rect.x1, b.rect.x1) - max(a.x, b.x)
            overlap_y = min(a.rect.y1, b.rect.y1) - max(a.y, b.y)
            if overlap_y > 4 and 0 < gap_x < ctx.cfg.min_node_gap:
                out.append(_v(ctx, "P04", f"{a.id} and {b.id} are only {gap_x:.0f}px apart",
                              [a.id, b.id], location=(max(a.x, b.x), a.y),
                              fixable=not (a.locked and b.locked)))
            elif overlap_x > 4 and 0 < gap_y < ctx.cfg.min_node_gap:
                out.append(_v(ctx, "P04", f"{a.id} and {b.id} are only {gap_y:.0f}px apart",
                              [a.id, b.id], location=(a.x, max(a.y, b.y)),
                              fixable=not (a.locked and b.locked)))
    return out


@rule("P05", "Layer alignment", "info", "placement", True,
      "Modules in the same layout rank do not share the same rank-axis coordinate.",
      "Re-align to the rank centre (generated layouts do this by construction).")
def p05(ctx: DrcContext) -> list[Violation]:
    ranks = ctx.ranks or _infer_ranks(ctx.g, ctx.cfg.direction)
    if not ranks:
        return []
    axis_x = ctx.cfg.direction in ("LR", "RL")
    groups: dict[int, list[Node]] = {}
    for nid, r in ranks.items():
        n = ctx.g.nodes.get(nid)
        if n and not n.container:
            groups.setdefault(str(r), []).append(n)
    out = []
    for r, ns in groups.items():
        if len(ns) < 2:
            continue
        coords = [n.x + n.w / 2 for n in ns] if axis_x else [n.y + n.h / 2 for n in ns]
        if max(coords) - min(coords) > ctx.cfg.alignment_tol:
            out.append(_v(ctx, "P05", f"rank {r} is misaligned by {max(coords) - min(coords):.0f}px",
                          [n.id for n in ns], fixable=True,
                          evidence={"spread": round(max(coords) - min(coords), 1)}))
    return out


@rule("P06", "Port congestion", "warning", "placement", True,
      "Too many connectors share one side, so their pitch is below the readable "
      "minimum.",
      "Grow the node or move the outermost ports to an adjacent side.")
def p06(ctx: DrcContext) -> list[Violation]:
    cfg = PortConfig(direction=ctx.cfg.direction, min_pitch=ctx.cfg.min_port_pitch)
    out = []
    for item in congestion_report(ctx.g, cfg):
        out.append(
            _v(ctx, "P06", f"{item['node']}.{item['side']} has {item['ports']} ports at {item['pitch']:.0f}px pitch",
               [item["node"]], fixable=True, evidence=item)
        )
    return out


@rule("P07", "Size consistency", "info", "placement", True,
      "Modules of the same kind have visibly different sizes.",
      "Uniform sizing per kind (skipped for locked nodes).")
def p07(ctx: DrcContext) -> list[Violation]:
    out = []
    groups: dict[str, list[Node]] = {}
    for n in ctx.g.nodes.values():
        if n.container:
            continue
        groups.setdefault(n.kind, []).append(n)
    for kind, ns in groups.items():
        if kind in ("default", "unknown") or len(ns) < 2:
            continue
        ws = [n.w for n in ns]
        hs = [n.h for n in ns]
        if max(ws) / max(1.0, min(ws)) > 1.25 or max(hs) / max(1.0, min(hs)) > 1.3:
            out.append(_v(ctx, "P07", f"{kind} modules have inconsistent sizes",
                          [n.id for n in ns], fixable=not any(n.locked for n in ns),
                          evidence={"w": [min(ws), max(ws)], "h": [min(hs), max(hs)]}))
    return out


@rule("P08", "Container bounds and membership", "warning", "placement", False,
      "A child escapes its container, or a container overlaps an unrelated module.",
      "Re-layout the container; membership is never changed automatically.")
def p08(ctx: DrcContext) -> list[Violation]:
    out = []
    for c in [n for n in ctx.g.nodes.values() if n.container]:
        for child in ctx.g.children(c.id):
            if not c.rect.contains_rect(child.rect, tol=4):
                out.append(_v(ctx, "P08", f"{child.id} escapes container {c.id}",
                              [c.id, child.id], location=(child.x, child.y), fixable=True))
    for e in ctx.g.edges:
        if not e.src or not e.tgt:
            continue
        if ctx.g.parent_of(e.src) and ctx.g.parent_of(e.src) == ctx.g.parent_of(e.tgt):
            c = ctx.g.nodes[ctx.g.parent_of(e.src)]
            p = ctx.path(e)
            for i in range(len(p) - 1):
                if not (c.rect.inflate(2).contains_point(p[i]) and c.rect.inflate(2).contains_point(p[i + 1])):
                    out.append(_v(ctx, "P08", f"internal edge {e.id} leaves {c.id}",
                                  [e.id, c.id], location=p[i], fixable=True))
                    break
    return out


@rule("P09", "Grouping sanity", "info", "placement", False,
      "A container is empty, has a single child, or its children have no internal "
      "connections.",
      "Report only; grouping is a semantic decision.")
def p09(ctx: DrcContext) -> list[Violation]:
    out = []
    for c in [n for n in ctx.g.nodes.values() if n.container]:
        kids = ctx.g.children(c.id)
        if len(kids) <= 1:
            out.append(_v(ctx, "P09", f"container {c.id} has {len(kids)} child", [c.id]))
            continue
        ids = {k.id for k in kids}
        inner = [e for e in ctx.g.edges if e.src in ids and e.tgt in ids]
        if not inner:
            out.append(_v(ctx, "P09", f"container {c.id} has no internal connections", [c.id]))
    return out


@rule("P10", "Layout stability", "error", "placement", False,
      "An existing drawing moved more than allowed (preserve mode), or a generated "
      "layout moved nodes excessively.",
      "Rollback; preserve mode must keep user geometry.")
def p10(ctx: DrcContext) -> list[Violation]:
    out = []
    moved = []
    for n in ctx.g.nodes.values():
        if not n.original:
            continue
        d = abs(n.x - n.original[0]) + abs(n.y - n.original[1])
        limit = 24.0 if n.meta.get("nudge") else 2.0
        if n.meta.get("preserve_mode") and d > limit:
            moved.append((n.id, d))
    for nid, d in moved:
        out.append(_v(ctx, "P10", f"node {nid} moved {d:.0f}px in preserve mode", [nid],
                      fixable=False, evidence={"displacement": round(d, 1)}))
    total = sum(
        abs(n.x - n.original[0]) + abs(n.y - n.original[1])
        for n in ctx.g.nodes.values()
        if n.original
    )
    if not ctx.g.nodes:
        return out
    avg = total / len(ctx.g.nodes)
    if avg > 60:
        out.append(_v(ctx, "P10", f"average node displacement is {avg:.0f}px",
                      [n.id for n in list(ctx.g.nodes.values())[:5]], fixable=False,
                      evidence={"average": round(avg, 1)}))
    return out


# ---------------------------------------------------------------------------
# label rules
# ---------------------------------------------------------------------------

@rule("L01", "Wire through label", "error", "labels", True,
      "A connector segment passes through a text box.",
      "Move the label to a clear position or re-route the edge.")
def l01(ctx: DrcContext) -> list[Violation]:
    out = []
    for e in ctx.g.edges:
        p = ctx.path(e)
        for box in ctx.labels():
            if box["kind"] == "edge" and box["owner"] == e.id:
                continue
            for i in range(len(p) - 1):
                if segment_rect_hit(p[i], p[i + 1], box["rect"].inflate(-1), tol=0.3):
                    out.append(_v(ctx, "L01", f"edge {e.id} crosses label {box['owner']!r}",
                                  [e.id, box["owner"]], location=box["rect"].center))
                    break
    return out


@rule("L02", "Label overlap", "error", "labels", True,
      "Two text boxes overlap.",
      "Re-place one label; shorten only as a last resort.")
def l02(ctx: DrcContext) -> list[Violation]:
    boxes = ctx.labels()
    out = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if boxes[i]["rect"].overlaps(boxes[j]["rect"].inflate(-1)):
                out.append(_v(ctx, "L02", f"labels {boxes[i]['owner']!r} and {boxes[j]['owner']!r} overlap",
                              [boxes[i]["owner"], boxes[j]["owner"]],
                              location=boxes[i]["rect"].center))
    return out


@rule("L03", "Label far from its wire", "warning", "labels", True,
      "An edge label is further than the configured distance from its own edge, "
      "or is nearer to a different edge.",
      "Re-centre the label on its edge.")
def l03(ctx: DrcContext) -> list[Violation]:
    out = []
    for box in [b for b in ctx.labels() if b["kind"] == "edge"]:
        e = ctx.edge_by_id(box["owner"])
        if not e or not e.path:
            continue
        d = _box_path_distance(box["rect"], e.path)
        if d > ctx.cfg.max_label_offset:
            out.append(_v(ctx, "L03", f"label of {e.id} is {d:.0f}px from its edge",
                          [e.id], location=box["rect"].center,
                          evidence={"distance": round(d, 1)}))
    return out


@rule("L04", "Text overflow", "error", "labels", True,
      "Text does not fit inside its node (measured with real font metrics, CJK aware).",
      "Grow the node or wrap the text; locked nodes are reported only.")
def l04(ctx: DrcContext) -> list[Violation]:
    out = []
    m_cache = {}
    for n in ctx.g.nodes.values():
        if not n.label or n.container:
            continue
        fs = float(n.meta.get("font_size", 12))
        m = m_cache.setdefault(fs, measurer(fs, False))
        inner_w = max(1.0, n.w - 14.0)
        inner_h = max(1.0, n.h - 10.0)
        w, h, _ = m.measure(n.label, inner_w)
        if w > inner_w + 1.0 or h > inner_h + 1.0:
            out.append(
                _v(ctx, "L04", f"text of {n.id} overflows its box",
                   [n.id], location=(n.x, n.y),
                   fixable=not n.locked,
                   evidence={"text_w": round(w, 1), "box_w": round(inner_w, 1),
                             "text_h": round(h, 1), "box_h": round(inner_h, 1),
                             "backend": m.backend})
            )
    return out


@rule("L05", "Inconsistent label position", "info", "labels", True,
      "Labels of the same signal class sit at very different fractions along their "
      "edges.",
      "Normalise to the middle segment for a calmer drawing.")
def l05(ctx: DrcContext) -> list[Violation]:
    groups: dict[str, list[float]] = {}
    for box in [b for b in ctx.labels() if b["kind"] == "edge"]:
        e = ctx.edge_by_id(box["owner"])
        if not e or not e.path:
            continue
        groups.setdefault(e.signal, []).append(-e.label_state.rel_x)
    out = []
    for sig, xs in groups.items():
        if len(xs) < 3:
            continue
        if pstdev(xs) > 0.18:
            out.append(_v(ctx, "L05", f"{sig} labels are placed inconsistently",
                          objects=[], fixable=True,
                          evidence={"stdev": round(pstdev(xs), 3), "count": len(xs)}))
    return out


@rule("L06", "Arrow / label conflict", "warning", "labels", True,
      "A label box overlaps the arrowhead zone at either end of its edge.",
      "Shift the label toward the middle of a long segment.")
def l06(ctx: DrcContext) -> list[Violation]:
    out = []
    for e in ctx.g.edges:
        if not e.label or not e.path:
            continue
        b = next((x for x in ctx.labels() if x["kind"] == "edge" and x["owner"] == e.id), None)
        if not b:
            continue
        for i in (0, -1):
            p = e.path[i]
            zone = Rect(p[0] - 10, p[1] - 10, 20, 20)
            if b["rect"].overlaps(zone):
                out.append(_v(ctx, "L06", f"label of {e.id} touches its arrowhead",
                              [e.id], location=p))
                break
    return out


@rule("L07", "Label occluded", "warning", "labels", True,
      "A label sits on top of a module it does not belong to, or is covered by a "
      "later-drawn connector.",
      "Move the label to free space.")
def l07(ctx: DrcContext) -> list[Violation]:
    out = []
    for box in ctx.labels():
        if box["kind"] != "edge":
            continue
        for n in ctx.g.nodes.values():
            if n.container:
                continue
            if box["rect"].overlaps(n.rect.inflate(-1)):
                out.append(_v(ctx, "L07", f"label of {box['owner']} is occluded by {n.id}",
                              [box["owner"], n.id], location=box["rect"].center))
                break
    return out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _qdir(a, b, tol: float = 1.0) -> tuple[int, int]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    return (0 if abs(dx) <= tol else (1 if dx > 0 else -1),
            0 if abs(dy) <= tol else (1 if dy > 0 else -1))


def _backward(a: Node, b: Node, d: str, tol: float = 8.0) -> bool:
    if d == "LR":
        return b.rect.cx < a.rect.cx - tol
    if d == "RL":
        return b.rect.cx > a.rect.cx + tol
    if d == "TB":
        return b.rect.cy < a.rect.cy - tol
    return b.rect.cy > a.rect.cy + tol


def _both_horizontal(a: str, b: str) -> bool:
    return a in ("L", "R") and b in ("L", "R")


def _both_vertical(a: str, b: str) -> bool:
    return a in ("T", "B") and b in ("T", "B")


def _cross_point(p, q):
    from .geometry import segment_intersection

    for i in range(len(p) - 1):
        for j in range(len(q) - 1):
            pt = segment_intersection(p[i], p[i + 1], q[j], q[j + 1])
            if pt:
                return pt
    return None


def _overlap_point(p, q):
    from .geometry import collinear_overlap

    for i in range(len(p) - 1):
        for j in range(len(q) - 1):
            if collinear_overlap(p[i], p[i + 1], q[j], q[j + 1]) > 4:
                return ((p[i][0] + p[i + 1][0] + q[j][0] + q[j + 1][0]) / 4,
                        (p[i][1] + p[i + 1][1] + q[j][1] + q[j + 1][1]) / 4)
    return None


def _shared_nodes(g: Graph, a: Edge, b: Edge) -> set[str]:
    return {x for x in (a.src, a.tgt) if x in (b.src, b.tgt)}


def _arrow_box(path, size: float) -> Rect:
    p = path[-1]
    q = path[-2]
    dx = 1 if p[0] > q[0] else (-1 if p[0] < q[0] else 0)
    dy = 1 if p[1] > q[1] else (-1 if p[1] < q[1] else 0)
    if dx:
        return Rect(p[0] - size if dx < 0 else p[0], p[1] - 6, size, 12)
    return Rect(p[0] - 6, p[1] - size if dy < 0 else p[1], 12, size)


def _box_path_distance(box: Rect, path) -> float:
    best = 1e9
    for corner in (box.center,):
        for i in range(len(path) - 1):
            from .geometry import point_segment_distance

            best = min(best, point_segment_distance(corner, path[i], path[i + 1]))
    return best


def _infer_ranks(g: Graph, direction: str) -> dict[str, int]:
    axis_x = direction in ("LR", "RL")
    ns = [n for n in g.nodes.values() if not n.container]
    if not ns:
        return {}
    vals = sorted({round((n.x + n.w / 2 if axis_x else n.y + n.h / 2) / 80.0) for n in ns})
    return {
        n.id: vals.index(round((n.x + n.w / 2 if axis_x else n.y + n.h / 2) / 80.0))
        for n in ns
    }


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

def run_drc(graph: Graph, config: DrcConfig | None = None, ranks: dict | None = None, render=None) -> list[Violation]:
    ctx = DrcContext(graph, config, ranks, render)
    out: list[Violation] = []
    for rid in sorted(RULES):
        if rid in ctx.cfg.disabled:
            continue
        try:
            out.extend(RULES[rid].check(ctx))
        except Exception as exc:  # a broken rule must not hide the rest
            out.append(
                Violation(rid, RULES[rid].name, "error", f"rule crashed: {exc}", fixable=False)
            )
    return out


def drc_summary(violations: list[Violation]) -> dict:
    by_rule: dict[str, dict] = {}
    for rid, r in RULES.items():
        by_rule[rid] = {
            "name": r.name,
            "severity": r.severity,
            "category": r.category,
            "fixable": r.fixable,
            "description": r.description,
            "count": 0,
            "fixed": 0,
        }
    for v in violations:
        item = by_rule.setdefault(
            v.rule_id,
            {"name": v.rule_name, "severity": v.severity, "category": "",
             "fixable": v.fixable, "description": "", "count": 0, "fixed": 0},
        )
        item["count"] += 1
        # a scoped run may override a rule's severity (e.g. L01 -> warning in
        # minimal repair); report the severity that actually applied
        item["severity"] = v.severity
        if v.fixed:
            item["fixed"] += 1
    counts = {s: sum(1 for v in violations if v.severity == s) for s in SEVERITIES}
    return {
        "violations": len(violations),
        "by_severity": counts,
        "by_rule": by_rule,
        "hard_failures": counts["error"],
    }
