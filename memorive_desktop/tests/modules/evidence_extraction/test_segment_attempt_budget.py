"""Segmented distillation: failed attempts are recorded per segment within a fixed budget.

`SegmentCompletionLedger.record_attempt` accepts an attempt only for a segment of the frozen
topology, with a new attempt id and a known state, while that segment and the whole paper still
have attempts left. The resume plan then shows which segments may run again.
"""
from __future__ import annotations

import pytest

from memorive.documents.card_distillation.segmented_distill import SegmentCompletionLedger
from memorive.documents.card_distillation.segmented_distill.contracts import SegmentedDistillContractError

TOPOLOGY = {"paper_id": "P1", "topology_hash": "T" * 64, "source_manifest_hash": "S" * 64,
            "segments": [{"segment_id": "seg-1"}, {"segment_id": "seg-2"}]}


def _ledger(**budget):
    return SegmentCompletionLedger(TOPOLOGY, **budget)


def _code(call):
    with pytest.raises(SegmentedDistillContractError) as caught:
        call()
    return caught.value.code


def test_attempts_are_recorded_and_shown_in_the_resume_plan():
    ledger = _ledger()
    ledger.record_attempt(segment_id="seg-1", attempt_id="a1", state="retryable", receipt_ref="r1",
                          reason_code="TIMEOUT")
    ledger.record_attempt(segment_id="seg-1", attempt_id="a2", state="capacity_blocked", receipt_ref="r2")
    snapshot = ledger.snapshot()
    assert [a["attempt_id"] for a in snapshot["attempts"]] == ["a1", "a2"]
    assert snapshot["attempts"][0]["reason_code"] == "TIMEOUT"
    plan = ledger.resume_plan()
    assert (plan["blocked_segment_ids"], plan["schedule_segment_ids"]) == (["seg-1"], ["seg-2"])


@pytest.mark.parametrize("state", ["terminal", "invalid_for_merge", "retryable", "capacity_blocked", "foreign_input"])
def test_each_known_state_is_accepted(state):
    _ledger().record_attempt(segment_id="seg-2", attempt_id="a1", state=state, receipt_ref="r1")


def test_an_attempt_for_a_segment_outside_the_topology_is_refused():
    assert _code(lambda: _ledger().record_attempt(segment_id="seg-9", attempt_id="a1", state="retryable",
                                                  receipt_ref="r1")) == "ORPHAN_SEGMENT_ID"


def test_an_attempt_id_is_used_once_per_paper():
    ledger = _ledger()
    ledger.record_attempt(segment_id="seg-1", attempt_id="a1", state="retryable", receipt_ref="r1")
    assert _code(lambda: ledger.record_attempt(segment_id="seg-2", attempt_id="a1", state="retryable",
                                               receipt_ref="r2")) == "ATTEMPT_ID_DUPLICATE"


def test_an_unknown_state_is_refused():
    assert _code(lambda: _ledger().record_attempt(segment_id="seg-1", attempt_id="a1", state="skipped",
                                                  receipt_ref="r1")) == "ATTEMPT_STATE_INVALID"


def test_each_segment_has_its_own_attempt_budget():
    ledger = _ledger(max_attempts_per_segment=2)
    for attempt in ("a1", "a2"):
        ledger.record_attempt(segment_id="seg-1", attempt_id=attempt, state="retryable", receipt_ref=attempt)
    assert _code(lambda: ledger.record_attempt(segment_id="seg-1", attempt_id="a3", state="retryable",
                                               receipt_ref="r3")) == "RECOVERY_BUDGET_EXHAUSTED"
    ledger.record_attempt(segment_id="seg-2", attempt_id="a3", state="retryable", receipt_ref="r3")


def test_the_paper_has_a_total_call_budget():
    ledger = _ledger(max_attempts_per_segment=5, max_total_calls=3)
    for attempt, segment in (("a1", "seg-1"), ("a2", "seg-2"), ("a3", "seg-1")):
        ledger.record_attempt(segment_id=segment, attempt_id=attempt, state="retryable", receipt_ref=attempt)
    assert _code(lambda: ledger.record_attempt(segment_id="seg-2", attempt_id="a4", state="retryable",
                                               receipt_ref="r4")) == "RECOVERY_BUDGET_EXHAUSTED"


@pytest.mark.parametrize("field", ["attempt_id", "receipt_ref"])
def test_attempt_id_and_receipt_must_be_text(field):
    values = dict(segment_id="seg-1", attempt_id="a1", state="retryable", receipt_ref="r1")
    values[field] = " "
    ledger = _ledger()
    with pytest.raises(SegmentedDistillContractError) as caught:
        ledger.record_attempt(**values)
    assert caught.value.code == "NON_EMPTY_TEXT_REQUIRED"
    assert ledger.snapshot()["attempts"] == []


def test_a_refused_attempt_is_not_recorded():
    ledger = _ledger()
    with pytest.raises(SegmentedDistillContractError):
        ledger.record_attempt(segment_id="seg-1", attempt_id="a1", state="skipped", receipt_ref="r1")
    assert ledger.snapshot()["attempts"] == []


@pytest.mark.parametrize("budget", [dict(max_attempts_per_segment=0), dict(max_total_calls=0)])
def test_a_budget_must_be_positive(budget):
    assert _code(lambda: _ledger(**budget)) == "RECOVERY_BUDGET_INVALID"
