"""Regression tests for narrowly canonicalizing current-paper bare chunk ids."""
from __future__ import annotations

import sys
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from evidence_extraction.errors import KeyDataInvalidError

distill = import_module("evidence_extraction.distill")


PAPER_ID = "Delee1998"
FULL_ID = "Delee1998#c0076"
QUOTE = (
    "A full-scale anaerobic plant with COD removal of 80% is in operation "
    "at a Spanish wool combing plant."
)


def _item(chunk_id: str) -> dict:
    return {
        "value": "80",
        "unit": "%",
        "metric": "COD removal",
        "sample": (
            "sample/control: full-scale anaerobic plant | phase/time: Not given | "
            "subprocess: overall | comparator: external source"
        ),
        "stat": "stat_type: single value | n: Not given | significance: Not given",
        "quote": QUOTE,
        "chunk_id": chunk_id,
    }


class ChunkIdCanonicalizationTest(unittest.TestCase):
    def _validate(self, chunk_id: str, chunk_texts: dict | None = None) -> list:
        with (
            patch.object(distill, "log"),
            patch.object(distill, "log_error"),
        ):
            return distill._validate_key_data(
                {"key_data": [_item(chunk_id)]},
                chunk_texts or {FULL_ID: QUOTE},
                PAPER_ID,
                "en",
            )

    def test_current_paper_bare_id_is_canonicalized(self) -> None:
        result = self._validate("c0076")
        self.assertEqual(result[0]["chunk_id"], FULL_ID)

    def test_existing_full_id_is_unchanged(self) -> None:
        result = self._validate(FULL_ID)
        self.assertEqual(result[0]["chunk_id"], FULL_ID)

    def test_other_paper_full_id_is_rejected(self) -> None:
        with self.assertRaises(KeyDataInvalidError):
            self._validate("OtherPaper#c0076")

    def test_unknown_current_paper_bare_id_is_rejected(self) -> None:
        with self.assertRaises(KeyDataInvalidError):
            self._validate("c9999")

    def test_arbitrary_string_ending_in_valid_suffix_is_rejected(self) -> None:
        with self.assertRaises(KeyDataInvalidError):
            self._validate("prefix-c0076")

    def test_bare_id_is_not_resolved_against_another_paper(self) -> None:
        with self.assertRaises(KeyDataInvalidError):
            self._validate("c0076", {"OtherPaper#c0076": QUOTE})


if __name__ == "__main__":
    unittest.main(verbosity=2)
