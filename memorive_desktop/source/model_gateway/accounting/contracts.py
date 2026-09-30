"""Typed, deterministic contracts for the ACCOUNTING-RECOVERY accounting kernel."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


class AccountingContractError(ValueError):
    """A value cannot enter the accounting kernel without guessing."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_datetime(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AccountingContractError("timestamp must carry an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def decimal_value(value: str | int | Decimal, *, field_name: str, allow_negative: bool = False) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise AccountingContractError(f"{field_name} must be a decimal string or integer, never binary float")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AccountingContractError(f"{field_name} is not a decimal") from exc
    if not parsed.is_finite() or (not allow_negative and parsed < 0):
        raise AccountingContractError(f"{field_name} must be finite and non-negative")
    return parsed


def decimal_string(value: Decimal) -> str:
    if not value.is_finite():
        raise AccountingContractError("non-finite money is forbidden")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def token_count(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AccountingContractError(f"{field_name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class PriceQuery:
    price_profile_id: str
    price_profile_revision: str
    requested_model: str
    returned_model: str
    model_alias_binding_ref: str
    observed_at: str
    region: str
    currency: str
    service_tier: str = "standard"
    context_tokens: int = 0

    def __post_init__(self) -> None:
        for name in (
            "price_profile_id",
            "price_profile_revision",
            "requested_model",
            "returned_model",
            "model_alias_binding_ref",
            "region",
            "currency",
            "service_tier",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise AccountingContractError(f"{name} must be non-empty")
        parse_datetime(self.observed_at)
        token_count(self.context_tokens, field_name="context_tokens")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PriceQuery":
        return cls(**dict(value))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UsageObservation:
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    input_includes_cache: bool = True
    source_kind: str = "provider_usage"
    source_evidence_ref: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cache_read_input_tokens",
            "cache_write_input_tokens",
        ):
            token_count(getattr(self, name), field_name=name)
        if self.reasoning_tokens > self.output_tokens:
            raise AccountingContractError("reasoning_tokens are an output-token subset")
        if not isinstance(self.input_includes_cache, bool):
            raise AccountingContractError("input_includes_cache must be boolean")
        if self.input_includes_cache and (
            self.cache_read_input_tokens + self.cache_write_input_tokens > self.input_tokens
        ):
            raise AccountingContractError("cache input components exceed total input_tokens")
        if not self.source_kind:
            raise AccountingContractError("source_kind must be non-empty")

    @classmethod
    def from_provider_usage(
        cls,
        usage: Mapping[str, Any] | None,
        *,
        input_includes_cache: bool = True,
        source_evidence_ref: str | None = None,
    ) -> "UsageObservation | None":
        if not usage:
            return None
        from model_gateway.prompt_cache import cache_usage
        observed_cache = cache_usage(dict(usage))
        if observed_cache["status"] == "INCONSISTENT":
            raise AccountingContractError("conflicting provider cache usage")
        completion_details = usage.get("completion_tokens_details") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        return cls(
            input_tokens=usage.get("prompt_tokens", usage.get("input_tokens", 0)),
            output_tokens=usage.get("completion_tokens", usage.get("output_tokens", 0)),
            reasoning_tokens=completion_details.get(
                "reasoning_tokens", usage.get("reasoning_tokens", 0)
            ),
            cache_read_input_tokens=prompt_details.get(
                "cached_tokens",
                observed_cache["read_tokens"] if observed_cache["read_tokens"] is not None else usage.get("cache_read_tokens", 0),
            ),
            cache_write_input_tokens=prompt_details.get(
                "cache_creation_tokens",
                usage.get("cache_write_input_tokens", usage.get("cache_creation_input_tokens", 0)),
            ),
            input_includes_cache=input_includes_cache,
            source_evidence_ref=source_evidence_ref,
        )

    def component_counts(self) -> dict[str, int]:
        plain_input = self.input_tokens
        if self.input_includes_cache:
            plain_input -= self.cache_read_input_tokens + self.cache_write_input_tokens
        return {
            "input": plain_input,
            "output": self.output_tokens,
            "cache_read": self.cache_read_input_tokens,
            "cache_write": self.cache_write_input_tokens,
        }

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AccountingEnvelope:
    stage: str
    accounting_status: str
    currency: str | None
    local_estimate: str | None
    breakdown: tuple[dict[str, Any], ...]
    identity: dict[str, Any]
    usage: dict[str, Any] | None
    price_binding: dict[str, Any] | None
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    unavailable_reasons: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = "AccountingEnvelope-v1"

    def __post_init__(self) -> None:
        if self.stage not in {"QUOTE", "OBSERVED"}:
            raise AccountingContractError("stage must be QUOTE or OBSERVED")
        if self.accounting_status not in {
            "EXACT_FROM_REPORTED_USAGE",
            "CONSERVATIVE_QUOTE",
            "PARTIAL_ESTIMATE",
            "NOT_ASSESSED",
        }:
            raise AccountingContractError("invalid accounting_status")
        if self.accounting_status in {"EXACT_FROM_REPORTED_USAGE", "CONSERVATIVE_QUOTE"}:
            if self.local_estimate is None or self.currency is None:
                raise AccountingContractError("numeric accounting status requires money and currency")
            decimal_value(self.local_estimate, field_name="local_estimate")
        elif self.local_estimate is not None:
            raise AccountingContractError("non-estimated envelope cannot carry numeric local_estimate")
        if self.stage == "QUOTE" and self.accounting_status == "EXACT_FROM_REPORTED_USAGE":
            raise AccountingContractError("QUOTE cannot claim reported-usage exactness")
        if self.stage == "OBSERVED" and self.accounting_status == "CONSERVATIVE_QUOTE":
            raise AccountingContractError("OBSERVED cannot claim quote status")

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "accounting_status": self.accounting_status,
            "currency": self.currency,
            "local_estimate": self.local_estimate,
            "breakdown": list(self.breakdown),
            "identity": self.identity,
            "usage": self.usage,
            "price_binding": self.price_binding,
            "evidence_refs": list(self.evidence_refs),
            "unavailable_reasons": list(self.unavailable_reasons),
        }
        payload["envelope_id"] = f"acct-{canonical_sha256(payload)[:24]}"
        return payload


@dataclass(frozen=True)
class SettlementObservation:
    observation_id: str
    attempt_id: str
    axis: str
    status: str
    requested_model: str
    returned_model: str
    model_alias_binding_ref: str
    currency: str | None
    amount: str | None
    evidence_ref: str
    request_id: str | None = None
    rounding_decimal_places: int | None = None
    expected_difference_reason: str | None = None
    price_profile_ref: str | None = None
    scope: str = "ATTEMPT"
    attempt_result: str | None = None

    def __post_init__(self) -> None:
        if self.axis not in {"LOCAL_ESTIMATE", "UPSTREAM_COST", "OPERATOR_CHARGE"}:
            raise AccountingContractError("invalid settlement axis")
        if self.status not in {"OBSERVED", "PROVISIONAL", "NOT_ASSESSED", "NOT_APPLICABLE"}:
            raise AccountingContractError("invalid settlement status")
        if self.scope not in {"ATTEMPT", "DAILY", "MONTHLY"}:
            raise AccountingContractError("invalid settlement scope")
        for name in (
            "observation_id",
            "attempt_id",
            "requested_model",
            "returned_model",
            "model_alias_binding_ref",
            "evidence_ref",
        ):
            if not getattr(self, name):
                raise AccountingContractError(f"{name} must be non-empty")
        if self.status == "OBSERVED":
            if self.currency is None or self.amount is None:
                raise AccountingContractError("OBSERVED requires currency and amount")
            decimal_value(self.amount, field_name="amount", allow_negative=True)
        elif self.amount is not None:
            raise AccountingContractError("non-observed settlement cannot carry numeric amount")
        if self.rounding_decimal_places is not None and (
            isinstance(self.rounding_decimal_places, bool)
            or not isinstance(self.rounding_decimal_places, int)
            or not 0 <= self.rounding_decimal_places <= 12
        ):
            raise AccountingContractError("rounding_decimal_places must be 0..12")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SettlementObservation":
        return cls(**dict(value))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def legacy_float(value: str | None) -> float | None:
    """Explicitly marked compatibility conversion; never used for reconciliation."""
    if value is None:
        return None
    result = float(decimal_value(value, field_name="legacy_projection"))
    if not math.isfinite(result):
        raise AccountingContractError("legacy float projection is not finite")
    return result
