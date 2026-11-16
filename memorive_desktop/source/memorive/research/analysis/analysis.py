"""The analysis of one paper: a context pack and a question become suggestions in four fixed sections.

The steps: render the prompt, build the payload, call the model through the gateway
(call("analysis"), never an SDK), parse the JSON strictly (a broken or empty reply raises), rebuild
each sentence's source_id from the given blocks and move sentences with an unknown source out of
the literature section, add the section labels and the disclaimer, write a reason record.

What it does not do:
- It reads the context pack and never retrieves on its own; active does not mean reviewed.
- It has no automatic check for overreach; the prompt and spot checks by people cover that.
- Finding a made-up source is a set membership test, without a model.
- Whether a real block actually supports its sentence is for the card review's judgment step;
  this module hands over the sentences with their source_id.

The model call can be injected (model_call; by default call("analysis")). Before the call the
pack's data ownership is bound, the ownership guard runs and, for an overseas provider, the
region check.
"""
from __future__ import annotations
from memorive.models.gateway.prompt_cache import source_first
from memorive.models.gateway.prompts import PROMPT_DIR

import json
from dataclasses import replace
from datetime import datetime

from memorive.platform.logs import log_reason
from memorive.research.retrieval.context_pack import validate_context_pack_binding
from memorive.research.retrieval.ownership import (
    OWNERSHIP_BINDING_MISMATCH,
    OWNERSHIP_LOCAL_ROUTE_UNAVAILABLE,
    OwnershipContractError,
    OwnershipGuard,
    OwnershipSnapshot,
    propagation_receipt,
    stable_sha256,
)
from memorive.platform.language.source_preservation import (
    SourceLanguageDriftError,
    language_display_name,
    normalize_source_language,
    validate_generated_language,
)

from memorive.research.analysis.types import (
    AnalysisRequestEnvelope,
    AnalysisResult,
    DISCLAIMER,
    FlaggedClaim,
    LiteratureClaim,
    PossiblyIrrelevantClaim,
    disclaimer_for,
)

_PROMPT_ROOT = PROMPT_DIR / "analysis"
_PROMPT_PATHS = {
    "legacy": _PROMPT_ROOT / "v4.md",
    "source_language": _PROMPT_ROOT / "v6.md",
}

_TIMEOUT = 180          # a reasoning model; analyses take long


class AnalysisError(Exception):
    """The analysis failed (a broken or empty model reply, say); half an analysis is never returned."""


class OwnershipFailClosed(AnalysisError):
    """Stopped for data ownership: the data is entrusted or not the user's to send, and no local
    analysis model is ready, so it is never sent to the cloud instead."""

    def __init__(self, message: str, *, error_code: str | None = None):
        self.error_code = error_code
        super().__init__(message)


class RegionBlocked(AnalysisError):
    """Stopped by the region check before an overseas API (a mainland exit, or the region is unknown)."""


_PROMPT_CACHE: dict[str, str] = {}
_ALLOWED_ORIGINS = {
    "direct_method",
    "direct_result",
    "author_interpretation",
    "secondary_citation_reported_by_paper",
    "analysis_inference",
}
_RETRIEVAL_GAP_PHRASES = (
    "no data",
    "no explicit",
    "not report",
    "not available",
    "missing",
    "supplied evidence",
    "retrieved evidence",
    "context pack",
)


def _prompt_template(*, output_language: str | None = None) -> str:
    key = "source_language" if output_language is not None else "legacy"
    if key not in _PROMPT_CACHE:
        _PROMPT_CACHE[key] = _PROMPT_PATHS[key].read_text(encoding="utf-8")
    return _PROMPT_CACHE[key]


def _render_blocks(context_pack) -> str:
    """Each block of the context pack as the model sees it: chunk_id, whether it is a counterexample, the text."""
    lines = []
    for b in getattr(context_pack, "blocks", ()) or ():
        cid = (getattr(b, "source", None) or {}).get("chunk_id")
        ce = "true" if getattr(b, "is_counterexample", False) else "false"
        lines.append(f"[chunk_id={cid}] is_counterexample={ce}\n{b.content}")
    return "\n\n".join(lines)


def _block_origin(block) -> str:
    source = getattr(block, "source", None) or {}
    value = getattr(block, "source_role", None) or source.get("source_role")
    return value if value in _ALLOWED_ORIGINS else "unknown"


def _block_facets(block) -> tuple[str, ...]:
    source = getattr(block, "source", None) or {}
    values = getattr(block, "facet_ids", None) or source.get("facet_ids") or ()
    return tuple(str(item) for item in values if item)


def _looks_like_retrieval_gap(text: str, missing_facets: tuple[str, ...]) -> bool:
    folded = text.casefold()
    facet_terms = {
        term
        for facet in missing_facets
        for term in (facet.casefold(), *facet.casefold().split("_"))
        if len(term) >= 4
    }
    return bool(
        facet_terms
        and any(term in folded for term in facet_terms)
        and any(phrase in folded for phrase in _RETRIEVAL_GAP_PHRASES)
    )


def _analysis_quality_metrics(literature_claims, by_cid: dict, context_pack) -> dict:
    chunk_ids = [claim.source_id.get("chunk_id") for claim in literature_claims]
    spans = []
    sections = []
    origins = []
    for claim in literature_claims:
        cid = claim.source_id.get("chunk_id")
        block = by_cid.get(cid)
        spans.append(getattr(block, "logical_span_id", None) or cid)
        section = getattr(block, "section_path", None)
        if section:
            sections.append(section)
        origins.append(claim.origin)
    count = len(literature_claims)
    span_counts = {}
    for span_id in spans:
        span_counts[span_id] = span_counts.get(span_id, 0) + 1
    max_from_one = max(span_counts.values(), default=0)
    coverage = getattr(context_pack, "coverage_by_facet", None) or {}
    covered = sum(
        bool(item.get("satisfied"))
        for item in coverage.values()
        if isinstance(item, dict) and item.get("required", True)
    )
    missing = sum(
        not bool(item.get("satisfied"))
        for item in coverage.values()
        if isinstance(item, dict) and item.get("required", True)
    )
    partial = sum(
        item.get("status") == "partial"
        for item in coverage.values()
        if isinstance(item, dict)
    )
    direct = sum(origin in {"direct_result", "direct_method"} for origin in origins)
    return {
        "literature_claim_count": count,
        "unique_chunk_count": len(set(chunk_ids)),
        "unique_logical_span_count": len(set(spans)),
        "unique_section_count": len(set(sections)),
        "direct_result_claim_count": origins.count("direct_result"),
        "author_interpretation_claim_count": origins.count("author_interpretation"),
        "secondary_citation_claim_count": origins.count(
            "secondary_citation_reported_by_paper"
        ),
        "direct_answer_ratio": direct / count if count else 0.0,
        "subquestion_coverage": {
            "covered": covered,
            "partial": partial,
            "missing": missing,
        },
        "source_dominance": {
            "max_claims_from_one_span": max_from_one,
            "max_share_from_one_span": max_from_one / count if count else 0.0,
        },
        "max_source_dominance": max_from_one / count if count else 0.0,
        "bibliography_share": float(
            getattr(context_pack, "bibliography_share", 0.0) or 0.0
        ),
    }


def build_payload(
    context_pack,
    question: str,
    *,
    output_language: str | None = None,
) -> dict:
    """The gateway payload; max_tokens comes from the analysis slot only."""
    template = (_prompt_template(output_language=output_language)
                .replace("__QUESTION__", question)
                .replace("__OUTPUT_LANGUAGE__", language_display_name(output_language)))
    rendered = _render_blocks(context_pack)
    prompt = (source_first(template, "__BLOCKS__", rendered)
              if rendered.strip() else template.replace("__BLOCKS__", ""))
    from memorive.research.retrieval.review_qualifications import render_review_context
    review_context = render_review_context(getattr(context_pack, "card_review_qualifications", ()) or ())
    if review_context:
        prompt += "\n\n" + review_context
    return {"messages": [{"role": "user", "content": prompt}],
            "timeout": _TIMEOUT}


def _strip_fence(body: str) -> str:
    body = body.strip()
    if body.startswith("```"):
        body = body.strip("`")
        if body[:4].lower() == "json":
            body = body[4:]
    return body.strip()


def _require_str_list(data: dict, key: str) -> list:
    v = data.get(key)
    if not isinstance(v, list):
        raise AnalysisError(f"响应缺 {key} 或非列表(得 {type(v).__name__})")
    out = []
    for i, x in enumerate(v):
        if not isinstance(x, str):
            raise AnalysisError(f"{key}[{i}] 非字符串")
        if x.strip():
            out.append(x.strip())
    return out


def parse_analysis(text) -> dict:
    """Parse the model's four-section JSON strictly; a broken, empty or invalid reply raises AnalysisError."""
    if not isinstance(text, str) or not text.strip():
        raise AnalysisError("模型响应为空 / 非字符串")
    try:
        data = json.loads(_strip_fence(text))
    except (json.JSONDecodeError, ValueError) as e:
        raise AnalysisError(f"模型响应非合法 JSON:{e}") from e
    if not isinstance(data, dict):
        raise AnalysisError("模型响应非 JSON 对象")

    ls_raw = data.get("literature_support")
    if not isinstance(ls_raw, list):
        raise AnalysisError("响应缺 literature_support 或非列表")
    lit = []
    for i, item in enumerate(ls_raw):
        if not isinstance(item, dict):
            raise AnalysisError(f"literature_support[{i}] 非对象")
        t = item.get("text")
        if not isinstance(t, str) or not t.strip():
            raise AnalysisError(f"literature_support[{i}].text 空 / 非字符串")
        cid = item.get("source_chunk_id")
        if cid is not None and not isinstance(cid, str):
            raise AnalysisError(f"literature_support[{i}].source_chunk_id 须为字符串或缺省")
        lit.append({"text": t.strip(),
                    "source_chunk_id": cid.strip() if isinstance(cid, str) and cid.strip() else None})

    # possibly_irrelevant: a missing key (an older reply) is an empty list; a key that is not a list
    # raises. Each item is {text, source_chunk_id, reason}, reason optional.
    pi_raw = data.get("possibly_irrelevant")
    pi = []
    if pi_raw is not None:
        if not isinstance(pi_raw, list):
            raise AnalysisError("响应 possibly_irrelevant 非列表")
        for i, item in enumerate(pi_raw):
            if not isinstance(item, dict):
                raise AnalysisError(f"possibly_irrelevant[{i}] 非对象")
            t = item.get("text")
            if not isinstance(t, str) or not t.strip():
                raise AnalysisError(f"possibly_irrelevant[{i}].text 空 / 非字符串")
            cid = item.get("source_chunk_id")
            if cid is not None and not isinstance(cid, str):
                raise AnalysisError(f"possibly_irrelevant[{i}].source_chunk_id 须为字符串或缺省")
            reason = item.get("reason")
            pi.append({"text": t.strip(),
                       "source_chunk_id": cid.strip() if isinstance(cid, str) and cid.strip() else None,
                       "reason": reason.strip() if isinstance(reason, str) and reason.strip() else None})

    return {
        "literature_support": lit,
        "possibly_irrelevant": pi,
        "ai_general_knowledge": _require_str_list(data, "ai_general_knowledge"),
        "risks_and_uncertainties": _require_str_list(data, "risks_and_uncertainties"),
        "recheck_locations": _require_str_list(data, "recheck_locations"),
    }


def _default_model_call(payload: dict) -> dict:
    """The default model call: call("analysis") through the gateway; a gateway failure raises
    AnalysisError. It uses the analysis slot (its cost record, region check and log), not distill or verify."""
    from memorive.models.gateway import call
    from memorive.models.gateway.errors import GatewayError
    try:
        return call("analysis", payload)
    except GatewayError as e:
        raise AnalysisError(f"MODEL_GATEWAY analysis 调用失败:{type(e).__name__}: {e}") from e


# ── ownership binding and the region check, before the model call ─────────────────────
# Only these two reasons pass, and only when not gated; gated, region_unknown and any future reason stop.
_REGION_PASS_REASONS = ("domestic_skip", "region_ok")


def build_analysis_request_envelope(
    context_pack,
    *,
    legacy_data_ownership=None,
    route_profile=None,
) -> AnalysisRequestEnvelope:
    """Bind the immutable pack snapshot before prompt rendering.

    ``legacy_data_ownership`` is compare-only.  It cannot establish authority
    for an old pack and cannot change the effective value of a native pack.
    """
    validate_context_pack_binding(context_pack)
    snapshot = OwnershipGuard.validate(context_pack)
    legacy_status = OwnershipGuard.compare_legacy(snapshot, legacy_data_ownership)
    preflight = OwnershipGuard.preflight(snapshot, route_profile)
    payload = {
        "context_pack_ref": context_pack.context_pack_ref,
        "context_pack_hash": context_pack.context_pack_hash,
        "ownership_snapshot": snapshot.to_dict(),
        "ownership_binding_status": legacy_status,
        "required_route_class": preflight.required_route_class,
        "preflight_status": preflight.preflight_status,
        "preflight_error_codes": list(preflight.error_codes),
    }
    digest = stable_sha256(payload)
    return AnalysisRequestEnvelope(
        envelope_ref=f"ARE-{digest.removeprefix('sha256:')[:24]}",
        envelope_hash=digest,
        context_pack_ref=context_pack.context_pack_ref,
        context_pack_hash=context_pack.context_pack_hash,
        ownership_snapshot=snapshot.to_dict(),
        ownership_binding_status=legacy_status,
        required_route_class=preflight.required_route_class,
        preflight_status=preflight.preflight_status,
        preflight_error_codes=preflight.error_codes,
    )


def analysis_binding_payload(
    analysis_result,
    *,
    snapshot: OwnershipSnapshot | None = None,
) -> dict:
    """Canonical non-recursive payload for the immutable Analysis hash."""
    if snapshot is None:
        raw = getattr(analysis_result, "ownership_snapshot", None)
        if not isinstance(raw, dict):
            raise OwnershipContractError(
                OWNERSHIP_BINDING_MISMATCH,
                "AnalysisResult lacks an ownership snapshot for hashing",
            )
        snapshot = OwnershipGuard.validate(OwnershipSnapshot.from_dict(raw))
    return {
        **({"review_limitations": tuple(analysis_result.review_limitations)}
           if getattr(analysis_result, "review_limitations", ()) else {}),
        "question": getattr(analysis_result, "question", None),
        "literature_support": tuple(getattr(analysis_result, "literature_support", ()) or ()),
        "ai_general_knowledge": tuple(getattr(analysis_result, "ai_general_knowledge", ()) or ()),
        "risks_and_uncertainties": tuple(getattr(analysis_result, "risks_and_uncertainties", ()) or ()),
        "recheck_locations": tuple(getattr(analysis_result, "recheck_locations", ()) or ()),
        "flagged_unsourced": tuple(getattr(analysis_result, "flagged_unsourced", ()) or ()),
        "disclaimer": getattr(analysis_result, "disclaimer", None),
        "analysis_model": getattr(analysis_result, "analysis_model", None),
        "analyzed_at": getattr(analysis_result, "analyzed_at", None),
        "possibly_irrelevant": tuple(getattr(analysis_result, "possibly_irrelevant", ()) or ()),
        "coverage_status": getattr(analysis_result, "coverage_status", None),
        "missing_facets": tuple(getattr(analysis_result, "missing_facets", ()) or ()),
        "answer_complete": getattr(analysis_result, "answer_complete", None),
        "study_limitations": tuple(getattr(analysis_result, "study_limitations", ()) or ()),
        "source_internal_inconsistencies": tuple(
            getattr(analysis_result, "source_internal_inconsistencies", ()) or ()
        ),
        "retrieval_limitations": tuple(getattr(analysis_result, "retrieval_limitations", ()) or ()),
        "quality_metrics": getattr(analysis_result, "quality_metrics", None),
        "coverage_assessment_ref": getattr(analysis_result, "coverage_assessment_ref", None),
        "coverage_assessment_hash": getattr(analysis_result, "coverage_assessment_hash", None),
        "context_pack_ref": getattr(analysis_result, "input_context_pack_ref", None),
        "context_pack_hash": getattr(analysis_result, "input_context_pack_hash", None),
        "ownership_digest": snapshot.ownership_digest,
        "ownership_binding_status": getattr(analysis_result, "ownership_binding_status", None),
        "ownership_legacy_binding_status": getattr(
            analysis_result, "ownership_legacy_binding_status", None
        ),
        "ownership_request_envelope_ref": getattr(
            analysis_result, "ownership_request_envelope_ref", None
        ),
        "ownership_request_envelope_hash": getattr(
            analysis_result, "ownership_request_envelope_hash", None
        ),
        "ownership_required_route_class": getattr(
            analysis_result, "ownership_required_route_class", None
        ),
        "ownership_preflight_status": getattr(
            analysis_result, "ownership_preflight_status", None
        ),
    }


def _raise_ownership_fail_closed(exc: OwnershipContractError, *, stage: str) -> None:
    log_reason(
        "RESEARCH_ANALYSIS",
        "RESEARCH_ANALYSIS ownership fail-closed",
        f"stage={stage} code={exc.code}",
        event_category=None,
        context={"stage": stage, "error_code": exc.code, "details": exc.details},
    )
    raise OwnershipFailClosed(
        f"RESEARCH_ANALYSIS ownership fail-closed:{exc}", error_code=exc.code
    ) from exc


def _default_region_check() -> None:
    """The region check, region_preflight('analysis'), decided by slot.provider: domestic or local
    passes (domestic_skip), another country passes (region_ok), mainland China stops (gated), an
    unknown region stops too and is left to the user. DeepSeek is domestic; Anthropic is overseas."""
    from memorive.models.gateway import region_preflight
    from memorive.models.gateway.errors import GatewayError
    try:
        rc = region_preflight("analysis")
    except GatewayError as e:
        raise AnalysisError(f"RESEARCH_ANALYSIS 区域门预检失败:{type(e).__name__}: {e}") from e
    # Only "not gated and reason in {domestic_skip, region_ok}" passes; gated, region_unknown and any
    # reason added later stop the call.
    if (not rc.get("gated")) and rc.get("reason") in _REGION_PASS_REASONS:
        return
    msg = rc.get("message") or f"区域门挡下:reason={rc.get('reason')}, gated={rc.get('gated')}"
    log_reason("RESEARCH_ANALYSIS", "RESEARCH_ANALYSIS 区域门挡下", msg, event_category=None,
               context={"region_reason": rc.get("reason"), "region": rc.get("region"),
                        "gated": rc.get("gated"), "provider": rc.get("provider")})
    raise RegionBlocked(msg)


def analyze(context_pack, question: str, *,
            data_ownership=None, legacy_data_ownership=None,
            ownership_route_profile=None, model_call=None,
            region_check=None, output_language: str | None = None) -> AnalysisResult:
    """Context pack and question -> an analysis in four sections (suggestions, not conclusions).

    Before the model call: the pack's ownership binding, the ownership guard, the region check.
    data_ownership: the old name of the parameter, compared only; new callers pass
      legacy_data_ownership.
    model_call: by default call("analysis"), the automatic API path with a cost record; an injected
      call is the manual (subscription) path or a test, without the gateway or a cost record.
    region_check: by default the check runs when the analysis slot's provider is overseas; tests
      inject one.
    constraints: reserved for output constraints.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("RESEARCH_ANALYSIS: 问题为空(fail-loud、不发空 prompt)")

    if data_ownership is not None and legacy_data_ownership is not None:
        if data_ownership != legacy_data_ownership:
            _raise_ownership_fail_closed(
                OwnershipContractError(
                    OWNERSHIP_BINDING_MISMATCH,
                    "deprecated and successor legacy ownership arguments disagree",
                ),
                stage="legacy_argument_compare",
            )
    legacy_value = (
        legacy_data_ownership
        if legacy_data_ownership is not None
        else data_ownership
    )
    try:
        ownership_envelope = build_analysis_request_envelope(
            context_pack,
            legacy_data_ownership=legacy_value,
            route_profile=ownership_route_profile,
        )
    except OwnershipContractError as exc:
        _raise_ownership_fail_closed(exc, stage="request_envelope")

    if ownership_envelope.preflight_error_codes:
        _raise_ownership_fail_closed(
            OwnershipContractError(
                ownership_envelope.preflight_error_codes[0],
                "ownership route preflight is blocked",
                details={
                    "required_route_class": ownership_envelope.required_route_class,
                    "preflight_status": ownership_envelope.preflight_status,
                },
            ),
            stage="route_preflight",
        )
    if (
        ownership_envelope.required_route_class in {"local_only", "local_only_unknown"}
        and model_call is None
    ):
        _raise_ownership_fail_closed(
            OwnershipContractError(
                OWNERSHIP_LOCAL_ROUTE_UNAVAILABLE,
                "a qualified local ownership profile cannot use the default MODEL_GATEWAY route",
            ),
            stage="model_seam",
        )

    # Index the blocks by chunk_id first: a repeated, missing or empty chunk_id raises before any
    # model call. source_id is what the card review attaches to, so a repeated chunk_id is refused
    # rather than silently overwritten (retrieval makes one query keyed by chunk_id, so a pack
    # should not have one).
    by_cid = {}
    for b in getattr(context_pack, "blocks", ()) or ():
        cid = (getattr(b, "source", None) or {}).get("chunk_id")
        if not isinstance(cid, str) or not cid.strip():
            raise AnalysisError("Context Pack 有块缺失 / 空 chunk_id;source_id 无法建立,拒绝分析(fail-loud、不发模型调用)")
        if cid in by_cid:
            raise AnalysisError(f"Context Pack 出现重复 chunk_id={cid!r};source_id 挂载点会错配,拒绝分析(fail-loud、不发模型调用)")
        by_cid[cid] = b

    # The region check runs only on the default gateway path (a real overseas call); tests inject
    # region_check. The manual path and tests (model_call injected, region_check not) send no real
    # call and skip it.
    if region_check is not None:
        region_check()
    elif model_call is None:
        _default_region_check()

    language = (
        normalize_source_language(output_language)
        if output_language is not None
        else None
    )
    source_text = "\n".join(
        str(getattr(block, "content", "") or "")
        for block in getattr(context_pack, "blocks", ()) or ()
    )
    if language is not None:
        try:
            validate_generated_language(
                source_text=source_text,
                generated_text=question,
                source_language=language,
                artifact_name="Analysis question",
            )
        except SourceLanguageDriftError as exc:
            raise AnalysisError(str(exc)) from exc

    payload = build_payload(context_pack, question, output_language=language)
    resp = (model_call or _default_model_call)(payload)
    text = (resp or {}).get("text") or ""
    parsed = parse_analysis(text)          # broken or empty: AnalysisError

    # possibly_irrelevant first, so its verdict on a source_chunk_id wins over literature_support:
    # otherwise a block the model cited both ways would show as support and as off topic. Every
    # possibly_irrelevant item resolves; one with an unknown chunk id goes to flagged.
    lit, flagged, possibly_irrelevant = [], [], []
    irrelevant_chunk_ids, suppressed_literature_chunk_ids = set(), []
    for item in parsed["possibly_irrelevant"]:
        cid = item["source_chunk_id"]
        block = by_cid.get(cid) if cid is not None else None
        if block is None:                  # off topic but citing an unknown chunk: flagged
            flagged.append(FlaggedClaim(text=item["text"], claimed_chunk_id=cid))
        else:
            possibly_irrelevant.append(PossiblyIrrelevantClaim(
                text=item["text"], source_id=dict(block.source), reason=item.get("reason")))
            irrelevant_chunk_ids.add(cid)

    for item in parsed["literature_support"]:
        cid = item["source_chunk_id"]
        block = by_cid.get(cid) if cid is not None else None
        if block is None:                  # no source, or not a given block: out of the literature section
            flagged.append(FlaggedClaim(text=item["text"], claimed_chunk_id=cid))
        elif cid in irrelevant_chunk_ids: # a block marked off topic cannot also support
            suppressed_literature_chunk_ids.append(cid)
        else:                              # source_id rebuilt from the block, not taken from the model
            lit.append(LiteratureClaim(
                text=item["text"],
                source_id=dict(block.source),
                origin=_block_origin(block),
                facet_ids=_block_facets(block),
            ))

    analysis_model = (resp or {}).get("model") or (
        {"zh":"(未知)","ja":"(不明)"}.get(language, "(unknown)")
    )
    mode = "manual" if model_call is not None else "auto"   # an injected model_call is manual (no gateway, no cost record); the "manual:" prefix only names analysis_model
    analyzed_at = datetime.now().astimezone().isoformat(timespec="minutes")
    coverage_status = str(getattr(context_pack, "coverage_status", "not_assessed"))
    missing_facets = tuple(getattr(context_pack, "missing_facets", ()) or ())
    study_limitations, moved_retrieval_gaps = [], []
    for risk in parsed["risks_and_uncertainties"]:
        if _looks_like_retrieval_gap(risk, missing_facets):
            moved_retrieval_gaps.append(risk)
        else:
            study_limitations.append(risk)
    retrieval_limitations = [
        f"Missing retrieval evidence for required facet: {facet}."
        for facet in missing_facets
    ]
    retrieval_limitations.extend(moved_retrieval_gaps)
    source_inconsistencies = tuple(
        getattr(context_pack, "source_internal_inconsistencies", ()) or ()
    )
    ai_general = (
        ()
        if coverage_status in {"partial", "insufficient"}
        else tuple(parsed["ai_general_knowledge"])
    )
    quality_metrics = _analysis_quality_metrics(lit, by_cid, context_pack)
    ownership_snapshot = OwnershipSnapshot.from_dict(ownership_envelope.ownership_snapshot)
    from memorive.research.retrieval.review_qualifications import review_limitations
    review_limits = review_limitations(getattr(context_pack, "card_review_qualifications", ()) or (), language)
    result = AnalysisResult(
        question=question.strip(),
        review_limitations=review_limits,
        literature_support=tuple(lit),
        ai_general_knowledge=ai_general,
        risks_and_uncertainties=tuple(study_limitations),
        recheck_locations=tuple(parsed["recheck_locations"]),
        flagged_unsourced=tuple(flagged),
        disclaimer=(disclaimer_for(language) if language is not None else DISCLAIMER),
        analysis_model=analysis_model,
        analyzed_at=analyzed_at,
        possibly_irrelevant=tuple(possibly_irrelevant),
        coverage_status=coverage_status,
        missing_facets=missing_facets,
        answer_complete=(False if review_limits else (coverage_status == "complete"
                         if coverage_status != "not_assessed" else None)),
        study_limitations=tuple(study_limitations),
        source_internal_inconsistencies=source_inconsistencies,
        retrieval_limitations=tuple(dict.fromkeys(retrieval_limitations)),
        quality_metrics=quality_metrics,
        input_context_pack_ref=ownership_envelope.context_pack_ref,
        input_context_pack_hash=ownership_envelope.context_pack_hash,
        ownership_snapshot=ownership_snapshot.to_dict(),
        ownership_binding_status="matched",
        ownership_legacy_binding_status=ownership_envelope.ownership_binding_status,
        ownership_request_envelope_ref=ownership_envelope.envelope_ref,
        ownership_request_envelope_hash=ownership_envelope.envelope_hash,
        ownership_required_route_class=ownership_envelope.required_route_class,
        ownership_preflight_status=ownership_envelope.preflight_status,
    )
    if language is not None:
        try:
            validate_generated_language(
                source_text=source_text,
                generated_text=result.render(language=language),
                source_language=language,
                artifact_name="Analysis",
            )
        except SourceLanguageDriftError as exc:
            raise AnalysisError(str(exc)) from exc
    analysis_hash = stable_sha256(analysis_binding_payload(result, snapshot=ownership_snapshot))
    analysis_ref = f"A-{analysis_hash.removeprefix('sha256:')[:24]}"
    receipt = propagation_receipt(
        ownership_snapshot,
        subject_chain={
            "context_pack": {
                "context_pack_ref": ownership_envelope.context_pack_ref,
                "context_pack_hash": ownership_envelope.context_pack_hash,
                "ownership_digest": ownership_snapshot.ownership_digest,
            },
            "analysis_request_envelope": {
                "envelope_ref": ownership_envelope.envelope_ref,
                "envelope_hash": ownership_envelope.envelope_hash,
            },
            "analysis_result": {
                "analysis_ref": analysis_ref,
                "analysis_hash": analysis_hash,
                "ownership_digest": ownership_snapshot.ownership_digest,
            },
        },
        checks=("PACK_TO_ENVELOPE_MATCH", "ENVELOPE_TO_ANALYSIS_MATCH"),
    )
    result = replace(
        result,
        analysis_ref=analysis_ref,
        analysis_hash=analysis_hash,
        ownership_propagation_receipt=receipt.to_dict(),
    )

    # The reason record, with the count of sentences without a valid source.
    log_reason("RESEARCH_ANALYSIS", "RESEARCH_ANALYSIS 分析",
               f"mode={mode} model={analysis_model} lit={len(lit)} ai={len(result.ai_general_knowledge)} "
               f"risk={len(result.risks_and_uncertainties)} recheck={len(result.recheck_locations)} "
               f"flagged_unsourced={len(flagged)} possibly_irrelevant={len(possibly_irrelevant)}"
               f"{' ⚠假源已移出文献支持段' if flagged else ''}"
               f"{' ⚠疑似不相关已移出文献支持段' if possibly_irrelevant else ''}",
               event_category=None, context={
                   "analysis_model": analysis_model, "mode": mode,
                   "effective_data_ownership": ownership_snapshot.effective_data_ownership,
                   "ownership_mix_state": ownership_snapshot.ownership_mix_state,
                   "ownership_digest": ownership_snapshot.ownership_digest,
                   "legacy_ownership_binding": ownership_envelope.ownership_binding_status,
                   "required_route_class": ownership_envelope.required_route_class,
                   "ownership_preflight_status": ownership_envelope.preflight_status,
                   "literature_support_count": len(lit),
                   "ai_general_count": len(result.ai_general_knowledge),
                   "risks_count": len(result.risks_and_uncertainties),
                   "recheck_count": len(result.recheck_locations),
                   "flagged_unsourced_count": len(flagged),
                   "flagged_claimed_chunk_ids": [c.claimed_chunk_id for c in flagged],
                   # off-topic sentences, moved out of the literature section
                   "possibly_irrelevant_count": len(possibly_irrelevant),
                   "possibly_irrelevant_chunk_ids": [c.source_id.get("chunk_id") for c in possibly_irrelevant],
                   "suppressed_literature_support_count": len(suppressed_literature_chunk_ids),
                   "suppressed_literature_support_chunk_ids": suppressed_literature_chunk_ids,
                   "block_count": len(by_cid),
                   "question": question.strip(),
                   "coverage_status": coverage_status,
                   "missing_facets": list(missing_facets),
                   "answer_complete": result.answer_complete,
                   "retrieval_limitations_count": len(result.retrieval_limitations),
                   "quality_metrics": quality_metrics,
               })
    return result
