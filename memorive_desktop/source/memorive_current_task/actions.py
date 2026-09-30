from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
import uuid
from typing import Any, Callable, Mapping

from .contracts import canonical_sha256, immutable, require_identifier


class ActionError(ValueError):
    pass


class CommandDisabled(ActionError):
    pass


class ConfirmationError(ActionError):
    pass


_SENSITIVE_KEY = re.compile(r"(^|_)(password|secret|token|cookie|credential|private_payload|gold|holdout)($|_)", re.IGNORECASE)


class ActionController:
    def __init__(self, adapter: Any, *, clock: Callable[[], datetime] | None = None, confirmation_seconds: int = 10):
        self.adapter = adapter
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.confirmation_seconds = confirmation_seconds
        self._confirmation: dict[str, Any] | None = None
        self._dedupe: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}

    def _job(self, job_id: str) -> dict[str, Any]:
        return self.adapter.call("get_job", {"job_id": job_id})

    def available_actions(self, job: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        state = job.get("control_state", job.get("job_control_state"))
        same_inputs = job.get("frozen_input_hashes") == job.get("current_input_hashes")
        pause_enabled = state == "RUNNING" and bool(job.get("pause_supported")) and bool(job.get("safe_checkpoint"))
        cancel_enabled = state in {"QUEUED", "RUNNING", "PAUSE_REQUESTED", "PAUSED", "RESUME_REQUESTED", "RECOVERY_REQUIRED", "RECOVERING"}
        resume_enabled = state == "PAUSED" and same_inputs
        retry_enabled = state in {"FAILED", "CANCELLED"}
        pending = [row for row in job.get("action_requests", []) if row.get("state") == "PENDING"]
        return {
            "pause": {"enabled": pause_enabled, "disabled_reason": None if pause_enabled else "PAUSE_REQUIRES_RUNNING_SAFE_CHECKPOINT", "confirmation_required": False},
            "cancel": {"enabled": cancel_enabled, "disabled_reason": None if cancel_enabled else "CANCEL_STATE_NOT_ELIGIBLE", "confirmation_required": True},
            "resume": {"enabled": resume_enabled, "disabled_reason": None if resume_enabled else ("RESUME_INPUT_DRIFT" if state == "PAUSED" and not same_inputs else "RESUME_REQUIRES_PAUSED"), "confirmation_required": False},
            "retry": {"enabled": retry_enabled, "disabled_reason": None if retry_enabled else "RETRY_REQUIRES_FAILED_OR_CANCELED", "confirmation_required": False},
            "respond_to_action": {"enabled": bool(pending), "disabled_reason": None if pending else "NO_PENDING_ACTION", "confirmation_required": False},
        }

    def _deduped_call(self, action: str, idempotency_key: str, payload: Mapping[str, Any], invoke: Callable[[], Any]) -> dict[str, Any]:
        key = require_identifier(idempotency_key, "idempotency_key")
        payload_hash = canonical_sha256(payload)
        slot = (action, key)
        prior = self._dedupe.get(slot)
        if prior is not None:
            if prior[0] != payload_hash:
                raise ActionError("IDEMPOTENCY_CONFLICT")
            return deepcopy(prior[1])
        result = invoke()
        receipt = {
            "schema_version": "CurrentTaskActionReceipt-v1",
            "action": action,
            "idempotency_key": key,
            "request_sha256": payload_hash,
            "facade_receipt": result,
            "receipt_sha256": canonical_sha256({"action": action, "idempotency_key": key, "request_sha256": payload_hash, "facade_receipt": result}),
        }
        self._dedupe[slot] = (payload_hash, immutable(receipt))
        return immutable(receipt)

    def _replay(self, action: str, idempotency_key: str, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        key = require_identifier(idempotency_key, "idempotency_key")
        prior = self._dedupe.get((action, key))
        if prior is None:
            return None
        if prior[0] != canonical_sha256(payload):
            raise ActionError("IDEMPOTENCY_CONFLICT")
        return deepcopy(prior[1])

    def execute(self, action: str, job_id: str, *, expected_version: int, idempotency_key: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if action == "cancel":
            raise ConfirmationError("CANCEL_CONFIRM_REQUIRED")
        if action not in {"pause", "resume", "retry", "respond_to_action"}:
            raise ActionError("ACTION_UNKNOWN")
        accepted = dict(payload or {})
        request = {"job_id": job_id, "expected_version": expected_version, "payload": accepted}
        replay = self._replay(action, idempotency_key, request)
        if replay is not None:
            return replay
        job = self._job(job_id)
        availability = self.available_actions(job).get(action)
        if availability is None:
            raise ActionError("ACTION_UNKNOWN")
        if not availability["enabled"]:
            raise CommandDisabled(str(availability["disabled_reason"]))
        if action == "pause":
            return self._deduped_call(action, idempotency_key, request, lambda: self.adapter.call("pause_job", {"job_id": job_id, "expected_version": expected_version, "idempotency_key": idempotency_key}))
        if action == "resume":
            return self._deduped_call(action, idempotency_key, request, lambda: self.adapter.call("resume_job", {"job_id": job_id, "expected_version": expected_version, "idempotency_key": idempotency_key}))
        if action == "retry":
            retry_policy = accepted.get("retry_policy")
            if not isinstance(retry_policy, Mapping):
                raise ActionError("RETRY_POLICY_REQUIRED")
            return self._deduped_call(action, idempotency_key, request, lambda: self.adapter.call("retry_job", {"job_id": job_id, "retry_policy": dict(retry_policy), "idempotency_key": idempotency_key}))
        if action == "respond_to_action":
            request_id = accepted.get("action_request_id")
            response = accepted.get("response")
            if not isinstance(request_id, str) or not isinstance(response, Mapping) or not response:
                raise ActionError("ACTION_RESPONSE_INVALID")
            self._reject_sensitive(response)
            return self._deduped_call(
                action,
                idempotency_key,
                request,
                lambda: self.adapter.call("respond_to_action", {"job_id": job_id, "action_request_id": request_id, "response": dict(response), "expected_version": expected_version, "idempotency_key": idempotency_key}),
            )
        raise ActionError("ACTION_UNKNOWN")

    def begin_cancel(self, job_id: str, *, expected_version: int, now: datetime | None = None) -> dict[str, Any]:
        job = self._job(job_id)
        availability = self.available_actions(job)["cancel"]
        if not availability["enabled"]:
            raise CommandDisabled(str(availability["disabled_reason"]))
        issued = now or self.clock()
        token = f"confirm-{uuid.uuid4().hex}"
        self._confirmation = {"token": token, "job_id": job_id, "expected_version": expected_version, "issued_at": issued, "expires_at": issued + timedelta(seconds=self.confirmation_seconds)}
        return {"schema_version": "CurrentTaskCancelConfirmation-v1", "token": token, "job_id": job_id, "expected_version": expected_version, "expires_at": self._confirmation["expires_at"].isoformat(), "executed": False, "countdown_seconds": self.confirmation_seconds}

    def confirm_cancel(self, token: str, job_id: str, *, expected_version: int, idempotency_key: str, now: datetime | None = None) -> dict[str, Any]:
        pending = self._confirmation
        accepted_now = now or self.clock()
        if pending is None:
            raise ConfirmationError("CANCEL_CONFIRM_MISSING")
        if (token, job_id, expected_version) != (pending["token"], pending["job_id"], pending["expected_version"]):
            self._confirmation = None
            raise ConfirmationError("CANCEL_CONFIRM_CONTEXT_CHANGED")
        if accepted_now > pending["expires_at"]:
            self._confirmation = None
            raise ConfirmationError("CANCEL_CONFIRM_TIMEOUT")
        self._confirmation = None
        request = {"job_id": job_id, "expected_version": expected_version, "confirmation_token": token}
        return self._deduped_call("cancel", idempotency_key, request, lambda: self.adapter.call("cancel_job", {"job_id": job_id, "expected_version": expected_version, "idempotency_key": idempotency_key}))

    def context_changed(self) -> None:
        self._confirmation = None

    @staticmethod
    def _reject_sensitive(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if _SENSITIVE_KEY.search(str(key)):
                    raise ActionError("ACTION_RESPONSE_SENSITIVE_FIELD_FORBIDDEN")
                ActionController._reject_sensitive(child)
        elif isinstance(value, list):
            for child in value:
                ActionController._reject_sensitive(child)
