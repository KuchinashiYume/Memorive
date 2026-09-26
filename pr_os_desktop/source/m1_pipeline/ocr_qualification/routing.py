"""Qualification lookup and explain-route projection.

T07 can recommend a future route but cannot activate one.  Every returned plan
therefore has zero visual calls and fallback disabled.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import (
    OCRQualificationContractError,
    SAFE_OWNERSHIP,
    validate_page_family_qualification,
)


_ROLE_ORDER = {"PRIMARY": 0, "RESCUE": 1, "REVIEW_ONLY": 2}


def explain_route(
    diagnostic: Mapping[str, Any],
    qualification_matrix: Sequence[Mapping[str, Any]],
    *,
    output_lane: str,
    preferred_candidate_order: Sequence[str] = (),
    runtime_availability: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Explain an exact-cell route without executing or enabling a channel."""

    required = {
        "native_text_quality",
        "page_family_primary",
        "modifiers",
        "data_ownership",
    }
    missing = sorted(required - set(diagnostic))
    if missing:
        raise OCRQualificationContractError("DIAGNOSTIC_FIELDS_MISSING", missing)
    availability = dict(runtime_availability or {})
    base = {
        "visual_call_count": 0,
        "fallback": "DISABLED",
        "activation_authorized": False,
        "selected_candidate": None,
        "recommended_candidate": None,
        "page_family": diagnostic["page_family_primary"],
        "modifiers": sorted(set(diagnostic["modifiers"])),
        "output_lane": output_lane,
    }
    if diagnostic["native_text_quality"] == "PASS":
        return {
            **base,
            "route": "LOCAL_TEXT_NO_VISUAL_CALL",
            "reason_codes": ["NATIVE_TEXT_QUALITY_PASS"],
            "human_review_required": False,
        }
    if diagnostic["data_ownership"] not in SAFE_OWNERSHIP:
        return {
            **base,
            "route": "HUMAN_REVIEW",
            "reason_codes": ["OWNERSHIP_PROVIDER_VISIBILITY_BLOCKED"],
            "human_review_required": True,
        }

    exact_cells = []
    for raw in qualification_matrix:
        cell = validate_page_family_qualification(raw)
        if (
            cell["page_family"] == diagnostic["page_family_primary"]
            and cell["modifiers"] == sorted(set(diagnostic["modifiers"]))
            and cell["output_lane"] == output_lane
        ):
            exact_cells.append(cell)
    if not exact_cells:
        return {
            **base,
            "route": "HUMAN_REVIEW_KEEP_DISABLED",
            "reason_codes": ["EXACT_QUALIFICATION_CELL_UNTESTED"],
            "human_review_required": True,
        }

    preferred_rank = {candidate: rank for rank, candidate in enumerate(preferred_candidate_order)}
    candidates = sorted(
        (
            cell
            for cell in exact_cells
            if cell["role_eligibility"] in _ROLE_ORDER
            and cell["quality_verdict"] == "PASS"
            and cell["identity_verdict"] == "PASS"
            and cell["accounting_verdict"] == "PASS"
            and cell["resource_verdict"] == "PASS"
        ),
        key=lambda cell: (
            _ROLE_ORDER[cell["role_eligibility"]],
            preferred_rank.get(cell["candidate_key"], len(preferred_rank)),
            cell["candidate_key"],
        ),
    )
    if not candidates:
        return {
            **base,
            "route": "HUMAN_REVIEW_KEEP_DISABLED",
            "reason_codes": ["NO_HARD_GATE_QUALIFIED_EXACT_CELL"],
            "human_review_required": True,
        }
    chosen = candidates[0]
    if availability.get(chosen["candidate_key"], True) is not True:
        return {
            **base,
            "route": "HUMAN_REVIEW_KEEP_DISABLED",
            "recommended_candidate": chosen["candidate_key"],
            "reason_codes": ["QUALIFIED_CHANNEL_UNAVAILABLE", "SILENT_FALLBACK_FORBIDDEN"],
            "human_review_required": True,
        }
    review_only = chosen["role_eligibility"] == "REVIEW_ONLY"
    return {
        **base,
        "route": "QUALIFIED_RECOMMENDATION_KEEP_DISABLED",
        "recommended_candidate": chosen["candidate_key"],
        "recommended_role": chosen["role_eligibility"],
        "qualification_evidence_refs": chosen["evidence_refs"],
        "reason_codes": [
            "T07_QUALIFICATION_DOES_NOT_AUTHORIZE_ACTIVATION",
            "REVIEWER_OUTPUT_MUST_NOT_REWRITE_BODY" if review_only else "FUTURE_ACTIVATION_REQUIRED",
        ],
        "human_review_required": review_only,
    }
