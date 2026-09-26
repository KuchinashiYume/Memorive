from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .contracts import ReviewOrchestrationContractError, canonical_hash, canonical_json_bytes
from .projection import GENESIS, CorruptReviewEventError, project_review_job


class StaleReviewHeadError(ReviewOrchestrationContractError):
    pass


class ReviewEventDedupeConflict(ReviewOrchestrationContractError):
    pass


class AppendOnlyReviewEventStore:
    def __init__(self, path: str | Path | None = None, *, sandbox_root: str | Path | None = None):
        self._lock = threading.RLock()
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._dedupe: dict[tuple[str, str], dict[str, Any]] = {}
        self._path: Path | None = None
        if path is not None:
            if sandbox_root is None:
                raise ReviewOrchestrationContractError("SANDBOX_ROOT_REQUIRED")
            root = Path(sandbox_root).resolve()
            target = Path(path).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ReviewOrchestrationContractError("EVENT_STORE_OUTSIDE_SANDBOX") from exc
            root.mkdir(parents=True, exist_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._path = target
            if target.exists():
                self._load_file()

    def _load_file(self) -> None:
        assert self._path is not None
        raw = self._path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise CorruptReviewEventError("CORRUPT_EVENT_TAIL")
        for line_number, line in enumerate(raw.splitlines(), start=1):
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CorruptReviewEventError(f"EVENT_JSON_CORRUPT:{line_number}") from exc
            job_id = event.get("job_id")
            dedupe_key = event.get("dedupe_key")
            if not isinstance(job_id, str) or not isinstance(dedupe_key, str):
                raise CorruptReviewEventError("EVENT_IDENTITY_MISSING")
            if (job_id, dedupe_key) in self._dedupe:
                raise CorruptReviewEventError("DUPLICATE_PHYSICAL_EVENT")
            self._events.setdefault(job_id, []).append(event)
            self._dedupe[(job_id, dedupe_key)] = event
        for events in self._events.values():
            project_review_job(events)

    def head(self, job_id: str) -> str:
        with self._lock:
            events = self._events.get(job_id, [])
            return events[-1]["event_hash"] if events else GENESIS

    def events(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._events.get(job_id, []))

    def event_by_dedupe(self, job_id: str, dedupe_key: str) -> dict[str, Any] | None:
        with self._lock:
            event = self._dedupe.get((job_id, dedupe_key))
            return deepcopy(event) if event is not None else None

    def projection(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            return project_review_job(self._events.get(job_id, []))

    def append(
        self,
        *,
        job_id: str,
        event_type: str,
        expected_previous_head_hash: str,
        actor_kind: str,
        actor_ref: str,
        occurred_at: str,
        payload: Mapping[str, Any],
        dedupe_key: str,
        reason_code: str,
        evidence_refs: list[str] | None = None,
        attempt_id: str | None = None,
        predecessor_attempt_ref: str | None = None,
        transition_guard: str | None = None,
        review_verdict: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        request = {
            "job_id": job_id,
            "event_type": event_type,
            "actor_kind": actor_kind,
            "actor_ref": actor_ref,
            "occurred_at": occurred_at,
            "payload_hash": canonical_hash(dict(payload)),
            "dedupe_key": dedupe_key,
            "reason_code": reason_code,
            "evidence_refs": evidence_refs or [],
            "attempt_id": attempt_id,
            "predecessor_attempt_ref": predecessor_attempt_ref,
            "transition_guard": transition_guard,
            "review_verdict": review_verdict,
        }
        fingerprint = canonical_hash(request)
        with self._lock:
            identity = (job_id, dedupe_key)
            if identity in self._dedupe:
                existing = self._dedupe[identity]
                if existing["request_fingerprint"] == fingerprint:
                    return deepcopy(existing), True
                raise ReviewEventDedupeConflict("DEDUPE_KEY_REUSED_WITH_DIFFERENT_EVENT")
            if expected_previous_head_hash != self.head(job_id):
                raise StaleReviewHeadError("EXPECTED_PREVIOUS_HEAD_MISMATCH")
            body = {
                "schema_version": "ReviewOrchestrationReviewJobEvent-v1",
                "event_id": "RJE-" + canonical_hash({"job_id": job_id, "dedupe_key": dedupe_key})[:32],
                "job_id": job_id,
                "event_ordinal": len(self._events.get(job_id, [])) + 1,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "actor_kind": actor_kind,
                "actor_ref": actor_ref,
                "expected_previous_head_hash": expected_previous_head_hash,
                "attempt_id": attempt_id,
                "predecessor_attempt_ref": predecessor_attempt_ref,
                "reason_code": reason_code,
                "evidence_refs": evidence_refs or [],
                "payload_hash": request["payload_hash"],
                "dedupe_key": dedupe_key,
                "request_fingerprint": fingerprint,
                "transition_guard": transition_guard,
                "review_verdict": review_verdict,
            }
            event = {**body, "event_hash": canonical_hash(body)}
            candidate = [*self._events.get(job_id, []), event]
            project_review_job(candidate)
            if self._path is not None:
                with self._path.open("ab") as handle:
                    handle.write(canonical_json_bytes(event) + b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            self._events.setdefault(job_id, []).append(event)
            self._dedupe[identity] = event
            return deepcopy(event), False


__all__ = [
    "AppendOnlyReviewEventStore",
    "ReviewEventDedupeConflict",
    "StaleReviewHeadError",
]
