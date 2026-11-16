"""Moving a card between review states.

transition() reads the card's current state, checks the move against the table in states.py,
writes the new state into the card and records the move in the admission log. A refused move
leaves the card unchanged. Who decides (a person, a rule, a review result) is the caller's business;
this module only applies the decision.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path

from memorive.documents.card_states import knowledge_admission_log
from memorive.documents.card_states.card_io import read_data_id, read_status, write_status
from memorive.documents.card_states.errors import (IllegalTransitionError, KnowledgeAdmissionError, MissingBasisError,
                     TransitionNotEnabledError)
from memorive.documents.card_states.states import ACTIVE, PENDING, QUARANTINED, is_legal, is_core_enabled


class Trigger(str, Enum):
    """Who or what started a move. The values are written into the log and must not change.

    AUTO_RULE means the automatic flow started the move, not that the card was checked.
    GRADED_ADMIT admits a card with a trust grade per field instead of quarantining it after a
    failed review. Lifting a retrieval penalty is not a review move, so it has no trigger here."""
    MANUAL = "我手动"
    PLACEHOLDER = "占位"          # offline self-tests only
    AUTO_RULE = "自动规则"
    EVIDENCE_REVIEW_VERDICT = "EvidenceReview校核结果"
    GRADED_ADMIT = "分级信任准入"


class Verdict(Enum):
    """A review result: PASS makes a pending card active, FAIL quarantines it."""
    PASS = "pass"
    FAIL = "fail"


def transition(card_path: Path, to_status: str, *, trigger: Trigger, reason: str) -> str:
    """Move a card to to_status and log the move; returns the new state.

    Refused, with the card unchanged:
    - no reason (not a non-empty string) or no Trigger: MissingBasisError
    - a move the table does not allow (active -> pending, to "archive", to the same state):
      IllegalTransitionError
    - active -> quarantined, which only the artifact registry starts: TransitionNotEnabledError
    - a card without source_anchor.paper_id: CardStateError
    - a log folder that cannot be written: KnowledgeAdmissionLogAppendError
    The state is written before the log line. If writing the log line then fails, the error is
    raised with the card already moved, so the gap shows instead of being hidden."""
    if not isinstance(reason, str) or not reason.strip():
        raise MissingBasisError(f"转移缺依据(reason 须为非空字符串): {card_path}")
    if not isinstance(trigger, Trigger):
        raise MissingBasisError(f"转移缺触发源(trigger 须为 Trigger 成员): {card_path}")
    current = read_status(card_path)
    if not is_legal(current, to_status):
        raise IllegalTransitionError(f"非法转移 {current}→{to_status}(守卫挡下、状态未改): {card_path}")
    if not is_core_enabled(current, to_status):
        raise TransitionNotEnabledError(
            f"转移 {current}→{to_status} 机制合法、Core 不启用、触发时机归 ARTIFACT_REGISTRY: {card_path}")
    data_id = read_data_id(card_path)
    knowledge_admission_log.preflight()
    write_status(card_path, to_status)
    knowledge_admission_log.append_transition(data_id=data_id, from_status=current, to_status=to_status,
                                              trigger=trigger.value, reason=reason)
    return to_status


def apply_verdict(card_path: Path, verdict: Verdict, *, trigger: Trigger, reason: str) -> str:
    """Apply a review result to a pending card: PASS -> active, FAIL -> quarantined.
    A card that is not pending is refused with IllegalTransitionError."""
    # A string such as "pass" is refused: it is not Verdict.PASS and would otherwise count as FAIL.
    if not isinstance(verdict, Verdict):
        raise KnowledgeAdmissionError(f"判定结论须为 Verdict 成员(得到 {verdict!r}),受控挡下、状态不变、不碰卡: {card_path}")
    current = read_status(card_path)
    if current != PENDING:
        raise IllegalTransitionError(f"apply_verdict 只作用 pending 卡,当前 {current}: {card_path}")
    to_status = ACTIVE if verdict is Verdict.PASS else QUARANTINED
    return transition(card_path, to_status, trigger=trigger, reason=reason)


def manual_approve(card_path: Path, *, reason: str) -> str:
    """A person approves a pending card: it becomes active."""
    return apply_verdict(card_path, Verdict.PASS, trigger=Trigger.MANUAL, reason=reason)


def manual_reject(card_path: Path, *, reason: str) -> str:
    """A person rejects a pending card: it is quarantined."""
    return apply_verdict(card_path, Verdict.FAIL, trigger=Trigger.MANUAL, reason=reason)


def placeholder_pass_verdict(card_path: Path) -> Verdict:
    """Offline self-tests only: always PASS. Never a way to admit real cards, so it is not
    exported from the package."""
    return Verdict.PASS
