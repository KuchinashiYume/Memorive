from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import threading

import pytest

from memorive.sessions.controller import SessionProductError, SessionsProductController


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
RUN_ROOT = ATTEMPT_ROOT.parents[1]


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
            return {
                "api_profiles": [
                    {
                        "profile_ref": "profile-sonnet",
                        "display_name": "sonnet5",
                    }
                ]
            }
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


def _browser_bundle() -> dict[str, object]:
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    conversations = []
    for provider, host, title in (
        ("deepseek", "chat.deepseek.com", "440大卡食物对照"),
        ("gemini", "gemini.google.com", "Gemini 完整会话"),
        ("kimi", "www.kimi.com", "川沙到浦东机场首班车"),
    ):
        conversations.append(
            {
                "provider_id": provider,
                "provider_session_id": f"{provider}-session-026",
                "title": title,
                "observed_at": observed_at,
                "source_url": f"https://{host}/app/session-026",
                "capture_method": "VISIBLE_DOM_FULL_SCROLL_V1",
                "source_truncated": False,
                "messages": [
                    {"message_id": f"{provider}-u", "role": "user", "text": f"{provider} 用户完整正文"},
                    {"message_id": f"{provider}-a", "role": "assistant", "text": f"{provider} AI 完整正文"},
                ],
            }
        )
    return {
        "schema_version": "MEMORIVE_BROWSER_SESSION_BUNDLE_V1",
        "source": "MEMORIVE_BROWSER_COMPANION_V1",
        "generated_at": observed_at,
        "conversations": conversations,
    }


def test_browser_bundle_imports_complete_deepseek_gemini_and_kimi_sessions(tmp_path: Path) -> None:
    controller = _controller(tmp_path)

    receipt = controller.import_browser_bundle(_browser_bundle())
    bootstrap = controller.call("sessions.bootstrap", {})

    assert receipt["imported_count"] == 3
    assert {row["provider_id"] for row in bootstrap["records"]} == {
        "deepseek",
        "gemini",
        "kimi",
    }
    assert bootstrap["source_count"] == 5
    assert "440大卡食物对照" in {row["title"] for row in bootstrap["records"]}
    assert "川沙到浦东机场首班车" in {row["title"] for row in bootstrap["records"]}
    for row in bootstrap["records"]:
        detail = controller.call(
            "sessions.get", {"local_projection_id": row["local_projection_id"]}
        )
        assert detail["completeness"]["status"] == "COMPLETE"
        assert detail["message_count"] == 2
        assert detail["session"]["delete_capability"] == "none"
    metrics = controller.call("sessions.effect_metrics", {})
    assert metrics["remote_session_mutations"] == 0
    assert metrics["cookie_value_reads"] == 0
    assert metrics["browser_storage_value_reads"] == 0


@pytest.mark.parametrize(
    "forbidden_field",
    ["cookie", "token", "authorization", "api_key", "credential", "local_storage"],
)
def test_browser_bundle_rejects_secret_or_browser_storage_fields(
    tmp_path: Path, forbidden_field: str
) -> None:
    controller = _controller(tmp_path)
    bundle = _browser_bundle()
    bundle[forbidden_field] = "must-not-be-read"
    with pytest.raises(SessionProductError, match="BROWSER_SESSION_BUNDLE_FIELDS_INVALID"):
        controller.import_browser_bundle(bundle)


def test_browser_bundle_rejects_unapproved_provider_host_and_partial_content(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    wrong_host = _browser_bundle()
    wrong_host["conversations"][0]["source_url"] = "https://example.invalid/chat"
    with pytest.raises(SessionProductError, match="BROWSER_SESSION_SOURCE_URL_INVALID"):
        controller.import_browser_bundle(wrong_host)

    partial = _browser_bundle()
    partial["conversations"][0]["source_truncated"] = True
    with pytest.raises(SessionProductError, match="BROWSER_SESSION_INCOMPLETE"):
        controller.import_browser_bundle(partial)


def test_local_model_discovery_and_verification_never_runs_inference_or_external_network(
    tmp_path: Path,
) -> None:
    from memorive.models.local.product_controller import LocalModelProductController, LocalModelProductError

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            payload = json.dumps({"models": [{"name": "qwen2.5:7b"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        controller = LocalModelProductController(tmp_path / "local-model-state")
        endpoint = f"http://127.0.0.1:{server.server_port}"
        saved = controller.call(
            "local_models.endpoint_save",
            {"display_name": "测试本地服务", "kind": "openai_compatible", "endpoint": endpoint},
        )
        verified = controller.call(
            "local_models.verify", {"endpoint_id": saved["endpoint"]["endpoint_id"]}
        )
        assert verified["service_reachable"] is True
        assert verified["model_listed"] is True
        assert verified["models"] == ["qwen2.5:7b"]
        assert verified["inference_run"] is False
        assert verified["status"] == "MODEL_LISTED_INFERENCE_NOT_RUN"
        assert controller.call("local_models.effect_metrics", {}) == {
            "loopback_http_requests": 1,
            "external_network_calls": 0,
            "inference_calls": 0,
            "process_starts": 0,
            "model_downloads": 0,
            "credential_value_reads": 0,
            "status": "PASS",
        }
        with pytest.raises(LocalModelProductError, match="LOCAL_MODEL_ENDPOINT_NOT_LOOPBACK"):
            controller.call(
                "local_models.endpoint_save",
                {"display_name": "禁止外网", "kind": "openai_compatible", "endpoint": "https://example.com"},
            )
    finally:
        server.shutdown()
        server.server_close()


def test_refinement_settings_and_draft_are_real_local_reviewable_outputs(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    codex_home.mkdir()
    claude_home.mkdir()
    controller = SessionsProductController(
        _HealthyService(),
        object(),
        ATTEMPT_ROOT / "contracts" / "analysis",
        ATTEMPT_ROOT / "fixtures_t08",
        tmp_path / "state",
        source_roots={"codex_home": codex_home, "claude_home": claude_home},
        refinement_task_manager=object(),
    )
    controller.import_browser_bundle(_browser_bundle())
    row = controller.call("sessions.list", {"source": "deepseek"})["rows"][0]

    settings = controller.call(
        "sessions.refinement_settings_save",
        {"profile_ref": "profile-sonnet", "include_tasks": True, "include_risks": True},
    )
    with pytest.raises(
        SessionProductError,
        match="SESSION_REFINEMENT_PROFILE_EXECUTION_UNAVAILABLE",
    ):
        controller.call(
            "sessions.refine", {"local_projection_id": row["local_projection_id"]}
        )

    assert settings["settings"]["profile_ref"] == "profile-sonnet"
    metrics = controller.call("sessions.effect_metrics", {})
    assert metrics["refinement_tasks_created"] == 0
    assert metrics["refinement_model_calls"] == 0


