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


def object_ref(value: Mapping[str, Any], *, id_field: str) -> str:
    checked = _mapping(value, "object")
    _text(checked[id_field], id_field)
    _positive_int(checked["revision"], "revision")
    validate_hash_descriptor(checked["content_hash"], "content_hash")
    return (
        f"{checked[id_field]}@revision:{checked['revision']}"
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
    "object_ref",
    "validate_context_capsule",
]
