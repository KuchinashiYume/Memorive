from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
import ctypes
from datetime import datetime, timezone
import hashlib
import hmac
import importlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Mapping
from urllib.parse import urlsplit
import uuid
import webbrowser

from application_behavior import WindowsAutostartManager, decide_close_action
from network_diagnostics import run_network_diagnostic
from memorive_browser_companion.browsers import BROWSERS, FAMILIES, discover as discover_browsers, family as browser_family, select as select_browser
from memorive_folder_management.policy import windows_io_path
from notification_runtime import NOTIFICATION_KINDS, plan_windows_notification


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

INBOX_METHODS = frozenset({
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


def assistant_visibility_after_preference_save(
    *, current_visible: bool, enabled: bool
) -> bool:
    """Saving preferences may disable the pet, but must never unhide it."""

    if not isinstance(current_visible, bool) or not isinstance(enabled, bool):
        raise ValueError("ASSISTANT_VISIBILITY_AXIS_INVALID")
    return current_visible and enabled


def _local_assistant_projection(*, visible: bool, refresh_unavailable: bool) -> dict[str, Any]:
    reason_code = (
        "SERVICE_REFRESH_UNAVAILABLE"
        if visible and refresh_unavailable
        else "NO_ACTIVE_REAL_JOB"
        if visible
        else "USER_HIDDEN"
    )
    summary = (
        "桌面助手已显示；任务状态将在服务恢复后自动刷新"
        if visible and refresh_unavailable
        else "桌面助手待命中"
        if visible
        else "桌面助手已隐藏"
    )
    return {
        "schema_version": "DesktopAssistantAssistantRuntimeProjection-v1",
        "presentation": {
            "display_state": "IDLE" if visible else "HIDDEN",
            "attention_state": "NONE",
            "animation_state": "STATIC",
            "safe_summary": summary,
            "reason_code": reason_code,
            "locator": None,
            "reduced_motion": False,
        },
        "source_track": "LOCAL_NATIVE_CONTROL",
        "real_job_count": 0,
        "sample_job_count": 0,
        "real_message_count": 0,
        "projected_input_count": 0,
        "sample_job_consumed": False,
        "sample_success_used_as_real_success": False,
        "native_presentation_status": "LOCAL_STATIC",
        "external_network_calls": 0,
        "external_model_calls": 0,
        "credential_value_reads": 0,
        "shell_command_calls": 0,
        "system_autostart_writes": 0,
        "status": "DEGRADED" if refresh_unavailable else "READY",
    }


def _sha256_json(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest().upper()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


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


def suppress_replayed_terminal_animation(
    presentation: Mapping[str, Any],
) -> dict[str, Any]:
    """Static snapshots may describe a terminal state but never replay its motion.

    SUCCESS/ERROR motion is reserved for a fresh durable transition delivery.
    Periodic bootstrap, restart and cosmetic blink refreshes therefore retain the
    terminal attention/base state while projecting a STATIC animation.
    """

    accepted = deepcopy(dict(presentation))
    terminal_reasons = {
        "JOB_TERMINAL_SUCCESS",
        "JOB_TERMINAL_ERROR",
        "MESSAGE_COMPLETED",
    }
    if (
        str(accepted.get("reason_code") or "") in terminal_reasons
        and str(accepted.get("animation_state") or "").upper() in {"SUCCESS", "ERROR"}
    ):
        accepted["animation_state"] = "STATIC"
    return accepted


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


class ServiceProcessManager:
    def __init__(
        self,
        source_root: Path,
        profile_root: Path,
        runtime_root: Path,
        *,
        test_fixture_mode: bool = False,
        service_revision: int = SERVICE_REVISION,
    ):
        self.source_root = source_root.resolve()
        self.profile_root = profile_root.resolve()
        self.runtime_root = runtime_root.resolve()
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        if isinstance(service_revision, bool) or not isinstance(service_revision, int) or service_revision <= 0:
            raise ValueError("APPLICATION_SERVICE_REVISION_INVALID")
        self.service_revision = service_revision
        self.parent_pid = os.getpid()
        self._owner_lock_path = self.runtime_root / "service_owner.lock"
        self._owner_record_path = self.runtime_root / "service_owner_v1.json"
        self._owner_stream: Any = None
        self._secret = bytearray(os.urandom(32))
        self._process: subprocess.Popen[str] | None = None
        self._last_shutdown_observation: dict[str, Any] = {}
        self._stdout_handle: Any = None
        self._stderr_handle: Any = None
        self._descriptor: dict[str, Any] | None = None
        self._rpc_transport: Any = None
        self._rpc_transport_lock = threading.Lock()
        self._frozen_runtime = bool(getattr(sys, "frozen", False))
        self._test_fixture_mode = bool(test_fixture_mode)
        if self._frozen_runtime:
            self._service_executable = Path(sys.executable).resolve()
        else:
            base_executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
            self._service_executable = base_executable if base_executable.is_file() else Path(sys.executable).resolve()
        if not self._service_executable.is_file():
            raise FileNotFoundError("APPLICATION_SERVICE_EXECUTABLE_MISSING")
        self._launch_id = f"service-{uuid.uuid4().hex}"
        self._endpoint_file = self.runtime_root / f"{self._launch_id}.endpoint.json"
        self._stdout_path = self.runtime_root / f"{self._launch_id}.stdout.log"
        self._stderr_path = self.runtime_root / f"{self._launch_id}.stderr.log"

    @staticmethod
    def _try_owner_lock(stream: Any) -> bool:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True
        import fcntl

        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    @staticmethod
    def _unlock_owner(stream: Any) -> None:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _read_owner_record(self) -> dict[str, Any] | None:
        if not self._owner_record_path.is_file():
            return None
        try:
            value = json.loads(self._owner_record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("APPLICATION_SERVICE_OWNER_RECORD_INVALID") from exc
        expected = {
            "schema_version",
            "service_revision",
            "owner_pid",
            "service_pid",
            "profile_root",
            "status",
            "updated_at_unix",
        }
        if (
            not isinstance(value, dict)
            or set(value) != expected
            or value.get("schema_version") != "ApplicationServiceStateOwner-v1"
            or isinstance(value.get("service_revision"), bool)
            or not isinstance(value.get("service_revision"), int)
            or value["service_revision"] <= 0
        ):
            raise RuntimeError("APPLICATION_SERVICE_OWNER_RECORD_INVALID")
        relocated = Path(str(value.get("profile_root"))).resolve() != self.profile_root
        if relocated and not (
            value.get("status") == "STOPPED" and value.get("service_pid") is None
        ):
            raise RuntimeError("APPLICATION_SERVICE_OWNER_RECORD_INVALID")
        return value

    def _write_owner_record(self, *, status: str, service_pid: int | None) -> None:
        payload = {
            "schema_version": "ApplicationServiceStateOwner-v1",
            "service_revision": self.service_revision,
            "owner_pid": self.parent_pid,
            "service_pid": service_pid,
            "profile_root": str(self.profile_root),
            "status": status,
            "updated_at_unix": time.time(),
        }
        temporary = self.runtime_root / (
            f".{self._owner_record_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self._owner_record_path)

    def _acquire_state_owner(self) -> None:
        if self._owner_stream is not None:
            return
        stream = self._owner_lock_path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
            os.fsync(stream.fileno())
        if not self._try_owner_lock(stream):
            stream.close()
            current = self._read_owner_record()
            if current is not None and current["service_revision"] > self.service_revision:
                raise RuntimeError("APPLICATION_SERVICE_NEWER_BUILD_OWNS_STATE")
            raise RuntimeError("APPLICATION_SERVICE_STATE_ALREADY_OWNED")
        try:
            current = self._read_owner_record()
            if current is not None and current["service_revision"] > self.service_revision:
                raise RuntimeError("APPLICATION_SERVICE_NEWER_BUILD_OWNS_STATE")
            if current is not None and Path(str(current["profile_root"])).resolve() != self.profile_root:
                # A stopped service owns no process. Portable relocation may
                # retain its old record; preserve that record before replacing
                # only ownership metadata, while holding the new root's lock.
                raw = self._owner_record_path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                previous = self.runtime_root / f"service_owner_relocated_{digest}.json"
                if not previous.exists():
                    with previous.open("xb") as saved:
                        saved.write(raw)
                        saved.flush()
                        os.fsync(saved.fileno())
                elif previous.read_bytes() != raw:
                    raise RuntimeError("APPLICATION_SERVICE_OWNER_RELOCATION_EVIDENCE_MISMATCH")
            self._write_owner_record(status="STARTING", service_pid=None)
            self._owner_stream = stream
        except BaseException:
            self._owner_stream = None
            self._unlock_owner(stream)
            stream.close()
            raise

    def _release_state_owner(self, *, status: str) -> None:
        stream = self._owner_stream
        if stream is None:
            return
        try:
            self._write_owner_record(status=status, service_pid=None)
        finally:
            self._unlock_owner(stream)
            stream.close()
            self._owner_stream = None

    def _service_command(self) -> list[str]:
        command = [str(self._service_executable)]
        if self._frozen_runtime:
            command.append("--desktop-service-mode")
        else:
            command.extend(["-m", "memorive_desktop_service"])
        command.extend(
            [
                "--profile-root",
                str(self.profile_root),
                "--endpoint-file",
                str(self._endpoint_file),
                "--parent-pid",
                str(self.parent_pid),
                "--service-revision",
                str(self.service_revision),
            ]
        )
        return command

    def _service_working_directory(self) -> Path:
        """Return a short, stable cwd for Windows child-process creation.

        Runtime state still lives below ``runtime_root`` and every service path
        is passed explicitly.  Using that potentially long private-profile path
        as ``cwd`` makes Windows CreateProcess fail with WinError 267 before the
        service can start.
        """
        working_directory = self._service_executable.parent.resolve()
        if os.name == "nt":
            # All service resources are absolute. A deeply installed executable
            # must not make CreateProcess inherit an over-MAX_PATH cwd.
            while len(str(working_directory).encode("utf-16-le")) // 2 >= 248:
                working_directory = working_directory.parent
        if not working_directory.is_dir():
            raise NotADirectoryError("APPLICATION_SERVICE_WORKING_DIRECTORY_INVALID")
        return working_directory

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def live(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, timeout_seconds: float = 15.0) -> None:
        if self._process is not None:
            raise RuntimeError("APPLICATION_SERVICE_ALREADY_STARTED")
        if self._endpoint_file.exists() or self._stdout_path.exists() or self._stderr_path.exists():
            raise FileExistsError("APPLICATION_SERVICE_LAUNCH_PATH_COLLISION")
        self._acquire_state_owner()
        environment = os.environ.copy()
        environment.pop('MEMORIVE_DESKTOP_PARENT_SHUTDOWN_LEVEL', None)
        parent_shutdown_level = process_shutdown_level()
        if parent_shutdown_level is not None:
            environment['MEMORIVE_DESKTOP_PARENT_SHUTDOWN_LEVEL'] = str(parent_shutdown_level)
        existing_python_path = environment.get("PYTHONPATH", "")
        local_import_roots = [str(self.source_root)]
        dependency_overlay = self.source_root.parent / "dependency_overlay"
        if dependency_overlay.is_dir():
            local_import_roots.append(str(dependency_overlay))
        if existing_python_path:
            local_import_roots.append(existing_python_path)
        environment["PYTHONPATH"] = os.pathsep.join(local_import_roots)
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        if self._test_fixture_mode:
            environment["MEMORIVE_Desktop_TEST_FIXTURE_MODE"] = "1"
        else:
            environment.pop("MEMORIVE_Desktop_TEST_FIXTURE_MODE", None)
        environment[SECRET_ENV_NAME] = bytes(self._secret).hex()
        try:
            self._stdout_handle = self._stdout_path.open("x", encoding="utf-8", newline="\n")
            self._stderr_handle = self._stderr_path.open("x", encoding="utf-8", newline="\n")
            try:
                command = self._service_command()
                self._process = subprocess.Popen(
                    command,
                    cwd=self._service_working_directory(),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=self._stdout_handle,
                    stderr=self._stderr_handle,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            finally:
                environment.pop(SECRET_ENV_NAME, None)
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    raise RuntimeError("APPLICATION_SERVICE_EXITED_BEFORE_READY")
                if self._endpoint_file.is_file():
                    descriptor = json.loads(self._endpoint_file.read_text(encoding="utf-8"))
                    expected = {
                        "schema_version", "protocol_version", "host", "port", "pid", "profile_root",
                        "secret_env_name", "secret_value_recorded", "external_network_calls",
                        "external_model_calls", "credential_value_reads", "parent_pid",
                        "service_revision", "status",
                    }
                    if set(descriptor) != expected:
                        raise RuntimeError("APPLICATION_SERVICE_ENDPOINT_FIELDS_INVALID")
                    port = descriptor["port"]
                    if (
                        descriptor["schema_version"] != "ApplicationServiceEndpoint-v1"
                        or descriptor["protocol_version"] != "1.0"
                        or descriptor["host"] != "127.0.0.1"
                        or isinstance(port, bool)
                        or not isinstance(port, int)
                        or not 1 <= port <= 65535
                        or descriptor["pid"] != self._process.pid
                        or descriptor["parent_pid"] != self.parent_pid
                        or descriptor["service_revision"] != self.service_revision
                        or Path(descriptor["profile_root"]).resolve() != self.profile_root
                        or descriptor["secret_env_name"] != SECRET_ENV_NAME
                        or descriptor["secret_value_recorded"] is not False
                        or descriptor["external_network_calls"] != 0
                        or descriptor["external_model_calls"] != 0
                        or descriptor["credential_value_reads"] != 0
                        or descriptor["status"] != "READY"
                    ):
                        raise RuntimeError("APPLICATION_SERVICE_ENDPOINT_INVALID")
                    self._descriptor = descriptor
                    self._write_owner_record(status="READY", service_pid=self._process.pid)
                    return
                time.sleep(0.05)
            raise TimeoutError("APPLICATION_SERVICE_READY_TIMEOUT")
        except BaseException:
            environment.pop(SECRET_ENV_NAME, None)
            self.close()
            raise

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        with self._rpc_transport_lock:
            if not self.live or self._descriptor is None:
                raise RuntimeError("APPLICATION_SERVICE_NOT_LIVE")
            if self._rpc_transport is None:
                module = importlib.import_module("memorive_desktop_service.sync_client")
                self._rpc_transport = module.ThreadedApplicationServiceClient(
                    self._descriptor["host"], int(self._descriptor["port"]),
                    secret=bytes(self._secret),
                )
            transport = self._rpc_transport
        return transport.call(method, dict(params or {}))

    def public_status(self) -> dict[str, Any]:
        if not self.live or self._descriptor is None:
            return {"status": "STOPPED", "pid": self.pid}
        return {
            "status": "READY",
            "pid": self.pid,
            "protocol_version": self._descriptor["protocol_version"],
            "host_is_loopback": self._descriptor["host"] == "127.0.0.1",
            "service_executable": str(self._service_executable),
            "embedded_runtime": self._frozen_runtime,
            "service_revision": self.service_revision,
            "parent_pid": self.parent_pid,
            "secret_env_name": SECRET_ENV_NAME,
            "secret_value_recorded": False,
        }

    def close(self, *, graceful: bool = False) -> dict[str, Any]:
        process = self._process
        forced = False
        observed_exit = None if process is None else process.poll()
        observation = self._last_shutdown_observation = {
            'phase': 'PRECHECK', 'pid': None if process is None else process.pid,
            'exit_code': observed_exit, 'stop_written': False, 'cooperative': graceful,
        }
        if process is not None and observed_exit is None:
            if graceful:
                payload={'parent_pid':self.parent_pid,'pid':process.pid,'action':'STOP'}
                signature=hmac.new(bytes(self._secret),json.dumps(payload,sort_keys=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest()
                stop_file=self._endpoint_file.with_name(self._endpoint_file.name.replace('.endpoint.json','.stop.json'))
                ProductApi._write_json_file(stop_file,dict(payload,hmac_sha256=signature))
                observation.update(phase='WAIT_SERVICE_EXIT', stop_written=True)
                # The authenticated stop prevents new work. An in-flight provider
                # call must finish writing its usage receipt before process exit;
                # its own configured transport deadline governs that drain.
                process.wait()
                observation['exit_code'] = process.returncode
                if process.returncode != 0:raise RuntimeError('SERVICE_GRACEFUL_SHUTDOWN_FAILED')
            else:
                process.terminate()
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    forced = True
                    process.kill()
                    process.wait(timeout=5.0)
        exit_code = None if process is None else process.poll()
        if graceful and process is not None and exit_code != 0:
            raise RuntimeError('SERVICE_GRACEFUL_SHUTDOWN_FAILED')
        with self._rpc_transport_lock:
            if self._rpc_transport is not None:
                self._rpc_transport.close()
                self._rpc_transport = None
        for handle in (self._stdout_handle, self._stderr_handle):
            if handle is not None and not handle.closed:
                handle.flush()
                handle.close()
        self._stdout_handle = None
        self._stderr_handle = None
        self._process = None
        self._descriptor = None
        for index in range(len(self._secret)):
            self._secret[index] = 0
        self._release_state_owner(status="STOPPED")
        observation.update(phase='STOPPED', exit_code=exit_code)
        return {
            "schema_version": "WorkLogApplicationServiceShutdownReceipt-v1",
            "exit_code": exit_code,
            "forced_kill": forced,
            "cooperative": graceful,
            "secret_zeroized_in_parent": True,
            "status": "STOPPED",
        }


class ProductApi:
    @staticmethod
    def _default_assistant_shortcut_entries(
        *,
        home: Path | None = None,
        workspace_candidates: tuple[Path, ...] | None = None,
    ) -> list[dict[str, str]]:
        """Seed useful first-run shortcuts without hard-coding another PC's paths."""

        resolved_home = (home or Path.home()).resolve(strict=False)
        candidates = workspace_candidates
        if candidates is None:
            candidates = tuple(
                candidate
                for candidate in (
                    Path(os.environ["MEMORIVE_WORKSPACE_ROOT"])
                    if os.environ.get("MEMORIVE_WORKSPACE_ROOT")
                    else None,
                    Path("G:/Memorive"),
                    Path.cwd(),
                )
                if candidate is not None
            )
        workspace = next(
            (
                candidate.resolve(strict=False)
                for candidate in candidates
                if candidate.resolve(strict=False).is_dir()
            ),
            None,
        )
        cc_switch = (resolved_home / "CC switch").resolve(strict=False)
        return [
            {
                "name": "Memorive 文件夹" if workspace is not None else "",
                "target": str(workspace) if workspace is not None else "",
            },
            {
                "name": "CC SWITCH" if cc_switch.is_dir() else "",
                "target": str(cc_switch) if cc_switch.is_dir() else "",
            },
            {"name": "GPT网站", "target": "https://chatgpt.com/"},
        ]

    @classmethod
    def _seed_blank_assistant_shortcut_file(cls, path: Path) -> bool:
        """Fill the shipped empty three-slot state without replacing user edits."""

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        entries = payload.get("shortcut_entries")
        if not isinstance(entries, list) or len(entries) != 3:
            return False
        if any(
            isinstance(entry, dict)
            and (str(entry.get("name") or "").strip() or str(entry.get("target") or "").strip())
            for entry in entries
        ):
            return False
        payload["shortcut_entries"] = cls._default_assistant_shortcut_entries()
        cls._write_json_file(path, payload)
        return True

    def _profile_path(self, logical_directory: str) -> Path:
        """Resolve a declared profile directory through the packaged policy.

        Normal startup creates the manager in ``__init__``. Recovery helpers
        and frozen test harnesses can intentionally construct ``ProductApi``
        without running that startup path, so initialize the same manager
        lazily instead of reverting to ad-hoc path joins.
        """

        profile_root = Path(self._profile_root).resolve(strict=False)
        manager = getattr(self, "_folder_manager", None)
        if manager is None or Path(manager.profile_root).resolve(strict=False) != profile_root:
            source_root = getattr(self, "_source_root", None)
            if source_root is not None:
                _ensure_source_importable(Path(source_root))
            folder_module = importlib.import_module("memorive_folder_management")
            manager = folder_module.ProfileFolderManager(profile_root)
            self._folder_manager = manager
        return manager.path_for(logical_directory)

    def __init__(
        self,
        state_dir: Path,
        source_root: Path,
        method_binding_path: Path,
        *,
        network_runner: Any = run_network_diagnostic,
        system_effects_enabled: bool = False,
        autostart_manager: WindowsAutostartManager | None = None,
        session_source_roots: Mapping[str, Path] | None = None,
        session_visible_ui_reader: Any | None = None,
        test_fixture_mode: bool = False,
        private_profile_selection: Mapping[str, Any] | None = None,
    ):
        requested_state_dir = state_dir.resolve()
        self._private_profile_selection: dict[str, Any] | None = None
        if bool(getattr(sys, "frozen", False)) and system_effects_enabled and not test_fixture_mode:
            from product_identity import BINDING

            if BINDING.get("channel") == "SANDBOX_TEST_ONLY_UNSIGNED":
                # app.main owns private-profile selection and bootstrapping.  A
                # second selection here used ``state_dir.parent`` as a new data
                # root and created state/private-profiles/state/private-profiles.
                # Accept only the exact receipt for the already-selected root.
                selection = dict(private_profile_selection or {})
                if selection.get("status") != "READY":
                    raise RuntimeError("PRIVATE_TEST_PROFILE_PRESELECTION_REQUIRED")
                if str(selection.get("package_id") or "") != str(BINDING.get("package_id") or ""):
                    raise RuntimeError("PRIVATE_TEST_PROFILE_PRESELECTION_BUILD_MISMATCH")
                selected_root = Path(str(selection.get("state_root") or "")).resolve(strict=False)
                if os.path.normcase(str(selected_root)) != os.path.normcase(str(requested_state_dir)):
                    raise RuntimeError("PRIVATE_TEST_PROFILE_PRESELECTION_ROOT_MISMATCH")
                self._private_profile_selection = selection
        self._state_dir = requested_state_dir
        from memorive_settings.task_scheduling import TASK_CATEGORIES
        TASK_CATEGORIES.bind(self._state_dir / 'profile')
        self._source_root = source_root.resolve()
        self._method_binding_path = method_binding_path.resolve()
        self._fixture_root = self._source_root.parent / "fixtures" / "source"
        self._window: Any | None = None
        self._assistant_window: Any | None = None
        self._assistant_presenter: Any | None = None
        self._notification_presenter: Any | None = None
        self._notification_delivery_count = 0
        self._last_notification_receipt: dict[str, Any] | None = None
        self._assistant_move_count = 0
        self._core_event_lock = threading.RLock()
        self._core_event_stop = threading.Event()
        self._core_event_thread: threading.Thread | None = None
        self._core_event_revision = 0
        self._core_event_fingerprint: str | None = None
        self._core_event_previous_snapshot: dict[str, Any] | None = None
        self._core_event_last_receipt: dict[str, Any] | None = None
        self._exit_requested = False
        self._network_runner = network_runner
        self._network_test_count = 0
        self._system_effects_enabled = bool(system_effects_enabled)
        self._autostart_manager = autostart_manager or WindowsAutostartManager()
        self._last_runtime_preferences_receipt: dict[str, Any] | None = None
        self._state_dir.mkdir(parents=True, exist_ok=True)
        _ensure_source_importable(self._source_root)
        binding = json.loads(self._method_binding_path.read_text(encoding="utf-8"))
        if binding.get("status") != "PASS" or binding.get("method_count") != 12:
            raise RuntimeError("FACADE_METHOD_BINDING_INVALID")
        self._method_binding = binding
        self._facade_methods = frozenset(binding["required_methods"])
        self._lock = threading.RLock()
        # Product calls remain serialized, but they must not hold the state /
        # durable-receipt lock while invoking WinForms or WebView presenters.
        # A presenter can synchronously write a receipt through ``self._lock``
        # on the UI thread; sharing that lock here creates a cross-thread cycle.
        self._product_call_lock = threading.RLock()
        event_root = self._state_dir / "events"
        event_root.mkdir(parents=True, exist_ok=True)
        self._event_root = event_root
        self._sequence = len(list(event_root.glob("event-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].json")))
        profile_root = self._state_dir / "profile"
        self._profile_root = profile_root
        folder_module = importlib.import_module("memorive_folder_management")
        self._folder_manager = folder_module.ProfileFolderManager(profile_root)
        self._inbox_root = self._profile_path("INBOX")
        self._inbox_source_root = self._profile_path("INBOX_SOURCE")
        windows_io_path(self._inbox_source_root).mkdir(parents=True, exist_ok=True)
        ui_root = self._profile_path("UI_STATE")
        ui_root.mkdir(parents=True, exist_ok=True)
        self._core_event_receipt_path = ui_root / "core_event_pump_receipt_v1.json"
        self._settings_save_receipt_path = ui_root / "settings_save_receipt_v1.json"
        self._assistant_presentation_receipt_path = ui_root / "assistant_presentation_receipt_v1.json"
        legacy_assistant_preferences_path = ui_root / "assistant_preferences_v1.json"
        legacy_assistant_preferences_v2_path = ui_root / "assistant_preferences_v2.json"
        self._assistant_preferences_path = ui_root / "assistant_preferences_v3.json"
        if not self._assistant_preferences_path.exists():
            enabled = True
            reminder_scope = "IMPORTANT_AND_COMPLETE"
            hide_sensitive_names = False
            shortcut_entries = self._default_assistant_shortcut_entries()
            legacy_preferences: dict[str, Any] = {}
            source_path = next(
                (
                    path
                    for path in (
                        legacy_assistant_preferences_v2_path,
                        legacy_assistant_preferences_path,
                    )
                    if path.is_file()
                ),
                None,
            )
            if source_path is not None:
                try:
                    legacy_preferences = json.loads(
                        source_path.read_text(encoding="utf-8")
                    )
                    if isinstance(legacy_preferences.get("enabled"), bool):
                        enabled = legacy_preferences["enabled"]
                    if legacy_preferences.get("reminder_scope") in {
                        "IMPORTANT_ONLY",
                        "IMPORTANT_AND_COMPLETE",
                        "INCLUDE_ORDINARY",
                    }:
                        reminder_scope = legacy_preferences["reminder_scope"]
                    if isinstance(legacy_preferences.get("hide_sensitive_names"), bool):
                        hide_sensitive_names = legacy_preferences["hide_sensitive_names"]
                except (OSError, json.JSONDecodeError, AttributeError):
                    legacy_preferences = {}
            legacy_labels = {
                "CODEX": "Codex",
                "CLAUDE_CODE": "Claude Code",
                "CC_SWITCH": "CC Switch",
            }
            legacy_slots = legacy_preferences.get("shortcut_slots", [])
            if not isinstance(legacy_slots, list):
                legacy_slots = []
            for index, target_id in enumerate(legacy_slots[:3]):
                if target_id not in legacy_labels:
                    continue
                target_path = self._legacy_assistant_target_path(target_id)
                if target_path is not None:
                    shortcut_entries[index] = {
                        "name": legacy_labels[target_id],
                        "target": str(target_path),
                    }
            self._write_json_file(
                self._assistant_preferences_path,
                {
                    "schema_version": "DesktopAssistantPreferences-v3",
                    "enabled": enabled,
                    "shortcut_entries": shortcut_entries,
                    "reminder_scope": reminder_scope,
                    "hide_sensitive_names": hide_sensitive_names,
                },
            )
        else:
            self._seed_blank_assistant_shortcut_file(self._assistant_preferences_path)
        control_module = importlib.import_module("memorive_desktop_service.control_store")
        outbox_module = importlib.import_module("memorive_desktop_service.command_outbox")
        instance_module = importlib.import_module("memorive_desktop_service.single_instance")
        self._control_store = control_module.ControlStore(ui_root)
        self._control_envelope = self._control_store.load()
        self._outbox = outbox_module.DesktopCommandOutbox(ui_root / "command_outbox.sqlite3")
        self._single_instance = instance_module.SingleInstanceCoordinator(ui_root)
        if not self._single_instance.acquire_primary():
            raise RuntimeError("DESKTOP_INSTANCE_ALREADY_ACTIVE")
        self._service = ServiceProcessManager(
            self._source_root,
            profile_root,
            self._state_dir / "service-runtime",
            test_fixture_mode=test_fixture_mode,
        )
        self._closed = False
        try:
            self._service.start()
            local_models_module = importlib.import_module("memorive_local_models.product_controller")
            self._local_models = local_models_module.LocalModelProductController(
                self._state_dir / "local_models"
            )
            accounting_module = importlib.import_module("memorive_accounting")
            self._accounting = accounting_module.AccountingProductController(
                settings_provider=lambda: self._service.call("settings.get_state", {}),
                local_model_registry=self._local_models,
                leaderboard_provider=lambda: self._service.call("settings.leaderboard_get", {}),
                exam_root=self._profile_path("MODEL_VALIDATION"),
                core_root=self._profile_path("Core_JOBS"),
            )
            refinement_module = importlib.import_module(
                "memorive_workflow_bridge.conversation_refinement"
            )
            self._conversation_refinement_channel = (
                refinement_module.ExistingConversationRefinementChannel(
                    local_model_registry=self._local_models,
                    profile_executor=self._service,
                )
            )
            refinement_tasks_module = importlib.import_module(
                "memorive_current_task.refinement_tasks"
            )
            self._refinement_queue_tasks = (
                refinement_tasks_module.SessionRefinementTaskStore(
                    self._state_dir / "sessions" / "refinement_queue_tasks"
                )
            )
            controller_module = importlib.import_module("memorive_current_task.product_controller")
            self._current_task = controller_module.CurrentTaskProductController(
                self._service,
                self._control_store,
                self._source_root.parent / "contracts" / "verification",
                settings_provider=lambda: self._service.call("settings.get_state", {}),
                local_model_registry=self._local_models,
                refinement_store=self._refinement_queue_tasks,
                include_synthetic_projection=test_fixture_mode,
                completed_success_retention_seconds=900,
                cancelled_retention_seconds=60,
                history_retention_seconds=30 * 24 * 60 * 60,
                task_lifecycle_root=self._state_dir / "current_task",
                core_task_control_root=self._profile_path(
                    "Core_TASK_CONTROLS"
                ),
            )
            messages_module = importlib.import_module("memorive_messages.product_controller")
            self._messages = messages_module.MessagesProductController(
                self._service, self._control_store,
                self._source_root.parent / "contracts" / "retrieval",
                self._source_root.parent / "fixtures_retrieval",
                self._state_dir / "messages",
                include_synthetic_projection=test_fixture_mode,
                core_job_provider=lambda: self._core_job_rows(include_history=True),
            )
            library_module = importlib.import_module("memorive_library.product_controller")
            self._library = library_module.LibraryProductController(
                self._service, self._control_store,
                self._source_root.parent / "contracts" / "integration",
                self._source_root.parent / "fixtures_integration",
                self._state_dir / "library",
                include_synthetic_projection=test_fixture_mode,
                core_artifact_provider=self._core_artifact_rows,
            )
            sessions_module = importlib.import_module("memorive_sessions.product_controller")
            if session_visible_ui_reader is None:
                visible_ui_module = importlib.import_module("memorive_sessions.edge_visible_ui")
                session_visible_ui_reader = {
                    "deepseek": visible_ui_module.EdgeDeepSeekVisibleUIReader(),
                    "gemini": visible_ui_module.EdgeGeminiVisibleUIReader(),
                    "kimi": visible_ui_module.EdgeKimiVisibleUIReader(),
                }
            self._sessions = sessions_module.SessionsProductController(
                self._service, self._control_store,
                self._source_root.parent / "contracts" / "analysis",
                self._source_root.parent / "fixtures_analysis",
                self._state_dir / "sessions",
                source_roots=session_source_roots,
                visible_ui_reader=session_visible_ui_reader,
                legacy_deepseek_visible_ui_projection=(
                    os.environ.get("MEMORIVE_ENABLE_RETIRED_CURRENT_PAGE_SNAPSHOTS") == "1"
                ),
                local_model_registry=self._local_models,
                conversation_refinement_channel=self._conversation_refinement_channel,
                refinement_task_manager=self._refinement_queue_tasks,
                refinement_lifecycle_observer=self._on_refinement_lifecycle,
                refinement_queue_observer=self._on_refinement_queue_projection,
                refinement_result_observer=self._on_refinement_result,
            )
            self._sessions.sync_refinement_queue()
            self._sessions.browser_sites_provider = self.browser_companion_sites
            work_log_module = importlib.import_module("memorive_work_log.product_controller")
            self._work_log = work_log_module.WorkLogProductController(
                self._service, self._control_store,
                self._source_root.parent / "contracts" / "logging",
                self._source_root.parent / "fixtures_logging",
                self._state_dir / "work_log",
                include_synthetic_projection=test_fixture_mode,
                refinement_task_provider=self._refinement_queue_tasks.list_jobs,
                refinement_product_provider=self._library.refinement_activity_rows,
                core_job_provider=self._core_job_rows,
                core_product_provider=self._library.core_activity_rows,
            )
            assistant_module = importlib.import_module("memorive_desktop_assistant")
            placement_module = importlib.import_module("memorive_desktop_assistant.placement")
            shortcut_module = importlib.import_module("memorive_desktop_assistant.shortcuts")

            def assistant_handler(request: dict[str, Any]) -> dict[str, Any]:
                action_id = request["action_id"]
                routes = {
                    "SHOW_DESKTOP_ASSISTANT": {"route": "assistant", "visibility": "visible"},
                    "TOGGLE_DESKTOP_ASSISTANT": {"route": "assistant", "visibility": "toggle"},
                    "OPEN_Desktop_MAIN": {"focus_existing_main": True, "preserve_route": True},
                    "OPEN_DESKTOP_ASSISTANT_SETTINGS": {"route": "settings", "section": "behavior"},
                    "HIDE_DESKTOP_ASSISTANT": {"route": "assistant", "visibility": "hidden"},
                    "OPEN_MESSAGE_DETAIL": {"route": "messages", "locator": request.get("locator")},
                    "RESTORE_DEFAULT_PLACEMENT": {"route": "assistant", "placement": "default_requested"},
                }
                return {"accepted": True, **routes[action_id]}

            handlers = {
                action_id: assistant_handler
                for action_id in shortcut_module.FIXED_INTERNAL_ACTION_IDS
            }
            shortcuts = assistant_module.SafeShortcutRegistry(handlers)
            self._assistant = assistant_module.DesktopAssistantController(self._control_store, shortcuts)
            persisted_assistant = self._load_assistant_preferences()
            self._assistant.set_control(
                enabled=bool(persisted_assistant["enabled"]),
                visible=bool(persisted_assistant["enabled"]),
            )
            self._assistant_placement_class = assistant_module.AssistantWindowPlacement
            self._assistant_clamp_placement = placement_module.clamp_placement
            self._assistant_visible_bounds = placement_module.visible_bounds
            self._assistant_areas = self._detect_assistant_work_areas(placement_module.WorkArea)
            self._browser_companion_auto_update = (
                self._refresh_prepared_browser_companion_package()
            )
            from memorive_research_runtime.runtime import ResearchRuntime
            self._research = ResearchRuntime(self)
            self._library.research_artifact_provider = self._research.library_rows
            from memorive_research_runtime.task_projection import log_rows
            self._work_log.research_provider=lambda:log_rows(self._research)
            self._research_selected_run=None
            from memorive_desktop_assistant.delivery import AssistantDeliveryQueue
            self._assistant_delivery_session = uuid.uuid4().hex
            self._assistant_deliveries = AssistantDeliveryQueue(self._profile_path("UI_STATE") / "assistant_deliveries_v1.json")
            self._assistant_deliveries.seed('message:' + row['message_id'] for row in self._messages.call('messages.list',{})['rows'])
        except Exception:
            self._service.close()
            self._single_instance.release()
            raise

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

    @staticmethod
    def _job_locator(job_id: Any) -> str | None:
        rendered = str(job_id or "")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", rendered):
            return f"memorive://job/{rendered}"
        return None

    def _core_job_rows(self, *, include_history: bool = False) -> list[dict[str, Any]]:
        """Return public-safe metadata for real Core jobs only."""

        projection = self._current_task._list(history_details=include_history)
        bindings = (
            [*projection.get("production_bindings", []),
             *(projection.get("history_bindings", []) if include_history else [])]
            if isinstance(projection, dict) else []
        )
        if not isinstance(bindings, list):
            raise RuntimeError("Core_JOB_BINDINGS_INVALID")
        rows: list[dict[str, Any]] = []
        for raw in bindings:
            if not isinstance(raw, Mapping) or raw.get("workflow_kind") != "CORE_DOCUMENT":
                continue
            job_id = str(raw.get("job_id") or "")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}", job_id):
                raise RuntimeError("Core_JOB_ID_INVALID")
            job = self._service.call("get_job", {"job_id": job_id})
            nodes = deepcopy(raw.get("workflow_nodes") or [])
            failed_node = next(
                (
                    str(node.get("node_id") or "")
                    for node in nodes
                    if isinstance(node, Mapping) and node.get("state") == "FAILED"
                ),
                "",
            )
            events = self._service.call(
                "get_job_events", {"job_id": job_id, "after_sequence": 0}
            )
            event_rows = events.get("events", []) if isinstance(events, Mapping) else []
            terminal_event = next(
                (
                    event
                    for event in reversed(event_rows)
                    if isinstance(event, Mapping)
                    and str(event.get("control_state") or "").upper()
                    in {"SUCCEEDED", "FAILED", "CANCELLED"}
                ),
                {},
            )
            event_type = str(terminal_event.get("event_type") or "")
            error_code = ""
            if str(job.get("control_state") or "").upper() == "FAILED":
                error_code = event_type.split("Core_FAILED:", 1)[-1] if "Core_FAILED:" in event_type else event_type
            rows.append(
                {
                    **deepcopy(dict(raw)),
                    "job_id": job_id,
                    "attempt_id": str(job.get("attempt_id") or raw.get("attempt_id") or ""),
                    "control_state": str(job.get("control_state") or raw.get("control_state") or "UNKNOWN").upper(),
                    "display_name": str(raw.get("display_name") or job_id)[:180],
                    "workflow_kind": "CORE_DOCUMENT",
                    "nodes": nodes,
                    "workflow_nodes": nodes,
                    "failed_node_id": failed_node or None,
                    "error_code": re.sub(r"[^A-Z0-9_:-]", "_", error_code.upper())[:120],
                    "created_at": job.get("created_at") or raw.get("created_at"),
                    "updated_at": job.get("updated_at") or raw.get("updated_at"),
                    "completed_at": (
                        job.get("updated_at")
                        if str(job.get("control_state") or "").upper() in {"SUCCEEDED", "FAILED", "CANCELLED"}
                        else None
                    ),
                    "artifact_count": len(job.get("artifact_bindings") or []),
                    "context_pack": (
                        deepcopy(raw.get("context_pack"))
                        if isinstance(raw.get("context_pack"), Mapping)
                        else None
                    ),
                    "raw_private_content_included": False,
                }
            )
        return rows

    def _core_library_job_rows(self) -> list[dict[str, Any]]:
        """Return durable, successfully delivered Core products.

        Current Task intentionally retains completed cards for only a short UI
        window. Library is a durable product index and must therefore enumerate
        Application Service jobs directly instead of inheriting that transient
        retention policy.
        """

        projection = self._service.call("list_jobs", {})
        source = projection.get("jobs", []) if isinstance(projection, Mapping) else []
        if not isinstance(source, list):
            raise RuntimeError("Core_LIBRARY_JOB_LIST_INVALID")
        rows: list[dict[str, Any]] = []
        for status in source:
            if not isinstance(status, Mapping) or status.get("job_type") != "memorive-core":
                continue
            job_id = str(status.get("job_id") or "")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}", job_id):
                raise RuntimeError("Core_JOB_ID_INVALID")
            job = self._service.call("get_job", {"job_id": job_id})
            bindings = job.get("artifact_bindings") or []
            # Persisting a binding is not final delivery. In particular, never
            # run legacy folder projection against a live worker's partial set.
            # Cancelled/failed stages remain available through task history.
            if str(job.get("control_state") or "").upper() != "SUCCEEDED":
                continue
            if not isinstance(bindings, list) or not bindings:
                continue
            resolved = (
                job.get("snapshots", {})
                .get("resolved_task_input", {})
                .get("content", {})
            )
            if not isinstance(resolved, Mapping):
                resolved = {}
            rows.append(
                {
                    "job_id": job_id,
                    "attempt_id": str(job.get("attempt_id") or status.get("attempt_id") or ""),
                    "control_state": str(
                        job.get("control_state")
                        or status.get("job_control_state")
                        or "UNKNOWN"
                    ).upper(),
                    "display_name": str(
                        resolved.get("display_name")
                        or status.get("display_name")
                        or job_id
                    )[:180],
                    "source_sha256": str(
                        resolved.get("content_sha256")
                        or job.get("source_sha256")
                        or ""
                    ).upper(),
                    "created_at": job.get("created_at") or status.get("created_at"),
                    "updated_at": job.get("updated_at") or status.get("updated_at"),
                    "artifact_count": len(bindings),
                    "context_pack": None,
                    "raw_private_content_included": False,
                }
            )
        rows.sort(
            key=lambda row: (
                str(row.get("updated_at") or ""),
                str(row.get("job_id") or ""),
            ),
            reverse=True,
        )
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
            source_rows = self._core_job_rows()

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
        if hasattr(self, "_assistant"):
            try:
                presenter = self._assistant_presenter
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

    @staticmethod
    def _core_projection_error_code(error: BaseException) -> str:
        if isinstance(error, OSError) and getattr(error, "winerror", None) in {206, 3}:
            return "Core_LIBRARY_LEGACY_PATH_UNAVAILABLE"
        value = str(error).strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,119}", value):
            return value
        return "Core_LIBRARY_" + type(error).__name__.upper()

    def _core_unavailable_artifact_row(
        self, core_job: Mapping[str, Any], error: BaseException
    ) -> dict[str, Any]:
        """Persist one public-safe degraded row without hiding the failed job."""

        job_id = str(core_job.get("job_id") or "")
        error_code = self._core_projection_error_code(error)
        root = (self._state_dir / "library" / "core-unavailable").resolve(strict=False)
        root.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(f"{job_id}\0{error_code}".encode("utf-8")).hexdigest()[:24]
        artifact_id = "core-product-" + identity
        target = root / f"{identity}.json"
        payload = {
            "schema_version": "MemoriveCoreUnavailableProjection-v1",
            "status": "SOURCE_UNAVAILABLE",
            "error_code": error_code,
            "job_id": job_id,
            "provider": "core_artifact_provider",
            "skipped_record_and_continued": True,
            "raw_private_content_included": False,
        }
        self._write_json_file(target, payload)
        content_sha256 = hashlib.sha256(target.read_bytes()).hexdigest().upper()
        source_sha256 = str(core_job.get("source_sha256") or "").upper()
        if re.fullmatch(r"[A-F0-9]{64}", source_sha256) is None:
            source_sha256 = hashlib.sha256(f"unavailable\0{job_id}".encode("utf-8")).hexdigest().upper()
        return {
            "artifact_id": artifact_id,
            "source_artifact_id": "core-unavailable",
            "artifact_kind": "Core_UNAVAILABLE",
            "job_id": job_id,
            "display_name": f"{str(core_job.get('display_name') or job_id)} · 源文件不可用",
            "kind": "源文件不可用",
            "content_sha256": content_sha256,
            "relative_path": f"core-unavailable/{target.name}",
            "private_path": str(target),
            "artifact_root": str(root),
            "updated_at": core_job.get("updated_at") or core_job.get("created_at"),
            "size_bytes": target.stat().st_size,
            "source_file_name": "source-unavailable.pdf",
            "source_private_path": str(root / "source-unavailable.pdf"),
            "source_root": str(root),
            "source_content_sha256": source_sha256,
            "source_file_exists": False,
            "source_error_code": error_code,
            "projection_status": "SOURCE_UNAVAILABLE",
            "product_files": [
                {"artifact_id": artifact_id, "kind": "源文件不可用", "file_name": target.name}
            ],
            "raw_private_content_included": False,
        }

    def _core_artifact_rows(self) -> list[dict[str, Any]]:
        """Project every job independently so one legacy defect cannot fail the page."""

        rows: list[dict[str, Any]] = []
        for core_job in self._core_library_job_rows():
            try:
                rows.extend(self._core_artifact_rows_for_jobs([core_job]))
            except (OSError, RuntimeError, ValueError) as error:
                rows.append(self._core_unavailable_artifact_row(core_job, error))
        return rows

    def _core_artifact_rows_for_jobs(
        self, core_jobs: list[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Resolve selected Core bindings to verified controlled files."""

        kind_names = {
            "CORE_SOURCE_DOCUMENT": "原始文献",
            "CORE_RAW_DOCUMENT": "原始转换文档",
            "CORE_CLEAN_DOCUMENT": "清洗文档",
            "CORE_CHUNKS": "Chunks",
            "CORE_CARD": "Card",
            "CORE_CONTEXT_PACK": "Context Pack",
            "CORE_ANALYSIS": "Analysis",
            "CORE_JUDGMENT_REVIEW": "判断类异源校对",
            "CORE_HUMAN_REVIEW": "人工最终复核包",
            "CORE_FOLDER_MANIFEST": "文件夹清单",
        }
        rows: list[dict[str, Any]] = []
        for core_job in core_jobs:
            job_id = core_job["job_id"]
            job = self._service.call("get_job", {"job_id": job_id})
            artifact_root = (
                self._profile_path("Core_JOBS")
                / job_id
                / "artifacts"
            ).resolve(strict=False)
            root = artifact_root.resolve(strict=True)
            resolved_input = (
                job.get("snapshots", {})
                .get("resolved_task_input", {})
                .get("content", {})
            )
            source_document: dict[str, Any] | None = None
            legacy_source_error_code: str | None = None
            legacy_source_candidate: Path | None = None
            legacy_source_sha256 = ""
            if isinstance(resolved_input, Mapping):
                source_locator = str(resolved_input.get("bound_locator") or "").replace(
                    "\\", "/"
                )
                source_sha256 = str(
                    resolved_input.get("content_sha256")
                    or core_job.get("source_sha256")
                    or job.get("source_sha256")
                    or ""
                ).upper()
                legacy_source_sha256 = source_sha256
                if source_locator or source_sha256:
                    source_parts = source_locator.split("/")
                    if (
                        not source_locator
                        or source_locator.startswith("/")
                        or any(
                            part in {"", ".", ".."} or ":" in part
                            for part in source_parts
                        )
                        or re.fullmatch(r"[A-F0-9]{64}", source_sha256) is None
                    ):
                        legacy_source_error_code = "Core_LIBRARY_SOURCE_BINDING_INVALID"
                    else:
                        inbox_root = self._profile_path("INBOX").resolve(strict=True)
                        source_candidate = inbox_root.joinpath(*source_parts)
                        legacy_source_candidate = source_candidate
                        try:
                            source_attributes = source_candidate.lstat()
                            source_target = source_candidate.resolve(strict=True)
                        except OSError as error:
                            legacy_source_error_code = (
                                "Core_LIBRARY_LEGACY_PATH_UNAVAILABLE"
                                if getattr(error, "winerror", None) in {206, 3}
                                else "Core_LIBRARY_SOURCE_UNAVAILABLE"
                            )
                        else:
                            reparse_mask = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                            if (
                                source_candidate.is_symlink()
                                or bool(
                                    getattr(source_attributes, "st_file_attributes", 0)
                                    & reparse_mask
                                )
                                or not source_target.is_relative_to(inbox_root)
                                or not source_target.is_file()
                                or source_target.suffix.casefold() != ".pdf"
                            ):
                                legacy_source_error_code = "Core_LIBRARY_SOURCE_PATH_INVALID"
                            else:
                                observed_source_sha256 = hashlib.sha256(
                                    source_target.read_bytes()
                                ).hexdigest().upper()
                                if observed_source_sha256 != source_sha256:
                                    legacy_source_error_code = "Core_LIBRARY_SOURCE_HASH_MISMATCH"
                                else:
                                    source_document = {
                                        "source_root": str(inbox_root),
                                        "private_path": str(source_target),
                                        "sha256": source_sha256,
                                    }
            verified_bindings: list[dict[str, Any]] = []
            for binding in job.get("artifact_bindings") or []:
                if not isinstance(binding, Mapping):
                    continue
                source_artifact_id = str(binding.get("artifact_id") or "")
                locator = str(binding.get("locator") or "")
                prefix = f"core-artifact:{job_id}:"
                if not locator.startswith(prefix):
                    raise RuntimeError("Core_LIBRARY_ARTIFACT_LOCATOR_INVALID")
                relative = locator[len(prefix) :].replace("\\", "/")
                parts = relative.split("/")
                if (
                    not relative
                    or relative.startswith("/")
                    or any(part in {"", ".", ".."} for part in parts)
                    or any(":" in part for part in parts)
                ):
                    raise RuntimeError("Core_LIBRARY_ARTIFACT_RELATIVE_PATH_INVALID")
                target = artifact_root.joinpath(*parts).resolve(strict=True)
                if (
                    not target.is_relative_to(root)
                    or not target.is_file()
                    or target.is_symlink()
                    or self._is_reparse_point(target)
                ):
                    raise RuntimeError("Core_LIBRARY_ARTIFACT_PATH_INVALID")
                observed = hashlib.sha256(target.read_bytes()).hexdigest().upper()
                expected = str(binding.get("sha256") or "").upper()
                if not re.fullmatch(r"[A-F0-9]{64}", expected) or observed != expected:
                    raise RuntimeError("Core_LIBRARY_ARTIFACT_HASH_MISMATCH")
                verified_bindings.append(
                    {
                        "binding": binding,
                        "artifact_id": source_artifact_id,
                        "relative_path": relative,
                        "target": target,
                        "sha256": observed,
                    }
                )

            legacy_stage_directories = {
                "analysis", "card", "context", "document", "review", "verification"
            }
            projection: dict[str, Any] | None = None
            if verified_bindings and (
                source_document is not None
                or any(
                str(row["relative_path"]).split("/", 1)[0] in legacy_stage_directories
                for row in verified_bindings
                )
            ):
                summary: dict[str, Any] = {}
                summary_path = artifact_root.parent / "core_execution_summary.json"
                if summary_path.is_file() and not summary_path.is_symlink():
                    try:
                        parsed = json.loads(summary_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as error:
                        raise RuntimeError("Core_FOLDER_PROJECTION_SUMMARY_INVALID") from error
                    if isinstance(parsed, dict):
                        summary = parsed
                folder_module = importlib.import_module("memorive_folder_management")
                projection = folder_module.ensure_legacy_core_projection(
                    artifact_root=artifact_root,
                    job_id=job_id,
                    paper_id=str(
                        summary.get("paper_id")
                        or core_job.get("paper_id")
                        or ""
                    ),
                    title=str(core_job["display_name"]),
                    source_sha256=(
                        str(core_job.get("source_sha256") or job.get("source_sha256") or "")
                        or None
                    ),
                    source_document=source_document,
                    bindings=[
                        {
                            "artifact_id": row["artifact_id"],
                            "relative_path": row["relative_path"],
                            "sha256": row["sha256"],
                        }
                        for row in verified_bindings
                    ],
                )

            source_target: Path | None = None
            source_content_sha256 = ""
            if projection is not None and projection.get("source_document") is not None:
                source_value = projection["canonical_path_by_artifact_id"].get(
                    "core-source-document"
                )
                source_content_sha256 = str(
                    projection["source_document"].get("sha256") or ""
                ).upper()
                if source_value:
                    source_target = Path(source_value).resolve(strict=True)
                    if (
                        not source_target.is_relative_to(root)
                        or not source_target.is_file()
                        or source_target.is_symlink()
                        or hashlib.sha256(source_target.read_bytes()).hexdigest().upper()
                        != source_content_sha256
                    ):
                        raise RuntimeError("Core_FOLDER_PROJECTION_SOURCE_INVALID")

            # Canonical jobs already carry the original PDF as a hash-verified
            # artifact. Their input snapshot need not retain an inbox locator.
            # Bind every product to that PDF, never to a display title or to its
            # own (different) artifact hash.
            if source_target is None:
                originals = [row for row in verified_bindings
                             if row["artifact_id"] == "core-source-document"]
                if len(originals) > 1:
                    raise RuntimeError("Core_LIBRARY_SOURCE_CARDINALITY_INVALID")
                if originals:
                    source_target = originals[0]["target"]
                    source_content_sha256 = originals[0]["sha256"]
                    expected_source = str(core_job.get("source_sha256") or job.get("source_sha256") or "").upper()
                    if expected_source and expected_source != source_content_sha256:
                        raise RuntimeError("Core_LIBRARY_SOURCE_HASH_MISMATCH")

            job_rows: list[dict[str, Any]] = []
            for verified in verified_bindings:
                binding = verified["binding"]
                source_artifact_id = str(verified["artifact_id"])
                relative = str(verified["relative_path"])
                target = Path(verified["target"])
                observed = str(verified["sha256"])
                canonical_target = target
                if projection is not None:
                    canonical_value = projection["canonical_path_by_artifact_id"].get(
                        source_artifact_id
                    )
                    if canonical_value:
                        canonical_target = Path(canonical_value).resolve(strict=True)
                        if (
                            not canonical_target.is_relative_to(artifact_root.resolve(strict=True))
                            or not canonical_target.is_file()
                            or canonical_target.is_symlink()
                            or hashlib.sha256(canonical_target.read_bytes()).hexdigest().upper()
                            != observed
                        ):
                            raise RuntimeError("Core_FOLDER_PROJECTION_TARGET_INVALID")
                canonical_relative = canonical_target.relative_to(artifact_root).as_posix()
                artifact_id = "core-product-" + hashlib.sha256(
                    f"{job_id}\0{source_artifact_id}".encode("utf-8")
                ).hexdigest()[:24]
                artifact_kind = source_artifact_id.replace("core-", "Core_").replace("-", "_").upper()
                display_name = kind_names.get(artifact_kind, source_artifact_id or target.stem)
                job_rows.append(
                    {
                        "artifact_id": artifact_id,
                        "source_artifact_id": source_artifact_id,
                        "artifact_kind": artifact_kind,
                        "job_id": job_id,
                        "display_name": f"{core_job['display_name']} · {display_name}",
                        "kind": display_name,
                        "content_sha256": observed,
                        "relative_path": f"core/{job_id}/{canonical_relative}",
                        "private_path": str(canonical_target),
                        "artifact_root": str(root),
                        "updated_at": core_job.get("updated_at") or core_job.get("created_at"),
                        "size_bytes": canonical_target.stat().st_size,
                        "context_pack": (
                            deepcopy(core_job.get("context_pack"))
                            if source_artifact_id == "core-context-pack"
                            and isinstance(core_job.get("context_pack"), Mapping)
                            else None
                        ),
                        "raw_private_content_included": False,
                    }
                )
            product_files = [
                {
                    "artifact_id": row["artifact_id"],
                    "kind": row["kind"],
                    "file_name": Path(row["private_path"]).name,
                }
                for row in job_rows
            ]
            if source_target is not None:
                for row in job_rows:
                    row.update(
                        {
                            "source_file_name": source_target.name,
                            "source_private_path": str(source_target),
                            "source_root": str(root),
                            "source_content_sha256": source_content_sha256,
                            "source_file_exists": True,
                        }
                    )
            elif legacy_source_error_code is not None:
                unavailable_root = self._profile_path("INBOX").resolve(strict=False)
                unavailable_source = legacy_source_candidate or (unavailable_root / "bound" / "source-unavailable.pdf")
                unavailable_sha256 = legacy_source_sha256
                if re.fullmatch(r"[A-F0-9]{64}", unavailable_sha256) is None:
                    unavailable_sha256 = hashlib.sha256(
                        f"unavailable\0{job_id}".encode("utf-8")
                    ).hexdigest().upper()
                for row in job_rows:
                    row.update(
                        {
                            "source_file_name": unavailable_source.name,
                            "source_private_path": str(unavailable_source),
                            "source_root": str(unavailable_root),
                            "source_content_sha256": unavailable_sha256,
                            "source_file_exists": False,
                            "source_error_code": legacy_source_error_code,
                            "projection_status": "SOURCE_UNAVAILABLE",
                        }
                    )
            for row in job_rows:
                row["product_files"] = deepcopy(product_files)
            rows.extend(job_rows)
        return rows

    def _assistant_inputs(self) -> tuple[list[dict[str, Any]], int, int, int]:
        job_projection = self._service.call("list_jobs", {})
        rows = job_projection.get("jobs", []) if isinstance(job_projection, dict) else []
        sample_job_ids = frozenset(getattr(self._current_task, "sample_job_ids", ()))
        real_jobs: list[dict[str, Any]] = []
        sample_job_count = 0
        for raw in rows if isinstance(rows, list) else []:
            if not isinstance(raw, dict):
                continue
            if raw.get("job_id") in sample_job_ids or self._sample_job(raw):
                sample_job_count += 1
                continue
            projected = dict(raw)
            projected.setdefault("source_kind", "job")
            projected.setdefault("locator", self._job_locator(projected.get("job_id")))
            if projected.get("locator") is None:
                projected.pop("locator", None)
            real_jobs.append(projected)

        for raw in self._refinement_queue_tasks.list_jobs():
            if not isinstance(raw, dict) or raw.get("control_state") != "RUNNING":
                continue
            projected = {
                "source_kind": "job",
                "job_id": raw.get("job_id"),
                "state": "RUNNING",
                "control_state": "RUNNING",
                "updated_at": raw.get("updated_at", ""),
                "event_sequence": int(raw.get("version", 0)),
                "locator": self._job_locator(raw.get("job_id")),
            }
            if projected["locator"] is None:
                projected.pop("locator")
            real_jobs.append(projected)

        message_projection = self._messages.call("messages.list", {})
        message_rows = (
            message_projection.get("rows", []) if isinstance(message_projection, dict) else []
        )
        reminder_scope = self._load_assistant_preferences()["reminder_scope"]
        allowed_severities = {
            "IMPORTANT_ONLY": {"RED", "YELLOW"},
            "IMPORTANT_AND_COMPLETE": {"RED", "YELLOW", "GREEN"},
            "INCLUDE_ORDINARY": {"RED", "YELLOW", "GREEN", "BLUE"},
        }[reminder_scope]
        real_messages: list[dict[str, Any]] = []
        for raw in message_rows if isinstance(message_rows, list) else []:
            if not isinstance(raw, dict) or self._sample_message(raw):
                continue
            unread = raw.get("read_state") == "UNREAD" or raw.get("read") is False
            current = raw.get("collection_state", "CURRENT") == "CURRENT"
            if not unread or not current:
                continue
            severity = str(raw.get("severity", "BLUE")).upper()
            if severity not in allowed_severities:
                continue
            if severity in {"BLUE", "GREEN"} and any(
                row.get("control_state") == "RUNNING" for row in real_jobs
            ):
                continue
            state = {
                "RED": "ERROR",
                "YELLOW": "WAITING_USER",
                "GREEN": "COMPLETED",
                "BLUE": "RUNNING",
            }.get(severity, "RUNNING")
            source = raw.get("source_event_ref")
            locator = raw.get("target_locator")
            if not locator and isinstance(source, dict):
                locator = source.get("locator")
            projected = {
                "source_kind": "message",
                "message_id": raw.get("message_id"),
                "state": state,
                "updated_at": raw.get("last_seen_at", raw.get("updated_at", "")),
                "event_sequence": raw.get("event_sequence", raw.get("sequence", 0)),
            }
            if isinstance(locator, str) and locator.startswith("memorive://"):
                projected["locator"] = locator
            real_messages.append(projected)
            deliveries = getattr(self, '_assistant_deliveries', None)
            if deliveries is not None and severity in {'BLUE','RED'} and projected.get('message_id'):
                deliveries.enqueue('message:' + projected['message_id'], {
                    'display_state':'NOTIFYING', 'animation_state':'ERROR' if severity=='RED' else 'MESSAGE',
                    'reason_code':'MESSAGE_NEEDS_ATTENTION' if severity=='RED' else 'MESSAGE_UPDATE',
                    'attention_state':'ERROR' if severity=='RED' else 'INFO',
                    'safe_summary':'有消息需要处理' if severity=='RED' else '收到新消息',
                    'locator':projected.get('locator'),
                })
        return real_jobs + real_messages, len(real_jobs), sample_job_count, len(real_messages)

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
        if hasattr(self, "_assistant"):
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

    def _current_preferences(self) -> dict[str, Any]:
        state = self._service.call("settings.get_state", {})
        settings = state.get("settings") if isinstance(state, dict) else None
        preferences = settings.get("preferences") if isinstance(settings, dict) else None
        if not isinstance(preferences, dict):
            raise RuntimeError("SETTINGS_PREFERENCES_UNAVAILABLE")
        return dict(preferences)

    def _apply_runtime_preferences(self, preferences: dict[str, Any]) -> dict[str, Any]:
        autostart = (
            self._autostart_manager.apply(bool(preferences.get("launch_at_startup")))
            if self._system_effects_enabled
            else {
                "schema_version": "DesktopAutostartMutationReceipt-v1",
                "enabled_requested": bool(preferences.get("launch_at_startup")),
                "mutation_count": 0,
                "status": "NOT_APPLIED_TEST_OR_SOURCE_MODE",
            }
        )
        panel_state_cleared = False
        if preferences.get("remember_panel_state") is False:
            def clear_panel_state(state: dict[str, Any]) -> None:
                nonlocal panel_state_cleared
                filters = dict(state["filters"])
                panel_state_cleared = filters.pop("panel_state_v1", None) is not None
                state["filters"] = filters

            self._control_envelope = self._control_store.update(clear_panel_state)
        live_ui_status = "WINDOW_NOT_ATTACHED"
        if self._window is not None:
            try:
                self._window.evaluate_js(
                    "window.__Desktop_INTEGRATED_APP__?.applyPreferences("
                    + json.dumps(preferences, ensure_ascii=False, separators=(",", ":"))
                    + ")"
                )
                live_ui_status = "APPLIED"
            except Exception as error:
                live_ui_status = "ERROR:" + type(error).__name__
        receipt = {
            "schema_version": "DesktopRuntimePreferencesReceipt-v1",
            "language": preferences.get("language"),
            "font_scale_percent": preferences.get("font_scale_percent"),
            "show_full_tooltips": preferences.get("show_full_tooltips"),
            "remember_panel_state": preferences.get("remember_panel_state"),
            "panel_state_cleared": panel_state_cleared,
            "autostart": autostart,
            "live_ui_status": live_ui_status,
            "status": "PASS",
        }
        self._last_runtime_preferences_receipt = receipt
        return receipt

    @staticmethod
    def _detect_assistant_work_areas(work_area_class: type) -> tuple[Any, ...]:
        if os.name != "nt":
            return (work_area_class("DISPLAY1", 0, 0, 1920, 1040, primary=True),)

        from ctypes import wintypes

        class Rect(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", Rect),
                ("rcWork", Rect),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32),
            ]

        user32 = ctypes.windll.user32
        monitor_rows: list[Any] = []
        monitor_enum_proc = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HANDLE,
            wintypes.HDC,
            ctypes.POINTER(Rect),
            wintypes.LPARAM,
        )

        def monitor_dpi_percent(handle: Any) -> int:
            try:
                shcore = ctypes.windll.shcore
                scale_factor = ctypes.c_int(100)
                shcore.GetScaleFactorForMonitor.argtypes = [
                    wintypes.HANDLE,
                    ctypes.POINTER(ctypes.c_int),
                ]
                shcore.GetScaleFactorForMonitor.restype = ctypes.c_long
                if int(
                    shcore.GetScaleFactorForMonitor(
                        handle, ctypes.byref(scale_factor)
                    )
                ) == 0 and 50 <= int(scale_factor.value) <= 500:
                    return int(scale_factor.value)
                dpi_x = ctypes.c_uint(96)
                dpi_y = ctypes.c_uint(96)
                shcore.GetDpiForMonitor.argtypes = [
                    wintypes.HANDLE,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_uint),
                    ctypes.POINTER(ctypes.c_uint),
                ]
                shcore.GetDpiForMonitor.restype = ctypes.c_long
                if int(
                    shcore.GetDpiForMonitor(
                        handle, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
                    )
                ) == 0:
                    return max(50, min(500, int(round(dpi_x.value * 100 / 96))))
            except (AttributeError, OSError):
                pass
            try:
                user32.GetDpiForSystem.restype = wintypes.UINT
                dpi = int(user32.GetDpiForSystem())
                if dpi > 0:
                    return max(50, min(500, int(round(dpi * 100 / 96))))
            except (AttributeError, OSError):
                pass
            return 100

        def append_monitor(handle, _device_context, _bounds, _data) -> bool:
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                return True
            device = str(info.szDevice).strip("\\.\x00")
            monitor_id = device if device else f"DISPLAY{len(monitor_rows) + 1}"
            area = info.rcWork
            if area.right > area.left and area.bottom > area.top:
                monitor_rows.append(
                    work_area_class(
                        monitor_id,
                        int(area.left),
                        int(area.top),
                        int(area.right),
                        int(area.bottom),
                        primary=bool(info.dwFlags & 1),
                        dpi_percent=monitor_dpi_percent(handle),
                    )
                )
            return True

        callback = monitor_enum_proc(append_monitor)
        user32.EnumDisplayMonitors(None, None, callback, 0)
        if monitor_rows:
            return tuple(monitor_rows)

        rect = Rect()
        spi_get_work_area = 0x0030
        if user32.SystemParametersInfoW(spi_get_work_area, 0, ctypes.byref(rect), 0):
            return (
                work_area_class(
                    "DISPLAY1",
                    int(rect.left),
                    int(rect.top),
                    int(rect.right),
                    int(rect.bottom),
                    primary=True,
                    dpi_percent=monitor_dpi_percent(None),
                ),
            )
        width = int(user32.GetSystemMetrics(0)) or 1920
        height = int(user32.GetSystemMetrics(1)) or 1080
        return (
            work_area_class(
                "DISPLAY1",
                0,
                0,
                width,
                height,
                primary=True,
                dpi_percent=monitor_dpi_percent(None),
            ),
        )

    def _refresh_assistant_display_topology(self) -> tuple[Any, ...]:
        if not self._assistant_areas:
            raise RuntimeError("ASSISTANT_WORK_AREAS_EMPTY")
        work_area_class = type(self._assistant_areas[0])
        self._assistant_areas = self._detect_assistant_work_areas(work_area_class)
        return self._assistant_areas

    def _params(self, method: str, args: list[Any], kwargs: dict[str, Any]) -> dict[str, Any]:
        specification = self._method_binding["methods"][method]
        positional = specification["positional_parameters"]
        if len(args) > len(positional):
            raise ValueError("FACADE_POSITIONAL_ARGUMENT_COUNT_INVALID")
        allowed = set(positional) | set(specification["keyword_only_parameters"])
        if not specification["allows_varkw"] and set(kwargs) - allowed:
            raise ValueError("FACADE_KEYWORD_ARGUMENT_INVALID")
        params = dict(kwargs)
        for index, value in enumerate(args):
            name = positional[index]
            if name in params:
                raise ValueError("FACADE_ARGUMENT_DUPLICATED")
            params[name] = value
        return params

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or sorted(request) != ["event_type", "payload", "request_id", "source"]:
            raise ValueError("typed event request key set mismatch")
        event_type = request["event_type"]
        if event_type not in ALLOWED_EVENTS:
            raise ValueError("typed event is not allowlisted")
        if not isinstance(request["request_id"], str) or not request["request_id"].startswith("desktop-ui-"):
            raise ValueError("request_id invalid")
        if not isinstance(request["source"], str) or not 1 <= len(request["source"]) <= 96:
            raise ValueError("source invalid")
        if not isinstance(request["payload"], dict):
            raise ValueError("payload must be an object")
        with self._lock:
            control_state_mutated = False
            if event_type == "navigation.select" and isinstance(request["payload"].get("route"), str):
                route = request["payload"]["route"]
                self._control_envelope = self._control_store.update(lambda state: state.__setitem__("route", route))
                control_state_mutated = True
            self._sequence += 1
            receipt = {
                "schema_version": "WorkLogTypedEventReceipt-v1",
                "sequence": self._sequence,
                "request_id": request["request_id"],
                "event_type": event_type,
                "source": request["source"],
                "payload_sha256": _sha256_json(request["payload"]),
                "transport_status": "LOCAL_PYWEBVIEW_DISPATCHED",
                "control_state_mutated": control_state_mutated,
                "control_store_revision": self._control_envelope["revision"],
                "business_state_mutated": False,
            }
            destination = self._event_root / f"event-{self._sequence:08d}.json"
            with destination.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(receipt, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            return receipt

    def invoke_facade(self, request: dict[str, Any]) -> Any:
        if not isinstance(request, dict) or sorted(request) != ["args", "kwargs", "method", "request_id"]:
            raise ValueError("facade request key set mismatch")
        method = request["method"]
        if method not in self._facade_methods:
            raise ValueError("facade method is not allowlisted")
        args = request["args"]
        kwargs = request["kwargs"]
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValueError("facade args/kwargs invalid")
        if any(str(key).startswith("_") for key in kwargs):
            raise ValueError("private facade kwarg forbidden")
        params = self._params(method, args, kwargs)
        with self._lock:
            if method in QUERY_METHODS:
                return self._service.call(method, params)
            idempotency_key = str(params.get("idempotency_key") or request["request_id"])
            self._outbox.enqueue(idempotency_key=idempotency_key, method=method, params=params)
            result = self._service.call(method, params)
            self._outbox.complete(idempotency_key, result, succeeded=True)
            return result

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        from memorive_research_workspace.service import DESKTOP_METHODS
        if method in DESKTOP_METHODS:
            with self._lock:
                if not hasattr(self, '_memo'):
                    from memorive_research_workspace.desktop import create
                    self._memo=create(self)
            return self._memo.call(method,dict(params or {}),desktop=True)
        from memorive_research_runtime.runtime import METHODS as RESEARCH_METHODS
        if method in RESEARCH_METHODS:
            return self._research.call(method,dict(params or {}))
        if method=='messages.navigate' and str((params or {}).get('locator','')).startswith('memorive://job/research-'):
            locator=params['locator'];run=locator.split('/')[-1]
            if params.get('message_id') and self._messages.store.get(params['message_id']).target_locator!=locator:
                raise ValueError('RESEARCH_MESSAGE_TARGET_MISMATCH')
            self._research.call('research.get',{'run_id':run})
            return {'status':'PASS','research_task_id':run,'route':'current-task','job_id':run}
        if method in CURRENT_TASK_METHODS and hasattr(self,'_research'):
            from memorive_research_runtime.task_projection import bindings as research_bindings,view as research_view
            p=dict(params or {})
            if method=='current_task.select_task':
                self._research_selected_run=p.get('run') if str(p.get('run','')).startswith('research-') else None
            selected=self._research_selected_run
            if method in {'current_task.list','current_task.refresh'}:
                result=self._current_task.call(method,p)
                if isinstance(result,dict):
                    result['research_bindings']=research_bindings(self._research)
                return result
            if selected and method in {'current_task.select_task','current_task.select_node','current_task.view'}:
                return research_view(self._research,selected)
            if selected and method=='current_task.close_all':
                self._research_selected_run=None
                return {'status':'PASS'}
            if selected and method not in {'current_task.get_contract','current_task.effect_metrics'}:
                raise ValueError('文献发现任务请使用流程图中的取消、重新检索或查看结果按钮')
        if method == 'messages.navigate' and str((params or {}).get('locator','')).startswith('memorive://artifact/research-'):
            from memorive_desktop_service.locator import StableLocator
            locator=StableLocator.parse(params['locator'])
            if locator.object_id != 'result':raise ValueError('RESEARCH_ARTIFACT_INVALID')
            run_id=locator.job_id
            if params.get('message_id') and self._messages.store.get(params['message_id']).target_locator!=params['locator']:
                raise ValueError('RESEARCH_MESSAGE_TARGET_MISMATCH')
            result=self._research.call('research.get',{'run_id':run_id})
            if result['status'] not in {'SUCCEEDED','PARTIAL'}:raise ValueError('RESEARCH_RESULT_UNAVAILABLE')
            row = self._library._get(run_id + '/result')
            return {'status':'PASS','research_run_id':run_id,'route':'library','view':row['mode'],
                    'artifact_id':row['artifact_id'],'result_sha256':row['result_sha256'],'target_locator':row['stable_locator']}
        if not isinstance(method, str) or method not in SETTINGS_METHODS | ACCOUNTING_METHODS | INBOX_METHODS | CURRENT_TASK_METHODS | MESSAGES_METHODS | SESSIONS_METHODS | LOCAL_MODEL_METHODS | ASSISTANT_PREFERENCE_METHODS | LIBRARY_METHODS | WORK_LOG_METHODS:
            raise ValueError("product method is not allowlisted")
        accepted = deepcopy(dict(params or {}))
        if any(str(key).startswith("_") for key in accepted):
            raise ValueError("private product param forbidden")
        if method == "settings.leaderboard_get":
            # The leaderboard is a read-only, internally synchronized service
            # projection.  Do not queue it behind unrelated UI mutations or a
            # long-running task request guarded by the product-wide lock.
            return self._service.call(method, accepted)
        with self._product_call_lock:
            if method in CURRENT_TASK_METHODS:
                return self._current_task.call(method, accepted)
            if method in MESSAGES_METHODS:
                if method == "messages.notification_deliver":
                    if set(accepted) != {"message_id"}:
                        raise ValueError("MESSAGES_NOTIFICATION_PARAMS_INVALID")
                    message_id = accepted["message_id"]
                    if not isinstance(message_id, str) or not message_id:
                        raise ValueError("MESSAGES_NOTIFICATION_ID_INVALID")
                    message = self._messages.store.get(message_id)
                    if message.notification_state == "TOAST_SENT":
                        return {
                            "schema_version": "DesktopNativeNotificationDeliveryReceipt-v1",
                            "message_id": message_id,
                            "status": "DUPLICATE_SUPPRESSED",
                            "windows_presenter_called": False,
                            "in_app_message_preserved": True,
                        }
                    kind = {
                        "RED": "ERROR",
                        "YELLOW": "APPROVAL",
                        "GREEN": "COMPLETE",
                        "BLUE": "WEEKLY",
                    }[message.severity]
                    if str(message.target_locator or '').startswith('memorive://artifact/research-'):
                        run=str(message.target_locator).split('/')[3]
                        state=self._research.call('research.get',{'run_id':run})
                        if state['kind']=='discovery':kind='LITERATURE'
                    delivery = self._deliver_windows_notification(
                        kind=kind,
                        locator=message.target_locator,
                        source_message_id=message_id,
                    )
                    stored_state = {
                        "DELIVERED": "TOAST_SENT",
                        "BLOCKED_DISABLED": "BLOCKED_DISABLED",
                        "BLOCKED_QUIET": "BLOCKED_QUIET",
                        "BLOCKED_UNAVAILABLE": "BLOCKED_UNAVAILABLE",
                    }.get(delivery["status"], "NOT_ELIGIBLE")
                    self._messages.store.update_record(
                        message_id,
                        lambda raw: raw.update({"notification_state": stored_state}),
                    )
                    return {
                        **delivery,
                        "message_id": message_id,
                        "notification_state": stored_state,
                        "in_app_message_preserved": True,
                    }
                return self._messages.call(method, accepted)
            if method in SESSIONS_METHODS:
                return self._sessions.call(method, accepted)
            if method in LOCAL_MODEL_METHODS:
                return self._local_models.call(method, accepted)
            if method in ACCOUNTING_METHODS:
                return self._accounting.call(method, accepted)
            if method == "assistant.preference_get":
                if accepted:
                    raise ValueError("ASSISTANT_PREFERENCES_GET_PARAMS_INVALID")
                return self._assistant_preference_get()
            if method == "assistant.preference_save":
                return self._assistant_preference_save(accepted)
            if method in LIBRARY_METHODS:
                return self._library.call(method, accepted)
            if method in WORK_LOG_METHODS:
                return self._work_log.call(method, accepted)
            if method in {"settings.preview", "settings.save"}:
                settings = accepted.get("settings")
                if isinstance(settings, dict) and isinstance(settings.get("preferences"), dict):
                    settings["preferences"]["restore_last_document"] = False
            assistant_before: dict[str, Any] | None = None
            assistant_scope_receipt: dict[str, Any] | None = None
            reset_scope = accepted.get("scope") if method == "settings.reset_scope" else None
            if reset_scope in {"notice", "behavior", "appearance"}:
                settings_before = self._service.call("settings.get_state", {})
                if accepted.get("expected_revision") == settings_before.get("revision"):
                    assistant_before = self._load_assistant_preferences()
                    assistant_candidate = self._assistant_preferences_for_reset_scope(
                        reset_scope,
                        assistant_before,
                    )
                    try:
                        assistant_scope_receipt = self._assistant_preference_save(
                            self._assistant_preference_save_params(assistant_candidate)
                        )
                    except Exception:
                        self._write_json_file(
                            self._assistant_preferences_path,
                            assistant_before,
                        )
                        raise
            try:
                result = self._service.call(method, accepted)
            except Exception:
                if assistant_before is not None:
                    try:
                        self._assistant_preference_save(
                            self._assistant_preference_save_params(assistant_before)
                        )
                    except Exception as rollback_error:
                        self._write_json_file(
                            self._assistant_preferences_path,
                            assistant_before,
                        )
                        raise RuntimeError(
                            "SETTINGS_SCOPE_RESET_ASSISTANT_ROLLBACK_FAILED"
                        ) from rollback_error
                raise
            if method == "inbox.set_auto_run":
                self._sessions.sync_refinement_queue()
            if method in {
                "settings.save",
                "settings.save_cli_services",
                "settings.verify_cli_model",
                "settings.reset_scope",
                "settings.import_redacted",
                "settings.model_service_remove",
            } and not (
                isinstance(result, dict)
                and result.get("persistent_mutation") is False
            ):
                try:
                    workflow_snapshot = self._current_task.update_workflow_settings(
                        self._service.call("settings.get_state", {})
                    )
                    preferences = self._current_preferences()
                    runtime_receipt = self._apply_runtime_preferences(preferences)
                except Exception:
                    if method not in {"settings.save_cli_services", "settings.verify_cli_model"}:
                        raise
                    if isinstance(result, dict):
                        result = {
                            **result,
                            "settings_persisted": True,
                            "restart_required": False,
                            "runtime_effects_status": "WARNING",
                            "runtime_effect_error_code": "CLI_RUNTIME_REFRESH_DEFERRED",
                        }
                else:
                    if isinstance(result, dict):
                        enriched_result = {
                            **result,
                            "restart_required": False,
                            "runtime_effects": runtime_receipt,
                            "core_workflow_mapping": {
                                "settings_revision": workflow_snapshot["settings_revision"],
                                "settings_sha256": workflow_snapshot["settings_sha256"],
                                "snapshot_sha256": workflow_snapshot["snapshot_sha256"],
                                "configuration_ready": workflow_snapshot["configuration_ready"],
                                "external_route_activated": workflow_snapshot["external_route_activated"],
                            },
                        }
                        if method in {"settings.save_cli_services", "settings.verify_cli_model"}:
                            enriched_result["settings_persisted"] = True
                            enriched_result["runtime_effects_status"] = "PASS"
                        if method == "settings.reset_scope":
                            enriched_result["assistant_scope_reset"] = {
                                "scope": reset_scope,
                                "applied": assistant_scope_receipt is not None,
                                "status": "PASS",
                            }
                        result = enriched_result
            return result

    def attach_window(self, window: Any) -> None:
        if self._window is not None:
            raise RuntimeError("PRODUCT_WINDOW_ALREADY_ATTACHED")
        self._window = window
        self._start_core_event_pump()

    def attach_assistant_window(self, window: Any) -> None:
        if self._assistant_window is not None:
            raise RuntimeError("ASSISTANT_WINDOW_ALREADY_ATTACHED")
        self._assistant_window = window

    def attach_assistant_presenter(self, presenter: Any) -> None:
        if self._assistant_presenter is not None:
            raise RuntimeError("ASSISTANT_PRESENTER_ALREADY_ATTACHED")
        if not callable(presenter):
            raise ValueError("ASSISTANT_PRESENTER_INVALID")
        self._assistant_presenter = presenter

    def attach_notification_presenter(self, presenter: Any) -> None:
        if self._notification_presenter is not None:
            raise RuntimeError("NOTIFICATION_PRESENTER_ALREADY_ATTACHED")
        if not callable(presenter):
            raise ValueError("NOTIFICATION_PRESENTER_INVALID")
        self._notification_presenter = presenter

    def _deliver_windows_notification(
        self,
        *,
        kind: str,
        locator: str | None = None,
        source_message_id: str | None = None,
    ) -> dict[str, Any]:
        notification_preferences=dict(self._current_preferences())
        if kind=='LITERATURE':
            notification_preferences['external_literature_notification']=self._service.call('settings.research_get',{})['config']['push_enabled']
        plan = plan_windows_notification(
            notification_preferences,
            kind=kind,
            locator=locator,
            source_message_id=source_message_id,
        )
        self._notification_delivery_count += 1
        if plan["status"] != "DELIVER":
            receipt = {
                **plan,
                "schema_version": "DesktopNativeNotificationDeliveryReceipt-v1",
                "delivery_sequence": self._notification_delivery_count,
                "windows_presenter_called": False,
                "status": plan["status"],
            }
            self._last_notification_receipt = receipt
            return receipt
        if self._notification_presenter is None:
            receipt = {
                **plan,
                "schema_version": "DesktopNativeNotificationDeliveryReceipt-v1",
                "delivery_sequence": self._notification_delivery_count,
                "windows_presenter_called": False,
                "reason_code": "WINDOWS_PRESENTER_NOT_ATTACHED",
                "status": "BLOCKED_UNAVAILABLE",
            }
            self._last_notification_receipt = receipt
            return receipt
        presenter_result = self._notification_presenter(dict(plan))
        receipt = {
            **plan,
            "schema_version": "DesktopNativeNotificationDeliveryReceipt-v1",
            "delivery_sequence": self._notification_delivery_count,
            "windows_presenter_called": True,
            "presenter_result": presenter_result if isinstance(presenter_result, dict) else None,
            "status": "DELIVERED",
        }
        self._last_notification_receipt = receipt
        return receipt

    def test_windows_notification(
        self, request: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        accepted = dict(request or {})
        if set(accepted) - {"kind"}:
            raise ValueError("NOTIFICATION_TEST_FIELDS_INVALID")
        kind = str(accepted.get("kind", "COMPLETE")).upper()
        if kind not in NOTIFICATION_KINDS:
            raise ValueError("NOTIFICATION_KIND_INVALID")
        return {
            **self._deliver_windows_notification(kind=kind),
            "test_notification": True,
            "real_windows_presentation_requested": True,
        }

    def activate_notification(
        self, request: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        accepted = dict(request or {})
        if set(accepted) - {"source_message_id", "activation_locator"}:
            raise ValueError("NOTIFICATION_ACTIVATION_FIELDS_INVALID")
        message_id = accepted.get("source_message_id")
        locator = accepted.get("activation_locator")
        navigation: dict[str, Any] | None = None
        if message_id:
            navigation = self._messages.call(
                "messages.notification_activate", {"message_id": str(message_id)}
            )
        elif locator:
            navigation = self._messages.call(
                "messages.navigate", {"locator": str(locator)}
            )
        self._bring_main_window_to_foreground()
        route = "current-task" if locator and "/job/" in str(locator) else "messages"
        if self._window is not None:
            try:
                self._window.evaluate_js(
                    "window.__Desktop_INTEGRATED_APP__?.activate("
                    + json.dumps(route)
                    + ")"
                )
            except Exception:
                pass
        return {
            "schema_version": "DesktopNativeNotificationActivationReceipt-v1",
            "source_message_id": message_id,
            "route": route,
            "navigation": navigation,
            "task_or_object_mutation": False,
            "status": "PASS",
        }

    def assistant_initial_placement(self) -> dict[str, Any]:
        with self._lock:
            native = (
                getattr(self._assistant_window, "native", None)
                if self._assistant_window is not None
                else None
            )
            if native is not None:
                self._refresh_assistant_display_topology()
            placement_keys = (
                "assistant.monitor_id",
                "assistant.x",
                "assistant.y",
                "assistant.width",
                "assistant.height",
                "assistant.dpi_percent",
            )
            placement_filters = self._control_store.load()["state"]["filters"]
            placement_persisted = all(
                key in placement_filters for key in placement_keys
            )
            stored = self._assistant.load_placement(self._assistant_areas)
            area = next(
                (
                    row
                    for row in self._assistant_areas
                    if row.monitor_id == stored.monitor_id
                ),
                next(
                    (row for row in self._assistant_areas if row.primary),
                    self._assistant_areas[0],
                ),
            )
            x = stored.x
            y = stored.y
            if stored.width == 224 and stored.height == 244:
                # One-time migration from the assistant-placement transparent host. The
                # old persisted x/y referred to the 224x244 form; translate
                # them to its visible pet origin before removing that host.
                legacy_left, legacy_top, _, _ = self._assistant_visible_bounds(stored)
                x += legacy_left
                y += legacy_top
            candidate = self._assistant_placement_class(
                monitor_id=area.monitor_id,
                x=x,
                y=y,
                width=90,
                height=90,
                dpi_percent=int(area.dpi_percent),
            )
            accepted = self._assistant_clamp_placement(candidate, self._assistant_areas)
            if accepted != stored or not placement_persisted:
                self._assistant.persist_placement(accepted, self._assistant_areas)
            return accepted.to_dict()

    def assistant_bootstrap(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        accepted = dict(request or {})
        if set(accepted) - {"reduced_motion"}:
            raise ValueError("ASSISTANT_BOOTSTRAP_FIELDS_INVALID")
        reduced_motion = accepted.get("reduced_motion", False)
        if not isinstance(reduced_motion, bool):
            raise ValueError("ASSISTANT_REDUCED_MOTION_INVALID")
        with self._lock:
            self._assistant.set_control(reduced_motion=reduced_motion)
            health = self._service.call("service.health", {})
            inputs, real_job_count, sample_job_count, real_message_count = self._assistant_inputs()
            presentation = suppress_replayed_terminal_animation(
                self._assistant.refresh(service_health=health, jobs=inputs)
            )
            assistant_preferences = self._load_assistant_preferences()
            deliveries = getattr(self, '_assistant_deliveries', None)
            pending = deliveries.peek() if deliveries is not None else None
            if assistant_preferences['enabled'] and presentation.get('display_state') not in {'HIDDEN','DISABLED'}:
                if pending:
                    presentation = {**presentation, **pending}
                elif deliveries is not None and presentation.get('reason_code') in {'MESSAGE_UPDATE','MESSAGE_NEEDS_ATTENTION'}:
                    presentation = dict(presentation, animation_state='STATIC', display_state='IDLE',
                                        reason_code='MESSAGE_ALREADY_PRESENTED', attention_state='NONE')
            if presentation.get("reason_code") == "NO_ACTIVE_JOB":
                presentation = {
                    **presentation,
                    "safe_summary": "桌面助手待命中",
                    "reason_code": "NO_ACTIVE_REAL_JOB",
                }
            presenter = self._assistant_presenter
            window_attached = self._assistant_window is not None
            move_count = self._assistant_move_count
        # The native presenter writes its durable STARTED/COMPLETED receipt
        # through this ProductApi.  Never invoke that external callback while
        # holding ``self._lock`` or the receipt write will deadlock trying to
        # reacquire the same lock from the WinForms UI thread.
        native_presentation_status = "NOT_ATTACHED"
        if presenter is not None:
            try:
                presenter(dict(presentation))
                native_presentation_status = "APPLIED"
            except Exception as error:
                native_presentation_status = "ERROR:" + type(error).__name__
        return {
            "schema_version": "DesktopAssistantAssistantRuntimeProjection-v1",
            "presentation": presentation,
            "source_track": "REAL_LOCAL_SERVICE",
            "real_job_count": real_job_count,
            "sample_job_count": sample_job_count,
            "real_message_count": real_message_count,
            "projected_input_count": len(inputs),
            "reminder_scope": assistant_preferences["reminder_scope"],
            "hide_sensitive_names": assistant_preferences["hide_sensitive_names"],
            "sample_job_count_available_in_test_track": sample_job_count,
            "sample_job_consumed": False,
            "sample_success_used_as_real_success": False,
            "native_presentation_status": native_presentation_status,
            "window_attached": window_attached,
            "move_count": move_count,
            "external_network_calls": 0,
            "external_model_calls": 0,
            "credential_value_reads": 0,
            "shell_command_calls": 0,
            "system_autostart_writes": 0,
            "status": "READY",
        }

    def assistant_ui_signal(self, request: dict[str, Any]) -> dict[str, Any]:
        """Project a bounded local UI acknowledgement through the existing pet presenter."""

        allowed = {"kind", "event_id", "reduced_motion"}
        if (
            not isinstance(request, dict)
            or set(request) - allowed
            or not {"kind", "event_id"} <= set(request)
        ):
            raise ValueError("ASSISTANT_UI_SIGNAL_FIELDS_INVALID")
        kind = request["kind"]
        event_id = request["event_id"]
        reduced_motion = request.get("reduced_motion", False)
        if kind not in {"SAVE_SUCCESS", "ERROR_MESSAGE"}:
            raise ValueError("ASSISTANT_UI_SIGNAL_KIND_INVALID")
        if (
            not isinstance(event_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}", event_id) is None
        ):
            raise ValueError("ASSISTANT_UI_SIGNAL_EVENT_ID_INVALID")
        if not isinstance(reduced_motion, bool):
            raise ValueError("ASSISTANT_UI_SIGNAL_REDUCED_MOTION_INVALID")
        mapping = {
            "SAVE_SUCCESS": (
                "SUCCESS", "SUCCESS", "LOCAL_UI_SAVE_SUCCESS", "设置已保存"
            ),
            "ERROR_MESSAGE": (
                "ERROR", "ERROR", "LOCAL_UI_ERROR_MESSAGE", "收到需要检查的错误消息"
            ),
        }
        animation, attention, reason_code, safe_summary = mapping[kind]
        presentation = {
            "schema_version": "DesktopAssistantAssistantPresentation-v1",
            "display_state": "NOTIFYING",
            "attention_state": attention,
            "animation_state": animation,
            "safe_summary": safe_summary,
            "reason_code": reason_code,
            "locator": "memorive://ui/" + kind.lower() + "/" + event_id,
            "delivery_id": event_id,
            "reduced_motion": reduced_motion,
        }
        with self._lock:
            presenter = self._assistant_presenter
        status = "NOT_ATTACHED"
        if presenter is not None:
            try:
                native_receipt = presenter(dict(presentation))
                if isinstance(native_receipt, dict) and str(
                    native_receipt.get("durable_claim_status", "")
                ).startswith("BLOCKED:"):
                    status = "BLOCKED_NATIVE_DELIVERY"
                else:
                    status = "APPLIED"
            except Exception as error:
                status = "ERROR:" + type(error).__name__
        return {
            "schema_version": "DesktopAssistantUiSignalReceipt-v1",
            "kind": kind,
            "event_id": event_id,
            "animation_state": animation,
            "reason_code": reason_code,
            "native_presentation_status": status,
            "external_network_calls": 0,
            "external_model_calls": 0,
            "credential_value_reads": 0,
            "status": status,
        }

    def _bring_main_window_to_foreground(self) -> None:
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        self._window.show()
        native = getattr(self._window, "native", None)
        if native is None:
            raise RuntimeError("PRODUCT_NATIVE_WINDOW_NOT_READY")
        import clr
        clr.AddReference("System.Windows.Forms")
        from System import Action
        from System.Windows.Forms import FormWindowState

        def activate() -> None:
            if native.WindowState == FormWindowState.Minimized:
                native.WindowState = FormWindowState.Normal
            native.Show()
            native.BringToFront()
            native.Activate()

        native.Invoke(Action(activate))

    def _set_assistant_window_visibility(self, visible: bool) -> dict[str, Any]:
        if self._assistant_window is None:
            raise RuntimeError("ASSISTANT_WINDOW_NOT_ATTACHED")
        native = getattr(self._assistant_window, "native", None)
        if native is None:
            if visible:
                self._assistant_window.show()
            else:
                self._assistant_window.hide()
            return {
                "schema_version": "DesktopAssistantAssistantNativeVisibilityReceipt-v1",
                "visible": visible,
                "native_window_ready": False,
                "brought_to_front": visible,
                "status": "PASS",
            }

        import clr
        clr.AddReference("System.Windows.Forms")
        from System import Action

        observed: dict[str, Any] = {}

        def apply_visibility() -> None:
            if visible:
                native.Show()
                native.TopMost = True
                native.BringToFront()
            else:
                native.Hide()
            observed["visible"] = bool(native.Visible)

        native.Invoke(Action(apply_visibility))
        if observed.get("visible") is not visible:
            raise RuntimeError("ASSISTANT_NATIVE_VISIBILITY_MISMATCH")
        return {
            "schema_version": "DesktopAssistantAssistantNativeVisibilityReceipt-v1",
            "visible": observed["visible"],
            "native_window_ready": True,
            "brought_to_front": visible,
            "status": "PASS",
        }

    def _assistant_window_is_visible(self) -> bool:
        if self._assistant_window is None:
            return bool(self._assistant.visible)
        native = getattr(self._assistant_window, "native", None)
        if native is None:
            return bool(self._assistant.visible)
        import clr
        clr.AddReference("System.Windows.Forms")
        from System import Action

        observed: dict[str, bool] = {}

        def inspect_visibility() -> None:
            observed["visible"] = bool(native.Visible)

        native.Invoke(Action(inspect_visibility))
        return observed["visible"]

    def _activate_main_route(self, route: str) -> None:
        if route not in {"home", "settings", "messages"}:
            raise ValueError("ASSISTANT_MAIN_ROUTE_INVALID")
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        self._window.evaluate_js(
            "window.__Desktop_INTEGRATED_APP__.activate(" + json.dumps(route) + ")"
        )
        self._bring_main_window_to_foreground()

    def _focus_main_window(self) -> dict[str, Any]:
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        # Restoring the native window is the primary action.  Route inspection
        # is diagnostic only and must never keep a hidden main window hidden
        # when WebView2 is suspended, unhealthy, or still resuming.
        self._bring_main_window_to_foreground()
        route_projection: Any = None
        route_observation_status = "UNAVAILABLE"
        route_observation_error_type: str | None = None
        try:
            route_projection = self._window.evaluate_js(
                "window.__Desktop_INTEGRATED_APP__ && window.__Desktop_INTEGRATED_APP__.inspect()"
            )
            if isinstance(route_projection, dict) and isinstance(
                route_projection.get("active_route"), str
            ):
                route_observation_status = "OBSERVED"
        except Exception as error:
            route_observation_error_type = type(error).__name__
        route = (
            route_projection.get("active_route")
            if isinstance(route_projection, dict)
            else None
        )
        return {
            "schema_version": "DesktopAssistantMainWindowFocusReceipt-v1",
            "route_before": route,
            "route_after": route,
            "route_preserved": True,
            "route_observation_status": route_observation_status,
            "route_observation_error_type": route_observation_error_type,
            "navigation_invoked": False,
            "window_shown": True,
            "window_focused": True,
            "status": "PASS",
        }

    def _move_assistant_window_native(
        self,
        x: int,
        y: int,
        *,
        native_width: int | None = None,
        native_height: int | None = None,
    ) -> dict[str, Any]:
        if self._assistant_window is None:
            raise RuntimeError("ASSISTANT_WINDOW_NOT_ATTACHED")
        native = getattr(self._assistant_window, "native", None)
        if native is None:
            self._assistant_window.move(x, y)
            return {
                "native_window_ready": False,
                "x": x,
                "y": y,
                "status": "PASS",
            }

        import clr
        clr.AddReference("System.Windows.Forms")
        from System import Action

        if (native_width is None) is not (native_height is None):
            raise ValueError("ASSISTANT_NATIVE_SIZE_INCOMPLETE")
        observed: dict[str, int] = {}

        def apply_move() -> None:
            if native_width is not None and native_height is not None:
                native.SetBounds(x, y, int(native_width), int(native_height))
            else:
                native.Left = x
                native.Top = y
            native.TopMost = True
            native.BringToFront()
            observed["x"] = int(native.Left)
            observed["y"] = int(native.Top)
            observed["width"] = int(native.Width)
            observed["height"] = int(native.Height)

        native.Invoke(Action(apply_move))
        if observed["x"] != x or observed["y"] != y:
            raise RuntimeError("ASSISTANT_NATIVE_MOVE_MISMATCH")
        if (
            native_width is not None
            and native_height is not None
            and (
                observed["width"] != native_width
                or observed["height"] != native_height
            )
        ):
            raise RuntimeError("ASSISTANT_NATIVE_SIZE_MISMATCH")
        return {
            "native_window_ready": True,
            "x": observed["x"],
            "y": observed["y"],
            "width": observed["width"],
            "height": observed["height"],
            "status": "PASS",
        }

    def assistant_move_window(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {"x", "y", "persist"}:
            raise ValueError("ASSISTANT_MOVE_FIELDS_INVALID")
        x = request["x"]
        y = request["y"]
        persist = request["persist"]
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (x, y)):
            raise ValueError("ASSISTANT_MOVE_COORDINATE_INVALID")
        if not isinstance(persist, bool):
            raise ValueError("ASSISTANT_MOVE_PERSIST_INVALID")
        if self._assistant_window is None:
            raise RuntimeError("ASSISTANT_WINDOW_NOT_ATTACHED")
        with self._lock:
            current = self.assistant_initial_placement()
            candidate = self._assistant_placement_class(
                monitor_id=str(current["monitor_id"]),
                x=x,
                y=y,
                width=90,
                height=90,
                dpi_percent=int(current["dpi_percent"]),
            )
            accepted = self._assistant_clamp_placement(candidate, self._assistant_areas)
            target_area = next(
                (
                    area
                    for area in self._assistant_areas
                    if area.monitor_id == accepted.monitor_id
                ),
                None,
            )
            if target_area is not None and accepted.dpi_percent != target_area.dpi_percent:
                accepted = self._assistant_clamp_placement(
                    self._assistant_placement_class(
                        monitor_id=accepted.monitor_id,
                        x=accepted.x,
                        y=accepted.y,
                        width=90,
                        height=90,
                        dpi_percent=int(target_area.dpi_percent),
                    ),
                    self._assistant_areas,
                )
            if persist:
                self._assistant.persist_placement(accepted, self._assistant_areas)
            visible_left, visible_top, visible_width, visible_height = self._assistant_visible_bounds(accepted)
            native_move = self._move_assistant_window_native(
                accepted.x,
                accepted.y,
                native_width=visible_width,
                native_height=visible_height,
            )
            self._assistant_move_count += 1
            return {
                "schema_version": "DesktopAssistantAssistantWindowMoveReceipt-v1",
                "placement": accepted.to_dict(),
                "persisted": persist,
                "native_move": native_move,
                "visible_bounds": {
                    "left": accepted.x + visible_left,
                    "top": accepted.y + visible_top,
                    "width": visible_width,
                    "height": visible_height,
                    "right": accepted.x + visible_left + visible_width,
                    "bottom": accepted.y + visible_top + visible_height,
                },
                "move_count": self._assistant_move_count,
                "status": "PASS",
            }

    @staticmethod
    def _assistant_target_descriptor(target: str) -> dict[str, Any]:
        if not isinstance(target, str):
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
        normalized = target.strip()
        if (
            not normalized
            or len(normalized) > 2048
            or any(character in normalized for character in ("\x00", "\r", "\n"))
        ):
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
        if normalized.startswith('"') and normalized.endswith('"') and normalized.count('"') == 2:
            normalized = normalized[1:-1].strip()
        if not normalized or '"' in normalized:
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")

        windows_path = bool(re.match(r"^[A-Za-z]:[\\/]", normalized)) or normalized.startswith("\\\\")
        parsed = urlsplit(normalized)
        if not windows_path and parsed.scheme:
            if (
                parsed.scheme.lower() not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
            try:
                parsed.port
            except ValueError as error:
                raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE") from error
            return {"kind": "URL", "target": normalized, "path": None}

        if re.search(
            r"(?i)\.(?:exe|com|bat|cmd|ps1|vbs|js|jse|wsf|wsh|hta|msi|reg|scr|lnk|url)['\"]?\s+",
            normalized,
        ):
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
        if any(character in normalized for character in ("<", ">", "|", "?", "*")):
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
        if len(normalized) > 2 and ":" in normalized[2:]:
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
        path = Path(normalized)
        if not path.is_absolute():
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
        resolved = path.resolve(strict=False)
        unsafe_suffixes = {
            ".bat", ".cmd", ".com", ".hta", ".js", ".jse", ".lnk", ".msi",
            ".ps1", ".reg", ".scr", ".url", ".vbs", ".wsf", ".wsh",
        }
        if resolved.suffix.lower() in unsafe_suffixes:
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
        if resolved.exists() and resolved.is_dir():
            kind = "DIRECTORY"
        elif resolved.suffix.lower() == ".exe":
            kind = "PROGRAM"
        else:
            kind = "FILE"
        return {"kind": kind, "target": str(resolved), "path": resolved}

    @classmethod
    def _normalize_assistant_shortcut_entries(cls, entries: object) -> list[dict[str, str]]:
        if not isinstance(entries, list) or len(entries) != 3:
            raise ValueError("ASSISTANT_SHORTCUT_ENTRIES_INVALID")
        normalized_entries: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"name", "target"}:
                raise ValueError("ASSISTANT_SHORTCUT_ENTRY_INVALID")
            name = entry.get("name")
            target = entry.get("target")
            if not isinstance(name, str) or not isinstance(target, str):
                raise ValueError("ASSISTANT_SHORTCUT_ENTRY_INVALID")
            name = name.strip()
            target = target.strip()
            if (
                len(name) > 40
                or any(character in name for character in ("\x00", "\r", "\n"))
                or bool(name) != bool(target)
            ):
                raise ValueError("ASSISTANT_SHORTCUT_ENTRY_INVALID")
            if target:
                target = str(cls._assistant_target_descriptor(target)["target"])
            normalized_entries.append({"name": name, "target": target})
        return normalized_entries

    def _load_assistant_preferences(self) -> dict[str, Any]:
        try:
            value = json.loads(self._assistant_preferences_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("ASSISTANT_PREFERENCES_UNAVAILABLE") from error
        expected = {
            "schema_version",
            "enabled",
            "shortcut_entries",
            "reminder_scope",
            "hide_sensitive_names",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise RuntimeError("ASSISTANT_PREFERENCES_FIELDS_INVALID")
        if (
            value.get("schema_version") != "DesktopAssistantPreferences-v3"
            or not isinstance(value.get("enabled"), bool)
            or not isinstance(value.get("hide_sensitive_names"), bool)
        ):
            raise RuntimeError("ASSISTANT_PREFERENCES_INVALID")
        try:
            value["shortcut_entries"] = self._normalize_assistant_shortcut_entries(
                value.get("shortcut_entries")
            )
        except ValueError as error:
            raise RuntimeError("ASSISTANT_SHORTCUT_ENTRIES_INVALID") from error
        if value.get("reminder_scope") not in {
            "IMPORTANT_ONLY",
            "IMPORTANT_AND_COMPLETE",
            "INCLUDE_ORDINARY",
        }:
            raise RuntimeError("ASSISTANT_REMINDER_SCOPE_INVALID")
        return value

    @classmethod
    def _assistant_preferences_for_reset_scope(
        cls,
        scope: str,
        current: Mapping[str, Any],
        *,
        default_shortcuts: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        candidate = deepcopy(dict(current))
        if scope == "notice":
            candidate["reminder_scope"] = "IMPORTANT_AND_COMPLETE"
            candidate["hide_sensitive_names"] = False
        elif scope == "behavior":
            candidate["enabled"] = True
            candidate["shortcut_entries"] = deepcopy(
                default_shortcuts
                if default_shortcuts is not None
                else cls._default_assistant_shortcut_entries()
            )
        elif scope == "appearance":
            candidate["enabled"] = True
        else:
            raise ValueError(f"ASSISTANT_RESET_SCOPE_INVALID:{scope}")
        return candidate

    @staticmethod
    def _assistant_preference_save_params(
        preferences: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "enabled": preferences["enabled"],
            "shortcut_entries": deepcopy(preferences["shortcut_entries"]),
            "reminder_scope": preferences["reminder_scope"],
            "hide_sensitive_names": preferences["hide_sensitive_names"],
        }

    def _assistant_preference_get(self) -> dict[str, Any]:
        return {
            "schema_version": "DesktopAssistantPreferencesProjection-v3",
            "preferences": self._load_assistant_preferences(),
            "assistant_visible": self._assistant_window_is_visible(),
            "status": "PASS",
        }

    def _assistant_preference_save(self, params: dict[str, Any]) -> dict[str, Any]:
        if set(params) != {
            "enabled",
            "shortcut_entries",
            "reminder_scope",
            "hide_sensitive_names",
        }:
            raise ValueError("ASSISTANT_PREFERENCES_SAVE_PARAMS_INVALID")
        candidate = {
            "schema_version": "DesktopAssistantPreferences-v3",
            "enabled": params["enabled"],
            "shortcut_entries": self._normalize_assistant_shortcut_entries(
                params["shortcut_entries"]
            ),
            "reminder_scope": params["reminder_scope"],
            "hide_sensitive_names": params["hide_sensitive_names"],
        }
        if not isinstance(candidate["enabled"], bool) or not isinstance(
            candidate["hide_sensitive_names"], bool
        ):
            raise ValueError("ASSISTANT_ENABLED_INVALID")
        if candidate["reminder_scope"] not in {
            "IMPORTANT_ONLY",
            "IMPORTANT_AND_COMPLETE",
            "INCLUDE_ORDINARY",
        }:
            raise ValueError("ASSISTANT_REMINDER_SCOPE_INVALID")
        try:
            current_visible = self._assistant_window_is_visible()
        except (AttributeError, RuntimeError):
            current_visible = bool(
                getattr(self._assistant, "visible", candidate["enabled"])
            )
        target_visible = assistant_visibility_after_preference_save(
            current_visible=current_visible,
            enabled=candidate["enabled"],
        )
        self._write_json_file(self._assistant_preferences_path, candidate)
        self._assistant.set_control(
            enabled=candidate["enabled"],
            visible=target_visible,
        )
        if target_visible != current_visible:
            visibility = self._set_assistant_window_visibility(target_visible)
        else:
            visibility = {
                "schema_version": "DesktopAssistantAssistantNativeVisibilityReceipt-v1",
                "visible": target_visible,
                "native_window_ready": self._assistant_window is not None
                if hasattr(self, "_assistant_window")
                else False,
                "brought_to_front": False,
                "preserved": True,
                "status": "PASS",
            }
        return {
            "schema_version": "DesktopAssistantPreferencesSaveReceipt-v3",
            "preferences": candidate,
            "assistant_window_visibility": visibility,
            "status": "PASS",
        }

    @staticmethod
    def _legacy_assistant_target_path(target_id: str) -> Path | None:
        executable_names = {
            "CODEX": ("Codex.exe", "codex.exe"),
            "CLAUDE_CODE": ("claude.exe",),
            "CC_SWITCH": ("CC-Switch.exe", "cc-switch.exe", "CCSwitch.exe"),
        }
        if target_id not in executable_names:
            return None
        for name in executable_names[target_id]:
            resolved = shutil.which(name)
            if resolved and Path(resolved).is_file():
                return Path(resolved).resolve()
        local_app_data = os.environ.get("LOCALAPPDATA")
        program_files = [
            value
            for value in (
                os.environ.get("ProgramFiles"),
                os.environ.get("ProgramFiles(x86)"),
            )
            if value
        ]
        candidates: dict[str, list[Path]] = {
            "CODEX": [],
            "CLAUDE_CODE": [],
            "CC_SWITCH": [],
        }
        if local_app_data:
            local_root = Path(local_app_data)
            candidates["CODEX"].extend(
                (
                    local_root / "Programs" / "Codex" / "Codex.exe",
                    local_root / "Programs" / "OpenAI" / "Codex" / "Codex.exe",
                )
            )
            candidates["CLAUDE_CODE"].append(
                local_root / "Programs" / "Claude Code" / "claude.exe"
            )
            candidates["CC_SWITCH"].extend(
                (
                    local_root / "Programs" / "CC-Switch" / "CC-Switch.exe",
                    local_root / "Programs" / "CC Switch" / "CC Switch.exe",
                )
            )
        for root_value in program_files:
            root = Path(root_value)
            candidates["CODEX"].append(root / "Codex" / "Codex.exe")
            candidates["CLAUDE_CODE"].append(root / "Claude Code" / "claude.exe")
            candidates["CC_SWITCH"].append(root / "CC-Switch" / "CC-Switch.exe")
        return next(
            (candidate.resolve() for candidate in candidates[target_id] if candidate.is_file()),
            None,
        )

    def assistant_target_status(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {"target"}:
            raise ValueError("ASSISTANT_TARGET_STATUS_REQUEST_INVALID")
        descriptor = self._assistant_target_descriptor(request["target"])
        path = descriptor["path"]
        available = descriptor["kind"] == "URL" or (
            isinstance(path, Path)
            and path.exists()
            and (
                (descriptor["kind"] == "DIRECTORY" and path.is_dir())
                or (descriptor["kind"] != "DIRECTORY" and path.is_file())
            )
        )
        return {
            "schema_version": "DesktopAssistantTargetStatus-v2",
            "target_kind": descriptor["kind"],
            "target_sha256": hashlib.sha256(descriptor["target"].encode("utf-8")).hexdigest().upper(),
            "available": available,
            "validation_state": "VALID" if available else "MISSING",
            "target_path_exposed": False,
            "status": "PASS",
        }

    def assistant_launch_target(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {"target"}:
            raise ValueError("ASSISTANT_TARGET_LAUNCH_REQUEST_INVALID")
        descriptor = self._assistant_target_descriptor(request["target"])
        path = descriptor["path"]
        process_id: int | None = None
        if descriptor["kind"] == "URL":
            if not webbrowser.open(descriptor["target"], new=2):
                raise FileNotFoundError("ASSISTANT_SHORTCUT_URL_OPEN_FAILED")
            launch_method = "DEFAULT_BROWSER"
        elif descriptor["kind"] == "PROGRAM":
            if not isinstance(path, Path) or not path.is_file():
                raise FileNotFoundError("ASSISTANT_SHORTCUT_TARGET_MISSING")
            process = subprocess.Popen(
                [str(path)],
                shell=False,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            process_id = int(process.pid)
            launch_method = "DIRECT_PROCESS"
        else:
            if not isinstance(path, Path) or not path.exists():
                raise FileNotFoundError("ASSISTANT_SHORTCUT_TARGET_MISSING")
            startfile = getattr(os, "startfile", None)
            if not callable(startfile):
                raise RuntimeError("ASSISTANT_SHORTCUT_OPEN_UNAVAILABLE")
            startfile(str(path))
            launch_method = "WINDOWS_ASSOCIATION"
        return {
            "schema_version": "DesktopAssistantTargetLaunchReceipt-v2",
            "target_kind": descriptor["kind"],
            "target_sha256": hashlib.sha256(descriptor["target"].encode("utf-8")).hexdigest().upper(),
            "process_id": process_id,
            "launch_method": launch_method,
            "shell_command_calls": 0,
            "freeform_argument_count": 0,
            "status": "LAUNCHED",
        }

    def assistant_shortcut(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("ASSISTANT_SHORTCUT_REQUEST_INVALID")
        main_window_focus = None
        assistant_window_visibility = None
        visibility_request: bool | None = None
        restore_native_move: tuple[int, int, int, int] | None = None
        with self._lock:
            receipt = self._assistant.dispatch_shortcut(request)
            action_id = receipt["action_id"]
            if action_id == "SHOW_DESKTOP_ASSISTANT":
                preferences = self._load_assistant_preferences()
                if preferences["enabled"]:
                    self._assistant.set_control(visible=True)
                    visibility_request = True
            elif action_id == "HIDE_DESKTOP_ASSISTANT":
                self._assistant.set_control(visible=False)
                visibility_request = False
            elif action_id == "RESTORE_DEFAULT_PLACEMENT":
                self._refresh_assistant_display_topology()
                restored = self._assistant.restore_default(self._assistant_areas)
                placement = restored["placement"]
                accepted = self._assistant_placement_class(
                    monitor_id=str(placement["monitor_id"]),
                    x=int(placement["x"]),
                    y=int(placement["y"]),
                    width=int(placement["width"]),
                    height=int(placement["height"]),
                    dpi_percent=int(placement["dpi_percent"]),
                )
                if self._assistant_window is None:
                    raise RuntimeError("ASSISTANT_WINDOW_NOT_ATTACHED")
                _, _, visible_width, visible_height = self._assistant_visible_bounds(accepted)
                restore_native_move = (
                    accepted.x,
                    accepted.y,
                    visible_width,
                    visible_height,
                )

        # pywebview/WinForms presenters can synchronously call back into this
        # ProductApi to persist receipts.  Native window work must therefore
        # happen after releasing ``self._lock``; otherwise the UI thread and
        # the API worker can wait on one another indefinitely.
        if action_id == "TOGGLE_DESKTOP_ASSISTANT":
            target_visible = not self._assistant_window_is_visible()
            with self._lock:
                self._assistant.set_control(visible=target_visible)
            visibility_request = target_visible
        if visibility_request is not None:
            assistant_window_visibility = self._set_assistant_window_visibility(
                visibility_request
            )
        elif action_id == "OPEN_Desktop_MAIN":
            main_window_focus = self._focus_main_window()
        elif action_id == "OPEN_DESKTOP_ASSISTANT_SETTINGS":
            self._activate_main_route("settings")
            self._window.evaluate_js(
                "window.DesktopPreview_settings?.root?.querySelector('[data-category=\"behavior\"]')?.click()"
            )
        elif action_id == "OPEN_MESSAGE_DETAIL":
            self._activate_main_route("messages")
        elif restore_native_move is not None:
            move_x, move_y, visible_width, visible_height = restore_native_move
            self._move_assistant_window_native(
                move_x,
                move_y,
                native_width=visible_width,
                native_height=visible_height,
            )

        with self._lock:
            assistant_visible = bool(self._assistant.visible)
            assistant_window = self._assistant_window
        presentation_refresh_status = "READY"
        if not assistant_visible:
            # Hiding is a complete native action.  It must not be converted
            # into a failure merely because the background service is down.
            projection = _local_assistant_projection(
                visible=False,
                refresh_unavailable=False,
            )
            presentation_refresh_status = "LOCAL_ONLY"
        else:
            try:
                projection = self.assistant_bootstrap({})
            except Exception:
                # Showing/moving the already-created native window does not
                # depend on Application Service.  Keep the successful local
                # action and use a safe static projection until the event pump
                # reconnects; never leak an internal IPC code to the product.
                projection = _local_assistant_projection(
                    visible=True,
                    refresh_unavailable=True,
                )
                presentation_refresh_status = "DEGRADED"
        if (
            action_id in {"SHOW_DESKTOP_ASSISTANT", "TOGGLE_DESKTOP_ASSISTANT"}
            and assistant_visible
            and assistant_window is not None
            and hasattr(assistant_window, "evaluate_js")
        ):
            assistant_window.evaluate_js(
                "window.__Desktop_ASSISTANT_RUNTIME__?.applyPresentation("
                + json.dumps(
                    projection["presentation"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + ")"
            )
        return {
            **receipt,
            "main_window_focus": main_window_focus,
            "assistant_window_visibility": assistant_window_visibility,
            "presentation": projection,
            "presentation_refresh_status": presentation_refresh_status,
            "sample_job_consumed": False,
            "sample_success_used_as_real_success": False,
        }

    def _effective_directory(self, role: str) -> Path:
        if role not in {"workspace_root", "artifact_root", "external_library"}:
            raise ValueError("DIRECTORY_ROLE_INVALID")
        state = self._service.call("settings.get_state", {})
        settings = state.get("settings") if isinstance(state, dict) else None
        directories = settings.get("directories") if isinstance(settings, dict) else None
        value: Any = None
        if isinstance(directories, dict):
            if role == "external_library":
                external = directories.get("external_library")
                if isinstance(external, dict) and external.get("enabled"):
                    value = external.get("root")
            else:
                value = directories.get(role)
        if isinstance(value, str) and value:
            raw = Path(value)
            try:
                raw_io = windows_io_path(raw)
                if (
                    raw.is_absolute()
                    and raw_io.exists()
                    and raw_io.is_dir()
                    and not raw_io.is_symlink()
                    and not self._is_reparse_point(raw_io)
                ):
                    return raw.resolve(strict=False)
            except OSError:
                pass
        fallback = self._profile_path(
            "ARTIFACTS" if role == "artifact_root" else "WORKSPACE"
        )
        windows_io_path(fallback).mkdir(parents=True, exist_ok=True)
        return fallback.resolve(strict=False)

    def pick_inbox_files(self) -> dict[str, Any]:
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        from webview import FileDialog
        selected = self._window.create_file_dialog(
            FileDialog.OPEN,
            directory=str(self._effective_directory("workspace_root")),
            allow_multiple=True,
            file_types=(
                "Supported files (*.txt;*.md;*.json;*.pdf;*.docx;*.xlsx;*.pptx;*.png;*.jpg;*.jpeg;*.tif;*.tiff)",
                "All files (*.*)",
            ),
        )
        if not selected:
            return {
                "schema_version": "DesktopAssistantInboxPickerReceipt-v2",
                "paths": [],
                "path_count": 0,
                "source_allowlist_enforced_by_service": True,
                "status": "CANCELLED",
            }
        return self.stage_inbox_paths([str(path) for path in selected])

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))

    def stage_inbox_paths(self, paths: list[str]) -> dict[str, Any]:
        if not isinstance(paths, list) or not 1 <= len(paths) <= 32:
            raise ValueError("INBOX_STAGE_PATH_COUNT_INVALID")
        accepted: list[tuple[Path, Path]] = []
        for value in paths:
            if not isinstance(value, str) or not value:
                raise ValueError("INBOX_STAGE_PATH_INVALID")
            candidate = Path(value).resolve(strict=False)
            candidate_io = windows_io_path(candidate)
            if (
                not candidate_io.is_file()
                or candidate_io.is_symlink()
                or self._is_reparse_point(candidate_io)
            ):
                raise ValueError("INBOX_STAGE_SOURCE_NOT_REGULAR_FILE")
            size = candidate_io.stat().st_size
            if size <= 0 or size > 64 * 1024 * 1024:
                raise ValueError("INBOX_STAGE_SOURCE_SIZE_INVALID")
            accepted.append((candidate, candidate_io))
        batch_id = "stage_" + uuid.uuid4().hex
        batch_root = (self._inbox_source_root / batch_id).resolve(strict=False)
        if not batch_root.is_relative_to(self._inbox_source_root.resolve(strict=False)):
            raise RuntimeError("INBOX_STAGE_ROOT_ESCAPE")
        windows_io_path(batch_root).mkdir(parents=False, exist_ok=False)
        staged: list[str] = []
        hashes: list[str] = []
        used_names: set[str] = set()
        for index, (source, source_io) in enumerate(accepted):
            target_name = source.name
            if target_name.casefold() in used_names:
                target_name = f"{source.stem}_{index + 1}{source.suffix}"
            used_names.add(target_name.casefold())
            target = batch_root / target_name
            target_io = windows_io_path(target)
            shutil.copy2(source_io, target_io, follow_symlinks=False)
            staged.append(str(target))
            hashes.append(hashlib.sha256(target_io.read_bytes()).hexdigest().upper())
        return {
            "schema_version": "DesktopAssistantInboxPickerReceipt-v2",
            "stage_id": batch_id,
            "paths": staged,
            "path_count": len(staged),
            "sha256s": hashes,
            "source_files_moved_or_deleted": 0,
            "source_allowlist_enforced_by_service": True,
            "original_names_preserved": True,
            "status": "SELECTED",
        }

    def _inbox_item_path(self, item_id: str) -> tuple[dict[str, Any], Path]:
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("INBOX_ITEM_ID_INVALID")
        detail = self._service.call("inbox.item_detail", {"item_id": item_id})
        locator = detail.get("bound_locator")
        if not isinstance(locator, str) or not locator or ".." in Path(locator).parts:
            raise ValueError("INBOX_ITEM_NOT_BOUND")
        path = (self._inbox_root / locator).resolve(strict=False)
        if not path.is_relative_to(self._inbox_root.resolve(strict=False)):
            raise ValueError("INBOX_ITEM_PATH_ESCAPE")
        path_io = windows_io_path(path)
        if not path_io.is_file() or path_io.is_symlink() or self._is_reparse_point(path_io):
            raise ValueError("INBOX_ITEM_FILE_INVALID")
        observed = hashlib.sha256(path_io.read_bytes()).hexdigest().upper()
        if observed != detail.get("content_sha256"):
            raise ValueError("INBOX_ITEM_HASH_MISMATCH")
        return detail, path

    def get_inbox_preview(self, item_id: str) -> dict[str, Any]:
        detail, path = self._inbox_item_path(item_id)
        extension = str(detail.get("extension") or "").lower()
        common = {
            "schema_version": "DesktopAssistantInboxPreviewProjection-v1",
            "item": detail,
            "content_sha256": detail["content_sha256"],
            "sample": bool(detail.get("sample")),
            "read_only": True,
            "status": "PASS",
        }
        if extension in {".docx", ".pptx", ".xlsx"}:
            return {**common, "mode": "PROPERTY_ONLY", "mime_type": None}
        if extension in {".txt", ".md", ".json"}:
            maximum = 256 * 1024
            raw = windows_io_path(path).read_bytes()
            decoded = raw[:maximum].decode("utf-8-sig", errors="replace")
            return {
                **common,
                "mode": "TEXT",
                "mime_type": "application/json" if extension == ".json" else "text/plain",
                "text": decoded,
                "truncated": len(raw) > maximum,
            }
        if extension == ".pdf":
            raw = windows_io_path(path).read_bytes()
            return {
                **common,
                "mode": "PDF",
                "mime_type": "application/pdf",
                "data_base64": base64.b64encode(raw).decode("ascii"),
            }
        if extension in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            raw = windows_io_path(path).read_bytes()
            return {
                **common,
                "mode": "IMAGE",
                "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "data_base64": base64.b64encode(raw).decode("ascii"),
            }
        return {**common, "mode": "UNSUPPORTED", "mime_type": None}

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
            assistant_window = self._assistant_window

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

    def test_network_connection(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        accepted = dict(request or {})
        if set(accepted) - {"proxy_mode", "proxy_address", "timeout_seconds"}:
            raise ValueError("NETWORK_TEST_FIELDS_INVALID")
        preferences = self._current_preferences()
        proxy_mode = accepted.get("proxy_mode", preferences["proxy_mode"])
        proxy_address = accepted.get("proxy_address", preferences["proxy_address"])
        timeout_seconds = accepted.get("timeout_seconds", preferences["request_timeout_seconds"])
        self._network_test_count += 1
        try:
            receipt = self._network_runner(
                proxy_mode=proxy_mode,
                proxy_address=proxy_address,
                timeout_seconds=timeout_seconds,
            )
            return {
                **receipt,
                "test_sequence": self._network_test_count,
                "settings_revision_source": self._service.call("settings.get_state", {})["revision"],
            }
        except Exception as error:
            return {
                "schema_version": "DesktopNetworkDiagnosticReceipt-v1",
                "status": "FAIL",
                "reason_code": str(error)[:160] or type(error).__name__,
                "local_ip": None,
                "local_addresses": [],
                "egress_ip": None,
                "country_code": "",
                "colo": "",
                "proxy_mode": str(proxy_mode).upper(),
                "test_sequence": self._network_test_count,
                "external_network_calls": 1,
                "provider_calls": 0,
                "external_model_calls": 0,
                "credential_value_reads": 0,
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

    def locate_inbox_item(self, item_id: str) -> dict[str, Any]:
        detail, path = self._inbox_item_path(item_id)
        process = subprocess.Popen(
            ["explorer.exe", "/select,", str(path)],
            shell=False,
            close_fds=True,
        )
        return {
            "schema_version": "DesktopAssistantInboxLocateReceipt-v1",
            "item_id": detail["item_id"],
            "process_id": process.pid,
            "selected_controlled_copy": True,
            "status": "OPENED",
        }

    def open_inbox_item(self, item_id: str) -> dict[str, Any]:
        detail, path = self._inbox_item_path(item_id)
        os.startfile(path, "open")
        return {
            "schema_version": "DesktopAssistantInboxExternalOpenReceipt-v1",
            "item_id": detail["item_id"],
            "opened_controlled_copy": True,
            "status": "OPENED",
        }

    def memo_pick_vault(self):
        if self._window is None: return {'path':None}
        import webview
        value=self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return {'path':str(value[0]) if value else None}

    def memo_pick_materials(self):
        if self._window is None:return {'paths':[]}
        import webview
        value=self._window.create_file_dialog(webview.FileDialog.OPEN,allow_multiple=True,
            file_types=('Research materials (*.pdf;*.md;*.txt;*.ris;*.bib;*.bibtex;*.xml)',))
        return {'paths':[str(p) for p in value] if value else []}

    def memo_open_evidence(self, request):
        self.call('memo.capabilities',{})
        ref=self._memo.index.read(request['evidence_id'],request['project'],expected_hash=request.get('expected_hash'))
        os.startfile(ref['path'])
        return {'status':'OPENED','artifact_id':ref['artifact_id']}

    def memo_open_handoff(self, request):
        self.call('memo.capabilities',{})
        value=self._memo.export_handoff(request['handoff_id'])
        os.startfile(value['path'])
        return {'status':'OPENED'}


    def memo_desktop_apps(self):
        import subprocess,json,time
        cached=getattr(self,'_memo_desktop_apps_cache',None)
        if cached and time.monotonic()-cached[0]<60:return cached[1]
        cmd="Get-StartApps | Where-Object { ($_.Name -match '^(Codex|Claude)( |$)') -or ($_.AppID -match '^OpenAI.Codex_') } | Select-Object Name,AppID | ConvertTo-Json -Compress"
        try:
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',cmd],capture_output=True,timeout=12,creationflags=subprocess.CREATE_NO_WINDOW)
            rows=json.loads(result.stdout.decode('utf-8-sig',errors='replace') or '[]')
            if isinstance(rows,dict):rows=[rows]
        except (OSError,ValueError,subprocess.TimeoutExpired):rows=[]
        apps={}
        for row in rows:
            name=str(row.get('Name',''));appid=str(row.get('AppID',''))
            if not appid or any(c in appid for c in '\r\n\x00'):continue
            key='codex-desktop' if name.lower().startswith('codex') or appid.startswith('OpenAI.Codex_') else 'claude-desktop'
            apps.setdefault(key,{'name':name,'app_id':appid})
        self._memo_desktop_apps_cache=(time.monotonic(),apps)
        return apps

    def memo_pick_agent_app(self):
        if self._window is None:return {'path':None}
        import webview
        value=self._window.create_file_dialog(webview.FileDialog.OPEN,allow_multiple=False,file_types=('Application (*.exe)',))
        return {'path':str(value[0]) if value else None}

    def memo_prepare_desktop_handoff(self,request):
        self.call('memo.capabilities',{})
        from memorive_research_workspace.service import desktop_handoff
        # GetFolderPath uses the Windows account path, not the packaged private profile override.
        import ctypes
        buffer=ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None,28,None,0,buffer)!=0:raise ValueError('HANDOFF_DIRECTORY_UNAVAILABLE')
        root=Path(buffer.value)/'Memorive'/'Handoff'
        return desktop_handoff(self._memo,request['thread_id'],request.get('question',''),root)

    def memo_desktop_handoff_action(self,request):
        self.call('memo.capabilities',{})
        from memorive_research_workspace.store import digest
        row=self._memo.store.get('desktop_handoff',request.get('id',''))
        if not row:raise ValueError('HANDOFF_NOT_FOUND')
        folder=Path(row['path']);manifest=folder/'manifest.json'
        if digest(manifest.read_bytes())!=row['manifest_hash']:raise ValueError('HANDOFF_CHANGED')
        for name,meta in row['files'].items():
            p=folder/name
            if not p.resolve().is_relative_to(folder.resolve()) or digest(p.read_bytes())!=meta['sha256']:raise ValueError('HANDOFF_CHANGED')
        if request.get('action')=='folder':os.startfile(str(folder));return {'status':'OPEN_REQUESTED'}
        if request.get('action')!='launch':raise ValueError('HANDOFF_ACTION_INVALID')
        path=row.get('app_path','')
        if path:
            p=Path(path)
            if not p.is_absolute() or not p.is_file() or p.suffix.lower()!='.exe' or str(p).startswith(('\\\\','//')) or p.name.lower() in {'cmd.exe','powershell.exe','pwsh.exe','wscript.exe','cscript.exe','mshta.exe','rundll32.exe'}:raise ValueError('AGENT_APP_PATH_INVALID')
            os.startfile(str(p))
        else:
            app=self.memo_desktop_apps().get(row['agent'])
            if not app:raise ValueError('AGENT_DESKTOP_NOT_FOUND')
            # App identity comes only from the local registered Start menu inventory.
            os.startfile('shell:AppsFolder\\'+app['app_id'])
        return {'status':'OPEN_REQUESTED','model_called':False,'task_started':False}

    def memo_agent_config(self):
        self.call('memo.capabilities',{})
        base=Path(sys.executable).parent if getattr(sys,'frozen',False) else self._source_root.parent
        helper=Path(sys.executable) if getattr(sys,'frozen',False) else base/'Memorive.exe'
        args=['--memo-agent','--workspace',str(self._memo.store.root),'--mcp']
        if not helper.exists():
            helper=Path(sys.executable);args=[str(self._source_root/'memorive_research_workspace/agent_entry.py'),*[v for v in args if v!='--memo-agent']]
        toml='[mcp_servers.memo]\ncommand = '+json.dumps(str(helper),ensure_ascii=False)+'\nargs = '+json.dumps(args,ensure_ascii=False)+'\n'
        return {'mcpServers':{'memo':{'command':str(helper),'args':args}},'codex_config_toml':toml,
                'obsidian_plugin':str(base/'integrations/obsidian/memorive-companion'),
                'zotero_plugin':str(base/'integrations/zotero/memorive-companion-0.8.113.xpi'),
                'helper':str(helper),
                'skills':str(base/'integrations/skills'),
                'workspace':str(self._memo.store.root),
                'permissions':'Search and draft submission; human approval stays in Memorive'}

    def open_library_sample_artifact(self, artifact_id: str) -> dict[str, Any]:
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("LIBRARY_SAMPLE_ARTIFACT_ID_INVALID")
        validation = self._library.call("library.validate_external_view", {"artifact_id": artifact_id})
        if validation.get("status") != "PASS_VALIDATED_NOT_LAUNCHED":
            raise RuntimeError(f"LIBRARY_SAMPLE_OPEN_BLOCKED:{validation.get('rejection_reason') or 'VALIDATION_FAILED'}")
        projection = self._library.call("library.get_artifact", {"artifact_id": artifact_id})
        artifact = projection.get("artifact")
        if not isinstance(artifact, dict):
            raise RuntimeError("LIBRARY_SAMPLE_ARTIFACT_PROJECTION_INVALID")
        relative = str(artifact.get("relative_path") or "").replace("\\", "/")
        relative_path = Path(relative)
        if not relative or relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
            raise RuntimeError("LIBRARY_SAMPLE_RELATIVE_PATH_INVALID")
        fixture_root = (self._source_root.parent / "fixtures_integration" / "files").resolve(strict=True)
        target = fixture_root.joinpath(*relative_path.parts).resolve(strict=True)
        if not target.is_relative_to(fixture_root):
            raise RuntimeError("LIBRARY_SAMPLE_PATH_ESCAPE")
        if not target.is_file() or target.is_symlink() or self._is_reparse_point(target):
            raise RuntimeError("LIBRARY_SAMPLE_FILE_INVALID")
        observed_sha256 = hashlib.sha256(target.read_bytes()).hexdigest().upper()
        os.startfile(target, "open")
        return {
            "schema_version": "DesktopAssistantLibrarySampleOpenReceipt-v1",
            "artifact_id": artifact_id,
            "sample": True,
            "controlled_fixture": True,
            "content_sha256": observed_sha256,
            "opened_by_windows_default_application": True,
            "status": "OPENED",
        }

    def locate_library_refinement_artifact(self, artifact_id: str) -> dict[str, Any]:
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("LIBRARY_REFINEMENT_ARTIFACT_ID_INVALID")
        target = self._library.resolve_refinement_readable_artifact(artifact_id)
        process = subprocess.Popen(
            ["explorer.exe", "/select,", str(target)],
            shell=False,
            close_fds=True,
        )
        return {
            "schema_version": "DesktopLibraryRefinementLocateReceipt-v1",
            "artifact_id": artifact_id,
            "process_id": process.pid,
            "selected_controlled_private_product": True,
            "review_open_recorded": False,
            "status": "OPENED",
        }

    def open_library_refinement_artifact(self, artifact_id: str) -> dict[str, Any]:
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("LIBRARY_REFINEMENT_ARTIFACT_ID_INVALID")
        target = self._library.resolve_refinement_readable_artifact(artifact_id)
        try:
            os.startfile(target, "open")
        except OSError as error:
            if getattr(error, "winerror", None) not in {31, 1155}: raise
            # No Markdown association: open this same controlled .md in the
            # system text viewer, never fall back to exposing the JSON.
            viewer = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/notepad.exe"
            subprocess.Popen([str(viewer), str(target)], shell=False, close_fds=True)
        review_receipt = self._library.record_refinement_open(artifact_id)
        return {
            "schema_version": "DesktopLibraryRefinementExternalOpenReceipt-v1",
            "artifact_id": artifact_id,
            "opened_controlled_private_product": True,
            "review_open_recorded": True,
            "opened_at": review_receipt["opened_at"],
            "verified_draft_sha256": review_receipt["verified_draft_sha256"],
            "status": "OPENED",
        }

    def locate_library_core_artifact(self, artifact_id: str) -> dict[str, Any]:
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("LIBRARY_Core_ARTIFACT_ID_INVALID")
        target = self._library.resolve_core_private_artifact(artifact_id)
        process = subprocess.Popen(
            ["explorer.exe", "/select,", str(target)],
            shell=False,
            close_fds=True,
        )
        return {
            "schema_version": "DesktopCoreArtifactLocateReceipt-v1",
            "artifact_id": artifact_id,
            "process_id": process.pid,
            "selected_controlled_core_artifact": True,
            "status": "OPENED",
        }

    def locate_library_core_source(self, artifact_id: str) -> dict[str, Any]:
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("LIBRARY_Core_ARTIFACT_ID_INVALID")
        target = self._library.resolve_core_private_source(artifact_id)
        process = subprocess.Popen(
            ["explorer.exe", "/select,", str(target)],
            shell=False,
            close_fds=True,
        )
        return {
            "schema_version": "DesktopCoreSourceLocateReceipt-v1",
            "artifact_id": artifact_id,
            "process_id": process.pid,
            "selected_controlled_core_source": True,
            "status": "OPENED",
        }

    def open_library_core_artifact(self, artifact_id: str) -> dict[str, Any]:
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("LIBRARY_Core_ARTIFACT_ID_INVALID")
        target = self._library.resolve_core_private_artifact(artifact_id)
        os.startfile(target, "open")
        self._library.paper_review.open(artifact_id)
        return {
            "schema_version": "DesktopCoreArtifactExternalOpenReceipt-v1",
            "artifact_id": artifact_id,
            "opened_controlled_core_artifact": True,
            "status": "OPENED",
        }

    def get_console_connection(self) -> dict[str, Any]:
        """Read the actual authenticated console bridge, never telemetry flags."""
        bridge = getattr(self, '_console_bridge', None)
        connected = bool(bridge is not None and getattr(bridge, '_server', None) is not None)
        allowed = self._read_console_permission()
        enabled = connected and allowed and bridge._access_enabled and not bridge._closing
        expressions = bridge._expressions.state() if connected else {}
        return {'schema_version':'DesktopConsoleConnection-v1',
                'status':('CONNECTED' if enabled else 'PAUSED') if connected else 'NOT_CONNECTED',
                'isolated_session':connected, 'telemetry_upload_enabled':False,
                'automatic_execution_enabled':False,'access_enabled':bool(allowed),
                'can_toggle':not connected or not bridge._closing,
                'scope':'CURRENT_PROFILE',
                'interfaces':{'business':bool(enabled),'expressions':bool(enabled and expressions.get('renderer_ready'))},
                'expression_event_count':33 if expressions.get('renderer_ready') else 0,
                'active_expression_state':(expressions.get('active') or {}).get('state')}

    def _read_console_permission(self) -> bool:
        from console_permission import read_permission
        return read_permission(self._state_dir)

    def set_console_connection(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request,dict) or set(request)!={'enabled'} or type(request['enabled']) is not bool:
            raise ValueError('CONSOLE_ACCESS_BOOLEAN_REQUIRED')
        bridge=getattr(self,'_console_bridge',None)
        if bridge is not None and bridge._closing:raise ValueError('CONSOLE_SESSION_CLOSING')
        from console_permission import write_permission
        with self._lock:write_permission(self._state_dir,request['enabled'])
        if bridge is not None:bridge.set_access(request['enabled'])
        return self.get_console_connection()

    def open_work_log_sample_directory(self) -> dict[str, Any]:
        log_root = (self._state_dir / "work_log").resolve(strict=True)
        if not log_root.is_relative_to(self._state_dir.resolve(strict=True)) or not log_root.is_dir():
            raise RuntimeError("WORK_LOG_SAMPLE_DIRECTORY_INVALID")
        process = subprocess.Popen(["explorer.exe", str(log_root)], shell=False, close_fds=True)
        return {
            "schema_version": "DesktopAssistantWorkLogSampleDirectoryReceipt-v1",
            "sample": True,
            "controlled_projection_directory": True,
            "process_id": process.pid,
            "status": "OPENED",
        }

    def locate_work_log_sample_projection(self) -> dict[str, Any]:
        log_root = (self._state_dir / "work_log").resolve(strict=True)
        projection = (log_root / "work_log_projection.json").resolve(strict=True)
        if not projection.is_relative_to(log_root) or not projection.is_file():
            raise RuntimeError("WORK_LOG_SAMPLE_PROJECTION_INVALID")
        process = subprocess.Popen(
            ["explorer.exe", "/select,", str(projection)],
            shell=False,
            close_fds=True,
        )
        return {
            "schema_version": "DesktopAssistantWorkLogSampleProjectionLocateReceipt-v1",
            "sample": True,
            "selected_controlled_projection": True,
            "process_id": process.pid,
            "status": "OPENED",
        }

    def get_inbox_fixture_paths(self) -> dict[str, Any]:
        paths = [self._fixture_root / "sample.docx", self._fixture_root / "sample.png"]
        if not all(path.is_file() and path.resolve().is_relative_to(self._fixture_root.resolve()) for path in paths):
            raise RuntimeError("SYNTHETIC_INBOX_FIXTURE_MISSING")
        return {
            "schema_version": "WorkLogSyntheticFixtureLocatorProjection-v1",
            "paths": [str(path.resolve()) for path in paths],
            "sha256s": [hashlib.sha256(path.read_bytes()).hexdigest().upper() for path in paths],
            "synthetic_only": True,
            "status": "PASS",
        }

    def pick_settings_directory(self, role: str) -> dict[str, Any]:
        if role not in {"workspace_root", "artifact_root", "external_library"}:
            raise ValueError("DIRECTORY_ROLE_INVALID")
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        from webview import FileDialog
        selected = self._window.create_file_dialog(
            FileDialog.FOLDER,
            directory=str(self._effective_directory(role)),
            allow_multiple=False,
        )
        if not selected:
            return {
                "schema_version": "DesktopAssistantDirectoryPickerReceipt-v1",
                "role": role,
                "path": None,
                "status": "CANCELLED",
            }
        selected_path = Path(selected[0]).resolve(strict=False)
        selected_io = windows_io_path(selected_path)
        if not selected_io.is_dir() or selected_io.is_symlink() or self._is_reparse_point(selected_io):
            raise ValueError("DIRECTORY_SELECTION_NOT_REGULAR_DIRECTORY")
        path = str(selected_path)
        receipt = self._service.call("settings.register_directory", {"role": role, "path": path})
        return {
            "schema_version": "DesktopAssistantDirectoryPickerReceipt-v1",
            "role": role,
            "path": receipt["path"],
            "status": "SELECTED",
        }

    def test_external_library(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {
            "provider_kind", "display_name", "root"
        }:
            raise ValueError("EXTERNAL_LIBRARY_TEST_FIELDS_INVALID")
        provider_kind = str(request["provider_kind"]).upper()
        if provider_kind not in {"OBSIDIAN", "LOGSEQ", "ZOTERO", "CUSTOM"}:
            raise ValueError("EXTERNAL_LIBRARY_PROVIDER_INVALID")
        display_name = request["display_name"]
        if (
            not isinstance(display_name, str)
            or not display_name.strip()
            or len(display_name.strip()) > 80
        ):
            raise ValueError("EXTERNAL_LIBRARY_DISPLAY_NAME_REQUIRED")
        root_value = request["root"]
        if not isinstance(root_value, str) or not root_value:
            raise ValueError("EXTERNAL_LIBRARY_ROOT_REQUIRED")
        raw_root = Path(root_value)
        raw_root_io = windows_io_path(raw_root)
        if (
            not raw_root.is_absolute()
            or not raw_root_io.exists()
            or not raw_root_io.is_dir()
            or raw_root_io.is_symlink()
            or self._is_reparse_point(raw_root_io)
        ):
            raise ValueError("EXTERNAL_LIBRARY_ROOT_INVALID")
        root = raw_root.resolve(strict=False)
        marker_name: str | None = None
        marker_kind: str | None = None
        if provider_kind == "OBSIDIAN":
            marker_name, marker_kind = ".obsidian", "directory"
        elif provider_kind == "LOGSEQ":
            marker_name, marker_kind = "logseq", "directory"
        elif provider_kind == "ZOTERO":
            marker_name, marker_kind = "zotero.sqlite", "file"
        if marker_name is not None:
            marker = root / marker_name
            marker_io = windows_io_path(marker)
            marker_valid = (
                marker_io.is_dir() if marker_kind == "directory" else marker_io.is_file()
            )
            if (
                not marker_valid
                or marker_io.is_symlink()
                or self._is_reparse_point(marker_io)
            ):
                raise ValueError(f"{provider_kind}_VAULT_MARKER_MISSING")
        registration = self._service.call(
            "settings.register_directory",
            {"role": "external_library", "path": str(root)},
        )
        return {
            "schema_version": "DesktopExternalLibraryTestReceipt-v1",
            "provider_kind": provider_kind,
            "display_name": display_name.strip(),
            "root": registration["path"],
            "marker": marker_name,
            "marker_kind": marker_kind,
            "directory_access_checked": True,
            "persistent_mutation": False,
            "external_process_launches": 0,
            "external_network_calls": 0,
            "status": "PASS",
        }

    def record_settings_save_receipt(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {"overall_status", "stages"}:
            raise ValueError("SETTINGS_SAVE_RECEIPT_FIELDS_INVALID")
        if request["overall_status"] not in {"PASS", "WARNING", "FAILED"}:
            raise ValueError("SETTINGS_SAVE_RECEIPT_STATUS_INVALID")
        raw_stages = request.get("stages")
        allowed_stages = ("settings", "refinement", "assistant", "refresh")
        if not isinstance(raw_stages, Mapping) or set(raw_stages) != set(allowed_stages):
            raise ValueError("SETTINGS_SAVE_RECEIPT_STAGES_INVALID")
        stages: dict[str, dict[str, Any]] = {}
        for name in allowed_stages:
            raw = raw_stages[name]
            if not isinstance(raw, Mapping):
                raise ValueError("SETTINGS_SAVE_RECEIPT_STAGE_INVALID")
            status = str(raw.get("status") or "")
            if status not in {"PASS", "WARNING", "FAILED", "SKIPPED"}:
                raise ValueError("SETTINGS_SAVE_RECEIPT_STAGE_STATUS_INVALID")
            error_code = re.sub(
                r"[^A-Z0-9_:-]", "_", str(raw.get("error_code") or "").upper()
            )[:120]
            revision = raw.get("revision")
            if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int)):
                raise ValueError("SETTINGS_SAVE_RECEIPT_REVISION_INVALID")
            stages[name] = {
                "status": status,
                "error_code": error_code or None,
                "revision": revision,
            }
        with self._lock:
            revision = 1
            if self._settings_save_receipt_path.is_file():
                try:
                    previous = json.loads(
                        self._settings_save_receipt_path.read_text(encoding="utf-8")
                    )
                    revision = int(previous.get("revision", 0)) + 1
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    revision = 1
            receipt = {
                "schema_version": "DesktopSettingsSaveAggregateReceipt-v1",
                "revision": revision,
                "saved_at": _utc_now(),
                "overall_status": request["overall_status"],
                "stages": stages,
                "credential_values_included": False,
                "raw_private_content_included": False,
                "status": "PASS",
            }
            self._write_json_file(self._settings_save_receipt_path, receipt)
            return deepcopy(receipt)

    def claim_assistant_presentation_delivery(
        self, request: dict[str, Any]
    ) -> dict[str, Any]:
        """Atomically claim one durable pet delivery before any motion starts."""

        required = {
            "locator", "reason_code", "animation_state", "delivery_id", "reduced_motion"
        }
        if not isinstance(request, dict) or set(request) != required:
            raise ValueError("ASSISTANT_PRESENTATION_CLAIM_FIELDS_INVALID")
        locator = request["locator"]
        if (
            not isinstance(locator, str)
            or not locator.startswith("memorive://")
            or len(locator) > 320
        ):
            raise ValueError("ASSISTANT_PRESENTATION_CLAIM_LOCATOR_INVALID")
        reason_code = str(request["reason_code"])
        animation_state = str(request["animation_state"]).upper()
        delivery_id = request["delivery_id"]
        if not re.fullmatch(r"[A-Z0-9_]{1,96}", reason_code):
            raise ValueError("ASSISTANT_PRESENTATION_CLAIM_REASON_INVALID")
        if not re.fullmatch(r"[A-Z0-9_]{1,32}", animation_state):
            raise ValueError("ASSISTANT_PRESENTATION_CLAIM_ANIMATION_INVALID")
        if (
            not isinstance(delivery_id, str)
            or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", delivery_id) is None
        ):
            raise ValueError("ASSISTANT_PRESENTATION_CLAIM_DELIVERY_INVALID")
        if not isinstance(request["reduced_motion"], bool):
            raise ValueError("ASSISTANT_PRESENTATION_CLAIM_REDUCED_MOTION_INVALID")
        key_material = {
            "locator": locator,
            "reason_code": reason_code,
            "animation_state": animation_state,
            "delivery_id": delivery_id,
        }
        entry_key = _sha256_json(key_material)
        now = _utc_now()
        with self._lock:
            envelope: dict[str, Any] = {
                "schema_version": "DesktopAssistantPresentationReceiptStore-v1",
                "revision": 0,
                "updated_at": now,
                "entries": {},
                "raw_private_content_included": False,
            }
            if self._assistant_presentation_receipt_path.is_file():
                try:
                    loaded = json.loads(
                        self._assistant_presentation_receipt_path.read_text(encoding="utf-8")
                    )
                    if (
                        isinstance(loaded, dict)
                        and loaded.get("schema_version") == envelope["schema_version"]
                        and isinstance(loaded.get("entries"), dict)
                    ):
                        envelope = loaded
                except (OSError, json.JSONDecodeError):
                    pass
            entries = dict(envelope.get("entries") or {})
            existing = entries.get(entry_key)
            if delivery_id.startswith('message:') and isinstance(existing, Mapping):
                if existing.get('completed_at'):
                    self._assistant_deliveries.ack(delivery_id)
                elif existing.get('delivery_session') != self._assistant_delivery_session:
                    # Only an unacknowledged message is recoverable after a
                    # presenter restart. Historical completion dedupe is intact.
                    existing = None
            already_consumed = bool(
                isinstance(existing, Mapping)
                and (
                    existing.get("claimed_at")
                    or existing.get("started_at")
                    or existing.get("completed_at")
                )
            )
            if already_consumed:
                return {
                    "schema_version": "DesktopAssistantPresentationDeliveryClaim-v1",
                    "receipt_key": entry_key,
                    "already_consumed": True,
                    "status": "ALREADY_CONSUMED",
                }
            entry = {
                "locator": locator,
                "reason_code": reason_code,
                "animation_state": animation_state,
                "delivery_id": delivery_id,
                "requested_at": now,
                "claimed_at": now,
                "delivery_session": self._assistant_delivery_session,
                "started_at": None,
                "completed_at": None,
                "deduplicated_count": 0,
                "last_event": "CLAIMED",
                "last_observed_at": now,
                "reduced_motion": request["reduced_motion"],
                "deduplicated": False,
                "single_shot_started": False,
            }
            entries[entry_key] = entry
            if len(entries) > 128:
                entries = dict(
                    sorted(
                        entries.items(),
                        key=lambda row: str(row[1].get("last_observed_at") or ""),
                        reverse=True,
                    )[:128]
                )
            envelope.update(
                {
                    "revision": int(envelope.get("revision", 0)) + 1,
                    "updated_at": now,
                    "entries": entries,
                    "raw_private_content_included": False,
                }
            )
            self._write_json_file(self._assistant_presentation_receipt_path, envelope)
            return {
                "schema_version": "DesktopAssistantPresentationDeliveryClaim-v1",
                "receipt_key": entry_key,
                "already_consumed": False,
                "status": "CLAIMED",
            }

    def assistant_ack_delivery(self, request):
        if not isinstance(request,dict) or set(request) != {'delivery_id'} or not isinstance(request['delivery_id'],str):
            raise ValueError('ASSISTANT_DELIVERY_ACK_INVALID')
        return self._assistant_deliveries.ack(request['delivery_id'])

    def record_assistant_presentation_receipt(
        self, request: dict[str, Any]
    ) -> dict[str, Any]:
        allowed = {
            "event",
            "locator",
            "reason_code",
            "animation_state",
            "reduced_motion",
            "deduplicated",
            "single_shot_started",
        }
        if (
            not isinstance(request, dict)
            or not allowed <= set(request)
            or set(request) - allowed - {"delivery_id"}
        ):
            raise ValueError("ASSISTANT_PRESENTATION_RECEIPT_FIELDS_INVALID")
        event = str(request["event"])
        if event not in {
            "STARTED",
            "COMPLETED",
            "DEDUPLICATED",
            "STATIC",
            "QUEUED",
            "BLINK_QUEUE_SUPPRESSED",
            "CLAIMED",
            "COALESCED",
            "INTERRUPTED",
        }:
            raise ValueError("ASSISTANT_PRESENTATION_RECEIPT_EVENT_INVALID")
        locator = request.get("locator")
        if locator is not None and (
            not isinstance(locator, str)
            or not locator.startswith("memorive://")
            or len(locator) > 320
        ):
            raise ValueError("ASSISTANT_PRESENTATION_RECEIPT_LOCATOR_INVALID")
        reason_code = str(request.get("reason_code") or "")
        animation_state = str(request.get("animation_state") or "").upper()
        delivery_id = request.get("delivery_id")
        if delivery_id is not None and (
            not isinstance(delivery_id, str)
            or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", delivery_id) is None
        ):
            raise ValueError("ASSISTANT_PRESENTATION_RECEIPT_DELIVERY_INVALID")
        if not re.fullmatch(r"[A-Z0-9_]{1,96}", reason_code):
            raise ValueError("ASSISTANT_PRESENTATION_RECEIPT_REASON_INVALID")
        if not re.fullmatch(r"[A-Z0-9_]{1,32}", animation_state):
            raise ValueError("ASSISTANT_PRESENTATION_RECEIPT_ANIMATION_INVALID")
        for field in ("reduced_motion", "deduplicated", "single_shot_started"):
            if not isinstance(request[field], bool):
                raise ValueError("ASSISTANT_PRESENTATION_RECEIPT_BOOLEAN_INVALID")
        key_material = {
            "locator": locator,
            "reason_code": reason_code,
            "animation_state": animation_state,
            "delivery_id": delivery_id,
        }
        entry_key = _sha256_json(key_material)
        now = _utc_now()
        with self._lock:
            envelope: dict[str, Any] = {
                "schema_version": "DesktopAssistantPresentationReceiptStore-v1",
                "revision": 0,
                "updated_at": now,
                "entries": {},
                "raw_private_content_included": False,
            }
            if self._assistant_presentation_receipt_path.is_file():
                try:
                    loaded = json.loads(
                        self._assistant_presentation_receipt_path.read_text(encoding="utf-8")
                    )
                    if (
                        isinstance(loaded, dict)
                        and loaded.get("schema_version") == envelope["schema_version"]
                        and isinstance(loaded.get("entries"), dict)
                    ):
                        envelope = loaded
                except (OSError, json.JSONDecodeError):
                    pass
            entries = dict(envelope.get("entries") or {})
            entry = dict(
                entries.get(entry_key)
                or {
                    "locator": locator,
                    "reason_code": reason_code,
                    "animation_state": animation_state,
                    "delivery_id": delivery_id,
                    "requested_at": now,
                    "started_at": None,
                    "completed_at": None,
                    "deduplicated_count": 0,
                }
            )
            if event == "STARTED":
                entry["started_at"] = entry.get("started_at") or now
            elif event == "COMPLETED":
                entry["completed_at"] = now
                if delivery_id and hasattr(self, '_assistant_deliveries'):
                    self._assistant_deliveries.ack(delivery_id)
            elif event == "STATIC" and request["reduced_motion"] and delivery_id and hasattr(self, '_assistant_deliveries'):
                self._assistant_deliveries.ack(delivery_id)
            elif event == "DEDUPLICATED":
                entry["deduplicated_count"] = int(entry.get("deduplicated_count", 0)) + 1
            entry.update(
                {
                    "last_event": event,
                    "last_observed_at": now,
                    "reduced_motion": request["reduced_motion"],
                    "deduplicated": request["deduplicated"],
                    "single_shot_started": request["single_shot_started"],
                }
            )
            entries[entry_key] = entry
            if len(entries) > 128:
                entries = dict(
                    sorted(
                        entries.items(),
                        key=lambda row: str(row[1].get("last_observed_at") or ""),
                        reverse=True,
                    )[:128]
                )
            envelope.update(
                {
                    "revision": int(envelope.get("revision", 0)) + 1,
                    "updated_at": now,
                    "entries": entries,
                    "raw_private_content_included": False,
                }
            )
            self._write_json_file(self._assistant_presentation_receipt_path, envelope)
            return {
                "schema_version": "DesktopAssistantPresentationReceipt-v1",
                "receipt_key": entry_key,
                "revision": envelope["revision"],
                "entry": deepcopy(entry),
                "status": "PASS",
            }

    @staticmethod
    def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        if len(encoded) > 4 * 1024 * 1024:
            raise ValueError("JSON_EXPORT_TOO_LARGE")
        path = Path(path).resolve(strict=False)
        target_io = windows_io_path(path)
        if not windows_io_path(path.parent).is_dir():
            raise ValueError("JSON_EXPORT_PARENT_MISSING")
        temporary = path.parent / f".json.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        temporary_io = windows_io_path(temporary)
        try:
            with temporary_io.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_io, target_io)
        finally:
            if temporary_io.exists():
                temporary_io.unlink()

    def _save_json_dialog(self, payload: dict[str, Any], filename: str) -> dict[str, Any]:
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        from webview import FileDialog
        selected = self._window.create_file_dialog(
            FileDialog.SAVE,
            directory=str(self._effective_directory("artifact_root")),
            save_filename=filename,
            file_types=("JSON files (*.json)",),
        )
        if not selected:
            return {"path": None, "bytes": 0, "status": "CANCELLED"}
        selected_path = selected if isinstance(selected, str) else selected[0]
        destination = Path(selected_path).resolve(strict=False)
        if destination.suffix.lower() != ".json":
            destination = destination.with_suffix(".json")
        if not windows_io_path(destination.parent).is_dir():
            raise ValueError("JSON_EXPORT_PARENT_MISSING")
        self._write_json_file(destination, payload)
        destination_io = windows_io_path(destination)
        return {
            "path": str(destination),
            "bytes": destination_io.stat().st_size,
            "sha256": hashlib.sha256(destination_io.read_bytes()).hexdigest().upper(),
            "status": "EXPORTED",
        }

    def export_redacted_settings_file(self) -> dict[str, Any]:
        payload = self._service.call("settings.export_redacted", {})
        receipt = self._save_json_dialog(payload, "Memorive-settings-redacted.json")
        return {"schema_version": "DesktopAssistantSettingsFileExportReceipt-v1", **receipt}

    def import_redacted_settings_file(self) -> dict[str, Any]:
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        from webview import FileDialog
        selected = self._window.create_file_dialog(
            FileDialog.OPEN,
            directory=str(self._effective_directory("workspace_root")),
            allow_multiple=False,
            file_types=("JSON files (*.json)",),
        )
        if not selected:
            return {
                "schema_version": "DesktopAssistantSettingsFileImportProjection-v1",
                "payload": None,
                "status": "CANCELLED",
            }
        source = Path(selected[0]).resolve(strict=False)
        source_io = windows_io_path(source)
        if not source_io.is_file() or source_io.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("SETTINGS_IMPORT_FILE_INVALID")
        payload = json.loads(source_io.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("SETTINGS_IMPORT_OBJECT_REQUIRED")
        return {
            "schema_version": "DesktopAssistantSettingsFileImportProjection-v1",
            "payload": payload,
            "source_sha256": hashlib.sha256(source_io.read_bytes()).hexdigest().upper(),
            "status": "SELECTED",
        }

    def browser_companion_sites(self) -> dict[str, Any]:
        from memorive_browser_companion.site_profiles import SiteProfiles, default_sites, normalize_sites, profile_hash, site_authorizations, OFFICIAL_WEB_SITES, BUILTINS
        catalog = [dict(provider=key, name=name, hosts=list(hosts), adapter=key if key in BUILTINS else 'generic')
                   for key, name, hosts, _proof in OFFICIAL_WEB_SITES]
        if not hasattr(self, '_profile_root'):
            rows = normalize_sites(default_sites())
            return dict(revision=0, sites=rows, profile_sha256=profile_hash(rows), site_authorizations=site_authorizations(rows), official_site_catalog=catalog)
        state = SiteProfiles(self._profile_path('BROWSER_COMPANION')).get()
        return dict(state, site_authorizations=site_authorizations(state['sites']), official_site_catalog=catalog)

    def save_browser_companion_sites(self, sites, expected_revision):
        from memorive_browser_companion.site_profiles import SiteProfiles
        # Reconcile a newly arrived terminal report before inspecting the
        # durable request marker. A completed request is not an active worker.
        self.browser_companion_status()
        state_path = self._browser_companion_state_path()
        if state_path.is_file():
            running = json.loads(state_path.read_text(encoding='utf-8'))
            if running.get('sync_requested_mode'):
                raise ValueError('BROWSER_SYNC_ALREADY_RUNNING')
        result = SiteProfiles(self._profile_path('BROWSER_COMPANION')).save(sites, expected_revision)
        refreshed = self._refresh_prepared_browser_companion_package()
        return dict(result, package_refresh=refreshed)

    def _browser_enabled_sites(self):
        from memorive_browser_companion.site_profiles import active_sites
        return active_sites(self.browser_companion_sites()['sites'])

    def _browser_companion_package(self) -> tuple[Path, set[str], str]:
        source = (self._source_root / "memorive_browser_companion" / "extension").resolve()
        try:
            source.relative_to(self._source_root)
        except ValueError as error:
            raise RuntimeError("BROWSER_COMPANION_SOURCE_ESCAPE") from error
        required = {
            "manifest.json",
            "background.js",
            "content.js",
            "popup.html",
            "popup.css",
            "popup.js",
            "runner.html",
            "runner.js",
            "README.txt",
            "site_profiles.js",
            "recording.js", "browser_identity.js",
        }
        if not source.is_dir() or {path.name for path in source.iterdir() if path.is_file()} != required:
            raise RuntimeError("BROWSER_COMPANION_PACKAGE_INVALID")
        from memorive_browser_companion.site_profiles import configured_extension, default_sites, profile_hash
        settings = self.browser_companion_sites()
        if settings['profile_sha256'] != profile_hash(default_sites()):
            source = configured_extension(source, self._browser_companion_export_root()/'packages', settings['sites'])
        digest = hashlib.sha256()
        for name in sorted(required):
            digest.update(name.encode("utf-8"))
            digest.update(b"\x00")
            digest.update((source / name).read_bytes())
        return source, required, digest.hexdigest().upper()

    def _browser_companion_export_root(self) -> Path:
        # Browser-facing files are public generated assets, not the private state.
        # Use a short, stable, per-profile namespace outside sandbox nesting.
        owner = hashlib.sha256(str(self._profile_root.resolve()).casefold().encode()).hexdigest()[:10]
        return (Path.home() / "AppData" / "Local" / "Memorive" / "Bridge" / owner).resolve()

    def _check_browser_companion_destination(self, destination: Path) -> None:
        resolved = destination.resolve()
        # Permit only this profile's old directory or its own short export root.
        roots = (self._profile_path("BROWSER_COMPANION").resolve(), self._browser_companion_export_root())
        if not any(resolved.is_relative_to(root) and resolved != root for root in roots):
            raise RuntimeError("BROWSER_COMPANION_DESTINATION_ESCAPE")

    def open_browser_companion_site(self, url, preferred_browser=None):
        from memorive_browser_companion.site_profiles import normalize_sites, official_web_identity, GENERIC, BUILTINS
        identity = official_web_identity(url)
        if not identity:
            raise ValueError("BROWSER_SITE_NOT_VERIFIED_OFFICIAL")
        row = normalize_sites([dict(slot=1, name=identity['name'], url=url,
            adapter=identity['provider'] if identity['provider'] in BUILTINS else 'generic',
            enabled=True, selectors=GENERIC)])[0]
        selected = select_browser(self._browser_executables(), preferred_browser)
        command = [str(selected[1]), row['url']]
        if self._system_effects_enabled:
            subprocess.Popen(command, shell=False, close_fds=True)
        return dict(status='OPENED' if self._system_effects_enabled else 'READY', url=row['url'], browser=selected[0])

    def open_browser_companion_folder(self):
        setup_path = self._browser_companion_state_path()
        state = json.loads(setup_path.read_text(encoding='utf8')) if setup_path.is_file() else {}
        if not state.get('extension_path'):
            raise ValueError('BROWSER_COMPANION_NOT_PREPARED')
        destination = Path(state['extension_path'])
        self._check_browser_companion_destination(destination)
        if not (destination / 'manifest.json').is_file():
            raise ValueError('BROWSER_COMPANION_NOT_PREPARED')
        if self._system_effects_enabled and os.name == 'nt':
            subprocess.Popen(['explorer.exe', str(destination)], shell=False, close_fds=True)
        return dict(path=str(destination))

    def _browser_companion_state_path(self) -> Path:
        return self._profile_path("BROWSER_COMPANION") / "setup_v1.json"

    def _browser_companion_manifest_projection(self) -> dict[str, str]:
        source, _required, _package_sha256 = self._browser_companion_package()
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        version = manifest.get("version")
        if not isinstance(version, str) or not version.strip():
            raise RuntimeError("BROWSER_COMPANION_MANIFEST_VERSION_MISSING")
        # An unpacked extension keeps a browser/profile-specific id.  Do not
        # force a manifest key during an upgrade: adding one would change the
        # identity of already loaded 3.1 installations.  The extension writes
        # chrome.runtime.id into its signed-shape local sync report instead.
        return {"extension_version": version.strip()}

    @staticmethod
    def _browser_companion_installed_version(destination: Path) -> str | None:
        manifest_path = destination / "manifest.json"
        if not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        version = manifest.get("version") if isinstance(manifest, dict) else None
        return version.strip() if isinstance(version, str) and version.strip() else None

    def _latest_browser_companion_sync_report(
        self,
        *,
        prepared_at_unix: int | None = None,
        expected_sync_mode: str | None = None,
        expected_browser: str | None = None,
        expected_extension_id: str | None = None,
    ) -> dict[str, Any] | None:
        candidates: list[Path] = []
        for root in self._browser_session_download_roots():
            for folder in (root, root / "Memorive会话桥接"):
                if not folder.is_dir():
                    continue
                candidates.extend(
                    path.resolve()
                    for path in folder.glob("memorive-session-library-sync-report-*.json")
                    if path.is_file()
                )
        required_fields = {
            "schema_version", "source", "extension_version", "extension_id", "browser_family",
            "generated_at", "trigger", "status", "providers",
            "cookie_value_reads", "browser_storage_value_reads",
            "hidden_provider_api_calls", "remote_session_mutations",
        }
        enriched_fields = {
            "per_provider_limit", "sync_mode", "active_provider_count",
            "provider_parallelism", "maximum_capture_windows",
            "per_provider_capture_mode",
        }
        dedicated_window_fields = {"window_mode", "user_tab_reused"}
        optional_terminal_fields = {"error_code", "hard_timeout_ms"}
        required_provider_fields = {
            "provider_id", "status", "indexed_count", "captured_count",
            "failed_count", "error_code",
        }
        enriched_provider_fields = {
            "candidate_count", "capture_target", "body_read_count",
            "unchanged_count", "skipped_candidate_count",
            "skipped_error_codes", "safe_diagnostic",
        }
        for path in sorted(
            candidates,
            key=lambda value: (value.stat().st_mtime_ns, value.name),
            reverse=True,
        ):
            try:
                stat_result = path.stat()
                if stat_result.st_size <= 0 or stat_result.st_size > 1024 * 1024:
                    continue
                if prepared_at_unix and stat_result.st_mtime < prepared_at_unix:
                    continue
                payload_bytes = path.read_bytes()
                payload = json.loads(payload_bytes.decode("utf-8"))
                if isinstance(payload, dict) and (
                    (expected_browser is not None and payload.get("browser_family") != expected_browser)
                    or (expected_extension_id is not None and payload.get("extension_id") != expected_extension_id)
                ):
                    continue
                payload_fields = set(payload) if isinstance(payload, dict) else set()
                if (
                    not isinstance(payload, dict)
                    or not required_fields.issubset(payload_fields)
                    or not payload_fields.issubset(
                        required_fields
                        | enriched_fields
                        | dedicated_window_fields
                        | optional_terminal_fields | {'site_profile_sha256'}
                    )
                ):
                    continue
                has_enriched_report = bool(payload_fields & enriched_fields)
                if has_enriched_report and not enriched_fields.issubset(payload_fields):
                    continue
                has_dedicated_window_contract = bool(
                    payload_fields & dedicated_window_fields
                )
                if (
                    has_dedicated_window_contract
                    and not dedicated_window_fields.issubset(payload_fields)
                ):
                    continue
                if (
                    payload.get("schema_version") != "MEMORIVE_BROWSER_SESSION_SYNC_REPORT_V2"
                    or payload.get("source") != "MEMORIVE_BROWSER_COMPANION_RUNNER_V4_DRAFT"
                    or not isinstance(payload.get("extension_version"), str)
                    or not self._browser_companion_semver_at_least(
                        payload.get("extension_version"), (0, 0, 0)
                    )
                    or not isinstance(payload.get("extension_id"), str)
                    or len(payload.get("extension_id")) != 32
                    or any(character not in "abcdefghijklmnop" for character in payload["extension_id"])
                    or payload.get("browser_family") not in FAMILIES | {"CHROMIUM"}
                    or not isinstance(payload.get("generated_at"), str)
                    or not isinstance(payload.get("trigger"), str)
                    or payload.get("status") not in {"COMPLETE", "ERROR"}
                    or any(
                        payload.get(field) != 0
                        for field in (
                            "cookie_value_reads", "browser_storage_value_reads",
                            "hidden_provider_api_calls", "remote_session_mutations",
                        )
                    )
                ):
                    continue
                if has_enriched_report:
                    if (
                        not isinstance(payload.get("per_provider_limit"), int)
                        or not 1 <= payload["per_provider_limit"] <= 50
                        or payload.get("sync_mode")
                        not in {"PREFLIGHT", "FULL", "INCREMENTAL"}
                        or (
                            payload["sync_mode"] == "PREFLIGHT"
                            and payload["per_provider_limit"] != 2
                        )
                        or (
                            payload["sync_mode"] == "FULL"
                            and payload["per_provider_limit"] <= 2
                        )
                        or (
                            payload["sync_mode"] == "INCREMENTAL"
                            and payload["per_provider_limit"] != 10
                        )
                        or payload.get("active_provider_count") != len(self._browser_enabled_sites())
                        or not isinstance(payload.get("provider_parallelism"), bool)
                        or (
                            "error_code" in payload
                            and not isinstance(payload.get("error_code"), str)
                        )
                        or (
                            "hard_timeout_ms" in payload
                            and (
                                not isinstance(payload.get("hard_timeout_ms"), int)
                                or not 1_000
                                <= payload["hard_timeout_ms"]
                                <= 5_400_000
                            )
                        )
                    ):
                        continue
                    dedicated_window_contract = bool(
                        has_dedicated_window_contract
                        and (payload.get("window_mode"), payload.get("per_provider_capture_mode"), payload.get("provider_parallelism")) in (
                            ("DEDICATED_MINIMIZED", "DEDICATED_MINIMIZED_WINDOW_OWNED_TABS", True),
                            ("DEDICATED_VISIBLE", "DEDICATED_VISIBLE_WINDOW_OWNED_TABS", False),
                        )
                        and payload.get("user_tab_reused") is False
                        and payload.get("maximum_capture_windows") == 1
                    )
                    legacy_window_contract = bool(
                        not has_dedicated_window_contract
                        and payload.get("maximum_capture_windows") == 3
                        and payload.get("per_provider_capture_mode")
                        in {
                            "SEQUENTIAL_REUSED_SINGLE_WINDOW",
                            "DEEPSEEK_FOREGROUND_REUSED_TAB__GEMINI_KIMI_ISOLATED_REUSED_WINDOW",
                        }
                    )
                    requires_dedicated_window = (
                        self._browser_companion_semver_at_least(
                            payload["extension_version"], (3, 4, 8)
                        )
                    )
                    if (
                        requires_dedicated_window
                        and not dedicated_window_contract
                    ) or (
                        not requires_dedicated_window
                        and not (
                            legacy_window_contract
                            or dedicated_window_contract
                        )
                    ):
                        continue
                if expected_sync_mode and payload.get("sync_mode") != expected_sync_mode:
                    continue
                providers = payload.get("providers")
                expected_providers = {row['provider_id'] for row in self._browser_enabled_sites()}
                if (self._browser_companion_semver_at_least(payload.get('extension_version'),(3,6,0))
                        and payload.get('site_profile_sha256') != self.browser_companion_sites()['profile_sha256']):
                    continue
                if not isinstance(providers, list) or not expected_providers or len(providers) != len(expected_providers):
                    continue
                provider_projection: dict[str, dict[str, Any]] = {}
                valid = True
                for row in providers:
                    row_fields = set(row) if isinstance(row, dict) else set()
                    if (
                        not isinstance(row, dict)
                        or not required_provider_fields.issubset(row_fields)
                        or not row_fields.issubset(
                            required_provider_fields | enriched_provider_fields
                        )
                    ):
                        valid = False
                        break
                    provider_id = row.get("provider_id")
                    if provider_id not in expected_providers or provider_id in provider_projection:
                        valid = False
                        break
                    if (
                        not isinstance(row.get("status"), str)
                        or not isinstance(row.get("error_code"), str)
                        or any(
                            not isinstance(row.get(field), int) or row[field] < 0
                            for field in ("indexed_count", "captured_count", "failed_count")
                        )
                    ):
                        valid = False
                        break
                    if any(
                        field in row
                        and (
                            not isinstance(row.get(field), int)
                            or row[field] < 0
                        )
                        for field in (
                            "candidate_count", "capture_target", "body_read_count",
                            "unchanged_count", "skipped_candidate_count",
                        )
                    ):
                        valid = False
                        break
                    if (
                        "skipped_error_codes" in row
                        and (
                            not isinstance(row.get("skipped_error_codes"), list)
                            or any(
                                not isinstance(value, str)
                                for value in row["skipped_error_codes"]
                            )
                        )
                    ):
                        valid = False
                        break
                    if (
                        "safe_diagnostic" in row
                        and row.get("safe_diagnostic") is not None
                        and not isinstance(row.get("safe_diagnostic"), dict)
                    ):
                        valid = False
                        break
                    provider_projection[provider_id] = dict(row)
                if not valid or set(provider_projection) != expected_providers:
                    continue
                return {
                    "path": path,
                    "sha256": hashlib.sha256(payload_bytes).hexdigest().upper(),
                    "browser_family": payload["browser_family"],
                    "extension_version": payload["extension_version"],
                    "extension_id": payload["extension_id"],
                    "generated_at": payload["generated_at"],
                    "status": payload["status"],
                    "sync_mode": payload.get("sync_mode"),
                    "per_provider_limit": payload.get("per_provider_limit"),
                    "provider_parallelism": payload.get("provider_parallelism"),
                    "maximum_capture_windows": payload.get(
                        "maximum_capture_windows"
                    ),
                    "per_provider_capture_mode": payload.get(
                        "per_provider_capture_mode"
                    ),
                    "window_mode": payload.get("window_mode"),
                    "user_tab_reused": payload.get("user_tab_reused"),
                    "providers": provider_projection,
                    "site_profile_sha256": payload.get('site_profile_sha256'),
                }
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError):
                continue
        return None

    @staticmethod
    def _browser_companion_extension_id_valid(value: Any) -> bool:
        return bool(
            isinstance(value, str)
            and len(value) == 32
            and all(character in "abcdefghijklmnop" for character in value)
        )

    def _latest_browser_companion_identity_receipt(
        self,
        *,
        current_extension_version: str,
        expected_extension_id: str | None = None,
        expected_browser: str | None = None,
        prepared_at_unix: int | None = None,
    ) -> dict[str, Any] | None:
        """Read and retain the newest fail-closed bridge identity receipt."""

        candidates: list[Path] = []
        for root in self._browser_session_download_roots():
            for folder in (root, root / "Memorive会话桥接"):
                if not folder.is_dir():
                    continue
                candidates.extend(
                    path.resolve()
                    for path in folder.glob("memorive-browser-companion-identity-*.json")
                    if path.is_file()
                )
        required_fields = {
            "schema_version",
            "extension_id",
            "extension_version",
            "observed_at",
            "lifecycle_reason",
            "trigger_protocol",
            "preflight_limit",
            "full_limit",
            "incremental_limit",
            "providers",
            "cookie_value_reads",
            "browser_storage_value_reads",
            "hidden_provider_api_calls",
        }
        for path in sorted(
            candidates,
            key=lambda value: (value.stat().st_mtime_ns, value.name),
            reverse=True,
        ):
            try:
                stat_result = path.stat()
                if stat_result.st_size <= 0 or stat_result.st_size > 1024 * 1024:
                    continue
                if prepared_at_unix and stat_result.st_mtime < prepared_at_unix:
                    continue
                payload_bytes = path.read_bytes()
                payload = json.loads(payload_bytes.decode("utf-8"))
                if (not isinstance(payload, dict) or not required_fields.issubset(payload)
                        or set(payload) - required_fields - {'site_profile_sha256', 'browser_family'}):
                    continue
                extension_id = payload.get("extension_id")
                extension_version = payload.get("extension_version")
                if (
                    payload.get("schema_version")
                    != "MEMORIVE_BROWSER_COMPANION_IDENTITY_V1"
                    or not self._browser_companion_extension_id_valid(extension_id)
                    or (
                        isinstance(expected_extension_id, str)
                        and expected_extension_id
                        and extension_id != expected_extension_id
                    )
                    or extension_version != current_extension_version
                    or (self._browser_companion_semver_at_least(extension_version, (3, 10, 1))
                        and payload.get("browser_family") not in FAMILIES)
                    or (expected_browser is not None and payload.get("browser_family") != expected_browser)
                    or not self._browser_companion_semver_at_least(
                        extension_version, (3, 4, 0)
                    )
                    or not isinstance(payload.get("observed_at"), str)
                    or not payload["observed_at"].strip()
                    or not isinstance(payload.get("lifecycle_reason"), str)
                    or not payload["lifecycle_reason"].strip()
                    or payload.get("trigger_protocol")
                    != "HTTPS_APPROVED_FRAGMENT_V3"
                    or payload.get("preflight_limit") != 2
                    or payload.get("full_limit") != 50
                    or payload.get("incremental_limit") != 10
                    or payload.get("providers")
                    != [row['provider_id'] for row in self._browser_enabled_sites()]
                    or (self._browser_companion_semver_at_least(extension_version,(3,6,0))
                        and payload.get('site_profile_sha256') != self.browser_companion_sites()['profile_sha256'])
                    or any(
                        payload.get(field) != 0
                        for field in (
                            "cookie_value_reads",
                            "browser_storage_value_reads",
                            "hidden_provider_api_calls",
                        )
                    )
                ):
                    continue
                retained_path = self._retain_browser_session_artifact(
                    path, payload_bytes
                )
                return {
                    "path": retained_path,
                    "sha256": hashlib.sha256(payload_bytes).hexdigest().upper(),
                    "extension_id": extension_id,
                    "extension_version": extension_version,
                    "observed_at": payload["observed_at"],
                    "lifecycle_reason": payload["lifecycle_reason"],
                    "browser_family": payload.get("browser_family"),
                    "trigger_protocol": payload["trigger_protocol"],
                    "incremental_limit": payload["incremental_limit"],
                }
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError):
                continue
        return None

    @staticmethod
    def _browser_companion_semver_at_least(
        version: Any,
        minimum: tuple[int, int, int],
    ) -> bool:
        if not isinstance(version, str):
            return False
        match = re.fullmatch(
            r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
            r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
            r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
            version,
        )
        if not match:
            return False
        core = tuple(int(match.group(index)) for index in (1, 2, 3))
        if core != minimum:
            return core > minimum
        # A prerelease has lower precedence than the corresponding stable floor.
        return match.group(4) is None

    @staticmethod
    def _browser_companion_full_sync_gate(
        handshake: Mapping[str, Any] | None,
        *,
        current_extension_version: str,
        enabled_providers=None,
        site_profile_sha256=None,
    ) -> dict[str, Any]:
        minimum_version = (3, 5, 0)
        report_version = handshake.get("extension_version") if handshake else None
        version_eligible = ProductApi._browser_companion_semver_at_least(
            report_version,
            minimum_version,
        )
        identity_matches = bool(
            handshake
            and report_version == current_extension_version
        )
        report_contract_matches = bool(
            handshake
            and version_eligible
            and identity_matches
            and handshake.get("sync_mode") == "PREFLIGHT"
            and handshake.get("per_provider_limit") == 2
            and (site_profile_sha256 is None or handshake.get('site_profile_sha256') == site_profile_sha256)
        )
        report_complete = bool(
            report_contract_matches and handshake.get("status") == "COMPLETE"
        )
        provider_rows = handshake.get("providers", {}) if handshake else {}
        providers: dict[str, dict[str, Any]] = {}
        for provider in (enabled_providers if enabled_providers is not None else ("deepseek", "gemini", "kimi")):
            row = provider_rows.get(provider, {}) if isinstance(provider_rows, Mapping) else {}
            captured_count = row.get("captured_count", 0)
            if not isinstance(captured_count, int) or captured_count < 0:
                captured_count = 0
            failed_count = row.get("failed_count", 0)
            if not isinstance(failed_count, int) or failed_count < 0:
                failed_count = 0
            diagnostic = row.get("safe_diagnostic")
            diagnostic_text = (
                json.dumps(diagnostic, ensure_ascii=True, sort_keys=True)
                if isinstance(diagnostic, Mapping)
                else ""
            )
            human_verification = "HUMAN_VERIFICATION" in (
                f"{row.get('error_code', '')} {diagnostic_text}".upper()
            )
            provider_passes = bool(
                report_contract_matches
                and captured_count > 0
                and failed_count == 0
                and not human_verification
            )
            eligible = report_complete and provider_passes
            providers[provider] = {
                "captured_count": captured_count,
                "failed_count": failed_count,
                "human_verification_detected": human_verification,
                "eligible": eligible,
                "status": (
                    "HUMAN_VERIFICATION_BLOCKED"
                    if human_verification
                    else "PREFLIGHT_FAILED"
                    if report_contract_matches and failed_count > 0
                    else "PREFLIGHT_PASS"
                    if provider_passes
                    else "NO_CAPTURED_CONVERSATIONS"
                    if report_contract_matches
                    else "PREFLIGHT_REQUIRED"
                ),
            }
        eligible = bool(providers) and report_complete and all(row["eligible"] for row in providers.values())
        return {
            "minimum_extension_version": "3.5.0",
            "current_extension_version": current_extension_version,
            "report_extension_version": report_version,
            "version_eligible": version_eligible,
            "identity_matches": identity_matches,
            "report_sync_mode": handshake.get("sync_mode") if handshake else None,
            "report_status": handshake.get("status") if handshake else None,
            "report_per_provider_limit": (
                handshake.get("per_provider_limit") if handshake else None
            ),
            "report_generated_at": handshake.get("generated_at") if handshake else None,
            "report_sha256": handshake.get("sha256") if handshake else None,
            "providers": providers,
            "eligible": eligible,
        }

    def _browser_companion_incremental_cursor(self) -> dict[str, Any]:
        """Build a privacy-safe cursor from the local three-provider archive."""

        session_controller = getattr(self, "_sessions", None)
        archive = getattr(session_controller, "_session_archive", None)
        provider_ids = [row['provider_id'] for row in self._browser_enabled_sites()]
        empty_counts = {provider_id: 0 for provider_id in provider_ids}
        if archive is None or not hasattr(archive, "list_sessions"):
            return {
                "schema_version": "DesktopBrowserCompanionIncrementalCursorProjection-v1",
                "provider_counts": empty_counts,
                "known_session_count": 0,
                "overlap_fingerprint_count": 0,
                "eligible": False,
                "raw_session_ids_in_token": False,
                "raw_private_content_in_token": False,
            }
        try:
            cursor_module = importlib.import_module(
                "memorive_browser_companion.incremental_cursor"
            )
            provider_counts = {
                provider_id: len(
                    archive.list_sessions(
                        provider=provider_id,
                        limit=50,
                        search="",
                    )
                )
                for provider_id in provider_ids
            }
            if not provider_ids or not all(provider_counts.values()):
                return {
                    "schema_version": "DesktopBrowserCompanionIncrementalCursorProjection-v1",
                    "provider_counts": provider_counts,
                    "known_session_count": sum(provider_counts.values()),
                    "overlap_fingerprint_count": 0,
                    "eligible": False,
                    "raw_session_ids_in_token": False,
                    "raw_private_content_in_token": False,
                }
            built = cursor_module.build_incremental_cursor(archive, providers=provider_ids)
        except (ImportError, AttributeError, OSError, RuntimeError, ValueError):
            built = None
        if not built:
            return {
                "schema_version": "DesktopBrowserCompanionIncrementalCursorProjection-v1",
                "provider_counts": empty_counts,
                "known_session_count": 0,
                "overlap_fingerprint_count": 0,
                "eligible": False,
                "raw_session_ids_in_token": False,
                "raw_private_content_in_token": False,
            }
        cursor_payload = built["payload"]
        cursor_providers = cursor_payload["providers"]
        provider_counts = {
            provider_id: len(row["known_session_hashes"])
            for provider_id, row in cursor_providers.items()
        }
        cursor_token = built["token"]
        return {
            "schema_version": "DesktopBrowserCompanionIncrementalCursorProjection-v1",
            "cursor_schema_version": cursor_payload["schema_version"],
            "providers": cursor_providers,
            "provider_counts": provider_counts,
            "known_session_count": built["known_session_count"],
            "overlap_fingerprint_count": built["overlap_fingerprint_count"],
            "cursor_token": cursor_token,
            "cursor_token_sha256": hashlib.sha256(
                cursor_token.encode("ascii")
            ).hexdigest().upper(),
            "eligible": True,
            "raw_session_ids_in_token": False,
            "raw_private_content_in_token": False,
        }

    def _refresh_prepared_browser_companion_package(self) -> dict[str, Any]:
        """Refresh an already prepared unpacked extension without resetting setup."""
        state_path = self._browser_companion_state_path()
        if not state_path.is_file():
            return {"updated": False, "status": "NOT_PREPARED"}
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"updated": False, "status": "STATE_UNAVAILABLE"}
        if not isinstance(state, dict) or not state.get("prepared"):
            return {"updated": False, "status": "NOT_PREPARED"}
        source, required, package_sha256 = self._browser_companion_package()
        current_version = self._browser_companion_manifest_projection()[
            "extension_version"
        ]
        prepared_path = state.get("extension_path")
        destination = (
            Path(prepared_path) if isinstance(prepared_path, str) and prepared_path
            else self._profile_path("BROWSER_COMPANION") / "extension-v1"
        ).resolve()
        self._check_browser_companion_destination(destination)
        short_destination = self._browser_companion_export_root() / (browser_family(state.get('preferred_browser') or state.get('connected_browser')) or 'browser').lower()
        path_changed = destination != short_destination
        if path_changed:
            destination = short_destination
        installed_version = self._browser_companion_installed_version(destination)
        if installed_version is None:
            prior_state_version = state.get("package_extension_version")
            installed_version = (
                prior_state_version.strip()
                if isinstance(prior_state_version, str)
                and prior_state_version.strip()
                else None
            )
        version_changed = bool(
            installed_version and installed_version != current_version
        )
        capture_repair_required = bool(
            state.get("capture_repair_preflight_required") or version_changed
            or state.get('site_profile_sha256') != self.browser_companion_sites()['profile_sha256']
        )
        if (
            state.get("package_sha256") == package_sha256
            and installed_version == current_version
            and not path_changed
        ):
            return {
                "updated": False,
                "capture_repair_preflight_required": capture_repair_required,
                "status": "CURRENT",
            }
        destination.mkdir(parents=True, exist_ok=True)
        for name in sorted(required):
            shutil.copy2(source / name, destination / name)
        state["package_sha256"] = package_sha256
        if path_changed:
            state.update(extension_path=str(destination), path_changed=True,
                         prepared_at_unix=int(time.time()), extension_id=None,
                         extension_connected=False, connected_browser=None)
            for key in ('identity_receipt_sha256', 'identity_observed_at'):
                state.pop(key, None)
        state['site_profile_sha256'] = self.browser_companion_sites()['profile_sha256']
        # The files on disk are now current, but Chromium keeps the already
        # loaded service worker and runner until the extension/browser reloads.
        state["browser_reload_required"] = True
        state["package_updated_at_unix"] = int(time.time())
        state["package_extension_version"] = current_version
        state["capture_repair_preflight_required"] = capture_repair_required
        if capture_repair_required:
            state["capture_repair_target_version"] = current_version
        self._write_json_file(state_path, state)
        return {
            "updated": True,
            "browser_reload_required": state["browser_reload_required"],
            "capture_repair_preflight_required": capture_repair_required,
            "status": "UPDATED",
        }

    def model_dashboard(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read-only bottom panel; no model execution or metadata refresh."""
        request = request or {}
        if not isinstance(request, dict) or set(request) - {"view"} or request.get("view") not in {"ranking", "billing"}:
            return {"status": "ERROR", "reason": "DASHBOARD_REQUEST_INVALID"}
        try:
            module = importlib.import_module("memorive_settings.model_dashboard")
            exam_root = self._profile_path("MODEL_VALIDATION")
            if request["view"] == "billing":
                data = module.billing_projection(exam_root, self._profile_path("Core_JOBS"))
            else:
                projection = self._service.call("settings.get_state", {})
                history_module = importlib.import_module("memorive_settings.exam_score_history")
                history = history_module.ExamScoreHistory(exam_root / "workflow_exam_scores").rows()
                external_root = self._profile_path("SETTINGS") / "external_data"
                external = {"rows": [], "unavailable_sources": []}
                if external_root.is_dir():
                    try:
                        sources_module = importlib.import_module("memorive_settings.external_sources")
                        external = sources_module.ExternalDataSources(external_root, environment={}).public_catalog()
                    except (OSError, ValueError, TypeError, KeyError):
                        # A corrupt external cache must not hide validated exams.
                        external["unavailable_sources"] = ["EXTERNAL_CACHE_UNAVAILABLE"]
                data = module.ranking_projection(projection.get("workflow_exam_score_catalog") or {}, history, external["rows"])
                data["unavailable_sources"] = external["unavailable_sources"]
            return {"status": "PASS", "data": data}
        except Exception:
            # Provider strings, paths and raw records must not become UI errors.
            return {"status": "ERROR", "reason": "DASHBOARD_READ_FAILED"}

    def browser_companion_status(self) -> dict[str, Any]:
        _source, _required, package_sha256 = self._browser_companion_package()
        identity = self._browser_companion_manifest_projection()
        path = self._browser_companion_state_path()
        state: dict[str, Any] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state = loaded
            except (OSError, json.JSONDecodeError):
                state = {}
        package_current = state.get("package_sha256") == package_sha256
        prepared = bool(state.get("prepared"))
        user_confirmed = bool(state.get("user_confirmed"))
        import_verified = bool(state.get("import_verified"))
        freshness_values = [
            state.get("prepared_at_unix"),
            state.get("package_updated_at_unix"),
            state.get("bridge_reload_requested_at_unix"),
        ]
        # Legacy setup files did not carry a preparation timestamp.  Never let
        # an unrelated, older report already present in Downloads impersonate
        # a fresh extension handshake for a newly created profile; the setup
        # state file's own mtime is the conservative lower bound in that case.
        try:
            state_file_mtime = int(path.stat().st_mtime)
        except OSError:
            state_file_mtime = None
        freshness_floor = max(
            (value for value in freshness_values if isinstance(value, int)),
            default=state_file_mtime,
        )
        identity_receipt = self._latest_browser_companion_identity_receipt(
            current_extension_version=identity["extension_version"],
            expected_extension_id=(
                state.get("extension_id")
                if self._browser_companion_extension_id_valid(
                    state.get("extension_id")
                )
                else None
            ),
            prepared_at_unix=freshness_floor,
            expected_browser=browser_family(state.get("preferred_browser")),
        )
        report_identity = {
            "expected_browser": browser_family(state.get("preferred_browser")),
            "expected_extension_id": (identity_receipt or {}).get("extension_id"),
        }
        latest_report = self._latest_browser_companion_sync_report(
            prepared_at_unix=freshness_floor, **report_identity
        )
        latest_preflight_report = self._latest_browser_companion_sync_report(
            prepared_at_unix=freshness_floor,
            expected_sync_mode="PREFLIGHT", **report_identity,
        )
        bridge_identity_verified = bool(identity_receipt and package_current)
        handshake = (
            latest_report
            if (
                bridge_identity_verified
                and latest_report
                and latest_report.get("extension_id")
                == identity_receipt.get("extension_id")
                and latest_report.get("extension_version")
                == identity_receipt.get("extension_version")
            )
            else None
        )
        preflight_handshake = (
            latest_preflight_report
            if (
                bridge_identity_verified
                and latest_preflight_report
                and latest_preflight_report.get("extension_id")
                == identity_receipt.get("extension_id")
                and latest_preflight_report.get("extension_version")
                == identity_receipt.get("extension_version")
            )
            else None
        )
        full_sync_gate = self._browser_companion_full_sync_gate(
            preflight_handshake,
            current_extension_version=identity["extension_version"],
            enabled_providers=[row['provider_id'] for row in self._browser_enabled_sites()],
            site_profile_sha256=(self.browser_companion_sites()['profile_sha256']
                if self._browser_companion_semver_at_least(identity['extension_version'],(3,6,0)) else None),
        )
        preflight_passed = bool(full_sync_gate["eligible"])
        preflight_status = (
            "PASS"
            if preflight_passed
            else "FAIL"
            if preflight_handshake
            else "NOT_RUN"
        )
        if state.get("sync_requested_mode") in {'PREFLIGHT','INCREMENTAL','FULL'}:
            requested_handshake = preflight_handshake if state['sync_requested_mode']=='PREFLIGHT' else handshake
            previous_report_sha256 = state.get(
                "sync_requested_previous_report_sha256"
            )
            current_report_sha256 = (
                requested_handshake.get("sha256")
                if requested_handshake and requested_handshake.get('sync_mode') == state['sync_requested_mode']
                else None
            )
            if (
                not current_report_sha256
                or current_report_sha256 == previous_report_sha256
            ):
                if state['sync_requested_mode']=='PREFLIGHT': preflight_status = "RUNNING"
            else:
                state.pop("sync_requested_mode", None)
                state.pop("sync_requested_at_unix", None)
                state.pop("sync_requested_previous_report_sha256", None)
        incremental_cursor = self._browser_companion_incremental_cursor()
        extension_connected = bridge_identity_verified
        configured = extension_connected
        capture_repair_required = bool(
            state.get("capture_repair_preflight_required")
        )
        capture_repair_target = state.get("capture_repair_target_version")
        capture_repair_completed = bool(
            capture_repair_required
            and capture_repair_target == identity["extension_version"]
            and bridge_identity_verified
            and full_sync_gate["eligible"]
        )
        if capture_repair_completed:
            capture_repair_required = False
            state.update(
                {
                    "capture_repair_preflight_required": False,
                    "capture_repair_completed_version": identity[
                        "extension_version"
                    ],
                    "capture_repair_report_sha256": full_sync_gate[
                        "report_sha256"
                    ],
                    "capture_repair_completed_at_unix": int(time.time()),
                }
            )
        if capture_repair_required:
            full_sync_gate = {
                **full_sync_gate,
                "eligible": False,
                "capture_repair_preflight_required": True,
            }
        incremental_sync_eligible = bool(
            incremental_cursor["eligible"] and not capture_repair_required
        )
        browser_reload_required = bool(
            prepared
            and package_current
            and (
                state.get("browser_reload_required")
                or not bridge_identity_verified
            )
        )
        if extension_connected:
            state.update(
                {
                    "extension_connected": True,
                    "extension_id": identity_receipt["extension_id"],
                    "identity_receipt_sha256": identity_receipt["sha256"],
                    "identity_observed_at": identity_receipt["observed_at"],
                    "connected_browser": identity_receipt.get("browser_family") or state.get("connected_browser"),
                    "browser_reload_required": False,
                }
            )
            if handshake:
                state.update(
                    {
                        "connected_browser": handshake["browser_family"],
                        "last_sync_report_sha256": handshake["sha256"],
                        "last_sync_report_generated_at": handshake["generated_at"],
                    }
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            self._write_json_file(path, state)
        return {
            "schema_version": "DesktopBrowserCompanionStatus-v3",
            "manual_import_available": True,
            "manual_capture_available": bool(prepared and package_current),
            "manual_import_observed": bool(import_verified or incremental_cursor["known_session_count"]),
            "automatic_sync_connection": "VERIFIED" if bridge_identity_verified else "UNCONFIRMED",
            "prepared": prepared,
            "extension_path": state.get("extension_path"),
            "path_changed": state.get("path_changed", False),
            "user_confirmed": user_confirmed,
            "import_verified": import_verified,
            "extension_connected": extension_connected,
            "bridge_identity_verified": bridge_identity_verified,
            "preflight_eligible": bool(
                prepared and package_current and bridge_identity_verified and self._browser_enabled_sites()
            ),
            "configured": configured,
            "package_current": package_current,
            "update_available": prepared and not package_current,
            "bridge_reload_required": False if extension_connected else browser_reload_required,
            "browser_reload_required": False if extension_connected else browser_reload_required,
            "providers": [row['provider_id'] for row in self._browser_enabled_sites()],
            "site_settings": self.browser_companion_sites(),
            "provider_sync": handshake["providers"] if handshake else {},
            "preflight_status": preflight_status,
            "preflight_report_sha256": (
                preflight_handshake.get("sha256")
                if preflight_handshake
                else None
            ),
            "latest_sync_mode": handshake.get("sync_mode") if handshake else None,
            "latest_per_provider_limit": (
                handshake.get("per_provider_limit") if handshake else None
            ),
            "latest_sync_report_sha256": (
                handshake.get("sha256") if handshake else None
            ),
            "next_sync_mode": (
                "INCREMENTAL" if incremental_sync_eligible else "PREFLIGHT"
            ),
            "incremental_sync_eligible": incremental_sync_eligible,
            "capture_repair_preflight_required": capture_repair_required,
            "capture_repair_target_version": capture_repair_target,
            "archive_provider_counts": incremental_cursor["provider_counts"],
            "incremental_known_session_count": incremental_cursor["known_session_count"],
            "incremental_overlap_fingerprint_count": incremental_cursor[
                "overlap_fingerprint_count"
            ],
            "full_sync_eligible": full_sync_gate["eligible"],
            "full_sync_gate": full_sync_gate,
            "connected_browser": state.get("connected_browser"),
            "selected_browser": browser_family(state.get("preferred_browser")) or browser_family(state.get("connected_browser")),
            "browser_options": self._browser_options(),
            "extension_version": identity["extension_version"],
            "identity_extension_version": (
                identity_receipt["extension_version"] if identity_receipt else None
            ),
            "report_extension_version": (
                latest_report["extension_version"] if latest_report else None
            ),
            "identity_receipt_sha256": (
                identity_receipt["sha256"] if identity_receipt else None
            ),
            "identity_observed_at": (
                identity_receipt["observed_at"] if identity_receipt else None
            ),
            "extension_id": (
                identity_receipt["extension_id"]
                if identity_receipt
                else state.get("extension_id")
            ),
            "cookie_permission": False,
            "browser_storage_read": False,
            "hidden_provider_api": False,
            "status": (
                "EXTENSION_IDENTITY_VERIFIED"
                if configured
                else "PACKAGE_UPDATE_REQUIRED"
                if prepared and not package_current
                else "AWAITING_EXTENSION_IDENTITY"
                if prepared and package_current
                else "NOT_CONFIGURED"
            ),
        }

    @staticmethod
    def _browser_executables() -> list[tuple[str, Path, str]]:
        return discover_browsers()

    def _browser_options(self) -> list[dict[str, Any]]:
        available = {browser_family(row[0]) for row in self._browser_executables()}
        return [{"id": key, "name": label, "installed": key in available}
                for key, label, _exe, _manager, _suffix in BROWSERS]

    def _launch_browser_companion_reload(
        self, preferred: str | None = None
    ) -> dict[str, Any]:
        """Open the public HTTPS marker consumed only by bridge 3.3.9+."""

        sites = self._browser_enabled_sites()
        trigger_url = (self._browser_sync_trigger('memorive-browser-companion-reload-v1')
                       .replace('?memorive_bridge_sync=v1#', '?memorive_bridge_reload=v1#')) if sites else None
        browsers = self._browser_executables()
        if not browsers:
            return {
                "schema_version": "DesktopBrowserCompanionReloadLaunchReceipt-v1",
                "selected_browser": None,
                "trigger_transport": "HTTPS_APPROVED_FRAGMENT_RELOAD",
                "trigger_url": trigger_url,
                "process_id": None,
                "status": "SUPPORTED_BROWSER_NOT_FOUND",
            }
        selected = select_browser(browsers, preferred)
        selected_browser, executable, _manager_url = selected
        # All sites disabled: use only the browser's local extension manager.
        trigger_url = trigger_url or _manager_url
        process_id = None
        if self._system_effects_enabled and os.name == "nt":
            process = subprocess.Popen(
                [str(executable), trigger_url], shell=False, close_fds=True
            )
            process_id = process.pid
        return {
            "schema_version": "DesktopBrowserCompanionReloadLaunchReceipt-v1",
            "selected_browser": selected_browser,
            "trigger_transport": "HTTPS_APPROVED_FRAGMENT_RELOAD",
            "trigger_url": trigger_url,
            "process_id": process_id,
            "status": (
                "BRIDGE_RELOAD_TRIGGERED" if process_id else "BRIDGE_RELOAD_READY"
            ),
        }

    def install_browser_companion(self, preferred_browser: str | None = None) -> dict[str, Any]:
        if not self._browser_enabled_sites():
            return {"status": "BROWSER_NO_VERIFIED_OFFICIAL_SITES", "prepared": False}
        source, required, package_sha256 = self._browser_companion_package()
        setup_path = self._browser_companion_state_path()
        try:
            prior_selection = json.loads(setup_path.read_text(encoding="utf-8")) if setup_path.is_file() else {}
        except (OSError, ValueError):
            prior_selection = {}
        if not isinstance(prior_selection, dict):
            prior_selection = {}
        target = preferred_browser or prior_selection.get("preferred_browser") or browser_family(prior_selection.get("connected_browser"))
        browsers = self._browser_executables()
        selected = select_browser(browsers, target) if browsers or target else None
        selected_family = browser_family(selected[0]) if selected else None
        switched_browser = bool(selected_family and selected_family != browser_family(prior_selection.get("preferred_browser") or prior_selection.get("connected_browser")))
        destination = self._browser_companion_export_root() / (selected_family.lower() if selected_family else "browser")
        prior_path = prior_selection.get("extension_path")
        path_changed = bool(prior_path and Path(prior_path).resolve() != destination.resolve())
        # Relocation changes Chromium's unpacked extension ID; require a fresh
        # load/receipt instead of accepting an old path's identity.
        switched_browser = switched_browser or path_changed
        self._check_browser_companion_destination(destination)
        prior_destination_version = self._browser_companion_installed_version(
            destination
        )
        destination.mkdir(parents=True, exist_ok=True)
        for name in sorted(required):
            shutil.copy2(source / name, destination / name)
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("manifest_version") != 3 or "cookies" in manifest.get("permissions", []):
            raise RuntimeError("BROWSER_COMPANION_MANIFEST_BOUNDARY_INVALID")
        setup_path = self._browser_companion_state_path()
        prior_state: dict[str, Any] = {}
        setup_path_io = windows_io_path(setup_path)
        if setup_path_io.is_file():
            try:
                loaded = json.loads(setup_path_io.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    prior_state = loaded
            except (OSError, json.JSONDecodeError):
                prior_state = {}
        prior_hashes = prior_state.get("imported_bundle_sha256s", [])
        if not isinstance(prior_hashes, list):
            prior_hashes = []
        if switched_browser:
            for key in ("extension_id", "connected_browser", "identity_receipt_sha256", "identity_observed_at", "sync_requested_mode", "sync_requested_at_unix", "sync_requested_previous_report_sha256"):
                prior_state.pop(key, None)
        prior_prepared = bool(prior_state.get("prepared") and not switched_browser)
        prior_connected_browser = prior_state.get("connected_browser")
        prior_extension_id = prior_state.get("extension_id")
        current_version = manifest.get("version")
        if not isinstance(current_version, str) or not current_version.strip():
            raise RuntimeError("BROWSER_COMPANION_MANIFEST_VERSION_MISSING")
        current_version = current_version.strip()
        prior_state_version = prior_state.get("package_extension_version")
        prior_version = prior_destination_version or (
            prior_state_version.strip()
            if isinstance(prior_state_version, str) and prior_state_version.strip()
            else None
        )
        package_unchanged = bool(
            prior_prepared
            and prior_destination_version == current_version
            and prior_state.get("package_sha256") == package_sha256
        )
        version_changed = bool(prior_version and prior_version != current_version)
        package_changed = bool(prior_prepared and not package_unchanged)
        capture_repair_required = bool(
            prior_state.get("capture_repair_preflight_required")
            or version_changed
            or package_changed
        )
        if prior_connected_browser not in FAMILIES:
            prior_connected_browser = None
        if not self._browser_companion_extension_id_valid(prior_extension_id):
            prior_extension_id = None
        current_identity = self._latest_browser_companion_identity_receipt(
            current_extension_version=current_version,
            expected_extension_id=prior_extension_id,
            expected_browser=selected_family,
            prepared_at_unix=max((value for value in (
                prior_state.get("prepared_at_unix"),
                prior_state.get("package_updated_at_unix"),
                prior_state.get("bridge_reload_requested_at_unix"),
            ) if isinstance(value, int)), default=None),
        )
        if path_changed:
            current_identity = None
        if prior_prepared and prior_extension_id is None and current_identity:
            prior_extension_id = current_identity["extension_id"]
        bridge_identity_current = bool(
            current_identity
            and current_identity.get("extension_id") == prior_extension_id
        )
        auto_reload_eligible = bool(
            prior_prepared
            and self._browser_companion_extension_id_valid(prior_extension_id)
        )
        reload_needed = bool(
            auto_reload_eligible
            and (not package_unchanged or not bridge_identity_current)
        )
        now = int(time.time())
        prepared_at_unix = (
            prior_state.get("prepared_at_unix")
            if package_unchanged
            and isinstance(prior_state.get("prepared_at_unix"), int)
            else now
        )
        next_state = {
            **prior_state,
            "schema_version": "DesktopBrowserCompanionSetupState-v1",
            "package_sha256": package_sha256,
            "prepared": True,
            "user_confirmed": bool(prior_state.get("user_confirmed")),
            "extension_connected": bridge_identity_current,
            "import_verified": bool(prior_state.get("import_verified")),
            "imported_bundle_sha256s": [
                value for value in prior_hashes if isinstance(value, str) and value
            ][-256:],
            "browser_reload_required": reload_needed,
            "prepared_at_unix": prepared_at_unix,
            "path_changed": path_changed,
            "connected_browser": prior_connected_browser,
            "preferred_browser": selected_family,
            "extension_path": str(destination),
            "extension_id": prior_extension_id,
            "package_extension_version": current_version,
            "capture_repair_preflight_required": capture_repair_required,
            "capture_repair_target_version": (
                current_version if capture_repair_required else None
            ),
        }
        if package_changed:
            next_state["package_updated_at_unix"] = now
        self._write_json_file(
            setup_path,
            next_state,
        )
        folder_opened = False
        browser_manager_opened = False
        selected_browser = None
        reload_receipt: dict[str, Any] | None = None
        if self._system_effects_enabled and os.name == "nt":
            if reload_needed:
                reload_receipt = self._launch_browser_companion_reload(
                    selected_family
                )
                selected_browser = reload_receipt.get("selected_browser")
                refreshed_state = json.loads(setup_path.read_text(encoding="utf-8"))
                refreshed_state["bridge_reload_requested_at_unix"] = int(time.time())
                refreshed_state["bridge_reload_requested_for"] = selected_browser
                self._write_json_file(setup_path, refreshed_state)
            elif not (package_unchanged and bridge_identity_current):
                subprocess.Popen(["explorer.exe", str(destination)], shell=False, close_fds=True)
                folder_opened = True
                if selected:
                    selected_browser, executable, manager_url = selected
                    subprocess.Popen([str(executable), manager_url], shell=False, close_fds=True)
                    browser_manager_opened = True
        return {
            "schema_version": "DesktopBrowserCompanionPreparationReceipt-v1",
            "extension_path": str(destination),
            "folder_opened": folder_opened,
            "browser_manager_opened": browser_manager_opened,
            "selected_browser": selected_browser,
            "supported_browsers": [row[1] for row in BROWSERS],
            "selected_browser_family": selected_family,
            "providers": [row['provider_id'] for row in self._browser_enabled_sites()],
            "package_unchanged": package_unchanged,
            "already_configured": bool(
                package_unchanged and bridge_identity_current
            ),
            "manual_load_required": not auto_reload_eligible,
            "path_changed": path_changed,
            "cookie_permission": False,
            "browser_storage_read": False,
            "hidden_provider_api": False,
            "one_time_setup": True,
            "extension_id": prior_extension_id,
            "bridge_reload_requested": bool(
                reload_receipt and reload_receipt.get("process_id")
            ),
            "capture_repair_preflight_required": capture_repair_required,
            "reload_receipt": reload_receipt,
            "status": (
                reload_receipt["status"]
                if reload_receipt
                else "ALREADY_CONFIGURED"
                if package_unchanged and bridge_identity_current
                else "AWAITING_EXTENSION_LOAD"
                if not auto_reload_eligible
                else "AWAITING_EXTENSION_IDENTITY"
            ),
        }

    def confirm_browser_companion_setup(self) -> dict[str, Any]:
        status = self.browser_companion_status()
        if not status["prepared"]:
            raise RuntimeError("BROWSER_COMPANION_NOT_PREPARED")
        if not status["extension_connected"]:
            raise RuntimeError("BROWSER_COMPANION_HANDSHAKE_NOT_FOUND")
        return {**status, "schema_version": "DesktopBrowserCompanionConfirmationReceipt-v2"}

    def reload_browser_companion(self) -> dict[str, Any]:
        """Reload the unpacked bridge without starting any provider sync."""

        status = self.browser_companion_status()
        if not status["prepared"]:
            raise RuntimeError("BROWSER_COMPANION_NOT_PREPARED")
        path = self._browser_companion_state_path()
        state = json.loads(path.read_text(encoding="utf-8"))
        state["browser_reload_required"] = True
        state["extension_connected"] = False
        state["bridge_reload_requested_at_unix"] = int(time.time())
        preferred = status.get("selected_browser") or status.get("connected_browser")
        self._write_json_file(path, state)
        receipt = self._launch_browser_companion_reload(preferred)
        if receipt["status"] == "SUPPORTED_BROWSER_NOT_FOUND":
            raise RuntimeError("SUPPORTED_BROWSER_NOT_FOUND")
        state["bridge_reload_requested_for"] = receipt["selected_browser"]
        self._write_json_file(path, state)
        return {
            **receipt,
            "expected_extension_version": status["extension_version"],
            "bridge_identity_verified": False,
            "browser_reload_required": True,
        }

    def run_browser_companion_reload(self) -> dict[str, Any]:
        """UI-facing alias for the explicit bridge self-reload action."""

        return self.reload_browser_companion()

    @staticmethod
    def _browser_companion_sync_window_contract() -> dict[str, Any]:
        """Describe the extension-owned window boundary for every sync launch."""

        return {
            "window_mode": "DEDICATED_VISIBLE",
            "dedicated_window_focused": False,
            "user_tab_reused": False,
            "user_tabs_queried": False,
            "user_tabs_closed": False,
            "maximum_capture_windows": 1,
            "per_provider_capture_mode": (
                "DEDICATED_VISIBLE_WINDOW_OWNED_TABS"
            ),
            "trigger_tab_created_once": True,
            "trigger_tab_lifecycle": (
                "EXTENSION_MIGRATES_THEN_CLOSES_TRIGGER_ONLY"
            ),
        }

    def _launch_browser_companion_sync_fragment(
        self,
        status: Mapping[str, Any],
        *,
        trigger_url: str,
        sync_mode: str,
        per_provider_limit: int,
        trigger_transport: str,
        incremental_cursor: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        browsers = self._browser_executables()
        if not browsers:
            raise RuntimeError("SUPPORTED_BROWSER_NOT_FOUND")
        preferred = status.get("selected_browser") or status.get("connected_browser")
        selected = select_browser(browsers, preferred)
        selected_browser, executable, _manager_url = selected
        process_id = None
        if self._system_effects_enabled and os.name == "nt":
            process = subprocess.Popen(
                [str(executable), trigger_url], shell=False, close_fds=True
            )
            process_id = process.pid
        receipt = {
            "schema_version": "DesktopBrowserCompanionSyncLaunchReceipt-v2",
            "selected_browser": selected_browser,
            "extension_id": status["extension_id"],
            "trigger_transport": trigger_transport,
            "trigger_url": trigger_url,
            "sync_mode": sync_mode,
            "per_provider_limit": per_provider_limit,
            "providers": [row['provider_id'] for row in self._browser_enabled_sites()],
            "site_profile_sha256": self.browser_companion_sites()['profile_sha256'],
            "process_id": process_id,
            **self._browser_companion_sync_window_contract(),
            "status": (
                "FULL_SYNC_TRIGGERED"
                if process_id and sync_mode == "FULL"
                else "FULL_SYNC_READY"
                if sync_mode == "FULL"
                else "INCREMENTAL_SYNC_TRIGGERED"
                if process_id and sync_mode == "INCREMENTAL"
                else "INCREMENTAL_SYNC_READY"
                if sync_mode == "INCREMENTAL"
                else "SYNC_TRIGGERED"
                if process_id
                else "SYNC_READY"
            ),
        }
        if sync_mode == "INCREMENTAL" and incremental_cursor:
            receipt.update(
                {
                    "cursor_schema_version": incremental_cursor.get(
                        "cursor_schema_version"
                    ),
                    "cursor_token_sha256": incremental_cursor.get(
                        "cursor_token_sha256"
                    ),
                    "known_session_count": incremental_cursor.get(
                        "known_session_count"
                    ),
                    "overlap_fingerprint_count": incremental_cursor.get(
                        "overlap_fingerprint_count"
                    ),
                    "archive_provider_counts": incremental_cursor.get(
                        "provider_counts"
                    ),
                    "raw_session_ids_in_trigger": False,
                    "raw_private_content_in_trigger": False,
                }
            )
        if process_id and hasattr(self, "_profile_root"):
            state_path = self._browser_companion_state_path()
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {}
            if isinstance(state, dict):
                state["sync_requested_mode"] = sync_mode
                state["sync_requested_at_unix"] = int(time.time())
                state["sync_requested_previous_report_sha256"] = status.get(
                    "latest_sync_report_sha256"
                )
                self._write_json_file(state_path, state)
        return receipt

    def run_browser_companion_test_read(self) -> dict[str, Any]:
        return self.run_browser_companion_sync('PREFLIGHT')

    def _browser_sync_trigger(self, suffix):
        rows = self._browser_enabled_sites()
        if not rows: raise ValueError('BROWSER_NO_ENABLED_SITES')
        entry = next((row['url'] for row in rows if row['provider_id']=='gemini'), rows[0]['url'])
        # No disabled provider page is opened even as a one-shot trigger.
        return entry + '?memorive_bridge_sync=v1#' + suffix

    def run_browser_companion_sync(self, requested_mode=None) -> dict[str, Any]:
        """Launch preflight first, then a privacy-safe bounded incremental sync."""

        status = self.browser_companion_status()
        if not self._browser_enabled_sites():
            return {'status':'BROWSER_NO_ENABLED_SITES','sync_mode':requested_mode or 'INCREMENTAL'}
        if requested_mode not in (None,'PREFLIGHT','INCREMENTAL'):
            raise ValueError('BROWSER_SYNC_MODE_INVALID')
        if not status["prepared"]:
            preparation = self.install_browser_companion()
            return {
                "schema_version": "DesktopBrowserCompanionSyncLaunchReceipt-v2",
                "extension_connected": False,
                "preparation": preparation,
                "sync_mode": "PREFLIGHT",
                "per_provider_limit": 2,
                **self._browser_companion_sync_window_contract(),
                "status": "PREPARATION_REQUIRED",
            }
        if (
            not status.get("bridge_identity_verified")
            or status.get("preflight_eligible") is not True
        ):
            return {
                "schema_version": "DesktopBrowserCompanionSyncLaunchReceipt-v3",
                "extension_connected": False,
                "bridge_identity_verified": False,
                "bridge_reload_required": bool(
                    status.get("bridge_reload_required")
                ),
                "expected_extension_version": status.get("extension_version"),
                "sync_mode": "PREFLIGHT",
                "per_provider_limit": 2,
                **self._browser_companion_sync_window_contract(),
                "status": "BRIDGE_IDENTITY_REQUIRED",
            }
        if requested_mode == 'INCREMENTAL' and status.get("capture_repair_preflight_required"):
            return {'status':'PREFLIGHT_REQUIRED','sync_mode':'INCREMENTAL'}
        if requested_mode == 'PREFLIGHT' or status.get("capture_repair_preflight_required"):
            return self._launch_browser_companion_sync_fragment(
                status,
                trigger_url=self._browser_sync_trigger('memorive-browser-session-preflight-2-v5'),
                sync_mode="PREFLIGHT",
                per_provider_limit=2,
                trigger_transport="HTTPS_APPROVED_FRAGMENT_PREFLIGHT",
            )
        incremental_cursor = self._browser_companion_incremental_cursor()
        if incremental_cursor["eligible"]:
            cursor_token = incremental_cursor["cursor_token"]
            return self._launch_browser_companion_sync_fragment(
                status,
                trigger_url=self._browser_sync_trigger(f'memorive-browser-session-incremental-v2:{cursor_token}'),
                sync_mode="INCREMENTAL",
                per_provider_limit=10,
                trigger_transport="HTTPS_APPROVED_FRAGMENT_INCREMENTAL",
                incremental_cursor=incremental_cursor,
            )
        if requested_mode == 'INCREMENTAL':
            return {'status':'INCREMENTAL_BASELINE_REQUIRED','sync_mode':'INCREMENTAL'}
        return self._launch_browser_companion_sync_fragment(
            status,
            trigger_url=self._browser_sync_trigger('memorive-browser-session-preflight-2-v5'),
            sync_mode="PREFLIGHT",
            per_provider_limit=2,
            trigger_transport="HTTPS_APPROVED_FRAGMENT_PREFLIGHT",
        )

    def run_browser_companion_full_sync(self) -> dict[str, Any]:
        """Launch full sync only after a current capability report passes every provider gate."""

        status = self.browser_companion_status()
        full_sync_gate = status.get("full_sync_gate", {})
        if (
            not status.get("prepared")
            or not status.get("extension_connected")
            or not status.get("bridge_identity_verified")
            or status.get("full_sync_eligible") is not True
        ):
            return {
                "schema_version": "DesktopBrowserCompanionSyncLaunchReceipt-v2",
                "extension_connected": bool(status.get("extension_connected")),
                "sync_mode": "FULL",
                "per_provider_limit": 50,
                "preflight_gate_passed": False,
                "full_sync_gate": full_sync_gate,
                "capture_repair_preflight_required": bool(
                    status.get("capture_repair_preflight_required")
                ),
                **self._browser_companion_sync_window_contract(),
                "status": "FULL_SYNC_PREFLIGHT_REQUIRED",
            }
        receipt = self._launch_browser_companion_sync_fragment(
            status,
            trigger_url=self._browser_sync_trigger('memorive-browser-session-full-v4'),
            sync_mode="FULL",
            per_provider_limit=50,
            trigger_transport="HTTPS_APPROVED_FRAGMENT_FULL",
        )
        receipt["preflight_gate_passed"] = True
        receipt["full_sync_gate"] = full_sync_gate
        return receipt

    def restart_browser_companion(self) -> dict[str, Any]:
        """Compatibility alias; reload only the unpacked extension service worker."""

        return self.reload_browser_companion()

    def api_call_readiness(self) -> dict[str, Any]:
        """Project pre-send readiness without resolving a credential or sending a request."""

        projection = self._service.call("settings.get_state", {})
        settings = projection.get("settings", {}) if isinstance(projection, Mapping) else {}
        raw_profiles = projection.get("api_profiles", []) if isinstance(projection, Mapping) else []
        if not isinstance(raw_profiles, list) or not raw_profiles:
            raw_profiles = settings.get("model_services", []) if isinstance(settings, Mapping) else []
        raw_references = settings.get("credential_references", []) if isinstance(settings, Mapping) else []
        reference_status = {
            str(value.get("credential_ref") or "").strip(): str(value.get("status") or "UNKNOWN")
            for value in raw_references
            if isinstance(value, Mapping) and str(value.get("credential_ref") or "").strip()
        }
        profiles: list[dict[str, Any]] = []
        for value in raw_profiles if isinstance(raw_profiles, list) else []:
            if not isinstance(value, Mapping):
                continue
            profile_ref = str(value.get("profile_ref") or value.get("config_id") or "").strip()
            model_name = str(value.get("display_name") or value.get("model_name") or "").strip()
            provider = str(value.get("provider") or value.get("provider_name") or "").strip()
            credential_ref = str(value.get("credential_ref") or "").strip()
            credential_status = reference_status.get(credential_ref, "UNKNOWN")
            if not profile_ref:
                continue
            blockers = []
            if not model_name:
                blockers.append("MODEL_NAME_MISSING")
            if credential_ref and credential_status not in {"STORED_UNVERIFIED", "STORED_VERIFIED"}:
                blockers.append("CREDENTIAL_REFERENCE_NOT_STORED")
            profiles.append(
                {
                    "profile_ref": profile_ref,
                    "provider": provider,
                    "model_name": model_name,
                    "tier": str(value.get("tier") or "").strip(),
                    "credential_reference_status": credential_status,
                    "connection_status": str(value.get("connection_status") or "UNVERIFIED"),
                    "configuration_ready": not blockers,
                    "blockers": blockers,
                }
            )
        ready = [row for row in profiles if row["configuration_ready"]]
        return {
            "schema_version": "DesktopApiCallReadinessProjection-v1",
            "configured_profile_count": len(profiles),
            "configuration_ready_count": len(ready),
            "profiles": profiles,
            "required_before_send": [
                "MODEL_GATEWAY route/profile binding",
                "requested and returned model evidence",
                "region and egress evidence",
                "token, cost and latency receipt",
                "low-cost rehearsal when required",
            ],
            "request_sent": False,
            "credential_value_read": False,
            "external_network_calls": 0,
            "external_model_calls": 0,
            "status": "READY_FOR_AUTHORIZED_PREFLIGHT" if ready else "BLOCKED_NO_CONFIGURED_MODEL",
        }

    def _browser_session_managed_root(self) -> Path:
        """Return the app-owned durable intake next to the user's Memorive state."""

        destination = (
            self._profile_path("BROWSER_COMPANION") / "session-library"
        ).resolve(strict=False)
        try:
            destination.relative_to(self._profile_root.resolve(strict=False))
        except ValueError as error:
            raise RuntimeError("BROWSER_SESSION_MANAGED_ROOT_ESCAPE") from error
        return destination

    def _retain_browser_session_artifact(
        self, source: Path, payload_bytes: bytes
    ) -> Path:
        """Keep a durable app-owned copy without overwriting a conflicting file."""

        managed_root = self._browser_session_managed_root()
        windows_io_path(managed_root).mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(payload_bytes).hexdigest().lower()
        destination = managed_root / source.name
        destination_io = windows_io_path(destination)
        if destination_io.is_file():
            if hashlib.sha256(destination_io.read_bytes()).hexdigest().lower() == digest:
                return destination
            destination = managed_root / f"{source.stem}-{digest[:12]}{source.suffix}"
            destination_io = windows_io_path(destination)
        if not destination_io.exists():
            temporary = managed_root / f".session.{uuid.uuid4().hex}.tmp"
            temporary_io = windows_io_path(temporary)
            temporary_io.write_bytes(payload_bytes)
            os.replace(temporary_io, destination_io)
        return destination

    def _browser_session_download_roots(self) -> list[Path]:
        """Resolve the app-owned intake first, then compatible browser downloads."""
        candidates: list[Path] = [self._browser_session_managed_root()]
        # A profile can consume its own intake without discovering other data.
        # Setup restores the legacy known-folder discovery only for the plugin
        # output subfolder. General Downloads remain outside automatic scope.
        # Explicit profile-scoped directory grants remain supported.
        setup = {}
        setup_path = self._browser_companion_state_path()
        if setup_path.is_file():
            try:
                value = json.loads(setup_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    setup = value
            except (OSError, ValueError):
                pass
        grants = setup.get("authorized_import_sources", [])
        if isinstance(grants, list):
            for grant in grants:
                if (isinstance(grant, dict) and grant.get("authorized") is True
                    and grant.get("kind") == "DIRECTORY" and
                    isinstance(grant.get("path"), str) and
                    Path(grant["path"]).is_absolute()):
                    path = Path(grant["path"])
                    path_io = windows_io_path(path)
                    if path_io.is_dir() and not (getattr(path_io.stat(),"st_file_attributes",0) & 0x400):
                        candidates.append(path)
        legacy_roots: list[Path] = []
        configured = os.environ.get("MEMORIVE_BROWSER_SESSION_IMPORT_DIR")
        if configured:
            legacy_roots.append(Path(configured).expanduser())
        else:
            legacy_roots.append(Path.home() / "Downloads")
            for environment_name in ("USERPROFILE", "OneDrive", "OneDriveConsumer"):
                if root := os.environ.get(environment_name):
                    legacy_roots.append(Path(root) / "Downloads")
            if os.name == "nt":
                try:
                    import winreg

                    with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
                    ) as key:
                        raw, _kind = winreg.QueryValueEx(
                            key, "{374DE290-123F-4565-9164-39C4925E467B}"
                        )
                        if isinstance(raw, str) and raw.strip():
                            legacy_roots.append(Path(os.path.expandvars(raw)).expanduser())
                except (OSError, ImportError):
                    pass
        if configured:
            candidates.extend(path for path in legacy_roots if path.is_absolute())
        elif setup.get("prepared") is True:
            candidates.extend(path / "Memorive会话桥接" for path in legacy_roots if path.is_absolute())
        resolved: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            try:
                value = candidate.resolve(strict=False)
                value_io = windows_io_path(value)
            except OSError:
                continue
            if value_io.is_dir() and value not in seen:
                seen.add(value)
                resolved.append(value)
        return resolved

    def sync_browser_sessions_from_downloads(self) -> dict[str, Any]:
        """Import browser bundles and retain them under Memorive managed state."""
        candidates: dict[Path, None] = {}
        reports: dict[Path, None] = {}
        for root in self._browser_session_download_roots():
            for folder in (root, root / "Memorive会话桥接"):
                folder_io = windows_io_path(folder)
                if not folder_io.is_dir():
                    continue
                for path_io in folder_io.glob("memorive-session-library-sync-report-*.json"):
                    if path_io.is_file():
                        reports[(folder / path_io.name).resolve(strict=False)] = None
                for path_io in folder_io.glob("memorive-session-library-*.json"):
                    if (
                        path_io.is_file()
                        and re.fullmatch(
                            r"memorive-session-library-(?:deepseek|gemini|kimi|universal|web-[a-f0-9]{16})-.+\.json",
                            path_io.name,
                            flags=re.IGNORECASE,
                        )
                    ):
                        candidates[(folder / path_io.name).resolve(strict=False)] = None

        setup_path = self._browser_companion_state_path()
        setup_state: dict[str, Any] = {}
        setup_path_io = windows_io_path(setup_path)
        if setup_path_io.is_file():
            try:
                loaded = json.loads(setup_path_io.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    setup_state = loaded
            except (OSError, json.JSONDecodeError):
                setup_state = {}
        prior_hashes = setup_state.get("imported_bundle_sha256s", [])
        imported_hashes = {
            value for value in prior_hashes if isinstance(value, str) and value
        }
        # Automatic intake belongs to this configured bridge, not every old
        # test export that happens to remain in the Downloads folder.
        intake_since = setup_state.get("prepared_at_unix") if setup_state.get("prepared") else None
        site_profile = setup_state.get("site_profile_sha256") if setup_state.get("prepared") else None
        ignored_files = 0
        imported_files = 0
        imported_sessions = 0
        retained_files: set[Path] = set()
        failures: list[dict[str, str]] = []
        for report in reports:
            try:
                report_bytes = windows_io_path(report).read_bytes()
                if 0 < len(report_bytes) <= 1024 * 1024:
                    retained_files.add(
                        self._retain_browser_session_artifact(report, report_bytes)
                    )
            except OSError:
                continue
        for source in sorted(candidates, key=lambda value: (windows_io_path(value).stat().st_mtime_ns, value.name)):
            try:
                source_io = windows_io_path(source)
                size = source_io.stat().st_size
                if size <= 0 or size > 128 * 1024 * 1024:
                    raise ValueError("BROWSER_SESSION_IMPORT_SIZE_INVALID")
                payload_bytes = source_io.read_bytes()
                digest = hashlib.sha256(payload_bytes).hexdigest().upper()
                if digest in imported_hashes:
                    continue
                payload = json.loads(payload_bytes.decode("utf-8-sig"))
                if isinstance(intake_since, int) or site_profile:
                    generated = payload.get("generated_at") if isinstance(payload, dict) else None
                    try:
                        generated_at = datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
                        current_capture = generated_at.tzinfo is not None and (
                            not isinstance(intake_since, int) or generated_at.timestamp() >= intake_since
                        )
                    except (TypeError, ValueError):
                        current_capture = False
                    if not current_capture or (site_profile and payload.get("site_profile_sha256") != site_profile):
                        ignored_files += 1
                        continue
                retained_files.add(
                    self._retain_browser_session_artifact(source, payload_bytes)
                )
                receipt = self._sessions.import_session_file(source)
                if receipt.get("status") != "PASS":
                    raise ValueError("BROWSER_SESSION_IMPORT_NOT_ACCEPTED")
                imported_hashes.add(digest)
                imported_files += 1
                imported_sessions += int(receipt.get("imported_count", 0))
                # Save each accepted file so interruption does not replay the batch.
                if setup_path_io.is_file():
                    latest_state = json.loads(setup_path_io.read_text(encoding="utf-8"))
                    if isinstance(latest_state, dict):
                        setup_state.update(latest_state)
                imported_hashes.update(setup_state.get("imported_bundle_sha256s", []))
                setup_state.update(import_verified=True,
                    imported_bundle_sha256s=sorted(imported_hashes)[-256:],
                    verified_at_unix=int(time.time()))
                self._write_json_file(setup_path, setup_state)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                failures.append(
                    {
                        "source_name": source.name,
                        "error_code": (
                            re.sub(
                                r"[^A-Z0-9_]+",
                                "_",
                                (str(error) or type(error).__name__).upper(),
                            ).strip("_")[:120]
                            or type(error).__name__.upper()
                        ),
                    }
                )

        if imported_files:
            _package, _required, package_sha256 = self._browser_companion_package()
            setup_state.update(
                {
                    "schema_version": "DesktopBrowserCompanionSetupState-v1",
                    "package_sha256": setup_state.get("package_sha256", package_sha256),
                    "prepared": bool(setup_state.get("prepared")),
                    "user_confirmed": False,
                    "import_verified": True,
                    "imported_bundle_sha256s": sorted(imported_hashes)[-256:],
                    "verified_at_unix": int(time.time()),
                }
            )
            windows_io_path(setup_path.parent).mkdir(parents=True, exist_ok=True)
            self._write_json_file(setup_path, setup_state)
        return {
            "schema_version": "DesktopBrowserSessionLibraryAutoImportReceipt-v1",
            "candidate_file_count": len(candidates),
            "ignored_historical_or_other_profile_count": ignored_files,
            "imported_file_count": imported_files,
            "imported_session_count": imported_sessions,
            "managed_artifact_count": len(retained_files),
            "managed_directory": str(self._browser_session_managed_root()),
            "failure_count": len(failures),
            "failures": failures,
            "remote_session_mutations": 0,
            "cookie_value_reads": 0,
            "browser_storage_value_reads": 0,
            "status": "PASS" if not failures else "PASS_WITH_REJECTED_FILES",
        }

    def import_browser_sessions_file(self) -> dict[str, Any]:
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        from webview import FileDialog
        managed = self._browser_session_managed_root()
        downloads = Path.home() / "Downloads"
        selected = self._window.create_file_dialog(
            FileDialog.OPEN,
            directory=str(
                managed
                if managed.is_dir()
                else downloads
                if downloads.is_dir()
                else self._effective_directory("workspace_root")
            ),
            allow_multiple=False,
            file_types=("JSON files (*.json)",),
        )
        if not selected:
            return {
                "schema_version": "DesktopBrowserSessionFileImportReceipt-v1",
                "status": "CANCELLED",
            }
        selected_path = selected if isinstance(selected, str) else selected[0]
        source = Path(selected_path).resolve(strict=False)
        source_io = windows_io_path(source)
        if not source_io.is_file() or source.suffix.casefold() != ".json":
            raise ValueError("BROWSER_SESSION_IMPORT_FILE_INVALID")
        size = source_io.stat().st_size
        if size <= 0 or size > 128 * 1024 * 1024:
            raise ValueError("BROWSER_SESSION_IMPORT_SIZE_INVALID")
        receipt = self._sessions.import_session_file(source)
        setup_path = self._browser_companion_state_path()
        if receipt.get("status") == "PASS":
            retained_path = self._retain_browser_session_artifact(
                source, source_io.read_bytes()
            )
            _package, _required, package_sha256 = self._browser_companion_package()
            setup_path.parent.mkdir(parents=True, exist_ok=True)
            setup_state = (
                json.loads(setup_path.read_text(encoding="utf-8"))
                if setup_path.is_file()
                else {
                    "schema_version": "DesktopBrowserCompanionSetupState-v1",
                    "package_sha256": package_sha256,
                    "prepared": False,
                    "prepared_at_unix": None,
                }
            )
            setup_state["package_sha256"] = package_sha256
            setup_state["import_verified"] = True
            setup_state["user_confirmed"] = False
            imported_hashes = setup_state.get("imported_bundle_sha256s", [])
            if not isinstance(imported_hashes, list):
                imported_hashes = []
            imported_hashes.append(hashlib.sha256(source_io.read_bytes()).hexdigest().upper())
            setup_state["imported_bundle_sha256s"] = sorted(
                {value for value in imported_hashes if isinstance(value, str) and value}
            )[-256:]
            setup_state["verified_at_unix"] = int(time.time())
            self._write_json_file(setup_path, setup_state)
            receipt = {
                **receipt,
                "managed_copy": str(retained_path),
            }
        return {
            "schema_version": "DesktopBrowserSessionFileImportReceipt-v1",
            **receipt,
            "source_name": source.name,
            "source_sha256": hashlib.sha256(source_io.read_bytes()).hexdigest().upper(),
            "source_bytes": size,
        }

    def export_support_bundle_file(self) -> dict[str, Any]:
        payload = self._service.call("settings.support_bundle", {})
        receipt = self._save_json_dialog(payload, "Memorive-support-bundle.json")
        return {"schema_version": "DesktopAssistantSupportBundleFileReceipt-v1", **receipt}

    def open_settings_log_directory(self) -> dict[str, Any]:
        log_root = (self._state_dir / "service-runtime").resolve(strict=True)
        process = subprocess.Popen(["explorer.exe", str(log_root)], shell=False, close_fds=True)
        return {
            "schema_version": "DesktopAssistantOpenLogDirectoryReceipt-v1",
            "process_id": process.pid,
            "status": "OPENED",
        }

    def clear_temporary_cache(self) -> dict[str, Any]:
        cache_root = self._inbox_source_root.resolve(strict=False)
        cache_root_io = windows_io_path(cache_root)
        if not cache_root_io.is_dir():
            raise RuntimeError("CACHE_ROOT_UNAVAILABLE")
        removed = 0
        removed_bytes = 0
        for child_io in cache_root_io.iterdir():
            child = cache_root / child_io.name
            resolved = child.resolve(strict=False)
            if not resolved.is_relative_to(cache_root) or resolved == cache_root:
                raise RuntimeError("CACHE_TARGET_ESCAPE")
            if child_io.is_dir() and not child_io.is_symlink():
                removed_bytes += sum(path.stat().st_size for path in child_io.rglob("*") if path.is_file())
                shutil.rmtree(child_io)
                removed += 1
        return {
            "schema_version": "DesktopAssistantTemporaryCacheClearReceipt-v1",
            "removed_stage_directories": removed,
            "removed_bytes": removed_bytes,
            "formal_logs_deleted": 0,
            "controlled_inbox_files_deleted": 0,
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

    def open_project_page(self) -> dict[str, Any]:
        from release_links import PROJECT_URL
        opened = webbrowser.open(PROJECT_URL, new=2)
        return {
            "schema_version": "DesktopAssistantProjectPageOpenReceipt-v1",
            "opened": bool(opened),
            "url": PROJECT_URL,
            "status": "OPENED" if opened else "BLOCKED",
        }

    def resolve_locator(self, locator: str) -> dict[str, Any]:
        if not isinstance(locator, str):
            raise ValueError("locator must be a string")
        return self._service.call("service.resolve_locator", {"locator": locator})

    def open_developer_sdk(self) -> dict[str, Any]:
        import sys,os
        root=(Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2])/'integrations/developer'
        if not (root/'api-v1.schema.json').is_file():raise ValueError('DEVELOPER_SDK_MISSING')
        os.startfile(str(root))
        return {'status':'OPENED','api_version':'1.0.0'}

    def open_local_help(self, kind: str, language: str = 'zh-CN') -> dict[str, Any]:
        from offline_help import open_help_asset
        return open_help_asset(kind, language)

    def get_control_state(self) -> dict[str, Any]:
        with self._lock:
            self._control_envelope = self._control_store.load()
            return {
                "schema_version": "WorkLogControlStateProjection-v1",
                "revision": self._control_envelope["revision"],
                "state": self._control_envelope["state"],
                "status": "READY",
            }

    def get_runtime_status(self) -> dict[str, Any]:
        with self._lock:
            health = self._service.call("service.health", {})
            capabilities = self._service.call("service.capabilities", {})
            adapter = capabilities["adapter"]
            return {
                "schema_version": "WorkLogProductRuntimeStatus-v1",
                "typed_event_sequence": self._sequence,
                "facade_method_count": len(self._facade_methods),
                "settings_method_count": len(SETTINGS_METHODS),
                "inbox_method_count": len(INBOX_METHODS),
                "current_task_method_count": len(CURRENT_TASK_METHODS),
                "messages_method_count": len(MESSAGES_METHODS),
                "sessions_method_count": len(SESSIONS_METHODS),
                "library_method_count": len(LIBRARY_METHODS),
                "work_log_method_count": len(WORK_LOG_METHODS),
                "assistant_method_count": 5,
                "total_required_product_methods": (
                    len(self._facade_methods) + len(SETTINGS_METHODS) + len(INBOX_METHODS)
                    + len(CURRENT_TASK_METHODS) + len(MESSAGES_METHODS) + len(SESSIONS_METHODS)
                    + len(LIBRARY_METHODS) + len(WORK_LOG_METHODS) + 5
                ),
                "adapter_kind": adapter["adapter_kind"],
                "messages_adapter_kind": self._messages.adapter_kind,
                "sessions_adapter_kind": self._sessions.adapter_kind,
                "library_adapter_kind": self._library.adapter_kind,
                "work_log_adapter_kind": self._work_log.adapter_kind,
                "missing_required_methods": adapter["missing_methods"],
                "control_store_revision": self._control_envelope["revision"],
                "restored_route": self._control_envelope["state"]["route"],
                "service": self._service.public_status(),
                "service_health": health,
                "single_instance_primary": True,
                "assistant_window_attached": self._assistant_window is not None,
                "assistant_sample_job_consumed": False,
                "network_calls": 0,
                "external_network_calls": 0,
                "model_calls": 0,
                "credential_value_reads": 0,
            }

    def close(self, *, graceful_service: bool = True) -> dict[str, Any]:
        bridge = getattr(self, '_console_bridge', None)
        if bridge is not None:
            bridge._stop.set()
            bridge._expressions.close()
        if hasattr(self, '_memo'):
            self._memo.close()
        if hasattr(self, '_research'):
            self._research.close()
        self._stop_core_event_pump()
        with self._lock:
            if self._closed:
                return {
                    "schema_version": "WorkLogProductShutdownReceipt-v1",
                    "already_closed": True,
                    "status": "STOPPED",
                }
            service_receipt = self._service.close(graceful=True) if graceful_service or getattr(self,'_system_session_ending',False) else self._service.close()
            # Window lifetime belongs to exit_application / the native loop.
            # A synchronous GUI call here can wait on a loop already torn down.
            self._single_instance.release()
            self._closed = True
            return {
                "schema_version": "WorkLogProductShutdownReceipt-v1",
                "service": service_receipt,
                "single_instance_released": True,
                "status": "STOPPED",
            }
