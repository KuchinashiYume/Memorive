"""Three-source reconciliation and append-only discrepancy queue."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Iterable

from .contracts import (
    AccountingContractError,
    SettlementObservation,
    canonical_json,
    decimal_string,
    decimal_value,
)


AXIS_ORDER = ("LOCAL_ESTIMATE", "UPSTREAM_COST", "OPERATOR_CHARGE")


def _quantized(observation: SettlementObservation) -> Decimal:
    amount = decimal_value(observation.amount or "0", field_name="amount", allow_negative=True)
    places = observation.rounding_decimal_places
    if places is None:
        return amount
    quantum = Decimal(1).scaleb(-places)
    return amount.quantize(quantum, rounding=ROUND_HALF_EVEN)


def reconcile_observations(
    observations: Iterable[SettlementObservation],
) -> dict:
    rows = list(observations)
    if not rows:
        return {
            "schema_version": "ReconciliationResult-v1",
            "reconciliation_status": "NOT_ASSESSED",
            "reason_codes": ["NO_ACCOUNTING_SOURCE"],
            "observations": [],
            "result_id": "recon-empty",
        }
    attempt_ids = {row.attempt_id for row in rows}
    axes = [row.axis for row in rows]
    reasons: list[str] = []
    if len(attempt_ids) != 1:
        reasons.append("ATTEMPT_ID_MISMATCH")
    if len(axes) != len(set(axes)):
        reasons.append("DUPLICATE_SOURCE_AXIS")
    identities = {
        (row.requested_model, row.returned_model, row.model_alias_binding_ref)
        for row in rows
    }
    if len(identities) != 1:
        reasons.append("MODEL_IDENTITY_MISMATCH")
    currencies = {row.currency for row in rows if row.status == "OBSERVED"}
    if len(currencies) > 1:
        reasons.append("CURRENCY_MISMATCH")
    request_ids = {row.request_id for row in rows if row.request_id is not None}
    if len(request_ids) > 1:
        reasons.append("REQUEST_ID_MISMATCH")
    identity_or_currency = {
        "ATTEMPT_ID_MISMATCH",
        "MODEL_IDENTITY_MISMATCH",
        "CURRENCY_MISMATCH",
        "REQUEST_ID_MISMATCH",
    }
    if set(reasons) & identity_or_currency:
        status = "NOT_ASSESSED"
    elif reasons:
        status = "MISMATCH_PENDING"
    else:
        by_axis = {row.axis: row for row in rows}
        observed = [row for row in rows if row.status == "OBSERVED"]
        if any(row.scope != "ATTEMPT" and row.request_id is None for row in rows):
            status = "NOT_ASSESSED"
            reasons.append("AGGREGATE_ONLY_NOT_PER_ATTEMPT")
        elif not observed:
            status = "NOT_ASSESSED"
            reasons.append("NO_NUMERIC_OBSERVATION")
        elif any(row.status == "NOT_ASSESSED" for row in rows):
            status = "NOT_ASSESSED"
            reasons.append("SOURCE_NOT_ASSESSED")
        elif len(by_axis) < 2:
            status = "NOT_ASSESSED"
            reasons.append("SETTLEMENT_SOURCE_PENDING")
        else:
            amounts = {_quantized(row) for row in observed}
            if len(amounts) == 1:
                status = "MATCHED"
            else:
                operator = by_axis.get("OPERATOR_CHARGE")
                non_operator = [row for row in observed if row.axis != "OPERATOR_CHARGE"]
                non_operator_amounts = {_quantized(row) for row in non_operator}
                if (
                    operator is not None
                    and operator.expected_difference_reason == "OPERATOR_MARKUP"
                    and len(non_operator_amounts) <= 1
                ):
                    status = "EXPECTED_DIFFERENCE"
                    reasons.append("OPERATOR_MARKUP_DECLARED")
                else:
                    status = "MISMATCH_PENDING"
                    reasons.append("MONEY_AMOUNT_MISMATCH")
                    price_refs = {row.price_profile_ref for row in observed if row.price_profile_ref}
                    if len(price_refs) > 1:
                        reasons.append("PRICE_REVISION_DRIFT")

    payload = {
        "schema_version": "ReconciliationResult-v1",
        "attempt_id": next(iter(attempt_ids)) if len(attempt_ids) == 1 else None,
        "reconciliation_status": status,
        "reason_codes": sorted(set(reasons)),
        "observations": [row.as_dict() for row in sorted(rows, key=lambda item: (AXIS_ORDER.index(item.axis), item.observation_id))],
    }
    payload["result_id"] = f"recon-{hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()[:24]}"
    return payload


class DiscrepancyQueue:
    """Append-only JSONL queue; an identical result is idempotent."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def append(self, reconciliation: dict) -> str:
        if reconciliation.get("reconciliation_status") not in {"MISMATCH_PENDING", "NOT_ASSESSED"}:
            raise AccountingContractError("only unresolved reconciliation enters discrepancy queue")
        result_id = reconciliation.get("result_id")
        if not result_id:
            raise AccountingContractError("reconciliation result_id is required")
        canonical = canonical_json(reconciliation)
        existing = self.read()
        for row in existing:
            if row["result_id"] != result_id:
                continue
            if canonical_json(row) == canonical:
                return "noop"
            raise AccountingContractError("result_id conflict; prior discrepancy preserved")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        before = self.path.read_bytes() if self.path.exists() else b""
        encoded = (canonical + "\n").encode("utf-8")
        with self.path.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        after = self.path.read_bytes()
        if not after.startswith(before) or len(after) != len(before) + len(encoded):
            raise AccountingContractError("discrepancy queue append verification failed")
        return "appended"

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise AccountingContractError("truncated discrepancy queue")
        result: list[dict] = []
        for line in raw.decode("utf-8").splitlines():
            result.append(json.loads(line))
        return result
