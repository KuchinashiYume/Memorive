"""Phase 1 P0-A structure-aware retrieval orchestration.

The existing M3b/M3c APIs stay available.  This additive wrapper adds query
facets, Card-anchor seeds, a high-confidence bibliography eligibility gate,
runtime logical spans, one controlled retry, and an explicit coverage report.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import Any, Callable, Iterable

from m11_log import log_reason

from .context_pack import build_context_pack
from .ownership import (
    OWNERSHIP_CONFLICT,
    OWNERSHIP_MISSING,
    OwnershipContractError,
    aggregate_ownership,
    bind_authority_projection,
    snapshot_fields,
    stable_sha256,
    validate_snapshot,
)
from .types import Candidate, CandidateSet, ContextPack, LogicalSpan, QueryFacet


SINGLE_PAPER_EXTRACTION = "single_paper_extraction"
MULTI_PAPER_SYNTHESIS = "multi_paper_synthesis"
_BIBLIOGRAPHY_INTENTS = {
    "citation_lineage",
    "bibliography_mapping",
    "find_cited_studies",
    "reference_verification",
}
_REFERENCE_SECTIONS = {"references", "reference", "bibliography", "参考文献"}
_BIBLIOGRAPHY_TYPES = {
    "bibliography_entry",
    "reference_entry",
    "references",
    "list",
    "list_item",
}
_TABLE_TYPES = {"table"}
_TABLE_CAPTION_TYPES = {"table_caption", "caption_table"}
_TABLE_FOOTNOTE_TYPES = {"table_footnote", "footnote"}
_CITATION_LINE = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:[\[［【]\s*\d+(?:\s*[-,]\s*\d+)*\s*[\]］】]|"
    r"\d+[.)]\s+|[A-Z][A-Za-z'’-]+,\s+[A-Z])"
)
_TABLE_CAPTION_LINE = re.compile(r"^\s*(?:[*_`]+\s*)?(?:table\s+|表\s*)\d+", re.I)
_TABLE_FOOTNOTE_LINE = re.compile(
    r"^\s*(?:note\s*[*:]?|footnote\s*:|[*†‡]\s+|[a-z]\s+(?:k[- ]?values?|values?|data|results?)\b)",
    re.I,
)
_NARRATIVE_CITATION = re.compile(
    r"(?:\bet\s+al\.|\[[0-9]+(?:\s*[-,]\s*[0-9]+)*\]|"
    r"\b(?:reported|found|stated|showed|demonstrated|according\s+to)\b)",
    re.I,
)
_AUTHOR_INTERPRETATION_TEXT = re.compile(
    r"\b(?:future\s+stud(?:y|ies)|further\s+(?:study|studies|research)|"
    r"should\s+(?:also\s+)?be\s+(?:conducted|investigated)|more\s+direct\s+evidence|"
    r"qualitative\s+support|cannot\s+be\s+ruled\s+out|no\s+clear\s+evidence)\b",
    re.I,
)
_CHUNK_SEQ = re.compile(r"#c(\d+)$", re.I)
LOGICAL_SPAN_TOKEN_LIMIT = 2048
LOGICAL_NEIGHBOR_TOKEN_LIMIT = 4096
_LOGICAL_BLOCK_OVERHEAD = 32


_FACET_SPECS = {
    "experimental_conditions": {
        "question_terms": (
            "tested conditions", "experimental conditions", "operating conditions",
            "reaction conditions", "实验条件", "运行条件", "反应条件",
        ),
        "terms": (
            "concentration", "temperature", "duration", "reactor", "bottle",
            "dosage", "dose", "days", " g/l", "°c", " ℃", "ph",
            "浓度", "温度", "时长", "反应器", "投加量",
        ),
        "paths": ("method", "boundary_conditions", "key_data"),
        "sections": ("materials and methods", "methods", "experimental"),
        "blocks": ("paragraph", "table"),
    },
    "comparator": {
        "question_terms": (
            "comparator", "control", "compared with", "compared to", "versus",
            " vs ", "baseline", "对照", "相比", "比较对象",
        ),
        "terms": (
            "comparator", "control", "compared with", "compared to", "versus",
            " vs ", "baseline", "non-amended", "untreated", "对照", "相比",
        ),
        "paths": ("method", "key_results", "key_data", "boundary_conditions"),
        "sections": ("materials and methods", "methods", "results"),
        "blocks": ("paragraph", "table"),
    },
    "substrate_conditions": {
        "terms": ("substrate", "electron donor", "medium", "vfa", "cod", "底物", "电子供体", "培养基"),
        "paths": ("method", "boundary_conditions"),
        "sections": ("materials and methods", "methods", "experimental"),
        "blocks": ("paragraph", "table"),
    },
    "redox_conditions": {
        "question_terms": (
            "redox", "oxidation-reduction", "sulphide", "sulfide", "n2", "co2",
            "氧化还原", "硫化物",
        ),
        "terms": ("redox", "anaerobic", "sulphide", "sulfide", "n2", "co2", "氧化还原", "厌氧", "硫化物"),
        "paths": ("method", "boundary_conditions", "key_data"),
        "sections": ("materials and methods", "methods", "results"),
        "blocks": ("paragraph", "table"),
    },
    "dye_conditions": {
        "terms": ("dye condition", "dye concentration", "dye class", "azo dye", "染料条件", "染料浓度", "染料类型"),
        "paths": ("research_object", "method", "boundary_conditions"),
        "sections": ("materials and methods", "methods", "results"),
        "blocks": ("paragraph", "table"),
    },
    "kinetics": {
        "terms": ("kinetic", "rate constant", "half-life", "first-order", "lag phase", "k-value", "动力学", "速率常数", "半衰期"),
        "paths": ("key_results", "key_data"),
        "sections": ("results",),
        "blocks": ("paragraph", "table"),
    },
    "performance": {
        "terms": ("performance", "decolourisation", "decolorization", "removal", "yield", "性能", "脱色率", "去除率"),
        "paths": ("key_results", "key_data"),
        "sections": ("results",),
        "blocks": ("paragraph", "table"),
    },
    "quantitative_performance": {
        "question_terms": (
            "quantitative outcome", "quantitative result", "numerical result",
            "exact value", "effect size", "percentage", "fold change", "numeric outcome",
            "定量结果", "数值结果", "具体数值", "百分比", "倍数变化",
        ),
        "terms": (
            "performance", "yield", "rate", "removal", "increase", "decrease",
            "%", "fold", "higher", "lower", "产率", "速率", "去除", "提高", "降低",
        ),
        "paths": ("key_results", "key_data"),
        "sections": ("results",),
        "blocks": ("paragraph", "table"),
    },
    "mechanism_direct_evidence": {
        "question_terms": (
            "direct evidence", "mechanistic evidence", "mechanism evidence",
            "distinguish", "distinguishes", "differentiates", "demonstrate the mechanism",
            "直接证据", "机制证据", "区分", "证明机制",
        ),
        "terms": (
            "direct evidence", "mechanism", "diet", "interspecies electron transfer",
            "conductivity", "electron transfer", "pili", "cytochrome", "adsorption",
            "直接证据", "机制", "电子传递", "电导", "吸附",
        ),
        "paths": ("key_results", "key_data", "author_conclusion"),
        "sections": ("results", "findings"),
        "blocks": ("paragraph", "table"),
    },
    "interpretation_limits": {
        "question_terms": (
            "limit", "uncertainty", "interpret", "caveat", "applicability",
            "局限", "不确定性", "解释边界", "适用性",
        ),
        "terms": (
            "limit", "uncertainty", "interpret", "caveat", "applicability",
            "future stud", "further research", "more direct evidence", "qualitative support",
            "局限", "不确定性", "解释边界", "适用性", "未来研究",
        ),
        "paths": ("boundary_conditions", "author_limitations_outlook", "author_conclusion"),
        "sections": ("discussion", "conclusions", "materials and methods"),
        "blocks": ("paragraph",),
    },
}

_FACET_SOURCE_ROLE_RULES = {
    "experimental_conditions": frozenset({"direct_method", "direct_result"}),
    "comparator": frozenset({"direct_method", "direct_result"}),
    "quantitative_performance": frozenset({"direct_result"}),
    "mechanism_direct_evidence": frozenset({"direct_result"}),
    "interpretation_limits": frozenset(
        {"direct_method", "direct_result", "author_interpretation"}
    ),
}


def detect_query_facets(question: str) -> tuple[QueryFacet, ...]:
    """Rule-based facets; no model call and no domain-specific hidden inference."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    folded = question.casefold()
    facets = []
    for facet_id, spec in _FACET_SPECS.items():
        question_terms = spec.get("question_terms", spec["terms"])
        if any(term.casefold() in folded for term in question_terms):
            facets.append(QueryFacet(
                id=facet_id,
                required=True,
                query_terms=tuple(spec["terms"]),
                preferred_card_paths=tuple(spec["paths"]),
                preferred_sections=tuple(spec["sections"]),
                preferred_block_types=tuple(spec["blocks"]),
            ))
    if not facets:
        facets.append(QueryFacet(id="direct_answer", required=True, query_terms=(question.strip(),)))
    return tuple(facets)


def bibliography_is_eligible(query_intent: str | None) -> bool:
    return query_intent in _BIBLIOGRAPHY_INTENTS


def _section_leaf(section_path: str | None) -> str:
    if not isinstance(section_path, str):
        return ""
    return re.split(r"\s*(?:>|/|::)\s*", section_path.strip())[-1].casefold()


def is_high_confidence_bibliography(candidate: Candidate) -> bool:
    """Hard exclusion needs both a reference section and entry-like structure."""
    if _section_leaf(candidate.section_path) not in _REFERENCE_SECTIONS:
        return False
    if (candidate.block_type or "").casefold() in _BIBLIOGRAPHY_TYPES:
        return True
    if candidate.metadata_status == "unknown":
        return False
    return bool(_CITATION_LINE.search(candidate.text or ""))


def infer_source_role(
    section_path: str | None,
    block_type: str | None,
    text: str | None = None,
    *,
    document_is_review: bool = False,
) -> str | None:
    """Infer only high-confidence origin classes from structural metadata."""
    section = str(section_path or "").casefold()
    leaf = _section_leaf(section_path)
    block = str(block_type or "").casefold()
    if leaf in _REFERENCE_SECTIONS:
        return "secondary_citation_reported_by_paper"
    if document_is_review:
        if any(term in section for term in ('conclusion', 'outlook', '结论', '展望')):
            return 'author_interpretation'
        return 'secondary_citation_reported_by_paper'
    if any(term in section for term in ("method", "materials", "experimental", "protocol", "实验", "材料", "方法")):
        return "direct_method"
    is_background = any(
        term in section
        for term in ("introduction", "background", "related work", "literature review")
    )
    if is_background and _NARRATIVE_CITATION.search(text or ""):
        return "secondary_citation_reported_by_paper"
    has_result = any(term in section for term in ("result", "finding", "performance", "data", "结果", "效能", "性能"))
    has_interpretation = any(
        term in section for term in ("discussion", "conclusion", "outlook", "limitation", "讨论", "结论", "展望", "局限")
    )
    if _AUTHOR_INTERPRETATION_TEXT.search(text or ""):
        return "author_interpretation"
    if has_result and (block == "table" or bool(re.search(r"\d", text or ""))):
        return "direct_result"
    if has_interpretation:
        return "author_interpretation"
    if document_is_review:
        return "secondary_citation_reported_by_paper"
    if has_result:
        return "direct_result"
    if block == "table" and _TABLE_CAPTION_LINE.search(text or ""):
        return "direct_result"
    return None


def _is_review_card(card: dict[str, Any] | None) -> bool:
    if not isinstance(card, dict):
        return False
    source_anchor = card.get("source_anchor")
    paper_id = (
        card.get("paper_id")
        or (source_anchor.get("paper_id") if isinstance(source_anchor, dict) else None)
        or ""
    )
    values: list[Any] = [paper_id, card.get("title"), card.get("research_question"), card.get("method")]
    folded = " ".join(str(value or "") for value in values).casefold()
    return "review" in folded or "综述" in folded or "研究进展" in folded


def _chunk_sequence(chunk_id: str) -> int | None:
    match = _CHUNK_SEQ.search(str(chunk_id))
    return int(match.group(1)) if match else None


def _stable_union(*values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for value in values for item in value if item))


def _starts_as_continuation(text: str) -> bool:
    stripped = (text or "").lstrip()
    return bool(
        stripped[:1].islower()
        or bool(re.match(r'[\u3400-\u9fff]', stripped))
        or stripped.casefold().startswith(("that ", "which ", "tested ", "of ", "and ", "but "))
    )


def _ends_as_incomplete(text: str) -> bool:
    stripped = (text or "").rstrip()
    return bool(stripped) and (
        not stripped.endswith((".", "?", "!", ":", ";", "。", "？", "！"))
        or stripped.endswith(("-", "–", "—"))
    )


def _is_table_caption(candidate: Candidate) -> bool:
    block = (candidate.block_type or "").casefold()
    if block in _TABLE_CAPTION_TYPES:
        return True
    if block in {"figure_caption", "paragraph"}:
        return bool(_TABLE_CAPTION_LINE.search(candidate.text or ""))
    return False


def _is_table_title(candidate: Candidate) -> bool:
    text = (candidate.text or "").strip()
    return (
        (candidate.block_type or "").casefold() == "paragraph"
        and 0 < len(text) <= 240
        and "|" not in text
    )


def _is_table_footnote(candidate: Candidate) -> bool:
    block = (candidate.block_type or "").casefold()
    return block in _TABLE_FOOTNOTE_TYPES or (
        block == "paragraph" and bool(_TABLE_FOOTNOTE_LINE.search(candidate.text or ""))
    )


def _logical_span_tokens(members: Iterable[Candidate]) -> int:
    """Conservative upper bound aligned with the Context Pack default estimator."""
    return sum(len(item.text or "") + _LOGICAL_BLOCK_OVERHEAD for item in members)


def _neighbor_candidate(
    chunk: dict[str, Any],
    seed: Candidate,
    *,
    document_is_review: bool,
) -> Candidate:
    subject_ref = f"chunk:{str(chunk.get('paper_id') or seed.paper_id)}:{chunk['chunk_id']}"
    authority = seed.ownership_contributors[0] if len(seed.ownership_contributors) == 1 else None
    try:
        validated_seed = validate_snapshot(seed)
    except OwnershipContractError as exc:
        snapshot = aggregate_ownership(
            seed.ownership_contributors,
            subject_ref=subject_ref,
            inherited_errors=(*seed.ownership_error_codes, exc.code),
            warnings=seed.ownership_warnings,
            legacy_bucket=seed.legacy_bucket,
        )
    else:
        snapshot = bind_authority_projection(
            subject_ref=subject_ref,
            authoritative_value=validated_seed.effective_data_ownership,
            assertion_ref=seed.ownership_assertion_ref or (authority.assertion_ref if authority else None),
            revision=seed.ownership_revision or (authority.revision if authority else None),
            basis_ref=seed.ownership_basis_ref or (authority.basis_ref if authority else None),
            projection_value=chunk.get("data_ownership"),
            projection_revision=chunk.get("ownership_revision"),
            projection_ref=f"chunk-sidecar:{chunk['chunk_id']}",
        )
    return Candidate(
        chunk_id=str(chunk["chunk_id"]),
        paper_id=str(chunk.get("paper_id") or seed.paper_id),
        text=str(chunk.get("text") or ""),
        distance=seed.distance,
        admission=seed.admission,
        field=seed.field,
        verified_by_m6=seed.verified_by_m6,
        credibility=seed.credibility,
        rerank_score=seed.rerank_score,
        section_path=chunk.get("section_path"),
        block_type=chunk.get("block_type"),
        page_start=chunk.get("page_start"),
        page_end=chunk.get("page_end"),
        metadata_status="chunk_sidecar",
        facet_ids=seed.facet_ids,
        retrieval_reasons=_stable_union(
            seed.retrieval_reasons,
            ("logical_span_neighbor", f"neighbor_of:{seed.chunk_id}"),
        ),
        source_role=chunk.get("source_role") or infer_source_role(
            chunk.get("section_path"),
            chunk.get("block_type"),
            chunk.get("text"),
            document_is_review=document_is_review,
        ),
        ownership_assertion_ref=(authority.assertion_ref if authority else seed.ownership_assertion_ref),
        ownership_revision=(authority.revision if authority else seed.ownership_revision),
        ownership_basis_ref=(authority.basis_ref if authority else seed.ownership_basis_ref),
        ownership_projection_value=chunk.get("data_ownership"),
        ownership_projection_revision=chunk.get("ownership_revision"),
        **snapshot_fields(snapshot),
    )


def _controlled_neighbor_candidates(
    candidates: Iterable[Candidate],
    chunks: list[dict[str, Any]],
    *,
    document_is_review: bool,
    token_limit: int = LOGICAL_NEIGHBOR_TOKEN_LIMIT,
) -> tuple[Candidate, ...]:
    """Fetch only strict structural neighbors from an explicitly supplied full chunk index."""
    seeds = tuple(candidates)
    existing_ids = {item.chunk_id for item in seeds}
    chunk_by_location: dict[tuple[str, int], dict[str, Any]] = {}
    for chunk in chunks:
        if not isinstance(chunk, dict) or chunk.get("admission") not in (None, "active"):
            continue
        chunk_id = chunk.get("chunk_id")
        sequence = _chunk_sequence(str(chunk_id or ""))
        paper_id = str(chunk.get("paper_id") or "")
        if isinstance(chunk_id, str) and sequence is not None and paper_id:
            chunk_by_location[(paper_id, sequence)] = chunk

    added: dict[str, Candidate] = {}
    used_tokens = 0

    def at(seed: Candidate, sequence: int) -> Candidate | None:
        chunk = chunk_by_location.get((seed.paper_id, sequence))
        if chunk is None or chunk.get("section_path") != seed.section_path:
            return None
        return _neighbor_candidate(chunk, seed, document_is_review=document_is_review)

    def add(item: Candidate | None) -> bool:
        nonlocal used_tokens
        if item is None:
            return False
        if item.chunk_id in existing_ids or item.chunk_id in added:
            return True
        cost = _logical_span_tokens((item,))
        if used_tokens + cost > token_limit:
            return False
        added[item.chunk_id] = item
        used_tokens += cost
        return True

    for seed in sorted(
        seeds,
        key=lambda item: (
            item.paper_id,
            _chunk_sequence(item.chunk_id)
            if _chunk_sequence(item.chunk_id) is not None
            else 10**12,
            item.chunk_id,
        ),
    ):
        seed_seq = _chunk_sequence(seed.chunk_id)
        if seed_seq is None:
            continue
        block = (seed.block_type or "").casefold()
        if block in _TABLE_TYPES:
            first_seq = last_seq = seed_seq
            sequence = seed_seq - 1
            while (item := at(seed, sequence)) is not None and (
                item.block_type or ""
            ).casefold() in _TABLE_TYPES:
                if not add(item):
                    break
                first_seq = sequence
                sequence -= 1
            sequence = seed_seq + 1
            while (item := at(seed, sequence)) is not None and (
                item.block_type or ""
            ).casefold() in _TABLE_TYPES:
                if not add(item):
                    break
                last_seq = sequence
                sequence += 1
            before_one = at(seed, first_seq - 1)
            before_two = at(seed, first_seq - 2)
            if before_one and _is_table_caption(before_one):
                add(before_one)
            elif (
                before_one
                and before_two
                and _is_table_title(before_one)
                and _is_table_caption(before_two)
            ):
                add(before_two)
                add(before_one)
            after_one = at(seed, last_seq + 1)
            if after_one and _is_table_footnote(after_one):
                add(after_one)
        elif _is_table_caption(seed):
            next_item = at(seed, seed_seq + 1)
            next_two = at(seed, seed_seq + 2)
            if next_item and (next_item.block_type or "").casefold() in _TABLE_TYPES:
                add(next_item)
            elif (
                next_item
                and next_two
                and _is_table_title(next_item)
                and (next_two.block_type or "").casefold() in _TABLE_TYPES
            ):
                add(next_item)
                add(next_two)
        elif _is_table_footnote(seed):
            previous = at(seed, seed_seq - 1)
            if previous and (previous.block_type or "").casefold() in _TABLE_TYPES:
                add(previous)
        elif block == "paragraph":
            previous = at(seed, seed_seq - 1)
            next_item = at(seed, seed_seq + 1)
            if (
                previous
                and next_item
                and _is_table_title(seed)
                and _is_table_caption(previous)
                and (next_item.block_type or "").casefold() in _TABLE_TYPES
            ):
                add(previous)
                add(next_item)
                continue
            if _ends_as_incomplete(seed.text):
                if (
                    next_item
                    and _starts_as_continuation(next_item.text)
                    and _logical_span_tokens((seed, next_item)) <= LOGICAL_SPAN_TOKEN_LIMIT
                ):
                    add(next_item)
            if _starts_as_continuation(seed.text):
                if (
                    previous
                    and _ends_as_incomplete(previous.text)
                    and _logical_span_tokens((previous, seed)) <= LOGICAL_SPAN_TOKEN_LIMIT
                ):
                    add(previous)
    return tuple(added.values())


def _make_span(members: list[Candidate], logical_role: str) -> LogicalSpan:
    member_ids = tuple(member.chunk_id for member in members)
    digest = hashlib.sha256("|".join(member_ids).encode("utf-8")).hexdigest()[:16]
    pages_start = [item.page_start for item in members if isinstance(item.page_start, int)]
    pages_end = [item.page_end for item in members if isinstance(item.page_end, int)]
    roles = {item.source_role for item in members if item.source_role}
    metadata_states = {item.metadata_status for item in members}
    span_ref = f"span:S-{digest}"
    inherited_errors = [
        code
        for item in members
        for code in (
            item.ownership_error_codes
            or (() if item.ownership_contributors else (OWNERSHIP_MISSING,))
        )
    ]
    for item in members:
        try:
            validate_snapshot(item)
        except OwnershipContractError as exc:
            inherited_errors.append(exc.code)
    snapshot = aggregate_ownership(
        (
            contributor
            for item in members
            for contributor in item.ownership_contributors
        ),
        subject_ref=span_ref,
        inherited_errors=inherited_errors,
        warnings=(
            warning
            for item in members
            for warning in item.ownership_warnings
        ),
        legacy_bucket=(
            "L0_native_v1"
            if all(item.legacy_bucket == "L0_native_v1" for item in members)
            else "L1_traceable_card"
        ),
    )
    authority = snapshot.ownership_contributors[0] if len(snapshot.ownership_contributors) == 1 else None
    return LogicalSpan(
        logical_span_id=f"S-{digest}",
        member_chunk_ids=member_ids,
        content="\n".join(item.text for item in members if item.text),
        paper_id=members[0].paper_id,
        section_path=members[0].section_path,
        logical_role=logical_role,
        block_type="table" if logical_role == "table_with_footnotes" else members[0].block_type,
        page_start=min(pages_start) if pages_start else None,
        page_end=max(pages_end) if pages_end else None,
        distance=min(item.distance for item in members),
        facet_ids=_stable_union(*(item.facet_ids for item in members)),
        retrieval_reasons=_stable_union(*(item.retrieval_reasons for item in members)),
        source_role=next(iter(roles)) if len(roles) == 1 else "mixed" if roles else None,
        metadata_status=next(iter(metadata_states)) if len(metadata_states) == 1 else "mixed",
        field=members[0].field,
        admission=members[0].admission,
        verified_by_m6=members[0].verified_by_m6,
        credibility=members[0].credibility,
        rerank_score=max(
            (item.rerank_score for item in members if item.rerank_score is not None),
            default=None,
        ),
        ownership_assertion_ref=authority.assertion_ref if authority else None,
        ownership_revision=authority.revision if authority else None,
        ownership_basis_ref=authority.basis_ref if authority else None,
        **snapshot_fields(snapshot),
    )


def build_logical_spans(
    candidates: Iterable[Candidate],
    *,
    token_limit: int = LOGICAL_SPAN_TOKEN_LIMIT,
) -> tuple[LogicalSpan, ...]:
    """Group strict neighbors only; this function never recalls unseen chunks."""
    if token_limit <= 0:
        raise ValueError("token_limit must be positive")
    ordered = sorted(
        tuple(candidates),
        key=lambda item: (
            item.paper_id,
            _chunk_sequence(item.chunk_id)
            if _chunk_sequence(item.chunk_id) is not None
            else 10**12,
            item.chunk_id,
        ),
    )
    consumed: set[str] = set()
    spans: list[LogicalSpan] = []
    by_location = {
        (item.paper_id, _chunk_sequence(item.chunk_id)): item
        for item in ordered
        if _chunk_sequence(item.chunk_id) is not None
    }
    for seed in ordered:
        if seed.chunk_id in consumed or (seed.block_type or "").casefold() not in _TABLE_TYPES:
            continue
        seed_seq = _chunk_sequence(seed.chunk_id)
        if seed_seq is None:
            continue
        table_members = [seed]
        for direction in (-1, 1):
            sequence = seed_seq + direction
            while True:
                item = by_location.get((seed.paper_id, sequence))
                if (
                    item is None
                    or item.chunk_id in consumed
                    or item.section_path != seed.section_path
                    or (item.block_type or "").casefold() not in _TABLE_TYPES
                ):
                    break
                table_members.append(item)
                sequence += direction
        table_members.sort(
            key=lambda item: (_chunk_sequence(item.chunk_id) or 10**12, item.chunk_id)
        )
        first_seq = _chunk_sequence(table_members[0].chunk_id) or seed_seq
        last_seq = _chunk_sequence(table_members[-1].chunk_id) or seed_seq
        before_one = by_location.get((seed.paper_id, first_seq - 1))
        before_two = by_location.get((seed.paper_id, first_seq - 2))
        prefixes: list[Candidate] = []
        if before_one and before_one.section_path == seed.section_path:
            if _is_table_caption(before_one):
                prefixes.append(before_one)
            elif _is_table_title(before_one) and before_two and _is_table_caption(before_two):
                prefixes.extend((before_two, before_one))
        after_one = by_location.get((seed.paper_id, last_seq + 1))
        suffixes = (
            [after_one]
            if after_one
            and after_one.section_path == seed.section_path
            and _is_table_footnote(after_one)
            else []
        )
        members = [*prefixes, *table_members, *suffixes]
        logical_role = "table_with_footnotes"
        if _logical_span_tokens(members) > token_limit:
            members = [seed]
            logical_role = "single_chunk"
        consumed.update(item.chunk_id for item in members)
        spans.append(_make_span(members, logical_role))

    for seed in ordered:
        if seed.chunk_id in consumed:
            continue
        if _section_leaf(seed.section_path) in _REFERENCE_SECTIONS:
            consumed.add(seed.chunk_id)
            spans.append(_make_span([seed], "single_chunk"))
            continue
        partner = None
        sequence = None
        seed_seq = _chunk_sequence(seed.chunk_id)
        block = (seed.block_type or "").casefold()
        if seed_seq is not None and block == "paragraph":
            next_item = by_location.get((seed.paper_id, seed_seq + 1))
            previous = by_location.get((seed.paper_id, seed_seq - 1))
            if (
                next_item
                and next_item.chunk_id not in consumed
                and next_item.section_path == seed.section_path
                and (next_item.block_type or "").casefold() == "paragraph"
                and _ends_as_incomplete(seed.text)
                and _starts_as_continuation(next_item.text)
            ):
                partner, sequence = next_item, [seed, next_item]
            elif (
                previous
                and previous.chunk_id not in consumed
                and previous.section_path == seed.section_path
                and (previous.block_type or "").casefold() == "paragraph"
                and _starts_as_continuation(seed.text)
                and _ends_as_incomplete(previous.text)
            ):
                partner, sequence = previous, [previous, seed]
        if sequence is not None and _logical_span_tokens(sequence) > token_limit:
            partner, sequence = None, None
        if partner is not None:
            consumed.update((seed.chunk_id, partner.chunk_id))
            spans.append(_make_span(sequence, "paragraph_continuation"))
        else:
            consumed.add(seed.chunk_id)
            spans.append(_make_span([seed], "single_chunk"))
    return tuple(spans)


def _normalise_anchors(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [
            dict(item) if isinstance(item, dict) else {"chunk_id": item}
            for item in value
            if isinstance(item, dict) or isinstance(item, str)
        ]
    if isinstance(value, dict) and isinstance(value.get("chunk_ids"), list):
        return [{"chunk_id": chunk_id} for chunk_id in value["chunk_ids"]]
    if isinstance(value, dict) and value.get("chunk_id"):
        return [dict(value)]
    return []


def build_card_anchor_candidates(
    card: dict[str, Any],
    chunks: list[dict[str, Any]],
    facets: Iterable[QueryFacet],
) -> tuple[Candidate, ...]:
    """Use Card anchors only as seeds; returned text always comes from Chunks."""
    by_field = ((card.get("source_anchor") or {}).get("by_field") or {})
    if not isinstance(by_field, dict):
        return ()
    chunk_map = {
        item.get("chunk_id"): item
        for item in chunks
        if isinstance(item, dict) and isinstance(item.get("chunk_id"), str)
    }
    seeded: dict[str, Candidate] = {}
    document_is_review = _is_review_card(card)
    card_paper_id = str(card.get("paper_id") or "")
    authoritative_value = card.get("data_ownership")
    revision = card.get("ownership_revision", 1)
    authority_payload = {
        "paper_id": card_paper_id,
        "data_ownership": authoritative_value,
        "ownership_revision": revision,
        "source_anchor_paper_id": (card.get("source_anchor") or {}).get("paper_id")
        if isinstance(card.get("source_anchor"), dict)
        else None,
    }
    authority_digest = stable_sha256(authority_payload).removeprefix("sha256:")
    assertion_ref = card.get("ownership_assertion_ref") or f"OS-CARD-{authority_digest[:24]}"
    basis_ref = card.get("ownership_basis_ref") or f"card-object-sha256:{authority_digest}"
    for facet in facets:
        fields = facet.preferred_card_paths or tuple(by_field)
        for field in fields:
            for anchor in _normalise_anchors(by_field.get(field)):
                chunk = chunk_map.get(anchor.get("chunk_id"))
                if not chunk:
                    continue
                chunk_id = chunk["chunk_id"]
                current = seeded.get(chunk_id)
                facet_ids = _stable_union(current.facet_ids if current else (), (facet.id,))
                reasons = _stable_union(
                    current.retrieval_reasons if current else (),
                    ("card_anchor", f"card_path:{field}"),
                )
                paper_id = str(chunk.get("paper_id") or card_paper_id)
                snapshot = bind_authority_projection(
                    subject_ref=f"chunk:{paper_id}:{chunk_id}",
                    authoritative_value=authoritative_value,
                    assertion_ref=assertion_ref,
                    revision=revision,
                    basis_ref=basis_ref,
                    projection_value=chunk.get("data_ownership"),
                    projection_revision=chunk.get("ownership_revision"),
                    projection_ref=f"chunk-sidecar:{chunk_id}",
                )
                seeded[chunk_id] = Candidate(
                    chunk_id=chunk_id,
                    paper_id=paper_id,
                    text=str(chunk.get("text") or ""),
                    distance=current.distance if current else 0.0,
                    admission="active",
                    field=field,
                    credibility=current.credibility if current else None,
                    section_path=chunk.get("section_path"),
                    block_type=chunk.get("block_type"),
                    page_start=chunk.get("page_start"),
                    page_end=chunk.get("page_end"),
                    metadata_status="chunk_sidecar",
                    facet_ids=facet_ids,
                    retrieval_reasons=reasons,
                    source_role=chunk.get("source_role") or infer_source_role(
                        chunk.get("section_path"),
                        chunk.get("block_type"),
                        chunk.get("text"),
                        document_is_review=document_is_review,
                    ),
                    ownership_assertion_ref=assertion_ref,
                    ownership_revision=revision,
                    ownership_basis_ref=basis_ref,
                    ownership_projection_value=chunk.get("data_ownership"),
                    ownership_projection_revision=chunk.get("ownership_revision"),
                    **snapshot_fields(snapshot),
                )
    return tuple(seeded.values())


def retrieve_faceted_candidates(
    question: str,
    facets: Iterable[QueryFacet],
    *,
    retrieve_fn: Callable[..., CandidateSet],
    **retrieve_kwargs,
) -> CandidateSet:
    """Run one independent retrieval per required facet and merge by chunk ID."""
    query_facets = tuple(facets)
    results = []
    decorated = []
    for facet in query_facets:
        terms = " ".join(facet.query_terms)
        facet_query = f"{question}\nRequired facet: {facet.id}. Retrieval terms: {terms}".strip()
        result = retrieve_fn(facet_query, **retrieve_kwargs)
        if not isinstance(result, CandidateSet):
            raise TypeError("retrieve_fn must return CandidateSet")
        results.append(result)
        decorated.extend(
            replace(
                candidate,
                facet_ids=_stable_union(candidate.facet_ids, (facet.id,)),
                retrieval_reasons=_stable_union(
                    candidate.retrieval_reasons, (f"facet:{facet.id}",)
                ),
            )
            for candidate in result.candidates
        )
    if not results:
        return CandidateSet(
            candidates=(),
            target_active_count=0,
            raw_recall_count=0,
            filtered_non_active_count=0,
            final_active_count=0,
            reached_overfetch_cap=False,
        )
    scopes = {(result.scope_mode, result.scope_paper_id) for result in results}
    if len(scopes) != 1:
        raise ValueError("facet retrieval returned inconsistent source scopes")
    merged = _dedupe(decorated)
    scope_mode, scope_paper_id = next(iter(scopes))
    output = CandidateSet(
        candidates=merged,
        target_active_count=sum(result.target_active_count for result in results),
        raw_recall_count=sum(result.raw_recall_count for result in results),
        filtered_non_active_count=sum(
            result.filtered_non_active_count for result in results
        ),
        final_active_count=len(merged),
        reached_overfetch_cap=any(result.reached_overfetch_cap for result in results),
        require_verified=any(result.require_verified for result in results),
        scope_mode=scope_mode,
        scope_paper_id=scope_paper_id,
    )
    log_reason(
        "M3",
        "M3b 分面独立召回",
        f"facets={len(query_facets)} raw={output.raw_recall_count} merged={len(merged)}",
        event_category=None,
        context={
            "facet_ids": [facet.id for facet in query_facets],
            "retrieval_call_count": len(query_facets),
            "raw_recall_count": output.raw_recall_count,
            "final_active_count": output.final_active_count,
            "scope_mode": scope_mode,
            "scope_paper_id": scope_paper_id,
        },
    )
    return output


def _candidate_facets(candidate: Candidate, facets: tuple[QueryFacet, ...]) -> tuple[str, ...]:
    facet_by_id = {facet.id: facet for facet in facets}
    matched = [
        facet_id
        for facet_id in candidate.facet_ids
        if facet_id not in facet_by_id
        or _facet_candidate_eligible(candidate, facet_by_id[facet_id])
    ]
    haystack = " ".join(
        str(value or "")
        for value in (candidate.text, candidate.field, candidate.section_path, candidate.block_type)
    ).casefold()
    for facet in facets:
        if facet.id in matched:
            continue
        path_match = candidate.field in facet.preferred_card_paths
        section_match = any(item.casefold() in haystack for item in facet.preferred_sections)
        block_match = candidate.block_type in facet.preferred_block_types
        term_match = any(term.casefold() in haystack for term in facet.query_terms)
        if not _facet_candidate_eligible(candidate, facet):
            continue
        if term_match or (path_match and (section_match or block_match)):
            matched.append(facet.id)
    return tuple(matched)


def _facet_candidate_eligible(candidate: Candidate, facet: QueryFacet) -> bool:
    allowed = _FACET_SOURCE_ROLE_RULES.get(facet.id)
    if allowed is not None and candidate.source_role not in allowed:
        return False
    if facet.id == "quantitative_performance" and not re.search(r"\d", candidate.text or ""):
        return False
    return True


def _annotate_candidate(
    candidate: Candidate,
    facets: tuple[QueryFacet, ...],
    *,
    document_is_review: bool,
) -> Candidate:
    inferred_role = infer_source_role(
        candidate.section_path,
        candidate.block_type,
        candidate.text,
        document_is_review=document_is_review,
    )
    role = (
        inferred_role
        if inferred_role in {
            "direct_method",
            "author_interpretation",
            "secondary_citation_reported_by_paper",
        }
        else candidate.source_role or inferred_role
    )
    with_role = replace(candidate, source_role=role)
    return replace(with_role, facet_ids=_candidate_facets(with_role, facets))


def _dedupe(candidates: Iterable[Candidate]) -> tuple[Candidate, ...]:
    result: dict[str, Candidate] = {}
    for candidate in candidates:
        existing = result.get(candidate.chunk_id)
        if existing is None:
            result[candidate.chunk_id] = candidate
            continue
        preferred = candidate if candidate.distance < existing.distance else existing
        other = existing if preferred is candidate else candidate
        merged = replace(
            preferred,
            facet_ids=_stable_union(existing.facet_ids, candidate.facet_ids),
            retrieval_reasons=_stable_union(
                existing.retrieval_reasons, candidate.retrieval_reasons
            ),
            section_path=preferred.section_path or other.section_path,
            block_type=preferred.block_type or other.block_type,
            page_start=preferred.page_start if preferred.page_start is not None else other.page_start,
            page_end=preferred.page_end if preferred.page_end is not None else other.page_end,
            metadata_status=(
                preferred.metadata_status
                if preferred.metadata_status != "unknown"
                else other.metadata_status
            ),
        )
        validation_errors = []
        for item in (existing, candidate):
            try:
                validate_snapshot(item)
            except OwnershipContractError as exc:
                validation_errors.append(exc.code)
        ownership_differs = existing.ownership_digest != candidate.ownership_digest
        if ownership_differs or validation_errors:
            conflict_errors = (OWNERSHIP_CONFLICT,) if ownership_differs else ()
            snapshot = aggregate_ownership(
                (*existing.ownership_contributors, *candidate.ownership_contributors),
                subject_ref=f"chunk:{merged.paper_id}:{merged.chunk_id}",
                inherited_errors=(
                    *existing.ownership_error_codes,
                    *candidate.ownership_error_codes,
                    *conflict_errors,
                    *validation_errors,
                ),
                warnings=(*existing.ownership_warnings, *candidate.ownership_warnings),
                legacy_bucket="L2_conflicting_projection",
            )
            merged = replace(
                merged,
                ownership_assertion_ref=None,
                ownership_revision=None,
                ownership_basis_ref=None,
                **snapshot_fields(snapshot),
            )
        result[candidate.chunk_id] = merged
    return tuple(result.values())


def _missing_facets(
    candidates: Iterable[Candidate], facets: tuple[QueryFacet, ...]
) -> tuple[str, ...]:
    counts = {
        facet.id: sum(facet.id in candidate.facet_ids for candidate in candidates)
        for facet in facets
        if facet.required
    }
    return tuple(
        facet.id
        for facet in facets
        if facet.required and counts.get(facet.id, 0) < max(1, facet.min_evidence_blocks)
    )


def _span_candidate(span: LogicalSpan) -> Candidate:
    return Candidate(
        chunk_id=span.member_chunk_ids[0],
        paper_id=span.paper_id,
        text=span.content,
        distance=span.distance,
        admission=span.admission,
        field=span.field,
        verified_by_m6=span.verified_by_m6,
        credibility=span.credibility,
        rerank_score=span.rerank_score,
        section_path=span.section_path,
        block_type=span.block_type,
        page_start=span.page_start,
        page_end=span.page_end,
        metadata_status=span.metadata_status,
        facet_ids=span.facet_ids,
        retrieval_reasons=span.retrieval_reasons,
        source_role=span.source_role,
        member_chunk_ids=span.member_chunk_ids,
        logical_span_id=span.logical_span_id,
        logical_role=span.logical_role,
        ownership_subject_ref=span.ownership_subject_ref,
        ownership_contract_version=span.ownership_contract_version,
        ownership_aggregation_policy=span.ownership_aggregation_policy,
        data_ownership=span.data_ownership,
        ownership_assertion_ref=span.ownership_assertion_ref,
        ownership_revision=span.ownership_revision,
        ownership_basis_ref=span.ownership_basis_ref,
        ownership_mix_state=span.ownership_mix_state,
        ownership_contributors=span.ownership_contributors,
        ownership_escalated_by=span.ownership_escalated_by,
        ownership_digest=span.ownership_digest,
        ownership_resolution_status=span.ownership_resolution_status,
        ownership_error_codes=span.ownership_error_codes,
        ownership_warnings=span.ownership_warnings,
        legacy_bucket=span.legacy_bucket,
    )


def build_structured_context_pack(
    candidate_set: CandidateSet,
    question: str,
    *,
    facets: Iterable[QueryFacet] | None = None,
    retrieval_mode: str = MULTI_PAPER_SYNTHESIS,
    query_intent: str | None = None,
    card: dict[str, Any] | None = None,
    chunks: list[dict[str, Any]] | None = None,
    retry_fn: Callable[[tuple[str, ...]], CandidateSet | Iterable[Candidate]] | None = None,
    strategy: Any = None,
    token_hard_limit: int | None = None,
) -> ContextPack:
    """Build a coverage-aware pack and perform at most one controlled retry."""
    if retrieval_mode not in {SINGLE_PAPER_EXTRACTION, MULTI_PAPER_SYNTHESIS}:
        raise ValueError(f"unknown retrieval_mode: {retrieval_mode}")
    query_facets = tuple(facets) if facets is not None else detect_query_facets(question)
    candidates = list(candidate_set.candidates)
    if card is not None and chunks is not None:
        candidates.extend(build_card_anchor_candidates(card, chunks, query_facets))
    document_is_review = _is_review_card(card)
    candidates = [
        _annotate_candidate(
            candidate,
            query_facets,
            document_is_review=document_is_review,
        )
        for candidate in _dedupe(candidates)
    ]
    excluded = 0
    if not bibliography_is_eligible(query_intent):
        kept = []
        for candidate in candidates:
            if is_high_confidence_bibliography(candidate):
                excluded += 1
            else:
                kept.append(candidate)
        candidates = kept

    retry_count = 0
    missing_before = _missing_facets(candidates, query_facets)
    if missing_before and retry_fn is not None:
        retry_result = retry_fn(missing_before)
        retry_candidates = (
            tuple(retry_result.candidates)
            if isinstance(retry_result, CandidateSet)
            else tuple(retry_result)
        )
        retry_candidates = tuple(
            _annotate_candidate(
                candidate,
                query_facets,
                document_is_review=document_is_review,
            )
            for candidate in retry_candidates
        )
        if not bibliography_is_eligible(query_intent):
            retained = []
            for candidate in retry_candidates:
                if is_high_confidence_bibliography(candidate):
                    excluded += 1
                else:
                    retained.append(candidate)
            retry_candidates = tuple(retained)
        candidates = list(_dedupe((*candidates, *retry_candidates)))
        retry_count = 1

    neighbor_candidates: tuple[Candidate, ...] = ()
    logical_span_source = "recalled_candidates_only"
    if chunks is not None:
        neighbor_candidates = _controlled_neighbor_candidates(
            candidates,
            chunks,
            document_is_review=document_is_review,
        )
        candidates = list(_dedupe((*candidates, *neighbor_candidates)))
        logical_span_source = "explicit_full_chunk_index_strict_neighbors"

    spans = build_logical_spans(candidates)
    span_candidates = tuple(_span_candidate(span) for span in spans)
    packed_set = CandidateSet(
        candidates=span_candidates,
        target_active_count=max(candidate_set.target_active_count, len(span_candidates)),
        raw_recall_count=candidate_set.raw_recall_count,
        filtered_non_active_count=candidate_set.filtered_non_active_count,
        final_active_count=len(span_candidates),
        reached_overfetch_cap=candidate_set.reached_overfetch_cap,
        require_verified=candidate_set.require_verified,
        scope_mode=candidate_set.scope_mode,
        scope_paper_id=candidate_set.scope_paper_id,
    )
    from .review_qualifications import card_review_qualifications
    qualifications = (card_review_qualifications(card, paper_id=candidate_set.scope_paper_id)
                      if candidate_set.scope_mode == "paper_id" else ())
    pack = build_context_pack(
        packed_set,
        strategy,
        retrieval_mode=retrieval_mode,
        token_hard_limit=token_hard_limit,
        card_review_qualifications=qualifications,
    )
    coverage_by_facet = {}
    for facet in query_facets:
        block_ids = [
            block.logical_span_id or block.source.get("chunk_id")
            for block in pack.blocks
            if facet.id in block.facet_ids
        ]
        coverage_by_facet[facet.id] = {
            "required": facet.required,
            "satisfied": len(block_ids) >= max(1, facet.min_evidence_blocks),
            "evidence_span_ids": block_ids,
            "eligible_source_roles": sorted(_FACET_SOURCE_ROLE_RULES[facet.id])
            if facet.id in _FACET_SOURCE_ROLE_RULES
            else None,
        }
    missing = tuple(
        facet.id
        for facet in query_facets
        if facet.required and not coverage_by_facet[facet.id]["satisfied"]
    )
    coverage_status = "complete" if not missing else "partial" if pack.blocks else "insufficient"
    member_count = sum(len(block.member_chunk_ids) for block in pack.blocks)
    bibliography_count = sum(
        len(block.member_chunk_ids)
        for block in pack.blocks
        if _section_leaf(block.section_path) in _REFERENCE_SECTIONS
        and (block.block_type or "").casefold() in _BIBLIOGRAPHY_TYPES
    )
    result = replace(
        pack,
        retrieval_mode=retrieval_mode,
        query_facets=query_facets,
        coverage_status=coverage_status,
        missing_facets=missing,
        coverage_by_facet=coverage_by_facet,
        retry_count=retry_count,
        fallback_used=retry_count > 0,
        answer_complete=coverage_status == "complete",
        bibliography_share=(bibliography_count / member_count if member_count else 0.0),
        excluded_bibliography_count=excluded,
    )
    log_reason(
        "M3",
        "M3c 结构覆盖门",
        f"facets={len(query_facets)} coverage={coverage_status} "
        f"missing={len(missing)} retry={retry_count} spans={len(spans)}",
        event_category=None,
        context={
            "retrieval_mode": retrieval_mode,
            "query_facets": [facet.id for facet in query_facets],
            "coverage_status": coverage_status,
            "missing_facets": list(missing),
            "facet_source_role_rules": {
                facet.id: sorted(_FACET_SOURCE_ROLE_RULES[facet.id])
                for facet in query_facets
                if facet.id in _FACET_SOURCE_ROLE_RULES
            },
            "retry_count": retry_count,
            "logical_span_count": len(spans),
            "logical_span_source": logical_span_source,
            "logical_neighbor_count": len(neighbor_candidates),
            "logical_span_token_limit": LOGICAL_SPAN_TOKEN_LIMIT,
            "logical_neighbor_token_limit": LOGICAL_NEIGHBOR_TOKEN_LIMIT,
            "excluded_bibliography_count": excluded,
            "bibliography_share": result.bibliography_share,
        },
    )
    return result
