"""Regression tests for two-call, whole-card EVIDENCE_REVIEW transcription verification."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from memorive.documents.card_review import grade_and_admit, transcription
from memorive.documents.card_states import DecisionError


def _batch_response(
    locations: list[str],
    violations: list[dict] | None = None,
    *,
    checked_items: list[dict] | None = None,
    item_reviews: list[dict] | None = None,
) -> str:
    violations = violations or []
    coverage = checked_items or [
        {"location": location, "item_indexes": [0]}
        for location in locations
    ]
    violating_items = {
        (row["location"], row.get("item_index", 0))
        for row in violations
    }
    return json.dumps(
        {
            "checked_locations": locations,
            "checked_items": coverage,
            "item_reviews": item_reviews or [
                {
                    "location": row["location"],
                    "item_index": item_index,
                    "status": (
                        "violation"
                        if (row["location"], item_index) in violating_items
                        else "supported"
                    ),
                }
                for row in coverage
                for item_index in row["item_indexes"]
            ],
            "violations": violations,
        },
        ensure_ascii=False,
    )


class BatchTranscriptionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="m6_batch_")
        self.root = Path(self.temp.name)
        self.previous_report_dir = os.environ.get("MEMORIVE_EVIDENCE_REVIEW_REPORT_DIR")
        self.previous_log_dir = os.environ.get("MEMORIVE_RUNTIME_LOG_LOG_DIR")
        os.environ["MEMORIVE_EVIDENCE_REVIEW_REPORT_DIR"] = str(self.root / "reports")
        os.environ["MEMORIVE_RUNTIME_LOG_LOG_DIR"] = str(self.root / "runtime_log")
        self.card, self.locations = self._write_sixteen_subject_fixture()

    def tearDown(self) -> None:
        if self.previous_report_dir is None:
            os.environ.pop("MEMORIVE_EVIDENCE_REVIEW_REPORT_DIR", None)
        else:
            os.environ["MEMORIVE_EVIDENCE_REVIEW_REPORT_DIR"] = self.previous_report_dir
        if self.previous_log_dir is None:
            os.environ.pop("MEMORIVE_RUNTIME_LOG_LOG_DIR", None)
        else:
            os.environ["MEMORIVE_RUNTIME_LOG_LOG_DIR"] = self.previous_log_dir
        self.temp.cleanup()

    def _write_sixteen_subject_fixture(self) -> tuple[Path, list[str]]:
        folder = self.root / "library" / "[Batch] sixteen subjects"
        folder.mkdir(parents=True)
        locations = [f"by_field.field_{index:02d}" for index in range(16)]
        field_lines = []
        anchor_lines = []
        for index in range(16):
            name = f"field_{index:02d}"
            value = "Five reactors" if index == 0 else f"Control statement {index:02d}"
            field_lines.extend([f"{name}:", f"- {value}"])
            anchor_lines.extend(
                [
                    f"    {name}:",
                    "    - chunk_id: Batch#c1",
                    "      quote: null",
                    "      section_page: p1",
                ]
            )
        card = folder / "[Card][Batch] sixteen subjects.md"
        card.write_text(
            "---\n"
            "title: sixteen subject batch fixture\n"
            + "\n".join(field_lines)
            + "\nsource_anchor:\n"
            "  paper_id: Batch\n"
            "  by_field:\n"
            + "\n".join(anchor_lines)
            + "\ndata_ownership: self\n"
            "distill_model: deepseek-v4-pro\n"
            "review_status: pending\n"
            "---\nfixture\n",
            encoding="utf-8",
        )
        chunks = folder / "[Chunks][Batch] sixteen subjects.jsonl"
        chunks.write_text(
            json.dumps(
                {
                    "paper_id": "Batch",
                    "chunk_id": "Batch#c1",
                    "text": "The methods report eight reactors.",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return card, locations


    def test_final_review_can_add_missed_violation(self) -> None:
        prompts: list[str] = []
        violation = {
            "location": "by_field.field_00",
            "item_index": 0,
            "rule": "R4",
            "card_quote": "Five reactors",
            "source_quote": "eight reactors",
            "evidence_relation": "direct_conflict",
        }

        def review_adds_violation(prompt: str) -> str:
            prompts.append(prompt)
            if "EVIDENCE_REVIEW_BATCH_INITIAL" in prompt:
                return _batch_response(self.locations)
            return _batch_response(self.locations, [violation])

        report = transcription.transcription_verify(
            self.card,
            call_verify=review_adds_violation,
        )

        self.assertEqual(len(prompts), 2)
        self.assertEqual(report.verdict, "FAIL")
        self.assertEqual(len(report.problems), 1)
        self.assertEqual(report.problems[0].location, "by_field.field_00")
        self.assertEqual(report.problems[0].kind, "R4")
        evidence = json.loads(report.problems[0].quote)
        self.assertEqual(evidence["card_quote"], "Five reactors")
        self.assertEqual(evidence["source_quote"], "eight reactors")
        self.assertEqual(evidence["evidence_relation"], "direct_conflict")
        self.assertEqual(evidence["item_index"], 0)


    def test_final_review_can_remove_initial_semantic_false_positive(self) -> None:
        prompts: list[str] = []
        initial = {
            "location": "by_field.field_00",
            "item_index": 0,
            "rule": "R4",
            "card_quote": "Five reactors",
            "source_quote": "eight reactors",
            "evidence_relation": "direct_conflict",
        }

        def final_clears_initial(prompt: str) -> str:
            prompts.append(prompt)
            if "EVIDENCE_REVIEW_BATCH_INITIAL" in prompt:
                return _batch_response(self.locations, [initial])
            self.assertIn('"location": "by_field.field_00"', prompt)
            return _batch_response(self.locations)

        report = transcription.transcription_verify(
            self.card,
            call_verify=final_clears_initial,
        )
        self.assertEqual(len(prompts), 2)
        self.assertEqual(report.verdict, "PASS")
        self.assertEqual(report.problems, [])

    def test_literal_propagation_does_not_add_model_calls(self) -> None:
        text = self.card.read_text(encoding="utf-8")
        self.card.write_text(
            text.replace("Control statement 01", "Five reactors"),
            encoding="utf-8",
        )
        prompts: list[str] = []
        violation = {
            "location": "by_field.field_00",
            "item_index": 0,
            "rule": "R4",
            "card_quote": "Five reactors",
            "source_quote": "eight reactors",
            "evidence_relation": "direct_conflict",
        }

        def one_final_conflict(prompt: str) -> str:
            prompts.append(prompt)
            rows = [violation] if "EVIDENCE_REVIEW_BATCH_FINAL_REVIEW" in prompt else []
            return _batch_response(self.locations, rows)

        report = transcription.transcription_verify(
            self.card,
            call_verify=one_final_conflict,
        )
        self.assertEqual(len(prompts), 2)
        self.assertEqual(
            {problem.location for problem in report.problems},
            {"by_field.field_00", "by_field.field_01"},
        )

    def test_batch_prompt_reuses_v14_unicode_and_scope_rules(self) -> None:
        prompts: list[str] = []

        def capture(prompt: str) -> str:
            prompts.append(prompt)
            return _batch_response(self.locations)

        transcription.transcription_verify(self.card, call_verify=capture)
        for phrase in (
            "可逆、互补或单调等价表示",
            "同一对象、同一层级、同一口径",
            "人名、文内引用、变量、希腊字母",
            "不得建立字符白名单",
            "具名方法、工具、软件或统计程序",
            "逐个 item_index",
        ):
            self.assertIn(phrase, prompts[0])
            self.assertIn(phrase, prompts[1])
        for marker in ("__FIELD_NAME__", "__FIELD_VALUE__", "__CHUNK_TEXT__"):
            self.assertNotIn(marker, prompts[0])


    def test_initial_contract_failure_retries_once_then_reviews(self) -> None:
        prompts: list[str] = []

        def first_initial_is_incomplete(prompt: str) -> str:
            prompts.append(prompt)
            if len(prompts) == 1:
                return _batch_response(self.locations[:-1])
            return _batch_response(self.locations)

        report = transcription.transcription_verify(
            self.card,
            call_verify=first_initial_is_incomplete,
        )

        self.assertEqual(report.verdict, "PASS")
        self.assertEqual(len(prompts), 3)
        self.assertIn("EVIDENCE_REVIEW_BATCH_INITIAL", prompts[0])
        self.assertIn("EVIDENCE_REVIEW_BATCH_TECHNICAL_RETRY", prompts[1])
        self.assertIn("EVIDENCE_REVIEW_BATCH_FINAL_REVIEW", prompts[2])

    def test_final_contract_failure_retries_final_only(self) -> None:
        prompts: list[str] = []
        unknown = {
            "location": "by_field.not_in_card",
            "item_index": 0,
            "rule": "R4",
            "card_quote": "Five reactors",
            "source_quote": "eight reactors",
            "evidence_relation": "direct_conflict",
        }

        def first_final_is_invalid(prompt: str) -> str:
            prompts.append(prompt)
            if len(prompts) == 1:
                return _batch_response(self.locations)
            if len(prompts) == 2:
                return _batch_response(self.locations, [unknown])
            return _batch_response(self.locations)

        report = transcription.transcription_verify(
            self.card,
            call_verify=first_final_is_invalid,
        )

        self.assertEqual(report.verdict, "PASS")
        self.assertEqual(len(prompts), 3)
        self.assertIn("EVIDENCE_REVIEW_BATCH_FINAL_REVIEW", prompts[1])
        self.assertIn("EVIDENCE_REVIEW_BATCH_TECHNICAL_RETRY", prompts[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
