"""The document pipeline: the five steps in order, each unchanged.

Two entry points, both used by the workflow executor:
  - run_document_processing(...): ingest, convert, clean, chunk; stops at the written Chunks file.
  - embed_from_chunks(...): embedding of a written Chunks file.

A rerun never overwrites: embedding refuses a chunk_id already stored (VectorDuplicateError).
DocumentProcessingError and GatewayError become a result with status="failed"; other exceptions
are raised.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml
from memorive.documents.front_matter import split_front_matter
from memorive.platform.logs import log, log_error
from memorive.models.gateway.errors import GatewayError

from memorive.documents.processing.config import COLLECTION, chroma_dir
from memorive.documents.processing.errors import DocumentProcessingError
from memorive.documents.processing.document_ingest_ingest import ingest
from memorive.documents.processing.document_convert_convert import convert
from memorive.documents.processing.document_clean_clean import clean
from memorive.documents.processing.document_chunk_chunk import chunk
from memorive.documents.processing.document_embed_embed import embed
from memorive.documents.processing.errors import VectorDuplicateError
import uuid

_MODULE = "DOCUMENT_PROCESSING"   # the pipeline's log module (the steps log as DocumentIngest ... DocumentEmbed)


@dataclass
class PipelineResult:
    """How the pipeline ended: completed, note_review_pending, partial_recovery_pending or failed."""
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


# ── helpers (the collection is opened through the same config as embedding) ──
def _open_collection(target: str):
    import chromadb
    cdir = chroma_dir(target)
    cdir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(cdir)).get_or_create_collection(COLLECTION[target])


def _backup_dir(target: str) -> Path:
    """The folder for re_embed snapshots, next to the vector store ({OPS_ROOT}/chroma-backup for the
    sandbox): in the ops area, outside the library and outside git. A snapshot holds the chunk texts,
    so entrusted text stays on this computer. It moves with chroma_dir's environment variable."""
    return chroma_dir(target).parent / "chroma-backup"


def _read_frontmatter(rawmd_path) -> dict:
    """Read and check the RawMD front matter (for resume and re_embed; from the file, not from memory).
    A missing or invalid file, or a key field that is missing or not a string, raises
    DocumentProcessingError. This keeps paper_id=None away from col.delete(where={"paper_id": None}),
    which Chroma might apply to everything."""
    p = Path(rawmd_path)
    if not p.exists():
        log_error(_MODULE, "RawMD 不存在", context={
            "step": "读 RawMD frontmatter", "error": f"RawMD 不存在: {p}", "source": str(p)})
        raise DocumentProcessingError(f"RawMD 不存在: {p}")
    parts = split_front_matter(p.read_text(encoding="utf-8"))
    if parts is None:
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


def _failed(stage, paper_id, doc_type, target, exc, *, extra_hint="", **paths) -> PipelineResult:
    """A known error as a failed result, with a pipeline_failed log entry (the step logged its own details)."""
    msg = str(exc) + extra_hint
    log_error(_MODULE, f"pipeline_failed@{stage}", context={
        "step": f"pipeline/{stage}", "error": f"{type(exc).__name__}: {exc}",
        "paper_id": str(paper_id), "stopped_at": stage})
    return PipelineResult(status="failed", paper_id=paper_id, doc_type=doc_type, target=target,
                          stopped_at=stage, error_type=type(exc).__name__, error_message=msg, **paths)


def _run_tail(rawmd_path, target, paper_id, doc_type, *, finished_event="pipeline_finished") -> PipelineResult:
    """Clean, chunk and embed, tracking stopped_at; a known error gives a failed result (a duplicate
    points to the re-embed entry)."""
    cleanp = chunksp = None
    stage = "M1c"
    try:
        cleanp = clean(rawmd_path, target=target)
        stage = "DocumentChunk"
        chunksp = chunk(cleanp, target=target)
        stage = "M1e"
        summary = embed(chunksp, target=target)
    except (DocumentProcessingError, GatewayError) as exc:
        hint = ""
        if isinstance(exc, VectorDuplicateError):    # a duplicate: point to re_embed; the normal path never deletes
            hint = " 若要改后重嵌,请走 re_embed_from_rawmd(rawmd_path, target=...)(唯一允许清旧向量的入口)。"
        return _failed(stage, paper_id, doc_type, target, exc, extra_hint=hint,
                       rawmd_path=str(rawmd_path),
                       cleanmd_path=str(cleanp) if cleanp else None,
                       chunks_path=str(chunksp) if chunksp else None)
    log(_MODULE, finished_event, data={
        "paper_id": paper_id, "target": target, "backend": summary.get("backend"),
        "count": summary.get("count"), "run_id": summary.get("embedding_run_id")})
    return PipelineResult(status="completed", paper_id=paper_id, doc_type=doc_type, target=target,
                          rawmd_path=str(rawmd_path), cleanmd_path=str(cleanp), chunks_path=str(chunksp),
                          embed_summary=summary)


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


def run_pipeline(source, *, data_ownership: str, doc_type: str = "literature",
                 paper_id: str, title: str, target: str = "sandbox",
                 skip_note_review: bool = False,
                 ocr_pages: list[int] | None = None) -> PipelineResult:
    """All five steps. A note (doc_type=note) pauses after conversion for a person to check, unless skip_note_review."""
    log(_MODULE, "pipeline_start", data={
        "paper_id": paper_id, "doc_type": doc_type, "target": target,
        "source": str(source), "skip_note_review": skip_note_review,
        "ocr_pages": sorted(ocr_pages or [])})
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
            "不得直接进入 DocumentClean/DocumentChunk/DocumentEmbed。"
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

    if doc_type == "note" and not skip_note_review:                 # a note: pause (a normal return)
        hint = f"人核 RawMD 转录/格式后,调 resume_after_note_review(r'{rawmd}', target='{target}')"
        log(_MODULE, "note_review_pending", data={
            "paper_id": paper_id, "rawmd_path": rawmd, "stopped_at": "after_document_convert"})
        return PipelineResult(status="note_review_pending", paper_id=paper_id, doc_type=doc_type,
                              target=target, rawmd_path=rawmd, stopped_at="after_document_convert", resume_hint=hint)
    if doc_type == "note" and skip_note_review:                     # skipping is recorded too
        log(_MODULE, "note_review_skipped", data={
            "paper_id": paper_id, "rawmd_path": rawmd,
            "note": "doc_type=note 但闸门被显式跳过(skip_note_review=True)"})

    return _run_tail(rawmd, target, paper_id, doc_type)


def resume_after_note_review(rawmd_path, *, target: str = "sandbox") -> PipelineResult:
    """After a person checked the RawMD: clean, chunk, embed. The metadata is read from the front matter."""
    try:
        fm = _read_frontmatter(rawmd_path)
    except DocumentProcessingError as exc:
        return _failed("读 RawMD frontmatter", None, None, target, exc, rawmd_path=str(rawmd_path))
    paper_id, doc_type = fm["paper_id"], fm["doc_type"]
    log(_MODULE, "note_review_confirmed", data={"paper_id": paper_id, "rawmd_path": str(rawmd_path)})
    log(_MODULE, "pipeline_resumed", data={"paper_id": paper_id, "target": target, "stage": "M1c"})
    return _run_tail(rawmd_path, target, paper_id, doc_type)


def re_embed_from_rawmd(rawmd_path, *, target: str = "sandbox") -> PipelineResult:
    """Re-embed one paper, the only way vectors are deleted: write a snapshot, delete, embed again; if
    the embedding fails or the delete leaves vectors behind, the snapshot restores the state before.
    The collection is opened only after paper_id has been checked (_read_frontmatter), so paper_id=None
    never reaches col.delete or get. Embedding assumes one writer: do not re-embed or run the pipeline
    for the same paper_id at the same time; several processes would need a lock here."""
    try:
        fm = _read_frontmatter(rawmd_path)
    except DocumentProcessingError as exc:
        return _failed("读 RawMD frontmatter", None, None, target, exc, rawmd_path=str(rawmd_path))
    paper_id, doc_type = fm["paper_id"], fm["doc_type"]

    # 1) Clean and chunk first: if that fails, it fails before the old vectors are touched.
    stage = "DocumentClean(重嵌前置)"
    try:
        cleanp = clean(rawmd_path, target=target)
        stage = "DocumentChunk(重嵌前置)"
        chunksp = chunk(cleanp, target=target)
    except (DocumentProcessingError, GatewayError) as exc:
        return _failed(stage, paper_id, doc_type, target, exc, rawmd_path=str(rawmd_path))

    col = _open_collection(target)
    run_id = uuid.uuid4().hex

    # 2) Take the snapshot of the old vectors (embeddings, metadatas, documents).
    snap = col.get(where={"paper_id": paper_id}, include=["embeddings", "metadatas", "documents"])
    old_ids = list(snap.get("ids") or [])
    old_count = len(old_ids)
    # chromadb returns numpy arrays: no `or []` (ambiguous truth value), and convert to Python floats
    # (json.dump cannot write numpy.float32).
    raw_emb = snap.get("embeddings")
    emb_list = [] if raw_emb is None else [[float(x) for x in e] for e in raw_emb]
    snap_obj = {"paper_id": paper_id, "run_id": run_id, "collection": COLLECTION[target], "ids": old_ids,
                "embeddings": emb_list,
                "metadatas": list(snap.get("metadatas") or []),
                "documents": list(snap.get("documents") or [])}

    # 3) Write the snapshot to disk, so a crash can still be recovered by hand; log re_embed_purge_planned.
    bdir = _backup_dir(target)
    bdir.mkdir(parents=True, exist_ok=True)
    snap_path = bdir / f"{paper_id}_{run_id}.json"
    snap_path.write_text(json.dumps(snap_obj, ensure_ascii=False), encoding="utf-8")
    log(_MODULE, "re_embed_purge_planned", data={
        "paper_id": paper_id, "old_count": old_count, "snapshot_path": str(snap_path),
        "collection": COLLECTION[target], "trigger": "re_embed_from_rawmd", "basis": "显式重嵌(人触发)"})

    # 4) Delete the old vectors and confirm none are left (embedding over a leftover id would be
    #    ignored silently and mix old and new); deleted_count records the real number.
    if old_count:
        col.delete(where={"paper_id": paper_id})
    remaining_after = len(col.get(where={"paper_id": paper_id}).get("ids") or [])
    deleted = old_count - remaining_after
    log(_MODULE, "re_embed_purge", data={
        "paper_id": paper_id, "old_count": old_count, "remaining_after_delete": remaining_after,
        "deleted_count": deleted, "collection": COLLECTION[target],
        "trigger": "re_embed_from_rawmd", "basis": "显式重嵌(人触发)"})
    if remaining_after:                       # vectors left behind: restore from the snapshot instead of embedding
        log_error(_MODULE, "re_embed_purge_not_clean", context={
            "step": "re_embed_from_rawmd/purge", "paper_id": str(paper_id),
            "error": (f"delete 后仍剩 {remaining_after} 条(应 0);无法自证旧向量完好,"
                      f"转从快照恢复到重嵌前状态(不 embed 以免混合态)。")})
        return _restore_from_snapshot(
            col, paper_id, doc_type, target, snap_obj, snap_path, rawmd_path, cleanp, chunksp,
            reason="purge 未清净", cause_detail=f"delete 后仍剩 {remaining_after} 条(应 0)", error_type="DocumentProcessingError")

    # 5) Write the new vectors.
    try:
        summary = embed(chunksp, target=target)
    except (DocumentProcessingError, GatewayError) as exc:
        return _restore_from_snapshot(
            col, paper_id, doc_type, target, snap_obj, snap_path, rawmd_path, cleanp, chunksp,
            reason="新嵌入失败", cause_detail=f"{type(exc).__name__}: {exc}", error_type=type(exc).__name__)

    # 6) Success: delete the snapshot (chroma-backup is normally empty).
    snap_path.unlink(missing_ok=True)
    log(_MODULE, "pipeline_finished", data={
        "paper_id": paper_id, "target": target, "backend": summary.get("backend"),
        "count": summary.get("count"), "run_id": summary.get("embedding_run_id"), "reembed": True})
    return PipelineResult(status="completed", paper_id=paper_id, doc_type=doc_type, target=target,
                          rawmd_path=str(rawmd_path), cleanmd_path=str(cleanp), chunks_path=str(chunksp),
                          embed_summary=summary)


def _restore_from_snapshot(col, paper_id, doc_type, target, snap_obj, snap_path, rawmd_path, cleanp, chunksp,
                           *, reason, cause_detail, error_type) -> PipelineResult:
    """After a failed re-embed, restore the old vectors from the snapshot: delete what is there, confirm
    it is empty, add everything back, check the count. The gateway is never called. Success logs
    re_embed_restore; failure logs re_embed_restore_failed and keeps the snapshot for a person. The
    messages say only what the code can confirm. Restoring everything is repeatable and does not rely
    on whatever was left behind."""
    old_ids = snap_obj["ids"]
    try:
        # Delete and confirm empty first: after an abort, new chunk ids or undeleted old ones may be
        # there, and adding over them would be ignored silently and mix old and new.
        col.delete(where={"paper_id": paper_id})
        pre = len(col.get(where={"paper_id": paper_id}).get("ids") or [])
        if pre:                                      # not empty: do not add; report the restore as failed
            raise RuntimeError(f"清净失败:delete 后仍剩 {pre} 条")
        if old_ids:                                  # nothing to add back when the paper had no vectors
            col.add(ids=old_ids, embeddings=snap_obj["embeddings"],
                    metadatas=snap_obj["metadatas"], documents=snap_obj["documents"])   # the old vectors as they were; no model call
        restored = len(col.get(where={"paper_id": paper_id}).get("ids") or [])
        if restored != len(old_ids):
            raise RuntimeError(f"恢复条数不符:期望 {len(old_ids)}、实得 {restored}")
    except Exception as rexc:                         # restore failed: keep the snapshot and say how to restore by hand
        log_error(_MODULE, "re_embed_restore_failed", context={
            "step": "re_embed_from_rawmd/restore", "paper_id": str(paper_id),
            "error": (f"重嵌中止({reason})后旧向量恢复失败,快照(含原文 chunk 文本、属运维区本地文件)保留在 {snap_path},"
                      f"请人工按日志恢复。触发:{cause_detail};restore 失败:{type(rexc).__name__}: {rexc}")})
        return PipelineResult(status="failed", paper_id=paper_id, doc_type=doc_type, target=target,
                              rawmd_path=str(rawmd_path), cleanmd_path=str(cleanp), chunks_path=str(chunksp),
                              stopped_at=f"重嵌中止({reason});restore 失败", error_type=error_type,
                              error_message=(f"重嵌中止({reason}:{cause_detail})且旧向量恢复失败;"
                                             f"快照(含原文)在 {snap_path},请人工恢复。"))
    # Restored: delete the snapshot.
    snap_path.unlink(missing_ok=True)
    log(_MODULE, "re_embed_restore", data={
        "paper_id": paper_id, "restored_count": len(old_ids), "collection": COLLECTION[target], "reason": reason,
        "note": ("重嵌中止,旧向量已按快照原样恢复到重嵌前(未重调 MODEL_GATEWAY);"
                 "⚠ CleanMD/Chunks 已按当前 RawMD 重生,需再次 re_embed 才能使文件与向量重新一致")})
    return PipelineResult(status="failed", paper_id=paper_id, doc_type=doc_type, target=target,
                          rawmd_path=str(rawmd_path), cleanmd_path=str(cleanp), chunks_path=str(chunksp),
                          stopped_at=f"重嵌中止({reason});旧向量已恢复", error_type=error_type,
                          error_message=(f"重嵌中止({reason}:{cause_detail});旧向量已按快照恢复、库回到重嵌前状态。"
                                         f"⚠ CleanMD/Chunks 已重生,需再次 re_embed 才能使文件与向量重新一致。"))
