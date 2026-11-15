from __future__ import annotations

import json
import unittest
from pathlib import Path

from research_reports.digest import GENERATOR_VERSION
from research_reports.policy import SECTION_TITLES

# The packaged report templates. CT-007 used to compare copies of them inside a materialized
# acceptance sandbox; the sandbox and the schemas it copied were removed with the replay CLI
# (b3407ef), and both copies came from this folder anyway.
TEMPLATES = Path(__file__).resolve().parents[3] / "source" / "research_reports" / "templates"


class ContractTests(unittest.TestCase):
    def test_CT_007_templates_match_frozen_section_policy(self):
        """CT-007"""
        for kind, sections in SECTION_TITLES.items():
            template = json.loads((TEMPLATES / f"{kind}.json").read_text(encoding="utf-8"))
            self.assertEqual(template["sections"], list(sections))

    def test_CT_008_generator_version_binds_package_tree(self):
        """CT-008"""
        self.assertEqual(GENERATOR_VERSION, "research_reports-research-changelog/1.1.0")
        self.assertNotIn("+sha256.", GENERATOR_VERSION)


if __name__ == "__main__":
    unittest.main()
