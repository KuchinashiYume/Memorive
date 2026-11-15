"""Regression tests for same-paper deterministic key_data re-anchoring."""
from __future__ import annotations

import copy
import sys
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

distill = import_module("evidence_extraction.distill")

PAPER_ID = "Khafipour2020"
WRONG_ID = f"{PAPER_ID}#c0011"
SPLIT_ID = f"{PAPER_ID}#c0013"
RESULT_ID = f"{PAPER_ID}#c0062"
REFERENCE_ID = f"{PAPER_ID}#c0149"
RESULT_DUPLICATE_ID = f"{PAPER_ID}#c0063"

WRONG_TEXT = (
    "The relative abundance of dominant methanogens changed during inhibition, "
    "while Methanobrevibacter and Methanosphaera that were prominent"
)
SPLIT_TEXT = (
    "in the manure feedstock increased from 17.36 to 79.45% and from 0.14 "
    "to 1.12%, respectively. Shifts in bacterial and archaeal compositions "
    "returned toward the pre-shock state."
)
RESULT_SENTENCE = (
    "The relative abundances of 16S rRNA gene from _Methanobrevibacte_ r, "
    "_Methanosphaera, and Methanocorpusculum_ increased (from 17.36 to "
    "79.45%, from 0.14 to 1.12%, and from 0.09 to 0.6%, respectively) "
    "during the inhibition stage, indicating that these organisms were "
    "introduced through the continual addition of manure, but remained "
    "inactive as no methane production was observed."
)
RESULT_TEXT = (
    "They regain their abundances following the recovery stage. "
    f"{RESULT_SENTENCE} Their relative abundances later decreased."
)

ENTRY = {
    "value": "17.36→79.45",
    "unit": "%",
    "metric": "relative abundance of Methanobrevibacter 16S rRNA gene",
    "sample": (
        "sample/control: propionic-spiked digesters | "
        "phase/time: inhibition stage | subprocess: methanogenesis | "
        "comparator: Not given"
    ),
    "stat": (
        "stat_type: range (from pre-shock to inhibition) | n: Not given | "
        "significance: Not given"
    ),
    "quote": (
        "while Methanobrevibacter and Methanosphaera that were prominent "
        "in the manure feedstock increased from 17.36 to 79.45% and from "
        "0.14 to 1.12%, respectively."
    ),
    "chunk_id": WRONG_ID,
}


def _record(
    chunk_id: str,
    text: str,
    section_path: str,
    *,
    paper_id: str = PAPER_ID,
) -> dict:
    return {
        "paper_id": paper_id,
        "chunk_id": chunk_id,
        "page_start": 1,
        "page_end": 1,
        "section_path": section_path,
        "block_type": "paragraph",
        "text": text,
    }


def _base_records() -> dict[str, dict]:
    records = (
        _record(WRONG_ID, WRONG_TEXT, "Front Matter"),
        _record(SPLIT_ID, SPLIT_TEXT, "Front Matter"),
        _record(RESULT_ID, RESULT_TEXT, "RESULTS"),
    )
    return {record["chunk_id"]: record for record in records}


class DeterministicKeyDataReanchorTest(unittest.TestCase):
    def _recover(
        self,
        entry: dict,
        records: dict[str, dict],
        *,
        paper_id: str = PAPER_ID,
    ) -> tuple[str, str] | None:
        recover = getattr(
            distill,
            "_deterministic_key_data_reanchor",
            None,
        )
        self.assertTrue(
            callable(recover),
            "_deterministic_key_data_reanchor must exist",
        )
        return recover(entry, entry["chunk_id"], records, paper_id)

    def test_actual_khafipour_entry_selects_results_not_split_abstract(self) -> None:
        recovered = self._recover(copy.deepcopy(ENTRY), _base_records())

        self.assertEqual(recovered, (RESULT_ID, RESULT_SENTENCE))
        self.assertIn(recovered[1], RESULT_TEXT)
        self.assertNotIn(recovered[1], SPLIT_TEXT)

    def test_reference_candidate_is_excluded(self) -> None:
        records = _base_records()
        records.pop(RESULT_ID)
        records[REFERENCE_ID] = _record(
            REFERENCE_ID,
            RESULT_TEXT,
            "REFERENCES",
        )

        self.assertIsNone(self._recover(copy.deepcopy(ENTRY), records))

    def test_multiple_legal_candidates_fail_closed(self) -> None:
        records = _base_records()
        records[RESULT_DUPLICATE_ID] = _record(
            RESULT_DUPLICATE_ID,
            RESULT_TEXT,
            "DISCUSSION",
        )

        self.assertIsNone(self._recover(copy.deepcopy(ENTRY), records))

    def test_numeric_unit_object_and_paper_scope_mismatches_are_rejected(self) -> None:
        cases = []

        numeric = copy.deepcopy(ENTRY)
        numeric["value"] = "17.36→79.46"
        cases.append(("numeric", numeric, _base_records(), PAPER_ID))

        unit = copy.deepcopy(ENTRY)
        unit["unit"] = "mg/L"
        cases.append(("unit", unit, _base_records(), PAPER_ID))

        object_mismatch = copy.deepcopy(ENTRY)
        object_mismatch["metric"] = "COD removal efficiency"
        cases.append(("object", object_mismatch, _base_records(), PAPER_ID))

        other_paper_records = {
            "Other2020#c0062": _record(
                "Other2020#c0062",
                RESULT_TEXT,
                "RESULTS",
                paper_id="Other2020",
            )
        }
        cases.append(("paper", copy.deepcopy(ENTRY), other_paper_records, PAPER_ID))

        for label, entry, records, paper_id in cases:
            with self.subTest(label=label):
                self.assertIsNone(
                    self._recover(entry, records, paper_id=paper_id)
                )

    def test_validator_updates_quote_and_chunk_before_model_reanchor(self) -> None:
        records = _base_records()
        texts = {
            chunk_id: record["text"]
            for chunk_id, record in records.items()
        }
        events = []

        def capture_log(module, event, **kwargs):
            events.append((module, event, kwargs))

        with (
            patch.object(distill, "log", side_effect=capture_log),
            patch.object(distill, "log_error"),
            patch.object(
                distill,
                "_reanchor_quote",
                side_effect=AssertionError("model reanchor must not run"),
            ) as reanchor,
        ):
            output = distill._validate_key_data(
                {"key_data": [copy.deepcopy(ENTRY)]},
                texts,
                PAPER_ID,
                "en",
                records_by_id=records,
            )

        self.assertEqual(output[0]["chunk_id"], RESULT_ID)
        self.assertEqual(output[0]["quote"], RESULT_SENTENCE)
        self.assertIn(output[0]["quote"], texts[output[0]["chunk_id"]])
        reanchor.assert_not_called()
        recovery_events = [
            event
            for _, event, _ in events
            if event == "key_data_deterministic_reanchor"
        ]
        self.assertEqual(
            recovery_events,
            ["key_data_deterministic_reanchor"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
