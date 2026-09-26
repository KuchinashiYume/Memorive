from __future__ import annotations

from pathlib import Path
import re
import struct
import zipfile

from pr_os_folder_management.policy import windows_io_path


def _zip_count(path: Path, pattern: re.Pattern[str]) -> int | None:
    try:
        with zipfile.ZipFile(windows_io_path(path)) as archive:
            return sum(1 for name in archive.namelist() if pattern.fullmatch(name))
    except (OSError, zipfile.BadZipFile):
        return None


def workload_projection(path: Path, extension: str, size: int) -> dict[str, object]:
    value: object = "—"
    label = "属性"
    if extension == ".pdf":
        try:
            import pymupdf

            with pymupdf.open(windows_io_path(path)) as document:
                value = len(document) or "—"
        except ImportError:
            try:
                value = len(re.findall(rb"/Type\s*/Page\b", windows_io_path(path).read_bytes())) or "—"
            except OSError:
                value = "—"
        except (OSError, RuntimeError, ValueError):
            value = "—"
        label = "页数"
    elif extension == ".docx":
        label = "约页数"
        value = "—"
    elif extension == ".pptx":
        label = "幻灯片"
        value = _zip_count(path, re.compile(r"ppt/slides/slide\d+\.xml")) or "—"
    elif extension == ".xlsx":
        label = "工作表"
        value = _zip_count(path, re.compile(r"xl/worksheets/sheet\d+\.xml")) or "—"
    elif extension == ".png":
        label = "像素"
        try:
            header = windows_io_path(path).read_bytes()[:24]
            value = f"{struct.unpack('>I', header[16:20])[0]}×{struct.unpack('>I', header[20:24])[0]}" if header[:8] == b"\x89PNG\r\n\x1a\n" else "—"
        except (OSError, struct.error):
            value = "—"
    elif extension in {".txt", ".md", ".json"}:
        label = "行数"
        try:
            value = windows_io_path(path).read_text(encoding="utf-8").count("\n") + 1
        except (OSError, UnicodeError):
            value = "—"
    return {
        "schema_version": "InboxWorkloadProjection-v1",
        "label": label,
        "value": value,
        "bytes": size,
        "non_blocking_unknown": value == "—",
    }


__all__ = ["workload_projection"]
