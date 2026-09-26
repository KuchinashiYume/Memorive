from __future__ import annotations

import asyncio
import json
import re
import struct
from typing import Any, Mapping

from .errors import ProtocolViolation, SchemaIncompatible


PROTOCOL_VERSION = "1.0"
REQUEST_SCHEMA = "DesktopIPCRequest-v1"
RESPONSE_SCHEMA = "DesktopIPCResponse-v1"
CANCEL_SCHEMA = "DesktopIPCCancel-v1"
CHALLENGE_SCHEMA = "DesktopIPCAuthChallenge-v1"
AUTH_RESPONSE_SCHEMA = "DesktopIPCAuthResponse-v1"
AUTH_ACK_SCHEMA = "DesktopIPCAuthAck-v1"
DEFAULT_MAX_FRAME_BYTES = 1024 * 1024
MODEL_EXECUTION_METHODS = frozenset({
    "settings.execute_structured_chat", "settings.report_profile_execute",
    "settings.research_chat_execute", "settings.research_image_execute",
    "settings.research_embedding_execute",
})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolViolation("IPC_JSON_ENCODE_INVALID") from exc


def _reject_constant(value: str) -> None:
    raise ProtocolViolation(f"IPC_JSON_CONSTANT_FORBIDDEN:{value}")


def decode_json_object(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    except UnicodeDecodeError as exc:
        raise ProtocolViolation("IPC_FRAME_NOT_UTF8") from exc
    except json.JSONDecodeError as exc:
        raise ProtocolViolation("IPC_FRAME_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise ProtocolViolation("IPC_FRAME_OBJECT_REQUIRED")
    return value


def _require_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ProtocolViolation(f"IPC_{field.upper()}_INVALID")
    return value


def _exact_fields(value: Mapping[str, Any], *, required: set[str], optional: set[str], label: str) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing:
        raise ProtocolViolation(f"{label}_MISSING_FIELD:{','.join(missing)}")
    if unknown:
        raise ProtocolViolation(f"{label}_UNKNOWN_FIELD:{','.join(unknown)}")


def validate_protocol_version(value: Any) -> str:
    if value != PROTOCOL_VERSION:
        raise SchemaIncompatible(f"IPC_PROTOCOL_VERSION_UNSUPPORTED:{value}")
    return PROTOCOL_VERSION


def validate_request(value: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(
        value,
        required={"schema_version", "protocol_version", "request_id", "correlation_id", "method", "params"},
        optional={"deadline_unix_ms"},
        label="IPC_REQUEST",
    )
    if value["schema_version"] != REQUEST_SCHEMA:
        raise SchemaIncompatible("IPC_REQUEST_SCHEMA_UNSUPPORTED")
    validate_protocol_version(value["protocol_version"])
    request_id = _require_identifier(value["request_id"], "request_id")
    correlation_id = _require_identifier(value["correlation_id"], "correlation_id")
    method = _require_identifier(value["method"], "method")
    if not isinstance(value["params"], dict):
        raise ProtocolViolation("IPC_PARAMS_OBJECT_REQUIRED")
    deadline = value.get("deadline_unix_ms")
    if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, int) or deadline <= 0):
        raise ProtocolViolation("IPC_DEADLINE_INVALID")
    return {
        "schema_version": REQUEST_SCHEMA,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "correlation_id": correlation_id,
        "method": method,
        "params": json.loads(canonical_json_bytes(value["params"]).decode("utf-8")),
        **({"deadline_unix_ms": deadline} if deadline is not None else {}),
    }


def validate_cancel(value: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(
        value,
        required={"schema_version", "protocol_version", "request_id", "target_request_id"},
        optional=set(),
        label="IPC_CANCEL",
    )
    if value["schema_version"] != CANCEL_SCHEMA:
        raise SchemaIncompatible("IPC_CANCEL_SCHEMA_UNSUPPORTED")
    validate_protocol_version(value["protocol_version"])
    return {
        "schema_version": CANCEL_SCHEMA,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": _require_identifier(value["request_id"], "request_id"),
        "target_request_id": _require_identifier(value["target_request_id"], "target_request_id"),
    }


def success_response(request: Mapping[str, Any], result: Any) -> dict[str, Any]:
    return {
        "schema_version": RESPONSE_SCHEMA,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request["request_id"],
        "correlation_id": request["correlation_id"],
        "ok": True,
        "result": result,
        "error": None,
    }


def error_response(
    request_id: str,
    correlation_id: str,
    *,
    code: str,
    category: str,
    retryable: bool,
    message: str,
) -> dict[str, Any]:
    return {
        "schema_version": RESPONSE_SCHEMA,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "correlation_id": correlation_id,
        "ok": False,
        "result": None,
        "error": {
            "schema_version": "DesktopIPCError-v1",
            "code": code,
            "category": category,
            "retryable": retryable,
            "message": message[:512],
        },
    }


async def read_frame(reader: asyncio.StreamReader, *, max_frame_bytes: int | None = DEFAULT_MAX_FRAME_BYTES) -> dict[str, Any]:
    try:
        header = await reader.readexactly(4)
    except asyncio.IncompleteReadError as exc:
        raise EOFError("IPC_STREAM_CLOSED") from exc
    length = struct.unpack("!I", header)[0]
    if length <= 0:
        raise ProtocolViolation("IPC_FRAME_EMPTY")
    if max_frame_bytes is not None and length > max_frame_bytes:
        raise ProtocolViolation("IPC_FRAME_OVERSIZE")
    try:
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise ProtocolViolation("IPC_FRAME_TRUNCATED") from exc
    return decode_json_object(payload)


async def write_frame(
    writer: asyncio.StreamWriter,
    value: Mapping[str, Any],
    *,
    max_frame_bytes: int | None = DEFAULT_MAX_FRAME_BYTES,
) -> None:
    payload = canonical_json_bytes(dict(value))
    # uint32 is the existing wire-format limit, not a model-output policy.
    if not payload or len(payload) > 0xFFFFFFFF or (max_frame_bytes is not None and len(payload) > max_frame_bytes):
        raise ProtocolViolation("IPC_FRAME_OVERSIZE")
    writer.write(struct.pack("!I", len(payload)) + payload)
    await writer.drain()


__all__ = [
    "AUTH_ACK_SCHEMA",
    "AUTH_RESPONSE_SCHEMA",
    "CANCEL_SCHEMA",
    "CHALLENGE_SCHEMA",
    "DEFAULT_MAX_FRAME_BYTES",
    "PROTOCOL_VERSION",
    "REQUEST_SCHEMA",
    "RESPONSE_SCHEMA",
    "canonical_json_bytes",
    "decode_json_object",
    "error_response",
    "read_frame",
    "success_response",
    "validate_cancel",
    "validate_protocol_version",
    "validate_request",
    "write_frame",
]
