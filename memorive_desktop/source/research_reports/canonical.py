"""JCS, hashing and hostile-text helpers for research reports.

The JCS serializer and payload hashes come from retrieval_weighting.canonical, which keeps
parity with the accepted RESEARCH-CONTRACTS validator (UTF-16 key order, I-JSON numbers).
"""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from retrieval_weighting.canonical import canonical_json_bytes, sha256_bytes, typed_payload_hash  # used by the other report modules


FORBIDDEN_REPORT_PHRASES = (
    "因此可以得出",
    "研究已经证明",
    "研究证明",
    "应当采用",
    "自动综述",
    "这表明",
    "可以断言",
)


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


