"""DOCUMENT_PROCESSING 流水线编排：把既有五段串起来，不改各段内部逻辑。

两个入口，工作流执行器都在用：
  - `run_document_processing(...)`  ingest→convert→clean→chunk，停在落盘的 Chunks 文件。
  - `embed_from_chunks(...)`         对已落盘的 Chunks 文件做 DocumentEmbed。

约定：
  - 重跑不覆盖：DocumentEmbed 遇到已存在的 chunk_id 时用 `VectorDuplicateError` 挡下，只增不改。
  - 受控的 `DocumentProcessingError` / `GatewayError` 记为 `status="failed"` 的结果返回；其他异常照常抛出。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml
from runtime_log import log, log_error
from model_gateway.errors import GatewayError

from .config import COLLECTION, chroma_dir
from .errors import DocumentProcessingError
from .document_ingest_ingest import ingest
from .document_convert_convert import convert
from .document_clean_clean import clean
from .document_chunk_chunk import chunk
from .document_embed_embed import embed

_MODULE = "DOCUMENT_PROCESSING"   # 编排层 RUNTIME_LOG 分区键(各段用 DocumentIngest…DocumentEmbed)


@dataclass
class PipelineResult:
    """流水线终态：completed / note_review_pending / partial_recovery_pending / failed。"""
    status: str
    paper_id: str | None = None
    doc_type: str | None = None
    target: str = "sandbox"
    rawmd_path: str | None = None
    cleanmd_path: str | None = None
    chunks_path: str | None = None
    embed_summary: dict | None = None
    stopped_at: str | None = None
    resume_hint: str | None = None
    error_type: str | None = None
    error_message: str | None = None


# ── 编排层小工具(不改 DocumentIngest–DocumentEmbed;开 collection 复用公共 config,与 DocumentEmbed 同一路径源)──
def _open_collection(target: str):
    import chromadb
    cdir = chroma_dir(target)
    cdir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(cdir)).get_or_create_collection(COLLECTION[target])


def _read_frontmatter(rawmd_path) -> dict:
    """读并**受控校验** RawMD frontmatter(resume/re_embed 共享;不依赖内存对象)。
    不存在 / 非法 / 关键字段缺失或非 str → 抛受控 `DocumentProcessingError`。★杜绝 paper_id=None 走到
    `col.delete(where={"paper_id": None})`(Chroma 里 None 可能匹配全部 → 删光整库)。"""
    p = Path(rawmd_path)
    if not p.exists():
        log_error(_MODULE, "RawMD 不存在", context={
            "step": "读 RawMD frontmatter", "error": f"RawMD 不存在: {p}", "source": str(p)})
        raise DocumentProcessingError(f"RawMD 不存在: {p}")
    parts = p.read_text(encoding="utf-8").split("---", 2)
    if len(parts) < 3:
        log_error(_MODULE, "RawMD frontmatter 非法", context={
            "step": "读 RawMD frontmatter", "error": "未以 --- frontmatter --- 开头(split 后不足 3 段)",
            "source": str(p)})
        raise DocumentProcessingError(f"RawMD frontmatter 非法(需 --- … --- 开头): {p}")
    fm = yaml.safe_load(parts[1])
    if not isinstance(fm, dict):
        log_error(_MODULE, "RawMD frontmatter 非 dict", context={
            "step": "读 RawMD frontmatter", "error": f"frontmatter 解析非 dict:{type(fm).__name__}",
            "source": str(p)})
        raise DocumentProcessingError(f"RawMD frontmatter 解析异常(非 dict): {p}")
    for k in ("paper_id", "title", "data_ownership", "doc_type"):
        v = fm.get(k)
        if not (isinstance(v, str) and v.strip()):
            log_error(_MODULE, f"RawMD frontmatter 缺 {k}", context={
                "step": "读 RawMD frontmatter", "error": f"字段 {k} 缺失/非字符串:{v!r}", "source": str(p)})
            raise DocumentProcessingError(f"RawMD frontmatter 字段 {k} 缺失/非法({v!r}): {p}")
    convert_report = fm.get("convert_report")
    if isinstance(convert_report, dict) and convert_report.get("complete") is False:
        failed_pages = convert_report.get("pages_failed") or []
        message = (
            f"RawMD 的 OCR 页面救援尚未完整，pages_failed={failed_pages}；"
            "禁止进入 DocumentClean/DocumentChunk/DocumentEmbed，请先重试或人工修复并复核。"
        )
        log_error(_MODULE, "RawMD OCR 页面救援未完整", context={
            "step": "读 RawMD frontmatter/OCR 完整性",
            "error": message,
            "source": str(p),
        })
        raise DocumentProcessingError(message)
    return fm


def _failed(stage, paper_id, doc_type, target, exc, **paths) -> PipelineResult:
    """受控错误 → 统一 failed 结果 + RUNTIME_LOG pipeline_failed(该段自身已写 detail;此处记编排级归位)。"""
    msg = str(exc)
    log_error(_MODULE, f"pipeline_failed@{stage}", context={
        "step": f"pipeline/{stage}", "error": f"{type(exc).__name__}: {exc}",
        "paper_id": str(paper_id), "stopped_at": stage})
    return PipelineResult(status="failed", paper_id=paper_id, doc_type=doc_type, target=target,
                          stopped_at=stage, error_type=type(exc).__name__, error_message=msg, **paths)


def _run_document_tail(rawmd_path, target, paper_id, doc_type) -> PipelineResult:
    """Run the adopted DocumentClean/DocumentChunk seam without crossing into vector embedding.

    Desktop exposes document processing and embedding as two independently
    configurable product nodes.  This adapter only rearranges the existing
    Core stages; ``clean`` and ``chunk`` remain the authoritative
    implementations and no alternate parser/chunker is introduced.
    """
    cleanp = chunksp = None
    stage = "M1c"
    try:
        cleanp = clean(rawmd_path, target=target)
        stage = "DocumentChunk"
        chunksp = chunk(cleanp, target=target)
    except (DocumentProcessingError, GatewayError) as exc:
        return _failed(
            stage,
            paper_id,
            doc_type,
            target,
            exc,
            rawmd_path=str(rawmd_path),
            cleanmd_path=str(cleanp) if cleanp else None,
            chunks_path=str(chunksp) if chunksp else None,
        )
    log(_MODULE, "document_processing_finished", data={
        "paper_id": paper_id,
        "target": target,
        "cleanmd_path": str(cleanp),
        "chunks_path": str(chunksp),
        "next_stage": "M1e",
    })
    return PipelineResult(
        status="completed",
        paper_id=paper_id,
        doc_type=doc_type,
        target=target,
        rawmd_path=str(rawmd_path),
        cleanmd_path=str(cleanp),
        chunks_path=str(chunksp),
        stopped_at="after_document_chunk",
        resume_hint="Call embed_from_chunks(chunks_path, target=...) to run adopted Core DocumentEmbed.",
    )


def run_document_processing(source, *, data_ownership: str, doc_type: str = "literature",
                            paper_id: str, title: str, target: str = "sandbox",
                            skip_note_review: bool = False,
                            ocr_pages: list[int] | None = None) -> PipelineResult:
    """Run adopted Core DocumentIngest–DocumentChunk and stop at the durable Chunks artifact."""
    log(_MODULE, "document_processing_start", data={
        "paper_id": paper_id,
        "doc_type": doc_type,
        "target": target,
        "source": str(source),
        "skip_note_review": skip_note_review,
        "ocr_pages": sorted(ocr_pages or []),
    })
    try:
        raw = ingest(source, data_ownership=data_ownership, doc_type=doc_type)
        conv = convert(
            raw,
            paper_id=paper_id,
            title=title,
            target=target,
            ocr_pages=ocr_pages,
        )
    except (DocumentProcessingError, GatewayError) as exc:
        return _failed("DocumentIngest/DocumentConvert", paper_id, doc_type, target, exc)
    rawmd = str(conv.rawmd_path)
    if not conv.report.complete:
        failed_pages = conv.report.pages_failed
        hint = (
            f"OCR 页面救援未完整，pages_failed={failed_pages}。"
            "已保留 RawMD 和完成页；请在隔离环境重试失败页或人工修复复核，"
            "不得直接进入 DocumentClean/DocumentChunk。"
        )
        log(_MODULE, "partial_recovery_pending", data={
            "paper_id": paper_id,
            "rawmd_path": rawmd,
            "pages_failed": failed_pages,
            "stopped_at": "after_document_convert",
        })
        return PipelineResult(
            status="partial_recovery_pending",
            paper_id=paper_id,
            doc_type=doc_type,
            target=target,
            rawmd_path=rawmd,
            stopped_at="after_document_convert",
            resume_hint=hint,
        )
    if doc_type == "note" and not skip_note_review:
        hint = f"人核 RawMD 转录/格式后，再执行文档清洗与切块: r'{rawmd}'"
        log(_MODULE, "note_review_pending", data={
            "paper_id": paper_id,
            "rawmd_path": rawmd,
            "stopped_at": "after_document_convert",
        })
        return PipelineResult(
            status="note_review_pending",
            paper_id=paper_id,
            doc_type=doc_type,
            target=target,
            rawmd_path=rawmd,
            stopped_at="after_document_convert",
            resume_hint=hint,
        )
    if doc_type == "note" and skip_note_review:
        log(_MODULE, "note_review_skipped", data={
            "paper_id": paper_id,
            "rawmd_path": rawmd,
            "note": "doc_type=note but skip_note_review=True",
        })
    return _run_document_tail(rawmd, target, paper_id, doc_type)


def embed_from_chunks(chunks_path, *, target: str = "sandbox") -> PipelineResult:
    """Run the adopted Core DocumentEmbed against an already durable Chunks file."""
    path = Path(chunks_path)
    paper_id = None
    doc_type = None
    try:
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not records:
            raise DocumentProcessingError(f"Chunks 为空: {path}")
        paper_id = records[0].get("paper_id")
        doc_type = records[0].get("doc_type")
        summary = embed(path, target=target)
    except (DocumentProcessingError, GatewayError) as exc:
        return _failed(
            "M1e",
            paper_id,
            doc_type,
            target,
            exc,
            chunks_path=str(path),
        )
    log(_MODULE, "embedding_finished", data={
        "paper_id": paper_id,
        "target": target,
        "backend": summary.get("backend"),
        "count": summary.get("count"),
        "run_id": summary.get("embedding_run_id"),
    })
    return PipelineResult(
        status="completed",
        paper_id=paper_id,
        doc_type=doc_type,
        target=target,
        chunks_path=str(path),
        embed_summary=summary,
    )
