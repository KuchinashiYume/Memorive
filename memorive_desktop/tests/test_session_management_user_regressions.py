from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from memorive.sessions.controller import SessionsProductController


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"


class _HealthyService:
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "service.health":
            return {
                "schema_version": "ApplicationServiceHealth-v1",
                "protocol_version": "1.0",
                "adapter_kind": "sample-test-service",
                "request_count": 0,
                "auth_rejection_count": 0,
                "connection_count": 1,
                "uptime_seconds": 1,
                "external_network_calls": 0,
                "external_model_calls": 0,
                "credential_value_reads": 0,
            }
        if method == "settings.get_state":
            return {"api_profiles": []}
        raise AssertionError(method)


def _controller(tmp_path: Path) -> SessionsProductController:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    codex_home.mkdir()
    claude_home.mkdir()
    return SessionsProductController(
        _HealthyService(),
        object(),
        ATTEMPT_ROOT / "contracts" / "analysis",
        ATTEMPT_ROOT / "fixtures_t08",
        tmp_path / "state",
        source_roots={"codex_home": codex_home, "claude_home": claude_home},
    )


def _conversation(provider: str, index: int, *, conversation_at: str) -> dict[str, object]:
    hosts = {
        "deepseek": "chat.deepseek.com",
        "gemini": "gemini.google.com",
        "kimi": "www.kimi.com",
    }
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "provider_id": provider,
        "provider_session_id": f"{provider}-session-{index:03d}",
        "title": f"{provider} 会话 {index:03d}",
        "conversation_at": conversation_at,
        "observed_at": observed_at,
        "source_url": f"https://{hosts[provider]}/chat/{index:03d}",
        "capture_method": "VISIBLE_DOM_FULL_SCROLL_V2",
        "source_truncated": False,
        "messages": [
            {"message_id": f"{provider}-{index}-u", "role": "user", "text": "用户正文"},
            {"message_id": f"{provider}-{index}-a", "role": "assistant", "text": "助手正文"},
        ],
    }


def _bundle(conversations: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "MEMORIVE_BROWSER_SESSION_BUNDLE_V2",
        "source": "MEMORIVE_BROWSER_COMPANION_V2",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "conversations": conversations,
    }


def test_session_list_limits_each_provider_to_latest_20_or_50(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    now = datetime.now(timezone.utc)
    conversations: list[dict[str, object]] = []
    for provider in ("deepseek", "gemini", "kimi"):
        for index in range(60):
            occurred = (now - timedelta(minutes=index)).isoformat(timespec="seconds").replace("+00:00", "Z")
            conversations.append(_conversation(provider, index, conversation_at=occurred))
    controller.import_browser_bundle(_bundle(conversations))

    twenty = controller.call(
        "sessions.list", {"source": "all", "limit_per_source": 20}
    )
    fifty = controller.call(
        "sessions.list", {"source": "all", "limit_per_source": 50}
    )

    assert Counter(row["provider_id"] for row in twenty["rows"]) == {
        "deepseek": 20,
        "gemini": 20,
        "kimi": 20,
    }
    assert Counter(row["provider_id"] for row in fifty["rows"]) == {
        "deepseek": 50,
        "gemini": 50,
        "kimi": 50,
    }
    assert twenty["total_count"] == 180
    assert fifty["total_count"] == 180


def test_bulk_management_hides_only_local_projection_and_never_mutates_remote(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    controller.import_browser_bundle(
        _bundle([
            _conversation("deepseek", 1, conversation_at=now),
            _conversation("kimi", 2, conversation_at=now),
        ])
    )
    rows = controller.call(
        "sessions.list", {"source": "all", "limit_per_source": 20}
    )["rows"]
    receipt = controller.call(
        "sessions.bulk_delete_local",
        {"local_projection_ids": [row["local_projection_id"] for row in rows]},
    )

    assert receipt["success_count"] == 2
    assert receipt["blocked_count"] == 0
    assert receipt["remote_session_mutations"] == 0
    assert controller.call(
        "sessions.list", {"source": "all", "limit_per_source": 20}
    )["rows"] == []
    assert len(list((tmp_path / "state" / "browser_sessions_private").glob("*.json"))) == 2


