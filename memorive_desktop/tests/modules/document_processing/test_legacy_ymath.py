# -*- coding: utf-8 -*-
"""Unit regressions for the audited legacy YMath recovery path."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = (Path(__file__).resolve().parents[3] / "source")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memorive.documents.processing.engines.legacy_ymath import (  # noqa: E402
    LegacyYMathRecoveryError,
    _render_line,
)


class _LineBuilder:
    def __init__(self) -> None:
        self.spans: list[dict] = []
        self.trace: dict[tuple[str, tuple[float, float]], tuple[int, int]] = {}
        self._x = 0.0

    def add(
        self,
        font: str,
        values: list[tuple[str, bool, int | None, int | None]],
    ) -> None:
        chars = []
        for value, synthetic, glyph_id, trace_unicode in values:
            origin = (self._x, 0.0)
            char = {"c": value, "origin": origin, "synthetic": synthetic}
            chars.append(char)
            if glyph_id is not None:
                self.trace[(font, origin)] = (
                    ord(value) if trace_unicode is None else trace_unicode,
                    glyph_id,
                )
            self._x += 1.0
        self.spans.append({"font": font, "chars": chars})

    def line(self) -> dict:
        return {"spans": self.spans}


def _plain(value: str) -> list[tuple[str, bool, None, None]]:
    return [(char, False, None, None) for char in value]


class LegacyYMathRenderTest(unittest.TestCase):
    def test_ligature_decimal_and_superscripts_are_explicit(self) -> None:
        builder = _LineBuilder()
        builder.add("YMath-Pack-One", _plain("signi"))
        builder.add(
            "YMath-Pack-One",
            [("Ð", False, 82, 0x00D0)],
        )
        builder.add("YMath-Pack-One", _plain("cant 0"))
        builder.add(
            "YMath-Pack-Two",
            [("É", False, 61, 0x00C9)],
        )
        builder.add("YMath-Pack-One", _plain("63 kg m"))
        builder.add(
            "YMath-Pack-FIve",
            [("~", False, 15, 0x007E), ("3", False, None, None)],
        )
        builder.add("YMath-Pack-One", _plain(" day"))
        builder.add(
            "YMath-Pack-FIve",
            [("~", False, 15, 0x007E), ("1", False, None, None)],
        )

        result = _render_line(builder.line(), builder.trace, page_number=1)

        self.assertEqual(
            result.text,
            "significant 0.63 kg m^−3 day^−1",
        )
        self.assertEqual(result.mapped_chars, 4)
        self.assertEqual(result.superscript_runs, 2)
        self.assertEqual(result.superscript_chars, 4)

    def test_pack_five_unit_exponents_and_baseline_symbols_are_distinct(
        self,
    ) -> None:
        builder = _LineBuilder()
        builder.add("YMath-Pack-One", _plain("below "))
        builder.add(
            "YMath-Pack-FIve",
            [("[", False, 12, 0x005B)],
        )
        builder.add("YMath-Pack-One", _plain("450 dm"))
        builder.add("YMath-Pack-FIve", _plain("3"))
        builder.add("YMath-Pack-One", _plain(" and m"))
        builder.add(
            "YMath-Pack-FIve",
            [("~", False, 15, 0x007E), ("3", False, None, None)],
        )
        builder.add("YMath-Pack-One", _plain(" plus "))
        builder.add(
            "YMath-Pack-FIve",
            [("]", False, 13, 0x005D)],
        )

        result = _render_line(builder.line(), builder.trace, page_number=1)

        self.assertEqual(result.text, "below −450 dm^3 and m^−3 plus +")
        self.assertEqual(result.superscript_runs, 2)
        self.assertEqual(result.superscript_chars, 3)

    def test_pack_five_formula_reference_and_isotope_stay_baseline(
        self,
    ) -> None:
        builder = _LineBuilder()
        builder.add("YMath-Pack-One", _plain("H"))
        builder.add("YMath-Pack-FIve", _plain("2"))
        builder.add("YMath-Pack-One", _plain("O"))
        builder.add("YMath-Pack-FIve", _plain("2"))
        builder.add("YMath-Pack-One", _plain(" reference "))
        builder.add("YMath-Pack-FIve", _plain("137"))
        builder.add("YMath-Pack-One", _plain(" isotope "))
        builder.add("YMath-Pack-FIve", _plain("14"))
        builder.add("YMath-Pack-One", _plain("C"))

        result = _render_line(builder.line(), builder.trace, page_number=1)

        self.assertEqual(result.text, "H2O2 reference 137 isotope 14C")
        self.assertEqual(result.superscript_runs, 0)
        self.assertEqual(result.superscript_chars, 0)

    def test_pack_five_footnote_label_is_explicit(self) -> None:
        builder = _LineBuilder()
        builder.add("YMath-Pack-FIve", _plain("a"))
        builder.add("YMath-Pack-One", _plain(" no exact concentrations"))

        result = _render_line(builder.line(), builder.trace, page_number=4)

        self.assertEqual(result.text, "^a no exact concentrations")
        self.assertEqual(result.superscript_runs, 1)
        self.assertEqual(result.superscript_chars, 1)

    def test_zmath_charges_and_footnotes_are_explicit(self) -> None:
        builder = _LineBuilder()
        builder.add("YMath-Pack-One", _plain("NH"))
        builder.add("YMath-Pack-FIve", _plain("4"))
        builder.add(
            "ZMath-Pack-Six",
            [("`", False, 1, 0x0060)],
        )
        builder.add("YMath-Pack-One", _plain(" Cr"))
        builder.add("YMath-Pack-FIve", _plain("3"))
        builder.add(
            "ZMath-Pack-Six",
            [("`", False, 1, 0x0060)],
        )
        builder.add("YMath-Pack-One", _plain(" ratio 1:1000"))
        builder.add(
            "ZMath-Pack-Six",
            [("a", False, 2, 0x0061)],
        )
        builder.add("YMath-Pack-One", _plain(" note "))
        builder.add(
            "ZMath-Pack-Six",
            [("b", False, 3, 0x0062)],
        )

        result = _render_line(builder.line(), builder.trace, page_number=9)

        self.assertEqual(
            result.text,
            "NH4^+ Cr^3+ ratio 1:1000^a note ^b",
        )
        self.assertEqual(result.superscript_runs, 4)
        self.assertEqual(result.superscript_chars, 5)

    def test_pack_five_audited_anion_charges_are_explicit(self) -> None:
        builder = _LineBuilder()
        builder.add("YMath-Pack-One", _plain("S"))
        builder.add(
            "YMath-Pack-FIve",
            [("2", False, None, None), ("~", False, 15, 0x007E)],
        )
        builder.add("YMath-Pack-One", _plain(" SO"))
        builder.add(
            "YMath-Pack-FIve",
            [
                ("4", False, None, None),
                ("2", False, None, None),
                ("~", False, 15, 0x007E),
            ],
        )
        builder.add("YMath-Pack-One", _plain(" PO"))
        builder.add(
            "YMath-Pack-FIve",
            [("4", False, None, None), ("~", False, 15, 0x007E)],
        )

        result = _render_line(builder.line(), builder.trace, page_number=1)

        self.assertEqual(result.text, "S^2− SO4^2− PO4^−")
        self.assertEqual(result.superscript_runs, 3)
        self.assertEqual(result.superscript_chars, 5)

    def test_combining_marks_suppress_only_synthetic_gap(self) -> None:
        builder = _LineBuilder()
        builder.add("YMath-Pack-One", _plain("Biolo"))
        builder.add(
            "YMath-Pack-One",
            [("\u008d", False, 76, 0xFFFD)],
        )
        builder.add("YMath-Pack-One", [(" ", True, None, None)])
        builder.add("YMath-Pack-One", _plain("gica e Qu"))
        builder.add(
            "YMath-Pack-One",
            [("•", False, 78, 0x2022), ("\u008d", False, 76, 0xFFFD)],
        )
        builder.add("YMath-Pack-One", _plain("mica Remoc"))
        builder.add(
            "YMath-Pack-One",
            [("Ó", False, 84, 0x00D3)],
        )
        builder.add("YMath-Pack-One", [(" ", True, None, None)])
        builder.add("YMath-Pack-One", _plain("a"))
        builder.add(
            "YMath-Pack-Four",
            [("8", False, 1, 0x0038)],
        )
        builder.add("YMath-Pack-One", [(" ", True, None, None)])
        builder.add("YMath-Pack-One", _plain("o"))

        result = _render_line(builder.line(), builder.trace, page_number=1)

        self.assertEqual(
            result.text,
            "Biológica e Química Remoção",
        )
        self.assertEqual(result.suppressed_accent_spaces, 3)

    def test_wrong_glyph_id_fails_closed(self) -> None:
        builder = _LineBuilder()
        builder.add(
            "YMath-Pack-One",
            [("Ð", False, 999, 0x00D0)],
        )

        with self.assertRaisesRegex(
            LegacyYMathRecoveryError,
            "glyph id 999 != 82",
        ):
            _render_line(builder.line(), builder.trace, page_number=4)

    def test_unknown_superscript_character_fails_closed(self) -> None:
        builder = _LineBuilder()
        builder.add("YMath-Pack-FIve", _plain("z"))

        with self.assertRaisesRegex(
            LegacyYMathRecoveryError,
            "unexpected superscript character",
        ):
            _render_line(builder.line(), builder.trace, page_number=2)

if __name__ == "__main__":
    unittest.main(verbosity=2)
