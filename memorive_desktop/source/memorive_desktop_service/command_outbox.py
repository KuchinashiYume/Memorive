from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping

from .errors import SensitivePersistenceRejected
from .protocol import canonical_json_bytes


_SENSITIVE = re.compile(r"(^|_)(api[_-]?key|authorization|cookie|credential|password|secret|token)($|_)", re.IGNORECASE)


def _scan(value: Any, path: str = "params") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _SENSITIVE.search(str(key)):
                raise SensitivePersistenceRejected(f"OUTBOX_SENSITIVE_FIELD:{path}.{key}")
            _scan(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan(child, f"{path}[{index}]")


class DesktopCommandOutbox:
    """Durable command metadata/receipt ledger; request payloads are never stored."""

    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS commands (
                    idempotency_key TEXT PRIMARY KEY,
                    method TEXT NOT NULL,
                    params_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    response_sha256 TEXT,
                    response_json TEXT
                )
                """
            )
            connection.commit()

    def enqueue(self, *, idempotency_key: str, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", idempotency_key):
            raise ValueError("OUTBOX_IDEMPOTENCY_KEY_INVALID")
        if not isinstance(method, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", method):
            raise ValueError("OUTBOX_METHOD_INVALID")
        _scan(params)
        params_sha256 = hashlib.sha256(canonical_json_bytes(dict(params))).hexdigest().upper()
        with closing(sqlite3.connect(self.database_path, timeout=10.0)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM commands WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                if existing["method"] != method or existing["params_sha256"] != params_sha256:
                    raise ValueError("OUTBOX_IDEMPOTENCY_CONFLICT")
                connection.commit()
                return {
                    "schema_version": "DesktopCommandOutboxReceipt-v1",
                    "idempotency_key": idempotency_key,
                    "method": method,
                    "params_sha256": params_sha256,
                    "state": existing["state"],
                    "replayed": True,
                    "response_sha256": existing["response_sha256"],
                }
            connection.execute(
                "INSERT INTO commands(idempotency_key, method, params_sha256, state) VALUES(?, ?, ?, 'PENDING')",
                (idempotency_key, method, params_sha256),
            )
            connection.commit()
        return {
            "schema_version": "DesktopCommandOutboxReceipt-v1",
            "idempotency_key": idempotency_key,
            "method": method,
            "params_sha256": params_sha256,
            "state": "PENDING",
            "replayed": False,
            "response_sha256": None,
        }

    def complete(self, idempotency_key: str, response: Mapping[str, Any], *, succeeded: bool) -> dict[str, Any]:
        _scan(response, "response")
        encoded = canonical_json_bytes(dict(response))
        response_sha256 = hashlib.sha256(encoded).hexdigest().upper()
        state = "ACKNOWLEDGED" if succeeded else "FAILED"
        with closing(sqlite3.connect(self.database_path, timeout=10.0)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, response_sha256 FROM commands WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if row is None:
                raise KeyError("OUTBOX_COMMAND_NOT_FOUND")
            if row["state"] != "PENDING":
                if row["state"] != state or row["response_sha256"] != response_sha256:
                    raise ValueError("OUTBOX_COMPLETION_CONFLICT")
                connection.commit()
                replayed = True
            else:
                connection.execute(
                    "UPDATE commands SET state=?, response_sha256=?, response_json=? WHERE idempotency_key=?",
                    (state, response_sha256, encoded.decode("utf-8"), idempotency_key),
                )
                connection.commit()
                replayed = False
        return {
            "schema_version": "DesktopCommandCompletionReceipt-v1",
            "idempotency_key": idempotency_key,
            "state": state,
            "response_sha256": response_sha256,
            "replayed": replayed,
        }

    def lookup(self, idempotency_key: str) -> dict[str, Any] | None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM commands WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if row is None:
            return None
        return {
            "schema_version": "DesktopCommandOutboxRecord-v1",
            "idempotency_key": row["idempotency_key"],
            "method": row["method"],
            "params_sha256": row["params_sha256"],
            "state": row["state"],
            "response_sha256": row["response_sha256"],
            "response": None if row["response_json"] is None else json.loads(row["response_json"]),
        }


__all__ = ["DesktopCommandOutbox"]
