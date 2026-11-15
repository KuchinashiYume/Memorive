from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from .contracts import canonical_hash, validate_review_job_event


GENESIS = "GENESIS"
TERMINAL_STATES = {"COMPLETED", "FAILED", "ABORTED"}


class ReviewProjectionError(RuntimeError):
    pass


class ReviewTransitionError(ReviewProjectionError):
    pass


class CorruptReviewEventError(ReviewProjectionError):
    pass


def _transition(state: str | None, event: Mapping[str, Any]) -> str:
    event_type = event["event_type"]
    guard = event.get("transition_guard")
    if state is None:
        if event_type != "JOB_CREATED":
            raise ReviewTransitionError("FIRST_EVENT_MUST_BE_JOB_CREATED")
        return "PENDING"
    if state in TERMINAL_STATES:
        raise ReviewTransitionError("TERMINAL_STATE_TRANSITION_FORBIDDEN")
    no_change = {
        ("RUNNING", "PAUSE_REQUESTED"),
        ("RUNNING", "CANCEL_REQUESTED"),
    }
    if (state, event_type) in no_change:
        return state
    transitions = {
        ("PENDING", "RUN_CLAIMED"): "RUNNING",
        ("PENDING", "RUN_PAUSED"): "PAUSED",
        ("PENDING", "RUN_BLOCKED"): "BLOCKED",
        ("PENDING", "RUN_ABORTED"): "ABORTED",
        ("RUNNING", "RUN_PAUSED"): "PAUSED",
        ("RUNNING", "RUN_BLOCKED"): "BLOCKED",
        ("RUNNING", "RUN_COMPLETED"): "COMPLETED",
        ("RUNNING", "RUN_FAILED"): "FAILED",
        ("RUNNING", "RUN_ABORTED"): "ABORTED",
        ("PAUSED", "RUN_RESUMED"): "PENDING",
        ("PAUSED", "RUN_ABORTED"): "ABORTED",
        ("BLOCKED", "RUN_RESUMED"): "PENDING",
        ("BLOCKED", "RUN_ABORTED"): "ABORTED",
    }
    if (state, event_type) == ("RUNNING", "RUN_REQUEUED"):
        if guard != "PROVED_NOT_SENT_AND_ATTEMPT_BUDGET_REMAINS":
            raise ReviewTransitionError("RUN_REQUEUE_GUARD_INVALID")
        return "PENDING"
    try:
        return transitions[(state, event_type)]
    except KeyError as exc:
        raise ReviewTransitionError(f"TRANSITION_FORBIDDEN:{state}->{event_type}") from exc


def project_review_job(events: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    values = [validate_review_job_event(item) for item in events]
    if not values:
        return None
    state: str | None = None
    previous = GENESIS
    job_id = values[0]["job_id"]
    attempts: list[str] = []
    review_verdict: str | None = None
    dedupe_keys: set[str] = set()
    for ordinal, event in enumerate(values, start=1):
        if event["job_id"] != job_id:
            raise CorruptReviewEventError("MIXED_JOB_IDS")
        if event["event_ordinal"] != ordinal:
            raise CorruptReviewEventError("EVENT_ORDINAL_GAP")
        if event["expected_previous_head_hash"] != previous:
            raise CorruptReviewEventError("EVENT_HASH_CHAIN_BROKEN")
        if event["dedupe_key"] in dedupe_keys:
            raise CorruptReviewEventError("DUPLICATE_PHYSICAL_EVENT")
        dedupe_keys.add(event["dedupe_key"])
        state = _transition(state, event)
        if event["event_type"] == "RUN_CLAIMED":
            attempt_id = event.get("attempt_id")
            if not attempt_id or attempt_id in attempts:
                raise CorruptReviewEventError("ATTEMPT_ID_DUPLICATE_OR_MISSING")
            attempts.append(attempt_id)
        if event["event_type"] == "RUN_COMPLETED":
            review_verdict = event.get("review_verdict")
        previous = event["event_hash"]
    body = {
        "schema_version": "ReviewOrchestrationReviewJobProjection-v1",
        "job_id": job_id,
        "job_state": state,
        "head_event_hash": previous,
        "last_event_ordinal": len(values),
        "attempt_count": len(attempts),
        "attempt_ids": attempts,
        "review_verdict": review_verdict,
        "terminal": state in TERMINAL_STATES,
    }
    return {**body, "content_hash": canonical_hash(body)}


def projection_bytes(events: Iterable[Mapping[str, Any]]) -> bytes:
    import json

    projection = project_review_job(events)
    return json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def clone_projection(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return deepcopy(value) if value is not None else None


__all__ = [
    "CorruptReviewEventError",
    "GENESIS",
    "ReviewProjectionError",
    "ReviewTransitionError",
    "TERMINAL_STATES",
    "project_review_job",
    "projection_bytes",
]
