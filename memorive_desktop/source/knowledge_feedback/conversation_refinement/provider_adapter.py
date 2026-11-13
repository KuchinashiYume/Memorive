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
]
