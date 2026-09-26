"""M1e 嵌入 —— 传送带第五节(T3 第 3 步)。

读 `[Chunks].jsonl`(M1d 产)→ 按 `data_ownership` 分流 → 调 M9 拿向量 → 写 Chroma + metadata → 记 M11。

流程(承上会话定稿 + 对抗性审查加固):
  ① 字段/唯一性校验:关键字段非空 + **篇内 chunk_id 唯一**(早于 M9/Chroma,防裸异常与部分写)。
  ② 归属分流:self→云 `embed_cloud` / entrusted→本地 `embed_local` / **缺失或未知→报错挡下**(不猜、不默认上云,拍板①)。
  ③ 安全阀(红线):entrusted 走本地前,先经 **M9 healthcheck** 纯探测;未就绪 → 抛 `EmbedLocalNotReadyError`
     中断本篇、**零写入、绝不静默上云**;M1e 从不 catch 后改调 embed_cloud(结构性保证)。
     ⚠「本地=回环」由 M9 ollama adapter 硬断言(endpoint 非 localhost 即挡下),M1e 不重定义 endpoint。
  ④ 整库重嵌前置检查:目标 collection 已含不同 `embedding_model` / `vector_type` → 挡下(取回不全则 fail-closed 保守挡)。
  ⑤ 去重前置检查:`col.get(ids)` 有已存在 → 报错(只增不改)。
  ⑥ 原子:先内存嵌完(逐块调 M9、验向量)——**add 之前任一失败零写入**;分批写(≤5000/批,防 Chroma 批上限)+
     **写后校验**;写阶段任一失败 → 按 `embedding_run_id` 精确回滚本批、**失败即零残留**,再上抛(裸异常转受控)。

⚠ Chroma 重复 id 行为(实测 1.5.9):**跨 add / 与库中已存在 id → 静默忽略**(保留旧值不覆盖);
  **同一 add 调用内部重复 id → 抛 DuplicateIDError**。故篇内唯一性必须由本模块 ① 显式预检,不靠 Chroma。
⚠ 并发:M1e 假设**单写者**(去重预检与 add 非原子、Chroma 文件级无跨进程锁);post-write run_id 校验是竞态最后兜底。

守边界:M1e 只「读归属→选 embed_cloud/local 槽→调 m9_gateway.embed/healthcheck→写 chroma+metadata→记 M11」;
endpoint/model/**归一化**/**回环断言**/健康探测/**归属保护文案**全在 M9;canonical 模型名从 M9 承带(不自定义)。

留痕:每条向量 metadata 12 项,只增不改。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from m11_log import log, log_error
from m9_gateway import CANONICAL_EMBED_MODEL as CANON_EMBED_MODEL   # 单一来源(承对抗审查 #8)
from m9_gateway import embed as m9_embed
from m9_gateway import healthcheck as m9_healthcheck

from .config import COLLECTION, chroma_dir
from .errors import (EmbedLocalNotReadyError, M1Error, OwnershipMissingError,
                     OwnershipRouteError, VectorDuplicateError,
                     VectorModelConflictError, VectorWriteVerifyError)
from .chroma_durability import ensure_chroma_writable, write_embedding_checkpoint

VECTOR_TYPE = "chunk"                   # 本阶段只嵌下层原文向量(卡片字段向量属 T4/T7,不在此)
_SLOT = {"self": "embed_cloud", "entrusted": "embed_local"}    # 归属 → M9 槽
_BACKEND = {"embed_cloud": "cloud", "embed_local": "local"}
_MAX_ADD_BATCH = 5000                   # < Chroma 硬上限 5461,分批写防 InternalError
_STRUCTURE_METADATA = ("page_start", "page_end", "section_path", "block_type")


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with temporary.open("xb") as stream:
        stream.write(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _spool_root(target: str, paper_id: str, source_sha256: str) -> Path:
    safe_paper = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in str(paper_id)
    )[:96]
    parent = (chroma_dir(target).parent / "embedding-spool").resolve(strict=False)
    return (parent / f"{safe_paper}_{source_sha256[:20].lower()}").resolve(
        strict=False
    )


def _spool_record_path(root: Path, index: int, chunk_id: str) -> Path:
    identity = hashlib.sha256(chunk_id.encode("utf-8")).hexdigest()[:16]
    return root / f"{index:06d}_{identity}.json"


def _load_spooled_vector(path: Path, record: dict) -> tuple[list[float], str] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise VectorWriteVerifyError(
            f"[{record['chunk_id']}] 嵌入恢复检查点结构无效；已挡下。"
        )
    text_sha = hashlib.sha256(record["text"].encode("utf-8")).hexdigest().upper()
    vector = value.get("vector")
    if (
        value.get("chunk_id") != record["chunk_id"]
        or value.get("text_sha256") != text_sha
        or value.get("embedding_model") != CANON_EMBED_MODEL
        or not isinstance(value.get("provider_model_id"), str)
        or not isinstance(vector, list)
        or not vector
        or any(
            not isinstance(item, (int, float)) or isinstance(item, bool)
            for item in vector
        )
    ):
        raise VectorWriteVerifyError(
            f"[{record['chunk_id']}] 嵌入恢复检查点与当前 chunk 不一致；已挡下。"
        )
    return [float(item) for item in vector], value["provider_model_id"]


def _remove_spool(root: Path, target: str) -> None:
    parent = (chroma_dir(target).parent / "embedding-spool").resolve(strict=False)
    resolved = root.resolve(strict=False)
    if resolved.parent != parent or not resolved.name:
        raise VectorWriteVerifyError("嵌入检查点清理路径越界；已保留检查点并挡下。")
    if resolved.is_dir():
        shutil.rmtree(resolved)


def _now_minute() -> str:
    """一次运行统一的**带时区**时间戳、**到分钟**(承约束三:非 naive、不逐 chunk 各取 now)。"""
    return datetime.now().astimezone().isoformat(timespec="minutes")


def _open_collection(target: str):
    """打开(或建)目标 Chroma collection。物理落库外运维区(承 D2 隔离);chromadb 懒加载。"""
    import chromadb                                   # 懒加载:不装 chromadb 时不阻塞 import m1_pipeline
    cdir = chroma_dir(target)
    cdir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(cdir))
    name = COLLECTION[target]
    return client.get_or_create_collection(name), name


def _validate_records(recs: list, chunk_ids: list, paper_id, src: str):
    """① 字段完整性 + 篇内唯一性(早于 M9/Chroma;只记 chunk_id、不记文本)。"""
    for r in recs:                                     # 关键字段非空(防 metadata None → Chroma 裸 TypeError)
        cid, pid = r.get("chunk_id"), r.get("paper_id")
        if not (isinstance(cid, str) and cid.strip()):
            log_error("M1e", "chunk_id 缺失/非法,挡下", context={
                "step": "embed/字段校验", "paper_id": str(paper_id), "source": src,
                "error": f"有 chunk 的 chunk_id 缺失/非字符串:{cid!r};无法作向量 id,挡下。"})
            raise M1Error(f"[{paper_id}] chunk_id 缺失/非法:{cid!r}。")
        if not (isinstance(pid, str) and pid.strip()):
            log_error("M1e", "paper_id 缺失/非法,挡下", context={
                "step": "embed/字段校验", "paper_id": str(paper_id), "chunk_id": cid, "source": src,
                "error": f"chunk {cid} 的 paper_id 缺失/非字符串:{pid!r};挡下。"})
            raise M1Error(f"[{paper_id}] chunk {cid} 的 paper_id 缺失/非法。")
        txt = r.get("text")                            # text 非空 str(免下游 r["text"] 裸 KeyError;空文本无可嵌)
        if not (isinstance(txt, str) and txt.strip()):
            log_error("M1e", "chunk text 缺失/空,挡下", context={
                "step": "embed/字段校验", "paper_id": str(paper_id), "chunk_id": cid, "source": src,
                "error": f"chunk {cid} 的 text 缺失/空/非字符串;无可嵌,挡下。"})
            raise M1Error(f"[{paper_id}] chunk {cid} 的 text 缺失/空;挡下。")
    seen, dups = set(), set()                          # 篇内 chunk_id 唯一(防 Chroma DuplicateIDError / 静默去重致部分写)
    for c in chunk_ids:
        (dups if c in seen else seen).add(c)
    if dups:
        log_error("M1e", "篇内 chunk_id 重复,挡下", context={
            "step": "embed/唯一性", "paper_id": str(paper_id), "source": src,
            "error": f"同篇 chunk_id 重复 {sorted(dups)[:5]}(共 {len(dups)});一篇内 chunk_id 必须唯一,挡下(零写入)。"})
        raise VectorDuplicateError(f"[{paper_id}] 篇内重复 chunk_id {sorted(dups)[:5]};必须唯一。")


def _route(recs: list, paper_id, src: str) -> str:
    """② 归属分流:返回统一 ownership;缺失/非字符串/未知/混合一律报错挡下(绝不默认上云)。"""
    raw = [r.get("data_ownership") for r in recs]
    # 先判类型再 strip:非字符串(int/list/bool/None)/空串 → 归属不可读,调 M9 前挡下(免 .strip() 裸 AttributeError)
    bad = [v for v in raw if not isinstance(v, str) or not v.strip()]
    if bad:
        kinds = sorted({repr(v) if isinstance(v, str) else type(v).__name__ for v in bad})
        log_error("M1e", "归属标记缺失/非字符串,挡下(未上云)", context={
            "step": "embed/分流", "paper_id": str(paper_id), "source": src,
            "error": f"有 chunk 的 data_ownership 缺失/空/非字符串({kinds});归属不明**绝不默认上云**,挡下待人确认(拍板①)。"})
        raise OwnershipMissingError(f"[{paper_id}] 有 chunk 归属缺失/非字符串({kinds});归属不明不嵌入、绝不默认云端。")
    owners = {v.strip() for v in raw}
    unknown = owners - {"self", "entrusted"}
    if unknown:
        log_error("M1e", "归属标记非法,挡下(未上云)", context={
            "step": "embed/分流", "paper_id": str(paper_id), "source": src,
            "error": f"未知 data_ownership 值 {sorted(unknown)};只认 self/entrusted,挡下。"})
        raise OwnershipRouteError(f"[{paper_id}] 未知归属值 {sorted(unknown)};只认 self/entrusted。")
    if len(owners) > 1:
        log_error("M1e", "同篇归属不一致,挡下", context={
            "step": "embed/分流", "paper_id": str(paper_id), "source": src,
            "error": f"同篇 chunks 归属混合 {sorted(owners)};一篇应统一归属,挡下。"})
        raise OwnershipRouteError(f"[{paper_id}] 同篇归属混合 {sorted(owners)};一篇应统一,挡下。")
    return owners.pop()


def _safety_valve(paper_id):
    """③ 安全阀:entrusted 走本地前,经 M9 healthcheck 纯探测;未就绪→报错挡下、零写入、绝不上云。"""
    hc = m9_healthcheck("embed_local")               # M9 报状态(纯 GET /api/tags,不发任何 chunk 文本;含回环断言)
    if not hc.get("ready"):
        msg = hc.get("message") or "[M9/embed_local] 本地嵌入未就绪;他人托付数据已挡下、未上云。"
        log_error("M1e", "安全阀:他人托付+本地未就绪,挡下(未上云)", context={
            "step": "embed/安全阀", "paper_id": str(paper_id),
            "dependency": "ollama/bge-m3(embed_local)", "error": msg})
        raise EmbedLocalNotReadyError(msg)           # ← 直接上抛,M1e 从不 catch 后改调 embed_cloud


def _check_rebuild_conflict(col, col_name, paper_id):
    """④ 整库重嵌前置检查:已有向量的 embedding_model / vector_type 与本次不一致 → 挡下。
    取回不全(未来 chromadb 分页?)→ fail-closed 保守挡下,不漏检异模型(承对抗审查)。"""
    total = col.count()
    if total == 0:
        return                                        # 空库,无冲突
    metas = col.get(include=["metadatas"]).get("metadatas") or []
    if len(metas) < total:
        log_error("M1e", "整库一致性检查取回不全,保守挡下", context={
            "step": "embed/整库检查", "paper_id": str(paper_id),
            "error": f"collection {col_name} count={total} 但 get 只回 {len(metas)};无法确保穷尽,保守挡下(防漏检异模型)。"})
        raise VectorModelConflictError(
            f"collection {col_name} 一致性检查取回不全({len(metas)}<{total});保守挡下,请人工核查。")
    # 不剔除 None:任一条 metadata 缺 embedding_model / vector_type 即当作冲突 fail-closed(缺字段≠无冲突)
    models = {(m or {}).get("embedding_model") for m in metas}
    vtypes = {(m or {}).get("vector_type") for m in metas}
    if models - {CANON_EMBED_MODEL}:                  # 含 None(缺字段)即触发
        shown = sorted(map(str, models))              # map(str) 避免 None 与 str 排序比较崩
        log_error("M1e", "整库重嵌冲突:模型不一致/缺失,挡下", context={
            "step": "embed/整库检查", "paper_id": str(paper_id),
            "error": (f"collection {col_name} 含 embedding_model={shown}(非全 {CANON_EMBED_MODEL},None=缺字段);"
                      f"换模型=整库重嵌、不能新旧混查。")})
        raise VectorModelConflictError(
            f"collection {col_name} 含模型 {shown}(非全 {CANON_EMBED_MODEL});换模型须整库重嵌(清库/新 collection),不能混入。")
    if vtypes - {VECTOR_TYPE}:
        shown = sorted(map(str, vtypes))
        log_error("M1e", "向量类型冲突/缺失,挡下", context={
            "step": "embed/整库检查", "paper_id": str(paper_id),
            "error": f"collection {col_name} 含 vector_type={shown}(非全 {VECTOR_TYPE},None=缺字段;防卡片字段向量混入 chunk 库)。"})
        raise VectorModelConflictError(
            f"collection {col_name} 含 vector_type {shown}(非全 {VECTOR_TYPE});不与 chunk 向量混库。")


def _check_duplicates(col, chunk_ids, paper_id):
    """⑤ 去重前置检查:库内已存在 chunk_id(Chroma add 遇已存在静默忽略,故须显式预检)。"""
    existing = col.get(ids=chunk_ids).get("ids") or []
    if existing:
        log_error("M1e", "chunk_id 已存在,挡下(只增不改)", context={
            "step": "embed/去重", "paper_id": str(paper_id),
            "error": (f"{len(existing)} 个 chunk_id 已在 collection(如 {existing[:3]});"
                      f"只增不改,要重嵌请清库/新 collection/走重嵌流程。")})
        raise VectorDuplicateError(
            f"[{paper_id}] {len(existing)} 个 chunk_id 已存在;只增不改,勿重复写。")


def _verify_written(col, chunk_ids, run_id, paper_id):
    """写后 post-write verification:取回数量 + 本批 run_id/model/vector_type 全符合。"""
    got = col.get(ids=chunk_ids, include=["metadatas"])
    got_ids = got.get("ids") or []
    if len(got_ids) != len(chunk_ids):
        raise VectorWriteVerifyError(f"[{paper_id}] 写后校验数量不符:写 {len(chunk_ids)}、回 {len(got_ids)}。")
    for m in (got.get("metadatas") or []):
        if not (m and m.get("embedding_run_id") == run_id
                and m.get("embedding_model") == CANON_EMBED_MODEL
                and m.get("vector_type") == VECTOR_TYPE):
            raise VectorWriteVerifyError(f"[{paper_id}] 写后校验:metadata 与本批不符。")


def _write_atomic(col, chunk_ids, vectors, metadatas, documents, run_id, paper_id):
    """⑥ 分批写(≤5000/批)+ 写后校验;任一步失败 → 按 run_id 精确回滚本批、**失败即零残留**,再上抛。"""
    try:
        for i in range(0, len(chunk_ids), _MAX_ADD_BATCH):
            s = slice(i, i + _MAX_ADD_BATCH)
            col.add(ids=chunk_ids[s], embeddings=vectors[s], metadatas=metadatas[s], documents=documents[s])
        _verify_written(col, chunk_ids, run_id, paper_id)
    except Exception as exc:
        try:
            col.delete(where={"embedding_run_id": run_id})     # 精确回滚本批(run_id 唯一),使"失败=零残留"
        except Exception as del_exc:
            log_error("M1e", "回滚失败(需人工按 run_id 清理)", context={
                "step": "embed/回滚", "paper_id": str(paper_id),
                "error": f"写失败后按 run_id={run_id} 回滚亦失败:{type(del_exc).__name__}: {del_exc}"})
        if not isinstance(exc, M1Error):                       # 裸 Chroma 异常 → 记痕 + 转受控(守 errors 契约)
            log_error("M1e", "写入失败(已回滚),转受控", context={
                "step": "embed/写入", "paper_id": str(paper_id),
                "error": f"{type(exc).__name__}: {exc};已按 run_id={run_id} 回滚零残留。"})
            raise VectorWriteVerifyError(
                f"[{paper_id}] 写入失败(已回滚零残留):{type(exc).__name__}: {exc}") from exc
        raise                                                 # 受控错误(如写后校验)→ 回滚后原样上抛


def _vector_metadata(r: dict, *, ownership: str, provider_model_id: str,
                     backend: str, embedded_at: str, run_id: str) -> dict:
    """Build Chroma metadata and additively preserve valid M1d structure fields."""
    metadata = {
        "paper_id": r["paper_id"],
        "chunk_id": r["chunk_id"],
        "data_ownership": ownership,
        "embedding_model": CANON_EMBED_MODEL,
        "provider_model_id": provider_model_id,
        "embed_backend": backend,
        "embedded_at": embedded_at,
        "embedding_run_id": run_id,
        "vector_type": VECTOR_TYPE,
        "review_status": "pending",
        "is_derived": False,
        "chunk_schema_version": str(r.get("chunk_schema_version", "1")),
    }
    for key in ("page_start", "page_end"):
        value = r.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            metadata[key] = value
    for key in ("section_path", "block_type"):
        value = r.get(key)
        if isinstance(value, str) and value.strip():
            metadata[key] = value.strip()
    return metadata


def embed(chunks_jsonl, *, target: str = "sandbox") -> dict:
    """把一篇 `[Chunks].jsonl` 嵌入并写入向量库。返回本次运行摘要。"""
    p = Path(chunks_jsonl)
    if not p.exists():
        log_error("M1e", "Chunks 不存在", context={
            "step": "embed", "error": f"Chunks jsonl 不存在: {p}", "source": str(p)})
        raise M1Error(f"Chunks 不存在: {p}")
    recs = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not recs:
        log_error("M1e", "Chunks 为空", context={
            "step": "embed", "error": f"Chunks 为空、无可嵌: {p}", "source": str(p)})
        raise M1Error(f"Chunks 为空: {p}")
    paper_id = recs[0].get("paper_id")
    chunk_ids = [r.get("chunk_id") for r in recs]

    # ① 字段完整性 + 篇内唯一性(早于分流/M9/Chroma)
    _validate_records(recs, chunk_ids, paper_id, str(p))

    # ② 分流(缺失/未知/混合 → 在调 M9 之前挡下)
    ownership = _route(recs, paper_id, str(p))
    slot = _SLOT[ownership]
    backend = _BACKEND[slot]

    # ③ 安全阀(仅 entrusted;未就绪 → 挡下、零写入、绝不上云)
    if slot == "embed_local":
        _safety_valve(paper_id)

    # ④⑤ 在构造 Chroma client 前隔离不可重启的 HNSW 库。
    try:
        ensure_chroma_writable(target)
    except RuntimeError as exc:
        raise VectorWriteVerifyError(str(exc)) from exc
    col, col_name = _open_collection(target)
    _check_rebuild_conflict(col, col_name, paper_id)
    _check_duplicates(col, chunk_ids, paper_id)

    # ⑥ 逐 chunk 持久化模型结果；Chroma 仍保持整篇原子写入。进程或
    # 本地服务中断后可从最后一个成功 chunk 继续，不重复烧掉前序调用。
    run_id = uuid.uuid4().hex          # 完整 128 位:run_id 兼作回滚 delete 条件,截断碰撞会误删他批向量(守「数据永不误删」)
    embedded_at = _now_minute()
    vectors, models_seen, provider_ids = [], set(), set()
    source_sha256 = hashlib.sha256(p.read_bytes()).hexdigest().upper()
    spool_root = _spool_root(target, str(paper_id), source_sha256)
    spool_root.mkdir(parents=True, exist_ok=True)
    _atomic_json(
        spool_root / "manifest.json",
        {
            "schema_version": "P08Build075EmbeddingSpool-v1",
            "paper_id": paper_id,
            "source_sha256": source_sha256,
            "chunk_count": len(recs),
            "embedding_model": CANON_EMBED_MODEL,
            "raw_text_recorded": False,
        },
    )
    reused_checkpoint_count = 0
    new_embedding_count = 0
    for index, r in enumerate(recs):
        checkpoint_path = _spool_record_path(spool_root, index, r["chunk_id"])
        recovered = _load_spooled_vector(checkpoint_path, r)
        if recovered is not None:
            vector, provider_id = recovered
            vectors.append(vector)
            models_seen.add(CANON_EMBED_MODEL)
            provider_ids.add(provider_id)
            reused_checkpoint_count += 1
            continue
        res = m9_embed(slot, [r["text"]])            # M9 已校验单条返回==1、已归一化 embedding_model
        embs = res.get("embeddings") or []
        if len(embs) != 1:                            # 双保险(M9 已保证)
            log_error("M1e", "单块嵌入返回数量异常", context={
                "step": "embed/累积", "paper_id": str(paper_id), "chunk_id": r["chunk_id"],
                "error": f"chunk {r['chunk_id']} 返回 {len(embs)} 条(期望 1);零写入。"})
            raise VectorWriteVerifyError(f"[{r['chunk_id']}] 单块返回数量异常;零写入。")
        v = embs[0]                                   # 畸形向量(空/None/非数值)校验(承对抗审查 #5)
        if not (isinstance(v, (list, tuple)) and v
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)):
            log_error("M1e", "嵌入向量畸形,挡下", context={
                "step": "embed/累积", "paper_id": str(paper_id), "chunk_id": r["chunk_id"],
                "error": f"chunk {r['chunk_id']} 返回向量非空数值列表(len={len(v) if hasattr(v,'__len__') else '?'});零写入。"})
            raise VectorWriteVerifyError(f"[{r['chunk_id']}] 嵌入向量畸形;零写入。")
        accepted_vector = [float(item) for item in v]
        vectors.append(accepted_vector)
        models_seen.add(res.get("embedding_model"))
        provider_id = str(res.get("provider_model_id") or CANON_EMBED_MODEL)
        provider_ids.add(provider_id)
        _atomic_json(
            checkpoint_path,
            {
                "schema_version": "P08Build075EmbeddingChunkCheckpoint-v1",
                "chunk_id": r["chunk_id"],
                "text_sha256": hashlib.sha256(
                    r["text"].encode("utf-8")
                ).hexdigest().upper(),
                "embedding_model": CANON_EMBED_MODEL,
                "provider_model_id": provider_id,
                "vector": accepted_vector,
                "raw_text_recorded": False,
            },
        )
        new_embedding_count += 1

    if len(vectors) != len(recs):                     # 总数校验(纵深)
        raise VectorWriteVerifyError(f"[{paper_id}] 累积向量数 {len(vectors)} ≠ chunk 数 {len(recs)};零写入。")
    if models_seen != {CANON_EMBED_MODEL}:            # 归一化后必须全 bge-m3
        log_error("M1e", "嵌入模型归一化后非预期", context={
            "step": "embed/一致性", "paper_id": str(paper_id),
            "error": f"本批 embedding_model={sorted(models_seen)}(期望 [{CANON_EMBED_MODEL}]);零写入。"})
        raise VectorModelConflictError(f"[{paper_id}] 本批模型 {sorted(models_seen)} ≠ {CANON_EMBED_MODEL};零写入。")
    provider_model_id = "|".join(sorted(x for x in provider_ids if x)) or CANON_EMBED_MODEL

    documents = [r["text"] for r in recs]             # documents=清洗后 chunk text(拍板①,库外)
    metadatas = [
        _vector_metadata(
            r,
            ownership=ownership,
            provider_model_id=provider_model_id,
            backend=backend,
            embedded_at=embedded_at,
            run_id=run_id,
        )
        for r in recs
    ]

    _write_atomic(col, chunk_ids, vectors, metadatas, documents, run_id, paper_id)
    try:
        write_embedding_checkpoint(
            p,
            target=target,
            embedding_run_id=run_id,
            embedding_model=CANON_EMBED_MODEL,
        )
    except Exception as exc:
        try:
            col.delete(where={"embedding_run_id": run_id})
        except Exception:
            pass
        raise VectorWriteVerifyError(
            f"[{paper_id}] 嵌入完成检查点落盘失败，本批已回滚:{type(exc).__name__}"
        ) from exc
    _remove_spool(spool_root, target)

    log("M1e", "嵌入完成", data={
        "paper_id": paper_id, "collection": col_name, "backend": backend, "chunks": len(recs),
        "embedding_model": CANON_EMBED_MODEL, "embedding_run_id": run_id, "embedded_at": embedded_at})
    return {"paper_id": paper_id, "collection": col_name, "backend": backend,
            "count": len(recs), "embedding_run_id": run_id, "embedded_at": embedded_at,
            "reused_checkpoint_count": reused_checkpoint_count,
            "new_embedding_count": new_embedding_count,
            "durable_resume_enabled": True}
