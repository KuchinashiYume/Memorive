from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from memorive.documents.card_distillation.card import CONTENT_FIELDS, build_card, write_card
from memorive.documents.card_distillation.config import CARD_SCHEMA_VERSION
import memorive.documents.card_review as card_review
from memorive.documents.card_review import repair_flow
from memorive.documents.card_review.repair_flow import repair_and_admit
from memorive.models.gateway.errors import DependencyError


def _frontmatter(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])


def _batch_response(
    checked_items: dict[str, list[int]],
    violations: list[dict] | None = None,
) -> str:
    violations = violations or []
    violating = {
        (row["location"], row["item_index"])
        for row in violations
    }
    locations = list(checked_items)
    return json.dumps(
        {
            "checked_locations": locations,
            "checked_items": [
                {"location": location, "item_indexes": indexes}
                for location, indexes in checked_items.items()
            ],
            "item_reviews": [
                {
                    "location": location,
                    "item_index": item_index,
                    "status": "violation" if (location, item_index) in violating else "supported",
                }
                for location, indexes in checked_items.items()
                for item_index in indexes
            ],
            "violations": violations,
        },
        ensure_ascii=False,
    )


class RepairFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="m6_repair_flow_")
        self.root = Path(self.temp.name)
        self.env_before = {
            name: os.environ.get(name)
            for name in (
                "MEMORIVE_EVIDENCE_REVIEW_REPORT_DIR",
                "MEMORIVE_EVIDENCE_REVIEW_REPAIR_DIR",
                "MEMORIVE_KNOWLEDGE_ADMISSION_LOG_DIR",
                "MEMORIVE_RUNTIME_LOG_LOG_DIR",
            )
        }
        os.environ["MEMORIVE_EVIDENCE_REVIEW_REPORT_DIR"] = str(self.root / "ops" / "evidence_review-reports")
        os.environ["MEMORIVE_EVIDENCE_REVIEW_REPAIR_DIR"] = str(self.root / "ops" / "evidence_review-repairs")
        os.environ["MEMORIVE_KNOWLEDGE_ADMISSION_LOG_DIR"] = str(self.root / "ops" / "knowledge_admission")
        os.environ["MEMORIVE_RUNTIME_LOG_LOG_DIR"] = str(self.root / "ops" / "runtime_log")
        self.card, self.initial_items = self._write_fixture()

    def tearDown(self) -> None:
        for name, value in self.env_before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.temp.cleanup()

    def _write_fixture(self) -> tuple[Path, dict[str, list[int]]]:
        folder = self.root / "library" / "[Paper] Fixture"
        folder.mkdir(parents=True)
        fields = {
            "research_question": "Which treatment works?",
            "research_object": ["dye wastewater"],
            "method": ["Five reactors", "PANDAseq processed reads"],
            "key_results": "Removal was 77%.",
            "author_conclusion": "The treatment was effective.",
            "boundary_conditions": ["Mesophilic operation"],
        }
        by_field = {
            field: [
                {
                    "chunk_id": "Paper#c1",
                    "quote": None,
                    "section_page": "p1",
                }
            ]
            for field in CONTENT_FIELDS
        }
        fm = build_card(
            paper_id="Paper",
            title="Fixture",
            data_ownership="self",
            fields=fields,
            by_field=by_field,
            distill_model="deepseek-v4-pro",
            distilled_at="2026-07-13T12:00+08:00",
            schema_version=CARD_SCHEMA_VERSION,
            key_data=[
                {
                    "value": "11",
                    "unit": "%",
                    "metric": "verified metric",
                    "sample": "sample/control: test | phase/time: end | subprocess: whole | comparator: control",
                    "stat": "stat_type: point | n: Not given | significance: Not given",
                    "quote": "The verified metric was 11%.",
                    "chunk_id": "Paper#c1",
                    "needs_review": True,
                }
            ],
        )
        card = folder / "[Card][Paper] Fixture.md"
        write_card(card, fm)
        chunks = folder / "[Chunks][Paper] Fixture.jsonl"
        chunks.write_text(
            "\n".join(
                json.dumps(row, ensure_ascii=False)
                for row in (
                    {
                        "paper_id": "Paper",
                        "chunk_id": "Paper#c1",
                        "text": "The methods used eight reactors under mesophilic operation. The verified metric was 11%.",
                    },
                    {
                        "paper_id": "Paper",
                        "chunk_id": "Paper#c2",
                        "text": "PANDAseq was used to assemble paired-end reads.",
                    },
                )
            )
            + "\n",
            encoding="utf-8",
        )
        checked_items = {
            "by_field.research_question": [0],
            "by_field.research_object": [0],
            "by_field.method": [0, 1],
            "by_field.key_results": [0],
            "by_field.author_conclusion": [0],
            "by_field.boundary_conditions": [0],
            "key_data[0]": [0],
        }
        return card, checked_items

    @staticmethod
    def _initial_violations() -> list[dict]:
        return [
            {
                "location": "by_field.method",
                "item_index": 0,
                "rule": "R4",
                "card_quote": "Five reactors",
                "source_quote": "eight reactors",
                "evidence_relation": "direct_conflict",
            },
            {
                "location": "by_field.method",
                "item_index": 1,
                "rule": "R8",
                "card_quote": "PANDAseq",
                "source_quote": None,
                "evidence_relation": "missing_from_given_chunks",
            },
        ]

    def test_public_grade_and_admit_is_the_repair_flow(self) -> None:
        self.assertIs(card_review.grade_and_admit, repair_and_admit)
        self.assertIsNot(card_review.legacy_grade_and_admit, repair_and_admit)

    @staticmethod
    def _repair_payload() -> dict:
        return {
            "repairs": [
                {
                    "location": "by_field.method",
                    "item_index": 0,
                    "action": "drop",
                    "replacement": None,
                    "anchors": [],
                    "disposition": "source_conflict",
                    "reason": "The source contains an unresolved reactor-count conflict.",
                },
                {
                    "location": "by_field.method",
                    "item_index": 1,
                    "action": "reanchor",
                    "replacement": None,
                    "anchors": [
                        {
                            "chunk_id": "Paper#c2",
                            "quote": "PANDAseq was used to assemble paired-end reads.",
                            "section_page": "p2",
                        }
                    ],
                    "disposition": None,
                    "reason": "The claim is supported in another same-paper chunk.",
                },
            ]
        }


if __name__ == "__main__":
    unittest.main(verbosity=2)
