from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Mapping


SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
GIT_OID_RE = re.compile(r"^[0-9a-f]{40,64}$")
OWNERSHIP_VALUES = {"self", "entrusted", "unknown", "mixed"}
POLICY_FIELD_DEFAULTS: dict[str, Any] = {
    "analysis_kind": "standard",
    "risk_tier": "normal",
    "stratum_id": "general_50",
    "manual_review_required": False,
    "flagged_unsourced_count": 0,
    "source_internal_inconsistency_count": 0,
    "coverage_status": "complete",
    "answer_complete": True,
    "condition_codes": [],
    "safe_local_route_available": False,
    "producer_cohort": "stable",
}
EVENT_FIELDS = {
    "event_schema_version",
    "event_id",
    "analysis_artifact_ref",
    "analysis_artifact_sha256",
    "analysis_schema_ref",
    "analysis_revision",
    "context_pack_ref",
    "context_pack_sha256",
    "producer_binding_ref",
    "producer_behavior_hash",
    "ownership_snapshot_ref",
    "ownership_snapshot_sha256",
    "ownership_value",
    "completed_at",
    "source_commit",
    "source_tree",
    "completion_receipt_ref",
    *POLICY_FIELD_DEFAULTS,
    "content_hash",
}


class AnalysisCompletionEventError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisCompletionEventError("REQUIRED_TEXT_INVALID", field)
    return value.strip()


def _sha256(value: Any, field: str) -> str:
    text = _text(value, field).upper()
    if not SHA256_RE.fullmatch(text):
        raise AnalysisCompletionEventError("SHA256_INVALID", field)
    return text


def _timestamp(value: Any, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnalysisCompletionEventError("TIMESTAMP_INVALID", field) from exc
    if parsed.tzinfo is None:
        raise AnalysisCompletionEventError("TIMESTAMP_TIMEZONE_REQUIRED", field)
    return text


def _git_oid(value: Any, field: str) -> str:
    text = _text(value, field).lower()
    if not GIT_OID_RE.fullmatch(text):
        raise AnalysisCompletionEventError("GIT_OID_INVALID", field)
    return text


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AnalysisCompletionEventError("NON_NEGATIVE_INTEGER_REQUIRED", field)
    return value


def _event_identity(body: Mapping[str, Any]) -> str:
    identity = {
        "analysis_artifact_ref": body["analysis_artifact_ref"],
        "analysis_revision": body["analysis_revision"],
        "analysis_artifact_sha256": body["analysis_artifact_sha256"],
        "ownership_snapshot_sha256": body["ownership_snapshot_sha256"],
    }
    return "ACE-" + canonical_hash(identity)[:32]


def validate_analysis_completed_event(event: Mapping[str, Any]) -> dict[str, Any]:
    required_text = (
        "event_id",
        "event_schema_version",
        "analysis_artifact_ref",
        "analysis_schema_ref",
        "context_pack_ref",
        "producer_binding_ref",
        "ownership_snapshot_ref",
        "completion_receipt_ref",
        "analysis_kind",
        "risk_tier",
        "stratum_id",
        "coverage_status",
        "producer_cohort",
    )
    required_hashes = (
        "analysis_artifact_sha256",
        "context_pack_sha256",
        "producer_behavior_hash",
        "ownership_snapshot_sha256",
    )
    normalized = dict(event)
    if set(normalized) != EVENT_FIELDS:
        missing = sorted(EVENT_FIELDS - set(normalized))
        extra = sorted(set(normalized) - EVENT_FIELDS)
        raise AnalysisCompletionEventError(
            "EVENT_FIELD_SET_INVALID", f"missing={missing};extra={extra}"
        )
    for field in required_text:
        normalized[field] = _text(normalized.get(field), field)
    for field in required_hashes:
        normalized[field] = _sha256(normalized.get(field), field)
    normalized["completed_at"] = _timestamp(normalized.get("completed_at"), "completed_at")
    normalized["source_commit"] = _git_oid(normalized.get("source_commit"), "source_commit")
    normalized["source_tree"] = _git_oid(normalized.get("source_tree"), "source_tree")
    revision = normalized.get("analysis_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise AnalysisCompletionEventError("ANALYSIS_REVISION_INVALID")
    ownership = normalized.get("ownership_value")
    if ownership not in OWNERSHIP_VALUES:
        raise AnalysisCompletionEventError("OWNERSHIP_VALUE_INVALID", str(ownership))
    for field in ("flagged_unsourced_count", "source_internal_inconsistency_count"):
        normalized[field] = _non_negative_int(normalized.get(field), field)
    for field in ("manual_review_required", "answer_complete", "safe_local_route_available"):
        if not isinstance(normalized.get(field), bool):
            raise AnalysisCompletionEventError("BOOLEAN_REQUIRED", field)
    codes = normalized.get("condition_codes")
    if not isinstance(codes, list) or any(not isinstance(item, str) or not item for item in codes):
        raise AnalysisCompletionEventError("CONDITION_CODES_INVALID")
    if len(codes) != len(set(codes)):
        raise AnalysisCompletionEventError("CONDITION_CODES_DUPLICATE")
    if codes != sorted(codes):
        raise AnalysisCompletionEventError("CONDITION_CODES_NOT_CANONICAL")
    if normalized["event_schema_version"] != "ReviewOrchestrationAnalysisCompletedEvent-v1":
        raise AnalysisCompletionEventError("EVENT_SCHEMA_VERSION_UNSUPPORTED")
    if normalized["event_id"] != _event_identity(normalized):
        raise AnalysisCompletionEventError("EVENT_IDENTITY_MISMATCH")
    supplied_hash = _sha256(normalized.get("content_hash"), "content_hash")
    hash_body = {key: value for key, value in normalized.items() if key != "content_hash"}
    if supplied_hash != canonical_hash(hash_body):
        raise AnalysisCompletionEventError("EVENT_CONTENT_HASH_MISMATCH")
    return normalized


def build_analysis_completed_event(
    *,
    analysis_artifact_ref: str,
    analysis_artifact_sha256: str,
    analysis_schema_ref: str,
    analysis_revision: int,
    context_pack_ref: str,
    context_pack_sha256: str,
    producer_binding_ref: str,
    producer_behavior_hash: str,
    ownership_snapshot_ref: str,
    ownership_snapshot_sha256: str,
    ownership_value: str,
    completed_at: str,
    source_commit: str,
    source_tree: str,
    completion_receipt_ref: str,
    policy_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    policy = dict(POLICY_FIELD_DEFAULTS)
    if policy_fields:
        unknown = sorted(set(policy_fields) - set(POLICY_FIELD_DEFAULTS))
        if unknown:
            raise AnalysisCompletionEventError("POLICY_FIELD_NOT_ALLOWED", ",".join(unknown))
        policy.update(policy_fields)
    policy["condition_codes"] = sorted(policy["condition_codes"])
    body: dict[str, Any] = {
        "event_schema_version": "ReviewOrchestrationAnalysisCompletedEvent-v1",
        "analysis_artifact_ref": analysis_artifact_ref,
        "analysis_artifact_sha256": analysis_artifact_sha256.upper(),
        "analysis_schema_ref": analysis_schema_ref,
        "analysis_revision": analysis_revision,
        "context_pack_ref": context_pack_ref,
        "context_pack_sha256": context_pack_sha256.upper(),
        "producer_binding_ref": producer_binding_ref,
        "producer_behavior_hash": producer_behavior_hash.upper(),
        "ownership_snapshot_ref": ownership_snapshot_ref,
        "ownership_snapshot_sha256": ownership_snapshot_sha256.upper(),
        "ownership_value": ownership_value,
        "completed_at": completed_at,
        "source_commit": source_commit.lower(),
        "source_tree": source_tree.lower(),
        "completion_receipt_ref": completion_receipt_ref,
        **policy,
    }
    body["event_id"] = _event_identity(body)
    event = {**body, "content_hash": canonical_hash(body)}
    return validate_analysis_completed_event(event)


__all__ = [
    "AnalysisCompletionEventError",
    "build_analysis_completed_event",
    "canonical_hash",
    "canonical_json_bytes",
    "validate_analysis_completed_event",
]
