"""EVIDENCE_REVIEW 判断类校核 · GPT 异源定向证伪(Verification 第 3 步)。

对 **Claude 的分析结论**做异源证伪:GPT 站对立面挑「依据撑不起结论」(夸大/曲解/无依据/超范围)。
护城河(最要紧)——**只给「结论 + 该结论引用的那几个 chunk」,严禁整篇、严禁推理链**;经 MODEL_GATEWAY 调 verify_judgment 槽(GPT)、绝不直连。

⚠ **真实对象待 Analysis/Integration**:判断类真实对象 = RESEARCH_ANALYSIS 分析结论,**RESEARCH_ANALYSIS(Analysis)未建** → 本步搭机制 + prompt 契约 + 分级,
用**构造测试结论**验通机制,**真实分析结论校核端到端待 Analysis/Integration**、不写成已端到端。

守边界:**绝不自检**(必须换 GPT、不是让原模型自看)、**经 MODEL_GATEWAY 不直连**、**不做 MODEL_EVALUATION 长期统计**、机制层不驱动 KNOWLEDGE_ADMISSION
(KNOWLEDGE_ADMISSION 是卡片准入闸门;判断类校的是 RESEARCH_ANALYSIS 分析结论、非卡片)。机械 + 语义双层:先机械层校结论引用的
chunk_id resolve、再 GPT 语义层。
"""
from __future__ import annotations
from model_gateway.prompt_cache import source_first

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from knowledge_admission import DecisionError    # 坏/空响应 = 环节失败信号(绝不当「无异议」)
from runtime_log import log_reason
from retrieval.ownership import (
    OWNERSHIP_BINDING_MISMATCH,
    OWNERSHIP_LOCAL_ROUTE_UNAVAILABLE,
    OwnershipContractError,
)
from .judgment_debt import record_pending_judgment
from .ownership_binding import build_ownership_review_task

from .errors import OwnershipFailClosed        # 判断类归属安全阀(Integration第2步复审:补 GPT 送原文出境的洞)
from memorive_source_language import language_display_name, normalize_source_language

_PROMPT_ROOT = Path(__file__).resolve().parent.parent / "model_gateway" / "prompts" / "verify"
_PROMPT_PATHS = {
    "legacy": _PROMPT_ROOT / "judgment_cross_source_v2.md",
    "source_language": _PROMPT_ROOT / "judgment_cross_source_v3.md",
}
_LEGACY_DISPUTE_TYPES = frozenset({"夸大", "曲解", "无依据", "超范围"})
_SOURCE_LANGUAGE_DISPUTE_TYPES = frozenset(
    {"overstatement", "distortion", "unsupported", "out_of_scope"}
)
_REGION_PASS_REASONS = ("domestic_skip", "region_ok")   # 区域门白名单放行(承 RESEARCH_ANALYSIS/EVIDENCE_REVIEW 同套门)

# 分级 6 信号(承决策 D):任一 true → 送 GPT;含手动开关 manual_override + return_to_library
_GRADE_SIGNALS = ("will_publish", "core_data", "return_to_library",
                  "low_confidence", "has_number_or_causal", "manual_override")
_JUDGMENT_TASK_ORDER = ("verify_judgment", "verify_judgment_fallback")
_ALREADY_IN_RESEARCH_ANALYSIS_CHAIN = frozenset({"anthropic", "deepseek"})
_PROVIDER_PREFIXES = (("claude", "anthropic"), ("gpt", "openai"), ("deepseek", "deepseek"))


@dataclass
class Conclusion:
    """判断类校核对象(**构造测试用**;真实来自 RESEARCH_ANALYSIS/Analysis、未建)。
    text=Claude 分析结论;cited_chunk_ids=该结论引用的 chunk(精确取);6 个分级信号。"""
    text: str
    cited_chunk_ids: list = field(default_factory=list)
    will_publish: bool = False          # 会写进论文的关键结论
    core_data: bool = False             # 核心数据
    return_to_library: bool = False     # 将回流入库(KNOWLEDGE_FEEDBACK)
    low_confidence: bool = False        # Claude 自标低置信 / 依据单薄
    has_number_or_causal: bool = False  # 含具体数字 / 因果断言
    manual_override: bool = False       # 手动开关「这条我要用,送 GPT 复核」


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
    """分级(承决策 D):6 信号任一 true → (True, None) 送 GPT;否则 (False, skip_reason) 跳过省额度。
    **跳过必返回 skip_reason**(可审计:为什么没送 GPT)。"""
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

    from model_gateway.config import GatewayConfig
    from model_gateway.errors import UnknownTaskType

    config_path = Path(__file__).resolve().parent.parent / "model_gateway" / "models.yaml"
    config = GatewayConfig(config_path)
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
    """组装发给 GPT 的输入(**护城河**):**只含「结论 + 该结论引用的那几个 chunk」**。
    按 cited_chunk_ids **精确取**(即便 chunk_texts 里有整篇,也只放引用的那几段)——**不含整篇、不含推理链**。"""
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
    """解析 GPT 异源响应 → {agrees: bool, disputes:[{type,detail,quote}]}。
    坏/空/schema 非法响应 → DecisionError(**绝不静默当「无异议」**);type 只能 夸大/曲解/无依据/超范围、detail 非空。"""
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
    # 守卫①:一致性——agrees=true 必空 disputes;agrees=false 必至少一条。矛盾 → DecisionError(不静默接受)
    if agrees and out:
        raise DecisionError("GPT 异源响应矛盾:agrees=true 却带 disputes(不静默接受)")
    if not agrees and not out:
        raise DecisionError("GPT 异源响应矛盾:agrees=false 却无 disputes(不静默接受)")
    return {"agrees": agrees, "disputes": out}


def _default_region_check(task_type: str = "verify_judgment") -> None:
    """判断类区域门(承 Integration第2步②):openai(境外)发 GPT 调用前必过 region_preflight('verify_judgment')——
    大陆挡、非陆放、查不到交人;挡下 → DecisionError(不硬撞 403、可重跑 / 升级)。复用 RESEARCH_ANALYSIS/EVIDENCE_REVIEW 同一套区域门。"""
    from model_gateway import region_preflight
    from model_gateway.errors import GatewayError
    try:
        rc = region_preflight(task_type)
    except GatewayError as e:
        raise DecisionError(f"EVIDENCE_REVIEW 判断类区域门预检失败(可重跑):{type(e).__name__}: {e}") from e
    if (not rc.get("gated")) and rc.get("reason") in _REGION_PASS_REASONS:
        return
    msg = rc.get("message") or f"区域门挡下:reason={rc.get('reason')}, gated={rc.get('gated')}"
    raise DecisionError(f"EVIDENCE_REVIEW 判断类区域门挡下(不硬撞 403、交人 / 重跑):{msg}")


def _default_verifier_call(task_type: str, payload: dict) -> str:
    """真实异源校核:经 MODEL_GATEWAY 调 verify_judgment 槽(GPT)。MODEL_GATEWAY 依赖失败 → DecisionError。**绝不直连 SDK。**"""
    from model_gateway import call
    from model_gateway.errors import GatewayError
    try:
        resp = call(task_type, payload)
    except GatewayError as e:
        raise DecisionError(f"MODEL_GATEWAY {task_type} 调用失败:{type(e).__name__}: {e}") from e
    return resp.get("text") or ""


def _default_gpt_call(payload: dict) -> str:
    """Compatibility wrapper for the original GPT judgment slot."""
    return _default_verifier_call("verify_judgment", payload)


def _judgment_local_ready() -> bool:
    """本地判断类异源校核槽(本地异源判据模型 ≠ 生成结论的 Claude)是否就绪。
    Core:无本地判断类模型、判断类恒走 GPT(openai 境外)→ **恒 False**(entrusted / 归属不明恒 fail-closed);
    未来若落地本地异源判据模型可启用(债)。与 transcription._evidence_review_local_ready 同构、同接入 gate 条件。"""
    return False


def _judgment_ownership_gate(data_ownership) -> None:
    """判断类归属安全阀(Integration第2步复审·补 f850cba 洞):判断类把 chunk 原文发 GPT(openai 境外),
    与搬运类 Haiku 同样是「送原文出境」——**非明确 self 一律 fail-closed**(对齐 distill / EVIDENCE_REVIEW 转录阀口径,
    不信调用方声明为 self 之外的任何值)。**结构与 transcription._ownership_gate 对齐**:self 放行 /
    entrusted+本地判断类槽就绪放行(Core 恒 False、走不到)/ 其余(entrusted 未就绪 · 缺失 · 未知 · None)恒挡。
    抛 OwnershipFailClosed(不驱动 KNOWLEDGE_ADMISSION、GPT 一次不许调)。"""
    if data_ownership == "self":
        return                                             # self:自有数据 → GPT 异源校核 OK
    if data_ownership == "entrusted" and _judgment_local_ready():
        return                                             # (未来)本地判断类槽就绪 → entrusted 可本地校核;Core 恒 False、走不到
    msg = (f"判断类校核:数据归属非明确 self(得 {data_ownership!r};entrusted / 缺失 / 未知一律挡)+ "
           "本地判断类校核槽未就绪 → fail-closed;绝不把他人托付 / 归属不明原文送 GPT(openai)境外云")
    log_reason("EVIDENCE_REVIEW", "EVIDENCE_REVIEW 判断类归属安全阀挡下", msg, event_category="verify", target="")
    raise OwnershipFailClosed(msg)


def judgment_verify(conclusion, chunk_texts: dict, *, gpt_call=None, region_check=None,
                    data_ownership=None, verify_task_type: str = "verify_judgment",
                    output_language: str | None = None, force_review: bool = False) -> dict:
    """判断类 GPT 异源校核。机械 + 语义双层:
    ① 机械层:结论引用的 chunk_id 都 resolve 吗?否 → 拦下、不送 GPT。
    ② 分级:6 信号判是否送 GPT;跳过带 skip_reason。
    ③ 区域门:真 MODEL_GATEWAY 路径(未注入 gpt_call)发 GPT 前必过 region_preflight('verify_judgment');注入 mock 跳过。
    ④ 送 GPT:payload 只含结论 + 引用 chunk(护城河)→ 解析异议。
    返回 dict(含 stage / gpt_sent / payload_sent 便于护城河断言);**不驱动 KNOWLEDGE_ADMISSION**(校的是 RESEARCH_ANALYSIS 分析结论、非卡片)。
    region_check 可注入(离线测各区域场景);默认查 verify_judgment 槽 provider 属境外才 preflight。
    data_ownership:**归属安全阀**——非明确 'self'(entrusted / 缺失 / 未知 / None)→ 在任何 GPT / mock 调用前 fail-closed
      (OwnershipFailClosed),绝不把原文送 GPT 境外云;判断类无本地路故 self 外皆挡。"""
    _judgment_ownership_gate(data_ownership)               # ① 归属安全阀:最先、在机械层 / 建 payload / 区域门 / GPT 之前
    cited = conclusion.cited_chunk_ids
    if not cited:                                       # 守卫③:无任何引用 → 无依据可证伪,不把无依据结论发给 GPT
        return {"stage": "mechanical_failed", "gpt_sent": False, "broken_chunk_ids": [],
                "note": "结论无 cited_chunk_ids(无依据可证伪),机械层拦下、不送 GPT"}
    # 双层·机械层先过 + 守卫②:引用断链 或 chunk 文本为空 / 全空白 → 不送 GPT(与第 2 步「无 chunk 不发空 prompt」一致)
    bad = [c for c in cited if c not in chunk_texts or not (chunk_texts.get(c) or "").strip()]
    if bad:
        return {"stage": "mechanical_failed", "gpt_sent": False, "broken_chunk_ids": bad,
                "note": "结论引用的 chunk_id 未 resolve 或原文为空 / 空白,机械层拦下、不送 GPT(不发空 prompt)"}
    send, skip_reason = should_gpt_verify(conclusion)   # 分级
    if not send and not force_review:
        return {"stage": "graded_skip", "gpt_sent": False, "skip_reason": skip_reason}
    payload = build_gpt_payload(
        conclusion, chunk_texts, output_language=output_language
    )   # 护城河:只含结论 + 引用 chunk
    if region_check is not None:                           # 区域门:真调用前必过(承②;mock 注入 gpt_call 则跳过)
        region_check()
    elif gpt_call is None:
        _default_region_check(verify_task_type)
    resp = (gpt_call or (lambda body: _default_verifier_call(verify_task_type, body)))(payload)
    parsed = parse_gpt_disputes(
        resp, output_language=output_language
    )                                                      # 坏响应 → DecisionError(不当无异议)
    return {"stage": "gpt_verified", "gpt_sent": True, "conclusion": conclusion.text,
            "agrees": parsed["agrees"], "disputes": parsed["disputes"], "payload_sent": payload,
            "verifier_task_type": verify_task_type}


# ── RESEARCH_ANALYSIS 分析结论 → 判断类校核 glue(Integration第2步② 归位;接 RESEARCH_ANALYSIS 输出 → judgment 入口)────────────
# 红线:**不驱动 KNOWLEDGE_ADMISSION、不改任何卡 review_status、不 reverse-quarantine 来源卡**(判断类校的是 RESEARCH_ANALYSIS 分析结论、非卡片)。
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
    """粗判结论是否「含具体数字 / 因果断言」(分级信号;偏宽 = 多送 GPT 更安全)。"""
    t = text or ""
    return bool(_NUM_RE.search(t)) or any(w in t for w in _CAUSAL_WORDS)


def conclusion_from_claim(claim, *, will_publish=False, manual_override=False) -> Conclusion:
    """RESEARCH_ANALYSIS 文献支持句(LiteratureClaim:text + source_id{chunk_id,paper_id,field})→ 判断类 Conclusion。
    引用既有权威 source_id 的完整成员；不扩展到未被该逻辑块引用的邻段。**不改 RESEARCH_ANALYSIS 产物。**"""
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
    """接 RESEARCH_ANALYSIS AnalysisResult → 判断类校核报告(Integration第2步② 归位)。**不驱动 KNOWLEDGE_ADMISSION、不碰任何卡**(红线)。
    **data_ownership 归属安全阀**:须显式声明 'self' 才放行;**默认 None → 非 self → 优雅 fail-closed(OwnershipFailClosed)**
      (复审[1]:默认 None 而非必填,漏传得 fail-closed 挡下而非 TypeError 崩;仍绝不放行非 self)。非明确 self → 在任何
      GPT 调用前 fail-closed,绝不把他人托付 / 归属不明原文送 GPT 境外云。⚠ **债**:本轮用调用方显式声明 (a) 兜底;更稳的
      「逐条 source_id.paper_id 回查各来源卡 data_ownership、任一非 self 即挡」(b) 待 paper_id→卡解析现成后补(不信调用方)。
    ① 溯源校验(机械层):每句文献支持的 source_id.chunk_id 是否 resolve 到给定 chunk 原文;
    ② 选至多 max_gpt 条「含数字 / 因果 且 溯源挂得上」的代表结论,送 GPT 异源证伪(护城河:只结论 + 引用 chunk);
    ③ 逐条裁决 + target 指向 RESEARCH_ANALYSIS 分析对象(分析句有问题 ≠ 来源卡该隔离,故不动来源卡)。"""
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
    from memorive_workflow.node_progress import scope
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
        "drives_knowledge_admission": False,   # 红线:判断类不驱动 KNOWLEDGE_ADMISSION、不 reverse-quarantine 来源卡
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
