"""Memorive FACT-IDENTITY/EVIDENCE_EXTRACTION shared contract primitives.

This module deliberately has no dependency on EVIDENCE_REVIEW.  EVIDENCE_EXTRACTION producers enforce these
deterministic primitives directly, while EVIDENCE_REVIEW imports and re-exports the same
non-bypassable absence contract without creating a reverse dependency.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, MutableSequence


REVIEW_STATUSES = ("pending", "active", "quarantined")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ID = re.compile(r"^art_[0-9a-f]{32}$")


class ContractError(ValueError):
    """Base error for deterministic FACT-IDENTITY contract failures."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class AbsenceEvidenceError(ContractError):
    """Raised when absence evidence cannot support ``absent_in_source``."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_payload_bytes(payload: Mapping[str, Any]) -> bytes:
    return (canonical_json(payload) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path | str) -> str:
    return sha256_bytes(Path(path).read_bytes())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError("INVALID_TEXT", f"{field} must be non-empty text")
    return value.strip()


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ContractError("INVALID_SHA256", f"{field} must be lowercase SHA-256")
    return value


def _text_list(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ContractError("INVALID_TEXT_LIST", f"{field} must be a text list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ContractError(
            "INVALID_TEXT_LIST", f"{field} contains empty or non-text values"
        )
    if len(value) != len(set(value)):
        raise ContractError("DUPLICATE_TEXT_LIST", f"{field} must be unique")
    return value


def _audit(
    events: MutableSequence[dict[str, Any]] | None,
    event: str,
    reason_code: str,
    **details: Any,
) -> None:
    if events is not None:
        events.append(
            {"event": event, "reason_code": reason_code, "details": details}
        )


def planned_artifact_ref(
    payload: Mapping[str, Any], *, artifact_type: str
) -> dict[str, str]:
    content_hash = sha256_bytes(canonical_payload_bytes(payload))
    identity = sha256_bytes(f"{artifact_type}\x1f{content_hash}".encode("utf-8"))
    return {"artifact_id": "art_" + identity[:32], "content_hash": content_hash}


def validate_artifact_ref(
    ref: Mapping[str, Any],
    *,
    payload: Mapping[str, Any] | None = None,
    artifact_type: str | None = None,
) -> Mapping[str, Any]:
    if not isinstance(ref, Mapping) or set(ref) != {"artifact_id", "content_hash"}:
        raise ContractError(
            "ARTIFACT_REF_FIELDS",
            "artifact ref must contain artifact_id and content_hash",
        )
    artifact_id = ref.get("artifact_id")
    if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(artifact_id):
        raise ContractError("ARTIFACT_REF_ID", "invalid artifact_id")
    _sha(ref.get("content_hash"), "artifact_ref.content_hash")
    if payload is not None:
        if not artifact_type:
            raise ContractError(
                "ARTIFACT_TYPE_REQUIRED",
                "artifact_type is required when checking payload",
            )
        expected = planned_artifact_ref(payload, artifact_type=artifact_type)
        if dict(ref) != expected:
            raise ContractError(
                "ARTIFACT_REF_HASH_MISMATCH",
                "artifact ref does not match canonical payload",
            )
    return ref


def validate_absence_policy(policy: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate the deterministic policy subset used by EVIDENCE_EXTRACTION and EVIDENCE_REVIEW."""

    required = {
        "artifact_type",
        "policy_version",
        "required_sections_or_roles",
        "allowed_methods",
        "coverage_rule",
        "created_at",
    }
    if not isinstance(policy, Mapping) or set(policy) != required:
        raise AbsenceEvidenceError(
            "ABSENCE_POLICY_FIELDS", "absence policy field set must be exact"
        )
    if policy.get("artifact_type") != "absence_check_policy":
        raise AbsenceEvidenceError("ABSENCE_POLICY_TYPE", "wrong artifact type")
    _text(policy.get("policy_version"), "absence_policy.policy_version")
    _text_list(
        policy.get("required_sections_or_roles"), "required_sections_or_roles"
    )
    _text_list(policy.get("allowed_methods"), "allowed_methods")
    if policy.get("coverage_rule") != "all_required_roles_checked":
        raise AbsenceEvidenceError(
            "ABSENCE_POLICY_COVERAGE", "coverage must require all roles"
        )
    _text(policy.get("created_at"), "absence_policy.created_at")
    return policy


def validate_absence_check_evidence(
    payload: Mapping[str, Any],
    artifact_ref: Mapping[str, Any],
    *,
    template_contract_ref: Mapping[str, Any],
    policy_payload: Mapping[str, Any],
    policy_ref: Mapping[str, Any],
    source_artifact_ref: Mapping[str, Any],
    source_content_hash: str,
    slot_id: str,
) -> Mapping[str, Any]:
    """Apply the non-bypassable shared absence-evidence contract."""

    if not isinstance(payload, Mapping):
        raise AbsenceEvidenceError(
            "ABSENCE_EVIDENCE_OBJECT", "absence evidence must be an object"
        )
    validate_artifact_ref(
        artifact_ref, payload=payload, artifact_type="absence_check_evidence"
    )
    validate_absence_policy(policy_payload)
    validate_artifact_ref(
        policy_ref, payload=policy_payload, artifact_type="absence_check_policy"
    )
    if payload.get("template_contract_ref") != dict(template_contract_ref):
        raise AbsenceEvidenceError("ABSENCE_TEMPLATE_REF", "template ref mismatch")
    if payload.get("policy_ref") != dict(policy_ref):
        raise AbsenceEvidenceError("ABSENCE_POLICY_REF", "policy ref mismatch")
    if payload.get("source_artifact_ref") != dict(source_artifact_ref):
        raise AbsenceEvidenceError("ABSENCE_SOURCE_REF", "source ref mismatch")
    if payload.get("source_content_hash") != source_content_hash:
        raise AbsenceEvidenceError("ABSENCE_SOURCE_HASH", "source hash mismatch")
    if payload.get("slot_id") != slot_id:
        raise AbsenceEvidenceError("ABSENCE_SLOT", "slot mismatch")
    required = set(policy_payload["required_sections_or_roles"])
    payload_required = set(payload.get("required_sections_or_roles", []))
    checked = set(payload.get("checked_sections_or_roles", []))
    if not required.issubset(payload_required) or not payload_required.issubset(checked):
        raise AbsenceEvidenceError(
            "ABSENCE_COVERAGE_INSUFFICIENT", "required roles were not fully checked"
        )
    if payload.get("coverage_complete") is not True:
        raise AbsenceEvidenceError(
            "ABSENCE_COVERAGE_INCOMPLETE", "coverage_complete must be true"
        )
    if payload.get("method") not in policy_payload["allowed_methods"]:
        raise AbsenceEvidenceError("ABSENCE_METHOD", "method is not allowed")
    if payload.get("unresolved_conflicts") != []:
        raise AbsenceEvidenceError(
            "ABSENCE_CONFLICT_UNRESOLVED", "unresolved check conflicts remain"
        )
    _text_list(payload.get("search_terms_or_rules"), "search_terms_or_rules")
    _text_list(payload.get("evidence_refs"), "evidence_refs")
    _text(payload.get("checked_by"), "checked_by")
    _text(payload.get("checked_at"), "checked_at")
    return payload


def validate_review_status(value: Any) -> str:
    if value not in REVIEW_STATUSES:
        raise ContractError(
            "INVALID_REVIEW_STATUS",
            f"review_status must be one of {REVIEW_STATUSES}, got {value!r}",
        )
    return str(value)


__all__ = [
    "AbsenceEvidenceError",
    "ContractError",
    "REVIEW_STATUSES",
    "canonical_json",
    "canonical_payload_bytes",
    "now_iso",
    "planned_artifact_ref",
    "sha256_bytes",
    "sha256_file",
    "validate_absence_check_evidence",
    "validate_absence_policy",
    "validate_artifact_ref",
    "validate_review_status",
]
