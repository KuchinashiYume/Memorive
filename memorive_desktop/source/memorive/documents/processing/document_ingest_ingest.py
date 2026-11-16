"""Ingest, the first step of the pipeline.

Takes a source file (PDF, picture, note, Markdown), records its source, the time, its data
ownership, its document type and format, and returns a RawObject with the metadata. A file without
a declared data ownership is refused; the ownership travels with the RawObject through conversion,
cleaning, chunking and embedding. Ingest only records; it converts nothing.
"""
from __future__ import annotations

from pathlib import Path

from memorive.platform.logs import log, log_error

from memorive.documents.processing.config import now_iso
from memorive.documents.processing.errors import DocumentProcessingError
from memorive.documents.processing.types import IngestMeta, RawObject

_FORMAT_BY_EXTENSION = {
    "pdf": "pdf",
    "md": "md",
    "markdown": "md",
    "txt": "txt",
    "csv": "csv",
    "html": "html",
    "htm": "html",
    "docx": "docx",
    "pptx": "pptx",
    "xlsx": "xlsx",
    "doc": "doc",
    "ppt": "ppt",
    "xls": "xls",
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "png": "png",
    "tif": "tiff",
    "tiff": "tiff",
}


def _infer_format(p: Path) -> str:
    ext = p.suffix.lower().lstrip(".")
    try:
        return _FORMAT_BY_EXTENSION[ext]
    except KeyError as exc:
        raise DocumentProcessingError(
            f"不支持的文档格式: {p.suffix or '<none>'};"
            "只允许 DOCUMENT-FORMATS 冻结的 14 种格式"
        ) from exc


def ingest(source_path, *, data_ownership: str, doc_type: str = "literature") -> RawObject:
    """Ingest one source file. data_ownership is required ('self' or 'entrusted')."""
    p = Path(source_path)

    # The source file must exist; otherwise log it at detail level and raise.
    if not p.exists() or not p.is_file():
        log_error("M1a", "源文件不存在", context={
            "step": "ingest", "error": f"源文件不存在或非文件: {p}", "source": str(p)})
        raise DocumentProcessingError(f"源文件不存在或非文件: {p}")

    # A missing or invalid data ownership is refused.
    if data_ownership not in ("self", "entrusted"):
        log_error("M1a", "数据归属未声明", context={
            "step": "ingest", "error": f"data_ownership 非法: {data_ownership!r}",
            "source": str(p)})
        raise DocumentProcessingError("data_ownership 必填,只能是 'self'(自有)或 'entrusted'(他人托付)")

    fmt = _infer_format(p)
    meta = IngestMeta(source=str(p), imported_at=now_iso(),
                      data_ownership=data_ownership, doc_type=doc_type, original_format=fmt)
    log("M1a", "导入完成", data={
        "source": meta.source, "data_ownership": meta.data_ownership,
        "doc_type": meta.doc_type, "original_format": meta.original_format})
    return RawObject(path=p, meta=meta)
