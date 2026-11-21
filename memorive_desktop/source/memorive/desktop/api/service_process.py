"""ServiceProcessManager: starts the background service, calls it and stops it.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any
import uuid
from memorive.platform.json_files import write_json_indented
from memorive.desktop.api.common import SECRET_ENV_NAME, SERVICE_REVISION, process_shutdown_level


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
                    try:
                        descriptor = json.loads(self._endpoint_file.read_text(encoding="utf-8"))
                    except PermissionError:
                        # Windows refuses the read while the service is replacing the file; read it again.
                        time.sleep(0.05)
                        continue
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
                write_json_indented(stop_file,dict(payload,hmac_sha256=signature))
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
