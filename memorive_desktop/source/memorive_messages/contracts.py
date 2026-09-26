from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from memorive_desktop_service.locator import StableLocator


MESSAGE_SCHEMA_VERSION = "MessagesMessageRecord-v1"
SEVERITIES = frozenset({"RED", "YELLOW", "GREEN", "BLUE"})
READ_STATES = frozenset({"UNREAD", "READ"})
CONFIRMATION_STATES = frozenset({"UNCONFIRMED", "CONFIRMED_PENDING_HISTORY", "CONFIRMED"})
COLLECTION_STATES = frozenset({"CURRENT", "HISTORY"})
NOTIFICATION_STATES = frozenset(
    {
        "NOT_EVALUATED",
        "IN_APP_ONLY",
        "TOAST_SENT",
        "BLOCKED_DISABLED",
        "BLOCKED_QUIET",
        "BLOCKED_UNAVAILABLE",
        "NOT_ELIGIBLE",
    }
)
LIFECYCLE_STATES = frozenset(
    {"in_progress", "completed", "failed", "blocked", "paused", "aborted", "NOT_ASSESSED"}
)
VERIFICATION_STATES = frozenset({"PASS", "FAIL", "ERROR", "NOT_ASSESSED"})
ACCEPTANCE_STATES = frozenset({"PASS", "FAIL", "NOT_ASSESSED"})
CAPABILITY_STATES = frozenset({"AVAILABLE", "BLOCKED", "DISABLED", "NOT_ASSESSED"})
SEVERITY_RANK = {"RED": 0, "YELLOW": 1, "GREEN": 2, "BLUE": 3}

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_SHA256 = re.compile(r"^[0-9A-F]{64}$")


class MessageContractError(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def require_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise MessageContractError(f"MESSAGE_{field.upper()}_INVALID")
    return value


def require_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) > 48:
        raise MessageContractError(f"MESSAGE_{field.upper()}_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MessageContractError(f"MESSAGE_{field.upper()}_INVALID") from exc
    if parsed.tzinfo is None:
        raise MessageContractError(f"MESSAGE_{field.upper()}_TIMEZONE_REQUIRED")
    return value


def require_text(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise MessageContractError(f"MESSAGE_{field.upper()}_INVALID")
    if not allow_empty and not value.strip():
        raise MessageContractError(f"MESSAGE_{field.upper()}_EMPTY")
    return value


@dataclass(frozen=True)
class StatusAxes:
    lifecycle_status: str = "NOT_ASSESSED"
    verification_result: str = "NOT_ASSESSED"
    acceptance_verdict: str = "NOT_ASSESSED"
    capability_status: str = "NOT_ASSESSED"

    def __post_init__(self) -> None:
        if self.lifecycle_status not in LIFECYCLE_STATES:
            raise MessageContractError("MESSAGE_LIFECYCLE_STATUS_INVALID")
        if self.verification_result not in VERIFICATION_STATES:
            raise MessageContractError("MESSAGE_VERIFICATION_RESULT_INVALID")
        if self.acceptance_verdict not in ACCEPTANCE_STATES:
            raise MessageContractError("MESSAGE_ACCEPTANCE_VERDICT_INVALID")
        if self.capability_status not in CAPABILITY_STATES:
            raise MessageContractError("MESSAGE_CAPABILITY_STATUS_INVALID")

    def to_dict(self) -> dict[str, str]:
        return {
            "lifecycle_status": self.lifecycle_status,
            "verification_result": self.verification_result,
            "acceptance_verdict": self.acceptance_verdict,
            "capability_status": self.capability_status,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "StatusAxes":
        if value is None:
            return cls()
        if not isinstance(value, Mapping) or set(value) != {
            "lifecycle_status",
            "verification_result",
            "acceptance_verdict",
            "capability_status",
        }:
            raise MessageContractError("MESSAGE_STATUS_AXES_FIELDS_INVALID")
        return cls(**dict(value))


def validate_attachment_refs(value: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list) or len(value) > 16:
        raise MessageContractError("MESSAGE_ATTACHMENT_REFS_INVALID")
    accepted: list[dict[str, str]] = []
    for row in value:
        if not isinstance(row, Mapping) or set(row) != {"artifact_id", "display_name", "sha256"}:
            raise MessageContractError("MESSAGE_ATTACHMENT_REF_FIELDS_INVALID")
        artifact_id = require_identifier(row["artifact_id"], "attachment_artifact_id")
        display_name = require_text(row["display_name"], "attachment_display_name", 180)
        digest = row["sha256"]
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise MessageContractError("MESSAGE_ATTACHMENT_SHA256_INVALID")
        accepted.append({"artifact_id": artifact_id, "display_name": display_name, "sha256": digest})
    return tuple(accepted)


def validate_source_event_ref(value: Any) -> dict[str, Any]:
    required = {
        "event_id",
        "sequence",
        "job_id",
        "run_id",
        "attempt_id",
    }
    optional = {"node_id", "target_unavailable_at"}
    if (
        not isinstance(value, Mapping)
        or not required <= set(value)
        or set(value) - required - optional
    ):
        raise MessageContractError("MESSAGE_SOURCE_EVENT_REF_FIELDS_INVALID")
    sequence = value["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
        raise MessageContractError("MESSAGE_SOURCE_EVENT_SEQUENCE_INVALID")
    attempt_id = value["attempt_id"]
    if attempt_id is not None:
        attempt_id = require_identifier(attempt_id, "source_attempt_id")
    accepted = {
        "event_id": require_identifier(value["event_id"], "source_event_id"),
        "sequence": sequence,
        "job_id": require_identifier(value["job_id"], "source_job_id"),
        "run_id": require_identifier(value["run_id"], "source_run_id"),
        "attempt_id": attempt_id,
    }
    if "node_id" in value:
        node_id = value["node_id"]
        accepted["node_id"] = (
            require_identifier(node_id, "source_node_id") if node_id is not None else None
        )
    if "target_unavailable_at" in value:
        unavailable_at = value["target_unavailable_at"]
        accepted["target_unavailable_at"] = (
            require_timestamp(unavailable_at, "source_target_unavailable_at")
            if unavailable_at is not None
            else None
        )
    return accepted


@dataclass(frozen=True)
class MessageRecord:
    message_id: str
    dedupe_key: str
    severity: str
    title: str
    summary: str
    body: str
    created_at: str
    first_seen_at: str
    last_seen_at: str
    occurrence_count: int
    read_state: str
    read_at: str | None
    confirmation_state: str
    confirmed_at: str | None
    collection_state: str
    starred: bool
    starred_at: str | None
    protected_bulk_read: bool
    attachment_refs: tuple[dict[str, str], ...]
    target_locator: str | None
    notification_state: str
    source_event_ref: dict[str, Any]
    status_axes: StatusAxes

    def __post_init__(self) -> None:
        require_identifier(self.message_id, "id")
        if not isinstance(self.dedupe_key, str) or not _SHA256.fullmatch(self.dedupe_key):
            raise MessageContractError("MESSAGE_DEDUPE_KEY_INVALID")
        if self.severity not in SEVERITIES:
            raise MessageContractError("MESSAGE_SEVERITY_INVALID")
        require_text(self.title, "title", 180)
        require_text(self.summary, "summary", 320)
        require_text(self.body, "body", 4000, allow_empty=True)
        for field, value in (
            ("created_at", self.created_at),
            ("first_seen_at", self.first_seen_at),
            ("last_seen_at", self.last_seen_at),
        ):
            require_timestamp(value, field)
        if isinstance(self.occurrence_count, bool) or not isinstance(self.occurrence_count, int) or self.occurrence_count <= 0:
            raise MessageContractError("MESSAGE_OCCURRENCE_COUNT_INVALID")
        if self.read_state not in READ_STATES:
            raise MessageContractError("MESSAGE_READ_STATE_INVALID")
        if self.confirmation_state not in CONFIRMATION_STATES:
            raise MessageContractError("MESSAGE_CONFIRMATION_STATE_INVALID")
        if self.collection_state not in COLLECTION_STATES:
            raise MessageContractError("MESSAGE_COLLECTION_STATE_INVALID")
        if not isinstance(self.starred, bool) or not isinstance(self.protected_bulk_read, bool):
            raise MessageContractError("MESSAGE_BOOLEAN_AXIS_INVALID")
        for field, value in (
            ("read_at", self.read_at),
            ("confirmed_at", self.confirmed_at),
            ("starred_at", self.starred_at),
        ):
            if value is not None:
                require_timestamp(value, field)
        if self.read_state == "UNREAD" and self.read_at is not None:
            raise MessageContractError("MESSAGE_UNREAD_WITH_READ_AT")
        if self.confirmation_state == "UNCONFIRMED" and self.confirmed_at is not None:
            raise MessageContractError("MESSAGE_UNCONFIRMED_WITH_CONFIRMED_AT")
        if self.collection_state == "HISTORY" and self.confirmation_state != "CONFIRMED":
            raise MessageContractError("MESSAGE_HISTORY_REQUIRES_CONFIRMED")
        if self.starred is False and self.starred_at is not None:
            raise MessageContractError("MESSAGE_UNSTARRED_WITH_STARRED_AT")
        validate_attachment_refs(list(self.attachment_refs))
        if self.target_locator is not None:
            StableLocator.parse(self.target_locator)
        if self.notification_state not in NOTIFICATION_STATES:
            raise MessageContractError("MESSAGE_NOTIFICATION_STATE_INVALID")
        validate_source_event_ref(self.source_event_ref)
        if not isinstance(self.status_axes, StatusAxes):
            raise MessageContractError("MESSAGE_STATUS_AXES_INVALID")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MessageRecord":
        required = {
            "schema_version",
            "message_id",
            "dedupe_key",
            "severity",
            "title",
            "summary",
            "body",
            "created_at",
            "first_seen_at",
            "last_seen_at",
            "occurrence_count",
            "read_state",
            "read_at",
            "confirmation_state",
            "confirmed_at",
            "collection_state",
            "starred",
            "starred_at",
            "protected_bulk_read",
            "attachment_refs",
            "target_locator",
            "notification_state",
            "source_event_ref",
            "status_axes",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise MessageContractError("MESSAGE_RECORD_FIELDS_INVALID")
        if value["schema_version"] != MESSAGE_SCHEMA_VERSION:
            raise MessageContractError("MESSAGE_SCHEMA_VERSION_INVALID")
        return cls(
            message_id=value["message_id"],
            dedupe_key=value["dedupe_key"],
            severity=value["severity"],
            title=value["title"],
            summary=value["summary"],
            body=value["body"],
            created_at=value["created_at"],
            first_seen_at=value["first_seen_at"],
            last_seen_at=value["last_seen_at"],
            occurrence_count=value["occurrence_count"],
            read_state=value["read_state"],
            read_at=value["read_at"],
            confirmation_state=value["confirmation_state"],
            confirmed_at=value["confirmed_at"],
            collection_state=value["collection_state"],
            starred=value["starred"],
            starred_at=value["starred_at"],
            protected_bulk_read=value["protected_bulk_read"],
            attachment_refs=validate_attachment_refs(value["attachment_refs"]),
            target_locator=value["target_locator"],
            notification_state=value["notification_state"],
            source_event_ref=validate_source_event_ref(value["source_event_ref"]),
            status_axes=StatusAxes.from_mapping(value["status_axes"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MESSAGE_SCHEMA_VERSION,
            "message_id": self.message_id,
            "dedupe_key": self.dedupe_key,
            "severity": self.severity,
            "title": self.title,
            "summary": self.summary,
            "body": self.body,
            "created_at": self.created_at,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "occurrence_count": self.occurrence_count,
            "read_state": self.read_state,
            "read_at": self.read_at,
            "confirmation_state": self.confirmation_state,
            "confirmed_at": self.confirmed_at,
            "collection_state": self.collection_state,
            "starred": self.starred,
            "starred_at": self.starred_at,
            "protected_bulk_read": self.protected_bulk_read,
            "attachment_refs": [dict(row) for row in self.attachment_refs],
            "target_locator": self.target_locator,
            "notification_state": self.notification_state,
            "source_event_ref": dict(self.source_event_ref),
            "status_axes": self.status_axes.to_dict(),
        }


def message_dedupe_key(event: Mapping[str, Any]) -> str:
    report_period = event.get("report_period")
    identity = {
        "job_id": require_identifier(event.get("job_id"), "dedupe_job_id"),
        "run_id": require_identifier(event.get("run_id"), "dedupe_run_id"),
        "node_id": event.get("node_id"),
        "event_type": require_identifier(event.get("event_type"), "dedupe_event_type"),
        "root_cause": require_identifier(event.get("root_cause"), "dedupe_root_cause"),
        "report_period": report_period,
    }
    if identity["node_id"] is not None:
        identity["node_id"] = require_identifier(identity["node_id"], "dedupe_node_id")
    if report_period is not None:
        identity["report_period"] = require_identifier(report_period, "dedupe_report_period")
    return canonical_sha256(identity)


def message_id_for_dedupe(dedupe_key: str) -> str:
    if not _SHA256.fullmatch(dedupe_key):
        raise MessageContractError("MESSAGE_DEDUPE_KEY_INVALID")
    return "msg-" + dedupe_key[:24].lower()


def stable_message_sort(records: Sequence[MessageRecord]) -> list[MessageRecord]:
    return sorted(
        records,
        key=lambda row: (
            SEVERITY_RANK[row.severity],
            -int(datetime.fromisoformat(row.last_seen_at.replace("Z", "+00:00")).timestamp() * 1_000_000),
            row.message_id,
        ),
    )


__all__ = [
    "ACCEPTANCE_STATES",
    "CAPABILITY_STATES",
    "COLLECTION_STATES",
    "CONFIRMATION_STATES",
    "LIFECYCLE_STATES",
    "MESSAGE_SCHEMA_VERSION",
    "MessageContractError",
    "MessageRecord",
    "NOTIFICATION_STATES",
    "READ_STATES",
    "SEVERITIES",
    "StatusAxes",
    "VERIFICATION_STATES",
    "canonical_json_bytes",
    "canonical_sha256",
    "message_dedupe_key",
    "message_id_for_dedupe",
    "require_identifier",
    "require_text",
    "require_timestamp",
    "stable_message_sort",
]
