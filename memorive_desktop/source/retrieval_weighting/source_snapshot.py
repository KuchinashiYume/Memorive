"""Pure active-only runtime SourceSnapshot construction for FIELD-VECTORS."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping, Sequence

from .canonical import canonical_json_bytes, sha256_bytes, typed_payload_hash
from .contracts import (
    make_hashed_payload,
    require_non_empty_refs,
    require_non_empty_string,
    require_utc_timestamp,
    validate_card_authority,
    validate_doc_type_metadata,
)
from .errors import ContractViolation, FieldVectorError, SourceEligibilityError
from .field_registry import expand_card_fields, get_field_registry


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)


def _reason(exc: Exception) -> str:
    if isinstance(exc, FieldVectorError):
        context_reason = exc.context.get("reason")
        if isinstance(context_reason, str) and context_reason:
            return context_reason
    text = str(exc)
    known = (
        "REVIEW_STATUS_NOT_ACTIVE",
        "DOC_TYPE_MISSING",
        "DOC_TYPE_UNKNOWN",
        "DOC_TYPE_CONFLICT",
        "DATA_OWNERSHIP_UNKNOWN",
        "PAPER_ID_MISSING",
    )
    return next((item for item in known if item in text), getattr(exc, "code", "SOURCE_CONTRACT_REJECT"))


def _metadata_index(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for ordinal, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ContractViolation(f"doc_type_metadata[{ordinal}] must be an object")
        metadata_id = require_non_empty_string(
            row.get("metadata_id"), f"doc_type_metadata[{ordinal}].metadata_id"
        )
        if metadata_id in result:
            raise ContractViolation("duplicate doc_type metadata identity")
        result[metadata_id] = row
    return result


def _build_one(
    row: Mapping[str, Any],
    metadata_by_id: Mapping[str, Mapping[str, Any]],
    build_cutoff_at: str,
) -> list[dict[str, Any]]:
    expected_keys = {"fixture_id", "scenario", "authority", "doc_type_metadata_id", "card"}
    if set(row) != expected_keys:
        raise ContractViolation("source record keys must match the frozen Card-v6 fixture shape")
    fixture_id = require_non_empty_string(row["fixture_id"], "source_record.fixture_id")
    require_non_empty_string(row["scenario"], "source_record.scenario")
    card = row["card"]
    if not isinstance(card, Mapping):
        raise ContractViolation("source_record.card must be an object")
    authority = validate_card_authority(row["authority"], card)
    metadata_id = require_non_empty_string(
        row["doc_type_metadata_id"], "source_record.doc_type_metadata_id"
    )
    if metadata_id not in metadata_by_id:
        raise SourceEligibilityError("DOC_TYPE_MISSING", context={"reason": "DOC_TYPE_MISSING"})
    metadata = validate_doc_type_metadata(metadata_by_id[metadata_id], authority)
    if _parse_utc(metadata["observed_at"]) > _parse_utc(build_cutoff_at):
        raise SourceEligibilityError("doc_type observation is after build cutoff")

    if card.get("schema_version") != 6:
        raise SourceEligibilityError("only Card Schema v6 is eligible")
    if card.get("review_status") != "active":
        raise SourceEligibilityError(
            "REVIEW_STATUS_NOT_ACTIVE", context={"reason": "REVIEW_STATUS_NOT_ACTIVE"}
        )
    if card.get("completion_status") not in {"complete", "passed_with_omissions"}:
        raise SourceEligibilityError("active Card is not completion-closed")
    if card.get("is_derived") is not False:
        raise SourceEligibilityError("derived Card cannot enter the literature field index")
    ownership = card.get("data_ownership")
    if ownership not in {"self", "entrusted"}:
        raise SourceEligibilityError(
            "DATA_OWNERSHIP_UNKNOWN", context={"reason": "DATA_OWNERSHIP_UNKNOWN"}
        )
    source_anchor = card.get("source_anchor")
    if not isinstance(source_anchor, Mapping):
        raise SourceEligibilityError("PAPER_ID_MISSING", context={"reason": "PAPER_ID_MISSING"})
    paper_id = require_non_empty_string(source_anchor.get("paper_id"), "card.source_anchor.paper_id")

    artifact_hash_expected = typed_payload_hash(
        {
            "artifact_ref": authority["card_artifact_ref"],
            "card_id": authority["card_id"],
            "card_revision": authority["revision"],
            "card_content_hash": authority["content_hash"],
        }
    )
    if metadata["artifact_content_hash"] != artifact_hash_expected:
        raise SourceEligibilityError("artifact hash does not bind Card authority")

    registry = get_field_registry()
    registry_hash = registry["content_hash"]["value"]
    parent_refs = require_non_empty_refs(
        [authority["card_artifact_ref"]], "field_source.parent_refs"
    )
    provenance_refs = require_non_empty_refs(
        [metadata["metadata_id"], *metadata["source_refs"]],
        "field_source.provenance_refs",
    )
    fields = expand_card_fields(card, card_artifact_ref=authority["card_artifact_ref"])
    output: list[dict[str, Any]] = []
    for field in fields:
        identity = {
            "paper_id": paper_id,
            "card_id": authority["card_id"],
            "card_revision": authority["revision"],
            "card_content_hash": authority["content_hash"],
            "field_path": field["field_path"],
            "value_locator": field["value_locator"],
            "value_hash": field["value_hash"],
            "doc_type": metadata["canonical_value"],
            "data_ownership": ownership,
            "field_registry_hash": registry_hash,
        }
        field_source_id = "fsi1_" + sha256_bytes(canonical_json_bytes(identity)).lower()
        payload = {
            "object_type": "FieldSourceItem",
            "schema_version": "1.0",
            "field_source_id": field_source_id,
            "fixture_id": fixture_id,
            "paper_id": paper_id,
            "card_artifact_ref": authority["card_artifact_ref"],
            "card_id": authority["card_id"],
            "card_revision": authority["revision"],
            "card_content_hash": deepcopy(authority["content_hash"]),
            "field_path": field["field_path"],
            "value_locator": field["value_locator"],
            "canonical_value": field["canonical_value"],
            "value_hash": deepcopy(field["value_hash"]),
            "source_refs": deepcopy(field["source_refs"]),
            "source_coverage": field["source_coverage"],
            "provenance_refs": provenance_refs,
            "parent_refs": parent_refs,
            "doc_type": metadata["canonical_value"],
            "data_ownership": ownership,
            "review_status_snapshot": "active",
            "observed_at": metadata["observed_at"],
            "build_cutoff_at": build_cutoff_at,
            "field_registry_id": registry["registry_id"],
            "field_registry_version": registry["revision"],
            "field_registry_hash": registry_hash,
            "production_eligible": False,
        }
        output.append(make_hashed_payload(payload))
    return output


def build_source_snapshot(
    *,
    records: Sequence[Mapping[str, Any]],
    doc_type_metadata: Sequence[Mapping[str, Any]],
    build_cutoff_at: str,
    public_safe: bool,
) -> dict[str, Any]:
    """Build a hashed active-only runtime snapshot from explicit Card-v6 fixtures."""

    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ContractViolation("records must be a sequence")
    if not isinstance(doc_type_metadata, Sequence) or isinstance(doc_type_metadata, (str, bytes)):
        raise ContractViolation("doc_type_metadata must be a sequence")
    cutoff = require_utc_timestamp(build_cutoff_at, "build_cutoff_at")
    if public_safe is not True:
        raise ContractViolation("this candidate builder accepts only public_safe=true fixtures")
    metadata_by_id = _metadata_index(doc_type_metadata)
    accepted: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    seen_input_ids: set[str] = set()
    for ordinal, row in enumerate(records):
        if not isinstance(row, Mapping):
            rejections.append({"input_ordinal": ordinal, "reason": "SOURCE_RECORD_NOT_OBJECT"})
            continue
        fixture_id = row.get("fixture_id")
        if not isinstance(fixture_id, str) or not fixture_id:
            fixture_id = f"INPUT_ORDINAL_{ordinal}"
        if fixture_id in seen_input_ids:
            rejections.append({"fixture_id": fixture_id, "reason": "DUPLICATE_SOURCE_ID"})
            continue
        seen_input_ids.add(fixture_id)
        try:
            accepted.extend(_build_one(row, metadata_by_id, cutoff))
        except (FieldVectorError, TypeError, ValueError) as exc:
            rejections.append({"fixture_id": fixture_id, "reason": _reason(exc)})

    accepted.sort(
        key=lambda item: (
            item["paper_id"],
            item["card_id"],
            item["card_revision"],
            item["value_locator"],
        )
    )
    member_ids = [item["field_source_id"] for item in accepted]
    snapshot_identity = {
        "build_cutoff_at": cutoff,
        "public_safe": True,
        "member_ids": member_ids,
        "rejections": rejections,
    }
    source_snapshot_id = "ss1_" + sha256_bytes(canonical_json_bytes(snapshot_identity)).lower()
    observed_at = max(
        (item["observed_at"] for item in accepted),
        default=cutoff,
    )
    payload = {
        "object_type": "SourceSnapshot",
        "schema_version": "1.0",
        "source_snapshot_id": source_snapshot_id,
        "state": "snapshot_ready",
        "observed_at": observed_at,
        "build_cutoff_at": cutoff,
        "public_safe": True,
        "records": accepted,
        "rejections": rejections,
        "production_eligible": False,
    }
    return make_hashed_payload(payload)
