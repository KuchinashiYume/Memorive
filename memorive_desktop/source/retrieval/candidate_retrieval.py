"""CandidateRetrieval Candidate Retrieval —— chunk 单层召回 + active-only 回读活卡 + 归并回卡片(Retrieval 第 2 步)。

承 Retrieval 第 0 步契约 §一/§二/§五 + 两拍板:
- 拍板①:ServiceContracts 库只 chunk 级向量 → **chunk 单层 overfetch 召回** + 经 paper_id/source_anchor 归并回卡片/字段。
- 拍板②:active-only 是 **post-filter**——回读卡片 frontmatter 活 `review_status`(`card_io.read_status`),
  **绝不信向量库 metadata 里冻结的 pending**。
**只召回、不打包**(排权重/反例名额/token 全留 ContextAssembly);查现成 Chroma、`get_collection` 不建库。

⚠ 距离≠相似度(口径②):Chroma `.query()` 回 `distances`(**越小越近**);排序按 distance **升序**。
⚠ 固定召回(口径①):**单次** query(n_results=min(MAX_OVERFETCH, count))→ active-only → require_verified →
   每卡限额 → 取 target。「够不够」按**最终 final**判(非 raw active——早停会漏补每卡限额/require_verified 挤掉的名额)。
⚠ logical rerank(A·修跨篇混入):active-only 之后保留**可注入排序缝**。旧 score reranker 仅作兼容；
   新 `multimodal_listwise` 使用本地多模态模型返回完整候选 ID 顺序，不伪造 0–1 分数、不做阈值硬砍。
   两者均默认关；本地 listwise 可处理 entrusted，任何失败整批回退 distance，绝不 cloud fallback。
   五维/DerivedPenalty/doc_type 仍不做;active 读活卡、非冻结 metadata。
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
from datetime import datetime
from pathlib import Path

import yaml

from runtime_log import log_reason
from knowledge_admission import knowledge_admission_log
from knowledge_admission.card_io import read_status
from knowledge_admission.errors import CardStateError

from .ownership import bind_authority_projection, snapshot_fields
from .types import Candidate, CandidateSet

# ── 默认值:全 env 可配、读取失败回默认;RUNTIME_LOG 记本次实际用值(口径⑦)──
_ENV = {
    "target": ("MEMORIVE_RETRIEVAL_TARGET_RECALL", 15),
    "factor": ("MEMORIVE_RETRIEVAL_OVERFETCH_FACTOR", 4),
    "max_overfetch": ("MEMORIVE_RETRIEVAL_MAX_OVERFETCH", 200),
    "per_card": ("MEMORIVE_RETRIEVAL_PER_CARD_CAP", 3),
}
_CANON_EMBED = "bge-m3"                          # query 必与 chunk 同向量空间(口径⑥)
_FENCE = re.compile(r"(?m)^---[ \t]*\r?$")       # frontmatter fence(best-effort 读 by_field 用)
_MAX_DROPPED_EXAMPLES = 8                        # RUNTIME_LOG 附少量剔除样例(计数为主,不全量)
_OWNERSHIP_VALUES = {"self", "entrusted", "unknown"}
_TRIAL_CARD_SUFFIX = re.compile(r"__v\d+trial$")  # 保留的蒸馏试验旁证卡，不是当前活卡


def _cfg() -> dict:
    """读 env 配置,任一非正/解析失败回默认(口径⑦)。"""
    out = {}
    for key, (env, default) in _ENV.items():
        raw = os.environ.get(env)
        try:
            v = int(raw) if raw is not None and raw.strip() else default
        except (ValueError, AttributeError):
            v = default
        out[key] = v if v > 0 else default
    return out


# ── rerank 配置(A·修跨篇混入):float/bool,不进 int-only `_cfg`;阈值允许 0/负=不砍,不套 v>0 ──
_RERANK_ENV = {
    "enabled": ("MEMORIVE_RETRIEVAL_RERANK_ENABLED", False),     # bool:默认关(A1a 零回归;开=开启 rerank)
    "threshold": ("MEMORIVE_RETRIEVAL_RERANK_THRESHOLD", 0.3),   # float:relevance_score < 阈值 → 砍。默认 0.3 保守:
    # A1b 真跑标定(Yaseen 综述问题)——切题 Yaseen 分∈[0.89,0.996]、离题 Chen 分∈[0.013,0.262],间隔极大;
    # 0.3 砍净离题、对切题留巨大余量(承拍板⑤阈值保守·不饿死反例名额)。设 0/负=只重排不砍;可 env 覆盖。
}
_RANK_MODE_ENV = "MEMORIVE_RETRIEVAL_RANK_MODE"
_RANK_MODES = frozenset({"off", "legacy_score", "multimodal_listwise", "auto"})


def _rerank_cfg() -> dict:
    """读 rerank env(bool enabled / float threshold)。解析失败/NaN/inf 回默认;**阈值不套 `>0` 卡**(0/负合法=不砍)。"""
    raw = os.environ.get(_RERANK_ENV["enabled"][0])
    if raw is not None and raw.strip():
        enabled = raw.strip().lower() in ("1", "true", "yes", "on")
    else:
        enabled = _RERANK_ENV["enabled"][1]
    thr = _RERANK_ENV["threshold"][1]
    raw = os.environ.get(_RERANK_ENV["threshold"][0])
    if raw is not None and raw.strip():
        try:
            v = float(raw)
            if math.isfinite(v):
                thr = v
        except (ValueError, AttributeError):
            pass
    raw_mode = os.environ.get(_RANK_MODE_ENV)
    if raw_mode is None or not raw_mode.strip():
        mode = "legacy_score" if enabled else "off"
    else:
        mode = raw_mode.strip().lower()
        if mode not in _RANK_MODES:
            mode = "off"
    return {"enabled": mode != "off", "mode": mode, "threshold": thr}


# ── query 嵌入(MODEL_GATEWAY,可注入;槽参数化,口径⑥)────────────────────────────
def _default_embed(question: str, embed_task_type: str) -> list:
    """默认 query 嵌入:经 MODEL_GATEWAY `embed(embed_task_type, [question])`,取第一条向量。
    模型须 bge-m3(与 chunk 同向量空间)——否则拒用。query=自有数据→默认 cloud 槽;留 embed_local seam。"""
    from model_gateway import embed as model_gateway_embed     # 局部 import:测试注入 embed_fn 时不触发 MODEL_GATEWAY
    res = model_gateway_embed(embed_task_type, [question])
    if res.get("embedding_model") != _CANON_EMBED:
        raise ValueError(f"CandidateRetrieval: query 嵌入模型 {res.get('embedding_model')!r} ≠ {_CANON_EMBED};"
                         f"须与 chunk 向量空间一致,拒用。")
    embs = res.get("embeddings") or []
    if len(embs) != 1:
        raise ValueError(f"CandidateRetrieval: query 嵌入返回 {len(embs)} 条(期望 1)。")
    return list(embs[0])


# ── rerank 打分(MODEL_GATEWAY,可注入;A·修跨篇混入)────────────────────────────────
def _default_rerank(question: str, texts: list, rerank_task_type: str = "rerank") -> list:
    """默认 rerank:经 MODEL_GATEWAY `rerank(rerank_task_type, question, texts)`,回**每条 relevance_score**(按 texts 序对齐)。
    契约:MODEL_GATEWAY 回 `{"results": [{"index", "relevance_score"}, …]}`(adapter 保证按 index 排序对齐 texts)。
    query=自有数据→默认 cloud siliconflow 槽(国内 domestic_skip);entrusted 本地 rerank 路留 seam(A1a 未接,后续)。"""
    from model_gateway import rerank as model_gateway_rerank      # 局部 import:测试注入 rerank_fn 时不触发 MODEL_GATEWAY
    res = model_gateway_rerank(rerank_task_type, question, list(texts))
    results = res.get("results") or []
    if len(results) != len(texts):
        raise ValueError(f"CandidateRetrieval rerank: 返回 {len(results)} 条 ≠ 文档 {len(texts)} 条。")
    return [r.get("relevance_score") for r in results]


def _default_multimodal_rank(
    question: str,
    documents: list[dict],
    query_intent: str,
) -> dict:
    """Invoke the disabled-by-default local MODEL_GATEWAY listwise adapter.

    The exact local model digest must be configured; absence is a controlled
    failure that the caller converts to the frozen distance fallback.
    """

    from model_gateway.local_multimodal_ranker import LocalMultimodalRankerAdapter

    digest = os.environ.get("MEMORIVE_RETRIEVAL_LOCAL_MULTIMODAL_MODEL_DIGEST", "").strip()
    if not digest:
        raise ValueError("LOCAL_MULTIMODAL_MODEL_DIGEST_UNBOUND")
    binding = {
        "schema_version": "RetrievalCalibrationLocalMultimodalRankerBinding-v1",
        "endpoint": os.environ.get(
            "MEMORIVE_RETRIEVAL_LOCAL_MULTIMODAL_ENDPOINT", "http://127.0.0.1:11434/api/chat"
        ),
        "requested_model": os.environ.get(
            "MEMORIVE_RETRIEVAL_LOCAL_MULTIMODAL_MODEL", "qwen3.8:27b-q4_K_M"
        ),
        "expected_model_digest_prefix": digest,
        "think": False,
        "temperature": 0,
        "seed": 20260820,
        "num_predict": 2048,
        "max_candidates_per_query": 64,
        "cloud_fallback": False,
        "dedicated_reranker_fallback": False,
    }
    outcome = LocalMultimodalRankerAdapter().execute(
        [
            {
                "query_id": "RETRIEVAL-LIVE-QUERY",
                "query": question,
                "query_intent": query_intent,
                "documents": documents,
            }
        ],
        binding,
    )
    if not outcome.success or len(outcome.rankings) != 1:
        raise ValueError(outcome.error_code or "LOCAL_MULTIMODAL_RANK_FAILED")
    return {
        "candidate_ids": list(outcome.rankings[0]["candidate_ids"]),
        "receipt": outcome.receipt,
    }


# ── 开现成 Chroma:get_collection、不建库(口径⑧)──────────────────────
def _open_collection(target: str):
    """开现成 collection(get_collection、**非** get_or_create);查不到即报错、绝不建库。"""
    import chromadb
    from document_processing.config import COLLECTION, chroma_dir
    client = chromadb.PersistentClient(path=str(chroma_dir(target)))
    return client.get_collection(COLLECTION[target])


# ── paper_id → 卡片路径(D2;含字面 [ ],遍历前缀匹配、不用 glob 元字符;0/多匹配 → None)口径③ ──
def _resolve_paper_folder(paper_id: str, lib_root: Path) -> Path | None:
    if not lib_root.is_dir():
        return None
    fp = f"[{paper_id}]"
    folders = [d for d in lib_root.iterdir()
               if d.is_dir() and (d.name.startswith(fp + " ") or d.name == fp)]
    return folders[0] if len(folders) == 1 else None


def _resolve_card(paper_id: str, lib_root: Path) -> Path | None:
    """文献库/[<paper_id>] …/[Card][<paper_id>] ….md;0 或多匹配 → None(调用方 fail-closed),**不 crash、不猜**。"""
    folder = _resolve_paper_folder(paper_id, lib_root)
    if folder is None:
        return None
    cp = f"[Card][{paper_id}]"
    cards = [f for f in folder.iterdir()
             if f.is_file() and f.suffix == ".md" and f.name.startswith(cp)
             and not _TRIAL_CARD_SUFFIX.search(f.stem)]
    return cards[0] if len(cards) == 1 else None


def resolve_chunk_metadata(paper_id: str, chunk_id: str, lib_root: Path,
                           cache: dict) -> dict:
    """Best-effort fallback for legacy vectors; missing data stays explicit unknown."""
    if paper_id not in cache:
        records: dict[str, dict] = {}
        folder = _resolve_paper_folder(paper_id, Path(lib_root))
        if folder is not None:
            prefix = f"[Chunks][{paper_id}]"
            files = [
                path for path in folder.iterdir()
                if path.is_file() and path.suffix.casefold() == ".jsonl"
                and path.name.startswith(prefix)
            ]
            if len(files) == 1:
                try:
                    for line in files[0].read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        item = json.loads(line)
                        cid = item.get("chunk_id") if isinstance(item, dict) else None
                        if not isinstance(cid, str):
                            continue
                        metadata = {
                            key: item.get(key)
                            for key in ("section_path", "block_type", "page_start", "page_end",
                                        "page_family", "page_family_primary",
                                        "page_family_modifiers", "modifiers", "source_role")
                            if item.get(key) is not None
                        }
                        metadata["metadata_status"] = "chunk_sidecar"
                        records[cid] = metadata
                except (OSError, UnicodeError, json.JSONDecodeError):
                    records = {}
        cache[paper_id] = records
    return dict(cache[paper_id].get(chunk_id) or {"metadata_status": "unknown"})


def _live_status(paper_id: str, lib_root: Path, cache: dict) -> str:
    """回读活卡 review_status(拍板②)。返回 active/pending/quarantined,或 **card_missing**(解析不出)/
    **card_state_error**(坏卡/缺字段:read_status 抛)——两者均 fail-closed 当非 active(口径③,区分卡找不到 vs 坏卡)。
    同 paper_id 只读一次(cache)。"""
    if paper_id in cache:
        return cache[paper_id]
    card = _resolve_card(paper_id, lib_root)
    if card is None:
        st = "card_missing"
    else:
        try:
            st = read_status(card)                          # active/pending/quarantined
        except (CardStateError, OSError):
            st = "card_state_error"
    cache[paper_id] = st
    return st


def _read_frontmatter(card_path: Path) -> dict | None:
    """best-effort 读 frontmatter(首两独占行 --- 之间 → yaml)。失败返 None、绝不抛。"""
    try:
        text = card_path.read_bytes().decode("utf-8")
        fences = list(_FENCE.finditer(text))
        if len(fences) < 2:
            return None
        fm = yaml.safe_load(text[fences[0].end():fences[1].start()])
        return fm if isinstance(fm, dict) else None
    except Exception:
        return None


def _field_for_chunk(paper_id: str, chunk_id: str, lib_root: Path, fm_cache: dict) -> str | None:
    """best-effort:读卡片 source_anchor.by_field 返回含该 chunk_id 的字段名;任何异常 → None(不阻断,口径⑤)。
    **真兼容(口径④)**:anchors 是 dict → v2 读 `chunk_ids`;是 list → v3 读元素 `chunk_id`(或 v1 裸串);其它 → 跳过。"""
    try:
        if paper_id not in fm_cache:
            card = _resolve_card(paper_id, lib_root)
            fm_cache[paper_id] = _read_frontmatter(card) if card else None
        by_field = (((fm_cache[paper_id] or {}).get("source_anchor") or {}).get("by_field")) or {}
        if not isinstance(by_field, dict):
            return None
        for fname, anchors in by_field.items():
            if isinstance(anchors, dict):                   # v2: {section_page, chunk_ids:[…]}
                if chunk_id in (anchors.get("chunk_ids") or []):
                    return fname
            elif isinstance(anchors, list):                 # v3: [{chunk_id,…}] / v1: [str]
                for a in anchors:
                    cid = a.get("chunk_id") if isinstance(a, dict) else a
                    if cid == chunk_id:
                        return fname
    except Exception:
        return None
    return None


def _credibility_for_field(paper_id: str, field, fm_cache: dict):
    """v6.4 之四·上半:从卡片 `field_credibility` 回读该字段 EVIDENCE_REVIEW 信用档(**先只透传**、不改检索权重)。
    field 为 None(综合字段/查不到)或旧 v1–v4 卡缺 field_credibility → None、**不阻断**。复用 _field_for_chunk 已填的 fm_cache。"""
    if field is None:
        return None
    try:
        fc = ((fm_cache.get(paper_id) or {}).get("field_credibility")) or {}
        entry = fc.get(field)
        if isinstance(entry, dict):
            return entry.get("credibility")
    except Exception:
        return None
    return None


def _data_ownership_for_paper(paper_id: str, lib_root: Path, fm_cache: dict) -> str | None:
    """回读活卡归属。缺失/非法/读卡失败一律 None，供 rerank fail-closed 跳过云端。

    归属是可否把候选原文送云的唯一依据，和 active-only 一样必须以卡片 live frontmatter 为准，
    不信向量 metadata 的冻结副本。
    """
    try:
        if paper_id not in fm_cache:
            card = _resolve_card(paper_id, lib_root)
            fm_cache[paper_id] = _read_frontmatter(card) if card else None
        ownership = (fm_cache.get(paper_id) or {}).get("data_ownership")
        if isinstance(ownership, str):
            ownership = ownership.strip()
            if ownership in _OWNERSHIP_VALUES:
                return ownership
    except Exception:
        pass
    return None


def _ownership_for_candidate(paper_id: str, chunk_id: str, hit: dict,
                             lib_root: Path, fm_cache: dict):
    """Bind the live Card authority to the vector projection without voting.

    Existing Cards predate typed assertions.  A unique live Card is therefore
    adapted explicitly with its safe locator hash and revision 1; vector
    metadata remains compare-only.  Missing/invalid Card authority or any
    vector mismatch yields a blocked snapshot and can never become ``self``.
    """
    card = _resolve_card(paper_id, lib_root)
    if paper_id not in fm_cache:
        fm_cache[paper_id] = _read_frontmatter(card) if card else None
    frontmatter = fm_cache.get(paper_id) or {}
    authoritative_value = frontmatter.get("data_ownership")
    revision = frontmatter.get("ownership_revision", 1)
    assertion_ref = frontmatter.get("ownership_assertion_ref")
    basis_ref = frontmatter.get("ownership_basis_ref")
    if card is not None:
        try:
            card_hash = hashlib.sha256(card.read_bytes()).hexdigest()
            assertion_ref = assertion_ref or f"OS-CARD-{card_hash[:24]}"
            basis_ref = basis_ref or f"card-sha256:{card_hash}"
        except OSError:
            pass
    subject_ref = f"chunk:{paper_id}:{chunk_id}"
    return bind_authority_projection(
        subject_ref=subject_ref,
        authoritative_value=authoritative_value,
        assertion_ref=assertion_ref,
        revision=revision,
        basis_ref=basis_ref,
        projection_value=hit.get("data_ownership"),
        projection_revision=hit.get("ownership_revision"),
        projection_ref=f"vector:{paper_id}:{chunk_id}",
    )


# ── require_verified:§二.3 fail-closed(默认关,口径④真 ts fail-closed)────────────────
def _judge_verified(recs: list, evidence_review_value: str) -> bool:
    """该 paper_id 的 to=='active' 记录集:任一 ts 缺失 / fromisoformat 解析失败 / 最新时间不唯一 → False(fail-closed);
    唯一最新且 trigger==EvidenceReview校核结果 → True。**不静默丢缺 ts、不用字符串 max。**"""
    if not recs:
        return False                                        # 查不到 → fail-closed
    parsed = []
    for r in recs:
        ts = r.get("ts")
        if not isinstance(ts, str) or not ts.strip():
            return False                                    # 任一缺 ts → fail-closed
        try:
            dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return False                                    # 解析失败 → fail-closed
        parsed.append((dt, r))
    latest = max(dt for dt, _ in parsed)
    newest = [r for dt, r in parsed if dt == latest]
    if len(newest) != 1:                                    # 最新时间不唯一 → fail-closed
        return False
    return newest[0].get("trigger") == evidence_review_value


def _verified_by_evidence_review(paper_id: str, cache: dict) -> bool:
    """扫描现存 KNOWLEDGE_ADMISSION_log_YYYY.jsonl,取 data_id==paper_id 且 to=='active' 记录,交 _judge_verified 判(真 ts fail-closed)。"""
    if paper_id in cache:
        return cache[paper_id]
    from knowledge_admission.machine import Trigger
    log_dir = Path(os.environ.get(knowledge_admission_log.ENV_KNOWLEDGE_ADMISSION_LOG_DIR, knowledge_admission_log.DEFAULT_KNOWLEDGE_ADMISSION_LOG_DIR))
    recs = []
    if log_dir.is_dir():
        for p in log_dir.glob("KNOWLEDGE_ADMISSION_log_*.jsonl"):
            try:
                year = int(p.stem.split("_")[-1])
            except (ValueError, IndexError):
                continue
            recs += [r for r in knowledge_admission_log.read_records(year)
                     if r.get("data_id") == paper_id and r.get("to") == "active"]
    ok = _judge_verified(recs, Trigger.EVIDENCE_REVIEW_VERDICT.value)
    cache[paper_id] = ok
    return ok


# ── Chroma 查询 → 内部命中列表 ───────────────────────────────────────────
def _query(col, qvec, n: int, *, where: dict | None = None) -> list:
    """Chroma query → [dict(chunk_id,paper_id,text,distance)]；where 非空时在向量层硬限来源。"""
    if n <= 0:
        return []
    kwargs = {
        "query_embeddings": [qvec],
        "n_results": n,
        "include": ["documents", "metadatas", "distances"],
    }
    if where is not None:
        kwargs["where"] = where
    res = col.query(**kwargs)
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    hits = []
    for i, cid in enumerate(ids):
        m = metas[i] if i < len(metas) and metas[i] else {}
        structure = {
            key: m.get(key)
            for key in (
                "section_path", "block_type", "page_start", "page_end",
                "page_family", "page_family_primary", "page_family_modifiers",
                "modifiers", "source_role",
            )
            if m.get(key) is not None
        }
        hits.append({
            "chunk_id": cid,
            "paper_id": m.get("paper_id") or "",
            "text": docs[i] if i < len(docs) else "",
            "distance": float(dists[i]) if i < len(dists) else float("inf"),
            "data_ownership": m.get("data_ownership"),
            "ownership_revision": m.get("ownership_revision"),
            **structure,
            "metadata_status": "vector" if structure else "unknown",
        })
    return hits


def _enrich_rank_metadata(hit: dict, lib_root: Path, cache: dict) -> None:
    """Bind vector and sidecar metadata before any model ranking call."""

    keys = (
        "section_path",
        "block_type",
        "page_start",
        "page_end",
        "page_family",
        "page_family_primary",
        "page_family_modifiers",
        "modifiers",
        "source_role",
    )
    fallback = resolve_chunk_metadata(hit["paper_id"], hit["chunk_id"], lib_root, cache)
    for key in keys:
        if hit.get(key) is None and fallback.get(key) is not None:
            hit[key] = fallback[key]
    if fallback.get("metadata_status") == "chunk_sidecar":
        hit["metadata_status"] = "chunk_sidecar"


def _rank_provenance(hit: dict) -> dict:
    family = hit.get("page_family", hit.get("page_family_primary")) or "unknown"
    modifiers = hit.get("page_family_modifiers", hit.get("modifiers")) or []
    if not isinstance(modifiers, (list, tuple)):
        modifiers = []
    modifiers = sorted({value.strip() for value in modifiers if isinstance(value, str) and value.strip()})
    source_role = hit.get("source_role") or "unknown"
    provenance = {
        "schema_version": "CoreRetrievalRankingDocumentProvenance-v1",
        "page_family": str(family),
        "page_family_modifiers": modifiers,
        "page_span": {"start": hit.get("page_start"), "end": hit.get("page_end")},
        "source_role": str(source_role),
        "metadata_status": str(hit.get("metadata_status") or "unknown"),
    }
    encoded = json.dumps(
        provenance, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    provenance["provenance_sha256"] = hashlib.sha256(encoded).hexdigest().upper()
    return provenance


def _rank_documents(active: list[dict]) -> list[dict]:
    return [
        {
            "candidate_id": hit["chunk_id"],
            "text": hit["text"],
            "provenance": _rank_provenance(hit),
        }
        for hit in active
    ]


def _should_run_multimodal_rank(strategy, active: list[dict]) -> bool:
    if len(active) < 2:
        return False
    complex_families = {
        "degraded_scan", "multicolumn", "role_dense", "formula",
        "table_numeric", "chart_mixed", "unknown",
    }
    families = {
        hit.get("page_family", hit.get("page_family_primary")) or "unknown"
        for hit in active
    }
    roles = {hit.get("source_role") or "unknown" for hit in active}
    qtype = getattr(getattr(strategy, "qtype", None), "value", None)
    return bool(
        families & complex_families
        or roles & {"references", "citation_discussion", "page_metadata"}
        or qtype in {"find_method", "find_counterexample", "review_synthesis"}
    )


def _count_for_scope(col, where: dict | None) -> int:
    """共享模式用 collection.count；指定来源时按同一 metadata where 计数。"""
    if where is None:
        return col.count()
    scoped = col.get(where=where, include=[])
    return len(scoped.get("ids") or [])


def retrieve_candidates(question: str, strategy=None, *, target: str = "sandbox",
                        require_verified: bool = False, embed_task_type: str = "embed_cloud",
                        rerank_task_type: str = "rerank", embed_fn=None, rerank_fn=None,
                        multimodal_rank_fn=None,
                        collection=None, lib_root=None,
                        paper_id: str | None = None) -> CandidateSet:
    """CandidateRetrieval 主入口:问题 → CandidateSet(chunk 单层召回 + active-only 回读活卡 + rerank + 归并回卡片)。

    paper_id=None → 共享 collection；paper_id 非空 → Chroma `where={"paper_id": paper_id}` 硬限指定论文。
    自然语言中的标题/ID 不视为来源约束，调用方必须显式传本参数；scope 同步进入 CandidateSet 与 RUNTIME_LOG。
    embed_fn / rerank_fn / multimodal_rank_fn / collection / lib_root 可注入(验收用)。
    **单次** overfetch 到 min(MAX_OVERFETCH, count)→ active-only → require_verified → **rerank(默认关)** → 每卡限额 → 取 target。
    `MEMORIVE_RETRIEVAL_RANK_MODE=multimodal_listwise|auto` 时走本地完整 ID 顺序；不产生 relevance score、
      不做阈值砍除。调用/结果任一异常退回 distance。旧 `rerank_fn(question,texts)->list[float]`
      仅在 `legacy_score` 兼容模式使用；所有模式默认关闭。
    只召回、不打包;active-only 是 post-filter(回读活卡、非冻结 metadata);固定召回不随库膨胀。
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("CandidateRetrieval: question 必须是非空字符串(空 / 纯空白 fail-loud)")
    if paper_id is not None:
        if not isinstance(paper_id, str) or not paper_id.strip():
            raise ValueError("CandidateRetrieval: paper_id scope 必须是非空字符串或 None")
        paper_id = paper_id.strip()
    scope_where = None if paper_id is None else {"paper_id": paper_id}
    scope_mode = "shared" if paper_id is None else "paper_id"
    cfg = _cfg()
    # Shared retrieval needs a per-paper diversity cap. In an explicit
    # single-paper scope that cap becomes a deterministic 3-block bottleneck,
    # so let the requested target and downstream token budget decide instead.
    effective_per_card = cfg["target"] if paper_id is not None else cfg["per_card"]
    if lib_root is None:
        from document_processing.config import library_root
        lib_root = library_root(target)
    lib_root = Path(lib_root)

    qvec = (embed_fn or (lambda q: _default_embed(q, embed_task_type)))(question)
    col = collection if collection is not None else _open_collection(target)
    count = _count_for_scope(col, scope_where)

    # 单次 overfetch 到上限(口径①:别两段早停;够不够按最终 final 判)。
    hits = _query(col, qvec, min(cfg["max_overfetch"], count), where=scope_where)

    drop = {k: 0 for k in ("pending", "quarantined", "card_missing", "card_state_error",
                           "verified_fail", "per_card_dropped", "field_missing", "rerank_dropped")}
    examples: list = []

    def _ex(chunk_id, reason):
        if len(examples) < _MAX_DROPPED_EXAMPLES:
            examples.append({"chunk_id": chunk_id, "reason": reason})

    # active-only post-filter(回读活卡;分类计数,口径③⑤)。
    st_cache, fm_cache, ver_cache, chunk_metadata_cache = {}, {}, {}, {}
    active = []
    for h in hits:
        st = _live_status(h["paper_id"], lib_root, st_cache)
        if st == "active":
            active.append(h)
        else:
            drop[st] += 1                                   # pending/quarantined/card_missing/card_state_error
            _ex(h["chunk_id"], st)

    # require_verified(默认关):仅放行触发源=EvidenceReview校核结果者(口径④真 ts fail-closed)。
    if require_verified:
        kept = []
        for h in active:
            if _verified_by_evidence_review(h["paper_id"], ver_cache):
                kept.append(h)
            else:
                drop["verified_fail"] += 1
                _ex(h["chunk_id"], "verified_fail")
        active = kept

    # logical rerank 默认关。legacy_score 保留旧分数/阈值兼容；multimodal_listwise 只接受完整 ID 顺序。
    rcfg = _rerank_cfg()
    # 关闭时保持 Core 精确旧路径：不为未执行的模型排序提前读 sidecar。
    if rcfg["enabled"]:
        for hit in active:
            _enrich_rank_metadata(hit, lib_root, chunk_metadata_cache)
    rr_dropped_examples, rr_dropped_ce = [], 0
    rerank_state = {"enabled": rcfg["enabled"], "attempted": False, "skipped": False,
                    "mode": rcfg["mode"], "output_kind": None,
                    "dedicated_reranker_required": False,
                    "execution_receipt": None,
                    "skip_reason": None, "fallback_reason": None, "ownership": {}}
    if rcfg["enabled"] and active:
        ownerships = [_data_ownership_for_paper(h["paper_id"], lib_root, fm_cache) for h in active]
        for ownership in ownerships:
            label = ownership or "unknown"
            rerank_state["ownership"][label] = rerank_state["ownership"].get(label, 0) + 1

        selected_mode = rcfg["mode"]
        if selected_mode == "auto" and not _should_run_multimodal_rank(strategy, active):
            rerank_state["skipped"] = True
            rerank_state["skip_reason"] = "AUTO_SIMPLE_ROUTE"
            active.sort(key=lambda h: h["distance"])
        elif selected_mode == "legacy_score":
            rerank_state["dedicated_reranker_required"] = True
            if any(ownership != "self" for ownership in ownerships):
                rerank_state["skipped"] = True
                rerank_state["skip_reason"] = (
                    "entrusted_present" if "entrusted" in ownerships else "ownership_unknown"
                )
                active.sort(key=lambda h: h["distance"])
            else:
                from .context_pack import _is_counterexample
                rf = rerank_fn or (lambda q, txts: _default_rerank(q, txts, rerank_task_type))
                scores = None
                try:
                    raw_scores = rf(question, [h["text"] for h in active])
                    if (isinstance(raw_scores, (list, tuple)) and len(raw_scores) == len(active)
                            and all(isinstance(score, (int, float)) and not isinstance(score, bool)
                                    and math.isfinite(score) for score in raw_scores)):
                        scores = [float(score) for score in raw_scores]
                    else:
                        rerank_state["fallback_reason"] = "invalid_scores"
                except Exception as exc:
                    rerank_state["fallback_reason"] = f"exception:{type(exc).__name__}"
                rerank_state["attempted"] = True
                rerank_state["output_kind"] = "PER_CANDIDATE_RELEVANCE_SCORE"
                if scores is not None:
                    for h, score in zip(active, scores):
                        h["rerank_score"] = score
                    thr = rcfg["threshold"]
                    kept = []
                    for h in active:
                        rs = h.get("rerank_score")
                        if rs < thr:
                            drop["rerank_dropped"] += 1
                            if _is_counterexample(h["text"]):
                                rr_dropped_ce += 1
                            if len(rr_dropped_examples) < _MAX_DROPPED_EXAMPLES:
                                rr_dropped_examples.append({"chunk_id": h["chunk_id"], "rerank_score": rs})
                        else:
                            kept.append(h)
                    active = sorted(kept, key=lambda h: h["rerank_score"], reverse=True)
                else:
                    active.sort(key=lambda h: h["distance"])
        else:
            # This route is local-only, so entrusted content remains inside the
            # loopback boundary. No dedicated reranker or cloud fallback exists.
            ranker = multimodal_rank_fn or _default_multimodal_rank
            documents = _rank_documents(active)
            query_intent = getattr(getattr(strategy, "qtype", None), "value", None) or "unknown"
            ranked_ids = None
            try:
                rank_result = ranker(question, documents, query_intent)
                if isinstance(rank_result, dict):
                    raw_ids = rank_result.get("candidate_ids")
                    receipt = rank_result.get("receipt")
                    if not isinstance(receipt, dict):
                        raise ValueError("LOCAL_RANK_RECEIPT_MISSING")
                    if (
                        receipt.get("status") != "PASS"
                        or receipt.get("external_request_count") != 0
                        or receipt.get("cloud_fallback_count") != 0
                        or receipt.get("dedicated_reranker_fallback_count") != 0
                        or receipt.get("requested_model") != receipt.get("returned_model")
                    ):
                        raise ValueError("LOCAL_RANK_RECEIPT_INVALID")
                    rerank_state["execution_receipt"] = {
                        key: receipt.get(key)
                        for key in (
                            "schema_version", "receipt_sha256", "requested_model",
                            "returned_model", "model_digest", "latency_ms",
                            "local_metadata_request_count", "local_model_request_count",
                            "external_request_count", "cloud_fallback_count",
                            "dedicated_reranker_fallback_count", "production_mutation_count",
                        )
                    }
                else:
                    raw_ids = rank_result
                expected_ids = [hit["chunk_id"] for hit in active]
                if (
                    isinstance(raw_ids, (list, tuple))
                    and len(raw_ids) == len(expected_ids)
                    and len(set(raw_ids)) == len(raw_ids)
                    and set(raw_ids) == set(expected_ids)
                ):
                    ranked_ids = list(raw_ids)
                else:
                    rerank_state["fallback_reason"] = "invalid_candidate_exact_set"
            except Exception as exc:
                rerank_state["fallback_reason"] = f"exception:{type(exc).__name__}"
            rerank_state["attempted"] = True
            rerank_state["output_kind"] = "GENERATIVE_LISTWISE_EXACT_SET"
            if ranked_ids is not None:
                by_id = {hit["chunk_id"]: hit for hit in active}
                active = [by_id[candidate_id] for candidate_id in ranked_ids]
                for rank, hit in enumerate(active, 1):
                    hit["ranking_rank"] = rank
            else:
                active.sort(key=lambda h: h["distance"])
    else:
        active.sort(key=lambda h: h["distance"])            # 口径②:distance 升序(越小越近);rerank 关时的现状路径

    # 每卡限额 + 取满 target(贪心按序；listwise 用序位、legacy 用分数、关闭时用距离)。
    per_card, final = {}, []
    for h in active:
        if len(final) >= cfg["target"]:
            break
        if per_card.get(h["paper_id"], 0) >= effective_per_card:
            drop["per_card_dropped"] += 1
            _ex(h["chunk_id"], "per_card_cap")
            continue
        per_card[h["paper_id"]] = per_card.get(h["paper_id"], 0) + 1
        field = _field_for_chunk(h["paper_id"], h["chunk_id"], lib_root, fm_cache)
        if field is None:
            drop["field_missing"] += 1
        cred = _credibility_for_field(h["paper_id"], field, fm_cache)   # v6.4 之四:透传该字段 EVIDENCE_REVIEW 信用档
        structure = {
            key: h.get(key)
            for key in ("section_path", "block_type", "page_start", "page_end", "source_role")
            if h.get(key) is not None
        }
        if len(structure) < 4:
            fallback = resolve_chunk_metadata(
                h["paper_id"], h["chunk_id"], lib_root, chunk_metadata_cache
            )
            for key in ("section_path", "block_type", "page_start", "page_end", "source_role"):
                if structure.get(key) is None and fallback.get(key) is not None:
                    structure[key] = fallback[key]
            metadata_status = (
                "chunk_sidecar"
                if fallback.get("metadata_status") == "chunk_sidecar"
                else h.get("metadata_status", "unknown")
            )
        else:
            metadata_status = h.get("metadata_status", "vector")
        ownership = _ownership_for_candidate(
            h["paper_id"], h["chunk_id"], h, lib_root, fm_cache
        )
        authority = ownership.ownership_contributors[0] if ownership.ownership_contributors else None
        final.append(Candidate(
            chunk_id=h["chunk_id"], paper_id=h["paper_id"], text=h["text"],
            distance=h["distance"], admission="active", field=field,
            verified_by_evidence_review=(True if require_verified else None), credibility=cred,
            rerank_score=h.get("rerank_score"),
            ranking_rank=h.get("ranking_rank"),
            section_path=structure.get("section_path"),
            block_type=structure.get("block_type"),
            page_start=structure.get("page_start"),
            page_end=structure.get("page_end"),
            source_role=structure.get("source_role"),
            metadata_status=metadata_status,
            ownership_assertion_ref=(authority.assertion_ref if authority else None),
            ownership_revision=(authority.revision if authority else None),
            ownership_basis_ref=(authority.basis_ref if authority else None),
            ownership_projection_value=h.get("data_ownership"),
            ownership_projection_revision=h.get("ownership_revision"),
            **snapshot_fields(ownership),
        ))

    non_active = (drop["pending"] + drop["quarantined"] + drop["card_missing"]
                  + drop["card_state_error"] + drop["verified_fail"])
    reached_cap = (len(final) < cfg["target"]) and (count > cfg["max_overfetch"])

    result = CandidateSet(
        candidates=tuple(final), target_active_count=cfg["target"],
        raw_recall_count=len(hits), filtered_non_active_count=non_active,
        final_active_count=len(final), reached_overfetch_cap=reached_cap,
        require_verified=require_verified, scope_mode=scope_mode,
        scope_paper_id=paper_id,
    )
    rerank_note = ""
    if rcfg["enabled"]:
        if rerank_state["skipped"]:
            rerank_note = f" rerank跳过({rerank_state['skip_reason']}·精度降级)"
        elif rerank_state["fallback_reason"]:
            rerank_note = f" rerank回退distance({rerank_state['fallback_reason']})"
        else:
            rerank_note = (
                " listwise已排序"
                if rerank_state["output_kind"] == "GENERATIVE_LISTWISE_EXACT_SET"
                else f" rerank砍{drop['rerank_dropped']}(其中反例{rr_dropped_ce})"
            )
    log_reason("RETRIEVAL", "CandidateRetrieval 候选召回",
               f"raw={len(hits)} 非active剔除={non_active} final={len(final)}/{cfg['target']}"
               f"{' 到overfetch上限仍不足' if reached_cap else ''}"
               f"{' require_verified' if require_verified else ''}"
               f" scope={scope_mode}{':' + paper_id if paper_id else ''}"
               f"{rerank_note}",
               event_category=None, context={
                   "target_active_count": cfg["target"], "raw_recall_count": len(hits),
                   "filtered_non_active_count": non_active, "final_active_count": len(final),
                   "reached_overfetch_cap": reached_cap, "require_verified": require_verified,
                   "drop_breakdown": drop, "dropped_examples": examples,
                   "config": {**cfg, "configured_per_card": cfg["per_card"],
                              "per_card": effective_per_card},
                   "target_collection": target,
                   "scope": {"mode": scope_mode, "paper_id": paper_id,
                             "matched_vector_count": count},
                   "strategy_qtype": getattr(getattr(strategy, "qtype", None), "value", None),
                   # logical rerank 留痕；listwise 序位与 score 严格分型。
                   "rerank": {"enabled": rcfg["enabled"], "threshold": rcfg["threshold"],
                              "mode": rerank_state["mode"],
                              "output_kind": rerank_state["output_kind"],
                              "dedicated_reranker_required": rerank_state["dedicated_reranker_required"],
                              "execution_receipt": rerank_state["execution_receipt"],
                              "attempted": rerank_state["attempted"], "skipped": rerank_state["skipped"],
                              "skip_reason": rerank_state["skip_reason"],
                              "fallback_reason": rerank_state["fallback_reason"],
                              "ownership": rerank_state["ownership"],
                              "ranking_order": [h["chunk_id"] for h in active]
                              if rerank_state["output_kind"] == "GENERATIVE_LISTWISE_EXACT_SET"
                              and rerank_state["fallback_reason"] is None else [],
                              "dropped": drop["rerank_dropped"], "dropped_counterexamples": rr_dropped_ce,
                              "dropped_examples": rr_dropped_examples},
               })
    return result
