from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from memorive.sessions.companion.incremental_cursor import (  # noqa: E402
    build_incremental_cursor,
)


class _Archive:
    def __init__(self, per_provider: int = 55):
        self.rows = {
            provider: [
                {"provider_conversation_id": f"{provider}-private-session-{index:03d}"}
                for index in range(per_provider)
            ]
            for provider in ("deepseek", "gemini", "kimi")
        }

    def list_sessions(
        self, *, provider: str | None = None, limit: int = 20, search: str = ""
    ) -> list[dict[str, Any]]:
        assert provider is not None
        assert search == ""
        return self.rows[provider][:limit]

    def get_messages(
        self, provider: str, provider_conversation_id: str
    ) -> list[dict[str, str]]:
        return [
            {"role": "user", "content": f"{provider_conversation_id} 问题🙂"},
            {"role": "assistant", "content": "回答"},
        ]


def _decode(token: str) -> dict[str, Any]:
    padding = "=" * ((4 - len(token) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(token + padding).decode("utf-8"))


def test_incremental_cursor_is_bounded_hash_only_and_three_provider_complete() -> None:
    archive = _Archive()

    cursor = build_incremental_cursor(archive)

    assert cursor is not None
    assert cursor["schema_version"] == "DesktopBrowserSessionIncrementalCursor-v1"
    assert cursor["providers"] == ["deepseek", "gemini", "kimi"]
    assert cursor["known_session_count"] == 150
    assert cursor["overlap_fingerprint_count"] == 6
    assert len(cursor["token"]) <= 8192
    assert re.fullmatch(r"[A-Za-z0-9_-]+", cursor["token"])
    payload = _decode(cursor["token"])
    assert payload == cursor["payload"]
    assert payload["schema_version"] == "MEMORIVE_BROWSER_SESSION_CURSOR_V1"
    for provider_id, row in payload["providers"].items():
        assert len(row["known_session_hashes"]) == 50
        assert len(row["overlap_fingerprints"]) == 2
        assert all(re.fullmatch(r"[a-f0-9]{24}", value) for value in row["known_session_hashes"])
        assert all(re.fullmatch(r"[a-f0-9]{64}", value) for value in row["overlap_fingerprints"].values())
        raw_id = f"{provider_id}-private-session-000"
        expected_identity = hashlib.sha256(
            f"{provider_id}\x00{raw_id}".encode("utf-8")
        ).hexdigest()[:24]
        assert row["known_session_hashes"][0] == expected_identity
        assert raw_id not in json.dumps(payload, ensure_ascii=False)
    assert cursor["privacy_projection"] == {
        "raw_session_ids": 0,
        "titles": 0,
        "message_content": 0,
        "account_identity": 0,
    }


def test_incremental_fingerprint_matches_javascript_utf16_string_length() -> None:
    archive = _Archive(per_provider=1)

    cursor = build_incremental_cursor(archive)

    assert cursor is not None
    provider_id = "deepseek"
    raw_id = "deepseek-private-session-000"
    identity = hashlib.sha256(f"{provider_id}\x00{raw_id}".encode()).hexdigest()[:24]
    # JavaScript sees the emoji as two UTF-16 code units.
    first = f"{raw_id} 问题🙂"
    expected_rows = [
        f"user\x1f{len(first.encode('utf-16-le')) // 2}\x1f{first}",
        "assistant\x1f2\x1f回答",
    ]
    expected = hashlib.sha256("\x1e".join(expected_rows).encode("utf-8")).hexdigest()
    assert (
        cursor["payload"]["providers"][provider_id]["overlap_fingerprints"][identity]
        == expected
    )


def test_incremental_cursor_requires_a_local_baseline_for_every_provider() -> None:
    archive = _Archive(per_provider=1)
    archive.rows["gemini"] = []

    assert build_incremental_cursor(archive) is None
