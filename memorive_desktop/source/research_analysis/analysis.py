"""RESEARCH_ANALYSIS 分析输出契约本体(Analysis 第 1 步)——拿 Context Pack + 问题 → 固定四段分析建议。

承 Analysis 第 0 步契约。链路:渲染 prompt(§一四段) → 组 payload → 经 MODEL_GATEWAY call("analysis")
(绝不直连 SDK;§六) → 严格解析 JSON(坏/空 fail-loud) → 模块据权威块重建 source_id
+ 假源移出文献支持段(§三 + Q2 fail-closed) → 强制四段标签 + 免责语(§二/§四) → RUNTIME_LOG 留痕(§七)。

守边界(承契约):
- 消费 Context Pack、不自检索;active ≠ 已校验(不据 admission 判可信);认真对待反例名额。
- 不造自动越界检测器(§四:越界靠 prompt 约束 + 人工抽查)。
- 假源检测是**集合成员判断**、纯确定性、**不上模型判官**(含 deepseek-flash)。
- 「真 id 但块不支撑该句」的语义误挂属 EVIDENCE_REVIEW 分析级校核(verify_judgment、Integration 接)、
  RESEARCH_ANALYSIS 只交挂了 source_id 的校核对象、不自判语义支撑(登债)。

调模型经可注入 seam model_call(默认 call("analysis"));链路健壮性(双档留痕/记账/区域门/
归属安全阀/红线)留第 2 步,本步聚焦「输出对不对」。
"""
from __future__ import annotations
from model_gateway.prompt_cache import source_first

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from runtime_log import log_reason
from retrieval.context_pack import validate_context_pack_binding
from retrieval.ownership import (
    OWNERSHIP_BINDING_MISMATCH,
    OWNERSHIP_LOCAL_ROUTE_UNAVAILABLE,
    OwnershipContractError,
    OwnershipGuard,
    OwnershipSnapshot,
    propagation_receipt,
    stable_sha256,
)
from memorive_source_language import (
    SourceLanguageDriftError,
    language_display_name,
    normalize_source_language,
    validate_generated_language,
)

from .types import (
    AnalysisRequestEnvelope,
    AnalysisResult,
    DISCLAIMER,
    FlaggedClaim,
    LiteratureClaim,
    PossiblyIrrelevantClaim,
    disclaimer_for,
)

_PROMPT_ROOT = Path(__file__).resolve().parent.parent / "model_gateway" / "prompts" / "analysis"
_PROMPT_PATHS = {
    "legacy": _PROMPT_ROOT / "v4.md",
    "source_language": _PROMPT_ROOT / "v6.md",
}

_TIMEOUT = 180          # deepseek-v4 推理模型、分析慢,给足


class AnalysisError(Exception):
    """RESEARCH_ANALYSIS 分析失败(坏/空模型响应等)——绝不静默产半份分析(承 EVIDENCE_REVIEW fail-loud 口径)。"""


class OwnershipFailClosed(AnalysisError):
    """归属安全阀挡下:他人托付 / 无权处置数据 + 本地 RESEARCH_ANALYSIS 推理槽未就绪 → 绝不静默转云(承 §七 b)。"""

    def __init__(self, message: str, *, error_code: str | None = None):
        self.error_code = error_code
        super().__init__(message)


class RegionBlocked(AnalysisError):
    """境外 API 出口区域预检门挡下(大陆出口 / 出口区域无法确认;承 §六、v6.4 之二)。"""


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
    """把 Context Pack 每块渲染给模型:chunk_id + 是否反例 + 原文。"""
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
    """组 MODEL_GATEWAY payload；max_tokens 由 analysis 槽作单一配置源。"""
    template = (_prompt_template(output_language=output_language)
                .replace("__QUESTION__", question)
                .replace("__OUTPUT_LANGUAGE__", language_display_name(output_language)))
    rendered = _render_blocks(context_pack)
    prompt = (source_first(template, "__BLOCKS__", rendered)
              if rendered.strip() else template.replace("__BLOCKS__", ""))
    from retrieval.review_qualifications import render_review_context
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
    """严格解析模型四段 JSON;坏/空/schema 非法 → AnalysisError(绝不静默产半份)。"""
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

    # possibly_irrelevant(A·修跨篇混入·RESEARCH_ANALYSIS 相关性闸):**优雅兜底**——键缺省(v2 旧响应)→ 空列表、绝不 fail-loud;
    # 键在但非列表 → fail-loud(v3 契约违反);每条 {text, source_chunk_id, reason}(reason 可缺)。
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
    """默认:经 MODEL_GATEWAY call("analysis")——绝不直连 SDK;MODEL_GATEWAY 失败 → AnalysisError。
    ⚠ 保持 analysis 语义(承第 0 步首跑硬约束):走 analysis 槽记账/区域门/留痕,不复用 distill/verify。"""
    from model_gateway import call
    from model_gateway.errors import GatewayError
    try:
        return call("analysis", payload)
    except GatewayError as e:
        raise AnalysisError(f"MODEL_GATEWAY analysis 调用失败:{type(e).__name__}: {e}") from e


# ── 归属对象绑定 + 区域门(Analysis 第2步;发模型调用前的两道闸)──────────────────────
# 区域门白名单:仅这两个 reason + not gated 才放行;gated / region_unknown / 任何将来新增未知 reason 一律默认挡。
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
    """区域门(承 §六、v6.4 之二):经 region_preflight('analysis')(由 slot.provider 驱动)——
    国内 / 本地 → domestic_skip 放行;非陆 → region_ok 放行;大陆 → gated 挡下;查不到 → 交人(均挡下、绝不静默发调用)。
    首跑 DeepSeek(国内)自动放行;升 Anthropic(境外)才真进门。"""
    from model_gateway import region_preflight
    from model_gateway.errors import GatewayError
    try:
        rc = region_preflight("analysis")
    except GatewayError as e:
        raise AnalysisError(f"RESEARCH_ANALYSIS 区域门预检失败:{type(e).__name__}: {e}") from e
    # 真 fail-closed · 白名单放行:仅「非 gated 且 reason ∈ {domestic_skip, region_ok}」放行;
    # gated / region_unknown / 任何将来新增未知 reason 一律默认挡(绝不静默放行)。
    if (not rc.get("gated")) and rc.get("reason") in _REGION_PASS_REASONS:
        return
    msg = rc.get("message") or f"区域门挡下:reason={rc.get('reason')}, gated={rc.get('gated')}"
    log_reason("RESEARCH_ANALYSIS", "RESEARCH_ANALYSIS 区域门挡下", msg, event_category=None,
               context={"region_reason": rc.get("reason"), "region": rc.get("region"),
                        "gated": rc.get("gated"), "provider": rc.get("provider")})
    raise RegionBlocked(msg)


def analyze(context_pack, question: str, constraints=None, *,
            data_ownership=None, legacy_data_ownership=None,
            ownership_route_profile=None, model_call=None,
            region_check=None, output_language: str | None = None) -> AnalysisResult:
    """RESEARCH_ANALYSIS 主入口:Context Pack + 问题 → 固定四段分析建议(建议性、非结论)。

    融合铁律 + source_id 强制 + 越界防线 + 免责语。发模型调用前依次:pack ownership binding → guard → 区域门。
    data_ownership:旧参数别名，仅 compare-only；新调用使用 legacy_data_ownership。
    model_call:可注入(默认 call("analysis") 走**自动 API 档**;注入 = **手动订阅档**/合成,不经 MODEL_GATEWAY 不记账)。
    region_check:可注入(默认查 analysis 槽 provider 属境外才 preflight;离线测各区域场景用)。
    constraints:输出约束占位(留后续)。
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

    # 先建权威块索引 + guard(加固):重复 / 缺失 / 空 chunk_id → fail-loud、**不发模型调用**。
    # source_id 是 EVIDENCE_REVIEW 下游挂载点;把"上游不出重复 chunk_id"从隐式覆盖(by_cid[cid]=block)变显式拒绝。
    # (当前 CandidateRetrieval 单次 col.query()、向量 id=chunk_id、无多查询合并,pack 内重复不该出现;此为显式护栏、非活 bug 修复。)
    by_cid = {}
    for b in getattr(context_pack, "blocks", ()) or ():
        cid = (getattr(b, "source", None) or {}).get("chunk_id")
        if not isinstance(cid, str) or not cid.strip():
            raise AnalysisError("Context Pack 有块缺失 / 空 chunk_id;source_id 无法建立,拒绝分析(fail-loud、不发模型调用)")
        if cid in by_cid:
            raise AnalysisError(f"Context Pack 出现重复 chunk_id={cid!r};source_id 挂载点会错配,拒绝分析(fail-loud、不发模型调用)")
        by_cid[cid] = b

    # 区域门:仅默认 MODEL_GATEWAY 路径(境外 API 真调用)前挡;region_check 可注入(离线测各场景)。
    # 手动档 / 合成(注入 model_call、未注入 region_check)不发真调用 → 跳过区域门。
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
    parsed = parse_analysis(text)          # 坏/空 → AnalysisError

    # possibly_irrelevant(A·RESEARCH_ANALYSIS 相关性闸兜底):先分流它，使同一 source_chunk_id 的相关性判定对
    # literature_support 有优先级。否则模型双挂同一块时会把离题块既显示为支持、又显示为离题，误导读者。
    # 不变量:possibly_irrelevant 恒可解析——cid 解析不出(假源)则归 flagged,不留在此段。
    lit, flagged, possibly_irrelevant = [], [], []
    irrelevant_chunk_ids, suppressed_literature_chunk_ids = set(), []
    for item in parsed["possibly_irrelevant"]:
        cid = item["source_chunk_id"]
        block = by_cid.get(cid) if cid is not None else None
        if block is None:                  # 模型把离题标到一个不在库的假源上 → 当假源处理(归 flagged)
            flagged.append(FlaggedClaim(text=item["text"], claimed_chunk_id=cid))
        else:
            possibly_irrelevant.append(PossiblyIrrelevantClaim(
                text=item["text"], source_id=dict(block.source), reason=item.get("reason")))
            irrelevant_chunk_ids.add(cid)

    for item in parsed["literature_support"]:
        cid = item["source_chunk_id"]
        block = by_cid.get(cid) if cid is not None else None
        if block is None:                  # 无效/假源(不在给定块内 或 没挂)→ 移出文献支持段(Q2)
            flagged.append(FlaggedClaim(text=item["text"], claimed_chunk_id=cid))
        elif cid in irrelevant_chunk_ids: # 相关性闸优先：同源离题块不得仍充当文献支持
            suppressed_literature_chunk_ids.append(cid)
        else:                              # 权威重建 source_id(据块、非模型自报)
            lit.append(LiteratureClaim(
                text=item["text"],
                source_id=dict(block.source),
                origin=_block_origin(block),
                facet_ids=_block_facets(block),
            ))

    analysis_model = (resp or {}).get("model") or (
        "(unknown)" if language == "en" else "(未知)"
    )
    mode = "manual" if model_call is not None else "auto"   # 双档由路径定:注入 model_call=手动订阅/合成(不经 MODEL_GATEWAY 不记账);默认=自动 API 档(经 MODEL_GATEWAY 记账)。「manual:」前缀仅作 analysis_model 命名规范
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
    from retrieval.review_qualifications import review_limitations
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

    # RUNTIME_LOG 留痕(§七):响亮记假源计数(承 Q2)。
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
                   # A(修跨篇混入):相关性闸留痕(离题块移出文献支持、单列疑似不相关)
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
