"""A refined conversation that went on can be refined again.

Asking to refine a conversation that already had a finished refinement returned that old task
(deduplicated, TASK_SUCCEEDED) even after new messages were synced, and the page kept the button
disabled. A finished refinement is now reused only for the same messages. The task store is the real one;
the conversation, the inbox and the settings are stand-ins.
"""
from __future__ import annotations

from types import SimpleNamespace

from memorive.desktop.current_task.refinement_tasks import REFINEMENT_NODE_IDS
from test_refinement_start_failure import Queued


class Refined(Queued):
    def __init__(self, root):
        super().__init__(root)
        self.messages = [{"role": "user", "text": "First question."}, {"role": "assistant", "text": "First answer."}]
        self.service = SimpleNamespace(call=lambda method, params: {})
        self._conversation_refinement_channel = SimpleNamespace(execute=lambda request: None)
        self.enqueued = 0

    def _messages_for(self, row, presentation=False):
        return list(self.messages), {"status": "COMPLETE"}

    def _load_refinement_settings(self):
        return {"profile_ref": "model"}

    def _prepare_refinement_settings_for_start(self, settings):
        return settings, None

    def _find_inbox_refinement(self, projection_id):
        return None

    def _schedule_refinement_queue_item(self, item):
        pass

    def _inbox_refinement_call(self, method, params):
        if method == "inbox.enqueue_session_refinement":
            self.enqueued += 1
            return {"item": {"item_id": f"item-{self.enqueued}"}}
        return super()._inbox_refinement_call(method, params)


def _finish(store, job_id):
    for node in REFINEMENT_NODE_IDS:
        store.begin_step(job_id, node)
        store.complete_step(job_id, node)
    store.succeed(job_id, draft_id="draft-1", model_calls=1, external_model_calls=1)


def test_new_messages_make_a_new_refinement_and_the_same_messages_do_not(tmp_path):
    refined = Refined(tmp_path / "refinement")
    first = refined._create_refinement_task("conversation-1")
    _finish(refined._refinement_task_manager, first["job_id"])
    done = refined._refinement_task_manager.get_session_refinement_task(first["job_id"])

    again = refined._create_refinement_task("conversation-1")
    assert (again["job_id"], again["deduplicated"], again["status"]) == (first["job_id"], True, "TASK_SUCCEEDED")
    assert refined._refinement_source_changed("conversation-1", done) is False

    refined.messages += [{"role": "user", "text": "One more question."}, {"role": "assistant", "text": "One more answer."}]
    assert refined._refinement_source_changed("conversation-1", done) is True
    newer = refined._create_refinement_task("conversation-1")
    assert newer["job_id"] != first["job_id"] and newer["status"] == "INBOX_QUEUED"
