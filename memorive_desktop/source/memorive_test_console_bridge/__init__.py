"""Authenticated loopback bridge for an isolated Memorive console session.

Commands use native product stores and services in an isolated console session.
Research commands retain their inherited source admission gates. Protocol
support does not imply that live research execution is qualified.
"""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, wait
from copy import deepcopy
from .protocol import OPERATIONS, validate_command

from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit
import hashlib
import hmac
import json
import os
import re
import threading
import uuid


PROTOCOL_VERSION = "1.0"
BASE_PATH = "/memorive/test-bridge/v1"
SUPPORTED_OPERATIONS = tuple(OPERATIONS)
SESSION_PATTERN = re.compile(r"^review-[0-9]{8}T[0-9]{6}-[a-f0-9]{12}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
CONSOLE_ENVIRONMENT = (
    "MEMORIVE_TEST_CONSOLE_PROTOCOL",
    "MEMORIVE_TEST_CONSOLE_SESSION_ID",
    "MEMORIVE_TEST_CONSOLE_DATA_ROOT",
    "MEMORIVE_TEST_CONSOLE_DESCRIPTOR_PATH",
    "MEMORIVE_TEST_CONSOLE_TOKEN",
    "MEMORIVE_TEST_CONSOLE_BUILD_SHA256",
    "MEMORIVE_TEST_CONSOLE_AUTO_EXECUTION",
)


class ConsoleBridgeError(ValueError):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolved_child(root: Path, value: Path, label: str) -> Path:
    root_path = Path(root).resolve(strict=True)
    value_path = Path(value).resolve(strict=False)
    if value_path != root_path and root_path not in value_path.parents:
        raise ConsoleBridgeError(f"CONSOLE_PATH_OUTSIDE_DATA_ROOT:{label}")
    return value_path


def _read_owner_marker(root: Path, session_id: str) -> None:
    marker_path = root / ".console-session.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConsoleBridgeError("CONSOLE_OWNER_MARKER_INVALID") from error
    if marker != {
        "owner": "MEMORIVE_INDEPENDENT_CONSOLE",
        "session_id": session_id,
    }:
        raise ConsoleBridgeError("CONSOLE_OWNER_MARKER_MISMATCH")


@dataclass(frozen=True)
class ConsoleLaunch:
    session_id: str
    data_root: Path
    descriptor_path: Path
    token: str
    build_sha256: str

    @classmethod
    def from_environment(
        cls,
        executable_path: Path,
        environment: Mapping[str, str] | None = None,
    ) -> "ConsoleLaunch | None":
        values = dict(os.environ if environment is None else environment)
        present = {name for name in CONSOLE_ENVIRONMENT if values.get(name)}
        if not present:
            return None
        missing = set(CONSOLE_ENVIRONMENT) - present
        if missing:
            raise ConsoleBridgeError(
                "CONSOLE_ENVIRONMENT_INCOMPLETE:" + ",".join(sorted(missing))
            )
        if values["MEMORIVE_TEST_CONSOLE_PROTOCOL"] != PROTOCOL_VERSION:
            raise ConsoleBridgeError("CONSOLE_PROTOCOL_UNSUPPORTED")
        if values["MEMORIVE_TEST_CONSOLE_AUTO_EXECUTION"] != "DISABLED":
            raise ConsoleBridgeError("CONSOLE_AUTOMATIC_EXECUTION_NOT_DISABLED")
        session_id = values["MEMORIVE_TEST_CONSOLE_SESSION_ID"]
        if SESSION_PATTERN.fullmatch(session_id) is None:
            raise ConsoleBridgeError("CONSOLE_SESSION_ID_INVALID")
        token = values["MEMORIVE_TEST_CONSOLE_TOKEN"]
        if re.fullmatch(r"[a-f0-9]{64}", token) is None:
            raise ConsoleBridgeError("CONSOLE_TOKEN_INVALID")
        expected_sha256 = values["MEMORIVE_TEST_CONSOLE_BUILD_SHA256"]
        if SHA256_PATTERN.fullmatch(expected_sha256) is None:
            raise ConsoleBridgeError("CONSOLE_BUILD_SHA256_INVALID")
        executable = Path(executable_path).resolve(strict=True)
        if _file_sha256(executable) != expected_sha256:
            raise ConsoleBridgeError("CONSOLE_BUILD_SHA256_MISMATCH")
        root = Path(values["MEMORIVE_TEST_CONSOLE_DATA_ROOT"]).resolve(strict=True)
        descriptor = _resolved_child(
            root,
            Path(values["MEMORIVE_TEST_CONSOLE_DESCRIPTOR_PATH"]),
            "descriptor",
        )
        for name in ("LOCALAPPDATA", "APPDATA", "TEMP", "TMP"):
            raw = values.get(name)
            if not raw:
                raise ConsoleBridgeError(f"CONSOLE_REDIRECT_MISSING:{name}")
            redirected = _resolved_child(root, Path(raw), name)
            if not redirected.is_dir():
                raise ConsoleBridgeError(f"CONSOLE_REDIRECT_NOT_DIRECTORY:{name}")
        if values.get("MEMORIVE_WEBVIEW2_STORAGE_PATH"):
            raise ConsoleBridgeError("CONSOLE_WEBVIEW_OVERRIDE_FORBIDDEN")
        _read_owner_marker(root, session_id)
        return cls(
            session_id=session_id,
            data_root=root,
            descriptor_path=descriptor,
            token=token,
            build_sha256=expected_sha256,
        )

    def verify_write_paths(self, paths: Mapping[str, Path]) -> dict[str, str]:
        if not paths:
            raise ConsoleBridgeError("CONSOLE_WRITE_PATH_SET_EMPTY")
        verified: dict[str, str] = {}
        for label, path in paths.items():
            resolved = _resolved_child(self.data_root, path, label)
            verified[str(label)] = resolved.relative_to(self.data_root).as_posix() or "."
        return verified


class ConsoleBridge:
    """Small product-owned HTTP bridge with no external or model execution."""

    def __init__(
        self,
        launch: ConsoleLaunch,
        *,
        product_api: Any | None = None,
        system_effects_locked: bool = False,
        package_id: str,
        verified_write_paths: Mapping[str, str],
    ) -> None:
        if product_api is not None:
            system_effects_locked = (
                getattr(product_api, "_system_effects_enabled", True) is False
            )
        if system_effects_locked is not True:
            raise ConsoleBridgeError("CONSOLE_SYSTEM_EFFECTS_NOT_LOCKED")
        self.launch = launch
        self.product_api = product_api
        self._product_runtime_ready = product_api is not None
        self.package_id = str(package_id)
        self.verified_write_paths = dict(verified_write_paths)
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._commands_by_request: dict[str, dict[str, Any]] = {}
        self._commands_by_id: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memorive-console-command")
        self._futures = []
        self._stop = threading.Event()
        from .expressions import ExpressionController
        self._expressions = ExpressionController(launch.session_id, self._stop)
        self._adapter = None
        self._started = False
        self._closing = False
        self._access_enabled = False
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        if product_api is not None:
            self.bind_product_api(product_api)

    def bind_product_api(self, product_api: Any) -> None:
        if getattr(product_api, "_system_effects_enabled", True) is not False:
            raise ConsoleBridgeError("CONSOLE_SYSTEM_EFFECTS_NOT_LOCKED")
        with self._lock:
            self.product_api = product_api
            self._access_enabled = bool(getattr(product_api,'_read_console_permission',lambda:False)())
            from .product_adapter import ProductConsoleAdapter
            self._adapter = ProductConsoleAdapter(product_api, self.launch, self._stop)
            if not self._product_runtime_ready:
                self._product_runtime_ready = True
                self._event("MEMORIVE_PRODUCT_RUNTIME_READY")

    def _event(self, event_type: str, **fields: Any) -> None:
        self._events.append(
            {
                "sequence": len(self._events) + 1,
                "event_type": event_type,
                "occurred_at": _utc_now(),
                **fields,
            }
        )

    def _error(self, code: str, status: int = 400) -> tuple[int, dict[str, Any]]:
        return status, {"session_id": self.launch.session_id, "error_code": code}

    def _require_started(self) -> None:
        if not self._started:
            raise ConsoleBridgeError("SESSION_NOT_STARTED", 409)

    def set_access(self, enabled: bool) -> dict[str, Any]:
        """Native settings only. Remote console commands cannot enable access."""
        if type(enabled) is not bool:raise ValueError("CONSOLE_ACCESS_BOOLEAN_REQUIRED")
        with self._lock:
            if self._closing:raise ValueError("CONSOLE_SESSION_CLOSING")
            self._access_enabled = enabled
            self._event("MEMORIVE_CONSOLE_ACCESS_CHANGED",access_enabled=enabled)
        if not enabled:self._expressions.cancel()
        return {"access_enabled":enabled,"scope":"CURRENT_ISOLATED_SESSION",
                "running_business_tasks_cancelled":False}

    def _snapshot(self) -> dict[str, Any]:
        self._require_started()
        if not self._access_enabled:
            raise ConsoleBridgeError("CONSOLE_ACCESS_DISABLED",403)
        native = self._adapter.snapshot() if self._adapter and not self._closing else {}
        return {
            "session_id": self.launch.session_id,
            "observed_at": _utc_now(),
            "automatic_execution": False,
            "automatic_execution_locked": True,
            "console_access_enabled": self._access_enabled,
            "messages": native.get("messages", []),
            "reports": native.get("reports", []),
            "jobs": native.get("jobs", []),
            "inbox": native.get("inbox", []),
            "supported_operations": list(SUPPORTED_OPERATIONS),
            "model_calls_started": None,
            "model_call_measurement": "NOT_INSTRUMENTED_USE_NATIVE_CALL_RECEIPTS",
            "session_data_only": True,
            "product_runtime_state": (
                "READY" if self._product_runtime_ready else "INITIALIZING"
            ),
        }

    def _start_session(self, body: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "protocol_version",
            "session_id",
            "data_root",
            "build_sha256",
            "automatic_execution",
            "close_policy",
        }
        if set(body) != required:
            raise ConsoleBridgeError("SESSION_START_FIELDS_INVALID")
        expected = {
            "protocol_version": PROTOCOL_VERSION,
            "session_id": self.launch.session_id,
            "data_root": str(self.launch.data_root),
            "build_sha256": self.launch.build_sha256,
            "automatic_execution": False,
            "close_policy": "DISCARD_ON_CLOSE",
        }
        if dict(body) != expected:
            raise ConsoleBridgeError("SESSION_REQUEST_REJECTED")
        if self._closing:
            raise ConsoleBridgeError("SESSION_NOT_ACCEPTING_COMMANDS", 409)
        if not self._started:
            self._started = True
            self._event("MEMORIVE_SESSION_READY")
        return {
            "protocol_version": PROTOCOL_VERSION,
            "session_id": self.launch.session_id,
            "state": "READY",
            "implementation_kind": "MEMORIVE_BUILD",
            "build_sha256": self.launch.build_sha256,
            "data_root": str(self.launch.data_root),
            "automatic_execution": False,
            "automatic_execution_locked": True,
            "all_writes_inside_data_root": True,
            "cleanup_supported": True,
            "operations": list(SUPPORTED_OPERATIONS),
            "package_id": self.package_id,
            "console_access_enabled": self._access_enabled,
            "console_access_required": True,
        }

    def _leaderboard_diagnostics(self) -> dict[str, Any]:
        # Local projection only: diagnostics never fetch metadata or change prefs.
        if not self._access_enabled:
            return {'status':'CONSOLE_ACCESS_DISABLED'}
        call = getattr(self.product_api, 'call', None)
        if not self._product_runtime_ready or not callable(call):
            return {'status':'NOT_READY'}
        try:
            state = call('settings.leaderboard_get', {})
            fx = state.get('fx')
            return {
                'status':'READY', 'schema_version':state['schema_version'],
                'currency':state['preferences']['currency'],
                'catalog_count':state['catalog_policy']['default_count'],
                'ranked_count':sum(m['catalog_eligible'] and m['ability'] is not None for m in state['rows']),
                'algorithm':state['algorithm']['id'],
                'sources':[{'id':s['id'],'status':s['status'],'scored_count':s['scored_count']} for s in state['sources']],
                'fx':None if not fx else {'base':'USD','rate_date':fx['rateDate'],'units_per_usd':fx['unitsPerUsd']},
                'network_refresh_requested':False,
            }
        except Exception:
            return {'status':'UNAVAILABLE','error_code':'LEADERBOARD_DIAGNOSTICS_UNAVAILABLE'}

    def _diagnostics(self) -> dict[str, Any]:
        return {
            "schema_version": "MemoriveConsoleDiagnostics-v1",
            "protocol_version": PROTOCOL_VERSION,
            "implementation_kind": "MEMORIVE_BUILD",
            "package_id": self.package_id,
            "ephemeral_profile": True,
            "automatic_execution": False,
            "automatic_execution_locked": True,
            "system_effects_enabled": False,
            "all_writes_inside_data_root": True,
            "verified_write_paths": dict(self.verified_write_paths),
            "model_calls_started": None,
            "model_call_measurement": "NOT_INSTRUMENTED_USE_NATIVE_CALL_RECEIPTS",
            "credential_value_reads": None,
            "research_execution": {"periodic_report":"NATIVE_DESKTOP_EVENTS", "discovery":"MANUAL_SELECTED_METADATA_SOURCES", "entrypoint":"ProductApi.research", "requires_selected_literature_source":True},
            "leaderboard": self._leaderboard_diagnostics(),
            "product_runtime_state": (
                "READY" if self._product_runtime_ready else "INITIALIZING"
            ),
        }

    def _command(self, body: Mapping[str, Any]) -> dict[str, Any]:
        self._require_started()
        if self._closing:
            raise ConsoleBridgeError("SESSION_NOT_ACCEPTING_COMMANDS", 409)
        if set(body) != {
            "session_id",
            "request_id",
            "operation",
            "params",
            "allow_model_calls",
        }:
            raise ConsoleBridgeError("COMMAND_FIELDS_INVALID")
        if body.get("session_id") != self.launch.session_id:
            raise ConsoleBridgeError("SESSION_MISMATCH")
        request_id = body.get("request_id")
        if not isinstance(request_id, str) or IDENTIFIER_PATTERN.fullmatch(request_id) is None:
            raise ConsoleBridgeError("REQUEST_ID_INVALID")
        fingerprint = hashlib.sha256(_canonical_bytes(body)).hexdigest()
        previous = self._commands_by_request.get(request_id)
        if previous is not None:
            if previous["request_sha256"] != fingerprint:
                raise ConsoleBridgeError("IDEMPOTENCY_CONFLICT", 409)
            return {**previous, "replayed": True}
        try:
            validate_command({k:v for k,v in body.items() if k != "session_id"})
        except ValueError as error:
            raise ConsoleBridgeError(str(error)) from None
        if not self._access_enabled and body["operation"] not in {"diagnostics","expression.cancel"}:
            raise ConsoleBridgeError("CONSOLE_ACCESS_DISABLED",403)
        if not self._product_runtime_ready and body["operation"] != "diagnostics":
            raise ConsoleBridgeError("PRODUCT_RUNTIME_INITIALIZING", 409)
        if len(self._commands_by_id) >= 1000:
            raise ConsoleBridgeError("SESSION_COMMAND_LIMIT", 429)
        record = {
            "session_id": self.launch.session_id, "request_id": request_id,
            "command_id": "memorive-command-" + uuid.uuid4().hex,
            "operation": body["operation"], "state": "QUEUED",
            "request_sha256": fingerprint, "created_at": _utc_now(),
        }
        self._commands_by_request[request_id] = record
        self._commands_by_id[record["command_id"]] = record
        if body["operation"] == "diagnostics":
            record.update(state="SUCCEEDED", result={"diagnostics":self._diagnostics()}, completed_at=_utc_now())
        elif body["operation"].startswith("expression."):
            # Accept/cancel only; the visual worker runs independently. A model
            # or report command in the executor must not delay preview cancel.
            try:
                value = (self._expressions.trigger(body["params"],request_id)
                         if body["operation"]=="expression.trigger" else self._expressions.cancel())
                record.update(state="SUCCEEDED",result=value,completed_at=_utc_now())
            except ValueError as error:
                record.update(state="FAILED",error_code=str(error),completed_at=_utc_now())
            self._event("MEMORIVE_COMMAND_COMPLETED",command_id=record["command_id"],operation=body["operation"],state=record["state"])
        else:
            self._futures.append(self._executor.submit(self._execute, record, deepcopy(dict(body))))
        return deepcopy(record)

    def _execute(self, record, body):
        with self._lock:
            if self._stop.is_set():
                record.update(state="FAILED", error_code="SESSION_CLOSING", completed_at=_utc_now())
                return
            if not self._access_enabled:
                record.update(state="FAILED",error_code="CONSOLE_ACCESS_DISABLED",completed_at=_utc_now())
                return
            record["state"] = "RUNNING"
        try:
            if body["operation"] == "expression.trigger":
                result = self._expressions.trigger(body["params"], body["request_id"])
            elif body["operation"] == "expression.cancel":
                result = self._expressions.cancel()
            else:
                result = self._adapter.execute(body["operation"], body["params"], body["request_id"], body["allow_model_calls"])
            terminal = {"state":"SUCCEEDED", "result":result}
        except Exception as error:
            code = getattr(error, "code", str(error))
            if not isinstance(code,str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,120}",code):
                code = "PRODUCT_OPERATION_FAILED"
            terminal = {"state":"FAILED", "error_code":code}
        with self._lock:
            record.update(**terminal, completed_at=_utc_now())
            self._event("MEMORIVE_COMMAND_COMPLETED", command_id=record["command_id"], operation=body["operation"], state=record["state"])


    def _end_session(self, body: Mapping[str, Any]) -> dict[str, Any]:
        self._require_started()
        if dict(body) != {
            "session_id": self.launch.session_id,
            "close_policy": "DISCARD_ON_CLOSE",
        }:
            raise ConsoleBridgeError("SESSION_END_REQUEST_INVALID")
        with self._lock:
            self._closing = True
            self._stop.set()
            if self.product_api is not None and hasattr(self.product_api, '_research'):
                self.product_api._research.stop.set()
        expressions_stopped = self._expressions.close()
        _, pending = wait(self._futures, timeout=2.0)
        if pending or not expressions_stopped:
            return {"session_id":self.launch.session_id, "state":"QUIESCING", "all_work_stopped":False, "accepted_new_commands":False}
        if self.product_api is not None:
            self.product_api.close()
        self._executor.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            self._event("MEMORIVE_SESSION_QUIESCED")
        return {"session_id":self.launch.session_id, "state":"QUIESCED", "all_work_stopped":True, "accepted_new_commands":False, "supported_work_count":len(self._commands_by_id)}

    def handle_get(self, raw_path: str) -> tuple[int, dict[str, Any]]:
        parsed = urlsplit(raw_path)
        if not parsed.path.startswith(BASE_PATH):
            return self._error("NOT_FOUND", 404)
        route = parsed.path[len(BASE_PATH) :]
        # These cached reads deliberately never acquire the heavy snapshot lock.
        # HTTP authentication/owner session validation still happens in _handler.
        if route in {"/expressions/catalog", "/expressions/state"}:
            if parsed.query:return self._error("EXPRESSION_QUERY_INVALID")
            if not self._started:return self._error("SESSION_NOT_STARTED",409)
            if route.endswith("/catalog"):
                from .expressions import catalog
                return 200, {"session_id":self.launch.session_id, **catalog()}
            return 200, {**self._expressions.state(),"access_enabled":self._access_enabled}
        with self._lock:
            try:
                if route == "/snapshot" and not parsed.query:
                    return 200, self._snapshot()
                if route == "/events":
                    self._require_started()
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    if set(query) - {"after", "limit"}:
                        raise ConsoleBridgeError("EVENT_QUERY_INVALID")
                    after = int(query.get("after", ["0"])[0])
                    limit = int(query.get("limit", ["100"])[0])
                    if after < 0 or not 1 <= limit <= 100:
                        raise ConsoleBridgeError("EVENT_CURSOR_INVALID")
                    return 200, {
                        "session_id": self.launch.session_id,
                        "events": [
                            dict(row)
                            for row in self._events
                            if row["sequence"] > after
                        ][:limit],
                    }
                prefix = "/commands/"
                if route.startswith(prefix) and not parsed.query:
                    self._require_started()
                    command_id = route[len(prefix) :]
                    if IDENTIFIER_PATTERN.fullmatch(command_id) is None:
                        raise ConsoleBridgeError("COMMAND_ID_INVALID")
                    record = self._commands_by_id.get(command_id)
                    if record is None:
                        raise ConsoleBridgeError("COMMAND_NOT_FOUND", 404)
                    if not self._access_enabled and record['operation'] not in {'diagnostics','expression.trigger','expression.cancel'}:
                        raise ConsoleBridgeError('CONSOLE_ACCESS_DISABLED',403)
                    return 200, dict(record)
                raise ConsoleBridgeError("NOT_FOUND", 404)
            except ConsoleBridgeError as error:
                return self._error(error.code, error.status)
            except (ValueError, TypeError):
                return self._error("EVENT_QUERY_INVALID")

    def handle_post(
        self, raw_path: str, body: Mapping[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        parsed = urlsplit(raw_path)
        if parsed.query or not parsed.path.startswith(BASE_PATH):
            return self._error("NOT_FOUND", 404)
        route = parsed.path[len(BASE_PATH) :]
        if route == "/session/end":
            try:
                return 200, self._end_session(body)
            except ConsoleBridgeError as error:
                return self._error(error.code,error.status)
        with self._lock:
            try:
                if route == "/session/start":
                    return 200, self._start_session(body)
                if route == "/commands":
                    return 200, self._command(body)
                if route == "/session/end":
                    return 200, self._end_session(body)
                raise ConsoleBridgeError("NOT_FOUND", 404)
            except ConsoleBridgeError as error:
                return self._error(error.code, error.status)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "Memorive-Console-Bridge"
            sys_version = ""

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _authorized(self) -> bool:
                server = bridge._server
                return bool(
                    server is not None
                    and self.headers.get("Host") == f"127.0.0.1:{server.server_port}"
                    and self.headers.get("Origin") is None
                    and hmac.compare_digest(
                        self.headers.get("Authorization", ""),
                        "Bearer " + bridge.launch.token,
                    )
                    and self.headers.get("X-Memorive-Session")
                    == bridge.launch.session_id
                )

            def _send(self, status: int, value: Mapping[str, Any]) -> None:
                data = _canonical_bytes(dict(value))
                if len(data) > MAX_RESPONSE_BYTES:
                    status = 500
                    data = _canonical_bytes(
                        {
                            "session_id": bridge.launch.session_id,
                            "error_code": "RESPONSE_TOO_LARGE",
                        }
                    )
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

            def _deny(self) -> None:
                self._send(
                    403,
                    {
                        "session_id": bridge.launch.session_id,
                        "error_code": "AUTH_REQUIRED",
                    },
                )

            def do_GET(self) -> None:
                if not self._authorized():
                    self._deny()
                    return
                status, value = bridge.handle_get(self.path)
                self._send(status, value)

            def do_POST(self) -> None:
                if not self._authorized():
                    self._deny()
                    return
                if self.headers.get("Transfer-Encoding"):
                    self._send(*bridge._error("TRANSFER_ENCODING_FORBIDDEN"))
                    return
                if not self.headers.get("Content-Type", "").lower().startswith(
                    "application/json"
                ):
                    self._send(*bridge._error("CONTENT_TYPE_INVALID"))
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= MAX_REQUEST_BYTES:
                        raise ConsoleBridgeError("BODY_SIZE_INVALID")
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ConsoleBridgeError("BODY_OBJECT_REQUIRED")
                    status, value = bridge.handle_post(self.path, body)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    status, value = bridge._error("BODY_JSON_INVALID")
                except (ValueError, TypeError, ConsoleBridgeError) as error:
                    code = error.code if isinstance(error, ConsoleBridgeError) else "BODY_SIZE_INVALID"
                    status, value = bridge._error(code)
                self._send(status, value)

            def do_OPTIONS(self) -> None:
                self._deny()

        return Handler

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._server is not None:
                raise ConsoleBridgeError("CONSOLE_BRIDGE_ALREADY_STARTED")
            server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
            server.daemon_threads = True
            self._server = server
            self._thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.1},
                name="memorive-console-bridge",
                daemon=True,
            )
            self._thread.start()
            descriptor = {
                "protocol_version": PROTOCOL_VERSION,
                "host": "127.0.0.1",
                "port": server.server_port,
                "session_id": self.launch.session_id,
                "pid": os.getpid(),
                "implementation_kind": "MEMORIVE_BUILD",
                "secret_value_recorded": False,
            }
            temporary = self.launch.descriptor_path.with_name(
                "." + self.launch.descriptor_path.name + f".{os.getpid()}.tmp"
            )
            temporary.write_bytes(_canonical_bytes(descriptor) + b"\n")
            os.replace(temporary, self.launch.descriptor_path)
            return descriptor

    def close(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            self._closing = True
            self._stop.set()
            self._server = None
            self._thread = None
        self._expressions.close()
        self._executor.shutdown(wait=False, cancel_futures=True)
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)


__all__ = [
    "BASE_PATH",
    "CONSOLE_ENVIRONMENT",
    "ConsoleBridge",
    "ConsoleBridgeError",
    "ConsoleLaunch",
    "PROTOCOL_VERSION",
    "SUPPORTED_OPERATIONS",
]
