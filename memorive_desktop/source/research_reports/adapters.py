"""Strict, read-only KNOWLEDGE_ADMISSION/RUNTIME_LOG/ARTIFACT_REGISTRY adapters into the DataContracts ResearchEvent view."""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

from .canonical import (
    canonical_json_bytes,
    forbidden_phrase,
    parse_datetime,
    require_safe_text,
    sha256_bytes,
    typed_payload_hash,
)
from .errors import SourceRecordError

FAILURE_SEMANTICS = {
    "BLOCKED": "fail_closed",
    "ERROR": "quarantined",
    "FAIL": "fail_closed",
    "NOT_ASSESSED": "no_eligibility",
}
IMMUTABLE_FIELDS = [
    "object_id",
    "schema_version",
    "revision",
    "content_hash",
    "producer",
    "parent_refs",
    "provenance_refs",
    "supersedes",
]

KNOWLEDGE_ADMISSION_STATES = {"pending", "active", "quarantined"}
KNOWLEDGE_ADMISSION_TRANSITIONS = {
    ("pending", "active"),
    ("pending", "quarantined"),
    ("active", "quarantined"),
}
EVIDENCE_REVIEW_TRIGGER = "EvidenceReview校核结果"
KNOWLEDGE_ADMISSION_TRIGGERS = {"我手动", "占位", "自动规则", "EvidenceReview校核结果", "分级信任准入"}
EVIDENCE_REVIEW_REPORT_TOKEN = re.compile(r"(?:^|;\s*)report=([A-Za-z0-9_-]+)(?=$|;\s*)")

ATTEMPT_RESULTS = {"success", "failure", "incomplete", "error", "cancelled", "not_assessed"}
PAPER_OUTCOMES = {"success", "failure", "partial", "not_run"}
PUBLICATION_STATUSES = {"not_requested", "not_published", "published", "publication_failed"}
LIFECYCLE_STATUSES = {"completed", "aborted", "invalidated"}
ACCEPTANCE_VERDICTS = {"PASS", "FAIL", "NOT_ASSESSED"}
RECORD_KINDS = {"terminal", "amendment"}

RUNTIME_LOG_COMMON = {
    "schema_version",
    "record_type",
    "ledger_type",
    "record_id",
    "record_kind",
    "run_id",
    "created_at",
    "source_evidence_refs",
}
ATTEMPT_BASE = {
    "attempt_id",
    "logical_operation_id",
    "attempt_no",
    "task_id",
    "stage",
    "paper_id",
    "retry_of_attempt_id",
    "business_repair_of",
    "route_snapshot_id",
    "execution_config_hash",
}
PAPER_FIELDS = {
    "outcome_id",
    "paper_id",
    "outcome_category",
    "outcome_code",
    "reason_codes",
    "final_artifact_refs",
    "supersedes_record_id",
    "reprocesses_run_id",
}
PUBLICATION_FIELDS = {
    "publication_event_id",
    "publication_status",
    "manifest_ref",
    "subject_artifact_refs",
    "receipt_ref",
    "supersedes_record_id",
    "absence_reason",
}

# The package that owns a record format also provides its validator. The files
# are listed so that a report can say which code checked its records.
_OFFICIAL_MODULE_FILES = {
    "run_ledger.schema": ("run_ledger/schema.py",),
    "artifact_registry.schema_v2": ("artifact_registry/schema.py", "artifact_registry/schema_v2.py"),
}
OFFICIAL_VALIDATOR_RECEIPTS: dict[str, dict[str, Any]] = {}


RUN_TERMINAL_FIELDS = {
    "schema_version",
    "record_type",
    "ledger_type",
    "record_id",
    "run_id",
    "lifecycle_status",
    "acceptance_verdict",
    "terminal_at",
    "source_evidence_refs",
    "supersedes_record_id",
}


def _error(code: str, message: str, *fields: str) -> None:
    raise SourceRecordError(code, message, field_names=fields)


def _exact(record: dict[str, Any], expected: set[str], label: str) -> None:
    if set(record) != expected:
        _error(
            "SOURCE_FIELDS_MISMATCH",
            f"{label} fields mismatch",
            *(sorted(expected ^ set(record))),
        )


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    try:
        return require_safe_text(value, field, allow_empty=allow_empty)
    except (TypeError, ValueError) as exc:
        _error("INVALID_IDENTITY_FIELD", str(exc), field)
    raise AssertionError("unreachable")


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _error("INVALID_INTEGER_FIELD", f"{field} must be a positive integer", field)
    return value


def _event_id(source_system: str, stable_source_identity: str) -> str:
    digest = sha256_bytes(f"{source_system}|{stable_source_identity}".encode("utf-8"))
    return "RE-" + digest[:32]
