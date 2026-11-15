from __future__ import annotations

import asyncio
import inspect
import ipaddress
import time
from typing import Any, Mapping

from memorive_app.errors import ApplicationError
from memorive_app.ipc_auth import LoopbackPeerAuthenticator

from .adapters import FacadeAdapter
from .errors import DesktopServiceError, ProtocolViolation, RequestCancelled, RequestTimeout
from .locator import StableLocatorResolver
from .protocol import (
    AUTH_ACK_SCHEMA,
    AUTH_RESPONSE_SCHEMA,
    CANCEL_SCHEMA,
    CHALLENGE_SCHEMA,
    DEFAULT_MAX_FRAME_BYTES,
    MODEL_EXECUTION_METHODS,
    PROTOCOL_VERSION,
    REQUEST_SCHEMA,
    error_response,
    read_frame,
    success_response,
    validate_cancel,
    validate_request,
    write_frame,
)


CANCEL_ACK_SCHEMA = "DesktopIPCCancelAck-v1"
CANCELLABLE_METHODS = frozenset(
    {
        "list_jobs",
        "get_job",
        "get_attempt",
        "get_job_graph",
        "get_job_events",
        "watch_job_events",
        "list_action_requests",
        "get_artifact_bindings",
        "get_terminal_receipt",
        "resolve_job_locator",
        "lookup_start_by_idempotency_key",
        "get_status",
        "get_progress",
        "list_capabilities",
        "run_doctor",
        "build_support_bundle",
        "service.health",
        "service.resolve_locator",
        "service.capabilities",
        "settings.get_contract",
        "settings.get_state",
        "settings.user_get",
        "settings.external_sources_get",
        "settings.external_model_reference",
        "settings.preview",
        "settings.export_redacted",
        "settings.capability_state",
        "settings.credential_status",
        "settings.verify_api_model",
        "settings.verify_cli_model",
        "settings.cli_verification_status",
        "settings.test_workflow_node",
        "settings.diagnostics",
        "settings.support_bundle",
        "settings.effect_metrics",
        "inbox.get_contract",
        "inbox.get_auto_run",
        "inbox.recover",
        "inbox.projection",
        "inbox.refresh",
        "inbox.effect_metrics",
    }
)


def _safe_error(exc: BaseException) -> tuple[str, str, bool, str]:
    if isinstance(exc, DesktopServiceError):
        return exc.code, exc.category, exc.retryable, exc.code
    if isinstance(exc, ApplicationError):
        return exc.code, exc.category, bool(exc.retryable), exc.code
    if isinstance(exc, asyncio.TimeoutError | TimeoutError):
        return RequestTimeout.code, RequestTimeout.category, True, RequestTimeout.code
    if isinstance(exc, asyncio.CancelledError):
        return RequestCancelled.code, RequestCancelled.category, True, RequestCancelled.code
    if isinstance(exc, (TypeError, ValueError, KeyError, LookupError, StopIteration)):
        return "PARAMETER_INVALID", "validation", False, "PARAMETER_INVALID"
    return "INTERNAL_UNCLASSIFIED", "internal", False, "INTERNAL_UNCLASSIFIED"


class ApplicationServiceHost:
    def __init__(
        self,
        adapter: FacadeAdapter,
        *,
        secret: bytes,
        host: str = "127.0.0.1",
        port: int = 0,
        max_frame_bytes: int | None = None,
        request_timeout_seconds: float = 5.0,
        auth_timeout_seconds: float = 3.0,
    ):
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("IPC_BIND_HOST_NOT_LOOPBACK")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("IPC_PORT_INVALID")
        if request_timeout_seconds <= 0 or auth_timeout_seconds <= 0:
            raise ValueError("IPC_TIMEOUT_INVALID")
        self.adapter = adapter
        self.authenticator = LoopbackPeerAuthenticator(secret, ttl_seconds=30)
        self.host = host
        self.port = port
        self.max_frame_bytes = max_frame_bytes
        self.request_timeout_seconds = request_timeout_seconds
        self.auth_timeout_seconds = auth_timeout_seconds
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[asyncio.StreamWriter] = set()
        self._request_count = 0
        self._auth_rejection_count = 0
        self._started_monotonic: float | None = None
        self._closing = False

    @property
    def bound_port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("IPC_SERVER_NOT_STARTED")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("IPC_SERVER_ALREADY_STARTED")
        self._closing = False
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        self._started_monotonic = time.monotonic()

    async def close(self) -> None:
        self._closing = True
        server = self._server
        self._server = None
        if server is not None:
            server.close()
        writers = list(self._connections)
        for writer in writers:
            writer.close()
        if writers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(writer.wait_closed() for writer in writers), return_exceptions=True),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                pass
        self._connections.clear()
        if server is not None:
            # Python 3.13 waits for accepted transports too. Their writers
            # must be closed before waiting for listener shutdown.
            await server.wait_closed()

    def health(self) -> dict[str, Any]:
        return {
            "schema_version": "ApplicationServiceHealth-v1",
            "protocol_version": PROTOCOL_VERSION,
            "adapter_kind": self.adapter.adapter_kind,
            "request_count": self._request_count,
            "auth_rejection_count": self._auth_rejection_count,
            "connection_count": len(self._connections),
            "uptime_seconds": 0 if self._started_monotonic is None else round(time.monotonic() - self._started_monotonic, 6),
            "external_network_calls": 0,
            "external_model_calls": 0,
            "credential_value_reads": 0,
        }

    def _service_call(self, method: str, params: Mapping[str, Any]) -> Any:
        if method == "service.health":
            if params:
                raise ProtocolViolation("SERVICE_HEALTH_PARAMS_FORBIDDEN")
            return self.health()
        if method == "service.capabilities":
            if params:
                raise ProtocolViolation("SERVICE_CAPABILITIES_PARAMS_FORBIDDEN")
            return {
                "schema_version": "ApplicationServiceCapabilities-v1",
                "protocol_version": PROTOCOL_VERSION,
                "adapter": self.adapter.parity_projection(),
                "cancellable_methods": sorted(CANCELLABLE_METHODS),
                "max_frame_bytes": self.max_frame_bytes,
            }
        if method == "service.resolve_locator":
            if set(params) != {"locator"}:
                raise ProtocolViolation("SERVICE_LOCATOR_PARAMS_INVALID")
            return StableLocatorResolver(self.adapter.facade).resolve(params["locator"])
        return self.adapter.call(method, params)

    async def _execute(
        self,
        request: Mapping[str, Any],
        send: Any,
    ) -> None:
        try:
            # Model execution owns its provider/user resource policy. An IPC
            # read deadline must not abort a healthy in-flight model response.
            timeout = None if request['method'] in MODEL_EXECUTION_METHODS else self.request_timeout_seconds
            deadline = request.get("deadline_unix_ms")
            if deadline is not None:
                remaining = (deadline - int(time.time() * 1000)) / 1000.0
                if remaining <= 0:
                    raise RequestTimeout("IPC_DEADLINE_EXPIRED")
                timeout = remaining if timeout is None else min(timeout, remaining)
            result = await asyncio.wait_for(
                asyncio.to_thread(self._service_call, request["method"], request["params"]),
                timeout=timeout,
            )
            self._request_count += 1
            await send(success_response(request, result))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            code, category, retryable, message = _safe_error(exc)
            await send(
                error_response(
                    request["request_id"],
                    request["correlation_id"],
                    code=code,
                    category=category,
                    retryable=retryable,
                    message=message,
                )
            )

    async def _authenticate(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer_host: str
    ) -> bool:
        auth_frame_limit = min(self.max_frame_bytes or DEFAULT_MAX_FRAME_BYTES, DEFAULT_MAX_FRAME_BYTES)
        try:
            challenge = self.authenticator.issue_challenge(peer_host)
            await write_frame(
                writer,
                {
                    "schema_version": CHALLENGE_SCHEMA,
                    "protocol_version": PROTOCOL_VERSION,
                    "challenge": challenge,
                },
                max_frame_bytes=auth_frame_limit,
            )
            response = await asyncio.wait_for(
                read_frame(reader, max_frame_bytes=auth_frame_limit),
                timeout=self.auth_timeout_seconds,
            )
            if set(response) != {"schema_version", "protocol_version", "response"}:
                raise ProtocolViolation("IPC_AUTH_RESPONSE_FIELDS_INVALID")
            if response["schema_version"] != AUTH_RESPONSE_SCHEMA or response["protocol_version"] != PROTOCOL_VERSION:
                raise ProtocolViolation("IPC_AUTH_RESPONSE_SCHEMA_INVALID")
            if not self.authenticator.verify(peer_host, challenge, response["response"]):
                raise ProtocolViolation("IPC_AUTH_RESPONSE_REJECTED")
            await write_frame(
                writer,
                {
                    "schema_version": AUTH_ACK_SCHEMA,
                    "protocol_version": PROTOCOL_VERSION,
                    "authenticated": True,
                    "supported_protocol_versions": [PROTOCOL_VERSION],
                },
                max_frame_bytes=auth_frame_limit,
            )
            return True
        except Exception:
            self._auth_rejection_count += 1
            return False

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closing:
            writer.close()
            await writer.wait_closed()
            return
        peer = writer.get_extra_info("peername")
        peer_host = peer[0] if isinstance(peer, tuple) and peer else "invalid"
        try:
            if not ipaddress.ip_address(peer_host).is_loopback:
                self._auth_rejection_count += 1
                return
        except ValueError:
            self._auth_rejection_count += 1
            return
        self._connections.add(writer)
        pending: dict[str, tuple[asyncio.Task[None], Mapping[str, Any]]] = {}
        write_lock = asyncio.Lock()

        async def send(value: Mapping[str, Any]) -> None:
            async with write_lock:
                await write_frame(writer, value, max_frame_bytes=self.max_frame_bytes)

        try:
            if not await self._authenticate(reader, writer, peer_host):
                return
            while True:
                try:
                    frame = await read_frame(reader, max_frame_bytes=self.max_frame_bytes)
                except EOFError:
                    break
                schema = frame.get("schema_version")
                if schema == CANCEL_SCHEMA:
                    cancel = validate_cancel(frame)
                    target_entry = pending.get(cancel["target_request_id"])
                    target = None if target_entry is None else target_entry[0]
                    target_request = None if target_entry is None else target_entry[1]
                    cancellable = bool(
                        target
                        and not target.done()
                        and target_request is not None
                        and target_request["method"] in CANCELLABLE_METHODS
                    )
                    if cancellable:
                        await send(
                            error_response(
                                target_request["request_id"],
                                target_request["correlation_id"],
                                code=RequestCancelled.code,
                                category=RequestCancelled.category,
                                retryable=True,
                                message=RequestCancelled.code,
                            )
                        )
                        target.cancel()
                    await send(
                        {
                            "schema_version": CANCEL_ACK_SCHEMA,
                            "protocol_version": PROTOCOL_VERSION,
                            "request_id": cancel["request_id"],
                            "target_request_id": cancel["target_request_id"],
                            "cancelled": cancellable,
                        }
                    )
                    continue
                if schema != REQUEST_SCHEMA:
                    raise ProtocolViolation("IPC_MESSAGE_SCHEMA_UNSUPPORTED")
                request = validate_request(frame)
                request_id = request["request_id"]
                if request_id in pending:
                    raise ProtocolViolation("IPC_REQUEST_ID_DUPLICATE_ON_CONNECTION")
                task = asyncio.create_task(self._execute(request, send))
                pending[request_id] = (task, request)
                task.add_done_callback(lambda done, key=request_id: pending.pop(key, None))
        except ProtocolViolation:
            pass
        finally:
            tasks = [entry[0] for entry in pending.values()]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._connections.discard(writer)
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
            except (Exception, asyncio.TimeoutError):
                pass


__all__ = ["ApplicationServiceHost", "CANCELLABLE_METHODS", "CANCEL_ACK_SCHEMA"]
