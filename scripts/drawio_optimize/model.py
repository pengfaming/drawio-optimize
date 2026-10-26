"""Unified graph model shared by parser, layout, router, DRC and writer.

The model deliberately keeps four coordinate concepts apart:

* ``Node.x/y/w/h``      -- geometry (absolute canvas coordinates)
* ``Port.side/frac``    -- semantic connection anchor on a node boundary
* ``Edge.author_waypoints`` -- control points read from an existing .drawio
* ``Edge.path``         -- the final orthogonal polyline computed by the router

``Edge.rendered_path`` is filled from the real export (SVG) when available and
is never used for writing.  This separation is what prevents "the XML looks
right but draw.io renders a different line".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .geometry import Rect, polyline_length, simplify_points
from .textmetrics import measurer

SIDES = ("L", "R", "T", "B")
SIDE_NORMAL = {"L": (-1.0, 0.0), "R": (1.0, 0.0), "T": (0.0, -1.0), "B": (0.0, 1.0)}
OPPOSITE = {"L": "R", "R": "L", "T": "B", "B": "T"}


@dataclass
class Port:
    """A connection anchor on one side of a node."""

    side: str = "R"
    frac: float = 0.5          # 0..1 along that side (L->R on T/B, T->B on L/R)
    role: str = "auto"         # in | out | bidir | auto
    signal: str = "data"
    name: str = ""             # optional logical port name from a spec
    locked: bool = False       # side was explicitly requested by the user

    def point(self, rect: Rect) -> tuple[float, float]:
        f = max(0.0, min(1.0, self.frac))
        if self.side == "L":
            return (rect.x, rect.y + rect.h * f)
        if self.side == "R":
            return (rect.x1, rect.y + rect.h * f)
        if self.side == "T":
            return (rect.x + rect.w * f, rect.y)
        return (rect.x + rect.w * f, rect.y1)

    def normal(self) -> tuple[float, float]:
        return SIDE_NORMAL[self.side]

    def copy(self) -> "Port":
        return Port(self.side, self.frac, self.role, self.signal, self.name, self.locked)


@dataclass
class Node:
    id: str
    label: str = ""
    x: float = 0.0
    y: float = 0.0
    w: float = 120.0
    h: float = 56.0
    kind: str = "default"
    locked: bool = False
    parent: str | None = None
    container: bool = False
    container_style: str = ""      # "swimlane" | "group" | "rect"
    style: str = ""                # raw draw.io style string (preserved)
    meta: dict[str, Any] = field(default_factory=dict)
    original: tuple[float, float, float, float] | None = None

    @property
    def rect(self) -> Rect:
        return Rect(self.x, self.y, self.w, self.h)

    def original_rect(self) -> Rect:
        if self.original:
            return Rect(*self.original)
        return self.rect

    def copy(self) -> "Node":
        n = Node(
            id=self.id,
            label=self.label,
            x=self.x,
            y=self.y,
            w=self.w,
            h=self.h,
            kind=self.kind,
            locked=self.locked,
            parent=self.parent,
            container=self.container,
            container_style=self.container_style,
            style=self.style,
            meta=dict(self.meta),
            original=self.original,
        )
        return n


@dataclass
class EdgeLabel:
    text: str = ""
    # Offset used by draw.io child label cells.  x is a fraction along the
    # edge (-1..1), y is a perpendicular offset in pixels (calibrated in
    # drawio_io.write via EDGE_LABEL_Y_SCALE).
    rel_x: float = 0.0
    rel_y: float = 0.0
    absolute: tuple[float, float] | None = None   # computed label centre
    bbox: Rect | None = None
    attached: bool = True                          # label is on the edge itself


@dataclass
class Edge:
    id: str
    src: str | None
    tgt: str | None
    label: str = ""
    signal: str = "data"
    src_port: Port | None = None
    tgt_port: Port | None = None
    waypoints: list[tuple[float, float]] = field(default_factory=list)  # authored
    path: list[tuple[float, float]] = field(default_factory=list)       # routed
    rendered_path: list[tuple[float, float]] = field(default_factory=list)
    style: str = ""                # raw draw.io style (preserved)
    parent: str | None = None
    label_cell_id: str | None = None
    extra_labels: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    label_state: EdgeLabel = field(default_factory=EdgeLabel)
    route_method: str = ""
    route_cost: float = 0.0

    @property
    def bends(self) -> int:
        return max(0, len(simplify_points(self.path)) - 2)

    @property
    def length(self) -> float:
        return polyline_length(self.path) if self.path else 0.0

    def copy(self) -> "Edge":
        return Edge(
            id=self.id,
            src=self.src,
            tgt=self.tgt,
            label=self.label,
            signal=self.signal,
            src_port=self.src_port.copy() if self.src_port else None,
            tgt_port=self.tgt_port.copy() if self.tgt_port else None,
            waypoints=[tuple(p) for p in self.waypoints],
            path=[tuple(p) for p in self.path],
            rendered_path=[tuple(p) for p in self.rendered_path],
            style=self.style,
            parent=self.parent,
            label_cell_id=self.label_cell_id,
            extra_labels=[dict(x) for x in self.extra_labels],
            meta=dict(self.meta),
            label_state=EdgeLabel(
                self.label_state.text,
                self.label_state.rel_x,
                self.label_state.rel_y,
                self.label_state.absolute,
                self.label_state.bbox,
                self.label_state.attached,
            ),
            route_method=self.route_method,
            route_cost=self.route_cost,
        )


@dataclass
class PreservedCell:
    """An element we do not understand; kept byte-faithful and never dropped."""

    xml: str
    parent: str | None
    cell_id: str
    reason: str = "unsupported"


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    direction: str = "LR"                 # LR | RL | TB | BT
    title: str = ""
    page: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None
    preserved: list[PreservedCell] = field(default_factory=list)
    other_pages: list[dict[str, Any]] = field(default_factory=list)
    # absolute obstacles that are not module nodes (images, notes, ...)
    extra_obstacles: list[Rect] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)

    # -- nodes -------------------------------------------------------------
    def add_node(self, node: Node) -> Node:
        self.nodes[node.id] = node
        return node

    def rect(self, node_id: str) -> Rect:
        n = self.nodes[node_id]
        return n.rect

    def parent_of(self, node_id: str) -> str | None:
        return self.nodes[node_id].parent if node_id in self.nodes else None

    def ancestors(self, node_id: str) -> list[str]:
        out: list[str] = []
        cur = self.parent_of(node_id)
        while cur and cur in self.nodes:
            out.append(cur)
            cur = self.parent_of(cur)
        return out

    def descendants(self, node_id: str) -> list[str]:
        direct = [n.id for n in self.nodes.values() if n.parent == node_id]
        out: list[str] = []
        for d in direct:
            out.append(d)
            out.extend(self.descendants(d))
        return out

    def children(self, node_id: str | None) -> list[Node]:
        return [n for n in self.nodes.values() if n.parent == node_id]

    def is_ancestor(self, maybe_ancestor: str, node_id: str) -> bool:
        return maybe_ancestor in self.ancestors(node_id)

    def related(self, a: str, b: str) -> bool:
        """True when nodes are the same, or one contains the other."""
        return a == b or self.is_ancestor(a, b) or self.is_ancestor(b, a)

    def obstacles_for(self, edge: Edge, clearance: float = 0.0) -> list[tuple[str, Rect]]:
        """Node rects that this edge must not cross.

        Source/target, their ancestors (containers) and descendants of the
        source/target containers are excluded.  Containers are never routing
        obstacles in this implementation: crossing a group boundary is legal.
        """
        if not edge.src or not edge.tgt:
            return []
        out: list[tuple[str, Rect]] = []
        for n in self.nodes.values():
            if n.container:
                continue
            if n.id in (edge.src, edge.tgt):
                continue
            if self.related(n.id, edge.src) or self.related(n.id, edge.tgt):
                continue
            r = n.rect.inflate(clearance)
            out.append((n.id, r))
        # container title bands are visual obstacles: an edge crossing a
        # swimlane title makes the group name unreadable
        for c in self.nodes.values():
            if not c.container or not c.label:
                continue
            out.append(("__title_" + c.id, Rect(c.x, c.y, c.w, 24.0).inflate(clearance)))
        return out

    # -- labels ------------------------------------------------------------
    def node_label_boxes(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for n in self.nodes.values():
            if not n.label:
                continue
            m = measurer(float(n.meta.get("font_size", 12)), n.container)
            if n.container:
                # swimlane title lives in the top band, not in the centre
                inner_w = max(10.0, n.w - 24)
                w, h, lines = m.measure(n.label, inner_w)
                out.append(
                    {
                        "owner": n.id,
                        "kind": "node",
                        "text": n.label,
                        "lines": lines,
                        "rect": Rect(n.x + 10, n.y + 4, min(w, inner_w), max(h, 16.0)),
                        "inner": Rect(n.x + 10, n.y + 4, inner_w, 22),
                    }
                )
                continue
            inner_w = max(10.0, n.w - 2 * 8)
            w, h, lines = m.measure(n.label, inner_w)
            out.append(
                {
                    "owner": n.id,
                    "kind": "node",
                    "text": n.label,
                    "lines": lines,
                    "rect": Rect(
                        n.x + (n.w - w) / 2.0,
                        n.y + (n.h - h) / 2.0,
                        w,
                        h,
                    ),
                    "inner": Rect(n.x + 4, n.y + 4, max(1, n.w - 8), max(1, n.h - 8)),
                }
            )
        return out

    def edge_label_boxes(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for e in self.edges:
            if not e.label:
                continue
            fs = float(
                e.meta.get("label_font_size")
                or e.meta.get("style_map", {}).get("fontSize", 10)
                or 10
            )
            max_w = float(e.meta.get("style_map", {}).get("labelWidth", 150) or 150)
            m = measurer(fs, False)
            w, h, _ = m.label_box(e.label, max(40.0, max_w))
            if e.label_state.absolute:
                cx, cy = e.label_state.absolute
            elif e.path:
                from .geometry import point_at_fraction

                # Draw.io's child label geometry: x in [-1,1] along the edge,
                # y in pixels upward from the edge.
                if e.label_cell_id or e.meta.get("label_moved") or e.label_state.rel_x or e.label_state.rel_y:
                    t = max(0.0, min(1.0, (e.label_state.rel_x + 1.0) / 2.0))
                else:
                    t = 0.5
                p = point_at_fraction(e.path, t)
                cx, cy = p[0], p[1] - e.label_state.rel_y
            else:
                cx, cy = (0.0, 0.0)
            out.append(
                {
                    "owner": e.id,
                    "kind": "edge",
                    "text": e.label,
                    "rect": Rect(cx - w / 2, cy - h / 2, w, h),
                }
            )
        return out

    def all_label_boxes(self) -> list[dict[str, Any]]:
        return self.node_label_boxes() + self.edge_label_boxes()

    # -- identity ----------------------------------------------------------
    def topology_signature(self) -> tuple:
        sig = []
        for e in self.edges:
            sig.append(
                (
                    e.id,
                    e.src,
                    e.tgt,
                    e.signal,
                    (e.label or "").strip(),
                )
            )
        return tuple(sorted(sig))

    def geometry_snapshot(self) -> dict[str, tuple[float, float, float, float]]:
        return {n.id: (n.x, n.y, n.w, n.h) for n in self.nodes.values()}

    def displacement(self, snapshot: dict[str, tuple[float, float, float, float]]) -> float:
        total = 0.0
        for nid, (x, y, w, h) in snapshot.items():
            n = self.nodes.get(nid)
            if n is None:
                continue
            total += abs(n.x - x) + abs(n.y - y) + abs(n.w - w) + abs(n.h - h)
        return total

    def copy(self) -> "Graph":
        g = Graph(
            direction=self.direction,
            title=self.title,
            page=dict(self.page),
            source_path=self.source_path,
            other_pages=[dict(p) for p in self.other_pages],
            extra_obstacles=[Rect(r.x, r.y, r.w, r.h) for r in self.extra_obstacles],
            diagnostics=list(self.diagnostics),
        )
        for n in self.nodes.values():
            g.nodes[n.id] = n.copy()
        g.edges = [e.copy() for e in self.edges]
        g.preserved = [
            PreservedCell(p.xml, p.parent, p.cell_id, p.reason) for p in self.preserved
        ]
        return g

    def summary(self) -> dict[str, Any]:
        return {
            "nodes": len(self.nodes),
            "containers": sum(1 for n in self.nodes.values() if n.container),
            "edges": len(self.edges),
            "cross_container_edges": sum(
                1
                for e in self.edges
                if e.src
                and e.tgt
                and (self.parent_of(e.src) or "") != (self.parent_of(e.tgt) or "")
            ),
            "preserved_cells": len(self.preserved),
            "direction": self.direction,
            "source": self.source_path,
        }
