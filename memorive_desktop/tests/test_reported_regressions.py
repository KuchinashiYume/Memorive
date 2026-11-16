from __future__ import annotations

import json
from pathlib import Path

from memorive.sessions.controller import SessionsProductController


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"
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


class _VisibleReader:
    def __init__(self, provider: str, text: str = "当前可见会话") -> None:
        self.provider = provider
        self.text = text

    def capture_visible_conversation(self) -> dict[str, object]:
        return {
            "provider_profile": f"{self.provider.upper()}_EDGE_VISIBLE_UI_V1",
            "conversation_id": f"{self.provider}-visible-001",
            "messages": [
                {"message_id": "u1", "role": "user", "text": self.text},
                {"message_id": "a1", "role": "assistant", "text": "完整回复"},
            ],
            "observed_at": "2026-08-28T00:00:00Z",
            "source_truncated": False,
            "boundary_method": "HOST_OBSERVED_VISIBLE_UI_FULL_SCROLL_V2",
        }


class _BrokenReader:
    def capture_visible_conversation(self) -> dict[str, object]:
        raise RuntimeError("VISIBLE_WINDOW_TEMPORARILY_UNAVAILABLE")


class _LocalModelRegistry:
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        assert method == "local_models.list"
        assert params == {}
        return {
            "recognized_models": [
                {
                    "profile_ref": "local:000000000000000000000001",
                    "display_name": "[本地] qwen3:8b",
                    "model_name": "qwen3:8b",
                    "connection_status": "AVAILABLE",
                    "execution_eligible": True,
                    "exact_identity_available": True,
                    "endpoint_kind": "ollama",
                    "model_digest": "A" * 64,
                    "structured_chat_adapter": "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1",
                },
                {
                    "profile_ref": "local:000000000000000000000002",
                    "display_name": "[本地] deepseek-r1:14b",
                    "model_name": "deepseek-r1:14b",
                    "connection_status": "AVAILABLE",
                    "execution_eligible": True,
                    "exact_identity_available": True,
                    "endpoint_kind": "ollama",
                    "model_digest": "B" * 64,
                    "structured_chat_adapter": "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1",
                },
                {
                    "profile_ref": "local:000000000000000000000003",
                    "display_name": "[本地] gemma-3-12b",
                    "model_name": "gemma-3-12b",
                    "connection_status": "AVAILABLE",
                    "execution_eligible": False,
                    "exact_identity_available": False,
                    "endpoint_kind": "openai_compatible",
                    "model_digest": None,
                    "structured_chat_adapter": None,
                },
            ]
        }


def _controller(
    tmp_path: Path,
    *,
    readers: object | None = None,
    local_models: object | None = None,
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
        visible_ui_reader=readers,
        local_model_registry=local_models,
    )


def test_visible_ui_rows_can_be_hidden_in_batch_without_remote_mutation(
    tmp_path: Path,
) -> None:
    controller = _controller(
        tmp_path,
        readers={"kimi": _VisibleReader("kimi")},
    )
    controller.call("sessions.refresh", {})
    rows = controller.call("sessions.list", {"source": "kimi"})["rows"]

    assert len(rows) == 1
    assert rows[0]["delete_capability"] == "hide_local_projection"
    receipt = controller.call(
        "sessions.bulk_delete_local",
        {"local_projection_ids": [rows[0]["local_projection_id"]]},
    )
    assert receipt["status"] == "PASS"
    assert receipt["remote_session_mutations"] == 0


def test_deepseek_snapshot_is_restored_after_transient_reader_failure(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    first = _controller(tmp_path, readers={"deepseek": _VisibleReader("deepseek")})
    first.call("sessions.refresh", {})
    assert first.call("sessions.list", {"source": "deepseek"})["row_count"] == 1

    second = SessionsProductController(
        _HealthyService(),
        object(),
        ATTEMPT_ROOT / "contracts" / "analysis",
        ATTEMPT_ROOT / "fixtures_t08",
        state_root,
        source_roots={
            "codex_home": tmp_path / "codex-home",
            "claude_home": tmp_path / "claude-home",
        },
        visible_ui_reader={"deepseek": _BrokenReader()},
    )
    second.call("sessions.refresh", {})
    rows = second.call("sessions.list", {"source": "deepseek"})["rows"]

    assert len(rows) == 1
    assert rows[0]["title"] == "当前可见会话"
    detail = second.call(
        "sessions.get", {"local_projection_id": rows[0]["local_projection_id"]}
    )
    assert [row["content"] for row in detail["messages"]] == ["当前可见会话", "完整回复"]


def test_kimi_current_visible_conversation_is_projected_read_only(tmp_path: Path) -> None:
    controller = _controller(tmp_path, readers={"kimi": _VisibleReader("kimi", "Kimi 用户问题")})
    controller.call("sessions.refresh", {})
    rows = controller.call("sessions.list", {"source": "kimi"})["rows"]

    assert len(rows) == 1
    assert rows[0]["source_name"] == "Kimi 网页"
    assert rows[0]["captured_at"] == "UNKNOWN"
    detail = controller.call(
        "sessions.get", {"local_projection_id": rows[0]["local_projection_id"]}
    )
    assert detail["messages"][0]["content"] == "Kimi 用户问题"
    assert detail["completeness"]["status"] == "COMPLETE"


def test_edge_visible_ui_reader_has_portable_provider_profiles() -> None:
    module = (SOURCE_ROOT / "memorive" / "sessions" / "edge_visible_ui.py").read_text(encoding="utf-8")

    assert "class EdgeKimiVisibleUIReader" in module
    assert "class EdgeGeminiVisibleUIReader" in module
    assert '"www.kimi.com"' in module
    assert '"gemini.google.com"' in module


def test_refinement_options_include_every_verified_local_model(tmp_path: Path) -> None:
    controller = _controller(tmp_path, local_models=_LocalModelRegistry())
    projection = controller.call("sessions.refinement_settings_get", {})
    options = projection["model_options"]

    assert [row["display_name"] for row in options] == [
        "[本地] qwen3:8b",
        "[本地] deepseek-r1:14b",
        "[本地] gemma-3-12b（本地模型当前未通过精炼执行资格校验）",
    ]
    assert [row["execution_eligible"] for row in options] == [True, True, False]
    assert len({row["profile_ref"] for row in options}) == 3
    selected = options[1]["profile_ref"]
    receipt = controller.call(
        "sessions.refinement_settings_save",
        {"profile_ref": selected, "include_tasks": True, "include_risks": True},
    )
    assert receipt["settings"]["profile_ref"] == selected


def test_runtime_enables_visible_readers_and_migrates_part11_preferences() -> None:
    runtime = (ATTEMPT_ROOT / "source" / "memorive" / "desktop" / "api.py").read_text(encoding="utf-8")

    assert "legacy_deepseek_visible_ui_projection=False" not in runtime
    assert "EdgeKimiVisibleUIReader" in runtime
    # The desk assistant's preferences and their upgrade live in memorive/desktop/assistant/desktop.py.
    assistant = (ATTEMPT_ROOT / "source" / "memorive" / "desktop" / "assistant" / "desktop.py").read_text(encoding="utf-8")
    assert "DesktopAssistantPreferences-v3" in assistant
    assert "logo_left_action" not in runtime + assistant
    assert "logo_right_action" not in runtime + assistant
    assert '"shortcut_entries"' in assistant
    assert '"reminder_scope"' in assistant
    assert '"hide_sensitive_names"' in assistant
