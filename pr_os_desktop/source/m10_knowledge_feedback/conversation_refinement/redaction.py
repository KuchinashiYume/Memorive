"""Deterministic redaction before any T12 refinement subject."""

from __future__ import annotations

import re
from typing import Any, Mapping

from m10_knowledge_feedback.canonical import make_hashed_payload, verify_hashed_payload

from .contracts import ConversationContractError


_PATTERNS = (
    ("OPENAI_STYLE_KEY", re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")),
    ("BEARER_TOKEN", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~-]{12,}")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("COOKIE_ASSIGNMENT", re.compile(r"(?i)\b(cookie|session|token)\s*[:=]\s*[^\s,;]{6,}")),
)
_RESULT_KEYS = {
    "schema_version",
    "redacted_text",
    "redaction_count",
    "categories",
    "original_text_retained",
    "credential_cookie_storage_value_retained",
    "content_hash",
}


def validate_redaction_result(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "redaction_result")
    except Exception as exc:
        raise ConversationContractError("REDACTION_RESULT_HASH_INVALID") from exc
    if set(result) != _RESULT_KEYS:
        raise ConversationContractError("REDACTION_RESULT_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "P06_T12_REDACTION_RESULT_V1":
        raise ConversationContractError("REDACTION_RESULT_SCHEMA_UNSUPPORTED")
    if not isinstance(result["redacted_text"], str):
        raise ConversationContractError("REDACTED_TEXT_INVALID")
    if isinstance(result["redaction_count"], bool) or not isinstance(result["redaction_count"], int) or result["redaction_count"] < 0:
        raise ConversationContractError("REDACTION_COUNT_INVALID")
    if result["categories"] != sorted(set(result["categories"])):
        raise ConversationContractError("REDACTION_CATEGORIES_INVALID")
    if result["original_text_retained"] is not False or result["credential_cookie_storage_value_retained"] is not False:
        raise ConversationContractError("REDACTION_RETENTION_BOUNDARY_INVALID")
    return result


def redact_text(text: str) -> dict[str, Any]:
    if not isinstance(text, str):
        raise ConversationContractError("REDACTION_INPUT_MUST_BE_TEXT")
    redacted = text
    categories: list[str] = []
    count = 0
    for category, pattern in _PATTERNS:
        redacted, matches = pattern.subn(f"[REDACTED:{category}]", redacted)
        if matches:
            categories.append(category)
            count += matches
    return validate_redaction_result(
        make_hashed_payload(
            {
                "schema_version": "P06_T12_REDACTION_RESULT_V1",
                "redacted_text": redacted,
                "redaction_count": count,
                "categories": sorted(categories),
                "original_text_retained": False,
                "credential_cookie_storage_value_retained": False,
            }
        )
    )


__all__ = ["redact_text", "validate_redaction_result"]
