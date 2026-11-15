"""Verification qualification evidence for the accepted RETRIEVAL_WEIGHTING DerivedPenalty contract."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from retrieval_weighting.contracts import (
    make_hashed_payload,
    typed_payload_hash,
    validate_hash_descriptor,
    verify_hashed_payload,
)
from retrieval_weighting.derived_penalty import (
    apply_authority_penalty,
    validate_derived_penalty_against_policy,
)

from .errors import ContractRejected


_EVIDENCE_KEYS = {
    "schema_version",
    "evidence_id",
    "candidate_id",
    "penalty_record_hash",
    "penalty_policy_hash",
    "provenance_state",
    "authority_only",
    "relevance_subtotal_before",
    "relevance_subtotal_after",
    "authority_subtotal_before",
    "authority_subtotal_after",
    "authority_blocked",
    "eligible",
    "issue_codes",
    "scientific_optimality",
    "test_only",
    "content_hash",
}


def validate_penalty_eligibility_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "penalty_eligibility_evidence")
    except Exception as exc:
        raise ContractRejected("PENALTY_EVIDENCE_HASH_INVALID") from exc
    if set(result) != _EVIDENCE_KEYS:
        raise ContractRejected("PENALTY_EVIDENCE_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "KNOWLEDGE_FEEDBACK_DERIVED_PENALTY_ELIGIBILITY_EVIDENCE_V1":
        raise ContractRejected("PENALTY_EVIDENCE_SCHEMA_UNSUPPORTED")
    for field in ("evidence_id", "candidate_id", "provenance_state"):
        if not isinstance(result[field], str) or not result[field]:
            raise ContractRejected(f"PENALTY_EVIDENCE_{field.upper()}_INVALID")
    for field in ("penalty_record_hash", "penalty_policy_hash"):
        try:
            validate_hash_descriptor(result[field], field)
        except Exception as exc:
            raise ContractRejected(
                f"PENALTY_EVIDENCE_{field.upper()}_INVALID"
            ) from exc
    for field in ("authority_only", "authority_blocked", "eligible", "test_only"):
        if not isinstance(result[field], bool):
            raise ContractRejected(f"PENALTY_EVIDENCE_{field.upper()}_INVALID")
    if result["authority_only"] is not True or result["test_only"] is not True:
        raise ContractRejected("PENALTY_EVIDENCE_BOUNDARY_DRIFT")
    before = result["relevance_subtotal_before"]
    after = result["relevance_subtotal_after"]
    if before != after:
        raise ContractRejected("DERIVED_PENALTY_CHANGED_RELEVANCE")
    for field in (
        "relevance_subtotal_before",
        "relevance_subtotal_after",
        "authority_subtotal_before",
        "authority_subtotal_after",
    ):
        value_number = result[field]
        if isinstance(value_number, bool) or not isinstance(value_number, (int, float)):
            raise ContractRejected(f"PENALTY_EVIDENCE_{field.upper()}_INVALID")
    issues = result["issue_codes"]
    if not isinstance(issues, list) or issues != sorted(set(issues)):
        raise ContractRejected("PENALTY_EVIDENCE_ISSUE_CODES_INVALID")
    if result["authority_blocked"] and result["eligible"]:
        raise ContractRejected("AUTHORITY_BLOCKED_PENALTY_CANNOT_BE_ELIGIBLE")
    if result["scientific_optimality"] != "NOT_ASSESSED":
        raise ContractRejected("PENALTY_SCIENTIFIC_OPTIMALITY_MUST_REMAIN_NOT_ASSESSED")
    return result


def build_penalty_eligibility_evidence(
    *,
    evidence_id: str,
    penalty_record: Mapping[str, Any],
    penalty_policy: Mapping[str, Any],
    relevance_subtotal: float = 0.65,
    authority_subtotal: float = 0.35,
) -> dict[str, Any]:
    checked = validate_derived_penalty_against_policy(penalty_record, penalty_policy)
    applied = apply_authority_penalty(
        relevance_subtotal=relevance_subtotal,
        authority_subtotal=authority_subtotal,
        penalty_record=checked,
        penalty_policy=penalty_policy,
    )
    return validate_penalty_eligibility_evidence(
        make_hashed_payload(
            {
                "schema_version": "KNOWLEDGE_FEEDBACK_DERIVED_PENALTY_ELIGIBILITY_EVIDENCE_V1",
                "evidence_id": evidence_id,
                "candidate_id": checked["candidate_id"],
                "penalty_record_hash": deepcopy(checked["content_hash"]),
                "penalty_policy_hash": typed_payload_hash(dict(penalty_policy)),
                "provenance_state": checked["provenance_state"],
                "authority_only": True,
                "relevance_subtotal_before": applied["relevance_subtotal"],
                "relevance_subtotal_after": applied["relevance_subtotal"],
                "authority_subtotal_before": applied["authority_subtotal"],
                "authority_subtotal_after": applied["penalized_authority_subtotal"],
                "authority_blocked": applied["authority_blocked"],
                "eligible": not applied["authority_blocked"],
                "issue_codes": sorted(checked["issue_codes"]),
                "scientific_optimality": "NOT_ASSESSED",
                "test_only": True,
            }
        )
    )


__all__ = [
    "build_penalty_eligibility_evidence",
    "validate_penalty_eligibility_evidence",
]

