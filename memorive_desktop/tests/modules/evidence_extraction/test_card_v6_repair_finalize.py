from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from memorive.documents.card_distillation.card import (
    CONTENT_FIELDS,
    build_card,
    finalize_card_after_repair,
    write_card,
)
from memorive.documents.card_distillation.config import CARD_SCHEMA_VERSION


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---", 2)[1])


class CardV6RepairFinalizeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="card_v6_repair_")
        self.root = Path(self.temp.name)
        self.fields = {
            "research_question": "Which treatment works?",
            "research_object": ["dye wastewater"],
            "method": ["Five reactors", "PANDAseq processed reads"],
            "key_results": "Removal was 77.20 ± 2.97%.",
            "author_conclusion": "The treatment was effective.",
            "boundary_conditions": ["Mesophilic operation"],
        }
        self.by_field = {
            field: [
                {
                    "chunk_id": "Paper#c1",
                    "quote": None,
                    "section_page": "p1",
                }
            ]
            for field in CONTENT_FIELDS
        }
        self.fm = build_card(
            paper_id="Paper",
            title="Fixture",
            data_ownership="self",
            fields=self.fields,
            by_field=self.by_field,
            distill_model="deepseek-v4-pro",
            distilled_at="2026-07-13T12:00+08:00",
            schema_version=CARD_SCHEMA_VERSION,
            key_data=[],
        )
        self.card = self.root / "[Card][Paper] Fixture.md"
        write_card(self.card, self.fm)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_new_cards_start_as_v6_pending_review_without_omissions(self) -> None:
        self.assertEqual(CARD_SCHEMA_VERSION, 6)
        self.assertEqual(self.fm["schema_version"], 6)
        self.assertEqual(self.fm["completion_status"], "pending_review")
        self.assertEqual(self.fm["omissions"], [])

    def test_finalize_can_remove_scalar_and_list_item_with_tombstones(self) -> None:
        original_body = self.card.read_text(encoding="utf-8").split("---", 2)[2]
        repaired_fields = dict(self.fields)
        repaired_fields["method"] = ["PANDAseq processed reads"]
        repaired_fields["key_results"] = None
        omissions = [
            {
                "paper_id": "Paper",
                "location": "by_field.method",
                "item_index": 0,
                "disposition": "source_conflict",
                "removed_value": "Five reactors",
                "related_anchors": self.by_field["method"],
                "attempts": {
                    "sonnet_initial_review_calls": 1,
                    "deepseek_repair_calls": 1,
                    "sonnet_targeted_review_calls": 0,
                },
                "initial_report_id": "Paper-EVIDENCE_REVIEW-initial",
                "final_report_id": None,
            },
            {
                "paper_id": "Paper",
                "location": "by_field.key_results",
                "item_index": 0,
                "disposition": "source_conflict",
                "removed_value": "Removal was 77.20 ± 2.97%.",
                "related_anchors": self.by_field["key_results"],
                "attempts": {
                    "sonnet_initial_review_calls": 1,
                    "deepseek_repair_calls": 1,
                    "sonnet_targeted_review_calls": 0,
                },
                "initial_report_id": "Paper-EVIDENCE_REVIEW-initial",
                "final_report_id": None,
            },
        ]

        finalize_card_after_repair(
            self.card,
            fields=repaired_fields,
            by_field=self.by_field,
            key_data=[],
            field_credibility=self.fm["field_credibility"],
            omissions=omissions,
            completion_status="passed_with_omissions",
        )

        final = _frontmatter(self.card)
        self.assertEqual(final["schema_version"], 6)
        self.assertEqual(final["completion_status"], "passed_with_omissions")
        self.assertEqual(final["method"], ["PANDAseq processed reads"])
        self.assertIsNone(final["key_results"])
        self.assertEqual(final["omissions"], omissions)
        self.assertEqual(
            self.card.read_text(encoding="utf-8").split("---", 2)[2],
            original_body,
        )

    def test_finalize_rejects_status_omission_mismatch_without_touching_card(self) -> None:
        before = self.card.read_bytes()
        with self.assertRaises(ValueError):
            finalize_card_after_repair(
                self.card,
                fields=self.fields,
                by_field=self.by_field,
                key_data=[],
                field_credibility=self.fm["field_credibility"],
                omissions=[],
                completion_status="passed_with_omissions",
            )
        self.assertEqual(self.card.read_bytes(), before)

    def test_finalize_rejects_incomplete_tombstone_without_touching_card(self) -> None:
        before = self.card.read_bytes()
        incomplete = [{
            "location": "by_field.method",
            "item_index": 0,
            "disposition": "source_conflict",
            "removed_value": "Five reactors",
            "initial_report_id": "Paper-EVIDENCE_REVIEW-initial",
            "final_report_id": None,
        }]
        with self.assertRaises(ValueError):
            finalize_card_after_repair(
                self.card,
                fields=self.fields,
                by_field=self.by_field,
                key_data=[],
                field_credibility=self.fm["field_credibility"],
                omissions=incomplete,
                completion_status="passed_with_omissions",
            )
        self.assertEqual(self.card.read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
