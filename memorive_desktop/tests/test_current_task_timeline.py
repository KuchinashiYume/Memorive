"""A job's timeline on the current task page (TimelineReconciler).

The service numbers the events of all jobs in one sequence, so one job's events have increasing
numbers with other jobs' events in between. The timeline used to wait for the number after the
cursor: every job but the first of a profile showed no events, and a job with more than 256 events
made the task list fail (BUFFER_OVERFLOW), and with it the window's start.
"""
from __future__ import annotations

import pytest

from memorive.desktop.current_task.events import EventIntegrityError, TimelineReconciler

JOB, ATTEMPT = "job_b", "job_b_attempt_001"


def event(sequence, state="RUNNING", event_type="NODE_PROGRESS"):
    return {"sequence": sequence, "job_id": JOB, "attempt_id": ATTEMPT, "control_state": state, "event_type": event_type}


def test_a_later_job_with_many_events_shows_them_all_in_order():
    # 389 events from number 351 on, as a long paper processed after other jobs; another job's
    # events (900-909) came in between when both ran.
    numbers = list(range(351, 900)) + list(range(910, 1000))
    events = [event(351, "QUEUED", "JOB_CREATED")] + [event(n) for n in numbers[1:-1]] + \
             [event(numbers[-1], "SUCCEEDED", "JOB_SUCCEEDED")]
    reconciler = TimelineReconciler(JOB, ATTEMPT)
    receipt = reconciler.ingest(events)
    assert receipt["accepted_count"] == len(events) > 256
    assert [e["sequence"] for e in reconciler.timeline()] == numbers
    assert (receipt["cursor"], receipt["terminal_state"]) == (999, "SUCCEEDED")


def test_events_given_again_or_out_of_order_are_taken_once_in_order():
    reconciler = TimelineReconciler(JOB, ATTEMPT)
    receipt = reconciler.ingest([event(20), event(12), event(15)])
    assert receipt["out_of_order_input"] is True
    assert [e["sequence"] for e in reconciler.timeline()] == [12, 15, 20]
    again = reconciler.ingest([event(15), event(20), event(31)])
    assert (again["duplicate_count"], again["accepted_count"]) == (2, 1)
    assert [e["sequence"] for e in reconciler.timeline()] == [12, 15, 20, 31]


def test_a_finished_job_does_not_go_back_to_running():
    reconciler = TimelineReconciler(JOB, ATTEMPT)
    with pytest.raises(EventIntegrityError, match="TERMINAL_STATE_ROLLBACK"):
        reconciler.ingest([event(40, "FAILED", "JOB_FAILED"), event(52, "RUNNING")])
