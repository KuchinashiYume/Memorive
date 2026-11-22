"""A session refinement that cannot start ends its task, so a later request makes a new attempt.

The refinement queue (sessions/controller/refinement_queue.py) starts a queued task; before the model
runs, the start reads the conversation again and compares it with the snapshot the task was queued
with. When the conversation changed in between, the start is refused. The task used to stay QUEUED:
the inbox showed the failure, but the next request for the same conversation found the old task and was
refused with SESSION_REFINEMENT_SOURCE_SNAPSHOT_CHANGED. The task store and the queue are the real ones;
the inbox and the captured conversation are stand-ins.
"""
from __future__ import annotations

import threading
from queue import Queue

from memorive.desktop.current_task.refinement_tasks import SessionRefinementTaskStore
from memorive.sessions.controller.refinement_queue import RefinementQueueMixin
from memorive.sessions.controller.refinement_tasks import RefinementTasksMixin


class Queued(RefinementQueueMixin, RefinementTasksMixin):
    def __init__(self, root):
        self._refinement_task_manager = SessionRefinementTaskStore(root)
        self._refinement_lock = threading.RLock()
        self._refinement_queue = Queue()
        self._refinement_queue_items = {"item-1"}
        self._refinement_workers = {}
        self._metrics = {"refinement_tasks_created": 0}
        self.inbox_errors = []

    def _refinement_task_for_projection(self, projection_id):
        return self._refinement_task_manager.find_session_refinement_task(projection_id)

    def _notify_refinement_queue_projection(self, item_id):
        pass

    def _refinement_auto_run_enabled(self):
        return True

    def _inbox_refinement_call(self, method, params):
        if method == "inbox.item_detail":
            return {"item_kind": "SESSION_REFINEMENT", "state": "QUEUED"}
        if method == "inbox.complete_session_refinement":
            self.inbox_errors.append(params["error_code"])

    def _get_internal(self, projection_id):
        return {"provider_id": "test", "captured_at": "2026-11-21T00:00:00Z"}

    def _messages_for(self, row, presentation=False):
        # The conversation as it is now: different from the snapshot it was queued with.
        return [{"role": "user", "text": "The conversation changed after it was queued."}], {"status": "COMPLETE"}

    def _refinement_source_content_allowed(self, row, messages, completeness):
        return True

    def _refinement_channel_messages(self, messages):
        return messages


def test_a_refinement_whose_conversation_changed_before_it_started_can_be_requested_again(tmp_path):
    queued = Queued(tmp_path / "refinement")
    request = {"item_id": "item-1", "display_name": "A conversation", "local_projection_id": "conversation-1",
               "source_snapshot_sha256": "1" * 64, "selected_profile_ref": "model", "message_count": 1}
    first = queued._ensure_refinement_queue_task(request)
    queued._refinement_queue.put(request)
    queued._drain_refinement_queue()
    assert queued.inbox_errors == ["SESSION_REFINEMENT_FROZEN_SNAPSHOT_DRIFT"]
    ended = queued._refinement_task_manager.get_session_refinement_task(first["job_id"])
    assert (ended["control_state"], ended["error_code"], ended["model_calls"]) == (
        "FAILED", "SESSION_REFINEMENT_FROZEN_SNAPSHOT_DRIFT", 0)
    # Asked again for the conversation as it is now: a new attempt, not the old task.
    second = queued._ensure_refinement_queue_task(dict(request, source_snapshot_sha256="2" * 64))
    assert second["job_id"] != first["job_id"] and second["control_state"] == "QUEUED"
