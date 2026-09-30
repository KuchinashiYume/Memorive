from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any


CONTRACT_REVISION = "INBOX-InboxImportContract-r1"
IMPORTABLE_EXTENSIONS = frozenset(
    {".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".txt", ".md", ".json"}
)
INTERNAL_STATES = frozenset(
    {"IMPORTING", "QUEUED", "PROCESSING", "ERROR", "HANDED_OFF", "TRASHED", "DEDUPLICATED"}
)
DISPLAY_STATES = {"QUEUED": "已排队", "PROCESSING": "处理中", "ERROR": "异常"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class InboxError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest().upper()


def immutable(value: Any) -> Any:
    return deepcopy(value)


def require_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise InboxError(f"INBOX_{field.upper()}_INVALID")
    return value


def require_item_state(value: Any) -> str:
    if value not in INTERNAL_STATES:
        raise InboxError("INBOX_ITEM_STATE_INVALID")
    return str(value)


def item_id_for(request_id: str, item_index: int, source_name: str) -> str:
    digest = hashlib.sha256(f"{request_id}\0{item_index}\0{source_name}".encode("utf-8")).hexdigest()
    return f"inbox_{digest[:24]}"


def batch_id_for(request_id: str) -> str:
    digest = hashlib.sha256(f"batch\0{request_id}".encode("utf-8")).hexdigest()
    return f"batch_{digest[:24]}"


__all__ = [
    "CONTRACT_REVISION",
    "DISPLAY_STATES",
    "IMPORTABLE_EXTENSIONS",
    "INTERNAL_STATES",
    "InboxError",
    "batch_id_for",
    "canonical_bytes",
    "canonical_sha256",
    "immutable",
    "item_id_for",
    "require_identifier",
    "require_item_state",
    "utc_now",
]
