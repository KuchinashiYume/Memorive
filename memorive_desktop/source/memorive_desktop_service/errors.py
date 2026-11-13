from __future__ import annotations

from typing import Any


class DesktopServiceError(RuntimeError):
    code = "DESKTOP_SERVICE_ERROR"
    category = "internal"
    retryable = False

    def __init__(self, message: str | None = None, **context: Any):
        self.context = dict(context)
        super().__init__(message or self.code)


class ProtocolViolation(DesktopServiceError, ValueError):
    code = "IPC_PROTOCOL_VIOLATION"
    category = "validation"


class AuthenticationRejected(DesktopServiceError):
    code = "IPC_AUTH_REJECTED"
    category = "security"


class SchemaIncompatible(ProtocolViolation):
    code = "SCHEMA_INCOMPATIBLE"


class RequestTimeout(DesktopServiceError, TimeoutError):
    code = "IPC_REQUEST_TIMEOUT"
    category = "transport"
    retryable = True


class RequestCancelled(DesktopServiceError):
    code = "IPC_REQUEST_CANCELLED"
    category = "transport"
    retryable = True


class ConnectionLost(DesktopServiceError, ConnectionError):
    code = "IPC_CONNECTION_LOST"
    category = "transport"
    retryable = True


class ControlStoreCorrupt(DesktopServiceError):
    code = "CONTROL_STORE_CORRUPT"
    category = "recovery"


class SensitivePersistenceRejected(DesktopServiceError):
    code = "CONTROL_STORE_SENSITIVE_FIELD_REJECTED"
    category = "security"


__all__ = [
    "AuthenticationRejected",
    "ConnectionLost",
    "ControlStoreCorrupt",
    "DesktopServiceError",
    "ProtocolViolation",
    "RequestCancelled",
    "RequestTimeout",
    "SchemaIncompatible",
    "SensitivePersistenceRejected",
]
