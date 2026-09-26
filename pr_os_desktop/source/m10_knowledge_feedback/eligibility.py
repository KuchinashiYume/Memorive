"""Field-level, fail-closed controlled-ingest eligibility evaluation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from m12_ddl.knowledge_decision import (
    KnowledgeDecisionError,
    assert_decision_binds_candidate,
)

from .canonical import (
    make_hashed_payload,
    typed_payload_hash,
    validate_hash_descriptor,
    verify_hashed_payload,
)
from .contracts import (
    assert_candidate_binds_snapshot,
    validate_derived_knowledge_candidate,
    validate_source_snapshot,
)
from .errors import ContractRejected, SourceIneligible
from .penalty import validate_penalty_eligibility_evidence


CHECK_REGISTRY = [
    "candidate_exact_contract",
    "candidate_snapshot_binding",
    "candidate_state_approved",
    "decision_exact_candidate_binding",
    "decision_approve",
    "decision_object_authorization",
    "source_has_non_derived_eligible_parent",
    "source_omission_non_resurrection",
    "penalty_exact_candidate_binding",
    "penalty_authority_only",
    "penalty_not_authority_blocked",
]
_REPORT_KEYS = {
    "schema_version",
    "report_id",
    "candidate_id",
    "candidate_revision",
    "candidate_hash",
    "source_snapshot_hash",
    "decision_receipt_hash",
    "penalty_evidence_hash",
    "field_checks",
    "candidate_contract_eligible",
    "dry_run_eligible",
    "run_execution_authorized",
    "controlled_ingest_execution_eligible",
    "artifact_creation_allowed",
    "issue_codes",
    "aggregate_pass_does_not_replace_field_evidence",
    "test_only",
    "content_hash",
}
_CHECK_KEYS = {"check_id", "expected", "observed", "pass", "reason_code", "provenance_ref"}


def _check(
    check_id: str,
    *,
    expected: Any,
    observed: Any,
    passed: bool,
    reason_code: str,
    provenance_ref: str,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "expected": expected,
        "observed": observed,
        "pass": passed,
        "reason_code": reason_code,
        "provenance_ref": provenance_ref,
    }


def validate_eligibility_report(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "controlled_ingest_eligibility_report")
    except Exception as exc:
        raise ContractRejected("ELIGIBILITY_REPORT_HASH_INVALID") from exc
    if set(result) != _REPORT_KEYS:
        raise ContractRejected("ELIGIBILITY_REPORT_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "P03_T06_CONTROLLED_INGEST_ELIGIBILITY_REPORT_V2":
        raise ContractRejected("ELIGIBILITY_REPORT_SCHEMA_UNSUPPORTED")
    for field in ("report_id", "candidate_id"):
        if not isinstance(result[field], str) or not result[field]:
            raise ContractRejected(f"ELIGIBILITY_REPORT_{field.upper()}_INVALID")
    revision = result["candidate_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ContractRejected("ELIGIBILITY_REPORT_REVISION_INVALID")
    for field in (
        "candidate_hash",
        "source_snapshot_hash",
        "decision_receipt_hash",
        "penalty_evidence_hash",
    ):
        try:
            validate_hash_descriptor(result[field], field)
        except Exception as exc:
            raise ContractRejected(
                f"ELIGIBILITY_REPORT_{field.upper()}_INVALID"
            ) from exc
    rows = result["field_checks"]
    if not isinstance(rows, list) or len(rows) != len(CHECK_REGISTRY):
        raise ContractRejected("ELIGIBILITY_REPORT_CHECK_COUNT_MISMATCH")
    if [row.get("check_id") for row in rows if isinstance(row, Mapping)] != CHECK_REGISTRY:
        raise ContractRejected("ELIGIBILITY_REPORT_CHECK_ORDER_MISMATCH")
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != _CHECK_KEYS:
            raise ContractRejected("ELIGIBILITY_REPORT_CHECK_SHAPE_INVALID")
        if not isinstance(row["pass"], bool):
            raise ContractRejected("ELIGIBILITY_REPORT_CHECK_PASS_INVALID")
        for field in ("check_id", "reason_code", "provenance_ref"):
            if not isinstance(row[field], str) or not row[field]:
                raise ContractRejected("ELIGIBILITY_REPORT_CHECK_TEXT_INVALID")
    for field in (
        "candidate_contract_eligible",
        "dry_run_eligible",
        "run_execution_authorized",
        "controlled_ingest_execution_eligible",
        "artifact_creation_allowed",
        "aggregate_pass_does_not_replace_field_evidence",
        "test_only",
    ):
        if not isinstance(result[field], bool):
            raise ContractRejected(f"ELIGIBILITY_REPORT_{field.upper()}_INVALID")
    all_pass = all(row["pass"] for row in rows)
    if result["candidate_contract_eligible"] != all_pass:
        raise ContractRejected("ELIGIBILITY_REPORT_AGGREGATE_DRIFT")
    if result["dry_run_eligible"] != all_pass:
        raise ContractRejected("ELIGIBILITY_REPORT_DRY_RUN_DRIFT")
    if result["run_execution_authorized"] is not False:
        raise ContractRejected("T06_RUN_MUST_NOT_AUTHORIZE_REAL_INGEST")
    if result["controlled_ingest_execution_eligible"] is not False:
        raise ContractRejected("T06_RUN_MUST_NOT_BE_REAL_INGEST_ELIGIBLE")
    if result["artifact_creation_allowed"] is not False:
        raise ContractRejected("T06_RUN_MUST_NOT_ALLOW_ARTIFACT_CREATION")
    if result["aggregate_pass_does_not_replace_field_evidence"] is not True:
        raise ContractRejected("ELIGIBILITY_REPORT_AGGREGATE_GUARD_MISSING")
    if result["test_only"] is not True:
        raise ContractRejected("ELIGIBILITY_REPORT_MUST_REMAIN_TEST_ONLY")
    issues = result["issue_codes"]
    if not isinstance(issues, list) or issues != sorted(set(issues)):
        raise ContractRejected("ELIGIBILITY_REPORT_ISSUES_INVALID")
    expected_issues = sorted(row["reason_code"] for row in rows if not row["pass"])
    if issues != expected_issues:
        raise ContractRejected("ELIGIBILITY_REPORT_ISSUE_SET_DRIFT")
    return result


def build_eligibility_report(
    *,
    report_id: str,
    candidate: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
    decision_receipt: Mapping[str, Any] | None,
    penalty_evidence: Mapping[str, Any],
    evaluated_at: str,
) -> dict[str, Any]:
    checked_candidate = validate_derived_knowledge_candidate(candidate)
    checked_snapshot = validate_source_snapshot(source_snapshot)
    omission_safe = True
    omission_reason = "PASS"
    try:
        assert_candidate_binds_snapshot(checked_candidate, checked_snapshot)
        snapshot_binding = True
        snapshot_reason = "PASS"
    except SourceIneligible as exc:
        reason = str(exc)
        if reason in {
            "CANDIDATE_USES_TOMBSTONED_SOURCE_TARGET",
            "CANDIDATE_RECALLS_TOMBSTONED_CONTENT_HASH",
            "CANDIDATE_SOURCE_USAGE_TARGET_NOT_ANCHORED",
        }:
            snapshot_binding = True
            snapshot_reason = "PASS"
            omission_safe = False
            omission_reason = reason
        else:
            snapshot_binding = False
            snapshot_reason = reason
            omission_safe = False
            omission_reason = "SOURCE_OMISSION_BOUNDARY_NOT_VERIFIABLE"
    checked_decision: dict[str, Any] | None = None
    try:
        checked_decision = assert_decision_binds_candidate(
            decision_receipt,
            checked_candidate,
            evaluated_at=evaluated_at,
            allow_synthetic=True,
            require_synthetic=True,
        )
        decision_binding = True
        decision_reason = "PASS"
    except KnowledgeDecisionError as exc:
        decision_binding = False
        decision_reason = str(exc)
    checked_penalty = validate_penalty_eligibility_evidence(penalty_evidence)
    common = checked_candidate["common_object"]
    parent_refs = set(common["parent_refs"])
    eligible_items = [item for item in checked_snapshot["source_items"] if item["eligible"]]
    source_parent_eligible = (
        parent_refs <= {item["source_ref"] for item in eligible_items}
        and any(
            item["source_ref"] in parent_refs and not item["derived_only"]
            for item in eligible_items
        )
    )
    minimal_decision = (
        None
        if checked_decision is None
        else {
            "decision_id": checked_decision["receipt_id"],
            "decided_by": checked_decision["decided_by"],
            "decided_at": checked_decision["decided_at"],
            "decision": checked_decision["decision"],
            "evidence_refs": checked_decision["evidence_refs"],
        }
    )
    observed_decision = None if checked_decision is None else checked_decision["decision"]
    observed_authorization = (
        False
        if checked_decision is None
        else checked_decision["controlled_ingest_authorized"]
    )
    decision_observation_hash = (
        typed_payload_hash({"submitted_decision_receipt": deepcopy(decision_receipt)})
        if checked_decision is None
        else deepcopy(checked_decision["content_hash"])
    )
    rows = [
        _check("candidate_exact_contract", expected=True, observed=True, passed=True, reason_code="PASS", provenance_ref="candidate.content_hash"),
        _check("candidate_snapshot_binding", expected=True, observed=snapshot_binding, passed=snapshot_binding, reason_code=snapshot_reason, provenance_ref="candidate.source_snapshot_hash"),
        _check("candidate_state_approved", expected="approved_for_controlled_ingest", observed=common["state"], passed=common["state"] == "approved_for_controlled_ingest", reason_code="PASS" if common["state"] == "approved_for_controlled_ingest" else "CANDIDATE_STATE_NOT_APPROVED", provenance_ref="candidate.common_object.state"),
        _check("decision_exact_candidate_binding", expected=True, observed=decision_binding, passed=decision_binding, reason_code=decision_reason, provenance_ref="decision.candidate_hash"),
        _check("decision_approve", expected="APPROVE", observed=observed_decision, passed=observed_decision == "APPROVE", reason_code="PASS" if observed_decision == "APPROVE" else ("DECISION_NOT_VERIFIABLE" if checked_decision is None else "DECISION_NOT_APPROVE"), provenance_ref="decision.decision"),
        _check("decision_object_authorization", expected=True, observed=observed_authorization, passed=observed_authorization is True and common["human_approval"] == minimal_decision and common["controlled_ingest_authorized"] is True, reason_code="PASS" if observed_authorization is True and common["human_approval"] == minimal_decision and common["controlled_ingest_authorized"] is True else "DECISION_NOT_PROJECTED_TO_EXACT_CANDIDATE", provenance_ref="candidate.common_object.human_approval"),
        _check("source_has_non_derived_eligible_parent", expected=True, observed=source_parent_eligible, passed=source_parent_eligible, reason_code="PASS" if source_parent_eligible else "SOURCE_HAS_NO_NON_DERIVED_ELIGIBLE_PARENT", provenance_ref="source_snapshot.source_items"),
        _check("source_omission_non_resurrection", expected=True, observed=omission_safe, passed=omission_safe, reason_code=omission_reason, provenance_ref="source_snapshot.source_items[].omission_boundary+candidate.source_usage_bindings"),
        _check(
            "penalty_exact_candidate_binding",
            expected={
                "candidate_id": common["object_id"],
                "evidence_id": checked_candidate["derived_penalty_eligibility_ref"],
                "penalty_record_hash": checked_candidate["derived_penalty_record_hash"],
            },
            observed={
                "candidate_id": checked_penalty["candidate_id"],
                "evidence_id": checked_penalty["evidence_id"],
                "penalty_record_hash": checked_penalty["penalty_record_hash"],
            },
            passed=(
                checked_penalty["candidate_id"] == common["object_id"]
                and checked_penalty["evidence_id"]
                == checked_candidate["derived_penalty_eligibility_ref"]
                and checked_penalty["penalty_record_hash"]
                == checked_candidate["derived_penalty_record_hash"]
            ),
            reason_code=(
                "PASS"
                if (
                    checked_penalty["candidate_id"] == common["object_id"]
                    and checked_penalty["evidence_id"]
                    == checked_candidate["derived_penalty_eligibility_ref"]
                    and checked_penalty["penalty_record_hash"]
                    == checked_candidate["derived_penalty_record_hash"]
                )
                else "PENALTY_EXACT_CANDIDATE_BINDING_MISMATCH"
            ),
            provenance_ref="candidate.derived_penalty_record_hash+penalty_evidence.penalty_record_hash",
        ),
        _check("penalty_authority_only", expected=True, observed=checked_penalty["authority_only"], passed=checked_penalty["authority_only"] is True and checked_penalty["relevance_subtotal_before"] == checked_penalty["relevance_subtotal_after"], reason_code="PASS" if checked_penalty["authority_only"] is True and checked_penalty["relevance_subtotal_before"] == checked_penalty["relevance_subtotal_after"] else "PENALTY_SCOPE_VIOLATION", provenance_ref="penalty_evidence.authority_only"),
        _check("penalty_not_authority_blocked", expected=False, observed=checked_penalty["authority_blocked"], passed=checked_penalty["authority_blocked"] is False and checked_penalty["eligible"] is True, reason_code="PASS" if checked_penalty["authority_blocked"] is False and checked_penalty["eligible"] is True else "PENALTY_AUTHORITY_BLOCKED", provenance_ref="penalty_evidence.authority_blocked"),
    ]
    all_pass = all(row["pass"] for row in rows)
    return validate_eligibility_report(
        make_hashed_payload(
            {
                "schema_version": "P03_T06_CONTROLLED_INGEST_ELIGIBILITY_REPORT_V2",
                "report_id": report_id,
                "candidate_id": common["object_id"],
                "candidate_revision": common["revision"],
                "candidate_hash": deepcopy(checked_candidate["content_hash"]),
                "source_snapshot_hash": deepcopy(checked_snapshot["content_hash"]),
                "decision_receipt_hash": decision_observation_hash,
                "penalty_evidence_hash": deepcopy(checked_penalty["content_hash"]),
                "field_checks": rows,
                "candidate_contract_eligible": all_pass,
                "dry_run_eligible": all_pass,
                "run_execution_authorized": False,
                "controlled_ingest_execution_eligible": False,
                "artifact_creation_allowed": False,
                "issue_codes": sorted(row["reason_code"] for row in rows if not row["pass"]),
                "aggregate_pass_does_not_replace_field_evidence": True,
                "test_only": True,
            }
        )
    )


__all__ = [
    "CHECK_REGISTRY",
    "build_eligibility_report",
    "validate_eligibility_report",
]

