"""Versioned provider adapters for Extensions.

The synthetic profile and two bounded local-JSONL profiles are admitted in
Construction A.  The local profiles are based on current physical canaries and
the pinned public CC Switch adapter reference; web profiles are never guessed.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .contracts import (
    ConversationContractError,
    make_export_snapshot,
    validate_source_access_envelope,
)


SYNTHETIC_PROFILE = "GENERIC_SYNTHETIC_EXPORT_V1"
CODEX_LOCAL_PROFILE = "CODEX_LOCAL_JSONL_READONLY_V1"
CLAUDE_CODE_LOCAL_PROFILE = "CLAUDE_CODE_LOCAL_JSONL_READONLY_V1"
CODEX_APP_THREAD_BRIDGE_PROFILE = "CODEX_APP_THREAD_READONLY_BRIDGE_V1"
DEEPSEEK_EDGE_VISIBLE_UI_PROFILE = "DEEPSEEK_EDGE_VISIBLE_UI_V1"
ADMITTED_PROFILES = {
    SYNTHETIC_PROFILE,
    CODEX_LOCAL_PROFILE,
    CLAUDE_CODE_LOCAL_PROFILE,
    CODEX_APP_THREAD_BRIDGE_PROFILE,
    DEEPSEEK_EDGE_VISIBLE_UI_PROFILE,
}
ADAPTER_BEHAVIOR = {
    "profile": SYNTHETIC_PROFILE,
    "unknown_field_policy": "FAIL_CLOSED",
    "attachment_body_policy": "METADATA_ONLY",
    "role_mapping": "EXACT_V1",
    "edge_mapping": "EXPLICIT_PARENT_ONLY_V1",
}
LOCAL_ADAPTER_BEHAVIORS = {
    CODEX_LOCAL_PROFILE: {
        "profile": CODEX_LOCAL_PROFILE,
        "reference": "CC_SWITCH_MAIN_A98829BA1E8BD99A1DF671E3C36C8BB6AA537E47",
        "canary_receipt_sha256": "42701389DAF2ACC8DE5C87C058B9FEB73D90F3FB8E91B2B7F6F9FDF9D86C82AC",
        "profile_observation_sha256": "DFFBAA99066F4ED7256F4FE4922C2D3158DB848FBA52C695ECFAB0E9C386F2ED",
        "unknown_record_policy": "IGNORE_WITH_PUBLIC_SAFE_DIAGNOSTIC",
        "attachment_body_policy": "METADATA_ONLY",
        "role_mapping": "CODEX_RESPONSE_ITEM_TEXT_V1",
        "edge_mapping": "SOURCE_ORDER_PARENT_CHAIN_V1",
        "acquisition_ceiling_enforced": True,
    },
    CLAUDE_CODE_LOCAL_PROFILE: {
        "profile": CLAUDE_CODE_LOCAL_PROFILE,
        "reference": "CC_SWITCH_MAIN_A98829BA1E8BD99A1DF671E3C36C8BB6AA537E47",
        "canary_receipt_sha256": "720243CD8E7B711209B76B0D98A9FB712A5E8AEF3AC77F2FD431AE64800589BE",
        "profile_observation_sha256": "6329201630175487B4D810B8BF4A3E6A4A8589520EB68C9766F8885874B56B90",
        "unknown_record_policy": "IGNORE_WITH_PUBLIC_SAFE_DIAGNOSTIC",
        "attachment_body_policy": "METADATA_ONLY",
        "role_mapping": "CLAUDE_MESSAGE_TEXT_V1",
        "edge_mapping": "SOURCE_ORDER_PARENT_CHAIN_V1",
        "acquisition_ceiling_enforced": True,
    },
}
THREAD_BRIDGE_BEHAVIOR = {
    "profile": CODEX_APP_THREAD_BRIDGE_PROFILE,
    "transport": "CODEX_APP_OFFICIAL_READONLY_THREAD_BRIDGE",
    "thread_kinds": ["chatgpt", "codex"],
    "unknown_field_policy": "FAIL_CLOSED",
    "title_policy": "PRIVATE_TITLE_WITHHELD",
    "attachment_body_policy": "DISABLED",
    "credential_cookie_storage_policy": "FORBIDDEN",
    "role_mapping": "EXACT_USER_ASSISTANT_TEXT_V1",
    "text_normalization": "OUTER_WHITESPACE_STRIP_V1",
    "edge_mapping": "SOURCE_ORDER_PARENT_CHAIN_V1",
    "source_actor_identity_binding": "EXACT_NONSELF_V1",
    "raw_bridge_bundle_policy": "PRIVATE_RUN_CUSTODY_ONLY",
    "acquisition_ceiling_enforced": True,
}
VISIBLE_UI_BRIDGE_BEHAVIORS = {
    DEEPSEEK_EDGE_VISIBLE_UI_PROFILE: {
        "profile": DEEPSEEK_EDGE_VISIBLE_UI_PROFILE,
        "provider": "DEEPSEEK_WEB",
        "surface": "EDGE_BROWSER_VISIBLE_UI",
        "transport": "EDGE_BROWSER_EXTENSION_VISIBLE_UI_READONLY",
        "unknown_field_policy": "FAIL_CLOSED",
        "title_policy": "PRIVATE_TITLE_WITHHELD",
        "attachment_body_policy": "DISABLED",
        "credential_cookie_storage_policy": "FORBIDDEN",
        "role_mapping": "HOST_OBSERVED_VISIBLE_UI_TURN_ORDER_V1",
        "timestamp_mapping": "CAPTURE_OBSERVATION_TIME_NOT_PROVIDER_TIMESTAMP_V1",
        "edge_mapping": "SOURCE_ORDER_PARENT_CHAIN_V1",
        "raw_bridge_bundle_policy": "PRIVATE_RUN_CUSTODY_ONLY",
        "visible_slice_only": True,
        "acquisition_ceiling_enforced": True,
    }
}

_THREAD_BRIDGE_BUNDLE_KEYS = {
    "provider_profile",
    "thread_kind",
    "thread_id",
    "capture_actor_id",
    "source_equals_capture_actor",
    "messages",
    "has_more",
    "source_truncated",
}
_THREAD_BRIDGE_MESSAGE_KEYS = {
    "message_id",
    "role",
    "text",
    "created_at",
}
_THREAD_BRIDGE_KINDS = {"chatgpt", "codex"}
_VISIBLE_UI_BUNDLE_KEYS = {
    "provider_profile",
    "conversation_id",
    "messages",
    "observed_at",
    "source_truncated",
    "boundary_method",
}
_VISIBLE_UI_MESSAGE_KEYS = {"message_id", "role", "text"}
_VISIBLE_UI_BOUNDARY_METHOD = "HOST_OBSERVED_VISIBLE_UI_TURN_ORDER_V1"


def _bridge_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConversationContractError(f"{field.upper()}_MUST_BE_EXACT_TEXT")
    return value


def _bridge_message_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ConversationContractError(f"{field.upper()}_MUST_BE_TEXT")
    normalized = value.strip()
    if not normalized:
        raise ConversationContractError(f"{field.upper()}_MUST_NOT_BE_EMPTY")
    return normalized


def _bridge_timestamp(value: Any, field: str) -> str:
    text = _bridge_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConversationContractError(f"{field.upper()}_MUST_BE_ISO8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConversationContractError(f"{field.upper()}_OFFSET_REQUIRED")
    return text


def _safe_member_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name:
        raise ConversationContractError("ARCHIVE_MEMBER_NAME_INVALID")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ConversationContractError("ARCHIVE_MEMBER_PATH_ESCAPE")
    if any(part.endswith(":") for part in path.parts):
        raise ConversationContractError("ARCHIVE_MEMBER_DRIVE_ESCAPE")
    return name


def validate_provider_export(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConversationContractError("EXPORT_MUST_BE_OBJECT")
    result = deepcopy(dict(value))
    if set(result) != {"provider_profile", "conversations", "archive_members"}:
        raise ConversationContractError("SYNTHETIC_EXPORT_EXACT_KEYS_MISMATCH")
    if result["provider_profile"] not in ADMITTED_PROFILES:
        raise ConversationContractError("PROVIDER_PROFILE_NOT_ADMITTED")
    conversations = result["conversations"]
    if isinstance(conversations, (str, bytes)) or not isinstance(conversations, Sequence):
        raise ConversationContractError("CONVERSATIONS_MUST_BE_ARRAY")
    ids: list[str] = []
    for conversation in conversations:
        if not isinstance(conversation, Mapping):
            raise ConversationContractError("CONVERSATION_MUST_BE_OBJECT")
        if set(conversation) != {"conversation_id", "title", "messages", "attachments"}:
            raise ConversationContractError("CONVERSATION_EXACT_KEYS_MISMATCH")
        conversation_id = conversation["conversation_id"]
        if not isinstance(conversation_id, str) or not conversation_id:
            raise ConversationContractError("CONVERSATION_ID_INVALID")
        ids.append(conversation_id)
        if not isinstance(conversation["title"], str):
            raise ConversationContractError("CONVERSATION_TITLE_INVALID")
        if not isinstance(conversation["messages"], Sequence) or isinstance(conversation["messages"], (str, bytes)):
            raise ConversationContractError("MESSAGES_MUST_BE_ARRAY")
        if not isinstance(conversation["attachments"], Sequence) or isinstance(conversation["attachments"], (str, bytes)):
            raise ConversationContractError("ATTACHMENTS_MUST_BE_ARRAY")
    if ids != sorted(set(ids)):
        raise ConversationContractError("CONVERSATIONS_MUST_BE_SORTED_UNIQUE")
    members = [_safe_member_name(item) for item in result["archive_members"]]
    if members != sorted(set(members)):
        raise ConversationContractError("ARCHIVE_MEMBERS_MUST_BE_SORTED_UNIQUE")
    return result


def validate_synthetic_export(value: Mapping[str, Any]) -> dict[str, Any]:
    result = validate_provider_export(value)
    if result["provider_profile"] != SYNTHETIC_PROFILE:
        raise ConversationContractError("SYNTHETIC_PROFILE_NOT_ADMITTED")
    return result


def build_synthetic_snapshot(
    *,
    snapshot_id: str,
    envelope: Mapping[str, Any],
    export: Mapping[str, Any],
    captured_at: str,
) -> dict[str, Any]:
    checked_envelope = validate_source_access_envelope(envelope)
    checked_export = validate_synthetic_export(export)
    if checked_envelope["access_mode"] != "SYNTHETIC_ISOMORPHIC_FIXTURE":
        raise ConversationContractError("SYNTHETIC_EXPORT_REQUIRES_SYNTHETIC_ENVELOPE")
    serialized = json.dumps(checked_export, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    conversation_count = len(checked_export["conversations"])
    message_count = sum(len(item["messages"]) for item in checked_export["conversations"])
    attachment_count = sum(len(item["attachments"]) for item in checked_export["conversations"])
    ceiling = checked_envelope["ceiling"]
    if conversation_count > ceiling["conversations"]:
        raise ConversationContractError("CAPTURE_CONVERSATION_CEILING_EXCEEDED")
    if message_count > ceiling["messages"]:
        raise ConversationContractError("CAPTURE_MESSAGE_CEILING_EXCEEDED")
    if attachment_count > ceiling["attachment_metadata"]:
        raise ConversationContractError("CAPTURE_ATTACHMENT_CEILING_EXCEEDED")
    if len(serialized) > ceiling["content_bytes"]:
        raise ConversationContractError("CAPTURE_BYTE_CEILING_EXCEEDED")
    return make_export_snapshot(
        snapshot_id=snapshot_id,
        envelope=checked_envelope,
        provider_profile=SYNTHETIC_PROFILE,
        adapter_behavior=ADAPTER_BEHAVIOR,
        payload=checked_export,
        payload_bytes=len(serialized),
        archive_members=checked_export["archive_members"],
        conversation_count=conversation_count,
        message_count=message_count,
        attachment_count=attachment_count,
        captured_at=captured_at,
        synthetic_fixture=True,
    )


def _extract_local_text(value: Any) -> tuple[str, list[str]]:
    attachment_types: list[str] = []
    if isinstance(value, str):
        return value, attachment_types
    if isinstance(value, Mapping):
        text = value.get("text")
        return (text if isinstance(text, str) else ""), attachment_types
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return "", attachment_types
    texts: list[str] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        item_type = str(item.get("type") or "unknown")
        for key in ("text", "input_text", "output_text"):
            if isinstance(item.get(key), str):
                texts.append(item[key])
                break
        else:
            if item_type not in {"text", "input_text", "output_text"}:
                attachment_types.append(item_type)
    return "\n".join(texts), attachment_types


def _local_record_message(
    profile: str, value: Mapping[str, Any]
) -> tuple[str, str, str | None, list[str]] | None:
    if profile == CODEX_LOCAL_PROFILE:
        if value.get("type") != "response_item" or not isinstance(value.get("payload"), Mapping):
            return None
        payload = dict(value["payload"])
        if payload.get("type") != "message":
            return None
        role = str(payload.get("role") or "unknown")
        content = payload.get("content")
    elif profile == CLAUDE_CODE_LOCAL_PROFILE:
        if value.get("isMeta") is True or not isinstance(value.get("message"), Mapping):
            return None
        payload = dict(value["message"])
        role = str(payload.get("role") or "unknown")
        content = payload.get("content")
    else:
        raise ConversationContractError("LOCAL_PROVIDER_PROFILE_NOT_ADMITTED")
    if role not in {"user", "assistant"}:
        return None
    text, attachment_types = _extract_local_text(content)
    text = text.strip()
    if not text:
        return None
    timestamp = value.get("timestamp") if isinstance(value.get("timestamp"), str) else None
    return role, text, timestamp, attachment_types


def capture_local_jsonl_snapshot(
    *,
    snapshot_id: str,
    envelope: Mapping[str, Any],
    source_root: str | Path,
    source_path: str | Path,
    provider_profile: str,
    captured_at: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Capture one selected local JSONL file under an acquisition-time ceiling."""

    checked_envelope = validate_source_access_envelope(envelope)
    if checked_envelope["access_mode"] != "AUTHORIZED_LOCAL_CLI_SESSION_ROOT":
        raise ConversationContractError("LOCAL_CAPTURE_REQUIRES_LOCAL_SESSION_ROOT_ENVELOPE")
    if provider_profile not in LOCAL_ADAPTER_BEHAVIORS:
        raise ConversationContractError("LOCAL_PROVIDER_PROFILE_NOT_ADMITTED")

    try:
        resolved_root = Path(source_root).resolve(strict=True)
        envelope_root = Path(checked_envelope["locator"]).resolve(strict=True)
        resolved_source = Path(source_path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ConversationContractError("LOCAL_SOURCE_RESOLUTION_FAILED") from exc
    if envelope_root != resolved_root:
        raise ConversationContractError("LOCAL_ROOT_ENVELOPE_MISMATCH")
    if not resolved_source.is_file() or not resolved_source.is_relative_to(resolved_root):
        raise ConversationContractError("LOCAL_SOURCE_OUTSIDE_FROZEN_ROOT")

    ceiling = checked_envelope["ceiling"]
    messages: list[dict[str, Any]] = []
    attachments: list[dict[str, str]] = []
    observed_key_sets: set[tuple[str, ...]] = set()
    bytes_read = 0
    source_lines_read = 0
    stop_reason = "SOURCE_EXHAUSTED"
    session_id: str | None = None
    previous_id: str | None = None

    with resolved_source.open("rb") as source:
        while bytes_read < ceiling["content_bytes"] and len(messages) < ceiling["messages"]:
            remaining = ceiling["content_bytes"] - bytes_read
            raw_line = source.readline(remaining)
            if not raw_line:
                break
            bytes_read += len(raw_line)
            source_lines_read += 1
            if not raw_line.endswith(b"\n") and bytes_read == ceiling["content_bytes"]:
                stop_reason = "CEILING_REACHED"
                break
            try:
                value = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(value, Mapping):
                continue
            value = dict(value)
            observed_key_sets.add(tuple(sorted(str(key) for key in value)))
            if provider_profile == CODEX_LOCAL_PROFILE:
                payload = value.get("payload")
                if value.get("type") == "session_meta" and isinstance(payload, Mapping):
                    candidate = payload.get("id")
                    if isinstance(candidate, str) and candidate:
                        session_id = candidate
            else:
                candidate = value.get("sessionId")
                if isinstance(candidate, str) and candidate:
                    session_id = candidate
            parsed = _local_record_message(provider_profile, value)
            if parsed is None:
                continue
            role, text, timestamp, attachment_types = parsed
            for item_type in attachment_types:
                if len(attachments) < ceiling["attachment_metadata"]:
                    attachments.append({"type": item_type})
            message_id = f"local-message-{len(messages) + 1:06d}"
            messages.append(
                {
                    "message_id": message_id,
                    "parent_id": previous_id,
                    "role": role,
                    "blocks": [{"type": "text", "text": text, "metadata": {}}],
                    "created_at": timestamp or captured_at,
                    "status": "complete",
                    "edit_of": None,
                    "regeneration_group": None,
                }
            )
            previous_id = message_id

    if len(messages) == ceiling["messages"] or bytes_read == ceiling["content_bytes"]:
        stop_reason = "CEILING_REACHED"
    if not messages:
        raise ConversationContractError("LOCAL_CAPTURE_CONTAINS_NO_ADMISSIBLE_MESSAGES")
    conversation_id = session_id or resolved_source.stem
    export = {
        "provider_profile": provider_profile,
        "conversations": [
            {
                "conversation_id": conversation_id,
                "title": "PRIVATE_TITLE_WITHHELD",
                "messages": messages,
                "attachments": attachments,
            }
        ],
        "archive_members": [resolved_source.name],
    }
    checked_export = validate_provider_export(export)
    serialized = json.dumps(
        checked_export, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    while messages and len(serialized) > ceiling["content_bytes"]:
        messages.pop()
        checked_export = validate_provider_export(export)
        serialized = json.dumps(
            checked_export, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        stop_reason = "CEILING_REACHED"
    if not messages:
        raise ConversationContractError("LOCAL_CAPTURE_NORMALIZED_PAYLOAD_EXCEEDS_CEILING")

    snapshot = make_export_snapshot(
        snapshot_id=snapshot_id,
        envelope=checked_envelope,
        provider_profile=provider_profile,
        adapter_behavior=LOCAL_ADAPTER_BEHAVIORS[provider_profile],
        payload=checked_export,
        payload_bytes=len(serialized),
        archive_members=checked_export["archive_members"],
        conversation_count=1,
        message_count=len(messages),
        attachment_count=len(attachments),
        captured_at=captured_at,
        synthetic_fixture=False,
    )
    observation = {
        "source_bytes_read": bytes_read,
        "source_lines_read": source_lines_read,
        "stop_reason": stop_reason,
        "observed_top_level_key_sets": [list(keys) for keys in sorted(observed_key_sets)],
        "attachment_metadata_types": sorted({item["type"] for item in attachments}),
        "source_mutations": 0,
        "credential_cookie_storage_reads": 0,
        "external_model_requests": 0,
        "formal_ingest_writes": 0,
    }
    return snapshot, checked_export, observation


def build_readonly_thread_bridge_snapshot(
    *,
    snapshot_id: str,
    envelope: Mapping[str, Any],
    bundle: Mapping[str, Any],
    captured_at: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Normalize one bounded, host-injected official read-only thread bundle.

    The host connector performs the provider read.  This adapter never reads a
    browser profile, cookie, credential, or provider storage and never claims
    that Memorive can directly access ChatGPT's private storage.
    """

    checked_envelope = validate_source_access_envelope(envelope)
    if checked_envelope["access_mode"] != "PROVIDER_OFFICIAL_API_READ":
        raise ConversationContractError(
            "THREAD_BRIDGE_REQUIRES_OFFICIAL_API_READ_ENVELOPE"
        )
    if (
        checked_envelope["provider"] != "OPENAI_CODEX_APP"
        or checked_envelope["surface"] != "CODEX_APP_READONLY_THREAD_BRIDGE"
    ):
        raise ConversationContractError("THREAD_BRIDGE_ENVELOPE_SURFACE_MISMATCH")
    if not isinstance(bundle, Mapping):
        raise ConversationContractError("THREAD_BRIDGE_BUNDLE_MUST_BE_OBJECT")
    checked_bundle = deepcopy(dict(bundle))
    if set(checked_bundle) != _THREAD_BRIDGE_BUNDLE_KEYS:
        raise ConversationContractError("THREAD_BRIDGE_BUNDLE_EXACT_KEYS_MISMATCH")
    if checked_bundle["provider_profile"] != CODEX_APP_THREAD_BRIDGE_PROFILE:
        raise ConversationContractError("THREAD_BRIDGE_PROFILE_NOT_ADMITTED")
    if checked_bundle["thread_kind"] not in _THREAD_BRIDGE_KINDS:
        raise ConversationContractError("THREAD_BRIDGE_KIND_INVALID")

    thread_id = _bridge_text(checked_bundle["thread_id"], "thread_bridge.thread_id")
    capture_actor_id = _bridge_text(
        checked_bundle["capture_actor_id"], "thread_bridge.capture_actor_id"
    )
    identity_claim = checked_bundle["source_equals_capture_actor"]
    if not isinstance(identity_claim, bool):
        raise ConversationContractError("THREAD_BRIDGE_IDENTITY_CLAIM_MUST_BE_BOOLEAN")
    if identity_claim != (thread_id == capture_actor_id):
        raise ConversationContractError("THREAD_BRIDGE_IDENTITY_BINDING_MISMATCH")
    if identity_claim:
        raise ConversationContractError("THREAD_BRIDGE_SELF_SOURCE_FORBIDDEN")

    for field in ("has_more", "source_truncated"):
        if not isinstance(checked_bundle[field], bool):
            raise ConversationContractError(
                f"THREAD_BRIDGE_{field.upper()}_MUST_BE_BOOLEAN"
            )
    if checked_bundle["has_more"] and not checked_bundle["source_truncated"]:
        raise ConversationContractError("THREAD_BRIDGE_TRUNCATION_CLAIM_MISMATCH")

    raw_messages = checked_bundle["messages"]
    if isinstance(raw_messages, (str, bytes)) or not isinstance(raw_messages, Sequence):
        raise ConversationContractError("THREAD_BRIDGE_MESSAGES_MUST_BE_ARRAY")
    if not raw_messages:
        raise ConversationContractError("THREAD_BRIDGE_MESSAGES_MUST_NOT_BE_EMPTY")
    ceiling = checked_envelope["ceiling"]
    if ceiling["conversations"] < 1:
        raise ConversationContractError("CAPTURE_CONVERSATION_CEILING_EXCEEDED")
    if len(raw_messages) > ceiling["messages"]:
        raise ConversationContractError("CAPTURE_MESSAGE_CEILING_EXCEEDED")

    messages: list[dict[str, Any]] = []
    message_ids: set[str] = set()
    previous_id: str | None = None
    for index, raw_message in enumerate(raw_messages, start=1):
        if not isinstance(raw_message, Mapping):
            raise ConversationContractError("THREAD_BRIDGE_MESSAGE_MUST_BE_OBJECT")
        item = deepcopy(dict(raw_message))
        if set(item) != _THREAD_BRIDGE_MESSAGE_KEYS:
            raise ConversationContractError(
                "THREAD_BRIDGE_MESSAGE_EXACT_KEYS_MISMATCH"
            )
        message_id = _bridge_text(
            item["message_id"], f"thread_bridge.messages[{index}].message_id"
        )
        if message_id in message_ids:
            raise ConversationContractError("THREAD_BRIDGE_MESSAGE_IDS_MUST_BE_UNIQUE")
        message_ids.add(message_id)
        if item["role"] not in {"user", "assistant"}:
            raise ConversationContractError("THREAD_BRIDGE_MESSAGE_ROLE_INVALID")
        text = _bridge_message_text(
            item["text"], f"thread_bridge.messages[{index}].text"
        )
        created_at = _bridge_timestamp(
            item["created_at"], f"thread_bridge.messages[{index}].created_at"
        )
        messages.append(
            {
                "message_id": message_id,
                "parent_id": previous_id,
                "role": item["role"],
                "blocks": [{"type": "text", "text": text, "metadata": {}}],
                "created_at": created_at,
                "status": "complete",
                "edit_of": None,
                "regeneration_group": None,
            }
        )
        previous_id = message_id

    export = {
        "provider_profile": CODEX_APP_THREAD_BRIDGE_PROFILE,
        "conversations": [
            {
                "conversation_id": thread_id,
                "title": "PRIVATE_TITLE_WITHHELD",
                "messages": messages,
                "attachments": [],
            }
        ],
        "archive_members": [],
    }
    checked_export = validate_provider_export(export)
    serialized = json.dumps(
        checked_export, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(serialized) > ceiling["content_bytes"]:
        raise ConversationContractError("CAPTURE_BYTE_CEILING_EXCEEDED")

    snapshot = make_export_snapshot(
        snapshot_id=snapshot_id,
        envelope=checked_envelope,
        provider_profile=CODEX_APP_THREAD_BRIDGE_PROFILE,
        adapter_behavior=THREAD_BRIDGE_BEHAVIOR,
        payload=checked_export,
        payload_bytes=len(serialized),
        archive_members=[],
        conversation_count=1,
        message_count=len(messages),
        attachment_count=0,
        captured_at=captured_at,
        synthetic_fixture=False,
    )
    observation = {
        "thread_kind": checked_bundle["thread_kind"],
        "nonself_source": True,
        "source_has_more": checked_bundle["has_more"],
        "source_truncated": checked_bundle["source_truncated"],
        "selected_message_count": len(messages),
        "normalized_content_bytes": len(serialized),
        "source_mutations": 0,
        "credential_cookie_storage_reads": 0,
        "attachment_body_reads": 0,
        "external_model_requests": 0,
        "formal_ingest_writes": 0,
    }
    return snapshot, checked_export, observation


def build_visible_ui_thread_bridge_snapshot(
    *,
    snapshot_id: str,
    envelope: Mapping[str, Any],
    bundle: Mapping[str, Any],
    captured_at: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Normalize one host-observed, bounded visible-UI conversation slice.

    The host browser controller supplies only visible user/assistant text. This
    adapter never reads browser storage, cookies, credentials, attachments, or
    provider-private endpoints, and it never upgrades a visible slice to a
    complete-history claim.
    """

    checked_envelope = validate_source_access_envelope(envelope)
    if checked_envelope["access_mode"] != "VISIBLE_AUTHENTICATED_UI_READONLY_CAPTURE":
        raise ConversationContractError(
            "VISIBLE_UI_BRIDGE_REQUIRES_VISIBLE_READONLY_ENVELOPE"
        )
    if not isinstance(bundle, Mapping):
        raise ConversationContractError("VISIBLE_UI_BUNDLE_MUST_BE_OBJECT")
    checked_bundle = deepcopy(dict(bundle))
    if set(checked_bundle) != _VISIBLE_UI_BUNDLE_KEYS:
        raise ConversationContractError("VISIBLE_UI_BUNDLE_EXACT_KEYS_MISMATCH")

    provider_profile = checked_bundle["provider_profile"]
    if provider_profile not in VISIBLE_UI_BRIDGE_BEHAVIORS:
        raise ConversationContractError("VISIBLE_UI_PROFILE_NOT_ADMITTED")
    behavior = VISIBLE_UI_BRIDGE_BEHAVIORS[provider_profile]
    if (
        checked_envelope["provider"] != behavior["provider"]
        or checked_envelope["surface"] != behavior["surface"]
    ):
        raise ConversationContractError("VISIBLE_UI_ENVELOPE_PROVIDER_SURFACE_MISMATCH")
    if checked_bundle["boundary_method"] != _VISIBLE_UI_BOUNDARY_METHOD:
        raise ConversationContractError("VISIBLE_UI_BOUNDARY_METHOD_INVALID")
    observed_at = _bridge_timestamp(
        checked_bundle["observed_at"], "visible_ui.observed_at"
    )
    if not isinstance(checked_bundle["source_truncated"], bool):
        raise ConversationContractError("VISIBLE_UI_SOURCE_TRUNCATED_MUST_BE_BOOLEAN")

    conversation_id = _bridge_text(
        checked_bundle["conversation_id"], "visible_ui.conversation_id"
    )
    raw_messages = checked_bundle["messages"]
    if isinstance(raw_messages, (str, bytes)) or not isinstance(raw_messages, Sequence):
        raise ConversationContractError("VISIBLE_UI_MESSAGES_MUST_BE_ARRAY")
    if not raw_messages:
        raise ConversationContractError("VISIBLE_UI_MESSAGES_MUST_NOT_BE_EMPTY")
    ceiling = checked_envelope["ceiling"]
    if ceiling["conversations"] < 1:
        raise ConversationContractError("CAPTURE_CONVERSATION_CEILING_EXCEEDED")
    if len(raw_messages) > ceiling["messages"]:
        raise ConversationContractError("CAPTURE_MESSAGE_CEILING_EXCEEDED")

    messages: list[dict[str, Any]] = []
    message_ids: set[str] = set()
    previous_id: str | None = None
    for index, raw_message in enumerate(raw_messages, start=1):
        if not isinstance(raw_message, Mapping):
            raise ConversationContractError("VISIBLE_UI_MESSAGE_MUST_BE_OBJECT")
        item = deepcopy(dict(raw_message))
        if set(item) != _VISIBLE_UI_MESSAGE_KEYS:
            raise ConversationContractError("VISIBLE_UI_MESSAGE_EXACT_KEYS_MISMATCH")
        message_id = _bridge_text(
            item["message_id"], f"visible_ui.messages[{index}].message_id"
        )
        if message_id in message_ids:
            raise ConversationContractError("VISIBLE_UI_MESSAGE_IDS_MUST_BE_UNIQUE")
        message_ids.add(message_id)
        if item["role"] not in {"user", "assistant"}:
            raise ConversationContractError("VISIBLE_UI_MESSAGE_ROLE_INVALID")
        text = _bridge_message_text(
            item["text"], f"visible_ui.messages[{index}].text"
        )
        messages.append(
            {
                "message_id": message_id,
                "parent_id": previous_id,
                "role": item["role"],
                "blocks": [{"type": "text", "text": text, "metadata": {}}],
                "created_at": observed_at,
                "status": "complete",
                "edit_of": None,
                "regeneration_group": None,
            }
        )
        previous_id = message_id

    checked_export = validate_provider_export(
        {
            "provider_profile": provider_profile,
            "conversations": [
                {
                    "conversation_id": conversation_id,
                    "title": "PRIVATE_TITLE_WITHHELD",
                    "messages": messages,
                    "attachments": [],
                }
            ],
            "archive_members": [],
        }
    )
    serialized = json.dumps(
        checked_export,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(serialized) > ceiling["content_bytes"]:
        raise ConversationContractError("CAPTURE_BYTE_CEILING_EXCEEDED")

    snapshot = make_export_snapshot(
        snapshot_id=snapshot_id,
        envelope=checked_envelope,
        provider_profile=provider_profile,
        adapter_behavior=behavior,
        payload=checked_export,
        payload_bytes=len(serialized),
        archive_members=[],
        conversation_count=1,
        message_count=len(messages),
        attachment_count=0,
        captured_at=captured_at,
        synthetic_fixture=False,
    )
    observation = {
        "provider_profile": provider_profile,
        "visible_only": True,
        "source_truncated": checked_bundle["source_truncated"],
        "boundary_method": checked_bundle["boundary_method"],
        "provider_timestamp_available": False,
        "selected_message_count": len(messages),
        "normalized_content_bytes": len(serialized),
        "source_mutations": 0,
        "credential_cookie_storage_reads": 0,
        "attachment_body_reads": 0,
        "external_model_requests": 0,
        "formal_ingest_writes": 0,
    }
    return snapshot, checked_export, observation


def profile_admission(profile: str) -> str:
    if profile == SYNTHETIC_PROFILE:
        return "ADMITTED"
    if (
        profile in LOCAL_ADAPTER_BEHAVIORS
        or profile == CODEX_APP_THREAD_BRIDGE_PROFILE
        or profile in VISIBLE_UI_BRIDGE_BEHAVIORS
    ):
        return "ADMITTED_WITH_LIMITS"
    return "PROFILE_REQUIRED"


__all__ = [
    "ADAPTER_BEHAVIOR",
    "ADMITTED_PROFILES",
    "CLAUDE_CODE_LOCAL_PROFILE",
    "CODEX_APP_THREAD_BRIDGE_PROFILE",
    "CODEX_LOCAL_PROFILE",
    "DEEPSEEK_EDGE_VISIBLE_UI_PROFILE",
    "LOCAL_ADAPTER_BEHAVIORS",
    "SYNTHETIC_PROFILE",
    "THREAD_BRIDGE_BEHAVIOR",
    "VISIBLE_UI_BRIDGE_BEHAVIORS",
    "build_readonly_thread_bridge_snapshot",
    "build_visible_ui_thread_bridge_snapshot",
    "build_synthetic_snapshot",
    "capture_local_jsonl_snapshot",
    "profile_admission",
    "validate_provider_export",
    "validate_synthetic_export",
]
