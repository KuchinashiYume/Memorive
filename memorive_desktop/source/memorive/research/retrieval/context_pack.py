"""Context assembly: the candidates packed into the context pack the analysis reads, with a simple
weight, reserved room for counterexamples and a hard token limit.

Input: candidate retrieval's CandidateSet and, optionally, query understanding's strategy. Nothing
is recalled again.

Two rules always hold:
- Room for counterexamples: counterexample_target places are reserved for likely counterexamples,
  which are protected when the pack is cut; when fewer are found, counterexample_filled < target is
  recorded as it is. Getting more counterexamples into the candidates is future work for retrieval.
- The token limit: the limit comes first, then protected counterexamples, then the rest. Whole
  blocks are kept or dropped, never cut; a block larger than the limit is dropped
  (single_block_over_limit). estimated_tokens <= token_hard_limit always holds; an empty pack is
  allowed.

The weight is the reranker's score when there is one (candidate_retrieval.py reranks), else
1/(1+distance); weight_fn can replace it. admission is only marked active. The pack holds packing
fields only, nothing of the analysis or opportunities; candidate retrieval's counts pass through.
Counterexamples are found by strong signals only and marked "likely"; the analysis judges them.
"""
from __future__ import annotations

import math
import os

from memorive.platform.logs import log_reason

from memorive.research.retrieval.ownership import (
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
from memorive.research.retrieval.types import ContextBlock, ContextPack

# ── defaults: each can be set by environment; the values used are logged ──
_ENV = {
    "counterexample_target": ("MEMORIVE_RETRIEVAL_COUNTEREXAMPLE_TARGET", 3),
    "token_hard_limit": ("MEMORIVE_RETRIEVAL_TOKEN_HARD_LIMIT", 6000),
    "token_ratio": ("MEMORIVE_RETRIEVAL_TOKEN_RATIO", 1.0),          # float; estimating high is the safe side
    "overhead_per_block": ("MEMORIVE_RETRIEVAL_TOKEN_OVERHEAD_PER_BLOCK", 32),
}

# Strong signals: each alone sets is_counterexample.
_STRONG_CE = (
    "相反", "反对", "反证", "不支持", "证伪", "无显著", "未观察到", "未发现", "不成立",
    "相矛盾", "驳斥", "证据不足", "并不支持", "无法重现", "未能重现",
    "contrary", "contradict", "refute", "no significant", "failed to", "did not support",
    "does not support", "no evidence", "inconsistent with", "disprove", "rebut",
)
# Weak signals: never alone; kept as a record of what was considered.
_WEAK_CE = ("但", "然而", "局限", "however", "but", "limitation")


def _cfg() -> dict:
    """The settings from the environment; a value that is not positive or not readable falls back to the default."""
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
    """1/(1+max(distance, 0)) times the rules; a missing, NaN or infinite distance gives None (the
    caller then uses 0.0 and logs it, so the order is not disturbed)."""
    if distance is None or not isinstance(distance, (int, float)) or not math.isfinite(distance):
        return None
    return (1.0 / (1.0 + max(float(distance), 0.0))) * rule_weight


def _default_weight(candidate):
    """The reranker's relevance score when there is one (larger is more relevant); otherwise
    1/(1+distance). Both are larger-is-better, so the order is the same kind."""
    rs = getattr(candidate, "rerank_score", None)
    if rs is not None and isinstance(rs, (int, float)) and math.isfinite(rs):
        return float(rs)
    return _weight(getattr(candidate, "distance", None))


def _estimate_tokens(content, ratio: float, overhead: int) -> int:
    """ceil(len(content) x ratio) + overhead: an upper estimate; overhead covers the serialized
    source, admission and so on."""
    return math.ceil(len(content or "") * ratio) + overhead


def _is_counterexample(text) -> bool:
    """A likely counterexample: only strong signals count, weak ones alone do not. Chinese as
    substrings, English in lower case."""
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
        verified_by_evidence_review=candidate.verified_by_evidence_review,
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
    """CandidateSet -> ContextPack: weights, reserved counterexample room, the token limit.

    The limit first, then protected counterexamples, then the rest; whole blocks only;
    estimated_tokens <= token_hard_limit always holds. Counterexamples and the rest are each sorted by
    weight (descending), distance and chunk_id (ascending), so the result is the same every time.
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
    from memorive.research.retrieval.review_qualifications import render_review_context
    qualifications = tuple(deepcopy(card_review_qualifications))
    review_text = render_review_context(qualifications)
    review_tokens = _estimate_tokens(review_text, ratio, 0) if review_text else 0
    if review_tokens > hard:
        raise ValueError("CONTEXT_PACK_REVIEW_QUALIFICATIONS_EXCEED_CAPACITY")
    cands = list(getattr(candidate_set, "candidates", ()) or ())
    wf = weight_fn or _default_weight

    # The room for counterexamples: larger for FIND_COUNTEREXAMPLE, at most the number of candidates.
    single_paper = retrieval_mode == "single_paper_extraction"
    ce_target = (0 if single_paper
                 else cfg["counterexample_target"])
    boosted = not single_paper and bool(getattr(strategy, "counterexample_boost", False))
    if boosted:
        ce_target = max(ce_target, math.ceil(getattr(candidate_set, "final_active_count", len(cands)) * 0.5))
    ce_target = min(ce_target, len(cands))

    # 1. Weight, tokens and likely counterexample of each candidate.
    bad_distance = 0
    rows = []
    for c in cands:
        w = wf(c)
        if w is None or not isinstance(w, (int, float)) or not math.isfinite(w):
            w = 0.0
            bad_distance += 1
        rows.append({"c": c, "w": float(w), "tok": _estimate_tokens(c.text, ratio, overhead),
                     "ce": _is_counterexample(c.text)})

    # 2. A stable order: a model-generated ranking first, else weight and distance. A ranking place
    #    is not turned into a fake relevance score.
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

    # 3. Pack: the limit first, then protected counterexamples, then the rest; whole blocks only.
    selected, total, ce_filled = [], review_tokens, 0
    single_over, ce_token_dropped, main_truncated = [], [], []
    for r in ce_pool:                                       # 3a: up to ce_target counterexamples, protected
        if ce_filled >= ce_target:
            break
        if r["tok"] > hard:                                # a counterexample larger than the limit is dropped
            single_over.append(r["c"].chunk_id)
        elif total + r["tok"] > hard:                      # no room left for it: underfilled; try a smaller one
            ce_token_dropped.append(r["c"].chunk_id)
        else:
            selected.append(r)
            total += r["tok"]
            ce_filled += 1
    for r in main_pool:                                    # 3b: the rest; when space is short they go first, never a chosen counterexample
        if r["tok"] > hard:
            single_over.append(r["c"].chunk_id)
        elif total + r["tok"] > hard:
            main_truncated.append(r["c"].chunk_id)
        else:
            selected.append(r)
            total += r["tok"]

    # 4. The pack, by weight (the counterexamples are among the selected blocks).
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
    log_reason("RETRIEVAL", "ContextAssembly 打包",
               f"blocks={len(blocks)} tokens={total}/{hard} 反例={ce_filled}/{ce_target}"
               f"{' underfilled' if ce_filled < ce_target else ''}{' truncated' if truncated else ''}",
               event_category=None, context={
                   "estimated_tokens": total, "token_hard_limit": hard,
                   "counterexample_slots": {"target": ce_target, "filled": ce_filled},   # the same form as in ContextPack
                   "counterexample_target": ce_target, "counterexample_filled": ce_filled,  # flat copies for diagnosis
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
                   # candidate retrieval's counts, passed through unchanged
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
