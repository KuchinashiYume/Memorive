"""Local deterministic page-family diagnostic; no model or network access."""

from __future__ import annotations

import math
import unicodedata
from typing import Any, Mapping

from .contracts import OCRQualificationContractError, PAGE_MODIFIERS, SAFE_OWNERSHIP


_SIGNAL_DEFAULTS = {
    "text_coverage_ratio": 0.0,
    "replacement_ratio": 0.0,
    "control_ratio": 0.0,
    "column_count": 1,
    "table_cell_count": 0,
    "numeric_token_ratio": 0.0,
    "formula_token_ratio": 0.0,
    "role_boundary_count": 0,
    "chart_label_count": 0,
    "blur_score": 0.0,
    "skew_degrees": 0.0,
    "contrast_score": 1.0,
    "dense_symbol_ratio": 0.0,
    "minimum_font_px": 16.0,
    "rotated_region_count": 0,
}


def _ratio(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OCRQualificationContractError("DIAGNOSTIC_SIGNAL_INVALID", [field])
    result = float(value)
    if not math.isfinite(result) or result < 0.0 or result > 1.0:
        raise OCRQualificationContractError("DIAGNOSTIC_RATIO_OUT_OF_RANGE", [field])
    return result


def _count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OCRQualificationContractError("DIAGNOSTIC_COUNT_INVALID", [field])
    return value


def diagnose_page(
    native_text: str,
    *,
    data_ownership: str,
    signals: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic diagnostic that cannot itself execute a route."""

    if not isinstance(native_text, str):
        raise OCRQualificationContractError("NATIVE_TEXT_TYPE_INVALID")
    if data_ownership not in {"self", "public-safe", "entrusted", "unknown"}:
        raise OCRQualificationContractError("OWNERSHIP_VALUE_INVALID", [data_ownership])
    supplied = dict(signals or {})
    unknown = sorted(set(supplied) - set(_SIGNAL_DEFAULTS))
    if unknown:
        raise OCRQualificationContractError("DIAGNOSTIC_SIGNAL_UNKNOWN", unknown)
    values = {**_SIGNAL_DEFAULTS, **supplied}
    ratios = {
        key: _ratio(values[key], key)
        for key in (
            "text_coverage_ratio",
            "replacement_ratio",
            "control_ratio",
            "numeric_token_ratio",
            "formula_token_ratio",
            "blur_score",
            "contrast_score",
            "dense_symbol_ratio",
        )
    }
    counts = {
        key: _count(values[key], key)
        for key in (
            "column_count",
            "table_cell_count",
            "role_boundary_count",
            "chart_label_count",
            "rotated_region_count",
        )
    }
    skew = values["skew_degrees"]
    font = values["minimum_font_px"]
    if isinstance(skew, bool) or not isinstance(skew, (int, float)) or not math.isfinite(float(skew)):
        raise OCRQualificationContractError("DIAGNOSTIC_SIGNAL_INVALID", ["skew_degrees"])
    if isinstance(font, bool) or not isinstance(font, (int, float)) or not math.isfinite(float(font)) or float(font) <= 0:
        raise OCRQualificationContractError("DIAGNOSTIC_SIGNAL_INVALID", ["minimum_font_px"])

    compact = "".join(native_text.split())
    forbidden_controls = sum(
        unicodedata.category(char) == "Cc" and char not in "\t\n\r"
        for char in native_text
    )
    observed_control_ratio = forbidden_controls / max(len(native_text), 1)
    replacement_ratio = max(ratios["replacement_ratio"], compact.count("\ufffd") / max(len(compact), 1))
    control_ratio = max(ratios["control_ratio"], observed_control_ratio)
    native_reasons: list[str] = []
    if not compact:
        native_reasons.append("NATIVE_TEXT_EMPTY")
    if ratios["text_coverage_ratio"] < 0.65:
        native_reasons.append("NATIVE_TEXT_COVERAGE_LOW")
    if replacement_ratio >= 0.02:
        native_reasons.append("NATIVE_TEXT_REPLACEMENT_CORRUPTION")
    if control_ratio >= 0.02:
        native_reasons.append("NATIVE_TEXT_CONTROL_CORRUPTION")
    native_quality = "PASS" if not native_reasons else "FAIL"

    modifiers: list[str] = []
    if ratios["blur_score"] >= 0.35:
        modifiers.append("blur")
    if abs(float(skew)) >= 2.0:
        modifiers.append("skew")
    if ratios["contrast_score"] <= 0.45:
        modifiers.append("low_contrast")
    if ratios["dense_symbol_ratio"] >= 0.25:
        modifiers.append("dense_symbols")
    if float(font) < 10.0:
        modifiers.append("small_font")
    if counts["rotated_region_count"] > 0:
        modifiers.append("rotated_region")
    if not set(modifiers).issubset(PAGE_MODIFIERS):  # defensive invariant
        raise OCRQualificationContractError("DIAGNOSTIC_MODIFIER_INTERNAL_INVALID")

    family_reasons: list[str] = []
    if native_quality == "PASS":
        family = "digital"
        family_reasons.append("NATIVE_TEXT_QUALITY_PASS")
    elif counts["table_cell_count"] >= 4 and ratios["numeric_token_ratio"] >= 0.15:
        family = "table_numeric"
        family_reasons.append("TABLE_NUMERIC_SIGNALS")
    elif ratios["formula_token_ratio"] >= 0.08 or ratios["dense_symbol_ratio"] >= 0.35:
        family = "formula"
        family_reasons.append("FORMULA_OR_DENSE_SYMBOL_SIGNALS")
    elif counts["chart_label_count"] >= 2:
        family = "chart_mixed"
        family_reasons.append("CHART_LABEL_SIGNALS")
    elif counts["column_count"] >= 2:
        family = "multicolumn"
        family_reasons.append("MULTICOLUMN_SIGNAL")
    elif counts["role_boundary_count"] >= 4:
        family = "role_dense"
        family_reasons.append("ROLE_BOUNDARY_DENSITY")
    elif {"blur", "skew", "low_contrast"} & set(modifiers):
        family = "degraded_scan"
        family_reasons.append("SCAN_DEGRADATION_SIGNAL")
    else:
        family = "clean_scan"
        family_reasons.append("SCAN_WITHOUT_DOMINANT_SPECIAL_FAMILY")

    high_risk = (
        family in {"formula", "table_numeric", "chart_mixed"}
        or bool({"blur", "low_contrast", "dense_symbols", "small_font"} & set(modifiers))
    )
    confidence = 1.0
    competing_signals = sum(
        [
            counts["table_cell_count"] >= 4,
            ratios["formula_token_ratio"] >= 0.08,
            counts["chart_label_count"] >= 2,
            counts["column_count"] >= 2,
            counts["role_boundary_count"] >= 4,
        ]
    )
    if competing_signals > 1:
        confidence = 0.75
        family_reasons.append("MULTIPLE_FAMILY_SIGNALS")
    if data_ownership not in SAFE_OWNERSHIP:
        family_reasons.append("OWNERSHIP_BLOCKS_PROVIDER_VISIBILITY")

    return {
        "native_text_quality": native_quality,
        "page_family_primary": family,
        "modifiers": sorted(modifiers),
        "risk_level": "high-risk" if high_risk else "normal",
        "diagnostic_confidence": confidence,
        "reasons": sorted(set(native_reasons + family_reasons)),
        "data_ownership": data_ownership,
        "provider_visibility_allowed": data_ownership in SAFE_OWNERSHIP,
        "visual_call_count": 0,
        "model_or_network_used": False,
    }
