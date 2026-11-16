from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

import jsonschema

from retrieval_weighting.contracts import make_hashed_payload, typed_payload_hash
from memorive.documents.card_states.derived_knowledge_artifact import LEGAL_DERIVED_ARTIFACT_TRANSITIONS, make_controlled_activation_preview, make_pending_artifact_projection, validate_derived_knowledge_artifact_projection
from memorive.documents.card_states.errors import AdmissionBlocked, RealStateTransitionForbidden
from memorive.documents.card_states.knowledge_submission import make_controlled_ingest_dry_run_receipt, validate_controlled_ingest_dry_run_receipt
from knowledge_feedback.contracts import assert_candidate_binds_snapshot, knowledge_text_hash, project_candidate_transition, validate_derived_knowledge_candidate, validate_source_snapshot
from knowledge_feedback.eligibility import build_eligibility_report
from knowledge_feedback.errors import ContractRejected, SourceIneligible
from knowledge_feedback.lineage import build_lineage_ledger, build_replay_plan, validate_lineage_ledger, validate_replay_plan
from decision_log.knowledge_decision import KnowledgeDecisionError, assert_decision_binds_candidate
from memorive.documents.registry.knowledge_feedback_adapter import make_knowledge_registration_preview
from memorive.documents.registry.errors import ContractValidationError
from tests.modules.knowledge_feedback.feedback_helpers.controlled_knowledge_feedback import audit_self_reinforcement
from tests.modules.knowledge_feedback.feedback_helpers.controlled_knowledge_feedback_fixture import (
    EVALUATED_AT,
    EXPIRES_AT,
    make_baseline_bundle,
)


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
CONTRACTS = Path(__file__).resolve().parents[4] / "contracts" / "KnowledgeFeedback_知识反馈" / "10_模块契约"
SCHEMA_PATH = CONTRACTS / "Memorive_KNOWLEDGE_FEEDBACK_DerivedKnowledgeCandidate_模块契约_r0.2.schema.json"
SOURCE_SCHEMA_PATH = CONTRACTS / "Memorive_KNOWLEDGE_FEEDBACK_KnowledgeSourceSnapshot_模块契约_r0.1.schema.json"


def rehash(value: dict) -> dict:
    copied = deepcopy(value)
    copied.pop("content_hash", None)
    return make_hashed_payload(copied)


class ControlledKnowledgeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle = make_baseline_bundle(suffix=self._testMethodName.lower())

    def test_baseline_exact_contract_and_json_schema(self) -> None:
        candidate = validate_derived_knowledge_candidate(self.bundle["candidate"])
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(candidate)
        source_schema = json.loads(SOURCE_SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(source_schema).validate(
            self.bundle["source_snapshot"]
        )
        self.assertEqual(candidate["review_state"], "approved_for_controlled_ingest")
        self.assertTrue(candidate["not_literature_card"])
        self.assertEqual(candidate["scientific_judgment_status"], "NOT_ASSESSED")

    def test_tampered_candidate_hash_is_rejected(self) -> None:
        candidate = deepcopy(self.bundle["candidate"])
        candidate["reusable_claim"] += " tamper"
        with self.assertRaises(ContractRejected):
            validate_derived_knowledge_candidate(candidate)

    def test_only_frozen_candidate_transitions_are_legal(self) -> None:
        candidate = self.bundle["candidate_v1"]
        review = project_candidate_transition(candidate, target_state="review_pending")
        self.assertEqual(review["common_object"]["revision"], 2)
        self.assertIsNotNone(review["common_object"]["supersedes"])
        with self.assertRaisesRegex(ContractRejected, "ILLEGAL_DKC_TRANSITION"):
            project_candidate_transition(review, target_state="candidate")
        with self.assertRaisesRegex(ContractRejected, "ILLEGAL_DKC_TRANSITION"):
            project_candidate_transition(self.bundle["candidate"], target_state="rejected")

    def test_source_snapshot_is_sorted_frozen_and_non_derived(self) -> None:
        snapshot = validate_source_snapshot(self.bundle["source_snapshot"])
        refs = [item["source_ref"] for item in snapshot["source_items"]]
        self.assertEqual(refs, sorted(refs))
        self.assertEqual(snapshot["real_source_count"], 0)
        self.assertTrue(any(not item["derived_only"] for item in snapshot["source_items"]))
        self.assertTrue(all("omission_boundary" in item for item in snapshot["source_items"]))

    def test_card_v6_complete_and_passed_with_omissions_are_explicit(self) -> None:
        complete = next(
            item
            for item in self.bundle["source_snapshot"]["source_items"]
            if item["source_ref"] == self.bundle["analysis_ref"]
        )["omission_boundary"]
        self.assertEqual(complete["completion_status"], "complete")
        self.assertEqual(complete["tombstones"], [])

        passed = make_baseline_bundle(
            suffix=f"{self._testMethodName}_passed", omission_mode="passed_safe"
        )
        boundary = next(
            item
            for item in passed["source_snapshot"]["source_items"]
            if item["source_ref"] == passed["analysis_ref"]
        )["omission_boundary"]
        self.assertEqual(boundary["completion_status"], "passed_with_omissions")
        self.assertEqual(len(boundary["tombstones"]), 1)
        self.assertEqual(
            boundary["tombstones"][0]["attempts"]["deepseek_repair_calls"], 2
        )
        self.assertTrue(passed["eligibility_report"]["candidate_contract_eligible"])

    def test_tombstoned_target_and_exact_removed_content_are_blocked(self) -> None:
        target = make_baseline_bundle(
            suffix=f"{self._testMethodName}_target", omission_mode="target_recall"
        )
        self.assertEqual(
            target["eligibility_report"]["issue_codes"],
            ["CANDIDATE_USES_TOMBSTONED_SOURCE_TARGET"],
        )
        content = make_baseline_bundle(
            suffix=f"{self._testMethodName}_content", omission_mode="content_recall"
        )
        self.assertEqual(
            content["eligibility_report"]["issue_codes"],
            ["CANDIDATE_RECALLS_TOMBSTONED_CONTENT_HASH"],
        )
        self.assertEqual(
            content["candidate"]["reusable_claim_hash"],
            knowledge_text_hash(content["omitted_text_fixture_only"]),
        )

    def test_omission_manifest_status_duplicate_and_plaintext_fail_closed(self) -> None:
        for mutation, code in (
            ("manifest", "SOURCE_OMISSION_MANIFEST_HASH_MISMATCH"),
            ("status", "SOURCE_OMISSION_STATUS_TOMBSTONE_MISMATCH"),
            ("duplicate", "SOURCE_OMISSION_TOMBSTONE_DUPLICATE"),
            ("plaintext", "SOURCE_OMISSION_PLAINTEXT_REMOVED_VALUE_FORBIDDEN"),
            ("manifest_basis", "SOURCE_OMISSION_MANIFEST_HASH_BASIS_INVALID"),
            ("value_basis", "SOURCE_OMISSION_REMOVED_VALUE_HASH_BASIS_INVALID"),
        ):
            with self.subTest(mutation=mutation):
                bundle = make_baseline_bundle(
                    suffix=f"{self._testMethodName}_{mutation}",
                    omission_mode="passed_safe",
                )
                snapshot = deepcopy(bundle["source_snapshot"])
                snapshot.pop("content_hash")
                boundary = next(
                    item
                    for item in snapshot["source_items"]
                    if item["source_ref"] == bundle["analysis_ref"]
                )["omission_boundary"]
                if mutation == "manifest":
                    boundary["tombstones"][0]["target_ref"] += "/tampered"
                elif mutation == "status":
                    boundary["completion_status"] = "complete"
                elif mutation == "duplicate":
                    boundary["tombstones"].append(
                        deepcopy(boundary["tombstones"][0])
                    )
                elif mutation == "plaintext":
                    boundary["plaintext_removed_values_present"] = True
                elif mutation == "manifest_basis":
                    boundary["omission_manifest_hash_basis"] = "UNKNOWN"
                else:
                    boundary["tombstones"][0]["removed_value_hash_basis"] = "UNKNOWN"
                with self.assertRaisesRegex(SourceIneligible, code):
                    validate_source_snapshot(make_hashed_payload(snapshot))

    def test_omission_boundary_carries_hashes_not_removed_plaintext(self) -> None:
        bundle = make_baseline_bundle(
            suffix=self._testMethodName, omission_mode="passed_safe"
        )
        boundary = next(
            item
            for item in bundle["source_snapshot"]["source_items"]
            if item["source_ref"] == bundle["analysis_ref"]
        )["omission_boundary"]
        serialized = json.dumps(boundary, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(bundle["omitted_text_fixture_only"], serialized)
        self.assertNotIn('"removed_value"', serialized)
        self.assertIn('"removed_value_hash"', serialized)

    def test_source_usage_bindings_cover_every_parent(self) -> None:
        candidate = deepcopy(self.bundle["candidate_v1"])
        candidate.pop("content_hash")
        candidate["source_usage_bindings"] = candidate["source_usage_bindings"][:-1]
        with self.assertRaisesRegex(
            ContractRejected, "SOURCE_USAGE_BINDINGS_PARENT_SET_MISMATCH"
        ):
            validate_derived_knowledge_candidate(make_hashed_payload(candidate))
        candidate = deepcopy(self.bundle["candidate_v1"])
        candidate.pop("content_hash")
        candidate["reusable_claim_hash_basis"] = "UNKNOWN"
        with self.assertRaisesRegex(
            ContractRejected, "REUSABLE_CLAIM_HASH_BASIS_INVALID"
        ):
            validate_derived_knowledge_candidate(make_hashed_payload(candidate))

    def test_derived_only_source_set_is_rejected(self) -> None:
        snapshot = deepcopy(self.bundle["source_snapshot"])
        snapshot.pop("content_hash")
        for item in snapshot["source_items"]:
            item["source_kind"] = "SYNTHETIC_DERIVED"
            item["derived_only"] = True
            item["original_source_refs"] = ["source://synthetic/root"]
        with self.assertRaisesRegex(SourceIneligible, "DERIVED_ONLY_SOURCE_SET_BLOCKED"):
            validate_source_snapshot(make_hashed_payload(snapshot))

    def test_candidate_must_bind_exact_snapshot(self) -> None:
        other = make_baseline_bundle(suffix=f"{self._testMethodName}_other")
        with self.assertRaisesRegex(SourceIneligible, "SNAPSHOT_REF_MISMATCH"):
            assert_candidate_binds_snapshot(self.bundle["candidate"], other["source_snapshot"])

    def test_decision_binds_id_revision_hash_and_scope(self) -> None:
        receipt = assert_decision_binds_candidate(
            self.bundle["decision_receipt"],
            self.bundle["candidate"],
            evaluated_at=EVALUATED_AT,
            allow_synthetic=True,
        )
        self.assertTrue(receipt["controlled_ingest_authorized"])
        wrong = deepcopy(receipt)
        wrong.pop("content_hash")
        wrong["candidate_hash"] = typed_payload_hash({"wrong": True})
        with self.assertRaisesRegex(KnowledgeDecisionError, "CANDIDATE_HASH_MISMATCH"):
            assert_decision_binds_candidate(
                make_hashed_payload(wrong),
                self.bundle["candidate"],
                evaluated_at=EVALUATED_AT,
                allow_synthetic=True,
            )

    def test_decision_future_and_exact_expiry_are_rejected(self) -> None:
        with self.assertRaisesRegex(KnowledgeDecisionError, "NOT_YET_EFFECTIVE"):
            assert_decision_binds_candidate(
                self.bundle["decision_receipt"],
                self.bundle["candidate"],
                evaluated_at="2026-08-08T00:59:59Z",
                allow_synthetic=True,
                require_synthetic=True,
            )
        with self.assertRaisesRegex(KnowledgeDecisionError, "RECEIPT_EXPIRED"):
            assert_decision_binds_candidate(
                self.bundle["decision_receipt"],
                self.bundle["candidate"],
                evaluated_at=EXPIRES_AT,
                allow_synthetic=True,
                require_synthetic=True,
            )

    def test_construction_a_rejects_purported_real_decision(self) -> None:
        receipt = deepcopy(self.bundle["decision_receipt"])
        receipt.pop("content_hash")
        receipt["synthetic_fixture"] = False
        receipt["authority_class"] = "USER_OR_DESIGNATED_HUMAN"
        receipt["decided_by"] = "USER"
        receipt = make_hashed_payload(receipt)
        with self.assertRaisesRegex(KnowledgeDecisionError, "REAL_DECISION_NOT_ALLOWED"):
            assert_decision_binds_candidate(
                receipt,
                self.bundle["candidate"],
                evaluated_at=EVALUATED_AT,
                allow_synthetic=True,
                require_synthetic=True,
            )

    def test_missing_decision_produces_fail_closed_field_report(self) -> None:
        report = build_eligibility_report(
            report_id=f"missing_decision_{self._testMethodName}",
            candidate=self.bundle["candidate_v2"],
            source_snapshot=self.bundle["source_snapshot"],
            decision_receipt=None,
            penalty_evidence=self.bundle["penalty_evidence"],
            evaluated_at=EVALUATED_AT,
        )
        self.assertFalse(report["candidate_contract_eligible"])
        self.assertFalse(report["dry_run_eligible"])
        self.assertIn("DECISION_RECEIPT_HASH_OR_SHAPE_INVALID", report["issue_codes"])
        self.assertIn("DECISION_NOT_VERIFIABLE", report["issue_codes"])
        self.assertFalse(report["run_execution_authorized"])

    def test_penalty_evidence_must_bind_exact_penalty_hash(self) -> None:
        other = make_baseline_bundle(suffix=f"{self._testMethodName}_other")
        report = build_eligibility_report(
            report_id=f"report_{self._testMethodName}",
            candidate=self.bundle["candidate"],
            source_snapshot=self.bundle["source_snapshot"],
            decision_receipt=self.bundle["decision_receipt"],
            penalty_evidence=other["penalty_evidence"],
            evaluated_at=EVALUATED_AT,
        )
        self.assertFalse(report["candidate_contract_eligible"])
        self.assertIn("PENALTY_EXACT_CANDIDATE_BINDING_MISMATCH", report["issue_codes"])

    def test_penalty_evidence_ref_must_bind_exact_candidate_ref(self) -> None:
        evidence = deepcopy(self.bundle["penalty_evidence"])
        evidence.pop("content_hash")
        evidence["evidence_id"] = "dpee_other_exact_ref"
        report = build_eligibility_report(
            report_id=f"report_{self._testMethodName}",
            candidate=self.bundle["candidate"],
            source_snapshot=self.bundle["source_snapshot"],
            decision_receipt=self.bundle["decision_receipt"],
            penalty_evidence=make_hashed_payload(evidence),
            evaluated_at=EVALUATED_AT,
        )
        self.assertFalse(report["candidate_contract_eligible"])
        self.assertIn("PENALTY_EXACT_CANDIDATE_BINDING_MISMATCH", report["issue_codes"])

    def test_ineligible_report_cannot_generate_dry_run(self) -> None:
        rejected = make_baseline_bundle(decision="REJECT", suffix=self._testMethodName)
        with self.assertRaisesRegex(AdmissionBlocked, "NOT_ELIGIBLE"):
            make_controlled_ingest_dry_run_receipt(
                receipt_id="cidr_blocked",
                candidate=rejected["candidate"],
                eligibility_report=rejected["eligibility_report"],
            )

    def test_dry_run_and_registry_preview_have_zero_side_effects(self) -> None:
        dry_run = self.bundle["dry_run_receipt"]
        self.assertEqual(sum(dry_run["side_effect_counters"].values()), 0)
        preview = make_knowledge_registration_preview(
            preview_id=f"artifact_registry_{self._testMethodName}",
            candidate=self.bundle["candidate"],
            dry_run_receipt=dry_run,
        )
        self.assertFalse(preview["artifact_registry_is_state_owner"])
        self.assertEqual(preview["registry_mutation_count"], 0)
        self.assertEqual(preview["index_mutation_count"], 0)

    def test_dry_run_and_registry_preview_require_exact_identity_revision(self) -> None:
        report = deepcopy(self.bundle["eligibility_report"])
        report.pop("content_hash")
        report["candidate_revision"] += 1
        with self.assertRaisesRegex(AdmissionBlocked, "REVISION_MISMATCH"):
            make_controlled_ingest_dry_run_receipt(
                receipt_id=f"cidr_{self._testMethodName}",
                candidate=self.bundle["candidate"],
                eligibility_report=make_hashed_payload(report),
            )

        dry_run = deepcopy(self.bundle["dry_run_receipt"])
        dry_run.pop("content_hash")
        dry_run["candidate_id"] = "dkc_other_identity"
        with self.assertRaisesRegex(ContractValidationError, "ID_MISMATCH"):
            make_knowledge_registration_preview(
                preview_id=f"artifact_registry_{self._testMethodName}",
                candidate=self.bundle["candidate"],
                dry_run_receipt=make_hashed_payload(dry_run),
            )

    def test_dry_run_side_effect_boolean_zero_is_rejected(self) -> None:
        dry_run = deepcopy(self.bundle["dry_run_receipt"])
        dry_run.pop("content_hash")
        dry_run["side_effect_counters"] = dict(dry_run["side_effect_counters"])
        dry_run["side_effect_counters"]["external_calls"] = False
        with self.assertRaisesRegex(
            RealStateTransitionForbidden, "SIDE_EFFECT_NONZERO"
        ):
            validate_controlled_ingest_dry_run_receipt(
                make_hashed_payload(dry_run)
            )

    def test_pending_projection_and_activation_preview_do_not_activate(self) -> None:
        artifact = make_pending_artifact_projection(
            artifact_id=f"artifact_{self._testMethodName}", candidate=self.bundle["candidate"]
        )
        preview = make_controlled_activation_preview(
            preview_id=f"activation_{self._testMethodName}", artifact=artifact
        )
        self.assertEqual(LEGAL_DERIVED_ARTIFACT_TRANSITIONS["pending"], {"active", "quarantined"})
        self.assertFalse(artifact["materialized"])
        self.assertFalse(preview["transition_executed"])
        self.assertEqual(preview["state_after"], "pending")
        forged = deepcopy(artifact)
        forged.pop("content_hash")
        forged["state"] = "active"
        forged["activation_receipt_ref"] = "fixture://activation/forged"
        with self.assertRaisesRegex(
            RealStateTransitionForbidden, "STATE_MUST_BE_PENDING"
        ):
            validate_derived_knowledge_artifact_projection(
                make_hashed_payload(forged)
            )

    def test_lineage_detects_self_cycle_dangling_and_duplicate(self) -> None:
        common_hash = typed_payload_hash({"fixture": self._testMethodName})
        entries = [
            {
                "node_ref": "node://a",
                "revision": 1,
                "content_hash": common_hash,
                "parent_refs": ["node://a", "node://b", "node://missing"],
                "supersedes_ref": None,
                "ingest_key": "duplicate:key",
                "status": "candidate",
            },
            {
                "node_ref": "node://b",
                "revision": 1,
                "content_hash": common_hash,
                "parent_refs": ["node://a"],
                "supersedes_ref": None,
                "ingest_key": "duplicate:key",
                "status": "candidate",
            },
        ]
        ledger = build_lineage_ledger(
            ledger_id=f"ledger_{self._testMethodName}",
            entries=entries,
            allowed_external_refs=[],
        )
        self.assertFalse(ledger["valid"])
        self.assertEqual(
            set(ledger["issue_codes"]),
            {"LINEAGE_SELF_REFERENCE", "LINEAGE_CYCLE", "LINEAGE_DANGLING_REFERENCE", "DUPLICATE_INGEST_KEY"},
        )

    def test_lineage_validator_recomputes_reported_findings(self) -> None:
        ledger = deepcopy(self.bundle["lineage_ledger"])
        ledger.pop("content_hash")
        first = ledger["entries"][0]
        first["parent_refs"] = sorted(
            set(first["parent_refs"] + [first["node_ref"]])
        )
        with self.assertRaisesRegex(ContractRejected, "SELF_REF_FINDINGS_DRIFT"):
            validate_lineage_ledger(make_hashed_payload(ledger))

    def test_replay_plan_rejects_malformed_hash_descriptor(self) -> None:
        plan = build_replay_plan(
            plan_id=f"malformed_{self._testMethodName}",
            action="REPLAY",
            candidate_ref="fixture://candidate/exact",
            candidate_hash=self.bundle["candidate"]["content_hash"],
            reason="synthetic descriptor test",
        )
        plan.pop("content_hash")
        plan["candidate_hash"] = dict(plan["candidate_hash"])
        plan["candidate_hash"]["algorithm"] = "md5"
        with self.assertRaisesRegex(ContractRejected, "CANDIDATE_HASH_INVALID"):
            validate_replay_plan(make_hashed_payload(plan))

    def test_replay_plan_requires_new_revision_and_preserves_old_bytes(self) -> None:
        before = deepcopy(self.bundle["candidate"])
        plan = build_replay_plan(
            plan_id=f"replay_{self._testMethodName}",
            action="REPLAY",
            candidate_ref="fixture://candidate/exact",
            candidate_hash=self.bundle["candidate"]["content_hash"],
            reason="synthetic replay contract test",
        )
        self.assertTrue(plan["new_revision_required"])
        self.assertFalse(plan["prior_bytes_mutated"])
        self.assertFalse(plan["execution_authorized"])
        self.assertEqual(before, self.bundle["candidate"])

    def test_self_reinforcement_audit_separates_structure_from_science(self) -> None:
        audit = audit_self_reinforcement(
            audit_id=f"audit_{self._testMethodName}",
            candidate=self.bundle["candidate"],
            lineage_ledger=self.bundle["lineage_ledger"],
            retrieval_rows=self.bundle["retrieval_rows"],
        )
        self.assertEqual(audit["verification_result"], "PASS")
        self.assertEqual(audit["scientific_quality"], "NOT_ASSESSED")
        self.assertEqual(audit["feedback_influence_count"], 0)
        self.assertEqual(audit["tombstoned_recall_count"], 0)

    def test_self_reinforcement_detects_tombstoned_source_recall(self) -> None:
        bundle = make_baseline_bundle(
            suffix=self._testMethodName, omission_mode="passed_safe"
        )
        rows = deepcopy(bundle["retrieval_rows"])
        rows.append(
            {
                "result_ref": bundle["discarded_target"],
                "provenance_state": "derived",
                "supports_candidate": True,
                "influences_candidate_qualification": False,
            }
        )
        audit = audit_self_reinforcement(
            audit_id=f"audit_{self._testMethodName}",
            candidate=bundle["candidate"],
            lineage_ledger=bundle["lineage_ledger"],
            retrieval_rows=rows,
            tombstoned_refs=bundle["tombstoned_refs"],
        )
        self.assertEqual(audit["verification_result"], "FAIL")
        self.assertEqual(audit["tombstoned_recall_count"], 1)
        self.assertIn(
            "SELF_REINFORCEMENT_TOMBSTONED_SOURCE_RECALL",
            audit["issue_codes"],
        )


if __name__ == "__main__":
    unittest.main()
