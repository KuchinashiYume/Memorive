"""Candidate retrieval: chunks recalled for a question, only from active cards, mapped back to their card fields.

- The library holds vectors per chunk only, so chunks are recalled in one query with overfetch and
  mapped back to cards and fields through paper_id and the card's source_anchor.
- Only active cards count, decided after the query from the card's own front matter
  (card_io.read_status), never from the review status frozen in the vector metadata.
It recalls only; weights, counterexample room and tokens are context assembly's. It opens the
existing Chroma collection and never creates one.

Distance is not similarity: Chroma's query returns distances, smaller is closer, sorted ascending.
A fixed number is recalled: one query (n_results=min(MAX_OVERFETCH, count)), then active only,
then require_verified, then the per-card limit, then up to target. Whether there are enough is
judged on the final list, not on the raw active hits.
Reranking can be plugged in after the active filter: the older score reranker (legacy_score) is
kept for compatibility; multimodal_listwise asks a local multimodal model for the full order of
candidate ids, with no made-up 0-1 scores and no threshold. Both are off by default. The local
listwise ranking may see entrusted data; any failure falls back to distance order for the whole
batch, never to a cloud model.
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

from memorive.documents.front_matter import split_front_matter
from memorive.platform.logs import log_reason
from memorive.documents.card_states import knowledge_admission_log
from memorive.documents.card_states.card_io import read_status
from memorive.documents.card_states.errors import CardStateError

from memorive.research.retrieval.ownership import bind_authority_projection, snapshot_fields
from memorive.research.retrieval.types import Candidate, CandidateSet

# ── defaults: each can be set by environment; the values used are logged ──
_ENV = {
    "target": ("MEMORIVE_RETRIEVAL_TARGET_RECALL", 15),
    "factor": ("MEMORIVE_RETRIEVAL_OVERFETCH_FACTOR", 4),
    "max_overfetch": ("MEMORIVE_RETRIEVAL_MAX_OVERFETCH", 200),
    "per_card": ("MEMORIVE_RETRIEVAL_PER_CARD_CAP", 3),
}
_CANON_EMBED = "bge-m3"                          # the question must be in the chunks' vector space
_MAX_DROPPED_EXAMPLES = 8                        # a few dropped examples go into the log; mainly counts
_OWNERSHIP_VALUES = {"self", "entrusted", "unknown"}
_TRIAL_CARD_SUFFIX = re.compile(r"__v\d+trial$")  # a card kept from a distillation trial, not the live card


def _cfg() -> dict:
    """The settings from the environment; a value that is not positive or not readable falls back to the default."""
    out = {}
    for key, (env, default) in _ENV.items():
        raw = os.environ.get(env)
        try:
            v = int(raw) if raw is not None and raw.strip() else default
        except (ValueError, AttributeError):
            v = default
        out[key] = v if v > 0 else default
    return out


# ── rerank settings: a bool and a float, apart from the integer _cfg; a threshold of 0 or less cuts nothing ──
_RERANK_ENV = {
    "enabled": ("MEMORIVE_RETRIEVAL_RERANK_ENABLED", False),     # bool, off by default
    "threshold": ("MEMORIVE_RETRIEVAL_RERANK_THRESHOLD", 0.3),   # float: a relevance_score below it is cut. 0.3 is cautious:
    # measured on a review question, on-topic chunks scored 0.89 to 0.996 and off-topic ones 0.013 to
    # 0.262, so 0.3 cuts the off-topic ones and keeps a wide margin, without starving the room for
    # counterexamples. 0 or less only reorders.
}
_RANK_MODE_ENV = "MEMORIVE_RETRIEVAL_RANK_MODE"
_RANK_MODES = frozenset({"off", "legacy_score", "multimodal_listwise", "auto"})


def _rerank_cfg() -> dict:
    """The rerank settings (bool enabled, float threshold); unreadable, NaN or infinite values fall back
    to the default. The threshold may be 0 or negative, meaning nothing is cut."""
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


# ── the question's vector (through the gateway; injectable; the slot is a parameter) ──
def _default_embed(question: str, embed_task_type: str) -> list:
    """The question's vector through the gateway, embed(embed_task_type, [question]). The model must be
    bge-m3, the chunks' vector space, or the vector is refused. The question is the user's own data,
    so the cloud slot is the default."""
    from memorive.models.gateway import embed as model_gateway_embed     # imported here, so an injected embed_fn never loads the gateway
    res = model_gateway_embed(embed_task_type, [question])
    if res.get("embedding_model") != _CANON_EMBED:
        raise ValueError(f"CandidateRetrieval: query 嵌入模型 {res.get('embedding_model')!r} ≠ {_CANON_EMBED};"
                         f"须与 chunk 向量空间一致,拒用。")
    embs = res.get("embeddings") or []
    if len(embs) != 1:
        raise ValueError(f"CandidateRetrieval: query 嵌入返回 {len(embs)} 条(期望 1)。")
    return list(embs[0])


# ── rerank scores (through the gateway; injectable) ──
def _default_rerank(question: str, texts: list, rerank_task_type: str = "rerank") -> list:
    """Reranking through the gateway, rerank(rerank_task_type, question, texts): one relevance_score per
    text, in the order of texts (the gateway returns {"results": [{"index", "relevance_score"}, ...]}
    sorted by index). The default is the cloud SiliconFlow slot (domestic, so the region check skips
    it); a local rerank for entrusted data is not connected."""
    from memorive.models.gateway import rerank as model_gateway_rerank      # imported here, so an injected rerank_fn never loads the gateway
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

    from memorive.models.gateway.local_multimodal_ranker import LocalMultimodalRankerAdapter

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


# ── the existing Chroma collection; never created here ──
def _open_collection(target: str):
    """Open the existing collection (get_collection, not get_or_create); a missing one raises."""
    import chromadb
    from memorive.documents.processing.config import COLLECTION, chroma_dir
    client = chromadb.PersistentClient(path=str(chroma_dir(target)))
    return client.get_collection(COLLECTION[target])


# ── paper_id -> card file: names contain literal [ ], so prefixes are compared instead of glob;
#    no match or several give None ──
def _resolve_paper_folder(paper_id: str, lib_root: Path) -> Path | None:
    if not lib_root.is_dir():
        return None
    fp = f"[{paper_id}]"
    folders = [d for d in lib_root.iterdir()
               if d.is_dir() and (d.name.startswith(fp + " ") or d.name == fp)]
    return folders[0] if len(folders) == 1 else None


def _resolve_card(paper_id: str, lib_root: Path) -> Path | None:
    """The card file, library/[<paper_id>] .../[Card][<paper_id>] ....md; None when there is no match or
    more than one (the caller treats it as not active)."""
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
    """The card's live review_status: active, pending or quarantined, or card_missing (no card file) or
    card_state_error (read_status raised on a broken card); both of those count as not active, kept
    apart in the counts. Each paper_id is read once (cache)."""
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
    """The card's front matter (YAML between the first two lines that are just ---), or None; never raises."""
    try:
        text = card_path.read_bytes().decode("utf-8")
        parts = split_front_matter(text)
        if parts is None:
            return None
        fm = yaml.safe_load(parts[1])
        return fm if isinstance(fm, dict) else None
    except Exception:
        return None


def _field_for_chunk(paper_id: str, chunk_id: str, lib_root: Path, fm_cache: dict) -> str | None:
    """The card field whose source_anchor.by_field holds this chunk_id, or None on any problem (the
    chunk is kept). Reads all card versions: a dict of anchors (v2, chunk_ids), a list (v3, each
    item's chunk_id, or v1 plain strings); anything else is skipped."""
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
    """The card review's credibility of the field, from the card's field_credibility; passed on only,
    it changes no weight. None when field is None or an older card (v1 to v4) has no
    field_credibility. Uses the front matter cache _field_for_chunk filled."""
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
    """The card's live data ownership; None when missing, invalid or unreadable, and then reranking
    skips the cloud.

    Ownership alone decides whether a candidate's text may go to the cloud, so like the active
    filter it is read from the card's front matter, never from the copy in the vector metadata.
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


# ── require_verified (off by default): any doubt counts as not verified ──
def _judge_verified(recs: list, evidence_review_value: str) -> bool:
    """Whether the paper's latest to=='active' record was triggered by a card review verdict. Any record
    without a readable ts, or two records sharing the latest time, gives False; times are parsed,
    never compared as strings."""
    if not recs:
        return False                                        # none found: not verified
    parsed = []
    for r in recs:
        ts = r.get("ts")
        if not isinstance(ts, str) or not ts.strip():
            return False                                    # a record without ts: not verified
        try:
            dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return False                                    # an unreadable ts: not verified
        parsed.append((dt, r))
    latest = max(dt for dt, _ in parsed)
    newest = [r for dt, r in parsed if dt == latest]
    if len(newest) != 1:                                    # two latest records: not verified
        return False
    return newest[0].get("trigger") == evidence_review_value


def _verified_by_evidence_review(paper_id: str, cache: dict) -> bool:
    """Read the card state logs (KNOWLEDGE_ADMISSION_log_YYYY.jsonl) for the paper's to=='active'
    records and let _judge_verified decide."""
    if paper_id in cache:
        return cache[paper_id]
    from memorive.documents.card_states.machine import Trigger
    log_dir = knowledge_admission_log.log_dir()
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


# ── Chroma query -> hits ──────────────────────────────────────────────
def _query(col, qvec, n: int, *, where: dict | None = None) -> list:
    """A Chroma query as [dict(chunk_id, paper_id, text, distance)]; a non-empty where limits the source
    inside the vector store."""
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
    """collection.count in shared mode; with a source, the count under the same metadata where."""
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
    """Question -> CandidateSet: recall chunks, keep those of active cards, rerank, map back to cards.

    paper_id=None searches the shared collection; a paper_id limits it in Chroma with
    where={"paper_id": paper_id}. A title or id in the question text is not taken as a limit; callers
    pass paper_id. The scope goes into the CandidateSet and the log.
    embed_fn, rerank_fn, multimodal_rank_fn, collection and lib_root can be injected (tests).
    One overfetch query up to min(MAX_OVERFETCH, count), then active only, then require_verified,
    then reranking (off by default), then the per-card limit, then up to target.
    MEMORIVE_RETRIEVAL_RANK_MODE=multimodal_listwise or auto uses a local model's full order of ids:
      no relevance score, no threshold; any failure falls back to distance order. The older
      rerank_fn(question, texts) -> list[float] is used only in legacy_score mode. All modes are off
      by default. The number recalled is fixed and does not grow with the library.
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
        from memorive.documents.processing.config import library_root
        lib_root = library_root(target)
    lib_root = Path(lib_root)

    qvec = (embed_fn or (lambda q: _default_embed(q, embed_task_type)))(question)
    col = collection if collection is not None else _open_collection(target)
    count = _count_for_scope(col, scope_where)

    # One overfetch query up to the cap; whether there are enough is judged on the final list.
    hits = _query(col, qvec, min(cfg["max_overfetch"], count), where=scope_where)

    drop = {k: 0 for k in ("pending", "quarantined", "card_missing", "card_state_error",
                           "verified_fail", "per_card_dropped", "field_missing", "rerank_dropped")}
    examples: list = []

    def _ex(chunk_id, reason):
        if len(examples) < _MAX_DROPPED_EXAMPLES:
            examples.append({"chunk_id": chunk_id, "reason": reason})

    # Keep chunks of active cards (the card's live status), counting the rest by reason.
    st_cache, fm_cache, ver_cache, chunk_metadata_cache = {}, {}, {}, {}
    active = []
    for h in hits:
        st = _live_status(h["paper_id"], lib_root, st_cache)
        if st == "active":
            active.append(h)
        else:
            drop[st] += 1                                   # pending/quarantined/card_missing/card_state_error
            _ex(h["chunk_id"], st)

    # require_verified (off by default): only cards admitted on a card review verdict.
    if require_verified:
        kept = []
        for h in active:
            if _verified_by_evidence_review(h["paper_id"], ver_cache):
                kept.append(h)
            else:
                drop["verified_fail"] += 1
                _ex(h["chunk_id"], "verified_fail")
        active = kept

    # Reranking is off by default. legacy_score keeps the old scores and threshold; multimodal_listwise
    # accepts only a full order of ids.
    rcfg = _rerank_cfg()
    # When off, nothing changes: no sidecar is read for a ranking that will not run.
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
                from memorive.research.retrieval.context_pack import _is_counterexample
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
        active.sort(key=lambda h: h["distance"])            # distance ascending, smaller is closer; the path without reranking

    # The per-card limit, then up to target, in order (the listwise place, the legacy score, or
    # the distance when reranking is off).
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
        cred = _credibility_for_field(h["paper_id"], field, fm_cache)   # the card review's credibility of the field, passed on
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
                   # the reranking record; a listwise place and a score are kept apart
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
