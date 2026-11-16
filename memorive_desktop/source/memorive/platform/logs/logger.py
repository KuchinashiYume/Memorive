"""Writing the runtime log: one entry point and two levels.

There is one way in: the module-level log(); log_error() is log(..., is_error=True), not a second
writer. The entry point adds the timestamp, serializes the entry as one JSON line and appends it;
_append() is the only place that writes the file. Entries are progress level (for people) or detail
level (for an AI reading the log); an error is always written at detail level. records.py writes
the accounting and reason records, reader.py reads one module's lines, retention.py removes old days.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from memorive.platform.logs.config import get_log_path
from memorive.platform.logs.lock import log_lock

LEVELS = ("progress", "detail")                    # for people / for an AI; errors are always detail
RECORD_TYPES = ("event", "accounting", "reason")   # the last two are written by records.py
_ERROR_CONTEXT_KEYS = ("step", "error")            # an error says which step and what went wrong; the module is top-level


class AppendOnlyViolation(RuntimeError):
    """The log file got smaller during a write, so something truncated or overwrote it.

    This catches only a file that shrank, not one overwritten with longer content. The log stays
    append-only because _append() is the only writer and opens the file with "a", there is no API
    that overwrites or clears it, and extraction refuses to write over the log; a test checks that
    earlier entries are never rewritten.
    """


class Logger:
    """The writer behind log() and log_error(), holding the log file's path. Modules call the
    module-level functions; tests make a Logger with a temporary path. Not exported by the package."""

    def __init__(self, log_path: Path | None = None):
        self.log_path = log_path or get_log_path()

    def log(self, module: str, event: str, *, level: str = "progress",
            context: dict | None = None, reason: str | None = None,
            record_type: str = "event", data: dict | None = None,
            is_error: bool = False) -> dict:
        """Write one entry: add the timestamp, serialize, append. Returns the entry written.

        is_error=True writes the entry at detail level; nothing is guessed from the event text.
        An error entry must carry a non-empty context with step and error, or it is refused.
        """
        if is_error:
            level = "detail"                       # errors are always detail level
        _validate(module, event, level, record_type)
        if is_error:
            _validate_error_context(context)   # a non-empty dict with non-empty step and error
        entry = self._build_entry(module, event, level, record_type, context, reason, data)
        self._append(_to_jsonl(entry))
        return entry

    def log_error(self, module: str, event: str, *, context: dict | None = None,
                  reason: str | None = None) -> dict:
        """Write an error entry: the same as log(..., is_error=True). context is required."""
        return self.log(module, event, context=context, reason=reason, is_error=True)

    # ── internal ────────────────────────────────────────────
    def _build_entry(self, module, event, level, record_type, context, reason, data) -> dict:
        entry = {
            "ts": _now_iso(),          # added here; callers never pass it
            "module": module,          # reader.py extracts a module's lines by this key
            "level": level,
            "record_type": record_type,
            "event": event,
        }
        if context is not None:
            entry["context"] = context
        if reason is not None:         # used mainly by reason records (record_type='reason')
            entry["reason"] = reason
        if data is not None:           # the fields of accounting and reason records
            entry["data"] = data
        return entry

    def _append(self, line: str) -> None:
        """The only place that writes the log file: always in append mode, never truncating."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # The same cross-process lock as retention.py, so no entry is lost while it replaces the file.
        with log_lock(self.log_path):
            before = self.log_path.stat().st_size if self.log_path.exists() else 0
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
            # The file only grows; if it is smaller after the write, something truncated or
            # overwrote it (see AppendOnlyViolation for what this does not catch).
            after = self.log_path.stat().st_size
        if after < before:
            raise AppendOnlyViolation(
                f"append-only 被破坏: 写后文件变小 ({before}→{after} 字节),疑覆盖 / 截断")


# ── the module-level entry points ─────────────────────────────────────────
_default: Logger | None = None


def _get_default() -> Logger:
    global _default
    current_path = get_log_path()
    if _default is None or _default.log_path != current_path:
        _default = Logger(current_path)
    return _default


def log(module: str, event: str, *, level: str = "progress", context: dict | None = None,
        reason: str | None = None, record_type: str = "event", data: dict | None = None,
        is_error: bool = False) -> dict:
    """Write a log entry. Usage: from memorive.platform.logs import log; log("DocumentConvert", "processing paper 3")"""
    return _get_default().log(module, event, level=level, context=context, reason=reason,
                              record_type=record_type, data=data, is_error=is_error)


def log_error(module: str, event: str, *, context: dict | None = None,
              reason: str | None = None) -> dict:
    """Write an error entry (detail level; context is required). Usage: from memorive.platform.logs import log_error"""
    return _get_default().log_error(module, event, context=context, reason=reason)


# ── helpers ─────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")   # 2026-07-01T14:32:05+08:00


def _to_jsonl(entry: dict) -> str:
    return json.dumps(entry, ensure_ascii=False) + "\n"   # non-ASCII text stays readable


def _validate(module: str, event: str, level: str, record_type: str) -> None:
    if not isinstance(module, str) or not module.strip():
        raise ValueError("module 必须是非空字符串 (不能为空或纯空白)")
    if not isinstance(event, str) or not event.strip():
        raise ValueError("event 必须是非空字符串 (不能为空或纯空白)")
    if level not in LEVELS:
        raise ValueError(f"未知 level: {level!r} (应为 {LEVELS})")
    if record_type not in RECORD_TYPES:
        raise ValueError(f"未知 record_type: {record_type!r} (应为 {RECORD_TYPES})")


def _validate_error_context(context) -> None:
    """An error entry's context must be a non-empty dict with non-empty step and error. For an error
    involving a model, an API, a file or a database, call / dependency / file / paper_id / chunk_id /
    input_summary help too (not required)."""
    if not (isinstance(context, dict) and context):
        raise ValueError(
            "错误日志 (is_error / log_error) 必须提供非空 context (dict);见设计文档 §1.5。")
    missing = [k for k in _ERROR_CONTEXT_KEYS
               if not (isinstance(context.get(k), str) and context.get(k).strip())]
    if missing:
        raise ValueError(
            f"错误日志 context 必含非空 {list(_ERROR_CONTEXT_KEYS)} (缺 / 空: {missing});"
            "达到「陌生 AI 光看一条即可定位: 哪个模块(module)+ 第几步(step)+ 什么错(error)」;"
            "涉外部依赖时建议再补 call / dependency / file / paper_id / chunk_id / input_summary。")
