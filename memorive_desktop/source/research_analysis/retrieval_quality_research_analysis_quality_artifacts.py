"""Memorive RETRIEVAL-QUALITY/RESEARCH_ANALYSIS · immutable AnalysisQualityAssessment builder."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Sequence

from artifact_registry import new_artifact_id

from retrieval.retrieval_quality_retrieval_coverage_artifacts import (
    ArtifactPointer,
    FrozenArtifactRecord,
    EvidenceArtifactValidationError,
    build_verification_artifact_record,
    legacy_analysis_projection,
    resolved_parent_link,
)

from .types import AnalysisResult


class AnalysisQualityValidationError(EvidenceArtifactValidationError):
    """RESEARCH_ANALYSIS attempted to cross a producer or source-ID boundary."""


def validate_analysis_source_ids(
    result: AnalysisResult,
    *,
    source_authority_artifacts: Sequence[FrozenArtifactRecord],
) -> None:
    """Validate claims against immutable RETRIEVAL artifacts, never a caller-made ID set."""

    if not source_authority_artifacts:
        raise AnalysisQualityValidationError("source authority artifacts must not be empty")
    valid: dict[str, str] = {}
    for artifact in source_authority_artifacts:
        if not isinstance(artifact, FrozenArtifactRecord) or artifact.artifact_type != "logical_span":
            raise AnalysisQualityValidationError("source authority must be a frozen RETRIEVAL logical_span")
        payload = artifact.payload
        if payload.get("producer_module") != "RETRIEVAL":
            raise AnalysisQualityValidationError("source authority producer must remain RETRIEVAL")
        paper_id = payload.get("paper_id")
        members = payload.get("member_chunk_ids")
        if not isinstance(paper_id, str) or not isinstance(members, list) or not members:
            raise AnalysisQualityValidationError("source authority logical_span is malformed")
        aliases = [payload.get("logical_span_id"), *members]
        for alias in aliases:
            if not isinstance(alias, str) or not alias:
                raise AnalysisQualityValidationError("source authority contains an invalid alias")
            previous = valid.setdefault(alias, paper_id)
            if previous != paper_id:
                raise AnalysisQualityValidationError("source authority aliases cross paper boundaries")
    for claim in result.literature_support:
        source = claim.source_id if isinstance(claim.source_id, dict) else {}
        chunk_id = source.get("chunk_id")
        paper_id = source.get("paper_id")
        if not isinstance(chunk_id, str) or chunk_id not in valid:
            raise AnalysisQualityValidationError(
                f"literature source_id is not in immutable RETRIEVAL source authority: {chunk_id!r}"
            )
        if paper_id != valid[chunk_id]:
            raise AnalysisQualityValidationError(
                f"literature source_id paper binding mismatch: {chunk_id!r}"
            )


def project_coverage_onto_analysis(
    result: AnalysisResult,
    coverage: FrozenArtifactRecord,
) -> AnalysisResult:
    """Return a new legacy Analysis projection; never mutates either input."""

    projection = legacy_analysis_projection(coverage)
    if (
        projection["coverage_status"] in {"partial", "insufficient"}
        and result.ai_general_knowledge
    ):
        raise AnalysisQualityValidationError(
            "partial/insufficient coverage must already suppress ai_general_knowledge"
        )
    return replace(result, **projection)


def build_analysis_quality_assessment(
    result: AnalysisResult,
    *,
    analysis_artifact_ref: ArtifactPointer,
    retrieval_coverage: FrozenArtifactRecord,
    method_policy_ref: ArtifactPointer,
    source_authority_artifacts: Sequence[FrozenArtifactRecord],
    producer_version: str,
    run_ref: str,
    route_snapshot_ref: str,
    computed_at: str,
    locator_path: Path | str,
    artifact_id: str | None = None,
) -> FrozenArtifactRecord:
    if retrieval_coverage.artifact_type != "retrieval_coverage_assessment":
        raise AnalysisQualityValidationError("RESEARCH_ANALYSIS requires RetrievalCoverageAssessment")
    if retrieval_coverage.payload.get("producer_module") != "RETRIEVAL":
        raise AnalysisQualityValidationError("retrieval coverage producer must remain RETRIEVAL")
    validate_analysis_source_ids(
        result,
        source_authority_artifacts=source_authority_artifacts,
    )
    metrics = result.quality_metrics
    if not isinstance(metrics, dict):
        raise AnalysisQualityValidationError("AnalysisResult lacks RESEARCH_ANALYSIS quality_metrics")
    required = {"direct_answer_ratio", "subquestion_coverage", "source_dominance"}
    if not required.issubset(metrics):
        raise AnalysisQualityValidationError(
            f"quality_metrics missing fields: {sorted(required - set(metrics))}"
        )
    retrieval_ref = retrieval_coverage.as_pointer()
    source_refs = [item.as_pointer() for item in source_authority_artifacts]
    inputs = [analysis_artifact_ref, retrieval_ref, method_policy_ref, *source_refs]
    payload = {
        "artifact_type": "analysis_quality_assessment",
        "artifact_id": artifact_id or new_artifact_id(),
        "producer_module": "RESEARCH_ANALYSIS",
        "retrieval_coverage_ref": retrieval_ref.to_dict(),
        "analysis_artifact_ref": analysis_artifact_ref.to_dict(),
        "direct_answer_ratio": metrics["direct_answer_ratio"],
        "subquestion_coverage": dict(metrics["subquestion_coverage"]),
        "source_dominance": dict(metrics["source_dominance"]),
        "producer_version": producer_version,
        "method_policy_ref": method_policy_ref.to_dict(),
        "input_artifact_refs": [item.to_dict() for item in inputs],
        "run_ref": run_ref,
        "route_snapshot_ref": route_snapshot_ref,
        "computed_at": computed_at,
        "field_provenance": {
            "direct_answer_ratio": "RESEARCH_ANALYSIS",
            "subquestion_coverage": "RESEARCH_ANALYSIS",
            "source_dominance": "RESEARCH_ANALYSIS",
        },
        "verification_scope": "governance_diagnostic",
        "non_authoritative": retrieval_coverage.payload.get("non_authoritative", False),
    }
    links = [
        resolved_parent_link(
            analysis_artifact_ref,
            relation="assesses",
            source_field="analysis_artifact_ref",
            evidence_ref=run_ref,
        ),
        resolved_parent_link(
            retrieval_ref,
            relation="reads_coverage",
            source_field="retrieval_coverage_ref",
            evidence_ref=run_ref,
        ),
        resolved_parent_link(
            method_policy_ref,
            relation="governed_by",
            source_field="method_policy_ref",
            evidence_ref=run_ref,
        ),
    ]
    links.extend(
        resolved_parent_link(
            source_ref,
            relation="authorizes_source_ids",
            source_field="input_artifact_refs",
            evidence_ref=run_ref,
        )
        for source_ref in source_refs
    )
    return build_verification_artifact_record(
        payload,
        locator_path=locator_path,
        parent_links=links,
        source_artifact_ids=[item.artifact_id for item in inputs],
        artifact_schema_ref="Memorive-RETRIEVAL-QUALITY-ANALYSIS-QUALITY-ASSESSMENT@r1",
        evidence_refs=[run_ref],
    )


__all__ = [
    "AnalysisQualityValidationError",
    "build_analysis_quality_assessment",
    "project_coverage_onto_analysis",
    "validate_analysis_source_ids",
]
