"""Graded admission: the card review's findings become a credibility for each field and each key_data
entry, and the card becomes active carrying them, instead of the whole card being quarantined.

The review has false positives (too strict, structural, new kinds), so findings only set a
credibility and never remove a card. Quarantine is left for real failures (the gateway, parsing or
the region check failing again and again: the card stays pending) and for manual rejection.
The four credibilities, from the existing R1 to R9 rules and the anchor misplacement verdict:
  - verified: actually checked, no violation (the only one that may be cited directly; danger None)
  - anchor_uncertain: the content is probably elsewhere in the source, not in this chunk (structural,
    not made up)
  - flagged: a real violation (a word replaced, a number changed, generalized, added content ...)
  - unknown: not actually checked (not taken as a subject, no anchor, chunk missing); never verified,
    since no violations is not the same as checked
Every key_data entry or content field that is not verified carries a mark: flagged the loudest
(real violation, do not cite, check the source), anchor_uncertain and unknown a plain one (check the
source). Only content a second vendor checked and found clean is verified without a mark. Admitting
instead of quarantining is safe only because these marks are clear, so the per-entry list of what
was checked decides verified.
It never edits the card's content, does not touch the analysis, and keeps the two review layers apart.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from memorive.platform.logs import log_reason
from memorive.documents.card_states import ACTIVE, DecisionError, Trigger, transition
from memorive.documents.card_distillation.card import (CONTENT_FIELDS, CRED_ANCHOR_UNCERTAIN, CRED_FLAGGED, CRED_UNKNOWN,
                             CRED_VERIFIED, DANGER_UNKNOWN_FIELD, DANGER_UNKNOWN_KD,
                             apply_field_credibility)

from memorive.documents.card_review.mechanical import _read_frontmatter
from memorive.documents.card_review.errors import BatchContractExhausted
from memorive.documents.card_review.transcription import transcription_verify

_VALID_RULES = frozenset(f"R{i}" for i in range(1, 10))
_ANCHOR_MARK = "疑锚点归位"   # the v2 prompt's mark for "the content is probably elsewhere in the source" (only this one counts)
_BATCH_ITEM_PREFIX_RE = re.compile(r"^item_index=\d+\s*[；;]\s*")


def _is_anchor_uncertain(p) -> bool:
    """Whether a problem is an anchor misplacement, judged strictly. The v2 prompt reports one only as
    rule R8 with a detail that starts with the misplacement mark; a real R8 overreach starts its detail
    differently. So it must be R8 and the stripped detail must start with the mark: limited to R8, so a
    real R9 number error mentioning the words is not downgraded; at the start, so a real R8 mentioning
    them later is not either. Any variation counts as flagged, the safe side."""
    detail = (getattr(p, "detail", "") or "").strip()
    detail = _BATCH_ITEM_PREFIX_RE.sub("", detail, count=1)
    return getattr(p, "kind", None) == "R8" and detail.startswith(_ANCHOR_MARK)


def _r_problems(problems):
    """Only R1 to R9 violations (warnings such as malformed_subject do not change the credibility)."""
    return [p for p in problems if getattr(p, "kind", None) in _VALID_RULES]


def _classify(rprobs):
    """R1 to R9 problems -> (credibility, note): any real violation flagged, only misplacements
    anchor_uncertain, none verified."""
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
    """What grade_and_admit returns; outcome is active or escalated."""
    outcome: str
    attempts: int
    report_id: str | None = None
    field_credibility: dict | None = None
    key_data_credibility: list | None = None


def _mark(cred, rp, note: str, *, numeric: bool):
    """The mark of anything not verified: flagged the loudest (the rule code and the review's verdict
    with the source value), anchor_uncertain and unknown a plain one (check the source); verified None,
    the only one that may be cited directly."""
    what = "数值" if numeric else "该字段"
    if cred == CRED_FLAGGED:
        rules = ",".join(sorted({p.kind for p in rp if not _is_anchor_uncertain(p)}))
        return f"⚠⚠ {what}经 EVIDENCE_REVIEW 异源判有真违规({rules})·勿直接引·须核原文 ── EVIDENCE_REVIEW 判词:{note}"
    if cred == CRED_ANCHOR_UNCERTAIN:
        return f"⚠ {what} EVIDENCE_REVIEW 疑锚点归位·未能在本块证实·勿直接引·须核原文 ── {note}"
    if cred == CRED_UNKNOWN:                                # the same mark text the card writer uses
        return DANGER_UNKNOWN_KD if numeric else DANGER_UNKNOWN_FIELD
    return None                                             # verified: checked and clean, the only one without a mark


def _grade_one(cred_or_none, rp, note, *, numeric):
    """The credibility of one field or key_data entry; cred_or_none=None means not checked, unknown."""
    cred, note = (CRED_UNKNOWN, None) if cred_or_none is None else (cred_or_none, note)
    return {"credibility": cred, "note": note, "danger": _mark(cred, rp, note, numeric=numeric)}


def grade_from_report(rep, key_data_count: int):
    """EvidenceReviewReport -> (field_credibility {field: {credibility, note, danger}},
    key_data_credibility, a list in key_data order).

    Only fields and key_data entries whose location is in rep.checked_locations (a prompt was really
    sent) are graded by R1 to R9; the rest are unknown, never verified, so "no violations" never stands
    for "checked". Anything not verified carries a mark (_mark)."""
    problems = list(getattr(rep, "problems", []) or [])
    checked = set(getattr(rep, "checked_locations", None) or [])
    field_cred = {}
    for f in CONTENT_FIELDS:                                # the six core fields; subject location by_field.{f}
        if f"by_field.{f}" not in checked:                 # not checked: unknown
            field_cred[f] = _grade_one(None, [], None, numeric=False)
            continue
        rp = _r_problems([p for p in problems if getattr(p, "field", None) == f])
        cred, note = _classify(rp)
        field_cred[f] = _grade_one(cred, rp, note, numeric=False)
    kd_cred = []
    for i in range(key_data_count):                        # key_data:subject location = key_data[i]
        loc = f"key_data[{i}]"
        if loc not in checked:                             # not checked (needs_review false, or the chunk was missing): unknown
            kd_cred.append(_grade_one(None, [], None, numeric=True))
            continue
        rp = _r_problems([p for p in problems if getattr(p, "location", None) == loc])
        cred, note = _classify(rp)
        kd_cred.append(_grade_one(cred, rp, note, numeric=True))
    return field_cred, kd_cred


def admit_unreviewed(card_path, **_ignored) -> AdmitResult:
    """Admission when the card check (workflow node 04) is turned off: nothing is checked, so every field and
    key_data entry is unknown (marked, never verified) and the card becomes active (trigger=GRADED_ADMIT).
    documents/workflow/executor.py passes it to review_card_revision in place of the review."""
    card_path = Path(card_path)
    fm = _read_frontmatter(card_path)
    kd = fm.get("key_data") if isinstance(fm, dict) else None
    unchecked = type("Unchecked", (), {"problems": [], "checked_locations": []})()
    field_cred, kd_cred = grade_from_report(unchecked, len(kd) if isinstance(kd, list) else 0)
    apply_field_credibility(card_path, field_cred, kd_cred)
    reason = "卡片核对（04）已关闭：没有核对，各字段都标为未核，不能直接引用"
    transition(card_path, ACTIVE, trigger=Trigger.GRADED_ADMIT, reason=reason)
    log_reason("EVIDENCE_REVIEW", "admit_unreviewed", reason, event_category="verify", target=str(card_path))
    return AdmitResult(outcome="active", attempts=0, field_credibility=field_cred, key_data_credibility=kd_cred)


def grade_and_admit(card_path, *, call_verify=None, region_check=None, escalate_after=2) -> AdmitResult:
    """Graded admission: run the transcription check, write each field's and key_data entry's
    credibility, move the card from pending to active (trigger=GRADED_ADMIT).

    Review findings do not quarantine the card; the credibilities carry them. Gateway, parsing or
    region check failures (DecisionError) up to the limit give outcome='escalated' and the card stays
    pending; a bad reply never counts as no violations. call_verify and region_check can be injected
    (tests); by default the real review runs with a second vendor, after the region check.
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
                "EVIDENCE_REVIEW",
                "grade_and_admit_contract_exhausted",
                f"EVIDENCE_REVIEW 阶段技术输出契约已完成唯一重试仍失败；不再整卡重跑，升级人工:{e}",
                event_category="verify",
                target=str(card_path),
            )
            return AdmitResult(outcome="escalated", attempts=consecutive + 1)
        except DecisionError as e:
            consecutive += 1
            log_reason("EVIDENCE_REVIEW", "grade_and_admit_rerun",
                       f"EVIDENCE_REVIEW 校核环节失败第 {consecutive} 次(灾难·可重跑):{e}",
                       event_category="verify", target=str(card_path))
            if consecutive >= escalate_after:
                log_reason("EVIDENCE_REVIEW", "grade_and_admit_escalate",
                           f"EVIDENCE_REVIEW 连错 {consecutive} 次达阈值 {escalate_after},升级人工(卡留 pending、**不 admit**)",
                           event_category="verify", target=str(card_path))
                return AdmitResult(outcome="escalated", attempts=consecutive)
            continue
    # No subject was actually checked (all anchors broken, chunks missing, needs_review false
    # everywhere): every field would come out verified from _classify([]), yet nothing was checked.
    # The card is not admitted that way; it is escalated and stays pending.
    if getattr(rep, "checked_count", 0) <= 0:
        log_reason("EVIDENCE_REVIEW", "grade_and_admit_escalate",
                   f"EVIDENCE_REVIEW 真核过 subject 数=0(锚点全断 / 缺 chunk / needs_review 全 false),verified 不可信,"
                   f"升级人工(卡留 pending、**不 admit**);report={rep.report_id}",
                   event_category="verify", target=str(card_path))
        return AdmitResult(outcome="escalated", attempts=consecutive + 1, report_id=rep.report_id)
    fm = _read_frontmatter(card_path)
    kd = fm.get("key_data") if isinstance(fm, dict) else None
    field_cred, kd_cred = grade_from_report(rep, len(kd) if isinstance(kd, list) else 0)
    apply_field_credibility(card_path, field_cred, kd_cred)      # writes the credibilities only, never the field values
    flagged_fields = [f for f, c in field_cred.items() if c["credibility"] == CRED_FLAGGED]
    unknown_fields = [f for f, c in field_cred.items() if c["credibility"] == CRED_UNKNOWN]   # core fields not checked (active with a mark, for a person to check)
    flagged_kd = sum(1 for c in kd_cred if c["credibility"] == CRED_FLAGGED)
    unknown_kd = sum(1 for c in kd_cred if c["credibility"] == CRED_UNKNOWN)
    reason = (f"分级信任准入:EVIDENCE_REVIEW 判 → 卡带 per-field 信用档进 active(不整卡 quarantine);report={rep.report_id}; "
              f"flagged字段={flagged_fields or '无'} flagged_kd={flagged_kd}; "
              f"未核unknown字段={unknown_fields or '无'} unknown_kd={unknown_kd}(带⚠标·非verified·勿直接引)")
    transition(card_path, ACTIVE, trigger=Trigger.GRADED_ADMIT, reason=reason)
    log_reason("EVIDENCE_REVIEW", "grade_and_admit", reason, event_category="verify", target=str(card_path))
    return AdmitResult(outcome="active", attempts=consecutive + 1, report_id=rep.report_id,
                       field_credibility=field_cred, key_data_credibility=kd_cred)
