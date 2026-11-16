"""quality-B fault injection tests for semantic occurrence binding and local recovery."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from evidence_review.claim_evidence import validate_claim_evidence_review
from evidence_review.claim_evidence import _unify_fact_ids, apply_fact_verdicts, build_claim_evidence_view, repair_evidence_occurrence, render_reviewed_field
class ClaimEvidenceFaultInjectionP0Test(unittest.TestCase):
    def setUp(self) -> None:
        self.correct_quote = (
            "In the second cycle, accumulative methane production reached "
            "5.97 ± 0.59 L/L in CB versus 2.89 ± 1.99 L/L in P."
        )
        self.wrong_quote = (
            "In the third cycle, methane production was 3.54 ± 0.30 L/L in CB "
            "versus 3.10 ± 0.71 L/L in P."
        )
        self.card = {
            "paper_id": "QuantPaper",
            "schema_version": "6",
            "key_results": (
                "In the second cycle, accumulative methane production reached "
                "5.97 ± 0.59 L/L in CB versus 2.89 ± 1.99 L/L in P. "
                "Unrelated terminal pH remained 7.15."
            ),
            "key_data": [
                {
                    "metric": "accumulative methane production",
                    "value": "5.97 ± 0.59",
                    "unit": "L/L",
                    "sample": (
                        "sample/control: CB culture | phase/time: second cycle | "
                        "comparator: P culture 2.89 ± 1.99 L/L"
                    ),
                    "stat": "stat_type: mean ± SD | n: 3",
                    "quote": self.correct_quote,
                    "chunk_id": "QuantPaper#c1",
                }
            ],
            "source_anchor": {
                "paper_id": "QuantPaper",
                "by_field": {
                    "key_results": [
                        {"chunk_id": "QuantPaper#c1", "quote": None},
                        {
                            "chunk_id": "QuantPaper#c2",
                            "quote": "Unrelated terminal pH remained 7.15.",
                        },
                    ]
                },
            },
        }
        self.chunks = [
            {
                "paper_id": "QuantPaper",
                "chunk_id": "QuantPaper#c1",
                "section_path": "3. Results",
                "block_type": "paragraph",
                "text": f"{self.correct_quote} {self.wrong_quote}",
            },
            {
                "paper_id": "QuantPaper",
                "chunk_id": "QuantPaper#c2",
                "section_path": "3. Results",
                "block_type": "paragraph",
                "text": "Unrelated terminal pH remained 7.15.",
            },
        ]

    @staticmethod
    def _review_payload(view: dict, *, force_supported_claim_id: str | None = None) -> dict:
        reviews = []
        for claim in view["claims"]:
            supported = claim["mapping_status"] == "resolved"
            if claim["claim_id"] == force_supported_claim_id:
                supported = True
            link_ids = claim["allowed_link_ids"] or claim["candidate_link_ids"]
            reviews.append(
                {
                    "claim_id": claim["claim_id"],
                    "atom_reviews": [
                        {
                            "atom_id": atom["atom_id"],
                            "status": "SUPPORTED" if supported else "NEEDS_REVIEW",
                            "evidence_link_ids": [link_ids[0]] if supported and link_ids else [],
                            "reason": "",
                        }
                        for atom in claim["atoms"]
                        if atom["required"]
                    ],
                }
            )
        return {"claim_reviews": reviews}

    def _baseline(self) -> dict:
        return build_claim_evidence_view(
            deepcopy(self.card),
            deepcopy(self.chunks),
            fields=("key_results", "key_data"),
        )

    @staticmethod
    def _target_claims(view: dict) -> list[dict]:
        return [claim for claim in view["claims"] if "5.97" in claim["original_text"]]

    def test_correct_fact_is_reused_across_fields_and_clean_review_passes(self) -> None:
        view = self._baseline()
        targets = self._target_claims(view)

        self.assertEqual(len(targets), 2)
        self.assertEqual({item["field"] for item in targets}, {"key_results", "key_data"})
        self.assertEqual(len({item["fact_id"] for item in targets}), 1)
        self.assertTrue(all(item["mapping_status"] == "resolved" for item in targets))

        receipt = validate_claim_evidence_review(
            self._review_payload(view),
            view,
            self.chunks,
        )

        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["model_verdict"], "MODEL_REVIEW_PASS")

    def test_scope_mismatched_occurrence_is_candidate_and_original_is_reselected(self) -> None:
        view = self._baseline()
        target = next(item for item in self._target_claims(view) if item["field"] == "key_data")

        repaired = repair_evidence_occurrence(
            view,
            self.chunks,
            occurrence_id=target["occurrence_id"],
            replacement_chunk_id="QuantPaper#c1",
            replacement_quote=self.wrong_quote,
            reason="fault_injection_phase_and_comparator_mismatch",
        )

        updated = repaired["view"]
        updated_target = next(
            item for item in updated["claims"] if item["claim_id"] == target["claim_id"]
        )
        links = {item["link_id"]: item for item in updated["claim_evidence_links"]}
        evidence = {item["evidence_id"]: item for item in updated["evidence_records"]}
        candidate = links[repaired["audit"]["injected_link_id"]]
        selected = links[updated_target["allowed_link_ids"][0]]

        self.assertEqual(candidate["mapping_status"], "scope_mismatch")
        self.assertNotIn(candidate["link_id"], updated_target["allowed_link_ids"])
        self.assertIn(candidate["link_id"], updated_target["candidate_link_ids"])
        self.assertEqual(selected["mapping_status"], "best_anchor_scope_exact")
        self.assertEqual(evidence[selected["evidence_id"]]["quote"], self.correct_quote)
        self.assertEqual(repaired["audit"]["final_status"], "RECOVERED")
        self.assertNotEqual(
            repaired["audit"]["old_occurrence_id"],
            repaired["audit"]["new_occurrence_id"],
        )

    def _assert_scope_quote_cannot_be_supported(self, quote: str) -> None:
        card = deepcopy(self.card)
        card["key_data"][0]["quote"] = quote
        chunks = [
            {
                "paper_id": "QuantPaper",
                "chunk_id": "QuantPaper#c1",
                "section_path": "3. Results",
                "block_type": "paragraph",
                "text": quote,
            }
        ]
        view = build_claim_evidence_view(card, chunks, fields=("key_data",))
        claim = view["claims"][0]
        links = [
            item for item in view["claim_evidence_links"]
            if item["claim_id"] == claim["claim_id"]
        ]

        self.assertEqual(claim["mapping_status"], "unresolved")
        self.assertEqual(claim["allowed_link_ids"], [])
        self.assertTrue(any(item["mapping_status"] == "scope_mismatch" for item in links))
        receipt = validate_claim_evidence_review(
            self._review_payload(view, force_supported_claim_id=claim["claim_id"]),
            view,
            chunks,
        )
        self.assertFalse(receipt["ok"])
        self.assertIn("MAPPING_UNRESOLVED", receipt["errors"])

    def test_same_value_unit_metric_comparator_but_wrong_sample_is_not_supported(self) -> None:
        self._assert_scope_quote_cannot_be_supported(
            "In the second cycle, accumulative methane production reached "
            "5.97 ± 0.59 L/L in AC versus 2.89 ± 1.99 L/L in P."
        )

    def test_same_value_unit_metric_comparator_but_missing_phase_is_not_supported(self) -> None:
        self._assert_scope_quote_cannot_be_supported(
            "Accumulative methane production reached 5.97 ± 0.59 L/L in CB "
            "versus 2.89 ± 1.99 L/L in P."
        )

    def test_no_correct_evidence_flags_only_target_fact_and_preserves_key_results(self) -> None:
        baseline = self._baseline()
        all_verified = apply_fact_verdicts(
            baseline,
            {fact["fact_id"]: "verified" for fact in baseline["facts"]},
        )
        target = next(
            item for item in self._target_claims(all_verified) if item["field"] == "key_data"
        )
        target_fact_id = target["fact_id"]
        unavailable_chunks = deepcopy(self.chunks)
        unavailable_chunks[0]["text"] = self.wrong_quote
        unrelated_before = {
            item["claim_id"]: deepcopy(item)
            for item in all_verified["claims"]
            if item["fact_id"] != target_fact_id
        }

        repaired = repair_evidence_occurrence(
            all_verified,
            unavailable_chunks,
            occurrence_id=target["occurrence_id"],
            replacement_chunk_id="QuantPaper#c1",
            replacement_quote=self.wrong_quote,
            reason="fault_injection_no_correct_evidence_available",
        )

        updated = repaired["view"]
        target_claims = [item for item in updated["claims"] if item["fact_id"] == target_fact_id]
        target_occurrences = [
            item for item in updated["occurrences"] if item["fact_id"] == target_fact_id
        ]
        unrelated_after = {
            item["claim_id"]: item
            for item in updated["claims"]
            if item["fact_id"] != target_fact_id
        }

        self.assertEqual(repaired["audit"]["final_status"], "FLAGGED_LOCAL")
        self.assertTrue(all(item["mapping_status"] == "unresolved" for item in target_claims))
        self.assertTrue(all(item["verdict"] == "flagged" for item in target_claims))
        self.assertTrue(all(item["verdict"] == "flagged" for item in target_occurrences))
        self.assertEqual(unrelated_after, unrelated_before)
        self.assertIn("5.97", render_reviewed_field(updated, "key_results"))
        self.assertIn("7.15", render_reviewed_field(updated, "key_results"))

        payload = self._review_payload(
            updated,
            force_supported_claim_id=target["claim_id"],
        )
        receipt = validate_claim_evidence_review(payload, updated, unavailable_chunks)
        self.assertFalse(receipt["ok"])
        self.assertIn("MAPPING_UNRESOLVED", receipt["errors"])
        self.assertIn("EVIDENCE_LINK_NOT_ALLOWED_FOR_ATOM", receipt["errors"])

    def test_mutation_is_copy_on_write_and_the_log_has_occurrence_diff(self) -> None:
        baseline = self._baseline()
        before = deepcopy(baseline)
        chunks_before = deepcopy(self.chunks)
        target = next(item for item in self._target_claims(baseline) if item["field"] == "key_data")
        prior = os.environ.get("MEMORIVE_RUNTIME_LOG_LOG_DIR")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MEMORIVE_RUNTIME_LOG_LOG_DIR"] = tmp
            try:
                with patch("memorive.platform.logs.logger._default", None):
                    repaired = repair_evidence_occurrence(
                        baseline,
                        self.chunks,
                        occurrence_id=target["occurrence_id"],
                        replacement_chunk_id="QuantPaper#c1",
                        replacement_quote=self.wrong_quote,
                        reason="fault_injection_audit",
                    )
                records = [
                    json.loads(line)
                    for line in (Path(tmp) / "log.txt").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            finally:
                if prior is None:
                    os.environ.pop("MEMORIVE_RUNTIME_LOG_LOG_DIR", None)
                else:
                    os.environ["MEMORIVE_RUNTIME_LOG_LOG_DIR"] = prior

        self.assertEqual(baseline, before)
        self.assertEqual(self.chunks, chunks_before)
        record = records[-1]
        context = record["context"]
        self.assertEqual(context["old_occurrence_id"], repaired["audit"]["old_occurrence_id"])
        self.assertEqual(context["new_occurrence_id"], repaired["audit"]["new_occurrence_id"])
        self.assertEqual(context["reason_code"], "fault_injection_audit")
        self.assertEqual(context["final_status"], "RECOVERED")
        self.assertIn("diff", context)

    def _assert_fact_scope_difference_does_not_merge(
        self,
        left_scope: dict,
        right_scope: dict,
    ) -> None:
        claims = [
            {
                "claim_id": "C-left",
                "fact_id": "F-left",
                "target_path": "key_data[0]",
                "segment_index": 0,
                "original_text": "Methane production was 5.97 L/L.",
                "semantic_scope": left_scope,
            },
            {
                "claim_id": "C-right",
                "fact_id": "F-right",
                "target_path": "key_data[1]",
                "segment_index": 0,
                "original_text": "Methane production was 5.97 L/L.",
                "semantic_scope": right_scope,
            },
        ]
        links = [
            {
                "claim_id": claim["claim_id"],
                "fact_id": claim["fact_id"],
                "evidence_id": "E-shared",
                "mapping_status": "explicit",
            }
            for claim in claims
        ]

        _unify_fact_ids(claims, links)

        self.assertEqual(len({claim["fact_id"] for claim in claims}), 2)

    def test_same_value_but_different_phase_does_not_merge(self) -> None:
        base = {
            "sample_scope": "CB culture",
            "phase_time": "second cycle",
            "comparator": "P culture",
            "stat_type": "mean",
            "condition_signature": "anaerobic digestion",
        }
        self._assert_fact_scope_difference_does_not_merge(
            base,
            {**base, "phase_time": "third cycle"},
        )

    def test_same_value_but_different_sample_does_not_merge(self) -> None:
        base = {
            "sample_scope": "CB culture",
            "phase_time": "second cycle",
            "comparator": "P culture",
            "stat_type": "mean",
            "condition_signature": "anaerobic digestion",
        }
        self._assert_fact_scope_difference_does_not_merge(
            base,
            {**base, "sample_scope": "AC culture"},
        )

    def test_same_value_but_minimum_and_mean_do_not_merge(self) -> None:
        base = {
            "sample_scope": "CB culture",
            "phase_time": "second cycle",
            "comparator": "P culture",
            "stat_type": "minimum",
            "condition_signature": "anaerobic digestion",
        }
        self._assert_fact_scope_difference_does_not_merge(
            base,
            {**base, "stat_type": "mean"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
