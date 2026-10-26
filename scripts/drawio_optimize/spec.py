"""Structured spec -> Graph.

The spec is the stable contract between a natural-language request and the
engine.  It intentionally has no layout coordinates unless the user wants to pin
something; layout is the skill's job, not the caller's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .layout import LayoutConfig
from .model import Edge, Graph, Node, Port
from .ports import PREFERRED
from .util import SIGNAL_STYLES

# infer_kind lives in drawio_io; import it lazily to keep spec standalone
from .drawio_io import infer_kind, infer_signal


@dataclass
class SpecOptions:
    layout: LayoutConfig = field(default_factory=LayoutConfig)
    preset: str = "balanced"
    weights: dict[str, float] = field(default_factory=dict)
    render_formats: tuple[str, ...] = ("png", "svg")
    title: str = ""


class SpecError(ValueError):
    pass


def graph_from_spec(spec: dict[str, Any]) -> tuple[Graph, SpecOptions]:
    if not isinstance(spec, dict):
        raise SpecError("spec must be a mapping")
    direction = str(spec.get("direction", "LR")).upper()
    if direction not in ("LR", "RL", "TB", "BT"):
        raise SpecError(f"invalid direction {direction!r}; use LR, RL, TB or BT")
    opts = SpecOptions()
    opts.preset = str(spec.get("preset", "balanced"))
    if opts.preset not in ("compact", "balanced", "clean-routing"):
        raise SpecError(f"unknown preset {opts.preset!r}")
    opts.weights = dict(spec.get("weights") or {})
    opts.title = str(spec.get("title") or "")
    cons = spec.get("constraints") or {}
    opts.layout = LayoutConfig(
        direction=direction,
        h_gap=float(cons.get("h_gap", cons.get("rank_gap", 84))),
        v_gap=float(cons.get("v_gap", cons.get("node_gap", 52))),
        margin=float(cons.get("margin", 40)),
        mode=str(spec.get("layout_mode", "full")),
        align=str(cons.get("align", "center")),
        uniform_kinds=bool(cons.get("uniform_kinds", True)),
    )

    g = Graph(direction=direction, title=opts.title)
    g.page = {"_page_name": spec.get("page_name") or "Page-1"}

    node_specs = list(spec.get("nodes") or [])
    group_specs = list(spec.get("groups") or spec.get("containers") or [])
    group_of: dict[str, str] = {}

    for gs in group_specs:
        gid = str(gs.get("id") or gs.get("name") or "").strip()
        if not gid:
            raise SpecError("every group needs an id")
        if gid in g.nodes:
            raise SpecError(f"duplicate id {gid!r} (group and node ids share a namespace)")
        g.add_node(
            Node(
                id=gid,
                label=str(gs.get("label") or gid),
                kind="group",
                container=True,
                container_style="swimlane",
                meta={"generated": True, "uniform": False},
            )
        )
        for child in gs.get("nodes") or gs.get("children") or []:
            group_of[str(child)] = gid

    for ns in node_specs:
        nid = str(ns.get("id") or ns.get("name") or "").strip()
        if not nid:
            raise SpecError("every node needs an id")
        if nid in g.nodes:
            raise SpecError(f"duplicate id {nid!r}")
        label = str(ns.get("label") or nid)
        kind = str(ns.get("kind") or infer_kind(label))
        if kind not in (
            "interface", "controller", "memory", "datapath", "clock", "external", "default", "group"
        ):
            kind = "default"
        parent = ns.get("group")
        if parent is not None:
            parent = str(parent)
            if parent not in {n.id for n in g.nodes.values() if n.container}:
                raise SpecError(f"node {nid!r} refers to unknown group {parent!r}")
        elif nid in group_of:
            parent = group_of[nid]
        n = Node(
            id=nid,
            label=label,
            kind=kind,
            parent=parent,
            locked=bool(ns.get("locked") or ns.get("fixed") or False),
            meta={"generated": True, "font_size": float(ns.get("font_size", 12))},
        )
        size = ns.get("size")
        if isinstance(size, (list, tuple)) and len(size) == 2:
            n.w, n.h = float(size[0]), float(size[1])
            n.meta["keep_w"] = True
            n.meta["keep_h"] = True
        pos = ns.get("pos") or ns.get("position")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            n.x, n.y = float(pos[0]), float(pos[1])
            n.meta["keep_pos"] = True
            n.locked = True
        g.add_node(n)

    used = {n.id for n in g.nodes.values()}
    for i, es in enumerate(spec.get("edges") or []):
        src_raw = es.get("from", es.get("source"))
        tgt_raw = es.get("to", es.get("target"))
        if src_raw is None or tgt_raw is None:
            raise SpecError(f"edge #{i} needs 'from' and 'to'")
        src, sp = _resolve_endpoint(str(src_raw), g, direction)
        tgt, tp = _resolve_endpoint(str(tgt_raw), g, direction)
        if src is None:
            raise SpecError(f"edge #{i} refers to unknown node {src_raw!r}")
        if tgt is None:
            raise SpecError(f"edge #{i} refers to unknown node {tgt_raw!r}")
        label = str(es.get("label") or "")
        signal = str(es.get("signal") or infer_signal(label))
        if signal not in SIGNAL_STYLES:
            signal = "data"
        eid = str(es.get("id") or f"e{i + 1}")
        base = eid
        k = 2
        while eid in used:
            eid = f"{base}_{k}"
            k += 1
        used.add(eid)
        edge = Edge(
            id=eid,
            src=src,
            tgt=tgt,
            label=label,
            signal=signal,
            meta={"generated": True, "label_font_size": float(es.get("font_size", 10))},
        )
        if sp:
            edge.src_port = sp
        if tp:
            edge.tgt_port = tp
        g.edges.append(edge)

    _validate_unique(g)
    return g, opts


def _resolve_endpoint(raw: str, g: Graph, direction: str) -> tuple[str | None, Port | None]:
    raw = raw.strip()
    # longest matching node id first (ids may contain dots)
    candidates = sorted(g.nodes, key=len, reverse=True)
    for nid in candidates:
        if raw == nid:
            return nid, None
        if raw.startswith(nid + "."):
            qualifier = raw[len(nid) + 1:].strip()
            port = _port_from_qualifier(qualifier, direction)
            return nid, port
    return None, None


def _port_from_qualifier(q: str, direction: str) -> Port | None:
    ql = q.lower()
    side = None
    if len(q) == 1 and q.upper() in ("L", "R", "T", "B"):
        side = q.upper()
    elif ql in ("out", "output", "o"):
        side = PREFERRED[direction][0]
    elif ql in ("in", "input", "i"):
        side = PREFERRED[direction][1]
    if side:
        return Port(side=side, frac=0.5, role="out" if side == PREFERRED[direction][0] else "in",
                    locked=True, name=q)
    return None


def _validate_unique(g: Graph) -> None:
    ids = set()
    for n in g.nodes.values():
        if n.id in ids:
            raise SpecError(f"duplicate id {n.id!r}")
        ids.add(n.id)
    for e in g.edges:
        if e.id in ids:
            raise SpecError(f"duplicate edge id {e.id!r}")
        ids.add(e.id)


def validate_spec(spec: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    try:
        g, _ = graph_from_spec(spec)
    except SpecError as exc:
        return [str(exc)]
    ids = {n.id for n in g.nodes.values()}
    for e in g.edges:
        if e.src not in ids or e.tgt not in ids:
            problems.append(f"edge {e.id} has a missing endpoint")
    if not g.nodes:
        problems.append("spec has no nodes")
    return problems
