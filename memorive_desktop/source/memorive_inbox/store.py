from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping

from .contracts import InboxError, canonical_bytes, canonical_sha256, utc_now


Mutator = Callable[[dict[str, Any]], None]


class InboxStore:
    schema_version = "InboxDurableStore-v1"

    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)
        self.change_listener = None
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(self._connect()) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS batches (
                        batch_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL UNIQUE,
                        record_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS items (
                        item_id TEXT PRIMARY KEY,
                        batch_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        content_sha256 TEXT,
                        record_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_inbox_content ON items(content_sha256);
                    CREATE TABLE IF NOT EXISTS outbox (
                        intent_id TEXT PRIMARY KEY,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        item_id TEXT NOT NULL,
                        params_sha256 TEXT NOT NULL,
                        state TEXT NOT NULL,
                        record_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS operations (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        operation_id TEXT NOT NULL UNIQUE,
                        state TEXT NOT NULL,
                        record_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL,
                        item_id TEXT,
                        event_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS settings (
                        setting_key TEXT PRIMARY KEY,
                        record_json TEXT NOT NULL
                    );
                    """
                )
                result = connection.execute("PRAGMA quick_check").fetchone()[0]
                if result != "ok":
                    raise InboxError("INBOX_STORE_CORRUPT")
                connection.commit()
        except sqlite3.DatabaseError as exc:
            raise InboxError("INBOX_STORE_CORRUPT") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _encode(value: Mapping[str, Any]) -> str:
        return canonical_bytes(dict(value)).decode("utf-8")

    @staticmethod
    def _decode(value: str) -> dict[str, Any]:
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise InboxError("INBOX_STORE_RECORD_INVALID")
        return decoded

    def _event(self, connection: sqlite3.Connection, event_type: str, item_id: str | None, payload: Mapping[str, Any]) -> None:
        event = {
            "schema_version": "InboxStoreEvent-v1",
            "event_type": event_type,
            "item_id": item_id,
            "payload_sha256": canonical_sha256(payload),
            "created_at": utc_now(),
        }
        connection.execute(
            "INSERT INTO events(event_type, item_id, event_json) VALUES(?, ?, ?)",
            (event_type, item_id, self._encode(event)),
        )

    def create_or_get_batch(self, record: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        accepted = deepcopy(dict(record))
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT record_json FROM batches WHERE request_id=?", (accepted["request_id"],)
            ).fetchone()
            if existing is not None:
                current = self._decode(existing["record_json"])
                if current["batch_id"] != accepted["batch_id"] or current["request_sha256"] != accepted["request_sha256"]:
                    raise InboxError("INBOX_BATCH_IDEMPOTENCY_CONFLICT")
                connection.commit()
                return current, True
            connection.execute(
                "INSERT INTO batches(batch_id, request_id, record_json) VALUES(?, ?, ?)",
                (accepted["batch_id"], accepted["request_id"], self._encode(accepted)),
            )
            self._event(connection, "BATCH_CREATED", None, accepted)
            connection.commit()
        return accepted, False

    def update_batch(self, batch_id: str, mutator: Mutator) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT record_json FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if row is None:
                raise InboxError("INBOX_BATCH_NOT_FOUND")
            record = self._decode(row["record_json"])
            mutator(record)
            record["updated_at"] = utc_now()
            connection.execute("UPDATE batches SET record_json=? WHERE batch_id=?", (self._encode(record), batch_id))
            self._event(connection, "BATCH_UPDATED", None, record)
            connection.commit()
        return deepcopy(record)

    def create_or_get_item(self, record: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        accepted = deepcopy(dict(record))
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT record_json FROM items WHERE item_id=?", (accepted["item_id"],)).fetchone()
            if row is not None:
                connection.commit()
                return self._decode(row["record_json"]), True
            connection.execute(
                "INSERT INTO items(item_id, batch_id, state, content_sha256, record_json) VALUES(?, ?, ?, ?, ?)",
                (accepted["item_id"], accepted["batch_id"], accepted["state"], accepted.get("content_sha256"), self._encode(accepted)),
            )
            self._event(connection, "ITEM_CREATED", accepted["item_id"], accepted)
            connection.commit()
        return accepted, False

    def read_item(self, item_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT record_json FROM items WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            raise InboxError("INBOX_ITEM_NOT_FOUND")
        return deepcopy(self._decode(row["record_json"]))

    def list_items(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT record_json FROM items ORDER BY item_id").fetchall()
        return [deepcopy(self._decode(row["record_json"])) for row in rows]

    def find_by_content(self, content_sha256: str, *, exclude_item_id: str | None = None) -> dict[str, Any] | None:
        query = (
            "SELECT record_json FROM items WHERE content_sha256=? "
            "AND state NOT IN ('TRASHED','DELETED','DEDUPLICATED')"
        )
        parameters: list[Any] = [content_sha256]
        if exclude_item_id is not None:
            query += " AND item_id!=?"
            parameters.append(exclude_item_id)
        query += " ORDER BY item_id LIMIT 1"
        with closing(self._connect()) as connection:
            row = connection.execute(query, parameters).fetchone()
        return None if row is None else deepcopy(self._decode(row["record_json"]))

    def active_content_items(self, content_sha256: str, *, exclude_item_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows=connection.execute("SELECT record_json FROM items WHERE content_sha256=? AND item_id!=? AND state NOT IN ('TRASHED','DELETED','DEDUPLICATED') ORDER BY item_id",(content_sha256,exclude_item_id)).fetchall()
        return [deepcopy(self._decode(row['record_json'])) for row in rows]

    def update_item(self, item_id: str, event_type: str, mutator: Mutator) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT record_json FROM items WHERE item_id=?", (item_id,)).fetchone()
            if row is None:
                raise InboxError("INBOX_ITEM_NOT_FOUND")
            record = self._decode(row["record_json"])
            mutator(record)
            record["version"] = int(record.get("version", 0)) + 1
            record["updated_at"] = utc_now()
            connection.execute(
                "UPDATE items SET state=?, content_sha256=?, record_json=? WHERE item_id=?",
                (record["state"], record.get("content_sha256"), self._encode(record), item_id),
            )
            self._event(connection, event_type, item_id, record)
            connection.commit()
        if self.change_listener:
            try:self.change_listener(deepcopy(record))
            except Exception as exc:self.index_sync_error=type(exc).__name__
        return deepcopy(record)

    def enqueue_intent(self, record: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        accepted = deepcopy(dict(record))
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT params_sha256, record_json FROM outbox WHERE idempotency_key=?",
                (accepted["idempotency_key"],),
            ).fetchone()
            if row is not None:
                if row["params_sha256"] != accepted["params_sha256"]:
                    raise InboxError("INBOX_DISPATCH_IDEMPOTENCY_CONFLICT")
                connection.commit()
                return self._decode(row["record_json"]), True
            if accepted.get('params', {}).get('effect') == 'START_JOB_INTENT':
                existing = connection.execute(
                    "SELECT record_json FROM outbox WHERE item_id=? AND params_sha256=? AND state='PENDING' ORDER BY intent_id LIMIT 1",
                    (accepted['item_id'], accepted['params_sha256']),
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return self._decode(existing['record_json']), True
            connection.execute(
                "INSERT INTO outbox(intent_id, idempotency_key, item_id, params_sha256, state, record_json) VALUES(?, ?, ?, ?, ?, ?)",
                (accepted["intent_id"], accepted["idempotency_key"], accepted["item_id"], accepted["params_sha256"], accepted["state"], self._encode(accepted)),
            )
            self._event(connection, "DISPATCH_INTENT_ENQUEUED", accepted["item_id"], accepted)
            connection.commit()
        return accepted, False

    def read_intent(self, idempotency_key: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT record_json FROM outbox WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if row is None:
            raise InboxError("INBOX_DISPATCH_INTENT_NOT_FOUND")
        return deepcopy(self._decode(row["record_json"]))

    def update_intent(self, idempotency_key: str, state: str, receipt: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT record_json FROM outbox WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if row is None:
                raise InboxError("INBOX_DISPATCH_INTENT_NOT_FOUND")
            record = self._decode(row["record_json"])
            receipt_sha = canonical_sha256(receipt)
            if record["state"] != "PENDING":
                if record["state"] != state or record.get("receipt_sha256") != receipt_sha:
                    raise InboxError("INBOX_DISPATCH_COMPLETION_CONFLICT")
                connection.commit()
                return record, True
            record.update(
                {
                    "state": state,
                    "receipt": deepcopy(dict(receipt)),
                    "receipt_sha256": receipt_sha,
                    "updated_at": utc_now(),
                }
            )
            connection.execute(
                "UPDATE outbox SET state=?, record_json=? WHERE idempotency_key=?",
                (state, self._encode(record), idempotency_key),
            )
            self._event(connection, "DISPATCH_INTENT_COMPLETED", record["item_id"], record)
            connection.commit()
        return deepcopy(record), False

    def list_intents(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT record_json FROM outbox ORDER BY intent_id").fetchall()
        return [deepcopy(self._decode(row["record_json"])) for row in rows]

    def create_operation(self, record: Mapping[str, Any]) -> dict[str, Any]:
        accepted = deepcopy(dict(record))
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO operations(operation_id, state, record_json) VALUES(?, ?, ?)",
                (accepted["operation_id"], accepted["state"], self._encode(accepted)),
            )
            self._event(connection, "UNDO_OPERATION_CREATED", None, accepted)
            connection.commit()
        return accepted

    def read_operation(self, operation_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT record_json FROM operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        return None if row is None else deepcopy(self._decode(row["record_json"]))

    def latest_operation(
        self,
        state: str,
        *,
        include_internal: bool = True,
        redo_order: bool = False,
    ) -> dict[str, Any] | None:
        where = "state=?"
        if not include_internal:
            where += " AND operation_id NOT LIKE 'refinement-%'"
        order = "ASC" if redo_order else "DESC"
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT record_json FROM operations WHERE {where} ORDER BY sequence {order} LIMIT 1",
                (state,),
            ).fetchone()
        return None if row is None else deepcopy(self._decode(row["record_json"]))

    def discard_user_operations(self, state: str) -> int:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT operation_id, record_json FROM operations "
                "WHERE state=? AND operation_id NOT LIKE 'refinement-%' ORDER BY sequence",
                (state,),
            ).fetchall()
            for row in rows:
                record = self._decode(row["record_json"])
                record["state"] = "DISCARDED"
                record["updated_at"] = utc_now()
                connection.execute(
                    "UPDATE operations SET state='DISCARDED', record_json=? WHERE operation_id=?",
                    (self._encode(record), row["operation_id"]),
                )
                self._event(connection, "UNDO_OPERATION_DISCARDED", None, record)
            connection.commit()
        return len(rows)

    def update_operation(self, operation_id: str, state: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT record_json FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None:
                raise InboxError("INBOX_UNDO_OPERATION_NOT_FOUND")
            record = self._decode(row["record_json"])
            record["state"] = state
            record["updated_at"] = utc_now()
            connection.execute(
                "UPDATE operations SET state=?, record_json=? WHERE operation_id=?",
                (state, self._encode(record), operation_id),
            )
            self._event(connection, "UNDO_OPERATION_UPDATED", None, record)
            connection.commit()
        return deepcopy(record)

    def clear_operation_history(self) -> int:
        with closing(self._connect()) as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0])
            connection.execute("DELETE FROM operations")
            connection.commit()
        return count

    def events(self, after_sequence: int = 0) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT sequence, event_json FROM events WHERE sequence>? ORDER BY sequence", (after_sequence,)
            ).fetchall()
        return [{"sequence": int(row["sequence"]), **self._decode(row["event_json"])} for row in rows]

    def get_setting(self, setting_key: str, default: Mapping[str, Any]) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT record_json FROM settings WHERE setting_key=?", (setting_key,)).fetchone()
        return deepcopy(dict(default)) if row is None else deepcopy(self._decode(row["record_json"]))

    def set_setting(self, setting_key: str, record: Mapping[str, Any]) -> dict[str, Any]:
        accepted = deepcopy(dict(record))
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO settings(setting_key, record_json) VALUES(?, ?) ON CONFLICT(setting_key) DO UPDATE SET record_json=excluded.record_json",
                (setting_key, self._encode(accepted)),
            )
            self._event(connection, "SETTING_CHANGED", None, accepted)
            connection.commit()
        return accepted

    def integrity_report(self) -> dict[str, Any]:
        try:
            with closing(self._connect()) as connection:
                result = connection.execute("PRAGMA integrity_check").fetchone()[0]
                counts = {
                    name: int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
                    for name in ("batches", "items", "outbox", "operations", "events", "settings")
                }
        except sqlite3.DatabaseError as exc:
            raise InboxError("INBOX_STORE_CORRUPT") from exc
        if result != "ok":
            raise InboxError("INBOX_STORE_CORRUPT")
        return {
            "schema_version": self.schema_version,
            "status": "PASS",
            "journal_mode": "WAL",
            "synchronous": "FULL",
            **{f"{name}_count": count for name, count in counts.items()},
        }


__all__ = ["InboxStore"]
