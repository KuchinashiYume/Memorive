"""Log retention ("日志保留" in Settings, preferences.retention_days).

The background service calls runtime_log.retention.apply at start and once a day. These tests
call it directly with a fixed clock: event and reason entries older than the limit go, accounting
entries and lines without a readable time stay, files of earlier service starts and old window
start-up logs are deleted, the running service's files are not, and an unusable value changes
nothing.

Appending and the retention pass share one lock (runtime_log.lock, the file next to the log): an
entry written by another thread or process while a pass replaces the log waits and is kept, and
the logger refuses any shrink of the log, whenever it happens.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime_log import retention
from runtime_log.lock import LOCK_SUFFIX, log_lock
from runtime_log.logger import AppendOnlyViolation, Logger

NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)


def entry(record_type: str, when: datetime, event: str) -> str:
    return json.dumps({"ts": when.isoformat(timespec="seconds"), "module": "M", "level": "progress",
                       "record_type": record_type, "event": event}, ensure_ascii=False) + "\n"


def events(path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line)["event"])
        except (ValueError, KeyError, TypeError):
            out.append(line)
    return out


def set_age(path, days: float) -> None:
    stamp = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


@pytest.mark.parametrize("keep_days", [7, 30, 90])
def test_entries_at_the_limit_stay_and_older_ones_go(tmp_path, keep_days):
    log = tmp_path / "log.txt"
    limit = NOW - timedelta(days=keep_days)
    log.write_text(
        entry("event", limit - timedelta(seconds=1), "just older")
        + entry("event", limit, "exactly at the limit")
        + entry("reason", limit - timedelta(days=1), "old reason")
        + entry("event", limit + timedelta(seconds=1), "just newer")
        + entry("event", NOW, "today"),
        encoding="utf-8",
    )
    assert retention.prune_log(log, keep_days, now=NOW) == {"kept": 3, "removed": 2}
    assert events(log) == ["exactly at the limit", "just newer", "today"]


def test_accounting_entries_stay_whatever_their_age(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text(entry("accounting", NOW - timedelta(days=400), "old cost")
                   + entry("event", NOW - timedelta(days=400), "old event"), encoding="utf-8")
    retention.prune_log(log, 30, now=NOW)
    assert events(log) == ["old cost"]


def test_lines_without_a_readable_time_stay(tmp_path):
    log = tmp_path / "log.txt"
    old = (NOW - timedelta(days=400)).isoformat()
    lines = [
        "not json at all\n",
        json.dumps({"record_type": "event", "event": "no ts"}) + "\n",
        json.dumps({"ts": "last tuesday", "record_type": "event", "event": "bad ts"}) + "\n",
        json.dumps({"ts": 12345, "record_type": "event", "event": "number ts"}) + "\n",
        json.dumps({"ts": old, "record_type": "event", "event": "old"}) + "\n",
    ]
    log.write_text("".join(lines), encoding="utf-8")
    assert retention.prune_log(log, 30, now=NOW) == {"kept": 4, "removed": 1}
    assert events(log) == ["not json at all", "no ts", "bad ts", "number ts"]


def test_a_time_without_offset_is_read_as_local_time(tmp_path):
    log = tmp_path / "log.txt"
    local_old = (NOW - timedelta(days=31)).astimezone().replace(tzinfo=None)
    log.write_text(json.dumps({"ts": local_old.isoformat(), "record_type": "event", "event": "naive"}) + "\n",
                   encoding="utf-8")
    assert retention.prune_log(log, 30, now=NOW)["removed"] == 1


def test_nothing_to_remove_leaves_the_file_untouched(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text(entry("event", NOW, "today"), encoding="utf-8")
    set_age(log, 3)
    before = (log.read_bytes(), log.stat().st_mtime)
    assert retention.prune_log(log, 30, now=NOW) == {"kept": 1, "removed": 0}
    assert (log.read_bytes(), log.stat().st_mtime) == before
    assert not list(tmp_path.glob("*.retention.tmp"))


def test_a_second_pass_removes_nothing(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text(entry("event", NOW - timedelta(days=60), "old") + entry("event", NOW, "new"), encoding="utf-8")
    assert retention.prune_log(log, 30, now=NOW)["removed"] == 1
    after_first = log.read_bytes()
    assert retention.prune_log(log, 30, now=NOW)["removed"] == 0
    assert log.read_bytes() == after_first


def test_a_missing_log_is_not_an_error(tmp_path):
    assert retention.prune_log(tmp_path / "nothing.txt", 30, now=NOW) == {"kept": 0, "removed": 0}


def test_an_entry_appended_while_the_pass_runs_is_kept(tmp_path, monkeypatch):
    # The logger appends after the pass has read the log and before it takes the lock.
    log = tmp_path / "log.txt"
    log.write_text(entry("event", NOW - timedelta(days=60), "old") + entry("event", NOW, "new"), encoding="utf-8")
    real_mkstemp = retention.tempfile.mkstemp

    def mkstemp(*args, **kwargs):
        Logger(log).log("M", "appended meanwhile")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(retention.tempfile, "mkstemp", mkstemp)
    assert retention.prune_log(log, 30, now=NOW)["removed"] == 1
    assert events(log) == ["new", "appended meanwhile"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows refuses to replace a file another handle has open")
def test_a_log_held_open_by_a_writer_is_left_for_the_next_pass(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text(entry("event", NOW - timedelta(days=60), "old") + entry("event", NOW, "new"), encoding="utf-8")
    with open(log, "a", encoding="utf-8") as writer:
        before = log.read_bytes()
        assert retention.prune_log(log, 30, now=NOW) == {"kept": 2, "removed": 0}
        writer.write(entry("event", NOW, "written while open"))
    assert log.read_bytes().startswith(before)
    assert events(log) == ["old", "new", "written while open"]
    assert not list(tmp_path.glob("*.retention.tmp"))


def test_the_logger_appends_after_a_pass(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text(entry("event", datetime.now(timezone.utc) - timedelta(days=60), "old"), encoding="utf-8")
    logger = Logger(log)
    logger.log("M", "before the pass")
    assert retention.prune_log(log, 30)["removed"] == 1   # the file is replaced
    logger.log("M", "after the pass")
    assert events(log) == ["before the pass", "after the pass"]


def test_old_files_of_earlier_service_starts_go_and_the_running_ones_stay(tmp_path):
    launch = tmp_path / "launch"
    launch.mkdir()
    names = {
        "service-old.stdout.log": 40, "service-old.stderr.log": 40, "service-old.endpoint.json": 40,
        "service-old.stop.json": 40, "service-old.shutdown_order.json": 40,
        "service-recent.stdout.log": 2,
        "service-running.stdout.log": 40, "service-running.endpoint.json": 40,
        "other.log": 40,
    }
    for name, age in names.items():
        (launch / name).write_text("x", encoding="utf-8")
        set_age(launch / name, age)
    removed = retention.prune_files(launch, retention.SERVICE_LAUNCH_FILES, 30, skip_prefix="service-running", now=NOW)
    assert removed == 5
    assert sorted(p.name for p in launch.iterdir()) == [
        "other.log", "service-recent.stdout.log", "service-running.endpoint.json", "service-running.stdout.log"]


def test_apply_covers_the_log_the_service_files_and_the_start_up_logs(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text(entry("event", NOW - timedelta(days=10), "ten days") + entry("event", NOW, "today"), encoding="utf-8")
    launch = tmp_path / "launch"
    evidence = tmp_path / "evidence"
    launch.mkdir()
    evidence.mkdir()
    for folder, name, age in [(launch, "service-a.stdout.log", 10), (launch, "service-b.stdout.log", 10),
                              (evidence, "webview_startup_1.log", 10), (evidence, "webview_startup_2.log", 1),
                              (evidence, "other.json", 10)]:
        (folder / name).write_text("x", encoding="utf-8")
        set_age(folder / name, age)
    result = retention.apply(7, log_path=log, launch_folder=launch, running_launch="service-b",
                             startup_log_folder=evidence, now=NOW)
    assert result == {"log_entries_removed": 1, "service_files_removed": 1, "startup_logs_removed": 1}
    assert events(log) == ["today"]
    assert sorted(p.name for p in launch.iterdir()) == ["service-b.stdout.log"]
    assert sorted(p.name for p in evidence.iterdir()) == ["other.json", "webview_startup_2.log"]


@pytest.mark.parametrize("keep_days", [0, 366, -1, "30", 30.0, True, None])
def test_an_unusable_value_changes_nothing(tmp_path, keep_days):
    log = tmp_path / "log.txt"
    log.write_text(entry("event", NOW - timedelta(days=400), "very old"), encoding="utf-8")
    launch = tmp_path / "launch"
    launch.mkdir()
    (launch / "service-a.stdout.log").write_text("x", encoding="utf-8")
    set_age(launch / "service-a.stdout.log", 400)
    before = log.read_bytes()
    result = retention.apply(keep_days, log_path=log, launch_folder=launch, startup_log_folder=launch, now=NOW)
    assert result == {"log_entries_removed": 0, "service_files_removed": 0, "startup_logs_removed": 0}
    assert log.read_bytes() == before
    assert (launch / "service-a.stdout.log").exists()


@pytest.mark.parametrize("keep_days", [1, 365])
def test_the_ends_of_the_allowed_range_are_applied(tmp_path, keep_days):
    log = tmp_path / "log.txt"
    log.write_text(entry("event", NOW - timedelta(days=keep_days + 1), "older"), encoding="utf-8")
    assert retention.apply(keep_days, log_path=log, now=NOW)["log_entries_removed"] == 1


def test_an_entry_appended_while_the_copy_is_flushed_to_disk_is_kept(tmp_path, monkeypatch):
    # Another process appends while the kept part is being written out (flush and fsync).
    log = tmp_path / "log.txt"
    log.write_text(entry("event", NOW - timedelta(days=60), "old") + entry("event", NOW, "new"), encoding="utf-8")
    real_fsync = os.fsync
    state = {"done": False}

    def fsync(fd):
        if not state["done"]:
            state["done"] = True
            with open(log, "a", encoding="utf-8") as other:
                other.write(entry("event", NOW, "appended during fsync"))
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    retention.prune_log(log, 30, now=NOW)
    assert events(log) == ["new", "appended during fsync"]


def test_a_pass_leaves_no_temporary_file_behind(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text(entry("event", NOW - timedelta(days=60), "old") + entry("event", NOW, "today"), encoding="utf-8")
    assert retention.prune_log(log, 30, now=NOW)["removed"] == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["log.txt", "log.txt" + LOCK_SUFFIX]


def test_temporary_files_of_an_interrupted_pass_are_cleared(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text(entry("event", NOW - timedelta(days=60), "old") + entry("event", NOW, "today"), encoding="utf-8")
    stale = tmp_path / ("log.txt.interrupted" + retention.TEMPORARY_SUFFIX)
    fresh = tmp_path / ("log.txt.running" + retention.TEMPORARY_SUFFIX)
    for leftover in (stale, fresh):
        leftover.write_text("partial", encoding="utf-8")
    two_hours_ago = datetime.now().timestamp() - 2 * 3600
    os.utime(stale, (two_hours_ago, two_hours_ago))
    retention.prune_log(log, 30, now=NOW)
    assert not stale.exists() and fresh.exists()


def test_a_log_replaced_by_another_pass_meanwhile_is_left_alone(tmp_path, monkeypatch):
    log = tmp_path / "log.txt"
    log.write_text(entry("event", NOW - timedelta(days=60), "old") + entry("event", NOW, "today"), encoding="utf-8")
    real_mkstemp = retention.tempfile.mkstemp
    other = "".join(entry("event", NOW, f"written by the other pass, a longer entry {n}") for n in range(3))

    def mkstemp(*args, **kwargs):
        replacement = tmp_path / "other-pass.tmp"
        replacement.write_text(other, encoding="utf-8")
        os.replace(replacement, log)
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(retention.tempfile, "mkstemp", mkstemp)
    assert retention.prune_log(log, 30, now=NOW) == {"kept": 2, "removed": 0}
    assert log.read_text(encoding="utf-8") == other
    assert not list(tmp_path.glob("*" + retention.TEMPORARY_SUFFIX))


def _hold_the_pass_inside_its_lock(monkeypatch, action):
    """Run action while prune_log holds the log's lock, right before it copies the tail and replaces."""
    real = retention._remove_stale_temporaries

    def inside(path, *, keep):
        action()
        return real(path, keep=keep)

    monkeypatch.setattr(retention, "_remove_stale_temporaries", inside)


def _wait_for(condition, seconds=30):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def test_an_append_from_another_thread_waits_for_the_pass(tmp_path, monkeypatch):
    log = tmp_path / "log.txt"
    log.write_text(entry("event", NOW - timedelta(days=60), "old") + entry("accounting", NOW - timedelta(days=400),
                                                                            "old cost"), encoding="utf-8")
    started, done = threading.Event(), threading.Event()
    seen = {}

    def append():
        started.set()
        Logger(log).log("M", "cost from another writer", record_type="accounting", data={"tokens": 3})
        done.set()

    def while_locked():
        writer = threading.Thread(target=append)
        writer.start()
        seen["writer"] = writer
        started.wait(10)
        seen["finished_while_locked"] = done.wait(1.0)

    _hold_the_pass_inside_its_lock(monkeypatch, while_locked)
    assert retention.prune_log(log, 30, now=NOW)["removed"] == 1
    seen["writer"].join(30)
    assert seen["finished_while_locked"] is False
    assert events(log) == ["old cost", "cost from another writer"]


CHILD = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from runtime_log.logger import Logger
Path(sys.argv[3]).write_text("about to append", encoding="utf-8")
Logger(Path(sys.argv[2])).log("M", "cost from another process", record_type="accounting", data={"tokens": 5})
Path(sys.argv[4]).write_text("appended", encoding="utf-8")
"""


def test_an_append_from_another_process_waits_for_the_pass(tmp_path, monkeypatch):
    log = tmp_path / "log.txt"
    log.write_text(entry("event", NOW - timedelta(days=60), "old") + entry("event", NOW, "new"), encoding="utf-8")
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    started, finished = tmp_path / "started", tmp_path / "finished"
    source_root = str(Path(retention.__file__).resolve().parents[1])
    seen = {}

    def while_locked():
        seen["child"] = subprocess.Popen([sys.executable, str(script), source_root, str(log), str(started),
                                          str(finished)])
        _wait_for(started.exists)
        time.sleep(1.0)
        seen["finished_while_locked"] = finished.exists()

    _hold_the_pass_inside_its_lock(monkeypatch, while_locked)
    assert retention.prune_log(log, 30, now=NOW)["removed"] == 1
    assert seen["child"].wait(timeout=60) == 0
    assert seen["finished_while_locked"] is False
    assert events(log) == ["new", "cost from another process"]


def _shrink_before_writing(monkeypatch, log):
    """Something cuts the older half of the entries between the logger's size check and its write."""
    import runtime_log.logger as logger_module

    real_open = open

    def opening(path, mode="r", *args, **kwargs):
        if str(path) == str(log) and mode == "a":
            lines = log.read_bytes().splitlines(keepends=True)
            log.write_bytes(b"".join(lines[len(lines) // 2:]))
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(logger_module, "open", opening, raising=False)


def _logger_with_history(log):
    logger = Logger(log)
    for number in range(20):
        logger.log("M", f"entry {number}")
    return logger


def test_the_logger_refuses_a_log_that_shrank_while_it_wrote(tmp_path, monkeypatch):
    log = tmp_path / "log.txt"
    logger = _logger_with_history(log)
    _shrink_before_writing(monkeypatch, log)
    with pytest.raises(AppendOnlyViolation):
        logger.log("M", "after the cut")


def test_a_shrink_is_refused_even_right_after_a_retention_pass(tmp_path, monkeypatch):
    log = tmp_path / "log.txt"
    log.write_text(entry("event", datetime.now(timezone.utc) - timedelta(days=60), "old"), encoding="utf-8")
    logger = _logger_with_history(log)
    assert retention.prune_log(log, 30)["removed"] == 1
    _shrink_before_writing(monkeypatch, log)
    with pytest.raises(AppendOnlyViolation):
        logger.log("M", "after the cut")


def test_the_logger_and_the_pass_use_the_lock_next_to_the_log(tmp_path):
    log = tmp_path / "logs" / "log.txt"
    Logger(log).log("M", "first")
    assert (tmp_path / "logs" / ("log.txt" + LOCK_SUFFIX)).is_file()
    with log_lock(log):
        pass
    Logger(log).log("M", "second")
    assert events(log) == ["first", "second"]
