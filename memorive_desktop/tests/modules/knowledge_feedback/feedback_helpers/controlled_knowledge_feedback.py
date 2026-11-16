"""MODEL_EVALUATION structural self-reinforcement audit for KNOWLEDGE-FEEDBACK.

The audit is deterministic and synthetic.  It measures safety invariants only;
it does not estimate scientific quality, production utility, or optimal penalty
weights.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from memorive.research.ranking.contracts import (
    make_hashed_payload,
    validate_hash_descriptor,
    verify_hashed_payload,
)
from memorive.research.knowledge_feedback.contracts import candidate_version_ref, validate_derived_knowledge_candidate
from memorive.research.knowledge_feedback.lineage import validate_lineage_ledger


_AUDIT_KEYS = {
    "schema_version",
    "audit_id",
    "candidate_id",
    "candidate_hash",
    "lineage_ledger_hash",
    "source_evidence_count",
    "derived_evidence_count",
    "derived_only",
    "self_reference_count",
    "lineage_cycle_count",
    "duplicate_ingest_count",
    "top_k",
    "top_k_original_count",
    "top_k_derived_count",
    "source_crowd_out",
    "feedback_influence_count",
    "tombstoned_recall_count",
    "revoked_recall_count",
    "checks",
    "verification_result",
    "issue_codes",
    "scientific_quality",
    "threshold_scope",
    "test_only",
    "content_hash",
}
_CHECK_KEYS = {"check_id", "expected", "observed", "pass", "reason_code"}
CHECK_REGISTRY = [
    "has_original_source",
    "not_derived_only",
    "no_self_reference",
    "no_lineage_cycle",
    "no_duplicate_ingest",
    "no_source_crowd_out",
    "no_feedback_influence",
    "no_tombstoned_source_recall",
    "no_revoked_recall",
]


class SelfReinforcementAuditError(ValueError):
    """Audit input or output violates the frozen structural contract."""


def _check(check_id: str, expected: Any, observed: Any, reason: str) -> dict[str, Any]:
    passed = observed == expected
    return {
        "check_id": check_id,
        "expected": expected,
        "observed": observed,
        "pass": passed,
        "reason_code": "PASS" if passed else reason,
    }


def validate_self_reinforcement_audit(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "self_reinforcement_audit")
    except Exception as exc:
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_AUDIT_HASH_INVALID") from exc
    if set(result) != _AUDIT_KEYS:
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_AUDIT_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "KNOWLEDGE_FEEDBACK_SELF_REINFORCEMENT_AUDIT_V2":
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_AUDIT_SCHEMA_UNSUPPORTED")
    for field in ("audit_id", "candidate_id"):
        if not isinstance(result[field], str) or not result[field]:
            raise SelfReinforcementAuditError(f"SELF_REINFORCEMENT_{field.upper()}_INVALID")
    for field in ("candidate_hash", "lineage_ledger_hash"):
        try:
            validate_hash_descriptor(result[field], field)
        except Exception as exc:
            raise SelfReinforcementAuditError(
                f"SELF_REINFORCEMENT_{field.upper()}_INVALID"
            ) from exc
    for field in (
        "source_evidence_count",
        "derived_evidence_count",
        "self_reference_count",
        "lineage_cycle_count",
        "duplicate_ingest_count",
        "top_k",
        "top_k_original_count",
        "top_k_derived_count",
        "feedback_influence_count",
        "tombstoned_recall_count",
        "revoked_recall_count",
    ):
        observed = result[field]
        if isinstance(observed, bool) or not isinstance(observed, int) or observed < 0:
            raise SelfReinforcementAuditError(f"SELF_REINFORCEMENT_{field.upper()}_INVALID")
    for field in ("derived_only", "source_crowd_out", "test_only"):
        if not isinstance(result[field], bool):
            raise SelfReinforcementAuditError(f"SELF_REINFORCEMENT_{field.upper()}_INVALID")
    rows = result["checks"]
    if not isinstance(rows, list) or len(rows) != len(CHECK_REGISTRY):
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_CHECK_COUNT_MISMATCH")
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != _CHECK_KEYS or not isinstance(row["pass"], bool):
            raise SelfReinforcementAuditError("SELF_REINFORCEMENT_CHECK_SHAPE_INVALID")
    if [row["check_id"] for row in rows] != CHECK_REGISTRY:
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_CHECK_ORDER_MISMATCH")
    if result["top_k"] != result["source_evidence_count"] + result["derived_evidence_count"]:
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_TOP_K_COUNT_DRIFT")
    if result["top_k_original_count"] != result["source_evidence_count"]:
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_ORIGINAL_COUNT_DRIFT")
    if result["top_k_derived_count"] != result["derived_evidence_count"]:
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_DERIVED_COUNT_DRIFT")
    if result["derived_only"] != (result["source_evidence_count"] == 0):
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_DERIVED_ONLY_DRIFT")
    if result["source_crowd_out"] != (result["source_evidence_count"] == 0):
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_CROWD_OUT_DRIFT")
    expected_rows = [
        _check("has_original_source", True, result["source_evidence_count"] >= 1, "SELF_REINFORCEMENT_NO_ORIGINAL_SOURCE"),
        _check("not_derived_only", False, result["derived_only"], "SELF_REINFORCEMENT_DERIVED_ONLY"),
        _check("no_self_reference", 0, result["self_reference_count"], "SELF_REINFORCEMENT_SELF_REFERENCE"),
        _check("no_lineage_cycle", 0, result["lineage_cycle_count"], "SELF_REINFORCEMENT_LINEAGE_CYCLE"),
        _check("no_duplicate_ingest", 0, result["duplicate_ingest_count"], "SELF_REINFORCEMENT_DUPLICATE_INGEST"),
        _check("no_source_crowd_out", False, result["source_crowd_out"], "SELF_REINFORCEMENT_SOURCE_CROWD_OUT"),
        _check("no_feedback_influence", 0, result["feedback_influence_count"], "SELF_REINFORCEMENT_QUALIFICATION_FEEDBACK"),
        _check("no_tombstoned_source_recall", 0, result["tombstoned_recall_count"], "SELF_REINFORCEMENT_TOMBSTONED_SOURCE_RECALL"),
        _check("no_revoked_recall", 0, result["revoked_recall_count"], "SELF_REINFORCEMENT_REVOKED_RECALL"),
    ]
    if rows != expected_rows:
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_CHECK_VALUE_DRIFT")
    all_pass = all(row["pass"] for row in rows)
    if result["verification_result"] != ("PASS" if all_pass else "FAIL"):
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_RESULT_DRIFT")
    issues = sorted(row["reason_code"] for row in rows if not row["pass"])
    if result["issue_codes"] != issues:
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_ISSUE_SET_DRIFT")
    if result["scientific_quality"] != "NOT_ASSESSED":
        raise SelfReinforcementAuditError("SCIENTIFIC_QUALITY_MUST_REMAIN_NOT_ASSESSED")
    if result["threshold_scope"] != "TEST_ONLY_STRUCTURAL_SAFETY_NOT_PRODUCTION_OPTIMALITY":
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_THRESHOLD_SCOPE_DRIFT")
    if result["test_only"] is not True:
        raise SelfReinforcementAuditError("SELF_REINFORCEMENT_AUDIT_MUST_REMAIN_TEST_ONLY")
    return result


def audit_self_reinforcement(
    *,
    audit_id: str,
    candidate: Mapping[str, Any],
    lineage_ledger: Mapping[str, Any],
    retrieval_rows: Sequence[Mapping[str, Any]],
    tombstoned_refs: Sequence[str] = (),
    revoked_refs: Sequence[str] = (),
) -> dict[str, Any]:
    checked_candidate = validate_derived_knowledge_candidate(candidate)
    checked_ledger = validate_lineage_ledger(lineage_ledger)
    rows = [dict(item) for item in retrieval_rows]
    if not rows:
        raise SelfReinforcementAuditError("RETRIEVAL_ROWS_REQUIRED")
    for ordinal, row in enumerate(rows):
        if set(row) != {
            "result_ref",
            "provenance_state",
            "supports_candidate",
            "influences_candidate_qualification",
        }:
            raise SelfReinforcementAuditError(f"RETRIEVAL_ROW_{ordinal}_SHAPE_INVALID")
        if not isinstance(row["result_ref"], str) or not row["result_ref"]:
            raise SelfReinforcementAuditError("RETRIEVAL_RESULT_REF_INVALID")
        if row["provenance_state"] not in {"original", "derived"}:
            raise SelfReinforcementAuditError("RETRIEVAL_PROVENANCE_STATE_INVALID")
        if not isinstance(row["supports_candidate"], bool):
            raise SelfReinforcementAuditError("RETRIEVAL_SUPPORT_FLAG_INVALID")
        if not isinstance(row["influences_candidate_qualification"], bool):
            raise SelfReinforcementAuditError("RETRIEVAL_FEEDBACK_FLAG_INVALID")
    source_count = sum(row["provenance_state"] == "original" for row in rows)
    derived_count = sum(row["provenance_state"] == "derived" for row in rows)
    candidate_id = checked_candidate["common_object"]["object_id"]
    self_refs = {candidate_id, candidate_version_ref(checked_candidate)}
    self_count = sum(
        row["result_ref"] in self_refs and row["supports_candidate"] for row in rows
    )
    feedback_count = sum(row["influences_candidate_qualification"] for row in rows)
    if any(not isinstance(ref, str) or not ref for ref in tombstoned_refs):
        raise SelfReinforcementAuditError("TOMBSTONED_REF_INVALID")
    tombstoned = set(tombstoned_refs)
    tombstoned_recall = sum(row["result_ref"] in tombstoned for row in rows)
    if any(not isinstance(ref, str) or not ref for ref in revoked_refs):
        raise SelfReinforcementAuditError("REVOKED_REF_INVALID")
    revoked = set(revoked_refs)
    revoked_recall = sum(row["result_ref"] in revoked for row in rows)
    cycles = len(checked_ledger["cycle_paths"])
    duplicates = len(checked_ledger["duplicate_ingest_keys"])
    derived_only = source_count == 0
    crowd_out = source_count == 0
    checks = [
        _check("has_original_source", True, source_count >= 1, "SELF_REINFORCEMENT_NO_ORIGINAL_SOURCE"),
        _check("not_derived_only", False, derived_only, "SELF_REINFORCEMENT_DERIVED_ONLY"),
        _check("no_self_reference", 0, self_count, "SELF_REINFORCEMENT_SELF_REFERENCE"),
        _check("no_lineage_cycle", 0, cycles, "SELF_REINFORCEMENT_LINEAGE_CYCLE"),
        _check("no_duplicate_ingest", 0, duplicates, "SELF_REINFORCEMENT_DUPLICATE_INGEST"),
        _check("no_source_crowd_out", False, crowd_out, "SELF_REINFORCEMENT_SOURCE_CROWD_OUT"),
        _check("no_feedback_influence", 0, feedback_count, "SELF_REINFORCEMENT_QUALIFICATION_FEEDBACK"),
        _check("no_tombstoned_source_recall", 0, tombstoned_recall, "SELF_REINFORCEMENT_TOMBSTONED_SOURCE_RECALL"),
        _check("no_revoked_recall", 0, revoked_recall, "SELF_REINFORCEMENT_REVOKED_RECALL"),
    ]
    all_pass = all(row["pass"] for row in checks)
    return validate_self_reinforcement_audit(
        make_hashed_payload(
            {
                "schema_version": "KNOWLEDGE_FEEDBACK_SELF_REINFORCEMENT_AUDIT_V2",
                "audit_id": audit_id,
                "candidate_id": candidate_id,
                "candidate_hash": checked_candidate["content_hash"],
                "lineage_ledger_hash": checked_ledger["content_hash"],
                "source_evidence_count": source_count,
                "derived_evidence_count": derived_count,
                "derived_only": derived_only,
                "self_reference_count": self_count,
                "lineage_cycle_count": cycles,
                "duplicate_ingest_count": duplicates,
                "top_k": len(rows),
                "top_k_original_count": source_count,
                "top_k_derived_count": derived_count,
                "source_crowd_out": crowd_out,
                "feedback_influence_count": feedback_count,
                "tombstoned_recall_count": tombstoned_recall,
                "revoked_recall_count": revoked_recall,
                "checks": checks,
                "verification_result": "PASS" if all_pass else "FAIL",
                "issue_codes": sorted(row["reason_code"] for row in checks if not row["pass"]),
                "scientific_quality": "NOT_ASSESSED",
                "threshold_scope": "TEST_ONLY_STRUCTURAL_SAFETY_NOT_PRODUCTION_OPTIMALITY",
                "test_only": True,
            }
        )
    )


__all__ = [
    "CHECK_REGISTRY",
    "SelfReinforcementAuditError",
    "audit_self_reinforcement",
    "validate_self_reinforcement_audit",
]
