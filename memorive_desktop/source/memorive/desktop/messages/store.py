"""The message list of the desktop window, kept in message_projection_store.json.

The file holds {schema_version, revision, updated_at, state, state_sha256}; the state holds the
messages by id, the dedupe index (dedupe key -> message id, which follows from the messages)
and the projection cursor with the fingerprints of projected events. Every message is checked
as a MessageRecord and scanned for secrets when it is read or written. A file that cannot be
read is moved aside as message_projection.corrupt.<hash>.json and replaced by an empty list.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping
import uuid

from memorive.desktop.messages.contracts import MessageRecord, canonical_json_bytes
from memorive.desktop.messages.privacy import PrivacyBoundaryError, RedactionPolicy
from memorive.desktop.messages.projection_lock import projection_lock


STORE_SCHEMA_VERSION = "MessagesMessageProjectionStore-v1"


class MessageStoreCorrupt(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def default_projection_state() -> dict[str, Any]:
    return {"cursor": 0, "fingerprints": {}}


def default_state() -> dict[str, Any]:
    return {"messages": {}, "dedupe_index": {}, "projection": default_projection_state()}


def _state_sha(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(dict(value))).hexdigest().upper()


class MessageStore:
    def __init__(self, profile_root: Path | str, *, redaction_policy: RedactionPolicy | None = None):
        self.profile_root = Path(profile_root)
        self.profile_root.mkdir(parents=True, exist_ok=True)
        self.path = self.profile_root / "message_projection_store.json"
        self.recovery_receipt_path = self.profile_root / "message_projection_recovery.json"
        self.redaction_policy = redaction_policy or RedactionPolicy()

    def write_json(self, path: Path, payload: Mapping[str, Any]) -> None:
        """Write payload to path through a temporary file in the same folder (also used by the messages controller)."""
        temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        with temporary.open("xb") as stream:
            stream.write(canonical_json_bytes(dict(payload)) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def _validate_state(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping) or not isinstance(value.get("messages", {}), Mapping):
            raise MessageStoreCorrupt("MESSAGE_STORE_MESSAGES_INVALID")
        messages: dict[str, dict[str, Any]] = {}
        for raw in value.get("messages", {}).values():
            record = MessageRecord.from_mapping(raw).to_dict()
            self.redaction_policy.assert_public_safe(record)
            messages[record["message_id"]] = record
        projection = value.get("projection", default_projection_state())
        cursor = projection.get("cursor") if isinstance(projection, Mapping) else None
        fingerprints = projection.get("fingerprints") if isinstance(projection, Mapping) else None
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise MessageStoreCorrupt("MESSAGE_STORE_CURSOR_INVALID")
        if not isinstance(fingerprints, Mapping):
            raise MessageStoreCorrupt("MESSAGE_STORE_FINGERPRINTS_INVALID")
        accepted: dict[int, str] = {}
        for sequence, digest in fingerprints.items():
            try:
                number = int(sequence)
            except (TypeError, ValueError) as exc:
                raise MessageStoreCorrupt("MESSAGE_STORE_FINGERPRINT_SEQUENCE_INVALID") from exc
            if number <= 0 or not isinstance(digest, str) or len(digest) != 64:
                raise MessageStoreCorrupt("MESSAGE_STORE_FINGERPRINT_INVALID")
            accepted[number] = digest
        return {
            "messages": dict(sorted(messages.items())),
            "dedupe_index": dict(sorted((row["dedupe_key"], key) for key, row in messages.items())),
            "projection": {"cursor": cursor, "fingerprints": {str(n): accepted[n] for n in sorted(accepted)}},
        }

    def _write(self, state: Mapping[str, Any], revision: int) -> dict[str, Any]:
        accepted = self._validate_state(state)
        # updated_at and state_sha256 are kept for earlier versions, which require them
        envelope = {"schema_version": STORE_SCHEMA_VERSION, "revision": revision, "updated_at": utc_now(),
                    "state": accepted, "state_sha256": _state_sha(accepted)}
        self.write_json(self.path, envelope)
        return envelope

    def _recover(self, raw: bytes, reason: str) -> dict[str, Any]:
        digest = hashlib.sha256(raw).hexdigest().upper()
        quarantine = self.profile_root / f"message_projection.corrupt.{digest[:16]}.json"
        if not quarantine.exists() and self.path.exists():
            os.replace(self.path, quarantine)
        recovered = self._write(default_state(), 0)
        self.write_json(self.recovery_receipt_path, {
            "schema_version": "MessagesMessageStoreRecoveryReceipt-v1", "reason_code": reason,
            "corrupt_bytes": len(raw), "corrupt_sha256": digest, "quarantine_name": quarantine.name,
            "raw_content_in_receipt": False, "delete_performed": False,
            "status": "RECOVERED_TO_SAFE_EMPTY_PROJECTION"})
        return recovered

    def _load(self, *, recover_corruption: bool = True) -> dict[str, Any]:
        if not self.path.exists():
            return self._write(default_state(), 0)
        raw = self.path.read_bytes()
        try:
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, Mapping) or value.get("schema_version") != STORE_SCHEMA_VERSION:
                raise MessageStoreCorrupt("MESSAGE_STORE_SCHEMA_UNSUPPORTED")
            revision = value.get("revision")
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
                raise MessageStoreCorrupt("MESSAGE_STORE_REVISION_INVALID")
            return {**value, "state": self._validate_state(value.get("state"))}
        except PrivacyBoundaryError:
            raise
        except Exception as exc:
            if recover_corruption:
                return self._recover(raw, str(exc).split(":", 1)[0] or type(exc).__name__)
            if isinstance(exc, MessageStoreCorrupt):
                raise
            raise MessageStoreCorrupt("MESSAGE_STORE_DECODE_FAILED") from exc

    def load(self, *, recover_corruption: bool = True) -> dict[str, Any]:
        with projection_lock(self.profile_root):
            return self._load(recover_corruption=recover_corruption)

    def save(self, state: Mapping[str, Any], *, expected_revision: int) -> dict[str, Any]:
        with projection_lock(self.profile_root):
            current = self._load(recover_corruption=False)["revision"]
            if current != expected_revision:
                raise ValueError(f"MESSAGE_STORE_REVISION_CONFLICT:{expected_revision}:{current}")
            return self._write(state, expected_revision + 1)

    def transaction(self, mutator: Callable[[dict[str, Any]], Any]) -> tuple[dict[str, Any], Any]:
        with projection_lock(self.profile_root):
            current = self._load(recover_corruption=False)
            result = mutator(current["state"])
            return self._write(current["state"], current["revision"] + 1), result

    def records(self) -> list[MessageRecord]:
        envelope = self.load(recover_corruption=False)
        return [MessageRecord.from_mapping(row) for row in envelope["state"]["messages"].values()]

    def get(self, message_id: str) -> MessageRecord:
        envelope = self.load(recover_corruption=False)
        raw = envelope["state"]["messages"].get(message_id)
        if raw is None:
            raise KeyError(message_id)
        return MessageRecord.from_mapping(raw)

    def update_record(self, message_id: str, mutator: Callable[[dict[str, Any]], None]) -> MessageRecord:
        def apply(state: dict[str, Any]) -> dict[str, Any]:
            raw = state["messages"].get(message_id)
            if raw is None:
                raise KeyError(message_id)
            mutator(raw)
            record = MessageRecord.from_mapping(raw).to_dict()
            state["messages"][message_id] = record
            return record

        _, result = self.transaction(apply)
        return MessageRecord.from_mapping(result)

    def delete_records(self, message_ids: list[str] | tuple[str, ...]) -> dict[str, Any]:
        """Delete an exact message set atomically and return a narrow receipt.

        This is not a general UI action: it removes stale projection rows (the dedupe index follows
        from the messages that remain).
        """

        accepted = tuple(dict.fromkeys(str(value).strip() for value in message_ids))
        if not accepted or any(not value for value in accepted):
            raise ValueError("MESSAGE_STORE_DELETE_IDS_INVALID")

        def apply(state: dict[str, Any]) -> dict[str, Any]:
            missing = [message_id for message_id in accepted if message_id not in state["messages"]]
            if missing:
                raise ValueError("MESSAGE_STORE_DELETE_TARGET_MISSING:" + ",".join(missing))
            removed = [state["messages"].pop(message_id) for message_id in accepted]
            return {"removed": [{key: raw[key] for key in ("message_id", "title", "severity", "collection_state")}
                                for raw in removed],
                    "remaining_count": len(state["messages"])}

        saved, result = self.transaction(apply)
        return {
            "schema_version": "DesktopMessageProjectionCleanupReceipt-v1",
            "removed_message_ids": list(accepted),
            "removed_count": len(accepted),
            "removed": result["removed"],
            "remaining_count": result["remaining_count"],
            "revision": saved["revision"],
            "state_sha256": saved["state_sha256"],
            "status": "PASS",
        }


__all__ = [
    "MessageStore",
    "MessageStoreCorrupt",
    "STORE_SCHEMA_VERSION",
    "default_projection_state",
    "default_state",
    "utc_now",
]
