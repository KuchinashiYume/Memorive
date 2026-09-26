"""Fail-closed contracts for CONVERSATION-REFINEMENT conversation refinement.

The objects in this module are deterministic, append-only projections.  They
never read provider credentials, call a model, mutate a source conversation,
or authorize a real knowledge ingest.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping, Sequence

from knowledge_feedback.canonical import (
    make_hashed_payload,
    typed_payload_hash,
    validate_hash_descriptor,
    verify_hashed_payload,
)


ACCESS_MODES = {
    "USER_PROVIDED_EXPORT",
    "PROVIDER_OFFICIAL_EXPORT_REQUEST",
    "PROVIDER_OFFICIAL_API_READ",
    "VISIBLE_AUTHENTICATED_UI_READONLY_CAPTURE",
    "AUTHORIZED_LOCAL_CLI_SESSION_ROOT",
    "SYNTHETIC_ISOMORPHIC_FIXTURE",
}
SCOPE_MODES = {"SELECTED_CONVERSATIONS", "ALL_HISTORY_BOUNDED"}
REFINEMENT_TYPES = {
    "USER_DECISION_CLAIM",
    "CONSTRAINT_OR_PREFERENCE",
    "REPOSITORY_VERIFIABLE_FACT_CLAIM",
    "REUSABLE_METHOD",
    "AI_SUGGESTION",
    "HYPOTHESIS",
    "OPEN_QUESTION",
    "FAILED_ATTEMPT",
    "OVERTURNED_CONCLUSION",
    "PROVENANCE_ONLY",
    "FORBIDDEN_REUSE",
}
SELECTION_STATES = {"INCLUDE", "EXCLUDE", "DEFER"}
DECISION_STATES = {"ACCEPT", "EDIT", "REJECT", "DEFER"}
DESTINATIONS = {
    "project_decision_candidate",
    "knowledge_feedback_derived_knowledge_candidate",
    "backlog_candidate",
    "provenance_only",
    "blocked_no_ingest",
}
ROLES = {"user", "assistant", "system", "tool", "unknown"}
BLOCK_TYPES = {
    "text",
    "code",
    "image",
    "audio",
    "file",
    "link",
    "tool_call",
    "tool_result",
    "unknown",
}


class ConversationContractError(ValueError):
    """A Extensions object failed an exact, fail-closed contract."""


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConversationContractError(f"{field.upper()}_MUST_BE_OBJECT")
    return deepcopy(dict(value))


def _exact(value: Mapping[str, Any], keys: set[str], field: str) -> None:
    if set(value) != keys:
        raise ConversationContractError(
            f"{field.upper()}_EXACT_KEYS_MISMATCH:"
            f"missing={sorted(keys - set(value))},extra={sorted(set(value) - keys)}"
        )


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConversationContractError(f"{field.upper()}_MUST_BE_EXACT_TEXT")
    return value


def _timestamp(value: Any, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConversationContractError(f"{field.upper()}_MUST_BE_ISO8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConversationContractError(f"{field.upper()}_OFFSET_REQUIRED")
    return text


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConversationContractError(f"{field.upper()}_MUST_BE_NON_NEGATIVE_INT")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConversationContractError(f"{field.upper()}_MUST_BE_POSITIVE_INT")
    return value


def _sorted_unique_texts(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ConversationContractError(f"{field.upper()}_MUST_BE_ARRAY")
    result = [_text(item, f"{field}[]") for item in value]
    if not allow_empty and not result:
        raise ConversationContractError(f"{field.upper()}_MUST_NOT_BE_EMPTY")
    if result != sorted(set(result)):
        raise ConversationContractError(f"{field.upper()}_MUST_BE_SORTED_UNIQUE")
    return result


def _verify(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    try:
        return verify_hashed_payload(_mapping(value, field), field)
    except ConversationContractError:
        raise
    except Exception as exc:
        raise ConversationContractError(f"{field.upper()}_HASH_INVALID") from exc


def object_ref(value: Mapping[str, Any], *, id_field: str, revision_field: str = "revision") -> str:
    checked = _mapping(value, "object")
    _text(checked[id_field], id_field)
    _positive_int(checked[revision_field], revision_field)
    validate_hash_descriptor(checked["content_hash"], "content_hash")
    return (
        f"{checked[id_field]}@revision:{checked[revision_field]}"
        f"#sha256:{checked['content_hash']['value']}"
    )


_ENVELOPE_KEYS = {
    "schema_version",
    "source_id",
    "provider",
    "surface",
    "access_mode",
    "locator",
    "scope_mode",
    "ceiling",
    "attachment_body_allowed",
    "ownership",
    "classification",
    "retention",
    "raw_payload_git_eligible",
    "private_payload_external_model_allowed",
    "credential_cookie_storage_reads_allowed",
    "source_mutation_allowed",
    "formal_ingest_authorized",
    "created_at",
    "content_hash",
}
_CEILING_KEYS = {
    "conversations",
    "messages",
    "attachment_metadata",
    "attachment_body_reads",
    "content_bytes",
}


def validate_source_access_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _verify(value, "source_access_envelope")
    _exact(result, _ENVELOPE_KEYS, "source_access_envelope")
    if result["schema_version"] != "CONVERSATION_REFINEMENT_SOURCE_ACCESS_ENVELOPE_V1":
        raise ConversationContractError("SOURCE_ACCESS_ENVELOPE_SCHEMA_UNSUPPORTED")
    for field in ("source_id", "provider", "surface", "locator", "ownership", "classification", "retention"):
        _text(result[field], field)
    if result["access_mode"] not in ACCESS_MODES:
        raise ConversationContractError("SOURCE_ACCESS_MODE_INVALID")
    if result["scope_mode"] not in SCOPE_MODES:
        raise ConversationContractError("SOURCE_SCOPE_MODE_INVALID")
    ceiling = _mapping(result["ceiling"], "ceiling")
    _exact(ceiling, _CEILING_KEYS, "ceiling")
    for field, item in ceiling.items():
        _non_negative_int(item, f"ceiling.{field}")
    if ceiling["conversations"] < 1 or ceiling["messages"] < 1 or ceiling["content_bytes"] < 1:
        raise ConversationContractError("SOURCE_CEILING_MUST_ALLOW_BOUNDED_CONTENT")
    if ceiling["attachment_body_reads"] != 0 or result["attachment_body_allowed"] is not False:
        raise ConversationContractError("ATTACHMENT_BODY_MUST_REMAIN_DISABLED")
    for field in (
        "raw_payload_git_eligible",
        "private_payload_external_model_allowed",
        "credential_cookie_storage_reads_allowed",
        "source_mutation_allowed",
        "formal_ingest_authorized",
    ):
        if result[field] is not False:
            raise ConversationContractError(f"{field.upper()}_MUST_REMAIN_FALSE")
    _timestamp(result["created_at"], "created_at")
    return result


def make_source_access_envelope(
    *,
    source_id: str,
    provider: str,
    surface: str,
    access_mode: str,
    locator: str,
    scope_mode: str,
    ceiling: Mapping[str, int],
    ownership: str,
    classification: str,
    retention: str,
    created_at: str,
) -> dict[str, Any]:
    return validate_source_access_envelope(
        make_hashed_payload(
            {
                "schema_version": "CONVERSATION_REFINEMENT_SOURCE_ACCESS_ENVELOPE_V1",
                "source_id": source_id,
                "provider": provider,
                "surface": surface,
                "access_mode": access_mode,
                "locator": locator,
                "scope_mode": scope_mode,
                "ceiling": deepcopy(dict(ceiling)),
                "attachment_body_allowed": False,
                "ownership": ownership,
                "classification": classification,
                "retention": retention,
                "raw_payload_git_eligible": False,
                "private_payload_external_model_allowed": False,
                "credential_cookie_storage_reads_allowed": False,
                "source_mutation_allowed": False,
                "formal_ingest_authorized": False,
                "created_at": created_at,
            }
        )
    )


_SNAPSHOT_KEYS = {
    "schema_version",
    "snapshot_id",
    "revision",
    "source_id",
    "source_envelope_hash",
    "provider_profile",
    "adapter_behavior_hash",
    "source_payload_hash",
    "source_payload_bytes",
    "archive_members",
    "conversation_count",
    "message_count",
    "attachment_count",
    "immutable",
    "raw_payload_git_eligible",
    "captured_at",
    "synthetic_fixture",
    "content_hash",
}


def validate_export_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _verify(value, "conversation_export_snapshot")
    _exact(result, _SNAPSHOT_KEYS, "conversation_export_snapshot")
    if result["schema_version"] != "CONVERSATION_REFINEMENT_CONVERSATION_EXPORT_SNAPSHOT_V1":
        raise ConversationContractError("EXPORT_SNAPSHOT_SCHEMA_UNSUPPORTED")
    for field in ("snapshot_id", "source_id", "provider_profile"):
        _text(result[field], field)
    _positive_int(result["revision"], "revision")
    for field in ("source_envelope_hash", "adapter_behavior_hash", "source_payload_hash"):
        validate_hash_descriptor(result[field], field)
    _non_negative_int(result["source_payload_bytes"], "source_payload_bytes")
    _non_negative_int(result["conversation_count"], "conversation_count")
    _non_negative_int(result["message_count"], "message_count")
    _non_negative_int(result["attachment_count"], "attachment_count")
    members = result["archive_members"]
    if isinstance(members, (str, bytes)) or not isinstance(members, Sequence):
        raise ConversationContractError("ARCHIVE_MEMBERS_MUST_BE_ARRAY")
    if list(members) != sorted(set(members)):
        raise ConversationContractError("ARCHIVE_MEMBERS_MUST_BE_SORTED_UNIQUE")
    if result["immutable"] is not True or result["raw_payload_git_eligible"] is not False:
        raise ConversationContractError("EXPORT_SNAPSHOT_IMMUTABILITY_BOUNDARY_INVALID")
    if not isinstance(result["synthetic_fixture"], bool):
        raise ConversationContractError("SYNTHETIC_FIXTURE_MUST_BE_BOOLEAN")
    _timestamp(result["captured_at"], "captured_at")
    return result


def make_export_snapshot(
    *,
    snapshot_id: str,
    envelope: Mapping[str, Any],
    provider_profile: str,
    adapter_behavior: Mapping[str, Any],
    payload: Mapping[str, Any],
    payload_bytes: int,
    archive_members: Sequence[str],
    conversation_count: int,
    message_count: int,
    attachment_count: int,
    captured_at: str,
    synthetic_fixture: bool,
) -> dict[str, Any]:
    checked_envelope = validate_source_access_envelope(envelope)
    return validate_export_snapshot(
        make_hashed_payload(
            {
                "schema_version": "CONVERSATION_REFINEMENT_CONVERSATION_EXPORT_SNAPSHOT_V1",
                "snapshot_id": snapshot_id,
                "revision": 1,
                "source_id": checked_envelope["source_id"],
                "source_envelope_hash": deepcopy(checked_envelope["content_hash"]),
                "provider_profile": provider_profile,
                "adapter_behavior_hash": typed_payload_hash(deepcopy(dict(adapter_behavior))),
                "source_payload_hash": typed_payload_hash(deepcopy(dict(payload))),
                "source_payload_bytes": payload_bytes,
                "archive_members": sorted(set(archive_members)),
                "conversation_count": conversation_count,
                "message_count": message_count,
                "attachment_count": attachment_count,
                "immutable": True,
                "raw_payload_git_eligible": False,
                "captured_at": captured_at,
                "synthetic_fixture": synthetic_fixture,
            }
        )
    )


_CAPTURE_RECEIPT_KEYS = {
    "schema_version",
    "receipt_id",
    "revision",
    "source_id",
    "source_envelope_hash",
    "provider_profile",
    "required_source",
    "input_controller_class",
    "capture_transport",
    "private_custody_locator_hash",
    "snapshot_hash",
    "ceiling",
    "observed",
    "ceiling_replay_pass",
    "redaction_before_external_model",
    "side_effect_counters",
    "stop_reason",
    "terminal_status",
    "task_terminal_credit",
    "created_at",
    "content_hash",
}
_CAPTURE_OBSERVED_KEYS = {
    "conversation_count",
    "message_count",
    "attachment_metadata_count",
    "content_bytes",
}
_CAPTURE_SIDE_EFFECT_KEYS = {
    "credential_reads",
    "cookie_reads",
    "browser_storage_reads",
    "attachment_body_reads",
    "source_mutations",
    "external_model_requests",
    "formal_ingest_writes",
}
_CAPTURE_STOP_REASONS = {
    "SELECTED_SCOPE_COMPLETE",
    "SOURCE_EXHAUSTED",
    "CEILING_REACHED",
    "BLOCKED_USER_REAUTH_REQUIRED",
    "PROFILE_REQUIRED",
    "BLOCKED_UNKNOWN_REVISION",
    "PARSER_FAILED",
}
_CAPTURE_TERMINAL_STATUSES = {"PASS", "BLOCKED", "FAIL", "ERROR"}
_INPUT_CONTROLLER_CLASSES = {
    "PUBLIC_SYNTHETIC",
    "USER_ACCOUNT_OFFICIAL_THREAD_BRIDGE",
    "USER_ACCOUNT_VISIBLE_UI",
    "USER_OWNED_LOCAL_SESSION_ROOT",
    "USER_PROVIDED_EXPORT",
}
_CAPTURE_TRANSPORTS = {
    "OFFICIAL_THREAD_READONLY_BRIDGE",
    "SYNTHETIC_FIXTURE",
    "VISIBLE_UI_READONLY",
    "LOCAL_FILE_READONLY",
    "USER_PROVIDED_EXPORT",
}


def validate_capture_attempt_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a public-safe source attempt without admitting task completion."""

    result = _verify(value, "capture_attempt_receipt")
    _exact(result, _CAPTURE_RECEIPT_KEYS, "capture_attempt_receipt")
    if result["schema_version"] != "CONVERSATION_REFINEMENT_CAPTURE_ATTEMPT_RECEIPT_V1":
        raise ConversationContractError("CAPTURE_ATTEMPT_RECEIPT_SCHEMA_UNSUPPORTED")
    for field in ("receipt_id", "source_id", "provider_profile"):
        _text(result[field], field)
    _positive_int(result["revision"], "revision")
    validate_hash_descriptor(result["source_envelope_hash"], "source_envelope_hash")
    for field in ("private_custody_locator_hash", "snapshot_hash"):
        if result[field] is not None:
            validate_hash_descriptor(result[field], field)
    if not isinstance(result["required_source"], bool):
        raise ConversationContractError("REQUIRED_SOURCE_MUST_BE_BOOLEAN")
    if result["input_controller_class"] not in _INPUT_CONTROLLER_CLASSES:
        raise ConversationContractError("INPUT_CONTROLLER_CLASS_INVALID")
    if result["capture_transport"] not in _CAPTURE_TRANSPORTS:
        raise ConversationContractError("CAPTURE_TRANSPORT_INVALID")

    ceiling = _mapping(result["ceiling"], "ceiling")
    _exact(ceiling, _CEILING_KEYS, "ceiling")
    for field, item in ceiling.items():
        _non_negative_int(item, f"ceiling.{field}")
    observed = _mapping(result["observed"], "observed")
    _exact(observed, _CAPTURE_OBSERVED_KEYS, "observed")
    for field, item in observed.items():
        _non_negative_int(item, f"observed.{field}")
    replay_pass = (
        observed["conversation_count"] <= ceiling["conversations"]
        and observed["message_count"] <= ceiling["messages"]
        and observed["attachment_metadata_count"] <= ceiling["attachment_metadata"]
        and observed["content_bytes"] <= ceiling["content_bytes"]
        and ceiling["attachment_body_reads"] == 0
    )
    if result["ceiling_replay_pass"] is not replay_pass or not replay_pass:
        raise ConversationContractError("CAPTURE_CEILING_REPLAY_FAILED")

    side_effects = _mapping(result["side_effect_counters"], "side_effect_counters")
    _exact(side_effects, _CAPTURE_SIDE_EFFECT_KEYS, "side_effect_counters")
    for field, item in side_effects.items():
        if _non_negative_int(item, f"side_effect_counters.{field}") != 0:
            raise ConversationContractError("CAPTURE_SIDE_EFFECT_COUNTER_MUST_BE_ZERO")
    if result["redaction_before_external_model"] is not True:
        raise ConversationContractError("REDACTION_BEFORE_EXTERNAL_MODEL_REQUIRED")
    if result["stop_reason"] not in _CAPTURE_STOP_REASONS:
        raise ConversationContractError("CAPTURE_STOP_REASON_INVALID")
    if result["terminal_status"] not in _CAPTURE_TERMINAL_STATUSES:
        raise ConversationContractError("CAPTURE_TERMINAL_STATUS_INVALID")
    if result["task_terminal_credit"] is not False:
        raise ConversationContractError("SINGLE_CAPTURE_CANNOT_GRANT_TASK_TERMINAL_CREDIT")

    if result["terminal_status"] == "PASS":
        if result["snapshot_hash"] is None or result["private_custody_locator_hash"] is None:
            raise ConversationContractError("CAPTURE_PASS_REQUIRES_PRIVATE_SNAPSHOT_BINDING")
        if observed["conversation_count"] < 1 or observed["message_count"] < 1 or observed["content_bytes"] < 1:
            raise ConversationContractError("CAPTURE_PASS_REQUIRES_BOUNDED_CONTENT")
        if result["stop_reason"] not in {
            "SELECTED_SCOPE_COMPLETE",
            "SOURCE_EXHAUSTED",
            "CEILING_REACHED",
        }:
            raise ConversationContractError("CAPTURE_PASS_STOP_REASON_INVALID")
    elif result["stop_reason"] == "BLOCKED_USER_REAUTH_REQUIRED":
        if result["snapshot_hash"] is not None or any(observed.values()):
            raise ConversationContractError("REAUTH_BLOCKER_CANNOT_CLAIM_CAPTURED_CONTENT")

    _timestamp(result["created_at"], "created_at")
    return result


def make_capture_attempt_receipt(
    *,
    receipt_id: str,
    envelope: Mapping[str, Any],
    provider_profile: str,
    required_source: bool,
    input_controller_class: str,
    capture_transport: str,
    private_custody_locator_hash: Mapping[str, Any] | None,
    snapshot_hash: Mapping[str, Any] | None,
    conversation_count: int,
    message_count: int,
    attachment_metadata_count: int,
    content_bytes: int,
    stop_reason: str,
    terminal_status: str,
    created_at: str,
) -> dict[str, Any]:
    checked_envelope = validate_source_access_envelope(envelope)
    ceiling = deepcopy(checked_envelope["ceiling"])
    observed = {
        "conversation_count": conversation_count,
        "message_count": message_count,
        "attachment_metadata_count": attachment_metadata_count,
        "content_bytes": content_bytes,
    }
    replay_pass = (
        conversation_count <= ceiling["conversations"]
        and message_count <= ceiling["messages"]
        and attachment_metadata_count <= ceiling["attachment_metadata"]
        and content_bytes <= ceiling["content_bytes"]
        and ceiling["attachment_body_reads"] == 0
    )
    return validate_capture_attempt_receipt(
        make_hashed_payload(
            {
                "schema_version": "CONVERSATION_REFINEMENT_CAPTURE_ATTEMPT_RECEIPT_V1",
                "receipt_id": receipt_id,
                "revision": 1,
                "source_id": checked_envelope["source_id"],
                "source_envelope_hash": deepcopy(checked_envelope["content_hash"]),
                "provider_profile": provider_profile,
                "required_source": required_source,
                "input_controller_class": input_controller_class,
                "capture_transport": capture_transport,
                "private_custody_locator_hash": deepcopy(private_custody_locator_hash),
                "snapshot_hash": deepcopy(snapshot_hash),
                "ceiling": ceiling,
                "observed": observed,
                "ceiling_replay_pass": replay_pass,
                "redaction_before_external_model": True,
                "side_effect_counters": {
                    "credential_reads": 0,
                    "cookie_reads": 0,
                    "browser_storage_reads": 0,
                    "attachment_body_reads": 0,
                    "source_mutations": 0,
                    "external_model_requests": 0,
                    "formal_ingest_writes": 0,
                },
                "stop_reason": stop_reason,
                "terminal_status": terminal_status,
                "task_terminal_credit": False,
                "created_at": created_at,
            }
        )
    )


_SELECTION_KEYS = {
    "schema_version",
    "selection_id",
    "revision",
    "snapshot_ref",
    "snapshot_hash",
    "decisions",
    "default_state",
    "selected_conversation_ids",
    "selected_message_ids",
    "model_request_count_for_unselected",
    "created_at",
    "content_hash",
}
_SELECTION_DECISION_KEYS = {"conversation_id", "message_ids", "state", "reason"}


def validate_selection_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _verify(value, "conversation_selection_receipt")
    _exact(result, _SELECTION_KEYS, "conversation_selection_receipt")
    if result["schema_version"] != "CONVERSATION_REFINEMENT_CONVERSATION_SELECTION_RECEIPT_V1":
        raise ConversationContractError("SELECTION_RECEIPT_SCHEMA_UNSUPPORTED")
    _text(result["selection_id"], "selection_id")
    _positive_int(result["revision"], "revision")
    _text(result["snapshot_ref"], "snapshot_ref")
    validate_hash_descriptor(result["snapshot_hash"], "snapshot_hash")
    if result["default_state"] != "EXCLUDE":
        raise ConversationContractError("SELECTION_DEFAULT_MUST_BE_EXCLUDE")
    if result["model_request_count_for_unselected"] != 0:
        raise ConversationContractError("UNSELECTED_MODEL_REQUEST_COUNT_MUST_BE_ZERO")
    selected_conversations: list[str] = []
    selected_messages: list[str] = []
    decisions = result["decisions"]
    if isinstance(decisions, (str, bytes)) or not isinstance(decisions, Sequence):
        raise ConversationContractError("SELECTION_DECISIONS_MUST_BE_ARRAY")
    observed: list[str] = []
    for ordinal, raw in enumerate(decisions):
        decision = _mapping(raw, f"decisions[{ordinal}]")
        _exact(decision, _SELECTION_DECISION_KEYS, f"decisions[{ordinal}]")
        conversation_id = _text(decision["conversation_id"], "conversation_id")
        observed.append(conversation_id)
        message_ids = _sorted_unique_texts(decision["message_ids"], "message_ids", allow_empty=True)
        if decision["state"] not in SELECTION_STATES:
            raise ConversationContractError("SELECTION_STATE_INVALID")
        if decision["state"] == "INCLUDE":
            if not message_ids:
                raise ConversationContractError("INCLUDE_REQUIRES_MESSAGE_IDS")
            selected_conversations.append(conversation_id)
            selected_messages.extend(message_ids)
        elif message_ids:
            raise ConversationContractError("NON_INCLUDE_MESSAGE_IDS_FORBIDDEN")
        if decision["reason"] is not None:
            _text(decision["reason"], "reason")
    if observed != sorted(set(observed)):
        raise ConversationContractError("SELECTION_CONVERSATIONS_MUST_BE_SORTED_UNIQUE")
    if result["selected_conversation_ids"] != sorted(selected_conversations):
        raise ConversationContractError("SELECTED_CONVERSATION_PROJECTION_DRIFT")
    if result["selected_message_ids"] != sorted(set(selected_messages)):
        raise ConversationContractError("SELECTED_MESSAGE_PROJECTION_DRIFT")
    _timestamp(result["created_at"], "created_at")
    return result


def make_selection_receipt(
    *,
    selection_id: str,
    snapshot: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
    created_at: str,
) -> dict[str, Any]:
    checked_snapshot = validate_export_snapshot(snapshot)
    copied = sorted((deepcopy(dict(item)) for item in decisions), key=lambda item: item.get("conversation_id", ""))
    selected_conversations = sorted(item["conversation_id"] for item in copied if item.get("state") == "INCLUDE")
    selected_messages = sorted(
        {
            message_id
            for item in copied
            if item.get("state") == "INCLUDE"
            for message_id in item.get("message_ids", [])
        }
    )
    return validate_selection_receipt(
        make_hashed_payload(
            {
                "schema_version": "CONVERSATION_REFINEMENT_CONVERSATION_SELECTION_RECEIPT_V1",
                "selection_id": selection_id,
                "revision": 1,
                "snapshot_ref": object_ref(checked_snapshot, id_field="snapshot_id"),
                "snapshot_hash": deepcopy(checked_snapshot["content_hash"]),
                "decisions": copied,
                "default_state": "EXCLUDE",
                "selected_conversation_ids": selected_conversations,
                "selected_message_ids": selected_messages,
                "model_request_count_for_unselected": 0,
                "created_at": created_at,
            }
        )
    )


_CAPSULE_KEYS = {
    "schema_version",
    "capsule_id",
    "revision",
    "required_message_refs",
    "condition_refs",
    "negation_refs",
    "correction_refs",
    "failure_refs",
    "scope",
    "limitations",
    "context_complete",
    "content_hash",
}


def validate_context_capsule(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _verify(value, "context_capsule")
    _exact(result, _CAPSULE_KEYS, "context_capsule")
    if result["schema_version"] != "CONVERSATION_REFINEMENT_CONTEXT_CAPSULE_V1":
        raise ConversationContractError("CONTEXT_CAPSULE_SCHEMA_UNSUPPORTED")
    _text(result["capsule_id"], "capsule_id")
    _positive_int(result["revision"], "revision")
    for field in ("required_message_refs", "condition_refs", "negation_refs", "correction_refs", "failure_refs"):
        _sorted_unique_texts(result[field], field, allow_empty=field != "required_message_refs")
    _text(result["scope"], "scope")
    if not isinstance(result["limitations"], Sequence) or isinstance(result["limitations"], (str, bytes)):
        raise ConversationContractError("LIMITATIONS_MUST_BE_ARRAY")
    _sorted_unique_texts(result["limitations"], "limitations", allow_empty=True)
    if not isinstance(result["context_complete"], bool):
        raise ConversationContractError("CONTEXT_COMPLETE_MUST_BE_BOOLEAN")
    return result


def make_context_capsule(
    *,
    capsule_id: str,
    required_message_refs: Sequence[str],
    scope: str,
    limitations: Sequence[str] = (),
    condition_refs: Sequence[str] = (),
    negation_refs: Sequence[str] = (),
    correction_refs: Sequence[str] = (),
    failure_refs: Sequence[str] = (),
    context_complete: bool = True,
) -> dict[str, Any]:
    return validate_context_capsule(
        make_hashed_payload(
            {
                "schema_version": "CONVERSATION_REFINEMENT_CONTEXT_CAPSULE_V1",
                "capsule_id": capsule_id,
                "revision": 1,
                "required_message_refs": sorted(set(required_message_refs)),
                "condition_refs": sorted(set(condition_refs)),
                "negation_refs": sorted(set(negation_refs)),
                "correction_refs": sorted(set(correction_refs)),
                "failure_refs": sorted(set(failure_refs)),
                "scope": scope,
                "limitations": sorted(set(limitations)),
                "context_complete": context_complete,
            }
        )
    )


def payload_identity(value: Any) -> dict[str, str]:
    """Return the shared typed SHA-256 descriptor for a JSON-domain value."""

    return typed_payload_hash(deepcopy(value))


__all__ = [
    "ACCESS_MODES",
    "BLOCK_TYPES",
    "ConversationContractError",
    "DECISION_STATES",
    "DESTINATIONS",
    "REFINEMENT_TYPES",
    "ROLES",
    "SCOPE_MODES",
    "SELECTION_STATES",
    "make_context_capsule",
    "make_capture_attempt_receipt",
    "make_export_snapshot",
    "make_selection_receipt",
    "make_source_access_envelope",
    "object_ref",
    "payload_identity",
    "validate_context_capsule",
    "validate_capture_attempt_receipt",
    "validate_export_snapshot",
    "validate_selection_receipt",
    "validate_source_access_envelope",
]
