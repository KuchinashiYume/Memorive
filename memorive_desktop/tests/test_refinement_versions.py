"""Each refinement of a session is kept as its own version.

A refinement wrote <session>.json, so refining a session again replaced the earlier result, and while the new
one waited or ran the page showed nothing. Each job now keeps its draft in <session>/<job>.json;
sessions.refinement_get lists the versions and shows the newest (or one asked for), also while a newer one is
queued, runs or failed. A draft made before v2.00 (<session>.json) reads as the earliest version, and a task
that wrote one just before the app closed is still found at start.
"""
from __future__ import annotations

import json

import pytest

from memorive.sessions.controller import SessionProductError
from memorive.sessions.controller.product_controller import SessionsProductController
from test_refinement_start_failure import Queued


class _Page(Queued):
    def __init__(self, root):
        super().__init__(root)
        self._refinement_root = root / "drafts"
        self._refinement_root.mkdir()
        self.changed = False

    def _require_keys(self, accepted, keys, optional=None):
        SessionsProductController._require_keys(accepted, keys, optional)

    def _refinement_source_changed(self, projection_id, made):
        return self.changed

    def _refinement_auto_run_enabled(self):
        return True

    def get(self, **params):
        return SessionsProductController._get_refinement(self, {"local_projection_id": "conversation-1", **params})


def _draft(page, job_id, created_at, **content):
    draft = {"task_id": job_id, "created_at": created_at, "local_projection_id": "conversation-1", **content}
    path = page._refinement_draft_path("conversation-1", job_id)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(draft), encoding="utf-8")
    return draft


def test_versions_are_kept_and_the_newest_is_shown(tmp_path):
    page = _Page(tmp_path)
    old = {"task_id": "job-0", "created_at": "2026-11-01T00:00:00Z", "items": [{}, {}]}   # made before v2.00
    (page._refinement_root / "conversation-1.json").write_text(json.dumps(old), encoding="utf-8")
    first = _draft(page, "job-1", "2026-11-20T00:00:00Z", brief={"items": [{}]})
    second = _draft(page, "job-2", "2026-11-22T00:00:00Z", brief={"items": []})

    shown = page.get()
    assert (shown["status"], shown["draft"]) == ("PASS", second)
    assert shown["versions"] == [
        {"job_id": "job-0", "created_at": "2026-11-01T00:00:00Z", "item_count": 2, "brief": False},
        {"job_id": "job-1", "created_at": "2026-11-20T00:00:00Z", "item_count": 1, "brief": True},
        {"job_id": "job-2", "created_at": "2026-11-22T00:00:00Z", "item_count": 0, "brief": True}]
    assert page.get(job_id="job-1")["draft"] == first
    assert page.get(job_id="job-0")["draft"] == old
    with pytest.raises(SessionProductError, match="SESSION_REFINEMENT_DRAFT_NOT_FOUND"):
        page.get(job_id="job-9")


def test_the_newest_version_stays_while_a_newer_one_waits_or_fails(tmp_path):
    page = _Page(tmp_path)
    first = _draft(page, "job-1", "2026-11-20T00:00:00Z", brief={"items": []})
    page.changed = True
    assert page.get()["source_changed"] is True
    task = page._ensure_refinement_queue_task({
        "item_id": "item-1", "display_name": "A conversation", "local_projection_id": "conversation-1",
        "source_snapshot_sha256": "2" * 64, "selected_profile_ref": "model", "message_count": 3})

    waiting = page.get()
    assert (waiting["status"], waiting["draft"], len(waiting["versions"])) == ("TASK_QUEUED", first, 1)
    page._refinement_task_manager.fail_session_refinement_task(task["job_id"], error_code="SESSION_REFINEMENT_INTERRUPTED")
    assert (page.get()["status"], page.get()["draft"]) == ("TASK_FAILED", first)


def test_a_task_that_wrote_its_draft_is_found_in_either_layout(tmp_path):
    page = _Page(tmp_path)
    written = _draft(page, "job-1", "2026-11-22T00:00:00Z", brief={"items": []})
    assert page._written_refinement_draft("conversation-1", "job-1") == written
    old = {"task_id": "job-0", "created_at": "2026-11-01T00:00:00Z", "items": [{}]}
    (page._refinement_root / "conversation-1.json").write_text(json.dumps(old), encoding="utf-8")
    assert page._written_refinement_draft("conversation-1", "job-0") == old
    assert page._written_refinement_draft("conversation-1", "job-9") is None
