from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from m9_gateway.execution_core.contracts import canonical_sha256, immutable_copy

from .contracts import RelayContractError
from .qualification import classify_backend_identity


def _usage(value: Mapping[str, Any] | None, status: str) -> dict[str, Any]:
    if status == "UNKNOWN":
        if value not in (None, {}):
            raise RelayContractError("UNKNOWN_USAGE_MUST_BE_NULL")
        return {
            "status": "UNKNOWN",
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "total_tokens": None,
        }
    if status not in {"ACTUAL_REPORTED", "ACTUAL_VERIFIED"}:
        raise RelayContractError("TOKEN_USAGE_STATUS_INVALID")
    fields = {"input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise RelayContractError("TOKEN_USAGE_FIELDS_INVALID")
    accepted: dict[str, Any] = {"status": status}
    for field in fields:
        item = value[field]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise RelayContractError("TOKEN_USAGE_INVALID", [field])
        accepted[field] = item
    if accepted["total_tokens"] != accepted["input_tokens"] + accepted["output_tokens"]:
        raise RelayContractError("TOKEN_TOTAL_MISMATCH")
    if accepted["reasoning_tokens"] > accepted["output_tokens"]:
        raise RelayContractError("REASONING_TOKENS_EXCEED_OUTPUT")
    return accepted


def _billing(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    short_fields = {"status", "value", "currency"}
    full_fields = short_fields | {"source", "locator", "tax_and_multiplier_included"}
    if not isinstance(value, Mapping) or set(value) not in (short_fields, full_fields):
        raise RelayContractError("BILLING_FIELDS_INVALID", [field])
    accepted = immutable_copy(value)
    accepted.setdefault("source", "UNSPECIFIED")
    accepted.setdefault("locator", None)
    accepted.setdefault("tax_and_multiplier_included", None)
    if accepted["status"] == "ACTUAL":
        accepted["status"] = "ACTUAL_VERIFIED"
    if accepted["status"] not in {
        "ACTUAL_VERIFIED", "ESTIMATED", "UNKNOWN", "N/A", "RECORDED_UNVERIFIED",
    }:
        raise RelayContractError("BILLING_STATUS_INVALID", [field])
    if accepted["status"] in {"ACTUAL_VERIFIED", "ESTIMATED"}:
        amount = accepted["value"]
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0 or not math.isfinite(amount):
            raise RelayContractError("BILLING_VALUE_INVALID", [field])
        if not isinstance(accepted["currency"], str) or not accepted["currency"]:
            raise RelayContractError("BILLING_CURRENCY_INVALID", [field])
    elif accepted["value"] is not None or accepted["currency"] is not None:
        raise RelayContractError("BILLING_UNKNOWN_NOT_NULL", [field])
    if accepted["locator"] is not None and not isinstance(accepted["locator"], str):
        raise RelayContractError("BILLING_LOCATOR_INVALID", [field])
    if accepted["tax_and_multiplier_included"] not in {True, False, None}:
        raise RelayContractError("BILLING_TAX_FLAG_INVALID", [field])
    return accepted


def _hash(value: str | None, field: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or len(value) != 64:
        raise RelayContractError("EVIDENCE_HASH_INVALID", [field])
    try:
        int(value, 16)
    except ValueError as exc:
        raise RelayContractError("EVIDENCE_HASH_INVALID", [field]) from exc
    return value.upper()


def build_relay_attempt_receipt(
    *,
    attempt_id: str,
    profile: Mapping[str, Any],
    requested_model: str,
    relay_reported_model: str | None,
    returned_model: str | None,
    independent_backend_identity: str | None,
    route: str,
    region: str,
    egress: str,
    behavior_hash: str,
    input_hash: str,
    output_hash: str | None,
    latency_ms: float | None,
    usage: Mapping[str, Any] | None,
    operator_billing: Mapping[str, Any],
    upstream_billing: Mapping[str, Any],
    usage_status: str = "ACTUAL_REPORTED",
    external_request_started: bool = True,
    lifecycle_result: str = "PASS",
    reason_code: str | None = None,
    retry_of: str | None = None,
    fallback_of: str | None = None,
    request_started_at: str | None = None,
    request_finished_at: str | None = None,
    request_id: str | None = None,
    response_id: str | None = None,
    response_body_models: Sequence[str] = (),
    response_header_models: Sequence[str] = (),
    response_event_models: Sequence[str] = (),
    source_evidence_refs: Sequence[str] = (),
    protocol_adapter_id: str | None = None,
    protocol_revision: str | None = None,
    qualification_key: str | None = None,
) -> dict[str, Any]:
    if lifecycle_result not in {"PASS", "FAIL", "ERROR", "BLOCKED_PRE_SEND", "INVALIDATED"}:
        raise RelayContractError("ATTEMPT_RESULT_INVALID")
    if not isinstance(external_request_started, bool):
        raise RelayContractError("ATTEMPT_STARTED_FLAG_INVALID")
    if not external_request_started and lifecycle_result != "BLOCKED_PRE_SEND":
        raise RelayContractError("PRESEND_RESULT_MISMATCH")
    if external_request_started:
        if not isinstance(latency_ms, (int, float)) or isinstance(latency_ms, bool) or latency_ms < 0 or not math.isfinite(latency_ms):
            raise RelayContractError("LATENCY_INVALID")
    elif latency_ms is not None or output_hash is not None:
        raise RelayContractError("PRESEND_WIRE_FIELDS_FORBIDDEN")
    identity = classify_backend_identity(
        transport_kind=str(profile.get("transport_kind")),
        requested_model=requested_model,
        relay_reported_model=relay_reported_model,
        response_body_models=response_body_models or ([returned_model] if returned_model else []),
        response_header_models=response_header_models,
        response_event_models=response_event_models,
        independent_backend_identity=independent_backend_identity,
    )
    receipt = {
        "schema_version": "P05_T12_RELAY_ATTEMPT_EVIDENCE_V1",
        "attempt_id": attempt_id,
        "retry_of": retry_of,
        "fallback_of": fallback_of,
        "external_request_started": external_request_started,
        "lifecycle_result": lifecycle_result,
        "reason_code": reason_code,
        "request_started_at": request_started_at,
        "request_finished_at": request_finished_at,
        "request_id": request_id,
        "response_id": response_id,
        "profile_id": profile.get("profile_id"),
        "profile_fingerprint": profile.get("profile_fingerprint"),
        "qualification_key": qualification_key,
        "transport_kind": profile.get("transport_kind"),
        "protocol_family": profile.get("protocol_family"),
        "protocol_adapter_id": protocol_adapter_id or profile.get("protocol_family"),
        "protocol_revision": protocol_revision or profile.get("protocol_revision"),
        "endpoint_operator": profile.get("endpoint_operator"),
        "claimed_upstream_provider": profile.get("claimed_upstream_provider"),
        "endpoint_origin_hash": canonical_sha256(profile.get("origin")),
        "requested_model": requested_model,
        "relay_reported_model": relay_reported_model,
        "returned_model": identity["returned_model"],
        "independent_backend_identity": independent_backend_identity,
        "identity_grade": identity["identity_grade"],
        "identity_reason_code": identity["reason_code"],
        "response_body_models": list(response_body_models),
        "response_header_models": list(response_header_models),
        "response_event_models": list(response_event_models),
        "route": route,
        "region": region,
        "egress": egress,
        "access_mode": profile.get("access_mode"),
        "billing_mode": profile.get("billing_mode"),
        "behavior_hash": _hash(behavior_hash, "behavior_hash"),
        "input_hash": _hash(input_hash, "input_hash"),
        "output_hash": _hash(output_hash, "output_hash", allow_none=True),
        "latency_ms": latency_ms,
        "usage": _usage(usage, usage_status),
        "operator_billing": _billing(operator_billing, "operator_billing"),
        "upstream_billing": _billing(upstream_billing, "upstream_billing"),
        "source_evidence_refs": list(source_evidence_refs),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def build_presend_blocked_receipt(
    *,
    attempt_id: str,
    profile: Mapping[str, Any],
    requested_model: str,
    route: str,
    region: str,
    egress: str,
    behavior_hash: str,
    input_hash: str,
    reason_code: str,
    retry_of: str | None = None,
) -> dict[str, Any]:
    unknown = {"status": "UNKNOWN", "value": None, "currency": None}
    return build_relay_attempt_receipt(
        attempt_id=attempt_id,
        profile=profile,
        requested_model=requested_model,
        relay_reported_model=None,
        returned_model=None,
        independent_backend_identity=None,
        route=route,
        region=region,
        egress=egress,
        behavior_hash=behavior_hash,
        input_hash=input_hash,
        output_hash=None,
        latency_ms=None,
        usage=None,
        usage_status="UNKNOWN",
        operator_billing=unknown,
        upstream_billing=unknown,
        external_request_started=False,
        lifecycle_result="BLOCKED_PRE_SEND",
        reason_code=reason_code,
        retry_of=retry_of,
    )


class AttemptLedger:
    """Append-only validator for physical requests and pre-send blocks."""

    def __init__(self, *, attempt_ceiling: int):
        if isinstance(attempt_ceiling, bool) or not isinstance(attempt_ceiling, int) or attempt_ceiling < 0:
            raise RelayContractError("ATTEMPT_CEILING_INVALID")
        self.attempt_ceiling = attempt_ceiling
        self._receipts: list[dict[str, Any]] = []
        self._ids: set[str] = set()

    def append(self, receipt: Mapping[str, Any]) -> None:
        attempt_id = receipt.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id or attempt_id in self._ids:
            raise RelayContractError("ATTEMPT_ID_DUPLICATE_OR_INVALID")
        parent = receipt.get("retry_of") or receipt.get("fallback_of")
        if parent is not None and parent not in self._ids:
            raise RelayContractError("ATTEMPT_LINEAGE_PARENT_MISSING")
        started_count = sum(item["external_request_started"] for item in self._receipts)
        if receipt.get("external_request_started") and started_count >= self.attempt_ceiling:
            raise RelayContractError("ATTEMPT_CEILING_EXHAUSTED")
        self._receipts.append(immutable_copy(receipt))
        self._ids.add(attempt_id)

    def summary(self) -> dict[str, Any]:
        started = sum(item["external_request_started"] for item in self._receipts)
        blocked = len(self._receipts) - started
        return {
            "attempt_denominator": len(self._receipts),
            "physical_request_starts": started,
            "pre_send_blocked": blocked,
            "attempt_ceiling": self.attempt_ceiling,
            "remaining": self.attempt_ceiling - started,
            "receipt_hashes": [item["receipt_sha256"] for item in self._receipts],
        }


def project_m11_relay_extension(receipt: Mapping[str, Any]) -> dict[str, Any]:
    forbidden = {"credential", "credential_value", "raw", "prompt", "response_raw"}
    if forbidden & set(receipt):
        raise RelayContractError("RELAY_RECEIPT_FORBIDDEN_FIELDS")
    safe_fields = (
        "attempt_id", "retry_of", "fallback_of", "external_request_started",
        "lifecycle_result", "reason_code", "profile_id", "profile_fingerprint",
        "qualification_key", "transport_kind", "protocol_family",
        "protocol_adapter_id", "protocol_revision", "endpoint_operator",
        "claimed_upstream_provider", "endpoint_origin_hash", "requested_model",
        "relay_reported_model", "returned_model", "independent_backend_identity",
        "identity_grade", "identity_reason_code", "route", "region", "egress",
        "access_mode", "billing_mode", "behavior_hash", "input_hash", "output_hash",
        "latency_ms", "usage", "operator_billing", "upstream_billing",
        "source_evidence_refs", "receipt_sha256",
    )
    missing = [field for field in safe_fields if field not in receipt]
    if missing:
        raise RelayContractError("RELAY_RECEIPT_FIELDS_MISSING", missing)
    return {
        "schema_version": "P05_T12_M11_RELAY_EXTENSION_V1",
        "relay_evidence": {field: immutable_copy(receipt[field]) for field in safe_fields},
    }
