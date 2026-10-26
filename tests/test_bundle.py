"""Regression tests for endpoint jogs and dense vertical-bus cleanup."""
from __future__ import annotations

import unittest

from drawio_optimize.drc import DrcConfig, run_drc
from drawio_optimize.geometry import simplify_points
from drawio_optimize.model import Edge, Graph, Node, Port
from drawio_optimize.repair import (
    RepairOptions,
    repair_bundle_lanes,
    repair_drc_config,
    repair_endpoint_jogs,
    repair_terminal_hooks,
)


def _edge(g: Graph, eid: str, src: str, tgt: str, sp: Port, tp: Port, path):
    e = Edge(eid, src, tgt, src_port=sp, tgt_port=tp)
    e.path = list(path)
    e.waypoints = list(path[1:-1])
    g.edges.append(e)
    return e


class EndpointJogTest(unittest.TestCase):
    def test_source_staircase_is_straightened(self):
        g = Graph()
        g.add_node(Node("a", "A", x=0, y=0, w=100, h=100))
        g.add_node(Node("b", "B", x=400, y=0, w=100, h=100))
        e = _edge(
            g, "e1", "a", "b",
            Port("R", 0.2, role="out"), Port("L", 0.3, role="in"),
            [(100, 20), (125, 20), (125, 30), (400, 30)],
        )
        changes, unfixed = repair_endpoint_jogs(g, repair_drc_config(), RepairOptions())
        self.assertEqual(len(changes), 1, unfixed)
        self.assertEqual(changes[0].kind, "anchor_source")
        self.assertAlmostEqual(e.src_port.frac, 0.3, places=3)
        self.assertEqual(len(simplify_points(e.path)), 2, e.path)
        self.assertEqual((e.path[0][0], e.path[-1][0]), (100.0, 400.0))
        self.assertAlmostEqual(e.path[0][1], e.path[-1][1], places=2)

    def test_z_shape_collapses_to_a_straight_vertical(self):
        g = Graph()
        g.add_node(Node("a", "A", x=0, y=0, w=400, h=100))
        g.add_node(Node("b", "B", x=300, y=200, w=100, h=100))
        e = _edge(
            g, "e1", "a", "b",
            Port("B", 0.5, role="out"), Port("T", 0.5, role="in"),
            [(200, 100), (200, 130), (350, 130), (350, 200)],
        )
        changes, unfixed = repair_endpoint_jogs(g, repair_drc_config(), RepairOptions())
        self.assertEqual(len(changes), 1, unfixed)
        self.assertAlmostEqual(e.src_port.frac, 0.875, places=3)
        self.assertEqual(len(simplify_points(e.path)), 2, e.path)
        self.assertAlmostEqual(e.path[0][0], e.path[-1][0], places=2)


class DenseParallelRuleTest(unittest.TestCase):
    def _graph(self, gap: float) -> Graph:
        g = Graph()
        g.add_node(Node("a", "A", x=0, y=0, w=50, h=50))
        g.add_node(Node("b", "B", x=0, y=200, w=50, h=50))
        for i, x in enumerate((100.0, 100.0 + gap)):
            e = Edge(f"e{i}", "a", "b", src_port=Port("B", 0.5), tgt_port=Port("T", 0.5))
            e.path = [(x, 50), (x, 200)]
            g.edges.append(e)
        return g

    def test_close_parallel_wires_are_reported(self):
        found = run_drc(self._graph(10.0), DrcConfig(min_wire_pitch=24.0))
        r13 = [v for v in found if v.rule_id == "R13"]
        self.assertEqual(len(r13), 1, [v.to_dict() for v in r13])
        self.assertEqual(set(r13[0].objects), {"e0", "e1"})
        self.assertIn("10.0px", r13[0].message)

    def test_pitch_at_or_above_limit_is_clear(self):
        found = run_drc(self._graph(36.0), DrcConfig(min_wire_pitch=24.0))
        self.assertFalse([v for v in found if v.rule_id == "R13"])


class TerminalHookTest(unittest.TestCase):
    def test_outward_terminal_hook_is_removed(self):
        g = Graph()
        g.add_node(Node("a", "A", x=0, y=0, w=100, h=100))
        g.add_node(Node("b", "B", x=300, y=0, w=100, h=100))
        e = _edge(
            g, "e1", "a", "b",
            Port("B", 0.5, role="out"), Port("R", 0.5, role="in"),
            [(50, 100), (50, 200), (420, 200), (420, 90),
             (440, 90), (440, 50), (400, 50)],
        )
        cfg = repair_drc_config()
        before = run_drc(g, cfg)
        hooks = [v for v in before if v.rule_id == "R14"]
        self.assertEqual(len(hooks), 1, [v.to_dict() for v in before])
        self.assertEqual(hooks[0].severity, "error")
        changes, unfixed = repair_terminal_hooks(g, cfg, RepairOptions())
        self.assertEqual(len(changes), 1, unfixed)
        self.assertEqual(changes[0].kind, "terminal_hook")
        self.assertFalse(
            any(v.rule_id in ("R14", "R01", "R12") and e.id in v.objects for v in run_drc(g, cfg))
        )
        self.assertEqual(e.path[-2], (418.0, 50.0))
        self.assertGreaterEqual(e.path[-2][0] - e.path[-1][0], 18.0)


class BundleLaneTest(unittest.TestCase):
    def test_dense_bus_gets_even_lanes_and_the_badge_is_reported(self):
        g = Graph()
        offsets = [0, 10, 20, 30, 40, 50]
        for i, x in enumerate(offsets):
            sx = 200 + i * 90
            g.add_node(Node(f"s{i}", "", x=sx, y=0, w=60, h=80))
            g.add_node(Node(f"t{i}", "", x=sx, y=620, w=60, h=80))
            _edge(
                g, f"w{i}", f"s{i}", f"t{i}",
                Port("L", 0.5, role="out"), Port("R", 0.5, role="in"),
                [(sx, 40), (x, 40), (x, 660), (sx + 60, 660)],
            )
        badge = Node("badge", "04", x=60, y=250, w=40, h=44)
        header = Node("header", "Header title", x=104, y=250, w=300, h=44)
        g.add_node(badge)
        g.add_node(header)

        # The bus pass is deliberately judged with all rules disabled here:
        # this fixture is only about lane geometry and the reported badge move.
        cfg = DrcConfig(disabled=(
            "R01", "R02", "R03", "R04", "R05", "R06", "R07", "R08", "R09",
            "R10", "R11", "R12", "P01", "P04", "P05", "P06", "P07", "P08",
            "P09", "P10", "L01", "L02", "L03", "L04", "L05", "L06", "L07",
        ))
        changes, node_changes, unfixed = repair_bundle_lanes(g, cfg, RepairOptions())
        self.assertEqual(unfixed, [])
        self.assertEqual(len(node_changes), 2)
        self.assertEqual({c.edge_id for c in node_changes}, {"badge", "header"})
        self.assertGreater(badge.x, 60)
        self.assertGreater(header.x, 104)
        self.assertGreaterEqual(len(changes), 4)

        # Every long vertical run now sits on one of six evenly spaced lanes.
        lanes = []
        for e in g.edges:
            ys = [p[1] for p in simplify_points(e.path)]
            if max(ys) - min(ys) >= 100:
                xs = [p[0] for p in simplify_points(e.path) if p[1] not in (min(ys), max(ys))]
                lanes.append(round(xs[0], 1) if xs else round(e.path[0][0], 1))
        lanes = sorted(set(lanes))
        self.assertGreaterEqual(len(lanes), 5, lanes)
        gaps = [b - a for a, b in zip(lanes, lanes[1:])]
        self.assertGreaterEqual(min(gaps), 16.0, lanes)

    def test_functional_nodes_are_not_moved_by_the_bundle_pass(self):
        g = Graph()
        g.add_node(Node("a", "A", x=0, y=0, w=100, h=50))
        g.add_node(Node("b", "B", x=400, y=0, w=100, h=50))
        e = _edge(
            g, "e1", "a", "b",
            Port("R", 0.5, role="out"), Port("L", 0.5, role="in"),
            [(100, 25), (400, 25)],
        )
        before = {n.id: (n.x, n.y) for n in g.nodes.values()}
        repair_bundle_lanes(g, repair_drc_config(), RepairOptions())
        self.assertEqual({n.id: (n.x, n.y) for n in g.nodes.values()}, before)
        self.assertEqual(len(simplify_points(e.path)), 2)


if __name__ == "__main__":
    unittest.main()
