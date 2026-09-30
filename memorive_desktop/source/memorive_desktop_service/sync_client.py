from __future__ import annotations

import asyncio
import threading
from typing import Any, Mapping

from .client import ApplicationServiceClient
from .protocol import MODEL_EXECUTION_METHODS


class ThreadedApplicationServiceClient:
    """Own one reusable control connection across synchronous desktop threads.

    Only the control connection is serialized. Model execution and long event
    waits use independent connections, so their duration cannot block task
    status, pause, cancellation, or other desktop controls. Retry eligibility
    remains entirely with ApplicationServiceClient; mutations are not replayed.
    """

    def __init__(self, host: str, port: int, *, secret: bytes):
        self._options = dict(host=host, port=port, secret=bytes(secret))
        self._lifecycle_lock = threading.Lock()
        self._closed = False
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._loop = asyncio.new_event_loop()
        self._active: set[asyncio.Task[Any]] = set()
        self._thread = threading.Thread(
            target=self._run, name="memo-service-rpc", daemon=True,
        )
        self._thread.start()
        self._ready.wait()
        if self._startup_error is not None:
            self._thread.join()
            raise RuntimeError("APPLICATION_SERVICE_TRANSPORT_START_FAILED") from self._startup_error

    def _run(self) -> None:
        try:
            asyncio.set_event_loop(self._loop)
            self._control = ApplicationServiceClient(**self._options)
            self._control_lock = asyncio.Lock()
        except BaseException as exc:
            self._startup_error = exc
        finally:
            self._ready.set()
        try:
            if self._startup_error is None:
                self._loop.run_forever()
        finally:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    async def _call(self, method: str, params: Mapping[str, Any]) -> Any:
        task = asyncio.current_task()
        assert task is not None
        self._active.add(task)
        try:
            if method in MODEL_EXECUTION_METHODS or method == "watch_job_events":
                client = ApplicationServiceClient(**self._options)
                try:
                    return await client.call(method, params)
                finally:
                    await client.close()
                    client.secret = b""
            async with self._control_lock:
                return await self._control.call(method, params)
        finally:
            self._active.discard(task)

    def call(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        if threading.current_thread() is self._thread:
            raise RuntimeError("APPLICATION_SERVICE_TRANSPORT_REENTRANT_CALL")
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("APPLICATION_SERVICE_TRANSPORT_CLOSED")
            future = asyncio.run_coroutine_threadsafe(
                self._call(method, dict(params or {})), self._loop,
            )
        return future.result()

    async def _shutdown(self) -> None:
        tasks = list(self._active)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._control.close()
        self._control.secret = b""

    def close(self) -> None:
        if threading.current_thread() is self._thread:
            raise RuntimeError("APPLICATION_SERVICE_TRANSPORT_REENTRANT_CLOSE")
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        try:
            future.result()
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
            self._options["secret"] = b""


__all__ = ["ThreadedApplicationServiceClient"]
