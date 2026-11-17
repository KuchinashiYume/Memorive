"""Made-up records for the page check, so that lists, rows and detail panels have something to show.

probe.py calls seed() once the desktop service has started on the throw-away profile and before the page opens.
Everything goes in through the product's own entry points, the same way the test console seeds its sessions
(memorive/desktop/test_console/product_adapter.py):

- inbox: automatic processing is turned off, then two small files are imported from the folder the inbox
  accepts files from (where the file picker copies them), so they stay queued. The first is starred: the inbox
  orders newly imported files by their import time read backwards, which differs from run to run, and a
  starred file comes first;
- messages: three messages (finished, needs review, failed) through MessagesProductController.post_event;
- sessions: one Codex conversation in the session source folder the service reads. The sessions page lists only
  the last 30 days, so it is dated yesterday; compare.py ignores dates and clock times.

The current task, the work log and the library show document jobs; they stay empty here.
Every other value is fixed, so two runs record the same records. Change them only together with both runs.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SESSION_ID = "01b00000-0000-7000-8000-0000000000c1"
FILES = {
    "研究笔记_样例.md": "# 研究笔记（样例）\n\n这是一份编造的笔记，用来检查收件箱的列表和详情。\n\n- 第一条\n- 第二条\n",
    "meeting-notes.txt": "Made-up meeting notes for the page check.\nNothing here is real.\n",
}
MESSAGES = [
    ("success", "JOB_COMPLETED", "GREEN", "样例任务已完成", "编造的完成消息，用来检查消息列表。", "2026-01-05T09:00:00+00:00"),
    ("review", "ACTION_REQUIRED", "YELLOW", "样例任务等待确认", "编造的待确认消息，打开详情可以看到正文。", "2026-01-05T09:10:00+00:00"),
    ("error", "JOB_FAILED", "RED", "样例任务失败", "编造的失败消息：没有找到可用的模型设置。", "2026-01-05T09:20:00+00:00"),
]
TURNS = [
    ("user", "帮我整理一下这周读过的三篇文章的要点。"),
    ("assistant", "好的。下面按文章列出要点：\n\n1. 第一篇讲方法。\n2. 第二篇讲数据。\n3. 第三篇讲结论。"),
    ("user", "第二篇的数据来源是什么？"),
    ("assistant", "文中写的是公开数据集，没有给出具体版本。"),
]


def seed(api, sandbox: Path, codex_home: Path) -> dict:
    """Put the made-up records in; returns what each step answered, for run.json."""
    out = {"auto_run": api.call("inbox.set_auto_run", {"enabled": False})}
    folder = api._inbox_source_root / "page-check"
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in FILES.items():
        (folder / name).write_text(text, encoding="utf-8", newline="\n")
        paths.append(str(folder / name))
    receipt = api.call("inbox.import_paths", {"paths": paths, "request_id": "page-check-import", "source_kind": "picker"})
    first = receipt["items"][0]["item"]["item_id"]
    out["starred"] = api.call("inbox.set_starred", {"item_id": first, "starred": True}).get("starred")
    out["messages"] = [api._messages.post_event(_event(*row))[0] for row in MESSAGES]
    _codex_session(codex_home)
    out["session"] = SESSION_ID
    return out


def _event(kind, event_type, severity, title, body, occurred_at):
    key = "page-check-" + kind
    return {
        "event_id": key, "event_type": event_type, "job_id": key, "run_id": "page-check", "attempt_id": key,
        "node_id": None, "root_cause": "PAGE_CHECK_" + kind.upper(), "occurred_at": occurred_at,
        "severity": severity, "message_required": True, "safe_title": title, "safe_summary": body,
        "safe_body": body, "target_locator": "memorive://job/" + key,
        "protected_bulk_read": severity in {"RED", "YELLOW"}, "attachment_refs": [],
        "status_axes": {"lifecycle_status": "completed", "verification_result": "NOT_ASSESSED",
                        "acceptance_verdict": "NOT_ASSESSED", "capability_status": "AVAILABLE"},
        "report_period": None,
    }


def _codex_session(codex_home: Path) -> None:
    """One conversation as Codex stores it: the rollout file and its row in state_5.sqlite."""
    start = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=1)
    day = start.strftime("%Y-%m-%d")
    rollout = codex_home / "sessions" / day[:4] / day[5:7] / day[8:] / f"rollout-{SESSION_ID}.jsonl"
    rollout.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"timestamp": f"{day}T08:00:00Z", "type": "session_meta",
             "payload": {"id": SESSION_ID, "cwd": "C:\\samples"}}]
    for index, (role, text) in enumerate(TURNS):
        rows.append({"timestamp": f"{day}T08:00:{index + 1:02d}Z", "type": "response_item",
                     "payload": {"type": "message", "role": role,
                                 "content": [{"type": "input_text" if role == "user" else "output_text",
                                              "text": text}]}})
    rollout.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    with sqlite3.connect(codex_home / "state_5.sqlite") as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, "
                           "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, cwd TEXT NOT NULL, "
                           "title TEXT NOT NULL, preview TEXT NOT NULL, model TEXT)")
        connection.execute("INSERT OR REPLACE INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (SESSION_ID, str(rollout), int(start.timestamp()), int(start.timestamp()) + 4, "C:\\samples",
                            "整理三篇文章的要点（样例）", "帮我整理一下这周读过的三篇文章的要点。", "sample-model"))
