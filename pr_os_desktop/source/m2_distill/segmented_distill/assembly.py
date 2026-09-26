"""Field composition, assembly barrier, and final Card candidate builder."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping, Sequence

from .contracts import (
    ArtifactKind,
    SegmentedDistillContractError,
    canonical_hash,
    dispatch_artifact_validation,
    validate_field_candidate,
)


def build_field_candidates(merge_ledger: Mapping[str, Any]) -> list[dict[str, Any]]:
    by_id = {item["fact_record_id"]: item for item in merge_ledger["fact_ledger"]}
    output: list[dict[str, Any]] = []
    for slot, assessment in sorted(merge_ledger["slot_assessments"].items()):
        if assessment["state"] != "present":
            continue
        facts = [by_id[item] for item in assessment["fact_record_ids"]]
        values = {(item["normalized_value_candidate"], item.get("unit")) for item in facts}
        if len(values) != 1:
            raise SegmentedDistillContractError(
                "FIELD_CANDIDATE_VALUE_NOT_UNIQUE", slot
            )
        value, unit = next(iter(values))
        source_refs = []
        seen: set[str] = set()
        for fact in facts:
            for occurrence in fact["occurrences"]:
                clean = {key: value for key, value in occurrence.items() if key != "core_owned"}
                identity = canonical_hash(clean)
                if identity not in seen:
                    source_refs.append(clean)
                    seen.add(identity)
        body = {
            "schema_version": "p06-t06-card-field-candidate-v1",
            "artifact_kind": ArtifactKind.CARD_FIELD_CANDIDATE.value,
            "paper_id": merge_ledger["paper_id"],
            "field_path": slot,
            "value": {"text": value, "unit": unit},
            "fact_record_ids": sorted(assessment["fact_record_ids"]),
            "source_refs": sorted(
                source_refs,
                key=lambda item: (item["chunk_id"], item["start_offset"], item["end_offset"]),
            ),
            "composition_mode": "DETERMINISTIC",
        }
        candidate = {**body, "content_hash": canonical_hash(body)}
        output.append(validate_field_candidate(candidate))
    return output


def build_assembly_manifest(
    *,
    completion_ledger: Mapping[str, Any],
    merge_ledger: Mapping[str, Any],
    field_candidates: Sequence[Mapping[str, Any]],
    required_core_fields: Sequence[str],
    card_schema_sha256: str,
    prompt_revision: str,
    profile_revision: str,
    target_exists: bool = False,
) -> dict[str, Any]:
    reasons: list[str] = []
    if completion_ledger["terminal_segment_ids"] != completion_ledger["expected_segment_ids"]:
        reasons.append("TERMINAL_SEGMENT_EXACT_SET_INCOMPLETE")
    if completion_ledger["missing_segment_ids"]:
        reasons.append("MISSING_SEGMENTS")
    if completion_ledger["recovery_state"] != "completed":
        reasons.append("RECOVERY_NOT_COMPLETED")
    if merge_ledger["segment_completion_ledger_hash"] != completion_ledger["content_hash"]:
        reasons.append("MERGE_LEDGER_STALE")
    if merge_ledger["axes"]["conflict"] != "none":
        reasons.append("UNRESOLVED_CONFLICT")
    if merge_ledger["axes"]["capacity"] != "within_budget":
        reasons.append("CAPACITY_NOT_CLOSED")
    fields = {item["field_path"]: item for item in field_candidates}
    if len(fields) != len(field_candidates):
        reasons.append("DUPLICATE_FIELD_CANDIDATE")
    for field in required_core_fields:
        assessment = merge_ledger["slot_assessments"].get(field)
        if not assessment or assessment["state"] != "present" or field not in fields:
            reasons.append(f"REQUIRED_CORE_FIELD_NOT_CLOSED:{field}")
    if any(item["paper_id"] != completion_ledger["paper_id"] for item in field_candidates):
        reasons.append("FOREIGN_FIELD_CANDIDATE")
    if target_exists:
        reasons.append("TARGET_CARD_ALREADY_EXISTS")
    body = {
        "schema_version": "p06-t06-card-assembly-manifest-v1",
        "paper_id": completion_ledger["paper_id"],
        "source_manifest_hash": completion_ledger["source_manifest_hash"],
        "topology_hash": completion_ledger["topology_hash"],
        "segment_completion_ledger_hash": completion_ledger["content_hash"],
        "fact_merge_ledger_hash": merge_ledger["content_hash"],
        "field_candidate_hashes": {
            item["field_path"]: item["content_hash"] for item in field_candidates
        },
        "required_core_fields": list(required_core_fields),
        "card_schema_sha256": card_schema_sha256,
        "prompt_revision": prompt_revision,
        "profile_revision": profile_revision,
        "axes": deepcopy(merge_ledger["axes"]),
        "barrier_result": "PASS" if not reasons else "BLOCK",
        "blocking_reasons": reasons,
        "card_candidate_visible": not reasons,
    }
    return {**body, "content_hash": canonical_hash(body)}


def dry_assemble(**kwargs: Any) -> dict[str, Any]:
    manifest = build_assembly_manifest(**kwargs)
    return {
        "result": manifest["barrier_result"],
        "blocking_reasons": manifest["blocking_reasons"],
        "assembly_manifest_hash": manifest["content_hash"],
        "card_bytes_created": False,
    }


CardFactory = Callable[[str, Mapping[str, Mapping[str, Any]], Mapping[str, Any]], Mapping[str, Any]]


def _default_card_factory(
    paper_id: str,
    fields: Mapping[str, Mapping[str, Any]],
    lineage: Mapping[str, Any],
) -> Mapping[str, Any]:
    return {
        "schema_version": "p06-t06-card-candidate-v1",
        "artifact_kind": ArtifactKind.CARD_CANDIDATE.value,
        "paper_id": paper_id,
        "review_status": "pending",
        "fields": dict(fields),
        "lineage": dict(lineage),
    }


def assemble_card_candidate(
    *,
    assembly_manifest: Mapping[str, Any],
    field_candidates: Sequence[Mapping[str, Any]],
    full_card_validator: Callable[[Mapping[str, Any]], Any],
    card_factory: CardFactory = _default_card_factory,
) -> dict[str, Any]:
    if assembly_manifest["barrier_result"] != "PASS" or assembly_manifest["blocking_reasons"]:
        raise SegmentedDistillContractError(
            "CARD_ASSEMBLY_BARRIER_NOT_PASS",
            repr(assembly_manifest["blocking_reasons"]),
        )
    expected_hashes = assembly_manifest["field_candidate_hashes"]
    observed_hashes = {item["field_path"]: item["content_hash"] for item in field_candidates}
    if expected_hashes != observed_hashes:
        raise SegmentedDistillContractError(
            "FIELD_CANDIDATE_EXACT_SET_MISMATCH", "assembly input changed"
        )
    fields = {
        item["field_path"]: {
            "value": deepcopy(item["value"]),
            "source_refs": deepcopy(item["source_refs"]),
            "fact_record_ids": list(item["fact_record_ids"]),
        }
        for item in sorted(field_candidates, key=lambda value: value["field_path"])
    }
    lineage = {
        "source_manifest_hash": assembly_manifest["source_manifest_hash"],
        "topology_hash": assembly_manifest["topology_hash"],
        "segment_completion_ledger_hash": assembly_manifest["segment_completion_ledger_hash"],
        "fact_merge_ledger_hash": assembly_manifest["fact_merge_ledger_hash"],
        "assembly_manifest_hash": assembly_manifest["content_hash"],
        "original_source_identity_only": True,
    }
    body = dict(card_factory(assembly_manifest["paper_id"], fields, lineage))
    body["artifact_kind"] = ArtifactKind.CARD_CANDIDATE.value
    candidate = {**body, "content_hash": canonical_hash(body)}
    dispatch_artifact_validation(
        expected_artifact_kind=ArtifactKind.CARD_CANDIDATE,
        payload=candidate,
        full_card_validator=full_card_validator,
    )
    return candidate


def build_analysis_lineage(
    *,
    card_candidate: Mapping[str, Any],
    context_pack_hash: str,
    producing_run_id: str,
    prior_analysis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    card_hash = card_candidate["content_hash"]
    prior_card_hash = (prior_analysis or {}).get("card_candidate_hash")
    return {
        "card_candidate_hash": card_hash,
        "context_pack_hash": context_pack_hash,
        "producing_run_id": producing_run_id,
        "source_identity_mode": "ORIGINAL_CHUNK_IDS",
        "prior_analysis_state": (
            "stale_needs_revalidation"
            if prior_card_hash and prior_card_hash != card_hash
            else "not_applicable"
            if prior_analysis is None
            else "current"
        ),
        "prior_analysis_mutated": False,
    }


__all__ = [
    "assemble_card_candidate",
    "build_analysis_lineage",
    "build_assembly_manifest",
    "build_field_candidates",
    "dry_assemble",
]
