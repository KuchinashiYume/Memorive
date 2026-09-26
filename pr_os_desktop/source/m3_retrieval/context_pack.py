"""M3c Context Pack Builder —— 简化权重 + 强制反例名额 + token 硬上限打包(T7 第 3 步)。

承契约 §一/§三/§四/§六。输入 = M3b `CandidateSet` + M3a `strategy`(可选),**不重新召回**。

两条命门本步兑现(**= 完成判据,不打折**):
- **强制反例名额(§四)**:pool 内**强制预留** `counterexample_target` 个「疑似反例」名额 + 截断中受保护 +
  填不满如实 `counterexample_filled < target`(M11)。名额机制本步做全;仅「让反例真进 pool」
  (M3b 检索多样化)是未来增强/债——**不是**把名额留 Phase 3。
- **token 硬上限(§三·生死线)**:三级优先级 **硬顶(绝对)> 反例保护 > 主流**;**只整块取舍、不切块内文本**;
  单块 > 硬顶 → 整块不入(记 single_block_over_limit);`estimated_tokens ≤ token_hard_limit` **永远成立**、允许空 pack。

守边界:不重新召回;不造五维/DerivedPenalty/doc_type(留 `weight_fn` seam);admission 只标 active;
  子对话精炼不做(登债);**reranker 在 M3b 落**(A·修跨篇混入),M3c 侧仅承其分:`_default_weight` 优先用
  `rerank_score`(承拍板②)、ContextBlock 透传该分;ContextPack **绝不混 M4/M5 字段**;M3b 召回计数原样透传。
反例判定是**粗筛**(强信号)、恒标「疑似反例」交 M4,不裁判真实张力(承 §四.6)。
"""
from __future__ import annotations

import math
import os

from m11_log import log_reason

from .ownership import (
    OWNERSHIP_BINDING_MISMATCH,
    OWNERSHIP_LEGACY_ADAPTER_REQUIRED,
    OwnershipContractError,
    aggregate_blocks,
    aggregate_ownership,
    propagation_receipt,
    snapshot_fields,
    stable_sha256,
    validate_snapshot,
)
from .types import ContextBlock, ContextPack

# ── 默认值:全 env 可配、读取失败回默认;M11 记本次实际用值(口径⑦)──
_ENV = {
    "counterexample_target": ("PROS_M3_COUNTEREXAMPLE_TARGET", 3),
    "token_hard_limit": ("PROS_M3_TOKEN_HARD_LIMIT", 6000),
    "token_ratio": ("PROS_M3_TOKEN_RATIO", 1.0),          # float(保守偏高估=安全)
    "overhead_per_block": ("PROS_M3_TOKEN_OVERHEAD_PER_BLOCK", 32),
}

# 强信号:**单独触发** is_counterexample(疑似反例)。承口径①。
_STRONG_CE = (
    "相反", "反对", "反证", "不支持", "证伪", "无显著", "未观察到", "未发现", "不成立",
    "相矛盾", "驳斥", "证据不足", "并不支持", "无法重现", "未能重现",
    "contrary", "contradict", "refute", "no significant", "failed to", "did not support",
    "does not support", "no evidence", "inconsistent with", "disprove", "rebut",
)
# 弱信号:仅辅助、**不单独触发**(留作口径记录;本步不单独用,承口径①)。
_WEAK_CE = ("但", "然而", "局限", "however", "but", "limitation")


def _cfg() -> dict:
    """读 env 配置,任一非正/解析失败回默认(口径⑦)。"""
    out = {}
    for key, (env, default) in _ENV.items():
        raw = os.environ.get(env)
        try:
            if key == "token_ratio":
                v = float(raw) if raw is not None and raw.strip() else default
            else:
                v = int(raw) if raw is not None and raw.strip() else default
        except (ValueError, AttributeError):
            v = default
        out[key] = v if v > 0 else default
    return out


def _weight(distance, rule_weight: float = 1.0):
    """`1/(1+max(distance,0))×规则`;distance 缺失/NaN/inf → None(调用方置 0.0 最低权重 + M11,不污染排序)。承口径⑧。"""
    if distance is None or not isinstance(distance, (int, float)) or not math.isfinite(distance):
        return None
    return (1.0 / (1.0 + max(float(distance), 0.0))) * rule_weight


def _default_weight(candidate):
    """A(修跨篇混入·承拍板②):rerank **开**(candidate.rerank_score 有效)→ 直接用 relevance_score 作权重
    (越大越相关);rerank **关**/跳过/未跑(rerank_score=None)→ 退回 `1/(1+distance)`。二者皆越大越优,排序一致。"""
    rs = getattr(candidate, "rerank_score", None)
    if rs is not None and isinstance(rs, (int, float)) and math.isfinite(rs):
        return float(rs)
    return _weight(getattr(candidate, "distance", None))


def _estimate_tokens(content, ratio: float, overhead: int) -> int:
    """`ceil(len(content)×ratio)+overhead`(保守上界,overhead 含 source/admission 等序列化开销)。承口径③。"""
    return math.ceil(len(content or "") * ratio) + overhead


def _is_counterexample(text) -> bool:
    """**强信号才触发**疑似反例(承口径①);弱信号不单独触发。中文子串 + 英文小写匹配。"""
    t = (text or "").lower()
    return any(kw in t for kw in _STRONG_CE)


def _ownership_block(row) -> ContextBlock:
    """Build one immutable block from the selected Candidate snapshot.

    A logical-span Candidate already contains every member contributor.  The
    block therefore re-aggregates the complete contributor set under its own
    identity instead of copying a caller hint or the first member only.
    """
    candidate = row["c"]
    source_id = candidate.logical_span_id or candidate.chunk_id
    block_ref = f"block:{source_id}"
    inherited_errors = list(candidate.ownership_error_codes)
    try:
        validate_snapshot(candidate)
    except OwnershipContractError as exc:
        inherited_errors.append(exc.code)
    snapshot = aggregate_ownership(
        candidate.ownership_contributors,
        subject_ref=block_ref,
        inherited_errors=inherited_errors,
        warnings=candidate.ownership_warnings,
        legacy_bucket=candidate.legacy_bucket,
    )
    authority = snapshot.ownership_contributors[0] if len(snapshot.ownership_contributors) == 1 else None
    source = {
        "chunk_id": candidate.chunk_id,
        "paper_id": candidate.paper_id,
        "field": candidate.field,
        "member_chunk_ids": list(candidate.member_chunk_ids or (candidate.chunk_id,)),
    }
    ranking_rank = getattr(candidate, "ranking_rank", None)
    if isinstance(ranking_rank, int) and not isinstance(ranking_rank, bool) and ranking_rank > 0:
        source["ranking_mode"] = "GENERATIVE_LISTWISE_EXACT_SET"
        source["ranking_rank"] = ranking_rank
    return ContextBlock(
        content=candidate.text,
        source=source,
        weight=row["w"],
        is_counterexample=row["ce"],
        tokens=row["tok"],
        admission="active",
        verified_by_m6=candidate.verified_by_m6,
        credibility=candidate.credibility,
        rerank_score=getattr(candidate, "rerank_score", None),
        member_chunk_ids=tuple(candidate.member_chunk_ids or (candidate.chunk_id,)),
        logical_span_id=candidate.logical_span_id,
        logical_role=candidate.logical_role,
        facet_ids=tuple(candidate.facet_ids or ()),
        retrieval_reasons=tuple(candidate.retrieval_reasons or ()),
        source_role=candidate.source_role,
        metadata_status=candidate.metadata_status,
        section_path=candidate.section_path,
        block_type=candidate.block_type,
        page_start=candidate.page_start,
        page_end=candidate.page_end,
        block_ref=block_ref,
        ownership_assertion_ref=authority.assertion_ref if authority else None,
        ownership_revision=authority.revision if authority else None,
        ownership_basis_ref=authority.basis_ref if authority else None,
        **snapshot_fields(snapshot),
    )


def _context_pack_identity(blocks: tuple[ContextBlock, ...], qualifications=()) -> tuple[str, str]:
    ordered = [block.block_ref for block in blocks]
    identity_digest = stable_sha256({"ordered_block_refs": ordered})
    context_pack_ref = f"CP-{identity_digest.removeprefix('sha256:')[:24]}"
    content_binding = [
        {
            "block_ref": block.block_ref,
            "source": block.source,
            "content_sha256": stable_sha256(block.content),
            "weight": block.weight,
            "tokens": block.tokens,
            "ownership_digest": block.ownership_digest,
        }
        for block in blocks
    ]
    binding = {"context_pack_ref": context_pack_ref, "ordered_blocks": content_binding}
    if qualifications:
        binding["card_review_qualifications"] = qualifications
    return context_pack_ref, stable_sha256(binding)


def validate_context_pack_binding(context_pack):
    """Recompute block, order, content, and ownership bindings fail-closed."""
    blocks = tuple(getattr(context_pack, "blocks", ()) or ())
    expected_ref, expected_hash = _context_pack_identity(
        blocks, getattr(context_pack, "card_review_qualifications", ()) or ())
    observed_ref = getattr(context_pack, "context_pack_ref", None)
    observed_hash = getattr(context_pack, "context_pack_hash", None)
    if not observed_ref or not observed_hash or not getattr(
        context_pack, "ownership_contract_version", None
    ):
        raise OwnershipContractError(
            OWNERSHIP_LEGACY_ADAPTER_REQUIRED,
            "legacy Context Pack requires an explicit traceable authority adapter",
            details={"legacy_bucket": "L3_insufficient_authority"},
        )
    if observed_ref != expected_ref or observed_hash != expected_hash:
        raise OwnershipContractError(
            OWNERSHIP_BINDING_MISMATCH,
            "Context Pack ref/hash does not match the ordered block set",
            details={
                "expected_ref": expected_ref,
                "observed_ref": observed_ref,
                "expected_hash": expected_hash,
                "observed_hash": observed_hash,
            },
        )
    recomputed = aggregate_blocks(blocks, subject_ref=observed_ref)
    if (
        recomputed.ownership_digest != getattr(context_pack, "ownership_digest", None)
        or recomputed.effective_data_ownership
        != getattr(context_pack, "effective_data_ownership", None)
        or recomputed.ownership_mix_state != getattr(context_pack, "ownership_mix_state", None)
        or recomputed.ownership_error_codes
        != tuple(getattr(context_pack, "ownership_error_codes", ()) or ())
    ):
        raise OwnershipContractError(
            OWNERSHIP_BINDING_MISMATCH,
            "Context Pack ownership snapshot does not match its current blocks",
            details={"expected": recomputed.to_dict()},
        )
    return recomputed


def build_context_pack(candidate_set, strategy=None, *, weight_fn=None,
                       retrieval_mode: str | None = None,
                       token_hard_limit: int | None = None,
                       card_review_qualifications=()) -> ContextPack:
    """M3c 主入口:CandidateSet → ContextPack(简化权重 + 强制反例名额 + token 硬上限打包)。

    三级优先级 **硬顶(绝对)> 反例保护 > 主流**;只整块取舍;`estimated_tokens ≤ token_hard_limit` 永成立。
    确定化:反例池/主流池各按 (weight 降 / distance 升 / chunk_id 升) 稳定排序,可复现可测。
    """
    cfg = _cfg()
    if token_hard_limit is not None and (
        isinstance(token_hard_limit, bool)
        or not isinstance(token_hard_limit, int)
        or token_hard_limit <= 0
    ):
        raise ValueError("CONTEXT_PACK_TOKEN_HARD_LIMIT_INVALID")
    ratio, overhead = cfg["token_ratio"], cfg["overhead_per_block"]
    hard = token_hard_limit or cfg["token_hard_limit"]
    from copy import deepcopy
    from .review_qualifications import render_review_context
    qualifications = tuple(deepcopy(card_review_qualifications))
    review_text = render_review_context(qualifications)
    review_tokens = _estimate_tokens(review_text, ratio, 0) if review_text else 0
    if review_tokens > hard:
        raise ValueError("CONTEXT_PACK_REVIEW_QUALIFICATIONS_EXCEED_CAPACITY")
    cands = list(getattr(candidate_set, "candidates", ()) or ())
    wf = weight_fn or _default_weight

    # 名额:找反例类放大(承口径⑦);≤ 候选数。
    single_paper = retrieval_mode == "single_paper_extraction"
    ce_target = (0 if single_paper
                 else cfg["counterexample_target"])
    boosted = not single_paper and bool(getattr(strategy, "counterexample_boost", False))
    if boosted:
        ce_target = max(ce_target, math.ceil(getattr(candidate_set, "final_active_count", len(cands)) * 0.5))
    ce_target = min(ce_target, len(cands))

    # 1. 算 weight + tokens + 疑似反例(distance 防守,口径⑧)。
    bad_distance = 0
    rows = []
    for c in cands:
        w = wf(c)
        if w is None or not isinstance(w, (int, float)) or not math.isfinite(w):
            w = 0.0
            bad_distance += 1
        rows.append({"c": c, "w": float(w), "tok": _estimate_tokens(c.text, ratio, overhead),
                     "ce": _is_counterexample(c.text)})

    # 2. 稳定排序:listwise 序位优先；否则沿用 weight / distance。序位不是伪造 relevance score。
    def _key(r):
        d = r["c"].distance
        d = d if isinstance(d, (int, float)) and math.isfinite(d) else math.inf
        ranking_rank = getattr(r["c"], "ranking_rank", None)
        if isinstance(ranking_rank, int) and not isinstance(ranking_rank, bool) and ranking_rank > 0:
            return (0, ranking_rank, d, str(r["c"].chunk_id))
        return (1, -r["w"], d, str(r["c"].chunk_id))
    # Single-paper extraction preserves all source paragraphs. Zero reserved
    # counterexample slots disables preferential allocation, not source access.
    # Keep the suspicion annotation on each block, but do not filter the pool.
    ce_pool = [] if single_paper else sorted((r for r in rows if r["ce"]), key=_key)
    main_pool = sorted(rows if single_paper else (r for r in rows if not r["ce"]), key=_key)

    # 3. 三级优先级打包(硬顶绝对 > 反例保护 > 主流;整块取舍,口径④⑤⑥)。
    selected, total, ce_filled = [], review_tokens, 0
    single_over, ce_token_dropped, main_truncated = [], [], []
    for r in ce_pool:                                       # 3a 先放至多 ce_target 个反例(受保护)
        if ce_filled >= ce_target:
            break
        if r["tok"] > hard:                                # 反例自身超顶 → 整块丢、破不了顶(口径⑤)
            single_over.append(r["c"].chunk_id)
        elif total + r["tok"] > hard:                      # 硬顶已紧,该反例放不下 → underfill(试下一个更小的)
            ce_token_dropped.append(r["c"].chunk_id)
        else:
            selected.append(r); total += r["tok"]; ce_filled += 1
    for r in main_pool:                                    # 3b 再放主流(紧时先砍主流、绝不挤已选反例)
        if r["tok"] > hard:
            single_over.append(r["c"].chunk_id)
        elif total + r["tok"] > hard:
            main_truncated.append(r["c"].chunk_id)
        else:
            selected.append(r); total += r["tok"]

    # 4. 产物:按 weight 排序输出(反例已在 selected 中受保护)。
    blocks = tuple(_ownership_block(row) for row in sorted(selected, key=_key))
    context_pack_ref, context_pack_hash = _context_pack_identity(blocks, qualifications)
    pack_snapshot = aggregate_blocks(blocks, subject_ref=context_pack_ref)
    receipt = propagation_receipt(
        pack_snapshot,
        subject_chain={
            "selected_candidates": [
                {"chunk_id": row["c"].chunk_id, "ownership_digest": row["c"].ownership_digest}
                for row in sorted(selected, key=_key)
            ],
            "context_blocks": [
                {"block_ref": block.block_ref, "ownership_digest": block.ownership_digest}
                for block in blocks
            ],
            "context_pack": {
                "context_pack_ref": context_pack_ref,
                "context_pack_hash": context_pack_hash,
                "ownership_digest": pack_snapshot.ownership_digest,
            },
        },
        checks=(
            "SELECTED_BLOCKS_ONLY",
            "ORDERED_BLOCK_IDENTITY_BOUND",
            "STRICTEST_V1_RECOMPUTED",
            "NO_CALLER_OWNERSHIP_AUTHORITY",
        ),
    )
    truncated = bool(single_over or ce_token_dropped or main_truncated)
    pack = ContextPack(
        blocks=blocks, estimated_tokens=total, token_hard_limit=hard,
        counterexample_slots={"target": ce_target, "filled": ce_filled}, truncated=truncated,
        raw_recall_count=getattr(candidate_set, "raw_recall_count", 0),
        filtered_non_active_count=getattr(candidate_set, "filtered_non_active_count", 0),
        final_active_count=getattr(candidate_set, "final_active_count", len(cands)),
        require_verified=getattr(candidate_set, "require_verified", False),
        scope_mode=getattr(candidate_set, "scope_mode", "shared"),
        scope_paper_id=getattr(candidate_set, "scope_paper_id", None),
        retrieval_mode=retrieval_mode or "multi_paper_synthesis",
        context_pack_ref=context_pack_ref,
        context_pack_hash=context_pack_hash,
        original_estimated_tokens=review_tokens + sum(row["tok"] for row in rows),
        card_review_qualifications=qualifications,
        dropped_evidence_block_count=max(0, len(rows) - len(selected)),
        dropped_reason_counts={
            "single_block_over_limit": len(single_over),
            "counterexample_capacity_dropped": len(ce_token_dropped),
            "mainstream_capacity_dropped": len(main_truncated),
        },
        ownership_propagation_receipt=receipt.to_dict(),
        **snapshot_fields(pack_snapshot, pack=True),
    )
    log_reason("M3", "M3c 打包",
               f"blocks={len(blocks)} tokens={total}/{hard} 反例={ce_filled}/{ce_target}"
               f"{' underfilled' if ce_filled < ce_target else ''}{' truncated' if truncated else ''}",
               event_category=None, context={
                   "estimated_tokens": total, "token_hard_limit": hard,
                   "counterexample_slots": {"target": ce_target, "filled": ce_filled},   # 主口径,与 ContextPack 一致
                   "counterexample_target": ce_target, "counterexample_filled": ce_filled,  # 扁平诊断(保留)
                   "counterexample_boost": boosted, "truncated": truncated,
                   "single_block_over_limit": single_over, "ce_token_dropped": ce_token_dropped,
                   "mainstream_truncated": main_truncated, "bad_distance": bad_distance,
                   "block_count": len(blocks), "config": {**cfg, "token_hard_limit": hard},
                   "token_limit_source": (
                       "CALLER_MODEL_CAPACITY_DERIVED"
                       if token_hard_limit is not None
                       else "LEGACY_ENV_DEFAULT"
                   ),
                   "retrieval_mode": retrieval_mode or "multi_paper_synthesis",
                   # M3b 计数原样透传(M3c 不改)
                   "raw_recall_count": pack.raw_recall_count,
                   "filtered_non_active_count": pack.filtered_non_active_count,
                   "final_active_count": pack.final_active_count,
                   "require_verified": pack.require_verified,
                   "scope": {"mode": pack.scope_mode, "paper_id": pack.scope_paper_id},
                   "ownership": {
                       "effective_data_ownership": pack.effective_data_ownership,
                       "ownership_mix_state": pack.ownership_mix_state,
                       "ownership_digest": pack.ownership_digest,
                       "ownership_resolution_status": pack.ownership_resolution_status,
                       "ownership_error_codes": list(pack.ownership_error_codes),
                   },
               })
    return pack
