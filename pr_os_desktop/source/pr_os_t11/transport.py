from __future__ import annotations

import asyncio
from typing import Any, Mapping

from pr_os_desktop_service.client import ApplicationServiceClient
from pr_os_desktop_service.service import ApplicationServiceHost


class LoopbackApplicationService:
    """Real authenticated loopback transport around the frozen service host."""

    def __init__(self, adapter: Any, *, secret: bytes = b"p08-t11-isolated-loopback-secret"):
        self.adapter = adapter
        self.secret = bytes(secret)
        self.host: ApplicationServiceHost | None = None
        self.client: ApplicationServiceClient | None = None
        self.restart_count = 0

    async def start(self) -> None:
        if self.host is not None:
            raise RuntimeError("T11_SERVICE_ALREADY_STARTED")
        host = ApplicationServiceHost(
            self.adapter,
            secret=self.secret,
            host="127.0.0.1",
            port=0,
            request_timeout_seconds=3.0,
            auth_timeout_seconds=3.0,
        )
        await host.start()
        client = ApplicationServiceClient(
            "127.0.0.1",
            host.bound_port,
            secret=self.secret,
            default_timeout_seconds=3.0,
        )
        await client.connect()
        self.host = host
        self.client = client

    async def call(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        if self.client is None:
            raise RuntimeError("T11_SERVICE_NOT_STARTED")
        return await self.client.call(method, params or {})

    async def stop(self) -> None:
        client, host = self.client, self.host
        self.client = None
        self.host = None
        if client is not None:
            await client.close()
        if host is not None:
            await host.close()

    async def restart(self) -> None:
        await self.stop()
        self.restart_count += 1
        await self.start()

    async def __aenter__(self) -> "LoopbackApplicationService":
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.stop()
