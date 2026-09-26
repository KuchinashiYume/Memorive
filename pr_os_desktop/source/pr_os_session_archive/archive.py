from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Iterable
from uuid import uuid4
from urllib.parse import urlparse
import re

from .models import (
    BROWSER_CAPTURE_SOURCE_STRUCTURED_DOM_V4,
    BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1,
    BROWSER_CAPTURE_STRUCTURED_DOM_PREDECESSORS,
    ImportBatch,
    NormalizedConversation,
    NormalizedMessage,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _body_payload(conversation: NormalizedConversation) -> list[dict[str, str]]:
    return [
        {
            "role": message.role,
            "content_kind": message.content_kind,
            "content": message.content,
        }
        for message in conversation.messages
    ]


def _body_sha256(conversation: NormalizedConversation) -> str:
    return _sha256(_json_bytes(_body_payload(conversation)))


def _body_character_count(conversation: NormalizedConversation) -> int:
    return sum(len(message.content) for message in conversation.messages)


def _compatible_visible_body(
    previous: NormalizedMessage,
    incoming: NormalizedMessage,
) -> bool:
    if (
        previous.role != incoming.role
        or previous.content_kind != incoming.content_kind
    ):
        return False
    if previous.content == incoming.content:
        return True
    if previous.content and previous.content in incoming.content:
        return True
    if not incoming.content or incoming.content not in previous.content:
        return False
    return (
        len(incoming.content) >= 6
        or len(incoming.content) / max(1, len(previous.content)) >= 0.72
    )


def _merge_verified_append_prefix(
    previous: tuple[NormalizedMessage, ...],
    incoming: tuple[NormalizedMessage, ...],
) -> tuple[tuple[NormalizedMessage, ...], bool]:
    if not previous or len(incoming) < len(previous):
        return incoming, False
    merged: list[NormalizedMessage] = []
    restored = False
    for old, new in zip(previous, incoming[: len(previous)], strict=True):
        if not _compatible_visible_body(old, new):
            return incoming, False
        if len(old.content) > len(new.content):
            merged.append(old)
            restored = True
        else:
            merged.append(new)
    merged.extend(incoming[len(previous) :])
    return tuple(merged), restored


def _content_sha256(conversation: NormalizedConversation) -> str:
    return _sha256(_json_bytes(conversation.as_dict()))


def _title_is_noise(title: str, conversation_id: str) -> bool:
    normalized = " ".join(title.casefold().split())
    generic = {
        "deepseek",
        "gemini",
        "kimi",
        "new chat",
        "untitled",
        "新建对话",
        "新对话",
        "无标题",
    }
    return (
        not normalized
        or normalized in generic
        or normalized == conversation_id.casefold()
        or normalized.startswith("http://")
        or normalized.startswith("https://")
    )


def _synchronized(method):
    """Serialize one shared SQLite connection across pywebview worker threads."""

    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return guarded


class SessionArchive:
    """Non-destructive local archive for normalized AI conversation exports."""

    def __init__(self, database_path: Path, snapshot_root: Path):
        self.database_path = Path(database_path)
        self.snapshot_root = Path(snapshot_root)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            str(self.database_path),
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    @_synchronized
    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "SessionArchive":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_runs (
                run_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                expected_count INTEGER NOT NULL,
                captured_count INTEGER NOT NULL,
                failed_count INTEGER NOT NULL,
                verification_status TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                source_snapshot_path TEXT NOT NULL,
                retained_not_observed_count INTEGER NOT NULL,
                retained_previous_verified_count INTEGER NOT NULL,
                retained_newer_over_stale_count INTEGER NOT NULL,
                corruption_flags_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                provider TEXT NOT NULL,
                provider_conversation_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                message_count INTEGER NOT NULL,
                body_sha256 TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                completeness TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_locator TEXT NOT NULL,
                source_run_id TEXT NOT NULL,
                hidden INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (provider, provider_conversation_id)
            );

            CREATE TABLE IF NOT EXISTS messages (
                provider TEXT NOT NULL,
                provider_conversation_id TEXT NOT NULL,
                message_index INTEGER NOT NULL,
                message_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content_kind TEXT NOT NULL,
                content TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                PRIMARY KEY (provider, provider_conversation_id, message_index),
                FOREIGN KEY (provider, provider_conversation_id)
                    REFERENCES conversations(provider, provider_conversation_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS revisions (
                revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                provider_conversation_id TEXT NOT NULL,
                source_run_id TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                disposition TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                normalized_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_run_items (
                run_id TEXT NOT NULL,
                provider_conversation_id TEXT NOT NULL,
                item_status TEXT NOT NULL,
                detail_code TEXT NOT NULL,
                PRIMARY KEY (run_id, provider_conversation_id, item_status)
            );

            CREATE TABLE IF NOT EXISTS capture_aliases (
                provider TEXT NOT NULL, alias_id TEXT NOT NULL, canonical_id TEXT NOT NULL,
                PRIMARY KEY (provider, alias_id)
            );
            CREATE INDEX IF NOT EXISTS ix_conversations_capture_url
            ON conversations(provider, source_locator);
            CREATE INDEX IF NOT EXISTS ix_conversations_provider_updated
            ON conversations(provider, updated_at DESC);
            """
        )
        self._connection.commit()

    def _store_source_snapshot(self, batch: ImportBatch, source_sha256: str) -> Path:
        provider_root = self.snapshot_root / batch.provider
        provider_root.mkdir(parents=True, exist_ok=True)
        suffix = batch.source_path.suffix.lower() or ".json"
        snapshot_path = provider_root / f"{source_sha256}{suffix}"
        if snapshot_path.exists():
            if _sha256(snapshot_path.read_bytes()) != source_sha256:
                raise RuntimeError("SOURCE_SNAPSHOT_HASH_COLLISION")
            return snapshot_path
        with snapshot_path.open("xb") as handle:
            handle.write(batch.raw_bytes)
        return snapshot_path

    @staticmethod
    def _detect_corruption(batch: ImportBatch) -> list[str]:
        conversations = tuple(batch.conversations)
        identities: dict[str, set[tuple[str, str]]] = {}
        seen_ids: set[tuple[str, str]] = set()
        flags: list[str] = []
        for conversation in conversations:
            identity = (
                conversation.provider,
                conversation.provider_conversation_id,
            )
            if identity in seen_ids:
                if "DUPLICATE_PROVIDER_CONVERSATION_ID" not in flags:
                    flags.append("DUPLICATE_PROVIDER_CONVERSATION_ID")
                continue
            seen_ids.add(identity)
            if not batch.source_kind.startswith("BROWSER_COMPANION_VISIBLE_UI"):
                continue
            body_hash = _body_sha256(conversation)
            identities.setdefault(body_hash, set()).add(
                (conversation.provider_conversation_id, conversation.title)
            )
        if any(len(group) >= 3 for group in identities.values()):
            flags.append("DUPLICATE_BODY_ACROSS_THREE_OR_MORE_IDENTITIES")
        return flags

    @_synchronized
    def ingest(self, batch: ImportBatch) -> dict[str, Any]:
        source_sha256 = _sha256(batch.raw_bytes)
        snapshot_path = self._store_source_snapshot(batch, source_sha256)
        run_id = f"sync_{uuid4().hex}"
        imported_at = _utc_now()
        conversations = tuple(batch.conversations)
        failures = tuple(batch.failures)
        corruption_flags = self._detect_corruption(batch)

        observed_ids = {
            conversation.provider_conversation_id for conversation in conversations
        }
        failed_ids = {failure.provider_conversation_id for failure in failures}
        existing_rows = self._connection.execute(
            "SELECT provider_conversation_id, completeness FROM conversations WHERE provider = ?",
            (batch.provider,),
        ).fetchall()
        existing = {
            row["provider_conversation_id"]: row["completeness"]
            for row in existing_rows
        }
        retained_not_observed_count = len(
            set(existing).difference(observed_ids).difference(failed_ids)
        )
        retained_previous_verified_count = sum(
            1
            for failure_id in failed_ids
            if existing.get(failure_id) == "COMPLETE"
        )
        retained_newer_over_stale_count = 0
        applied_count = 0
        resolved_identities = []

        expected_matches = batch.expected_count == len(conversations) + len(failures)
        fully_complete = all(
            conversation.completeness == "COMPLETE" for conversation in conversations
        )
        if corruption_flags:
            verification_status = "REJECTED_CORRUPT"
        elif expected_matches and not failures and fully_complete:
            verification_status = "VERIFIED_COMPLETE"
        else:
            verification_status = "PARTIAL"

        with self._connection:
            if not corruption_flags:
                for conversation in conversations:
                    conversation = self._capture_identity(batch, conversation)
                    resolved_identities.append((batch.provider, conversation.provider_conversation_id))
                    disposition = self._merge_conversation(
                        batch=batch,
                        conversation=conversation,
                        run_id=run_id,
                        imported_at=imported_at,
                    )
                    if disposition == "RETAINED_NEWER_OVER_STALE":
                        retained_newer_over_stale_count += 1
                    if disposition == "APPLIED":
                        applied_count += 1
                    if disposition in {
                        "RETAINED_COMPLETE_OVER_PARTIAL",
                        "RETAINED_COMPLETE_OVER_SHORTER_COMPLETE",
                    }:
                        retained_previous_verified_count += 1
                        verification_status = "PARTIAL"
                    self._connection.execute(
                        "INSERT INTO sync_run_items VALUES (?, ?, ?, ?)",
                        (
                            run_id,
                            conversation.provider_conversation_id,
                            disposition,
                            conversation.completeness,
                        ),
                    )
            for failure in failures:
                disposition = (
                    "RETAINED_PREVIOUS_VERIFIED"
                    if existing.get(failure.provider_conversation_id) == "COMPLETE"
                    else "FAILED"
                )
                self._connection.execute(
                    "INSERT INTO sync_run_items VALUES (?, ?, ?, ?)",
                    (
                        run_id,
                        failure.provider_conversation_id,
                        disposition,
                        failure.error_code,
                    ),
                )
            self._connection.execute(
                """
                INSERT INTO sync_runs (
                    run_id, provider, source_kind, generated_at, imported_at,
                    expected_count, captured_count, failed_count,
                    verification_status, source_sha256, source_snapshot_path,
                    retained_not_observed_count,
                    retained_previous_verified_count,
                    retained_newer_over_stale_count, corruption_flags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    batch.provider,
                    batch.source_kind,
                    batch.generated_at,
                    imported_at,
                    batch.expected_count,
                    len(conversations),
                    len(failures),
                    verification_status,
                    source_sha256,
                    str(snapshot_path),
                    retained_not_observed_count,
                    retained_previous_verified_count,
                    retained_newer_over_stale_count,
                    json.dumps(corruption_flags, ensure_ascii=False),
                ),
            )

        return {
            "run_id": run_id,
            "provider": batch.provider,
            "verification_status": verification_status,
            "source_sha256": source_sha256,
            "source_snapshot_path": str(snapshot_path),
            "captured_count": len(conversations),
            "applied_count": applied_count,
            "resolved_identities": resolved_identities,
            "failed_count": len(failures),
            "retained_not_observed_count": retained_not_observed_count,
            "retained_previous_verified_count": retained_previous_verified_count,
            "retained_newer_over_stale_count": retained_newer_over_stale_count,
            "corruption_flags": corruption_flags,
        }

    def _capture_identity(self, batch, conversation):
        if batch.source_kind != BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1:
            return conversation
        url = conversation.source_locator
        parsed = urlparse(url)
        if not re.search(r"/(?:c|chat|app|conversation|session)/[^/]+/?$", parsed.path):
            return conversation
        rows = self._connection.execute(
            "SELECT provider_conversation_id FROM conversations WHERE provider = ? "
            "AND RTRIM(source_locator, '/') = ? AND source_kind = ? "
            "ORDER BY CASE WHEN completeness = 'COMPLETE' THEN 0 ELSE 1 END, "
            "message_count DESC, first_seen_at, provider_conversation_id",
            (batch.provider, url.rstrip('/'), batch.source_kind),
        ).fetchall()
        if not rows:
            return conversation
        winner = rows[0]["provider_conversation_id"]
        # Keep old IDs/messages/revisions available for existing task references.
        # Aliases are only removed from list/count projections, never deleted.
        for row in rows[1:]:
            self._connection.execute("INSERT OR REPLACE INTO capture_aliases VALUES (?, ?, ?)",
                (batch.provider, row["provider_conversation_id"], winner))
        self._connection.execute("DELETE FROM capture_aliases WHERE provider = ? AND alias_id = ?",
            (batch.provider, winner))
        return replace(conversation, provider_conversation_id=winner)

    def _merge_conversation(
        self,
        *,
        batch: ImportBatch,
        conversation: NormalizedConversation,
        run_id: str,
        imported_at: str,
    ) -> str:
        existing = self._connection.execute(
            """
            SELECT c.title, c.created_at, c.updated_at, c.completeness,
                   c.source_kind,
                   c.content_sha256, c.message_count,
                   COALESCE((
                       SELECT SUM(LENGTH(m.content))
                       FROM messages AS m
                       WHERE m.provider = c.provider
                         AND m.provider_conversation_id = c.provider_conversation_id
                   ), 0) AS body_character_count
            FROM conversations AS c
            WHERE c.provider = ? AND c.provider_conversation_id = ?
            """,
            (batch.provider, conversation.provider_conversation_id),
        ).fetchone()
        verified_prefix_restored = False
        if (
            existing is not None
            and existing["completeness"] == "COMPLETE"
            and conversation.completeness == "COMPLETE"
            and existing["source_kind"] == BROWSER_CAPTURE_SOURCE_STRUCTURED_DOM_V4
            and batch.source_kind == BROWSER_CAPTURE_SOURCE_STRUCTURED_DOM_V4
        ):
            existing_messages = tuple(
                NormalizedMessage(
                    message_id=str(row["message_id"]),
                    role=str(row["role"]),
                    content_kind=str(row["content_kind"]),
                    content=str(row["content"]),
                    occurred_at=str(row["occurred_at"]),
                )
                for row in self._connection.execute(
                    """
                    SELECT message_id, role, content_kind, content, occurred_at
                    FROM messages
                    WHERE provider = ? AND provider_conversation_id = ?
                    ORDER BY message_index ASC
                    """,
                    (batch.provider, conversation.provider_conversation_id),
                ).fetchall()
            )
            merged_messages, verified_prefix_restored = (
                _merge_verified_append_prefix(
                    existing_messages,
                    conversation.messages,
                )
            )
            if verified_prefix_restored:
                conversation = replace(conversation, messages=merged_messages)
        incoming_content_sha256 = _content_sha256(conversation)
        incoming_normalized_json = _json_bytes(conversation.as_dict()).decode("utf-8")
        capture_parser_successor = bool(
            existing is not None
            and batch.source_kind == BROWSER_CAPTURE_SOURCE_STRUCTURED_DOM_V4
            and existing["source_kind"]
            in BROWSER_CAPTURE_STRUCTURED_DOM_PREDECESSORS
        )
        if (
            existing is not None
            and not capture_parser_successor
            and conversation.updated_at != "UNKNOWN"
            and existing["updated_at"] != "UNKNOWN"
            and conversation.updated_at < existing["updated_at"]
        ):
            self._connection.execute(
                """
                INSERT INTO revisions (
                    provider, provider_conversation_id, source_run_id,
                    recorded_at, disposition, content_sha256, normalized_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.provider,
                    conversation.provider_conversation_id,
                    run_id,
                    imported_at,
                    "RETAINED_NEWER_OVER_STALE",
                    incoming_content_sha256,
                    incoming_normalized_json,
                ),
            )
            return "RETAINED_NEWER_OVER_STALE"
        if (
            existing is not None
            and existing["completeness"] == "COMPLETE"
            and conversation.completeness != "COMPLETE"
        ):
            self._connection.execute(
                """
                INSERT INTO revisions (
                    provider, provider_conversation_id, source_run_id,
                    recorded_at, disposition, content_sha256, normalized_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.provider,
                    conversation.provider_conversation_id,
                    run_id,
                    imported_at,
                    "RETAINED_COMPLETE_OVER_PARTIAL",
                    incoming_content_sha256,
                    incoming_normalized_json,
                ),
            )
            return "RETAINED_COMPLETE_OVER_PARTIAL"
        if (
            existing is not None
            and not capture_parser_successor
            and batch.source_kind.startswith("BROWSER_COMPANION_VISIBLE_UI")
            and existing["completeness"] == "COMPLETE"
            and conversation.completeness == "COMPLETE"
            and (
                len(conversation.messages) < int(existing["message_count"])
                or (
                    len(conversation.messages) == int(existing["message_count"])
                    and _body_character_count(conversation)
                    < int(existing["body_character_count"])
                )
            )
        ):
            self._connection.execute(
                """
                INSERT INTO revisions (
                    provider, provider_conversation_id, source_run_id,
                    recorded_at, disposition, content_sha256, normalized_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.provider,
                    conversation.provider_conversation_id,
                    run_id,
                    imported_at,
                    "RETAINED_COMPLETE_OVER_SHORTER_COMPLETE",
                    incoming_content_sha256,
                    incoming_normalized_json,
                ),
            )
            return "RETAINED_COMPLETE_OVER_SHORTER_COMPLETE"

        if (existing is not None and batch.source_kind == BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1
            and existing["completeness"] == "PARTIAL" and conversation.completeness == "PARTIAL"
            and (len(conversation.messages) < existing["message_count"] or
                 (len(conversation.messages) == existing["message_count"] and
                  _body_character_count(conversation) < existing["body_character_count"]))):
            self._connection.execute(
                "INSERT INTO revisions (provider, provider_conversation_id, source_run_id, "
                "recorded_at, disposition, content_sha256, normalized_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (batch.provider, conversation.provider_conversation_id, run_id, imported_at,
                 "RETAINED_LONGER_VISIBLE_CAPTURE", incoming_content_sha256, incoming_normalized_json))
            return "RETAINED_LONGER_VISIBLE_CAPTURE"

        effective = conversation
        if existing is not None:
            effective = replace(
                conversation,
                title=(
                    str(existing["title"])
                    if _title_is_noise(
                        conversation.title, conversation.provider_conversation_id
                    )
                    and not _title_is_noise(
                        str(existing["title"]), conversation.provider_conversation_id
                    )
                    else conversation.title
                ),
                created_at=(
                    str(existing["created_at"])
                    if conversation.created_at == "UNKNOWN"
                    and str(existing["created_at"]) != "UNKNOWN"
                    else conversation.created_at
                ),
                updated_at=(
                    str(existing["updated_at"])
                    if (
                        conversation.updated_at == "UNKNOWN"
                        and str(existing["updated_at"]) != "UNKNOWN"
                    )
                    or (
                        capture_parser_successor
                        and conversation.updated_at != "UNKNOWN"
                        and str(existing["updated_at"]) != "UNKNOWN"
                        and conversation.updated_at < str(existing["updated_at"])
                    )
                    else conversation.updated_at
                ),
            )
        content_sha256 = _content_sha256(effective)
        normalized_json = _json_bytes(effective.as_dict()).decode("utf-8")

        first_seen_at = imported_at
        if existing is not None:
            row = self._connection.execute(
                """
                SELECT first_seen_at FROM conversations
                WHERE provider = ? AND provider_conversation_id = ?
                """,
                (batch.provider, conversation.provider_conversation_id),
            ).fetchone()
            first_seen_at = row["first_seen_at"]

        self._connection.execute(
            """
            INSERT INTO conversations (
                provider, provider_conversation_id, title, created_at, updated_at,
                first_seen_at, last_seen_at, message_count, body_sha256,
                content_sha256, completeness, source_kind, source_locator,
                source_run_id, hidden
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(provider, provider_conversation_id) DO UPDATE SET
                title = excluded.title,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                last_seen_at = excluded.last_seen_at,
                message_count = excluded.message_count,
                body_sha256 = excluded.body_sha256,
                content_sha256 = excluded.content_sha256,
                completeness = excluded.completeness,
                source_kind = excluded.source_kind,
                source_locator = excluded.source_locator,
                source_run_id = excluded.source_run_id,
                hidden = 0
            """,
            (
                batch.provider,
                conversation.provider_conversation_id,
                effective.title,
                effective.created_at,
                effective.updated_at,
                first_seen_at,
                imported_at,
                len(effective.messages),
                _body_sha256(effective),
                content_sha256,
                effective.completeness,
                batch.source_kind,
                effective.source_locator,
                run_id,
            ),
        )
        self._connection.execute(
            "DELETE FROM messages WHERE provider = ? AND provider_conversation_id = ?",
            (batch.provider, conversation.provider_conversation_id),
        )
        self._connection.executemany(
            """
            INSERT INTO messages (
                provider, provider_conversation_id, message_index, message_id,
                role, content_kind, content, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    batch.provider,
                    conversation.provider_conversation_id,
                    index,
                    message.message_id,
                    message.role,
                    message.content_kind,
                    message.content,
                    message.occurred_at,
                )
                for index, message in enumerate(effective.messages)
            ],
        )
        self._connection.execute(
            """
            INSERT INTO revisions (
                provider, provider_conversation_id, source_run_id,
                recorded_at, disposition, content_sha256, normalized_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch.provider,
                conversation.provider_conversation_id,
                run_id,
                imported_at,
                (
                    "APPLIED_VERIFIED_APPEND_PREFIX_MERGE"
                    if verified_prefix_restored
                    else (
                        "APPLIED_CAPTURE_PARSER_SUCCESSOR"
                        if capture_parser_successor
                        else "APPLIED"
                    )
                ),
                content_sha256,
                normalized_json,
            ),
        )
        return "APPLIED"

    @_synchronized
    def count_sessions(self, provider: str | None = None) -> int:
        if provider is None:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM conversations WHERE hidden = 0 AND NOT EXISTS (SELECT 1 FROM capture_aliases a WHERE a.provider = conversations.provider AND a.alias_id = conversations.provider_conversation_id)"
            ).fetchone()
        else:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS count FROM conversations
                WHERE hidden = 0 AND NOT EXISTS (SELECT 1 FROM capture_aliases a WHERE a.provider = conversations.provider AND a.alias_id = conversations.provider_conversation_id) AND provider = ?
                """,
                (provider,),
            ).fetchone()
        return int(row["count"])

    @_synchronized
    def get_session(self, provider: str, provider_conversation_id: str) -> dict[str, Any]:
        row = self._connection.execute(
            """
            SELECT * FROM conversations
            WHERE provider = ? AND provider_conversation_id = ? AND hidden = 0
            """,
            (provider, provider_conversation_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"SESSION_NOT_FOUND: {provider}/{provider_conversation_id}")
        return dict(row)

    @_synchronized
    def get_messages(
        self, provider: str, provider_conversation_id: str
    ) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT message_index, message_id, role, content_kind, content, occurred_at
            FROM messages
            WHERE provider = ? AND provider_conversation_id = ?
            ORDER BY message_index ASC
            """,
            (provider, provider_conversation_id),
        ).fetchall()
        return [dict(row) for row in rows]

    @_synchronized
    def list_sessions(
        self,
        *,
        provider: str | None = None,
        limit: int = 20,
        search: str = "",
    ) -> list[dict[str, Any]]:
        if limit not in {20, 50}:
            raise ValueError("DISPLAY_LIMIT_MUST_BE_20_OR_50")
        clauses = ["hidden = 0", "NOT EXISTS (SELECT 1 FROM capture_aliases a WHERE a.provider = conversations.provider AND a.alias_id = conversations.provider_conversation_id)"]
        parameters: list[Any] = []
        if provider is not None:
            clauses.append("provider = ?")
            parameters.append(provider)
        term = search.strip()
        if term:
            clauses.append("(title LIKE ? OR provider_conversation_id LIKE ?)")
            pattern = f"%{term}%"
            parameters.extend((pattern, pattern))
        parameters.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT * FROM conversations
            WHERE {' AND '.join(clauses)}
            ORDER BY
                CASE WHEN updated_at = 'UNKNOWN' THEN 1 ELSE 0 END ASC,
                updated_at DESC,
                provider ASC,
                provider_conversation_id ASC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return [dict(row) for row in rows]

    @_synchronized
    def all_sessions(
        self,
        *,
        provider: str | None = None,
        include_hidden: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = ["1 = 1"] if include_hidden else ["hidden = 0", "NOT EXISTS (SELECT 1 FROM capture_aliases a WHERE a.provider = conversations.provider AND a.alias_id = conversations.provider_conversation_id)"]
        parameters: list[Any] = []
        if provider is not None:
            clauses.append("provider = ?")
            parameters.append(provider)
        rows = self._connection.execute(
            f"""
            SELECT * FROM conversations
            WHERE {' AND '.join(clauses)}
            ORDER BY
                CASE WHEN updated_at = 'UNKNOWN' THEN 1 ELSE 0 END ASC,
                updated_at DESC,
                provider ASC,
                provider_conversation_id ASC
            """,
            parameters,
        ).fetchall()
        return [dict(row) for row in rows]

    @_synchronized
    def set_hidden(
        self,
        identities: Iterable[tuple[str, str]],
        *,
        hidden: bool,
    ) -> dict[str, Any]:
        requested = tuple(dict.fromkeys(identities))
        changed = 0
        missing: list[str] = []
        with self._connection:
            for provider, provider_conversation_id in requested:
                cursor = self._connection.execute(
                    """
                    UPDATE conversations SET hidden = ?
                    WHERE provider = ? AND provider_conversation_id = ?
                    """,
                    (1 if hidden else 0, provider, provider_conversation_id),
                )
                if cursor.rowcount:
                    changed += 1
                else:
                    missing.append(f"{provider}:{provider_conversation_id}")
        return {
            "requested_count": len(requested),
            "changed_count": changed,
            "missing_identities": missing,
            "hidden": hidden,
            "remote_session_mutations": 0,
        }
