"""Memorive FACT-IDENTITY/EVIDENCE_REVIEW evidence report for the bounded repair-loop decision."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from evidence_extraction.fact_identity_evidence_extraction_contract_core import validate_artifact_ref


def build_repair_loop_evaluation_report(
    *,
    current_loop_contract_ref: Mapping[str, Any],
    frozen_dataset_refs: Sequence[Mapping[str, Any]],
    created_at: str,
) -> dict[str, Any]:
    validate_artifact_ref(current_loop_contract_ref)
    for ref in frozen_dataset_refs:
        validate_artifact_ref(ref)
    return {
        "artifact_type": "repair_loop_evaluation_report",
        "current_loop_contract_ref": dict(current_loop_contract_ref),
        "frozen_dataset_refs": [dict(ref) for ref in frozen_dataset_refs],
        "evidence_availability": "theory_and_cost_only",
        "quality_findings": [],
        "cost_model": {
            "second_full_review_cost_units": "not_measured",
            "model_calls": 0,
        },
        "termination_analysis": {
            "second_full_review_executed": False,
            "outcome_evidence_status": "not_tested",
        },
        "v1_v2_consistency_findings": [],
        "limitations": [
            "No frozen second-round candidate output was available.",
            "Theory and cost review cannot prove the second round ineffective.",
        ],
        "recommendation": "keep_closed",
        "created_at": created_at,
    }


__all__ = ["build_repair_loop_evaluation_report"]
