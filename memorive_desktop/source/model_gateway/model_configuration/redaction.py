from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from .contracts import ModelConfigurationError


REDACT_KEYS = {
    "api_key",
    "secret",
    "secret_value",
    "password",
    "token",
    "authorization",
    "cookie",
    "set_cookie",
}


def redact_public(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in REDACT_KEYS or normalized.endswith("_secret") or normalized.endswith("_token"):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = redact_public(item)
        return result
    if isinstance(value, list):
        return [redact_public(item) for item in value]
    if isinstance(value, tuple):
        return [redact_public(item) for item in value]
    return value


def assert_forbidden_values_absent(value: Any, forbidden_values: Sequence[str]) -> None:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    hits = [item for item in forbidden_values if item and item in serialized]
    if hits:
        raise ModelConfigurationError("SECRET_VALUE_LEAK_DETECTED", [f"hit_count={len(hits)}"])


__all__ = ["assert_forbidden_values_absent", "redact_public"]
