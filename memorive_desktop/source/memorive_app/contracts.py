from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from .errors import ContractViolation


CONTRACT_REVISION = "1.0"
SHA256_RE = re.compile(r"^[0-9A-F]{64}$")


class JobControlState(str, Enum):
    CREATED = "CREATED"
    PREFLIGHT = "PREFLIGHT"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    PAUSED = "PAUSED"
    RESUME_REQUESTED = "RESUME_REQUESTED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    RECOVERING = "RECOVERING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED_BEFORE_START = "BLOCKED_BEFORE_START"


JOB_CONTROL_STATES = frozenset(state.value for state in JobControlState)
VERIFICATION_RESULTS = frozenset({"PASS", "FAIL", "ERROR", "NOT_RUN", "NOT_ASSESSED"})
ACCEPTANCE_VERDICTS = frozenset({"PASS", "FAIL", "NOT_ASSESSED"})
TECHNICAL_HANDOFF_STATUSES = frozenset({"READY", "NOT_READY", "REVOKED"})
FORMALIZATION_STATUSES = frozenset({"AUTHORIZED", "NOT_AUTHORIZED", "FORMALIZED"})
DEPLOYMENT_STATUSES = frozenset({"disabled", "staged", "active", "rolled_back"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractViolation("CANONICAL_JSON_INVALID") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def immutable_copy(value: Any) -> Any:
    return json.loads(canonical_json_bytes(value).decode("utf-8"))


def require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractViolation(f"{field.upper()}_INVALID")
    return value.strip()


def require_timestamp(value: Any, field: str) -> str:
    accepted = require_text(value, field)
    try:
        parsed = datetime.fromisoformat(accepted.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractViolation(f"{field.upper()}_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractViolation(f"{field.upper()}_OFFSET_REQUIRED")
    return accepted


def require_sha256(value: Any, field: str) -> str:
    accepted = require_text(value, field).upper()
    if not SHA256_RE.fullmatch(accepted):
        raise ContractViolation(f"{field.upper()}_INVALID")
    return accepted


def resolved_profile_snapshot(
    *,
    profile_id: str,
    role: str,
    capability_revision: str,
    config_revision: str,
    config_sha256: str,
    credential_refs: Sequence[str],
    execution_channel: str,
    fallback_policy: str,
    resource_policy: Mapping[str, Any],
    budget_policy: Mapping[str, Any],
    egress_policy: str,
    data_classification: str,
    module_revisions: Mapping[str, str],
    source_hashes: Mapping[str, str],
    created_at: str,
    resolver_revision: str,
) -> dict[str, Any]:
    refs = [require_text(item, "credential_ref") for item in credential_refs]
    if any(not item.startswith(("ENV:", "REF:")) for item in refs):
        raise ContractViolation("CREDENTIAL_REF_NOT_REFERENCE")
    sources = {require_text(key, "source_hash_key"): require_sha256(value, "source_hash") for key, value in source_hashes.items()}
    payload = {
        "schema_version": "ResolvedProfileSnapshot-v1",
        "contract_revision": CONTRACT_REVISION,
        "profile_id": require_text(profile_id, "profile_id"),
        "role": require_text(role, "role"),
        "capability_revision": require_text(capability_revision, "capability_revision"),
        "config_revision": require_text(config_revision, "config_revision"),
        "config_sha256": require_sha256(config_sha256, "config_sha256"),
        "credential_refs": refs,
        "execution_channel": require_text(execution_channel, "execution_channel"),
        "fallback_policy": require_text(fallback_policy, "fallback_policy"),
        "resource_policy": immutable_copy(resource_policy),
        "budget_policy": immutable_copy(budget_policy),
        "egress_policy": require_text(egress_policy, "egress_policy"),
        "data_classification": require_text(data_classification, "data_classification"),
        "module_revisions": {str(key): require_text(value, "module_revision") for key, value in sorted(module_revisions.items())},
        "source_hashes": dict(sorted(sources.items())),
        "created_at": require_timestamp(created_at, "created_at"),
        "resolver_revision": require_text(resolver_revision, "resolver_revision"),
    }
    payload["snapshot_ref"] = canonical_sha256(payload)
    return payload


def error_envelope(
    *,
    code: str,
    category: str,
    severity: str,
    retryable: bool,
    user_action: str,
    technical_summary: str,
    correlation_id: str,
    job_id: str | None = None,
    attempt_id: str | None = None,
    retry_after: str | None = None,
    cause_ref: str | None = None,
    support_bundle_recommended: bool = False,
) -> dict[str, Any]:
    if not isinstance(retryable, bool) or not isinstance(support_bundle_recommended, bool):
        raise ContractViolation("ERROR_BOOLEAN_INVALID")
    return {
        "schema_version": "ErrorEnvelope-v1",
        "contract_revision": CONTRACT_REVISION,
        "error_code": require_text(code, "error_code"),
        "category": require_text(category, "category"),
        "severity": require_text(severity, "severity"),
        "retryable": retryable,
        "retry_after": retry_after,
        "user_action": require_text(user_action, "user_action"),
        "technical_summary": require_text(technical_summary, "technical_summary"),
        "correlation_id": require_text(correlation_id, "correlation_id"),
        "job_id": job_id,
        "attempt_id": attempt_id,
        "cause_ref": cause_ref,
        "support_bundle_recommended": support_bundle_recommended,
    }


__all__ = [
    "ACCEPTANCE_VERDICTS",
    "CONTRACT_REVISION",
    "DEPLOYMENT_STATUSES",
    "FORMALIZATION_STATUSES",
    "JOB_CONTROL_STATES",
    "JobControlState",
    "TECHNICAL_HANDOFF_STATUSES",
    "VERIFICATION_RESULTS",
    "canonical_json_bytes",
    "canonical_sha256",
    "error_envelope",
    "immutable_copy",
    "resolved_profile_snapshot",
    "utc_now",
]
