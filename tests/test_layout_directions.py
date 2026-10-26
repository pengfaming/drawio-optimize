"""LR / TB / RL placement and container containment."""
from __future__ import annotations

import unittest

from support import graph
from drawio_optimize.layout import LayeredLayout, LayoutConfig


class LayoutDirectionTest(unittest.TestCase):
    def _chain(self, direction):
        g = graph(
            [("a", "A"), ("b", "B"), ("c", "C")],
            [("e1", "a", "b"), ("e2", "b", "c")],
            direction=direction,
        )
        LayeredLayout(g, LayoutConfig(direction=direction)).run()
        return g

    def test_lr_flows_right(self):
        g = self._chain("LR")
        self.assertLess(g.nodes["a"].x, g.nodes["b"].x)
        self.assertLess(g.nodes["b"].x, g.nodes["c"].x)

    def test_tb_flows_down(self):
        g = self._chain("TB")
        self.assertLess(g.nodes["a"].y, g.nodes["b"].y)
        self.assertLess(g.nodes["b"].y, g.nodes["c"].y)

    def test_rl_flows_left(self):
        g = self._chain("RL")
        self.assertGreater(g.nodes["a"].x, g.nodes["b"].x)
        self.assertGreater(g.nodes["b"].x, g.nodes["c"].x)

    def test_bt_flows_up(self):
        g = self._chain("BT")
        self.assertGreater(g.nodes["a"].y, g.nodes["b"].y)

    def test_no_overlap_after_layout(self):
        g = self._chain("TB")
        ns = list(g.nodes.values())
        for i in range(len(ns)):
            for j in range(i + 1, len(ns)):
                self.assertFalse(ns[i].rect.overlaps(ns[j].rect), (ns[i].id, ns[j].id))

    def test_children_stay_inside_their_container(self):
        g = graph(
            [("g1", "Group", {"container": True}),
             ("a", "A", {"parent": "g1"}), ("b", "B", {"parent": "g1"})],
            [("e", "a", "b")],
        )
        LayeredLayout(g, LayoutConfig(direction="LR")).run()
        c = g.nodes["g1"]
        for nid in ("a", "b"):
            self.assertTrue(c.rect.contains_rect(g.nodes[nid].rect, tol=1), (nid, c.rect, g.nodes[nid].rect))
        self.assertLess(c.w, 600)


if __name__ == "__main__":
    unittest.main()
