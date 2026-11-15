from __future__ import annotations

from typing import Any, Mapping

from .contracts import ReviewOrchestrationContractError, canonical_hash


def diagnose_recovery(
    evidence: Mapping[str, Any], *, attempt_count: int, attempt_limit: int
) -> dict[str, Any]:
    if (
        isinstance(attempt_count, bool)
        or isinstance(attempt_limit, bool)
        or not isinstance(attempt_count, int)
        or not isinstance(attempt_limit, int)
        or not 0 <= attempt_count <= attempt_limit
    ):
        raise ReviewOrchestrationContractError("ATTEMPT_COUNT_OR_LIMIT_INVALID")
    if evidence.get("terminal_receipt_state") == "DURABLE":
        classification = "TERMINAL_DURABLE"
        actions = ["FINALIZE_NO_SEND"]
        automatic_requeue = False
    elif evidence.get("partial_output_state") == "PRESENT":
        classification = "NON_CONSUMABLE_PARTIAL"
        actions = ["QUARANTINE_PARTIAL", "NO_ACTION"]
        automatic_requeue = False
    elif evidence.get("pre_send_receipt_state") == "PROVED_NOT_SENT" and evidence.get("provider_request_receipt_state") == "ABSENT":
        classification = "PROVED_NOT_SENT"
        automatic_requeue = attempt_count < attempt_limit
        actions = ["REQUEUE_AFTER_PROVED_NOT_SENT"] if automatic_requeue else ["NO_ACTION"]
    else:
        classification = "UNKNOWN_AFTER_SEND"
        actions = ["UNBLOCK_WITH_RECONCILIATION_EVIDENCE", "NO_ACTION"]
        automatic_requeue = False
    body = {
        "schema_version": "ReviewOrchestrationRecoveryPlan-v1",
        "crash_classification": classification,
        "allowed_operator_actions": actions,
        "automatic_requeue_authorized": automatic_requeue,
        "automatic_resend_authorized": False,
        "partial_output_consumable": False,
        "attempt_count": attempt_count,
        "attempt_limit": attempt_limit,
        "evidence_hash": canonical_hash(dict(evidence)),
    }
    return {**body, "content_hash": canonical_hash(body)}


def diagnose_lock(evidence: Mapping[str, Any]) -> dict[str, Any]:
    state = evidence.get("kernel_lock_state")
    if state == "LIVE":
        disposition = "BLOCKED_LIVE_LOCK"
        takeover = False
    elif state == "UNKNOWN":
        disposition = "BLOCKED_LOCK_AUTHORITY_UNKNOWN"
        takeover = False
    elif state == "STALE_PROVEN":
        if evidence.get("ledger_integrity") != "VALID" or evidence.get("owner_liveness") != "PROVED_DEAD":
            disposition = "BLOCKED_STALE_EVIDENCE_INSUFFICIENT"
            takeover = False
        else:
            disposition = "SANDBOX_CONTROLLED_RECOVERY_ALLOWED"
            takeover = True
    elif state == "ABSENT":
        disposition = "NO_LIVE_LOCK_OBSERVED"
        takeover = False
    else:
        raise ReviewOrchestrationContractError("KERNEL_LOCK_STATE_INVALID")
    body = {
        "schema_version": "ReviewOrchestrationLockDiagnosis-v1",
        "kernel_lock_state": state,
        "lease_is_diagnostic_only": True,
        "disposition": disposition,
        "sandbox_takeover_authorized": takeover,
        "production_takeover_authorized": False,
        "evidence_hash": canonical_hash(dict(evidence)),
    }
    return {**body, "content_hash": canonical_hash(body)}


__all__ = ["diagnose_lock", "diagnose_recovery"]
