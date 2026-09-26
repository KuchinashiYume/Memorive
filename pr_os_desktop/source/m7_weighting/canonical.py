"""Accepted-P03/T01-parity RFC 8785 helpers for T03 candidate objects.

The serializer intentionally preserves the accepted implementation's UTF-16
object-key ordering and I-JSON numeric boundary.  It must not be replaced by
``json.dumps(sort_keys=True)``.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from decimal import Decimal
from typing import Any


def jcs_string(value: str) -> str:
    """Serialize one string in the accepted RFC 8785/I-JSON domain."""

    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError("RFC8785 JCS rejects lone UTF-16 surrogate code points")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def jcs_number(value: int | float) -> str:
    """Serialize one finite I-JSON number with accepted T01 parity."""

    if isinstance(value, bool):
        raise TypeError("boolean is not a JCS number")
    if isinstance(value, int):
        if abs(value) > 9_007_199_254_740_991:
            raise ValueError("integer is outside the I-JSON exact IEEE-754 range")
        return str(value)
    if not math.isfinite(value):
        raise ValueError("RFC8785 JCS rejects NaN and infinity")
    if value == 0:
        return "0"
    rendered = repr(value).lower()
    magnitude = abs(value)
    if 1e-6 <= magnitude < 1e21:
        fixed = format(Decimal(rendered), "f")
        if "." in fixed:
            fixed = fixed.rstrip("0").rstrip(".")
        return fixed
    if "e" not in rendered:
        rendered = format(Decimal(rendered).normalize(), "e")
    coefficient, exponent_text = rendered.split("e", 1)
    if coefficient.endswith(".0"):
        coefficient = coefficient[:-2]
    exponent = int(exponent_text)
    return f"{coefficient}e{'+' if exponent >= 0 else ''}{exponent}"


def jcs_serialize(value: Any) -> str:
    """Serialize a JSON-domain value according to the accepted JCS rules."""

    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return jcs_string(value)
    if isinstance(value, (int, float)):
        return jcs_number(value)
    if isinstance(value, list):
        return "[" + ",".join(jcs_serialize(item) for item in value) + "]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("RFC8785 JCS object keys must be strings")
        keys = sorted(value, key=lambda key: key.encode("utf-16-be"))
        return "{" + ",".join(
            f"{jcs_string(key)}:{jcs_serialize(value[key])}" for key in keys
        ) + "}"
    raise TypeError(f"unsupported RFC8785 JCS value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return accepted-parity RFC 8785 UTF-8 bytes for a JSON value."""

    return jcs_serialize(value).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    """Return an uppercase SHA-256 hexadecimal digest for exact bytes."""

    return hashlib.sha256(data).hexdigest().upper()


def typed_payload_hash(value: dict[str, Any]) -> dict[str, str]:
    """Hash a payload after removing its top-level ``content_hash`` member."""

    payload = deepcopy(value)
    payload.pop("content_hash", None)
    return {
        "algorithm": "sha256",
        "hash_kind": "rfc8785_jcs_sha256",
        "scope": "PAYLOAD_EXCLUDING_CONTENT_HASH",
        "value": sha256_bytes(canonical_json_bytes(payload)),
    }
