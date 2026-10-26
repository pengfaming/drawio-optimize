"""Acceptance test 3: an obstacle between two modules must be routed around."""
from __future__ import annotations

import unittest

from support import graph, routed
from drawio_optimize.geometry import segment_rect_hit


class NodePenetrationTest(unittest.TestCase):
    def test_router_goes_around_obstacle(self):
        g = graph(
            [("a", "A", {"x": 40, "y": 100, "w": 100, "h": 60, "meta": {"auto_layout": False}}),
             ("o", "Obstacle", {"x": 240, "y": 80, "w": 100, "h": 100, "meta": {"auto_layout": False}}),
             ("b", "B", {"x": 480, "y": 100, "w": 100, "h": 60, "meta": {"auto_layout": False}})],
            [("e", "a", "b")],
        )
        routed(g)
        e = g.edges[0]
        self.assertGreaterEqual(len(e.path), 3)
        for i in range(len(e.path) - 1):
            self.assertFalse(
                segment_rect_hit(e.path[i], e.path[i + 1], g.nodes["o"].rect, tol=0.2),
                f"segment {i} penetrates the obstacle: {e.path}",
            )
        self.assertLessEqual(e.bends, 4, e.path)
        # every segment stays orthogonal
        for i in range(len(e.path) - 1):
            dx = abs(e.path[i + 1][0] - e.path[i][0])
            dy = abs(e.path[i + 1][1] - e.path[i][1])
            self.assertTrue(dx < 0.6 or dy < 0.6, e.path)


if __name__ == "__main__":
    unittest.main()
