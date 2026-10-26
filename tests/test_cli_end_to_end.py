"""A real command-line run: artifacts, report contract and validation."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from support import ROOT, load, tmpdir
from drawio_optimize import cli
from drawio_optimize.validate import validate_file


class CliEndToEndTest(unittest.TestCase):
    def test_generate_writes_all_artifacts(self):
        with tmpdir() as d:
            out = Path(d) / "out"
            code = cli.main(
                "generate",
                [
                    "--spec", str(ROOT / "assets" / "templates" / "soc_dft.yaml"),
                    "--out-dir", str(out),
                    "--iterations", "2",
                    "--render", "png",
                    "--no-drawio",
                ],
            )
            self.assertEqual(code, 0)
            for name in ("diagram.drawio", "diagram.png", "drc-report.json", "drc-report.md"):
                self.assertTrue((out / name).exists(), name)
            report = json.loads((out / "drc-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["schema"], "drawio-optimize/report/v1")
            for key in ("summary", "metrics", "drc", "iterations", "render", "validation", "files"):
                self.assertIn(key, report)
            self.assertEqual(report["summary"]["edges"], 10)
            self.assertEqual(report["summary"]["nodes"], 8)
            self.assertTrue(report["validation"]["topology_ok"])
            self.assertEqual(report["drc"]["rules_total"], 33)
            # the file must be a real editable Draw.io document
            g = load(out / "diagram.drawio")
            self.assertEqual(len([n for n in g.nodes.values() if not n.container]), 8)
            self.assertEqual(len([n for n in g.nodes.values() if n.container]), 2)
            self.assertGreater(len(g.edges), 0)
            self.assertTrue(all(e.waypoints is not None for e in g.edges))
            self.assertTrue(validate_file(out / "diagram.drawio", reexport=False)["ok"])

    def test_drc_subcommand_reports_and_exits(self):
        with tmpdir() as d:
            out = Path(d) / "drc"
            cli.main("generate", ["--spec", str(ROOT / "assets" / "templates" / "microservices.yaml"),
                                  "--out-dir", str(Path(d) / "g"), "--render", "png", "--no-drawio"])
            src = Path(d) / "g" / "diagram.drawio"
            code = cli.main("drc", ["--input", str(src), "--out-dir", str(out)])
            self.assertIn(code, (0, 1))
            self.assertTrue((out / "drc-report.json").exists())
            self.assertTrue((out / "drc-report.md").exists())


if __name__ == "__main__":
    unittest.main()
