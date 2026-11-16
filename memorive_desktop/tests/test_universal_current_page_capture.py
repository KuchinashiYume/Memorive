from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from memorive.sessions.controller import SessionProductError, SessionsProductController


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"
EXTENSION_ROOT = ATTEMPT_ROOT / "source" / "memorive" / "sessions" / "companion" / "extension"


class _HealthyService:
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "service.health":
            return {
                "schema_version": "ApplicationServiceHealth-v1",
                "protocol_version": "1.0",
                "adapter_kind": "sample-universal-current-page-test",
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


def _current_page_bundle(*, source_url: str = "https://chat.qwen.ai/c/visible-session") -> dict[str, object]:
    observed = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "schema_version": "MEMORIVE_BROWSER_SESSION_LIBRARY_V4_DRAFT",
        "source": "MEMORIVE_BROWSER_COMPANION_VISIBLE_UI_V4_DRAFT",
        "generated_at": observed,
        "sync_mode": "CURRENT_PAGE",
        "sync_scope": "CURRENT_PAGE",
        "provider_id": "universal",
        "enumerated_count": 1,
        "candidate_count": 1,
        "capture_target": 1,
        "body_read_count": 1,
        "captured_count": 1,
        "unchanged_count": 0,
        "failed_count": 0,
        "skipped_candidate_count": 0,
        "conversations": [
            {
                "provider_id": "universal",
                "provider_session_id": "chat-qwen-ai-visible-session",
                "title": "千问当前会话",
                "created_at": "UNKNOWN",
                "updated_at": "UNKNOWN",
                "recency_bucket": "UNKNOWN",
                "selection_basis": "CURRENT_PAGE_USER_GESTURE",
                "source_recency_rank": 0,
                "observed_at": observed,
                "source_url": source_url,
                "capture_method": "BROWSER_COMPANION_ACTIVE_TAB_V1",
                "capture_status": "PARTIAL",
                "source_truncated": True,
                "top_boundary_reached": False,
                "bottom_boundary_reached": False,
                "error_code": "CURRENT_VISIBLE_INTERFACE_ONLY",
                "messages": [
                    {
                        "message_id": "universal-user-1",
                        "role": "user",
                        "content_kind": "text",
                        "content": "请解释这段代码",
                        "occurred_at": "UNKNOWN",
                    },
                    {
                        "message_id": "universal-assistant-1",
                        "role": "assistant",
                        "content_kind": "text",
                        "content": "这是千问当前界面中可见的回答。",
                        "occurred_at": "UNKNOWN",
                    },
                ],
            }
        ],
        "failures": [],
        "skipped_candidates": [],
    }


def test_universal_current_page_bundle_imports_as_partial_un_source(tmp_path: Path) -> None:
    controller = _controller(tmp_path)

    receipt = controller.import_browser_bundle(_current_page_bundle())
    listed = controller.call(
        "sessions.list",
        {"source": "universal", "search": "", "limit_per_source": 10},
    )

    assert receipt["provider_id"] == "universal"
    assert receipt["verification_status"] == "PARTIAL"
    assert listed["row_count"] == 1
    row = listed["rows"][0]
    assert row["provider_id"] == "universal"
    assert row["source_name"] == "千问网页 · 当前页录入"
    assert row["partial"] is True
    assert row["time_basis"] == "INGESTED_AT"
    detail = controller.call(
        "sessions.get", {"local_projection_id": row["local_projection_id"]}
    )
    assert detail["message_count"] == 2
    assert detail["completeness"]["status"] == "PARTIAL"
    assert [item["role"] for item in detail["messages"]] == ["user", "assistant"]


def test_current_page_bundle_rejects_query_fragment_and_non_https_remote_url(tmp_path: Path) -> None:
    controller = _controller(tmp_path)

    with pytest.raises(SessionProductError, match="BROWSER_CURRENT_PAGE_URL_NOT_SANITIZED"):
        controller.import_browser_bundle(
            _current_page_bundle(source_url="https://chat.qwen.ai/c/visible?token=forbidden")
        )
    with pytest.raises(SessionProductError, match="BROWSER_SITE_NOT_VERIFIED_OFFICIAL"):
        controller.import_browser_bundle(
            _current_page_bundle(source_url="http://chat.qwen.ai/c/visible")
        )


