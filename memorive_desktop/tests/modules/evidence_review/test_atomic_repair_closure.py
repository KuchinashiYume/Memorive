from __future__ import annotations

import json
import unittest
from copy import deepcopy

from memorive.documents.card_review.atomic_repair import ATOMIC_REPAIR_MAX_TOKENS, ATOMIC_REPAIR_TIMEOUT_SECONDS, DELTA_PASS, HUMAN_REQUIRED, PASS_WITH_TOMBSTONES, TARGETED_REPAIR, AtomicRepairContractError, canonical_hash
from memorive.documents.card_review.atomic_repair import apply_atomic_repairs, build_atomic_delta_view, build_atomic_repair_bundle, build_atomic_repair_request, classify_delta_resolution, governed_omission_precheck, prune_unresolved_targets, route_full_review_to_closure, validate_atomic_repair_gateway_response, validate_atomic_repairs
from memorive.documents.card_review.atomic_review import build_atomic_review_view
from memorive.documents.card_review.reviewer_contract import required_atom_manifest
from memorive.documents.card_review.reviewer_contract import build_full_review_completion_view, merge_full_review_completion_payload, missing_required_atom_ids, validate_full_review
class AtomicRepairClosureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.card = {
            "paper_id": "Paper",
            "research_question": "Does treatment improve removal?",
            "research_object": ["wastewater"],
            "method": ["Five reactors were operated.", "Mesophilic operation was maintained."],
            "key_results": "Removal reached 77%.",
            "author_conclusion": "The treatment improved removal.",
            "boundary_conditions": ["Mesophilic operation."],
            "source_anchor": {
                "paper_id": "Paper",
                "by_field": {
                    field: [{"chunk_id": "Paper#c1", "quote": "treatment", "section_page": "p1"}]
                    for field in (
                        "research_question", "research_object", "method", "key_results",
                        "author_conclusion", "boundary_conditions",
                    )
                },
            },
            "key_data": [],
        }
        self.chunks = [
            {
                "paper_id": "Paper",
                "chunk_id": "Paper#c1",
                "section": "Methods",
                "page": 1,
                "text": "The treatment used eight reactors under mesophilic operation and reached 77% removal.",
            }
        ]
        self.view = build_atomic_review_view(self.card, self.chunks)
        method_claim = next(item for item in self.view["claims"] if item["target_path"] == "method[0]")
        method_atom = method_claim["atoms"][0]
        self.payload = {
            "coverage_complete": True,
            "coverage": {
                "research_question": "SUPPORTED",
                "research_object": "SUPPORTED",
                "method": "PARTIAL",
                "key_results": "SUPPORTED",
                "author_conclusion": "SUPPORTED",
                "boundary_conditions": "SUPPORTED",
            },
            "supported_atom_groups": [],
            "atom_reviews": [
                {
                    "claim_id": method_claim["claim_id"],
                    "atom_id": method_atom["atom_id"],
                    "status": "CONFLICT",
                    "evidence_chunk_ids": ["Paper#c1"],
                    "reason": "The paper says eight reactors.",
                }
            ],
            "missing_claims": [],
            "cross_field_conflicts": [],
        }
        self.receipt = {"ok": True, "model_verdict": "MODEL_REVIEW_ISSUES", "payload": self.payload}

    def _bundle(self) -> dict:
        return build_atomic_repair_bundle(self.card, self.view, self.receipt, self.chunks)

    def _valid_response(self, bundle: dict) -> dict:
        target = bundle["targets"][0]
        return {
            "repairs": [
                {
                    "target_id": target["target_id"],
                    "finding_ids": [item["finding_id"] for item in target["findings"]],
                    "action": "replace",
                    "replacement": "Eight reactors were operated.",
                    "anchors": [
                        {
                            "chunk_id": "Paper#c1",
                            "quote": "eight reactors under mesophilic operation",
                            "section_page": "p1",
                        }
                    ],
                    "reason": "Correct the reactor count to match direct evidence.",
                }
            ]
        }

    def test_issue_route_never_returns_regenerate_or_second_full(self) -> None:
        self.assertEqual(route_full_review_to_closure(self.receipt), TARGETED_REPAIR)
        self.assertNotIn(route_full_review_to_closure(self.receipt), {"REGENERATE", "FULL_REVIEW"})
        self.assertEqual(route_full_review_to_closure({"ok": False}), HUMAN_REQUIRED)
        self.assertEqual(route_full_review_to_closure({"ok": True, "model_verdict": "MODEL_REVIEW_PASS"}), DELTA_PASS)

    def test_unreviewed_required_atom_routes_to_targeted_repair(self) -> None:
        receipt = deepcopy(self.receipt)
        receipt["ok"] = False
        receipt["errors"] = ["REQUIRED_ATOM_UNREVIEWED"]
        manifest = required_atom_manifest(self.view)
        omitted = manifest[0]["atom_id"]
        receipt["payload"]["atom_reviews"] = []
        receipt["payload"]["supported_atom_groups"] = [{
            "atom_ids": [item["atom_id"] for item in manifest if item["atom_id"] != omitted],
            "evidence_chunk_ids": ["Paper#c1"],
        }]
        receipt["required_atom_count"] = len(manifest)
        receipt["reviewed_atom_count"] = len(manifest) - 1
        self.assertEqual(route_full_review_to_closure(receipt), TARGETED_REPAIR)
        bundle = build_atomic_repair_bundle(self.card, self.view, receipt, self.chunks)
        findings = [finding for target in bundle["targets"] for finding in target["findings"]]
        self.assertTrue(any(item["finding_type"] == "UNREVIEWED_REQUIRED_ATOM" for item in findings))

    def test_many_unreviewed_atoms_stay_human_required_until_completion_review(self) -> None:
        receipt = deepcopy(self.receipt)
        receipt.update({
            "ok": False,
            "errors": ["REQUIRED_ATOM_UNREVIEWED"],
            "required_atom_count": 100,
            "reviewed_atom_count": 20,
        })
        self.assertEqual(route_full_review_to_closure(receipt), HUMAN_REQUIRED)

    def test_completion_view_and_merge_reduce_missing_atom_set(self) -> None:
        manifest = required_atom_manifest(self.view)
        base = deepcopy(self.payload)
        base["supported_atom_groups"] = []
        base["atom_reviews"] = []
        missing = missing_required_atom_ids(base, self.view)
        subset = build_full_review_completion_view(self.view, [missing[0]])
        self.assertEqual(required_atom_manifest(subset)[0]["atom_id"], missing[0])
        completion = {
            "supported_atom_groups": [{"atom_ids": [missing[0]], "evidence_chunk_ids": ["Paper#c1"]}],
            "atom_reviews": [],
        }
        merged = merge_full_review_completion_payload(base, completion)
        self.assertEqual(len(missing_required_atom_ids(merged, self.view)), len(missing) - 1)

    def test_other_invalid_full_contract_stays_human_required(self) -> None:
        receipt = deepcopy(self.receipt)
        receipt["ok"] = False
        receipt["errors"] = ["UNKNOWN_CLAIM_ID"]
        self.assertEqual(route_full_review_to_closure(receipt), HUMAN_REQUIRED)

    def test_unreviewed_key_data_anchor_maps_to_key_data_target(self) -> None:
        card = deepcopy(self.card)
        card["key_data"] = [{
            "value": "77",
            "unit": "%",
            "metric": "removal",
            "sample": "treatment",
            "stat": "reached",
            "quote": "reached 77% removal",
            "chunk_id": "Paper#c1",
        }]
        view = build_atomic_review_view(card, self.chunks)
        manifest = required_atom_manifest(view)
        key_claim = next(item for item in view["claims"] if item["target_path"] == "key_data[0]")
        omitted = key_claim["atoms"][0]["atom_id"]
        payload = deepcopy(self.payload)
        payload["atom_reviews"] = []
        payload["supported_atom_groups"] = [{
            "atom_ids": [item["atom_id"] for item in manifest if item["atom_id"] != omitted],
            "evidence_chunk_ids": ["Paper#c1"],
        }]
        receipt = {
            "ok": False,
            "model_verdict": "MODEL_REVIEW_ISSUES",
            "errors": ["REQUIRED_ATOM_UNREVIEWED"],
            "required_atom_count": len(manifest),
            "reviewed_atom_count": len(manifest) - 1,
            "payload": payload,
        }
        bundle = build_atomic_repair_bundle(card, view, receipt, self.chunks)
        key_targets = [item for item in bundle["targets"] if item["target_path"] == "key_data[0]"]
        self.assertEqual(len(key_targets), 1)
        self.assertEqual(key_targets[0]["target_kind"], "key_data")

    def test_bundle_freezes_original_findings_and_forbids_second_full(self) -> None:
        bundle = self._bundle()
        self.assertEqual(bundle["authoritative_card_sha256"], canonical_hash(self.card))
        self.assertTrue(bundle["rules"]["second_full_review_forbidden"])
        self.assertEqual(bundle["rules"]["next_review"], "DELTA_ONLY")
        self.assertEqual(len(bundle["targets"]), 1)
        self.assertEqual(bundle["targets"][0]["target_path"], "method[0]")
        self.assertEqual(len(bundle["targets"][0]["findings"]), 1)

    def test_repair_rejects_invented_chunk_and_incomplete_finding_coverage(self) -> None:
        bundle = self._bundle()
        invalid = self._valid_response(bundle)
        invalid["repairs"][0]["anchors"][0]["chunk_id"] = "Paper#invented"
        with self.assertRaises(AtomicRepairContractError):
            validate_atomic_repairs(invalid, bundle, self.chunks)

    def test_atomic_request_uses_long_output_budget(self) -> None:
        request = build_atomic_repair_request("repair")
        self.assertEqual(request["max_tokens"], 32768)
        self.assertEqual(request["max_tokens"], ATOMIC_REPAIR_MAX_TOKENS)
        self.assertEqual(request["timeout"], ATOMIC_REPAIR_TIMEOUT_SECONDS)

    def test_gateway_response_downgrades_length_truncation_without_parsing(self) -> None:
        bundle = self._bundle()
        response = {
            "text": '{"repairs":[',
            "usage": {"completion_tokens": ATOMIC_REPAIR_MAX_TOKENS},
            "raw": {"choices": [{"finish_reason": "length"}]},
        }
        repairs = validate_atomic_repair_gateway_response(response, bundle, self.chunks)
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]["action"], "no_change")
        self.assertEqual(
            repairs[0]["contract_downgrade"]["kind"],
            "TRUNCATED_RESPONSE_ALL_TARGETS_NO_CHANGE",
        )

    def test_gateway_response_parses_and_validates_complete_json(self) -> None:
        bundle = self._bundle()
        response = {
            "text": json.dumps(self._valid_response(bundle)),
            "usage": {"completion_tokens": 400},
            "raw": {"choices": [{"finish_reason": "stop"}]},
        }
        repairs = validate_atomic_repair_gateway_response(response, bundle, self.chunks)
        self.assertEqual(len(repairs), 1)

    def test_gateway_drops_one_invalid_anchor_but_keeps_grounded_repair(self) -> None:
        bundle = self._bundle()
        value = self._valid_response(bundle)
        value["repairs"][0]["anchors"].append({
            "chunk_id": "Paper#c1",
            "quote": "not a literal source substring",
            "section_page": "p1",
        })
        response = {
            "text": json.dumps(value),
            "usage": {"completion_tokens": 500},
            "raw": {"choices": [{"finish_reason": "stop"}]},
        }
        repairs = validate_atomic_repair_gateway_response(response, bundle, self.chunks)
        self.assertEqual(repairs[0]["action"], "replace")
        self.assertEqual(len(repairs[0]["anchors"]), 1)
        self.assertEqual(repairs[0]["contract_downgrade"]["kind"], "INVALID_ANCHORS_DROPPED")

    def test_gateway_downgrades_target_with_no_literal_anchor_to_no_change(self) -> None:
        bundle = self._bundle()
        value = self._valid_response(bundle)
        value["repairs"][0]["anchors"][0]["quote"] = "not a literal source substring"
        response = {
            "text": json.dumps(value),
            "usage": {"completion_tokens": 500},
            "raw": {"choices": [{"finish_reason": "stop"}]},
        }
        repairs = validate_atomic_repair_gateway_response(response, bundle, self.chunks)
        self.assertEqual(repairs[0]["action"], "no_change")
        self.assertIsNone(repairs[0]["replacement"])
        self.assertEqual(repairs[0]["anchors"], [])
        self.assertEqual(
            repairs[0]["contract_downgrade"]["kind"],
            "TARGET_DOWNGRADED_TO_NO_CHANGE",
        )
        invalid = self._valid_response(bundle)
        invalid["repairs"][0]["finding_ids"] = []
        with self.assertRaises(AtomicRepairContractError):
            validate_atomic_repairs(invalid, bundle, self.chunks)

    def test_delta_contains_original_issue_before_after_diff_and_no_unrelated_claim(self) -> None:
        bundle = self._bundle()
        repairs = validate_atomic_repairs(self._valid_response(bundle), bundle, self.chunks)
        application = apply_atomic_repairs(self.card, bundle, repairs)
        delta = build_atomic_delta_view(self.card, application, self.view, self.receipt, bundle, self.chunks)
        self.assertFalse(delta["whole_card_scope"])
        self.assertTrue(delta["second_full_review_forbidden"])
        self.assertEqual(delta["before_card_sha256"], canonical_hash(self.card))
        self.assertNotEqual(delta["before_card_sha256"], delta["after_card_sha256"])
        self.assertIn("Five reactors", delta["authoritative_diff"])
        self.assertIn("Eight reactors", delta["authoritative_diff"])
        self.assertEqual(len(delta["original_findings"]), 1)
        targets = {item["target_path"] for item in delta["claims"]}
        self.assertIn("method[0]", targets)
        self.assertNotIn("research_question", targets)

    def test_failed_list_delta_is_deleted_and_tombstoned(self) -> None:
        bundle = self._bundle()
        repairs = validate_atomic_repairs(self._valid_response(bundle), bundle, self.chunks)
        application = apply_atomic_repairs(self.card, bundle, repairs)
        delta = build_atomic_delta_view(self.card, application, self.view, self.receipt, bundle, self.chunks)
        payload = {"claim_reviews": []}
        for claim in delta["claims"]:
            payload["claim_reviews"].append(
                {
                    "claim_id": claim["claim_id"],
                    "atom_reviews": [],
                    "cross_field_conflicts": [],
                    "final_status": (
                        "RESOLVED" if str(claim.get("claim_type", "")).endswith("_source_anchor") else "STILL_FAIL"
                    ),
                }
            )
        resolution = classify_delta_resolution(payload, delta, bundle, application)
        result = prune_unresolved_targets(application["card"], resolution, payload, delta, self.chunks)
        self.assertEqual(result["outcome"], PASS_WITH_TOMBSTONES)
        self.assertTrue(result["tombstones"])
        self.assertNotIn("Eight reactors were operated.", result["card"]["method"])
        self.assertEqual(result["deletion_semantics"]["mode"], "logical_removal_only")
        self.assertEqual(result["tombstones"][0]["status"], "logically_removed_from_active_card")
        self.assertIn("removed_value", result["tombstones"][0])

    def test_failed_scalar_delta_becomes_governed_field_omission(self) -> None:
        card = deepcopy(self.card)
        view = build_atomic_review_view(card, self.chunks)
        claim = next(item for item in view["claims"] if item["target_path"] == "key_results")
        payload = deepcopy(self.payload)
        payload["coverage"]["method"] = "SUPPORTED"
        payload["coverage"]["key_results"] = "PARTIAL"
        payload["atom_reviews"] = [{
            "claim_id": claim["claim_id"],
            "atom_id": claim["atoms"][0]["atom_id"],
            "status": "UNSUPPORTED",
            "evidence_chunk_ids": ["Paper#c1"],
            "reason": "unsupported scope",
        }]
        receipt = {"ok": True, "model_verdict": "MODEL_REVIEW_ISSUES", "payload": payload}
        bundle = build_atomic_repair_bundle(card, view, receipt, self.chunks)
        target = bundle["targets"][0]
        response = {"repairs": [{
            "target_id": target["target_id"],
            "finding_ids": [item["finding_id"] for item in target["findings"]],
            "action": "no_change",
            "replacement": None,
            "anchors": [],
            "reason": "No safe source-grounded replacement.",
        }]}
        repairs = validate_atomic_repairs(response, bundle, self.chunks)
        application = apply_atomic_repairs(card, bundle, repairs)
        delta = build_atomic_delta_view(card, application, view, receipt, bundle, self.chunks)
        delta_payload = {"claim_reviews": [{
            "claim_id": item["claim_id"], "atom_reviews": [], "cross_field_conflicts": [], "final_status": "STILL_FAIL",
        } for item in delta["claims"]]}
        resolution = classify_delta_resolution(delta_payload, delta, bundle, application)
        result = prune_unresolved_targets(application["card"], resolution, delta_payload, delta, self.chunks)
        self.assertEqual(result["outcome"], PASS_WITH_TOMBSTONES)
        self.assertIsNone(result["card"]["key_results"])
        self.assertEqual(result["governed_omitted_fields"], ["key_results"])
        self.assertTrue(result["post_prune_precheck"]["ok"])

    def test_failed_scalar_claim_is_deleted_when_exact_and_field_survives(self) -> None:
        card = deepcopy(self.card)
        card["key_results"] = "Removal reached 77%. Methane yield increased 20%."
        failed_text = "Removal reached 77%."
        delta_view = {
            "claims": [{
                "claim_id": "KR-drop",
                "claim_type": "key_results",
                "target_path": "key_results",
                "original_text": failed_text,
                "atoms": [],
            }],
            "original_findings": [],
        }
        resolution = {
            "unresolved_targets": [{
                "target_kind": "scalar",
                "target_path": "key_results",
                "realized_target_path": "key_results",
                "failed_claim_ids": ["KR-drop"],
                "finding_ids": ["F-drop"],
            }],
        }
        result = prune_unresolved_targets(card, resolution, {}, delta_view, self.chunks)
        self.assertEqual(result["outcome"], PASS_WITH_TOMBSTONES)
        self.assertEqual(result["card"]["key_results"], "Methane yield increased 20%.")
        self.assertEqual(result["tombstones"][0]["claim_id"], "KR-drop")
        self.assertIn("Original value", result["tombstones"][0]["retention"])

    def test_failed_scalar_claim_blocks_when_text_is_ambiguous(self) -> None:
        card = deepcopy(self.card)
        card["key_results"] = "Removal improved. Removal improved."
        delta_view = {
            "claims": [{
                "claim_id": "KR-ambiguous",
                "claim_type": "key_results",
                "target_path": "key_results",
                "original_text": "Removal improved.",
                "atoms": [],
            }],
            "original_findings": [],
        }
        resolution = {
            "unresolved_targets": [{
                "target_kind": "scalar",
                "target_path": "key_results",
                "realized_target_path": "key_results",
                "failed_claim_ids": ["KR-ambiguous"],
                "finding_ids": ["F-ambiguous"],
            }],
        }
        result = prune_unresolved_targets(card, resolution, {}, delta_view, self.chunks)
        self.assertEqual(result["outcome"], HUMAN_REQUIRED)
        self.assertEqual(result["card"]["key_results"], card["key_results"])

    def test_failed_only_list_item_becomes_governed_field_omission(self) -> None:
        card = deepcopy(self.card)
        card["method"] = ["Five reactors were operated."]
        delta_view = {
            "claims": [{
                "claim_id": "MT-drop",
                "claim_type": "method",
                "target_path": "method[0]",
                "original_text": "Five reactors were operated.",
                "atoms": [],
            }],
            "original_findings": [],
        }
        resolution = {
            "unresolved_targets": [{
                "target_kind": "list_item",
                "target_path": "method[0]",
                "realized_target_path": "method[0]",
                "failed_claim_ids": ["MT-drop"],
                "finding_ids": ["F-drop"],
            }],
        }
        result = prune_unresolved_targets(card, resolution, {}, delta_view, self.chunks)
        self.assertEqual(result["outcome"], PASS_WITH_TOMBSTONES)
        self.assertIsNone(result["card"]["method"])
        self.assertEqual(result["governed_omitted_fields"], ["method"])
        self.assertEqual(result["tombstones"][0]["removed_value"], "Five reactors were operated.")

    def test_final_precheck_requires_tombstone_for_missing_field(self) -> None:
        card = deepcopy(self.card)
        card["method"] = None
        view = build_atomic_review_view(card, self.chunks)
        without_tombstone = governed_omission_precheck(card, self.chunks, view)
        self.assertFalse(without_tombstone["ok"])
        card["omissions"] = [{"location": "by_field.method"}]
        view = build_atomic_review_view(card, self.chunks)
        with_tombstone = governed_omission_precheck(card, self.chunks, view)
        self.assertTrue(with_tombstone["ok"])
        self.assertEqual(with_tombstone["governed_omitted_fields"], ["method"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
