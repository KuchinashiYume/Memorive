"""Canonical JSON, and JSON files written whole. Used by the settings (contracts.py, store.py),
model execution (memorive/models/execution/runs.py), the reply size rule, the exam contracts
(memorive/exams/outcomes.py) and the browser companion's site profiles.

canonical_json_bytes is the strict encoding: sorted keys, compact separators, UTF-8, and NaN or
infinity refused. canonical_sha256 is its SHA-256 in upper-case hex. write_json_atomic writes canonical
JSON and a newline to a temporary file in the same folder, flushes it to disk and moves it over the
target, so a reader never sees half a file. write_json_indented does the same with indented JSON; the
desktop (runtime.py) writes its profile and UI state files with it.

Some other packages keep their own encoder without the NaN check (the "lenient" one); they are not
merged here, because the two give different results for non-finite numbers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping
import uuid

from memorive.platform.windows_paths import windows_io_path


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def write_json_atomic(path: Path, payload: Mapping[str, Any], before_replace: Callable | None = None) -> None:
    """Write payload as canonical JSON to path: to a temporary file in the same folder, flushed to disk,
    then moved over path, so a reader never sees half a file. before_replace(stage, temporary), when
    given, runs just before the move (the settings store's tests inject faults there).

    The settings store writes through it (SettingsStore.write_json); model execution writes its
    snapshots and results with it."""
    encoded = canonical_json_bytes(dict(payload)) + b"\n"
    # Same folder, short name: Windows installations may still enforce MAX_PATH.
    temporary = path.parent / f".{uuid.uuid4().hex}.tmp"
    with temporary.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    if before_replace is not None:
        before_replace("before_replace", temporary)
    os.replace(temporary, path)


def write_json_indented(path: Path, payload: dict[str, Any]) -> None:
    """Write payload to path as indented, sorted, UTF-8 JSON and a newline, replacing the file whole
    (temporary file in the same folder, flushed, then moved over). The parent folder must exist and the
    text be at most 4 MiB. ProductApi writes its profile and UI state files with it (also as
    ProductApi._write_json_file); the desk assistant and the browser companion write their files with it.
    """
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    if len(encoded) > 4 * 1024 * 1024:
        raise ValueError("JSON_EXPORT_TOO_LARGE")
    path = Path(path).resolve(strict=False)
    target_io = windows_io_path(path)
    if not windows_io_path(path.parent).is_dir():
        raise ValueError("JSON_EXPORT_PARENT_MISSING")
    temporary = path.parent / f".json.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    temporary_io = windows_io_path(temporary)
    try:
        with temporary_io.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_io, target_io)
    finally:
        if temporary_io.exists():
            temporary_io.unlink()


def lenient_sha256(value: Any) -> str:
    """SHA-256, upper-case hex, of value as sorted, compact UTF-8 JSON. Unlike canonical_sha256 it
    lets NaN and infinity through; the desktop (runtime.py) and the desk assistant hash their receipts
    and projections with it."""
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest().upper()
