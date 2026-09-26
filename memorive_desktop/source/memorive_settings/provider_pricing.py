"""Price estimates resolved from existing project pricing sources before sending."""
from datetime import datetime, timezone
from decimal import Decimal

from .provider_catalog_aliases import official_deepseek, FLASH_ALIASES, SOURCE
from .leaderboard import identity, join_base
from .leaderboard_price_registry import resolved_public_route_price


def snapshot(service, model, at):
    requested = model.get("model_name")
    if official_deepseek(service):
        name = FLASH_ALIASES.get(requested, requested)
        if name not in {"deepseek-flash", "deepseek-v4-pro"}:
            return None
        stamp = datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(timezone.utc)
        if stamp < datetime(2026, 9, 10, 4, tzinfo=timezone.utc):
            return None
        peak = stamp.weekday() < 5 and (1 <= stamp.hour < 4 or 6 <= stamp.hour < 10)
        rates = ("0.02", "1", "4") if name == "deepseek-flash" else ("0.15", "4.5", "13.5")
        factor = Decimal(2 if peak else 1)
        return {
            "schema_version": "ProviderPriceSnapshot-v1", "source": SOURCE,
            "verified_at": "2026-09-14", "requested_model": requested,
            "billing_model": name, "currency": "CNY", "unit_tokens": 1_000_000,
            "price_basis": "PUBLISHED_PRICE_ESTIMATE", "effective_from": "2026-09-10T04:00:00Z",
            "billing_period": "PEAK" if peak else "OFF_PEAK", "request_at": at,
            "input_cache_hit": str(Decimal(rates[0]) * factor),
            "input_cache_miss": str(Decimal(rates[1]) * factor),
            "output": str(Decimal(rates[2]) * factor),
        }
    if not isinstance(requested, str) or not requested.strip():
        return None
    vendor, base_model, _ = identity({"model_id": requested, "source_id": "runtime-accounting"})
    price, evidence = resolved_public_route_price(vendor, join_base(base_model))
    if price is not None:
        return {
            "schema_version": "ProviderPriceSnapshot-v1",
            "source": price["source_url"], "source_id": price["source_id"],
            "verified_at": price["retrieved_at"], "requested_model": requested,
            "billing_model": price["model_id"], "currency": price["currency"],
            "unit_tokens": 1_000_000, "price_basis": "PROJECT_LEADERBOARD_REFERENCE_ESTIMATE",
            "billing_period": "STANDARD", "request_at": at,
            "input_cache_hit": str(price["input"]),
            "input_cache_miss": str(price["input"]), "output": str(price["output"]),
            "reference_scope": price["scope"],
            "selection": evidence["regular_price_reason"],
            "promotion_removed": evidence["promotion_removed"],
        }
    return None


def estimate(price, usage):
    if not price or not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens")
    output = usage.get("completion_tokens")
    if any(type(n) is not int or n < 0 for n in (prompt, output)):
        return None
    hit = usage.get("cached_input_tokens", usage.get("prompt_cache_hit_tokens", 0))
    if type(hit) is not int or hit < 0:
        return None
    hit = min(prompt, hit)
    value = (
        Decimal(hit) * Decimal(price["input_cache_hit"])
        + Decimal(prompt - hit) * Decimal(price["input_cache_miss"])
        + Decimal(output) * Decimal(price["output"])
    ) / Decimal(price["unit_tokens"])
    return float(value)
