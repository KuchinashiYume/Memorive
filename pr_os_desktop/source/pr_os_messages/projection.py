from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from pr_os_desktop_service.locator import StableLocator

from .contracts import (
    MessageContractError,
    MessageRecord,
    SEVERITY_RANK,
    StatusAxes,
    canonical_sha256,
    message_dedupe_key,
    message_id_for_dedupe,
    require_identifier,
    require_timestamp,
    validate_attachment_refs,
)
from .privacy import RedactionPolicy
from .store import MessageStore


SUPPRESSED_EVENT_TYPES = frozenset(
    {
        "NODE_STARTED",
        "NODE_COMPLETED",
        "PROGRESS_UPDATED",
        "RETRY_SCHEDULED",
        "AUTO_RECOVERED",
        "TRANSIENT_DISCONNECT",
    }
)
DEFAULT_TRIGGER_EVENT_TYPES = frozenset(
    {
        "JOB_FAILED",
        "JOB_ABORTED",
        "RETRY_LIMIT_EXCEEDED",
        "SERVICE_UNAVAILABLE",
        "ACTION_REQUIRED",
        "AUTHORIZATION_REQUIRED",
        "RISK_GATE_REACHED",
        "JOB_COMPLETED",
        "FINAL_ARTIFACT_READY",
        "REPORT_READY",
        "REPORT_FAILED",
        "SYSTEM_IMPACT_CHANGED",
        "USER_INFORMATION",
    }
)
_GENERIC_TITLE = {
    "RED": "任务需要注意",
    "YELLOW": "任务等待处理",
    "GREEN": "任务已完成",
    "BLUE": "有新的应用内消息",
}


class ProjectionIntegrityError(ValueError):
    pass


def _validate_event(raw: Mapping[str, Any], *, synthetic_only: bool) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ProjectionIntegrityError("MESSAGE_EVENT_SHAPE_INVALID")
    event = deepcopy(dict(raw))
    sequence = event.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
        raise ProjectionIntegrityError("MESSAGE_EVENT_SEQUENCE_INVALID")
    for field in ("event_id", "event_type", "job_id", "run_id", "root_cause"):
        try:
            event[field] = require_identifier(event.get(field), f"event_{field}")
        except MessageContractError as exc:
            raise ProjectionIntegrityError(str(exc)) from exc
    for optional in ("attempt_id", "node_id", "report_period"):
        if event.get(optional) is not None:
            try:
                event[optional] = require_identifier(event[optional], f"event_{optional}")
            except MessageContractError as exc:
                raise ProjectionIntegrityError(str(exc)) from exc
    try:
        event["occurred_at"] = require_timestamp(event.get("occurred_at"), "event_occurred_at")
    except MessageContractError as exc:
        raise ProjectionIntegrityError(str(exc)) from exc
    if event.get("target_locator") is not None:
        locator = StableLocator.parse(event["target_locator"])
        if synthetic_only:
            values = [locator.object_id] + ([locator.job_id] if locator.job_id is not None else [])
            if not all(value.startswith(("syn-", "job_fixture_", "attempt_fixture_", "artifact_fixture_", "event_fixture_", "action_fixture_")) for value in values):
                raise ProjectionIntegrityError("PRODUCTION_LOCATOR_FORBIDDEN")
    if event.get("severity") is not None:
        event["severity"] = str(event["severity"]).upper()
        if event["severity"] not in SEVERITY_RANK:
            raise ProjectionIntegrityError("MESSAGE_EVENT_SEVERITY_INVALID")
    if "message_required" in event and not isinstance(event["message_required"], bool):
        raise ProjectionIntegrityError("MESSAGE_EVENT_REQUIRED_FLAG_INVALID")
    return event


def _should_project(event: Mapping[str, Any]) -> bool:
    if event["event_type"] in SUPPRESSED_EVENT_TYPES:
        return False
    if event.get("message_required") is True:
        return True
    return event["event_type"] in DEFAULT_TRIGGER_EVENT_TYPES


class MessageProjectionEngine:
    def __init__(
        self,
        store: MessageStore,
        *,
        redaction_policy: RedactionPolicy | None = None,
        synthetic_only: bool = True,
        max_buffer: int = 256,
    ):
        if isinstance(max_buffer, bool) or not isinstance(max_buffer, int) or max_buffer <= 0:
            raise ProjectionIntegrityError("MESSAGE_EVENT_BUFFER_INVALID")
        self.store = store
        self.redaction_policy = redaction_policy or store.redaction_policy
        self.synthetic_only = synthetic_only
        self.max_buffer = max_buffer
        self._pending: dict[int, dict[str, Any]] = {}
        self._pending_fingerprints: dict[int, str] = {}

    def _project_event(self, state: dict[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
        if not _should_project(event):
            return {"sequence": event["sequence"], "event_id": event["event_id"], "result": "SUPPRESSED_BY_POLICY"}
        severity = event.get("severity")
        if severity is None:
            raise ProjectionIntegrityError("MESSAGE_EVENT_SEVERITY_REQUIRED")
        locator = event.get("target_locator")
        if severity in {"RED", "YELLOW"} and locator is None:
            raise ProjectionIntegrityError("ACTIONABLE_MESSAGE_LOCATOR_REQUIRED")
        fallback_title = _GENERIC_TITLE[severity]
        title = self.redaction_policy.sanitize(event.get("safe_title"), fallback=fallback_title, maximum=180)
        summary = self.redaction_policy.sanitize(
            event.get("safe_summary"), fallback="打开应用查看安全摘要。", maximum=320
        )
        body = self.redaction_policy.sanitize(
            event.get("safe_body", ""), fallback="请在应用内查看详细信息。", maximum=4000
        )
        attachments = validate_attachment_refs(event.get("attachment_refs", []))
        status_axes = StatusAxes.from_mapping(event.get("status_axes"))
        dedupe_key = message_dedupe_key(event)
        source_ref = {
            "event_id": event["event_id"],
            "sequence": event["sequence"],
            "job_id": event["job_id"],
            "run_id": event["run_id"],
            "attempt_id": event.get("attempt_id"),
        }
        if event.get("node_id") is not None:
            source_ref["node_id"] = event["node_id"]
        existing_id = state["dedupe_index"].get(dedupe_key)
        if existing_id is None:
            message_id = message_id_for_dedupe(dedupe_key)
            if message_id in state["messages"]:
                raise ProjectionIntegrityError("MESSAGE_ID_COLLISION")
            record = MessageRecord(
                message_id=message_id,
                dedupe_key=dedupe_key,
                severity=severity,
                title=title,
                summary=summary,
                body=body,
                created_at=event["occurred_at"],
                first_seen_at=event["occurred_at"],
                last_seen_at=event["occurred_at"],
                occurrence_count=1,
                read_state="UNREAD",
                read_at=None,
                confirmation_state="UNCONFIRMED",
                confirmed_at=None,
                collection_state="CURRENT",
                starred=False,
                starred_at=None,
                protected_bulk_read=bool(
                    event.get("protected_bulk_read", False)
                    or severity in {"RED", "YELLOW"}
                    or event["event_type"] in {"REPORT_READY", "REPORT_FAILED"}
                ),
                attachment_refs=attachments,
                target_locator=locator,
                notification_state="NOT_EVALUATED",
                source_event_ref=source_ref,
                status_axes=status_axes,
            )
            accepted = record.to_dict()
            self.redaction_policy.assert_public_safe(accepted)
            state["messages"][message_id] = accepted
            state["dedupe_index"][dedupe_key] = message_id
            return {
                "sequence": event["sequence"],
                "event_id": event["event_id"],
                "result": "MESSAGE_CREATED",
                "message_id": message_id,
            }

        raw = deepcopy(state["messages"][existing_id])
        existing = MessageRecord.from_mapping(raw)
        if existing.dedupe_key != dedupe_key:
            raise ProjectionIntegrityError("DEDUPE_KEY_COLLISION")
        upgraded = SEVERITY_RANK[severity] < SEVERITY_RANK[existing.severity]
        raw.update(
            {
                "severity": severity if upgraded else existing.severity,
                "title": title,
                "summary": summary,
                "body": body,
                "last_seen_at": event["occurred_at"],
                "occurrence_count": existing.occurrence_count + 1,
                "attachment_refs": [dict(row) for row in attachments],
                "target_locator": locator or existing.target_locator,
                "source_event_ref": source_ref,
                "status_axes": status_axes.to_dict(),
            }
        )
        if upgraded:
            raw["notification_state"] = "NOT_EVALUATED"
            if existing.collection_state == "HISTORY":
                raw.update(
                    {
                        "read_state": "UNREAD",
                        "read_at": None,
                        "confirmation_state": "UNCONFIRMED",
                        "confirmed_at": None,
                        "collection_state": "CURRENT",
                    }
                )
        updated = MessageRecord.from_mapping(raw)
        accepted = updated.to_dict()
        self.redaction_policy.assert_public_safe(accepted)
        state["messages"][existing_id] = accepted
        return {
            "sequence": event["sequence"],
            "event_id": event["event_id"],
            "result": "MESSAGE_UPDATED",
            "message_id": existing_id,
            "severity_upgraded": upgraded,
            "occurrence_count": updated.occurrence_count,
        }

    def ingest(self, events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        source = list(events)
        envelope = self.store.load(recover_corruption=False)
        cursor = envelope["state"]["projection"]["cursor"]
        persisted_fingerprints = {
            int(sequence): digest
            for sequence, digest in envelope["state"]["projection"]["fingerprints"].items()
        }
        duplicate_count = 0
        stale_count = 0
        out_of_order = False
        sequences: list[int] = []
        for raw in source:
            event = _validate_event(raw, synthetic_only=self.synthetic_only)
            sequence = event["sequence"]
            sequences.append(sequence)
            fingerprint = canonical_sha256(event)
            prior = self._pending_fingerprints.get(sequence) or persisted_fingerprints.get(sequence)
            if prior is not None:
                if prior != fingerprint:
                    raise ProjectionIntegrityError("SAME_SEQUENCE_CONFLICT")
                duplicate_count += 1
                continue
            if sequence <= cursor:
                stale_count += 1
                continue
            self._pending[sequence] = event
            self._pending_fingerprints[sequence] = fingerprint
        if len(self._pending) > self.max_buffer:
            raise ProjectionIntegrityError("MESSAGE_EVENT_BUFFER_OVERFLOW")
        if sequences:
            out_of_order = sequences != sorted(sequences)

        accepted_events: list[dict[str, Any]] = []
        next_cursor = cursor
        working_fingerprints = dict(persisted_fingerprints)
        while next_cursor + 1 in self._pending:
            event = self._pending.pop(next_cursor + 1)
            fingerprint = self._pending_fingerprints.pop(next_cursor + 1)
            accepted_events.append(event)
            next_cursor += 1
            working_fingerprints[next_cursor] = fingerprint
        working_fingerprints = dict(sorted(working_fingerprints.items())[-512:])

        def apply(state: dict[str, Any]) -> list[dict[str, Any]]:
            if state["projection"]["cursor"] != cursor:
                raise ProjectionIntegrityError("MESSAGE_CURSOR_REVISION_CONFLICT")
            actions = [self._project_event(state, event) for event in accepted_events]
            state["projection"] = {
                "cursor": next_cursor,
                "fingerprints": {str(sequence): digest for sequence, digest in working_fingerprints.items()},
            }
            return actions

        _, actions = self.store.transaction(apply)
        return {
            "schema_version": "P08T07MessageProjectionReceipt-v1",
            "input_count": len(source),
            "accepted_event_count": len(accepted_events),
            "message_created_count": sum(row["result"] == "MESSAGE_CREATED" for row in actions),
            "message_updated_count": sum(row["result"] == "MESSAGE_UPDATED" for row in actions),
            "suppressed_event_count": sum(row["result"] == "SUPPRESSED_BY_POLICY" for row in actions),
            "duplicate_count": duplicate_count,
            "stale_count": stale_count,
            "out_of_order_input": out_of_order,
            "gap_detected": bool(self._pending),
            "pending_sequences": sorted(self._pending),
            "cursor": next_cursor,
            "actions": actions,
            "raw_private_payload_persisted": False,
            "status": "RECONNECT_REQUIRED" if self._pending else "PASS",
        }


__all__ = [
    "DEFAULT_TRIGGER_EVENT_TYPES",
    "MessageProjectionEngine",
    "ProjectionIntegrityError",
    "SUPPRESSED_EVENT_TYPES",
]
