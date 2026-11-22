"""Refinement tasks: RefinementTasksMixin.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import re
import threading
from typing import Any, Iterable, Mapping
import uuid
from memorive.sessions.archive.models import BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1
from memorive.desktop.current_task.refinement_tasks import (
    REFINEMENT_NODE_IDS,
    RefinementTaskCancelled,
    RefinementTaskError,
    safe_error_code,
)
from memorive.sessions.controller.common import (
    REAL_CHANNELS,
    SessionProductError,
    _UNIVERSAL_PROVIDER_ID,
    _sha256_json,
    _text,
)


class RefinementTasksMixin:
    """Refinement tasks: the settings they run with, and starting, running and cancelling them."""

    def _configured_refinement_models(self) -> list[dict[str, Any]]:
        from memorive.sessions.refinement.model_choices import configured_refinement_models
        try:
            projection = self.service.call("settings.get_state", {})
        except Exception:
            projection = {}
        return configured_refinement_models(projection, self._local_model_registry)

    @staticmethod
    def _legacy_local_profile_ref(option: Mapping[str, Any]) -> str:
        endpoint_id = _text(option.get("endpoint_id"), 120)
        model_name = _text(option.get("model_name"), 160)
        if not endpoint_id or not model_name:
            return ""
        model_suffix = hashlib.sha256(model_name.encode("utf-8")).hexdigest()[:20]
        return f"local-model:{endpoint_id}:{model_suffix}"

    @classmethod
    def _selected_refinement_option(
        cls,
        profile_ref: Any,
        model_options: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any] | None:
        accepted_ref = _text(profile_ref, 192)
        for row in model_options:
            if row.get("profile_ref") == accepted_ref:
                return dict(row)
        if not re.fullmatch(
            r"local-model:local-model-[0-9a-f]{20}:[0-9a-f]{20}",
            accepted_ref,
            re.IGNORECASE,
        ):
            return None
        matches = [
            dict(row)
            for row in model_options
            if row.get("profile_kind") == "LOCAL"
            and cls._legacy_local_profile_ref(row).casefold() == accepted_ref.casefold()
        ]
        return matches[0] if len(matches) == 1 else None

    def _prepare_refinement_settings_for_start(
        self,
        settings: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        accepted_settings = deepcopy(dict(settings))
        model_options = self._configured_refinement_models()
        selected = self._selected_refinement_option(
            accepted_settings.get("profile_ref"), model_options
        )
        if selected is None:
            raise SessionProductError("SESSION_REFINEMENT_PROFILE_NOT_CONFIGURED")
        if selected.get("execution_eligible") is not True:
            endpoint_id = _text(selected.get("endpoint_id"), 120)
            model_name = _text(selected.get("model_name"), 160)
            if (
                selected.get("profile_kind") != "LOCAL"
                or selected.get("identity_refresh_supported") is not True
                or self._local_model_registry is None
                or not endpoint_id
                or not model_name
            ):
                raise SessionProductError(
                    "SESSION_REFINEMENT_PROFILE_EXECUTION_UNAVAILABLE"
                )
            verification = self._local_model_registry.call(
                "local_models.verify", {"endpoint_id": endpoint_id}
            )
            if (
                not isinstance(verification, Mapping)
                or verification.get("service_reachable") is not True
                or model_name not in verification.get("models", [])
                or verification.get("inference_run") is not False
            ):
                raise SessionProductError("SESSION_REFINEMENT_LOCAL_IDENTITY_REFRESH_FAILED")
            model_options = self._configured_refinement_models()
            selected = next(
                (
                    dict(row)
                    for row in model_options
                    if row.get("profile_ref") == selected.get("profile_ref")
                ),
                None,
            )
            if selected is None or selected.get("execution_eligible") is not True:
                raise SessionProductError("SESSION_REFINEMENT_LOCAL_IDENTITY_REFRESH_FAILED")
        canonical_ref = _text(selected.get("profile_ref"), 192)
        if accepted_settings.get("profile_ref") != canonical_ref:
            accepted_settings["profile_ref"] = canonical_ref
            self._write_private_json(self._refinement_settings_path, accepted_settings)
            self._metrics["local_projection_writes"] += 1
        return accepted_settings, selected

    def _refinement_settings_projection(self) -> dict[str, Any]:
        settings = self._load_refinement_settings()
        configured = bool(settings.get("profile_ref"))
        model_options = self._configured_refinement_models()
        selected_option = self._selected_refinement_option(
            settings.get("profile_ref"), model_options
        )
        selected_execution_eligible = bool(
            selected_option is not None
            and selected_option.get("execution_eligible") is True
        )
        execute = getattr(
            self._conversation_refinement_channel,
            "execute",
            None,
        )
        selected_start_eligible = bool(
            selected_execution_eligible
            or selected_option is not None
            and selected_option.get("profile_kind") == "LOCAL"
            and selected_option.get("identity_refresh_supported") is True
        )
        execution_ready = bool(
            callable(execute)
            and self._refinement_task_manager is not None
            and selected_start_eligible
        )
        return {
            "schema_version": "DesktopSessionRefinementSettingsProjection-v1",
            "settings": settings,
            "model_options": model_options,
            "configured": configured,
            "execution_mode": (
                "EXISTING_PROFILE_STRUCTURED_CHAT_EXECUTION"
                if configured and execution_ready
                else "REFINEMENT_PROFILE_EXECUTION_UNAVAILABLE"
                if configured
                and selected_option is not None
                and not selected_execution_eligible
                else "CONVERSATION_REFINEMENT_CHANNEL_NOT_AVAILABLE"
                if configured and selected_option is not None
                else "REFINEMENT_PROFILE_NOT_CONFIGURED"
                if configured
                else "MODEL_NOT_CONFIGURED"
            ),
            "selected_profile_kind": (
                selected_option.get("profile_kind")
                if selected_option is not None
                else None
            ),
            "selected_profile_execution_eligible": selected_execution_eligible,
            "selected_profile_start_eligible": selected_start_eligible,
            "selected_profile_execution_status": (
                "LOCAL_IDENTITY_REFRESH_REQUIRED"
                if selected_option is not None
                and selected_option.get("profile_kind") == "LOCAL"
                and not selected_execution_eligible
                and selected_start_eligible
                else selected_option.get("execution_status")
                if selected_option is not None
                else "PROFILE_NOT_CONFIGURED"
                if configured
                else "MODEL_NOT_CONFIGURED"
            ),
            "execution_ready": execution_ready,
            # What the settings and session pages say about the chosen model.
            "model_state": (
                "UNSELECTED" if not configured
                else "REMOVED" if selected_option is None
                else "READY" if execution_ready
                else "UNAVAILABLE"
            ),
            "external_model_calls": 0,
            "review_required": True,
            "auto_ingest": False,
            "status": "PASS",
        }

    def _refinement_task_for_projection(self, projection_id: str) -> dict[str, Any] | None:
        manager = self._refinement_task_manager
        finder = getattr(manager, "find_session_refinement_task", None)
        if not callable(finder):
            return None
        result = finder(projection_id)
        return dict(result) if isinstance(result, Mapping) else None

    @staticmethod
    def _refinement_source_content_allowed(
        row: Mapping[str, Any],
        messages: list[dict[str, Any]],
        completeness: Mapping[str, Any],
    ) -> bool:
        if not messages:
            return False
        status = str(completeness.get("status") or "")
        if status == "COMPLETE":
            return True
        lineage = row.get("lineage")
        return (
            status == "PARTIAL"
            and row.get("provider_id") == _UNIVERSAL_PROVIDER_ID
            and row.get("source_format") == "browser_current_page_v1"
            and isinstance(lineage, Mapping)
            and lineage.get("source_kind") == BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1
        )

    @staticmethod
    def _refinement_channel_messages(
        messages: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, str]]:
        """Project rich archive rows onto the exact Capabilities channel contract."""

        projected: list[dict[str, str]] = []
        for message in messages:
            if not isinstance(message, Mapping):
                raise SessionProductError("SESSION_REFINEMENT_MESSAGE_INVALID")
            role = str(message.get("role") or "").strip().lower()
            content = message.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str):
                raise SessionProductError("SESSION_REFINEMENT_MESSAGE_INVALID")
            if not content.strip():
                continue
            projected.append({"role": role, "content": content})
        if not projected:
            raise SessionProductError("SESSION_REFINEMENT_REQUIRES_COMPLETE_CONTENT")
        return projected

    def _run_refinement_task(
        self,
        *,
        job_id: str,
        projection_id: str,
        snapshot_sha256: str,
        messages: list[dict[str, str]],
        source_binding: Mapping[str, Any],
        settings: Mapping[str, Any],
    ) -> None:
        manager = self._refinement_task_manager
        model_calls = 0
        from memorive.models.scheduling import TASK_CATEGORIES
        gate = TASK_CATEGORIES.enter('REFINEMENT', checkpoint=lambda: self._refinement_wait_checkpoint(job_id))
        admitted = False
        try:
            gate.__enter__()
            admitted = True
            item = self._find_inbox_refinement(projection_id)
            if item:
                self._inbox_refinement_call('inbox.begin_session_refinement', {'item_id': item['item_id'], 'job_id': job_id})
            manager.begin_session_refinement_step(job_id, REFINEMENT_NODE_IDS[0])
            self._notify_refinement_lifecycle(job_id)
            if _sha256_json({"source": source_binding, "messages": messages}) != snapshot_sha256:
                raise SessionProductError("SESSION_REFINEMENT_FROZEN_SNAPSHOT_DRIFT")
            if manager.session_refinement_cancelled(job_id):
                return
            manager.complete_session_refinement_step(job_id, REFINEMENT_NODE_IDS[0])

            manager.begin_session_refinement_step(job_id, REFINEMENT_NODE_IDS[1])
            if any(
                not isinstance(message, Mapping)
                or not _text(message.get("role"), 80)
                or not _text(message.get("content"), 200000)
                for message in messages
            ):
                raise SessionProductError("SESSION_REFINEMENT_SOURCE_MESSAGE_INVALID")
            channel_messages = deepcopy(messages)
            if manager.session_refinement_cancelled(job_id):
                return
            manager.complete_session_refinement_step(job_id, REFINEMENT_NODE_IDS[1])

            current_task = manager.get_session_refinement_task(job_id)
            settings = {
                **deepcopy(dict(settings)),
                "profile_ref": current_task["selected_profile_ref"],
            }
            manager.begin_session_refinement_step(job_id, REFINEMENT_NODE_IDS[2])
            execute = getattr(
                self._conversation_refinement_channel,
                "execute",
                None,
            )
            if not callable(execute):
                raise SessionProductError("SESSION_REFINEMENT_CHANNEL_UNAVAILABLE")
            request = {
                "task_id": job_id,
                **({"language_context":current_task["language_context"]} if current_task.get("language_context") is not None else {}),
                "logical_slot": "conversation_refinement",
                "profile_ref": settings["profile_ref"],
                "explicit_user_action": True,
                "source_snapshot_sha256": snapshot_sha256,
                "source": deepcopy(dict(source_binding)),
                "messages": channel_messages,
                # The settings page's 提取待办 / 提取风险 switches, as saved when the task started.
                "options": {"include_tasks": settings.get("include_tasks") is not False,
                            "include_risks": settings.get("include_risks") is not False},
                "review_required": True,
                "auto_ingest": False,
            }
            # The channel builds this result in this process from checked parts; only secret
            # fields are looked for before it is stored with the draft.
            result = execute(request)
            self._reject_sensitive_channel_result(result)
            model_calls = 1
            self._metrics["refinement_model_calls"] += 1
            self._metrics["external_model_calls"] += int(result["external_model_calls"])
            self._metrics["external_network_calls"] += int(result["external_network_calls"])
            self._metrics["provider_calls"] += int(result["provider_calls"])
            if manager.session_refinement_cancelled(job_id):
                return
            manager.complete_session_refinement_step(job_id, REFINEMENT_NODE_IDS[2])

            manager.begin_session_refinement_step(job_id, REFINEMENT_NODE_IDS[3])
            draft_id = f"refinement-{job_id.removeprefix('refinement-')[:32]}"
            draft = {
                "schema_version": "DesktopSessionRefinementDraft-v4",
                **({"language_context":current_task["language_context"]} if current_task.get("language_context") is not None else {}),
                "draft_id": draft_id,
                "task_id": job_id,
                "display_name": current_task["display_name"],
                "local_projection_id": projection_id,
                "source_snapshot_sha256": snapshot_sha256,
                "source": deepcopy(dict(source_binding)),
                "message_count": len(channel_messages),
                "selected_profile_ref": settings["profile_ref"],
                "requested_model": result["requested_model"],
                "returned_model": result["returned_model"],
                "execution_mode": "EXISTING_CONVERSATION_REFINEMENT_CHANNEL",
                "logical_slot": "conversation_refinement",
                "brief": result["brief"],
                "execution_receipt": result["execution_receipt"],
                "review_required": True,
                "auto_ingest": False,
                "model_calls": 1,
                "external_model_calls": result["external_model_calls"],
                "external_network_calls": result["external_network_calls"],
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
            if manager.session_refinement_cancelled(job_id):
                return
            with self._refinement_lock:
                self._write_private_json(
                    self._refinement_root / f"{projection_id}.json", draft
                )
            self._metrics["local_projection_writes"] += 1
            self._metrics["refinement_drafts_created"] += 1
            if self._refinement_result_observer is not None:
                self._refinement_result_observer(deepcopy(draft))
            manager.complete_session_refinement_step(job_id, REFINEMENT_NODE_IDS[3])
            manager.succeed_session_refinement_task(
                job_id,
                draft_id=draft_id,
                model_calls=1,
                external_model_calls=int(result["external_model_calls"]),
            )
            self._notify_refinement_lifecycle(job_id)
        except RefinementTaskCancelled:
            return
        except Exception as error:
            error_code = (
                str(getattr(error, "code"))
                if isinstance(getattr(error, "code", None), str)
                else str(error)
                if isinstance(error, SessionProductError)
                else "SESSION_REFINEMENT_EXECUTION_FAILED"
            )
            self._metrics["refinement_tasks_failed"] += 1
            try:
                if not manager.session_refinement_cancelled(job_id):
                    manager.fail_session_refinement_task(
                        job_id,
                        error_code=safe_error_code(error_code),
                        model_calls=model_calls,
                    )
                    self._notify_refinement_lifecycle(job_id)
            except Exception:
                pass
        finally:
            if admitted:
                gate.__exit__(None, None, None)
            with self._refinement_lock:
                self._refinement_workers.pop(job_id, None)

    def _refinement_wait_checkpoint(self, job_id):
        if self._refinement_task_manager.session_refinement_cancelled(job_id):
            raise RefinementTaskCancelled('REFINEMENT_TASK_CANCELLED')

    def _refinement_input(self, projection_id: str) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, Any], str]:
        """What a refinement of this session reads, and its hash.

        The messages are the user and assistant text the session page shows: runtime envelopes and
        meta records of Codex and Claude Code are left out, text mixed with them is kept. The hash
        covers the session's identity, its coverage and the messages in order, nothing else: a re-sync,
        a new title or appended tool logs keep the existing refinement; new conversation makes a new one.
        """
        row = self._get_internal(projection_id)
        source_messages, completeness = self._messages_for(row, presentation=True)
        if not self._refinement_source_content_allowed(row, source_messages, completeness):
            raise SessionProductError("SESSION_REFINEMENT_REQUIRES_COMPLETE_CONTENT")
        messages = self._refinement_channel_messages(source_messages)
        source_binding = {
            "schema_version": "DesktopSessionRefinementSourceBinding-v2",
            "local_projection_id": projection_id,
            "provider_id": _text(row.get("provider_id"), 80),
            "provider_session_id": _text(row.get("provider_session_id"), 240),
            "source_format": _text(row.get("source_format"), 160),
            "coverage": _text(completeness.get("status"), 40) or "UNKNOWN",
        }
        return row, messages, source_binding, _sha256_json({"source": source_binding, "messages": messages})

    def _refinement_source_changed(self, projection_id: str, task: Mapping[str, Any]) -> bool:
        """Whether the session changed since this (finished) refinement was made."""
        current = self._refinement_input(projection_id)[3]
        return str(task.get("source_snapshot_sha256") or "").upper() != current.upper()

    def _create_refinement_task(self, projection_id: str) -> dict[str, Any]:
        row, messages, _source_binding, snapshot_sha256 = self._refinement_input(projection_id)
        settings = self._load_refinement_settings()
        if not settings.get("profile_ref"):
            raise SessionProductError("SESSION_REFINEMENT_MODEL_NOT_CONFIGURED")
        settings, selected_option = self._prepare_refinement_settings_for_start(settings)
        manager = self._refinement_task_manager
        if manager is None:
            raise SessionProductError("SESSION_REFINEMENT_TASK_MANAGER_NOT_CONFIGURED")
        if not callable(
            getattr(
                self._conversation_refinement_channel,
                "execute",
                None,
            )
        ):
            raise SessionProductError("SESSION_REFINEMENT_CHANNEL_UNAVAILABLE")
        active_task = self._refinement_task_for_projection(projection_id)
        control_state = str(active_task.get("control_state") or "") if active_task else ""
        # A finished refinement of the same messages is returned as it is; after new messages a new one is made.
        if active_task is not None and (control_state in {"QUEUED", "RUNNING"} or (
                control_state == "SUCCEEDED"
                and str(active_task.get("source_snapshot_sha256") or "").upper() == snapshot_sha256.upper())):
            return {
                "schema_version": "DesktopSessionRefineReceipt-v6",
                "local_projection_id": projection_id,
                "job_id": active_task["job_id"],
                "inbox_item_id": None,
                "workflow_kind": "SESSION_REFINEMENT",
                "deduplicated": True,
                "model_calls": int(active_task.get("model_calls", 0)),
                "status": {
                    "QUEUED": "TASK_QUEUED",
                    "RUNNING": "TASK_RUNNING",
                    "SUCCEEDED": "TASK_SUCCEEDED",
                }.get(control_state, "TASK_STATE_UNKNOWN"),
            }
        inbox_item = self._find_inbox_refinement(projection_id)
        if inbox_item is not None and inbox_item.get("state") in {
            "QUEUED",
            "PROCESSING",
        }:
            return {
                "schema_version": "DesktopSessionRefineReceipt-v6",
                "local_projection_id": projection_id,
                "job_id": inbox_item.get("job_id") or None,
                "inbox_item_id": inbox_item["item_id"],
                "workflow_kind": "SESSION_REFINEMENT",
                "deduplicated": True,
                "model_calls": 0,
                "status": "INBOX_QUEUED",
            }
        if inbox_item is not None and inbox_item.get("state") == "ERROR":
            self._inbox_refinement_call(
                "inbox.cancel_session_refinement",
                {
                    "item_id": inbox_item["item_id"],
                    "operation_id": "refinement-retry-retire-" + uuid.uuid4().hex,
                },
            )
        from memorive.platform.language import from_settings
        from memorive.platform.language.text import choose
        language_context=from_settings(self.service)
        display_name = choose(language_context,'精炼对话','Conversation refinement','会話の精錬')+' · '+(_text(row.get('title'),120) or projection_id)
        # An active Inbox item is the duplicate guard.  A fresh key permits a
        # user-requested retry after a failed/cancelled card has been removed.
        queue_key = "refinement-" + uuid.uuid4().hex
        try:
            queued = self._inbox_refinement_call(
                "inbox.enqueue_session_refinement",
                {
                    "local_projection_id": projection_id,
                    "display_name": display_name,
                    "source_snapshot_sha256": snapshot_sha256,
                    "selected_profile_ref": str(settings["profile_ref"]),
                    "message_count": len(messages),
                    "idempotency_key": queue_key,
                    "language_context": language_context,
                },
            )
        except (AssertionError, AttributeError):
            queued = None
        if isinstance(queued, Mapping):
            item = queued.get("item")
            if not isinstance(item, Mapping) or not item.get("item_id"):
                raise SessionProductError("SESSION_REFINEMENT_INBOX_RECEIPT_INVALID")
            # Persist the editable task while it is queued, before the worker
            # freezes execution. Previously there was no task during this
            # window, making the existing model selector unreachable.
            task = self._ensure_refinement_queue_task({
                "language_context": language_context,
                "display_name": display_name,
                "local_projection_id": projection_id,
                "source_snapshot_sha256": snapshot_sha256,
                "selected_profile_ref": str(settings["profile_ref"]),
                "message_count": len(messages),
            })
            self._schedule_refinement_queue_item(
                {
                    "item_id": item["item_id"],
                    "local_projection_id": projection_id,
                    "display_name": display_name,
                    "source_snapshot_sha256": snapshot_sha256,
                    "selected_profile_ref": settings["profile_ref"],
                    "message_count": len(messages),
                }
            )
            return {
                "schema_version": "DesktopSessionRefineReceipt-v6",
                "local_projection_id": projection_id,
                "job_id": task["job_id"],
                "inbox_item_id": item["item_id"],
                "workflow_kind": "SESSION_REFINEMENT",
                "source_snapshot_sha256": snapshot_sha256,
                "selected_profile_ref": settings["profile_ref"],
                "deduplicated": queued.get("replayed") is True,
                "review_required": True,
                "auto_ingest": False,
                "model_calls": 0,
                "status": "INBOX_QUEUED",
            }
        task = manager.create_session_refinement_task(
            display_name=display_name,
            local_projection_id=projection_id,
            source_snapshot_sha256=snapshot_sha256,
            selected_profile_ref=str(settings["profile_ref"]),
            message_count=len(messages),
            language_context=language_context,
        )
        job_id = str(task["job_id"])
        self._metrics["refinement_tasks_created"] += 1
        return {
            "schema_version": "DesktopSessionRefineReceipt-v5",
            "local_projection_id": projection_id,
            "job_id": job_id,
            "attempt_id": task["attempt_id"],
            "workflow_kind": "SESSION_REFINEMENT",
            "workflow_node_ids": list(REFINEMENT_NODE_IDS),
            "source_snapshot_sha256": snapshot_sha256,
            "selected_profile_ref": settings["profile_ref"],
            "review_required": True,
            "auto_ingest": False,
            "model_calls": 0,
            "status": "TASK_CREATED",
        }

    def _start_refinement_task(self, job_id: str) -> dict[str, Any]:
        manager = self._refinement_task_manager
        if manager is None:
            raise SessionProductError("SESSION_REFINEMENT_TASK_MANAGER_NOT_CONFIGURED")
        task = manager.get_session_refinement_task(job_id)
        if task.get("control_state") != "QUEUED":
            raise SessionProductError("SESSION_REFINEMENT_TASK_NOT_QUEUED")
        try:
            projection_id, messages, source_binding, snapshot_sha256, task_settings = self._refinement_start_inputs(task)
        except SessionProductError as error:
            # The task cannot start as it was queued (its source changed, or its content is incomplete):
            # end it, so the next request for this conversation makes a new attempt instead of finding it.
            manager.fail_session_refinement_task(job_id, error_code=str(error))
            raise
        worker = threading.Thread(
            target=self._run_refinement_task,
            kwargs={
                "job_id": job_id,
                "projection_id": projection_id,
                "snapshot_sha256": snapshot_sha256,
                "messages": messages,
                "source_binding": source_binding,
                "settings": deepcopy(task_settings),
            },
            name=f"memorive-session-refinement-{job_id[-12:]}",
            daemon=True,
        )
        with self._refinement_lock:
            if job_id in self._refinement_workers:
                raise SessionProductError("SESSION_REFINEMENT_TASK_ALREADY_STARTING")
            self._refinement_workers[job_id] = worker
        worker.start()
        return {
            "schema_version": "DesktopSessionRefinementStartReceipt-v1",
            "local_projection_id": projection_id,
            "job_id": job_id,
            "attempt_id": task["attempt_id"],
            "source_snapshot_sha256": snapshot_sha256,
            "selected_profile_ref": task_settings["profile_ref"],
            "model_calls": 0,
            "status": "TASK_STARTED",
        }

    def _refinement_start_inputs(self, task: dict[str, Any]) -> tuple:
        """What a queued task starts with: its conversation's messages and source binding, checked
        against the snapshot the task was queued with, and the settings it runs with."""
        projection_id = str(task["local_projection_id"])
        _row, messages, source_binding, snapshot_sha256 = self._refinement_input(projection_id)
        if snapshot_sha256.upper() != str(task["source_snapshot_sha256"]).upper():
            raise SessionProductError("SESSION_REFINEMENT_FROZEN_SNAPSHOT_DRIFT")
        saved_settings = self._load_refinement_settings()
        task_settings = {
            **saved_settings,
            "profile_ref": task["selected_profile_ref"],
        }
        task_settings, _ = self._prepare_refinement_settings_for_start(task_settings)
        return projection_id, messages, source_binding, snapshot_sha256, task_settings

    def _cancel_refinement_task(self, inbox_item_id: str) -> dict[str, Any]:
        item_id = str(inbox_item_id or "").strip()
        if not item_id:
            raise SessionProductError("SESSION_REFINEMENT_INBOX_ITEM_REQUIRED")
        item = self._inbox_refinement_call("inbox.item_detail", {"item_id": item_id})
        if not isinstance(item, Mapping) or item.get("item_kind") != "SESSION_REFINEMENT":
            raise SessionProductError("SESSION_REFINEMENT_INBOX_ITEM_REQUIRED")
        job_id = str(item.get("job_id") or "")
        if not job_id and self._refinement_task_manager is not None:
            queued_task = self._refinement_task_for_projection(str(item.get("local_projection_id") or ""))
            if (queued_task is not None
                    and queued_task.get("control_state") == "QUEUED"
                    and queued_task.get("source_snapshot_sha256") == item.get("source_snapshot_sha256")):
                job_id = str(queued_task["job_id"])
        task_state = "NOT_CREATED"
        if job_id and self._refinement_task_manager is not None:
            try:
                task = self._refinement_task_manager.get_session_refinement_task(job_id)
                task_state = str(task.get("control_state") or "")
                if task_state in {"QUEUED", "RUNNING"}:
                    cancel = getattr(
                        self._refinement_task_manager,
                        "cancel_session_refinement_task",
                        None,
                    ) or getattr(self._refinement_task_manager, "cancel", None)
                    if not callable(cancel):
                        raise SessionProductError(
                            "SESSION_REFINEMENT_CANCEL_CHANNEL_UNAVAILABLE"
                        )
                    task = cancel(job_id)
                    task_state = str(task.get("control_state") or "CANCELLED")
                    self._notify_refinement_lifecycle(job_id)
            except KeyError:
                task_state = "NOT_CREATED"
            except RefinementTaskError as error:
                if str(error) != "REFINEMENT_TASK_NOT_FOUND":
                    raise
                task_state = "NOT_CREATED"
        removal = self._inbox_refinement_call(
            "inbox.cancel_session_refinement",
            {
                "item_id": item_id,
                "operation_id": "refinement-cancel-" + uuid.uuid4().hex,
            },
        )
        self._notify_refinement_queue_projection(item_id)
        return {
            "schema_version": "DesktopSessionRefinementCancelReceipt-v2",
            "inbox_item_id": item_id,
            "job_id": job_id or None,
            "task_state": task_state,
            "permanent_deletes": 0,
            "removal": deepcopy(dict(removal)) if isinstance(removal, Mapping) else None,
            "status": "CANCELLED",
        }

    def _capture_intent(self, channel: str) -> dict[str, Any]:
        if channel == "exact_local_cli_session_root":
            return {
                "schema_version": "SessionsCaptureIntentReceipt-v2",
                "channel": channel,
                "status": "ALREADY_CONNECTED_READ_ONLY",
                "capture_started": False,
                "metadata_first": True,
                "selected_detail_on_demand": True,
            }
        if channel == "existing_login_visible_ui":
            refresh = self._refresh()
            status = refresh["source_status"].get("deepseek", {})
            return {
                "schema_version": "SessionsCaptureIntentReceipt-v2",
                "channel": channel,
                "status": status.get("status", "VISIBLE_UI_READ_FAILED"),
                "capture_started": False,
                "metadata_first": True,
                "selected_detail_on_demand": True,
                "record_count": int(status.get("record_count", 0)),
                "remote_session_mutated": False,
            }
        if channel in REAL_CHANNELS:
            return {
                "schema_version": "SessionsCaptureIntentReceipt-v2",
                "channel": channel,
                "status": "DISABLED_NOT_ASSESSED",
                "capture_started": False,
                "reason": "CHANNEL_NOT_ENABLED_FOR_VISIBLE_CHAT",
            }
        if channel != "synthetic_offline_d38":
            raise SessionProductError("SESSION_CAPTURE_CHANNEL_UNKNOWN")
        state = self._load()
        job_id = f"synthetic-capture-{len(state['capture_jobs']) + 1:04d}"
        state["capture_jobs"][job_id] = {
            "job_id": job_id,
            "channel": channel,
            "status": "WAITING",
            "progress": 0,
            "retry_count": 0,
            "receipt_id": f"capture-receipt-{len(state['capture_jobs']) + 1:04d}",
        }
        self._save(state)
        return {
            "schema_version": "SessionsCaptureIntentReceipt-v2",
            **deepcopy(state["capture_jobs"][job_id]),
            "synthetic_only": True,
            "real_source_content_captured": False,
            "status": "AUTHORIZED_SYNTHETIC_TEST_TRACK",
        }
