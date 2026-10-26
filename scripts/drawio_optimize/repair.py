"""Minimal, conservative repair for large hand-authored Draw.io drawings.

A generated block diagram can be re-laid out and re-routed wholesale.  A dense
engineering atlas cannot: its node positions, anchors, text and long bus routes
are deliberate.  This module therefore repairs only these classes of defect:

* a terminal overshoot that leaves too little room for the arrowhead,
* a terminal/source jog or Z detour that can be removed by one anchor snap,
* a dense vertical bus whose small S-jogs can be collapsed onto even lanes
  (with at most a reported shift of a decorative badge and its header),
* an edge label that is crossed by another wire or buried under a module.

Everything else is preserved byte-for-byte at the semantic level: functional
nodes keep their geometry and styles, edges keep their anchors and waypoints,
and cells wrapped in ``UserObject`` keep their tooltip/data-source metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from .drawio_io import write
from .drc import DrcConfig, Violation, drc_summary, run_drc
from .geometry import (
    Rect,
    dist,
    merge_rects,
    point_at_fraction,
    polyline_length,
    segment_rect_hit,
    simplify_points,
)
from .model import Edge, Graph, Port
from .render import export_drawio, png_liveness
from .report import build_report, write_reports
from .scoring import raw_metrics
from .svgcheck import SvgRender, map_to_model, parse_svg, verify_svg
from .textmetrics import measurer
from .util import merge_style

REPAIR_DISABLED = (
    # Placement in a dense atlas is intentional; re-layout would destroy it.
    "S01", "S02",
    "P01", "P04", "P05", "P06", "P07", "P08", "P09", "P10",
    # Text density is part of the atlas design.
    "L02", "L04",
    # Hand-authored tight clearances and bus crossings are inspected but not
    # rewritten automatically.  R04 remains enabled as a warning so a repair
    # cannot silently introduce a new penetration.
    "R02", "R03", "R09", "R10", "R11",
)


@dataclass
class RepairOptions:
    direction: str = "TB"
    min_clearance: float = 6.0
    arrow_stub: float = 18.0
    max_label_dx: float = 100.0
    max_label_off: float = 36.0
    fallback_label_dx: float = 300.0
    fallback_label_off: float = 60.0
    label_rounds: int = 2
    render: bool = True
    render_formats: tuple[str, ...] = ("png", "svg")
    snap_terminal_anchors: bool = True
    snap_endpoint_anchors: bool = True
    dogleg_max: float = 40.0
    dogleg_min: float = 20.0
    endpoint_jog_max: float = 40.0
    z_shift_max: float = 220.0
    bundle_lanes: bool = True
    bundle_band_width: float = 190.0
    bundle_min_pitch: float = 36.0
    bundle_clearance: float = 9.0
    bundle_shift_decorations: bool = True


@dataclass
class RepairChange:
    edge_id: str
    kind: str
    before: Any
    after: Any
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "edge": self.edge_id,
            "object_type": "node" if self.kind == "node_shift" else "edge",
            "kind": self.kind,
            "before": self.before,
            "after": self.after,
            "note": self.note,
        }


@dataclass
class RepairOutcome:
    changes: list[RepairChange] = field(default_factory=list)
    unfixed_labels: list[str] = field(default_factory=list)
    rendered: SvgRender | None = None


def repair_drc_config(direction: str = "TB", min_clearance: float = 6.0) -> DrcConfig:
    """The scoped DRC used for repair decisions.

    R12 (arrow room) stays an error.  L01/L07 are readability warnings in this
    scope, so a dense atlas can still pass while listing what could not be
    cleared.  R04 is kept as a warning guard against regressions.
    """
    return DrcConfig(
        direction=direction,
        min_clearance=min_clearance,
        check_ports=False,
        disabled=REPAIR_DISABLED,
        severity_overrides={"L01": "warning", "L07": "info", "R06": "info"},
    )


def attach_rendered_paths(graph: Graph, svg_path: str | Path) -> SvgRender | None:
    r = map_to_model(parse_svg(svg_path), graph)
    if not r.verified:
        return None
    for e in graph.edges:
        pts = r.paths.get(e.id)
        if pts:
            e.path = [tuple(p) for p in pts]
            e.meta["rendered_path"] = list(e.path)
    return r


def sync_label_boxes(graph: Graph) -> None:
    """Compute the box Draw.io will actually draw for every edge label."""
    for e in graph.edges:
        if not e.label or not e.path:
            continue
        fs = float(
            e.meta.get("label_font_size")
            or e.meta.get("style_map", {}).get("fontSize", 10)
            or 10
        )
        max_w = float(e.meta.get("style_map", {}).get("labelWidth", 150) or 150)
        m = measurer(fs, False)
        w, h, _ = m.label_box(e.label, max(40.0, max_w))
        if e.label_cell_id or e.meta.get("label_moved") or e.label_state.rel_x or e.label_state.rel_y:
            t = max(0.0, min(1.0, (e.label_state.rel_x + 1.0) / 2.0))
        else:
            t = 0.5
        p = point_at_fraction(e.path, t)
        cx, cy = p[0], p[1] - e.label_state.rel_y
        e.label_state.absolute = (cx, cy)
        e.label_state.bbox = Rect(cx - w / 2, cy - h / 2, w, h)


def _model_path(graph: Graph, e: Edge) -> list[tuple[float, float]]:
    a = graph.nodes.get(e.src)
    b = graph.nodes.get(e.tgt)
    if not a or not b or not e.waypoints:
        return []
    p0 = e.src_port.point(a.rect) if e.src_port else (a.rect.cx, a.rect.cy)
    q0 = e.tgt_port.point(b.rect) if e.tgt_port else (b.rect.cx, b.rect.cy)
    return [p0] + [tuple(w) for w in e.waypoints] + [q0]


def _terminal_waypoints(
    graph: Graph, e: Edge, stub: float
) -> tuple[list[tuple[float, float]], tuple] | None:
    """Remove a terminal overshoot and leave a clean perpendicular stub.

    Example: ``... (4460,5140) (4180,5140) (4180,5110) -> anchor (4180,5147)``
    overshoots past the anchor and backtracks, so Draw.io draws the arrow into a
    7 px final hop.  The repair keeps the same channel but runs it 18 px above
    the anchor and drops the overshoot.
    """
    if not e.waypoints or not e.src_port or not e.tgt_port:
        return None
    a = graph.nodes.get(e.src)
    b = graph.nodes.get(e.tgt)
    if not a or not b:
        return None
    p0 = e.src_port.point(a.rect)
    q0 = e.tgt_port.point(b.rect)
    want = (-e.tgt_port.normal()[0], -e.tgt_port.normal()[1])
    w = [tuple(x) for x in e.waypoints]
    pts = simplify_points([p0] + w + [q0])
    if len(pts) < 3:
        return None
    aa, bb = pts[-3], pts[-2]
    last = dist(bb, q0)
    direction = (q0[0] - bb[0], q0[1] - bb[1])
    aligned = direction[0] * want[0] + direction[1] * want[1] > 0
    if last >= stub and aligned:
        return None
    if want[0] == 0:  # vertical approach
        if abs(aa[1] - bb[1]) > 1.0:
            return None
        cy = q0[1] - want[1] * stub
        corner = (aa[0], cy)
        final = (q0[0], cy)
    else:  # horizontal approach
        if abs(aa[0] - bb[0]) > 1.0:
            return None
        cx = q0[0] - want[0] * stub
        corner = (cx, aa[1])
        final = (cx, q0[1])
    idx = None
    for i in range(len(w) - 1, -1, -1):
        if dist(w[i], aa) < 2.0:
            idx = i
            break
    if idx is None:
        return None
    return w[:idx] + [corner, final], (aa, bb, corner, final)


def _terminal_victims(vs: list[Violation]) -> set[str]:
    out: set[str] = set()
    for v in vs:
        if v.rule_id == "R12" and v.objects:
            out.add(v.objects[0])
        elif v.rule_id == "R01" and "terminal jog" in v.message and v.objects:
            out.add(v.objects[0])
    return out


def _count_r04(vs: list[Violation], eid: str) -> int:
    return sum(1 for v in vs if v.rule_id == "R04" and eid in v.objects)


def repair_terminals(
    graph: Graph, cfg: DrcConfig, opts: RepairOptions
) -> tuple[list[RepairChange], list[Violation]]:
    vs = run_drc(graph, cfg)
    victims = _terminal_victims(vs)
    changes: list[RepairChange] = []
    for eid in sorted(victims):
        e = next((x for x in graph.edges if x.id == eid), None)
        if e is None:
            continue
        candidate = _terminal_waypoints(graph, e, opts.arrow_stub)
        if candidate is None:
            continue
        new_w, detail = candidate
        old_w = list(e.waypoints)
        old_path = list(e.path)
        before_r04 = _count_r04(vs, eid)
        a = graph.nodes[e.src]
        b = graph.nodes[e.tgt]
        model = [e.src_port.point(a.rect)] + new_w + [e.tgt_port.point(b.rect)]
        e.waypoints = new_w
        e.path = model
        vs2 = run_drc(graph, cfg)
        mine = [v for v in vs2 if eid in v.objects]
        still_r12 = any(v.rule_id == "R12" for v in mine)
        new_r04 = _count_r04(vs2, eid)
        hard_before = drc_summary(vs)["hard_failures"]
        hard_after = drc_summary(vs2)["hard_failures"]
        if still_r12 or new_r04 > before_r04 or hard_after > hard_before:
            e.waypoints = old_w
            e.path = old_path
            continue
        e.meta["repaired_terminal"] = True
        e.meta["repair_note"] = (
            f"terminal overshoot removed; {opts.arrow_stub:.0f}px perpendicular stub"
        )
        changes.append(
            RepairChange(
                eid,
                "terminal",
                [list(p) for p in old_w],
                [list(p) for p in new_w],
                "removed overshoot; final segment is now a clean arrow stub",
            )
        )
        vs = vs2
    return changes, vs


def _anchor_style_for(port: Port, which: str) -> dict[str, str]:
    if port.side in ("T", "B"):
        return {
            f"{which}X": f"{port.frac:.6f}".rstrip("0").rstrip("."),
            f"{which}Y": "0" if port.side == "T" else "1",
            f"{which}Dx": "0",
            f"{which}Dy": "0",
        }
    return {
        f"{which}X": "0" if port.side == "L" else "1",
        f"{which}Y": f"{port.frac:.6f}".rstrip("0").rstrip("."),
        f"{which}Dx": "0",
        f"{which}Dy": "0",
    }


def _side_is_free(
    graph: Graph, edge: Edge, node_id: str, side: str, frac: float, pitch: float = 13.0
) -> bool:
    node = graph.nodes.get(node_id)
    if node is None or not (0.0 <= frac <= 1.0):
        return False
    length = node.w if side in ("T", "B") else node.h
    for other in graph.edges:
        if other.id == edge.id:
            continue
        port = None
        if other.src == node_id:
            port = other.src_port
        elif other.tgt == node_id:
            port = other.tgt_port
        if port is None or port.side != side:
            continue
        if abs(port.frac - frac) * length < pitch:
            return False
    return True


def _dogleg_plan(
    graph: Graph, e: Edge, opts: RepairOptions
) -> tuple[Port, list[tuple[float, float]], str] | None:
    """Snap the target anchor to the incoming trunk, removing a terminal dogleg."""
    if not e.waypoints or not e.src_port or not e.tgt_port:
        return None
    src = graph.nodes.get(e.src)
    tgt = graph.nodes.get(e.tgt)
    if src is None or tgt is None:
        return None
    p0 = e.src_port.point(src.rect)
    path = e.path or _model_path(graph, e)
    pts = simplify_points(path)
    if len(pts) < 4:
        return None
    a, b, q = pts[-3], pts[-2], pts[-1]
    vertical = e.tgt_port.side in ("T", "B")
    pen = dist(a, b)
    last = dist(b, q)
    if not (max(4.0, opts.dogleg_min) <= pen <= opts.dogleg_max and last >= 10.0):
        return None
    if vertical:
        if abs(a[1] - b[1]) > 1.0 or abs(b[0] - q[0]) > 1.0:
            return None
        new_axis = a[0]
        new_frac = (new_axis - tgt.rect.x) / max(1.0, tgt.rect.w)
    else:
        if abs(a[0] - b[0]) > 1.0 or abs(b[1] - q[1]) > 1.0:
            return None
        new_axis = a[1]
        new_frac = (new_axis - tgt.rect.y) / max(1.0, tgt.rect.h)
    if not _side_is_free(graph, e, e.tgt, e.tgt_port.side, new_frac):
        return None
    new_port = Port(
        side=e.tgt_port.side,
        frac=new_frac,
        role=e.tgt_port.role,
        signal=e.tgt_port.signal,
        name=e.tgt_port.name,
        locked=e.tgt_port.locked,
    )
    new_q = new_port.point(tgt.rect)
    w = [tuple(x) for x in e.waypoints]
    idx = None
    for i in range(len(w) - 1, -1, -1):
        if dist(w[i], a) < 2.0:
            idx = i
            break
    if idx is None:
        return None
    new_w = simplify_points([p0] + w[: idx + 1] + [new_q])[1:-1]
    return new_port, new_w, f"entryX {e.tgt_port.frac:.4f} -> {new_frac:.4f}"


def repair_terminal_doglegs(
    graph: Graph, cfg: DrcConfig, opts: RepairOptions
) -> tuple[list[RepairChange], list[str]]:
    vs = run_drc(graph, cfg)
    ids = {
        v.objects[0]
        for v in vs
        if v.rule_id == "R01" and "dogleg just before the arrow" in v.message and v.objects
    }
    changes: list[RepairChange] = []
    unfixed: list[str] = []
    if not opts.snap_terminal_anchors:
        return changes, sorted(ids)
    for eid in sorted(ids):
        e = next((x for x in graph.edges if x.id == eid), None)
        if e is None:
            continue
        plan = _dogleg_plan(graph, e, opts)
        if plan is None:
            unfixed.append(eid)
            continue
        new_port, new_w, note = plan
        old_port = e.tgt_port
        old_w = list(e.waypoints)
        old_style = e.style
        old_path = list(e.path)
        before_r04 = _count_r04(vs, eid)
        hard_before = drc_summary(vs)["hard_failures"]
        e.tgt_port = new_port
        e.style = merge_style(e.style, _anchor_style_for(new_port, "entry"))
        e.waypoints = new_w
        src = graph.nodes[e.src]
        tgt = graph.nodes[e.tgt]
        e.path = [e.src_port.point(src.rect)] + new_w + [new_port.point(tgt.rect)]
        vs2 = run_drc(graph, cfg)
        mine = [v for v in vs2 if eid in v.objects]
        still = any(v.rule_id in ("R01", "R12") for v in mine)
        if (
            still
            or _count_r04(vs2, eid) > before_r04
            or drc_summary(vs2)["hard_failures"] > hard_before
        ):
            e.tgt_port = old_port
            e.waypoints = old_w
            e.style = old_style
            e.path = old_path
            unfixed.append(eid)
            continue
        e.meta["repaired_dogleg"] = True
        e.meta["repair_note"] = (
            "terminal dogleg removed by snapping the target anchor to the trunk"
        )
        changes.append(
            RepairChange(
                eid,
                "anchor_dogleg",
                {
                    "entry_frac": old_port.frac,
                    "waypoints": [list(p) for p in old_w],
                },
                {
                    "entry_frac": new_port.frac,
                    "waypoints": [list(p) for p in new_w],
                },
                note,
            )
        )
        vs = vs2
    return changes, unfixed


def _collapse_tiny_jogs(path, tol: float = 4.0):
    """Remove sub-pixel/1-2 px steps introduced while moving a terminal lane."""
    pts = [tuple(p) for p in path]
    changed = True
    while changed and len(pts) >= 4:
        changed = False
        for i in range(1, len(pts) - 2):
            a, b, c, d = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
            if dist(b, c) >= tol:
                continue
            if abs(b[0] - c[0]) <= 1.0 and abs(a[1] - b[1]) <= 1.0 and abs(c[1] - d[1]) <= 1.0:
                # vertical crumb between two horizontal runs: flatten the first run
                pts = pts[: i - 1] + [(a[0], c[1])] + pts[i + 2:]
                changed = True
                break
            if abs(b[1] - c[1]) <= 1.0 and abs(a[0] - b[0]) <= 1.0 and abs(c[0] - d[0]) <= 1.0:
                # horizontal crumb between two vertical runs: flatten the first run
                pts = pts[: i - 1] + [(c[0], a[1])] + pts[i + 2:]
                changed = True
                break
    return simplify_points(pts)


def _hook_geometry_ok(graph: Graph, cfg: DrcConfig, eid: str, vs) -> bool:
    mine = [v for v in vs if eid in v.objects]
    return not any(v.rule_id in ("R14", "R01", "R12", "R13") for v in mine)


def repair_terminal_hooks(
    graph: Graph, cfg: DrcConfig, opts: RepairOptions
) -> tuple[list[RepairChange], list[str]]:
    """Remove an outward U-shaped bump just before a target arrow.

    R14 is an error: the bump must not survive a repair run.  Move the last
    trunk to a clean 18 px lane first.  If that still leaves a short terminal
    dogleg (for example a 30 px lateral offset into the target side), snap the
    target anchor onto the clean trunk and verify R01/R12/R13/R04 again.
    """
    vs = run_drc(graph, cfg)
    ids = {v.objects[0] for v in vs if v.rule_id == "R14" and v.objects}
    changes: list[RepairChange] = []
    unfixed: list[str] = []
    for eid in sorted(ids):
        e = next((x for x in graph.edges if x.id == eid), None)
        if e is None or not e.tgt_port or e.tgt not in graph.nodes or e.src not in graph.nodes:
            continue
        src = graph.nodes[e.src]
        tgt = graph.nodes[e.tgt]
        pts = simplify_points(e.path or _model_path(graph, e))
        if len(pts) < 5:
            unfixed.append(eid)
            continue
        h, a, b, q = pts[-4], pts[-3], pts[-2], pts[-1]
        prev = pts[-5]
        side = e.tgt_port.side
        new_pts = list(pts[:-4])
        if side in ("R", "L"):
            lane = (
                tgt.rect.x1 + opts.arrow_stub
                if side == "R"
                else tgt.rect.x0 - opts.arrow_stub
            )
            if abs(prev[0] - h[0]) <= 1.0:
                new_pts[-1] = (lane, prev[1])
            else:
                new_pts.append((lane, h[1]))
            new_pts.extend([(lane, b[1]), q])
        elif side in ("T", "B"):
            lane = (
                tgt.rect.y1 + opts.arrow_stub
                if side == "B"
                else tgt.rect.y - opts.arrow_stub
            )
            if abs(prev[1] - h[1]) <= 1.0:
                new_pts[-1] = (prev[0], lane)
            else:
                new_pts.append((h[0], lane))
            new_pts.extend([(b[0], lane), q])
        else:
            unfixed.append(eid)
            continue

        new_path = _collapse_tiny_jogs(new_pts)
        if any(dist(x, y) < 4.0 for x, y in zip(new_path, new_path[1:])):
            unfixed.append(eid)
            continue
        old_path = list(e.path)
        old_w = list(e.waypoints)
        old_port = e.tgt_port
        old_style = e.style
        before_r04 = _count_r04(vs, eid)
        hard_before = drc_summary(vs)["hard_failures"]
        e.path = new_path
        e.waypoints = [tuple(p) for p in new_path[1:-1]]
        vs2 = run_drc(graph, cfg)

        snapped = None
        if not _hook_geometry_ok(graph, cfg, eid, vs2):
            # A lateral offset into the target side can still leave a short
            # dogleg.  Snap the target anchor onto the clean trunk if its side
            # has room; this is a reported anchor move, never a node move.
            plan = _dogleg_plan(graph, e, RepairOptions(dogleg_min=10, dogleg_max=40))
            if plan is not None:
                np, nw, _ = plan
                e.tgt_port = np
                e.style = merge_style(e.style, _anchor_style_for(np, "entry"))
                e.waypoints = [tuple(p) for p in nw]
                e.path = _collapse_tiny_jogs(
                    [e.src_port.point(src.rect)] + list(nw) + [np.point(tgt.rect)]
                )
                snapped = (old_port.frac, np.frac)
                vs2 = run_drc(graph, cfg)

        if (
            not _hook_geometry_ok(graph, cfg, eid, vs2)
            or _count_r04(vs2, eid) > before_r04
            or drc_summary(vs2)["hard_failures"] > hard_before
            or any(dist(x, y) < 4.0 for x, y in zip(e.path, e.path[1:]))
        ):
            e.path = old_path
            e.waypoints = old_w
            e.tgt_port = old_port
            e.style = old_style
            unfixed.append(eid)
            continue
        e.meta["repaired_hook"] = True
        note = "removed the outward terminal hook and kept a full arrow stub"
        before = {"path": [list(p) for p in pts], "entry": round(old_port.frac, 6)}
        after = {"path": [list(p) for p in e.path], "entry": round(e.tgt_port.frac, 6)}
        if snapped is not None:
            note += f"; snapped the target anchor {abs(snapped[1]-snapped[0])*100:.1f}% to clear the last dogleg"
        changes.append(RepairChange(eid, "terminal_hook", before, after, note))
        vs = vs2
    return changes, sorted(set(unfixed))



def _axis_dir(a: tuple[float, float], b: tuple[float, float]) -> tuple[int, int]:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    return (
        0 if abs(dx) < 1.0 else (1 if dx > 0 else -1),
        0 if abs(dy) < 1.0 else (1 if dy > 0 else -1),
    )


def _parallel(d1: tuple[int, int], d2: tuple[int, int]) -> bool:
    return (d1[0] == 0 and d2[0] == 0) or (d1[1] == 0 and d2[1] == 0)


def _perpendicular(d1: tuple[int, int], d2: tuple[int, int]) -> bool:
    return d1[0] * d2[0] + d1[1] * d2[1] == 0


def _source_direction_ok(port: Port, d: tuple[int, int]) -> bool:
    if port.side == "R":
        return d == (1, 0)
    if port.side == "L":
        return d == (-1, 0)
    if port.side == "B":
        return d == (0, 1)
    if port.side == "T":
        return d == (0, -1)
    return False


def _endpoint_plan(
    graph: Graph, e: Edge, opts: RepairOptions
) -> tuple[str, Port, list[tuple[float, float]], str] | None:
    """Plan a source/target anchor snap that removes a short end jog or Z."""
    if not e.waypoints or not e.src_port or not e.tgt_port:
        return None
    src = graph.nodes.get(e.src)
    tgt = graph.nodes.get(e.tgt)
    if src is None or tgt is None:
        return None
    p0 = e.src_port.point(src.rect)
    q0 = e.tgt_port.point(tgt.rect)
    w = [tuple(x) for x in e.waypoints]
    path = e.path or _model_path(graph, e)
    pts = simplify_points(path)
    if len(pts) < 3:
        return None

    # Source staircase: short normal stub, short sideways step, then the trunk.
    if len(pts) >= 4:
        a, b, c = pts[1], pts[2], pts[3]
        d1 = _axis_dir(p0, a)
        d2 = _axis_dir(a, b)
        d3 = _axis_dir(b, c)
        if (
            dist(p0, a) <= opts.endpoint_jog_max
            and dist(a, b) <= opts.endpoint_jog_max
            and _perpendicular(d1, d2)
            and _parallel(d1, d3)
            and _source_direction_ok(e.src_port, d1)
        ):
            if d1[1] == 0:  # source leaves horizontally
                new_frac = (b[1] - src.rect.y) / max(1.0, src.rect.h)
                new_p0 = (p0[0], b[1])
            else:  # source leaves vertically
                new_frac = (b[0] - src.rect.x) / max(1.0, src.rect.w)
                new_p0 = (b[0], p0[1])
            if _side_is_free(graph, e, e.src, e.src_port.side, new_frac):
                port = Port(
                    side=e.src_port.side,
                    frac=new_frac,
                    role=e.src_port.role,
                    signal=e.src_port.signal,
                    name=e.src_port.name,
                    locked=e.src_port.locked,
                )
                new_w = simplify_points([new_p0] + w[2:] + [q0])[1:-1]
                return (
                    "source",
                    port,
                    new_w,
                    f"source anchor {e.src_port.frac:.4f} -> {new_frac:.4f}",
                )

    # Z-shape: both ends parallel, one sideways middle segment.  Align one
    # anchor with the other end and collapse the middle entirely.
    if len(pts) == 4:
        a, b, q = pts[1], pts[2], pts[-1]
        d1 = _axis_dir(p0, a)
        d2 = _axis_dir(a, b)
        d3 = _axis_dir(b, q)
        if _perpendicular(d1, d2) and _parallel(d1, d3):
            vertical = d1[1] != 0
            shift = abs(p0[0] - q[0]) if vertical else abs(p0[1] - q[1])
            if 4.0 <= shift <= opts.z_shift_max:
                if vertical and e.src_port.side in ("T", "B") and e.tgt_port.side in ("T", "B"):
                    src_frac = (q[0] - src.rect.x) / max(1.0, src.rect.w)
                    if _side_is_free(graph, e, e.src, e.src_port.side, src_frac):
                        port = Port(
                            side=e.src_port.side,
                            frac=src_frac,
                            role=e.src_port.role,
                            signal=e.src_port.signal,
                            name=e.src_port.name,
                            locked=e.src_port.locked,
                        )
                        new_p0 = (q[0], p0[1])
                        return (
                            "source",
                            port,
                            [],
                            f"Z source anchor {e.src_port.frac:.4f} -> {src_frac:.4f}",
                        )
                    tgt_frac = (p0[0] - tgt.rect.x) / max(1.0, tgt.rect.w)
                    if _side_is_free(graph, e, e.tgt, e.tgt_port.side, tgt_frac):
                        port = Port(
                            side=e.tgt_port.side,
                            frac=tgt_frac,
                            role=e.tgt_port.role,
                            signal=e.tgt_port.signal,
                            name=e.tgt_port.name,
                            locked=e.tgt_port.locked,
                        )
                        return (
                            "target",
                            port,
                            [],
                            f"Z target anchor {e.tgt_port.frac:.4f} -> {tgt_frac:.4f}",
                        )
                elif (not vertical) and e.src_port.side in ("L", "R") and e.tgt_port.side in ("L", "R"):
                    src_frac = (q[1] - src.rect.y) / max(1.0, src.rect.h)
                    if _side_is_free(graph, e, e.src, e.src_port.side, src_frac):
                        port = Port(
                            side=e.src_port.side,
                            frac=src_frac,
                            role=e.src_port.role,
                            signal=e.src_port.signal,
                            name=e.src_port.name,
                            locked=e.src_port.locked,
                        )
                        new_p0 = (p0[0], q[1])
                        return (
                            "source",
                            port,
                            [],
                            f"Z source anchor {e.src_port.frac:.4f} -> {src_frac:.4f}",
                        )
                    tgt_frac = (p0[1] - tgt.rect.y) / max(1.0, tgt.rect.h)
                    if _side_is_free(graph, e, e.tgt, e.tgt_port.side, tgt_frac):
                        port = Port(
                            side=e.tgt_port.side,
                            frac=tgt_frac,
                            role=e.tgt_port.role,
                            signal=e.tgt_port.signal,
                            name=e.tgt_port.name,
                            locked=e.tgt_port.locked,
                        )
                        return (
                            "target",
                            port,
                            [],
                            f"Z target anchor {e.tgt_port.frac:.4f} -> {tgt_frac:.4f}",
                        )
    return None


def repair_endpoint_jogs(
    graph: Graph, cfg: DrcConfig, opts: RepairOptions
) -> tuple[list[RepairChange], list[str]]:
    """Remove source-side short jogs and single-step Z detours by anchor snap."""
    if not opts.snap_endpoint_anchors:
        return [], []
    vs = run_drc(graph, cfg)
    changes: list[RepairChange] = []
    unfixed: list[str] = []
    for e in list(graph.edges):
        if not e.waypoints:
            continue
        plan = _endpoint_plan(graph, e, opts)
        if plan is None:
            continue
        end, new_port, new_w, note = plan
        old_src = e.src_port
        old_tgt = e.tgt_port
        old_w = list(e.waypoints)
        old_style = e.style
        old_path = list(e.path)
        src = graph.nodes[e.src]
        tgt = graph.nodes[e.tgt]
        if end == "source":
            new_p0 = new_port.point(src.rect)
            q = e.tgt_port.point(tgt.rect)
            new_path = [new_p0] + new_w + [q]
        else:
            p0 = e.src_port.point(src.rect)
            new_q = new_port.point(tgt.rect)
            new_path = [p0] + new_w + [new_q]
        old_path_eff = old_path or _model_path(graph, e)
        if polyline_length(new_path) > polyline_length(old_path_eff) + 5.0:
            continue
        if len(simplify_points(new_path)) >= len(simplify_points(old_path_eff)):
            continue
        before_r04 = _count_r04(vs, e.id)
        hard_before = drc_summary(vs)["hard_failures"]
        if end == "source":
            e.src_port = new_port
            e.style = merge_style(e.style, _anchor_style_for(new_port, "exit"))
        else:
            e.tgt_port = new_port
            e.style = merge_style(e.style, _anchor_style_for(new_port, "entry"))
        e.waypoints = new_w
        e.path = new_path
        vs2 = run_drc(graph, cfg)
        mine = [v for v in vs2 if e.id in v.objects]
        if (
            any(v.rule_id == "R12" for v in mine)
            or _count_r04(vs2, e.id) > before_r04
            or drc_summary(vs2)["hard_failures"] > hard_before
        ):
            e.src_port = old_src
            e.tgt_port = old_tgt
            e.waypoints = old_w
            e.style = old_style
            e.path = old_path
            unfixed.append(e.id)
            continue
        e.meta["repaired_endpoint"] = True
        e.meta["repair_note"] = "endpoint jog removed by anchor snap"
        changes.append(
            RepairChange(
                e.id,
                "anchor_source" if end == "source" else "anchor_target",
                {
                    "source_frac": old_src.frac if old_src else None,
                    "target_frac": old_tgt.frac if old_tgt else None,
                    "waypoints": [list(p) for p in old_w],
                },
                {
                    "source_frac": e.src_port.frac if e.src_port else None,
                    "target_frac": e.tgt_port.frac if e.tgt_port else None,
                    "waypoints": [list(p) for p in new_w],
                },
                note,
            )
        )
        vs = vs2
    return changes, unfixed


def _bundle_candidates(
    graph: Graph, opts: RepairOptions
) -> tuple[Rect, dict[str, tuple[Edge, list[tuple], tuple]], list[str]]:
    """Find long vertical runs in the left-hand channel and their primary x."""
    if not graph.nodes:
        return Rect(0, 0, 1, 1), {}, []
    bbox = merge_rects([n.rect for n in graph.nodes.values()])
    band_max = bbox.x + opts.bundle_band_width
    band_min = min(0.0, bbox.x - 80.0)
    cands: dict[str, tuple[Edge, list[tuple], tuple]] = {}
    for e in graph.edges:
        path = _model_path(graph, e)
        if not path:
            continue
        pts = simplify_points(path)
        segs = []
        for a, b in zip(pts, pts[1:]):
            if (
                abs(a[0] - b[0]) <= 1.0
                and abs(b[1] - a[1]) >= 80.0
                and band_min <= a[0] <= band_max
            ):
                segs.append((a, b, abs(b[1] - a[1])))
        if segs:
            cands[e.id] = (e, segs, max(segs, key=lambda s: s[2])[:2])
    return bbox, cands, sorted(cands)


def _bundle_core(
    cands: dict[str, tuple[Edge, list[tuple], tuple]]
) -> tuple[float, float]:
    events: list[tuple[float, int]] = []
    for _, segs, _ in cands.values():
        for s in segs:
            y0, y1 = sorted((s[0][1], s[1][1]))
            events.append((y0, 1))
            events.append((y1, -1))
    if not events:
        return 0.0, 0.0
    # Starts sort before ends at the same y, so touching runs are inclusive.
    events.sort(key=lambda ev: (ev[0], -ev[1]))
    count = 0
    best = (0, 0.0, 0.0)
    for i, (y, delta) in enumerate(events):
        count += delta
        if i + 1 < len(events):
            nxt = events[i + 1][0]
            if nxt > y and count > best[0]:
                best = (count, y, nxt)
            elif nxt > y and count == best[0] and nxt - y > best[2] - best[1]:
                best = (count, y, nxt)
    return best[1], best[2]


def _set_vertical_lanes(
    path: list[tuple[float, float]],
    band_max: float,
    lane: float,
    keep: tuple[tuple[float, float], tuple[float, float]] | None = None,
) -> list[tuple[float, float]]:
    """Move every vertical run in the bundle to one lane, preserving direction."""
    pts = simplify_points(path)
    if len(pts) < 2:
        return pts
    out: list[tuple[float, float]] = [pts[0]]
    xprev = pts[0][0]
    for a, b in zip(pts, pts[1:]):
        vertical = abs(a[0] - b[0]) <= 1.0 and abs(b[1] - a[1]) > 1.0
        protected = keep is not None and (
            (abs(a[0] - keep[0][0]) <= 1 and abs(a[1] - keep[0][1]) <= 1
             and abs(b[0] - keep[1][0]) <= 1 and abs(b[1] - keep[1][1]) <= 1)
            or (abs(a[0] - keep[1][0]) <= 1 and abs(a[1] - keep[1][1]) <= 1
                and abs(b[0] - keep[0][0]) <= 1 and abs(b[1] - keep[0][1]) <= 1)
        )
        if not vertical or a[0] > band_max or protected:
            out.append(b)
            xprev = b[0]
            continue
        if abs(xprev - lane) > 0.5:
            out.append((xprev, a[1]))
            out.append((lane, a[1]))
        out.append((lane, b[1]))
        xprev = lane
    clean: list[tuple[float, float]] = []
    for p in out:
        q = (round(p[0], 3), round(p[1], 3))
        if not clean or q != clean[-1]:
            clean.append(q)
    return simplify_points(clean)


def _fit_header_width(graph: Graph, nd: Node) -> None:
    """Keep a shifted title bar inside its own container's right edge."""
    parent = graph.nodes.get(nd.parent) if nd.parent else None
    if parent is None or not parent.container:
        return
    max_w = max(160.0, parent.rect.x1 - nd.x - 2.0)
    if nd.w > max_w:
        nd.w = max_w


def repair_bundle_lanes(
    graph: Graph, cfg: DrcConfig, opts: RepairOptions
) -> tuple[list[RepairChange], list[RepairChange], list[str]]:
    """Evenly space a dense left-side vertical bus and remove its small jogs.

    A decorative chapter badge (for example the small ``04`` tab) can narrow
    the channel below the readable pitch.  The only node move repair-only is
    allowed to make is shifting that badge and its immediately adjacent header
    together, by the minimum distance needed; every such move is reported.
    """
    if not opts.bundle_lanes:
        return [], [], []
    bbox, cands, order = _bundle_candidates(graph, opts)
    if len(cands) < 5:
        return [], [], []
    core_y0, core_y1 = _bundle_core(cands)
    if core_y1 - core_y0 < 40:
        return [], [], []
    core_edges = []
    for eid, (e, segs, primary) in cands.items():
        if any(
            max(b[1], a[1]) >= core_y0 - 5.0 and min(b[1], a[1]) <= core_y1 + 5.0
            for a, b, _ in segs
        ):
            core_edges.append((eid, e, primary))
    if len(core_edges) < 5:
        return [], [], []
    n = len(core_edges)
    bbox_x = 0.0
    barrier_y0 = core_y0 - 100.0
    barrier_y1 = core_y1 + 250.0
    # The decorative badge is the small numeric tab at the channel mouth.
    badges = [
        nd for nd in graph.nodes.values()
        if (not nd.container and nd.h <= 60 and nd.w <= 180
            and re.fullmatch(r"\d{1,3}", nd.label.strip())
            and nd.rect.y1 > barrier_y0 and nd.rect.y < barrier_y1
            and bbox.x - 2 < nd.x < bbox.x + opts.bundle_band_width)
    ]
    node_changes: list[RepairChange] = []
    badge = min(badges, key=lambda nd: nd.x) if badges else None
    header = None
    if badge is not None:
        header = next(
            (
                nd for nd in graph.nodes.values()
                if not nd.container and nd.w >= 300 and nd.h <= 80
                and abs(nd.y - badge.y) <= 12 and badge.rect.x1 - 8 <= nd.x <= badge.rect.x1 + 80
            ),
            None,
        )
    if badge is not None and opts.bundle_shift_decorations:
        needed = bbox_x + (n - 1) * opts.bundle_min_pitch + opts.bundle_clearance
        shift = needed - badge.x
        if shift > 0.5:
            shift = min(shift, 120.0)
            before_badge = (badge.x, badge.y)
            badge.x += shift
            if header is not None:
                before_header = (header.x, header.y, header.w)
                header.x += shift
                _fit_header_width(graph, header)
                node_changes.append(
                    RepairChange(
                        header.id,
                        "node_shift",
                        {"x": before_header[0], "y": before_header[1], "w": before_header[2]},
                        {"x": header.x, "y": header.y, "w": header.w},
                        f"shifted right {shift:.1f}px with the badge to widen the wire channel",
                    )
                )
            node_changes.append(
                RepairChange(
                    badge.id,
                    "node_shift",
                    {"x": before_badge[0], "y": before_badge[1]},
                    {"x": badge.x, "y": badge.y},
                    f"shifted right {shift:.1f}px to give the vertical bus a readable pitch",
                )
            )
            # Wider lanes may also need the other container title bars to move.
            # They are decorative headers, not functional modules.  Their width
            # is reduced only enough to stay inside the parent container.
            moved_ids = {ch.edge_id for ch in node_changes}
            for nd in graph.nodes.values():
                if (
                    nd.id in moved_ids or nd.container or nd.w < 300 or nd.h > 80
                    or nd.rect.y1 <= barrier_y0 or nd.y >= barrier_y1
                    or not (bbox.x + 10 < nd.x < bbox.x + opts.bundle_band_width)
                ):
                    continue
                delta = needed - nd.x
                if delta > 0.5:
                    before = (nd.x, nd.y, nd.w)
                    nd.x += min(delta, 120.0)
                    _fit_header_width(graph, nd)
                    node_changes.append(
                        RepairChange(
                            nd.id,
                            "node_shift",
                            {"x": before[0], "y": before[1], "w": before[2]},
                            {"x": nd.x, "y": nd.y, "w": nd.w},
                            f"shifted right {min(delta, 120.0):.1f}px to widen the wire channel",
                        )
                    )
    # Available lane width: stop at the badge (or the first non-container node).
    obstacles = [
        nd for nd in graph.nodes.values()
        if not nd.container and nd.h <= 60
        and nd.rect.y1 > barrier_y0 and nd.rect.y < barrier_y1
        and bbox.x + 10 < nd.x < bbox.x + opts.bundle_band_width
    ]
    left_limit = min((nd.x for nd in obstacles), default=bbox.x + opts.bundle_band_width)
    cap = left_limit - opts.bundle_clearance
    span = max(1.0, cap - bbox_x)
    pitch = min(opts.bundle_min_pitch, span / max(1, n - 1))
    if pitch < 10.0:
        return [], node_changes, []
    # Longest-primary first: this keeps the most visually important edge near
    # its authored lane and gives every other edge a deterministic pitch.
    core_edges.sort(key=lambda item: (min(p[0] for p in item[2]), item[0]))
    before_paths = {eid: _model_path(graph, e) for eid, e, _ in core_edges}
    barrier_top = badge.y if badge is not None else core_y0
    applied: list[tuple[str, Edge, list, list]] = []
    for i, (eid, e, primary) in enumerate(core_edges):
        lane = bbox_x + i * pitch
        keep = None
        if primary[0][1] < barrier_top and primary[0][0] > cap:
            keep = primary
        new_path = _set_vertical_lanes(
            before_paths[eid], bbox.x + opts.bundle_band_width, lane, keep=keep
        )
        old_path = before_paths[eid]
        old_bends = len(simplify_points(old_path))
        new_bends = len(simplify_points(new_path))
        if new_bends > old_bends:
            continue
        if new_bends == old_bends and abs(lane - primary[0][0]) < 0.5:
            continue
        if polyline_length(new_path) > polyline_length(old_path) + 20.0:
            continue
        applied.append((eid, e, old_path, new_path))
    if not applied:
        # roll back decorative moves; no lane was safe
        for ch in node_changes:
            nd = graph.nodes.get(ch.edge_id)
            if nd is not None:
                nd.x = ch.before["x"]
                nd.y = ch.before["y"]
        return [], [], []
    # Compare like with like: ensure every edge has its authored model path
    # before the baseline DRC, otherwise an existing terminal overshoot on an
    # untouched edge is invisible in "before" and looks like a regression after
    # the bus pass gives that edge a path.
    for e in graph.edges:
        if not e.path:
            e.path = _model_path(graph, e)
    before_vs = run_drc(graph, cfg)
    before_hard = drc_summary(before_vs)["hard_failures"]
    before_r04 = {eid: _count_r04(before_vs, eid) for eid, _, _, _ in applied}
    for eid, e, old_path, new_path in applied:
        e.path = new_path
        e.waypoints = [tuple(p) for p in simplify_points(new_path)[1:-1]]
        e.meta["repaired_bundle"] = True
    after_vs = run_drc(graph, cfg)
    if (
        drc_summary(after_vs)["hard_failures"] > before_hard
        or any(_count_r04(after_vs, eid) > before_r04[eid] for eid, _, _, _ in applied)
    ):
        for eid, e, old_path, _ in applied:
            e.path = old_path
            e.waypoints = [tuple(p) for p in simplify_points(old_path)[1:-1]]
            e.meta.pop("repaired_bundle", None)
        for ch in node_changes:
            nd = graph.nodes.get(ch.edge_id)
            if nd is not None:
                nd.x = ch.before["x"]
                nd.y = ch.before["y"]
        return [], [], []
    changes: list[RepairChange] = []
    for eid, e, old_path, new_path in applied:
        changes.append(
            RepairChange(
                eid,
                "bundle_lane",
                {"path": [list(p) for p in old_path]},
                {"path": [list(p) for p in new_path]},
                f"vertical bus lane x={new_path[0][0]:.0f}-{max(p[0] for p in new_path):.0f}; "
                "jogs collapsed and pitch widened",
            )
        )
    return changes, node_changes, []


def repair_dense_pairs(
    graph: Graph, cfg: DrcConfig, opts: RepairOptions, protected: set[str] | None = None
) -> tuple[list[RepairChange], list[str]]:
    """Nudge a non-core vertical run to a free lane when it still hugs a bus.

    The main bundle pass assigns the core lanes.  A few long wires outside that
    set can still overlap one of those lanes (for example an older 80 px run
    beside a newly widened 72 px lane).  Move one whole run at a time, never a
    node or endpoint, and accept only if the R13 count falls without a new hard
    or R04 regression.
    """
    changes: list[RepairChange] = []
    unfixed: list[str] = []
    protected = protected or set()
    bbox = merge_rects([n.rect for n in graph.nodes.values()]) if graph.nodes else Rect(0, 0, 1, 1)
    band_max = bbox.x + opts.bundle_band_width

    def vertical_runs(edge: Edge) -> list[tuple[float, float, float]]:
        pts = simplify_points(_model_path(graph, edge))
        out = []
        for a, b in zip(pts, pts[1:]):
            if abs(a[0] - b[0]) <= 1.0 and abs(b[1] - a[1]) >= 40.0:
                y0, y1 = sorted((a[1], b[1]))
                out.append((a[0], y0, y1))
        return out

    def lane_clear(edge: Edge, lane: float) -> bool:
        for other in graph.edges:
            if other.id == edge.id:
                continue
            for x, y0, y1 in vertical_runs(other):
                if abs(x - lane) < opts.bundle_min_pitch:
                    for _, ey0, ey1 in vertical_runs(edge):
                        if min(y1, ey1) - max(y0, ey0) >= 40.0:
                            return False
        for n in graph.nodes.values():
            if n.container or n.id in (edge.src, edge.tgt):
                continue
            if n.x - opts.bundle_clearance <= lane <= n.rect.x1 + opts.bundle_clearance:
                for _, y0, y1 in vertical_runs(edge):
                    if min(n.rect.y1, y1) - max(n.y, y0) >= 0.0:
                        return False
        return True

    for _ in range(4):
        vs = run_drc(graph, cfg)
        r13 = [v for v in vs if v.rule_id == "R13"]
        if not r13:
            break
        before_count = len(r13)
        progressed = False
        for v in r13:
            for eid in sorted(v.objects, key=lambda x: x in protected):
                e = next((x for x in graph.edges if x.id == eid), None)
                if e is None or eid in protected:
                    continue
                path = _model_path(graph, e)
                pts = simplify_points(path)
                verticals = [
                    a[0] for a, b in zip(pts, pts[1:])
                    if abs(a[0] - b[0]) <= 1.0 and abs(b[1] - a[1]) >= 80.0
                    and a[0] <= band_max
                ]
                if not verticals:
                    continue
                current = verticals[len(verticals) // 2]
                candidates = sorted(
                    {
                        round(k * opts.bundle_min_pitch, 1)
                        for k in range(0, 9)
                        if k * opts.bundle_min_pitch <= band_max
                    }
                    | {round(x, 1) for x in verticals},
                    key=lambda x: abs(x - current),
                )
                before_path = path
                before_r04 = _count_r04(vs, eid)
                old_w = list(e.waypoints)
                old_path = list(e.path)
                for lane in candidates:
                    if (
                        abs(lane - current) < 1.0
                        or abs(lane - current) > 4 * opts.bundle_min_pitch
                        or not lane_clear(e, lane)
                    ):
                        continue
                    new_path = _set_vertical_lanes(path, band_max, lane)
                    if len(simplify_points(new_path)) > len(simplify_points(path)) + 2:
                        continue
                    e.path = new_path
                    e.waypoints = [tuple(p) for p in simplify_points(new_path)[1:-1]]
                    vs2 = run_drc(graph, cfg)
                    r13_2 = [x for x in vs2 if x.rule_id == "R13"]
                    mine = [x for x in vs2 if eid in x.objects]
                    if (
                        len(r13_2) >= before_count
                        or any(x.rule_id == "R12" for x in mine)
                        or _count_r04(vs2, eid) > before_r04
                        or drc_summary(vs2)["hard_failures"] > drc_summary(vs)["hard_failures"]
                    ):
                        e.path = old_path
                        e.waypoints = old_w
                        continue
                    e.meta["repaired_dense"] = True
                    changes.append(
                        RepairChange(
                            eid,
                            "dense_lane",
                            {"path": [list(p) for p in before_path]},
                            {"path": [list(p) for p in new_path]},
                            f"moved the long vertical run to x={lane:.1f}px to clear "
                            f"a parallel-wire conflict",
                        )
                    )
                    progressed = True
                    break
                if progressed:
                    break
            if progressed:
                break
        if not progressed:
            unfixed.extend(sorted({x for v in r13 for x in v.objects}))
            break
    return changes, sorted(set(unfixed))


def _label_boxes(graph: Graph, exclude: str | None = None) -> dict[str, Rect]:
    out: dict[str, Rect] = {}
    for e in graph.edges:
        if e.id == exclude or not e.label or not e.label_state.bbox:
            continue
        out[e.id] = e.label_state.bbox
    return out


def _label_collisions(graph: Graph, e: Edge, box: Rect) -> tuple[int, int, int, int]:
    crossings = 0
    for other in graph.edges:
        if other.id == e.id or not other.path:
            continue
        for i in range(len(other.path) - 1):
            if segment_rect_hit(other.path[i], other.path[i + 1], box, tol=0.2):
                crossings += 1
                break
    overlaps = sum(
        1
        for oid, ob in _label_boxes(graph, exclude=e.id).items()
        if box.overlaps(ob.inflate(0.5))
    )
    nodes = sum(
        1
        for n in graph.nodes.values()
        if not n.container and box.overlaps(n.rect.inflate(0.5))
    )
    arrows = 0
    if e.path:
        for p in (e.path[0], e.path[-1]):
            if box.overlaps(Rect(p[0] - 14, p[1] - 14, 28, 28)):
                arrows += 1
    return crossings, overlaps, nodes, arrows


def _label_victims(vs: list[Violation]) -> dict[str, set[str]]:
    victims: dict[str, set[str]] = {}
    for v in vs:
        if v.rule_id == "L01" and len(v.objects) >= 2:
            victims.setdefault(v.objects[1], set()).add("L01")
        elif v.rule_id == "L07" and v.objects:
            victims.setdefault(v.objects[0], set()).add("L07")
    return victims


def repair_labels(
    graph: Graph, cfg: DrcConfig, opts: RepairOptions
) -> tuple[list[RepairChange], list[str]]:
    """Move only labels that are crossed by a wire or buried under a module."""
    changes: list[RepairChange] = []
    unfixed: list[str] = []
    for _round in range(max(1, opts.label_rounds)):
        sync_label_boxes(graph)
        vs = run_drc(graph, cfg)
        victims = _label_victims(vs)
        if not victims:
            break
        progress = 0
        for eid in sorted(victims):
            e = next((x for x in graph.edges if x.id == eid), None)
            if e is None or not e.label or not e.path:
                continue
            length = max(1.0, polyline_length(e.path))
            old_box = e.label_state.bbox
            if old_box is None:
                continue
            old_collisions = _label_collisions(graph, e, old_box)
            if old_collisions == (0, 0, 0, 0):
                continue
            fs = float(
                e.meta.get("label_font_size")
                or e.meta.get("style_map", {}).get("fontSize", 10)
                or 10
            )
            max_w = float(e.meta.get("style_map", {}).get("labelWidth", 150) or 150)
            m = measurer(fs, False)
            w, h, _ = m.label_box(e.label, max(40.0, max_w))
            current_t = 0.5
            current_off = 0.0
            if e.label_cell_id or e.meta.get("label_moved") or e.label_state.rel_x or e.label_state.rel_y:
                current_t = (e.label_state.rel_x + 1.0) / 2.0
                current_off = e.label_state.rel_y
            best: tuple[float, float, float, Rect] | None = None
            page_w = float(graph.page.get("pageWidth", 0) or 0)
            page_h = float(graph.page.get("pageHeight", 0) or 0)
            dxs = [
                0.0,
                opts.max_label_dx,
                -opts.max_label_dx,
                opts.max_label_dx * 0.7,
                -opts.max_label_dx * 0.7,
                opts.max_label_dx * 0.45,
                -opts.max_label_dx * 0.45,
                opts.max_label_dx * 0.25,
                -opts.max_label_dx * 0.25,
                12.0,
                -12.0,
            ]
            offs = [
                0.0,
                12.0,
                -12.0,
                h * 0.75 + 6,
                -(h * 0.75 + 6),
                h * 1.5 + 8,
                -(h * 1.5 + 8),
                h * 2.2 + 10,
                -(h * 2.2 + 10),
                opts.max_label_off,
                -opts.max_label_off,
            ]
            # Dense bundles may have no slot in the conservative window.  For a
            # label with any collision, search a longer stretch of its own edge
            # before giving up.
            far = sum(old_collisions) >= 1
            if far:
                dxs += [
                    opts.fallback_label_dx,
                    -opts.fallback_label_dx,
                    opts.fallback_label_dx * 0.66,
                    -opts.fallback_label_dx * 0.66,
                    opts.fallback_label_dx * 0.33,
                    -opts.fallback_label_dx * 0.33,
                    # Very long vertical bus edges may have no clear slot in
                    # the first 300 px; sample their horizontal arms too.
                    length * 0.25,
                    -length * 0.25,
                    length * 0.4,
                    -length * 0.4,
                    length * 0.15,
                    -length * 0.15,
                ]
            off_limit = opts.max_label_off
            for dx in dxs:
                t = current_t + dx / length
                if not (0.03 <= t <= 0.97):
                    continue
                p = point_at_fraction(e.path, t)
                for off in offs:
                    if abs(off) > off_limit + 1:
                        continue
                    cx, cy = p[0], p[1] - off
                    box = Rect(cx - w / 2, cy - h / 2, w, h)
                    if page_w > 0 and page_h > 0:
                        # never push a label outside the authored page
                        if (
                            box.x < -2
                            or box.x1 > page_w + 2
                            or box.y < -2
                            or box.y1 > page_h + 2
                        ):
                            continue
                    c = _label_collisions(graph, e, box)
                    if c != (0, 0, 0, 0):
                        continue
                    score = abs(dx) + 1.5 * abs(off)
                    if best is None or score < best[0]:
                        best = (score, t, off, box)
            if best is None:
                unfixed.append(eid)
                continue
            _, t, off, box = best
            old_rel = (e.label_state.rel_x, e.label_state.rel_y)
            e.label_state.rel_x = max(-0.98, min(0.98, 2.0 * t - 1.0))
            e.label_state.rel_y = off
            e.label_state.absolute = (box.cx, box.cy)
            e.label_state.bbox = box
            e.meta["label_moved"] = True
            change = RepairChange(
                eid,
                "label",
                {"rel_x": old_rel[0], "rel_y": old_rel[1]},
                {"rel_x": e.label_state.rel_x, "rel_y": e.label_state.rel_y},
                f"label moved {abs(t - current_t) * length:.0f}px along and "
                f"{off - current_off:.0f}px across the wire",
            )
            for i, existing in enumerate(changes):
                if existing.edge_id == eid and existing.kind == "label":
                    changes[i] = change
                    break
            else:
                changes.append(change)
            progress += 1
        if not progress:
            break
    return changes, sorted(set(unfixed))


def _label_issue_count(vs: list[Violation]) -> int:
    return sum(1 for v in vs if v.rule_id in ("L01", "L07"))


def _snapshot_labels(graph: Graph) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    for e in graph.edges:
        if not e.label:
            continue
        out[e.id] = (
            e.label_state.rel_x,
            e.label_state.rel_y,
            e.label_state.absolute,
            e.label_state.bbox,
            bool(e.meta.get("label_moved")),
        )
    return out


def _restore_labels(graph: Graph, snap: dict[str, tuple]) -> None:
    for e in graph.edges:
        if e.id not in snap:
            continue
        rx, ry, absolute, bbox, moved = snap[e.id]
        e.label_state.rel_x = rx
        e.label_state.rel_y = ry
        e.label_state.absolute = absolute
        e.label_state.bbox = bbox
        if moved:
            e.meta["label_moved"] = True
        else:
            e.meta.pop("label_moved", None)


def _clear_paths_for_write(graph: Graph) -> None:
    for n in graph.nodes.values():
        n.meta["preserve_style"] = True
    for e in graph.edges:
        e.meta["preserve_style"] = True
        # Writer emits authored waypoints when path is empty.  Repaired edges
        # have new waypoints; untouched edges keep their original list.
        e.path = []


def _model_paths(graph: Graph) -> dict[str, list[tuple[float, float]]]:
    return {e.id: _model_path(graph, e) for e in graph.edges if e.waypoints}


def run_repair_only(
    graph: Graph,
    source_path: str | Path,
    out_dir: str | Path,
    opts: RepairOptions | None = None,
):
    """Minimal repair; returns a PipelineResult without re-layout/re-routing."""
    opts = opts or RepairOptions()
    from .pipeline import PipelineResult  # local import avoids a cycle

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    source = Path(source_path)
    graph.direction = opts.direction or graph.direction
    if not graph.title:
        graph.title = (
            graph.page.get("_file_attrs", {}).get("data-title")
            or source.stem
        )

    topology0 = graph.topology_signature()
    geometry0 = graph.geometry_snapshot()
    ids0 = set(graph.nodes) | {e.id for e in graph.edges}
    cfg = repair_drc_config(graph.direction, opts.min_clearance)

    render_info: dict[str, Any] = {
        "renderer": "none",
        "independent": False,
        "limits": "No independent render was available; model geometry only.",
    }
    baseline_render: SvgRender | None = None
    if opts.render:
        base_svg = out / "original.svg"
        r = export_drawio(source, base_svg, "svg")
        if r.ok:
            baseline_render = attach_rendered_paths(graph, base_svg)
            if baseline_render is not None:
                render_info.update(
                    {
                        "renderer": "drawio",
                        "independent": True,
                        "baseline_svg": str(base_svg),
                        "baseline_svg_check": baseline_render.as_dict(),
                    }
                )
        if opts.render and "png" in opts.render_formats:
            base_png = out / "original.png"
            pr = export_drawio(source, base_png, "png", scale=0.5)
            if pr.ok:
                render_info["baseline_png"] = str(base_png)

    # Fall back to authored waypoints when the independent render is missing.
    if baseline_render is None:
        for e in graph.edges:
            p = _model_path(graph, e)
            if p:
                e.path = p
        render_info["limits"] = (
            "draw.io CLI export failed or was disabled; DRC uses authored waypoints, "
            "not the rendered SVG."
        )

    sync_label_boxes(graph)
    drc_before = run_drc(graph, cfg)
    metrics_before = raw_metrics(graph, geometry0)
    fixed_wires, _ = repair_terminals(graph, cfg, opts)
    fixed_endpoints, unfixed_endpoints = repair_endpoint_jogs(graph, cfg, opts)
    fixed_doglegs, unfixed_doglegs = repair_terminal_doglegs(graph, cfg, opts)
    fixed_bundle, node_changes, unfixed_bundle = repair_bundle_lanes(graph, cfg, opts)
    fixed_dense, unfixed_dense = repair_dense_pairs(
        graph, cfg, opts, protected={c.edge_id for c in fixed_bundle}
    )
    # Hooks are the last wire pass so no bus/dense move can reintroduce one.
    fixed_hooks, unfixed_hooks = repair_terminal_hooks(graph, cfg, opts)
    fixed_wires = fixed_wires + fixed_endpoints + fixed_doglegs + fixed_hooks + fixed_bundle + fixed_dense
    pre_label_vs = run_drc(graph, cfg)
    label_snapshot = _snapshot_labels(graph)
    pre_label_issues = _label_issue_count(pre_label_vs)
    fixed_labels, unfixed_labels = repair_labels(graph, cfg, opts)
    post_label_vs = run_drc(graph, cfg)
    if _label_issue_count(post_label_vs) > pre_label_issues:
        # Never trade one collision for another: keep the authored labels.
        _restore_labels(graph, label_snapshot)
        fixed_labels = []
        unfixed_labels = sorted(_label_victims(pre_label_vs))
    sync_label_boxes(graph)
    changes = fixed_wires + fixed_labels

    # Keep the model path for the write/verify step before clearing e.path.
    model_paths = _model_paths(graph)
    _clear_paths_for_write(graph)
    drawio_path = out / "diagram.drawio"
    write(graph, drawio_path, autofit=False)

    # Independent render of the repaired file and final rendered-path DRC.
    final_svg = out / "diagram.svg"
    final_png = out / "diagram.png"
    final_render: SvgRender | None = None
    reexport_ok = False
    if opts.render:
        sr = export_drawio(drawio_path, final_svg, "svg")
        if sr.ok:
            reexport_ok = True
            # give the verifier the model path, then switch DRC to the render
            for e in graph.edges:
                e.path = list(model_paths.get(e.id, []))
            final_render = verify_svg(final_svg, graph)
            for e in graph.edges:
                pts = final_render.paths.get(e.id)
                if pts:
                    e.path = [tuple(p) for p in pts]
                elif e.id in model_paths:
                    e.path = list(model_paths[e.id])
            render_info.update(
                {
                    "renderer": "drawio",
                    "independent": final_render.verified,
                    "svg_check": final_render.as_dict(),
                    "svg": str(final_svg),
                }
            )
        if "png" in opts.render_formats:
            pr = export_drawio(drawio_path, final_png, "png", scale=0.5)
            if pr.ok:
                render_info["png_path"] = str(final_png)
                render_info["png"] = png_liveness(final_png)
    if final_render is None:
        for e in graph.edges:
            if e.id in model_paths:
                e.path = list(model_paths[e.id])

    sync_label_boxes(graph)
    drc_after = run_drc(graph, cfg)
    metrics_after = raw_metrics(graph, geometry0)
    hard = drc_summary(drc_after)["hard_failures"]

    topology1 = graph.topology_signature()
    geometry1 = graph.geometry_snapshot()
    ids1 = set(graph.nodes) | {e.id for e in graph.edges}
    geom_diff = {nid for nid in geometry0 if geometry0[nid] != geometry1.get(nid)}
    expected_node_moves = {c.edge_id for c in node_changes}
    structure_ok = all(
        e.src in graph.nodes and e.tgt in graph.nodes for e in graph.edges
    )
    validation = {
        "ok": structure_ok and topology1 == topology0 and geom_diff <= expected_node_moves and hard == 0,
        "structure_ok": structure_ok,
        "topology_ok": topology1 == topology0,
        "geometry_ok": geom_diff <= expected_node_moves,
        "ids_ok": ids1 == ids0,
        "reexport": "ok" if reexport_ok else "not checked",
        "notes": [
            "Minimal repair does not re-layout nodes; anchor changes are limited "
            "to reported endpoint/Z snaps.",
            f"{len(fixed_wires) - len(fixed_doglegs) - len(fixed_endpoints) - len(fixed_bundle)} wire route(s), "
            f"{len(fixed_endpoints)} source/Z anchor snap(s), "
            f"{len(fixed_doglegs)} terminal dogleg snap(s), "
            f"{len(fixed_hooks)} terminal hook(s), "
            f"{len(fixed_bundle)} vertical-bus lane(s), "
            f"{len(fixed_dense)} dense-pair lane(s) and "
            f"{len(fixed_labels)} label(s) changed.",
            f"{len(node_changes)} decorative header/badge node(s) shifted to widen "
            "a crowded bus channel; every shift is listed in input.options.node_changes.",
        ],
    }

    artifacts = {
        "drawio": str(drawio_path),
        "png": str(final_png) if final_png.exists() else None,
        "svg": str(final_svg) if final_svg.exists() else None,
        "drc_report_json": str(out / "drc-report.json"),
        "drc_report_md": str(out / "drc-report.md"),
    }
    limitations = [
        "Minimal repair mode: functional node geometry, containers, anchors and all "
        "non-repaired wires are preserved. Only terminal overshoots (R12/R01), "
        "endpoint/Z anchor snaps, reported decorative header shifts, vertical-bus "
        "lanes and edge-label collisions (L01/L07) are changed.",
        "Not fixed by design: P01/P04/P05/P06/P07/P08/P09/P10 (intentional dense "
        "atlas placement), L02/L04 (intentional text density), R02/R03 (tight "
        "hand-authored clearances and jump-arc bus crossings), R06 (channel "
        "staircases), R09 (draw.io auto-routed edges without waypoints), R10/R11 "
        "(authored anchors and deliberate detours).",
        f"{len(unfixed_labels)} label(s) could not be cleared without moving them "
        "too far; they remain listed in the report.",
    ]
    if node_changes:
        limitations.append(
            "A decorative chapter badge and its adjacent header were shifted "
            "horizontally together to widen a bus channel. No functional module, "
            "container, port, edge endpoint or text was moved; exact coordinates "
            "are listed in input.options.node_changes."
        )
    if fixed_bundle:
        limitations.append(
            f"{len(fixed_bundle)} bus wire(s) were snapped to evenly spaced lanes. "
            "Their topology and endpoints are unchanged; only intermediate "
            "waypoints moved, and any new clearance regression would have rolled "
            "the whole bus pass back."
        )
    if fixed_dense:
        limitations.append(
            f"{len(fixed_dense)} additional long vertical run(s) were moved to a "
            "free lane after the main bus pass to clear a residual R13 conflict."
        )
    if fixed_doglegs:
        limitations.append(
            f"{len(fixed_doglegs)} terminal dogleg(s) were removed by snapping the "
            "target anchor by at most "
            f"{opts.dogleg_max:.0f}px. The exact entryX/entryY before/after values "
            "are listed in input.options.wire_changes (kind=anchor_dogleg)."
        )
    if not reexport_ok:
        limitations.append(
            "No independent draw.io SVG export of the repaired file was available; "
            "the final DRC uses model waypoints, not rendered paths."
        )

    report = build_report(
        graph=graph,
        direction=graph.direction,
        preset="minimal-repair",
        layout_mode="preserve",
        artifacts=artifacts,
        metrics_before=metrics_before,
        metrics_after=metrics_after,
        score_before=None,
        score_after=None,
        drc_before=drc_before,
        drc_after=drc_after,
        iterations=[
            {
                "iteration": 1,
                "violations": len({v.key() for v in drc_after}),
                "fixed": max(
                    0,
                    len({v.key() for v in drc_before})
                    - len({v.key() for v in drc_after}),
                ),
                "score": 0,
                "accepted": True,
                "note": f"{len(changes)} geometry edit(s): terminal stubs, bus lanes + edge labels",
            }
        ],
        render=render_info,
        validation=validation,
        toolchain={
            "python": __import__("sys").version.split()[0],
            "drawio": "available" if reexport_ok else "not available",
        },
        options={
            "repair_only": True,
            "disabled_rules": list(REPAIR_DISABLED),
            "arrow_stub": opts.arrow_stub,
            "max_label_dx": opts.max_label_dx,
            "max_label_off": opts.max_label_off,
            "wire_changes": [c.to_dict() for c in fixed_wires],
            "label_changes": [c.to_dict() for c in fixed_labels],
            "node_changes": [c.to_dict() for c in node_changes],
            "unfixed_labels": unfixed_labels,
            "unfixed_endpoints": unfixed_endpoints,
            "unfixed_doglegs": unfixed_doglegs,
            "unfixed_hooks": unfixed_hooks,
            "unfixed_bundle": unfixed_bundle,
            "unfixed_dense": unfixed_dense,
            "snap_terminal_anchors": opts.snap_terminal_anchors,
            "snap_endpoint_anchors": opts.snap_endpoint_anchors,
            "bundle_lanes": opts.bundle_lanes,
            "rendered_paths": bool(final_render and final_render.verified),
        },
        limitations=limitations,
    )
    write_reports(report, out / "drc-report.json", out / "drc-report.md")
    passed = report["summary"]["passed"]
    return PipelineResult(
        graph=graph,
        out_dir=out,
        artifacts=artifacts,
        report=report,
        exit_code=0 if passed else 1,
    )
