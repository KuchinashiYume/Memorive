"""Public-safe synthetic fixture factory for KNOWLEDGE-FEEDBACK Construction A and Shadow B."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from retrieval_weighting.contracts import typed_payload_hash
from retrieval_weighting.derived_penalty import build_derived_penalty_record
from memorive.documents.card_states.knowledge_submission import make_controlled_ingest_dry_run_receipt
from memorive.documents.card_states.derived_knowledge_artifact import (
    make_controlled_activation_preview,
    make_pending_artifact_projection,
)
from knowledge_feedback.contracts import candidate_version_ref, knowledge_text_hash, make_derived_knowledge_candidate, make_source_omission_boundary, make_source_snapshot, project_candidate_transition
from knowledge_feedback.eligibility import build_eligibility_report
from knowledge_feedback.lineage import build_lineage_ledger
from knowledge_feedback.penalty import build_penalty_eligibility_evidence
from decision_log.knowledge_decision import build_synthetic_human_decision_fixture
from memorive.documents.registry.knowledge_feedback_adapter import make_knowledge_registration_preview
from .controlled_knowledge_feedback import audit_self_reinforcement


DECIDED_AT = "2026-08-08T01:00:00Z"
EXPIRES_AT = "2026-08-09T01:00:00Z"
EVALUATED_AT = "2026-08-08T02:00:00Z"
SNAPSHOT_FROZEN_AT = "2026-08-08T00:30:00Z"


def _source_item(
    source_ref: str,
    *,
    source_kind: str,
    original_source_refs: list[str],
    derived_only: bool,
    field_anchors: list[str],
    omission_boundary: dict[str, Any],
    eligible: bool = True,
    issue_codes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "source_ref": source_ref,
        "source_kind": source_kind,
        "revision": 1,
        "content_hash": typed_payload_hash({"synthetic_source_ref": source_ref}),
        "review_status": "synthetic_control" if source_kind == "SYNTHETIC_ORIGINAL" else "validated",
        "verification_result": "PASS",
        "source_locator": f"fixture://source/{source_ref.rsplit('/', 1)[-1]}",
        "field_anchors": sorted(field_anchors),
        "verification_evidence_refs": [f"fixture://verification/{source_ref.rsplit('/', 1)[-1]}"],
        "original_source_refs": sorted(original_source_refs),
        "derived_only": derived_only,
        "eligible": eligible,
        "issue_codes": sorted(issue_codes or []),
        "omission_boundary": deepcopy(omission_boundary),
    }


def _synthetic_tombstone(
    *, suffix: str, target_ref: str, removed_text: str
) -> dict[str, Any]:
    return {
        "tombstone_ref": f"fixture://omission/{suffix}/analysis/discarded/0",
        "target_ref": target_ref,
        "removed_value_hash": knowledge_text_hash(removed_text),
        "removed_value_hash_basis": "JCS_KNOWLEDGE_VALUE_WRAPPER_V1",
        "attempts": {
            "sonnet_initial_review_calls": 1,
            "deepseek_repair_calls": 2,
            "sonnet_targeted_review_calls": 0,
        },
        "initial_report_ref": f"fixture://review/{suffix}/initial",
        "final_report_ref": None,
    }


def make_baseline_bundle(
    *,
    decision: str = "APPROVE",
    suffix: str = "001",
    omission_mode: str = "complete",
) -> dict[str, Any]:
    original_ref = f"source://synthetic/original/{suffix}"
    analysis_ref = f"source://synthetic/analysis/{suffix}"
    object_id = f"dkc_test_{suffix}"
    original_targets = [
        f"fixture://field/{suffix}/original/claim",
        f"fixture://field/{suffix}/original/method",
    ]
    analysis_targets = [
        f"fixture://field/{suffix}/analysis/claim",
        f"fixture://field/{suffix}/analysis/method",
    ]
    discarded_target = f"fixture://field/{suffix}/analysis/discarded/0"
    omitted_text = f"Synthetic omitted source item {suffix}."
    if omission_mode == "complete":
        analysis_boundary = make_source_omission_boundary(
            applicability="INHERITED_CARD_V6",
            completion_status="complete",
            tombstones=[],
        )
    elif omission_mode in {"passed_safe", "target_recall", "content_recall"}:
        analysis_boundary = make_source_omission_boundary(
            applicability="INHERITED_CARD_V6",
            completion_status="passed_with_omissions",
            tombstones=[
                _synthetic_tombstone(
                    suffix=suffix,
                    target_ref=discarded_target,
                    removed_text=omitted_text,
                )
            ],
        )
    else:
        raise ValueError(f"UNSUPPORTED_OMISSION_MODE:{omission_mode}")
    original_boundary = make_source_omission_boundary(
        applicability="NOT_APPLICABLE",
        completion_status="NOT_APPLICABLE",
        tombstones=[],
    )
    upstream = {
        "initialization_common_object": typed_payload_hash({"authority": "Initialization_COMMON_OBJECT_V2"}),
        "configuration_handoff": typed_payload_hash({"authority": "CONFIGURATION_HANDOFF"}),
        "intake_handoff": typed_payload_hash({"authority": "INTAKE_HANDOFF"}),
    }
    source_snapshot = make_source_snapshot(
        snapshot_id=f"kss_test_{suffix}",
        source_items=[
            _source_item(
                original_ref,
                source_kind="SYNTHETIC_ORIGINAL",
                original_source_refs=[],
                derived_only=False,
                field_anchors=original_targets,
                omission_boundary=original_boundary,
            ),
            _source_item(
                analysis_ref,
                source_kind="SYNTHETIC_VERIFIED_ANALYSIS",
                original_source_refs=[original_ref],
                derived_only=False,
                field_anchors=analysis_targets,
                omission_boundary=analysis_boundary,
            ),
        ],
        upstream_authorities=upstream,
        frozen_at=SNAPSHOT_FROZEN_AT,
    )
    penalty_policy = {
        "policy_version": "DERIVED_PENALTY_POLICY_TEST_V1",
        "type_penalties": {"synthetic_method_note": 0.5},
        "unknown_provenance_issue": "UNKNOWN_PROVENANCE_AUTHORITY_BLOCKED",
        "override_contract_version": "DERIVED_PENALTY_OVERRIDE_V1",
        "override_event_allowlist": [],
        "authority_only": True,
    }
    penalty_record = build_derived_penalty_record(
        candidate_id=object_id,
        provenance_state="derived",
        derived_type="synthetic_method_note",
        parent_refs=[analysis_ref, original_ref],
        source_refs=[original_ref],
        generation_basis_ref=f"fixture://generation/{suffix}",
        producer_version="KNOWLEDGE_FEEDBACK_KNOWLEDGE_FEEDBACK_CONTROLLED_KNOWLEDGE_FEEDBACK_V2",
        lineage_refs=[f"fixture://lineage/{suffix}"],
        penalty_policy=penalty_policy,
        design_prior=0.5,
        base_penalty=0.5,
        override=None,
        effective_penalty=0.5,
        authority_blocked=False,
        issue_codes=[],
    )
    reusable_claim = (
        omitted_text
        if omission_mode == "content_recall"
        else f"Synthetic method note {suffix}; this is not a literature claim."
    )
    analysis_used_target = (
        discarded_target
        if omission_mode == "target_recall"
        else analysis_targets[1]
    )
    candidate_v1 = make_derived_knowledge_candidate(
        object_id=object_id,
        parent_refs=[analysis_ref, original_ref],
        provenance_refs=[f"fixture://provenance/{suffix}", f"fixture://verification/{suffix}"],
        candidate_kind="SYNTHETIC_REUSABLE_METHOD_NOTE",
        reusable_claim=reusable_claim,
        scope=["fixture://scope/controlled-feedback-method-boundary"],
        limitations=[
            "not-a-literature-card",
            "scientific-judgment-not-assessed",
            "test-only-no-production-ingest",
        ],
        source_snapshot=source_snapshot,
        evidence_refs=[f"fixture://provenance/{suffix}", f"fixture://verification/{suffix}"],
        field_qualification_refs=[f"fixture://qualification/{suffix}/claim", f"fixture://qualification/{suffix}/method"],
        source_usage_bindings=[
            {
                "source_ref": analysis_ref,
                "used_target_refs": [analysis_used_target],
            },
            {
                "source_ref": original_ref,
                "used_target_refs": [original_targets[1]],
            },
        ],
        generation_basis=f"fixture://generation/{suffix}",
        derived_penalty=0.5,
        derived_penalty_record_ref=f"fixture://penalty/{suffix}",
        derived_penalty_record_hash=penalty_record["content_hash"],
        derived_penalty_eligibility_ref=f"dpee_test_{suffix}",
        self_reinforcement_audit_ref=f"sra_test_{suffix}",
    )
    candidate_v2 = project_candidate_transition(candidate_v1, target_state="review_pending")
    receipt_id = f"hkdr_test_{suffix}_{decision.lower()}"
    evidence_refs = [f"fixture://decision/{suffix}/evidence"]
    minimal_decision = {
        "decision_id": receipt_id,
        "decided_by": "TEST_ONLY_SYNTHETIC_HUMAN_A",
        "decided_at": DECIDED_AT,
        "decision": decision,
        "evidence_refs": evidence_refs,
    }
    target_state = "approved_for_controlled_ingest" if decision == "APPROVE" else "rejected"
    candidate_v3 = project_candidate_transition(
        candidate_v2,
        target_state=target_state,
        human_decision=minimal_decision,
    )
    decision_receipt = build_synthetic_human_decision_fixture(
        receipt_id=receipt_id,
        candidate=candidate_v3,
        decision=decision,
        decided_at=DECIDED_AT,
        expires_at=EXPIRES_AT,
        evidence_refs=evidence_refs,
    )
    penalty_evidence = build_penalty_eligibility_evidence(
        evidence_id=f"dpee_test_{suffix}",
        penalty_record=penalty_record,
        penalty_policy=penalty_policy,
    )
    eligibility = build_eligibility_report(
        report_id=f"cier_test_{suffix}_{decision.lower()}",
        candidate=candidate_v3,
        source_snapshot=source_snapshot,
        decision_receipt=decision_receipt,
        penalty_evidence=penalty_evidence,
        evaluated_at=EVALUATED_AT,
    )
    versions = [candidate_v1, candidate_v2, candidate_v3]
    lineage_entries: list[dict[str, Any]] = []
    for candidate in versions:
        common = candidate["common_object"]
        lineage_entries.append(
            {
                "node_ref": candidate_version_ref(candidate),
                "revision": common["revision"],
                "content_hash": deepcopy(candidate["content_hash"]),
                "parent_refs": deepcopy(common["parent_refs"]),
                "supersedes_ref": common["supersedes"],
                "ingest_key": f"{common['object_id']}:{candidate['content_hash']['value']}",
                "status": common["state"],
            }
        )
    lineage = build_lineage_ledger(
        ledger_id=f"lsl_test_{suffix}",
        entries=lineage_entries,
        allowed_external_refs=[analysis_ref, original_ref],
    )
    retrieval_rows = [
        {"result_ref": original_ref, "provenance_state": "original", "supports_candidate": True, "influences_candidate_qualification": False},
        {"result_ref": analysis_ref, "provenance_state": "derived", "supports_candidate": True, "influences_candidate_qualification": False},
    ]
    tombstoned_refs = sorted(
        [
            ref
            for item in analysis_boundary["tombstones"]
            for ref in (item["tombstone_ref"], item["target_ref"])
        ]
    )
    audit = audit_self_reinforcement(
        audit_id=f"sra_test_{suffix}",
        candidate=candidate_v3,
        lineage_ledger=lineage,
        retrieval_rows=retrieval_rows,
        tombstoned_refs=tombstoned_refs,
    )
    bundle: dict[str, Any] = {
        "original_ref": original_ref,
        "analysis_ref": analysis_ref,
        "source_snapshot": source_snapshot,
        "penalty_policy": penalty_policy,
        "penalty_record": penalty_record,
        "candidate_v1": candidate_v1,
        "candidate_v2": candidate_v2,
        "candidate": candidate_v3,
        "decision_receipt": decision_receipt,
        "penalty_evidence": penalty_evidence,
        "eligibility_report": eligibility,
        "lineage_ledger": lineage,
        "retrieval_rows": retrieval_rows,
        "self_reinforcement_audit": audit,
        "tombstoned_refs": tombstoned_refs,
        "omitted_text_fixture_only": omitted_text,
        "discarded_target": discarded_target,
    }
    if decision == "APPROVE" and eligibility["candidate_contract_eligible"]:
        dry_run = make_controlled_ingest_dry_run_receipt(
            receipt_id=f"cidr_test_{suffix}",
            candidate=candidate_v3,
            eligibility_report=eligibility,
        )
        preview = make_knowledge_registration_preview(
            preview_id=f"artifact_registrykp_test_{suffix}",
            candidate=candidate_v3,
            dry_run_receipt=dry_run,
        )
        bundle["dry_run_receipt"] = dry_run
        bundle["artifact_registry_preview"] = preview
        pending_projection = make_pending_artifact_projection(
            artifact_id=f"dka_test_{suffix}",
            candidate=candidate_v3,
        )
        bundle["pending_artifact_projection"] = pending_projection
        bundle["activation_preview"] = make_controlled_activation_preview(
            preview_id=f"cap_test_{suffix}",
            artifact=pending_projection,
        )
    return bundle


__all__ = [
    "DECIDED_AT",
    "EVALUATED_AT",
    "EXPIRES_AT",
    "make_baseline_bundle",
]
