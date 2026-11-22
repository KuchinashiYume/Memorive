"""What the parts of ProductApi share: the method sets the window may call, the service IPC names,
the presentations of newly finished Core jobs, the source import path and the process shutdown order.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys
from typing import Any, Mapping
from memorive.platform.json_files import lenient_sha256 as _sha256_json
from memorive.documents.data_review.service import REVIEW_METHODS as _REVIEW_METHODS


ALLOWED_EVENTS = frozenset({
    "navigation.select", "window.sidebar.toggle", "window.inspector.toggle",
    "window.bottom.toggle", "window.panel.resize", "logo.activate",
    "motion.replay", "control.activate", "keyboard.shortcut",
})


QUERY_METHODS = frozenset({
    "list_jobs", "get_job", "get_attempt", "get_job_graph", "get_job_events",
    "watch_job_events", "list_action_requests", "get_artifact_bindings",
    "get_terminal_receipt", "resolve_job_locator", "lookup_start_by_idempotency_key",
})


SETTINGS_METHODS = frozenset({
    "settings.research_get", "settings.research_save", "settings.user_get", "settings.user_save",
    "settings.leaderboard_get", "settings.leaderboard_save", "settings.leaderboard_refresh",
    "settings.external_sources_get", "settings.external_sources_save",
    "settings.external_sources_refresh", "settings.external_model_reference",
    "settings.get_contract", "settings.get_state", "settings.register_directory", "settings.preview", "settings.save",
    "settings.cli_template_prepare",
    "settings.save_cli_services",
    "settings.revert", "settings.reset_scope", "settings.export_redacted", "settings.import_redacted",
    "settings.capability_state", "settings.credential_create", "settings.credential_replace",
    "settings.credential_status", "settings.credential_delete", "settings.model_service_remove", "settings.diagnostics",
    "settings.verify_api_model", "settings.verify_cli_model", "settings.execute_structured_chat", "settings.test_workflow_node",
    "settings.start_api_verification", "settings.api_verification_status",
    "settings.start_workflow_exam", "settings.workflow_exam_status", "settings.cancel_workflow_exam",
    "settings.start_cli_verification", "settings.cli_verification_status",
    "settings.cancel_cli_verification",
    "settings.support_bundle", "settings.effect_metrics",
})


ACCOUNTING_METHODS = frozenset({
    "accounting.get_projection_v3",
    "accounting.refresh_projection_v3",
    "models.get_commerce_context_v1",
})


REVIEW_METHODS=frozenset(_REVIEW_METHODS)


INBOX_METHODS = frozenset({
    "inbox.set_logic_review", "inbox.set_data_bindings",
    "inbox.get_contract", "inbox.import_paths", "inbox.enqueue_session_refinement",
    "inbox.find_session_refinement", "inbox.begin_session_refinement",
    "inbox.complete_session_refinement", "inbox.get_auto_run",
    "inbox.cancel_session_refinement",
    "inbox.set_auto_run", "inbox.retry_import", "inbox.recover",
    "inbox.dispatch", "inbox.record_dispatch_receipt", "inbox.cancel_dispatch",
    "inbox.set_starred", "inbox.soft_delete", "inbox.undo", "inbox.redo",
    "inbox.refresh", "inbox.projection", "inbox.item_detail", "inbox.close_session",
    "inbox.effect_metrics",
})


CURRENT_TASK_METHODS = frozenset({
    "current_task.get_contract",
    "current_task.list",
    "current_task.select_task",
    "current_task.select_node",
    "current_task.view",
    "current_task.start_core",
    "current_task.refresh",
    "current_task.pause",
    "current_task.pause_node",
    "current_task.resume_node",
    "current_task.resume",
    "current_task.begin_cancel",
    "current_task.confirm_cancel",
    "current_task.delete_failed",
    "current_task.retry",
    "current_task.rollback_node",
    "current_task.restart_status",
    "current_task.respond",
    "current_task.set_override",
    "current_task.restore_snapshot",
    "current_task.close_all",
    "current_task.effect_metrics",
})


MESSAGES_METHODS = frozenset({
    "messages.get_contract",
    "messages.bootstrap",
    "messages.list",
    "messages.switch_mode",
    "messages.open_detail",
    "messages.close_detail",
    "messages.toggle_star",
    "messages.confirm",
    "messages.archive_confirmed",
    "messages.mark_all_read",
    "messages.bulk_apply",
    "messages.refresh_archive",
    "messages.navigate",
    "messages.notification_deliver",
    "messages.notification_activate",
    "messages.effect_metrics",
})


SESSIONS_METHODS = frozenset({
    "sessions.get_contract",
    "sessions.bootstrap",
    "sessions.list",
    "sessions.get",
    "sessions.get_provider_capabilities",
    "sessions.capture_intent",
    "sessions.capture_progress",
    "sessions.capture_cancel",
    "sessions.capture_retry",
    "sessions.refine",
    "sessions.refinement_cancel",
    "sessions.refinement_start",
    "sessions.refinement_settings_get",
    "sessions.refinement_settings_save",
    "sessions.refinement_get",
    "sessions.delete_local",
    "sessions.bulk_delete_local",
    "sessions.refresh",
    "sessions.resolve_locator",
    "sessions.inject_fault",
    "sessions.restore",
    "sessions.effect_metrics",
})


LOCAL_MODEL_METHODS = frozenset({
    "local_models.bootstrap",
    "local_models.list",
    "local_models.discover",
    "local_models.endpoint_save",
    "local_models.endpoint_remove",
    "local_models.verify",
    "local_models.effect_metrics",
})


ASSISTANT_PREFERENCE_METHODS = frozenset({
    "assistant.preference_get",
    "assistant.preference_save",
})


LIBRARY_METHODS = frozenset({
    "library.get_contract",
    "library.bootstrap",
    "library.list_artifacts",
    "library.get_artifact",
    "library.get_artifact_bindings",
    "library.get_lineage",
    "library.get_status_counts",
    "library.set_view",
    "library.select",
    "library.resolve_locator",
    "library.validate_external_view",
    "library.open_artifact",
    "library.confirm_refinement_review",
    "library.refinement_review",
    "library.edit_refinement_item",
    "library.confirm_core_review",
    "library.refresh",
    "library.inject_fault",
    "library.restore",
    "library.effect_metrics",
})


WORK_LOG_METHODS = frozenset({
    "work_log.get_contract",
    "work_log.bootstrap",
    "work_log.list_entries",
    "work_log.get_entry",
    "work_log.get_milestones",
    "work_log.set_filter",
    "work_log.select",
    "work_log.resolve_job_locator",
    "work_log.resolve_evidence_locator",
    "work_log.get_source_status",
    "work_log.refresh",
    "work_log.export_preview",
    "work_log.export_redacted_view",
    "work_log.verify_export",
    "work_log.inject_fault",
    "work_log.restore",
    "work_log.effect_metrics",
})


SECRET_ENV_NAME = "MEMORIVE_ServiceContracts_IPC_SECRET_HEX"


SERVICE_REVISION = 1


def core_terminal_transition_presentations(
    previous_snapshot: Mapping[str, Any] | None,
    current_snapshot: Mapping[str, Any] | None,
    *,
    reduced_motion: bool = False,
) -> list[dict[str, Any]]:
    """Preserve each newly-terminal job when the next queued job starts."""

    previous_jobs = {
        str(row.get("job_id") or ""): row
        for row in (previous_snapshot or {}).get("jobs", [])
        if isinstance(row, Mapping) and row.get("job_id")
    }
    transitions: list[tuple[str, str, dict[str, Any]]] = []
    for row in (current_snapshot or {}).get("jobs", []):
        if not isinstance(row, Mapping):
            continue
        job_id = str(row.get("job_id") or "")
        state = str(row.get("control_state") or "").upper()
        before = str(previous_jobs.get(job_id, {}).get("control_state") or "").upper()
        if not job_id or state not in {"SUCCEEDED", "FAILED", "CANCELLED"} or before == state:
            continue
        success = state == "SUCCEEDED"
        delivery_material = {
            "job_id": job_id,
            "attempt_id": str(row.get("attempt_id") or ""),
            "control_state": state,
            "observed_version": int(row.get("observed_version", 0) or 0),
            "updated_at": str(row.get("updated_at") or ""),
        }
        transitions.append(
            (
                str(row.get("updated_at") or ""),
                job_id,
                {
                    "display_state": "NOTIFYING",
                    "attention_state": "SUCCESS" if success else "ERROR",
                    "animation_state": "STATIC" if reduced_motion else ("SUCCESS" if success else "ERROR"),
                    "safe_summary": "任务已完成" if success else "任务需要检查",
                    "reason_code": "JOB_TERMINAL_SUCCESS" if success else "JOB_TERMINAL_ERROR",
                    "locator": f"memorive://job/{job_id}",
                    "reduced_motion": reduced_motion,
                    "delivery_id": "core-terminal-"
                    + _sha256_json(delivery_material)[:24].lower(),
                },
            )
        )
    transitions.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in transitions]


def _ensure_source_importable(source_root: Path) -> None:
    import_roots = (source_root, source_root.parent / "dependency_overlay")
    for candidate in reversed(import_roots):
        if not candidate.is_dir():
            continue
        rendered = str(candidate)
        if rendered not in sys.path:
            sys.path.insert(0, rendered)


def process_shutdown_level() -> int | None:
    """Observe this GUI's Windows order without changing global or GUI policy."""
    if os.name != 'nt':
        return None
    level, flags = ctypes.c_uint(), ctypes.c_uint()
    if not ctypes.windll.kernel32.GetProcessShutdownParameters(ctypes.byref(level), ctypes.byref(flags)):
        return None
    return level.value
