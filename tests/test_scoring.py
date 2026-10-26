"""Normalisation, preset weights, hard-constraint dominance and rollback."""
from __future__ import annotations

import unittest

from support import graph, routed
from drawio_optimize.scoring import PRESETS, better, raw_metrics, score
from drawio_optimize.router import Router, RouterConfig


class ScoringTest(unittest.TestCase):
    def _graph(self):
        g = graph(
            [("a", "A", {"x": 40, "y": 40, "w": 110, "h": 60, "meta": {"auto_layout": False}}),
             ("b", "B", {"x": 400, "y": 40, "w": 110, "h": 60, "meta": {"auto_layout": False}})],
            [("e", "a", "b")],
        )
        routed(g)
        return g

    def test_hard_failures_dominate(self):
        g = self._graph()
        raw = raw_metrics(g)
        soft = score(raw, g, preset="balanced")
        hard = score(raw, g, preset="balanced", hard_failures=1)
        self.assertGreaterEqual(hard["total"] - soft["total"], 999.0)

    def test_presets_have_distinct_weights(self):
        self.assertEqual(set(PRESETS), {"compact", "balanced", "clean-routing"})
        self.assertGreater(
            PRESETS["clean-routing"]["crossings"], PRESETS["compact"]["crossings"]
        )
        g = self._graph()
        raw = raw_metrics(g)
        a = score(raw, g, preset="compact")
        b = score(raw, g, preset="clean-routing")
        self.assertNotEqual(a["total"], b["total"])

    def test_metrics_are_normalised(self):
        g = self._graph()
        raw = raw_metrics(g)
        s = score(raw, g)
        for k, v in s["normalised"].items():
            self.assertGreaterEqual(v, 0.0, k)
            self.assertLess(v, 100.0, k)

    def test_better_and_rollback_compare_totals(self):
        low = {"total": 1.0}
        high = {"total": 2.0}
        self.assertTrue(better(low, high))
        self.assertFalse(better(high, low))


if __name__ == "__main__":
    unittest.main()
