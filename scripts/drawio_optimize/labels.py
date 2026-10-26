"""Label placement and text fitting.

Node labels are centred by Draw.io; the job here is to make sure the box is big
enough for the measured text (CJK included).  Edge labels are placed along the
routed path: draw.io interprets a child label cell's ``x`` as a position in
[-1, 1] across the whole edge and ``y`` as a vertical pixel offset (positive up).
That calibration was verified against a real headless export, so the label
position we compute is the position draw.io renders.
"""
from __future__ import annotations

from dataclasses import dataclass

from .geometry import Rect, point_at_fraction, segment_rect_hit
from .model import Edge, Graph, Node
from .textmetrics import measurer


@dataclass
class LabelConfig:
    font_size: float = 10.0
    node_font_size: float = 12.0
    pad: float = 2.0
    max_label_width: float = 150.0
    allow_node_grow: bool = True
    avoid_own_edge: bool = False


def place_labels(graph: Graph, config: LabelConfig | None = None) -> None:
    cfg = config or LabelConfig()
    for n in graph.nodes.values():
        _fit_node_label(n, cfg)

    placed: list[Rect] = []
    # place labels for edges with more room first
    for e in sorted(graph.edges, key=lambda x: -(x.length or 0)):
        if not e.label:
            continue
        box = _place_edge_label(graph, e, cfg, placed)
        if box:
            placed.append(box)


def _fit_node_label(n: Node, cfg: LabelConfig) -> None:
    if not n.label or n.container:
        return
    m = measurer(float(n.meta.get("font_size", cfg.node_font_size)), False)
    inner_w = max(20.0, n.w - 14.0)
    lines = m.wrap(n.label, inner_w)
    need_h = len(lines) * m.font_size * 1.24 + 12.0
    need_w = max(m.line_width(line) for line in lines) + 18.0 if lines else n.w
    if need_w > n.w + 0.5 and not n.locked and cfg.allow_node_grow:
        n.w = min(need_w, max(n.w, cfg.max_label_width))
        n.meta["label_resized"] = True
    if need_h > n.h + 0.5 and not n.locked and cfg.allow_node_grow:
        n.h = need_h
        n.meta["label_resized"] = True
    if need_h > n.h + 0.5:
        n.meta["label_overflow"] = True


def _place_edge_label(graph: Graph, e: Edge, cfg: LabelConfig, placed: list[Rect]) -> Rect | None:
    if not e.path:
        return None
    text = e.label
    m = measurer(float(e.meta.get("label_font_size", cfg.font_size)), False)
    max_w = max(
        40.0,
        float(e.meta.get("style_map", {}).get("labelWidth", cfg.max_label_width) or cfg.max_label_width),
    )
    w, h, _ = m.label_box(text, max_w)
    total = e.length
    candidates: list[tuple[float, float]] = []
    for t in (0.5, 0.42, 0.58, 0.35, 0.65, 0.28, 0.72, 0.2, 0.8):
        for off in (0.0, h / 2 + 4, -h / 2 - 4, h + 10, -h - 10, h * 1.7 + 8, -h * 1.7 - 8):
            candidates.append((t, off))
    best = None
    for t, off in candidates:
        p = point_at_fraction(e.path, t)
        cx, cy = p[0], p[1] - off
        box = Rect(cx - w / 2, cy - h / 2, w, h)
        score = _label_collisions(graph, e, box, placed, cfg) * 1000.0
        score += abs(t - 0.5) * 20.0 + abs(off) * 0.6
        if best is None or score < best[0]:
            best = (score, t, off, box)
        if score < 1.0:
            break
    if best is None:
        return None
    _, t, off, box = best
    e.label_state.text = text
    e.label_state.rel_x = max(-0.98, min(0.98, 2.0 * t - 1.0))
    e.label_state.rel_y = off
    e.label_state.absolute = (box.cx, box.cy)
    e.label_state.bbox = box
    e.label_state.attached = True
    return box


def _label_collisions(graph: Graph, e: Edge, box: Rect, placed: list[Rect], cfg: LabelConfig) -> int:
    n = 0
    for node in graph.nodes.values():
        if node.container:
            continue
        if box.overlaps(node.rect.inflate(1)):
            # labels may touch their own endpoints only when the edge is short
            if node.id in (e.src, e.tgt) and e.length < 80:
                continue
            n += 1
    for other in placed:
        if box.overlaps(other.inflate(1)):
            n += 1
    # do not bury the arrowhead under its own label
    if e.path:
        for p in (e.path[0], e.path[-1]):
            if box.overlaps(Rect(p[0] - 10, p[1] - 10, 20, 20)):
                n += 1
                break
    for other in graph.edges:
        if other.id == e.id and not cfg.avoid_own_edge:
            continue
        if not other.path:
            continue
        for i in range(len(other.path) - 1):
            if segment_rect_hit(other.path[i], other.path[i + 1], box, tol=0.2):
                n += 1
                break
    return n


def label_report(graph: Graph) -> dict:
    boxes = graph.all_label_boxes()
    overlaps = 0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if boxes[i]["rect"].overlaps(boxes[j]["rect"]):
                overlaps += 1
    return {
        "labels": len(boxes),
        "overlaps": overlaps,
        "missing_edge_labels": sum(1 for e in graph.edges if not e.label),
    }
