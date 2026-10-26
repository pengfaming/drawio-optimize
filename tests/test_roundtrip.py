"""Acceptance test 5: generate -> write -> read must preserve topology and ids."""
from __future__ import annotations

import unittest
from pathlib import Path

from support import ROOT, load, tmpdir
from drawio_optimize.util import load_structured
from drawio_optimize.spec import graph_from_spec
from drawio_optimize.layout import LayeredLayout, LayoutConfig
from drawio_optimize.ports import assign_ports, PortConfig
from drawio_optimize.router import Router, RouterConfig
from drawio_optimize.labels import place_labels
from drawio_optimize.drawio_io import write


class RoundTripTest(unittest.TestCase):
    def test_spec_container_cjk_roundtrip(self):
        spec = load_structured(ROOT / "assets" / "templates" / "soc_dft.yaml")
        g, _ = graph_from_spec(spec)
        LayeredLayout(g, LayoutConfig(direction="LR")).run()
        assign_ports(g, PortConfig(direction="LR"))
        Router(g, RouterConfig()).route_all()
        place_labels(g)
        sig = g.topology_signature()
        ids = set(g.nodes) | {e.id for e in g.edges}
        with tmpdir() as d:
            p = Path(d) / "diagram.drawio"
            write(g, p)
            a = load(p)
            self.assertEqual(a.topology_signature(), sig)
            self.assertEqual(set(a.nodes) | {e.id for e in a.edges}, ids)
            self.assertEqual(len(a.nodes), len(g.nodes))
            p2 = Path(d) / "again.drawio"
            write(a, p2)
            b = load(p2)
            self.assertEqual(b.topology_signature(), sig)
            self.assertEqual(set(b.nodes) | {e.id for e in b.edges}, ids)

    def test_unknown_cells_are_preserved(self):
        xml = (
            '<mxfile host="t"><diagram id="p" name="x"><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="n" value="N" style="rounded=0;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="10" y="10" width="100" height="50" as="geometry"/></mxCell>'
            '<mxCell id="img" value="" style="shape=image;image=data:image/png;base64,AAA;" '
            'vertex="1" parent="1"><mxGeometry x="300" y="300" width="40" height="40" as="geometry"/></mxCell>'
            '</root></mxGraphModel></diagram></mxfile>'
        )
        with tmpdir() as d:
            p = Path(d) / "u.drawio"
            p.write_text(xml, encoding="utf-8")
            g = load(p)
            self.assertEqual(len(g.preserved), 1)
            out = Path(d) / "o.drawio"
            write(g, out)
            text = out.read_text(encoding="utf-8")
            self.assertIn('id="img"', text)
            # the unsupported cell must not silently disappear
            self.assertEqual(len(load(out).preserved), 1)


if __name__ == "__main__":
    unittest.main()
