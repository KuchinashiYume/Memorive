from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")

from memorive.documents.card_review.atomic_review import (
    ATOMIZATION_REVIEW_REQUIRED,
    build_atomic_review_view,
    deterministic_precheck,
    split_free_text,
)
from memorive.documents.card_review.reviewer_contract import CARD_REVIEW_PASS, MODEL_REVIEW_PASS
from memorive.documents.card_review.reviewer_contract import compose_card_decision, load_reviewer_contract, validate_delta_review, validate_full_review
from memorive.documents.card_review.reviewer_routing import DeltaRisk, classify_delta_risk


class AtomicReviewContractV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract_path = REPO_ROOT / "memorive" / "documents" / "card_review" / "contracts" / "card_review_atomic_v2.json"
        cls.contract = load_reviewer_contract(cls.contract_path)

    def setUp(self) -> None:
        self.card = {
            "paper_id": "Paper",
            "card_version": "v7-candidate",
            "source_version": "source-v1",
            "schema_version": "6",
            "research_question": "Does treatment improve removal?",
            "research_object": ["textile wastewater"],
            "method": ["PANDAseq assembled paired-end reads; PERMANOVA tested group differences."],
            "key_results": "Removal increased to 77% during Period 3 (Day 78–121).",
            "author_conclusion": "The results suggest improved removal.",
            "boundary_conditions": ["The result applies to the treatment reactors."],
            "source_anchor": {
                "paper_id": "Paper",
                "by_field": {
                    field: [{"chunk_id": "Paper#c1", "quote": "treatment", "section_page": "p1"}]
                    for field in (
                        "research_question",
                        "research_object",
                        "method",
                        "key_results",
                        "author_conclusion",
                        "boundary_conditions",
                    )
                },
            },
            "key_data": [
                {
                    "value": "0.043 ± 0.008",
                    "unit": "mbar/min",
                    "metric": "pressure decline",
                    "sample": "sample/control: Real textile wastewater | phase/time: Period 3 (Day 78–121) | comparator: control",
                    "stat": "stat_type: mean_sd | n: 5 | significance: p<0.05",
                    "quote": "The treatment pressure decline was 0.043 ± 0.008 mbar/min.",
                    "chunk_id": "Paper#c1",
                }
            ],
        }
        self.chunks = [
            {
                "paper_id": "Paper",
                "chunk_id": "Paper#c1",
                "section": "Results",
                "page": 1,
                "text": "The treatment pressure decline was 0.043 ± 0.008 mbar/min. Period 3 ended on Day 121; treatment reactors used textile wastewater. PANDAseq assembled paired-end reads and PERMANOVA tested group differences.",
            }
        ]
        self.view = build_atomic_review_view(self.card, self.chunks)

    def _supported_full(self) -> dict:
        atom_ids = []
        for claim in self.view["claims"]:
            for atom in claim["atoms"]:
                if atom["required"]:
                    atom_ids.append(atom["atom_id"])
        return {
            "coverage_complete": True,
            "coverage": {field: "SUPPORTED" for field in self.contract["core_fields"]},
            "supported_atom_groups": [{"atom_ids": atom_ids, "evidence_chunk_ids": ["Paper#c1"]}],
            "atom_reviews": [],
            "missing_claims": [],
            "cross_field_conflicts": [],
        }

    def test_contract_is_provider_neutral(self) -> None:
        text = self.contract_path.read_text(encoding="utf-8").casefold()
        for forbidden in ("sonnet", "anthropic", "openai", "terra", "adaptive", "thinking", "price"):
            self.assertNotIn(forbidden, text)

    def test_contract_versions_are_explicit(self) -> None:
        self.assertEqual(self.contract["review_contract_id"], "card-review-atomic-v2")
        for key in (
            "input_schema_version",
            "atomic_claim_schema_version",
            "full_review_schema_version",
            "delta_review_schema_version",
            "evidence_policy_version",
            "safety_policy_version",
            "gold_suite_version",
            "route_policy_version",
        ):
            self.assertTrue(self.contract[key])

    def test_all_required_atom_types_are_registered(self) -> None:
        required = {
            "research_object", "research_relation", "population_or_system", "sample_control",
            "intervention", "method_step", "instrument", "parameter", "value", "unit",
            "direction", "phase_time", "sample_size", "statistical_scope", "comparison",
            "claim_strength", "conclusion_scope", "limitation", "applicability", "uncertainty",
            "source_anchor",
        }
        self.assertTrue(required.issubset(set(self.contract["atom_types"])))

    def test_structured_fields_become_stable_claims(self) -> None:
        targets = {claim["target_path"] for claim in self.view["claims"]}
        self.assertIn("method[0]", targets)
        self.assertIn("boundary_conditions[0]", targets)
        self.assertIn("key_data[0]", targets)

    def test_key_data_is_atomized_without_rewriting(self) -> None:
        claim = next(item for item in self.view["claims"] if item["target_path"] == "key_data[0]")
        by_type = {atom["atom_type"]: atom["claim"] for atom in claim["atoms"]}
        self.assertEqual(by_type["value"], "0.043 ± 0.008")
        self.assertEqual(by_type["unit"], "mbar/min")
        self.assertEqual(by_type["sample_control"], "Real textile wastewater")
        self.assertEqual(by_type["phase_time"], "Period 3 (Day 78–121)")
        self.assertEqual(by_type["sample_size"], "5")
        self.assertEqual(claim["original_text"], json.dumps(self.card["key_data"][0], ensure_ascii=False, sort_keys=True))
        self.assertTrue(all(atom["required"] for atom in claim["atoms"]))

    def test_numeric_and_time_atoms_are_extracted_from_free_text(self) -> None:
        claim = next(item for item in self.view["claims"] if item["target_path"] == "key_results")
        atom_types = {atom["atom_type"] for atom in claim["atoms"]}
        self.assertIn("value", atom_types)
        self.assertIn("phase_time", atom_types)

    def test_unit_detector_does_not_match_letters_inside_words(self) -> None:
        card = deepcopy(self.card)
        card["key_results"] = "Samples remained stable during recovery."
        view = build_atomic_review_view(card, deepcopy(self.chunks))
        units = [
            atom["claim"]
            for claim in view["claims"]
            if claim["target_path"] == "key_results"
            for atom in claim["atoms"]
            if atom["atom_type"] == "unit"
        ]
        self.assertEqual(units, [])

    def test_field_source_anchors_are_reviewed_once_not_per_sentence(self) -> None:
        card = deepcopy(self.card)
        card["key_results"] = "First supported statement. Second supported statement."
        view = build_atomic_review_view(card, deepcopy(self.chunks))
        anchor_atoms = [
            atom
            for claim in view["claims"]
            if claim["target_path"] == "key_results"
            for atom in claim["atoms"]
            if atom["atom_type"] == "source_anchor"
        ]
        expected = len(card["source_anchor"]["by_field"]["key_results"])
        self.assertEqual(len(anchor_atoms), expected)

    def test_free_text_split_is_deterministic(self) -> None:
        first = split_free_text("First sentence. Second clause; third clause.")
        second = split_free_text("First sentence. Second clause; third clause.")
        self.assertEqual(first, second)
        self.assertEqual(first[0], ["First sentence.", "Second clause", "third clause."])

    def test_unbalanced_free_text_fails_closed(self) -> None:
        pieces, status = split_free_text("Result improved (during phase 3.")
        self.assertEqual(pieces, ["Result improved (during phase 3."])
        self.assertEqual(status, ATOMIZATION_REVIEW_REQUIRED)

    def test_required_atom_without_anchor_is_blocked(self) -> None:
        card = json.loads(json.dumps(self.card))
        card["source_anchor"]["by_field"]["method"] = []
        view = build_atomic_review_view(card, self.chunks)
        receipt = deterministic_precheck(card, self.chunks, view)
        self.assertTrue(any(issue["issue_type"] == "REQUIRED_ATOM_NO_ANCHOR" for issue in receipt["issues"]))

    def test_unknown_chunk_is_blocked(self) -> None:
        card = json.loads(json.dumps(self.card))
        card["source_anchor"]["by_field"]["method"][0]["chunk_id"] = "Paper#missing"
        view = build_atomic_review_view(card, self.chunks)
        receipt = deterministic_precheck(card, self.chunks, view)
        self.assertTrue(any(issue["issue_type"] == "CHUNK_NOT_FOUND" for issue in receipt["issues"]))

    def test_cross_paper_chunk_is_blocked(self) -> None:
        card = json.loads(json.dumps(self.card))
        card["source_anchor"]["by_field"]["method"][0]["chunk_id"] = "Other#c1"
        chunks = self.chunks + [{"paper_id": "Other", "chunk_id": "Other#c1", "text": "PANDAseq"}]
        view = build_atomic_review_view(card, chunks)
        receipt = deterministic_precheck(card, chunks, view)
        self.assertTrue(any(issue["issue_type"] == "CROSS_PAPER_CHUNK" for issue in receipt["issues"]))

    def test_quote_must_exist_in_chunk(self) -> None:
        card = json.loads(json.dumps(self.card))
        card["source_anchor"]["by_field"]["method"][0]["quote"] = "not present verbatim"
        view = build_atomic_review_view(card, self.chunks)
        receipt = deterministic_precheck(card, self.chunks, view)
        self.assertTrue(any(issue["issue_type"] == "QUOTE_NOT_IN_CHUNK" for issue in receipt["issues"]))

    def test_quote_allows_markdown_emphasis_only_difference(self) -> None:
        card = deepcopy(self.card)
        card["source_anchor"]["by_field"]["method"][0]["quote"] = "PANDAseq assembled paired-end reads"
        chunks = deepcopy(self.chunks)
        chunks[0]["text"] = chunks[0]["text"].replace(
            "PANDAseq assembled paired-end reads", "_PANDAseq_ assembled paired-end reads"
        )
        view = build_atomic_review_view(card, chunks)
        receipt = deterministic_precheck(card, chunks, view)
        self.assertFalse(any(issue["issue_type"] == "QUOTE_NOT_IN_CHUNK" for issue in receipt["issues"]))

    def test_placeholder_is_local_issue(self) -> None:
        card = json.loads(json.dumps(self.card))
        card["research_question"] = "Not given"
        view = build_atomic_review_view(card, self.chunks)
        receipt = deterministic_precheck(card, self.chunks, view)
        self.assertTrue(any(issue["issue_type"] == "PLACEHOLDER" for issue in receipt["issues"]))

    def test_full_review_requires_all_six_coverage_receipts(self) -> None:
        payload = self._supported_full()
        del payload["coverage"]["boundary_conditions"]
        receipt = validate_full_review(payload, self.view, self.chunks, self.contract)
        self.assertFalse(receipt["ok"])
        self.assertIn("COVERAGE_FIELDS_INCOMPLETE", receipt["errors"])

    def test_full_review_requires_every_required_atom(self) -> None:
        payload = self._supported_full()
        payload["supported_atom_groups"][0]["atom_ids"].pop()
        receipt = validate_full_review(payload, self.view, self.chunks, self.contract)
        self.assertFalse(receipt["ok"])
        self.assertIn("REQUIRED_ATOM_UNREVIEWED", receipt["errors"])

    def test_full_review_rejects_invented_evidence(self) -> None:
        payload = self._supported_full()
        payload["supported_atom_groups"][0]["evidence_chunk_ids"] = ["Paper#invented"]
        receipt = validate_full_review(payload, self.view, self.chunks, self.contract)
        self.assertFalse(receipt["ok"])
        self.assertIn("EVIDENCE_CHUNK_NOT_FOUND", receipt["errors"])

    def test_model_pass_is_not_card_pass(self) -> None:
        receipt = validate_full_review(self._supported_full(), self.view, self.chunks, self.contract)
        self.assertEqual(receipt["model_verdict"], MODEL_REVIEW_PASS)
        self.assertNotEqual(receipt["model_verdict"], CARD_REVIEW_PASS)
        self.assertEqual(compose_card_decision(receipt, {"ok": True, "issues": []}), CARD_REVIEW_PASS)

    def test_single_method_omission_routes_to_local_repair(self) -> None:
        payload = self._supported_full()
        payload["coverage"]["method"] = "PARTIAL"
        payload["missing_claims"] = [
            {
                "field": "method",
                "target_path": "method",
                "issue_type": "METHOD_OMISSION",
                "evidence_chunk_ids": ["Paper#c1"],
                "description": "one method step omitted",
            }
        ]
        receipt = validate_full_review(payload, self.view, self.chunks, self.contract)
        self.assertEqual(compose_card_decision(receipt, {"ok": True, "issues": []}), "LOCAL_REPAIR")

    def test_local_mechanical_failure_overrides_model_pass(self) -> None:
        receipt = validate_full_review(self._supported_full(), self.view, self.chunks, self.contract)
        self.assertEqual(compose_card_decision(receipt, {"ok": False, "issues": [{"issue_type": "CHUNK_NOT_FOUND"}]}), "LOCAL_REPAIR")

    def _delta_view(self) -> dict:
        claim = next(item for item in self.view["claims"] if item["target_path"] == "key_data[0]")
        return {"claims": [claim], "paper_id": "Paper", "whole_card_scope": False}

    def _delta_payload(self, status: str = "SUPPORTED", final_status: str = "RESOLVED") -> dict:
        claim = self._delta_view()["claims"][0]
        return {
            "claim_reviews": [
                {
                    "claim_id": claim["claim_id"],
                    "atom_reviews": [
                        {"atom_id": atom["atom_id"], "status": status, "evidence_chunk_ids": ["Paper#c1"], "reason": ""}
                        for atom in claim["atoms"] if atom["required"]
                    ],
                    "cross_field_conflicts": [],
                    "final_status": final_status,
                }
            ]
        }

    def test_delta_all_required_atoms_supported_can_resolve(self) -> None:
        receipt = validate_delta_review(self._delta_payload(), self._delta_view(), self.chunks, self.contract)
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["false_resolved_count"], 0)
        self.assertEqual(receipt["unsafe_pass_count"], 0)

    def test_delta_false_resolved_is_blocked(self) -> None:
        payload = self._delta_payload()
        payload["claim_reviews"][0]["atom_reviews"][0]["status"] = "UNSUPPORTED"
        receipt = validate_delta_review(payload, self._delta_view(), self.chunks, self.contract)
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["false_resolved_count"], 1)

    def test_delta_wrong_anchor_cannot_resolve(self) -> None:
        payload = self._delta_payload()
        anchor = next(item for item in payload["claim_reviews"][0]["atom_reviews"] if "source_anchor" in item["atom_id"])
        anchor["status"] = "UNSUPPORTED"
        receipt = validate_delta_review(payload, self._delta_view(), self.chunks, self.contract)
        self.assertEqual(receipt["false_resolved_count"], 1)

    def test_delta_claim_rejects_cross_paper_evidence(self) -> None:
        view = self._delta_view()
        view["claims"][0]["paper_id"] = "Paper"
        chunks = self.chunks + [{"paper_id": "Other", "chunk_id": "Other#c1", "text": "other"}]
        payload = self._delta_payload()
        payload["claim_reviews"][0]["atom_reviews"][0]["evidence_chunk_ids"] = ["Other#c1"]
        receipt = validate_delta_review(payload, view, chunks, self.contract)
        self.assertIn("EVIDENCE_CROSS_PAPER", receipt["errors"])

    def test_delta_cross_field_conflict_cannot_resolve(self) -> None:
        payload = self._delta_payload()
        payload["claim_reviews"][0]["cross_field_conflicts"] = [{"field": "boundary_conditions", "reason": "phase conflict"}]
        receipt = validate_delta_review(payload, self._delta_view(), self.chunks, self.contract)
        self.assertEqual(receipt["false_resolved_count"], 1)

    def test_delta_cannot_claim_whole_card_pass(self) -> None:
        payload = self._delta_payload()
        payload["card_review_pass"] = True
        receipt = validate_delta_review(payload, self._delta_view(), self.chunks, self.contract)
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["unsafe_pass_count"], 1)

    def test_local_only_delta_route(self) -> None:
        risk = classify_delta_risk([{"kind": "format", "semantic_change": False, "optional": True}])
        self.assertEqual(risk, DeltaRisk.LOCAL_ONLY)

    def test_low_risk_delta_route(self) -> None:
        risk = classify_delta_risk([{"kind": "text_normalization", "semantic_change": True, "optional": True, "atom_types": ["applicability"]}])
        self.assertEqual(risk, DeltaRisk.LOW_RISK)

    def test_numeric_change_is_high_risk_delta(self) -> None:
        risk = classify_delta_risk([{"kind": "replace", "semantic_change": True, "optional": False, "atom_types": ["value"]}])
        self.assertEqual(risk, DeltaRisk.HIGH_RISK)

    def test_anchor_dispute_is_high_risk_delta(self) -> None:
        risk = classify_delta_risk([{"kind": "reanchor", "semantic_change": False, "optional": True, "anchor_disputed": True}])
        self.assertEqual(risk, DeltaRisk.HIGH_RISK)


if __name__ == "__main__":
    unittest.main(verbosity=2)
