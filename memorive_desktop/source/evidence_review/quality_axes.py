"""Independent quality axes and conservative numeric advisories for quality-validation."""
from __future__ import annotations

import math
from typing import Any


PIPELINE_STATUSES = {"completed", "failed", "partial"}
ARTIFACT_AVAILABILITY = {"generated", "skipped_by_policy", "failed", "absent_unknown"}
VALIDATION_STATUSES = {"passed", "passed_with_flags", "failed", "not_run"}
EXTRACTION_COVERAGE = {"sufficient", "partial", "suspicious_gap", "not_assessed"}
REQUIRED_SLOT_STATES = {
    "present",
    "absent_in_source",
    "not_applicable",
    "extraction_gap",
    "not_assessed",
}
_PLAUSIBILITY_RULES = {
    "NON_NUMERIC_VALUE": {
        "severity": "medium",
        "rule": "numeric value must be parseable before magnitude checks",
    },
    "NON_FINITE_VALUE": {
        "severity": "high",
        "rule": "numeric value must be finite",
    },
    "PERCENT_OUT_OF_RANGE": {
        "severity": "high",
        "rule": "percentage values must remain within 0..100",
    },
    "EXTREME_MAGNITUDE_OR_UNIT": {
        "severity": "medium",
        "rule": "g/L magnitude at or above 10000 requires source/unit review",
    },
    "UNCERTAINTY_EXCEEDS_VALUE_SCALE": {
        "severity": "medium",
        "rule": "reported uncertainty must not exceed twice the absolute value scale",
    },
}
_SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


def _validate_axis(name: str, value: str, allowed: set[str]) -> str:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}; got {value!r}")
    return value


def build_quality_axes(
    *,
    pipeline_status: str,
    artifact_availability: str,
    validation_status: str,
    extraction_coverage: str,
    required_slots: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Keep operational, availability, validation, and coverage meanings separate."""
    pipeline_status = _validate_axis("pipeline_status", pipeline_status, PIPELINE_STATUSES)
    artifact_availability = _validate_axis(
        "artifact_availability", artifact_availability, ARTIFACT_AVAILABILITY
    )
    validation_status = _validate_axis(
        "validation_status", validation_status, VALIDATION_STATUSES
    )
    extraction_coverage = _validate_axis(
        "extraction_coverage", extraction_coverage, EXTRACTION_COVERAGE
    )
    slots = {
        str(name): _validate_axis(
            f"required_slot_state[{name}]", state, REQUIRED_SLOT_STATES
        )
        for name, state in (required_slots or {}).items()
    }
    return {
        "pipeline_status": pipeline_status,
        "artifact_availability": artifact_availability,
        "validation_status": validation_status,
        "extraction_coverage": extraction_coverage,
        "required_slot_state": slots,
        "artifact_failed": pipeline_status == "failed" or artifact_availability == "failed",
        "artifact_available": artifact_availability == "generated",
    }


def needs_review(credibility: str | None, *, manual_review_requested: bool = False) -> bool:
    """A local flag; it does not promote an ordinary gap to whole-card failure."""
    return bool(manual_review_requested or credibility != "verified")


def _normal(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, dict):
        return tuple(sorted((str(key), _normal(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_normal(item) for item in value)
    return value


def compare_numeric_facts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Compare values only after statistical and experimental scopes agree."""
    axes = (
        "metric",
        "unit",
        "condition_signature",
        "stat_type",
        "time_window",
        "sample_scope",
        "comparator",
    )
    different = [name for name in axes if _normal(left.get(name)) != _normal(right.get(name))]
    if different:
        return {
            "status": "not_directly_comparable",
            "different_axes": different,
            "hard_conflict": False,
        }
    try:
        left_value, right_value = float(left.get("value")), float(right.get("value"))
    except (TypeError, ValueError):
        return {"status": "not_assessed", "different_axes": [], "hard_conflict": False}
    if not (math.isfinite(left_value) and math.isfinite(right_value)):
        return {"status": "not_assessed", "different_axes": [], "hard_conflict": False}
    tolerance = max(1e-12, 1e-9 * max(abs(left_value), abs(right_value), 1.0))
    consistent = abs(left_value - right_value) <= tolerance
    return {
        "status": "consistent" if consistent else "conflict",
        "different_axes": [],
        "hard_conflict": not consistent,
        "difference": left_value - right_value,
    }


def assess_numeric_plausibility(
    fact: dict[str, Any],
    *,
    source_fidelity_status: str = "uncertain",
) -> dict[str, Any]:
    """Return an advisory while preserving the exact source value and unit."""
    if source_fidelity_status not in {"matched", "mismatched", "uncertain"}:
        raise ValueError("source_fidelity_status must be matched, mismatched, or uncertain")
    issues: list[str] = []
    try:
        numeric = float(fact.get("value"))
    except (TypeError, ValueError):
        numeric = None
        issues.append("NON_NUMERIC_VALUE")
    if numeric is not None and not math.isfinite(numeric):
        issues.append("NON_FINITE_VALUE")
    unit = str(fact.get("unit") or "")
    metric = str(fact.get("metric") or "")
    unit_key = unit.replace(" ", "").casefold()
    if numeric is not None and math.isfinite(numeric):
        if (unit_key in {"%", "percent", "percentage"} or "percent" in metric.casefold()) and not (
            0 <= numeric <= 100
        ):
            issues.append("PERCENT_OUT_OF_RANGE")
        if unit_key in {"g/l", "g·l−1", "g·l-1"} and abs(numeric) >= 10_000:
            issues.append("EXTREME_MAGNITUDE_OR_UNIT")
        try:
            uncertainty = abs(float(fact.get("uncertainty")))
        except (TypeError, ValueError):
            uncertainty = None
        if uncertainty is not None and uncertainty > max(abs(numeric) * 2, 1e-12):
            issues.append("UNCERTAINTY_EXCEEDS_VALUE_SCALE")
    issue_codes = list(dict.fromkeys(issues))
    triggered_rules = [
        {"rule_id": code, **_PLAUSIBILITY_RULES[code]}
        for code in issue_codes
    ]
    severity = max(
        (item["severity"] for item in triggered_rules),
        key=lambda value: _SEVERITY_RANK[value],
        default="none",
    )
    return {
        "source_fidelity_status": source_fidelity_status,
        "plausibility_status": "suspicious" if issues else "plausible",
        "issue_codes": issue_codes,
        "triggered_rules": triggered_rules,
        "severity": severity,
        "source_text_value": fact.get("source_text_value", fact.get("value")),
        "source_text_unit": fact.get("source_text_unit", fact.get("unit")),
        "do_not_autocorrect": True,
    }
