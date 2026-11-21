"""The refinement queue: RefinementQueueMixin.
"""
from __future__ import annotations

from copy import deepcopy
import json
import queue
import threading
import time
from typing import Any, Mapping
from memorive.desktop.current_task.refinement_tasks import REFINEMENT_NODE_IDS, safe_error_code
from memorive.sessions.controller.common import SessionProductError


class RefinementQueueMixin:
    """The queue of conversations waiting for refinement: scheduling, draining, notifications."""

    def _inbox_refinement_call(
        self, method: str, params: Mapping[str, Any]
    ) -> Any:
        return self.service.call(method, dict(params))

    def _find_inbox_refinement(
        self, projection_id: str
    ) -> dict[str, Any] | None:
        try:
            value = self._inbox_refinement_call(
                "inbox.find_session_refinement",
                {"local_projection_id": projection_id},
            )
        except (AssertionError, AttributeError):
            # Isolated legacy unit doubles predate the durable Inbox queue.
            return None
        return dict(value) if isinstance(value, Mapping) else None

    def _notify_refinement_lifecycle(self, job_id: str) -> None:
        observer = self._refinement_lifecycle_observer
        manager = self._refinement_task_manager
        if observer is None or manager is None:
            return
        try:
            task = manager.get_session_refinement_task(job_id)
            observer(
                {
                    "job_id": task["job_id"],
                    "attempt_id": task["attempt_id"],
                    "local_projection_id": task["local_projection_id"],
                    "display_name": task["display_name"],
                    "control_state": task["control_state"],
                    "error_code": task.get("error_code"),
                    "updated_at": task.get("updated_at"),
                }
            )
        except Exception:
            # Product notifications are projections.  They must never change
            # the execution result of the durable refinement task.
            self._metrics["refinement_lifecycle_projection_failures"] += 1
            return

    def _notify_refinement_queue_projection(self, item_id: str) -> None:
        observer = self._refinement_queue_observer
        if observer is None:
            return
        try:
            item = self._inbox_refinement_call(
                "inbox.item_detail", {"item_id": item_id}
            )
            if not isinstance(item, Mapping):
                raise SessionProductError("SESSION_REFINEMENT_QUEUE_ITEM_INVALID")
            observer(
                {
                    "item_id": item["item_id"],
                    "job_id": item.get("job_id"),
                    "local_projection_id": item.get("local_projection_id"),
                    "state": item.get("state"),
                    "updated_at": item.get("updated_at"),
                }
            )
        except Exception:
            # Queue state is already durable.  A failed UI projection must not
            # rewrite the queue or alter the model execution result.
            self._metrics["refinement_queue_projection_failures"] += 1
            return

    def _schedule_refinement_queue_item(self, payload: Mapping[str, Any]) -> None:
        if not self._refinement_auto_run_enabled():
            return
        item_id = str(payload["item_id"])
        with self._refinement_lock:
            if item_id in self._refinement_queue_items:
                return
            self._refinement_queue_items.add(item_id)
            self._refinement_queue.put(deepcopy(dict(payload)))
            worker = self._refinement_queue_worker
            if worker is not None and worker.is_alive():
                return
            worker = threading.Thread(
                target=self._drain_refinement_queue,
                name="memorive-session-refinement-inbox-queue",
                daemon=True,
            )
            self._refinement_queue_worker = worker
            worker.start()

    def _ensure_refinement_queue_task(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        # Enqueue and the background queue synchronizer can meet here. They
        # must reuse one durable task rather than race to create two workers.
        with self._refinement_lock:
            existing = self._refinement_task_for_projection(str(payload["local_projection_id"]))
            if existing is not None and existing.get("control_state") in {"QUEUED", "RUNNING"}:
                if str(existing["source_snapshot_sha256"]).upper() != str(payload["source_snapshot_sha256"]).upper():
                    raise SessionProductError("SESSION_REFINEMENT_SOURCE_SNAPSHOT_CHANGED")
                return existing
            task = self._refinement_task_manager.create_session_refinement_task(
                display_name=str(payload["display_name"]),
                local_projection_id=str(payload["local_projection_id"]),
                source_snapshot_sha256=str(payload["source_snapshot_sha256"]),
                selected_profile_ref=str(payload["selected_profile_ref"]),
                message_count=int(payload["message_count"]),
                language_context=payload.get("language_context"),
            )
            self._metrics["refinement_tasks_created"] += 1
            return task

    def _discard_pending_refinement_queue(self) -> int:
        """Forget queued work that has not been claimed by the worker yet."""

        discarded = 0
        while True:
            try:
                payload = self._refinement_queue.get_nowait()
            except queue.Empty:
                return discarded
            try:
                item_id = str(payload.get("item_id", ""))
                with self._refinement_lock:
                    self._refinement_queue_items.discard(item_id)
                discarded += 1
            finally:
                self._refinement_queue.task_done()

    def _drain_refinement_queue(self) -> None:
        while True:
            try:
                payload = self._refinement_queue.get_nowait()
            except queue.Empty:
                with self._refinement_lock:
                    self._refinement_queue_worker = None
                return
            item_id = str(payload["item_id"])
            try:
                if not self._refinement_auto_run_enabled():
                    self._discard_pending_refinement_queue()
                    with self._refinement_lock:
                        self._refinement_queue_worker = None
                    return
                item = self._inbox_refinement_call(
                    "inbox.item_detail", {"item_id": item_id}
                )
                if (
                    not isinstance(item, Mapping)
                    or item.get("item_kind") != "SESSION_REFINEMENT"
                    or item.get("state") != "QUEUED"
                ):
                    continue
                projection_id = str(payload["local_projection_id"])
                task = self._ensure_refinement_queue_task(payload)
                job_id = str(task["job_id"])
                self._notify_refinement_queue_projection(item_id)
                if task.get("control_state") == "QUEUED":
                    self._start_refinement_task(job_id)
                while True:
                    current = self._refinement_task_manager.get_session_refinement_task(
                        job_id
                    )
                    if current.get("control_state") in {
                        "SUCCEEDED",
                        "FAILED",
                        "CANCELLED",
                    }:
                        break
                    time.sleep(0.05)
                succeeded = current.get("control_state") == "SUCCEEDED"
                error_code = str(
                    current.get("error_code")
                    or (
                        "SESSION_REFINEMENT_CANCELLED"
                        if current.get("control_state") == "CANCELLED"
                        else ""
                    )
                )
                self._inbox_refinement_call(
                    "inbox.complete_session_refinement",
                    {
                        "item_id": item_id,
                        "succeeded": succeeded,
                        "error_code": error_code,
                    },
                )
                self._notify_refinement_queue_projection(item_id)
            except Exception as error:
                error_code = safe_error_code(
                    str(getattr(error, "code", None) or error)
                )
                try:
                    self._inbox_refinement_call(
                        "inbox.complete_session_refinement",
                        {
                            "item_id": item_id,
                            "succeeded": False,
                            "error_code": error_code,
                        },
                    )
                    self._notify_refinement_queue_projection(item_id)
                except Exception:
                    pass
            finally:
                with self._refinement_lock:
                    self._refinement_queue_items.discard(item_id)
                self._refinement_queue.task_done()

    def end_interrupted_refinements(self) -> dict[str, Any]:
        """Called once when the app starts, before the queue runs.

        A refinement whose worker had started when the app closed has no worker now. Its task stayed RUNNING
        (or QUEUED, when the app closed just after its inbox card became 处理中), the card showed 处理中 for
        good, a new request for the conversation got the old task back, and the queue waited for it. Such a
        task ends here: as it would have, when its draft had been written (the draft is its result), and
        otherwise FAILED with SESSION_REFINEMENT_INTERRUPTED. Its inbox card ends the same way.
        """
        manager = self._refinement_task_manager
        processing = {}  # job id -> inbox item id, for the cards still 处理中
        projection = self._inbox_refinement_call("inbox.projection", {})
        for card in projection.get("cards", []) if isinstance(projection, Mapping) else []:
            if card.get("item_kind") == "SESSION_REFINEMENT" and card.get("state") == "PROCESSING":
                detail = self._inbox_refinement_call("inbox.item_detail", {"item_id": card["item_id"]})
                if isinstance(detail, Mapping) and detail.get("job_id"):
                    processing[detail["job_id"]] = detail["item_id"]
        ended = []
        for task in manager.list_jobs():
            job_id = task["job_id"]
            if not (task["control_state"] == "RUNNING"
                    or (task["control_state"] == "QUEUED" and job_id in processing)):
                continue  # finished, or not started yet (the queue starts it)
            draft = self._written_refinement_draft(task["local_projection_id"], job_id)
            if draft is not None:
                for node in task["nodes"]:
                    if node["state"] == "RUNNING":
                        manager.complete_session_refinement_step(job_id, node["node_id"])
                task = manager.succeed_session_refinement_task(
                    job_id, draft_id=draft["draft_id"], model_calls=1,
                    external_model_calls=int(draft["external_model_calls"]))
            else:
                answered = any(node["node_id"] == REFINEMENT_NODE_IDS[2] and node["state"] == "COMPLETED"
                               for node in task["nodes"])
                task = manager.fail_session_refinement_task(
                    job_id, error_code="SESSION_REFINEMENT_INTERRUPTED", model_calls=int(answered))
            if job_id in processing:
                self._inbox_refinement_call("inbox.complete_session_refinement", {
                    "item_id": processing[job_id], "succeeded": task["control_state"] == "SUCCEEDED",
                    "error_code": task["error_code"]})
            ended.append(job_id)
        return {"schema_version": "DesktopSessionRefinementRestart-v1", "ended_job_ids": ended}

    def _written_refinement_draft(self, projection_id: str, job_id: str) -> dict[str, Any] | None:
        """The draft this task wrote for its conversation, or None."""
        try:
            draft = json.loads((self._refinement_root / f"{projection_id}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return draft if isinstance(draft, Mapping) and draft.get("task_id") == job_id else None

    def _refinement_auto_run_enabled(self) -> bool:
        setting = self._inbox_refinement_call("inbox.get_auto_run", {})
        return isinstance(setting, Mapping) and setting.get("enabled") is True

    def sync_refinement_queue(self) -> dict[str, Any]:
        """Schedule durable Inbox refinement intents only while auto-run is on."""

        if not self._refinement_auto_run_enabled():
            discarded = self._discard_pending_refinement_queue()
            return {
                "schema_version": "DesktopSessionRefinementQueueSync-v1",
                "auto_run": False,
                "scheduled_count": 0,
                "discarded_count": discarded,
                "status": "PAUSED",
            }
        projection = self._inbox_refinement_call("inbox.projection", {})
        cards = projection.get("cards", []) if isinstance(projection, Mapping) else []
        scheduled = 0
        for card in cards:
            if (
                not isinstance(card, Mapping)
                or card.get("item_kind") != "SESSION_REFINEMENT"
                or card.get("state") != "QUEUED"
            ):
                continue
            detail = self._inbox_refinement_call(
                "inbox.item_detail", {"item_id": card.get("item_id")}
            )
            if not isinstance(detail, Mapping):
                continue
            payload = {
                "item_id": detail["item_id"],
                "local_projection_id": detail["local_projection_id"],
                "display_name": detail["name"],
                "language_context": detail.get("language_context"),
                "source_snapshot_sha256": detail["source_snapshot_sha256"],
                "selected_profile_ref": detail["selected_profile_ref"],
                "message_count": detail["message_count"],
            }
            with self._refinement_lock:
                already_scheduled = str(detail["item_id"]) in self._refinement_queue_items
            self._schedule_refinement_queue_item(payload)
            if not already_scheduled:
                scheduled += 1
        return {
            "schema_version": "DesktopSessionRefinementQueueSync-v1",
            "auto_run": True,
            "scheduled_count": scheduled,
            "status": "PASS",
        }
