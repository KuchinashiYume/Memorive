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


def _zip_format(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return None
    if "word/document.xml" in names:
        return "docx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return None


def _ole_format(data: bytes) -> str | None:
    if not data.startswith(_OLE_SIGNATURE):
        return None
    if "WordDocument".encode("utf-16le") in data:
        return "doc"
    if "PowerPoint Document".encode("utf-16le") in data:
        return "ppt"
    if "Workbook".encode("utf-16le") in data or "Book".encode("utf-16le") in data:
        return "xls"
    return None


def _binary_signature_format(path: Path, data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    if data.startswith(b"PK\x03\x04"):
        return _zip_format(path)
    if data.startswith(_OLE_SIGNATURE):
        return _ole_format(data)
    return None


def _text_valid(data: bytes, declared: str) -> tuple[bool, tuple[str, ...]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False, ("UTF8_DECODE_ERROR",)
    if "\x00" in text:
        return False, ("NUL_BYTE_IN_TEXT",)
    controls = sum(ord(char) < 32 and char not in "\t\r\n" for char in text)
    if controls:
        return False, (f"CONTROL_CHAR_COUNT:{controls}",)
    if declared == "html" and "<html" not in text.lower() and "<!doctype html" not in text.lower():
        return False, ("HTML_ROOT_MISSING",)
    return True, ()


def _container_valid(path: Path, declared: str, data: bytes) -> tuple[bool, tuple[str, ...]]:
    if declared == "pdf":
        return (b"%%EOF" in data[-2048:], ("PDF_EOF_MISSING",) if b"%%EOF" not in data[-2048:] else ())
    if declared in {"docx", "pptx", "xlsx"}:
        detected = _zip_format(path)
        return (detected == declared, ("OOXML_REQUIRED_PART_MISSING",) if detected is None else ())
    if declared in {"doc", "ppt", "xls"}:
        detected = _ole_format(data)
        return (detected == declared, ("OLE_STREAM_IDENTITY_MISSING",) if detected is None else ())
    if declared == "png":
        valid = len(data) >= 33 and data.startswith(b"\x89PNG\r\n\x1a\n") and b"IEND" in data[-32:]
        return valid, ("PNG_STRUCTURE_INVALID",) if not valid else ()
    if declared == "jpeg":
        valid = len(data) >= 8 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
        return valid, ("JPEG_STRUCTURE_INVALID",) if not valid else ()
    if declared == "tiff":
        valid = len(data) >= 8 and data.startswith((b"II*\x00", b"MM\x00*"))
        return valid, ("TIFF_STRUCTURE_INVALID",) if not valid else ()
    return True, ()


def probe_document(path: str | Path, *, max_source_bytes: int = 16 * 1024 * 1024) -> DocumentProbe:
    source = Path(path)
    if not source.is_file():
        raise DocumentConversionError("SOURCE_NOT_FOUND", f"source does not exist: {source}")
    extension = source.suffix.lower()
    source_size = source.stat().st_size
    source_hash = _sha256(source)
    declared = EXTENSION_TO_FORMAT.get(extension)
    if declared is None:
        return DocumentProbe(
            path=source,
            source_sha256=source_hash,
            source_size=source_size,
            declared_extension=extension,
            detected_format=None,
            family=None,
            status="unsupported",
            signature_match=False,
            error_code="UNSUPPORTED_FORMAT",
            diagnostics=("EXTENSION_NOT_ADMITTED",),
        )
    spec = CANONICAL_FORMATS[declared]
    if source_size > max_source_bytes:
        return DocumentProbe(
            path=source, source_sha256=source_hash, source_size=source_size,
            declared_extension=extension, detected_format=declared, family=spec.family,
            status="resource_limit", signature_match=False,
            error_code="RESOURCE_LIMIT_REVIEW_REQUIRED", diagnostics=(f"SOURCE_BYTES:{source_size}",),
        )
    data = source.read_bytes()
    detected = _binary_signature_format(source, data)
    text_family = declared in {"md", "txt", "csv", "html"}
    if text_family:
        if detected is not None:
            return DocumentProbe(
                path=source, source_sha256=source_hash, source_size=source_size,
                declared_extension=extension, detected_format=detected,
                family=CANONICAL_FORMATS[detected].family, status="identity_mismatch",
                signature_match=False, error_code="FORMAT_IDENTITY_MISMATCH",
                diagnostics=(f"DECLARED:{declared}", f"DETECTED:{detected}"),
            )
        valid, diagnostics = _text_valid(data, declared)
        if not valid:
            return DocumentProbe(
                path=source, source_sha256=source_hash, source_size=source_size,
                declared_extension=extension, detected_format=declared, family=spec.family,
                status="corrupt", signature_match=False, error_code="TEXT_BINARY_OR_ENCODING_INVALID",
                diagnostics=diagnostics,
            )
        detected = declared
    else:
        if detected is not None and detected != declared:
            return DocumentProbe(
                path=source, source_sha256=source_hash, source_size=source_size,
                declared_extension=extension, detected_format=detected,
                family=CANONICAL_FORMATS[detected].family, status="identity_mismatch",
                signature_match=False, error_code="FORMAT_IDENTITY_MISMATCH",
                diagnostics=(f"DECLARED:{declared}", f"DETECTED:{detected}"),
            )
        if detected is None:
            return DocumentProbe(
                path=source, source_sha256=source_hash, source_size=source_size,
                declared_extension=extension, detected_format=declared, family=spec.family,
                status="corrupt", signature_match=False, error_code="CONTAINER_CORRUPT",
                diagnostics=("SIGNATURE_OR_CONTAINER_IDENTITY_MISSING",),
            )
        valid, diagnostics = _container_valid(source, declared, data)
        if not valid:
            return DocumentProbe(
                path=source, source_sha256=source_hash, source_size=source_size,
                declared_extension=extension, detected_format=declared, family=spec.family,
                status="corrupt", signature_match=False, error_code="CONTAINER_CORRUPT",
                diagnostics=diagnostics,
            )
    return DocumentProbe(
        path=source,
        source_sha256=source_hash,
        source_size=source_size,
        declared_extension=extension,
        detected_format=detected,
        family=spec.family,
        status="match",
        signature_match=True,
    )
