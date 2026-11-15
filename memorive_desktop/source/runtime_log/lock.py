"""One lock per log file, held by every process that appends to the log and by the retention pass.

The lock is the first byte of ``<log>.lock`` next to the log. Byte-range locks on Windows and ``flock`` elsewhere
hold across processes, so an append can never fall between the retention pass's last read and its replacement
of the file.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

LOCK_SUFFIX = ".lock"


@contextmanager
def log_lock(log_path: Path | str) -> Iterator[None]:
    """Hold the lock of ``log_path`` for the duration of the block.

    Waiting is bounded: on Windows the lock is tried for about ten seconds, then OSError is raised and the
    caller fails visibly instead of writing unprotected.
    """
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path.with_name(path.name + LOCK_SUFFIX), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


__all__ = ["LOCK_SUFFIX", "log_lock"]
