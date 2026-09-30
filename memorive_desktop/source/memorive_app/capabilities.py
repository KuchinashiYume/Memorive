from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from .contracts import canonical_sha256, immutable_copy, require_text, require_timestamp
from .errors import ContractViolation


REQUIREMENTS = frozenset({"required", "optional"})
AVAILABILITIES = frozenset({"ready", "disabled", "not_configured", "not_applicable", "blocked"})
QUALIFICATIONS = frozenset({"qualified", "not_qualified", "not_assessed"})


def _validate_state(value: Mapping[str, Any]) -> dict[str, Any]:
    capability_id = require_text(value.get("capability_id"), "capability_id")
    requirement = value.get("requirement")
    availability = value.get("availability")
    qualification = value.get("qualification")
    enabled = value.get("effective_enabled")
    if requirement not in REQUIREMENTS:
        raise ContractViolation("CAPABILITY_REQUIREMENT_INVALID")
    if availability not in AVAILABILITIES:
        raise ContractViolation("CAPABILITY_AVAILABILITY_INVALID")
    if qualification not in QUALIFICATIONS:
        raise ContractViolation("CAPABILITY_QUALIFICATION_INVALID")
    if not isinstance(enabled, bool):
        raise ContractViolation("CAPABILITY_ENABLED_INVALID")
    reason = require_text(value.get("reason_code"), "reason_code")
    action = require_text(value.get("required_action"), "required_action")
    if enabled and (availability != "ready" or qualification != "qualified"):
        raise ContractViolation("CAPABILITY_EFFECTIVE_STATE_INVALID")
    if not enabled and availability == "ready" and qualification == "qualified" and reason == "NONE":
        raise ContractViolation("CAPABILITY_EFFECTIVE_STATE_INVALID")
    return {
        "capability_id": capability_id,
        "requirement": requirement,
        "availability": availability,
        "qualification": qualification,
        "effective_enabled": enabled,
        "reason_code": reason,
        "required_action": action,
    }


def build_capability_snapshot(
    states: Sequence[Mapping[str, Any]],
    *,
    revision: str,
    created_at: str,
    ttl_seconds: int,
    source: str = "caller_declaration",
) -> dict[str, Any]:
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise ContractViolation("CAPABILITY_TTL_INVALID")
    accepted = sorted((_validate_state(value) for value in states), key=lambda item: item["capability_id"])
    ids = [item["capability_id"] for item in accepted]
    if len(ids) != len(set(ids)):
        raise ContractViolation("CAPABILITY_ID_DUPLICATE")
    payload = {
        "schema_version": "CapabilitySnapshot-v1",
        "revision": require_text(revision, "revision"),
        "created_at": require_timestamp(created_at, "created_at"),
        "ttl_seconds": ttl_seconds,
        "source": require_text(source, "source"),
        "capabilities": immutable_copy(accepted),
        "status": "READY",
    }
    payload["snapshot_ref"] = canonical_sha256(payload)
    return payload


def evaluate_preflight(snapshot: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    capabilities = snapshot.get("capabilities")
    if not isinstance(capabilities, list):
        raise ContractViolation("CAPABILITY_SNAPSHOT_INVALID")
    created_at = datetime.fromisoformat(require_timestamp(snapshot.get("created_at"), "created_at").replace("Z", "+00:00"))
    ttl_seconds = snapshot.get("ttl_seconds")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise ContractViolation("CAPABILITY_TTL_INVALID")
    accepted_now = now or datetime.now(timezone.utc)
    expired = accepted_now > created_at + timedelta(seconds=ttl_seconds)
    blockers = ["CAPABILITY_SNAPSHOT_EXPIRED"] if expired else []
    degradations = []
    for item in capabilities:
        accepted = _validate_state(item)
        if accepted["requirement"] == "required" and not accepted["effective_enabled"]:
            blockers.append(accepted["capability_id"])
        elif accepted["requirement"] == "optional" and not accepted["effective_enabled"]:
            degradations.append(accepted["capability_id"])
    return {
        "status": "BLOCKED" if blockers else "PASS",
        "blockers": sorted(blockers),
        "degradations": sorted(degradations),
        "snapshot_expired": expired,
    }


__all__ = ["build_capability_snapshot", "evaluate_preflight"]
