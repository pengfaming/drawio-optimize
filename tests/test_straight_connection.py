"""Acceptance test 1: aligned ports must give a single straight segment."""
from __future__ import annotations

import unittest

from support import assign_ports, graph, routed
from drawio_optimize.ports import PortConfig, _snap_straight
from drawio_optimize.router import Router, RouterConfig


class StraightConnectionTest(unittest.TestCase):
    def test_horizontal_pair_is_one_segment(self):
        g = graph(
            [("a", "Source", {"x": 100, "y": 100, "w": 120, "h": 60, "meta": {"auto_layout": False}}),
             ("b", "Sink", {"x": 400, "y": 100, "w": 120, "h": 60, "meta": {"auto_layout": False}})],
            [("e", "a", "b")],
            direction="LR",
        )
        routed(g)
        e = g.edges[0]
        self.assertEqual(len(e.path), 2, e.path)
        self.assertEqual(e.bends, 0)
        self.assertAlmostEqual(e.path[0][1], e.path[1][1], places=2)
        self.assertEqual(e.src_port.side, "R")
        self.assertEqual(e.tgt_port.side, "L")

    def test_vertical_pair_is_one_segment(self):
        g = graph(
            [("a", "Top", {"x": 100, "y": 100, "w": 120, "h": 60, "meta": {"auto_layout": False}}),
             ("b", "Bottom", {"x": 100, "y": 300, "w": 120, "h": 60, "meta": {"auto_layout": False}})],
            [("e", "a", "b")],
            direction="TB",
        )
        routed(g)
        e = g.edges[0]
        self.assertEqual(len(e.path), 2, e.path)
        self.assertEqual(e.bends, 0)
        self.assertAlmostEqual(e.path[0][0], e.path[1][0], places=2)
        self.assertEqual(e.src_port.side, "B")
        self.assertEqual(e.tgt_port.side, "T")
        # the final segment must approach the target along its inward normal
        dx = e.path[-1][0] - e.path[-2][0]
        dy = e.path[-1][1] - e.path[-2][1]
        self.assertEqual((dx, dy), (0.0, 140.0))

    def test_centres_aligned_but_anchors_offset_is_repaired(self):
        # simulates a file whose two anchors are at 15% / 85% although the
        # node centres match: the snapper must co-locate them
        g = graph(
            [("a", "A", {"x": 100, "y": 100, "w": 120, "h": 80, "meta": {"auto_layout": False}}),
             ("b", "B", {"x": 400, "y": 100, "w": 120, "h": 80, "meta": {"auto_layout": False}})],
            [("e", "a", "b")],
            direction="LR",
        )
        e = g.edges[0]
        from drawio_optimize.model import Port
        e.src_port = Port("R", 0.15, "out", "data")
        e.tgt_port = Port("L", 0.85, "in", "data")
        Router(g, RouterConfig()).route_all()
        self.assertGreater(e.bends, 0)
        _snap_straight(g, PortConfig(direction="LR"))
        Router(g, RouterConfig()).route_all()
        self.assertEqual(e.bends, 0, e.path)
        self.assertAlmostEqual(e.path[0][1], e.path[1][1], places=2)


if __name__ == "__main__":
    unittest.main()
