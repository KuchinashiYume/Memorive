"""A job attempt's events in order for the page (TimelineReconciler) and picking up again after
a lost event stream (WatchRecoveryController).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from memorive.desktop.current_task.contracts import KNOWN_CONTROL_STATES, TERMINAL_CONTROL_STATES, canonical_sha256, require_identifier


class EventIntegrityError(ValueError):
    pass


class TimelineReconciler:
    """The events of one job attempt, in order, for the current task page.

    The service numbers the events of all jobs in one sequence (memorive/platform/jobs/durable_store.py),
    so one job's events have increasing numbers with other jobs' events in between, and a page the
    service returns for a job holds all of that job's events after the cursor. Each event not seen
    before is accepted in number order. This used to wait for cursor + 1 and buffer the rest: every job
    but the first of a profile then showed no timeline, and a job with more than 256 events made the
    task list fail with BUFFER_OVERFLOW, and with it the window's start (it reads the task list).
    """

    def __init__(self, job_id: str, attempt_id: str, *, after_sequence: int = 0):
        self.job_id = require_identifier(job_id, "job_id")
        self.attempt_id = require_identifier(attempt_id, "attempt_id")
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise EventIntegrityError("EVENT_CURSOR_INVALID")
        self.cursor = after_sequence
        self._fingerprints: dict[int, str] = {}
        self._accepted: list[dict[str, Any]] = []
        self._terminal_state: str | None = None

    def _validate_event(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise EventIntegrityError("EVENT_SHAPE_INVALID")
        event = deepcopy(dict(raw))
        sequence = event.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            raise EventIntegrityError("EVENT_SEQUENCE_INVALID")
        if event.get("job_id") != self.job_id:
            raise EventIntegrityError("CROSS_JOB_EVENT")
        if event.get("attempt_id") != self.attempt_id:
            raise EventIntegrityError("CROSS_ATTEMPT_EVENT")
        state = event.get("control_state")
        if state is not None and state not in KNOWN_CONTROL_STATES:
            raise EventIntegrityError("UNKNOWN_STATE")
        event_type = str(event.get("event_type", ""))
        implied = {"JOB_SUCCEEDED": "SUCCEEDED", "JOB_FAILED": "FAILED", "JOB_CANCELLED": "CANCELLED"}.get(event_type)
        if implied is not None and state != implied:
            raise EventIntegrityError("CONTRADICTORY_STATE")
        return event

    def ingest(self, events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        source = list(events)
        sequences = [row.get("sequence") if isinstance(row, Mapping) else None for row in source]
        duplicate_count = 0
        stale_count = 0
        fresh: list[dict[str, Any]] = []
        for raw in source:
            event = self._validate_event(raw)
            sequence = event["sequence"]
            fingerprint = canonical_sha256(event)
            prior = self._fingerprints.get(sequence)
            if prior is not None:
                if prior != fingerprint:
                    raise EventIntegrityError("SAME_SEQUENCE_CONFLICT")
                duplicate_count += 1
                continue
            self._fingerprints[sequence] = fingerprint
            if sequence <= self.cursor:
                stale_count += 1
                continue
            fresh.append(event)
        accepted_now: list[dict[str, Any]] = []
        for event in sorted(fresh, key=lambda row: row["sequence"]):
            state = event.get("control_state")
            if self._terminal_state is not None and state not in {None, self._terminal_state}:
                raise EventIntegrityError("TERMINAL_STATE_ROLLBACK")
            if state in TERMINAL_CONTROL_STATES:
                self._terminal_state = state
            self.cursor = event["sequence"]
            self._accepted.append(event)
            accepted_now.append(event)
        return {
            "schema_version": "CurrentTaskEventReconciliationReceipt-v1",
            "job_id": self.job_id,
            "attempt_id": self.attempt_id,
            "input_count": len(source),
            "accepted_count": len(accepted_now),
            "duplicate_count": duplicate_count,
            "stale_count": stale_count,
            "out_of_order_input": sequences != sorted(sequences) if all(isinstance(row, int) for row in sequences) else False,
            "cursor": self.cursor,
            "terminal_state": self._terminal_state,
            "events": deepcopy(accepted_now),
        }

    def timeline(self) -> list[dict[str, Any]]:
        return deepcopy(self._accepted)


class WatchRecoveryController:
    def __init__(self, reconciler: TimelineReconciler):
        self.reconciler = reconciler
        self.connection_state = "CONNECTED"
        self.disconnect_reason: str | None = None
        self.recovery_epoch = 0

    def disconnect(self, reason: str) -> dict[str, Any]:
        self.connection_state = "DISCONNECTED"
        self.disconnect_reason = require_identifier(reason, "disconnect_reason")
        return self.snapshot("WATCH_DISCONNECT")

    def begin_reconnect(self) -> dict[str, Any]:
        if self.connection_state != "DISCONNECTED":
            raise EventIntegrityError("WATCH_RECONNECT_PRECONDITION")
        self.connection_state = "RECONNECTING"
        self.recovery_epoch += 1
        return self.snapshot("WATCH_RECONNECTING")

    def reconnect(self, events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        if self.connection_state not in {"DISCONNECTED", "RECONNECTING", "RECOVERING_AFTER_RESTART"}:
            raise EventIntegrityError("WATCH_RECONNECT_PRECONDITION")
        # The events after the cursor come as one page from the service: nothing is missing once read.
        receipt = self.reconciler.ingest(events)
        self.connection_state = "CONNECTED"
        self.disconnect_reason = None
        return {**self.snapshot("WATCH_RECONNECT"), "reconciliation": receipt}

    def service_restarted(self) -> dict[str, Any]:
        self.connection_state = "RECOVERING_AFTER_RESTART"
        self.recovery_epoch += 1
        return self.snapshot("SERVICE_RESTART")

    def snapshot(self, event: str = "SNAPSHOT") -> dict[str, Any]:
        return {
            "schema_version": "CurrentTaskWatchRecoveryReceipt-v1",
            "event": event,
            "connection_state": self.connection_state,
            "disconnect_reason": self.disconnect_reason,
            "recovery_epoch": self.recovery_epoch,
            "event_cursor": self.reconciler.cursor,
            "job_id": self.reconciler.job_id,
            "attempt_id": self.reconciler.attempt_id,
        }
