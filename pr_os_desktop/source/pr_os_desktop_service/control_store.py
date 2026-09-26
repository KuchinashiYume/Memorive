from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Callable, Mapping
import uuid

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from .errors import ControlStoreCorrupt, SensitivePersistenceRejected
from .locator import StableLocator
from .protocol import canonical_json_bytes


SCHEMA_VERSION = "DesktopControlStore-v2"
ALLOWED_ROUTES = frozenset(
    {"home", "settings", "inbox", "current-task", "messages", "sessions", "library", "work-log", "leaderboard", "assistant", "chat", "research-settings"}
)
_SENSITIVE_KEY = re.compile(
    r"(^|_)(api[_-]?key|authorization|cookie|credential|password|secret|token|private[_-]?payload|gold|holdout)($|_)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = (
    re.compile(r"(?i)(?:^|[^A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_STATE_LOCKS_GUARD = threading.Lock()
_STATE_LOCKS: dict[str, threading.RLock] = {}


def _thread_lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve()).casefold() if os.name == "nt" else str(path.resolve())
    with _STATE_LOCKS_GUARD:
        lock = _STATE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STATE_LOCKS[key] = lock
        return lock


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def default_state() -> dict[str, Any]:
    return {
        "route": "home",
        "window_bounds": {"x": 80, "y": 80, "width": 1280, "height": 800, "maximized": False},
        "panel_dimensions": {"left": 248, "right": 336, "bottom": 260},
        "filters": {},
        "selected_locator": None,
        "event_cursor_by_job": {},
        "pending_outbox_command_metadata": {},
    }


def _scan_sensitive(value: Any, path: str = "state") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            rendered = str(key)
            if _SENSITIVE_KEY.search(rendered):
                raise SensitivePersistenceRejected(f"SENSITIVE_KEY:{path}.{rendered}")
            _scan_sensitive(child, f"{path}.{rendered}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_sensitive(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in _SENSITIVE_VALUE):
            raise SensitivePersistenceRejected(f"SENSITIVE_VALUE:{path}")


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"CONTROL_STORE_{field.upper()}_INVALID")
    return value


def _validate_filters(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or len(value) > 32:
        raise ValueError("CONTROL_STORE_FILTERS_INVALID")
    accepted: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key):
            raise ValueError("CONTROL_STORE_FILTER_KEY_INVALID")
        if isinstance(item, (str, bool, int, float)) or item is None:
            accepted[key] = item
        elif isinstance(item, list) and len(item) <= 32 and all(isinstance(child, str) and len(child) <= 256 for child in item):
            accepted[key] = list(item)
        else:
            raise ValueError("CONTROL_STORE_FILTER_VALUE_INVALID")
    return accepted


def validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("CONTROL_STORE_STATE_INVALID")
    required = set(default_state())
    if set(value) != required:
        raise ValueError("CONTROL_STORE_STATE_FIELDS_INVALID")
    _scan_sensitive(value)
    route = value["route"]
    if route not in ALLOWED_ROUTES:
        raise ValueError("CONTROL_STORE_ROUTE_INVALID")
    bounds = value["window_bounds"]
    if not isinstance(bounds, Mapping) or set(bounds) != {"x", "y", "width", "height", "maximized"}:
        raise ValueError("CONTROL_STORE_WINDOW_INVALID")
    window = {
        "x": _bounded_int(bounds["x"], "window_x", -32768, 32768),
        "y": _bounded_int(bounds["y"], "window_y", -32768, 32768),
        "width": _bounded_int(bounds["width"], "window_width", 640, 7680),
        "height": _bounded_int(bounds["height"], "window_height", 480, 4320),
        "maximized": bounds["maximized"],
    }
    if not isinstance(window["maximized"], bool):
        raise ValueError("CONTROL_STORE_MAXIMIZED_INVALID")
    panels = value["panel_dimensions"]
    if not isinstance(panels, Mapping) or set(panels) != {"left", "right", "bottom"}:
        raise ValueError("CONTROL_STORE_PANELS_INVALID")
    accepted_panels = {name: _bounded_int(panels[name], f"panel_{name}", 0, 2000) for name in sorted(panels)}
    locator = value["selected_locator"]
    if locator is not None:
        StableLocator.parse(locator)
    cursors = value["event_cursor_by_job"]
    if not isinstance(cursors, Mapping) or len(cursors) > 512:
        raise ValueError("CONTROL_STORE_CURSOR_MAP_INVALID")
    accepted_cursors = {}
    for job_id, sequence in cursors.items():
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", job_id):
            raise ValueError("CONTROL_STORE_CURSOR_JOB_ID_INVALID")
        accepted_cursors[job_id] = _bounded_int(sequence, "event_cursor", 0, 2**63 - 1)
    outbox = value["pending_outbox_command_metadata"]
    if not isinstance(outbox, Mapping) or len(outbox) > 256:
        raise ValueError("CONTROL_STORE_OUTBOX_INVALID")
    accepted_outbox: dict[str, Any] = {}
    for key, row in outbox.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key):
            raise ValueError("CONTROL_STORE_OUTBOX_KEY_INVALID")
        if not isinstance(row, Mapping) or set(row) != {"method", "request_sha256", "state"}:
            raise ValueError("CONTROL_STORE_OUTBOX_ROW_INVALID")
        if row["state"] not in {"PENDING", "ACKNOWLEDGED", "FAILED"}:
            raise ValueError("CONTROL_STORE_OUTBOX_STATE_INVALID")
        if not isinstance(row["method"], str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", row["method"]):
            raise ValueError("CONTROL_STORE_OUTBOX_METHOD_INVALID")
        if not isinstance(row["request_sha256"], str) or not re.fullmatch(r"[0-9A-F]{64}", row["request_sha256"]):
            raise ValueError("CONTROL_STORE_OUTBOX_SHA_INVALID")
        accepted_outbox[key] = dict(row)
    return {
        "route": route,
        "window_bounds": window,
        "panel_dimensions": accepted_panels,
        "filters": _validate_filters(value["filters"]),
        "selected_locator": locator,
        "event_cursor_by_job": dict(sorted(accepted_cursors.items())),
        "pending_outbox_command_metadata": dict(sorted(accepted_outbox.items())),
    }


class ControlStore:
    def __init__(self, profile_root: Path | str):
        self.profile_root = Path(profile_root)
        self.profile_root.mkdir(parents=True, exist_ok=True)
        self.path = self.profile_root / "control_store.json"
        self.lock_path = self.profile_root / ".control_store.lock"
        self._thread_lock = _thread_lock_for(self.lock_path)
        self.recovery_receipt_path = self.profile_root / "control_store_recovery.json"
        self.migration_receipt_path = self.profile_root / "control_store_migration.json"

    @contextmanager
    def _exclusive_lock(self):
        with self._thread_lock:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock_path.open("a+b") as stream:
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                    os.fsync(stream.fileno())
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    stream.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _envelope(self, state: Mapping[str, Any], revision: int) -> dict[str, Any]:
        accepted = validate_state(state)
        return {
            "schema_version": SCHEMA_VERSION,
            "revision": revision,
            "updated_at": utc_now(),
            "state": accepted,
            "state_sha256": _sha(accepted),
        }

    def _atomic_write(self, path: Path, payload: Mapping[str, Any]) -> None:
        encoded = canonical_json_bytes(dict(payload)) + b"\n"
        temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def _validate_envelope(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "revision", "updated_at", "state", "state_sha256"}:
            raise ControlStoreCorrupt("CONTROL_STORE_ENVELOPE_INVALID")
        if value["schema_version"] != SCHEMA_VERSION:
            raise ControlStoreCorrupt("CONTROL_STORE_SCHEMA_UNSUPPORTED")
        revision = value["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ControlStoreCorrupt("CONTROL_STORE_REVISION_INVALID")
        state = validate_state(value["state"])
        if value["state_sha256"] != _sha(state):
            raise ControlStoreCorrupt("CONTROL_STORE_CHECKSUM_MISMATCH")
        return {**dict(value), "state": state}

    def _migrate_v1(self, value: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"schema_version", "revision", "current_route", "window", "panels", "filters", "selected_locator"}
        if set(value) - allowed:
            raise ControlStoreCorrupt("CONTROL_STORE_V1_UNKNOWN_FIELD")
        state = default_state()
        state.update(
            {
                "route": value.get("current_route", state["route"]),
                "window_bounds": value.get("window", state["window_bounds"]),
                "panel_dimensions": value.get("panels", state["panel_dimensions"]),
                "filters": value.get("filters", {}),
                "selected_locator": value.get("selected_locator"),
            }
        )
        migrated = self._envelope(state, int(value.get("revision", 0)) + 1)
        self._atomic_write(self.path, migrated)
        self._atomic_write(
            self.migration_receipt_path,
            {
                "schema_version": "ControlStoreMigrationReceipt-v1",
                "from_schema": "DesktopControlStore-v1",
                "to_schema": SCHEMA_VERSION,
                "result_revision": migrated["revision"],
                "status": "PASS",
            },
        )
        return migrated

    def _recover(self, raw: bytes, reason: str) -> dict[str, Any]:
        digest = hashlib.sha256(raw).hexdigest().upper()
        quarantine = self.profile_root / f"control_store.corrupt.{digest[:16]}.json"
        if not quarantine.exists() and self.path.exists():
            os.replace(self.path, quarantine)
        recovered = self._envelope(default_state(), 0)
        self._atomic_write(self.path, recovered)
        self._atomic_write(
            self.recovery_receipt_path,
            {
                "schema_version": "ControlStoreRecoveryReceipt-v1",
                "reason_code": reason,
                "corrupt_bytes": len(raw),
                "corrupt_sha256": digest,
                "quarantine_name": quarantine.name,
                "raw_content_in_receipt": False,
                "status": "RECOVERED_TO_SAFE_DEFAULT",
            },
        )
        return recovered

    def _load_unlocked(self, *, recover_corruption: bool = True) -> dict[str, Any]:
        if not self.path.exists():
            initial = self._envelope(default_state(), 0)
            self._atomic_write(self.path, initial)
            return deepcopy(initial)
        raw = self.path.read_bytes()
        try:
            value = json.loads(raw.decode("utf-8"))
            if isinstance(value, Mapping) and value.get("schema_version") == "DesktopControlStore-v1":
                return deepcopy(self._migrate_v1(value))
            return deepcopy(self._validate_envelope(value))
        except (SensitivePersistenceRejected, OSError):
            raise
        except Exception as exc:
            if not recover_corruption:
                if isinstance(exc, ControlStoreCorrupt):
                    raise
                raise ControlStoreCorrupt("CONTROL_STORE_DECODE_FAILED") from exc
            return deepcopy(self._recover(raw, getattr(exc, "code", type(exc).__name__)))

    def load(self, *, recover_corruption: bool = True) -> dict[str, Any]:
        with self._exclusive_lock():
            return self._load_unlocked(recover_corruption=recover_corruption)

    def save(self, state: Mapping[str, Any], *, expected_revision: int) -> dict[str, Any]:
        with self._exclusive_lock():
            current = self._load_unlocked(recover_corruption=False)
            if current["revision"] != expected_revision:
                raise ValueError(f"CONTROL_STORE_REVISION_CONFLICT:{expected_revision}:{current['revision']}")
            accepted = self._envelope(state, expected_revision + 1)
            self._atomic_write(self.path, accepted)
            return deepcopy(accepted)

    def update(self, mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self._exclusive_lock():
            current = self._load_unlocked(recover_corruption=False)
            state = deepcopy(current["state"])
            mutator(state)
            accepted = self._envelope(state, current["revision"] + 1)
            self._atomic_write(self.path, accepted)
            return deepcopy(accepted)


__all__ = ["ALLOWED_ROUTES", "ControlStore", "SCHEMA_VERSION", "default_state", "validate_state"]
