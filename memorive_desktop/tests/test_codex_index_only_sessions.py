"""Codex sessions open when Codex has only its session index, without state_5.sqlite.

Codex names a session file rollout-<time>-<session id>.jsonl. The index-only scan took the part after the
last underscore as the session id, so no file matched: the sessions were listed but could not be opened
or refined.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from memorive.sessions.controller import SessionsProductController


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SESSION_ID = "019a0000-0000-7000-8000-000000000045"


class _Service:
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "settings.get_state":
            return {"api_profiles": []}
        return {}


def _jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _message(role: str, text: str) -> dict[str, object]:
    kind = "input_text" if role == "user" else "output_text"
    return {"type": "response_item",
            "payload": {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}}


def test_index_only_codex_session_opens(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _jsonl(codex_home / "session_index.jsonl",
           [{"id": SESSION_ID, "thread_name": "索引里的会话", "updated_at": now}])
    _jsonl(codex_home / "sessions" / "2026" / "11" / "21" / f"rollout-2026-11-21T08-00-00-{SESSION_ID}.jsonl", [
        {"type": "session_meta", "payload": {"id": SESSION_ID, "cwd": "D:\\work"}},
        _message("user", "用户的问题"),
        _message("assistant", "助手的回答"),
    ])
    (tmp_path / "claude-home").mkdir()
    controller = SessionsProductController(
        _Service(), object(), ATTEMPT_ROOT / "contracts" / "analysis", ATTEMPT_ROOT / "fixtures_t08",
        tmp_path / "state",
        source_roots={"codex_home": codex_home, "claude_home": tmp_path / "claude-home"},
    )
    controller.call("sessions.refresh", {})
    rows = controller.call("sessions.list", {"source": "codex"})["rows"]
    assert [row["title"] for row in rows] == ["索引里的会话"]

    detail = controller.call("sessions.get", {"local_projection_id": rows[0]["local_projection_id"]})
    assert [row["content"] for row in detail["messages"]] == ["用户的问题", "助手的回答"]
