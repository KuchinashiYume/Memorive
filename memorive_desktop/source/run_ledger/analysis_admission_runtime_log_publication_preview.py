"""Memorive ANALYSIS-ADMISSION/RUNTIME_LOG non-authoritative publication candidate previews."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from artifact_registry.errors import ContractValidationError
from artifact_registry.analysis_admission_artifact_registry_materialization import ArtifactFactory
from artifact_registry.analysis_admission_artifact_registry_products import (
    ArtifactProduct,
    artifact_ref,
    stable_identifier,
    unique_text,
)
from knowledge_admission.analysis_admission_knowledge_admission_analysis_admission import require_buildable

from .errors import RealPublicationForbidden


MANIFEST_SCHEMA_VERSION = "analysis_admission-publication-candidate-manifest-v1"
DRY_RUN_SCHEMA_VERSION = "analysis_admission-publication-dry-run-report-v1"
RECEIPT_PREVIEW_SCHEMA_VERSION = "analysis_admission-publication-receipt-preview-v1"
COVERAGE_STATUSES = {"not_assessed", "legacy_partial", "available"}
PAPER_OUTCOME_STATUSES = {"passed", "failed", "incomplete", "unknown"}


@dataclass(frozen=True)
class PublicationPreviewBundle:
    report: ArtifactProduct
    receipt_preview: ArtifactProduct


def build_publication_candidate_manifest(
    *,
    factory: ArtifactFactory,
    admission: ArtifactProduct,
    assessment: ArtifactProduct,
    analysis: Mapping[str, Any],
    candidate_card: Mapping[str, Any],
    context_pack: Mapping[str, Any],
    coverage: Mapping[str, Any],
    paper_outcome: Mapping[str, Any],
    created_at: str,
) -> ArtifactProduct:
    require_buildable(admission)
    coverage_status = coverage.get("status")
    if coverage_status not in COVERAGE_STATUSES:
        raise ContractValidationError(f"invalid coverage status: {coverage_status!r}")
    outcome_status = paper_outcome.get("status")
    if outcome_status not in PAPER_OUTCOME_STATUSES:
        raise ContractValidationError(
            f"invalid paper outcome status: {outcome_status!r}"
        )
    structural_status = (
        "passed"
        if assessment.payload["overall_compatibility"] == "compatible"
        and assessment.payload["freshness"]["status"] == "fresh"
        and admission.payload["request_build_status"] == "buildable"
        else "blocked"
    )
    blockers = ["USER_SELECTION_PENDING_CONFIRMATION"]
    if structural_status != "passed":
        blockers.append("STRUCTURAL_COMPATIBILITY_NOT_PASSED")
    if outcome_status != "passed":
        blockers.append("PAPER_OUTCOME_NOT_PASSED")
    manifest_id = stable_identifier(
        "publication_candidate_",
        [
            admission.envelope["artifact_id"],
            assessment.envelope["artifact_id"],
            analysis["artifact_id"],
            created_at,
        ],
    )
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "manifest_id": manifest_id,
        "created_at": created_at,
        "dry_run": True,
        "non_authoritative": True,
        "candidate_refs": {
            "analysis": artifact_ref(analysis),
            "candidate_card": artifact_ref(candidate_card),
            "context_pack": artifact_ref(context_pack),
            "admission_request": artifact_ref(admission.envelope),
            "compatibility_assessment": artifact_ref(assessment.envelope),
        },
        "dimensions": {
            "structural_compatibility": {
                "status": structural_status,
                "assessment_ref": artifact_ref(assessment.envelope),
                "admission_ref": artifact_ref(admission.envelope),
            },
            "coverage": {
                "status": coverage_status,
                "evidence_refs": unique_text(coverage.get("evidence_refs", [])),
            },
            "paper_outcome": {
                "status": outcome_status,
                "evidence_refs": unique_text(paper_outcome.get("evidence_refs", [])),
            },
            "user_selection": {
                "status": "pending_confirmation",
                "selection_ref": None,
            },
        },
        "publication_blockers": unique_text(blockers),
        "eligible_for_real_publication": False,
        "canonical_pointer_update_allowed": False,
        "publication_ledger_write_allowed": False,
    }
    parents = [
        analysis,
        candidate_card,
        context_pack,
        admission.envelope,
        assessment.envelope,
    ]
    envelope = factory.create(
        artifact_type="publication_candidate_manifest",
        payload=payload,
        schema_ref="memorive://analysis_admission/publication-candidate-manifest/v1",
        paper_ids=analysis.get("source_scope", {}).get("paper_ids", []),
        source_artifact_ids=[item["artifact_id"] for item in parents],
        parents=[
            factory.link(
                item,
                relation="publication_candidate_input",
                source_field="candidate_refs",
                evidence_ref="publication-candidate:" + manifest_id,
            )
            for item in parents
        ],
        metadata={
            "dry_run": True,
            "user_selection": "pending_confirmation",
            "eligible_for_real_publication": False,
        },
        evidence_refs=[
            "artifact:" + admission.envelope["artifact_id"],
            "artifact:" + assessment.envelope["artifact_id"],
        ],
        semantic_key=manifest_id,
    )
    return ArtifactProduct(payload=payload, envelope=envelope)


def build_publication_dry_run(
    *,
    factory: ArtifactFactory,
    manifest: ArtifactProduct,
    evaluated_at: str,
    mode: str = "dry_run",
) -> PublicationPreviewBundle:
    if mode != "dry_run":
        raise RealPublicationForbidden(
            "Configuration supports mode='dry_run' only; no real publication writer exists"
        )
    report_id = stable_identifier(
        "publication_dry_run_",
        [manifest.envelope["artifact_id"], evaluated_at],
    )
    blockers = list(manifest.payload["publication_blockers"])
    report_payload = {
        "schema_version": DRY_RUN_SCHEMA_VERSION,
        "report_id": report_id,
        "evaluated_at": evaluated_at,
        "simulation": True,
        "non_authoritative": True,
        "manifest_ref": artifact_ref(manifest.envelope),
        "simulation_result": "blocked" if blockers else "would_be_eligible",
        "blockers": blockers,
        "writes_performed": {
            "publication": False,
            "canonical_pointer": False,
            "latest_pointer": False,
            "publication_ledger": False,
        },
    }
    report_envelope = factory.create(
        artifact_type="publication_dry_run_report",
        payload=report_payload,
        schema_ref="memorive://analysis_admission/publication-dry-run-report/v1",
        paper_ids=manifest.envelope.get("source_scope", {}).get("paper_ids", []),
        source_artifact_ids=[manifest.envelope["artifact_id"]],
        parents=[
            factory.link(
                manifest.envelope,
                relation="simulates_publication_of",
                source_field="manifest_ref",
                evidence_ref="publication-dry-run:" + report_id,
            )
        ],
        metadata={"simulation": True, "non_authoritative": True},
        evidence_refs=["artifact:" + manifest.envelope["artifact_id"]],
        semantic_key=report_id,
    )
    report = ArtifactProduct(payload=report_payload, envelope=report_envelope)
    receipt_id = stable_identifier(
        "publication_receipt_preview_",
        [report_envelope["artifact_id"], manifest.envelope["artifact_id"]],
    )
    receipt_payload = {
        "schema_version": RECEIPT_PREVIEW_SCHEMA_VERSION,
        "receipt_preview_id": receipt_id,
        "receipt_kind": "preview",
        "simulation": True,
        "non_authoritative": True,
        "manifest_ref": artifact_ref(manifest.envelope),
        "dry_run_report_ref": artifact_ref(report_envelope),
        "publication_status": "not_published",
        "publication_ledger_ref": None,
        "canonical_pointer_ref": None,
        "authoritative_receipt_created": False,
    }
    receipt_envelope = factory.create(
        artifact_type="publication_receipt_preview",
        payload=receipt_payload,
        schema_ref="memorive://analysis_admission/publication-receipt-preview/v1",
        paper_ids=manifest.envelope.get("source_scope", {}).get("paper_ids", []),
        source_artifact_ids=[
            manifest.envelope["artifact_id"],
            report_envelope["artifact_id"],
        ],
        parents=[
            factory.link(
                manifest.envelope,
                relation="previews_receipt_for",
                source_field="manifest_ref",
                evidence_ref="receipt-preview:" + receipt_id,
            ),
            factory.link(
                report_envelope,
                relation="derived_from_dry_run",
                source_field="dry_run_report_ref",
                evidence_ref="receipt-preview:" + receipt_id,
            ),
        ],
        metadata={
            "receipt_kind": "preview",
            "simulation": True,
            "non_authoritative": True,
        },
        evidence_refs=["artifact:" + report_envelope["artifact_id"]],
        semantic_key=receipt_id,
    )
    return PublicationPreviewBundle(
        report=report,
        receipt_preview=ArtifactProduct(
            payload=receipt_payload,
            envelope=receipt_envelope,
        ),
    )


def execute_publication(*_args: Any, **_kwargs: Any) -> None:
    raise RealPublicationForbidden("real publication is outside ANALYSIS-ADMISSION authority")


def update_canonical_or_latest_pointer(*_args: Any, **_kwargs: Any) -> None:
    raise RealPublicationForbidden(
        "canonical/latest pointer updates are outside ANALYSIS-ADMISSION authority"
    )


def append_publication_ledger(*_args: Any, **_kwargs: Any) -> None:
    raise RealPublicationForbidden(
        "formal Publication Ledger writes are outside ANALYSIS-ADMISSION authority"
    )
