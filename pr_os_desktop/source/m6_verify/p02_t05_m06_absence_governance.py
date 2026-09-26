"""PR-OS P02/T05/M06 absence evidence and coverage governance."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from m2_distill.p02_t05_m02_contract_core import (
    AbsenceEvidenceError,
    _sha,
    _text,
    validate_absence_check_evidence,
    validate_absence_policy,
    validate_artifact_ref,
)


COVERAGE_STATES = (
    "sufficient",
    "partial",
    "suspicious_gap",
    "not_assessed",
)


def build_absence_check_payload(
    *,
    template_contract_ref: Mapping[str, Any],
    slot_id: str,
    policy_ref: Mapping[str, Any],
    source_artifact_ref: Mapping[str, Any],
    source_content_hash: str,
    required_sections_or_roles: Sequence[str],
    checked_sections_or_roles: Sequence[str],
    search_terms_or_rules: Sequence[str],
    method: str,
    checked_by: str,
    checked_at: str,
    evidence_refs: Sequence[str],
    coverage_complete: bool,
    unresolved_conflicts: Sequence[str] = (),
) -> dict[str, Any]:
    for ref in (template_contract_ref, policy_ref, source_artifact_ref):
        validate_artifact_ref(ref)
    _sha(source_content_hash, "source_content_hash")
    return {
        "template_contract_ref": dict(template_contract_ref),
        "slot_id": _text(slot_id, "slot_id"),
        "policy_ref": dict(policy_ref),
        "source_artifact_ref": dict(source_artifact_ref),
        "source_content_hash": source_content_hash,
        "required_sections_or_roles": list(required_sections_or_roles),
        "checked_sections_or_roles": list(checked_sections_or_roles),
        "search_terms_or_rules": list(search_terms_or_rules),
        "method": _text(method, "method"),
        "coverage_complete": bool(coverage_complete),
        "checked_by": _text(checked_by, "checked_by"),
        "checked_at": _text(checked_at, "checked_at"),
        "evidence_refs": list(evidence_refs),
        "unresolved_conflicts": list(unresolved_conflicts),
    }


def aggregate_coverage(states: Iterable[str]) -> str:
    """Derive M6's deterministic coverage verdict for an M2 assessment."""

    values = list(states)
    if not values or all(value == "not_assessed" for value in values):
        return "not_assessed"
    if any(value == "extraction_gap" for value in values):
        return "suspicious_gap"
    if any(value == "not_assessed" for value in values):
        return "partial"
    return "sufficient"


__all__ = [
    "AbsenceEvidenceError",
    "COVERAGE_STATES",
    "aggregate_coverage",
    "build_absence_check_payload",
    "validate_absence_check_evidence",
    "validate_absence_policy",
]
