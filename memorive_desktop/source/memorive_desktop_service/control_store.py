"""Window and navigation state of the desktop window, kept in control_store.json.

The file holds {schema_version, revision, updated_at, state, state_sha256}. A state
takes its known fields from what it is given and default values for the rest. A
file that cannot be read is moved aside as control_store.corrupt.<hash>.json and
replaced by the default state; a file of the first version (DesktopControlStore-v1)
is converted when it is first loaded.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, Callable, Mapping
import uuid

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from .errors import ControlStoreCorrupt
from .locator import StableLocator
from .protocol import canonical_json_bytes

SCHEMA_VERSION = "DesktopControlStore-v2"
ALLOWED_ROUTES = frozenset({"home", "settings", "inbox", "current-task", "messages", "sessions", "library",
                            "work-log", "leaderboard", "assistant", "chat", "research-settings"})
WINDOW_LIMITS = {"x": (-32768, 32768), "y": (-32768, 32768), "width": (640, 7680), "height": (480, 4320)}
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def default_state() -> dict[str, Any]:
    return {"route": "home",
            "window_bounds": {"x": 80, "y": 80, "width": 1280, "height": 800, "maximized": False},
            "panel_dimensions": {"left": 248, "right": 336, "bottom": 260},
            "filters": {}, "selected_locator": None, "event_cursor_by_job": {}, "pending_outbox_command_metadata": {}}


def _in(value: Any, low: int, high: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and low <= value <= high


def validate_state(value: Any) -> dict[str, Any]:
    """The stored form of a state: known fields only, defaults for missing ones."""
    if not isinstance(value, Mapping):
        raise ValueError("CONTROL_STORE_STATE_INVALID")
    state = {**default_state(), **{key: value[key] for key in default_state() if key in value}}
    window, panels, cursors = state["window_bounds"], state["panel_dimensions"], state["event_cursor_by_job"]
    for field, ok in (
            ("route", isinstance(state["route"], str) and state["route"] in ALLOWED_ROUTES),
            ("window_bounds", isinstance(window, Mapping) and isinstance(window.get("maximized"), bool)
             and all(_in(window.get(name), *limits) for name, limits in WINDOW_LIMITS.items())),
            ("panel_dimensions", isinstance(panels, Mapping)
             and all(_in(panels.get(name), 0, 2000) for name in ("left", "right", "bottom"))),
            ("event_cursor_by_job", isinstance(cursors, Mapping)
             and all(isinstance(job, str) and _in(at, 0, 2**63 - 1) for job, at in cursors.items())),
            ("filters", isinstance(state["filters"], Mapping)),
            ("pending_outbox_command_metadata", isinstance(state["pending_outbox_command_metadata"], Mapping))):
        if not ok:
            raise ValueError("CONTROL_STORE_STATE_INVALID:" + field)
    if state["selected_locator"] is not None:
        StableLocator.parse(state["selected_locator"])
    state["window_bounds"] = {name: window[name] for name in (*WINDOW_LIMITS, "maximized")}
    state["panel_dimensions"] = {name: panels[name] for name in ("bottom", "left", "right")}
    state["event_cursor_by_job"] = dict(sorted(cursors.items()))
    state["pending_outbox_command_metadata"] = dict(sorted(state["pending_outbox_command_metadata"].items()))
    return deepcopy(state)


def _lock_first_byte(stream: Any, locked: bool) -> None:
    stream.seek(0)
    if os.name == "nt":
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if locked else msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX if locked else fcntl.LOCK_UN)


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with temporary.open("xb") as stream:
        stream.write(canonical_json_bytes(dict(payload)) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class ControlStore:
    def __init__(self, profile_root: Path | str):
        self.profile_root = Path(profile_root)
        self.profile_root.mkdir(parents=True, exist_ok=True)
        self.path = self.profile_root / "control_store.json"
        self.lock_path = self.profile_root / ".control_store.lock"
        key = str(self.lock_path.resolve())
        with _LOCKS_GUARD:  # one thread lock per file, shared by every store object in this process
            self._thread_lock = _LOCKS.setdefault(key.casefold() if os.name == "nt" else key, threading.RLock())
        self.recovery_receipt_path = self.profile_root / "control_store_recovery.json"

    @contextmanager
    def _exclusive_lock(self):
        with self._thread_lock:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock_path.open("a+b") as stream:
                if stream.seek(0, os.SEEK_END) == 0:  # the lock covers one byte, so the file needs one
                    stream.write(b"\0")
                    stream.flush()
                    os.fsync(stream.fileno())
                _lock_first_byte(stream, True)
                try:
                    yield
                finally:
                    _lock_first_byte(stream, False)

    def _write_state(self, state: Mapping[str, Any], revision: int) -> dict[str, Any]:
        state = validate_state(state)
        # updated_at and state_sha256 are kept for earlier versions, which require them
        envelope = {"schema_version": SCHEMA_VERSION, "revision": revision, "state": state,
                    "updated_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                    "state_sha256": hashlib.sha256(canonical_json_bytes(state)).hexdigest().upper()}
        _atomic_write(self.path, envelope)
        return envelope

    def _read(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ControlStoreCorrupt("CONTROL_STORE_ENVELOPE_INVALID")
        if value.get("schema_version") == "DesktopControlStore-v1":
            state = default_state()
            for old, new in (("current_route", "route"), ("window", "window_bounds"), ("panels", "panel_dimensions"),
                             ("filters", "filters"), ("selected_locator", "selected_locator")):
                state[new] = value.get(old, state[new])
            return self._write_state(state, int(value.get("revision", 0)) + 1)
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ControlStoreCorrupt("CONTROL_STORE_SCHEMA_UNSUPPORTED")
        if not _in(value.get("revision"), 0, 2**63 - 1):
            raise ControlStoreCorrupt("CONTROL_STORE_REVISION_INVALID")
        return {**value, "state": validate_state(value.get("state"))}

    def _recover(self, raw: bytes, reason: str) -> dict[str, Any]:
        digest = hashlib.sha256(raw).hexdigest().upper()
        quarantine = self.profile_root / f"control_store.corrupt.{digest[:16]}.json"
        if not quarantine.exists() and self.path.exists():
            os.replace(self.path, quarantine)
        recovered = self._write_state(default_state(), 0)
        _atomic_write(self.recovery_receipt_path, {
            "schema_version": "ControlStoreRecoveryReceipt-v1", "reason_code": reason, "corrupt_bytes": len(raw),
            "corrupt_sha256": digest, "quarantine_name": quarantine.name, "raw_content_in_receipt": False,
            "status": "RECOVERED_TO_SAFE_DEFAULT"})
        return recovered

    def _load_unlocked(self, *, recover_corruption: bool = True) -> dict[str, Any]:
        if not self.path.exists():
            return self._write_state(default_state(), 0)
        raw = self.path.read_bytes()
        try:
            return self._read(json.loads(raw.decode("utf-8")))
        except OSError:
            raise
        except Exception as exc:
            if recover_corruption:
                return self._recover(raw, getattr(exc, "code", type(exc).__name__))
            if isinstance(exc, ControlStoreCorrupt):
                raise
            raise ControlStoreCorrupt("CONTROL_STORE_DECODE_FAILED") from exc

    def load(self, *, recover_corruption: bool = True) -> dict[str, Any]:
        with self._exclusive_lock():
            return self._load_unlocked(recover_corruption=recover_corruption)

    def save(self, state: Mapping[str, Any], *, expected_revision: int) -> dict[str, Any]:
        with self._exclusive_lock():
            current = self._load_unlocked(recover_corruption=False)["revision"]
            if current != expected_revision:
                raise ValueError(f"CONTROL_STORE_REVISION_CONFLICT:{expected_revision}:{current}")
            return self._write_state(state, expected_revision + 1)

    def update(self, mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self._exclusive_lock():
            current = self._load_unlocked(recover_corruption=False)
            mutator(current["state"])
            return self._write_state(current["state"], current["revision"] + 1)


__all__ = ["ALLOWED_ROUTES", "ControlStore", "SCHEMA_VERSION", "default_state", "validate_state"]
