from __future__ import annotations

import asyncio
import time
from typing import Any, Mapping
import uuid

from pr_os_app.ipc_auth import compute_challenge_response

from .errors import AuthenticationRejected, ConnectionLost, DesktopServiceError, RequestTimeout
from .protocol import (
    AUTH_ACK_SCHEMA,
    AUTH_RESPONSE_SCHEMA,
    CANCEL_SCHEMA,
    CHALLENGE_SCHEMA,
    DEFAULT_MAX_FRAME_BYTES,
    MODEL_EXECUTION_METHODS,
    PROTOCOL_VERSION,
    REQUEST_SCHEMA,
    RESPONSE_SCHEMA,
    read_frame,
    write_frame,
)
from .service import CANCEL_ACK_SCHEMA


SAFE_RECONNECT_METHODS = frozenset(
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
        "settings.workflow_exam_status",
        # This mutation is safe to replay only because operation_id is a
        # required durable idempotency key at the Inbox boundary.
        "inbox.soft_delete",
    }
)


class RemoteCallError(DesktopServiceError):
    def __init__(self, error: Mapping[str, Any]):
        self.remote_code = str(error.get("code", "REMOTE_ERROR"))
        self.remote_category = str(error.get("category", "internal"))
        self.remote_retryable = bool(error.get("retryable", False))
        super().__init__(self.remote_code)


class ApplicationServiceClient:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        secret: bytes,
        max_frame_bytes: int | None = None,
        default_timeout_seconds: float = 5.0,
    ):
        self.host = host
        self.port = port
        self.secret = bytes(secret)
        self.max_frame_bytes = max_frame_bytes
        self.default_timeout_seconds = default_timeout_seconds
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._cancel_pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._closing = False

    @property
    def connected(self) -> bool:
        return (
            self._writer is not None
            and not self._writer.is_closing()
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    async def connect(self) -> None:
        if self.connected:
            return
        auth_frame_limit = min(self.max_frame_bytes or DEFAULT_MAX_FRAME_BYTES, DEFAULT_MAX_FRAME_BYTES)
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            challenge_frame = await asyncio.wait_for(
                read_frame(reader, max_frame_bytes=auth_frame_limit),
                timeout=self.default_timeout_seconds,
            )
            if set(challenge_frame) != {"schema_version", "protocol_version", "challenge"}:
                raise AuthenticationRejected("IPC_AUTH_CHALLENGE_FIELDS_INVALID")
            if challenge_frame["schema_version"] != CHALLENGE_SCHEMA or challenge_frame["protocol_version"] != PROTOCOL_VERSION:
                raise AuthenticationRejected("IPC_AUTH_CHALLENGE_SCHEMA_INVALID")
            response = compute_challenge_response(self.secret, challenge_frame["challenge"])
            await write_frame(
                writer,
                {
                    "schema_version": AUTH_RESPONSE_SCHEMA,
                    "protocol_version": PROTOCOL_VERSION,
                    "response": response,
                },
                max_frame_bytes=auth_frame_limit,
            )
            ack = await asyncio.wait_for(
                read_frame(reader, max_frame_bytes=auth_frame_limit),
                timeout=self.default_timeout_seconds,
            )
            if ack.get("schema_version") != AUTH_ACK_SCHEMA or ack.get("authenticated") is not True:
                raise AuthenticationRejected("IPC_AUTH_ACK_INVALID")
        except Exception:
            writer.close()
            await writer.wait_closed()
            raise
        self._reader = reader
        self._writer = writer
        self._closing = False
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def _reader_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                frame = await read_frame(self._reader, max_frame_bytes=self.max_frame_bytes)
                schema = frame.get("schema_version")
                if schema == RESPONSE_SCHEMA:
                    future = self._pending.pop(str(frame.get("request_id")), None)
                    if future is not None and not future.done():
                        future.set_result(frame)
                elif schema == CANCEL_ACK_SCHEMA:
                    future = self._cancel_pending.pop(str(frame.get("request_id")), None)
                    if future is not None and not future.done():
                        future.set_result(frame)
                else:
                    raise ConnectionLost("IPC_UNEXPECTED_SERVER_FRAME")
        except (EOFError, asyncio.CancelledError):
            pass
        except Exception:
            pass
        finally:
            error = ConnectionLost("IPC_READER_STOPPED")
            for mapping in (self._pending, self._cancel_pending):
                for future in mapping.values():
                    if not future.done():
                        if self._closing:
                            future.cancel()
                        else:
                            future.set_exception(error)
                mapping.clear()

    async def _send(self, value: Mapping[str, Any]) -> None:
        if self._writer is None or self._writer.is_closing():
            raise ConnectionLost("IPC_NOT_CONNECTED")
        async with self._write_lock:
            await write_frame(self._writer, value, max_frame_bytes=self.max_frame_bytes)

    async def call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
        correlation_id: str | None = None,
        reconnect_safe_query: bool = True,
    ) -> Any:
        accepted_timeout = (timeout_seconds if timeout_seconds is not None else
                            None if method in MODEL_EXECUTION_METHODS else self.default_timeout_seconds)
        if accepted_timeout is not None and accepted_timeout <= 0:
            raise ValueError("CLIENT_TIMEOUT_INVALID")
        attempts = 2 if reconnect_safe_query and method in SAFE_RECONNECT_METHODS else 1
        last_error: BaseException | None = None
        for attempt in range(attempts):
            request_id: str | None = None
            future: asyncio.Future[dict[str, Any]] | None = None
            try:
                if not self.connected:
                    await self.connect()
                request_id = f"req-{uuid.uuid4().hex}"
                correlation = correlation_id or f"corr-{uuid.uuid4().hex}"
                future = asyncio.get_running_loop().create_future()
                self._pending[request_id] = future
                await self._send(
                    {
                        "schema_version": REQUEST_SCHEMA,
                        "protocol_version": PROTOCOL_VERSION,
                        "request_id": request_id,
                        "correlation_id": correlation,
                        "method": method,
                        "params": dict(params or {}),
                        **({"deadline_unix_ms": int((time.time() + accepted_timeout) * 1000)}
                           if accepted_timeout is not None else {}),
                    }
                )
                try:
                    response = await asyncio.wait_for(asyncio.shield(future), timeout=None if accepted_timeout is None else accepted_timeout + 0.25)
                except asyncio.TimeoutError as exc:
                    await self.cancel(request_id)
                    raise RequestTimeout("CLIENT_REQUEST_TIMEOUT") from exc
                if response.get("ok") is not True:
                    raise RemoteCallError(response.get("error") or {})
                return response.get("result")
            except (ConnectionLost, OSError) as exc:
                last_error = exc
                await self.close()
                if attempt + 1 >= attempts:
                    raise ConnectionLost("IPC_RECONNECT_EXHAUSTED") from exc
            finally:
                if request_id is not None:
                    self._pending.pop(request_id, None)
                if future is not None and future.done() and not future.cancelled():
                    try:
                        future.exception()
                    except BaseException:
                        pass
        raise ConnectionLost("IPC_CALL_FAILED") from last_error

    async def cancel(self, target_request_id: str) -> bool:
        if not self.connected:
            return False
        cancel_id = f"cancel-{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        self._cancel_pending[cancel_id] = future
        await self._send(
            {
                "schema_version": CANCEL_SCHEMA,
                "protocol_version": PROTOCOL_VERSION,
                "request_id": cancel_id,
                "target_request_id": target_request_id,
            }
        )
        try:
            ack = await asyncio.wait_for(future, timeout=self.default_timeout_seconds)
        except asyncio.TimeoutError:
            self._cancel_pending.pop(cancel_id, None)
            return False
        return bool(ack.get("cancelled"))

    async def close(self) -> None:
        self._closing = True
        task = self._reader_task
        self._reader_task = None
        if task is not None:
            task.cancel()
        writer = self._writer
        self._writer = None
        self._reader = None
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
            except (Exception, asyncio.TimeoutError):
                pass
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def __aenter__(self) -> "ApplicationServiceClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()


__all__ = ["ApplicationServiceClient", "RemoteCallError", "SAFE_RECONNECT_METHODS"]
