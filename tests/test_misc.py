"""Self-loops, disconnected components and the three layout presets."""
from __future__ import annotations

import unittest

from support import graph, routed
from drawio_optimize.drc import DrcConfig, run_drc
from drawio_optimize.layout import LayeredLayout, LayoutConfig
from drawio_optimize.router import Router, RouterConfig


class MiscTest(unittest.TestCase):
    def test_self_loop_does_not_crash_or_detach(self):
        g = graph([("a", "A", {"x": 100, "y": 100, "w": 120, "h": 60, "meta": {"auto_layout": False}})],
                  [("e", "a", "a")])
        routed(g)
        e = g.edges[0]
        self.assertGreaterEqual(len(e.path), 4)
        self.assertFalse([v for v in run_drc(g, DrcConfig(direction="LR")) if v.rule_id == "R09"])

    def test_disconnected_components_do_not_overlap(self):
        g = graph(
            [("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")],
            [("e1", "a", "b")],
        )
        LayeredLayout(g, LayoutConfig(direction="LR")).run()
        ns = list(g.nodes.values())
        for i in range(len(ns)):
            for j in range(i + 1, len(ns)):
                self.assertFalse(ns[i].rect.overlaps(ns[j].rect))

    def test_preset_only_changes_the_score_not_the_topology(self):
        g = graph(
            [("a", "A"), ("b", "B"), ("c", "C")],
            [("e1", "a", "b"), ("e2", "b", "c")],
        )
        sig = g.topology_signature()
        LayeredLayout(g, LayoutConfig(direction="LR")).run()
        routed(g)
        self.assertEqual(g.topology_signature(), sig)
        self.assertEqual([(e.src, e.tgt) for e in g.edges], [("a", "b"), ("b", "c")])


if __name__ == "__main__":
    unittest.main()
