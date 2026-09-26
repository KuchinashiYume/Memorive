"""OS kernel lock plus persistent LockLease evidence for ACCOUNTING-RECOVERY."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import socket
import sys
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .errors import ConcurrentWriterError


LOCK_FILENAME = ".writer.lock"
LEASE_FILENAME = ".writer.lease.json"
LOCK_SENTINEL = b"\x00"
LEASE_SCHEMA_REVISION = "LockLease-v1"
WRITER_PROTOCOL_REVISION = "Capabilities_KERNEL_LOCK_V1"
RECOVERY_POLICY_REVISION = "ACCOUNTING_RECOVERY_RECOVERY_V1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def host_id_hash() -> str:
    return sha256_bytes(socket.gethostname().encode("utf-8"))


def process_start_marker(pid: int) -> str | None:
    """Return a boot-relative process creation marker without reading payloads."""
    if pid <= 0:
        return None
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

        class FILETIME(ctypes.Structure):
            _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        ]
        kernel32.GetProcessTimes.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if not handle:
            return None
        try:
            created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            ticks = (created.high << 32) | created.low
            return f"win-filetime:{ticks}"
        finally:
            kernel32.CloseHandle(handle)
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        fields = stat_path.read_text(encoding="ascii").split()
        return f"proc-start-ticks:{fields[21]}"
    except (OSError, IndexError, UnicodeError):
        return None


def process_identity_status(pid: int, expected_marker: str | None) -> str:
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if not handle:
            error = ctypes.get_last_error()
            return "UNKNOWN" if error == 5 else "DEAD"
        try:
            exit_code = ctypes.c_uint32()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return "UNKNOWN"
            if exit_code.value != STILL_ACTIVE:
                return "DEAD"
        finally:
            kernel32.CloseHandle(handle)
        current = process_start_marker(pid)
        if current is None or expected_marker is None:
            return "UNKNOWN"
        return "SAME_PROCESS_ALIVE" if current == expected_marker else "PID_REUSED"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "DEAD"
    except PermissionError:
        return "UNKNOWN"
    except OSError:
        return "DEAD"
    current = process_start_marker(pid)
    if current is None or expected_marker is None:
        return "UNKNOWN"
    return "SAME_PROCESS_ALIVE" if current == expected_marker else "PID_REUSED"


def ledger_prefix_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.glob("*.jsonl"), key=lambda item: item.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


@dataclass(frozen=True)
class LockLease:
    owner_id: str
    pid: int
    process_start_marker: str | None
    host_id_hash: str
    acquired_at: str
    heartbeat_at: str
    lease_seconds: int
    ledger_prefix_sha256: str
    state: str
    schema_revision: str = LEASE_SCHEMA_REVISION
    recovery_policy_revision: str = RECOVERY_POLICY_REVISION
    writer_protocol_revision: str = WRITER_PROTOCOL_REVISION

    def __post_init__(self) -> None:
        if not self.owner_id or self.pid <= 0 or self.lease_seconds <= 0:
            raise ValueError("LockLease owner, pid and positive lease are required")
        if self.state not in {"ACTIVE", "RELEASED", "RECOVERY_ACTIVE", "RECOVERED"}:
            raise ValueError("invalid LockLease state")
        for stamp in (self.acquired_at, self.heartbeat_at):
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("LockLease timestamps require an offset")

    @classmethod
    def from_dict(cls, value: dict) -> "LockLease":
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise ValueError(f"LockLease fields mismatch: {sorted(set(value) ^ expected)}")
        return cls(**value)

    def as_dict(self) -> dict:
        return asdict(self)


def read_lease_bytes(root: Path) -> bytes | None:
    path = root / LEASE_FILENAME
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def parse_lease(raw: bytes) -> LockLease:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("LockLease is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("LockLease must be an object")
    return LockLease.from_dict(value)


def atomic_write_lease(root: Path, lease: LockLease) -> bytes:
    root.mkdir(parents=True, exist_ok=True)
    path = root / LEASE_FILENAME
    encoded = (canonical_json(lease.as_dict()) + "\n").encode("utf-8")
    temporary = root / f".{LEASE_FILENAME}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with temporary.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return encoded


def archive_lease(root: Path, raw: bytes, *, evidence_dir_name: str = "_lock_evidence") -> Path:
    evidence_root = root / evidence_dir_name
    evidence_root.mkdir(parents=True, exist_ok=True)
    digest = sha256_bytes(raw)
    target = evidence_root / f"lease-{digest}.json"
    if not target.exists():
        with target.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    elif target.read_bytes() != raw:
        raise RuntimeError("lease evidence hash collision")
    return target


class KernelFileLock:
    """Non-blocking one-byte OS lock; the handle is the live authority."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.descriptor: int | None = None
        self.preexisting_bytes: bytes = b""

    def acquire(self) -> "KernelFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR)
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, LOCK_SENTINEL)
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            if sys.platform == "win32":
                import msvcrt

                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise ConcurrentWriterError(f"ACTIVE_KERNEL_HELD: {self.path}") from exc
            else:
                import fcntl

                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise ConcurrentWriterError(f"ACTIVE_KERNEL_HELD: {self.path}") from exc
            os.lseek(descriptor, 0, os.SEEK_SET)
            self.preexisting_bytes = os.read(descriptor, 4096)
            self.descriptor = descriptor
            return self
        except Exception:
            os.close(descriptor)
            raise

    def release(self) -> None:
        descriptor, self.descriptor = self.descriptor, None
        if descriptor is None:
            return
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> "KernelFileLock":
        return self.acquire()

    def __exit__(self, _type, _value, _traceback) -> None:
        self.release()


class KernelWriterLock:
    """Normal writer path; stale ACTIVE metadata requires the recovery tool."""

    def __init__(self, root: Path | str, *, owner_id: str, lease_seconds: int = 60):
        self.root = Path(root)
        self.owner_id = owner_id
        self.lease_seconds = lease_seconds
        self.kernel = KernelFileLock(self.root / LOCK_FILENAME)
        self.lease: LockLease | None = None

    def acquire(self) -> "KernelWriterLock":
        self.kernel.acquire()
        try:
            if self.kernel.preexisting_bytes not in {b"", LOCK_SENTINEL}:
                raise ConcurrentWriterError("BLOCKED_UNKNOWN_LEGACY: PID-only lock marker requires controlled recovery")
            previous_raw = read_lease_bytes(self.root)
            if previous_raw is not None:
                try:
                    previous = parse_lease(previous_raw)
                except ValueError as exc:
                    raise ConcurrentWriterError("RECOVERABLE_METADATA_DAMAGE: use recovery tool") from exc
                if previous.state in {"ACTIVE", "RECOVERY_ACTIVE"}:
                    raise ConcurrentWriterError("STALE_OR_LIVE_LEASE_REQUIRES_RECOVERY")
                archive_lease(self.root, previous_raw)
            marker = process_start_marker(os.getpid())
            now = utc_now()
            self.lease = LockLease(
                owner_id=self.owner_id,
                pid=os.getpid(),
                process_start_marker=marker,
                host_id_hash=host_id_hash(),
                acquired_at=now,
                heartbeat_at=now,
                lease_seconds=self.lease_seconds,
                ledger_prefix_sha256=ledger_prefix_sha256(self.root),
                state="ACTIVE",
            )
            atomic_write_lease(self.root, self.lease)
            return self
        except Exception:
            self.kernel.release()
            raise

    def heartbeat(self) -> LockLease:
        if self.kernel.descriptor is None or self.lease is None:
            raise RuntimeError("writer lock is not held")
        self.lease = replace(
            self.lease,
            heartbeat_at=utc_now(),
            ledger_prefix_sha256=ledger_prefix_sha256(self.root),
        )
        atomic_write_lease(self.root, self.lease)
        return self.lease

    def release(self) -> None:
        try:
            if self.kernel.descriptor is not None and self.lease is not None:
                self.lease = replace(
                    self.lease,
                    heartbeat_at=utc_now(),
                    ledger_prefix_sha256=ledger_prefix_sha256(self.root),
                    state="RELEASED",
                )
                atomic_write_lease(self.root, self.lease)
        finally:
            self.kernel.release()

    def __enter__(self) -> "KernelWriterLock":
        return self.acquire()

    def __exit__(self, _type, _value, _traceback) -> None:
        self.release()


@contextmanager
def writer_lock(root: Path | str, *, owner_id: str, lease_seconds: int = 60) -> Iterator[LockLease]:
    with KernelWriterLock(root, owner_id=owner_id, lease_seconds=lease_seconds) as held:
        assert held.lease is not None
        yield held.lease


__all__ = [
    "KernelFileLock",
    "KernelWriterLock",
    "LOCK_FILENAME",
    "LOCK_SENTINEL",
    "LEASE_FILENAME",
    "LockLease",
    "archive_lease",
    "atomic_write_lease",
    "host_id_hash",
    "ledger_prefix_sha256",
    "parse_lease",
    "process_identity_status",
    "process_start_marker",
    "read_lease_bytes",
    "sha256_bytes",
    "utc_now",
    "writer_lock",
]
