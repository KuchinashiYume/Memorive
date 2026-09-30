"""COST-ROUTING public-safe historical evidence recovery and classification.

The module is deliberately pure: it does not read provider services, mutate an
RUNTIME_LOG ledger, or infer missing identity fields.  Callers must supply source
records and receive deterministic append-ready projections.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from retrieval_weighting.contracts import make_hashed_payload


EVIDENCE_CLASSES = {
    "QUALIFICATION",
    "NONBLIND_REFERENCE",
    "SELECTION",
    "REGRESSION",
    "DIAGNOSTIC",
    "UNSCORED_NOT_ASSESSED",
}
EVIDENCE_ORIGIN_CLASS = {
    "formal_blind_qualification": "QUALIFICATION",
    "nonblind_reference": "NONBLIND_REFERENCE",
    "selection_study": "SELECTION",
    "same_form_regression": "REGRESSION",
    "diagnostic_observation": "DIAGNOSTIC",
    "unscored": "UNSCORED_NOT_ASSESSED",
}
MANDATORY_QUALIFICATION_BINDINGS = (
    "qualification_contract_ref",
    "qset_hash",
    "pack_hash",
    "scorer_contract_ref",
    "profile_hash",
    "route_snapshot_id",
    "provider_region",
    "egress_identity",
    "evaluation_window",
    "sample_basis",
)
_RECORD_KEYS = {
    "historical_record_id",
    "role_family",
    "candidate_id",
    "source_evidence_refs",
    "evidence_origin",
    "evidence_status",
    "quality_metric",
    "quality_score",
    "bindings",
}


class HistoricalRecoveryError(ValueError):
    """A supplied historical row cannot be recovered without guessing."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise HistoricalRecoveryError(f"{field} must be non-empty exact text")
    return value


def _refs(value: Any) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise HistoricalRecoveryError("source_evidence_refs must be an array")
    refs = [_text(item, "source_evidence_refs[]") for item in value]
    if not refs or refs != sorted(set(refs)):
        raise HistoricalRecoveryError("source_evidence_refs must be sorted and unique")
    return refs


def _binding_state(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {"status": "UNKNOWN", "value": None}
    if isinstance(value, str):
        return {"status": "EXACT", "value": _text(value, f"bindings.{field}")}
    if isinstance(value, Mapping) and set(value) == {"status", "value"}:
        status = value["status"]
        if status not in {"EXACT", "PARTIAL", "UNKNOWN"}:
            raise HistoricalRecoveryError(f"bindings.{field}.status is unsupported")
        observed = value["value"]
        if status == "UNKNOWN":
            if observed is not None:
                raise HistoricalRecoveryError(f"bindings.{field} UNKNOWN must have null value")
            return {"status": status, "value": None}
        return {"status": status, "value": _text(observed, f"bindings.{field}.value")}
    raise HistoricalRecoveryError(f"bindings.{field} must be exact text, state object, or null")


def validate_source_record(record: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, Mapping) or set(record) != _RECORD_KEYS:
        raise HistoricalRecoveryError("historical source record exact keys mismatch")
    result = deepcopy(dict(record))
    for field in ("historical_record_id", "role_family", "candidate_id", "evidence_origin", "evidence_status", "quality_metric"):
        _text(result[field], field)
    _refs(result["source_evidence_refs"])
    if result["evidence_origin"] not in EVIDENCE_ORIGIN_CLASS:
        raise HistoricalRecoveryError("evidence_origin is unsupported")
    if result["evidence_status"] not in {"PASS", "FAIL", "ERROR", "NOT_ASSESSED"}:
        raise HistoricalRecoveryError("evidence_status is unsupported")
    score = result["quality_score"]
    if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float))):
        raise HistoricalRecoveryError("quality_score must be numeric or null")
    if not isinstance(result["bindings"], Mapping):
        raise HistoricalRecoveryError("bindings must be an object")
    unknown = set(result["bindings"]) - set(MANDATORY_QUALIFICATION_BINDINGS)
    if unknown:
        raise HistoricalRecoveryError(f"unknown binding fields: {sorted(unknown)}")
    return result


def recover_historical_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Recover only exact/partial/unknown states explicitly supported by evidence."""

    source = validate_source_record(record)
    recovered = {
        field: _binding_state(source["bindings"].get(field), field)
        for field in MANDATORY_QUALIFICATION_BINDINGS
    }
    unknown_fields = sorted(
        field for field, state in recovered.items() if state["status"] == "UNKNOWN"
    )
    partial_fields = sorted(
        field for field, state in recovered.items() if state["status"] == "PARTIAL"
    )
    return make_hashed_payload(
        {
            "schema_version": "COST_ROUTING_HISTORICAL_RECOVERY_ROW_V1",
            "historical_record_id": source["historical_record_id"],
            "role_family": source["role_family"],
            "candidate_id": source["candidate_id"],
            "source_evidence_refs": source["source_evidence_refs"],
            "recovered_fields": recovered,
            "unknown_fields": unknown_fields,
            "partial_fields": partial_fields,
            "inference_performed": False,
            "rerun_performed": False,
        }
    )


def classify_evaluation_evidence(record: Mapping[str, Any]) -> dict[str, Any]:
    source = validate_source_record(record)
    evidence_class = EVIDENCE_ORIGIN_CLASS[source["evidence_origin"]]
    if source["quality_score"] is None or source["evidence_status"] == "NOT_ASSESSED":
        evidence_class = "UNSCORED_NOT_ASSESSED"
    return make_hashed_payload(
        {
            "schema_version": "COST_ROUTING_EVIDENCE_CLASSIFICATION_ROW_V1",
            "historical_record_id": source["historical_record_id"],
            "role_family": source["role_family"],
            "candidate_id": source["candidate_id"],
            "evidence_class": evidence_class,
            "evidence_status": source["evidence_status"],
            "source_evidence_refs": source["source_evidence_refs"],
            "classification_basis": source["evidence_origin"],
            "classification_overridden_by_score_absence": source["quality_score"] is None,
        }
    )


def project_qualification(record: Mapping[str, Any]) -> dict[str, Any]:
    source = validate_source_record(record)
    recovery = recover_historical_record(source)
    classification = classify_evaluation_evidence(source)
    reasons: list[str] = []
    if classification["evidence_class"] != "QUALIFICATION":
        reasons.append("EVIDENCE_CLASS_NOT_QUALIFICATION")
    if source["evidence_status"] != "PASS":
        reasons.append("EVIDENCE_STATUS_NOT_PASS")
    if recovery["unknown_fields"]:
        reasons.append("MANDATORY_BINDING_UNKNOWN")
    if recovery["partial_fields"]:
        reasons.append("MANDATORY_BINDING_PARTIAL")
    eligible = not reasons
    bindings = {
        field: recovery["recovered_fields"][field]["value"]
        for field in MANDATORY_QUALIFICATION_BINDINGS
    }
    return make_hashed_payload(
        {
            "schema_version": "COST_ROUTING_QUALIFICATION_PROJECTION_V1",
            "historical_record_id": source["historical_record_id"],
            "role_family": source["role_family"],
            "candidate_id": source["candidate_id"],
            "evidence_class": classification["evidence_class"],
            "evidence_status": source["evidence_status"],
            "quality_metric": source["quality_metric"],
            "quality_score": source["quality_score"],
            "bindings": bindings,
            "qualification_eligibility": eligible,
            "reason_codes": sorted(reasons),
            "source_evidence_refs": source["source_evidence_refs"],
        }
    )


def build_historical_projections(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise HistoricalRecoveryError("records must be an array")
    source_ids = [validate_source_record(row)["historical_record_id"] for row in records]
    if source_ids != sorted(set(source_ids)):
        raise HistoricalRecoveryError("historical_record_id values must be sorted and unique")
    recoveries = [recover_historical_record(row) for row in records]
    classifications = [classify_evaluation_evidence(row) for row in records]
    qualifications = [project_qualification(row) for row in records]
    return {
        "recovery_register": recoveries,
        "classification_ledger": classifications,
        "qualification_projection_set": qualifications,
        "denominator": len(records),
        "qualification_eligible_count": sum(
            1 for row in qualifications if row["qualification_eligibility"]
        ),
    }


__all__ = [
    "EVIDENCE_CLASSES",
    "EVIDENCE_ORIGIN_CLASS",
    "HistoricalRecoveryError",
    "MANDATORY_QUALIFICATION_BINDINGS",
    "build_historical_projections",
    "classify_evaluation_evidence",
    "project_qualification",
    "recover_historical_record",
    "validate_source_record",
]
