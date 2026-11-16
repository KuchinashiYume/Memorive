"""Deterministic Delta risk routing; the reviewer does not choose its own mode."""
from __future__ import annotations

from enum import Enum
from typing import Any


class DeltaRisk(str, Enum):
    LOCAL_ONLY = "local_only"
    LOW_RISK = "low_risk"
    HIGH_RISK = "high_risk"


_LOCAL_KINDS = {
    "format",
    "metadata",
    "optional_anchor_delete",
    "list_reorder",
    "old_hash_replace",
    "version_trace",
}
_HIGH_RISK_ATOMS = {
    "research_object",
    "research_relation",
    "population_or_system",
    "sample_control",
    "intervention",
    "method_step",
    "instrument",
    "parameter",
    "value",
    "unit",
    "direction",
    "phase_time",
    "sample_size",
    "statistical_scope",
    "comparison",
    "claim_strength",
    "conclusion_scope",
    "limitation",
    "source_anchor",
}


def classify_delta_risk(changes: list[dict[str, Any]]) -> DeltaRisk:
    if not isinstance(changes, list) or not changes:
        return DeltaRisk.HIGH_RISK
    local_only = True
    for change in changes:
        if not isinstance(change, dict):
            return DeltaRisk.HIGH_RISK
        kind = str(change.get("kind") or "")
        semantic_change = change.get("semantic_change") is True
        atoms = set(change.get("atom_types") or [])
        if change.get("anchor_disputed") is True or change.get("cross_field_dependency") is True:
            return DeltaRisk.HIGH_RISK
        if atoms & _HIGH_RISK_ATOMS:
            return DeltaRisk.HIGH_RISK
        if kind not in _LOCAL_KINDS or semantic_change:
            local_only = False
    if local_only:
        return DeltaRisk.LOCAL_ONLY
    if all(change.get("optional") is True for change in changes):
        return DeltaRisk.LOW_RISK
    return DeltaRisk.HIGH_RISK


def reviewer_mode_for_risk(risk: DeltaRisk) -> dict[str, Any]:
    if risk is DeltaRisk.LOCAL_ONLY:
        return {"call_reviewer": False, "mode": "local", "escalation_on_block": None}
    if risk is DeltaRisk.LOW_RISK:
        return {"call_reviewer": True, "mode": "low_risk", "escalation_on_block": "high_risk_or_human"}
    return {"call_reviewer": True, "mode": "high_risk", "escalation_on_block": "human_required"}
