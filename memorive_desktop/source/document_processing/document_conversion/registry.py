"""Exact 14-format registry and read-only identity probe."""
from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .models import DocumentConversionError, DocumentProbe


@dataclass(frozen=True)
class FormatSpec:
    format_key: str
    extensions: tuple[str, ...]
    family: str
    anchor_kind: str


_SPECS = (
    FormatSpec("pdf", (".pdf",), "paged_pdf", "page"),
    FormatSpec("md", (".md", ".markdown"), "local_text", "line"),
    FormatSpec("txt", (".txt",), "local_text", "line"),
    FormatSpec("csv", (".csv",), "tabular_text", "row"),
    FormatSpec("html", (".html", ".htm"), "local_markup", "block"),
    FormatSpec("docx", (".docx",), "modern_office", "block"),
    FormatSpec("pptx", (".pptx",), "modern_office", "slide"),
    FormatSpec("xlsx", (".xlsx",), "modern_office", "sheet"),
    FormatSpec("doc", (".doc",), "legacy_office", "block"),
    FormatSpec("ppt", (".ppt",), "legacy_office", "slide"),
    FormatSpec("xls", (".xls",), "legacy_office", "sheet"),
    FormatSpec("jpeg", (".jpg", ".jpeg"), "image", "frame"),
    FormatSpec("png", (".png",), "image", "frame"),
    FormatSpec("tiff", (".tif", ".tiff"), "image", "frame"),
)

CANONICAL_FORMATS: dict[str, FormatSpec] = {spec.format_key: spec for spec in _SPECS}
EXTENSION_TO_FORMAT = {extension: spec.format_key for spec in _SPECS for extension in spec.extensions}
_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()
