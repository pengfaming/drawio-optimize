"""Natural-language front end: domain templates + explicit relations."""
from __future__ import annotations

import unittest
from pathlib import Path

from support import pipeline, tmpdir
from drawio_optimize.nl import text_to_spec
from drawio_optimize.spec import graph_from_spec, validate_spec


class NaturalLanguageTest(unittest.TestCase):
    def test_soc_dft_prompt_selects_the_dft_template(self):
        prompt = (
            "请画一个 SoC DFT 架构图，包含 JTAG、TAP Controller、Scan Controller、"
            "Scan Chains、MBIST Controller。要求信号从左向右流动，"
            "控制信号与数据路径区分颜色。"
        )
        spec = text_to_spec(prompt)
        ids = {n["id"] for n in spec["nodes"]}
        self.assertTrue({"jtag", "tap", "scan_ctrl", "scan_chains", "mbist"} <= ids)
        self.assertEqual(validate_spec(spec), [])

    def test_explicit_arrows_are_never_invented(self):
        spec = text_to_spec("Alpha -> Beta [handshake]\nBeta -> Gamma")
        rels = [(e["from"], e["to"], e["label"]) for e in spec["edges"]]
        self.assertIn(("Alpha", "Beta", "handshake"), rels)
        self.assertIn(("Beta", "Gamma", ""), rels)
        self.assertEqual(len(rels), 2)

    def test_template_and_explicit_edges_merge(self):
        prompt = "SoC DFT 架构图 JTAG TAP scan mbist\nATPG -> Scan Controller [pattern]"
        spec = text_to_spec(prompt)
        ids = {n["id"] for n in spec["nodes"]}
        self.assertIn("atpg", ids)
        self.assertIn("scan_ctrl", ids)
        self.assertTrue(any(e["from"] == "atpg" for e in spec["edges"]))

    def test_nl_spec_generates_a_passing_diagram(self):
        prompt = "请画一个 SoC DFT 架构图，包含 JTAG、TAP Controller、Scan Controller、Scan Chains、MBIST Controller"
        spec = text_to_spec(prompt)
        g, _ = graph_from_spec(spec)
        with tmpdir() as d:
            res = pipeline(g, Path(d) / "out", max_iterations=2)
            self.assertEqual(
                res.exit_code, 0,
                [v["message"] for v in res.report["drc"]["unfixed_violations"]],
            )
            self.assertGreaterEqual(len(g.nodes), 6)


if __name__ == "__main__":
    unittest.main()
