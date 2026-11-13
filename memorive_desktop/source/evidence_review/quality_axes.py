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
