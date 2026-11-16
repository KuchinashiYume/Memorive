"""The admission log: one JSON line per review-state move, one file per year.

Each line holds the time, the card's data ID, the old and new state, the trigger and the reason.
The files live outside the library, in MEMORIVE_KNOWLEDGE_ADMISSION_LOG_DIR or by default in the
knowledge_admission folder of the ops area (runtime_log/config.py, ops_root). This module only
appends and reads; it offers no way to rewrite a file. The log is operational data: it is never
embedded or searched.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from runtime_log.config import ops_root

from .errors import KnowledgeAdmissionLogAppendError

ENV_KNOWLEDGE_ADMISSION_LOG_DIR = "MEMORIVE_KNOWLEDGE_ADMISSION_LOG_DIR"


def log_dir() -> Path:
    """The folder of the yearly log files. retrieval/candidate_retrieval.py reads it too."""
    return Path(os.environ.get(ENV_KNOWLEDGE_ADMISSION_LOG_DIR) or ops_root() / "knowledge_admission")


def _log_path(year: int) -> Path:
    return log_dir() / f"KNOWLEDGE_ADMISSION_log_{year}.jsonl"


def preflight() -> None:
    """Make sure the log folder exists and can be written. Called before a card is changed, so a
    missing or read-only folder stops the move instead of leaving it unlogged."""
    d = log_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise KnowledgeAdmissionLogAppendError(f"KNOWLEDGE_ADMISSION_log 目录不可建({d}): {e}") from e
    if not os.access(d, os.W_OK):
        raise KnowledgeAdmissionLogAppendError(f"KNOWLEDGE_ADMISSION_log 目录不可写: {d}")


def append_transition(*, data_id: str, from_status: str, to_status: str,
                      trigger: str, reason: str, event_category: str = "review_status") -> dict:
    """Append one move to this year's file and return the line written. transition() has checked
    every field before it calls this."""
    now = datetime.now().astimezone()
    entry = {"ts": now.isoformat(timespec="seconds"), "module": "KNOWLEDGE_ADMISSION", "record_type": "reason",
             "event_category": event_category, "data_id": data_id,
             "from": from_status, "to": to_status, "trigger": trigger, "reason": reason}
    path = _log_path(now.year)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        raise KnowledgeAdmissionLogAppendError(f"KNOWLEDGE_ADMISSION_log 落盘失败({type(e).__name__}): {path}: {e}") from e
    return entry


def read_records(year: int) -> list[dict]:
    """Every line of one year's file; [] when there is none. A line that is not JSON, such as a
    half-written last line after a crash, is skipped."""
    path = _log_path(year)
    if not path.exists():
        return []
    out: list[dict] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out
