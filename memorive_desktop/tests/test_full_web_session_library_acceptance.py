from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ATTEMPT_ROOT / "product" / "desktop"))

from memorive.sessions.controller import SessionProductError, SessionsProductController
from memorive.desktop.api import ProductApi
from browser_companion_support import companion  # noqa: E402


WEB_ROOT = ATTEMPT_ROOT / "product" / "web"
EXTENSION_ROOT = ATTEMPT_ROOT / "source" / "memorive" / "sessions" / "companion" / "extension"


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


class _CurrentPageReader:
    provider_profile = "DEEPSEEK_EDGE_VISIBLE_UI_V1"

    def capture_visible_conversation(self) -> dict[str, object]:
        return {
            "provider_profile": self.provider_profile,
            "conversation_id": "current-page-only",
            "messages": [
                {"message_id": "u-1", "role": "user", "text": "current page user"},
                {"message_id": "a-1", "role": "assistant", "text": "current page answer"},
            ],
            "observed_at": "2026-08-28T00:00:00Z",
            "source_truncated": False,
            "boundary_method": "HOST_OBSERVED_VISIBLE_UI_FULL_SCROLL_V2",
        }


def _controller(
    tmp_path: Path,
    *,
    reader: object | None = None,
    legacy_visible_ui: bool = False,
) -> SessionsProductController:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    codex_home.mkdir(exist_ok=True)
    claude_home.mkdir(exist_ok=True)
    return SessionsProductController(
        _HealthyService(),
        object(),
        ATTEMPT_ROOT / "contracts" / "analysis",
        ATTEMPT_ROOT / "fixtures_t08",
        tmp_path / "state",
        source_roots={"codex_home": codex_home, "claude_home": claude_home},
        visible_ui_reader={"deepseek": reader} if reader else None,
        legacy_deepseek_visible_ui_projection=legacy_visible_ui,
    )


def _conversation(
    provider: str,
    session_id: str,
    *,
    title: str,
    conversation_at: str = "UNKNOWN",
    recency_bucket: str = "LAST_7_DAYS",
    source_recency_rank: int = 0,
    capture_status: str = "COMPLETE",
) -> dict[str, object]:
    hosts = {
        "deepseek": "chat.deepseek.com",
        "gemini": "gemini.google.com",
        "kimi": "www.kimi.com",
    }
    complete = capture_status == "COMPLETE"
    return {
        "provider_id": provider,
        "provider_session_id": session_id,
        "title": title,
        "conversation_at": conversation_at,
        "recency_bucket": recency_bucket,
        "source_recency_rank": source_recency_rank,
        "observed_at": "2026-08-28T00:00:00Z",
        "source_url": f"https://{hosts[provider]}/chat/{session_id}",
        "capture_method": "BROWSER_COMPANION_BACKGROUND_TAB_V3",
        "capture_status": capture_status,
        "source_truncated": not complete,
        "error_code": "" if complete else "CONVERSATION_CAPTURE_FAILED",
        "messages": (
            [
                {"message_id": f"{session_id}-u", "role": "user", "text": "user message"},
                {"message_id": f"{session_id}-a", "role": "assistant", "text": "assistant message"},
            ]
            if complete
            else []
        ),
    }


def _bundle(provider: str, conversations: list[dict[str, object]]) -> dict[str, object]:
    failed = sum(row["capture_status"] != "COMPLETE" for row in conversations)
    return {
        "schema_version": "MEMORIVE_BROWSER_SESSION_LIBRARY_V3",
        "source": "MEMORIVE_BROWSER_COMPANION_LIBRARY_V3",
        "generated_at": "2026-08-28T00:00:00Z",
        "sync_scope": "RECENT_30_DAYS",
        "provider_id": provider,
        "expected_count": len(conversations),
        "captured_count": len(conversations) - failed,
        "failed_count": failed,
        "conversations": conversations,
    }


def test_formal_session_list_excludes_retired_current_page_snapshots(tmp_path: Path) -> None:
    controller = _controller(
        tmp_path,
        reader=_CurrentPageReader(),
        legacy_visible_ui=True,
    )
    controller.call("sessions.refresh", {})
    assert len(controller.call("sessions.list", {"source": "deepseek"})["rows"]) == 1

    controller._legacy_deepseek_visible_ui_projection = False
    controller.call("sessions.refresh", {})

    assert controller.call("sessions.list", {"source": "deepseek"})["rows"] == []


def test_legacy_confirmation_does_not_impersonate_extension_handshake(tmp_path: Path) -> None:
    api = companion()
    api.source_root = ATTEMPT_ROOT / "source"
    api.profile_root = tmp_path / "profile"
    state_path = api._browser_companion_state_path()
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": "DesktopBrowserCompanionSetupState-v1",
                "package_sha256": "OLD-PACKAGE",
                "prepared": True,
                "user_confirmed": True,
                "import_verified": False,
            }
        ),
        encoding="utf-8",
    )

    status = api.browser_companion_status()

    assert status["configured"] is False
    assert status["extension_connected"] is False
    assert status["user_confirmed"] is True
    assert status["package_current"] is False
    assert status["update_available"] is True


