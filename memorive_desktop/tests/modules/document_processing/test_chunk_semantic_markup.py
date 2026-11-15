# -*- coding: utf-8 -*-
"""DocumentChunk semantic-markup regression tests for Core Final second round."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from document_processing import document_chunk_chunk  # noqa: E402


YANG_FRAGMENT = (
    "The results indicated that the foulants in the surface fouling layer had "
    "greater potential to cause membrane fouling (2.56 × 10<sup>14</sup> "
    "–3.32 × 10<sup>15</sup> m<sup>−1</sup> /kg). Especially, the SFR "
    "value of RETRIEVAL_WEIGHTING (3.56 × 10<sup>15</sup> m<sup>−1</sup> /kg) was "
    "significantly higher than at other stages."
)
YANG_EXPECTED = (
    "The results indicated that the foulants in the surface fouling layer had "
    "greater potential to cause membrane fouling (2.56 × 10^14 "
    "–3.32 × 10^15 m^−1 /kg). Especially, the SFR "
    "value of RETRIEVAL_WEIGHTING (3.56 × 10^15 m^−1 /kg) was "
    "significantly higher than at other stages."
)


class ChunkSemanticMarkupTest(unittest.TestCase):
    def test_actual_yang_exponent_boundaries_are_preserved(self) -> None:
        actual = document_chunk_chunk._strip(YANG_FRAGMENT, keep_br=False)
        self.assertEqual(actual, YANG_EXPECTED)
        self.assertNotIn("× 1015", actual)
        self.assertNotIn("m−1", actual)

    def test_numeric_superscript_preserves_boundary_whitespace(self) -> None:
        self.assertEqual(
            document_chunk_chunk._strip("m<sup>−1 </sup>and", keep_br=False),
            "m^−1 and",
        )
        self.assertEqual(
            document_chunk_chunk._strip("area<sup> 2</sup>", keep_br=False),
            "area ^2",
        )
        self.assertEqual(
            document_chunk_chunk._strip("10<sup>1 5</sup>", keep_br=False),
            "101 5",
        )

    def test_table_path_preserves_br_and_exponents(self) -> None:
        self.assertEqual(
            document_chunk_chunk._strip(
                "value=10<sup>2</sup><br>m<sup>−1</sup>",
                keep_br=True,
            ),
            "value=10^2<br>m^−1",
        )

    def test_nonsemantic_html_behavior_is_unchanged(self) -> None:
        self.assertEqual(
            document_chunk_chunk._strip("the 1<sup>st</sup> result", keep_br=False),
            "the 1st result",
        )
        self.assertEqual(
            document_chunk_chunk._strip(
                "H<sub>2</sub>O <i>in situ</i>",
                keep_br=False,
            ),
            "H2O in situ",
        )
        self.assertEqual(
            document_chunk_chunk._strip("first<br>second", keep_br=False),
            "first second",
        )
        self.assertEqual(
            document_chunk_chunk._strip("first<br>second", keep_br=True),
            "first<br>second",
        )

    def test_plain_math_comparisons_are_not_tags(self) -> None:
        source = "COD < 100 mg/L and ratio 5<10 while flux > 40 kPa"
        self.assertEqual(document_chunk_chunk._strip(source, keep_br=False), source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
