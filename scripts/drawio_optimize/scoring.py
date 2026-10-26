"""Normalised multi-objective scoring.

All terms are dimensionless so different units never get added raw.  A hard
constraint violation dominates the score; the soft terms tune the look.
"""
from __future__ import annotations

from statistics import mean

from .geometry import bend_count, polyline_crossings, polyline_length
from .model import Graph

PRESETS: dict[str, dict[str, float]] = {
    "compact": {
        "crossings": 1.0, "bends": 0.6, "wirelength": 1.2,
        "clearance": 1.0, "alignment": 0.4, "labels": 1.0, "displacement": 0.2,
    },
    "balanced": {
        "crossings": 1.0, "bends": 1.0, "wirelength": 0.8,
        "clearance": 1.2, "alignment": 1.0, "labels": 1.2, "displacement": 1.0,
    },
    "clean-routing": {
        "crossings": 2.0, "bends": 1.8, "wirelength": 0.5,
        "clearance": 1.5, "alignment": 0.5, "labels": 1.5, "displacement": 1.0,
    },
}

HARD_PENALTY = 1000.0


def raw_metrics(graph: Graph, baseline: dict | None = None, ranks: dict | None = None) -> dict:
    edges = [e for e in graph.edges if e.path and len(e.path) >= 2]
    crossings = 0
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            crossings += polyline_crossings(edges[i].path, edges[j].path)
    bends = sum(bend_count(e.path) for e in edges)
    wire = sum(polyline_length(e.path) for e in edges)
    labels = graph.all_label_boxes()
    label_overlaps = 0
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            if labels[i]["rect"].overlaps(labels[j]["rect"]):
                label_overlaps += 1
    node_overlaps = 0
    nodes = [n for n in graph.nodes.values() if not n.container]
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if graph.related(nodes[i].id, nodes[j].id):
                continue
            if nodes[i].rect.overlaps(nodes[j].rect):
                node_overlaps += 1
    align = 0.0
    if ranks:
        groups: dict[int, list] = {}
        for nid, r in ranks.items():
            n = graph.nodes.get(nid)
            if n and not n.container:
                groups.setdefault(str(r), []).append(n)
        for r, ns in groups.items():
            if len(ns) < 2:
                continue
            xs = [n.x + n.w / 2 for n in ns]
            ys = [n.y + n.h / 2 for n in ns]
            align += (max(xs) - min(xs)) + (max(ys) - min(ys))
    disp = 0.0
    if baseline:
        disp = graph.displacement(baseline)
    return {
        "edges": len(edges),
        "nodes": len(nodes),
        "crossings": crossings,
        "bends": bends,
        "wirelength": round(wire, 1),
        "label_overlaps": label_overlaps,
        "node_overlaps": node_overlaps,
        "alignment_error": round(align, 1),
        "displacement": round(disp, 1),
    }


def normalise(raw: dict, graph: Graph, baseline_diagonal: float = 1.0, labels: int = 1) -> dict:
    e = max(1, raw["edges"])
    n = max(1, raw["nodes"])
    nodes = [x for x in graph.nodes.values() if not x.container]
    avg_size = mean([(x.w + x.h) / 2 for x in nodes]) if nodes else 100.0
    return {
        "crossings": raw["crossings"] / e,
        "bends": raw["bends"] / e,
        "wirelength": raw["wirelength"] / max(1.0, e * (avg_size * 4)),
        "clearance": raw.get("clearance_violations", 0) / e,
        "alignment": raw["alignment_error"] / max(1.0, n * avg_size),
        "labels": (raw["label_overlaps"] + raw["node_overlaps"]) / max(1, labels + n),
        "displacement": raw["displacement"] / max(1.0, baseline_diagonal),
    }


def score(
    raw: dict,
    graph: Graph,
    preset: str = "balanced",
    weights: dict | None = None,
    hard_failures: int = 0,
    baseline_diagonal: float = 1.0,
    labels: int = 1,
) -> dict:
    w = dict(PRESETS.get(preset, PRESETS["balanced"]))
    if weights:
        w.update(weights)
    norm = normalise(raw, graph, baseline_diagonal, labels)
    soft = sum(w.get(k, 0.0) * norm.get(k, 0.0) for k in w)
    total = soft + HARD_PENALTY * hard_failures
    return {
        "preset": preset,
        "weights": w,
        "normalised": {k: round(v, 4) for k, v in norm.items()},
        "soft": round(soft, 4),
        "hard_failures": hard_failures,
        "total": round(total, 4),
    }


def better(a: dict, b: dict | None) -> bool:
    if b is None:
        return True
    return a["total"] < b["total"] - 1e-9
