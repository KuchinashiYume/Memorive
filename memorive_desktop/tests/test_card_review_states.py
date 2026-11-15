"""Card review states (knowledge_admission): a card's review_status lives in its front matter.

A card starts as pending and is moved once, to active or to quarantined, by a person, a review
rule or a review result. Only the review_status line of the file changes. Every move is written
to the yearly admission log; here that log is a temporary folder.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_admission import knowledge_admission_log
from knowledge_admission.card_io import read_data_id, read_status, write_status
from knowledge_admission.dispatch import AutoResult, ReviewDecision, auto_review
from knowledge_admission.errors import (CardStateError, DecisionContractError, DecisionError,
                                        IllegalTransitionError, KnowledgeAdmissionError,
                                        KnowledgeAdmissionLogAppendError, MissingBasisError,
                                        TransitionNotEnabledError)
from knowledge_admission.machine import (Trigger, Verdict, apply_verdict, manual_approve, manual_reject,
                                         transition)

CARD = """---
title: Growth A---B at 37 C
review_status: {status}
source_anchor:
  paper_id: paper-0001
  page: 3
---
Body text.
---
More body with a dash line above.
"""


@pytest.fixture(autouse=True)
def log_dir(tmp_path, monkeypatch):
    folder = tmp_path / "admission_log"
    monkeypatch.setenv("MEMORIVE_KNOWLEDGE_ADMISSION_LOG_DIR", str(folder))
    monkeypatch.setenv("MEMORIVE_RUNTIME_LOG_LOG_DIR", str(tmp_path / "runtime_log"))
    return folder


def card(tmp_path, status="pending", text=CARD, newline="\n", name="card.md"):
    path = tmp_path / name
    path.write_bytes(text.format(status=status).replace("\n", newline).encode("utf-8"))
    return path


def records():
    from datetime import datetime
    return knowledge_admission_log.read_records(datetime.now().astimezone().year)


# Reading

def test_the_status_is_read_from_the_front_matter(tmp_path):
    assert read_status(card(tmp_path)) == "pending"
    assert read_status(card(tmp_path, "active", newline="\r\n")) == "active"
    assert read_data_id(card(tmp_path)) == "paper-0001"


@pytest.mark.parametrize("text", [
    "no front matter\nreview_status: pending\n",
    "\n---\nreview_status: pending\n---\n",
    "---\nreview_status: pending\n",
    "---\nreview_status: archive\n---\n",
    "---\ntitle: x\n---\n",
    "---\n- a list\n---\n",
])
def test_a_card_without_a_valid_status_is_refused(tmp_path, text):
    path = tmp_path / "bad.md"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CardStateError):
        read_status(path)


@pytest.mark.parametrize("anchor", ["", "source_anchor: x\n", "source_anchor:\n  paper_id: ''\n", "source_anchor:\n  paper_id: 7\n"])
def test_a_card_without_a_paper_id_has_no_data_id(tmp_path, anchor):
    path = tmp_path / "card.md"
    path.write_text("---\nreview_status: pending\n" + anchor + "---\n", encoding="utf-8")
    with pytest.raises(CardStateError):
        read_data_id(path)


# Writing

def test_writing_changes_only_the_status_line(tmp_path):
    for newline in ("\n", "\r\n"):
        path = card(tmp_path, newline=newline)
        before = path.read_bytes()
        write_status(path, "active")
        assert path.read_bytes() == before.replace(b"review_status: pending", b"review_status: active")
        assert not [p for p in tmp_path.iterdir() if p.name.startswith(".knowledge_admission_tmp_")]


@pytest.mark.parametrize("front", [
    'review_status: "pending"\n',
    "review_status: pending\nreview_status: pending\n",
    "review_status: pending\nreview_status: 'pending'\n",
])
def test_a_status_line_that_is_not_one_plain_value_is_left_alone(tmp_path, front):
    path = tmp_path / "card.md"
    path.write_text("---\n" + front + "---\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(CardStateError):
        write_status(path, "active")
    assert path.read_bytes() == before


def test_an_unknown_status_is_not_written(tmp_path):
    path = card(tmp_path)
    before = path.read_bytes()
    with pytest.raises(CardStateError):
        write_status(path, "archive")
    assert path.read_bytes() == before


# Moving a card

def test_a_pending_card_moves_once_and_the_move_is_logged(tmp_path):
    path = card(tmp_path)
    assert transition(path, "active", trigger=Trigger.AUTO_RULE, reason="all checks passed") == "active"
    assert read_status(path) == "active"
    [entry] = records()
    assert {k: entry[k] for k in ("data_id", "from", "to", "trigger", "reason", "event_category", "module")} == {
        "data_id": "paper-0001", "from": "pending", "to": "active", "trigger": Trigger.AUTO_RULE.value,
        "reason": "all checks passed", "event_category": "review_status", "module": "KNOWLEDGE_ADMISSION"}


@pytest.mark.parametrize("status, target, error", [
    ("active", "pending", IllegalTransitionError),
    ("active", "active", IllegalTransitionError),
    ("pending", "archive", IllegalTransitionError),
    ("quarantined", "active", IllegalTransitionError),
    ("active", "quarantined", TransitionNotEnabledError),
])
def test_a_move_the_table_does_not_allow_is_refused(tmp_path, status, target, error):
    path = card(tmp_path, status)
    before = path.read_bytes()
    with pytest.raises(error):
        transition(path, target, trigger=Trigger.MANUAL, reason="try")
    assert path.read_bytes() == before and records() == []


@pytest.mark.parametrize("trigger, reason", [
    (Trigger.MANUAL, ""), (Trigger.MANUAL, "   "), (Trigger.MANUAL, 123), (Trigger.MANUAL, None),
    ("我手动", "a reason"), (None, "a reason"),
])
def test_a_move_needs_a_reason_and_a_trigger(tmp_path, trigger, reason):
    path = card(tmp_path)
    before = path.read_bytes()
    with pytest.raises(MissingBasisError):
        transition(path, "active", trigger=trigger, reason=reason)
    assert path.read_bytes() == before


def test_every_trigger_may_move_a_card(tmp_path):
    for number, trigger in enumerate(Trigger):
        path = card(tmp_path, name=f"card{number}.md")
        assert transition(path, "quarantined", trigger=trigger, reason="checked") == "quarantined"
    assert len(records()) == len(Trigger)


def test_a_card_without_a_paper_id_is_not_moved(tmp_path):
    path = tmp_path / "card.md"
    path.write_text("---\nreview_status: pending\n---\n", encoding="utf-8")
    with pytest.raises(CardStateError):
        transition(path, "active", trigger=Trigger.MANUAL, reason="ok")
    assert read_status(path) == "pending"


def test_a_card_is_not_moved_when_the_log_cannot_be_written(tmp_path, monkeypatch):
    blocker = tmp_path / "log_is_a_file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("MEMORIVE_KNOWLEDGE_ADMISSION_LOG_DIR", str(blocker))
    path = card(tmp_path)
    with pytest.raises(KnowledgeAdmissionLogAppendError):
        transition(path, "active", trigger=Trigger.MANUAL, reason="ok")
    assert read_status(path) == "pending"


# Verdicts

def test_a_verdict_moves_a_pending_card(tmp_path):
    passed, failed = card(tmp_path, name="a.md"), card(tmp_path, name="b.md")
    assert apply_verdict(passed, Verdict.PASS, trigger=Trigger.EVIDENCE_REVIEW_VERDICT, reason="r") == "active"
    assert apply_verdict(failed, Verdict.FAIL, trigger=Trigger.EVIDENCE_REVIEW_VERDICT, reason="r") == "quarantined"
    assert manual_approve(card(tmp_path, name="c.md"), reason="looks right") == "active"
    assert manual_reject(card(tmp_path, name="d.md"), reason="wrong figure") == "quarantined"
    assert [r["trigger"] for r in records()][2:] == [Trigger.MANUAL.value] * 2


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL])
def test_a_verdict_only_moves_a_pending_card(tmp_path, verdict):
    path = card(tmp_path, "active")
    with pytest.raises(IllegalTransitionError):
        apply_verdict(path, verdict, trigger=Trigger.MANUAL, reason="r")
    assert read_status(path) == "active"


def test_a_verdict_must_be_a_verdict(tmp_path):
    path = card(tmp_path)
    with pytest.raises(KnowledgeAdmissionError):
        apply_verdict(path, "pass", trigger=Trigger.MANUAL, reason="r")
    assert read_status(path) == "pending"


# Automatic review

class Source:
    """A decision source that fails a given number of times before answering."""

    def __init__(self, answer, failures=0):
        self.answer, self.failures, self.calls = answer, failures, 0

    def __call__(self, path):
        self.calls += 1
        if self.calls <= self.failures:
            raise DecisionError("model timed out")
        return self.answer


def test_an_automatic_review_moves_the_card_by_its_verdict(tmp_path):
    passed, failed = card(tmp_path, name="a.md"), card(tmp_path, name="b.md")
    assert auto_review(passed, Source(Verdict.PASS)) == AutoResult(outcome="active", attempts=1)
    assert auto_review(failed, Source(Verdict.FAIL)) == AutoResult(outcome="quarantined", attempts=1)
    assert [(r["to"], r["trigger"], r["reason"]) for r in records()] == [
        ("active", Trigger.AUTO_RULE.value, "自动流转:decision_source=Source; verdict=pass"),
        ("quarantined", Trigger.AUTO_RULE.value, "自动流转:decision_source=Source; verdict=fail")]


def test_a_review_result_adds_its_summary_and_report_to_the_reason(tmp_path):
    decision = ReviewDecision(Verdict.FAIL, reason_summary="R1,R9", report_id="report-7")
    result = auto_review(card(tmp_path), Source(decision), trigger=Trigger.EVIDENCE_REVIEW_VERDICT)
    assert result.outcome == "quarantined"
    [entry] = records()
    assert entry["trigger"] == Trigger.EVIDENCE_REVIEW_VERDICT.value
    assert entry["reason"].endswith("; verdict=fail; summary=R1,R9; report=report-7")


def test_a_failed_review_step_is_run_again(tmp_path):
    source = Source(Verdict.PASS, failures=1)
    assert auto_review(card(tmp_path), source) == AutoResult(outcome="active", attempts=2)
    assert source.calls == 2


def test_two_failures_in_a_row_hand_the_card_to_a_person(tmp_path):
    path = card(tmp_path)
    source = Source(Verdict.PASS, failures=5)
    assert auto_review(path, source) == AutoResult(outcome="escalated", attempts=2)
    assert source.calls == 2 and read_status(path) == "pending" and records() == []
    assert auto_review(path, Source(Verdict.PASS, failures=5), escalate_after=3).attempts == 3


@pytest.mark.parametrize("answer", ["pass", None, ReviewDecision("pass")])
def test_a_source_that_answers_something_else_is_a_bug(tmp_path, answer):
    path = card(tmp_path)
    with pytest.raises(DecisionContractError):
        auto_review(path, Source(answer))
    assert read_status(path) == "pending"


def test_other_errors_of_a_source_are_not_retried(tmp_path):
    def broken(path):
        raise KeyError("bug")
    with pytest.raises(KeyError):
        auto_review(card(tmp_path), broken)


def test_an_automatic_review_only_takes_pending_cards(tmp_path):
    source = Source(Verdict.PASS)
    with pytest.raises(IllegalTransitionError):
        auto_review(card(tmp_path, "active"), source)
    assert source.calls == 0


# The log

def test_the_log_skips_lines_it_cannot_read(tmp_path, log_dir):
    transition(card(tmp_path), "active", trigger=Trigger.MANUAL, reason="ok")
    [path] = list(log_dir.iterdir())
    with path.open("a", encoding="utf-8") as f:
        f.write('{"half": \n\n')
    assert [r["to"] for r in records()] == ["active"]
    assert knowledge_admission_log.read_records(1999) == []
