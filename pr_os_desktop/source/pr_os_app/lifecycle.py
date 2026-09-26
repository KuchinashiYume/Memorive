from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from m11_terminal_ledger.writer_lock import (
    host_id_hash,
    ledger_prefix_sha256,
    parse_lease,
    process_identity_status,
    read_lease_bytes,
)

from .contracts import JOB_CONTROL_STATES
from .errors import InvalidTransition


LEGAL_TRANSITIONS = {
    "CREATED": frozenset({"PREFLIGHT"}),
    "PREFLIGHT": frozenset({"QUEUED", "BLOCKED_BEFORE_START"}),
    "QUEUED": frozenset({"RUNNING", "CANCEL_REQUESTED"}),
    "RUNNING": frozenset({"PAUSE_REQUESTED", "CANCEL_REQUESTED", "RECOVERY_REQUIRED", "SUCCEEDED", "FAILED"}),
    "PAUSE_REQUESTED": frozenset({"PAUSED", "CANCEL_REQUESTED", "FAILED"}),
    "PAUSED": frozenset({"RESUME_REQUESTED", "CANCEL_REQUESTED"}),
    "RESUME_REQUESTED": frozenset({"RUNNING", "RECOVERY_REQUIRED", "FAILED"}),
    "CANCEL_REQUESTED": frozenset({"CANCELLED", "FAILED"}),
    "RECOVERY_REQUIRED": frozenset({"RECOVERING", "FAILED"}),
    "RECOVERING": frozenset({"RUNNING", "PAUSED", "FAILED"}),
    "SUCCEEDED": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
    "BLOCKED_BEFORE_START": frozenset(),
}


def apply_transition(current: str, target: str) -> str:
    if current not in JOB_CONTROL_STATES or target not in JOB_CONTROL_STATES:
        raise InvalidTransition(f"unknown job state: {current}->{target}")
    if target not in LEGAL_TRANSITIONS[current]:
        raise InvalidTransition(f"illegal job transition: {current}->{target}")
    return target


def _event_integrity_status(root: Path) -> str:
    state_path = root / "job_state.json"
    event_path = root / "job_events.jsonl"
    if not state_path.exists() and not event_path.exists():
        return "PASS"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        raw = event_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            return "CORRUPT_EVENT_LOG"
        sequences = [json.loads(line.decode("utf-8")).get("sequence") for line in raw.splitlines()]
        if sequences != list(range(1, len(sequences) + 1)):
            return "CORRUPT_EVENT_LOG"
        if int(state.get("event_sequence", -1)) != len(sequences):
            return "CORRUPT_EVENT_LOG"
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return "CORRUPT_EVENT_LOG"
    return "PASS"


def recovery_classification(root: Path, *, now: datetime | None = None) -> dict[str, object]:
    root = Path(root)
    event_integrity = _event_integrity_status(root)
    if event_integrity != "PASS":
        return {"classification": event_integrity, "lease_present": (root / ".writer.lease.json").exists()}
    raw = read_lease_bytes(root)
    if raw is None:
        return {"classification": "NO_RECOVERY_REQUIRED", "lease_present": False}
    try:
        lease = parse_lease(raw)
    except ValueError:
        return {"classification": "BLOCKED_METADATA_DRIFT", "lease_present": True}
    if lease.state in {"RELEASED", "RECOVERED"}:
        return {"classification": "NO_RECOVERY_REQUIRED", "lease_present": True, "lease_state": lease.state}
    if lease.host_id_hash != host_id_hash():
        return {"classification": "BLOCKED_IDENTITY_UNKNOWN", "lease_present": True, "lease_state": lease.state}
    identity = process_identity_status(lease.pid, lease.process_start_marker)
    if identity == "SAME_PROCESS_ALIVE":
        return {"classification": "ACTIVE_WRITER_PRESENT", "lease_present": True, "identity": identity}
    if identity == "UNKNOWN":
        return {"classification": "BLOCKED_IDENTITY_UNKNOWN", "lease_present": True, "identity": identity}
    accepted_now = now or datetime.now(timezone.utc)
    heartbeat = datetime.fromisoformat(lease.heartbeat_at.replace("Z", "+00:00"))
    if accepted_now <= heartbeat + timedelta(seconds=lease.lease_seconds):
        return {"classification": "BLOCKED_LEASE_NOT_EXPIRED", "lease_present": True, "identity": identity}
    if lease.ledger_prefix_sha256 != ledger_prefix_sha256(root):
        return {"classification": "BLOCKED_METADATA_DRIFT", "lease_present": True, "identity": identity}
    return {"classification": "RECOVERABLE_STALE", "lease_present": True, "identity": identity}


__all__ = ["LEGAL_TRANSITIONS", "apply_transition", "recovery_classification"]
