from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any, Mapping, Protocol, Sequence


PROVIDER_ORDER = ("deepseek", "gemini", "kimi")
KNOWN_SESSION_LIMIT = 50
OVERLAP_FINGERPRINT_LIMIT = 2
CURSOR_TOKEN_LIMIT = 8192


class SessionArchiveLike(Protocol):
    def list_sessions(
        self, *, provider: str | None = None, limit: int = 20, search: str = ""
    ) -> list[dict[str, Any]]: ...

    def get_messages(
        self, provider: str, provider_conversation_id: str
    ) -> list[dict[str, Any]]: ...


class IncrementalCursorError(ValueError):
    """The local archive cannot form a bounded, privacy-safe cursor."""


def _session_identity_sha256(provider_id: str, provider_session_id: str) -> str:
    return hashlib.sha256(
        f"{provider_id}\x00{provider_session_id}".encode("utf-8")
    ).hexdigest()[:24]


def _javascript_string_length(value: str) -> int:
    """Match JavaScript String.length (UTF-16 code units), including emoji."""

    return len(value.encode("utf-16-le")) // 2


def _content_sha256(messages: Sequence[Mapping[str, Any]]) -> str:
    rows: list[str] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or not role or not isinstance(content, str):
            raise IncrementalCursorError("LOCAL_ARCHIVE_MESSAGE_INVALID")
        rows.append(f"{role}\x1f{_javascript_string_length(content)}\x1f{content}")
    if not rows:
        raise IncrementalCursorError("LOCAL_ARCHIVE_MESSAGES_EMPTY")
    return hashlib.sha256("\x1e".join(rows).encode("utf-8")).hexdigest()


def _encode_cursor(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if len(token) > CURSOR_TOKEN_LIMIT or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise IncrementalCursorError("INCREMENTAL_CURSOR_TOKEN_INVALID")
    return token


def build_incremental_cursor(
    archive: SessionArchiveLike,
    *, providers=None,
) -> dict[str, Any] | None:
    """Build a hash-only cursor from the newest local archive rows.

    The extension receives no title, provider session id, message text, path, or
    account data.  Each provider contributes at most 50 identity hashes and two
    content fingerprints.  Missing any provider returns ``None`` so the product
    keeps the bounded five-item preflight instead of pretending an incremental
    baseline exists.
    """

    order = tuple(PROVIDER_ORDER if providers is None else providers)
    if not order or len(order)>3 or len(set(order))!=len(order): return None
    if any(not re.fullmatch(r'(?:deepseek|gemini|kimi|web-[a-f0-9]{16})',x) for x in order):
        raise IncrementalCursorError('INCREMENTAL_CURSOR_PROVIDER_SCOPE_INVALID')
    providers: dict[str, dict[str, Any]] = {}
    total_known = 0
    total_overlap = 0
    for provider_id in order:
        rows = archive.list_sessions(
            provider=provider_id,
            limit=KNOWN_SESSION_LIMIT,
            search="",
        )
        known: list[str] = []
        overlap: dict[str, str] = {}
        seen: set[str] = set()
        for row in rows:
            raw_session_id = row.get("provider_conversation_id")
            if not isinstance(raw_session_id, str) or not raw_session_id.strip():
                continue
            session_id = raw_session_id.strip()
            identity_hash = _session_identity_sha256(provider_id, session_id)
            if identity_hash in seen:
                continue
            seen.add(identity_hash)
            known.append(identity_hash)
            if len(overlap) < OVERLAP_FINGERPRINT_LIMIT:
                messages = archive.get_messages(provider_id, session_id)
                try:
                    overlap[identity_hash] = _content_sha256(messages)
                except IncrementalCursorError:
                    # A malformed local body may not be used as an overlap
                    # fingerprint, but its identity still prevents a full scan.
                    pass
            if len(known) >= KNOWN_SESSION_LIMIT:
                break
        if not known:
            return None
        providers[provider_id] = {
            "known_session_hashes": known,
            "overlap_fingerprints": overlap,
        }
        total_known += len(known)
        total_overlap += len(overlap)

    payload = {
        "schema_version": "MEMORIVE_BROWSER_SESSION_CURSOR_V1",
        "providers": providers,
    }
    return {
        "schema_version": "DesktopBrowserSessionIncrementalCursor-v1",
        "payload": payload,
        "token": _encode_cursor(payload),
        "providers": list(order),
        "known_session_count": total_known,
        "overlap_fingerprint_count": total_overlap,
        "privacy_projection": {
            "raw_session_ids": 0,
            "titles": 0,
            "message_content": 0,
            "account_identity": 0,
        },
    }


__all__ = [
    "CURSOR_TOKEN_LIMIT",
    "IncrementalCursorError",
    "KNOWN_SESSION_LIMIT",
    "OVERLAP_FINGERPRINT_LIMIT",
    "PROVIDER_ORDER",
    "build_incremental_cursor",
]
