"""M6 分级信任准入(v6.4 之四·上半):M6 判 → per-field / per-key_data 信用档 → 卡带档进 active(不再整卡 quarantine)。

承设计与 round-1 结论:M6 会有假阳(偏严的、结构的、将来第 N 类)——一律**只落信用档、永不杀卡**;
quarantine 只留**灾难**(M9/解析/区域门连错升级、卡留 pending)/**手动**(manual_reject)。
信用档四值(复用现有 R1–R9 + v2 归位分类、**不新造判词**):
  · verified          —— M6 **真核过** 且无 R 违规(唯一「可直接引」档、danger=None)
  · anchor_uncertain  —— M6 判「疑锚点归位」(内容疑在原文别处非本块、结构性,非臆造)
  · flagged           —— M6 真违规(换实词 / 改数值 / 泛化 / 加内容 …)
  · unknown           —— M6 **没真核过**(未收为 subject / 锚点缺失 / 缺 chunk 被跳过)——**绝不当 verified**(承外审:0 违规≠核过)
**铁律 + 外审加固**:**任何非 verified 的 key_data / 内容字段都带标**——flagged→**最响** ⚠⚠(真违规·勿直接引·核原文);
  anchor_uncertain / unknown→⚠(锚点存疑 / 未核·勿直接引·须核原文)。只有「真被异源核过且干净」才 verified-无标。
  admit 不 quarantine 的数值安全全靠这道标够响;verified 被洗白(未核当已核)= 红线,故 per-entry 核过名单硬门。
守边界:检察官不下笔改卡(下半修订环**不建**)、不动 M4、不合并异源两层。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from m11_log import log_reason
from m8_state_machine import ACTIVE, DecisionError, Trigger, transition
from m2_distill.card import (CONTENT_FIELDS, CRED_ANCHOR_UNCERTAIN, CRED_FLAGGED, CRED_UNKNOWN,
                             CRED_VERIFIED, DANGER_UNKNOWN_FIELD, DANGER_UNKNOWN_KD,
                             apply_field_credibility)

from .mechanical import _read_frontmatter
from .errors import BatchContractExhausted
from .transcription import transcription_verify

_VALID_RULES = frozenset(f"R{i}" for i in range(1, 10))
_ANCHOR_MARK = "疑锚点归位"   # v2 prompt 对「内容疑在原文别处非本块」的归位判词标记(不新造、只认这个)
_BATCH_ITEM_PREFIX_RE = re.compile(r"^item_index=\d+\s*[；;]\s*")


def _is_anchor_uncertain(p) -> bool:
    """归位判定(**危险方向双重从严**,承外审 Finding #1):v2 prompt 只以 `rule=R8` + detail
    **以「疑锚点归位」开头**「疑锚点归位·内容疑在原文别处非本块·建议核锚点」输出归位;真越界 R8 的 detail
    以「越界·与原文冲突/原文无此」开头。**故须 kind==R8 且 detail.strip() 以「疑锚点归位」开头**——
    ① 限 R8:防真 R9 数值错 detail 偶含归位词被降级;② 限开头:防一条真越界 R8 的 detail 里(非开头)
    夹带归位词被降级。任何漂移(措辞变体 / 前缀)→ 落 flagged(安全方向、宁严勿漏)。"""
    detail = (getattr(p, "detail", "") or "").strip()
    detail = _BATCH_ITEM_PREFIX_RE.sub("", detail, count=1)
    return getattr(p, "kind", None) == "R8" and detail.startswith(_ANCHOR_MARK)


def _r_problems(problems):
    """只取 R1–R9 违规(malformed_subject 等 warn 不改信用档)。"""
    return [p for p in problems if getattr(p, "kind", None) in _VALID_RULES]


def _classify(rprobs):
    """一组 R1–R9 problems → (credibility, note)。任一真违规→flagged;全归位→anchor_uncertain;无→verified。"""
    reals = [p for p in rprobs if not _is_anchor_uncertain(p)]
    anchors = [p for p in rprobs if _is_anchor_uncertain(p)]
    if reals:
        chosen, cred = reals, CRED_FLAGGED
    elif anchors:
        chosen, cred = anchors, CRED_ANCHOR_UNCERTAIN
    else:
        return CRED_VERIFIED, None
    note = " ‖ ".join(f"[{p.kind}] {(p.detail or '')[:160]}" for p in chosen[:5])
    return cred, note


@dataclass(frozen=True)
class AdmitResult:
    """grade_and_admit 结果。outcome ∈ {active, escalated}。"""
    outcome: str
    attempts: int
    report_id: str | None = None
    field_credibility: dict | None = None
    key_data_credibility: list | None = None


def _mark(cred, rp, note: str, *, numeric: bool):
    """非 verified 一律带标(承外审第二轮:未核 / 归位的数值也不可无标直接引):
      flagged→最响 ⚠⚠(真违规·带 R 码 + M6 判词含原文值);anchor_uncertain→⚠(疑锚点归位·须核);
      unknown→⚠(未核·须核);verified→None(唯一可直接引)。"""
    what = "数值" if numeric else "该字段"
    if cred == CRED_FLAGGED:
        rules = ",".join(sorted({p.kind for p in rp if not _is_anchor_uncertain(p)}))
        return f"⚠⚠ {what}经 M6 异源判有真违规({rules})·勿直接引·须核原文 ── M6 判词:{note}"
    if cred == CRED_ANCHOR_UNCERTAIN:
        return f"⚠ {what} M6 疑锚点归位·未能在本块证实·勿直接引·须核原文 ── {note}"
    if cred == CRED_UNKNOWN:                                # 复用 card 建卡常量(建卡标=落档标逐字一致)
        return DANGER_UNKNOWN_KD if numeric else DANGER_UNKNOWN_FIELD
    return None                                             # verified:真核过且干净,唯一无标


def _grade_one(cred_or_none, rp, note, *, numeric):
    """一个字段 / 一条 key_data 的信用档三元组。cred_or_none=None 表示「未核」→ unknown。"""
    cred, note = (CRED_UNKNOWN, None) if cred_or_none is None else (cred_or_none, note)
    return {"credibility": cred, "note": note, "danger": _mark(cred, rp, note, numeric=numeric)}


def grade_from_report(rep, key_data_count: int):
    """M6Report → (field_credibility{字段:{credibility,note,danger}}, key_data_credibility[list·与 key_data 同序])。

    **per-entry 核过名单硬门(外审第二轮加固)**:只有 location 在 rep.checked_locations(真发过 prompt)内的
    字段 / key_data 才按 R1–R9 判级;**未核的一律 unknown(非 verified)**——杜绝「0 违规=verified」把没核当核过。
    非 verified 一律带标(见 _mark)。"""
    problems = list(getattr(rep, "problems", []) or [])
    checked = set(getattr(rep, "checked_locations", None) or [])
    field_cred = {}
    for f in CONTENT_FIELDS:                                # 6 核心字段:subject location = by_field.{f}
        if f"by_field.{f}" not in checked:                 # 未核 → unknown(非 verified)
            field_cred[f] = _grade_one(None, [], None, numeric=False)
            continue
        rp = _r_problems([p for p in problems if getattr(p, "field", None) == f])
        cred, note = _classify(rp)
        field_cred[f] = _grade_one(cred, rp, note, numeric=False)
    kd_cred = []
    for i in range(key_data_count):                        # key_data:subject location = key_data[i]
        loc = f"key_data[{i}]"
        if loc not in checked:                             # 未核(needs_review=false / 缺 chunk 跳过)→ unknown
            kd_cred.append(_grade_one(None, [], None, numeric=True))
            continue
        rp = _r_problems([p for p in problems if getattr(p, "location", None) == loc])
        cred, note = _classify(rp)
        kd_cred.append(_grade_one(cred, rp, note, numeric=True))
    return field_cred, kd_cred


def grade_and_admit(card_path, *, call_verify=None, region_check=None, escalate_after=2) -> AdmitResult:
    """分级信任准入:跑 M6 搬运类校核 → 落 per-field / per-key_data 信用档 → pending→active(trigger=GRADED_ADMIT)。

    **M6 flag 不驱动 quarantine**(改由信用档吸收);M9 / 解析 / 区域门失败(DecisionError)连错达阈值 →
    outcome='escalated'、卡留 pending(灾难路径,**绝不静默放行坏响应**、绝不当无违规=verified)。
    call_verify / region_check 可注入(离线自测);默认真 M6(Haiku 异源、先过区域门)。
    """
    card_path = Path(card_path)
    consecutive = 0
    while True:
        try:
            rep = transcription_verify(
                card_path,
                call_verify=call_verify,
                region_check=region_check,
                bypass_cache=consecutive > 0,
            )
            break
        except BatchContractExhausted as e:
            log_reason(
                "M6",
                "grade_and_admit_contract_exhausted",
                f"M6 阶段技术输出契约已完成唯一重试仍失败；不再整卡重跑，升级人工:{e}",
                event_category="verify",
                target=str(card_path),
            )
            return AdmitResult(outcome="escalated", attempts=consecutive + 1)
        except DecisionError as e:
            consecutive += 1
            log_reason("M6", "grade_and_admit_rerun",
                       f"M6 校核环节失败第 {consecutive} 次(灾难·可重跑):{e}",
                       event_category="verify", target=str(card_path))
            if consecutive >= escalate_after:
                log_reason("M6", "grade_and_admit_escalate",
                           f"M6 连错 {consecutive} 次达阈值 {escalate_after},升级人工(卡留 pending、**不 admit**)",
                           event_category="verify", target=str(card_path))
                return AdmitResult(outcome="escalated", attempts=consecutive)
            continue
    # 洞C(承外审补充 Finding):M6 真核过 0 个 subject(锚点全断 / 缺 chunk / needs_review 全 false)→
    # 全字段 _classify([]) 会误判 verified;但那是「没核成」非「核过且干净」。**绝不以无核 verified 准入** → 升级留 pending。
    if getattr(rep, "checked_count", 0) <= 0:
        log_reason("M6", "grade_and_admit_escalate",
                   f"M6 真核过 subject 数=0(锚点全断 / 缺 chunk / needs_review 全 false),verified 不可信,"
                   f"升级人工(卡留 pending、**不 admit**);report={rep.report_id}",
                   event_category="verify", target=str(card_path))
        return AdmitResult(outcome="escalated", attempts=consecutive + 1, report_id=rep.report_id)
    fm = _read_frontmatter(card_path)
    kd = fm.get("key_data") if isinstance(fm, dict) else None
    field_cred, kd_cred = grade_from_report(rep, len(kd) if isinstance(kd, list) else 0)
    apply_field_credibility(card_path, field_cred, kd_cred)      # 落档(不下笔改字段值、只写信用档)
    flagged_fields = [f for f, c in field_cred.items() if c["credibility"] == CRED_FLAGGED]
    unknown_fields = [f for f, c in field_cred.items() if c["credibility"] == CRED_UNKNOWN]   # 未核核心字段(带⚠标进 active、留人核)
    flagged_kd = sum(1 for c in kd_cred if c["credibility"] == CRED_FLAGGED)
    unknown_kd = sum(1 for c in kd_cred if c["credibility"] == CRED_UNKNOWN)
    reason = (f"分级信任准入:M6 判 → 卡带 per-field 信用档进 active(不整卡 quarantine);report={rep.report_id}; "
              f"flagged字段={flagged_fields or '无'} flagged_kd={flagged_kd}; "
              f"未核unknown字段={unknown_fields or '无'} unknown_kd={unknown_kd}(带⚠标·非verified·勿直接引)")
    transition(card_path, ACTIVE, trigger=Trigger.GRADED_ADMIT, reason=reason)
    log_reason("M6", "grade_and_admit", reason, event_category="verify", target=str(card_path))
    return AdmitResult(outcome="active", attempts=consecutive + 1, report_id=rep.report_id,
                       field_credibility=field_cred, key_data_credibility=kd_cred)
