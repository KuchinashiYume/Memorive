from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping
import uuid

from .contracts import MessageRecord, canonical_json_bytes
from .privacy import PrivacyBoundaryError, RedactionPolicy
from .projection_lock import projection_lock


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

    def _atomic_write(self, path: Path, payload: Mapping[str, Any]) -> None:
        encoded = canonical_json_bytes(dict(payload)) + b"\n"
        temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def _validate_state(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != {"messages", "dedupe_index", "projection"}:
            raise MessageStoreCorrupt("MESSAGE_STORE_STATE_FIELDS_INVALID")
        raw_messages = value["messages"]
        if not isinstance(raw_messages, Mapping) or len(raw_messages) > 10000:
            raise MessageStoreCorrupt("MESSAGE_STORE_MESSAGES_INVALID")
        messages: dict[str, dict[str, Any]] = {}
        for message_id, raw in raw_messages.items():
            record = MessageRecord.from_mapping(raw)
            if message_id != record.message_id:
                raise MessageStoreCorrupt("MESSAGE_STORE_MESSAGE_KEY_MISMATCH")
            accepted = record.to_dict()
            self.redaction_policy.assert_public_safe(accepted)
            messages[message_id] = accepted
        raw_index = value["dedupe_index"]
        if not isinstance(raw_index, Mapping) or len(raw_index) != len(messages):
            raise MessageStoreCorrupt("MESSAGE_STORE_DEDUPE_INDEX_INVALID")
        dedupe_index: dict[str, str] = {}
        for key, message_id in raw_index.items():
            if message_id not in messages or messages[message_id]["dedupe_key"] != key:
                raise MessageStoreCorrupt("MESSAGE_STORE_DEDUPE_INDEX_MISMATCH")
            dedupe_index[str(key)] = str(message_id)
        projection = value["projection"]
        if not isinstance(projection, Mapping) or set(projection) != {"cursor", "fingerprints"}:
            raise MessageStoreCorrupt("MESSAGE_STORE_PROJECTION_FIELDS_INVALID")
        cursor = projection["cursor"]
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise MessageStoreCorrupt("MESSAGE_STORE_CURSOR_INVALID")
        fingerprints = projection["fingerprints"]
        if not isinstance(fingerprints, Mapping) or len(fingerprints) > 512:
            raise MessageStoreCorrupt("MESSAGE_STORE_FINGERPRINTS_INVALID")
        accepted_fingerprints: dict[str, str] = {}
        for sequence, digest in fingerprints.items():
            try:
                number = int(sequence)
            except (TypeError, ValueError) as exc:
                raise MessageStoreCorrupt("MESSAGE_STORE_FINGERPRINT_SEQUENCE_INVALID") from exc
            if number <= 0 or not isinstance(digest, str) or len(digest) != 64:
                raise MessageStoreCorrupt("MESSAGE_STORE_FINGERPRINT_INVALID")
            accepted_fingerprints[str(number)] = digest
        return {
            "messages": dict(sorted(messages.items())),
            "dedupe_index": dict(sorted(dedupe_index.items())),
            "projection": {"cursor": cursor, "fingerprints": dict(sorted(accepted_fingerprints.items(), key=lambda row: int(row[0])))},
        }

    def _envelope(self, state: Mapping[str, Any], revision: int) -> dict[str, Any]:
        accepted = self._validate_state(state)
        return {
            "schema_version": STORE_SCHEMA_VERSION,
            "revision": revision,
            "updated_at": utc_now(),
            "state": accepted,
            "state_sha256": _state_sha(accepted),
        }

    def _validate_envelope(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "revision",
            "updated_at",
            "state",
            "state_sha256",
        }:
            raise MessageStoreCorrupt("MESSAGE_STORE_ENVELOPE_INVALID")
        if value["schema_version"] != STORE_SCHEMA_VERSION:
            raise MessageStoreCorrupt("MESSAGE_STORE_SCHEMA_UNSUPPORTED")
        revision = value["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise MessageStoreCorrupt("MESSAGE_STORE_REVISION_INVALID")
        state = self._validate_state(value["state"])
        if value["state_sha256"] != _state_sha(state):
            raise MessageStoreCorrupt("MESSAGE_STORE_CHECKSUM_MISMATCH")
        return {**dict(value), "state": state}

    def _recover(self, raw: bytes, reason: str) -> dict[str, Any]:
        digest = hashlib.sha256(raw).hexdigest().upper()
        quarantine = self.profile_root / f"message_projection.corrupt.{digest[:16]}.json"
        if not quarantine.exists() and self.path.exists():
            os.replace(self.path, quarantine)
        recovered = self._envelope(default_state(), 0)
        self._atomic_write(self.path, recovered)
        self._atomic_write(
            self.recovery_receipt_path,
            {
                "schema_version": "MessagesMessageStoreRecoveryReceipt-v1",
                "reason_code": reason,
                "corrupt_bytes": len(raw),
                "corrupt_sha256": digest,
                "quarantine_name": quarantine.name,
                "raw_content_in_receipt": False,
                "delete_performed": False,
                "status": "RECOVERED_TO_SAFE_EMPTY_PROJECTION",
            },
        )
        return recovered

    def load(self, *, recover_corruption: bool = True) -> dict[str, Any]:
        with projection_lock(self.profile_root):
            return self._load(recover_corruption=recover_corruption)

    def _load(self, *, recover_corruption: bool = True) -> dict[str, Any]:
        if not self.path.exists():
            initial = self._envelope(default_state(), 0)
            self._atomic_write(self.path, initial)
            return deepcopy(initial)
        raw = self.path.read_bytes()
        try:
            return deepcopy(self._validate_envelope(json.loads(raw.decode("utf-8"))))
        except PrivacyBoundaryError:
            raise
        except Exception as exc:
            if not recover_corruption:
                if isinstance(exc, MessageStoreCorrupt):
                    raise
                raise MessageStoreCorrupt("MESSAGE_STORE_DECODE_FAILED") from exc
            return deepcopy(self._recover(raw, str(exc).split(":", 1)[0] or type(exc).__name__))

    def save(self, state: Mapping[str, Any], *, expected_revision: int) -> dict[str, Any]:
        with projection_lock(self.profile_root):
            return self._save(state,expected_revision=expected_revision)

    def _save(self, state: Mapping[str, Any], *, expected_revision: int) -> dict[str, Any]:
        current = self._load(recover_corruption=False)
        if current["revision"] != expected_revision:
            raise ValueError(f"MESSAGE_STORE_REVISION_CONFLICT:{expected_revision}:{current['revision']}")
        accepted = self._envelope(state, expected_revision + 1)
        self._atomic_write(self.path, accepted)
        return deepcopy(accepted)

    def transaction(self, mutator: Callable[[dict[str, Any]], Any]) -> tuple[dict[str, Any], Any]:
        with projection_lock(self.profile_root):
            current = self._load(recover_corruption=False)
            state = deepcopy(current["state"])
            result = mutator(state)
            saved = self._save(state, expected_revision=current["revision"])
            return saved, result

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
            updated = deepcopy(raw)
            mutator(updated)
            record = MessageRecord.from_mapping(updated)
            state["messages"][message_id] = record.to_dict()
            return record.to_dict()

        _, result = self.transaction(apply)
        return MessageRecord.from_mapping(result)

    def delete_records(self, message_ids: list[str] | tuple[str, ...]) -> dict[str, Any]:
        """Delete an exact message set atomically and return a narrow receipt.

        This is intentionally not exposed as a general UI action.  It exists for
        explicit, audited cleanup of stale projection rows while keeping the
        dedupe index and envelope checksum consistent.
        """

        accepted = tuple(dict.fromkeys(str(value).strip() for value in message_ids))
        if not accepted or any(not value for value in accepted):
            raise ValueError("MESSAGE_STORE_DELETE_IDS_INVALID")

        def apply(state: dict[str, Any]) -> dict[str, Any]:
            missing = [message_id for message_id in accepted if message_id not in state["messages"]]
            if missing:
                raise ValueError("MESSAGE_STORE_DELETE_TARGET_MISSING:" + ",".join(missing))
            removed = []
            for message_id in accepted:
                raw = state["messages"].pop(message_id)
                dedupe_key = raw["dedupe_key"]
                if state["dedupe_index"].pop(dedupe_key, None) != message_id:
                    raise MessageStoreCorrupt("MESSAGE_STORE_DELETE_DEDUPE_MISMATCH")
                removed.append(
                    {
                        "message_id": message_id,
                        "title": raw["title"],
                        "severity": raw["severity"],
                        "collection_state": raw["collection_state"],
                    }
                )
            return {
                "removed": removed,
                "remaining_count": len(state["messages"]),
            }

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
