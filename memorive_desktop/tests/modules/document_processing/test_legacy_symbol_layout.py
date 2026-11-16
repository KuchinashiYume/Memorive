# -*- coding: utf-8 -*-
"""Regressions for exact-font legacy symbol Layout recovery."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = (Path(__file__).resolve().parents[3] / "source")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memorive.documents.processing.engines.legacy_symbol_layout import (  # noqa: E402
    LegacySymbolLayoutRecoveryError,
    _context_hint,
    _lookup_replacement,
    _replace_markdown_pages,
    _replace_unknowns,
)


BON_COPYRIGHT_SHA = (
    "f481dab1d75d93885ef23b2ff52d60482ee201b7a5f99108321bea1fc587ed12"
)
BON_SCIENCE_SHA = (
    "d8b89eb316dbfe23514a728d71679382b6c4d83741b1e9e59d6e33e4e1eac69e"
)
LASPIDOU_MATH_SHA = (
    "96623c54e51874464b5909b6f527c4007591bf0516f75941b25ddd2d3afa105c"
)


class LegacySymbolLayoutTest(unittest.TestCase):
    def test_same_raw_code_depends_on_font_program(self) -> None:
        self.assertEqual(
            _lookup_replacement(BON_COPYRIGHT_SHA, 1, 0xFFFD, 1),
            "©",
        )
        self.assertEqual(
            _lookup_replacement(LASPIDOU_MATH_SHA, 1, 0xFFFD, 1),
            "−",
        )
        self.assertIsNone(
            _lookup_replacement("0" * 64, 1, 0xFFFD, 1)
        )

    def test_science_glyphs_require_distinct_glyph_ids(self) -> None:
        self.assertEqual(
            _lookup_replacement(BON_SCIENCE_SHA, 2, 0xFFFD, 5),
            "°",
        )
        self.assertEqual(
            _lookup_replacement(BON_SCIENCE_SHA, 3, 0xFFFD, 6),
            "×",
        )
        self.assertIsNone(
            _lookup_replacement(BON_SCIENCE_SHA, 2, 0xFFFD, 6)
        )

    def test_replacement_preserves_ordinary_unicode(self) -> None:
        self.assertEqual(
            _replace_unknowns("Müller et al.: h�1", ["−"]),
            "Müller et al.: h−1",
        )
        with self.assertRaises(LegacySymbolLayoutRecoveryError):
            _replace_unknowns("Müller", ["−"])

    def test_page_replacement_preserves_production_picture_text(self) -> None:
        baseline = (
            "<!-- memorive:page:1 -->\n\n"
            "<!-- Start of picture text -->\nchart\n"
            "100<sup>�</sup> C\n\n"
            "<!-- memorive:page:2 -->\n\nh�1\n"
        )
        self.assertEqual(
            _replace_markdown_pages(
                baseline,
                {1: ["°"], 2: ["−"]},
                pages_total=2,
            ),
            baseline.replace("�", "°", 1).replace("�", "−", 1),
        )
        with self.assertRaises(LegacySymbolLayoutRecoveryError):
            _replace_markdown_pages(
                baseline,
                {1: [], 2: ["−"]},
                pages_total=2,
            )

    def test_context_hint_is_advisory(self) -> None:
        self.assertEqual(
            _context_hint("page-footer", ["©"]),
            "copyright_or_legal_notice",
        )
        self.assertEqual(
            _context_hint("text", ["−"]),
            "scientific_unit_or_formula",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
