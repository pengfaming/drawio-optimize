"""Verify the *rendered* SVG exported by draw.io.

Draw.io's SVG export keeps ``data-cell-id`` on group elements, so each connector
can be mapped back to its edge id.  The export is cropped, therefore the model
translation is recovered by matching node rectangles; if that cannot be done the
check is reported as unavailable instead of being silently skipped.

The checks here close the loop on the classic failure mode where the XML
contains the right waypoints but the renderer produces a different line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from xml.etree import ElementTree as ET

from .geometry import Rect, bend_count, dist, is_orthogonal_segment
from .model import Graph

NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)"


@dataclass
class SvgRender:
    verified: bool = False
    method: str = "unnamed"
    reason: str = ""
    paths: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    nodes: dict[str, Rect] = field(default_factory=dict)
    translation: tuple[float, float] | None = None
    diagonal_segments: int = 0
    endpoint_errors: dict[str, float] = field(default_factory=dict)
    bend_mismatches: dict[str, tuple[int, int]] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    limitations: str = (
        "Text is rendered as foreignObject/HTML by draw.io; label geometry is "
        "therefore checked from measured font metrics, not from SVG text nodes."
    )

    def as_dict(self) -> dict:
        return {
            "verified": self.verified,
            "method": self.method,
            "reason": self.reason,
            "translation": list(self.translation) if self.translation else None,
            "edges_matched": len(self.paths),
            "diagonal_segments": self.diagonal_segments,
            "endpoint_errors": {k: round(v, 2) for k, v in self.endpoint_errors.items()},
            "bend_mismatches": {k: list(v) for k, v in self.bend_mismatches.items()},
            "issues": self.issues,
            "limitations": self.limitations,
        }


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _translate(elem) -> tuple[float, float]:
    t = elem.get("transform") or ""
    m = re.search(rf"translate\(\s*({NUM})[ ,]+({NUM})\s*\)", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(rf"translate\(\s*({NUM})\s*\)", t)
    if m:
        return float(m.group(1)), 0.0
    return 0.0, 0.0


def _parse_d(d: str) -> list[tuple[float, float]] | None:
    """Flatten an SVG path to a polyline.

    Draw.io uses tiny cubic arcs for ``jumpStyle=arc`` at wire crossings.  An
    earlier version rejected every path containing ``C`` and therefore silently
    dropped all jump-arc edges (54 of 136 in the atlas).  Curves are flattened
    to their endpoint here: the decorative jump bump does not change the wire's
    topology or its obstacle clearance.
    """
    tokens = re.findall(rf"[MmLlHhVvCcSsQqTtAaZz]|{NUM}", d)
    if not tokens:
        return None
    pts: list[tuple[float, float]] = []
    i = 0
    cmd: str | None = None
    cur = (0.0, 0.0)
    start = (0.0, 0.0)

    def need(n: int) -> list[float]:
        nonlocal i
        vals = []
        for k in range(n):
            vals.append(float(tokens[i + k]))
        i += n
        return vals

    while i < len(tokens):
        tok = tokens[i]
        if re.fullmatch(r"[A-Za-z]", tok):
            cmd = tok
            i += 1
            if cmd in ("Z", "z"):
                cur = start
                pts.append(cur)
                continue
        if cmd is None:
            return None
        c = cmd.upper()
        rel = cmd.islower()
        try:
            if c == "M":
                x, y = need(2)
                cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
                start = cur
                pts.append(cur)
                # additional coordinate pairs after M are implicit lineto
                cmd = "l" if rel else "L"
            elif c == "L":
                x, y = need(2)
                cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
                pts.append(cur)
            elif c == "H":
                (x,) = need(1)
                cur = (cur[0] + x if rel else x, cur[1])
                pts.append(cur)
            elif c == "V":
                (y,) = need(1)
                cur = (cur[0], cur[1] + y if rel else y)
                pts.append(cur)
            elif c == "C":
                x1, y1, x2, y2, x, y = need(6)
                if rel:
                    x1, y1, x2, y2, x, y = (
                        cur[0] + x1, cur[1] + y1,
                        cur[0] + x2, cur[1] + y2,
                        cur[0] + x, cur[1] + y,
                    )
                cur = (x, y)
                pts.append(cur)
            elif c == "S":
                x2, y2, x, y = need(4)
                if rel:
                    x2, y2, x, y = cur[0] + x2, cur[1] + y2, cur[0] + x, cur[1] + y
                cur = (x, y)
                pts.append(cur)
            elif c == "Q":
                x1, y1, x, y = need(4)
                if rel:
                    x1, y1, x, y = cur[0] + x1, cur[1] + y1, cur[0] + x, cur[1] + y
                cur = (x, y)
                pts.append(cur)
            elif c == "T":
                x, y = need(2)
                cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
                pts.append(cur)
            elif c == "A":
                _rx, _ry, _rot, _laf, _sf, x, y = need(7)
                cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
                pts.append(cur)
            elif c == "Z":
                pass
            else:
                return None
        except (IndexError, ValueError):
            return None
    # drop consecutive duplicates (curve endpoints often repeat)
    clean: list[tuple[float, float]] = []
    for p in pts:
        if not clean or dist(clean[-1], p) > 0.01:
            clean.append(p)
    if len(clean) < 2:
        return None
    return clean


def _arrow_tip(poly: list[tuple[float, float]]) -> tuple[float, float]:
    """Return the apex of a closed arrow triangle.

    The line meets the two base corners, so an arrow polygon has one apex and
    two base points.  The apex is the vertex farthest from the midpoint of the
    other two; this works for both start and end arrows (the old "farthest from
    the line end" heuristic picked a base corner for start arrows).
    """
    pts: list[tuple[float, float]] = []
    for p in poly:
        if not pts or dist(pts[-1], p) > 0.01:
            pts.append(p)
    if len(pts) > 1 and dist(pts[0], pts[-1]) <= 0.01:
        pts = pts[:-1]
    if len(pts) < 3:
        return pts[0] if pts else (0.0, 0.0)
    best = None
    for i in range(len(pts)):
        a = pts[(i + 1) % len(pts)]
        b = pts[(i + 2) % len(pts)]
        mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        d = dist(pts[i], mid)
        if best is None or d > best[0]:
            best = (d, pts[i])
    return best[1]


def parse_svg(svg_path: str | Path) -> SvgRender:
    out = SvgRender(method="drawio-svg")
    try:
        root = ET.parse(str(svg_path)).getroot()
    except Exception as exc:
        out.reason = f"cannot parse SVG: {exc}"
        return out

    node_rects: dict[str, Rect] = {}
    edge_paths: dict[str, list[tuple[float, float]]] = {}
    arrow_paths: dict[str, list[tuple[float, float]]] = {}

    def walk(elem, tx: float, ty: float, cell: str | None) -> None:
        tag = _local(elem.tag)
        cid = elem.get("data-cell-id")
        if cid:
            cell = cid
        if tag in ("g", "svg"):
            dx, dy = _translate(elem)
            for child in elem:
                walk(child, tx + dx, ty + dy, cell)
        elif tag == "rect" and cell:
            try:
                w = float(elem.get("width", 0))
                h = float(elem.get("height", 0))
                if w > 1 and h > 1:
                    node_rects.setdefault(
                        cell, Rect(float(elem.get("x", 0)) + tx, float(elem.get("y", 0)) + ty, w, h)
                    )
            except ValueError:
                pass
        elif tag == "path" and cell:
            d = elem.get("d") or ""
            pts = _parse_d(d)
            if not pts:
                return
            if d.strip().endswith("Z"):
                arrow_paths.setdefault(cell, []).append(pts)
            else:
                cur = edge_paths.get(cell)
                if cur is None or len(pts) > len(cur):
                    edge_paths[cell] = pts

    walk(root, 0.0, 0.0, None)

    for nid, r in node_rects.items():
        out.nodes[nid] = r
    # graph is not available here; caller supplies it via map_to_model
    out.paths = edge_paths
    out._raw_arrows = arrow_paths  # type: ignore[attr-defined]
    if not edge_paths:
        out.reason = "no connector paths found (SVG may use a different export mode)"
        return out
    out.verified = True
    if any(_has_diagonal(p) for p in edge_paths.values()):
        out.diagonal_segments = sum(_diagonal_count(p) for p in edge_paths.values())
        out.issues.append(f"{out.diagonal_segments} diagonal segment(s) in the render")
    return out


def _has_diagonal(p) -> bool:
    return any(not is_orthogonal_segment(p[i], p[i + 1]) for i in range(len(p) - 1))


def _diagonal_count(p) -> int:
    return sum(1 for i in range(len(p) - 1) if not is_orthogonal_segment(p[i], p[i + 1]))


def map_to_model(render: SvgRender, graph: Graph) -> SvgRender:
    """Translate SVG coordinates back into model coordinates and compare paths."""
    if not render.verified:
        return render
    pairs = []
    for nid, r in render.nodes.items():
        n = graph.nodes.get(nid)
        if n:
            pairs.append((r.x - n.x, r.y - n.y))
    if not pairs:
        render.verified = False
        render.reason = "render has no node ids matching the model; cannot align coordinates"
        return render
    tx = median([p[0] for p in pairs])
    ty = median([p[1] for p in pairs])
    drift = max(max(abs(p[0] - tx), abs(p[1] - ty)) for p in pairs)
    if drift > 2.5:
        render.verified = False
        render.reason = f"node translation is inconsistent (drift {drift:.1f}px)"
        return render
    render.translation = (tx, ty)

    arrows = getattr(render, "_raw_arrows", {})
    translated: dict[str, list[tuple[float, float]]] = {}
    for eid, pts in render.paths.items():
        moved = [(x - tx, y - ty) for x, y in pts]
        e = next((x for x in graph.edges if x.id == eid), None)
        arrow_sets = arrows.get(eid) or []
        if not e or not e.path:
            if arrow_sets:
                translated_arrows = [
                    [(x - tx, y - ty) for x, y in poly] for poly in arrow_sets
                ]
                tips = [_arrow_tip(poly) for poly in translated_arrows]
                end_pt = min(tips, key=lambda p: dist(p, moved[-1]))
                # only attach a tip that belongs to the far end
                if (
                    0.25 < dist(end_pt, moved[-1]) < 40
                    and dist(end_pt, moved[-1]) < dist(end_pt, moved[0])
                ):
                    moved.append(end_pt)
            translated[eid] = moved
            continue
        start_err = dist(moved[0], e.path[0])
        end_pt = moved[-1]
        if arrow_sets:
            end_candidates: list[tuple[float, float]] = []
            start_candidates: list[tuple[float, float]] = []
            for poly in arrow_sets:
                tr = [(x - tx, y - ty) for x, y in poly]
                tip = _arrow_tip(tr)
                end_candidates.append(tip)
                start_candidates.append(tip)
            end_pt = min(end_candidates, key=lambda p: dist(p, e.path[-1]))
            # the SVG line stops at the base of the arrow; append the tip so
            # the rendered path ends at the anchor like the model path
            if 0.25 < dist(end_pt, moved[-1]) < 40:
                moved.append(end_pt)
            start_pt = min(start_candidates, key=lambda p: dist(p, e.path[0]))
            if (
                0.25 < dist(start_pt, moved[0]) < 40
                and dist(start_pt, moved[0]) < dist(start_pt, moved[-1])
            ):
                moved.insert(0, start_pt)
        translated[eid] = moved
        e.rendered_path = list(moved)
        start_err = dist(moved[0], e.path[0])
        end_err = dist(end_pt, e.path[-1])
        render.endpoint_errors[eid] = max(start_err, end_err)
        rb = bend_count(moved)
        mb = bend_count(e.path)
        if rb != mb:
            render.bend_mismatches[eid] = (mb, rb)
    for eid, err in render.endpoint_errors.items():
        if err > 12:
            render.issues.append(f"edge {eid}: rendered endpoint differs by {err:.1f}px")
    for eid, (mb, rb) in render.bend_mismatches.items():
        render.issues.append(f"edge {eid}: model {mb} bends, render {rb} bends")
    # Keep only paths that belong to graph edges; preserved images/text cells
    # can also emit non-Z paths and must not inflate "edges matched".
    edge_ids = {e.id for e in graph.edges}
    render.paths = {
        eid: pts for eid, pts in translated.items() if eid in edge_ids
    }
    return render


def verify_svg(svg_path: str | Path, graph: Graph) -> SvgRender:
    r = parse_svg(svg_path)
    if not r.verified:
        return r
    r = map_to_model(r, graph)
    if r.issues:
        # still verified as a render, but with findings
        r.reason = "; ".join(r.issues)
    return r
