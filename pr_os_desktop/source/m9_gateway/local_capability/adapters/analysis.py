"""LOCAL_ANALYSIS adapter output validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..errors import ContractViolation
from ..types import LogicalRole
from . import InjectedRoleAdapter, Runner


def validate_analysis_output(value: Mapping[str, Any]) -> None:
    candidate = value.get("candidate")
    if not isinstance(candidate, Mapping):
        raise ContractViolation("ANALYSIS_CANDIDATE_MISSING")
    citations = value.get("citations")
    if not isinstance(citations, list) or any(not isinstance(item, str) or not item for item in citations):
        raise ContractViolation("ANALYSIS_CITATIONS_MISSING")
    if not isinstance(value.get("abstain"), bool):
        raise ContractViolation("ANALYSIS_ABSTAIN_MISSING")
    if not value["abstain"] and not value["citations"]:
        raise ContractViolation("ANALYSIS_CITATION_HARD_GATE")
    claims = candidate.get("claims")
    if claims is not None and not isinstance(claims, list):
        raise ContractViolation("ANALYSIS_CLAIMS_INVALID")
    if value["abstain"] and claims:
        raise ContractViolation("ANALYSIS_GLOBAL_ABSTENTION_WITH_CLAIMS")
    abstentions = value.get("abstentions", [])
    if not isinstance(abstentions, list):
        raise ContractViolation("ANALYSIS_ABSTENTIONS_INVALID")
    targets: set[str] = set()
    for row in abstentions:
        if not isinstance(row, Mapping) or set(row) != {"target", "reason", "source_refs"}:
            raise ContractViolation("ANALYSIS_ABSTENTION_INVALID")
        target = row.get("target")
        reason = row.get("reason")
        source_refs = row.get("source_refs")
        if (
            not isinstance(target, str)
            or not target
            or target in targets
            or reason not in {"ABSENT_IN_SOURCE", "SOURCE_CONFLICT", "OWNERSHIP_CONFLICT"}
            or not isinstance(source_refs, list)
            or not source_refs
            or any(not isinstance(item, str) or not item for item in source_refs)
        ):
            raise ContractViolation("ANALYSIS_ABSTENTION_INVALID")
        targets.add(target)


class AnalysisAdapter(InjectedRoleAdapter):
    logical_role = LogicalRole.LOCAL_ANALYSIS

    def __init__(self, runner: Runner) -> None:
        super().__init__(runner, validate_analysis_output)
