# -*- coding: utf-8 -*-
"""Regressions for provenance-based Layout recovery."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = (Path(__file__).resolve().parents[3] / "source")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from document_processing.engines.legacy_ymath_layout import (  # noqa: E402
    _context_hint,
    _is_auxiliary_layout_line,
    _render_text_target,
    _serialize_markdown,
)


class LegacyYMathLayoutProvenanceTest(unittest.TestCase):
    def test_text_target_keeps_all_fragments_in_geometric_order(self) -> None:
        target_key = ("line", 8, 0)
        records = [
            {
                "target_key": target_key,
                "rect": (168.9, 0.0, 283.8, 8.0),
                "order": 40,
                "result": SimpleNamespace(text="anaeróbia de corantes têxteis."),
            },
            {
                "target_key": target_key,
                "rect": (105.1, 0.0, 126.6, 8.0),
                "order": 39,
                "result": SimpleNamespace(text="I. M.,"),
            },
            {
                "target_key": target_key,
                "rect": (44.8, 0.0, 102.0, 8.0),
                "order": 41,
                "result": SimpleNamespace(text="29. Gonçalves,"),
            },
            {
                "target_key": target_key,
                "rect": (129.7, 0.0, 165.7, 8.0),
                "order": 42,
                "result": SimpleNamespace(text="Remoção"),
            },
            {
                "target_key": ("line", 99, 0),
                "rect": (0.0, 0.0, 1.0, 1.0),
                "order": 0,
                "result": SimpleNamespace(text="outside"),
            },
        ]

        self.assertEqual(
            _render_text_target(records, target_key),
            "29. Gonçalves, I. M., Remoção anaeróbia de corantes têxteis.",
        )


    def test_normal_unicode_letter_is_never_a_legacy_signal(self) -> None:
        target = {
            "class": "text",
            "line": {
                "spans": [
                    {
                        "font": "Ordinary-Unicode-Font",
                        "text": "Müller et al. discussed Grüttner's result",
                    }
                ]
            },
        }

        self.assertFalse(_is_auxiliary_layout_line(target))
        self.assertEqual(
            _context_hint(target, "Müller et al."),
            "multilingual_name_term_or_citation",
        )

    def test_same_raw_codepoint_depends_on_font_provenance(self) -> None:
        ordinary = {
            "line": {
                "spans": [
                    {"font": "Ordinary-Unicode-Font", "text": "ü"}
                ]
            }
        }
        audited_legacy = {
            "line": {
                "spans": [
                    {"font": "YMath-Pack-One", "text": "ü"}
                ]
            }
        }

        self.assertFalse(_is_auxiliary_layout_line(ordinary))
        self.assertTrue(_is_auxiliary_layout_line(audited_legacy))

    def test_detached_superscript_line_is_auxiliary(self) -> None:
        target = {
            "line": {
                "spans": [
                    {"font": "YMath-Pack-Two", "text": "É"},
                    {"font": "YMath-Pack-FIve", "text": "~3"},
                ]
            }
        }

        self.assertTrue(_is_auxiliary_layout_line(target))


if __name__ == "__main__":
    unittest.main(verbosity=2)
