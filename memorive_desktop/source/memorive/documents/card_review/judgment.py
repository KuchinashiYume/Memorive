"""The judgment check of the card review: a second model (GPT) tries to refute an analysis conclusion.

GPT takes the opposite side and looks for conclusions their evidence does not carry (overstated,
distorted, without evidence, beyond scope). It is given only the conclusion and the chunks the
conclusion cites, never the whole paper and never the reasoning, through the gateway's
verify_judgment slot.

It never asks the model that wrote the conclusion to check itself, keeps no long-term statistics, and
never changes a card's state: it checks analysis conclusions, not cards. Two layers: first the
mechanical check that the cited chunk ids exist, then GPT. verify_research_analysis connects it to the
analysis output (AnalysisResult).
"""
from __future__ import annotations
from memorive.models.gateway.prompt_cache import source_first
from memorive.models.gateway.prompts import PROMPT_DIR

import json
import re
from dataclasses import asdict, dataclass, field

from memorive.documents.card_states import DecisionError    # a broken or empty reply is a step failure, never "no dispute"
from memorive.platform.logs import log_reason
from memorive.research.retrieval.ownership import (
    OWNERSHIP_BINDING_MISMATCH,
    OWNERSHIP_LOCAL_ROUTE_UNAVAILABLE,
    OwnershipContractError,
)
from memorive.documents.card_review.judgment_debt import record_pending_judgment
from memorive.documents.card_review.ownership_binding import build_ownership_review_task

from memorive.documents.card_review.errors import OwnershipFailClosed        # the data ownership check before chunk text goes to GPT
from memorive.platform.language.source_preservation import language_display_name, normalize_source_language

_PROMPT_ROOT = PROMPT_DIR / "verify"
_PROMPT_PATHS = {
    "legacy": _PROMPT_ROOT / "judgment_cross_source_v2.md",
    "source_language": _PROMPT_ROOT / "judgment_cross_source_v3.md",
}
_LEGACY_DISPUTE_TYPES = frozenset({"夸大", "曲解", "无依据", "超范围"})
_SOURCE_LANGUAGE_DISPUTE_TYPES = frozenset(
    {"overstatement", "distortion", "unsupported", "out_of_scope"}
)
_REGION_PASS_REASONS = ("domestic_skip", "region_ok")   # the reasons that pass the region check

# The six signals: any one sends the conclusion to GPT (manual_override is the hand switch)
_GRADE_SIGNALS = ("will_publish", "core_data", "return_to_library",
                  "low_confidence", "has_number_or_causal", "manual_override")
_JUDGMENT_TASK_ORDER = ("verify_judgment", "verify_judgment_fallback")
_ALREADY_IN_RESEARCH_ANALYSIS_CHAIN = frozenset({"anthropic", "deepseek"})
_PROVIDER_PREFIXES = (("claude", "anthropic"), ("gpt", "openai"), ("deepseek", "deepseek"))


@dataclass
class Conclusion:
    """A conclusion to check: text, the chunks it cites (cited_chunk_ids, taken exactly) and the six
    signals that decide whether it is sent."""
    text: str
    cited_chunk_ids: list = field(default_factory=list)
    will_publish: bool = False          # a key conclusion that will be published
    core_data: bool = False             # core data
    return_to_library: bool = False     # it will flow back into the library (knowledge feedback)
    low_confidence: bool = False        # the analysis marked it low confidence or thinly supported
    has_number_or_causal: bool = False  # a number or a causal claim
    manual_override: bool = False       # the user wants this one checked


@dataclass(frozen=True)
class VerifierCandidate:
    task_type: str
    provider: str
    model_id: str | None
    enabled: bool

    def trace(self) -> dict:
        return {"task_type": self.task_type, "provider": self.provider,
                "model_id": self.model_id, "enabled": self.enabled}


def should_gpt_verify(conclusion) -> tuple:
    """Whether to send it: any of the six signals gives (True, None); otherwise (False, skip_reason) to
    save quota, the reason saying why it was not sent."""
    hits = [s for s in _GRADE_SIGNALS if getattr(conclusion, s, False)]
    if hits:
        return True, None
    return False, ("一次性问答 / 纯描述性陈述:6 信号(will_publish / core_data / return_to_library / "
                   "low_confidence / has_number_or_causal / manual_override)均 false → 跳过 GPT 校核、省额度")


def _provider_for_model(model_id: str | None) -> str | None:
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    model = model_id.strip().lower()
    for prefix, provider in _PROVIDER_PREFIXES:
        if model.startswith(prefix):
            return provider
    return None


def select_judgment_candidates(producer_model: str | None) -> tuple[str | None, list[VerifierCandidate]]:
    """Pick configured verifiers that are provably different from the RESEARCH_ANALYSIS producer."""
    producer_provider = _provider_for_model(producer_model)
    if producer_provider is None:
        return None, []

    from memorive.models.gateway.config import MODELS_YAML, GatewayConfig
    from memorive.models.gateway.errors import UnknownTaskType

    config = GatewayConfig(MODELS_YAML)
    ordered = []
    for order, task_type in enumerate(_JUDGMENT_TASK_ORDER):
        try:
            slot = config.slot(task_type)
        except UnknownTaskType:
            continue
        if not slot.provider or slot.provider == producer_provider:
            continue
        ordered.append((slot.provider in _ALREADY_IN_RESEARCH_ANALYSIS_CHAIN, order,
                        VerifierCandidate(task_type=task_type, provider=slot.provider,
                                          model_id=slot.model_id, enabled=slot.enabled)))
    ordered.sort(key=lambda item: (item[0], item[1]))
    return producer_provider, [candidate for _known, _order, candidate in ordered]


_PROMPT_CACHE: dict[str, str] = {}
def _prompt_template(*, output_language: str | None = None) -> str:
    key = "source_language" if output_language is not None else "legacy"
    if key not in _PROMPT_CACHE:
        _PROMPT_CACHE[key] = _PROMPT_PATHS[key].read_text(encoding="utf-8")
    return _PROMPT_CACHE[key]


def _render_cited(cited: dict) -> str:
    return "\n\n".join(f"[{cid}] {txt}" for cid, txt in cited.items())


def build_gpt_payload(
    conclusion,
    chunk_texts: dict,
    *,
    output_language: str | None = None,
) -> dict:
    """What GPT is given: the conclusion and the chunks it cites, taken exactly by cited_chunk_ids even
    when chunk_texts holds the whole paper; never the whole paper, never the reasoning."""
    cited = {cid: chunk_texts.get(cid, "") for cid in conclusion.cited_chunk_ids}
    template = (_prompt_template(output_language=output_language)
                .replace("__CONCLUSION__", conclusion.text)
                .replace("__OUTPUT_LANGUAGE__", language_display_name(output_language)))
    prompt = source_first(template, "__CITED_CHUNKS__", _render_cited(cited))
    payload = {"messages": [{"role": "user", "content": prompt}], "max_tokens": 4096, "timeout": 90}
    if output_language is not None:
        payload['response_contract'] = 'judgment_source_language_v1'
    return payload


def parse_gpt_disputes(text, *, output_language: str | None = None) -> dict:
    """Parse GPT's reply into {agrees: bool, disputes: [{type, detail, quote}]}. A broken, empty or
    invalid reply raises DecisionError and never counts as "no dispute"; type must be one of the four
    dispute types and detail must not be empty."""
    if not isinstance(text, str) or not text.strip():
        raise DecisionError("GPT 异源响应为空 / 非字符串(不当无异议)")
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[4:].strip() if body[:4].lower() == "json" else body.strip()
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError) as e:
        raise DecisionError(f"GPT 异源响应非合法 JSON:{e}(不当无异议)") from e
    if not isinstance(data, dict) or not isinstance(data.get("agrees"), bool) or not isinstance(data.get("disputes"), list):
        raise DecisionError("GPT 异源响应缺 agrees(bool)/ disputes(list)(不当无异议)")
    allowed_types = (
        _SOURCE_LANGUAGE_DISPUTE_TYPES
        if output_language is not None
        else _LEGACY_DISPUTE_TYPES
    )
    out = []
    for i, d in enumerate(data["disputes"]):
        if not isinstance(d, dict):
            raise DecisionError(f"disputes[{i}] 非对象")
        if d.get("type") not in allowed_types:
            raise DecisionError(
                f"disputes[{i}].type invalid; expected one of "
                f"{sorted(allowed_types)}, got {d.get('type')!r}"
            )
        detail = d.get("detail")
        if not isinstance(detail, str) or not detail.strip():
            raise DecisionError(f"disputes[{i}].detail 空 / 非字符串")
        q = d.get("quote")
        if q is not None and not isinstance(q, str):
            raise DecisionError(f"disputes[{i}].quote 须为字符串或缺省")
        out.append({"type": d["type"], "detail": detail.strip(), "quote": q})
    agrees = data["agrees"]
    # agrees=true needs no disputes, agrees=false at least one; a contradiction raises DecisionError
    if agrees and out:
        raise DecisionError("GPT 异源响应矛盾:agrees=true 却带 disputes(不静默接受)")
    if not agrees and not out:
        raise DecisionError("GPT 异源响应矛盾:agrees=false 却无 disputes(不静默接受)")
    return {"agrees": agrees, "disputes": out}


def _default_region_check(task_type: str = "verify_judgment") -> None:
    """The region check before calling GPT (overseas): region_preflight('verify_judgment'), the same as
    for the analysis and the transcription check. Mainland China or an unknown region raises
    DecisionError (retried or escalated) instead of running into a 403."""
    from memorive.models.gateway import region_preflight
    from memorive.models.gateway.errors import GatewayError
    try:
        rc = region_preflight(task_type)
    except GatewayError as e:
        raise DecisionError(f"EVIDENCE_REVIEW 判断类区域门预检失败(可重跑):{type(e).__name__}: {e}") from e
    if (not rc.get("gated")) and rc.get("reason") in _REGION_PASS_REASONS:
        return
    msg = rc.get("message") or f"区域门挡下:reason={rc.get('reason')}, gated={rc.get('gated')}"
    raise DecisionError(f"EVIDENCE_REVIEW 判断类区域门挡下(不硬撞 403、交人 / 重跑):{msg}")


def _default_verifier_call(task_type: str, payload: dict) -> str:
    """The real check: the gateway's verify_judgment slot (GPT); a gateway failure raises DecisionError."""
    from memorive.models.gateway import call
    from memorive.models.gateway.errors import GatewayError
    try:
        resp = call(task_type, payload)
    except GatewayError as e:
        raise DecisionError(f"MODEL_GATEWAY {task_type} 调用失败:{type(e).__name__}: {e}") from e
    return resp.get("text") or ""


def _default_gpt_call(payload: dict) -> str:
    """Compatibility wrapper for the original GPT judgment slot."""
    return _default_verifier_call("verify_judgment", payload)


def _judgment_local_ready() -> bool:
    """Whether a local model for judgments (other than the one that wrote the conclusion) is ready.
    There is none, judgments always go to GPT overseas, so this is always False and entrusted or
    unclear data always stops. Built like transcription._evidence_review_local_ready."""
    return False


def _judgment_ownership_gate(data_ownership) -> None:
    """The data ownership check: a judgment sends chunk text to GPT overseas, like the transcription
    check sends it to another vendor, so anything not clearly the user's own stops. Built like
    transcription._ownership_gate: self passes; entrusted would pass with a ready local judgment model
    (there is none); anything else (entrusted, missing, unknown, None) raises OwnershipFailClosed before
    any GPT call."""
    if data_ownership == "self":
        return                                             # self: the user's own data may go to GPT
    if data_ownership == "entrusted" and _judgment_local_ready():
        return                                             # a ready local judgment model (there is none)
    msg = (f"判断类校核:数据归属非明确 self(得 {data_ownership!r};entrusted / 缺失 / 未知一律挡)+ "
           "本地判断类校核槽未就绪 → fail-closed;绝不把他人托付 / 归属不明原文送 GPT(openai)境外云")
    log_reason("EVIDENCE_REVIEW", "EVIDENCE_REVIEW 判断类归属安全阀挡下", msg, event_category="verify", target="")
    raise OwnershipFailClosed(msg)


def judgment_verify(conclusion, chunk_texts: dict, *, gpt_call=None, region_check=None,
                    data_ownership=None, verify_task_type: str = "verify_judgment",
                    output_language: str | None = None, force_review: bool = False) -> dict:
    """Check one conclusion with GPT, in two layers:
    1. mechanical: do all cited chunk ids exist? If not, stop without sending.
    2. the six signals decide whether to send; a skip carries skip_reason.
    3. on the real gateway path (gpt_call not injected) the region check runs first; a test double skips it.
    4. send only the conclusion and its cited chunks, and parse the disputes.
    Returns a dict (with stage, gpt_sent, payload_sent, so tests can check what was sent). It changes
    no card state. region_check can be injected; by default it runs when the slot's provider is overseas.
    data_ownership: anything but an explicit 'self' raises OwnershipFailClosed before any call, real or test."""
    _judgment_ownership_gate(data_ownership)               # ownership first, before anything else
    cited = conclusion.cited_chunk_ids
    if not cited:                                       # no citation: nothing to check against; not sent
        return {"stage": "mechanical_failed", "gpt_sent": False, "broken_chunk_ids": [],
                "note": "结论无 cited_chunk_ids(无依据可证伪),机械层拦下、不送 GPT"}
    # The mechanical layer: a broken citation or an empty chunk text means nothing is sent.
    bad = [c for c in cited if c not in chunk_texts or not (chunk_texts.get(c) or "").strip()]
    if bad:
        return {"stage": "mechanical_failed", "gpt_sent": False, "broken_chunk_ids": bad,
                "note": "结论引用的 chunk_id 未 resolve 或原文为空 / 空白,机械层拦下、不送 GPT(不发空 prompt)"}
    send, skip_reason = should_gpt_verify(conclusion)   # the six signals
    if not send and not force_review:
        return {"stage": "graded_skip", "gpt_sent": False, "skip_reason": skip_reason}
    payload = build_gpt_payload(
        conclusion, chunk_texts, output_language=output_language
    )   # only the conclusion and its cited chunks
    if region_check is not None:                           # the region check before a real call (an injected gpt_call skips it)
        region_check()
    elif gpt_call is None:
        _default_region_check(verify_task_type)
    resp = (gpt_call or (lambda body: _default_verifier_call(verify_task_type, body)))(payload)
    parsed = parse_gpt_disputes(
        resp, output_language=output_language
    )                                                      # a bad reply raises DecisionError
    return {"stage": "gpt_verified", "gpt_sent": True, "conclusion": conclusion.text,
            "agrees": parsed["agrees"], "disputes": parsed["disputes"], "payload_sent": payload,
            "verifier_task_type": verify_task_type}


# ── from analysis conclusions to the judgment check ──────────────────────────────
# It changes no card state and never quarantines a source card: it checks analysis conclusions, not cards.
_NUM_RE = re.compile(r"\d")
_CAUSAL_WORDS = ("因", "导致", "使得", "从而", "因此", "由于", "促使", "造成", "提高", "增加", "降低",
                 "减少", "改善", "增强", "抑制", "达到", "取决于", "influence", "cause", "lead", "result",
                 "increase", "decrease", "improve", "reduce", "enhance", "due to", "because")


def _failure(candidate: VerifierCandidate, exc: DecisionError) -> dict:
    return {"verifier": candidate.trace(), "error_type": type(exc).__name__,
            "reason": str(exc)[:500]}


def _verify_with_candidates(conclusion, chunk_texts: dict, candidates: list[VerifierCandidate], *,
                            data_ownership, gpt_call, verifier_call, region_check,
                            output_language=None, force_review=False):
    failures = []
    for candidate in candidates:
        if not candidate.enabled:
            failures.append({"verifier": candidate.trace(), "error_type": "SlotDisabled",
                             "reason": "configured slot is disabled"})
            continue
        try:
            def candidate_call(payload, current=candidate):
                if verifier_call is not None:
                    return verifier_call(current.task_type, payload)
                if gpt_call is not None:
                    if current.task_type == "verify_judgment":
                        return gpt_call(payload)
                    raise DecisionError("injected GPT call has no fallback verifier implementation")
                return _default_verifier_call(current.task_type, payload)

            def candidate_region_check(current=candidate):
                if region_check is not None:
                    region_check()
                else:
                    _default_region_check(current.task_type)

            result = judgment_verify(
                conclusion, chunk_texts, data_ownership=data_ownership,
                gpt_call=candidate_call, region_check=candidate_region_check,
                verify_task_type=candidate.task_type,
                output_language=output_language,
                force_review=force_review,
            )
        except DecisionError as exc:
            failures.append(_failure(candidate, exc))
            continue
        result["verifier"] = candidate.trace()
        return result, candidate, failures
    return None, None, failures


def _deferred_ruling(
    index: int,
    claim,
    reason: str,
    failures: list,
    *,
    output_language: str | None = None,
) -> dict:
    source_id = dict(getattr(claim, "source_id", None) or {})
    language = (
        "zh" if output_language is None else normalize_source_language(output_language)
    )
    status = {
        "zh": "未异源核·待核",
        "en": "cross-model review pending",
        "ja": "異種モデル検証待ち",
    }.get(language, "cross-model review pending")
    return {
        "i": index,
        "stage": "cross_source_pending",
        "gpt_sent": False,
        "status": status,
        "reason": reason,
        "text": getattr(claim, "text", "") or "",
        "source_id": source_id,
        "failures": failures,
    }


def _has_number_or_causal(text: str) -> bool:
    """A rough test for a number or a causal claim in the conclusion (a signal; erring wide sends more, the safe side)."""
    t = text or ""
    return bool(_NUM_RE.search(t)) or any(w in t for w in _CAUSAL_WORDS)


def conclusion_from_claim(claim, *, will_publish=False, manual_override=False) -> Conclusion:
    """A literature sentence of the analysis (LiteratureClaim: text and source_id {chunk_id, paper_id,
    field}) as a Conclusion citing exactly its source, not neighbouring chunks. The analysis is not changed."""
    sid = getattr(claim, "source_id", None) or {}
    cid = sid.get("chunk_id")
    members = sid.get("member_chunk_ids")
    if members is None:
        cited = [cid] if cid else []
    else:
        paper_id = sid.get("paper_id")
        if (not isinstance(members, (list, tuple)) or not members
                or members[0] != cid or not isinstance(paper_id, str) or not paper_id
                or any(not isinstance(member, str) or not member.startswith(paper_id + "#")
                       for member in members)
                or len(set(members)) != len(members)):
            raise DecisionError("ANALYSIS_CITED_MEMBERS_INVALID")
        cited = list(members)
    text = getattr(claim, "text", "") or ""
    return Conclusion(text=text, cited_chunk_ids=cited,
                      has_number_or_causal=_has_number_or_causal(text),
                      will_publish=will_publish, manual_override=manual_override)


def verify_research_analysis(analysis_result, chunk_texts: dict, *, data_ownership=None,
                       legacy_data_ownership=None, ownership_route_profile=None,
                       target=None, max_gpt: int = 2, gpt_call=None,
                       verifier_call=None, region_check=None,
                       candidate_resolver=None,
                       output_language: str | None = None,
                       claim_indexes=None) -> dict:
    """An AnalysisResult -> a judgment report. It changes no card.
    data_ownership must be 'self' explicitly; the default None stops (OwnershipFailClosed) rather than
      raising TypeError, and anything else stops before any GPT call. Still open: checking each
      source's card ownership by source_id.paper_id instead of trusting the caller.
    1. Mechanical: each literature sentence's source_id.chunk_id must resolve to a given chunk.
    2. Up to max_gpt conclusions with a number or causal claim and a resolving source go to GPT.
    3. Each verdict targets the analysis sentence; a weak sentence does not mean its source card should
       be quarantined, so source cards are untouched."""
    if data_ownership is not None and legacy_data_ownership is not None:
        if data_ownership != legacy_data_ownership:
            raise OwnershipFailClosed(
                f"{OWNERSHIP_BINDING_MISMATCH}: deprecated and successor legacy ownership arguments disagree"
            )
    legacy_value = (
        legacy_data_ownership
        if legacy_data_ownership is not None
        else data_ownership
    )
    try:
        ownership_task = build_ownership_review_task(
            analysis_result,
            legacy_data_ownership=legacy_value,
            route_profile=ownership_route_profile,
        )
    except OwnershipContractError as exc:
        log_reason(
            "EVIDENCE_REVIEW", "EVIDENCE_REVIEW Analysis ownership binding blocked",
            f"code={exc.code}", event_category="verify", target="",
            context={"error_code": exc.code, "details": exc.details},
        )
        raise OwnershipFailClosed(f"{exc.code}: {exc}") from exc
    if ownership_task.preflight_error_codes:
        code = ownership_task.preflight_error_codes[0]
        raise OwnershipFailClosed(
            f"{code}: EVIDENCE_REVIEW ownership route preflight blocked before any verifier seam"
        )
    effective_ownership = ownership_task.ownership_snapshot.get("effective_data_ownership")
    if effective_ownership != "self":
        raise OwnershipFailClosed(
            f"{OWNERSHIP_LOCAL_ROUTE_UNAVAILABLE}: EVIDENCE_REVIEW judgment local route is not implemented; "
            "qualified policy metadata alone cannot activate it"
        )
    _judgment_ownership_gate(effective_ownership)
    claims = list(getattr(analysis_result, "literature_support", ()) or ())
    if claim_indexes is not None:
        if (not isinstance(claim_indexes, (list, tuple)) or not claim_indexes
                or any(type(i) is not int or not 0 <= i < len(claims) for i in claim_indexes)
                or len(set(claim_indexes)) != len(claim_indexes)):
            raise ValueError("ANALYSIS_DELTA_REVIEW_INDEX_INVALID")
        selected_indexes = set(claim_indexes)
    else:
        selected_indexes = None
    producer_model = getattr(analysis_result, "analysis_model", None)
    if candidate_resolver is None:
        producer_provider, candidates = select_judgment_candidates(producer_model)
    else:
        selection = candidate_resolver(producer_model)
        if (
            not isinstance(selection, tuple)
            or len(selection) != 2
            or not isinstance(selection[1], list)
            or any(not isinstance(candidate, VerifierCandidate) for candidate in selection[1])
        ):
            raise DecisionError("EVIDENCE_REVIEW 判断类候选解析器返回非法结构")
        producer_provider, candidates = selection
    # source_id is rebuilt from Context Pack blocks by RESEARCH_ANALYSIS and already covered
    # by the immutable Analysis ownership/content binding above.
    conclusions = [conclusion_from_claim(c) for c in claims]
    trace = []
    from memorive.platform.progress import scope
    for i, c in enumerate(claims):
        sid = getattr(c, "source_id", None) or {}
        cid = sid.get("chunk_id")
        cited = conclusions[i].cited_chunk_ids
        resolved = bool(cited) and all(
            member in chunk_texts and bool((chunk_texts.get(member) or "").strip())
            for member in cited)
        trace.append({"i": i, "chunk_id": cid, "paper_id": sid.get("paper_id"),
                      "field": sid.get("field"), "cited_chunk_ids": list(cited),
                      "resolved": resolved})
    picked, rulings, deferred, failures = [], [], [], []
    active_candidates = list(candidates)
    selected_units = [i for i,c in enumerate(claims) if (selected_indexes is None or i in selected_indexes) and trace[i]["resolved"] and (selected_indexes is not None or _has_number_or_causal(getattr(c,"text","")))][:max_gpt]
    with scope('JUDGMENT_REVIEW', selected_units) as node_units:
        for i, c in enumerate(claims):
            if len(picked) >= max_gpt:
                break
            if selected_indexes is not None and i not in selected_indexes:
                continue
            if trace[i]["resolved"] and (selected_indexes is not None or _has_number_or_causal(getattr(c, "text", ""))):
                picked.append(i)
                if active_candidates:
                    res, selected, attempt_failures = _verify_with_candidates(
                        conclusions[i], chunk_texts, active_candidates,
                        data_ownership=effective_ownership, gpt_call=gpt_call,
                        verifier_call=verifier_call, region_check=region_check,
                        output_language=output_language,
                        force_review=selected_indexes is not None,
                    )
                    failures.extend(attempt_failures)
                else:
                    res, selected = None, None
                if res is not None:
                    rulings.append({"i": i, **res})
                    if res.get("gpt_sent"):
                        node_units.complete(i)
                    active_candidates = active_candidates[active_candidates.index(selected):]
                else:
                    reason = ("producer provider is unknown; cannot prove verifier is cross-source"
                              if producer_provider is None else
                              "all eligible cross-source verifiers are unavailable")
                    pending = _deferred_ruling(
                        i, c, reason, failures, output_language=output_language
                    )
                    deferred.append(pending)
                    rulings.append(pending)
                    active_candidates = []
    target_info = target or {"kind": "research_analysis", "question": getattr(analysis_result, "question", None),
                             "analysis_model": producer_model}
    debt = None
    if deferred:
        debt = record_pending_judgment(
            target=target_info, producer_model=producer_model, producer_provider=producer_provider,
            deferred=deferred, failures=failures,
        )
    report = {
        "target": target_info,
        "trace_total": len(claims), "trace_resolved": sum(1 for t in trace if t["resolved"]),
        "trace": trace, "gpt_picked": picked, "rulings": rulings,
        "selection": {"producer_model": producer_model, "producer_provider": producer_provider,
                      "candidates": [candidate.trace() for candidate in candidates],
                      "strategy": "exclude producer; prefer provider absent from current RESEARCH_ANALYSIS chain"},
        "deferred": deferred, "debt": debt,
        "review_scope": {"kind": "EXACT_CHANGED_CLAIMS" if selected_indexes is not None else "EXISTING_REPRESENTATIVE_CLAIMS",
                         "requested_indexes": sorted(selected_indexes) if selected_indexes is not None else None},
        "ownership_review_task": asdict(ownership_task),
        "ownership_propagation_receipt": ownership_task.ownership_propagation_receipt,
        "drives_knowledge_admission": False,   # judgments never change card states
    }
    log_reason("EVIDENCE_REVIEW", "judgment_verify_research_analysis",
               f"判断类校核 RESEARCH_ANALYSIS 分析:溯源 {report['trace_resolved']}/{len(claims)} resolve;异源候选送 {len(picked)} 条"
               f"(异议 {sum(1 for r in rulings if r.get('gpt_sent') and not r.get('agrees', True))}/{len(rulings)});不驱动 KNOWLEDGE_ADMISSION",
               event_category="verify", target=str((report['target'] or {}).get('question') or ''),
               context={"producer_model": producer_model, "producer_provider": producer_provider,
                        "candidates": [candidate.trace() for candidate in candidates],
                        "selected_tasks": [r.get("verifier", {}).get("task_type") for r in rulings
                                           if r.get("verifier")],
                        "deferred_count": len(deferred),
                        "ownership_digest": ownership_task.ownership_snapshot.get("ownership_digest"),
                        "ownership_review_task_ref": ownership_task.review_task_ref})
    if deferred:
        log_reason(
            "EVIDENCE_REVIEW", "EVIDENCE_REVIEW 判断类异源核搁置",
            f"{len(deferred)} 条代表结论未异源核·待核；所有异源候选不可用，分析继续且不驱动 KNOWLEDGE_ADMISSION",
            event_category="verify", target=str((report["target"] or {}).get("question") or ""),
            context={"debt": debt, "producer_model": producer_model,
                     "producer_provider": producer_provider,
                     "candidate_count": len(candidates), "failure_count": len(failures),
                     "deferred_indexes": [item["i"] for item in deferred]},
        )
    return report
