"""Every required DRC rule must exist with complete metadata."""
from __future__ import annotations

import unittest

from support import drc, graph
from drawio_optimize.drc import RULES, SEVERITIES


class RuleRegistryTest(unittest.TestCase):
    REQUIRED = (
        [f"R{i:02d}" for i in range(1, 15)]
        + [f"P{i:02d}" for i in range(1, 11)]
        + [f"L{i:02d}" for i in range(1, 8)]
    )

    def test_all_required_rules_registered(self):
        missing = [r for r in self.REQUIRED if r not in RULES]
        self.assertEqual(missing, [])

    def test_rule_metadata_is_complete(self):
        for rid, rule in RULES.items():
            self.assertEqual(rule.id, rid)
            self.assertTrue(rule.name)
            self.assertIn(rule.severity, SEVERITIES)
            self.assertTrue(rule.category)
            self.assertTrue(rule.description)
            self.assertTrue(callable(rule.check))

    def test_rules_run_on_an_empty_diagram(self):
        g = graph([], [])
        found = drc(g)
        self.assertEqual(found, [])

    def test_rule_crash_does_not_hide_other_findings(self):
        # a graph with a dangling edge exercises S01/S02/R09 simultaneously
        g = graph([("a", "A")], [("e", "a", "missing")])
        found = drc(g)
        self.assertTrue(found)
        self.assertTrue(all(v.rule_id in RULES for v in found))


if __name__ == "__main__":
    unittest.main()
