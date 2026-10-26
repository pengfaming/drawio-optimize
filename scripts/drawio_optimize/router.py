"""Orthogonal connector routing on a sparse Hanan grid.

Design notes
------------
*Every* path ends with a short stub perpendicular to the node side.  A polyline
that merely touches the correct anchor but approaches it sideways is rejected
even if it is shorter: Draw.io clips such a line along the node border, producing
the classic "arrow slides along the box then bends" artefact.
* Fast candidates (straight, one bend) are tried before the grid search.
* The grid search is A* over obstacle clearance lines with turn, crossing and
  overlap penalties.  It is deliberately bounded; large diagrams fall back to
  the candidate family rather than exploding.
* Containers are not routing obstacles.  Crossing a group boundary is legal and
  common in architecture diagrams; the DRC reports it as information only.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from .geometry import (
    Rect,
    bend_count,
    dist,
    is_orthogonal_segment,
    manhattan,
    polyline_length,
    segment_rect_hit,
    simplify_points,
)
from .model import Edge, Graph, Node, Port


@dataclass
class RouterConfig:
    stub: float = 18.0
    clearance: float = 14.0
    min_clearance: float = 3.0
    turn_cost: float = 46.0
    crossing_cost: float = 190.0
    overlap_cost: float = 420.0
    max_lanes: int = 70
    effort: str = "balanced"       # fast | balanced | thorough
    orthogonal: bool = True
    ripup_passes: int = 1
    window: float = 140.0


@dataclass
class RouteResult:
    edge_id: str
    path: list[tuple[float, float]] = field(default_factory=list)
    method: str = ""
    cost: float = 0.0
    clearance_used: float = 0.0
    crossings: int = 0
    bends: int = 0
    length: float = 0.0
    ok: bool = True
    reason: str = ""


# ---------------------------------------------------------------------------
# board: previously routed segments
# ---------------------------------------------------------------------------

class RouteBoard:
    def __init__(self) -> None:
        self.segments: list[tuple[str, tuple[float, float], tuple[float, float], str]] = []
        self.paths: dict[str, list[tuple[float, float]]] = {}
        self.signal: dict[str, str] = {}

    def register(self, edge: Edge) -> None:
        self.unregister(edge.id)
        self.paths[edge.id] = list(edge.path)
        self.signal[edge.id] = edge.signal
        for i in range(len(edge.path) - 1):
            self.segments.append((edge.id, edge.path[i], edge.path[i + 1], edge.signal))

    def unregister(self, edge_id: str) -> None:
        self.segments = [s for s in self.segments if s[0] != edge_id]
        self.paths.pop(edge_id, None)
        self.signal.pop(edge_id, None)

    def crossings(self, a, b) -> int:
        n = 0
        for eid, c, d, _ in self.segments:
            if not _seg_bbox_near(a, b, c, d):
                continue
            if proper_crossing(a, b, c, d):
                n += 1
        return n

    def overlaps(self, a, b) -> float:
        total = 0.0
        for _, c, d, _ in self.segments:
            if not _seg_bbox_near(a, b, c, d):
                continue
            total += collinear_overlap(a, b, c, d)
        return total

    def segment_list(self):
        return [(c, d) for _, c, d, _ in self.segments]


# local imports to avoid a cycle at module import time
from .geometry import collinear_overlap, proper_crossing  # noqa: E402


# ---------------------------------------------------------------------------
# router
# ---------------------------------------------------------------------------

class Router:
    def __init__(self, graph: Graph, config: RouterConfig | None = None):
        self.g = graph
        self.cfg = config or RouterConfig()
        self.board = RouteBoard()
        self.results: dict[str, RouteResult] = {}

    # -- public ------------------------------------------------------------
    def route_all(self, edges: list[Edge] | None = None) -> dict[str, RouteResult]:
        edges = list(self.g.edges if edges is None else edges)
        self.board = RouteBoard()
        routable = [
            e for e in edges if e.src in self.g.nodes and e.tgt in self.g.nodes
        ]
        # short connections first: they have fewer legal paths
        routable.sort(
            key=lambda e: (
                0 if e.src == e.tgt else 1,
                self._approx_span(e),
                e.id,
            )
        )
        for e in routable:
            res = self.route_edge(e)
            e.path = res.path
            self.results[e.id] = res
            self.board.register(e)
        if self.cfg.ripup_passes > 0 and len(routable) <= 120:
            self._ripup(routable)
        return self.results

    def route_edge(self, e: Edge, clearance: float | None = None) -> RouteResult:
        if e.src == e.tgt and e.src in self.g.nodes:
            return self._route_self_loop(e)
        if not e.src_port or not e.tgt_port:
            return RouteResult(e.id, [], "none", ok=False, reason="missing port")
        a = self.g.nodes[e.src]
        b = self.g.nodes[e.tgt]
        p0 = e.src_port.point(a.rect)
        q0 = e.tgt_port.point(b.rect)
        ns = e.src_port.normal()
        nt = e.tgt_port.normal()

        clr = self.cfg.clearance if clearance is None else clearance
        obstacles = self._obstacles(e, clr)
        # shorten stubs when a neighbouring shape is in the way
        ss = self._free_stub(p0, ns, obstacles, a, b)
        ts = self._free_stub(q0, nt, obstacles, a, b)
        p1 = (p0[0] + ns[0] * ss, p0[1] + ns[1] * ss)
        q1 = (q0[0] + nt[0] * ts, q0[1] + nt[1] * ts)

        candidates: list[tuple[list[tuple[float, float]], str]] = []
        direct = self._direct(p0, q0, ns, nt, obstacles, a, b)
        if direct:
            candidates.append((direct, "direct"))
        for cand in self._elbows(p0, q0, ns, nt, obstacles, a, b):
            candidates.append((cand, "L"))

        # A* is only worth its cost when the fast candidates are not already
        # clean (a straight, uncrossed line is optimal by definition).
        need_astar = not candidates
        if candidates and self.cfg.effort != "fast":
            clean = False
            for path, _ in candidates:
                if bend_count(path) <= 1 and _path_crossings(path, self.board) == 0:
                    clean = True
                    break
            need_astar = not clean or self.cfg.effort == "thorough"
        if need_astar:
            astar = self._astar(p1, q1, p0, q0, ns, nt, obstacles, a, b)
            if astar:
                candidates.append((astar, "astar"))

        if not candidates:
            # last resort: honour the anchors, accept a detour through obstacles
            astar = self._astar(p1, q1, p0, q0, ns, nt, obstacles, a, b, relaxed=True)
            if astar:
                candidates.append((astar, "relaxed"))
        if not candidates:
            path = [p0, p1, (p1[0], q1[1]), q1, q0]
            return RouteResult(
                e.id,
                _finish(path),
                "fallback",
                ok=False,
                reason="no obstacle-free path",
                clearance_used=clr,
            )

        best_path, best_method, best_cost = None, "", math.inf
        for path, method in candidates:
            if _has_reversal(simplify_points(path)):
                continue
            path = _finish(path)
            # a shortcut may never break the terminal stubs; keep the valid
            # pre-shortcut path when the cleaned variant fails
            before_clean = list(path)
            path = self._clean_micro_jogs(path, obstacles, a, b)
            if (not self._valid_ends(path, p0, q0, ns, nt)
                    or self._hits(path, obstacles, a, b)
                    or _has_reversal(path)):
                path = before_clean
            if not self._valid_ends(path, p0, q0, ns, nt):
                continue
            if self._hits(path, obstacles, a, b):
                continue
            cost = self._cost(path)
            if cost < best_cost:
                best_path, best_method, best_cost = path, method, cost
        if best_path is None:
            path = [p0, p1, (p1[0], q1[1]), q1, q0]
            return RouteResult(
                e.id,
                _finish(path),
                "fallback",
                ok=False,
                reason="all candidates invalid",
                clearance_used=clr,
            )

        return RouteResult(
            e.id,
            best_path,
            best_method,
            cost=round(best_cost, 2),
            clearance_used=clr,
            crossings=_path_crossings(best_path, self.board),
            bends=bend_count(best_path),
            length=round(polyline_length(best_path), 2),
        )

    # -- candidate construction -------------------------------------------
    def _direct(self, p0, q0, ns, nt, obstacles, a, b):
        if dist(p0, q0) < 1.0:
            return None
        if not (ns[0] == -nt[0] and ns[1] == -nt[1]):
            return None
        if not is_orthogonal_segment(p0, q0):
            return None
        d = _dir(p0, q0)
        if d != ns:
            return None
        path = [p0, q0]
        if self._hits(path, obstacles, a, b):
            return None
        return path

    def _elbows(self, p0, q0, ns, nt, obstacles, a, b):
        out = []
        for corner in ((q0[0], p0[1]), (p0[0], q0[1])):
            if dist(p0, corner) < 1.0 or dist(corner, q0) < 1.0:
                continue
            if _dir(p0, corner) != ns:
                continue
            if _dir(corner, q0) != (-nt[0], -nt[1]):
                continue
            path = [p0, corner, q0]
            if not self._hits(path, obstacles, a, b):
                out.append(path)
        return out

    def _astar(self, p1, q1, p0, q0, ns, nt, obstacles, a, b, relaxed=False):
        if relaxed:
            obstacles = []
        xs, ys = self._lanes(p1, q1, obstacles)
        if len(xs) > self.cfg.max_lanes or len(ys) > self.cfg.max_lanes:
            xs, ys = self._prune_lanes(xs, p1[0], q1[0]), self._prune_lanes(ys, p1[1], q1[1])
        start = (_nearest_index(xs, p1[0]), _nearest_index(ys, p1[1]))
        goal = (_nearest_index(xs, q1[0]), _nearest_index(ys, q1[1]))
        dirs = [(1, 0), (0, 1), (-1, 0), (0, -1)]
        # 0:E 1:S 2:W 3:N
        start_dir = _dir_index(ns)
        goal_stub_dir = _dir_index((-nt[0], -nt[1]))

        def blocked(x0, y0, x1, y1):
            seg = ((x0, y0), (x1, y1))
            for _, r in obstacles:
                if segment_rect_hit(seg[0], seg[1], r, tol=0.4):
                    return True
            for node in (a, b):
                if segment_rect_hit(seg[0], seg[1], node.rect, tol=0.4):
                    return True
            return False

        # cache legality of edges
        edge_ok: dict[tuple[int, int, int], bool] = {}

        heap: list[tuple[float, int, int, int, int]] = []
        gscore: dict[tuple[int, int, int], float] = {}
        came: dict[tuple[int, int, int], tuple[int, int, int] | None] = {}
        counter = 0
        for d in range(4):
            first = (dirs[d][0], dirs[d][1])
            if first == (-ns[0], -ns[1]):
                continue
            # first move must be along a lane from p1
            ni, nj = start[0] + dirs[d][0], start[1] + dirs[d][1]
            if not (0 <= ni < len(xs) and 0 <= nj < len(ys)):
                continue
            # only allow a real grid neighbour
            if abs(xs[ni] - xs[start[0]]) + abs(ys[nj] - ys[start[1]]) < 0.5:
                continue
            if blocked(p1[0], p1[1], xs[ni], ys[nj]):
                continue
            step = abs(xs[ni] - p1[0]) + abs(ys[nj] - p1[1])
            penalty = self._seg_penalty((p1[0], p1[1]), (xs[ni], ys[nj]))
            turn = 0.0 if d == start_dir else self.cfg.turn_cost
            st = (ni, nj, d)
            g = step + penalty + turn
            gscore[st] = g
            came[st] = None
            h = manhattan((xs[ni], ys[nj]), q1)
            heapq.heappush(heap, (g + h, counter, ni, nj, d))
            counter += 1

        best = None
        while heap:
            f, _, i, j, d = heapq.heappop(heap)
            if gscore.get((i, j, d), math.inf) < f - manhattan((xs[i], ys[j]), q1) - 1e-6:
                continue
            if (i, j) == goal:
                if d == _dir_index(nt):
                    continue   # would overshoot the anchor and reverse into the node
                tail = 0.0 if d == goal_stub_dir else self.cfg.turn_cost
                total = gscore[(i, j, d)] + tail
                if best is None or total < best[0]:
                    best = (total, (i, j, d))
                continue
            for nd, (dx, dy) in enumerate(dirs):
                ni, nj = i + dx, j + dy
                if not (0 <= ni < len(xs) and 0 <= nj < len(ys)):
                    continue
                x0, y0, x1, y1 = xs[i], ys[j], xs[ni], ys[nj]
                if abs(x1 - x0) + abs(y1 - y0) < 0.5:
                    continue
                k2 = (i, j, nd)
                if k2 in edge_ok and not edge_ok[k2]:
                    continue
                # the segment must be straight to a neighbour lane
                if abs(x1 - x0) > 0.5 and abs(y1 - y0) > 0.5:
                    continue
                ok = not blocked(x0, y0, x1, y1)
                edge_ok[k2] = ok
                if not ok:
                    continue
                step = abs(x1 - x0) + abs(y1 - y0)
                turn = 0.0 if nd == d else self.cfg.turn_cost
                penalty = self._seg_penalty((x0, y0), (x1, y1))
                st = (ni, nj, nd)
                g2 = gscore[(i, j, d)] + step + turn + penalty
                if g2 < gscore.get(st, math.inf) - 1e-6:
                    gscore[st] = g2
                    came[st] = (i, j, d)
                    heapq.heappush(
                        heap, (g2 + manhattan((x1, y1), q1), counter, ni, nj, nd)
                    )
                    counter += 1
        if best is None:
            return None
        state = best[1]
        pts = []
        while state is not None:
            i, j, _ = state
            pts.append((xs[i], ys[j]))
            state = came.get(state)
        pts.append(p1)
        pts.reverse()
        return [p0] + _dedupe(pts) + [q0]

    def _lanes(self, p1, q1, obstacles) -> tuple[list[float], list[float]]:
        win = self.cfg.window
        margin = max(self.cfg.stub * 2, 60.0)
        lo_x, hi_x = min(p1[0], q1[0]) - margin, max(p1[0], q1[0]) + margin
        lo_y, hi_y = min(p1[1], q1[1]) - margin, max(p1[1], q1[1]) + margin
        xs = {p1[0], q1[0], (p1[0] + q1[0]) / 2.0}
        ys = {p1[1], q1[1], (p1[1] + q1[1]) / 2.0}
        for nid, r in obstacles:
            if r.x1 < lo_x - win or r.x > hi_x + win:
                continue
            if r.y1 < lo_y - win or r.y > hi_y + win:
                continue
            xs.update((r.x, r.x1))
            ys.update((r.y, r.y1))
        for n in self.g.nodes.values():
            if n.container:
                continue
            if lo_x - win <= n.rect.x1 and n.x <= hi_x + win:
                xs.add(n.x + n.w / 2.0)
            if lo_y - win <= n.rect.y1 and n.y <= hi_y + win:
                ys.add(n.y + n.h / 2.0)
        xs.add(lo_x - 60)
        xs.add(hi_x + 60)
        ys.add(lo_y - 60)
        ys.add(hi_y + 60)
        xs = sorted(round(x, 2) for x in xs if lo_x - 80 <= x <= hi_x + 80)
        ys = sorted(round(y, 2) for y in ys if lo_y - 80 <= y <= hi_y + 80)
        xs = _dedupe_1d(xs)
        ys = _dedupe_1d(ys)
        return xs, ys

    def _prune_lanes(self, lanes: list[float], a: float, b: float) -> list[float]:
        if len(lanes) <= self.cfg.max_lanes:
            return lanes
        keep = {round(a, 2), round(b, 2), round((a + b) / 2.0, 2)}
        # keep lanes closest to the direct corridor, preserving extremes
        ordered = sorted(lanes, key=lambda v: (abs(v - (a + b) / 2.0), v))
        for v in ordered:
            if len(keep) >= self.cfg.max_lanes:
                break
            keep.add(round(v, 2))
        return sorted(keep)

    def _seg_penalty(self, a, b) -> float:
        p = 0.0
        length = manhattan(a, b)
        if length < 6.0:
            p += 30.0          # discourage grid stair treads
        n = self.board.crossings(a, b)
        if n:
            p += self.cfg.crossing_cost * n
        ov = self.board.overlaps(a, b)
        if ov > 3:
            p += self.cfg.overlap_cost * (ov / 100.0)
        return p

    def _clean_micro_jogs(self, path, obstacles, a: Node, b: Node):
        """Replace avoidable dog-legs with a single-bend L.

        Grid routers can produce a 4px left/down/right 'step' that is visually a
        defect even when it is geometrically valid.  Every shortcut is validated
        against obstacles and accepted only when it reduces bends without
        introducing a new crossing.
        """
        pts = simplify_points(path)
        changed = True
        guard = 0
        while changed and guard < 12:
            changed = False
            guard += 1
            base_cross = _path_crossings(pts, self.board)
            found = None
            for i in range(1, len(pts) - 2):
                for j in range(len(pts) - 1, i + 2, -1):
                    for corner in ((pts[j][0], pts[i][1]), (pts[i][0], pts[j][1])):
                        if dist(pts[i], corner) < 1.0 or dist(corner, pts[j]) < 1.0:
                            continue
                        cand = simplify_points(pts[: i + 1] + [corner] + pts[j:])
                        if len(cand) >= len(pts) - 1:
                            continue
                        if self._hits(cand, obstacles, a, b):
                            continue
                        if not all(
                            is_orthogonal_segment(cand[k], cand[k + 1])
                            for k in range(len(cand) - 1)
                        ):
                            continue
                        if _path_crossings(cand, self.board) > base_cross:
                            continue
                        if _has_reversal(cand):
                            continue
                        found = cand
                        break
                    if found:
                        break
                if found:
                    break
            if found:
                pts = found
                changed = True
        return pts

    # -- validation --------------------------------------------------------
    def _obstacles(self, e: Edge, clearance: float):
        return self.g.obstacles_for(e, clearance)

    def _hits(self, path, obstacles, a: Node, b: Node) -> bool:
        for i in range(len(path) - 1):
            for _, r in obstacles:
                if segment_rect_hit(path[i], path[i + 1], r, tol=0.4):
                    return True
            for node in (a, b):
                if segment_rect_hit(path[i], path[i + 1], node.rect, tol=0.4):
                    # the terminal stubs legitimately touch the boundary
                    if not (
                        i == 0
                        and _near(path[i], node.rect)
                        or i == len(path) - 2
                        and _near(path[i + 1], node.rect)
                    ):
                        return True
        return False

    def _valid_ends(self, path, p0, q0, ns, nt) -> bool:
        if len(path) < 2:
            return False
        if dist(path[0], p0) > 0.6 or dist(path[-1], q0) > 0.6:
            return False
        d0 = _dir(path[0], path[1])
        d1 = _dir(path[-2], path[-1])
        if d0 != ns or d1 != (-nt[0], -nt[1]):
            return False
        if _has_reversal(path):
            return False
        return all(is_orthogonal_segment(path[i], path[i + 1]) for i in range(len(path) - 1))

    def _free_stub(self, p, normal, obstacles, a, b) -> float:
        """Shrink the stub only if a neighbouring shape actually blocks it."""
        limit = self.cfg.stub
        for _, rect in obstacles:
            t = _ray_rect_entry(p, normal, rect)
            if t is not None and 0.0 < t < limit:
                limit = max(self.cfg.min_clearance, t - 2.0)
        return max(14.0, min(self.cfg.stub, limit))

    def _cost(self, path) -> float:
        c = polyline_length(path)
        c += self.cfg.turn_cost * bend_count(path)
        c += self.cfg.crossing_cost * _path_crossings(path, self.board)
        c += self.cfg.overlap_cost * (_path_overlap(path, self.board) / 100.0)
        return c

    def _approx_span(self, e: Edge) -> float:
        if not e.src or not e.tgt or e.src not in self.g.nodes or e.tgt not in self.g.nodes:
            return 0.0
        return manhattan(self.g.nodes[e.src].rect.center, self.g.nodes[e.tgt].rect.center)

    # -- rip-up and reroute ------------------------------------------------
    def _ripup(self, edges: list[Edge]) -> None:
        for _ in range(self.cfg.ripup_passes):
            order = sorted(
                edges,
                key=lambda e: (
                    -_path_crossings(self.results[e.id].path, self.board)
                    if e.id in self.results
                    else 0,
                    e.id,
                ),
            )
            changed = False
            for e in order:
                old = self.results.get(e.id)
                if not old or not old.path:
                    continue
                if _path_crossings(old.path, self.board) == 0:
                    continue
                self.board.unregister(e.id)
                new = self.route_edge(e)
                old_key = (
                    _path_crossings(old.path, self.board) * 1000
                    + bend_count(old.path) * 30
                    + polyline_length(old.path) * 0.05
                )
                new_key = (
                    _path_crossings(new.path, self.board) * 1000
                    + bend_count(new.path) * 30
                    + polyline_length(new.path) * 0.05
                )
                if new.ok and new_key < old_key - 1e-6:
                    self.results[e.id] = new
                    e.path = new.path
                    self.board.register(e)
                    changed = True
                else:
                    self.results[e.id] = old
                    e.path = old.path
                    self.board.register(e)
            if not changed:
                break

    # -- self loop ---------------------------------------------------------
    def _route_self_loop(self, e: Edge) -> RouteResult:
        n = self.g.nodes[e.src]
        r = n.rect
        e.src_port = Port(side="R", frac=0.5, role="out", signal=e.signal)
        e.tgt_port = Port(side="T", frac=0.7, role="in", signal=e.signal)
        p0 = e.src_port.point(r)
        q0 = e.tgt_port.point(r)
        p1 = (p0[0] + 34, p0[1])
        q1 = (q0[0], q0[1] - 34)
        path = _finish([p0, p1, (p1[0], q1[1]), q1, q0])
        return RouteResult(
            e.id, path, "self-loop", bends=bend_count(path), length=polyline_length(path)
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _dir(a, b) -> tuple[float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    if abs(dx) >= abs(dy):
        return (1.0 if dx > 0 else -1.0, 0.0)
    return (0.0, 1.0 if dy > 0 else -1.0)


def _dir_index(d: tuple[float, float]) -> int:
    if d[0] > 0:
        return 0
    if d[1] > 0:
        return 1
    if d[0] < 0:
        return 2
    return 3


def _near(p, rect: Rect, tol: float = 1.5) -> bool:
    return rect.distance_to_point(p) <= tol


def _dedupe(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in points:
        if not out or dist(out[-1], p) > 0.5:
            out.append(p)
    return out


def _nearest_index(lanes: list[float], v: float) -> int:
    if not lanes:
        return 0
    return min(range(len(lanes)), key=lambda i: abs(lanes[i] - v))


def _dedupe_1d(vals: list[float]) -> list[float]:
    out: list[float] = []
    for v in vals:
        if not out or abs(out[-1] - v) > 0.75:
            out.append(v)
    return out


def _finish(points) -> list[tuple[float, float]]:
    """Deduplicate and remove collinear points.  Never shortcut blindly:
    a cosmetic shortcut could cut through an obstacle the router avoided."""
    return simplify_points(_dedupe(list(points)))


def _ray_rect_entry(p, normal, rect: Rect) -> float | None:
    """Distance along an axis-aligned ray to the entry point of rect."""
    x, y = p
    if normal[0] > 0:
        if rect.y - 0.5 < y < rect.y1 + 0.5 and rect.x >= x:
            return rect.x - x
    elif normal[0] < 0:
        if rect.y - 0.5 < y < rect.y1 + 0.5 and rect.x1 <= x:
            return x - rect.x1
    elif normal[1] > 0:
        if rect.x - 0.5 < x < rect.x1 + 0.5 and rect.y >= y:
            return rect.y - y
    else:
        if rect.x - 0.5 < x < rect.x1 + 0.5 and rect.y1 <= y:
            return y - rect.y1
    return None


def _path_crossings(path, board: RouteBoard) -> int:
    n = 0
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        for c, d in board.segment_list():
            if not _seg_bbox_near(a, b, c, d):
                continue
            if proper_crossing(a, b, c, d):
                n += 1
    return n


def _seg_bbox_near(a, b, c, d, tol: float = 2.0) -> bool:
    return not (
        max(a[0], b[0]) < min(c[0], d[0]) - tol
        or max(c[0], d[0]) < min(a[0], b[0]) - tol
        or max(a[1], b[1]) < min(c[1], d[1]) - tol
        or max(c[1], d[1]) < min(a[1], b[1]) - tol
    )


def _has_reversal(path) -> bool:
    for i in range(len(path) - 2):
        d1 = _qdir(path[i], path[i + 1])
        d2 = _qdir(path[i + 1], path[i + 2])
        if d1[0] * d2[0] + d1[1] * d2[1] < 0:
            return True
    return False


def _qdir(a, b, tol: float = 1.0) -> tuple[int, int]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    return (0 if abs(dx) <= tol else (1 if dx > 0 else -1),
            0 if abs(dy) <= tol else (1 if dy > 0 else -1))


def _path_overlap(path, board: RouteBoard) -> float:
    total = 0.0
    for i in range(len(path) - 1):
        for c, d in board.segment_list():
            total += collinear_overlap(path[i], path[i + 1], c, d)
    return total


def route_graph(graph: Graph, config: RouterConfig | None = None) -> dict[str, RouteResult]:
    router = Router(graph, config)
    results = router.route_all()
    for e in graph.edges:
        r = results.get(e.id)
        if r:
            e.path = r.path
            e.route_method = r.method
            e.route_cost = r.cost
    return results
