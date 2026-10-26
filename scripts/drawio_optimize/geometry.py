"""Geometry primitives for the drawio-optimize layout/routing engine.

All coordinates are in Draw.io user units (pixels).  A rectangle is stored as
``x, y, w, h`` in absolute canvas coordinates.  Segments are pairs of points.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Iterable, Sequence

Point = tuple[float, float]
Segment = tuple[Point, Point]

EPS = 0.5  # half-pixel tolerance: Draw.io works on a 1px grid


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def x1(self) -> float:
        return self.x + self.w

    @property
    def y1(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    @property
    def center(self) -> Point:
        return (self.cx, self.cy)

    def inflate(self, d: float) -> "Rect":
        return Rect(self.x - d, self.y - d, self.w + 2 * d, self.h + 2 * d)

    def translated(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def overlaps(self, other: "Rect", tol: float = 0.0) -> bool:
        return not (
            self.x1 <= other.x + tol
            or other.x1 <= self.x + tol
            or self.y1 <= other.y + tol
            or other.y1 <= self.y + tol
        )

    def overlap_area(self, other: "Rect") -> float:
        dx = min(self.x1, other.x1) - max(self.x, other.x)
        dy = min(self.y1, other.y1) - max(self.y, other.y)
        return max(0.0, dx) * max(0.0, dy)

    def contains_rect(self, other: "Rect", tol: float = 0.0) -> bool:
        return (
            other.x >= self.x - tol
            and other.y >= self.y - tol
            and other.x1 <= self.x1 + tol
            and other.y1 <= self.y1 + tol
        )

    def contains_point(self, p: Point, tol: float = 0.0) -> bool:
        return (
            self.x - tol <= p[0] <= self.x1 + tol
            and self.y - tol <= p[1] <= self.y1 + tol
        )

    def strict_contains_point(self, p: Point, tol: float = 0.0) -> bool:
        return (
            self.x + tol < p[0] < self.x1 - tol
            and self.y + tol < p[1] < self.y1 - tol
        )

    def distance_to_point(self, p: Point) -> float:
        dx = max(self.x - p[0], 0.0, p[0] - self.x1)
        dy = max(self.y - p[1], 0.0, p[1] - self.y1)
        return hypot(dx, dy)

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.h)


def manhattan(a: Point, b: Point) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def dist(a: Point, b: Point) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


def _sign(v: float, tol: float = 0.25) -> int:
    if v > tol:
        return 1
    if v < -tol:
        return -1
    return 0


def direction(a: Point, b: Point) -> tuple[int, int]:
    """Axis direction of a segment as a unit tuple; (0, 0) for degenerate."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if abs(dx) >= abs(dy):
        return (_sign(dx), 0)
    return (0, _sign(dy))


def is_orthogonal_segment(a: Point, b: Point, tol: float = 0.6) -> bool:
    return abs(a[0] - b[0]) <= tol or abs(a[1] - b[1]) <= tol


def segment_length(a: Point, b: Point) -> float:
    return dist(a, b)


def polyline_length(points: Sequence[Point]) -> float:
    return sum(dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def simplify_points(points: Sequence[Point], tol: float = 0.5) -> list[Point]:
    """Remove duplicate and collinear intermediate points."""
    out: list[Point] = []
    for p in points:
        p = (round(float(p[0]), 2), round(float(p[1]), 2))
        if out and dist(out[-1], p) <= tol:
            continue
        out.append(p)
    changed = True
    while changed and len(out) > 2:
        changed = False
        keep = [out[0]]
        for i in range(1, len(out) - 1):
            a, b, c = keep[-1], out[i], out[i + 1]
            if is_orthogonal_segment(a, b, tol) and is_orthogonal_segment(b, c, tol):
                if (abs(a[0] - b[0]) <= tol and abs(b[0] - c[0]) <= tol) or (
                    abs(a[1] - b[1]) <= tol and abs(b[1] - c[1]) <= tol
                ):
                    # b is collinear with a and c -> redundant
                    changed = True
                    continue
            keep.append(b)
        keep.append(out[-1])
        out = keep
    return out


def bend_count(points: Sequence[Point]) -> int:
    return max(0, len(simplify_points(points)) - 2)


def segment_intersection(a: Point, b: Point, c: Point, d: Point) -> Point | None:
    """Intersection point of segments ab and cd, or None.

    Includes touching endpoints.  Uses exact-ish floating point orientation.
    """
    def orient(p: Point, q: Point, r: Point) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1 = orient(a, b, c)
    o2 = orient(a, b, d)
    o3 = orient(c, d, a)
    o4 = orient(c, d, b)
    if ((o1 > 0 and o2 < 0) or (o1 < 0 and o2 > 0)) and (
        (o3 > 0 and o4 < 0) or (o3 < 0 and o4 > 0)
    ):
        t = o1 / (o1 - o2)
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    # collinear / touching cases
    for p in (a, b):
        if point_on_segment(p, c, d):
            return p
    for p in (c, d):
        if point_on_segment(p, a, b):
            return p
    return None


def point_on_segment(p: Point, a: Point, b: Point, tol: float = 0.75) -> bool:
    if abs(a[0] - b[0]) <= tol:  # vertical
        return abs(p[0] - a[0]) <= tol and min(a[1], b[1]) - tol <= p[1] <= max(a[1], b[1]) + tol
    if abs(a[1] - b[1]) <= tol:  # horizontal
        return abs(p[1] - a[1]) <= tol and min(a[0], b[0]) - tol <= p[0] <= max(a[0], b[0]) + tol
    # general: distance + projection
    cross = abs((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]))
    if cross > tol * dist(a, b):
        return False
    dot = (p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])
    return -tol <= dot <= dist(a, b) ** 2 + tol


def proper_crossing(a: Point, b: Point, c: Point, d: Point, tol: float = 1.0) -> bool:
    """True only for an interior/interior crossing (a visual crossing)."""
    p = segment_intersection(a, b, c, d)
    if p is None:
        return False
    ends = (a, b, c, d)
    for e in ends:
        if dist(p, e) <= tol:
            return False
    return True


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    vx, vy = b[0] - a[0], b[1] - a[1]
    wx, wy = p[0] - a[0], p[1] - a[1]
    L2 = vx * vx + vy * vy
    if L2 <= 1e-9:
        return dist(p, a)
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / L2))
    proj = (a[0] + t * vx, a[1] + t * vy)
    return dist(p, proj)


def segment_rect_hit(
    a: Point, b: Point, rect: Rect, tol: float = 0.5, include_boundary: bool = False
) -> bool:
    """Does segment ab pass through the *interior* of rect?

    With ``include_boundary`` the boundary also counts (used by clearance
    checks with an already inflated rectangle).
    """
    if include_boundary:
        r = rect.inflate(-tol)
    else:
        r = rect.inflate(-tol)
    if abs(a[0] - b[0]) <= 0.6:  # vertical
        x = a[0]
        if not (r.x < x < r.x1):
            return False
        lo, hi = sorted((a[1], b[1]))
        return min(hi, r.y1) - max(lo, r.y) > tol
    if abs(a[1] - b[1]) <= 0.6:  # horizontal
        y = a[1]
        if not (r.y < y < r.y1):
            return False
        lo, hi = sorted((a[0], b[0]))
        return min(hi, r.x1) - max(lo, r.x) > tol
    return False


def segment_distance_to_rect(a: Point, b: Point, rect: Rect) -> float:
    if segment_rect_hit(a, b, rect, tol=0.0) or rect.contains_point(a) or rect.contains_point(b):
        return 0.0
    best = min(rect.distance_to_point(a), rect.distance_to_point(b))
    for corner in (
        (rect.x, rect.y),
        (rect.x1, rect.y),
        (rect.x, rect.y1),
        (rect.x1, rect.y1),
    ):
        best = min(best, point_segment_distance(corner, a, b))
    return best


def collinear_overlap(a: Point, b: Point, c: Point, d: Point, tol: float = 0.75) -> float:
    """Length of overlap of two collinear axis-aligned segments (0 if none)."""
    if abs(a[1] - b[1]) <= tol and abs(c[1] - d[1]) <= tol:  # both horizontal
        if abs(a[1] - c[1]) > tol:
            return 0.0
        lo = max(min(a[0], b[0]), min(c[0], d[0]))
        hi = min(max(a[0], b[0]), max(c[0], d[0]))
        return max(0.0, hi - lo)
    if abs(a[0] - b[0]) <= tol and abs(c[0] - d[0]) <= tol:  # both vertical
        if abs(a[0] - c[0]) > tol:
            return 0.0
        lo = max(min(a[1], b[1]), min(c[1], d[1]))
        hi = min(max(a[1], b[1]), max(c[1], d[1]))
        return max(0.0, hi - lo)
    return 0.0


def polyline_crossings(p: Sequence[Point], q: Sequence[Point], tol: float = 1.0) -> int:
    n = 0
    for i in range(len(p) - 1):
        for j in range(len(q) - 1):
            if proper_crossing(p[i], p[i + 1], q[j], q[j + 1], tol=tol):
                n += 1
    return n


def polyline_overlap(p: Sequence[Point], q: Sequence[Point], tol: float = 0.75) -> float:
    total = 0.0
    for i in range(len(p) - 1):
        for j in range(len(q) - 1):
            total += collinear_overlap(p[i], p[i + 1], q[j], q[j + 1], tol=tol)
    return total


def point_at_fraction(points: Sequence[Point], frac: float) -> Point:
    """Point at arc-length fraction (0..1) of a polyline."""
    if not points:
        return (0.0, 0.0)
    if len(points) == 1:
        return points[0]
    total = polyline_length(points)
    if total <= 1e-9:
        return points[0]
    target = max(0.0, min(1.0, frac)) * total
    walked = 0.0
    for i in range(len(points) - 1):
        seg = dist(points[i], points[i + 1])
        if walked + seg >= target:
            if seg <= 1e-9:
                return points[i]
            t = (target - walked) / seg
            return (
                points[i][0] + t * (points[i + 1][0] - points[i][0]),
                points[i][1] + t * (points[i + 1][1] - points[i][1]),
            )
        walked += seg
    return points[-1]


def bbox(points: Iterable[Point]) -> Rect:
    pts = list(points)
    if not pts:
        return Rect(0, 0, 0, 0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def merge_rects(rects: Sequence[Rect]) -> Rect:
    if not rects:
        return Rect(0, 0, 0, 0)
    x0 = min(r.x for r in rects)
    y0 = min(r.y for r in rects)
    x1 = max(r.x1 for r in rects)
    y1 = max(r.y1 for r in rects)
    return Rect(x0, y0, x1 - x0, y1 - y0)


def snap(v: float, grid: float = 10.0) -> float:
    return round(v / grid) * grid
