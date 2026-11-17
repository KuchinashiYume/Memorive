"""Pushing changes to the page: PageEventsMixin.
"""
from __future__ import annotations

from copy import deepcopy
import json
import threading
from typing import Any, Mapping
from memorive.platform.clock import utc_now as _utc_now
from memorive.platform.json_files import lenient_sha256 as _sha256_json
from memorive.desktop.api.common import core_terminal_transition_presentations


class PageEventsMixin:
    """Pushing changes to the page: the Core job event pump, recognising sample rows, and the
    refinement observers that refresh the page bridges."""

    @staticmethod
    def _sample_identifier(value: Any) -> bool:
        rendered = str(value or "").strip().lower()
        return bool(
            rendered.startswith(("syn-", "sample", "job_fixture_", "fixture-"))
            or "synthetic" in rendered
        )

    @classmethod
    def _sample_job(cls, row: Mapping[str, Any]) -> bool:
        """Recognize synthetic jobs even after their in-memory seed map is gone."""

        return cls._sample_identifier(row.get("job_id")) or cls._sample_identifier(
            row.get("job_type")
        )

    @classmethod
    def _sample_message(cls, row: dict[str, Any]) -> bool:
        if cls._sample_identifier(row.get("message_id")):
            return True
        source = row.get("source_event_ref")
        if isinstance(source, dict):
            return any(cls._sample_identifier(item) for item in source.values())
        return cls._sample_identifier(source)

    def _work_log_core_job_rows(self) -> list[dict[str, Any]]:
        rows = self._current_task.core_job_rows(include_history=True)
        counts = self._accounting.job_call_counts()
        for row in rows:
            row.update(counts.get(row['job_id'], {'model_calls':None,
                'external_model_calls':None, 'model_call_counts_complete':False}))
        return rows

    def _core_event_snapshot(self) -> dict[str, Any]:
        """Build a compact public-safe fingerprint input for real Core jobs."""

        if hasattr(self, "_service"):
            page = self._service.call("list_jobs", {})
            listed = page.get("jobs", []) if isinstance(page, Mapping) else []
            if not isinstance(listed, list):
                raise RuntimeError("Core_EVENT_JOB_LIST_INVALID")
            source_rows: list[dict[str, Any]] = []
            for raw in listed:
                if not isinstance(raw, Mapping):
                    raise RuntimeError("Core_EVENT_JOB_ROW_INVALID")
                if self._sample_job(raw):
                    continue
                if str(raw.get("job_type") or "").casefold() not in {
                    "core",
                    "core",
                    "memorive-core",
                    "memorive_core",
                }:
                    continue
                source_rows.append(dict(raw))
        else:
            # Compatibility for isolated contract tests that construct only the
            # historical provider hook.  Product runtime always takes the
            # single-call service path above.
            source_rows = self._current_task.core_job_rows()

        jobs: list[dict[str, Any]] = []
        for row in source_rows:
            progress = row.get("progress") if isinstance(row.get("progress"), Mapping) else {}
            accepted = {
                "job_id": str(row.get("job_id") or ""),
                "attempt_id": str(row.get("attempt_id") or ""),
                "control_state": str(
                    row.get("job_control_state") or row.get("control_state") or "UNKNOWN"
                ).upper(),
                "observed_version": int(
                    row.get("observed_version", row.get("version", 0)) or 0
                ),
                "updated_at": row.get("updated_at"),
            }
            if progress:
                accepted["progress"] = {
                        "completed_units": int(progress.get("completed_units", 0) or 0),
                        "total_units": progress.get("total_units"),
                        "sequence": int(progress.get("sequence", 0) or 0),
                        "phase_code": progress.get("phase_code"),
                }
            if "artifact_count" in row:
                accepted["artifact_count"] = int(row.get("artifact_count", 0) or 0)
            jobs.append(accepted)
        jobs.sort(key=lambda row: row["job_id"])
        return {
            "schema_version": "DesktopCoreDurableEventSnapshot-v1",
            "jobs": jobs,
            "job_count": len(jobs),
            "raw_private_content_included": False,
        }

    def core_event_poll(
        self, request: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        accepted = dict(request or {})
        if accepted:
            raise ValueError("Core_EVENT_POLL_PARAMS_INVALID")
        snapshot = self._core_event_snapshot()
        fingerprint = _sha256_json(snapshot)
        with self._core_event_lock:
            previous = self._core_event_fingerprint
            previous_snapshot = getattr(self, "_core_event_previous_snapshot", None)
            changed = previous is not None and previous != fingerprint
            baseline = previous is None
            if baseline or changed:
                self._core_event_fingerprint = fingerprint
                self._core_event_previous_snapshot = deepcopy(snapshot)
            if changed:
                self._core_event_revision += 1
            terminal_presentations = (
                core_terminal_transition_presentations(previous_snapshot, snapshot)
                if changed and previous_snapshot is not None
                else []
            )
            receipt = {
                "schema_version": "DesktopCoreDurableEventPollReceipt-v1",
                "revision": self._core_event_revision,
                "changed": changed,
                "baseline": baseline,
                "fingerprint": fingerprint,
                "snapshot": snapshot,
                "terminal_presentations": terminal_presentations,
                "observed_at": _utc_now(),
                "raw_private_content_included": False,
                "status": "CHANGED" if changed else "UNCHANGED",
            }
            self._core_event_last_receipt = deepcopy(receipt)
            return receipt

    def _publish_core_event(self, receipt: Mapping[str, Any]) -> None:
        backend_refresh: dict[str, str] = {}
        for name, controller, method in (
            ("messages", self._messages, "messages.bootstrap"),
            ("library", self._library, "library.refresh"),
            ("work_log", self._work_log, "work_log.refresh"),
        ):
            try:
                controller.call(method, {})
                backend_refresh[name] = "PASS"
            except Exception as error:
                backend_refresh[name] = "WARNING:" + type(error).__name__
        assistant_status = "NOT_READY"
        if hasattr(self, "_desk_assistant"):
            try:
                presenter = self._desk_assistant.presenter
                for presentation in receipt.get("terminal_presentations", []):
                    if presenter is not None and isinstance(presentation, Mapping):
                        presenter(dict(presentation))
                self.assistant_bootstrap({})
                assistant_status = "PASS"
            except Exception as error:
                assistant_status = "WARNING:" + type(error).__name__
        durable_receipt = {
            "schema_version": "DesktopCoreDurableEventDeliveryReceipt-v1",
            "revision": int(receipt["revision"]),
            "fingerprint": str(receipt["fingerprint"]),
            "observed_at": receipt["observed_at"],
            "backend_refresh": backend_refresh,
            "assistant_status": assistant_status,
            "ui_dispatch_status": "WINDOW_NOT_ATTACHED",
            "raw_private_content_included": False,
            "status": "PASS",
        }
        if self._window is not None:
            detail = {
                "schema_version": "DesktopCoreDurableEvent-v1",
                "revision": int(receipt["revision"]),
                "fingerprint": str(receipt["fingerprint"]),
                "snapshot": deepcopy(receipt["snapshot"]),
                "observed_at": receipt["observed_at"],
                "raw_private_content_included": False,
            }
            try:
                self._window.evaluate_js(
                    "window.dispatchEvent(new CustomEvent('desktop:core-durable-event',{detail:"
                    + json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
                    + "}))"
                )
                durable_receipt["ui_dispatch_status"] = "PASS"
            except Exception as error:
                durable_receipt["ui_dispatch_status"] = "WARNING:" + type(error).__name__
                durable_receipt["status"] = "WARNING"
        self._write_json_file(self._core_event_receipt_path, durable_receipt)

    def _send_page_event(self, name: str) -> None:
        """Dispatch the window event name on the page; nothing while no window is attached or Windows is ending the session."""
        if self._window is None or getattr(self, "_system_session_ending", False):
            return
        try:
            self._window.evaluate_js(f"window.dispatchEvent(new CustomEvent('{name}'))")
        except Exception:
            pass

    def _core_event_loop(self) -> None:
        while not self._core_event_stop.is_set():
            try:
                receipt = self.core_event_poll({})
                if receipt["changed"]:
                    self._publish_core_event(receipt)
            except Exception as error:
                failure = {
                    "schema_version": "DesktopCoreDurableEventDeliveryReceipt-v1",
                    "observed_at": _utc_now(),
                    "error_code": "Core_EVENT_PUMP_" + type(error).__name__.upper(),
                    "raw_private_content_included": False,
                    "status": "WARNING",
                }
                try:
                    self._write_json_file(self._core_event_receipt_path, failure)
                except Exception:
                    pass
            self._core_event_stop.wait(0.5)

    def _start_core_event_pump(self) -> None:
        if self._core_event_thread is not None and self._core_event_thread.is_alive():
            return
        self._core_event_stop.clear()
        self._core_event_thread = threading.Thread(
            target=self._core_event_loop,
            name="memorive-core-durable-event-pump",
            daemon=True,
        )
        self._core_event_thread.start()

    def _stop_core_event_pump(self) -> None:
        self._core_event_stop.set()
        thread = self._core_event_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._core_event_thread = None

    def _on_refinement_lifecycle(self, event: Mapping[str, Any]) -> None:
        self._messages.ingest_runtime_event(event)
        work_log = getattr(self, "_work_log", None)
        if work_log is not None:
            try:
                work_log.call("work_log.refresh", {})
            except Exception:
                pass
        if self._window is not None:
            try:
                self._window.evaluate_js(
                    "Promise.allSettled(["
                    "window.__Desktop_MESSAGES_BRIDGE__?.refresh?.(),"
                    "window.__Desktop_CURRENT_TASK_BRIDGE__?.refresh?.(),"
                    "window.__Desktop_INBOX_BRIDGE__?.refresh?.(),"
                    "window.__Desktop_WORK_LOG_BRIDGE__?.refresh?.()"
                    "])"
                )
            except Exception:
                pass
        if hasattr(self, "_desk_assistant"):
            self.assistant_bootstrap({})

    def _on_refinement_queue_projection(self, _event: Mapping[str, Any]) -> None:
        work_log = getattr(self, "_work_log", None)
        if work_log is not None:
            try:
                work_log.call("work_log.refresh", {})
            except Exception:
                pass
        if self._window is not None:
            try:
                self._window.evaluate_js(
                    "Promise.allSettled(["
                    "window.__Desktop_INBOX_BRIDGE__?.refresh?.(),"
                    "window.__Desktop_CURRENT_TASK_BRIDGE__?.refresh?.(),"
                    "window.__Desktop_WORK_LOG_BRIDGE__?.refresh?.()"
                    "])"
                )
            except Exception:
                pass

    def _on_refinement_result(self, draft: Mapping[str, Any]) -> None:
        self._library.register_refinement_draft(draft)
        work_log = getattr(self, "_work_log", None)
        if work_log is not None:
            try:
                work_log.call("work_log.refresh", {})
            except Exception:
                pass
        if self._window is not None:
            try:
                self._window.evaluate_js(
                    "Promise.allSettled(["
                    "window.__Desktop_LIBRARY_BRIDGE__?.refresh?.(),"
                    "window.__Desktop_WORK_LOG_BRIDGE__?.refresh?.()"
                    "])"
                )
            except Exception:
                pass
