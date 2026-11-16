"""Canonical JSON, and JSON files written whole. Shared by the settings, the model call records and
model execution.

canonical_json_bytes is the strict encoding: sorted keys, compact separators, UTF-8, and NaN or
infinity refused. canonical_sha256 is its SHA-256 in upper-case hex. write_json_atomic writes canonical
JSON and a newline to a temporary file in the same folder, flushes it to disk and moves it over the
target, so a reader never sees half a file.

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
