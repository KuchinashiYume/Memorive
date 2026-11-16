from __future__ import annotations

import base64
from copy import deepcopy
import ctypes
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
import uuid
import webbrowser

from application_behavior import WindowsAutostartManager, decide_close_action
from network_diagnostics import run_network_diagnostic
from memorive.platform.clock import utc_now as _utc_now
from memorive.platform.json_files import lenient_sha256 as _sha256_json, write_json_indented
from memorive.platform.windows_paths import windows_io_path
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

from memorive.documents.data_review.service import REVIEW_METHODS as _REVIEW_METHODS
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
            command.extend(["-m", "memorive.desktop.service"])
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
                module = importlib.import_module("memorive.desktop.service.sync_client")
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
                if process.returncode != 0:
                    raise RuntimeError('SERVICE_GRACEFUL_SHUTDOWN_FAILED')
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
            folder_module = importlib.import_module("memorive.platform.folders")
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
        from memorive.models.scheduling import TASK_CATEGORIES
        TASK_CATEGORIES.bind(self._state_dir / 'profile')
        self._source_root = source_root.resolve()
        self._method_binding_path = method_binding_path.resolve()
        self._fixture_root = self._source_root.parent / "fixtures" / "source"
        self._window: Any | None = None
        self._notification_presenter: Any | None = None
        self._notification_delivery_count = 0
        self._last_notification_receipt: dict[str, Any] | None = None
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
        self._sequence = 0  # numbers the UI event receipts of this run
        profile_root = self._state_dir / "profile"
        self._profile_root = profile_root
        folder_module = importlib.import_module("memorive.platform.folders")
        self._folder_manager = folder_module.ProfileFolderManager(profile_root)
        self._inbox_root = self._profile_path("INBOX")
        self._inbox_source_root = self._profile_path("INBOX_SOURCE")
        windows_io_path(self._inbox_source_root).mkdir(parents=True, exist_ok=True)
        ui_root = self._profile_path("UI_STATE")
        ui_root.mkdir(parents=True, exist_ok=True)
        self._core_event_receipt_path = ui_root / "core_event_pump_receipt_v1.json"
        self._settings_save_receipt_path = ui_root / "settings_save_receipt_v1.json"
        control_module = importlib.import_module("memorive.desktop.service.control_store")
        instance_module = importlib.import_module("memorive.desktop.service.single_instance")
        self._control_store = control_module.ControlStore(ui_root)
        self._control_envelope = self._control_store.load()
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
            local_models_module = importlib.import_module("memorive.models.local.product_controller")
            self._local_models = local_models_module.LocalModelProductController(
                self._state_dir / "local_models"
            )
            accounting_module = importlib.import_module("memorive.desktop.accounting")
            self._accounting = accounting_module.AccountingProductController(
                settings_provider=lambda: self._service.call("settings.get_state", {}),
                local_model_registry=self._local_models,
                leaderboard_provider=lambda: self._service.call("settings.leaderboard_get", {}),
                exam_root=self._profile_path("MODEL_VALIDATION"),
                core_root=self._profile_path("Core_JOBS"),
            )
            refinement_module = importlib.import_module(
                "memorive.sessions.refinement.model_channel"
            )
            self._conversation_refinement_channel = (
                refinement_module.ExistingConversationRefinementChannel(
                    local_model_registry=self._local_models,
                    profile_executor=self._service,
                )
            )
            refinement_tasks_module = importlib.import_module(
                "memorive.desktop.current_task.refinement_tasks"
            )
            self._refinement_queue_tasks = (
                refinement_tasks_module.SessionRefinementTaskStore(
                    self._state_dir / "sessions" / "refinement_queue_tasks"
                )
            )
            controller_module = importlib.import_module("memorive.desktop.current_task.product_controller")
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
            messages_module = importlib.import_module("memorive.desktop.messages.product_controller")
            self._messages = messages_module.MessagesProductController(
                self._service, self._control_store,
                self._source_root.parent / "contracts" / "retrieval",
                self._source_root.parent / "fixtures_retrieval",
                self._state_dir / "messages",
                include_synthetic_projection=test_fixture_mode,
                core_job_provider=lambda: self._current_task.core_job_rows(include_history=True),
            )
            from memorive.documents.library.core_products import CoreProducts
            self._core_products = CoreProducts(
                service=self._service, profile_path=self._profile_path, state_dir=self._state_dir,
                write_json_file=self._write_json_file, is_reparse_point=self._is_reparse_point,
            )
            library_module = importlib.import_module("memorive.documents.library.product_controller")
            self._library = library_module.LibraryProductController(
                self._service, self._control_store,
                self._source_root.parent / "contracts" / "integration",
                self._source_root.parent / "fixtures_integration",
                self._state_dir / "library",
                include_synthetic_projection=test_fixture_mode,
                core_artifact_provider=self._core_products.rows,
            )
            sessions_module = importlib.import_module("memorive.sessions.controller")
            if session_visible_ui_reader is None:
                visible_ui_module = importlib.import_module("memorive.sessions.edge_visible_ui")
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
            from memorive.sessions.companion.desktop import BrowserCompanionDesktop
            self._browser_companion = BrowserCompanionDesktop(
                profile_root=self._profile_root, source_root=self._source_root, sessions=self._sessions,
                profile_path=self._profile_path, write_json_file=self._write_json_file,
                effective_directory=self._effective_directory,
                system_effects_enabled=self._system_effects_enabled,
            )
            self._sessions.browser_sites_provider = self._browser_companion.browser_companion_sites
            work_log_module = importlib.import_module("memorive.desktop.work_log.product_controller")
            self._work_log = work_log_module.WorkLogProductController(
                self._service, self._control_store,
                self._source_root.parent / "contracts" / "logging",
                self._source_root.parent / "fixtures_logging",
                self._state_dir / "work_log",
                include_synthetic_projection=test_fixture_mode,
                refinement_task_provider=self._refinement_queue_tasks.list_jobs,
                refinement_product_provider=self._library.refinement_activity_rows,
                core_job_provider=self._work_log_core_job_rows,
                core_product_provider=self._library.core_activity_rows,
            )
            from memorive.desktop.assistant.desktop import AssistantDesktop
            self._desk_assistant = AssistantDesktop(
                ui_root=ui_root, control_store=self._control_store, service=self._service, lock=self._lock,
                messages=self._messages, current_task=self._current_task,
                refinement_tasks=self._refinement_queue_tasks,
                bring_main_window_to_foreground=self._bring_main_window_to_foreground,
                sample_job=self._sample_job, sample_message=self._sample_message,
            )
            self._browser_companion_auto_update = self._browser_companion.refresh_prepared_package()
            from memorive_research_runtime.host import ResearchHost
            from memorive_research_runtime.runtime import ResearchRuntime
            self._research = ResearchRuntime(ResearchHost(
                service=self._service, library=self._library, messages=self._messages,
                work_log=self._work_log, local_models=self._local_models,
                profile_path=self._profile_path, desktop_call=self.call,
                open_target=self.assistant_launch_target,
                library_job_rows=self._core_products.job_rows,
                notify_window=self._send_page_event,
                research_workspace=lambda: getattr(self, "_memo", None)))
            self._library.research_artifact_provider = self._research.library_rows
            from memorive_research_runtime.task_projection import log_rows
            self._work_log.research_provider=lambda:log_rows(self._research)
            self._desk_assistant.start_deliveries()
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
        from update_service import METHODS as UPDATE_METHODS
        if method in UPDATE_METHODS:
            with self._lock:
                if not hasattr(self, '_updates'):
                    from update_service import UpdateService
                    self._updates = UpdateService(self)
            return self._updates.call(method, dict(params or {}))
        from memorive_research_runtime.home import METHODS as HOME_METHODS, call as home_call
        from memorive_research_runtime.ai_briefing import METHODS as AI_METHODS
        if method in HOME_METHODS:
            return home_call(self._research.host,method,dict(params or {}))
        if method in AI_METHODS:
            return self._research.ai.call(method,dict(params or {}))
        from memorive.research.workspace.service import DESKTOP_METHODS
        if method in DESKTOP_METHODS:
            with self._lock:
                if not hasattr(self, '_memo'):
                    from memorive.research.workspace.desktop import create
                    self._memo=create(service=self._service,library=self._library,
                        local_models=getattr(self,'_local_models',None),profile_root=self._profile_root,
                        workspace_root=self._profile_path('WORKSPACE')/'research_workspace',
                        desktop_call=self.call,stage_inbox_paths=self.stage_inbox_paths)
            return self._memo.call(method,dict(params or {}),desktop=True)
        from memorive_research_runtime.runtime import METHODS as RESEARCH_METHODS
        if method in RESEARCH_METHODS:
            return self._research.call(method,dict(params or {}))
        # Research answers messages that point at its runs and draws its runs on the current-task page.
        research=getattr(self,'_research',None)
        if research is not None and (method=='messages.navigate' or method in CURRENT_TASK_METHODS):
            from memorive_research_runtime import routes as research_routes
            answer=(research_routes.navigate(research,dict(params or {})) if method=='messages.navigate'
                    else research_routes.current_task(research,method,dict(params or {}),self._current_task.call))
            if answer is not None:
                return answer
        if not isinstance(method, str) or method not in REVIEW_METHODS | SETTINGS_METHODS | ACCOUNTING_METHODS | INBOX_METHODS | CURRENT_TASK_METHODS | MESSAGES_METHODS | SESSIONS_METHODS | LOCAL_MODEL_METHODS | ASSISTANT_PREFERENCE_METHODS | LIBRARY_METHODS | WORK_LOG_METHODS:
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
            if method in REVIEW_METHODS:
                return self._service.call(method,accepted)
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
                    from memorive_research_runtime.routes import literature_notification
                    if literature_notification(self._research,message.target_locator):
                        kind='LITERATURE'
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
                return self._desk_assistant.preference_get()
            if method == "assistant.preference_save":
                return self._desk_assistant.preference_save(accepted)
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
                    assistant_before = self._desk_assistant.load_preferences()
                    assistant_candidate = self._desk_assistant.preferences_for_reset_scope(
                        reset_scope,
                        assistant_before,
                    )
                    try:
                        assistant_scope_receipt = self._desk_assistant.preference_save(
                            self._desk_assistant.preference_save_params(assistant_candidate)
                        )
                    except Exception:
                        self._write_json_file(
                            self._desk_assistant.preferences_path,
                            assistant_before,
                        )
                        raise
            try:
                result = self._service.call(method, accepted)
            except Exception:
                if assistant_before is not None:
                    try:
                        self._desk_assistant.preference_save(
                            self._desk_assistant.preference_save_params(assistant_before)
                        )
                    except Exception as rollback_error:
                        self._write_json_file(
                            self._desk_assistant.preferences_path,
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

    # The desk assistant lives in memorive/desktop/assistant/desktop.py (AssistantDesktop). The window,
    # app.py and the research runtime call these names on ProductApi.
    def assistant_bootstrap(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._desk_assistant.bootstrap(request)

    def assistant_ui_signal(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._desk_assistant.ui_signal(request)

    def assistant_initial_placement(self) -> dict[str, Any]:
        return self._desk_assistant.initial_placement()

    def assistant_move_window(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._desk_assistant.move_window(request)

    def assistant_shortcut(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._desk_assistant.shortcut(request)

    def assistant_launch_target(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._desk_assistant.launch_target(request)

    def assistant_target_status(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._desk_assistant.target_status(request)

    def claim_assistant_presentation_delivery(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._desk_assistant.claim_presentation_delivery(request)

    def record_assistant_presentation_receipt(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._desk_assistant.record_presentation_receipt(request)

    def assistant_ack_delivery(self, request):
        return self._desk_assistant.ack_delivery(request)

    def attach_assistant_window(self, window: Any) -> None:
        return self._desk_assistant.attach_window(window)

    def attach_assistant_presenter(self, presenter: Any) -> None:
        return self._desk_assistant.attach_presenter(presenter)

    def attach_window(self, window: Any) -> None:
        if self._window is not None:
            raise RuntimeError("PRODUCT_WINDOW_ALREADY_ATTACHED")
        self._window = window
        self._browser_companion.window = window
        self._desk_assistant.main_window = window
        self._start_core_event_pump()

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

    def pick_research_files(self) -> dict[str, Any]:
        """Keep source bundles together; research attachments own durable copying."""
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        from webview import FileDialog
        selected = self._window.create_file_dialog(
            FileDialog.OPEN,
            directory=str(self._effective_directory("workspace_root")),
            allow_multiple=True,
            file_types=(
                "Research attachments (*.pdf;*.md;*.txt;*.json;*.docx;*.png;*.jpg;*.jpeg;*.webp)",
                "All files (*.*)",
            ),
        )
        paths = [str(path) for path in selected] if selected else []
        return {"paths": paths, "path_count": len(paths),
                "status": "SELECTED" if paths else "CANCELLED"}

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
        if self._window is None:
            return {'path':None}
        import webview
        value=self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return {'path':str(value[0]) if value else None}

    def memo_pick_materials(self):
        if self._window is None:
            return {'paths':[]}
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
        if cached and time.monotonic()-cached[0]<60:
            return cached[1]
        cmd="Get-StartApps | Where-Object { ($_.Name -match '^(Codex|Claude)( |$)') -or ($_.AppID -match '^OpenAI.Codex_') } | Select-Object Name,AppID | ConvertTo-Json -Compress"
        try:
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',cmd],capture_output=True,timeout=12,creationflags=subprocess.CREATE_NO_WINDOW)
            rows=json.loads(result.stdout.decode('utf-8-sig',errors='replace') or '[]')
            if isinstance(rows,dict):
                rows=[rows]
        except (OSError,ValueError,subprocess.TimeoutExpired):
            rows=[]
        apps={}
        for row in rows:
            name=str(row.get('Name',''))
            appid=str(row.get('AppID',''))
            if not appid or any(c in appid for c in '\r\n\x00'):
                continue
            key='codex-desktop' if name.lower().startswith('codex') or appid.startswith('OpenAI.Codex_') else 'claude-desktop'
            apps.setdefault(key,{'name':name,'app_id':appid})
        self._memo_desktop_apps_cache=(time.monotonic(),apps)
        return apps

    def memo_pick_agent_app(self):
        if self._window is None:
            return {'path':None}
        import webview
        value=self._window.create_file_dialog(webview.FileDialog.OPEN,allow_multiple=False,file_types=('Application (*.exe)',))
        return {'path':str(value[0]) if value else None}

    def memo_prepare_desktop_handoff(self,request):
        self.call('memo.capabilities',{})
        from memorive.research.workspace.agents import desktop_handoff
        # GetFolderPath uses the Windows account path, not the packaged private profile override.
        import ctypes
        buffer=ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None,28,None,0,buffer)!=0:
            raise ValueError('HANDOFF_DIRECTORY_UNAVAILABLE')
        root=Path(buffer.value)/'Memorive'/'Handoff'
        return desktop_handoff(self._memo,request['thread_id'],request.get('question',''),root)

    def memo_desktop_handoff_action(self,request):
        self.call('memo.capabilities',{})
        row=self._memo.store.get('desktop_handoff',request.get('id',''))
        if not row:
            raise ValueError('HANDOFF_NOT_FOUND')
        ready=self._memo.handoffs.validate_desktop(row)
        if request.get('action')=='validate':
            return ready
        folder=Path(row['path'])
        if request.get('action')=='folder':
            os.startfile(str(folder))
            return {'status':'OPEN_REQUESTED'}
        if request.get('action')!='launch':
            raise ValueError('HANDOFF_ACTION_INVALID')
        path=row.get('app_path','')
        if path:
            p=Path(path)
            if not p.is_absolute() or not p.is_file() or p.suffix.lower()!='.exe' or str(p).startswith(('\\\\','//')) or p.name.lower() in {'cmd.exe','powershell.exe','pwsh.exe','wscript.exe','cscript.exe','mshta.exe','rundll32.exe'}:
                raise ValueError('AGENT_APP_PATH_INVALID')
            os.startfile(str(p))
        else:
            app=self.memo_desktop_apps().get(row['agent'])
            if not app:
                raise ValueError('AGENT_DESKTOP_NOT_FOUND')
            # App identity comes only from the local registered Start menu inventory.
            os.startfile('shell:AppsFolder\\'+app['app_id'])
        return {'status':'OPEN_REQUESTED','model_called':False,'task_started':False}

    def memo_agent_config(self):
        self.call('memo.capabilities',{})
        base=Path(sys.executable).parent if getattr(sys,'frozen',False) else self._source_root.parent
        helper=Path(sys.executable) if getattr(sys,'frozen',False) else base/'Memorive.exe'
        args=['--memo-agent','--workspace',str(self._memo.store.root),'--mcp']
        if not helper.exists():
            helper=Path(sys.executable)
            args=[str(self._source_root/'memorive/research/workspace/agent_entry.py'),*[v for v in args if v!='--memo-agent']]
        toml='[mcp_servers.memo]\ncommand = '+json.dumps(str(helper),ensure_ascii=False)+'\nargs = '+json.dumps(args,ensure_ascii=False)+'\n'
        return {'mcpServers':{'memo':{'command':str(helper),'args':args}},'codex_config_toml':toml,
                'obsidian_plugin':str(base/'integrations/obsidian/memorive-companion'),
                'zotero_plugin':str(base/'integrations/zotero/memorive-companion.xpi'),
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
            if getattr(error, "winerror", None) not in {31, 1155}:
                raise
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

    def open_structure_original(self, job_id: str) -> dict[str, Any]:
        import hashlib
        receipt = self._service.call('structure.original', {'job_id': job_id})
        target = Path(receipt['path'])
        with target.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != receipt['source_sha256']:
                raise ValueError('STRUCTURE_SOURCE_STALE')
        os.startfile(str(target), 'open')
        return {'status': 'OPENED', 'source_sha256': receipt['source_sha256']}

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
        if bridge is not None and bridge._closing:
            raise ValueError('CONSOLE_SESSION_CLOSING')
        from console_permission import write_permission
        with self._lock:
            write_permission(self._state_dir,request['enabled'])
        if bridge is not None:
            bridge.set_access(request['enabled'])
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

    # The profile and UI state files are written whole, indented (memorive/platform/json_files.py).
    _write_json_file = staticmethod(write_json_indented)

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

    def export_cli_template_file(self, service: dict[str, Any]) -> dict[str, Any]:
        prepared = self._service.call("settings.cli_template_prepare", {"action": "export", "payload": service})
        receipt = self._save_json_dialog(prepared["payload"], "Memorive-CLI-template.json")
        return {"schema_version": "DesktopCLITemplateExportReceipt-v1", **receipt}

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

    def model_dashboard(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read-only bottom panel; no model execution or metadata refresh."""
        request = request or {}
        if not isinstance(request, dict) or set(request) - {"view"} or request.get("view") not in {"ranking", "billing"}:
            return {"status": "ERROR", "reason": "DASHBOARD_REQUEST_INVALID"}
        try:
            module = importlib.import_module("memorive.desktop.settings.model_dashboard")
            exam_root = self._profile_path("MODEL_VALIDATION")
            if request["view"] == "billing":
                data = module.billing_projection(exam_root, self._profile_path("Core_JOBS"))
            else:
                projection = self._service.call("settings.get_state", {})
                history_module = importlib.import_module("memorive.exams.score_history")
                history = history_module.ExamScoreHistory(exam_root / "workflow_exam_scores").rows()
                external_root = self._profile_path("SETTINGS") / "external_data"
                external = {"rows": [], "unavailable_sources": []}
                if external_root.is_dir():
                    try:
                        sources_module = importlib.import_module("memorive.desktop.settings.external_sources")
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

    # The browser companion and browser sessions: the window calls these; memorive/sessions/companion/desktop.py
    # (BrowserCompanionDesktop) does the work.
    def browser_companion_sites(self) -> dict[str, Any]:
        return self._browser_companion.browser_companion_sites()

    def save_browser_companion_sites(self, sites, expected_revision):
        return self._browser_companion.save_browser_companion_sites(sites, expected_revision)

    def open_browser_companion_site(self, url, preferred_browser=None):
        return self._browser_companion.open_browser_companion_site(url, preferred_browser)

    def open_browser_companion_folder(self):
        return self._browser_companion.open_browser_companion_folder()

    def browser_companion_status(self) -> dict[str, Any]:
        return self._browser_companion.browser_companion_status()

    def install_browser_companion(self, preferred_browser: str | None = None) -> dict[str, Any]:
        return self._browser_companion.install_browser_companion(preferred_browser)

    def confirm_browser_companion_setup(self) -> dict[str, Any]:
        return self._browser_companion.confirm_browser_companion_setup()

    def reload_browser_companion(self) -> dict[str, Any]:
        return self._browser_companion.reload_browser_companion()

    def run_browser_companion_reload(self) -> dict[str, Any]:
        return self._browser_companion.run_browser_companion_reload()

    def run_browser_companion_test_read(self) -> dict[str, Any]:
        return self._browser_companion.run_browser_companion_test_read()

    def run_browser_companion_sync(self, requested_mode=None) -> dict[str, Any]:
        return self._browser_companion.run_browser_companion_sync(requested_mode)

    def run_browser_companion_full_sync(self) -> dict[str, Any]:
        return self._browser_companion.run_browser_companion_full_sync()

    def restart_browser_companion(self) -> dict[str, Any]:
        return self._browser_companion.restart_browser_companion()

    def sync_browser_sessions_from_downloads(self) -> dict[str, Any]:
        return self._browser_companion.sync_browser_sessions_from_downloads()

    def import_browser_sessions_file(self) -> dict[str, Any]:
        return self._browser_companion.import_browser_sessions_file()

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
        from memorive.desktop.release_links import PROJECT_URL
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
        root=(Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[4])/'integrations/developer'
        if not (root/'api-v1.schema.json').is_file():
            raise ValueError('DEVELOPER_SDK_MISSING')
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
                "assistant_window_attached": self._desk_assistant.window is not None,
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
