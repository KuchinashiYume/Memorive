"""PR-OS P01/T03/M01 · M1 流水线数据结构。归属标记(data_ownership)自 M1a 起随对象一路下传,
是 M1e 云/本地分流的唯一依据(承第 0 步硬约束①)。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class IngestMeta:
    """M1a 导入元数据。"""
    source: str            # 来源路径 / 收件箱位置
    imported_at: str       # ISO 8601 带时区
    data_ownership: str    # "self" 自有 | "entrusted" 他人托付 —— 红线, 分流唯一依据
    doc_type: str          # "literature" 正式文献 | "note" 笔记 | ...(供第 4 步笔记核对)
    original_format: str   # "pdf" | "image" | "txt" | "md"


@dataclass(frozen=True)
class RawObject:
    """M1a 输出:原始文件 + 元数据(归属标记随它走)。"""
    path: Path
    meta: IngestMeta


@dataclass(frozen=True)
class ConvertReport:
    """M1b 转换报告。"""
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
    """M1b 输出。meta 继续往 M1c/M1d/M1e 传;rawmd_path 交 M1c。"""
    paper_id: str
    title: str
    folder: Path
    rawmd_path: Path
    pdf_path: Path | None      # 非 PDF 原件时为 None(第 1 步沙盒为 PDF)
    report: ConvertReport
    meta: IngestMeta
    # P06/T05 successor projection. For PDF this equals pdf_path; for every
    # admitted non-PDF format it points at the byte-identical [Original] copy.
    original_path: Path | None = None
