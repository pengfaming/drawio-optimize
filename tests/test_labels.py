"""Label fitting and collision tests, including mixed Chinese/English text."""
from __future__ import annotations

import unittest

from support import drc, graph, place_labels, routed
from drawio_optimize.textmetrics import measurer


class LabelTest(unittest.TestCase):
    def test_cjk_text_is_measured_with_real_fonts(self):
        m = measurer(12)
        self.assertTrue(m.backend.startswith("pillow"), m.backend)
        self.assertGreater(m.line_width("扫描链"), m.line_width("ab"))
        w, h, lines = m.measure("扫描链控制信号", 80)
        self.assertGreaterEqual(len(lines), 1)
        self.assertGreater(w, 0)

    def test_small_node_grows_to_fit_cjk(self):
        g = graph(
            [("n", "扫描链控制器与旁路逻辑", {"x": 10, "y": 10, "w": 70, "h": 28})],
            [],
        )
        before = [v for v in drc(g) if v.rule_id == "L04"]
        self.assertTrue(before, "expected an overflow before fitting")
        place_labels(g)
        after = [v for v in drc(g) if v.rule_id == "L04"]
        self.assertEqual(after, [])
        self.assertGreater(g.nodes["n"].w + g.nodes["n"].h, 98)

    def test_edge_labels_do_not_overlap(self):
        g = graph(
            [("a", "A", {"x": 40, "y": 40, "w": 100, "h": 50, "meta": {"auto_layout": False}}),
             ("b", "B", {"x": 400, "y": 40, "w": 100, "h": 50, "meta": {"auto_layout": False}}),
             ("c", "C", {"x": 40, "y": 240, "w": 100, "h": 50, "meta": {"auto_layout": False}}),
             ("d", "D", {"x": 400, "y": 240, "w": 100, "h": 50, "meta": {"auto_layout": False}})],
            [("e1", "a", "b", {"label": "数据通路 data_path"}),
             ("e2", "c", "d", {"label": "控制信号 ctrl_en"})],
        )
        routed(g)
        place_labels(g)
        boxes = g.all_label_boxes()
        overlaps = 0
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if boxes[i]["rect"].overlaps(boxes[j]["rect"]):
                    overlaps += 1
        self.assertEqual(overlaps, 0)
        self.assertEqual([v for v in drc(g) if v.rule_id == "L02"], [])


if __name__ == "__main__":
    unittest.main()
