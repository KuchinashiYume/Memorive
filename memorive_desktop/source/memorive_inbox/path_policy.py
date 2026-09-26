from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence

from memorive_folder_management.policy import windows_io_path

from .contracts import IMPORTABLE_EXTENSIONS, InboxError


_DEVICE_PREFIX = re.compile(r"^(?:\\\\[.?]\\|//[.?]/|\\\\\?\\GLOBALROOT)", re.IGNORECASE)
_RESERVED_STEMS = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))})


def _is_reparse(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


class PathSecurityPolicy:
    def __init__(
        self,
        source_roots: Sequence[Path | str],
        destination_root: Path | str,
        *,
        maximum_bytes: int = 64 * 1024 * 1024,
        minimum_free_bytes: int = 1024 * 1024,
        probe_overrides: Mapping[str, Mapping[str, Any]] | None = None,
    ):
        self.source_roots = tuple(Path(root).resolve(strict=False) for root in source_roots)
        if not self.source_roots:
            raise InboxError("INBOX_SOURCE_ALLOWLIST_EMPTY")
        self.destination_root = Path(destination_root).resolve(strict=False)
        self.maximum_bytes = int(maximum_bytes)
        self.minimum_free_bytes = int(minimum_free_bytes)
        self.probe_overrides = {
            str(Path(path).resolve(strict=False)): dict(values)
            for path, values in (probe_overrides or {}).items()
        }

    def _inside_source_root(self, target: Path) -> bool:
        return any(target.is_relative_to(root) for root in self.source_roots)

    def _root_for(self, target: Path) -> Path:
        matches = [root for root in self.source_roots if target.is_relative_to(root)]
        if not matches:
            raise InboxError("IMPORT_SOURCE_OUTSIDE_ALLOWLIST")
        return max(matches, key=lambda root: len(root.parts))

    @staticmethod
    def _reject_raw_path(raw: str) -> None:
        if "\x00" in raw or _DEVICE_PREFIX.match(raw):
            raise InboxError("IMPORT_DEVICE_PATH_REJECTED")
        normalized = raw.replace("\\", "/")
        if any(component == ".." for component in normalized.split("/")):
            raise InboxError("IMPORT_PATH_TRAVERSAL_REJECTED")

    def inspect(self, source: Path | str) -> dict[str, Any]:
        raw = os.fspath(source)
        self._reject_raw_path(raw)
        unresolved = Path(raw)
        unresolved_io = windows_io_path(unresolved)
        resolved = unresolved.resolve(strict=False)
        resolved_io = windows_io_path(resolved)
        if not self._inside_source_root(resolved):
            raise InboxError("IMPORT_SOURCE_OUTSIDE_ALLOWLIST")
        root = self._root_for(resolved)
        override = self.probe_overrides.get(str(resolved), {})
        if override.get("device"):
            raise InboxError("IMPORT_DEVICE_PATH_REJECTED")
        if override.get("reparse") or unresolved.is_symlink() or unresolved_io.is_symlink():
            raise InboxError("IMPORT_REPARSE_OR_SYMLINK_REJECTED")
        cursor = root
        for part in resolved.relative_to(root).parts:
            cursor = cursor / part
            cursor_io = windows_io_path(cursor)
            if cursor_io.exists() and (cursor_io.is_symlink() or _is_reparse(cursor_io)):
                raise InboxError("IMPORT_REPARSE_OR_SYMLINK_REJECTED")
        if not resolved_io.exists():
            raise InboxError("IMPORT_SOURCE_NOT_FOUND", retryable=True)
        if not resolved_io.is_file():
            raise InboxError("IMPORT_SOURCE_NOT_REGULAR_FILE")
        if resolved.stem.upper() in _RESERVED_STEMS:
            raise InboxError("IMPORT_RESERVED_NAME_REJECTED")
        if override.get("locked"):
            raise InboxError("IMPORT_SOURCE_LOCKED", retryable=True)
        suffix = resolved.suffix.lower()
        if suffix not in IMPORTABLE_EXTENSIONS:
            raise InboxError("IMPORT_FORMAT_UNSUPPORTED")
        source_stat = resolved_io.stat()
        size = source_stat.st_size
        if size == 0:
            raise InboxError("IMPORT_EMPTY_FILE")
        if size > self.maximum_bytes or override.get("oversize"):
            raise InboxError("IMPORT_FILE_OVERSIZE")
        free_bytes = int(override.get("free_bytes", self.minimum_free_bytes * 4 + size))
        if free_bytes < self.minimum_free_bytes + size:
            raise InboxError("IMPORT_DISK_SPACE_INSUFFICIENT", retryable=True)
        return {
            "schema_version": "InboxPathPreflightReceipt-v1",
            "source_name": resolved.name,
            "source_path": str(resolved),
            "extension": suffix,
            "bytes": size,
            "mtime_ns": source_stat.st_mtime_ns,
            "source_root": str(root),
            "inside_allowlist": True,
            "regular_file": True,
            "symlink_or_reparse": False,
            "free_bytes": free_bytes,
            "status": "PASS",
            "injected_changed": bool(override.get("changed")),
            "injected_copy_interrupt": bool(override.get("copy_interrupt")),
        }


__all__ = ["PathSecurityPolicy"]
