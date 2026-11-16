from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from memorive.sessions.controller import SessionProductError, SessionsProductController


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"


class _HealthyService:
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        assert method == "service.health"
        assert params == {}
        return {
            "schema_version": "ApplicationServiceHealth-v1",
            "protocol_version": "1.0",
            "adapter_kind": "visible-chat-test-service",
            "request_count": 0,
            "auth_rejection_count": 0,
            "connection_count": 1,
            "uptime_seconds": 1,
            "external_network_calls": 0,
            "external_model_calls": 0,
            "credential_value_reads": 0,
        }


class _DeepSeekVisibleUiReader:
    def capture_visible_conversation(self) -> dict[str, object]:
        return {
            "provider_profile": "DEEPSEEK_EDGE_VISIBLE_UI_V1",
            "conversation_id": "deepseek-visible-visible-chat",
            "messages": [
                {"message_id": "visible-1", "role": "user", "text": "网页端用户消息"},
                {"message_id": "visible-2", "role": "assistant", "text": "网页端助手消息"},
            ],
            "observed_at": "2026-08-28T10:00:00Z",
            "source_truncated": True,
            "boundary_method": "HOST_OBSERVED_VISIBLE_UI_TURN_ORDER_V1",
        }


def _jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _source_roots_with_indexed_external_rollout(
    tmp_path: Path, *, indexed_session_id: str, embedded_session_id: str
) -> dict[str, Path]:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    claude_home.mkdir(parents=True)
    rollout = tmp_path / "exact-indexed-rollouts" / f"{indexed_session_id}.jsonl"
    _jsonl(
        rollout,
        [
            {
                "timestamp": "2026-08-28T09:00:00Z",
                "type": "session_meta",
                "payload": {"id": embedded_session_id, "cwd": "G:\\Memorive"},
            },
            {
                "timestamp": "2026-08-28T09:00:01Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "索引外正文"}],
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
              updated_at INTEGER NOT NULL, cwd TEXT NOT NULL, title TEXT NOT NULL,
              preview TEXT NOT NULL, model TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads
              (id, rollout_path, created_at, updated_at, cwd, title, preview, model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                indexed_session_id,
                str(rollout),
                1787880000,
                1787880001,
                "G:\\Memorive",
                "索引外真实会话",
                "数据库精确路径",
                "gpt-5.6-sol",
            ),
        )
    return {"codex_home": codex_home, "claude_home": claude_home}


def _controller(
    tmp_path: Path,
    source_roots: dict[str, Path],
    *,
    visible_ui_reader: object | None = None,
) -> SessionsProductController:
    return SessionsProductController(
        _HealthyService(),
        object(),
        ATTEMPT_ROOT / "contracts" / "analysis",
        ATTEMPT_ROOT / "fixtures_t08",
        tmp_path / "state",
        source_roots=source_roots,
        visible_ui_reader=visible_ui_reader,
    )


def test_codex_database_exact_external_rollout_is_rebound_to_the_indexed_session_id(
    tmp_path: Path,
) -> None:
    session_id = "01b00000-0000-7000-8000-000000000023"
    controller = _controller(
        tmp_path,
        _source_roots_with_indexed_external_rollout(
            tmp_path, indexed_session_id=session_id, embedded_session_id=session_id
        ),
    )
    bootstrap = controller.call("sessions.bootstrap", {})
    row = next(item for item in bootstrap["records"] if item["provider_id"] == "codex")
    assert row["partial"] is False
    detail = controller.call("sessions.get", {"local_projection_id": row["local_projection_id"]})
    assert [message["content"] for message in detail["messages"]] == ["索引外正文"]


def test_codex_database_external_rollout_fails_closed_when_embedded_identity_differs(
    tmp_path: Path,
) -> None:
    indexed_id = "01b00000-0000-7000-8000-000000000024"
    controller = _controller(
        tmp_path,
        _source_roots_with_indexed_external_rollout(
            tmp_path,
            indexed_session_id=indexed_id,
            embedded_session_id="01b00000-0000-7000-8000-999999999999",
        ),
    )
    bootstrap = controller.call("sessions.bootstrap", {})
    row = next(item for item in bootstrap["records"] if item["provider_id"] == "codex")
    assert row["partial"] is True
    with pytest.raises(SessionProductError, match="SESSION_SOURCE_RECORD_UNAVAILABLE"):
        controller.call("sessions.get", {"local_projection_id": row["local_projection_id"]})


def test_deepseek_partial_visible_ui_bundle_fails_closed_under_the_complete_conversation_successor(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "empty-codex"
    claude_home = tmp_path / "empty-claude"
    codex_home.mkdir()
    claude_home.mkdir()
    controller = _controller(
        tmp_path,
        {"codex_home": codex_home, "claude_home": claude_home},
        visible_ui_reader=_DeepSeekVisibleUiReader(),
    )
    bootstrap = controller.call("sessions.bootstrap", {})
    assert bootstrap["source_count"] == 3
    status = bootstrap["source_status"]["deepseek"]
    assert status["status"] == "VISIBLE_UI_READ_FAILED"
    assert status["error_code"] == "DEEPSEEK_VISIBLE_UI_CONVERSATION_INCOMPLETE"
    assert not any(item["provider_id"] == "deepseek" for item in bootstrap["records"])
    metrics = controller.call("sessions.effect_metrics", {})
    assert metrics["ui_session_reads"] == 0
    assert metrics["credential_value_reads"] == 0
    assert metrics["cookie_value_reads"] == 0
    assert metrics["browser_storage_value_reads"] == 0


def test_session_discovery_uses_the_current_profile_or_environment_not_a_machine_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "01b00000-0000-7000-8000-000000000025"
    roots = _source_roots_with_indexed_external_rollout(
        tmp_path, indexed_session_id=session_id, embedded_session_id=session_id
    )
    claude_id = "11111111-2222-4333-8444-555555555523"
    _jsonl(
        roots["claude_home"] / "projects" / "portable-project" / f"{claude_id}.jsonl",
        [
            {
                "type": "user",
                "timestamp": "2026-08-28T09:10:00Z",
                "sessionId": claude_id,
                "cwd": "D:\\another-workspace",
                "message": {"role": "user", "content": "可迁移 Claude 会话"},
            }
        ],
    )
    monkeypatch.setenv("MEMORIVE_CODEX_HOME", str(roots["codex_home"]))
    monkeypatch.setenv("MEMORIVE_CLAUDE_HOME", str(roots["claude_home"]))
    controller = SessionsProductController(
        _HealthyService(),
        object(),
        ATTEMPT_ROOT / "contracts" / "analysis",
        ATTEMPT_ROOT / "fixtures_t08",
        tmp_path / "portable-state",
    )
    bootstrap = controller.call("sessions.bootstrap", {})
    assert {row["provider_id"] for row in bootstrap["records"]} == {"codex", "claude"}
    implementation = (
        ATTEMPT_ROOT / "source" / "memorive" / "sessions" / "edge_visible_ui.py"
    ).read_text(encoding="utf-8")
    assert "KuchinashiYume" not in implementation
    assert "G:\\" not in implementation
    assert "chat.deepseek.com" in implementation


