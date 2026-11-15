from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ATTEMPT_ROOT / "product" / "desktop"))

from memorive_local_models.product_controller import LocalModelProductController
from memorive_sessions.edge_visible_ui import EdgeDeepSeekVisibleUIReader
from memorive_sessions.product_controller import SessionProductError, SessionsProductController
from runtime import ProductApi


WEB_ROOT = ATTEMPT_ROOT / "product" / "web"


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


class _FragmentedReader:
    provider_profile = "KIMI_EDGE_VISIBLE_UI_V1"

    def capture_visible_conversation(self) -> dict[str, object]:
        rows = [
            ("user", "A real user request"),
            ("assistant", "First answer paragraph."),
            ("user", "INLINE RICH HEADING"),
            ("assistant", "Continuation of the same rich answer."),
        ]
        return {
            "provider_profile": self.provider_profile,
            "conversation_id": "fragmented-visible-001",
            "messages": [
                {"message_id": f"m-{index}", "role": role, "text": text}
                for index, (role, text) in enumerate(rows)
            ],
            "observed_at": "2026-08-28T00:00:00Z",
            "source_truncated": False,
            "boundary_method": "HOST_OBSERVED_VISIBLE_UI_FULL_SCROLL_V2",
        }


class _NoReliableUserReader(_FragmentedReader):
    provider_profile = "GEMINI_EDGE_VISIBLE_UI_V1"

    def capture_visible_conversation(self) -> dict[str, object]:
        value = super().capture_visible_conversation()
        value["conversation_id"] = "no-reliable-user-001"
        value["messages"] = [
            {"message_id": "a-1", "role": "assistant", "text": "Long assistant paragraph one."},
            {"message_id": "u-1", "role": "user", "text": "INLINE LABEL"},
            {"message_id": "a-2", "role": "assistant", "text": "Long assistant paragraph two."},
        ]
        return value


def _controller(tmp_path: Path, *, readers: object | None = None) -> SessionsProductController:
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
        visible_ui_reader=readers,
    )


def _conversation(provider: str, index: int, conversation_at: str) -> dict[str, object]:
    hosts = {
        "deepseek": "chat.deepseek.com",
        "gemini": "gemini.google.com",
        "kimi": "www.kimi.com",
    }
    return {
        "provider_id": provider,
        "provider_session_id": f"{provider}-{index}",
        "title": f"{provider} conversation {index}",
        "conversation_at": conversation_at,
        "observed_at": "2026-08-28T00:00:00Z",
        "source_url": f"https://{hosts[provider]}/chat/{index}",
        "capture_method": "VISIBLE_DOM_FULL_SCROLL_V2",
        "source_truncated": False,
        "messages": [
            {"message_id": f"u-{index}", "role": "user", "text": "user message"},
            {"message_id": f"a-{index}", "role": "assistant", "text": "assistant message"},
        ],
    }


def _bundle(conversations: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "MEMORIVE_BROWSER_SESSION_BUNDLE_V2",
        "source": "MEMORIVE_BROWSER_COMPANION_V2",
        "generated_at": "2026-08-28T00:00:00Z",
        "conversations": conversations,
    }


def test_visible_ui_rich_fragments_are_folded_into_the_surrounding_assistant_turn(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, readers={"kimi": _FragmentedReader()})
    controller.call("sessions.refresh", {})
    row = controller.call("sessions.list", {"source": "kimi"})["rows"][0]
    detail = controller.call("sessions.get", {"local_projection_id": row["local_projection_id"]})

    assert row["title"] == "A real user request"
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert "INLINE RICH HEADING" in detail["messages"][1]["content"]
    assert "Continuation of the same rich answer." in detail["messages"][1]["content"]


def test_visible_ui_does_not_use_an_unreliable_inline_fragment_as_the_title(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, readers={"gemini": _NoReliableUserReader()})
    controller.call("sessions.refresh", {})
    row = controller.call("sessions.list", {"source": "gemini"})["rows"][0]

    assert "INLINE LABEL" not in row["title"]
    assert row["title"].startswith("Gemini 网页只读会话")


def test_refinement_requires_an_explicitly_configured_model(tmp_path: Path) -> None:
    controller = _controller(tmp_path, readers={"kimi": _FragmentedReader()})
    controller.call("sessions.refresh", {})
    row = controller.call("sessions.list", {"source": "kimi"})["rows"][0]
    projection = controller.call("sessions.refinement_settings_get", {})

    assert projection["configured"] is False
    assert projection["execution_mode"] == "MODEL_NOT_CONFIGURED"
    with pytest.raises(SessionProductError, match="SESSION_REFINEMENT_MODEL_NOT_CONFIGURED"):
        controller.call("sessions.refine", {"local_projection_id": row["local_projection_id"]})


def test_local_model_discovery_verifies_each_saved_loopback_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = LocalModelProductController(tmp_path / "local-models")
    called: list[str] = []

    def fake_verify(endpoint_id: str) -> dict[str, object]:
        called.append(endpoint_id)
        return {
            "endpoint_id": endpoint_id,
            "service_reachable": endpoint_id == sorted(controller._load()["endpoints"])[0],
            "models": ["local-test-model"] if not called[:-1] else [],
            "inference_run": False,
            "status": "MODEL_LISTED_INFERENCE_NOT_RUN",
        }

    monkeypatch.setattr(controller, "_verify", fake_verify)
    receipt = controller.call("local_models.discover", {})

    assert len(called) == 3
    assert receipt["checked_endpoint_count"] == 3
    assert receipt["inference_calls"] == 0
    assert receipt["external_network_calls"] == 0


def test_browser_companion_indexes_history_titles_and_captures_canonical_turns() -> None:
    content = (
        ATTEMPT_ROOT / "source" / "memorive_browser_companion" / "extension" / "content.js"
    ).read_text(encoding="utf-8")

    assert "indexRecentSessions" in content
    assert "titleForHistoryNode" in content
    assert "captureConversation" in content


def test_visible_ui_drops_nested_rich_text_children_before_geometry_role_merge() -> None:
    nodes = [
        {
            "role": "assistant",
            "text": "Answer heading\n\nAnswer paragraph",
            "top": 100.0,
            "left": 100.0,
            "width": 600.0,
            "height": 180.0,
            "right": 700.0,
            "bottom": 280.0,
        },
        {
            "role": "user",
            "text": "Answer heading",
            "top": 120.0,
            "left": 510.0,
            "width": 170.0,
            "height": 26.0,
            "right": 680.0,
            "bottom": 146.0,
        },
        {
            "role": "user",
            "text": "Actual next user message",
            "top": 320.0,
            "left": 560.0,
            "width": 310.0,
            "height": 60.0,
            "right": 870.0,
            "bottom": 380.0,
        },
    ]

    canonical = EdgeDeepSeekVisibleUIReader._canonical_visual_nodes(nodes)

    assert [row["text"] for row in canonical] == [
        "Answer heading\n\nAnswer paragraph",
        "Actual next user message",
    ]
    assert [row["role"] for row in canonical] == ["assistant", "user"]


class _ApiSettingsService:
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        assert method == "settings.get_state"
        assert params == {}
        return {
            "settings": {
                "model_services": [
                    {
                        "config_id": "deepseek-primary",
                        "provider": "DeepSeek",
                        "credential_ref": "cred-deepseek-primary",
                        "model_name": "deepseek-v4",
                        "tier": "flash",
                        "connection_status": "UNVERIFIED",
                    }
                ],
                "credential_references": [
                    {
                        "credential_ref": "cred-deepseek-primary",
                        "status": "STORED_UNVERIFIED",
                    }
                ],
            }
        }


def test_api_readiness_uses_real_settings_model_services_without_reading_key() -> None:
    api = ProductApi.__new__(ProductApi)
    api._service = _ApiSettingsService()

    receipt = api.api_call_readiness()

    assert receipt["configured_profile_count"] == 1
    assert receipt["configuration_ready_count"] == 1
    assert receipt["profiles"][0]["credential_reference_status"] == "STORED_UNVERIFIED"
    assert receipt["request_sent"] is False
    assert receipt["credential_value_read"] is False


