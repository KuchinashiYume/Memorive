"""Keep logs only for the number of days chosen in Settings ("日志保留").

Three kinds of log are covered:

- entries of the runtime log (log.txt): events and reasons older than the limit are dropped. Accounting
  entries stay, because they are the cost record, and so does any line whose time cannot be read;
- the files each start of the background service leaves behind (its output and small state files);
- the start-up log of the window (webview_startup_<pid>.log).

Nothing newer than the limit is removed, and the files of the running service are never touched.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

PRUNED_RECORD_TYPES = frozenset({"event", "reason"})
SERVICE_LAUNCH_FILES = ("service-*.stdout.log", "service-*.stderr.log", "service-*.endpoint.json",
                        "service-*.stop.json", "service-*.shutdown_order.json")
STARTUP_LOG_FILES = ("webview_startup_*.log",)


def _entry_time(line: bytes) -> tuple[datetime | None, object]:
    try:
        entry = json.loads(line)
        stamp = datetime.fromisoformat(entry["ts"])
    except (ValueError, KeyError, TypeError):
        return None, None
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()
    return stamp, entry.get("record_type")


def prune_log(log_path: Path | str, keep_days: int, *, now: datetime | None = None) -> dict[str, int]:
    """Drop the event and reason entries of a runtime log that are older than keep_days."""
    path = Path(log_path)
    result = {"kept": 0, "removed": 0}
    if not path.is_file():
        return result
    limit = (now or datetime.now(timezone.utc)) - timedelta(days=keep_days)
    raw = path.read_bytes()
    kept = []
    for line in raw.splitlines(keepends=True):
        stamp, kind = _entry_time(line)
        if stamp is not None and kind in PRUNED_RECORD_TYPES and stamp < limit:
            result["removed"] += 1
        else:
            kept.append(line)
            result["kept"] += 1
    if not result["removed"]:
        return result
    temporary = path.with_name(path.name + ".retention.tmp")
    try:
        with open(temporary, "wb") as handle:
            handle.writelines(kept)
            with open(path, "rb") as current:      # lines another process appended meanwhile
                current.seek(len(raw))
                handle.write(current.read())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        # The log is in use; it is left as it is and tried again next time.
        try:
            temporary.unlink()
        except OSError:
            pass
        return {"kept": result["kept"] + result["removed"], "removed": 0}
    return result


def prune_files(folder: Path | str, patterns: Iterable[str], keep_days: int, *,
                skip_prefix: str | None = None, now: datetime | None = None) -> int:
    """Delete the files in folder that match patterns and were last changed more than keep_days ago."""
    folder = Path(folder)
    if not folder.is_dir():
        return 0
    limit = (now.timestamp() if now else time.time()) - keep_days * 86400
    removed = 0
    for pattern in patterns:
        for path in folder.glob(pattern):
            if skip_prefix and path.name.startswith(skip_prefix):
                continue
            try:
                if path.is_file() and path.stat().st_mtime < limit:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


def apply(keep_days: object, *, log_path: Path | str | None = None, launch_folder: Path | str | None = None,
          running_launch: str | None = None, startup_log_folder: Path | str | None = None,
          now: datetime | None = None) -> dict[str, int]:
    """Apply the retention chosen in Settings; an unusable value changes nothing."""
    result = {"log_entries_removed": 0, "service_files_removed": 0, "startup_logs_removed": 0}
    if isinstance(keep_days, bool) or not isinstance(keep_days, int) or not 1 <= keep_days <= 365:
        return result
    if log_path is not None:
        result["log_entries_removed"] = prune_log(log_path, keep_days, now=now)["removed"]
    if launch_folder is not None:
        result["service_files_removed"] = prune_files(
            launch_folder, SERVICE_LAUNCH_FILES, keep_days, skip_prefix=running_launch, now=now)
    if startup_log_folder is not None:
        result["startup_logs_removed"] = prune_files(startup_log_folder, STARTUP_LOG_FILES, keep_days, now=now)
    return result
