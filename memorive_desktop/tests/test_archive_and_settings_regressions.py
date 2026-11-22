from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import threading


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "source", ROOT / "product" / "desktop"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive.models.local.product_controller import LocalModelProductController  # noqa: E402
from memorive.sessions.archive import (  # noqa: E402
    ArchiveInputError,
    DeepSeekOfficialImporter,
)
from memorive.sessions.controller import (  # noqa: E402
    SessionProductError,
    SessionsProductController,
)

import pytest  # noqa: E402


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


def _sessions(tmp_path: Path) -> SessionsProductController:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    codex_home.mkdir()
    claude_home.mkdir()
    return SessionsProductController(
        _HealthyService(),
        object(),
        ROOT / "contracts" / "analysis",
        ROOT / "fixtures_t08",
        tmp_path / "state",
        source_roots={"codex_home": codex_home, "claude_home": claude_home},
        legacy_deepseek_visible_ui_projection=False,
    )


def _v4_conversation(
    index: int,
    provider: str = "deepseek",
) -> dict[str, object]:
    prefixes = {"deepseek": "ds", "gemini": "gem", "kimi": "kimi"}
    labels = {"deepseek": "DeepSeek", "gemini": "Gemini", "kimi": "Kimi"}
    url_templates = {
        "deepseek": "https://chat.deepseek.com/a/chat/s/{session_id}",
        "gemini": "https://gemini.google.com/app/{session_id}",
        "kimi": "https://www.kimi.com/chat/{session_id}",
    }
    session_id = f"{prefixes[provider]}-{index:03d}"
    timestamp = f"2026-08-{27 - (index % 20):02d}T{index % 24:02d}:00:00Z"
    return {
        "provider_id": provider,
        "provider_session_id": session_id,
        "title": f"{labels[provider]} 会话 {index:03d}",
        "created_at": timestamp,
        "updated_at": timestamp,
        "recency_bucket": "LAST_30_DAYS",
        "source_url": url_templates[provider].format(session_id=session_id),
        "capture_method": "BROWSER_COMPANION_BACKGROUND_TAB_V3",
        "capture_status": "COMPLETE",
        "source_truncated": False,
        "top_boundary_reached": True,
        "bottom_boundary_reached": True,
        "messages": [
            {
                "message_id": f"{session_id}-u",
                "role": "user",
                "content_kind": "text",
                "content": f"问题 {index}",
                "occurred_at": timestamp,
            },
            {
                "message_id": f"{session_id}-a",
                "role": "assistant",
                "content_kind": "text",
                "content": f"回答 {index}",
                "occurred_at": timestamp,
            },
        ],
    }


def _v4_library(
    conversations: list[dict[str, object]],
    provider: str = "deepseek",
) -> dict[str, object]:
    return {
        "schema_version": "MEMORIVE_BROWSER_SESSION_LIBRARY_V4_DRAFT",
        "source": "MEMORIVE_BROWSER_COMPANION_VISIBLE_UI_V4_DRAFT",
        "generated_at": "2026-08-28T08:00:00Z",
        "sync_scope": "RECENT_30_DAYS",
        "provider_id": provider,
        "enumerated_count": len(conversations),
        "captured_count": len(conversations),
        "failed_count": 0,
        "conversations": conversations,
        "failures": [],
    }


def test_v4_library_archives_every_session_while_30_50_are_display_limits(
    tmp_path: Path,
) -> None:
    controller = _sessions(tmp_path)
    all_conversations = [_v4_conversation(index) for index in range(55)]

    receipt = controller.import_browser_bundle(_v4_library(all_conversations))
    controller.import_browser_bundle(_v4_library(all_conversations[:1]))

    assert receipt["imported_count"] == 55
    assert controller._session_archive.count_sessions("deepseek") == 55
    assert len(controller.call("sessions.list", {"source": "deepseek"})["rows"]) == 30
    assert (
        len(
            controller.call(
                "sessions.list", {"source": "deepseek", "limit_per_source": 50}
            )["rows"]
        )
        == 50
    )
    row = controller.call("sessions.list", {"source": "deepseek"})["rows"][0]
    detail = controller.call(
        "sessions.get", {"local_projection_id": row["local_projection_id"]}
    )
    assert detail["message_count"] == 2
    assert detail["messages"][0]["content"].startswith("问题")


def test_v4_library_rejects_old_or_unproven_recency_even_when_scope_claims_30_days(
    tmp_path: Path,
) -> None:
    controller = _sessions(tmp_path)
    old = _v4_conversation(1)
    old["created_at"] = "2025-01-15T08:00:00Z"
    old["updated_at"] = "2025-01-15T08:00:00Z"
    with pytest.raises(
        SessionProductError,
        match="BROWSER_CONVERSATION_OUTSIDE_RECENT_30_DAYS",
    ):
        controller.import_browser_bundle(_v4_library([old]))

    unknown = _v4_conversation(2)
    unknown["created_at"] = "UNKNOWN"
    unknown["updated_at"] = "UNKNOWN"
    unknown.pop("recency_bucket", None)
    with pytest.raises(
        SessionProductError,
        match="BROWSER_CONVERSATION_RECENCY_UNPROVEN",
    ):
        controller.import_browser_bundle(_v4_library([unknown]))


def _v4_bounded_library(
    conversations: list[dict[str, object]],
    provider: str = "deepseek",
) -> dict[str, object]:
    payload = _v4_library(conversations, provider)
    payload["sync_scope"] = "RECENT_14_DAYS_OR_30_CONVERSATIONS"
    return payload


def test_v4_bounded_scope_accepts_14_day_priority_and_sidebar_top_30_fallback(
    tmp_path: Path,
) -> None:
    controller = _sessions(tmp_path)
    dated = _v4_conversation(1)
    dated.update(
        {
            "created_at": "2026-08-14T08:00:00Z",
            "updated_at": "2026-08-14T08:00:00Z",
            "recency_bucket": "LAST_14_DAYS",
            "selection_basis": "RECENT_14_DAYS",
            "source_recency_rank": 49,
        }
    )
    fallback = _v4_conversation(2)
    fallback.update(
        {
            "created_at": "UNKNOWN",
            "updated_at": "UNKNOWN",
            "recency_bucket": "UNKNOWN",
            "selection_basis": "RECENT_30_CONVERSATIONS",
            "source_recency_rank": 29,
        }
    )

    receipt = controller.import_browser_bundle(
        _v4_bounded_library([dated, fallback])
    )

    assert receipt["imported_count"] == 2


def test_v4_sidebar_top_30_fallback_remains_visible_after_archive_refresh(
    tmp_path: Path,
) -> None:
    controller = _sessions(tmp_path)
    fallback = _v4_conversation(4)
    fallback.update(
        {
            "created_at": "2026-05-01T08:00:00Z",
            "updated_at": "2026-05-01T08:00:00Z",
            "recency_bucket": "UNKNOWN",
            "selection_basis": "RECENT_30_CONVERSATIONS",
            "source_recency_rank": 4,
        }
    )

    receipt = controller.import_browser_bundle(_v4_bounded_library([fallback]))
    rows = controller.call(
        "sessions.list",
        {"source": "deepseek", "limit_per_source": 20},
    )["rows"]

    assert receipt["imported_count"] == 1
    assert [row["provider_session_id"] for row in rows] == [
        fallback["provider_session_id"]
    ]


@pytest.mark.parametrize(
    ("selection_basis", "rank", "timestamp", "error_code"),
    (
        (
            "RECENT_14_DAYS",
            0,
            "2026-08-13T07:59:59Z",
            "BROWSER_CONVERSATION_OUTSIDE_RECENT_14_DAYS",
        ),
        (
            "RECENT_14_DAYS",
            50,
            "2026-08-27T08:00:00Z",
            "BROWSER_CONVERSATION_RECENCY_RANK_INVALID",
        ),
        (
            "RECENT_30_CONVERSATIONS",
            30,
            "UNKNOWN",
            "BROWSER_CONVERSATION_RECENCY_RANK_INVALID",
        ),
    ),
)
def test_v4_bounded_scope_rejects_invalid_date_or_rank_evidence(
    tmp_path: Path,
    selection_basis: str,
    rank: int,
    timestamp: str,
    error_code: str,
) -> None:
    controller = _sessions(tmp_path)
    conversation = _v4_conversation(3)
    conversation.update(
        {
            "created_at": timestamp,
            "updated_at": timestamp,
            "selection_basis": selection_basis,
            "source_recency_rank": rank,
            "recency_bucket": (
                "LAST_14_DAYS" if selection_basis == "RECENT_14_DAYS" else "UNKNOWN"
            ),
        }
    )

    with pytest.raises(SessionProductError, match=error_code):
        controller.import_browser_bundle(_v4_bounded_library([conversation]))


def test_v4_bounded_scope_rejects_more_than_50_enumerated_conversations(
    tmp_path: Path,
) -> None:
    controller = _sessions(tmp_path)
    conversations = []
    for index in range(51):
        conversation = _v4_conversation(index)
        conversation.update(
            {
                "selection_basis": "RECENT_14_DAYS",
                "source_recency_rank": index,
                "recency_bucket": "LAST_14_DAYS",
            }
        )
        conversations.append(conversation)

    with pytest.raises(
        SessionProductError,
        match="BROWSER_LIBRARY_RECENT_CONVERSATION_LIMIT_EXCEEDED",
    ):
        controller.import_browser_bundle(_v4_bounded_library(conversations))


@pytest.mark.parametrize(
    ("provider", "label"),
    (("deepseek", "DeepSeek"), ("gemini", "Gemini"), ("kimi", "Kimi")),
)
def test_each_web_provider_round_trips_stable_title_time_and_complete_body(
    tmp_path: Path,
    provider: str,
    label: str,
) -> None:
    controller = _sessions(tmp_path)
    conversation = _v4_conversation(3, provider)

    receipt = controller.import_browser_bundle(
        _v4_library([conversation], provider)
    )
    with pytest.raises(SessionProductError, match="BROWSER_LIBRARY_EMPTY"):
        controller.import_browser_bundle(_v4_library([], provider))
    rows = controller.call(
        "sessions.list", {"source": provider, "limit_per_source": 20}
    )["rows"]
    detail = controller.call(
        "sessions.get", {"local_projection_id": rows[0]["local_projection_id"]}
    )

    assert receipt["provider_id"] == provider
    assert receipt["imported_count"] == 1
    assert rows[0]["title"] == f"{label} 会话 003"
    assert rows[0]["captured_at"] == conversation["updated_at"]
    assert [message["content"] for message in detail["messages"]] == [
        "问题 3",
        "回答 3",
    ]
    assert detail["completeness"]["status"] == "COMPLETE"
    assert controller._session_archive.count_sessions(provider) == 1


def test_empty_or_message_less_official_exports_fail_explicitly(tmp_path: Path) -> None:
    controller = _sessions(tmp_path)
    deepseek = tmp_path / "deepseek-empty.json"
    deepseek.write_text("[]", encoding="utf-8")
    with pytest.raises(ArchiveInputError, match="DEEPSEEK_EXPORT_EMPTY"):
        DeepSeekOfficialImporter.parse(deepseek)

    gemini = tmp_path / "gemini-message-less.json"
    gemini.write_text(
        json.dumps(
            [
                {
                    "titleUrl": "https://gemini.google.com/app/c/gemini-empty-1",
                    "time": "2026-08-27T08:00:00Z",
                    "details": [],
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(SessionProductError, match="GEMINI_TAKEOUT_MESSAGES_NOT_FOUND"):
        controller.import_session_file(gemini)


def test_v4_complete_claim_requires_both_user_and_assistant_text(tmp_path: Path) -> None:
    controller = _sessions(tmp_path)
    conversation = _v4_conversation(13, "kimi")
    conversation["messages"] = conversation["messages"][:1]  # type: ignore[index]

    with pytest.raises(
        SessionProductError, match="BROWSER_MESSAGES_ROLE_COVERAGE_INCOMPLETE"
    ):
        controller.import_browser_bundle(_v4_library([conversation], "kimi"))


def test_shorter_complete_browser_resync_cannot_replace_a_longer_complete_body(
    tmp_path: Path,
) -> None:
    controller = _sessions(tmp_path)
    complete = _v4_conversation(4, "kimi")
    complete["messages"] = [
        *complete["messages"],  # type: ignore[list-item]
        {
            "message_id": "kimi-004-u2",
            "role": "user",
            "content_kind": "text",
            "content": "第二个问题",
            "occurred_at": "2026-08-27T06:10:00Z",
        },
        {
            "message_id": "kimi-004-a2",
            "role": "assistant",
            "content_kind": "text",
            "content": "第二个回答",
            "occurred_at": "2026-08-27T06:11:00Z",
        },
    ]
    complete["updated_at"] = "2026-08-27T06:11:00Z"
    controller.import_browser_bundle(_v4_library([complete], "kimi"))

    shorter = _v4_conversation(4, "kimi")
    shorter["updated_at"] = "2026-08-27T06:30:00Z"
    receipt = controller.import_browser_bundle(_v4_library([shorter], "kimi"))
    row = controller.call("sessions.list", {"source": "kimi"})["rows"][0]
    detail = controller.call(
        "sessions.get", {"local_projection_id": row["local_projection_id"]}
    )

    assert receipt["retained_previous_complete_count"] == 1
    assert row["captured_at"] == "2026-08-27T06:11:00Z"
    assert [message["content"] for message in detail["messages"]] == [
        "问题 4",
        "回答 4",
        "第二个问题",
        "第二个回答",
    ]


def test_equal_message_count_with_shorter_text_cannot_regress_complete_body(
    tmp_path: Path,
) -> None:
    controller = _sessions(tmp_path)
    complete = _v4_conversation(12, "deepseek")
    complete["messages"][1]["content"] = "完整回答包含不可丢失的后半段内容"  # type: ignore[index]
    controller.import_browser_bundle(_v4_library([complete], "deepseek"))

    shortened = _v4_conversation(12, "deepseek")
    shortened["messages"][1]["content"] = "完整回答"  # type: ignore[index]
    shortened["updated_at"] = "2026-08-28T09:00:00Z"
    receipt = controller.import_browser_bundle(_v4_library([shortened], "deepseek"))
    row = controller.call("sessions.list", {"source": "deepseek"})["rows"][0]
    detail = controller.call(
        "sessions.get", {"local_projection_id": row["local_projection_id"]}
    )

    assert receipt["retained_previous_complete_count"] == 1
    assert detail["messages"][1]["content"] == "完整回答包含不可丢失的后半段内容"


def test_unknown_time_and_noise_title_cannot_erase_verified_metadata(
    tmp_path: Path,
) -> None:
    controller = _sessions(tmp_path)
    original = _v4_conversation(5, "gemini")
    controller.import_browser_bundle(_v4_library([original], "gemini"))
    degraded = {**original, "title": "Gemini", "created_at": "UNKNOWN", "updated_at": "UNKNOWN"}

    controller.import_browser_bundle(_v4_library([degraded], "gemini"))
    row = controller.call("sessions.list", {"source": "gemini"})["rows"][0]

    assert row["title"] == "Gemini 会话 005"
    assert row["captured_at"] == original["updated_at"]


def test_v4_conversation_requires_explicit_top_and_bottom_completeness_proof(
    tmp_path: Path,
) -> None:
    controller = _sessions(tmp_path)
    incomplete_proof = _v4_conversation(6, "deepseek")
    incomplete_proof.pop("top_boundary_reached")

    with pytest.raises(SessionProductError, match="TOP_BOUNDARY_UNPROVEN"):
        controller.import_browser_bundle(_v4_library([incomplete_proof], "deepseek"))


def test_two_legitimate_duplicate_bodies_are_kept_but_three_way_stale_capture_fails(
    tmp_path: Path,
) -> None:
    controller = _sessions(tmp_path)

    def same_body(index: int) -> dict[str, object]:
        value = _v4_conversation(index, "deepseek")
        for message, content in zip(
            value["messages"],  # type: ignore[arg-type]
            ("相同的用户问题", "相同的助手回答"),
            strict=True,
        ):
            message["content"] = content
        return value

    accepted = controller.import_browser_bundle(
        _v4_library([same_body(7), same_body(8)], "deepseek")
    )
    assert accepted["imported_count"] == 2

    with pytest.raises(SessionProductError, match="REJECTED_CORRUPT"):
        controller.import_browser_bundle(
            _v4_library([same_body(9), same_body(10), same_body(11)], "deepseek")
        )

    assert controller._session_archive.count_sessions("deepseek") == 2


def test_failed_v4_resync_retains_last_complete_body_and_smaller_sync_keeps_history(
    tmp_path: Path,
) -> None:
    controller = _sessions(tmp_path)
    complete = _v4_library([_v4_conversation(1), _v4_conversation(2)])
    controller.import_browser_bundle(complete)
    failed = {
        **_v4_library([]),
        "enumerated_count": 1,
        "failed_count": 1,
        "failures": [
            {
                "provider_session_id": "ds-001",
                "title": "暂时同步失败",
                "error_code": "CAPTURE_FAILED",
            }
        ],
    }

    receipt = controller.import_browser_bundle(failed)

    assert receipt["retained_previous_complete_count"] == 1
    assert controller._session_archive.count_sessions("deepseek") == 2
    rows = controller.call(
        "sessions.list", {"source": "deepseek", "limit_per_source": 50}
    )["rows"]
    stable = next(row for row in rows if row["provider_session_id"] == "ds-001")
    detail = controller.call(
        "sessions.get", {"local_projection_id": stable["local_projection_id"]}
    )
    assert [message["content"] for message in detail["messages"]] == [
        "问题 1",
        "回答 1",
    ]


def test_v4_import_rejects_any_credential_shaped_field(tmp_path: Path) -> None:
    controller = _sessions(tmp_path)
    payload = _v4_library([_v4_conversation(1)])
    payload["cookie"] = "must-not-be-read"

    with pytest.raises(SessionProductError, match="CREDENTIAL_FIELD_FORBIDDEN"):
        controller.import_browser_bundle(payload)


def test_session_archive_is_safe_on_the_pywebview_worker_thread(tmp_path: Path) -> None:
    controller = _sessions(tmp_path)
    outcome: dict[str, object] = {}

    def bootstrap_from_worker() -> None:
        try:
            outcome["projection"] = controller.call("sessions.bootstrap", {})
        except BaseException as error:  # captured for an assertion on the caller thread
            outcome["error"] = error

    worker = threading.Thread(target=bootstrap_from_worker)
    worker.start()
    worker.join(timeout=10)

    assert worker.is_alive() is False
    assert "error" not in outcome
    assert outcome["projection"]["status"] == "PASS"  # type: ignore[index]


def test_all_three_local_service_presets_use_their_real_model_list_adapters(
    tmp_path: Path,
) -> None:
    requested_paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            requested_paths.append(self.path)
            payload = (
                {"models": [{"name": "ollama-model"}]}
                if self.path == "/api/tags"
                else {"data": [{"id": "openai-compatible-model"}]}
            )
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        controller = LocalModelProductController(tmp_path / "local-models")
        endpoint = f"http://127.0.0.1:{server.server_port}"
        for kind in ("ollama", "openai_compatible", "llama_cpp"):
            saved = controller.call(
                "local_models.endpoint_save",
                {"display_name": kind, "kind": kind, "endpoint": endpoint},
            )
            verified = controller.call(
                "local_models.verify", {"endpoint_id": saved["endpoint"]["endpoint_id"]}
            )
            assert verified["service_reachable"] is True
            assert verified["model_listed"] is True
            assert saved["endpoint"]["adapter_family"] in {
                "OLLAMA_NATIVE",
                "OPENAI_COMPATIBLE",
            }
            assert saved["endpoint"]["model_list_path"] in {"/api/tags", "/v1/models"}
    finally:
        server.shutdown()
        server.server_close()

    assert requested_paths.count("/api/tags") == 1
    assert requested_paths.count("/v1/models") == 2


def test_local_model_discovery_continues_after_one_adapter_returns_invalid_payload(
    tmp_path: Path,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            payload = (
                {"unexpected": []}
                if self.path == "/api/tags"
                else {"data": [{"id": "healthy-openai-compatible-model"}]}
            )
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        controller = LocalModelProductController(tmp_path / "local-models")
        state = controller._load()
        state["endpoints"] = {}
        controller._save(state)
        endpoint = f"http://127.0.0.1:{server.server_port}"
        for kind in ("ollama", "openai_compatible"):
            controller.call(
                "local_models.endpoint_save",
                {"display_name": kind, "kind": kind, "endpoint": endpoint},
            )

        receipt = controller.call("local_models.discover", {})
    finally:
        server.shutdown()
        server.server_close()

    assert receipt["checked_endpoint_count"] == 2
    assert receipt["reachable_endpoint_count"] == 1
    assert receipt["models"] == ["healthy-openai-compatible-model"]
    assert {row["status"] for row in receipt["results"]} == {
        "MODEL_LISTED_INFERENCE_NOT_RUN",
        "SERVICE_RESPONSE_INVALID_INFERENCE_NOT_RUN",
    }
