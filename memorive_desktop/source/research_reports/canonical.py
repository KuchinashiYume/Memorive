"""Accepted-Initialization-parity JCS, hashing and hostile-text helpers.

The JCS implementation below is intentionally kept in parity with the
accepted RESEARCH-CONTRACTS validator.  In particular, object keys use UTF-16 code-unit
order and numbers obey the I-JSON/IEEE-754 boundary.  Do not replace it with
``json.dumps(sort_keys=True)``: Python's Unicode ordering is not RFC 8785 key
ordering for supplementary characters.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from copy import deepcopy
from decimal import Decimal
from datetime import datetime
from pathlib import Path
from typing import Any


FORBIDDEN_REPORT_PHRASES = (
    "因此可以得出",
    "研究已经证明",
    "研究证明",
    "应当采用",
    "自动综述",
    "这表明",
    "可以断言",
)


def jcs_string(value: str) -> str:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError("RFC8785 JCS rejects lone UTF-16 surrogate code points")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def jcs_number(value: int | float) -> str:
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
    return jcs_serialize(value).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    # Validate the complete value against the same I-JSON domain before using
    # the human-readable serializer.
    jcs_serialize(value)
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def parse_datetime(value: str, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed


def require_safe_text(value: Any, field_name: str, *, allow_empty: bool = False) -> str:
    """Return exact text or fail; never normalize/strip an identity value."""

    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{field_name} must not contain leading/trailing whitespace")
    for char in value:
        category = unicodedata.category(char)
        if category in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            raise ValueError(f"{field_name} contains a forbidden Unicode control/format character")
    return value


def semantic_scan_form(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        char
        for char in normalized
        if not char.isspace()
        and unicodedata.category(char) != "Cf"
        and unicodedata.category(char)[0] not in {"M", "P", "S"}
    )


def forbidden_phrase(value: str) -> str | None:
    normalized = semantic_scan_form(value)
    for phrase in FORBIDDEN_REPORT_PHRASES:
        if semantic_scan_form(phrase) in normalized:
            return phrase
    return None


def validate_source_value(value: Any, field_name: str = "record") -> None:
    """Recursively reject dangerous strings and conclusion-language inputs."""

    if isinstance(value, str):
        require_safe_text(value, field_name, allow_empty=True)
        hit = forbidden_phrase(value)
        if hit:
            raise ValueError(f"{field_name} contains forbidden semantic phrase {hit!r}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_source_value(item, f"{field_name}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            require_safe_text(key, f"{field_name} key")
            hit = forbidden_phrase(key)
            if hit:
                raise ValueError(f"{field_name} key contains forbidden semantic phrase")
            validate_source_value(item, f"{field_name}.{key}")


def typed_payload_hash(value: dict[str, Any]) -> dict[str, str]:
    payload = deepcopy(value)
    payload.pop("content_hash", None)
    return {
        "algorithm": "sha256",
        "hash_kind": "rfc8785_jcs_sha256",
        "scope": "PAYLOAD_EXCLUDING_CONTENT_HASH",
        "value": sha256_bytes(canonical_json_bytes(payload)),
    }


def write_new_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def write_new_json(path: Path, value: Any) -> None:
    write_new_bytes(path, pretty_json_bytes(value))
