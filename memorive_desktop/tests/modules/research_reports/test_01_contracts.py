from __future__ import annotations

import unittest

from memorive.research.reports import renderer
from memorive.research.reports.digest import GENERATOR_VERSION
from memorive.research.reports.policy import SECTION_TITLES


class ContractTests(unittest.TestCase):
    def test_CT_007_the_report_shows_every_section_once(self):
        """CT-007: each section of a digest appears in the report, either under what needs attention
        or under the progress of the period, and in only one of the two."""
        for kind, sections in SECTION_TITLES.items():
            shown = list(renderer.ATTENTION[kind]) + list(renderer.PROGRESS[kind])
            self.assertEqual(sorted(shown), sorted(sections))

    def test_CT_008_generator_version_binds_package_tree(self):
        """CT-008"""
        self.assertEqual(GENERATOR_VERSION, "research_reports-research-changelog/1.1.0")
        self.assertNotIn("+sha256.", GENERATOR_VERSION)


if __name__ == "__main__":
    unittest.main()
