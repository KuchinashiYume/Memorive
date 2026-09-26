"""Append-only rejection and abstention ledger for P03/T07 advice."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from .writer_lock import KernelWriterLock

from m7_weighting.contracts import make_hashed_payload, make_stable_id, verify_hashed_payload


_ROW_KEYS = {
    "schema_version",
    "ledger_entry_id",
    "source_advice_hash",
    "source_window_ref",
    "candidate_id",
    "disposition",
    "reason_codes",
    "append_only",
    "content_hash",
}


class AdvisoryLedgerError(ValueError):
    """The advisory ledger is malformed, conflicting, or not append-only."""


def build_rejection_ledger_rows(
    routing_advice: Mapping[str, Any], *, source_window_ref: str
) -> list[dict[str, Any]]:
    if not isinstance(source_window_ref, str) or not source_window_ref:
        raise AdvisoryLedgerError("source_window_ref must be non-empty")
    if not isinstance(routing_advice, Mapping) or not isinstance(routing_advice.get("content_hash"), Mapping):
        raise AdvisoryLedgerError("routing_advice content_hash is required")
    entries = routing_advice.get("rejection_and_abstention_entries")
    if not isinstance(entries, list):
        raise AdvisoryLedgerError("routing_advice rejection entries are required")
    rows = []
    normalized_entries = list(entries)
    outcome = routing_advice.get("outcome")
    if isinstance(outcome, str) and outcome.startswith("ABSTAIN"):
        reasons = routing_advice.get("reason_codes")
        if not isinstance(reasons, list) or not reasons:
            raise AdvisoryLedgerError("abstention advice requires reason_codes")
        normalized_entries.append(
            {
                "candidate_id": "__ROUTING_WINDOW__",
                "status": outcome,
                "reason_codes": reasons,
                "append_only": True,
            }
        )
    for entry in normalized_entries:
        if not isinstance(entry, Mapping):
            raise AdvisoryLedgerError("rejection entry must be an object")
        identity = {
            "advice_hash": routing_advice["content_hash"],
            "candidate_id": entry.get("candidate_id"),
            "source_window_ref": source_window_ref,
        }
        rows.append(
            validate_rejection_ledger_row(
                make_hashed_payload(
                    {
                        "schema_version": "P03_T07_REJECTION_ABSTENTION_LEDGER_ROW_V1",
                        "ledger_entry_id": make_stable_id("route_reject_", identity),
                        "source_advice_hash": deepcopy(routing_advice["content_hash"]),
                        "source_window_ref": source_window_ref,
                        "candidate_id": entry.get("candidate_id"),
                        "disposition": entry.get("status"),
                        "reason_codes": deepcopy(entry.get("reason_codes")),
                        "append_only": True,
                    }
                )
            )
        )
    return sorted(rows, key=lambda row: row["ledger_entry_id"])


def validate_rejection_ledger_row(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        row = verify_hashed_payload(value, "rejection_ledger_row")
    except Exception as exc:
        raise AdvisoryLedgerError("REJECTION_LEDGER_ROW_HASH_INVALID") from exc
    if set(row) != _ROW_KEYS:
        raise AdvisoryLedgerError("REJECTION_LEDGER_ROW_EXACT_KEYS_MISMATCH")
    if row["schema_version"] != "P03_T07_REJECTION_ABSTENTION_LEDGER_ROW_V1":
        raise AdvisoryLedgerError("REJECTION_LEDGER_ROW_SCHEMA_UNSUPPORTED")
    for field in ("ledger_entry_id", "source_window_ref", "candidate_id", "disposition"):
        if not isinstance(row[field], str) or not row[field] or row[field] != row[field].strip():
            raise AdvisoryLedgerError(f"REJECTION_LEDGER_ROW_{field.upper()}_INVALID")
    reasons = row["reason_codes"]
    if not isinstance(reasons, list) or not reasons or any(not isinstance(reason, str) or not reason for reason in reasons) or reasons != sorted(set(reasons)):
        raise AdvisoryLedgerError("REJECTION_LEDGER_ROW_REASONS_INVALID")
    if row["append_only"] is not True:
        raise AdvisoryLedgerError("REJECTION_LEDGER_ROW_APPEND_ONLY_REQUIRED")
    return row


def append_rejection_ledger(path: Path, rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Append new rows, preserve the exact prefix, and reject ID conflicts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with KernelWriterLock(path.parent, owner_id="m11.p03_t07_advisory_ledger"):
        return _append_rejection_ledger_locked(path, rows)


def _append_rejection_ledger_locked(path: Path, rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    checked = [validate_rejection_ledger_row(row) for row in rows]
    ids = [row["ledger_entry_id"] for row in checked]
    if ids != sorted(set(ids)):
        raise AdvisoryLedgerError("REJECTION_LEDGER_INPUT_IDS_INVALID")
    before = path.read_bytes() if path.exists() else b""
    existing: dict[str, bytes] = {}
    if before:
        if not before.endswith(b"\n"):
            raise AdvisoryLedgerError("REJECTION_LEDGER_TRUNCATED_TAIL")
        for raw_line in before.splitlines():
            try:
                value = validate_rejection_ledger_row(json.loads(raw_line.decode("utf-8")))
            except Exception as exc:
                raise AdvisoryLedgerError("REJECTION_LEDGER_EXISTING_ROW_INVALID") from exc
            canonical = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            previous = existing.get(value["ledger_entry_id"])
            if previous is not None:
                code = "REJECTION_LEDGER_EXISTING_ID_CONFLICT" if previous != canonical else "REJECTION_LEDGER_EXISTING_DUPLICATE_ID"
                raise AdvisoryLedgerError(code)
            existing[value["ledger_entry_id"]] = canonical
    append_bytes = bytearray()
    appended = 0
    noop = 0
    for row in checked:
        canonical = (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        prior = existing.get(row["ledger_entry_id"])
        if prior is not None:
            if prior != canonical:
                raise AdvisoryLedgerError("REJECTION_LEDGER_ID_CONFLICT")
            noop += 1
            continue
        existing[row["ledger_entry_id"]] = canonical
        append_bytes.extend(canonical)
        appended += 1
    if append_bytes:
        with path.open("ab") as stream:
            stream.write(append_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        after = path.read_bytes()
        if not after.startswith(before) or after != before + bytes(append_bytes):
            raise AdvisoryLedgerError("REJECTION_LEDGER_APPEND_VERIFICATION_FAILED")
    return {"appended": appended, "noop": noop}


__all__ = [
    "AdvisoryLedgerError",
    "append_rejection_ledger",
    "build_rejection_ledger_rows",
    "validate_rejection_ledger_row",
]
