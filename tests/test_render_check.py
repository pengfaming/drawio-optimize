"""Real draw.io export + SVG path verification (skipped when unavailable)."""
from __future__ import annotations

import unittest
from pathlib import Path

from support import ROOT, pipeline, tmpdir
from drawio_optimize.render import find_drawio, png_liveness
from drawio_optimize.util import load_structured
from drawio_optimize.spec import graph_from_spec


@unittest.skipUnless(find_drawio(), "draw.io CLI not installed")
class RenderCheckTest(unittest.TestCase):
    def test_exported_paths_are_rectilinear_and_match_the_model(self):
        with tmpdir() as d:
            g, _ = graph_from_spec(load_structured(ROOT / "assets" / "templates" / "soc_dft.yaml"))
            res = pipeline(
                g,
                Path(d) / "out",
                use_drawio=True,
                render_formats=("png", "svg"),
                max_iterations=2,
            )
            render = res.report["render"]
            self.assertEqual(render["renderer"], "drawio")
            self.assertTrue(render["independent"])
            check = render["svg_check"]
            self.assertTrue(check["verified"], check)
            self.assertEqual(check["diagonal_segments"], 0, check)
            self.assertEqual(check["issues"], [])
            self.assertEqual(check["edges_matched"], len(g.edges))
            self.assertTrue(render["png"]["checked"])
            self.assertFalse(render["png"]["blank"])
            self.assertGreater(render["png"]["distinct_colors"], 5)


if __name__ == "__main__":
    unittest.main()
