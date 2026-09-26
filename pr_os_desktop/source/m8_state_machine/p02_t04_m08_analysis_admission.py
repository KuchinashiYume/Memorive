"""PR-OS P02/T04/M08 build-only Analysis admission requests."""

from __future__ import annotations

from typing import Any, Mapping

from .errors import AdmissionBlocked, RealStateTransitionForbidden
from m13_artifact_registry.p02_t04_m13_materialization import ArtifactFactory
from m13_artifact_registry.p02_t04_m13_products import (
    ArtifactProduct,
    artifact_ref,
    stable_identifier,
    unique_text,
)


ADMISSION_SCHEMA_VERSION = "p02-t04-analysis-admission-request-v1"
ANALYSIS_SCOPE_DEBT_ID = "P02-T04-DEBT-ANALYSIS-SCOPE-STATE-CONTRACT"


def _fixture_contract_is_usable(contract: Mapping[str, Any] | None) -> bool:
    if contract is None:
        return False
    return (
        contract.get("authority") == "fixture_only_non_authoritative"
        and contract.get("non_authoritative") is True
        and contract.get("simulation_only") is True
        and contract.get("deployment_eligible") is False
        and contract.get("approved") is False
        and isinstance(contract.get("contract_ref"), str)
        and bool(contract.get("contract_ref"))
        and contract.get("requested_scope") == "analysis"
    )


def build_analysis_admission_request(
    *,
    factory: ArtifactFactory,
    assessment: ArtifactProduct,
    analysis: Mapping[str, Any],
    candidate_card: Mapping[str, Any],
    context_pack: Mapping[str, Any] | None,
    state_contract: Mapping[str, Any] | None,
    m4_source_resolution: Mapping[str, Any] | None,
    m6_review: Mapping[str, Any] | None,
    requested_at: str,
) -> ArtifactProduct:
    reasons: list[str] = []
    debt_refs: list[str] = []
    if not _fixture_contract_is_usable(state_contract):
        reasons.append("ANALYSIS_SCOPE_STATE_CONTRACT_MISSING")
        debt_refs.append(ANALYSIS_SCOPE_DEBT_ID)
    if assessment.payload["overall_compatibility"] != "compatible":
        reasons.append("COMPATIBILITY_ASSESSMENT_NOT_COMPATIBLE")
    if assessment.payload["freshness"]["status"] != "fresh":
        reasons.append("ANALYSIS_FRESHNESS_NOT_FRESH")
    if assessment.payload["human_review_required"]:
        reasons.append("COMPATIBILITY_HUMAN_REVIEW_REQUIRED")
    if not isinstance(m4_source_resolution, Mapping) or (
        m4_source_resolution.get("all_literature_source_ids_resolve_to_pack") is not True
    ):
        reasons.append("M4_SOURCE_RESOLUTION_EVIDENCE_MISSING")
    if not isinstance(m6_review, Mapping) or m6_review.get("review_status") != "passed":
        reasons.append("M6_INDEPENDENT_REVIEW_EVIDENCE_MISSING")
    unresolved = {
        str(item.get("requirement")): str(item.get("reason_code"))
        for item in analysis.get("unresolved_parent_requirements", [])
        if isinstance(item, Mapping)
    }
    if "exact_card_version" in unresolved:
        reasons.append("ANALYSIS_EXACT_CARD_VERSION_UNRESOLVED")
    if {
        "independent_context_pack",
        "independent_context_pack_artifact",
    } & set(unresolved):
        reasons.append("ANALYSIS_INDEPENDENT_CONTEXT_PACK_UNRESOLVED")
    request_status = "blocked" if reasons else "buildable"
    request_id = stable_identifier(
        "admission_request_",
        [
            analysis["artifact_id"],
            candidate_card["artifact_id"],
            assessment.envelope["artifact_id"],
            requested_at,
        ],
    )
    contract_ref = (
        str(state_contract["contract_ref"])
        if _fixture_contract_is_usable(state_contract)
        else None
    )
    payload = {
        "schema_version": ADMISSION_SCHEMA_VERSION,
        "request_id": request_id,
        "requested_at": requested_at,
        "requested_scope": "analysis",
        "requested_transition": {
            "from_status": "analysis_generated",
            "to_status": "analysis_admitted",
            "state_owner": "M8",
            "event_emitted": False,
        },
        "request_build_status": request_status,
        "dry_run": True,
        "simulation_only": True,
        "non_authoritative": True,
        "state_contract_ref": contract_ref,
        "state_contract_debt_refs": debt_refs,
        "review_ref": str(m6_review.get("review_ref"))
        if isinstance(m6_review, Mapping) and m6_review.get("review_ref")
        else None,
        "m4_source_resolution_ref": str(m4_source_resolution.get("evidence_ref"))
        if isinstance(m4_source_resolution, Mapping)
        and m4_source_resolution.get("evidence_ref")
        else None,
        "compatibility_assessment_ref": artifact_ref(assessment.envelope),
        "subjects": {
            "analysis": artifact_ref(analysis),
            "candidate_card": artifact_ref(candidate_card),
            "context_pack": artifact_ref(context_pack)
            if context_pack is not None
            else None,
        },
        "reason_codes": unique_text(reasons),
    }
    parents = [analysis, candidate_card, assessment.envelope]
    if context_pack is not None:
        parents.append(context_pack)
    envelope = factory.create(
        artifact_type="analysis_admission_request",
        payload=payload,
        schema_ref="pros://p02/t04/analysis-admission-request/v1",
        paper_ids=analysis.get("source_scope", {}).get("paper_ids", []),
        source_artifact_ids=[item["artifact_id"] for item in parents],
        parents=[
            factory.link(
                item,
                relation="admission_evidence",
                source_field="subjects_or_assessment",
                evidence_ref="admission-request:" + request_id,
            )
            for item in parents
        ],
        metadata={
            "request_build_status": request_status,
            "dry_run": True,
            "state_owner": "M8",
            "event_emitted": False,
        },
        evidence_refs=[
            "artifact:" + assessment.envelope["artifact_id"],
            "contract:" + contract_ref if contract_ref else "debt:" + ANALYSIS_SCOPE_DEBT_ID,
        ],
        semantic_key=request_id,
    )
    return ArtifactProduct(payload=payload, envelope=envelope)


def require_buildable(request: ArtifactProduct) -> None:
    if request.payload.get("request_build_status") != "buildable":
        raise AdmissionBlocked(
            "Analysis admission remains blocked: "
            + ",".join(request.payload.get("reason_codes", []))
        )


def validate_real_downstream_eligibility(
    request: ArtifactProduct,
    state_contract: Mapping[str, Any] | None,
) -> None:
    require_buildable(request)
    if state_contract is None or (
        state_contract.get("non_authoritative") is True
        or state_contract.get("simulation_only") is True
        or state_contract.get("deployment_eligible") is not True
        or state_contract.get("approved") is not True
    ):
        raise AdmissionBlocked(
            "fixture/non-authoritative state contract is never real-downstream eligible"
        )


def execute_state_transition(*_args: Any, **_kwargs: Any) -> None:
    raise RealStateTransitionForbidden(
        "T4 adapter produces requests only; M8 state transition events are forbidden"
    )
