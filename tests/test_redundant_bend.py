"""Acceptance test 2: the terminal-jog drawing must simplify to one vertical line."""
from __future__ import annotations

import unittest
from pathlib import Path

from support import graph, load, pipeline, tmpdir, write_bad_jog
from drawio_optimize.geometry import is_orthogonal_segment


class RedundantBendTest(unittest.TestCase):
    def test_input_jog_is_detected_and_fixed(self):
        with tmpdir() as d:
            p = write_bad_jog(Path(d) / "bad.drawio")
            g = load(p)
            before = {nid: (n.x, n.y) for nid, n in g.nodes.items()}
            res = pipeline(g, Path(d) / "out", layout_mode="preserve", max_iterations=2)
            report = res.report
            before_rules = {v["rule"] for v in report["drc"]["violations_before"]}
            self.assertIn("R01", before_rules)
            self.assertEqual(
                [v for v in report["drc"]["violations_after"] if v["rule"] == "R01"], []
            )
            edge = next(e for e in res.graph.edges if e.id == "e1")
            self.assertEqual(edge.bends, 0, edge.path)
            self.assertTrue(is_orthogonal_segment(edge.path[0], edge.path[-1]))
            self.assertAlmostEqual(edge.path[0][0], edge.path[-1][0], places=2)
            after = {nid: (n.x, n.y) for nid, n in res.graph.nodes.items()}
            self.assertEqual(before, after, "preserve mode must not move nodes")
            # topology survived the round-trip
            reloaded = load(Path(d) / "out" / "diagram.drawio")
            self.assertEqual(reloaded.topology_signature(), g.topology_signature())
            self.assertEqual(len(reloaded.nodes), len(g.nodes))

    def test_waypoint_path_through_obstacle_is_detected(self):
        with tmpdir() as d:
            p = write_bad_jog(Path(d) / "bad2.drawio", through_obstacle=True)
            g = load(p)
            res = pipeline(g, Path(d) / "out", layout_mode="preserve", max_iterations=2)
            rules = {v["rule"] for v in res.report["drc"]["violations_before"]}
            self.assertIn("R02", rules)
            self.assertEqual(
                [v for v in res.report["drc"]["violations_after"] if v["rule"] == "R02"], []
            )
            edge = next(e for e in res.graph.edges if e.id == "e1")
            from drawio_optimize.geometry import segment_rect_hit
            for i in range(len(edge.path) - 1):
                self.assertFalse(
                    segment_rect_hit(edge.path[i], edge.path[i + 1], res.graph.nodes["ob"].rect, tol=0.2)
                )


if __name__ == "__main__":
    unittest.main()
