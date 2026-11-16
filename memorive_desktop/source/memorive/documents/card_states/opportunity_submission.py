"""Create-only KNOWLEDGE_ADMISSION submission adapter bound to an exact EVIDENCE_REVIEW report."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping

from research_opportunities.canonical import (
    content_hash_matches,
    sha256_bytes,
    typed_hash_is_valid,
    typed_payload_hash,
    with_content_hash,
)
from research_opportunities.errors import CandidateRejected


CANDIDATE_KEYS = {
    "object_id",
    "schema_version",
    "revision",
    "content_hash",
    "producer",
    "consumers",
    "state",
    "parent_refs",
    "provenance_refs",
    "supersedes",
    "immutable_fields",
    "failure_semantics",
    "opportunity_kind",
    "source_snapshot_ref",
    "source_snapshot_hash",
    "trigger_scope_receipt_hash",
    "service_contracts_registry_hash",
    "configuration_policy_hash",
    "gap_refs",
    "tension_refs",
    "anchor_resolution_receipt_refs",
    "comparison_assessment_refs",
    "pair_ledger_refs",
    "evidence_hashes",
    "coverage_status",
    "scientific_judgment_status",
    "score_snapshot",
    "human_decision_required",
    "human_decision",
    "automatic_state_transition",
}
SCORE_KEYS = {
    "evidence_completeness",
    "gap_evidence_count",
    "tension_classification_counts",
    "research_value_total_score",
    "state_authority",
    "ordering_tie_break",
}
CLASSIFICATION_COUNT_KEYS = {"A", "B", "C", "D", "coverage_limited"}
REF_LIST_FIELDS = (
    "gap_refs",
    "tension_refs",
    "anchor_resolution_receipt_refs",
    "comparison_assessment_refs",
    "pair_ledger_refs",
)
REPORT_KEYS = {
    "schema_version",
    "verification_rule_version",
    "candidate_id",
    "candidate_content_hash",
    "source_snapshot_ref",
    "source_snapshot_content_hash",
    "trigger_scope_receipt_hash",
    "service_contracts_registry_hash",
    "configuration_policy_hash",
    "ignore_authority_registry_hash",
    "evidence_set_hash",
    "verification_result",
    "field_checks",
    "issues",
    "aggregate_pass_does_not_replace_field_evidence",
    "required_check_ids",
    "required_check_registry",
    "coverage_summary",
    "dynamic_check_commitment",
    "review_capability",
    "scientific_judgment_status",
    "content_hash",
}
REQUIRED_CHECK_REGISTRY = {
    "source_snapshot_and_trigger_exact_contract": "required.source_snapshot_and_trigger_exact_contract",
    "ignore_authority_registry_binding": "required.ignore_authority_registry_binding",
    "candidate_exact_contract": "required.candidate_exact_contract",
    "candidate_mandatory_identity_and_policy": "required.candidate_mandatory_identity_and_policy",
    "candidate_ref_sets_sorted_unique": "required.candidate_ref_sets_sorted_unique",
    "projection_exact_set_and_recompute": "required.projection_exact_set_and_recompute",
    "anchor_resolution_receipt_exact_set_and_recompute": "required.anchor_resolution_receipt_exact_set_and_recompute",
    "evidence_exact_set_and_recompute": "required.evidence_exact_set_and_recompute",
    "assessment_exact_set_and_recompute": "required.assessment_exact_set_and_recompute",
    "pair_ledger_exact_set_and_recompute": "required.pair_ledger_exact_set_and_recompute",
    "dependency_closure": "required.dependency_closure",
    "candidate_state_and_score_authority": "required.candidate_state_and_score_authority",
}
REVIEW_CAPABILITY = {
    "role_separated_machine_verification": True,
    "research_opportunities_semantic_producer_imported": False,
    "independent_review": "NOT_ASSESSED",
    "third_party_review": "NOT_ASSESSED",
    "scientific_judgment": "NOT_ASSESSED",
}
REQUEST_KEYS = {
    "schema_version",
    "request_id",
    "candidate_id",
    "candidate_hash",
    "verification_report_hash",
    "source_snapshot_hash",
    "trigger_scope_receipt_hash",
    "service_contracts_registry_hash",
    "configuration_policy_hash",
    "evidence_set_hash",
    "current_state",
    "requested_target_state",
    "submission_eligible",
    "automatic_transition_performed",
    "state_after_request",
    "human_decision_required_for_acceptance",
    "human_decision",
    "derived_penalty_observed",
    "derived_penalty_is_state_authority",
    "score_snapshot_is_state_authority",
    "content_hash",
}
KNOWLEDGE_ADMISSION_VALIDATION_RECEIPT_KEYS = {
    "schema_version",
    "receipt_id",
    "validation_rule_version",
    "request_id",
    "request_hash",
    "candidate_id",
    "candidate_hash",
    "verification_report_hash",
    "validation_result",
    "requested_target_state",
    "automatic_transition_count",
    "state_before",
    "state_after",
    "human_decision_required",
    "score_or_penalty_state_authority",
    "content_hash",
}


def _finite_number_or_none(value: Any) -> bool:
    return value is None or (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _validate_exact_candidate(candidate: Mapping[str, Any]) -> None:
    if set(candidate) != CANDIDATE_KEYS:
        raise CandidateRejected("SUBMISSION_CANDIDATE_EXACT_KEYS_MISMATCH")
    if not content_hash_matches(candidate):
        raise CandidateRejected("SUBMISSION_CANDIDATE_HASH_MISMATCH")
    if candidate.get("schema_version") != "3.1":
        raise CandidateRejected("SUBMISSION_CANDIDATE_SCHEMA_MISMATCH")
    if (
        candidate.get("revision") != 1
        or candidate.get("producer") != "RESEARCH_OPPORTUNITIES_RESEARCH_OPPORTUNITIES_OPPORTUNITY_BUILDER_SUCCESSOR003"
        or candidate.get("consumers") != ["M06", "M08", "MODEL_EVALUATION"]
        or candidate.get("supersedes") is not None
    ):
        raise CandidateRejected("SUBMISSION_CANDIDATE_IDENTITY_CONTRACT_MISMATCH")
    if candidate.get("state") != "candidate":
        raise CandidateRejected("SUBMISSION_SOURCE_STATE_MUST_BE_CANDIDATE")
    if candidate.get("automatic_state_transition") is not False:
        raise CandidateRejected("SUBMISSION_CANDIDATE_AUTO_TRANSITION_FORBIDDEN")
    if candidate.get("human_decision") is not None:
        raise CandidateRejected("SUBMISSION_CANDIDATE_HUMAN_DECISION_MUST_BE_NULL")
    if candidate.get("human_decision_required") is not True:
        raise CandidateRejected("SUBMISSION_CANDIDATE_HUMAN_DECISION_REQUIRED")
    if candidate.get("scientific_judgment_status") != "NOT_ASSESSED":
        raise CandidateRejected("SUBMISSION_CANDIDATE_SCIENTIFIC_STATUS_INVALID")
    score = candidate.get("score_snapshot")
    if not isinstance(score, dict) or set(score) != SCORE_KEYS:
        raise CandidateRejected("SUBMISSION_SCORE_SNAPSHOT_INVALID")
    if score.get("state_authority") is not False or score.get("research_value_total_score") is not None:
        raise CandidateRejected("SUBMISSION_SCORE_STATE_AUTHORITY_FORBIDDEN")
    counts = score.get("tension_classification_counts")
    if (
        not isinstance(counts, dict)
        or set(counts) != CLASSIFICATION_COUNT_KEYS
        or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts.values())
    ):
        raise CandidateRejected("SUBMISSION_CLASSIFICATION_COUNTS_INVALID")
    refs: list[str] = []
    for field in REF_LIST_FIELDS:
        observed = candidate.get(field)
        if (
            not isinstance(observed, list)
            or not all(isinstance(item, str) and bool(item.strip()) for item in observed)
            or observed != sorted(set(observed))
        ):
            raise CandidateRejected("SUBMISSION_CANDIDATE_REFS_NOT_SORTED_UNIQUE")
        refs.extend(observed)
    if len(refs) != len(set(refs)):
        raise CandidateRejected("SUBMISSION_CANDIDATE_REF_NAMESPACE_COLLISION")
    for field in ("parent_refs", "provenance_refs"):
        observed = candidate.get(field)
        if (
            not isinstance(observed, list)
            or not all(isinstance(item, str) and bool(item.strip()) for item in observed)
            or observed != sorted(set(observed))
        ):
            raise CandidateRejected("SUBMISSION_CANDIDATE_LINEAGE_NOT_SORTED_UNIQUE")
    hashes = candidate.get("evidence_hashes")
    if (
        not isinstance(hashes, dict)
        or set(hashes) != set(refs)
        or not all(typed_hash_is_valid(value) for value in hashes.values())
    ):
        raise CandidateRejected("SUBMISSION_EVIDENCE_HASH_EXACT_SET_MISMATCH")


def _validate_exact_report(
    report: Mapping[str, Any], candidate: Mapping[str, Any]
) -> None:
    if set(report) != REPORT_KEYS:
        raise CandidateRejected("SUBMISSION_REPORT_EXACT_KEYS_MISMATCH")
    if not content_hash_matches(report):
        raise CandidateRejected("SUBMISSION_REPORT_HASH_MISMATCH")
    if report.get("schema_version") != "RESEARCH_OPPORTUNITIES_OPPORTUNITY_VERIFICATION_REPORT_V2":
        raise CandidateRejected("SUBMISSION_REPORT_SCHEMA_MISMATCH")
    if report.get("verification_rule_version") != "RESEARCH_OPPORTUNITIES_EVIDENCE_REVIEW_ROLE_SEPARATED_RECOMPUTE_SUCCESSOR003_V1":
        raise CandidateRejected("SUBMISSION_REPORT_RULE_VERSION_MISMATCH")
    if report.get("verification_result") != "PASS":
        raise CandidateRejected("SUBMISSION_REQUIRES_EVIDENCE_REVIEW_PASS")
    if report.get("candidate_id") != candidate.get("object_id"):
        raise CandidateRejected("SUBMISSION_REPORT_CANDIDATE_ID_MISMATCH")
    if report.get("candidate_content_hash") != candidate.get("content_hash"):
        raise CandidateRejected("SUBMISSION_STALE_REPORT_CANDIDATE_HASH_MISMATCH")
    if report.get("source_snapshot_content_hash") != candidate.get("source_snapshot_hash"):
        raise CandidateRejected("SUBMISSION_REPORT_SOURCE_HASH_MISMATCH")
    for report_key, candidate_key in (
        ("trigger_scope_receipt_hash", "trigger_scope_receipt_hash"),
        ("service_contracts_registry_hash", "service_contracts_registry_hash"),
        ("configuration_policy_hash", "configuration_policy_hash"),
    ):
        if report.get(report_key) != candidate.get(candidate_key):
            raise CandidateRejected(f"SUBMISSION_REPORT_{report_key.upper()}_MISMATCH")
    if not typed_hash_is_valid(report.get("ignore_authority_registry_hash")):
        raise CandidateRejected("SUBMISSION_REPORT_IGNORE_AUTHORITY_REGISTRY_HASH_INVALID")
    expected_evidence_set_hash = typed_payload_hash(candidate["evidence_hashes"])
    if report.get("evidence_set_hash") != expected_evidence_set_hash:
        raise CandidateRejected("SUBMISSION_REPORT_EVIDENCE_SET_HASH_MISMATCH")
    if report.get("aggregate_pass_does_not_replace_field_evidence") is not True:
        raise CandidateRejected("SUBMISSION_REPORT_AGGREGATE_FLAG_INVALID")
    if report.get("review_capability") != REVIEW_CAPABILITY:
        raise CandidateRejected("SUBMISSION_REPORT_REVIEW_CAPABILITY_INVALID")
    checks = report.get("field_checks")
    if not isinstance(checks, list) or not checks or any(
        not isinstance(item, dict)
        or set(item) != {"field", "expected", "observed", "pass"}
        or not isinstance(item.get("field"), str)
        or not item["field"]
        or item.get("pass") is not True
        for item in checks
    ):
        raise CandidateRejected("SUBMISSION_REPORT_FIELD_CHECK_FAILURE")
    if report.get("dynamic_check_commitment") != typed_payload_hash({"field_checks": checks}):
        raise CandidateRejected("SUBMISSION_REPORT_DYNAMIC_CHECK_COMMITMENT_MISMATCH")
    required_check_ids = list(REQUIRED_CHECK_REGISTRY)
    if report.get("required_check_ids") != required_check_ids:
        raise CandidateRejected("SUBMISSION_REPORT_REQUIRED_CHECK_IDS_MISMATCH")
    if report.get("required_check_registry") != REQUIRED_CHECK_REGISTRY:
        raise CandidateRejected("SUBMISSION_REPORT_REQUIRED_CHECK_REGISTRY_MISMATCH")
    by_field: dict[str, list[Mapping[str, Any]]] = {}
    for item in checks:
        by_field.setdefault(str(item["field"]), []).append(item)
    for field in REQUIRED_CHECK_REGISTRY.values():
        rows = by_field.get(field, [])
        if (
            len(rows) != 1
            or rows[0].get("expected") is not True
            or rows[0].get("observed") is not True
            or rows[0].get("pass") is not True
        ):
            raise CandidateRejected("SUBMISSION_REPORT_MANDATORY_CHECK_MISSING_OR_FAILED")
    expected_coverage_summary = {
        "required_check_count": len(required_check_ids),
        "covered_check_ids": required_check_ids,
        "passing_check_ids": required_check_ids,
        "failing_check_ids": [],
        "field_check_count": len(checks),
    }
    if report.get("coverage_summary") != expected_coverage_summary:
        raise CandidateRejected("SUBMISSION_REPORT_COVERAGE_SUMMARY_MISMATCH")
    if report.get("issues") != []:
        raise CandidateRejected("SUBMISSION_REPORT_ISSUES_NOT_EMPTY")
    if report.get("scientific_judgment_status") != "NOT_ASSESSED":
        raise CandidateRejected("SUBMISSION_REPORT_SCIENTIFIC_STATUS_INVALID")


def make_review_submission_request(
    *,
    candidate: Mapping[str, Any],
    verification_report: Mapping[str, Any],
    target_state: str = "review_pending",
    derived_penalty: float | int | None = None,
) -> dict[str, Any]:
    _validate_exact_candidate(candidate)
    _validate_exact_report(verification_report, candidate)
    if target_state != "review_pending":
        raise CandidateRejected("SUBMISSION_TARGET_MUST_BE_REVIEW_PENDING")
    if not _finite_number_or_none(derived_penalty):
        raise CandidateRejected("SUBMISSION_DERIVED_PENALTY_MUST_BE_FINITE_NUMBER_OR_NULL")
    request_id = "ors_" + sha256_bytes(
        (
            candidate["object_id"]
            + "\n"
            + candidate["content_hash"]["value"]
            + "\n"
            + verification_report["content_hash"]["value"]
        ).encode("utf-8")
    )[:24].lower()
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_OPPORTUNITY_REVIEW_SUBMISSION_REQUEST_V2",
            "request_id": request_id,
            "candidate_id": candidate["object_id"],
            "candidate_hash": deepcopy(candidate["content_hash"]),
            "verification_report_hash": deepcopy(verification_report["content_hash"]),
            "source_snapshot_hash": deepcopy(candidate["source_snapshot_hash"]),
            "trigger_scope_receipt_hash": deepcopy(candidate["trigger_scope_receipt_hash"]),
            "service_contracts_registry_hash": deepcopy(candidate["service_contracts_registry_hash"]),
            "configuration_policy_hash": deepcopy(candidate["configuration_policy_hash"]),
            "evidence_set_hash": deepcopy(verification_report["evidence_set_hash"]),
            "current_state": "candidate",
            "requested_target_state": "review_pending",
            "submission_eligible": True,
            "automatic_transition_performed": False,
            "state_after_request": "candidate",
            "human_decision_required_for_acceptance": True,
            "human_decision": None,
            "derived_penalty_observed": derived_penalty,
            "derived_penalty_is_state_authority": False,
            "score_snapshot_is_state_authority": False,
        }
    )


def validate_review_submission_request(
    request: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
    verification_report: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_exact_candidate(candidate)
    _validate_exact_report(verification_report, candidate)
    if set(request) != REQUEST_KEYS or not content_hash_matches(request):
        raise CandidateRejected("SUBMISSION_REQUEST_EXACT_CONTRACT_OR_HASH_MISMATCH")
    expected = make_review_submission_request(
        candidate=candidate,
        verification_report=verification_report,
        target_state="review_pending",
        derived_penalty=request.get("derived_penalty_observed"),
    )
    if dict(request) != expected:
        raise CandidateRejected("SUBMISSION_REQUEST_EXPECTED_OBSERVED_MISMATCH")
    receipt_id = "m8v_" + sha256_bytes(
        (
            "RESEARCH_OPPORTUNITIES_KNOWLEDGE_ADMISSION_VALIDATION_RECEIPT_V1\n"
            + request["content_hash"]["value"]
        ).encode("utf-8")
    )[:24].lower()
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_KNOWLEDGE_ADMISSION_VALIDATION_RECEIPT_V1",
            "receipt_id": receipt_id,
            "validation_rule_version": "RESEARCH_OPPORTUNITIES_KNOWLEDGE_ADMISSION_REQUEST_VALIDATOR_SUCCESSOR003_V1",
            "request_id": request["request_id"],
            "request_hash": deepcopy(request["content_hash"]),
            "candidate_id": candidate["object_id"],
            "candidate_hash": deepcopy(candidate["content_hash"]),
            "verification_report_hash": deepcopy(verification_report["content_hash"]),
            "validation_result": "PASS",
            "requested_target_state": "review_pending",
            "automatic_transition_count": 0,
            "state_before": "candidate",
            "state_after": "candidate",
            "human_decision_required": True,
            "score_or_penalty_state_authority": False,
        }
    )


def validate_knowledge_admission_validation_receipt(
    receipt: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    candidate: Mapping[str, Any],
    verification_report: Mapping[str, Any],
) -> None:
    if set(receipt) != KNOWLEDGE_ADMISSION_VALIDATION_RECEIPT_KEYS or not content_hash_matches(receipt):
        raise CandidateRejected("KNOWLEDGE_ADMISSION_VALIDATION_RECEIPT_EXACT_CONTRACT_OR_HASH_MISMATCH")
    expected = validate_review_submission_request(
        request,
        candidate=candidate,
        verification_report=verification_report,
    )
    if dict(receipt) != expected:
        raise CandidateRejected("KNOWLEDGE_ADMISSION_VALIDATION_RECEIPT_EXPECTED_OBSERVED_MISMATCH")
