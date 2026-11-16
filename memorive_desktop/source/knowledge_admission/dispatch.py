"""Automatic review of one pending card: ask a decision source, then move the card by its verdict.

The decision source is a callable taking the card's path. It returns
- Verdict.PASS or Verdict.FAIL; FAIL is a normal result and quarantines the card;
- or a ReviewDecision, a verdict with a summary of the rules it hit and a report ID, which are
  added to the reason in the admission log;
- or it raises DecisionError when the step failed for a passing reason (a timeout, a bad model
  answer); the step is then run again. After escalate_after failures in a row the card stays pending
  for a person, and nothing is written to the admission log. Retries and hand-overs go to the
  runtime log.
Any other answer is a bug and raises DecisionContractError; any other exception is not caught.
This module calls no model itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from memorive.platform.logs import log_reason

from .card_io import read_status
from .errors import DecisionContractError, DecisionError, IllegalTransitionError
from .machine import Trigger, Verdict, transition
from .states import ACTIVE, PENDING, QUARANTINED

ESCALATE_AFTER = 2   # one failure that a retry fixes does not bother anyone; two in a row do


@dataclass(frozen=True)
class AutoResult:
    """outcome: active, quarantined or escalated. attempts: how often the decision source was asked."""
    outcome: str
    attempts: int


@dataclass(frozen=True)
class ReviewDecision:
    """A verdict with the summary of the rules it hit (such as "R1,R9") and the review report's ID.
    The full findings stay in the report; the admission log only names it."""
    verdict: Verdict
    reason_summary: str | None = None
    report_id: str | None = None


DecisionSource = Callable[[Path], Verdict | ReviewDecision]


def _source_name(decision_source) -> str:
    return getattr(decision_source, "__name__", None) or type(decision_source).__name__


def auto_review(card_path: Path, decision_source: DecisionSource, *,
                escalate_after: int = ESCALATE_AFTER,
                trigger: Trigger = Trigger.AUTO_RULE) -> AutoResult:
    """Review a pending card with decision_source and move it by the verdict."""
    # Checked before the decision source runs, which may call a model.
    current = read_status(card_path)
    if current != PENDING:
        raise IllegalTransitionError(
            f"auto_review 只处理送审的 pending 卡,当前 {current}: {card_path}")
    src = _source_name(decision_source)
    consecutive = 0
    while True:
        try:
            outcome = decision_source(card_path)
        except DecisionError as e:
            consecutive += 1
            log_reason("KNOWLEDGE_ADMISSION", "auto_review_rerun",
                       f"判定环节失败第 {consecutive} 次(decision_source={src}):{e}",
                       event_category="verify", target=str(card_path))
            if consecutive >= escalate_after:
                log_reason("KNOWLEDGE_ADMISSION", "auto_review_escalate",
                           f"同一环节连错 {consecutive} 次达阈值 {escalate_after},升级人工(卡留 pending)",
                           event_category="verify", target=str(card_path))
                return AutoResult(outcome="escalated", attempts=consecutive)
            continue
        decision = outcome if isinstance(outcome, ReviewDecision) else ReviewDecision(outcome)
        # Anything but a Verdict would otherwise count as FAIL and quarantine the card.
        if not isinstance(decision.verdict, Verdict):
            raise DecisionContractError(
                f"decision_source={src} 违约:返回非 Verdict / ReviewDecision({outcome!r});"
                f"立即暴露、不重跑不隔离: {card_path}")
        to_status = ACTIVE if decision.verdict is Verdict.PASS else QUARANTINED
        reason = f"自动流转:decision_source={src}; verdict={decision.verdict.value}"
        if decision.reason_summary:
            reason += f"; summary={decision.reason_summary}"
        if decision.report_id:
            reason += f"; report={decision.report_id}"
        transition(card_path, to_status, trigger=trigger, reason=reason)
        return AutoResult(outcome=to_status, attempts=consecutive + 1)
