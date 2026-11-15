from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from memorive_sessions.product_controller import SessionsProductController


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ATTEMPT_ROOT.parents[1]
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"


class _HealthyService:
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        assert method == "service.health"
        assert params == {}
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


def _jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _real_local_sources(tmp_path: Path) -> dict[str, Path]:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    codex_session_id = "01b00000-0000-7000-8000-000000000021"
    codex_rollout = (
        codex_home
        / "sessions"
        / "2026"
        / "08"
        / "27"
        / f"rollout-2026-08-27T10-00-00-{codex_session_id}.jsonl"
    )
    _jsonl(
        codex_rollout,
        [
            {
                "timestamp": "2026-08-27T10:00:00Z",
                "type": "session_meta",
                "payload": {"id": codex_session_id, "cwd": "G:\\Memorive"},
            },
            {
                "timestamp": "2026-08-27T10:00:01Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "真实 Codex 用户消息"}],
                },
            },
            {
                "timestamp": "2026-08-27T10:00:02Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "真实 Codex 助手消息"}],
                },
            },
        ],
    )
    database = codex_home / "state_5.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE threads (
              id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL, source TEXT NOT NULL, model_provider TEXT NOT NULL,
              cwd TEXT NOT NULL, title TEXT NOT NULL, has_user_event INTEGER NOT NULL,
              archived INTEGER NOT NULL, preview TEXT NOT NULL, model TEXT,
              recency_at INTEGER NOT NULL, updated_at_ms INTEGER
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads
              (id, rollout_path, created_at, updated_at, source, model_provider, cwd,
               title, has_user_event, archived, preview, model, recency_at, updated_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                codex_session_id,
                str(codex_rollout),
                1787800000,
                1787800200,
                "app",
                "openai",
                "G:\\Memorive",
                "真实 Codex 会话",
                0,
                0,
                "真实 Codex 摘要",
                "gpt-5.6-sol",
                1787800200,
                1787800200000,
            ),
        )

    claude_session_id = "11111111-2222-4333-8444-555555555521"
    claude_rollout = claude_home / "projects" / "G--Memorive" / f"{claude_session_id}.jsonl"
    _jsonl(
        claude_rollout,
        [
            {
                "type": "user",
                "timestamp": "2026-08-27T11:00:00Z",
                "sessionId": claude_session_id,
                "cwd": "G:\\Memorive",
                "message": {"role": "user", "content": "真实 Claude 用户消息"},
            },
            {
                "type": "custom-title",
                "sessionId": claude_session_id,
                "customTitle": "真实 Claude Code 会话",
            },
            {
                "type": "assistant",
                "timestamp": "2026-08-27T11:00:02Z",
                "sessionId": claude_session_id,
                "cwd": "G:\\Memorive",
                "message": {
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-5",
                    "content": [{"type": "text", "text": "真实 Claude 助手消息"}],
                },
            },
            {
                "type": "last-prompt",
                "sessionId": claude_session_id,
                "lastPrompt": "真实 Claude 用户消息",
            },
        ],
    )
    return {"codex_home": codex_home, "claude_home": claude_home}


def _controller(tmp_path: Path) -> SessionsProductController:
    return SessionsProductController(
        _HealthyService(),
        object(),
        ATTEMPT_ROOT / "contracts" / "analysis",
        ATTEMPT_ROOT / "fixtures_t08",
        tmp_path / "state",
        source_roots=_real_local_sources(tmp_path),
    )


def test_real_codex_and_claude_sessions_replace_the_sample_projection(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    bootstrap = controller.call("sessions.bootstrap", {})
    assert bootstrap["status"] == "PASS"
    assert bootstrap["synthetic_only"] is False
    assert bootstrap["record_count"] == 2
    assert bootstrap["source_access_envelope_status"] == "ACTIVE_LOCAL_READ_ONLY"
    assert bootstrap["real_source_channels_assessed"] == 2
    assert {row["provider_id"] for row in bootstrap["records"]} == {"codex", "claude"}
    assert {row["title"] for row in bootstrap["records"]} == {
        "真实 Codex 会话",
        "真实 Claude Code 会话",
    }
    assert all("source_record_path" not in row for row in bootstrap["records"])
    assert all(row["delete_capability"] == "" for row in bootstrap["records"])
    assert all(row["refine_status"] == "disabled" for row in bootstrap["records"])


def test_session_body_is_loaded_only_for_the_selected_real_session(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    bootstrap = controller.call("sessions.bootstrap", {})
    assert all("messages" not in row for row in bootstrap["records"])
    rows = {row["provider_id"]: row for row in bootstrap["records"]}

    codex = controller.call(
        "sessions.get", {"local_projection_id": rows["codex"]["local_projection_id"]}
    )
    claude = controller.call(
        "sessions.get", {"local_projection_id": rows["claude"]["local_projection_id"]}
    )
    assert [message["content"] for message in codex["messages"]] == [
        "真实 Codex 用户消息",
        "真实 Codex 助手消息",
    ]
    assert [message["content"] for message in claude["messages"]] == [
        "真实 Claude 用户消息",
        "真实 Claude 助手消息",
    ]
    assert codex["local_private_content_included"] is True
    assert claude["local_private_content_included"] is True
    assert codex["raw_private_content_externalized"] is False
    metrics = controller.call("sessions.effect_metrics", {})
    assert metrics["cli_session_reads"] == 2
    assert metrics["credential_value_reads"] == 0
    assert metrics["cookie_value_reads"] == 0
    assert metrics["browser_storage_value_reads"] == 0
    assert metrics["external_network_calls"] == 0


