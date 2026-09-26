"""The sole Decimal tariff calculator used by quote and observed paths."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

from .contracts import (
    AccountingContractError,
    AccountingEnvelope,
    PriceQuery,
    UsageObservation,
    decimal_string,
    decimal_value,
)
from .pricing.catalog import PriceResolution


PER_MILLION = Decimal("1000000")
COMPONENT_QUANTUM = Decimal("0.000000000001")
TOTAL_QUANTUM = Decimal("0.000001")


class DecimalCalculator:
    """Pure calculation: no I/O, provider branches, clocks or environment reads."""

    def calculate(
        self,
        *,
        stage: str,
        query: PriceQuery,
        usage: UsageObservation | None,
        price: PriceResolution,
        evidence_refs: tuple[str, ...] = (),
    ) -> AccountingEnvelope:
        identity = {
            "requested_model": query.requested_model,
            "returned_model": query.returned_model,
            "model_alias_binding_ref": query.model_alias_binding_ref,
            "price_profile_id": query.price_profile_id,
            "price_profile_revision": query.price_profile_revision,
            "region": query.region,
            "service_tier": query.service_tier,
            "observed_at": query.observed_at,
        }
        if usage is None:
            return AccountingEnvelope(
                stage=stage,
                accounting_status="NOT_ASSESSED",
                currency=None,
                local_estimate=None,
                breakdown=(),
                identity=identity,
                usage=None,
                price_binding=None,
                evidence_refs=evidence_refs,
                unavailable_reasons=("USAGE_EVIDENCE_MISSING",),
            )
        if price.status != "RESOLVED" or price.tariff is None:
            return AccountingEnvelope(
                stage=stage,
                accounting_status="NOT_ASSESSED",
                currency=None,
                local_estimate=None,
                breakdown=(),
                identity=identity,
                usage=usage.as_dict(),
                price_binding={"catalog_sha256": price.catalog_sha256},
                evidence_refs=evidence_refs,
                unavailable_reasons=price.unavailable_reasons,
            )

        tariff = price.tariff
        counts = usage.component_counts()
        missing = sorted(
            f"RATE_MISSING_{component.upper()}"
            for component, count in counts.items()
            if count > 0 and tariff["rates_per_million"].get(component) is None
        )
        price_binding = {
            "catalog_sha256": price.catalog_sha256,
            "snapshot_id": tariff.get("snapshot_id"),
            "profile_id": tariff["profile_id"],
            "revision": tariff["revision"],
            "source_ref": tariff["source_ref"],
            "effective_from": tariff["effective_from"],
            "effective_until": tariff["effective_until"],
        }
        if missing:
            return AccountingEnvelope(
                stage=stage,
                accounting_status="NOT_ASSESSED",
                currency=None,
                local_estimate=None,
                breakdown=(),
                identity=identity,
                usage=usage.as_dict(),
                price_binding=price_binding,
                evidence_refs=evidence_refs,
                unavailable_reasons=tuple(missing),
            )

        breakdown: list[dict[str, Any]] = []
        total = Decimal("0")
        for component in ("input", "cache_read", "cache_write", "output"):
            count = counts[component]
            rate_raw = tariff["rates_per_million"].get(component)
            rate = decimal_value(rate_raw or "0", field_name=f"rate.{component}")
            subtotal = (Decimal(count) * rate / PER_MILLION).quantize(
                COMPONENT_QUANTUM, rounding=ROUND_HALF_EVEN
            )
            total += subtotal
            breakdown.append(
                {
                    "component": component,
                    "tokens": count,
                    "rate_per_million": decimal_string(rate),
                    "subtotal": decimal_string(subtotal),
                    "currency": tariff["currency"],
                }
            )

        for index, modifier in enumerate(tariff.get("modifiers") or []):
            if modifier.get("kind") != "MULTIPLIER":
                raise AccountingContractError("only predefined MULTIPLIER modifiers are permitted")
            factor = decimal_value(modifier["value"], field_name=f"modifier[{index}].value")
            before = total
            total = (total * factor).quantize(COMPONENT_QUANTUM, rounding=ROUND_HALF_EVEN)
            breakdown.append(
                {
                    "component": "modifier",
                    "modifier_id": modifier["modifier_id"],
                    "kind": "MULTIPLIER",
                    "value": decimal_string(factor),
                    "basis_before": decimal_string(before),
                    "subtotal_after": decimal_string(total),
                    "currency": tariff["currency"],
                }
            )

        total = total.quantize(TOTAL_QUANTUM, rounding=ROUND_HALF_EVEN)
        return AccountingEnvelope(
            stage=stage,
            accounting_status=(
                "CONSERVATIVE_QUOTE" if stage == "QUOTE" else "EXACT_FROM_REPORTED_USAGE"
            ),
            currency=tariff["currency"],
            local_estimate=decimal_string(total),
            breakdown=tuple(breakdown),
            identity=identity,
            usage=usage.as_dict(),
            price_binding=price_binding,
            evidence_refs=evidence_refs,
        )

    def direct_components(
        self,
        *,
        counts: dict[str, int],
        rates_per_million: dict[str, str],
        multiplier: str = "1",
    ) -> str:
        """Compatibility helper; still delegates to the one Decimal formula."""
        subtotal = Decimal("0")
        for component, count in counts.items():
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise AccountingContractError(f"invalid token count for {component}")
            rate = decimal_value(rates_per_million[component], field_name=f"rate.{component}")
            subtotal += Decimal(count) * rate / PER_MILLION
        subtotal *= decimal_value(multiplier, field_name="multiplier")
        return decimal_string(subtotal.quantize(TOTAL_QUANTUM, rounding=ROUND_HALF_EVEN))
