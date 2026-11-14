"""Deterministic issue and sandbox-only RUNTIME_LOG candidate records."""

from __future__ import annotations

import re
from typing import Iterable

from .canonical import canonical_json_bytes, sha256_bytes

SAFE_FIELD_NAME = re.compile(r"^[A-Za-z0-9_.\[\]-]+$")


def _sanitized_field_name(value: object) -> str:
    if isinstance(value, str) and SAFE_FIELD_NAME.fullmatch(value):
        return value
    raw = value.encode("utf-8", errors="surrogatepass") if isinstance(value, str) else repr(type(value)).encode("ascii")
    return "redacted_field_sha256_" + sha256_bytes(raw)[:16]


def make_issue(
    *,
    error_code: str,
    source_record_ref: str,
    source_content_hash: str,
    observed_at: str,
    blocking: bool,
    field_names: Iterable[str] = (),
    evidence_ref: str | None = None,
) -> dict:
    identity = {
        "error_code": error_code,
        "source_record_ref": source_record_ref,
        "source_content_hash": source_content_hash,
    }
    issue_id = "RESEARCH_REPORTS-ISSUE-" + sha256_bytes(canonical_json_bytes(identity))[:32]
    return {
        "schema_version": "1.0",
        "issue_id": issue_id,
        "error_code": error_code,
        "source_record_ref": source_record_ref,
        "source_content_hash": source_content_hash,
        "observed_at": observed_at,
        "affected_window": None,
        "blocking_status": "blocking" if blocking else "non_blocking",
        "evidence_ref": evidence_ref or source_record_ref,
        "sanitized_error_context": {
            "field_names": sorted({_sanitized_field_name(value) for value in field_names}),
            "payload_echoed": False,
        },
    }
