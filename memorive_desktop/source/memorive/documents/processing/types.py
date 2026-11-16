"""The records the document pipeline passes along. data_ownership is set at ingest and travels with
the paper; embedding alone decides from it whether the cloud or the local model is used."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class IngestMeta:
    """The metadata recorded at ingest."""
    source: str            # the source path or inbox place
    imported_at: str       # ISO 8601 with the offset
    data_ownership: str    # "self" or "entrusted"; decides cloud or local
    doc_type: str          # "literature", "note" ...
    original_format: str   # "pdf" | "image" | "txt" | "md"


@dataclass(frozen=True)
class RawObject:
    """What ingest returns: the source file and its metadata (with data_ownership)."""
    path: Path
    meta: IngestMeta


@dataclass(frozen=True)
class ConvertReport:
    """The conversion report."""
    engine: str
    pages_total: int
    pages_failed: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    complete: bool = True
    page_details: list = field(default_factory=list)
    engine_version: str | None = None
    source_sha256: str | None = None
    output_sha256: str | None = None
    config_hash: str | None = None
    format_verdict: str | None = None
    selection_status: str | None = None
    recognition_status: str | None = None
    fallback_trace: list = field(default_factory=list)
    anchor_map: list = field(default_factory=list)


@dataclass(frozen=True)
class ConvertResult:
    """What conversion returns. meta goes on to cleaning, chunking and embedding; cleaning reads rawmd_path."""
    paper_id: str
    title: str
    folder: Path
    rawmd_path: Path
    pdf_path: Path | None      # None when the source is not a PDF
    report: ConvertReport
    meta: IngestMeta
    # DOCUMENT-FORMATS successor projection. For PDF this equals pdf_path; for every
    # admitted non-PDF format it points at the byte-identical [Original] copy.
    original_path: Path | None = None
