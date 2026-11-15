from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from memorive_sessions.edge_visible_ui import EdgeDeepSeekVisibleUIReader
from memorive_sessions.product_controller import SessionProductError, SessionsProductController


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"


class _HealthyService:
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        assert method == "service.health"
        assert params == {}
        return {
            "schema_version": "ApplicationServiceHealth-v1",
            "protocol_version": "1.0",
            "adapter_kind": "chat-archive-test-service",
            "request_count": 0,
            "auth_rejection_count": 0,
            "connection_count": 1,
            "uptime_seconds": 1,
            "external_network_calls": 0,
            "external_model_calls": 0,
            "credential_value_reads": 0,
        }


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _indexed_codex_controller(tmp_path: Path, rows: list[dict[str, object]]) -> SessionsProductController:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    claude_home.mkdir(parents=True)
    session_id = "01b00000-0000-7000-8000-000000000024"
    rollout = codex_home / "sessions" / "2026" / "08" / "28" / f"rollout-{session_id}.jsonl"
    _write_jsonl(
        rollout,
        [
            {
                "timestamp": "2026-08-28T10:00:00Z",
                "type": "session_meta",
                "payload": {"id": session_id, "cwd": "G:\\Memorive"},
            },
            *rows,
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
                session_id,
                str(rollout),
                1787880000,
                1787880001,
                "G:\\Memorive",
                "chat-archive 完整会话",
                "不得截断",
                "gpt-5.6-sol",
            ),
        )
    return SessionsProductController(
        _HealthyService(),
        object(),
        ATTEMPT_ROOT / "contracts" / "analysis",
        ATTEMPT_ROOT / "fixtures_t08",
        tmp_path / "state",
        source_roots={"codex_home": codex_home, "claude_home": claude_home},
    )


def _codex_message(index: int, role: str, text: str) -> dict[str, object]:
    return {
        "timestamp": f"2026-08-28T10:{index // 60:02d}:{index % 60:02d}Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
        },
    }


def test_selected_codex_detail_preserves_every_turn_middle_content_and_formatting(tmp_path: Path) -> None:
    long_exact = "首段  保留双空格\n\n" + ("完整正文" * 7_000) + "\n末段"
    rows: list[dict[str, object]] = []
    expected: list[str] = []
    for index in range(132):
        text = long_exact if index == 61 else f"TURN-{index:03d}-" + ("内容" * 1_000)
        role = "user" if index % 2 == 0 else "assistant"
        rows.append(_codex_message(index, role, text))
        expected.append(text)
    controller = _indexed_codex_controller(tmp_path, rows)
    bootstrap = controller.call("sessions.bootstrap", {})
    row = next(item for item in bootstrap["records"] if item["provider_id"] == "codex")

    detail = controller.call("sessions.get", {"local_projection_id": row["local_projection_id"]})

    assert [message["content"] for message in detail["messages"]] == expected
    assert detail["message_count"] == 132
    assert detail["session"]["partial"] is False
    assert detail["completeness"]["status"] == "COMPLETE"
    assert detail["completeness"]["scope"] == "ALL_USER_ASSISTANT_TEXT_MESSAGES"


def test_session_discovery_lists_only_records_updated_within_the_last_thirty_days(tmp_path: Path) -> None:
    controller = _indexed_codex_controller(tmp_path, [_codex_message(1, "user", "最近会话")])
    codex_home = controller._source_roots["codex_home"]
    old_id = "01b00000-0000-7000-8000-000000000124"
    old_rollout = codex_home / "sessions" / "2026" / "06" / "01" / f"rollout-{old_id}.jsonl"
    _write_jsonl(
        old_rollout,
        [
            {
                "timestamp": "2026-06-01T00:00:00Z",
                "type": "session_meta",
                "payload": {"id": old_id, "cwd": "G:\\Memorive"},
            },
            _codex_message(2, "assistant", "三十日以前的会话"),
        ],
    )
    with sqlite3.connect(codex_home / "state_5.sqlite") as connection:
        connection.execute(
            """
            INSERT INTO threads
              (id, rollout_path, created_at, updated_at, cwd, title, preview, model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                old_id,
                str(old_rollout),
                1780272000,
                1780272000,
                "G:\\Memorive",
                "旧会话",
                "不应进入最近三十日列表",
                "gpt-5.6-sol",
            ),
        )

    bootstrap = controller.call("sessions.bootstrap", {})

    codex_rows = [row for row in bootstrap["records"] if row["provider_id"] == "codex"]
    assert len(codex_rows) == 1
    assert bootstrap["source_status"]["codex"]["record_count"] == 1
    assert bootstrap["discovery_window_days"] == 30


def test_claude_metadata_sampling_does_not_mark_a_complete_record_partial(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    codex_home.mkdir()
    session_id = "11111111-2222-4333-8444-555555555524"
    path = claude_home / "projects" / "portable" / f"{session_id}.jsonl"
    filler = "x" * 2_200_000
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "timestamp": "2026-08-28T11:00:00Z",
                "sessionId": session_id,
                "cwd": "D:\\portable",
                "message": {"role": "user", "content": "Claude 完整开头"},
            },
            {"type": "progress", "payload": filler},
            {
                "type": "assistant",
                "timestamp": "2026-08-28T11:00:02Z",
                "sessionId": session_id,
                "cwd": "D:\\portable",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Claude 完整结尾"}]},
            },
        ],
    )
    controller = SessionsProductController(
        _HealthyService(),
        object(),
        ATTEMPT_ROOT / "contracts" / "analysis",
        ATTEMPT_ROOT / "fixtures_t08",
        tmp_path / "state",
        source_roots={"codex_home": codex_home, "claude_home": claude_home},
    )

    bootstrap = controller.call("sessions.bootstrap", {})
    row = next(item for item in bootstrap["records"] if item["provider_id"] == "claude")
    detail = controller.call("sessions.get", {"local_projection_id": row["local_projection_id"]})

    assert row["partial"] is False
    assert detail["session"]["partial"] is False
    assert [message["content"] for message in detail["messages"]] == [
        "Claude 完整开头",
        "Claude 完整结尾",
    ]


def test_malformed_jsonl_fails_closed_instead_of_claiming_a_complete_conversation(tmp_path: Path) -> None:
    controller = _indexed_codex_controller(tmp_path, [_codex_message(1, "user", "有效消息")])
    bootstrap = controller.call("sessions.bootstrap", {})
    row = next(item for item in bootstrap["records"] if item["provider_id"] == "codex")
    internal = controller._get_internal(row["local_projection_id"])
    path = Path(internal["source_record_path"])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not-json}\n")

    with pytest.raises(SessionProductError, match="SESSION_SOURCE_RECORD_MALFORMED_JSONL"):
        controller.call("sessions.get", {"local_projection_id": row["local_projection_id"]})


class _CompleteDeepSeekReader:
    def capture_visible_conversation(self) -> dict[str, object]:
        messages = [
            {
                "message_id": f"deepseek-{index:03d}",
                "role": "user" if index % 2 == 0 else "assistant",
                "text": f"DeepSeek 完整消息 {index} " + ("正文" * 600),
            }
            for index in range(402)
        ]
        return {
            "provider_profile": "DEEPSEEK_EDGE_VISIBLE_UI_V1",
            "conversation_id": "deepseek-complete-chat-archive",
            "messages": messages,
            "observed_at": "2026-08-28T12:00:00Z",
            "source_truncated": False,
            "boundary_method": "HOST_OBSERVED_VISIBLE_UI_FULL_SCROLL_V2",
        }


def test_deepseek_complete_visible_ui_accepts_more_than_old_message_and_character_caps(tmp_path: Path) -> None:
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
        visible_ui_reader=_CompleteDeepSeekReader(),
    )

    bootstrap = controller.call("sessions.bootstrap", {})
    assert bootstrap["source_status"]["deepseek"]["status"] == "PASS_VISIBLE_UI_COMPLETE"
    row = next(item for item in bootstrap["records"] if item["provider_id"] == "deepseek")
    detail = controller.call("sessions.get", {"local_projection_id": row["local_projection_id"]})

    assert row["partial"] is False
    assert detail["message_count"] == 402
    assert detail["messages"][200]["content"].startswith("DeepSeek 完整消息 200")
    assert detail["completeness"]["status"] == "COMPLETE"


def test_deepseek_window_title_is_an_allowed_visible_fallback_when_address_bar_is_unreadable() -> None:
    assert EdgeDeepSeekVisibleUIReader._candidate_seed(
        "", "DeepSeek - 探索未至之境 - Microsoft Edge"
    ) == ("deepseek-visible-title", "VISIBLE_WINDOW_TITLE_FALLBACK")
    assert EdgeDeepSeekVisibleUIReader._candidate_seed(
        "", "Unrelated page - Microsoft Edge"
    ) is None


def test_deepseek_role_geometry_accepts_the_current_responsive_page_columns() -> None:
    assert EdgeDeepSeekVisibleUIReader._role_for_relative_geometry(0.571, 0.663) == "user"
    assert EdgeDeepSeekVisibleUIReader._role_for_relative_geometry(0.334, 0.375) == "assistant"
    assert EdgeDeepSeekVisibleUIReader._role_for_relative_geometry(0.017, 0.056) is None


def test_deepseek_page_stitching_preserves_all_turns_and_removes_only_scroll_overlap() -> None:
    pages = [
        [
            {"role": "user", "text": "U1"},
            {"role": "assistant", "text": "A1"},
            {"role": "user", "text": "U2"},
        ],
        [
            {"role": "user", "text": "U2"},
            {"role": "assistant", "text": "A2"},
            {"role": "user", "text": "U3 partial"},
        ],
        [
            {"role": "user", "text": "U3 partial and complete"},
            {"role": "assistant", "text": "A3"},
        ],
    ]


def test_deepseek_full_scroll_reaches_both_ends_and_restores_the_users_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = SimpleNamespace(percent=72.0, view_size=50.0)

    class _Pattern:
        @property
        def Current(self) -> SimpleNamespace:
            return SimpleNamespace(
                VerticalScrollPercent=state.percent,
                VerticalViewSize=state.view_size,
                VerticallyScrollable=True,
            )

        def SetScrollPercent(self, _horizontal: float, vertical: float) -> None:
            state.percent = float(vertical)

        def Scroll(self, _horizontal: object, _vertical: object) -> None:
            state.percent = min(100.0, state.percent + state.view_size)

    pattern = _Pattern()

    class _Element:
        def GetCurrentPattern(self, _pattern_id: object) -> _Pattern:
            return pattern

    element = _Element()
    monkeypatch.setattr(
        EdgeDeepSeekVisibleUIReader,
        "_scroll_target",
        classmethod(lambda _cls, _document, _api: (element, pattern)),
    )

    def _nodes(_cls: object, _document: object, _api: object) -> list[dict[str, object]]:
        if state.percent < 20:
            turns = [("user", "U1"), ("assistant", "A1"), ("user", "U2")]
        elif state.percent < 70:
            turns = [("user", "U2"), ("assistant", "A2"), ("user", "U3 partial")]
        else:
            turns = [("user", "U3 partial and complete"), ("assistant", "A3")]
        return [
            {"role": role, "text": text, "top": index * 10, "left": 0}
            for index, (role, text) in enumerate(turns)
        ]

    monkeypatch.setattr(
        EdgeDeepSeekVisibleUIReader,
        "_visible_text_nodes",
        classmethod(_nodes),
    )
    monkeypatch.setattr("memorive_sessions.edge_visible_ui.time.sleep", lambda _seconds: None)
    api = {
        "ScrollPattern": SimpleNamespace(Pattern=object(), NoScroll=-1.0),
        "ScrollAmount": SimpleNamespace(NoAmount=object(), LargeIncrement=object()),
    }

    pages = EdgeDeepSeekVisibleUIReader._turn_pages_for_document(object(), api)

    assert state.percent == 72.0
    assert EdgeDeepSeekVisibleUIReader._stitch_turn_pages(pages)[-2:] == [
        {"role": "user", "text": "U3 partial and complete"},
        {"role": "assistant", "text": "A3"},
    ]

    assert EdgeDeepSeekVisibleUIReader._stitch_turn_pages(pages) == [
        {"role": "user", "text": "U1"},
        {"role": "assistant", "text": "A1"},
        {"role": "user", "text": "U2"},
        {"role": "assistant", "text": "A2"},
        {"role": "user", "text": "U3 partial and complete"},
        {"role": "assistant", "text": "A3"},
    ]


