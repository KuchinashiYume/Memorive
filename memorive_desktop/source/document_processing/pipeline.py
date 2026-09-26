"""DOCUMENT_PROCESSING 流水线编排(ServiceContracts 第 4 步)。

三个对外入口(**只编排既有五段,不改 DocumentIngest–DocumentEmbed 内部逻辑**;闸门/重嵌/清库只在本编排层):
  - `run_pipeline(...)`         正线:ingest→convert→〔笔记闸门〕→clean→chunk→embed;笔记在 DocumentConvert 后**暂停**(正常返回、非异常)。
  - `resume_after_note_review`  人核 RawMD 后续跑 DocumentClean→DocumentEmbed;只靠 rawmd_path,元数据全从 frontmatter 读。
  - `re_embed_from_rawmd`       **唯一允许清旧向量**的显式入口;落盘快照 + 先清净再恢复(embed 失败可回滚,守「数据永不误删」)。

红线:
  - 只有 `re_embed_from_rawmd` 能 `col.delete`;run_pipeline / resume **结构上无清库能力**。
  - 默认非幂等重跑:DocumentEmbed 遇 chunk_id 已存在 → 照旧 `VectorDuplicateError` 挡下(只增不改),提示走 re_embed。
  - 失败处理:受控 `DocumentProcessingError/GatewayError` 捕获为 `status="failed"` 结果;非受控异常(疑 bug)**上抛不吞**。
  - `doc_type` 未进 DocumentEmbed 向量 metadata(缺口只记文档,见 Memorive_DOCUMENT_PROCESSING_DOCUMENT_PROCESSING_模块契约与骨架设计.md;本步不改五段)。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml
from runtime_log import log, log_error
from model_gateway.errors import GatewayError

from .config import COLLECTION, chroma_dir
from .errors import DocumentProcessingError, VectorDuplicateError
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


def _backup_dir(target: str) -> Path:
    """re_embed 快照落盘目录:与向量库同根旁置(sandbox→{OPS_ROOT}/chroma-backup)。
    ⚠ 属**运维区(OPS_ROOT)、库外、不入 git**(已核实:该路径 git check-ignore=outside repository;
    运维/chroma 完全不在 git,承 D0)——快照含 documents(原文 chunk 文本),他人托付原文仅落本地运维、绝不上云。
    随 chroma_dir 的 env 覆盖而移(测试自隔离)。"""
    return chroma_dir(target).parent / "chroma-backup"


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


def _failed(stage, paper_id, doc_type, target, exc, *, extra_hint="", **paths) -> PipelineResult:
    """受控错误 → 统一 failed 结果 + RUNTIME_LOG pipeline_failed(该段自身已写 detail;此处记编排级归位)。"""
    msg = str(exc) + extra_hint
    log_error(_MODULE, f"pipeline_failed@{stage}", context={
        "step": f"pipeline/{stage}", "error": f"{type(exc).__name__}: {exc}",
        "paper_id": str(paper_id), "stopped_at": stage})
    return PipelineResult(status="failed", paper_id=paper_id, doc_type=doc_type, target=target,
                          stopped_at=stage, error_type=type(exc).__name__, error_message=msg, **paths)


def _run_tail(rawmd_path, target, paper_id, doc_type, *, finished_event="pipeline_finished") -> PipelineResult:
    """DocumentClean→DocumentEmbed(clean→chunk→embed);逐段跟踪 stopped_at;受控错误→failed(去重错补重嵌提示)。"""
    cleanp = chunksp = None
    stage = "M1c"
    try:
        cleanp = clean(rawmd_path, target=target); stage = "DocumentChunk"
        chunksp = chunk(cleanp, target=target); stage = "M1e"
        summary = embed(chunksp, target=target)
    except (DocumentProcessingError, GatewayError) as exc:
        hint = ""
        if isinstance(exc, VectorDuplicateError):    # 去重挡下 → 指路唯一重嵌入口(不在正线/resume 清库)
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
    """正线:一键跑五段。笔记(doc_type=note)在 DocumentConvert 后暂停等人核(除非 skip_note_review)。"""
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

    if doc_type == "note" and not skip_note_review:                 # ■ 笔记闸门:暂停(正常返回)
        hint = f"人核 RawMD 转录/格式后,调 resume_after_note_review(r'{rawmd}', target='{target}')"
        log(_MODULE, "note_review_pending", data={
            "paper_id": paper_id, "rawmd_path": rawmd, "stopped_at": "after_document_convert"})
        return PipelineResult(status="note_review_pending", paper_id=paper_id, doc_type=doc_type,
                              target=target, rawmd_path=rawmd, stopped_at="after_document_convert", resume_hint=hint)
    if doc_type == "note" and skip_note_review:                     # 跳过也留痕、不静默
        log(_MODULE, "note_review_skipped", data={
            "paper_id": paper_id, "rawmd_path": rawmd,
            "note": "doc_type=note 但闸门被显式跳过(skip_note_review=True)"})

    return _run_tail(rawmd, target, paper_id, doc_type)


def resume_after_note_review(rawmd_path, *, target: str = "sandbox") -> PipelineResult:
    """人核 RawMD 后续跑 DocumentClean→DocumentEmbed;元数据全从 frontmatter 读(受控校验;不依赖内存 ConvertResult)。"""
    try:
        fm = _read_frontmatter(rawmd_path)
    except DocumentProcessingError as exc:
        return _failed("读 RawMD frontmatter", None, None, target, exc, rawmd_path=str(rawmd_path))
    paper_id, doc_type = fm["paper_id"], fm["doc_type"]
    log(_MODULE, "note_review_confirmed", data={"paper_id": paper_id, "rawmd_path": str(rawmd_path)})
    log(_MODULE, "pipeline_resumed", data={"paper_id": paper_id, "target": target, "stage": "M1c"})
    return _run_tail(rawmd_path, target, paper_id, doc_type)


def re_embed_from_rawmd(rawmd_path, *, target: str = "sandbox") -> PipelineResult:
    """★显式重嵌(唯一清库入口):落盘快照 + 先清净再恢复。embed 失败 / purge 未清净 → 旧向量按快照恢复到重嵌前(守「数据永不误删」)。
    ★合法 paper_id(经 _read_frontmatter 受控校验)之后才开 collection,杜绝 paper_id=None 走到 col.delete/get where。
    ⚠ 并发:承 DocumentEmbed「单写者假设」——勿对同一 paper_id 并发 re_embed / run_pipeline;将来多进程需在此加锁(当前不实现、留排查锚点)。"""
    try:
        fm = _read_frontmatter(rawmd_path)
    except DocumentProcessingError as exc:
        return _failed("读 RawMD frontmatter", None, None, target, exc, rawmd_path=str(rawmd_path))
    paper_id, doc_type = fm["paper_id"], fm["doc_type"]

    # 1) 先 clean→chunk:确认新产物能出(产不出就早失败,此刻还没碰旧向量)
    stage = "DocumentClean(重嵌前置)"
    try:
        cleanp = clean(rawmd_path, target=target); stage = "DocumentChunk(重嵌前置)"
        chunksp = chunk(cleanp, target=target)
    except (DocumentProcessingError, GatewayError) as exc:
        return _failed(stage, paper_id, doc_type, target, exc, rawmd_path=str(rawmd_path))

    col = _open_collection(target)
    run_id = uuid.uuid4().hex

    # 2) 取旧向量快照(含 embeddings/metadatas/documents)
    snap = col.get(where={"paper_id": paper_id}, include=["embeddings", "metadatas", "documents"])
    old_ids = list(snap.get("ids") or [])
    old_count = len(old_ids)
    # ⚠ chromadb 的 embeddings 返回 numpy 数组:不能 `or []`(布尔歧义崩)、须显式转 Python float(否则 json.dump 崩 numpy.float32)
    raw_emb = snap.get("embeddings")
    emb_list = [] if raw_emb is None else [[float(x) for x in e] for e in raw_emb]
    snap_obj = {"paper_id": paper_id, "run_id": run_id, "collection": COLLECTION[target], "ids": old_ids,
                "embeddings": emb_list,
                "metadatas": list(snap.get("metadatas") or []),
                "documents": list(snap.get("documents") or [])}

    # 3) ★快照落盘(非纯内存:进程崩了也能手工恢复)+ RUNTIME_LOG re_embed_purge_planned
    bdir = _backup_dir(target); bdir.mkdir(parents=True, exist_ok=True)
    snap_path = bdir / f"{paper_id}_{run_id}.json"
    snap_path.write_text(json.dumps(snap_obj, ensure_ascii=False), encoding="utf-8")
    log(_MODULE, "re_embed_purge_planned", data={
        "paper_id": paper_id, "old_count": old_count, "snapshot_path": str(snap_path),
        "collection": COLLECTION[target], "trigger": "re_embed_from_rawmd", "basis": "显式重嵌(人触发)"})

    # 4) 清旧 + ★清净验证(delete 未清净就 embed 会撞已存在 id 静默忽略 → 混合态);deleted_count 记实际值
    if old_count:
        col.delete(where={"paper_id": paper_id})
    remaining_after = len(col.get(where={"paper_id": paper_id}).get("ids") or [])
    deleted = old_count - remaining_after
    log(_MODULE, "re_embed_purge", data={
        "paper_id": paper_id, "old_count": old_count, "remaining_after_delete": remaining_after,
        "deleted_count": deleted, "collection": COLLECTION[target],
        "trigger": "re_embed_from_rawmd", "basis": "显式重嵌(人触发)"})
    if remaining_after:                       # 未清净 → 代码无法自证旧向量完好,**不谎报**;转从快照恢复到重嵌前(不 embed)
        log_error(_MODULE, "re_embed_purge_not_clean", context={
            "step": "re_embed_from_rawmd/purge", "paper_id": str(paper_id),
            "error": (f"delete 后仍剩 {remaining_after} 条(应 0);无法自证旧向量完好,"
                      f"转从快照恢复到重嵌前状态(不 embed 以免混合态)。")})
        return _restore_from_snapshot(
            col, paper_id, doc_type, target, snap_obj, snap_path, rawmd_path, cleanp, chunksp,
            reason="purge 未清净", cause_detail=f"delete 后仍剩 {remaining_after} 条(应 0)", error_type="DocumentProcessingError")

    # 5) 写新向量
    try:
        summary = embed(chunksp, target=target)
    except (DocumentProcessingError, GatewayError) as exc:
        return _restore_from_snapshot(
            col, paper_id, doc_type, target, snap_obj, snap_path, rawmd_path, cleanp, chunksp,
            reason="新嵌入失败", cause_detail=f"{type(exc).__name__}: {exc}", error_type=type(exc).__name__)

    # 6) 成功 → 删快照(chroma-backup 平时保持空)
    snap_path.unlink(missing_ok=True)
    log(_MODULE, "pipeline_finished", data={
        "paper_id": paper_id, "target": target, "backend": summary.get("backend"),
        "count": summary.get("count"), "run_id": summary.get("embedding_run_id"), "reembed": True})
    return PipelineResult(status="completed", paper_id=paper_id, doc_type=doc_type, target=target,
                          rawmd_path=str(rawmd_path), cleanmd_path=str(cleanp), chunks_path=str(chunksp),
                          embed_summary=summary)


def _restore_from_snapshot(col, paper_id, doc_type, target, snap_obj, snap_path, rawmd_path, cleanp, chunksp,
                           *, reason, cause_detail, error_type) -> PipelineResult:
    """重嵌中止(embed 失败 / purge 未清净)→ 用落盘快照把旧向量恢复到**重嵌前**:先清净→确认空→**全清全补** add→验条数;
    绝不重调 MODEL_GATEWAY。恢复成功=`re_embed_restore`;失败=`re_embed_restore_failed`(保留快照指路人工)。
    文案中性、只说代码能自证的话(不谎报"未受影响")。全清全补是幂等确定的,比"补回 missing"更强(不需信任残留那几条)。"""
    old_ids = snap_obj["ids"]
    try:
        # ★先清净 + 确认空:中止时库里可能残留(embed 已写部分新 chunk_id / purge 没删净);不清净就 add 会撞已存在 id 被静默忽略 → 混合态
        col.delete(where={"paper_id": paper_id})
        pre = len(col.get(where={"paper_id": paper_id}).get("ids") or [])
        if pre:                                      # 清净未成功 → 不 add(否则混合态),转恢复失败分支
            raise RuntimeError(f"清净失败:delete 后仍剩 {pre} 条")
        if old_ids:                                  # old_count>0 才恢复;=0 时清净后即回到重嵌前(该篇无向量)
            col.add(ids=old_ids, embeddings=snap_obj["embeddings"],
                    metadatas=snap_obj["metadatas"], documents=snap_obj["documents"])   # 旧向量原样,不调 MODEL_GATEWAY
        restored = len(col.get(where={"paper_id": paper_id}).get("ids") or [])
        if restored != len(old_ids):
            raise RuntimeError(f"恢复条数不符:期望 {len(old_ids)}、实得 {restored}")
    except Exception as rexc:                         # 恢复失败 → 保留快照、明确指路人工恢复
        log_error(_MODULE, "re_embed_restore_failed", context={
            "step": "re_embed_from_rawmd/restore", "paper_id": str(paper_id),
            "error": (f"重嵌中止({reason})后旧向量恢复失败,快照(含原文 chunk 文本、属运维区本地文件)保留在 {snap_path},"
                      f"请人工按日志恢复。触发:{cause_detail};restore 失败:{type(rexc).__name__}: {rexc}")})
        return PipelineResult(status="failed", paper_id=paper_id, doc_type=doc_type, target=target,
                              rawmd_path=str(rawmd_path), cleanmd_path=str(cleanp), chunks_path=str(chunksp),
                              stopped_at=f"重嵌中止({reason});restore 失败", error_type=error_type,
                              error_message=(f"重嵌中止({reason}:{cause_detail})且旧向量恢复失败;"
                                             f"快照(含原文)在 {snap_path},请人工恢复。"))
    # 恢复成功 → 删快照
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
