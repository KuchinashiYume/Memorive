"""A session refinement whose worker had started when the app closed is ended at the next start.

Before end_interrupted_refinements (sessions/controller/refinement_queue.py) the task stayed RUNNING with
no worker: its inbox card showed 处理中 for good, a new request for the conversation got the old task
back (or SESSION_REFINEMENT_SOURCE_SNAPSHOT_CHANGED when the conversation had changed), and the queue
waited for it. The task store and the queue are the real ones; the inbox and the captured conversation
are stand-ins, as in test_refinement_start_failure.py. A new store on the same folder is the next start.
"""
from __future__ import annotations

import json
import threading

from memorive.desktop.current_task.refinement_tasks import REFINEMENT_NODE_IDS
from test_refinement_start_failure import Queued

REQUEST = {"item_id": "item-1", "display_name": "A conversation", "local_projection_id": "conversation-1",
           "source_snapshot_sha256": "1" * 64, "selected_profile_ref": "model", "message_count": 1}


class Started(Queued):
    """The app over a task folder, a draft folder and an inbox of refinement cards."""

    def __init__(self, root, cards):
        super().__init__(root / "tasks")
        self._refinement_root = root / "drafts"
        self._refinement_root.mkdir(exist_ok=True)
        self.cards = cards  # item id -> {"state": ..., "job_id": ...}; shared by both starts
        self.completed = []

    def _inbox_refinement_call(self, method, params):
        if method == "inbox.projection":
            return {"cards": [{"item_id": item_id, "item_kind": "SESSION_REFINEMENT", "state": card["state"]}
                              for item_id, card in self.cards.items()]}
        if method == "inbox.item_detail":
            card = self.cards[params["item_id"]]
            return {"item_id": params["item_id"], "item_kind": "SESSION_REFINEMENT", "state": card["state"],
                    "job_id": card.get("job_id")}
        if method == "inbox.complete_session_refinement":
            self.completed.append((params["item_id"], params["succeeded"], params["error_code"]))
            self.cards[params["item_id"]]["state"] = "HANDED_OFF" if params["succeeded"] else "ERROR"


def _closed_while(tmp_path, steps_done, draft=False):
    """A refinement that had completed steps_done steps and begun the next when the app closed."""
    cards = {"item-1": {"state": "QUEUED"}}
    before = Started(tmp_path, cards)
    task = before._ensure_refinement_queue_task(REQUEST)
    job_id = task["job_id"]
    cards["item-1"].update(state="PROCESSING", job_id=job_id)
    manager = before._refinement_task_manager
    for node_id in REFINEMENT_NODE_IDS[:steps_done]:
        manager.begin_session_refinement_step(job_id, node_id)
        manager.complete_session_refinement_step(job_id, node_id)
    manager.begin_session_refinement_step(job_id, REFINEMENT_NODE_IDS[steps_done])
    if draft:
        (before._refinement_root / "conversation-1.json").write_text(json.dumps(
            {"task_id": job_id, "draft_id": "refinement-draft", "external_model_calls": 1}), encoding="utf-8")
    return job_id, Started(tmp_path, cards)


def test_a_refinement_left_running_ends_as_interrupted_and_can_be_asked_for_again(tmp_path):
    job_id, after = _closed_while(tmp_path, steps_done=2)  # the model step had begun
    assert after.end_interrupted_refinements()["ended_job_ids"] == [job_id]
    ended = after._refinement_task_manager.get_session_refinement_task(job_id)
    assert (ended["control_state"], ended["error_code"], ended["model_calls"]) == (
        "FAILED", "SESSION_REFINEMENT_INTERRUPTED", 0)
    assert after.completed == [("item-1", False, "SESSION_REFINEMENT_INTERRUPTED")]
    # Asked again, also for a conversation that changed meanwhile: a new attempt.
    again = after._ensure_refinement_queue_task(dict(REQUEST, source_snapshot_sha256="2" * 64))
    assert again["job_id"] != job_id and again["control_state"] == "QUEUED"


def test_the_queue_no_longer_waits_for_a_task_left_running(tmp_path):
    job_id, after = _closed_while(tmp_path, steps_done=0)
    after.end_interrupted_refinements()
    after.cards["item-2"] = {"state": "QUEUED"}
    after._refinement_queue.put(dict(REQUEST, item_id="item-2"))
    worker = threading.Thread(target=after._drain_refinement_queue, daemon=True)
    worker.start()
    worker.join(10)
    assert not worker.is_alive()  # it used to wait for the old task for good
    assert after.completed[-1][0] == "item-2"


def test_a_refinement_whose_draft_was_written_ends_as_it_would_have(tmp_path):
    job_id, after = _closed_while(tmp_path, steps_done=3, draft=True)
    after.end_interrupted_refinements()
    ended = after._refinement_task_manager.get_session_refinement_task(job_id)
    assert (ended["control_state"], ended["draft_id"], ended["model_calls"], ended["external_model_calls"]) == (
        "SUCCEEDED", "refinement-draft", 1, 1)
    assert after.completed == [("item-1", True, "")]


def test_a_refinement_not_yet_started_is_left_for_the_queue(tmp_path):
    cards = {"item-1": {"state": "QUEUED"}}
    task = Started(tmp_path, cards)._ensure_refinement_queue_task(REQUEST)
    after = Started(tmp_path, cards)
    assert after.end_interrupted_refinements()["ended_job_ids"] == []
    assert after._refinement_task_manager.get_session_refinement_task(task["job_id"])["control_state"] == "QUEUED"
    assert after.completed == []
