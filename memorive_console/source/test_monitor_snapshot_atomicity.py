"""Regression coverage for atomic monitor snapshot publication on Windows."""

import json
import os
from pathlib import Path
import threading
import time
from types import MethodType

import pytest

import common
import manager as manager_module


def _permission_error(code):
    error = PermissionError(13, "synthetic transient target lock")
    error.winerror = code
    return error


def _temporary_files(path):
    return list(path.parent.glob(path.name + ".*.tmp"))


def test_write_json_retries_transient_windows_replace_lock(tmp_path, monkeypatch):
    target = tmp_path / "latest_snapshot.json"
    target.write_text('{"old":true}', encoding="utf-8")
    real_replace = common.os.replace
    attempts = []

    def transient(source, destination):
        attempts.append(1)
        if len(attempts) == 1:
            raise _permission_error(5)
        return real_replace(source, destination)

    monkeypatch.setattr(common.os, "replace", transient)
    monkeypatch.setattr(common.time, "sleep", lambda *_: None)

    common.write_json(target, {"sequence": 2})

    assert len(attempts) == 2
    assert common.read_json(target) == {"sequence": 2}
    assert _temporary_files(target) == []


def test_write_json_persistent_replace_lock_surfaces_and_removes_temp(tmp_path, monkeypatch):
    target = tmp_path / "latest_snapshot.json"
    target.write_text('{"old":true}', encoding="utf-8")

    def persistent(*_):
        raise _permission_error(32)

    monkeypatch.setattr(common.os, "replace", persistent)
    monkeypatch.setattr(common.time, "sleep", lambda *_: None)

    with pytest.raises(PermissionError):
        common.write_json(target, {"sequence": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
    assert _temporary_files(target) == []


def test_write_json_nontransient_replace_error_is_not_hidden(tmp_path, monkeypatch):
    target = tmp_path / "latest_snapshot.json"

    def denied(*_):
        raise _permission_error(13)

    monkeypatch.setattr(common.os, "replace", denied)
    monkeypatch.setattr(common.time, "sleep", lambda *_: None)

    with pytest.raises(PermissionError):
        common.write_json(target, {"sequence": 1})

    assert _temporary_files(target) == []


def test_manager_publish_is_serialized(tmp_path, monkeypatch):
    instance = object.__new__(manager_module.Manager)
    instance.root = Path(tmp_path)
    (instance.root / "monitor").mkdir()
    instance.lock = threading.RLock()
    sequence_lock = threading.Lock()
    sequence = {"value": 0}

    def snapshot(_self):
        with sequence_lock:
            sequence["value"] += 1
            return {"sequence": sequence["value"]}

    instance.snapshot = MethodType(snapshot, instance)
    real_write = common.write_json
    active_lock = threading.Lock()
    active = {"count": 0, "maximum": 0}

    def observed_write(path, value):
        with active_lock:
            active["count"] += 1
            active["maximum"] = max(active["maximum"], active["count"])
        try:
            time.sleep(0.03)
            real_write(path, value)
        finally:
            with active_lock:
                active["count"] -= 1

    monkeypatch.setattr(manager_module, "write_json", observed_write)
    start = threading.Barrier(9)

    def publish():
        start.wait()
        instance.publish()

    threads = [threading.Thread(target=publish) for _ in range(8)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert active["maximum"] == 1
    assert isinstance(common.read_json(instance.root / "monitor" / "latest_snapshot.json")["sequence"], int)


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics required")
def test_write_json_waits_for_real_windows_delete_share_lock(tmp_path):
    import ctypes
    from ctypes import wintypes

    target = tmp_path / "latest_snapshot.json"
    target.write_text('{"old":true}', encoding="utf-8")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        str(target),
        0x80000000,
        0x00000001 | 0x00000002,
        None,
        3,
        0x80,
        None,
    )
    invalid = wintypes.HANDLE(-1).value
    assert handle not in (None, invalid)

    released = threading.Event()

    def release():
        time.sleep(0.12)
        kernel32.CloseHandle(handle)
        released.set()

    thread = threading.Thread(target=release)
    thread.start()
    try:
        common.write_json(target, {"sequence": 3})
    finally:
        thread.join(timeout=2)

    assert released.is_set()
    assert common.read_json(target) == {"sequence": 3}
    assert _temporary_files(target) == []
