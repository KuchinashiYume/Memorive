from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from .component_binding import ComponentBinding, verify_component_bindings
from .contracts import base_object, immutable_copy, utc_now, validate_initialization_object
from .workspace import JobWorkspace, WorkspaceViolation


SAFE_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
TERMINATION_GRACE_SECONDS = 0.1


@dataclass(frozen=True)
class ProcessSpec:
    adapter_id: str
    attempt_id: str
    argv: tuple[str, ...]
    cwd: str
    environment: Mapping[str, str]
    environment_allowlist: tuple[str, ...]
    timeout_seconds: float
    raw_cap_bytes: int
    stdin_bytes: bytes | None = None
    cancel_event: threading.Event | None = None
    declared_read_paths: tuple[str, ...] = ()
    declared_write_paths: tuple[str, ...] = ()
    component_bindings: tuple[ComponentBinding, ...] = ()


@dataclass(frozen=True)
class ProcessRun:
    receipt: dict[str, Any]
    stdout: bytes
    stderr: bytes


class _CaptureBudget:
    def __init__(self, cap: int):
        self.cap = cap
        self.kept = 0
        self.exceeded = threading.Event()
        self.lock = threading.Lock()

    def keep(self, chunk: bytes) -> bytes:
        with self.lock:
            remaining = max(0, self.cap - self.kept)
            kept = chunk[:remaining]
            self.kept += len(kept)
            if len(chunk) > remaining:
                self.exceeded.set()
            return kept


class _Collector:
    def __init__(self, stream: Any, budget: _CaptureBudget):
        self._stream = stream
        self._budget = budget
        self._parts: list[bytes] = []

    def run(self) -> None:
        while True:
            chunk = self._stream.read(8192)
            if not chunk:
                break
            kept = self._budget.keep(chunk)
            if kept:
                self._parts.append(kept)

    @property
    def value(self) -> bytes:
        return b"".join(self._parts)


class _TreeController:
    def __init__(self):
        self.creationflags = 0
        self.start_new_session = False
        self.mode = "POSIX_PROCESS_GROUP"
        self._job_handle: int | None = None
        if os.name == "nt":
            self.creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            self.mode = "WINDOWS_JOB_OBJECT"
        else:
            self.start_new_session = True

    def attach(self, process: subprocess.Popen[bytes]) -> None:
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(job)
            raise OSError(error, "SetInformationJobObject failed")
        if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(job)
            raise OSError(error, "AssignProcessToJobObject failed")
        self._job_handle = int(job)

    def terminate(self, process: subprocess.Popen[bytes]) -> None:
        if os.name == "nt" and self._job_handle is not None:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.TerminateJobObject(self._job_handle, 1)
            return
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
                return
            except (ProcessLookupError, PermissionError):
                pass
        try:
            process.kill()
        except OSError:
            pass

    def request_graceful(self, process: subprocess.Popen[bytes]) -> bool:
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(process.pid, signal.SIGTERM)
            return True
        except (OSError, ProcessLookupError, PermissionError):
            return False

    def close(self, process: subprocess.Popen[bytes] | None = None) -> None:
        if os.name == "nt" and self._job_handle is not None:
            import ctypes

            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._job_handle)
            self._job_handle = None
        elif os.name != "nt" and process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


class IsolatedJobRunner:
    """Local argv runner with bounded capture and no-shell process-tree control."""

    def __init__(
        self,
        core_schema: Mapping[str, Any],
        *,
        clock: Callable[[], str] = utc_now,
    ):
        self._core_schema = immutable_copy(core_schema)
        self._clock = clock

    def run(self, workspace: JobWorkspace, spec: ProcessSpec) -> ProcessRun:
        reason_code = self._validate_spec(workspace, spec)
        if reason_code is not None:
            return self._not_started(
                workspace,
                spec,
                self._canonical_error(reason_code),
                reason_code,
            )

        # First measurement closes configuration drift before receipt allocation.
        if spec.component_bindings:
            reason_code = verify_component_bindings(spec.component_bindings)
            if reason_code is not None:
                return self._not_started(workspace, spec, "WORKSPACE_POLICY_VIOLATION", reason_code)

        stdout_path = workspace.receipt_root / f"{spec.attempt_id}.stdout.bin"
        stderr_path = workspace.receipt_root / f"{spec.attempt_id}.stderr.bin"
        for target in (stdout_path, stderr_path):
            workspace.assert_write_path(target)
            if target.exists():
                return self._not_started(
                    workspace,
                    spec,
                    "WORKSPACE_POLICY_VIOLATION",
                    "RECEIPT_CREATE_ONLY_COLLISION",
                )

        controller = _TreeController()
        process: subprocess.Popen[bytes] | None = None
        started_wall = self._clock()
        started_monotonic = time.monotonic()
        error_code: str | None = None
        runner_reason_code: str | None = None
        timed_out = False
        killed = False
        budget = _CaptureBudget(spec.raw_cap_bytes)
        stdout_collector: _Collector | None = None
        stderr_collector: _Collector | None = None
        stdout_thread: threading.Thread | None = None
        stderr_thread: threading.Thread | None = None
        stdin_thread: threading.Thread | None = None
        stdin_error_types: list[str] = []
        stdin_thread_stuck = False
        graceful_termination_requested = False
        graceful_signal_sent = False
        force_tree_kill = False
        tree_container_closed = False

        def terminate_tree_with_grace() -> None:
            nonlocal graceful_termination_requested, graceful_signal_sent
            nonlocal force_tree_kill
            if process is None or process.poll() is not None:
                return
            graceful_termination_requested = True
            graceful_signal_sent = controller.request_graceful(process)
            grace_deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
            while process.poll() is None and time.monotonic() < grace_deadline:
                time.sleep(0.01)
            if process.poll() is None:
                controller.terminate(process)
                force_tree_kill = True
        try:
            # Second measurement is intentionally adjacent to Popen and catches
            # mutation after invocation construction / first preflight.
            if spec.component_bindings:
                reason_code = verify_component_bindings(spec.component_bindings)
                if reason_code is not None:
                    return self._not_started(workspace, spec, "WORKSPACE_POLICY_VIOLATION", reason_code)
            process = subprocess.Popen(
                list(spec.argv),
                cwd=spec.cwd,
                env=dict(spec.environment),
                stdin=subprocess.PIPE if spec.stdin_bytes is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=controller.creationflags,
                start_new_session=controller.start_new_session,
            )
            try:
                controller.attach(process)
            except OSError:
                controller.terminate(process)
                force_tree_kill = True
                process.wait(timeout=5)
                error_code = "WORKSPACE_POLICY_VIOLATION"
                runner_reason_code = "PROCESS_TREE_CONTROL_UNAVAILABLE"
                killed = True

            if error_code is None:
                assert process.stdout is not None and process.stderr is not None
                stdout_collector = _Collector(process.stdout, budget)
                stderr_collector = _Collector(process.stderr, budget)
                stdout_thread = threading.Thread(target=stdout_collector.run, daemon=True)
                stderr_thread = threading.Thread(target=stderr_collector.run, daemon=True)
                stdout_thread.start()
                stderr_thread.start()
                if process.stdin is not None:
                    stdin_payload = spec.stdin_bytes or b""
                    if len(stdin_payload) > spec.raw_cap_bytes:
                        error_code = "WORKSPACE_POLICY_VIOLATION"
                        runner_reason_code = "STDIN_CAP_EXCEEDED"
                        killed = True
                        terminate_tree_with_grace()
                    else:
                        def write_stdin() -> None:
                            try:
                                assert process is not None and process.stdin is not None
                                process.stdin.write(stdin_payload)
                                process.stdin.close()
                            except (BrokenPipeError, OSError, ValueError) as exc:
                                stdin_error_types.append(type(exc).__name__)

                        stdin_thread = threading.Thread(target=write_stdin, daemon=True)
                        stdin_thread.start()
                deadline = started_monotonic + spec.timeout_seconds
                while process.poll() is None:
                    if spec.cancel_event is not None and spec.cancel_event.is_set():
                        error_code = "WORKSPACE_POLICY_VIOLATION"
                        runner_reason_code = "PROCESS_CANCELLED"
                        killed = True
                        terminate_tree_with_grace()
                        break
                    if budget.exceeded.is_set():
                        error_code = "WORKSPACE_POLICY_VIOLATION"
                        runner_reason_code = "RAW_OUTPUT_CAP_EXCEEDED"
                        killed = True
                        terminate_tree_with_grace()
                        break
                    if time.monotonic() >= deadline:
                        error_code = "PROCESS_TIMEOUT"
                        timed_out = True
                        killed = True
                        terminate_tree_with_grace()
                        break
                    time.sleep(0.01)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    controller.terminate(process)
                    force_tree_kill = True
                    process.wait(timeout=5)
                    killed = True
                    error_code = error_code or "WORKSPACE_POLICY_VIOLATION"
                    runner_reason_code = (
                        runner_reason_code or "PROCESS_TREE_TERMINATION_FAILED"
                    )
        except (OSError, ValueError) as exc:
            error_code = "WORKSPACE_POLICY_VIOLATION"
            runner_reason_code = "PROCESS_START_FAILED"
            process = None
            start_exception_type = type(exc).__name__
        else:
            start_exception_type = None
        finally:
            controller.close(process)
            tree_container_closed = True
            if stdout_thread is not None:
                stdout_thread.join(timeout=5)
            if stderr_thread is not None:
                stderr_thread.join(timeout=5)
            if stdin_thread is not None:
                stdin_thread.join(timeout=5)
                stdin_thread_stuck = stdin_thread.is_alive()
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        try:
                            stream.close()
                        except OSError:
                            pass

        stdout = stdout_collector.value if stdout_collector else b""
        stderr = stderr_collector.value if stderr_collector else b""
        with stdout_path.open("xb") as handle:
            handle.write(stdout)
        with stderr_path.open("xb") as handle:
            handle.write(stderr)
        ended_wall = self._clock()
        duration_ms = round((time.monotonic() - started_monotonic) * 1000, 3)
        exit_code = process.returncode if process is not None else None
        if error_code is None and (stdin_error_types or stdin_thread_stuck):
            error_code = "WORKSPACE_POLICY_VIOLATION"
            runner_reason_code = (
                "STDIN_WRITER_STUCK" if stdin_thread_stuck else "STDIN_WRITE_FAILED"
            )
        if error_code is None and exit_code != 0:
            error_code = "PROCESS_EXIT_NONZERO"
            runner_reason_code = "PROCESS_EXIT_NONZERO"
        redacted_argv = json.dumps(
            [Path(spec.argv[0]).name]
            + [f"<arg:{index}>" for index in range(1, len(spec.argv))],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        command_template = json.dumps(
            {
                "executable_basename": Path(spec.argv[0]).name,
                "argument_count": max(0, len(spec.argv) - 1),
                "shell": False,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        receipt = self._receipt(
            workspace,
            spec,
            process_started=process is not None,
            started_at=started_wall if process is not None else None,
            ended_at=ended_wall,
            duration_ms=duration_ms,
            exit_code=exit_code,
            timeout=timed_out,
            killed=killed,
            stdout=stdout,
            stderr=stderr,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            error_code=error_code,
            extensions={
                "raw_cap_bytes": spec.raw_cap_bytes,
                "raw_truncated": budget.exceeded.is_set(),
                "stdout_bytes": len(stdout),
                "stderr_bytes": len(stderr),
                "process_tree_control": controller.mode,
                "process_id": process.pid if process is not None else None,
                "parent_process_id": os.getpid(),
                "graceful_termination_requested": graceful_termination_requested,
                "graceful_signal_sent": graceful_signal_sent,
                "termination_grace_seconds": TERMINATION_GRACE_SECONDS,
                "force_tree_kill": force_tree_kill,
                "tree_container_closed": tree_container_closed,
                "orphan_scan_status": (
                    "TREE_CONTAINER_CLOSED_AND_MARKER_PROBE_REQUIRED"
                    if killed
                    else "NO_FORCED_TERMINATION"
                ),
                "command_template_sha256": hashlib.sha256(
                    command_template.encode("utf-8")
                ).hexdigest().upper(),
                "argv_redacted_sha256": hashlib.sha256(
                    redacted_argv.encode("utf-8")
                ).hexdigest().upper(),
                "selected_manifest_hash": workspace.manifest.get("extensions", {}).get(
                    "selected_manifest_hash"
                ),
                "stdin_bytes": len(spec.stdin_bytes or b""),
                "stdin_sha256": (
                    hashlib.sha256(spec.stdin_bytes).hexdigest().upper()
                    if spec.stdin_bytes is not None
                    else None
                ),
                "stdin_error_types": stdin_error_types,
                "stdin_writer_stuck": stdin_thread_stuck,
                "start_exception_type": start_exception_type,
                "runner_reason_code": runner_reason_code,
            },
        )
        return ProcessRun(receipt, stdout, stderr)

    def _validate_spec(self, workspace: JobWorkspace, spec: ProcessSpec) -> str | None:
        if not spec.adapter_id or not SAFE_ATTEMPT_ID.fullmatch(spec.attempt_id):
            return "PROCESS_SPEC_ID_INVALID"
        expected_adapter = workspace.manifest.get("extensions", {}).get(
            "selected_adapter_id"
        )
        if expected_adapter and spec.adapter_id != expected_adapter:
            return "PROCESS_SPEC_ADAPTER_ID_MISMATCH"
        expected_attempt = workspace.manifest.get("extensions", {}).get(
            "expected_attempt_id"
        )
        if expected_attempt and spec.attempt_id != expected_attempt:
            return "PROCESS_SPEC_ATTEMPT_ID_MISMATCH"
        if not spec.argv or any(not isinstance(arg, str) for arg in spec.argv):
            return "ARGV_ARRAY_REQUIRED"
        executable = os.path.abspath(spec.argv[0])
        allowed_processes = {
            os.path.normcase(os.path.abspath(value))
            for value in workspace.manifest["process_allowlist"]
        }
        if os.path.normcase(os.path.abspath(executable)) not in allowed_processes:
            return "PROCESS_NOT_ALLOWLISTED"
        try:
            workspace.assert_cwd(spec.cwd)
        except WorkspaceViolation:
            return "WORKSPACE_POLICY_VIOLATION"
        if spec.timeout_seconds <= 0:
            return "TIMEOUT_POLICY_INVALID"
        contract_timeout = workspace.manifest.get("extensions", {}).get(
            "contract_timeout_seconds"
        )
        if isinstance(contract_timeout, (int, float)) and spec.timeout_seconds > float(
            contract_timeout
        ):
            return "PROCESS_SPEC_TIMEOUT_EXCEEDS_CONTRACT"
        if spec.raw_cap_bytes <= 0:
            return "RAW_CAP_POLICY_INVALID"
        contract_raw_cap = workspace.manifest.get("extensions", {}).get(
            "raw_cap_bytes"
        )
        if isinstance(contract_raw_cap, int) and spec.raw_cap_bytes > contract_raw_cap:
            return "RAW_CAP_EXCEEDS_CONTRACT"
        allowed_environment = set(workspace.manifest["environment_allowlist"])
        if set(spec.environment_allowlist) != allowed_environment:
            return "ENVIRONMENT_ALLOWLIST_MISMATCH"
        if not set(spec.environment).issubset(allowed_environment):
            return "ENVIRONMENT_POLICY_VIOLATION"
        if any(not isinstance(name, str) or not isinstance(value, str) for name, value in spec.environment.items()):
            return "ENVIRONMENT_VALUE_INVALID"
        if os.name == "nt":
            scratch = os.path.normcase(os.path.abspath(str(workspace.scratch_root)))
            for name in ("TEMP", "TMP"):
                value = spec.environment.get(name)
                if value is not None and os.path.normcase(os.path.abspath(value)) != scratch:
                    return "ENVIRONMENT_TEMP_ROOT_VIOLATION"
        for path in spec.declared_read_paths:
            try:
                workspace.assert_read_path(path)
            except WorkspaceViolation:
                return "READ_PATH_POLICY_VIOLATION"
        for path in spec.declared_write_paths:
            try:
                workspace.assert_write_path(path)
            except WorkspaceViolation:
                return "WRITE_PATH_POLICY_VIOLATION"
        return None

    @staticmethod
    def _canonical_error(reason_code: str) -> str:
        if reason_code == "PROCESS_TIMEOUT":
            return "PROCESS_TIMEOUT"
        if reason_code == "PROCESS_EXIT_NONZERO":
            return "PROCESS_EXIT_NONZERO"
        if reason_code == "NETWORK_POLICY_VIOLATION":
            return "NETWORK_POLICY_VIOLATION"
        return "WORKSPACE_POLICY_VIOLATION"

    def _not_started(
        self,
        workspace: JobWorkspace,
        spec: ProcessSpec,
        error_code: str,
        reason_code: str,
    ) -> ProcessRun:
        receipt = self._receipt(
            workspace,
            spec,
            process_started=False,
            started_at=None,
            ended_at=self._clock(),
            duration_ms=0,
            exit_code=None,
            timeout=False,
            killed=False,
            stdout=b"",
            stderr=b"",
            stdout_path=None,
            stderr_path=None,
            error_code=error_code,
            extensions={
                "raw_cap_bytes": spec.raw_cap_bytes,
                "raw_truncated": False,
                "runner_reason_code": reason_code,
            },
        )
        return ProcessRun(receipt, b"", b"")

    def _receipt(
        self,
        workspace: JobWorkspace,
        spec: ProcessSpec,
        *,
        process_started: bool,
        started_at: str | None,
        ended_at: str,
        duration_ms: float,
        exit_code: int | None,
        timeout: bool,
        killed: bool,
        stdout: bytes,
        stderr: bytes,
        stdout_path: Path | None,
        stderr_path: Path | None,
        error_code: str | None,
        extensions: Mapping[str, Any],
    ) -> dict[str, Any]:
        receipt = base_object(
            "ProcessExecutionReceipt",
            f"processexecutionreceipt_{workspace.manifest['job_id']}_{spec.attempt_id}",
            producer="Execution_JOB_RUNNER",
            single_writer="Execution_JOB_RUNNER",
            consumers=("Execution_EVIDENCE_ADAPTER", "RUNTIME_LOG", "ARTIFACT_REGISTRY"),
            clock=self._clock,
            source_evidence_refs=(workspace.manifest["object_id"],),
            extensions=extensions,
        )
        receipt.update(
            {
                "job_id": workspace.manifest["job_id"],
                "attempt_id": spec.attempt_id,
                "adapter_id": spec.adapter_id,
                "argv_redacted": json.dumps(
                    [Path(spec.argv[0]).name]
                    + [f"<arg:{index}>" for index in range(1, len(spec.argv))],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "working_directory": spec.cwd,
                "process_started": process_started,
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_ms": duration_ms,
                "exit_code": exit_code,
                "timeout": timeout,
                "killed": killed,
                "stdout_hash": hashlib.sha256(stdout).hexdigest().upper(),
                "stderr_hash": hashlib.sha256(stderr).hexdigest().upper(),
                "redaction_status": "ARGV_VALUES_REDACTED_ENV_VALUES_OMITTED",
                "output_files": [
                    str(path) for path in (stdout_path, stderr_path) if path is not None
                ],
                "environment_names": sorted(spec.environment),
                "error_code": error_code,
            }
        )
        return validate_initialization_object(
            receipt, "ProcessExecutionReceipt", self._core_schema
        )
