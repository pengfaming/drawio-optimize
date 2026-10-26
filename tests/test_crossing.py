"""Acceptance test 4: routing and ordering must remove avoidable crossings."""
from __future__ import annotations

import unittest

from support import graph, routed
from drawio_optimize.geometry import polyline_crossings
from drawio_optimize.router import RouterConfig, Router


class CrossingTest(unittest.TestCase):
    def test_diagonal_pair_is_untangled(self):
        g = graph(
            [("a", "A", {"x": 40, "y": 40, "w": 110, "h": 60, "meta": {"auto_layout": False}}),
             ("b", "B", {"x": 40, "y": 300, "w": 110, "h": 60, "meta": {"auto_layout": False}}),
             ("c", "C", {"x": 400, "y": 40, "w": 110, "h": 60, "meta": {"auto_layout": False}}),
             ("d", "D", {"x": 400, "y": 300, "w": 110, "h": 60, "meta": {"auto_layout": False}})],
            [("e1", "a", "d"), ("e2", "b", "c")],
        )
        routed(g)
        e1, e2 = g.edges
        self.assertEqual(polyline_crossings(e1.path, e2.path), 0, (e1.path, e2.path))
        self.assertEqual(e1.src, "a")
        self.assertEqual(e1.tgt, "d")
        self.assertEqual(e2.src, "b")
        self.assertEqual(e2.tgt, "c")

    def test_topology_is_never_rewired(self):
        g = graph(
            [("a", "A", {"meta": {"auto_layout": False}}), ("b", "B", {"meta": {"auto_layout": False}}),
             ("c", "C", {"meta": {"auto_layout": False}}), ("d", "D", {"meta": {"auto_layout": False}})],
            [("e1", "a", "b"), ("e2", "c", "d")],
        )
        for n, (x, y) in zip(g.nodes.values(), [(40, 40), (40, 300), (400, 40), (400, 300)]):
            n.x, n.y, n.w, n.h = x, y, 100, 60
        routed(g)
        self.assertEqual([(e.id, e.src, e.tgt) for e in g.edges], [("e1", "a", "b"), ("e2", "c", "d")])


if __name__ == "__main__":
    unittest.main()
