from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable

from .contracts import canonical_json_bytes, canonical_sha256, immutable_copy, utc_now
from .errors import CorruptEventLog, IdempotencyConflict, JobNotFound, VersionConflict


Mutator = Callable[[dict[str, Any]], dict[str, Any]]
ResultFactory = Callable[[dict[str, Any]], dict[str, Any]]
_SENSITIVE_FIELD = re.compile(r"(^|_)(api[_-]?key|password|secret|token|credential[_-]?value)($|_)", re.IGNORECASE)


def _require_secret_free(value: Any, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            rendered = str(key)
            if _SENSITIVE_FIELD.search(rendered):
                raise ValueError(f"SENSITIVE_OUTBOX_FIELD_FORBIDDEN:{path}.{rendered}")
            _require_secret_free(child, f"{path}.{rendered}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _require_secret_free(child, f"{path}[{index}]")


class DurableJobStore:
    """SQLite-backed product job, event, idempotency, and outbox store.

    Every mutation uses ``BEGIN IMMEDIATE`` and ``synchronous=FULL``.  Values
    crossing the storage boundary are canonical JSON; credentials are never a
    supported column or payload class.
    """

    schema_version = "DurableJobStore-v1"

    def __init__(self, database_path: Path | str, *, owner_id: str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.owner_id = str(owner_id)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL UNIQUE,
                    version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idempotency (
                    namespace TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY (namespace, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    effect_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    effect_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    UNIQUE (job_id, effect_type, idempotency_key),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', ?)",
                (self.schema_version,),
            )
            connection.execute("INSERT OR IGNORE INTO metadata(key, value) VALUES('event_sequence', '0')")
            connection.commit()

    @staticmethod
    def _encode(value: Any) -> str:
        return canonical_json_bytes(value).decode("utf-8")

    @staticmethod
    def _decode(value: str) -> Any:
        return json.loads(value)

    def _transaction(self, callback: Callable[[sqlite3.Connection], Any]) -> Any:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = callback(connection)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _append_event(self, connection: sqlite3.Connection, event_type: str, job: dict[str, Any]) -> dict[str, Any]:
        row = connection.execute("SELECT value FROM metadata WHERE key='event_sequence'").fetchone()
        sequence = int(row["value"]) + 1
        connection.execute("UPDATE metadata SET value=? WHERE key='event_sequence'", (str(sequence),))
        event = {
            "schema_version": "JobEvent-v1",
            "sequence": sequence,
            "event_type": event_type,
            "job_id": job["job_id"],
            "attempt_id": job.get("attempt_id"),
            "version": job["version"],
            "control_state": job["control_state"],
            "recorded_at": job.get("updated_at", utc_now()),
        }
        connection.execute(
            "INSERT INTO events(sequence, job_id, event_json) VALUES(?, ?, ?)",
            (sequence, job["job_id"], self._encode(event)),
        )
        return event

    def create_job_idempotent(
        self,
        job: dict[str, Any],
        *,
        namespace: str,
        idempotency_key: str,
        payload_sha256: str,
        result: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        def callback(connection: sqlite3.Connection) -> tuple[dict[str, Any], bool]:
            existing = connection.execute(
                "SELECT payload_sha256, result_json FROM idempotency WHERE namespace=? AND idempotency_key=?",
                (namespace, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["payload_sha256"] != payload_sha256:
                    raise IdempotencyConflict(f"{namespace}:{idempotency_key}")
                return immutable_copy(self._decode(existing["result_json"])), True
            try:
                connection.execute(
                    "INSERT INTO jobs(job_id, attempt_id, version, payload_json) VALUES(?, ?, ?, ?)",
                    (job["job_id"], job["attempt_id"], job["version"], self._encode(job)),
                )
            except sqlite3.IntegrityError as exc:
                raise IdempotencyConflict(job["job_id"]) from exc
            connection.execute(
                "INSERT INTO idempotency(namespace, idempotency_key, payload_sha256, result_json) VALUES(?, ?, ?, ?)",
                (namespace, idempotency_key, payload_sha256, self._encode(result)),
            )
            self._append_event(connection, "JOB_CREATED", job)
            return immutable_copy(result), False

        return self._transaction(callback)

    def create_job(self, job: dict[str, Any]) -> dict[str, Any]:
        def callback(connection: sqlite3.Connection) -> dict[str, Any]:
            try:
                connection.execute(
                    "INSERT INTO jobs(job_id, attempt_id, version, payload_json) VALUES(?, ?, ?, ?)",
                    (job["job_id"], job["attempt_id"], job["version"], self._encode(job)),
                )
            except sqlite3.IntegrityError as exc:
                raise IdempotencyConflict(job["job_id"]) from exc
            self._append_event(connection, "JOB_CREATED", job)
            return immutable_copy(job)

        return self._transaction(callback)

    def read_job(self, job_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT payload_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return immutable_copy(self._decode(row["payload_json"]))

    def list_jobs(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT payload_json FROM jobs ORDER BY job_id").fetchall()
        return [immutable_copy(self._decode(row["payload_json"])) for row in rows]

    def read_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        # attempt_id already has a UNIQUE index; never decode every other job
        # just to resolve one node's attempt or predecessor.
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload_json FROM jobs WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
        return None if row is None else immutable_copy(self._decode(row["payload_json"]))

    def lookup_idempotency(self, namespace: str, idempotency_key: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT result_json FROM idempotency WHERE namespace=? AND idempotency_key=?",
                (namespace, idempotency_key),
            ).fetchone()
        return None if row is None else immutable_copy(self._decode(row["result_json"]))

    def read_events(self, *, job_id: str | None = None, after_sequence: int = 0) -> list[dict[str, Any]]:
        query = "SELECT event_json FROM events WHERE sequence>?"
        parameters: list[Any] = [after_sequence]
        if job_id is not None:
            query += " AND job_id=?"
            parameters.append(job_id)
        query += " ORDER BY sequence"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [immutable_copy(self._decode(row["event_json"])) for row in rows]

    def update_job(self, job_id: str, *, expected_version: int, event_type: str, mutator: Mutator) -> dict[str, Any]:
        def callback(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute("SELECT version, payload_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFound(job_id)
            if int(row["version"]) != expected_version:
                raise VersionConflict(f"expected {expected_version}; observed {row['version']}")
            current = self._decode(row["payload_json"])
            accepted = immutable_copy(mutator(deepcopy(current)))
            accepted["version"] = expected_version + 1
            accepted["updated_at"] = utc_now()
            connection.execute(
                "UPDATE jobs SET version=?, payload_json=? WHERE job_id=?",
                (accepted["version"], self._encode(accepted), job_id),
            )
            self._append_event(connection, event_type, accepted)
            return immutable_copy(accepted)

        return self._transaction(callback)

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
        def callback(connection: sqlite3.Connection) -> tuple[dict[str, Any], bool]:
            existing = connection.execute(
                "SELECT payload_sha256, result_json FROM idempotency WHERE namespace=? AND idempotency_key=?",
                (namespace, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["payload_sha256"] != payload_sha256:
                    raise IdempotencyConflict(f"{namespace}:{idempotency_key}")
                return immutable_copy(self._decode(existing["result_json"])), True
            row = connection.execute("SELECT version, payload_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFound(job_id)
            if int(row["version"]) != expected_version:
                raise VersionConflict(f"expected {expected_version}; observed {row['version']}")
            accepted = immutable_copy(mutator(deepcopy(self._decode(row["payload_json"]))))
            accepted["version"] = expected_version + 1
            accepted["updated_at"] = utc_now()
            connection.execute(
                "UPDATE jobs SET version=?, payload_json=? WHERE job_id=?",
                (accepted["version"], self._encode(accepted), job_id),
            )
            self._append_event(connection, event_type, accepted)
            result = immutable_copy(result_factory(accepted))
            connection.execute(
                "INSERT INTO idempotency(namespace, idempotency_key, payload_sha256, result_json) VALUES(?, ?, ?, ?)",
                (namespace, idempotency_key, payload_sha256, self._encode(result)),
            )
            return result, False

        return self._transaction(callback)

    def enqueue_external_effect(
        self,
        job_id: str,
        *,
        effect_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload_copy = immutable_copy(payload)
        _require_secret_free(payload_copy)
        effect_id = "effect_" + canonical_sha256(
            {"job_id": job_id, "effect_type": effect_type, "idempotency_key": idempotency_key, "payload": payload_copy}
        )[:24].lower()

        def callback(connection: sqlite3.Connection) -> dict[str, Any]:
            if connection.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone() is None:
                raise JobNotFound(job_id)
            existing = connection.execute(
                "SELECT record_json, payload_json FROM outbox WHERE job_id=? AND effect_type=? AND idempotency_key=?",
                (job_id, effect_type, idempotency_key),
            ).fetchone()
            if existing is not None:
                if self._decode(existing["payload_json"]) != payload_copy:
                    raise IdempotencyConflict(f"outbox:{job_id}:{effect_type}:{idempotency_key}")
                return immutable_copy(self._decode(existing["record_json"]))
            now = utc_now()
            record = {
                "schema_version": "ExternalEffectOutboxRecord-v1",
                "effect_id": effect_id,
                "job_id": job_id,
                "effect_type": effect_type,
                "idempotency_key": idempotency_key,
                "version": 1,
                "state": "PENDING",
                "payload": payload_copy,
                "worker_id": None,
                "receipt": None,
                "created_at": now,
                "updated_at": now,
            }
            connection.execute(
                "INSERT INTO outbox(effect_id, job_id, effect_type, idempotency_key, version, state, payload_json, record_json) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (effect_id, job_id, effect_type, idempotency_key, 1, "PENDING", self._encode(payload_copy), self._encode(record)),
            )
            return immutable_copy(record)

        return self._transaction(callback)

    def _update_external_effect(
        self,
        effect_id: str,
        *,
        expected_version: int,
        allowed_state: str,
        mutator: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        def callback(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute("SELECT version, state, record_json FROM outbox WHERE effect_id=?", (effect_id,)).fetchone()
            if row is None:
                raise JobNotFound(effect_id)
            if int(row["version"]) != expected_version:
                raise VersionConflict(f"expected {expected_version}; observed {row['version']}")
            if row["state"] != allowed_state:
                raise VersionConflict(f"expected state {allowed_state}; observed {row['state']}")
            record = immutable_copy(self._decode(row["record_json"]))
            mutator(record)
            record["version"] = expected_version + 1
            record["updated_at"] = utc_now()
            connection.execute(
                "UPDATE outbox SET version=?, state=?, record_json=? WHERE effect_id=?",
                (record["version"], record["state"], self._encode(record), effect_id),
            )
            return immutable_copy(record)

        return self._transaction(callback)

    def claim_external_effect(self, effect_id: str, *, expected_version: int, worker_id: str) -> dict[str, Any]:
        return self._update_external_effect(
            effect_id,
            expected_version=expected_version,
            allowed_state="PENDING",
            mutator=lambda record: record.update({"state": "IN_FLIGHT", "worker_id": str(worker_id)}),
        )

    def complete_external_effect(
        self,
        effect_id: str,
        *,
        expected_version: int,
        succeeded: bool,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        _require_secret_free(receipt, "receipt")
        return self._update_external_effect(
            effect_id,
            expected_version=expected_version,
            allowed_state="IN_FLIGHT",
            mutator=lambda record: record.update(
                {"state": "SUCCEEDED" if succeeded else "FAILED", "receipt": immutable_copy(receipt)}
            ),
        )

    def list_external_effects(self, job_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT record_json FROM outbox"
        parameters: tuple[Any, ...] = ()
        if job_id is not None:
            query += " WHERE job_id=?"
            parameters = (job_id,)
        query += " ORDER BY effect_id"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [immutable_copy(self._decode(row["record_json"])) for row in rows]

    def integrity_report(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            event_rows = connection.execute("SELECT sequence, event_json FROM events ORDER BY sequence").fetchall()
            job_rows = connection.execute("SELECT job_id, version, payload_json FROM jobs ORDER BY job_id").fetchall()
            sequence_row = connection.execute("SELECT value FROM metadata WHERE key='event_sequence'").fetchone()
        if integrity != "ok":
            raise CorruptEventLog(f"sqlite integrity check failed: {integrity}")
        sequences = [int(row["sequence"]) for row in event_rows]
        if sequences != list(range(1, len(sequences) + 1)):
            raise CorruptEventLog("event sequence is not contiguous")
        if int(sequence_row["value"]) != len(sequences):
            raise CorruptEventLog("metadata/event sequence mismatch")
        for row in event_rows:
            self._decode(row["event_json"])
        for row in job_rows:
            payload = self._decode(row["payload_json"])
            if payload.get("job_id") != row["job_id"] or payload.get("version") != row["version"]:
                raise CorruptEventLog("job row projection mismatch")
        return {
            "status": "PASS",
            "schema_version": self.schema_version,
            "journal_mode": "WAL",
            "synchronous": "FULL",
            "event_count": len(event_rows),
            "job_count": len(job_rows),
            "outbox_count": len(self.list_external_effects()),
        }


__all__ = ["DurableJobStore"]
