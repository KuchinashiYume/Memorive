"""Append-only accounting amendment projection without denominator drift."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Iterable


class AccountingProjectionError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_accounting_amendment_projection(
    *,
    terminal_attempt: dict[str, Any],
    accounting_envelope: dict[str, Any],
    reconciliation_result: dict[str, Any],
    evidence_refs: Iterable[str],
) -> dict[str, Any]:
    if terminal_attempt.get("record_kind") != "terminal":
        raise AccountingProjectionError("amendment target must be a terminal attempt")
    attempt_id = terminal_attempt.get("attempt_id")
    if not attempt_id:
        raise AccountingProjectionError("terminal attempt_id is required")
    if reconciliation_result.get("attempt_id") not in {None, attempt_id}:
        raise AccountingProjectionError("reconciliation attempt lineage mismatch")
    refs = sorted(set(evidence_refs))
    if not refs:
        raise AccountingProjectionError("accounting amendment requires evidence refs")
    payload = {
        "schema_version": "AccountingAmendmentProjection-v1",
        "record_kind": "amendment",
        "amends_attempt_id": attempt_id,
        "run_id": terminal_attempt.get("run_id"),
        "logical_operation_id": terminal_attempt.get("logical_operation_id"),
        "attempt_no": terminal_attempt.get("attempt_no"),
        "amendment_fields": {
            "accounting_envelope": copy.deepcopy(accounting_envelope),
            "reconciliation_result": copy.deepcopy(reconciliation_result),
        },
        "source_evidence_refs": refs,
    }
    payload["record_id"] = f"acct-amend-{hashlib.sha256(_canonical(payload).encode('utf-8')).hexdigest()[:24]}"
    return payload


def project_current_attempts(
    terminals: Iterable[dict[str, Any]], amendments: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    terminal_rows = [copy.deepcopy(row) for row in terminals]
    by_attempt: dict[str, dict[str, Any]] = {}
    for row in terminal_rows:
        if row.get("record_kind") != "terminal" or not row.get("attempt_id"):
            raise AccountingProjectionError("invalid terminal row")
        if row["attempt_id"] in by_attempt:
            raise AccountingProjectionError("duplicate terminal attempt_id")
        by_attempt[row["attempt_id"]] = row
    seen_amendments: dict[str, str] = {}
    for amendment in amendments:
        record_id = amendment.get("record_id")
        target = amendment.get("amends_attempt_id")
        if not record_id or target not in by_attempt:
            raise AccountingProjectionError("orphan accounting amendment")
        canonical = _canonical(amendment)
        if record_id in seen_amendments:
            if seen_amendments[record_id] != canonical:
                raise AccountingProjectionError("accounting amendment id conflict")
            continue
        seen_amendments[record_id] = canonical
        for key, value in (amendment.get("amendment_fields") or {}).items():
            by_attempt[target][key] = copy.deepcopy(value)
        by_attempt[target].setdefault("_accounting_amendment_record_ids", []).append(record_id)
    return {
        "attempt_denominator": len(by_attempt),
        "amendment_count": len(seen_amendments),
        "current_attempts": [by_attempt[key] for key in sorted(by_attempt)],
    }


__all__ = [
    "AccountingProjectionError",
    "build_accounting_amendment_projection",
    "project_current_attempts",
]
