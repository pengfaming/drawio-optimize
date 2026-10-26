"""Regression tests for real-world Draw.io/SVG compatibility and minimal repair.

These cover three defects found on a 585-node hand-authored atlas:

* ``<UserObject>``-wrapped cells were dropped, disconnecting their edges;
* SVG jump arcs (``C`` commands) made the rendered-path checker skip 54 edges;
* a bidirectional edge has two arrow triangles, and picking the first one made
  the rendered path jump back to the source.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from support import graph, load, tmpdir, write  # noqa: F401
from drawio_optimize.drawio_io import load as load_drawio
from drawio_optimize.drawio_io import write as write_drawio
from drawio_optimize.drc import run_drc
from drawio_optimize.geometry import Rect
from drawio_optimize.model import Edge, Graph, Node, Port
from drawio_optimize.repair import (
    RepairOptions,
    repair_drc_config,
    repair_terminal_doglegs,
    repair_terminals,
    sync_label_boxes,
)
from drawio_optimize.svgcheck import map_to_model, parse_svg


USEROBJECT_XML = """<mxfile host="app.diagrams.net" data-visual-template="square-engineering-atlas">
  <diagram id="p" name="wrapped">
    <mxGraphModel dx="800" dy="600" page="1" pageWidth="850" pageHeight="1100">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <UserObject id="u1" label="" tooltip="module source" data-source="u1.v">
          <mxCell style="rounded=0;html=1;container=1;" vertex="1" parent="1">
            <mxGeometry x="100" y="100" width="240" height="160" as="geometry"/>
          </mxCell>
        </UserObject>
        <UserObject id="u2" label="Inner" tooltip="inner module" data-source="u2.v">
          <mxCell style="rounded=0;html=1;" vertex="1" parent="u1">
            <mxGeometry x="20" y="30" width="100" height="50" as="geometry"/>
          </mxCell>
        </UserObject>
        <mxCell id="e1" value="valid" style="edgeStyle=none;endArrow=block;html=1;exitX=0.5;exitY=1;entryX=0.5;entryY=0;"
                edge="1" parent="1" source="u2" target="u1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""


class UserObjectTest(unittest.TestCase):
    def test_wrapped_nodes_and_edges_survive_roundtrip(self):
        with tmpdir() as d:
            src = Path(d) / "wrapped.drawio"
            src.write_text(USEROBJECT_XML, encoding="utf-8")
            g = load_drawio(src)
            self.assertEqual(set(g.nodes), {"u1", "u2"})
            self.assertEqual(len(g.edges), 1)
            self.assertEqual(g.diagnostics, [])
            # the child is positioned relative to its wrapped container
            self.assertEqual((g.nodes["u1"].x, g.nodes["u1"].y), (100, 100))
            self.assertEqual((g.nodes["u2"].x, g.nodes["u2"].y), (120, 130))
            self.assertTrue(g.nodes["u1"].container)
            self.assertEqual(
                g.nodes["u1"].meta["wrapper"]["attrs"]["data-source"], "u1.v"
            )
            self.assertEqual(
                g.page.get("_file_attrs", {}).get("data-visual-template"),
                "square-engineering-atlas",
            )

            out = Path(d) / "out.drawio"
            write_drawio(g, out)
            text = out.read_text(encoding="utf-8")
            self.assertIn("<UserObject ", text)
            self.assertIn('id="u1"', text)
            self.assertIn('tooltip="module source"', text)
            self.assertIn('data-source="u2.v"', text)
            self.assertIn("data-visual-template=\"square-engineering-atlas\"", text)

            again = load_drawio(out)
            self.assertEqual(again.topology_signature(), g.topology_signature())
            self.assertEqual(set(again.nodes), {"u1", "u2"})
            self.assertEqual(again.nodes["u2"].parent, "u1")
            self.assertEqual(
                again.nodes["u2"].meta["wrapper"]["attrs"]["tooltip"],
                "inner module",
            )


SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400" viewBox="0 0 400 400">
  <g data-cell-id="n1"><g transform="translate(0.5,0.5)">
    <rect x="0" y="0" width="100" height="50" fill="#fff"/>
  </g></g>
  <g data-cell-id="n2"><g transform="translate(0.5,0.5)">
    <rect x="0" y="200" width="100" height="50" fill="#fff"/>
  </g></g>
  <g data-cell-id="e1"><g transform="translate(0.5,0.5)">
    <path d="M 50 50 L 50 150 C 50 160 60 160 60 150 L 50 190" fill="none" stroke="#000"/>
    <path d="M 50 50 L 45 60 L 55 60 Z" fill="#000"/>
    <path d="M 50 200 L 45 190 L 55 190 Z" fill="#000"/>
  </g></g>
</svg>
"""


class SvgPathTest(unittest.TestCase):
    def test_jump_arc_and_both_arrowheads(self):
        with tmpdir() as d:
            svg = Path(d) / "r.svg"
            svg.write_text(SVG, encoding="utf-8")
            g = Graph()
            g.add_node(Node("n1", "A", x=0, y=0, w=100, h=50))
            g.add_node(Node("n2", "B", x=0, y=200, w=100, h=50))
            e = Edge(
                "e1",
                "n1",
                "n2",
                src_port=Port("B", 0.5, role="out"),
                tgt_port=Port("T", 0.5, role="in"),
            )
            e.path = [(50, 50), (50, 200)]
            g.edges.append(e)

            r = map_to_model(parse_svg(svg), g)
            self.assertTrue(r.verified, r.reason)
            self.assertIn("e1", r.paths)
            # the C command must not make the whole connector disappear
            self.assertGreater(len(r.paths["e1"]), 2)
            # both arrow tips are present; the end tip terminates at the anchor
            self.assertLess(abs(r.paths["e1"][-1][0] - 50), 1.5)
            self.assertLess(abs(r.paths["e1"][-1][1] - 200), 1.5)
            self.assertEqual(r.endpoint_errors.get("e1", 99) < 2, True)


class MinimalRepairTest(unittest.TestCase):
    def test_terminal_overshoot_gets_a_clean_stub(self):
        g = Graph()
        g.add_node(Node("a", "A", x=100, y=100, w=100, h=50))
        g.add_node(Node("b", "B", x=70, y=300, w=100, h=50))
        e = Edge(
            "e1",
            "a",
            "b",
            src_port=Port("B", 0.5, role="out"),
            tgt_port=Port("T", 0.5, role="in"),
            waypoints=[(150, 293), (120, 293), (120, 330)],
            style="endArrow=block;html=1;exitX=0.5;exitY=1;entryX=0.5;entryY=0;",
        )
        g.edges.append(e)
        e.path = [(150, 150), (150, 293), (120, 293), (120, 330), (120, 300)]
        cfg = repair_drc_config("TB")
        before = run_drc(g, cfg)
        self.assertTrue(
            any(v.rule_id == "R12" and e.id in v.objects for v in before),
            [v.to_dict() for v in before],
        )
        changes, after = repair_terminals(g, cfg, RepairOptions(arrow_stub=18))
        self.assertEqual(len(changes), 1)
        self.assertFalse(
            any(v.rule_id == "R12" and e.id in v.objects for v in after)
        )
        self.assertEqual(e.waypoints[-1], (120.0, 282.0))
        self.assertEqual(e.waypoints[-2], (150.0, 282.0))
        # nodes must not move
        self.assertEqual((g.nodes["a"].x, g.nodes["a"].y), (100, 100))
        self.assertEqual((g.nodes["b"].x, g.nodes["b"].y), (70, 300))

    def test_terminal_dogleg_snaps_the_target_anchor(self):
        g = Graph()
        g.add_node(Node("a", "A", x=0, y=0, w=100, h=50))
        g.add_node(Node("b", "B", x=30, y=200, w=100, h=50))
        e = Edge(
            "e1",
            "a",
            "b",
            src_port=Port("B", 0.5, role="out"),
            tgt_port=Port("T", 0.5, role="in"),
            waypoints=[(50, 150), (80, 150)],
            style="endArrow=block;html=1;exitX=0.5;exitY=1;entryX=0.5;entryY=0;",
        )
        e.path = [(50, 50), (50, 150), (80, 150), (80, 200)]
        g.edges.append(e)
        cfg = repair_drc_config("TB")
        before = run_drc(g, cfg)
        self.assertTrue(
            any(v.rule_id == "R01" and "dogleg" in v.message for v in before),
            [v.to_dict() for v in before],
        )
        changes, unfixed = repair_terminal_doglegs(
            g, cfg, RepairOptions(dogleg_min=20)
        )
        self.assertEqual(len(changes), 1, unfixed)
        # target anchor slides 30px left onto the incoming trunk
        self.assertAlmostEqual(e.tgt_port.frac, 0.2, places=4)
        self.assertEqual(e.waypoints, [])
        after = run_drc(g, cfg)
        self.assertFalse(
            any(v.rule_id in ("R01", "R12") and e.id in v.objects for v in after)
        )
        self.assertEqual((g.nodes["a"].x, g.nodes["a"].y), (0, 0))
        self.assertEqual((g.nodes["b"].x, g.nodes["b"].y), (30, 200))

    def test_moved_value_label_uses_its_real_rel_position(self):
        g = Graph()
        g.add_node(Node("a", "A", x=0, y=0, w=50, h=20))
        g.add_node(Node("b", "B", x=200, y=0, w=50, h=20))
        e = Edge("e1", "a", "b", label="L")
        e.path = [(50, 40), (200, 40)]
        e.label_state.text = "L"
        e.label_state.rel_x = 0.5
        e.label_state.rel_y = 24
        e.meta["label_moved"] = True
        g.edges.append(e)
        sync_label_boxes(g)
        box = e.label_state.bbox
        self.assertIsNotNone(box)
        # x=0.5 -> 75% along a 150px edge, y is 24px above the wire
        self.assertAlmostEqual(box.cx, 162.5, delta=1.5)
        self.assertAlmostEqual(box.cy, 16.0, delta=1.5)


if __name__ == "__main__":
    unittest.main()
