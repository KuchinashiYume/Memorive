from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

from memorive_folder_management.policy import windows_io_path


_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,16}$")


class FileImportBinder:
    """Copy the bytes from one already-open source handle into immutable custody."""

    def __init__(self, destination_root: Path | str):
        self.destination_root = Path(destination_root).resolve()
        windows_io_path(self.destination_root).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _identity(stat: os.stat_result) -> tuple[int, int, int, int]:
        return (int(stat.st_dev), int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns))

    def bind(self, source: Path | str) -> dict[str, object]:
        source_path = Path(source).resolve(strict=False)
        source_io = windows_io_path(source_path)
        if source_path.is_symlink() or source_io.is_symlink() or not source_io.is_file():
            raise ValueError("IMPORT_SOURCE_NOT_REGULAR_FILE")
        temporary = self.destination_root / f".import.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        temporary_io = windows_io_path(temporary)
        digest = hashlib.sha256()
        total = 0
        try:
            with source_io.open("rb") as input_stream, temporary_io.open("xb") as output_stream:
                before = os.fstat(input_stream.fileno())
                try:
                    path_before = source_io.stat(follow_symlinks=False)
                except FileNotFoundError as exc:
                    raise ValueError("IMPORT_SOURCE_PATH_CHANGED_DURING_BIND") from exc
                if not stat.S_ISREG(path_before.st_mode) or self._identity(path_before) != self._identity(before):
                    raise ValueError("IMPORT_SOURCE_PATH_CHANGED_DURING_BIND")
                for block in iter(lambda: input_stream.read(1024 * 1024), b""):
                    digest.update(block)
                    total += len(block)
                    output_stream.write(block)
                output_stream.flush()
                os.fsync(output_stream.fileno())
                after = os.fstat(input_stream.fileno())
                try:
                    path_after = source_io.stat(follow_symlinks=False)
                except FileNotFoundError as exc:
                    raise ValueError("IMPORT_SOURCE_PATH_CHANGED_DURING_BIND") from exc
            if self._identity(before) != self._identity(after) or total != before.st_size:
                raise ValueError("IMPORT_SOURCE_CHANGED_DURING_BIND")
            if not stat.S_ISREG(path_after.st_mode) or self._identity(path_after) != self._identity(after):
                raise ValueError("IMPORT_SOURCE_PATH_CHANGED_DURING_BIND")
            sha256 = digest.hexdigest().upper()
            suffix = source_path.suffix.lower() if _SAFE_SUFFIX.fullmatch(source_path.suffix) else ".bin"
            destination = (self.destination_root / f"{sha256.lower()}{suffix}").resolve(strict=False)
            if not destination.is_relative_to(self.destination_root):
                raise ValueError("IMPORT_DESTINATION_OUTSIDE_ROOT")
            destination_io = windows_io_path(destination)
            if destination_io.exists():
                if hashlib.sha256(destination_io.read_bytes()).hexdigest().upper() != sha256:
                    raise ValueError("IMPORT_BOUND_HASH_CONFLICT")
                temporary_io.unlink()
            else:
                os.replace(temporary_io, destination_io)
            return {
                "schema_version": "BoundFileImportReceipt-v1",
                "source_name": source_path.name,
                "source_identity": {
                    "device": int(before.st_dev),
                    "inode": int(before.st_ino),
                    "size": int(before.st_size),
                    "mtime_ns": int(before.st_mtime_ns),
                },
                "bound_path": str(destination),
                "sha256": sha256,
                "bytes": total,
                "source_handle_stable_during_copy": True,
            }
        except Exception:
            if temporary_io.exists():
                temporary_io.unlink()
            raise


__all__ = ["FileImportBinder"]
