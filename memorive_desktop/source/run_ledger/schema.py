"""Schemas for three logical ledgers and their shared run-terminal event.

One annual JSONL may contain both ``ledger_record`` and
``run_terminal_event`` records.  Only the former has ``ledger_type`` and
feeds the Attempt, Paper Outcome and Publication read-only business views.
Run lifecycle and acceptance verdict remain orthogonal support-event facts.
"""

from __future__ import annotations

import json
from typing import Any

from .errors import SchemaValidationError

SCHEMA_VERSION = "2.0"
LEGACY_SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_VERSIONS = (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION)
RECORD_TYPES = ("ledger_record", "run_terminal_event")
LEDGER_TYPES = ("attempt", "paper_outcome", "publication")
RECORD_KINDS = ("terminal", "amendment")
LIFECYCLE_STATUSES = ("completed", "aborted", "invalidated")
ACCEPTANCE_VERDICTS = ("PASS", "FAIL", "NOT_ASSESSED")
ATTEMPT_RESULTS = (
    "success",
    "failure",
    "incomplete",
    "error",
    "cancelled",
    "not_assessed",
)
PAPER_OUTCOMES = ("success", "failure", "partial", "not_run")
PUBLICATION_STATUSES = (
    "not_requested",
    "not_published",
    "published",
    "publication_failed",
)
ATTEMPT_AMENDABLE_FIELDS = frozenset(
    {"accounting", "provider_identity", "token_usage", "cost"}
)


def canonical_json(record: dict[str, Any]) -> str:
    """Return the stable representation used for idempotency comparisons."""
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_text(record: dict[str, Any], field: str) -> None:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{field} must be a non-empty string")


def _require_optional_text(record: dict[str, Any], field: str) -> None:
    value = record.get(field)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise SchemaValidationError(f"{field} must be null or a non-empty string")


def _require_text_list(
    record: dict[str, Any], field: str, *, allow_empty: bool
) -> None:
    value = record.get(field)
    if not isinstance(value, list):
        raise SchemaValidationError(f"{field} must be a list")
    if not allow_empty and not value:
        raise SchemaValidationError(f"{field} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise SchemaValidationError(f"{field} entries must be non-empty strings")
