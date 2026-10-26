"""Layered (Sugiyama-style) placement.

Stages: rank assignment -> dummy nodes for long edges -> barycenter ordering
-> transpose -> coordinate assignment -> container recursion -> RL/BT mirror.
No external layout engine is required; Graphviz remains an optional extra that
this skill deliberately does not depend on.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import merge_rects, snap
from .model import Edge, Graph, Node
from .textmetrics import measurer


@dataclass
class LayoutConfig:
    direction: str = "LR"
    h_gap: float = 84.0
    v_gap: float = 52.0
    margin: float = 40.0
    mode: str = "full"            # full | preserve | hybrid
    align: str = "center"         # center | top/left
    uniform_kinds: bool = True
    grid: float = 10.0
    max_node_w: float = 240.0
    min_node_w: float = 110.0
    min_node_h: float = 48.0
    align_chains: bool = True
    locked_tol: float = 0.5


@dataclass
class RankInfo:
    ranks: dict[str, int] = field(default_factory=dict)
    order: dict[str, int] = field(default_factory=dict)
    layers: list[list[str]] = field(default_factory=list)


class LayeredLayout:
    def __init__(self, graph: Graph, config: LayoutConfig | None = None):
        self.g = graph
        self.cfg = config or LayoutConfig(direction=graph.direction)
        self.rank_info = RankInfo()

    # -- public ------------------------------------------------------------
    def run(self) -> RankInfo:
        if self.cfg.mode == "preserve":
            self.rank_info = self._infer_ranks()
            return self.rank_info
        self._layout_scope(None)
        self._place_scopes()
        if self.cfg.direction in ("RL", "BT"):
            self._mirror()
        self._normalize()
        return self.rank_info

    # -- scope recursion ---------------------------------------------------
    def _layout_scope(self, scope: str | None) -> None:
        children = [n for n in self.g.nodes.values() if n.parent == scope]
        for c in [x for x in children if x.container]:
            self._layout_scope(c.id)
        if not children:
            if scope is not None:
                self._size_container(self.g.nodes[scope])
            return
        self._size_nodes(children)
        self._layout_layer(children, scope)
        # shift container children into the container's local frame
        for c in [x for x in children if x.container]:
            self._shift_children(c)
        if scope is not None:
            self._size_container(self.g.nodes[scope])

    def _size_nodes(self, nodes: list[Node]) -> None:
        for n in nodes:
            if n.container:
                if n.w <= 0 or n.h <= 0:
                    n.w, n.h = 220, 140
                continue
            if n.locked:
                continue
            m = measurer(float(n.meta.get("font_size", 12)), bool(n.meta.get("bold")))
            w, h = m.node_size(n.label or n.id, self.cfg.min_node_w, self.cfg.min_node_h, self.cfg.max_node_w)
            # room for ports on the two vertical sides
            port_need = 0
            incident = [e for e in self.g.edges if e.src == n.id or e.tgt == n.id]
            port_need = max(0, len(incident) - 1) * 6
            n.w = max(n.w if n.meta.get("keep_w") else 0, w)
            n.h = max(n.h if n.meta.get("keep_h") else 0, h + port_need)
            n.w = min(n.w, self.cfg.max_node_w)
            n.w = snap(n.w, 2)
            n.h = snap(n.h, 2)
        if self.cfg.uniform_kinds:
            by_kind: dict[str, list[Node]] = {}
            for n in nodes:
                if n.container or n.locked:
                    continue
                by_kind.setdefault(n.kind, []).append(n)
            for kind, group in by_kind.items():
                if len(group) < 2:
                    continue
                w = max(x.w for x in group)
                h = max(x.h for x in group)
                for x in group:
                    x.w = min(w, self.cfg.max_node_w)
                    x.h = h

    def _size_container(self, c: Node) -> None:
        kids = [n for n in self.g.nodes.values() if n.parent == c.id]
        if not kids:
            c.w = max(c.w, 220)
            c.h = max(c.h, 120)
            return
        r = merge_rects([k.rect for k in kids])
        title = 28.0
        pad = 24.0
        c.meta["content_offset"] = (pad - r.x, title + pad - r.y)
        c.w = max(180.0, r.w + 2 * pad)
        c.h = max(120.0, r.h + title + 2 * pad)

    def _shift_children(self, c: Node) -> None:
        ox, oy = c.meta.get("content_offset", (24.0, 52.0))
        for k in [n for n in self.g.nodes.values() if n.parent == c.id]:
            k.x += ox
            k.y += oy

    # -- layer assignment --------------------------------------------------
    def _layout_layer(self, nodes: list[Node], scope: str | None) -> None:
        ids = {n.id for n in nodes}
        if not nodes:
            return
        # Project cross-container connections onto this scope: an edge from a
        # child of container A to a child of container B becomes A -> B here.
        # Without this, groups full of connected modules would be laid out as
        # isolated islands (usually stacked vertically).
        pairs: set[tuple[str, str]] = set()
        for e in self.g.edges:
            if not e.src or not e.tgt:
                continue
            a = self._scope_child(e.src, scope)
            b = self._scope_child(e.tgt, scope)
            if a in ids and b in ids and a != b:
                pairs.add((a, b))
        edges = [
            Edge(id=f"__ve{i}", src=a, tgt=b)
            for i, (a, b) in enumerate(sorted(pairs))
        ]
        axis = "x" if self.cfg.direction in ("LR", "RL") else "y"
        ranks = self._assign_ranks(nodes, edges)
        for nid, r in ranks.items():
            # rank identity is scoped: two inner ranks in different containers
            # are not the same visual row/column
            self.rank_info.ranks[nid] = f"{scope or ''}|{r}"
        layers, adj = self._build_layers(nodes, edges, ranks)
        layers = self._order_layers(layers, adj)
        # dummy nodes only guided ordering; they do not occupy space
        coord_layers = [
            [nid for nid in layer if not nid.startswith("__dummy")] for layer in layers
        ]
        self.rank_info.layers = coord_layers
        neigh: dict[str, list[str]] = {}
        for e in edges:
            neigh.setdefault(e.src, []).append(e.tgt)
            neigh.setdefault(e.tgt, []).append(e.src)
        positions = self._assign_coordinates(coord_layers, axis, neigh)

        for n in nodes:
            if n.id not in positions:
                positions[n.id] = (0.0, 0.0)
        if self.cfg.mode == "hybrid":
            self._hybrid_nudge(nodes, positions, axis)

        for n in nodes:
            if not n.locked:
                n.x, n.y = positions[n.id]
            if n.locked:
                # locked nodes keep their absolute position; record them as
                # anchors for the next rank packing pass
                pass

        self._resolve_locked(nodes)

    def _assign_ranks(self, nodes: list[Node], edges: list[Edge]) -> dict[str, int]:
        ids = [n.id for n in nodes]
        adj: dict[str, list[str]] = {i: [] for i in ids}
        for e in edges:
            if e.src in adj and e.tgt in adj:
                adj[e.src].append(e.tgt)
        # break cycles: DFS back edges are dropped for ranking
        color: dict[str, int] = {i: 0 for i in ids}
        dag: dict[str, list[str]] = {i: [] for i in ids}
        back: set[tuple[str, str]] = set()

        def dfs(u: str) -> None:
            color[u] = 1
            for v in adj.get(u, []):
                if color.get(v, 0) == 0:
                    dag[u].append(v)
                    dfs(v)
                elif color.get(v) == 1:
                    back.add((u, v))
                else:
                    dag[u].append(v)
            color[u] = 2

        for n in nodes:
            if color.get(n.id, 0) == 0:
                dfs(n.id)
        indeg = {i: 0 for i in ids}
        for u in dag:
            for v in dag[u]:
                indeg[v] += 1
        # longest path from sources
        rank = {i: 0 for i in ids}
        queue = [i for i in ids if indeg[i] == 0]
        # deterministic order
        queue.sort()
        processed = 0
        while queue:
            u = queue.pop(0)
            processed += 1
            for v in sorted(dag.get(u, [])):
                rank[v] = max(rank[v], rank[u] + 1)
                indeg[v] -= 1
                if indeg[v] == 0:
                    queue.append(v)
        if processed < len(ids):
            for i in ids:
                rank.setdefault(i, 0)
        return rank

    def _build_layers(
        self, nodes: list[Node], edges: list[Edge], ranks: dict[str, int]
    ) -> tuple[list[list[str]], dict[str, list[str]]]:
        max_rank = max(ranks.values()) if ranks else 0
        layers: list[list[str]] = [[] for _ in range(max_rank + 1)]
        by_id = {n.id: n for n in nodes}
        for nid in sorted(ranks, key=lambda x: (ranks[x], by_id[x].y, by_id[x].x)):
            layers[ranks[nid]].append(nid)

        adj: dict[str, list[str]] = {n.id: [] for n in nodes}
        dummy_seq = 0
        for e in edges:
            r1, r2 = ranks[e.src], ranks[e.tgt]
            if r1 == r2:
                continue
            step = 1 if r2 > r1 else -1
            prev = e.src
            for r in range(r1 + step, r2, step):
                did = f"__dummy{dummy_seq}"
                dummy_seq += 1
                layers[r].append(did)
                by_id[did] = Node(id=did, w=0, h=0, label="")  # type: ignore[arg-type]
                adj[prev] = adj.get(prev, []) + [did]
                adj[did] = [did]
                prev = did
            adj[prev] = adj.get(prev, []) + [e.tgt]
        # drop self-links created by adjacent ranks (prev == tgt handled above)
        for k in list(adj):
            adj[k] = [v for v in adj[k] if v != k]
        self._by_id = by_id
        self._layers = layers
        return layers, adj

    def _order_layers(
        self, layers: list[list[str]], adj: dict[str, list[str]]
    ) -> list[list[str]]:
        if len(layers) <= 1:
            return layers
        rev: dict[str, list[str]] = {}
        for u, vs in adj.items():
            for v in vs:
                rev.setdefault(v, []).append(u)
        crossing = _total_crossings(layers, adj)

        def sweep(down: bool) -> None:
            rng = range(1, len(layers)) if down else range(len(layers) - 2, -1, -1)
            for r in rng:
                neighbors = rev if down else adj
                ref = layers[r - 1] if down else layers[r + 1]
                pos = {nid: i for i, nid in enumerate(ref)}
                def key(nid: str):
                    vals = [pos[x] for x in neighbors.get(nid, []) if x in pos]
                    if not vals:
                        return (1, 0.0, nid)
                    vals.sort()
                    return (0, vals[len(vals) // 2], nid)
                layers[r].sort(key=key)

        for _ in range(4):
            before = crossing
            sweep(True)
            sweep(False)
            # transpose: adjacent swaps that reduce crossings
            for r in range(len(layers)):
                if len(layers[r]) > 40:
                    continue
                improved = True
                guard = 0
                while improved and guard < 3:
                    improved = False
                    guard += 1
                    for i in range(len(layers[r]) - 1):
                        cur = _total_crossings(layers, adj)
                        layers[r][i], layers[r][i + 1] = layers[r][i + 1], layers[r][i]
                        new = _total_crossings(layers, adj)
                        if new < cur:
                            improved = True
                        else:
                            layers[r][i], layers[r][i + 1] = layers[r][i + 1], layers[r][i]
            crossing = _total_crossings(layers, adj)
            if crossing >= before:
                break
        for r, layer in enumerate(layers):
            for i, nid in enumerate(layer):
                self.rank_info.order[nid] = i
        self.rank_info.layers = layers
        return layers

    def _scope_child(self, nid: str | None, scope: str | None) -> str | None:
        cur = nid
        guard = 0
        while cur in self.g.nodes and self.g.nodes[cur].parent != scope:
            cur = self.g.nodes[cur].parent
            guard += 1
            if guard > 64:
                return None
        return cur

    def _assign_coordinates(
        self, layers: list[list[str]], axis: str, neigh: dict[str, list[str]] | None = None
    ) -> dict[str, tuple[float, float]]:
        by_id = self._by_id
        gap = self.cfg.v_gap
        # rank bands
        sizes = []
        for layer in layers:
            sizes.append(max((self._thickness(by_id[n], axis) for n in layer), default=0.0))
        rank_start: list[float] = []
        cursor = 0.0
        for r, size in enumerate(sizes):
            rank_start.append(cursor)
            cursor += size + (self.cfg.h_gap if r < len(sizes) - 1 else 0.0)
        band = {nid: rank_start[r] for r, layer in enumerate(layers) for nid in layer}
        thickness = {nid: sizes[r] for r, layer in enumerate(layers) for nid in layer}

        # desired cross coordinate = median of neighbours
        if neigh is None:
            neigh = {}
            for e in self.g.edges:
                if e.src in band and e.tgt in band:
                    neigh.setdefault(e.src, []).append(e.tgt)
                    neigh.setdefault(e.tgt, []).append(e.src)

        def cross_of(nid: str) -> float:
            n = by_id[nid]
            return n.y + n.h / 2 if axis == "x" else n.x + n.w / 2

        pos: dict[str, float] = {}
        for layer in layers:
            for i, nid in enumerate(layer):
                span = self._cross_size(by_id[nid], axis)
                pos[nid] = i * (span + gap)

        # Alternate desired-position estimation and rank packing.  Recomputed
        # after every pack so a long-edge branch does not use stale positions.
        for iteration in range(4):
            sequence = layers if iteration % 2 == 0 else list(reversed(layers))
            reverse = iteration % 2 == 1
            for layer in sequence:
                for nid in layer:
                    span = self._cross_size(by_id[nid], axis)
                    vals = [
                        pos[x] + self._cross_size(by_id[x], axis) / 2.0
                        for x in neigh.get(nid, [])
                    ]
                    if vals:
                        pos[nid] = sum(vals) / len(vals) - span / 2.0
                self._pack_axis(layer, pos, axis, reverse=reverse)

        # final forward pass so the last rank incorporates the final positions
        # of its predecessors, then straighten degree-2 chains
        for layer in layers:
            for nid in layer:
                span = self._cross_size(by_id[nid], axis)
                vals = [
                    pos[x] + self._cross_size(by_id[x], axis) / 2.0
                    for x in neigh.get(nid, [])
                ]
                if vals:
                    pos[nid] = sum(vals) / len(vals) - span / 2.0
            self._pack_axis(layer, pos, axis)
        self._align_chains(layers, pos, neigh, axis, by_id)

        out: dict[str, tuple[float, float]] = {}
        for nid in band:
            n = by_id[nid]
            t = self._thickness(n, axis)
            start = band[nid] + (thickness[nid] - t) / 2.0
            if self.cfg.align == "center":
                pass
            if axis == "x":
                out[nid] = (start, pos[nid])
            else:
                out[nid] = (pos[nid], start)
        return out

    def _pack_axis(self, layer: list[str], pos: dict[str, float], axis: str, reverse: bool = False) -> None:
        seq = list(reversed(layer)) if reverse else layer
        prev_end = None
        for nid in seq:
            n = self._by_id[nid]
            size = self._cross_size(n, axis)
            p = pos[nid]
            if prev_end is not None:
                if reverse:
                    p = min(p, prev_end - size - self.cfg.v_gap)
                else:
                    p = max(p, prev_end + self.cfg.v_gap)
            pos[nid] = p
            prev_end = p + size if not reverse else p

    def _thickness(self, n: Node, axis: str) -> float:
        return n.w if axis == "x" else n.h

    def _cross_size(self, n: Node, axis: str) -> float:
        return n.h if axis == "x" else n.w

    def _align_chains(self, layers, pos, neigh, axis, by_id) -> None:
        """A node with exactly one predecessor and one successor should sit on
        the line between them; this is what makes chain edges perfectly straight."""
        indeg: dict[str, list[str]] = {}
        outdeg: dict[str, list[str]] = {}
        for e in self.g.edges:
            if not e.src or not e.tgt:
                continue
            outdeg.setdefault(e.src, []).append(e.tgt)
            indeg.setdefault(e.tgt, []).append(e.src)
        for layer in layers:
            for nid in layer:
                if len(indeg.get(nid, [])) != 1 or len(outdeg.get(nid, [])) != 1:
                    continue
                p = indeg[nid][0]
                if p not in pos:
                    continue
                span = self._cross_size(by_id[nid], axis)
                pspan = self._cross_size(by_id[p], axis)
                pos[nid] = pos[p] + pspan / 2.0 - span / 2.0
            self._pack_axis(layer, pos, axis)

    def _resolve_locked(self, nodes: list[Node]) -> None:
        """Keep locked nodes exactly where the user put them, push siblings."""
        locked = [n for n in nodes if n.locked]
        if not locked:
            return
        for lk in locked:
            for n in nodes:
                if n is lk or n.locked:
                    continue
                if n.rect.overlaps(lk.rect.inflate(8)):
                    if abs(n.y - lk.y) < abs(n.x - lk.x):
                        n.y = lk.y1 + self.cfg.v_gap
                    else:
                        n.x = lk.x1 + self.cfg.h_gap

    def _hybrid_nudge(
        self, nodes: list[Node], positions: dict[str, tuple[float, float]], axis: str
    ) -> None:
        # hybrid keeps the original position unless it causes an overlap
        for n in nodes:
            if n.locked:
                continue
            if axis == "x":
                positions[n.id] = (positions[n.id][0], n.y)
            else:
                positions[n.id] = (n.x, positions[n.id][1])

    def _place_scopes(self) -> None:
        """Translate scope-local coordinates into absolute canvas coordinates."""
        def walk(scope: str | None, ox: float, oy: float) -> None:
            for n in [x for x in self.g.nodes.values() if x.parent == scope]:
                if scope is not None:
                    n.x += ox
                    n.y += oy
                if n.container:
                    walk(n.id, n.x, n.y)
        walk(None, 0.0, 0.0)

    def _infer_ranks(self) -> RankInfo:
        nodes = list(self.g.nodes.values())
        if not nodes:
            return RankInfo()
        axis = "x" if self.cfg.direction in ("LR", "RL") else "y"
        vals = sorted({round(self._center(n, axis) / 60.0) * 60 for n in nodes})
        for n in nodes:
            local = vals.index(round(self._center(n, axis) / 60.0) * 60)
            self.rank_info.ranks[n.id] = f"{n.parent or ''}|{local}"
        layers: list[list[str]] = []
        for r in sorted(set(self.rank_info.ranks.values())):
            layer = [
                n.id
                for n in sorted(nodes, key=lambda x: self._center(x, "y" if axis == "x" else "x"))
                if self.rank_info.ranks[n.id] == r
            ]
            layers.append(layer)
        self.rank_info.layers = layers
        return self.rank_info

    @staticmethod
    def _center(n: Node, axis: str) -> float:
        return n.x + n.w / 2 if axis == "x" else n.y + n.h / 2

    def _normalize(self) -> None:
        nodes = list(self.g.nodes.values())
        if not nodes:
            return
        r = merge_rects([n.rect for n in nodes])
        dx = self.cfg.margin - r.x
        dy = self.cfg.margin - r.y
        for n in nodes:
            n.x += dx
            n.y += dy
        for n in nodes:
            # 1px grid keeps anchors exact; 10px would break centre alignment
            n.x = round(n.x, 1)
            n.y = round(n.y, 1)

    def _mirror(self) -> None:
        nodes = list(self.g.nodes.values())
        if not nodes:
            return
        r = merge_rects([n.rect for n in nodes])
        for n in nodes:
            if self.cfg.direction == "RL":
                n.x = r.x + r.x1 - n.x - n.w
            else:
                n.y = r.y + r.y1 - n.y - n.h


# ---------------------------------------------------------------------------
# crossing counting
# ---------------------------------------------------------------------------

def _total_crossings(layers: list[list[str]], adj: dict[str, list[str]]) -> int:
    total = 0
    for r in range(len(layers) - 1):
        pos = {nid: i for i, nid in enumerate(layers[r])}
        pos2 = {nid: i for i, nid in enumerate(layers[r + 1])}
        pairs = []
        for u in layers[r]:
            for v in adj.get(u, []):
                if v in pos2:
                    pairs.append((pos[u], pos2[v]))
        for i in range(len(pairs)):
            for j in range(i + 1, len(pairs)):
                (a, b), (c, d) = pairs[i], pairs[j]
                if (a - c) * (b - d) < 0:
                    total += 1
    return total


def layout_graph(graph: Graph, config: LayoutConfig | None = None) -> RankInfo:
    return LayeredLayout(graph, config).run()
