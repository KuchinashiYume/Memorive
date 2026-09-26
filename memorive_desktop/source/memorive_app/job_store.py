from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any, Callable
import uuid

from run_ledger.errors import ConcurrentWriterError
from run_ledger.writer_lock import KernelWriterLock

from .contracts import canonical_json_bytes, immutable_copy, utc_now
from .errors import ConcurrentWriter, CorruptEventLog, IdempotencyConflict, JobNotFound, VersionConflict


Mutator = Callable[[dict[str, Any]], dict[str, Any]]
ResultFactory = Callable[[dict[str, Any]], dict[str, Any]]


class SandboxJobStore:
    """Sandbox-only state plus append-only events, guarded by the RUNTIME_LOG writer lease."""

    def __init__(self, root: Path | str, *, owner_id: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.owner_id = owner_id
        self.state_path = self.root / "job_state.json"
        self.event_path = self.root / "job_events.jsonl"

    def _read_state(self) -> dict[str, Any]:
        try:
            raw = self.state_path.read_bytes()
        except FileNotFoundError:
            return {"schema_version": "SandboxJobStore-v1", "event_sequence": 0, "jobs": {}, "idempotency": {}}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CorruptEventLog("job state is not valid UTF-8 JSON") from exc
        if not isinstance(value, dict) or not isinstance(value.get("jobs"), dict) or not isinstance(value.get("idempotency"), dict):
            raise CorruptEventLog("job state shape invalid")
        return value

    def _write_state(self, state: dict[str, Any]) -> None:
        encoded = canonical_json_bytes(state) + b"\n"
        temporary = self.root / f".job_state.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.state_path)

    def _append_event(self, state: dict[str, Any], event_type: str, job: dict[str, Any]) -> None:
        state["event_sequence"] = int(state.get("event_sequence", 0)) + 1
        event = {
            "schema_version": "JobEvent-v1",
            "sequence": state["event_sequence"],
            "event_type": event_type,
            "job_id": job["job_id"],
            "attempt_id": job.get("attempt_id"),
            "version": job["version"],
            "control_state": job["control_state"],
            "recorded_at": job.get("updated_at", utc_now()),
        }
        with self.event_path.open("ab") as stream:
            stream.write(canonical_json_bytes(event) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _with_writer(self, callback: Callable[[dict[str, Any]], Any]) -> Any:
        try:
            with KernelWriterLock(self.root, owner_id=self.owner_id):
                state = self._read_state()
                result = callback(state)
                self._write_state(state)
                return result
        except ConcurrentWriterError as exc:
            raise ConcurrentWriter(str(exc)) from exc

    def create_job_idempotent(
        self,
        job: dict[str, Any],
        *,
        namespace: str,
        idempotency_key: str,
        payload_sha256: str,
        result: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        def callback(state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
            ledger_key = f"{namespace}:{idempotency_key}"
            existing = state["idempotency"].get(ledger_key)
            if existing is not None:
                if existing["payload_sha256"] != payload_sha256:
                    raise IdempotencyConflict(ledger_key)
                return immutable_copy(existing["result"]), True
            if job["job_id"] in state["jobs"]:
                raise IdempotencyConflict(job["job_id"])
            state["jobs"][job["job_id"]] = immutable_copy(job)
            state["idempotency"][ledger_key] = {"payload_sha256": payload_sha256, "result": immutable_copy(result)}
            self._append_event(state, "JOB_CREATED", job)
            return immutable_copy(result), False

        return self._with_writer(callback)

    def create_job(self, job: dict[str, Any]) -> dict[str, Any]:
        def callback(state: dict[str, Any]) -> dict[str, Any]:
            if job["job_id"] in state["jobs"]:
                raise IdempotencyConflict(job["job_id"])
            state["jobs"][job["job_id"]] = immutable_copy(job)
            self._append_event(state, "JOB_CREATED", job)
            return immutable_copy(job)

        return self._with_writer(callback)

    def read_job(self, job_id: str) -> dict[str, Any]:
        job = self._read_state()["jobs"].get(job_id)
        if job is None:
            raise JobNotFound(job_id)
        return immutable_copy(job)

    def list_jobs(self) -> list[dict[str, Any]]:
        return [immutable_copy(value) for _, value in sorted(self._read_state()["jobs"].items())]

    def lookup_idempotency(self, namespace: str, idempotency_key: str) -> dict[str, Any] | None:
        row = self._read_state()["idempotency"].get(f"{namespace}:{idempotency_key}")
        return None if row is None else immutable_copy(row["result"])

    def read_events(self, *, job_id: str | None = None, after_sequence: int = 0) -> list[dict[str, Any]]:
        try:
            raw = self.event_path.read_bytes()
        except FileNotFoundError:
            return []
        if raw and not raw.endswith(b"\n"):
            raise CorruptEventLog("event log has truncated tail")
        events: list[dict[str, Any]] = []
        for index, line in enumerate(raw.splitlines(), 1):
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CorruptEventLog(f"event {index} invalid") from exc
            if int(event.get("sequence", -1)) <= after_sequence:
                continue
            if job_id is not None and event.get("job_id") != job_id:
                continue
            events.append(immutable_copy(event))
        return events

    def update_job(self, job_id: str, *, expected_version: int, event_type: str, mutator: Mutator) -> dict[str, Any]:
        def callback(state: dict[str, Any]) -> dict[str, Any]:
            current = state["jobs"].get(job_id)
            if current is None:
                raise JobNotFound(job_id)
            if current.get("version") != expected_version:
                raise VersionConflict(f"expected {expected_version}; observed {current.get('version')}")
            accepted = immutable_copy(mutator(deepcopy(current)))
            accepted["version"] = expected_version + 1
            accepted["updated_at"] = utc_now()
            state["jobs"][job_id] = accepted
            self._append_event(state, event_type, accepted)
            return immutable_copy(accepted)

        return self._with_writer(callback)

    def update_job_idempotent(
        self,
        job_id: str,
        *,
        expected_version: int,
        namespace: str,
        idempotency_key: str,
        payload_sha256: str,
        event_type: str,
        mutator: Mutator,
        result_factory: ResultFactory,
    ) -> tuple[dict[str, Any], bool]:
        def callback(state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
            ledger_key = f"{namespace}:{idempotency_key}"
            existing = state["idempotency"].get(ledger_key)
            if existing is not None:
                if existing["payload_sha256"] != payload_sha256:
                    raise IdempotencyConflict(ledger_key)
                return immutable_copy(existing["result"]), True
            current = state["jobs"].get(job_id)
            if current is None:
                raise JobNotFound(job_id)
            if current.get("version") != expected_version:
                raise VersionConflict(f"expected {expected_version}; observed {current.get('version')}")
            accepted = immutable_copy(mutator(deepcopy(current)))
            accepted["version"] = expected_version + 1
            accepted["updated_at"] = utc_now()
            state["jobs"][job_id] = accepted
            self._append_event(state, event_type, accepted)
            result = immutable_copy(result_factory(accepted))
            state["idempotency"][ledger_key] = {"payload_sha256": payload_sha256, "result": result}
            return result, False

        return self._with_writer(callback)

    def integrity_report(self) -> dict[str, Any]:
        state = self._read_state()
        try:
            raw = self.event_path.read_bytes()
        except FileNotFoundError:
            raw = b""
        if raw and not raw.endswith(b"\n"):
            raise CorruptEventLog("event log has truncated tail")
        sequences = []
        for index, line in enumerate(raw.splitlines(), 1):
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CorruptEventLog(f"event {index} invalid") from exc
            sequences.append(event.get("sequence"))
        if sequences != list(range(1, len(sequences) + 1)):
            raise CorruptEventLog("event sequence is not contiguous")
        if int(state.get("event_sequence", 0)) != len(sequences):
            raise CorruptEventLog("state/event sequence mismatch")
        return {"status": "PASS", "event_count": len(sequences), "job_count": len(state["jobs"])}


__all__ = ["SandboxJobStore"]
