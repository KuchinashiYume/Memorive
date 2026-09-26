"""Reference builder for AccountingPanelProjection-v3.

Inputs are normalized, allowlisted accounting rows plus a frozen
SharedModelCommerceContext-v1 snapshot.  The builder reads no files, secrets or
provider APIs and never invents a missing charge or exchange rate.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


DISPLAY_LABEL = {"USD": "USD", "CNY": "RMB", "JPY": "JPY"}


def _decimal(value: Any, code: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(code) from error
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(code)
    return parsed


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if '.' in rendered: rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _timestamp(value: Any) -> datetime:
    rendered = str(value or "")
    if rendered.endswith("Z"):
        rendered = rendered[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(rendered)
    except ValueError as error:
        raise ValueError("ACCOUNTING_TIMESTAMP_INVALID") from error
    if parsed.tzinfo is None:
        raise ValueError("ACCOUNTING_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _integer(value: Any, code: str) -> int:
    if isinstance(value, bool):
        raise ValueError(code)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(code) from error
    if parsed < 0:
        raise ValueError(code)
    return parsed


def _normalize(raw: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "attempt_id", "occurred_at", "job_id", "profile_ref", "profile_kind",
        "provider_label", "model_label", "model_id", "outcome", "tokens_input",
        "tokens_output", "tokens_reasoning", "cost_status", "cost_value", "currency",
        "source_kind",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise ValueError("ACCOUNTING_ATTEMPT_KEY_SET_INVALID")
    output = _integer(raw["tokens_output"], "ACCOUNTING_OUTPUT_TOKENS_INVALID")
    reasoning = _integer(raw["tokens_reasoning"], "ACCOUNTING_REASONING_TOKENS_INVALID")
    if reasoning > output:
        raise ValueError("ACCOUNTING_REASONING_NOT_SUBSET_OF_OUTPUT")
    kind = str(raw["profile_kind"])
    if kind not in {"API", "CLI", "LOCAL"}:
        raise ValueError("ACCOUNTING_PROFILE_KIND_INVALID")
    outcome = str(raw["outcome"])
    if outcome not in {"SUCCEEDED", "FAILED"}:
        raise ValueError("ACCOUNTING_OUTCOME_INVALID")
    cost_status = str(raw["cost_status"])
    if cost_status not in {"ACTUAL", "ESTIMATED", "NOT_ASSESSED"}:
        raise ValueError("ACCOUNTING_COST_STATUS_INVALID")
    source_kind = str(raw["source_kind"])
    if source_kind not in {"ACCOUNTING_ENVELOPE", "SETTLEMENT_OBSERVATION", "LEGACY_M11"}:
        raise ValueError("ACCOUNTING_SOURCE_KIND_INVALID")
    currency = str(raw["currency"] or "").upper() or None
    if currency and (len(currency) != 3 or not currency.isalpha()):
        raise ValueError("ACCOUNTING_CURRENCY_INVALID")
    if cost_status != "NOT_ASSESSED" and not currency:
        raise ValueError("ACCOUNTING_ASSESSABLE_CURRENCY_MISSING")
    job_id = raw["job_id"]
    if job_id is not None and (not isinstance(job_id, str) or not job_id or len(job_id) > 192):
        raise ValueError("ACCOUNTING_JOB_ID_INVALID")
    return {
        "attempt_id": str(raw["attempt_id"]),
        "occurred_at": _timestamp(raw["occurred_at"]),
        "job_id": job_id,
        "profile_ref": str(raw["profile_ref"]),
        "profile_kind": kind,
        "provider_label": str(raw["provider_label"]),
        "model_label": str(raw["model_label"]),
        "model_id": str(raw["model_id"]),
        "outcome": outcome,
        "tokens_input": _integer(raw["tokens_input"], "ACCOUNTING_INPUT_TOKENS_INVALID"),
        "tokens_output": output,
        "tokens_reasoning": reasoning,
        "cost_status": cost_status,
        "cost_value": Decimal("0") if cost_status == "NOT_ASSESSED" else _decimal(raw["cost_value"], "ACCOUNTING_COST_INVALID"),
        "currency": currency,
        "source_kind": source_kind,
    }


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _conversion(context: Mapping[str, Any], source: str, target: str) -> tuple[Decimal | None, str]:
    if source == target:
        return Decimal("1"), "NATIVE"
    fx = context.get("fx_snapshot") or {}
    status = str(fx.get("status") or "MISSING")
    if status not in {"AVAILABLE", "CACHED"}:
        return None, status if status in {"EXPIRED", "MISSING", "INVALID"} else "INVALID"
    rates = fx.get("units_per_usd") or {}
    try:
        source_rate = _decimal(rates[source], "ACCOUNTING_SOURCE_FX_INVALID")
        target_rate = _decimal(rates[target], "ACCOUNTING_TARGET_FX_INVALID")
    except (KeyError, ValueError):
        return None, "MISSING"
    return target_rate / source_rate, status


def build_projection(
    raw_attempts: Iterable[Mapping[str, Any]],
    params: Mapping[str, Any],
    commerce_context: Mapping[str, Any],
    *,
    now: datetime | None = None,
    refreshed: bool = False,
) -> dict[str, Any]:
    accepted = dict(params)
    allowed = {"scope", "current_job_id", "time_range", "model_profile_ref", "group_by", "preferred_currency"}
    if set(accepted) - allowed or any(str(key).startswith("_") for key in accepted):
        raise ValueError("ACCOUNTING_PRODUCT_PARAMS_INVALID")
    scope = str(accepted.get("scope") or "ALL")
    current_job_id = accepted.get("current_job_id")
    preset = str(accepted.get("time_range") or "30D")
    selected_profile = str(accepted.get("model_profile_ref") or "ALL")
    group_by = str(accepted.get("group_by") or "MODEL_PROFILE")
    currency = str(accepted.get("preferred_currency") or commerce_context.get("display_currency") or "USD").upper()
    if scope not in {"ALL", "CURRENT_JOB"} or preset not in {"7D", "30D", "MONTH"} or group_by != "MODEL_PROFILE":
        raise ValueError("ACCOUNTING_FILTER_INVALID")
    if currency not in DISPLAY_LABEL:
        raise ValueError("ACCOUNTING_DISPLAY_CURRENCY_UNSUPPORTED")
    if commerce_context.get("schema_version") != "SharedModelCommerceContext-v1":
        raise ValueError("ACCOUNTING_COMMERCE_CONTEXT_INVALID")

    all_models = list(commerce_context.get("models") or [])
    by_profile = {str(row.get("profile_ref")): dict(row) for row in all_models}
    if len(by_profile) != len(all_models):
        raise ValueError("ACCOUNTING_COMMERCE_PROFILE_DUPLICATE")
    if selected_profile != "ALL" and selected_profile not in by_profile:
        raise ValueError("ACCOUNTING_MODEL_PROFILE_UNKNOWN")

    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    end = observed_at.date()
    start = end.replace(day=1) if preset == "MONTH" else end - timedelta(days=6 if preset == "7D" else 29)
    rows = [_normalize(row) for row in raw_attempts]
    unknown = sorted({row["profile_ref"] for row in rows if row["profile_ref"] not in by_profile})
    if unknown:
        raise ValueError("ACCOUNTING_UNKNOWN_PROFILE_REF")
    rows = [row for row in rows if start <= row["occurred_at"].date() <= end]
    range_profile_refs = {row["profile_ref"] for row in rows}
    if selected_profile != "ALL":
        rows = [row for row in rows if row["profile_ref"] == selected_profile]

    model_options = []
    for row in all_models:
        profile_ref = str(row.get("profile_ref"))
        available = row.get("availability") == "AVAILABLE"
        historical = profile_ref in range_profile_refs
        if not (available or historical):
            continue
        projected = dict(row)
        projected["has_historical_usage"] = historical
        if historical and not available:
            projected["model_label"] = f"{projected.get('model_label') or projected.get('model_id') or profile_ref} · 已停用"
        model_options.append(projected)

    eligible = len(rows)
    bound = sum(1 for row in rows if row["job_id"])
    coverage = bound / eligible if eligible else 0.0
    current_ready = bool(current_job_id and any(row['job_id'] == current_job_id for row in rows))
    capability_reasons: list[str] = []
    if not eligible:
        capability_reasons.append("NO_ELIGIBLE_ATTEMPTS")
    if bound != eligible:
        capability_reasons.append("ATTEMPT_JOB_BINDING_INCOMPLETE")
    if not current_job_id:
        capability_reasons.append("CURRENT_JOB_ID_UNAVAILABLE")
    if scope == "CURRENT_JOB":
        if not current_ready:
            raise ValueError("ACCOUNTING_CURRENT_JOB_SCOPE_NOT_READY")
        rows = [row for row in rows if row["job_id"] == current_job_id]

    source_counts: defaultdict[str, int] = defaultdict(int)
    native: defaultdict[str, dict[str, Any]] = defaultdict(lambda: {"actual": Decimal("0"), "estimated": Decimal("0"), "unpriced": 0})
    daily: defaultdict[tuple[date, str], dict[str, Any]] = defaultdict(
        lambda: {"actual": defaultdict(Decimal), "estimated": defaultdict(Decimal), "requests": 0, "input": 0, "output": 0, "unpriced": 0}
    )
    failed = unpriced = input_total = output_total = reasoning_total = 0
    seen_profiles: set[str] = set()
    for row in rows:
        seen_profiles.add(row["profile_ref"])
        failed += int(row["outcome"] == "FAILED")
        input_total += row["tokens_input"]
        output_total += row["tokens_output"]
        reasoning_total += row["tokens_reasoning"]
        source_counts[row["source_kind"]] += 1
        bucket = daily[(row["occurred_at"].date(), row["profile_ref"])]
        bucket["requests"] += 1
        bucket["input"] += row["tokens_input"]
        bucket["output"] += row["tokens_output"]
        if row["cost_status"] == "NOT_ASSESSED":
            unpriced += 1
            bucket["unpriced"] += 1
            continue
        target = native[row["currency"]]
        target["actual" if row["cost_status"] == "ACTUAL" else "estimated"] += row["cost_value"]
        bucket["actual" if row["cost_status"] == "ACTUAL" else "estimated"][row["currency"]] += row["cost_value"]

    conversion_statuses: set[str] = set()

    def convert(values: Mapping[str, Decimal]) -> Decimal | None:
        total = Decimal("0")
        for source, value in values.items():
            factor, status = _conversion(commerce_context, source, currency)
            conversion_statuses.add(status)
            if factor is None:
                return None
            total += value * factor
        return total

    actual_display = convert({code: values["actual"] for code, values in native.items()}) if any(r['cost_status']=='ACTUAL' for r in rows) else None
    estimated_display = convert({code: values["estimated"] for code, values in native.items()}) if any(r['cost_status']=='ESTIMATED' for r in rows) else None
    assessable_display = convert({code:values['actual']+values['estimated'] for code,values in native.items()}) if native else None
    if not native:
        conversion_status = "NOT_ASSESSED"
    elif any(item in {"EXPIRED", "MISSING", "INVALID"} for item in conversion_statuses):
        conversion_status = next(item for item in ("INVALID", "EXPIRED", "MISSING") if item in conversion_statuses)
    elif conversion_statuses == {"NATIVE"}:
        conversion_status = "NATIVE"
    elif "CACHED" in conversion_statuses:
        conversion_status = "CACHED"
    else:
        conversion_status = "AVAILABLE"

    visible_refs = [selected_profile] if selected_profile != "ALL" else [ref for ref in by_profile if ref in seen_profiles]
    visible_models = [by_profile[ref] for ref in visible_refs]
    series: list[dict[str, Any]] = []
    for day in _date_range(start, end):
        measures: list[dict[str, Any]] = []
        for profile_ref in visible_refs:
            values = daily[(day, profile_ref)]
            converted_actual = convert(values["actual"]) if values['actual'] else None
            converted_estimated = convert(values["estimated"]) if values['estimated'] else None
            measures.append({
                "profile_ref": profile_ref,
                "display_currency": currency,
                "display_actual_cost": _decimal_text(converted_actual) if converted_actual is not None else None,
                "display_estimated_cost": _decimal_text(converted_estimated) if converted_estimated is not None else None,
                "request_count": values["requests"],
                "tokens_input": values["input"],
                "tokens_output": values["output"],
                "unpriced_attempt_count": values["unpriced"],
            })
        series.append({"bucket": day.isoformat(), "measures": measures})

    reasons: list[str] = []
    if any(values["estimated"] > 0 for values in native.values()):
        reasons.append("ESTIMATE_PRESENT")
    if unpriced:
        reasons.append("UNPRICED_ATTEMPTS_PRESENT")
    if bound != eligible:
        reasons.append("LEGACY_JOB_BINDING_PARTIAL")
    if conversion_status in {"EXPIRED", "MISSING", "INVALID"}:
        reasons.append(f"FX_{conversion_status}")
    status = "NOT_ASSESSED" if not native else "PARTIAL" if reasons else "READY"
    context = dict(commerce_context)
    context["display_currency"] = currency
    context["display_currency_label"] = DISPLAY_LABEL[currency]
    return {
        "schema_version": "AccountingPanelProjection-v3",
        "generated_at": observed_at.isoformat().replace("+00:00", "Z"),
        "status": status,
        "refresh": {"status": "REFRESHED" if refreshed else "CURRENT", "mode": "LOCAL_SNAPSHOT", "external_network_calls": 0},
        "filters": {"scope": scope, "current_job_id": current_job_id, "time_range": preset, "model_profile_ref": selected_profile, "preferred_currency": currency, "group_by": group_by},
        "range": {"preset": preset, "start_date": start.isoformat(), "end_date": end.isoformat(), "granularity": "DAY", "bucket_count": (end - start).days + 1},
        "scope_capabilities": {"current_job": {"status": "READY" if current_ready else "NOT_READY", "eligible_attempt_count": eligible, "bound_attempt_count": bound, "coverage_ratio": round(coverage, 6), "reason_codes": capability_reasons}},
        "commerce_context": context,
        "display_currency": currency,
        "display_currency_label": DISPLAY_LABEL[currency],
        "cost_display": {
            "actual_cost": _decimal_text(actual_display) if actual_display is not None else None,
            "estimated_cost": _decimal_text(estimated_display) if estimated_display is not None else None,
            "assessable_cost": _decimal_text(assessable_display) if assessable_display is not None else None,
            "conversion_status": conversion_status,
            "fx_snapshot_id": None if conversion_status in {"NATIVE", "NOT_ASSESSED"} else context["fx_snapshot"].get("id"),
        },
        "native_cost_buckets": [
            {"currency": code, "actual_cost": _decimal_text(values["actual"]), "estimated_cost": _decimal_text(values["estimated"]), "assessable_cost": _decimal_text(values["actual"] + values["estimated"]), "unpriced_attempt_count": unpriced if index == 0 else 0}
            for index, (code, values) in enumerate(sorted(native.items()))
        ],
        "attempt_count": len(rows),
        "failed_attempt_count": failed,
        "unpriced_attempt_count": unpriced,
        "tokens": {"input": input_total, "output": output_total, "reasoning_subset_of_output": reasoning_total},
        "model_options": model_options,
        "models": visible_models,
        "series": series,
        "source_summary": {"accounting_envelope_count": source_counts["ACCOUNTING_ENVELOPE"], "settlement_observation_count": source_counts["SETTLEMENT_OBSERVATION"], "legacy_record_count": source_counts["LEGACY_M11"], "reason_codes": reasons},
        "raw_private_content_included": False,
    }


__all__ = ["build_projection"]
