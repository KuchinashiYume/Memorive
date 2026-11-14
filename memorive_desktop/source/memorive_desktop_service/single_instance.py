from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from .protocol import canonical_json_bytes


class SingleInstanceCoordinator:
    """One primary UI instance plus a durable, local activation queue."""

    _process_held: set[str] = set()

    def __init__(self, profile_root: Path | str, *, application_id: str = "memorive-desktop"):
        self.profile_root = Path(profile_root)
        self.profile_root.mkdir(parents=True, exist_ok=True)
        self.application_id = application_id
        self.lock_path = self.profile_root / "desktop.instance.lock"
        self.descriptor_path = self.profile_root / "desktop.instance.json"
        self.queue_path = self.profile_root / "desktop.activation.sqlite3"
        self._stream: Any | None = None
        self._primary = False
        self._lock_key = str(self.lock_path.resolve()).casefold()
        self._initialize_queue()

    def _initialize_queue(self) -> None:
        with closing(sqlite3.connect(self.queue_path, timeout=10.0)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS activations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    activation_id TEXT NOT NULL UNIQUE,
                    locator TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_by_pid INTEGER NOT NULL
                )
                """
            )
            connection.commit()

    @staticmethod
    def _try_lock(stream: Any) -> bool:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True
        import fcntl

        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    @staticmethod
    def _unlock(stream: Any) -> None:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def acquire_primary(self) -> bool:
        if self._stream is not None:
            return self._primary
        if self._lock_key in self._process_held:
            return False
        stream = self.lock_path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
            os.fsync(stream.fileno())
        accepted = self._try_lock(stream)
        if not accepted:
            stream.close()
            return False
        self._stream = stream
        self._primary = True
        self._process_held.add(self._lock_key)
        temporary = self.profile_root / f".{self.descriptor_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        temporary.write_bytes(
            canonical_json_bytes(
                {
                    "schema_version": "DesktopSingleInstanceDescriptor-v1",
                    "application_id": self.application_id,
                    "primary_pid": os.getpid(),
                    "secret_or_credential_present": False,
                }
            )
            + b"\n"
        )
        os.replace(temporary, self.descriptor_path)
        return True

    def release(self) -> None:
        if self._stream is None:
            return
        self._unlock(self._stream)
        self._stream.close()
        self._stream = None
        self._primary = False
        self._process_held.discard(self._lock_key)
        try:
            self.descriptor_path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> "SingleInstanceCoordinator":
        if not self.acquire_primary():
            raise RuntimeError("DESKTOP_INSTANCE_ALREADY_ACTIVE")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


__all__ = ["SingleInstanceCoordinator"]
