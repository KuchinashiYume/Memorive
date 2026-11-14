"""Memorive FACT-IDENTITY/DECISION_LOG adapter from EVIDENCE_REVIEW repair evidence to a valid DDL record."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .schema import validate_decision


_ARTIFACT_ID = re.compile(r"^art_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _report_reference_text(report_ref: Mapping[str, Any]) -> str:
    if not isinstance(report_ref, Mapping) or set(report_ref) != {
        "artifact_id",
        "content_hash",
    }:
        raise ValueError("report_ref must contain artifact_id and content_hash")
    artifact_id = report_ref.get("artifact_id")
    content_hash = report_ref.get("content_hash")
    if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(artifact_id):
        raise ValueError("report_ref.artifact_id is invalid")
    if not isinstance(content_hash, str) or not _SHA256.fullmatch(content_hash):
        raise ValueError("report_ref.content_hash is invalid")
    return f"repair_loop_evaluation_report:{artifact_id}@sha256:{content_hash}"


def build_ddl_decision(
    *, report_ref: Mapping[str, Any], decision_id: str
) -> dict[str, Any]:
    """Preserve the legacy Intake decision projection during module migration."""

    _report_reference_text(report_ref)
    if not isinstance(decision_id, str) or not decision_id.strip():
        raise ValueError("decision_id must be non-empty text")
    return {
        "decision_id": decision_id.strip(),
        "background": "Intake evaluated whether a second business repair loop should open.",
        "alternatives": ["keep_closed", "propose_separate_experiment"],
        "chosen_option": "keep_closed",
        "rejected_reasons": [
            "No frozen experimental evidence supports opening a second full review."
        ],
        "evidence_refs": [
            {
                "kind": "repair_loop_evaluation_report_ref",
                **dict(report_ref),
            }
        ],
    }


def build_intake_repair_loop_decision(
    *,
    report_ref: Mapping[str, Any],
    decision_id: str,
    created_at: str,
    review_date: str,
    external_feedback: Sequence[str] = (
        "No external feedback was available at decision time.",
    ),
    status: str = "Proposed",
) -> dict[str, Any]:
    """Build and validate the DECISION_LOG why-record for keeping a second loop closed."""

    record = {
        "schema_version": "1.0",
        "record_kind": "design_decision",
        "decision_id": decision_id,
        "decision_type": "process_governance",
        "title": "FACT-IDENTITY second business repair-loop decision",
        "background": (
            "Intake evaluated whether evidence supports opening a second business "
            "repair loop after the existing bounded review and repair contract."
        ),
        "alternatives": [
            {
                "alternative_id": "keep_closed",
                "summary": "Keep the second business repair loop disabled.",
            },
            {
                "alternative_id": "propose_separate_experiment",
                "summary": (
                    "Open a separately authorized experiment before changing the "
                    "production review contract."
                ),
            },
        ],
        "selected_alternative_id": "keep_closed",
        "final_decision": (
            "Keep the second business repair loop closed until a separately "
            "authorized frozen experiment supplies bounded benefit, cost, and "
            "termination evidence."
        ),
        "rejection_reasons": [
            {
                "alternative_id": "propose_separate_experiment",
                "reason": (
                    "The referenced evaluation contains no frozen experimental "
                    "evidence supporting an additional full review."
                ),
            }
        ],
        "affected_modules": ["M02", "M06", "DECISION_LOG"],
        "external_feedback": list(external_feedback),
        "status": status,
        "review_date": review_date,
        "created_at": created_at,
        "source_references": [_report_reference_text(report_ref)],
        "version": 1,
        "previous_version": None,
    }
    return validate_decision(record)


__all__ = ["build_ddl_decision", "build_intake_repair_loop_decision"]

