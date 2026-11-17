"""The main window and the app's lifetime: WindowMixin.
"""
from __future__ import annotations

import json
import threading
from typing import Any
from application_behavior import decide_close_action


class WindowMixin:
    """The main window and the app's lifetime: attaching the window, closing and exit, the
    window state, the panel layout and the control state."""

    def attach_window(self, window: Any) -> None:
        if self._window is not None:
            raise RuntimeError("PRODUCT_WINDOW_ALREADY_ATTACHED")
        self._window = window
        self._browser_companion.window = window
        self._desk_assistant.main_window = window
        self._start_core_event_pump()

    def end_windows_session(self) -> dict[str, Any]:
        # Called only after Windows confirms session end, never at query time.
        # Invalidate pending user-confirmation fallbacks before stopping workers.
        self._exit_requested = True
        self._system_session_ending = True
        path=self._state_dir/'ui'/'system_session_end_receipt.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        self._write_json_file(path,{'status':'QUIESCING','forced_kill':False})
        try:
            receipt=self.close(graceful_service=True)
            self._write_json_file(path,dict(receipt,system_session_end=True))
            return receipt
        except Exception as exc:
            code = str(exc) if str(exc) in {'SERVICE_GRACEFUL_SHUTDOWN_FAILED', 'RESEARCH_EXECUTION_STILL_QUIESCING'} else 'SESSION_CLEANUP_FAILED'
            self._write_json_file(path,{'status':'FAILED','error_type':type(exc).__name__,
                'error_code':code, 'service_observation':getattr(getattr(self, '_service', None), '_last_shutdown_observation', {}),
                'forced_kill':False,'system_session_end':True})
            raise

    def exit_application(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        accepted = dict(request or {})
        if accepted:
            raise ValueError("EXIT_APPLICATION_FIELDS_INVALID")
        with self._lock:
            already_requested = self._exit_requested
            self._exit_requested = True
            main_window = self._window
            assistant_window = self._desk_assistant.window

        if not already_requested:
            # Stop UI event publication before native windows are destroyed.
            # The service is drained by close() after the window loop returns.
            event_stop = getattr(self, '_core_event_stop', None)
            if event_stop is not None:
                event_stop.set()
            def close_windows() -> None:
                if assistant_window is not None:
                    try:
                        assistant_window.destroy()
                    except Exception:
                        pass
                if main_window is not None:
                    try:
                        main_window.destroy()
                    except Exception:
                        pass

            timer = threading.Timer(0.15, close_windows)
            timer.daemon = True
            timer.start()
        return {
            "schema_version": "DesktopAssistantApplicationExitReceipt-v1",
            "already_requested": already_requested,
            "main_window_attached": main_window is not None,
            "assistant_window_attached": assistant_window is not None,
            "external_process_launches": 0,
            "shell_command_calls": 0,
            "status": "EXIT_SCHEDULED",
        }

    def get_close_policy(self, user_choice: str | None = None) -> dict[str, Any]:
        preferences = self._current_preferences()
        jobs_projection = self._service.call("list_jobs", {})
        if not isinstance(jobs_projection, dict) or not isinstance(jobs_projection.get("jobs"), list):
            raise RuntimeError("CLOSE_JOB_STATE_UNAVAILABLE")
        rows = jobs_projection.get("jobs", []) if isinstance(jobs_projection, dict) else []
        sample_job_ids = frozenset(getattr(self._current_task, "sample_job_ids", ()))
        active_states = {
            "QUEUED", "RUNNING", "IN_PROGRESS", "STARTED", "WAITING",
            "WAITING_USER", "AWAITING_ACTION", "PAUSED", "NEEDS_INPUT",
        }
        active_job_count = 0
        for row in rows if isinstance(rows, list) else []:
            if (
                not isinstance(row, dict)
                or row.get("job_id") in sample_job_ids
                or self._sample_job(row)
            ):
                continue
            state = str(
                row.get("job_control_state", row.get("control_state", row.get("state", "")))
            ).upper()
            if state in active_states:
                active_job_count += 1
        action = decide_close_action(
            close_behavior=str(preferences["close_behavior"]),
            warn_on_close_running=bool(preferences["warn_on_close_running"]),
            keep_tasks_in_background=bool(preferences["keep_tasks_in_background"]),
            active_job_count=active_job_count,
            user_choice=user_choice,
        )
        return {
            "schema_version": "DesktopMainWindowClosePolicy-v1",
            "close_behavior": preferences["close_behavior"],
            "warn_on_close_running": preferences["warn_on_close_running"],
            "keep_tasks_in_background": preferences["keep_tasks_in_background"],
            "active_job_count": active_job_count,
            "action": action,
            "status": "READY",
        }

    def hide_main_window(self) -> dict[str, Any]:
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        self._window.hide()
        return {
            "schema_version": "DesktopMainWindowVisibilityReceipt-v1",
            "visible": False,
            "background_tasks_allowed": self._current_preferences()["keep_tasks_in_background"],
            "status": "HIDDEN_TO_TRAY",
        }

    def get_window_state(self) -> dict[str, Any]:
        return self._main_chrome.state()

    def update_window_regions(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._main_chrome.update_regions(request)

    def window_action(self, action: str) -> dict[str, Any]:
        return self._main_chrome.command(action)

    def prepare_window_close(self, edit: dict[str, Any]) -> dict[str, Any]:
        return self._main_chrome.prepare_close(edit)

    def commit_window_close(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._main_chrome.commit_close(request)

    def cancel_window_close(self) -> dict[str, Any]:
        return self._main_chrome.cancel_close()

    def persist_panel_state(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("PANEL_STATE_REQUEST_INVALID")
        required = {
            "route", "sidebar_open", "inspector_open", "bottom_open",
            "primary_size", "right_size", "bottom_size",
        }
        if set(request) != required:
            raise ValueError("PANEL_STATE_FIELDS_INVALID")
        route = request["route"]
        if route not in {
            "settings", "inbox", "current-task", "messages", "sessions", "library", "work-log", "leaderboard"
        }:
            raise ValueError("PANEL_STATE_ROUTE_INVALID")
        for name in ("sidebar_open", "inspector_open", "bottom_open"):
            if not isinstance(request[name], bool):
                raise ValueError("PANEL_STATE_BOOLEAN_INVALID")
        bounds = {"primary_size": (240, 2400), "right_size": (240, 2400), "bottom_size": (120, 1600)}
        for name, (minimum, maximum) in bounds.items():
            value = request[name]
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError("PANEL_STATE_SIZE_INVALID")
        preferences = self._current_preferences()
        if not preferences["remember_panel_state"]:
            return {
                "schema_version": "DesktopPanelStatePersistenceReceipt-v1",
                "route": route,
                "persisted": False,
                "reason_code": "PANEL_STATE_MEMORY_DISABLED",
                "status": "PASS",
            }

        def mutate(state: dict[str, Any]) -> None:
            filters = dict(state["filters"])
            raw = filters.get("panel_state_v1", "{}")
            try:
                all_panels = json.loads(raw) if isinstance(raw, str) else {}
            except json.JSONDecodeError:
                all_panels = {}
            if not isinstance(all_panels, dict):
                all_panels = {}
            all_panels[route] = dict(request)
            encoded = json.dumps(all_panels, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if len(encoded) > 8192:
                raise ValueError("PANEL_STATE_ENCODED_TOO_LARGE")
            filters["panel_state_v1"] = encoded
            state["filters"] = filters
            state["panel_dimensions"] = {
                "left": request["primary_size"],
                "right": request["right_size"],
                "bottom": request["bottom_size"],
            }

        with self._lock:
            self._control_envelope = self._control_store.update(mutate)
        return {
            "schema_version": "DesktopPanelStatePersistenceReceipt-v1",
            "route": route,
            "persisted": True,
            "revision": self._control_envelope["revision"],
            "status": "PASS",
        }

    def reset_panel_layout(self) -> dict[str, Any]:
        with self._lock:
            def reset(value: dict[str, Any]) -> None:
                value["panel_dimensions"] = {"left": 248, "right": 336, "bottom": 260}
                filters = dict(value["filters"])
                filters.pop("panel_state_v1", None)
                value["filters"] = filters

            self._control_envelope = self._control_store.update(reset)
        return {
            "schema_version": "DesktopAssistantPanelLayoutResetReceipt-v1",
            "revision": self._control_envelope["revision"],
            "panel_dimensions": self._control_envelope["state"]["panel_dimensions"],
            "status": "PASS",
        }

    def get_control_state(self) -> dict[str, Any]:
        with self._lock:
            self._control_envelope = self._control_store.load()
            return {
                "schema_version": "WorkLogControlStateProjection-v1",
                "revision": self._control_envelope["revision"],
                "state": self._control_envelope["state"],
                "status": "READY",
            }
