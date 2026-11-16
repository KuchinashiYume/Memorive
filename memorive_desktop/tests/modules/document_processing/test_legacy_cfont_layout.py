# -*- coding: utf-8 -*-
"""Regressions for provenance-only legacy C-font Layout recovery."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = (Path(__file__).resolve().parents[3] / "source")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memorive.documents.processing.engines.legacy_cfont_layout import (  # noqa: E402
    LegacyCFontLayoutRecoveryError,
    LegacyCFontRecoveryError,
    _context_hint,
    _is_auxiliary_layout_line,
    _protect_markdown_literal,
    _render_line,
    _validate_picture_payload,
)


class LegacyCFontLayoutProvenanceTest(unittest.TestCase):
    def test_normal_unicode_is_context_only_never_a_legacy_signal(self) -> None:
        target = {
            "class": "text",
            "line": {
                "spans": [
                    {
                        "font": "Ordinary-Unicode-Font",
                        "text": "Müller cited Gonçalves",
                    }
                ]
            },
        }

        self.assertFalse(
            _is_auxiliary_layout_line(target, frozenset({"AdvPSTim"}))
        )
        self.assertEqual(
            _context_hint(target, "Müller cited Gonçalves"),
            "multilingual_name_term_or_prose_uncertain",
        )

    def test_context_hint_distinguishes_likely_unicode_sources(self) -> None:
        text_target = {"class": "text", "line": {"spans": []}}

        self.assertEqual(
            _context_hint(
                text_target,
                "* Corresponding author: Jürgen Müller",
            ),
            "author_or_byline",
        )
        self.assertEqual(
            _context_hint(
                text_target,
                "Müller et al. (2020) reported the same trend.",
            ),
            "narrative_citation",
        )
        self.assertEqual(
            _context_hint(
                text_target,
                "The fitted coefficient was β = 0.42.",
            ),
            "scientific_symbol_or_variable",
        )
        self.assertEqual(
            _context_hint(
                text_target,
                "The über-treatment group remained stable.",
            ),
            "multilingual_name_term_or_prose_uncertain",
        )
        self.assertEqual(
            _context_hint(
                {"class": "list-item", "line": {"spans": []}},
                "Müller, J. (2020). Study title.",
            ),
            "bibliography_or_reference",
        )

    def test_auxiliary_line_requires_only_replacements_from_audited_font(
        self,
    ) -> None:
        audited = frozenset({"AdvPSTim"})
        exact_shadow = {
            "line": {
                "spans": [{"font": "AdvPSTim", "text": "\ufffd"}]
            }
        }
        ordinary = {
            "line": {
                "spans": [
                    {"font": "Ordinary-Unicode-Font", "text": "\ufffd"}
                ]
            }
        }
        mixed_content = {
            "line": {
                "spans": [{"font": "AdvPSTim", "text": "name \ufffd"}]
            }
        }

        self.assertTrue(_is_auxiliary_layout_line(exact_shadow, audited))
        self.assertFalse(_is_auxiliary_layout_line(ordinary, audited))
        self.assertFalse(_is_auxiliary_layout_line(mixed_content, audited))

    def test_text_footnote_star_is_escaped_without_deleting_it(self) -> None:
        self.assertEqual(
            _protect_markdown_literal(
                {"class": "text"},
                "* Corresponding author",
            ),
            r"\* Corresponding author",
        )
        self.assertEqual(
            _protect_markdown_literal(
                {"class": "list-item"},
                "* real list item",
            ),
            "* real list item",
        )

    def test_picture_payload_requires_exact_replacement_count(self) -> None:
        payload = (
            "\n\n<!-- Start of picture text -->\n"
            "\ufffd\ufffd\ufffd<br><!-- End of picture text -->\n\n"
        )
        _validate_picture_payload(payload, expected_replacements=3)
        with self.assertRaises(LegacyCFontLayoutRecoveryError):
            _validate_picture_payload(payload, expected_replacements=2)

    def test_render_line_requires_matching_trace_origin(self) -> None:
        line = {
            "spans": [
                {
                    "font": "AdvPSTim",
                    "chars": [
                        {
                            "c": "\x01",
                            "origin": (10.0, 20.0),
                        }
                    ],
                }
            ]
        }
        relations: set[tuple[str, int, int, int]] = set()

        result = _render_line(
            line,
            {
                ("AdvPSTim", (10.0, 20.0)): (0xFFFD, 26),
            },
            {"AdvPSTim": {1: "A"}},
            relations,
            page_number=1,
        )

        self.assertEqual(result.text, "A")
        self.assertEqual(result.mapped_chars, 1)
        self.assertEqual(relations, {("AdvPSTim", 1, 0xFFFD, 26)})
        with self.assertRaises(LegacyCFontRecoveryError):
            _render_line(
                line,
                {},
                {"AdvPSTim": {1: "A"}},
                set(),
                page_number=1,
            )

    def test_ordinary_unicode_passes_through_without_mapping(self) -> None:
        line = {
            "spans": [
                {
                    "font": "Ordinary-Unicode-Font",
                    "chars": [{"c": "ü", "origin": (1.0, 2.0)}],
                }
            ]
        }
        relations: set[tuple[str, int, int, int]] = set()

        result = _render_line(
            line,
            {},
            {"AdvPSTim": {1: "A"}},
            relations,
            page_number=1,
        )

        self.assertEqual(result.text, "ü")
        self.assertEqual(result.mapped_chars, 0)
        self.assertEqual(relations, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
