# -*- coding: utf-8 -*-
"""Boundary-noise regressions for deterministic DocumentClean cleaning."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = (Path(__file__).resolve().parents[3] / "source")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from document_processing.document_clean_clean import (  # noqa: E402
    _classify_boundary_noise,
    _strip_publisher_boilerplate,
)


class M1cBoundaryNoiseTest(unittest.TestCase):
    def test_footer_markdown_page_number_is_noise(self) -> None:
        self.assertEqual(
            _classify_boundary_noise(
                "### 1170",
                in_header_zone=False,
                in_footer_zone=True,
                is_repeated=False,
            ),
            "page_number",
        )

    def test_numeric_heading_outside_boundary_is_preserved(self) -> None:
        self.assertIsNone(
            _classify_boundary_noise(
                "### 1170",
                in_header_zone=False,
                in_footer_zone=False,
                is_repeated=False,
            )
        )

    def test_header_markdown_page_number_is_noise(self) -> None:
        self.assertEqual(
            _classify_boundary_noise(
                "### 1176",
                in_header_zone=True,
                in_footer_zone=False,
                is_repeated=False,
            ),
            "page_number",
        )

    def test_legitimate_numbered_section_is_preserved(self) -> None:
        self.assertIsNone(
            _classify_boundary_noise(
                "## 2. Materials and methods",
                in_header_zone=False,
                in_footer_zone=True,
                is_repeated=False,
            )
        )

    def test_structured_journal_running_heading_is_noise(self) -> None:
        self.assertEqual(
            _classify_boundary_noise(
                "### F.P.van der Zee et al./ Chemosphere 44 "
                "(2001) 1169–1176",
                in_header_zone=True,
                in_footer_zone=False,
                is_repeated=False,
            ),
            "running_header_footer",
        )

    def test_unicode_name_and_variable_are_not_source_of_rejection(self) -> None:
        self.assertIsNone(
            _classify_boundary_noise(
                "### Müller et al. developed α",
                in_header_zone=True,
                in_footer_zone=False,
                is_repeated=False,
            )
        )

    def test_repeated_short_plain_boundary_line_is_noise(self) -> None:
        self.assertEqual(
            _classify_boundary_noise(
                "Short running header",
                in_header_zone=True,
                in_footer_zone=False,
                is_repeated=True,
            ),
            "running_header_footer",
        )

    def test_copyright_suffix_is_trimmed_without_deleting_body(self) -> None:
        body = "Müller et al. found that α remained stable."
        line = (
            body
            + " © 2001 Elsevier Science Ltd. All rights reserved."
        )

        kept, removed = _strip_publisher_boilerplate(line)

        self.assertEqual(kept, body)
        self.assertEqual(
            removed,
            "© 2001 Elsevier Science Ltd. All rights reserved.",
        )

    def test_standalone_pii_masthead_is_removed_whole(self) -> None:
        line = (
            "0045-6535/01/$ - see front matter © 2001 Elsevier "
            "Science Ltd. All rights reserved. PII: S0045-6535"
        )

        kept, removed = _strip_publisher_boilerplate(line)

        self.assertEqual(kept, "")
        self.assertEqual(removed, line)

    def test_ordinary_elsevier_text_is_untouched(self) -> None:
        line = "Müller discussed an Elsevier article and variable α."

        self.assertEqual(
            _strip_publisher_boilerplate(line),
            (line, None),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
