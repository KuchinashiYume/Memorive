"""Pure normative FieldVectorRecord construction and verification for ServiceContracts."""

from __future__ import annotations

import hashlib
import math
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .canonical import canonical_json_bytes, sha256_bytes
from .contracts import (
    make_hashed_payload,
    make_value_hash,
    make_vector_id,
    require_non_empty_refs,
    require_non_empty_string,
    require_storage_token,
    require_utc_timestamp,
    require_vector_id,
    validate_embedding_profile,
    validate_hash_descriptor,
    validate_ownership_partition,
    verify_hashed_payload,
)
from .errors import ContractViolation, EmbeddingProfileError, IdentityConflict
from .field_registry import get_field_registry


OfflineEmbedder = Callable[[str, Mapping[str, Any]], Sequence[float]]


def deterministic_fixture_embedding(
    text: str,
    *,
    dimension: int,
    normalization: str,
) -> list[float]:
    """Map text to a fixed finite vector without model, provider or network calls."""

    if not isinstance(text, str) or not text:
        raise EmbeddingProfileError("fixture embedding text must be non-empty")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or not 1 <= dimension <= 65536:
        raise EmbeddingProfileError("fixture dimension must be an integer in [1, 65536]")
    if normalization not in {"none", "l2"}:
        raise EmbeddingProfileError("fixture normalization must be none or l2")
    # The explicit fixture model version prevents mixing vectors from older seeds.
    seed = b"Memorive:Research:OFFLINE_FIXTURE_V2\x00" + text.encode("utf-8")
    values: list[float] = []
    counter = 0
    while len(values) < dimension:
        digest = hashlib.sha256(seed + counter.to_bytes(8, "big")).digest()
        for offset in range(0, len(digest), 4):
            integer = int.from_bytes(digest[offset : offset + 4], "big")
            values.append((integer / 4_294_967_295.0) * 2.0 - 1.0)
            if len(values) == dimension:
                break
        counter += 1
    if normalization == "l2":
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0.0 or not math.isfinite(norm):
            raise EmbeddingProfileError("fixture embedding produced an invalid norm")
        values = [value / norm for value in values]
    return values


def _validated_vector(values: Sequence[float], dimension: int, normalization: str) -> list[float]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise EmbeddingProfileError("embedding must be a numeric sequence")
    if len(values) != dimension:
        raise EmbeddingProfileError("embedding dimension mismatch")
    result: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EmbeddingProfileError("embedding contains a non-number")
        number = float(value)
        if not math.isfinite(number):
            raise EmbeddingProfileError("embedding contains NaN or infinity")
        result.append(number)
    if normalization == "l2":
        norm = math.sqrt(sum(value * value for value in result))
        if not math.isclose(norm, 1.0, rel_tol=1e-12, abs_tol=1e-12):
            raise EmbeddingProfileError("l2 profile requires a unit-length embedding")
    return result


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)


def field_vector_identity_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact JCS payload used to derive a normative vector ID."""

    keys = (
        "identity_version",
        "paper_id",
        "card_artifact_ref",
        "card_id",
        "card_revision",
        "card_content_hash",
        "field_source_id",
        "field_path",
        "value_locator",
        "value_hash",
        "source_refs",
        "source_coverage",
        "provenance_refs",
        "parent_refs",
        "doc_type",
        "data_ownership",
        "review_status_snapshot",
        "embedding_profile_id",
        "embedding_model_id",
        "embedding_dimension",
        "embedding_normalization",
        "index_id",
        "index_version",
        "collection_id",
    )
    missing = [key for key in keys if key not in record]
    if missing:
        raise ContractViolation(
            "record lacks vector identity fields", context={"missing_keys": missing}
        )
    return {key: deepcopy(record[key]) for key in keys}


def _expected_storage_key(record: Mapping[str, Any]) -> str:
    return (
        "field-vector/v1/"
        + require_storage_token(record["index_id"], "record.index_id")
        + "/"
        + require_storage_token(record["collection_id"], "record.collection_id")
        + "/"
        + require_vector_id(record["vector_id"])
    )


def verify_field_vector_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute record hash, value hash, vector ID, storage key and invariants."""

    checked = verify_hashed_payload(record, "field_vector_record")
    normative_required = {
        "schema_version",
        "vector_id",
        "vector_type",
        "identity_version",
        "card_id",
        "card_revision",
        "card_content_hash",
        "field_path",
        "value_locator",
        "value_hash",
        "source_refs",
        "provenance_refs",
        "parent_refs",
        "doc_type",
        "data_ownership",
        "review_status_snapshot",
        "embedding_profile_id",
        "embedding_model_id",
        "embedding_dimension",
        "embedding_normalization",
        "embedding",
        "index_id",
        "index_version",
        "collection_id",
        "storage_key",
        "observed_at",
        "build_cutoff_at",
        "content_hash",
        "hash_kind",
        "supersedes",
        "invalidation_state",
        "invalidated_by",
    }
    extras_required = {
        "paper_id",
        "card_artifact_ref",
        "field_source_id",
        "canonical_value",
        "source_coverage",
        "source_snapshot_id",
        "source_snapshot_hash",
        "production_eligible",
    }
    missing = sorted((normative_required | extras_required) - set(checked))
    if missing:
        raise ContractViolation("field vector record is incomplete", context={"missing_keys": missing})
    if checked["schema_version"] != "1.0" or checked["vector_type"] != "card_field":
        raise ContractViolation("record schema/vector type mismatch")
    if checked["identity_version"] != 1:
        raise ContractViolation("record.identity_version must be 1")
    require_non_empty_string(checked["paper_id"], "record.paper_id")
    require_non_empty_string(checked["card_artifact_ref"], "record.card_artifact_ref")
    require_non_empty_string(checked["card_id"], "record.card_id")
    revision = checked["card_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ContractViolation("record.card_revision must be a positive integer")
    validate_hash_descriptor(checked["card_content_hash"], "record.card_content_hash")
    require_non_empty_string(checked["field_path"], "record.field_path")
    require_non_empty_string(checked["value_locator"], "record.value_locator")
    if checked["value_locator"] != "/" + checked["field_path"]:
        raise ContractViolation("value_locator must be the exact Card JSON locator")
    expected_value_hash = make_value_hash(checked["canonical_value"])
    if checked["value_hash"] != expected_value_hash:
        raise IdentityConflict("record.value_hash does not bind canonical_value")
    require_non_empty_refs(checked["source_refs"], "record.source_refs")
    require_non_empty_refs(checked["provenance_refs"], "record.provenance_refs")
    require_non_empty_refs(checked["parent_refs"], "record.parent_refs")
    if checked["source_coverage"] not in {
        "field_anchor",
        "item_anchor",
        "artifact_level_no_dedicated_anchor",
    }:
        raise ContractViolation("record.source_coverage is invalid")
    if checked["doc_type"] not in {"literature", "note"}:
        raise ContractViolation("record.doc_type is invalid")
    if checked["review_status_snapshot"] != "active":
        raise ContractViolation("record.review_status_snapshot must be active")
    require_non_empty_string(checked["source_snapshot_id"], "record.source_snapshot_id")
    validate_hash_descriptor(checked["source_snapshot_hash"], "record.source_snapshot_hash")

    profile = {
        "embedding_profile_id": checked["embedding_profile_id"],
        "embedding_model_id": checked["embedding_model_id"],
        "embedding_dimension": checked["embedding_dimension"],
        "embedding_normalization": checked["embedding_normalization"],
        "index_version": checked["index_version"],
    }
    validate_embedding_profile(profile)
    version = checked["index_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractViolation("record.index_version must be a positive integer")
    validate_ownership_partition(
        checked["data_ownership"], profile, checked["index_id"], checked["collection_id"]
    )
    validated_embedding = _validated_vector(
        checked["embedding"], checked["embedding_dimension"], checked["embedding_normalization"]
    )
    embedding_seed = canonical_json_bytes(
        {
            "canonical_value": checked["canonical_value"],
            "embedding_profile_id": checked["embedding_profile_id"],
            "embedding_model_id": checked["embedding_model_id"],
        }
    ).decode("utf-8")
    expected_embedding = deterministic_fixture_embedding(
        embedding_seed,
        dimension=checked["embedding_dimension"],
        normalization=checked["embedding_normalization"],
    )
    if validated_embedding != expected_embedding:
        raise IdentityConflict("record.embedding differs from the frozen fixture embedding")
    require_utc_timestamp(checked["observed_at"], "record.observed_at")
    require_utc_timestamp(checked["build_cutoff_at"], "record.build_cutoff_at")
    if _parse_utc(checked["observed_at"]) > _parse_utc(checked["build_cutoff_at"]):
        raise ContractViolation("record observation is after build cutoff")
    if checked["hash_kind"] != "rfc8785_jcs_sha256":
        raise ContractViolation("record.hash_kind must be rfc8785_jcs_sha256")
    if checked["storage_key"] != _expected_storage_key(checked):
        raise IdentityConflict("record.storage_key does not bind index/collection/vector")
    expected_id = make_vector_id(field_vector_identity_payload(checked))
    if checked["vector_id"] != expected_id:
        raise IdentityConflict("record.vector_id does not bind the exact identity payload")
    supersedes = checked["supersedes"]
    if supersedes is not None:
        require_vector_id(supersedes, "record.supersedes")
    state = checked["invalidation_state"]
    invalidated_by = checked["invalidated_by"]
    if state == "active":
        if invalidated_by is not None:
            raise ContractViolation("active record must have invalidated_by=null")
    elif state in {"invalidated", "tombstoned"}:
        require_vector_id(invalidated_by, "record.invalidated_by")
    else:
        raise ContractViolation("record.invalidation_state is invalid")
    if checked["production_eligible"] is not False:
        raise ContractViolation("field vector record must be non-production")
    return checked


_FIELD_SOURCE_KEYS = {
    "object_type",
    "schema_version",
    "field_source_id",
    "fixture_id",
    "paper_id",
    "card_artifact_ref",
    "card_id",
    "card_revision",
    "card_content_hash",
    "field_path",
    "value_locator",
    "canonical_value",
    "value_hash",
    "source_refs",
    "source_coverage",
    "provenance_refs",
    "parent_refs",
    "doc_type",
    "data_ownership",
    "review_status_snapshot",
    "observed_at",
    "build_cutoff_at",
    "field_registry_id",
    "field_registry_version",
    "field_registry_hash",
    "production_eligible",
    "content_hash",
}


def verify_field_source_item(
    source: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> dict[str, Any]:
    """Verify one exact FieldSourceItem and its binding to a SourceSnapshot."""

    checked = verify_hashed_payload(source, "field_source")
    if set(checked) != _FIELD_SOURCE_KEYS:
        raise ContractViolation(
            "field source keys differ from the frozen exact set",
            context={
                "missing": sorted(_FIELD_SOURCE_KEYS - set(checked)),
                "unexpected": sorted(set(checked) - _FIELD_SOURCE_KEYS),
            },
        )
    if checked.get("object_type") != "FieldSourceItem":
        raise ContractViolation("snapshot member must be FieldSourceItem")
    if checked.get("schema_version") != "1.0":
        raise ContractViolation("field source schema_version must be 1.0")
    if checked.get("review_status_snapshot") != "active":
        raise ContractViolation("snapshot member is not active")
    if checked.get("build_cutoff_at") != snapshot["build_cutoff_at"]:
        raise ContractViolation("field source cutoff differs from SourceSnapshot")
    if checked.get("production_eligible") is not False:
        raise ContractViolation("field source must be non-production")
    if checked.get("value_hash") != make_value_hash(checked.get("canonical_value")):
        raise IdentityConflict("field source value hash mismatch")
    if checked.get("value_locator") != "/" + str(checked.get("field_path")):
        raise IdentityConflict("field source value_locator does not bind field_path")
    validate_hash_descriptor(checked.get("card_content_hash"), "field_source.card_content_hash")
    for field_name in ("source_refs", "provenance_refs", "parent_refs"):
        if checked.get(field_name) != require_non_empty_refs(
            checked.get(field_name), f"field_source.{field_name}"
        ):
            raise ContractViolation(f"field_source.{field_name} is not canonically sorted")
    if checked.get("source_coverage") not in {
        "field_anchor",
        "item_anchor",
        "artifact_level_no_dedicated_anchor",
    }:
        raise ContractViolation("field_source.source_coverage is invalid")
    if checked.get("doc_type") not in {"literature", "note"}:
        raise ContractViolation("field_source.doc_type is invalid")
    if checked.get("data_ownership") not in {"self", "entrusted"}:
        raise ContractViolation("field_source.data_ownership is invalid")
    require_utc_timestamp(checked.get("observed_at"), "field_source.observed_at")
    require_utc_timestamp(checked.get("build_cutoff_at"), "field_source.build_cutoff_at")
    if _parse_utc(checked["observed_at"]) > _parse_utc(checked["build_cutoff_at"]):
        raise ContractViolation("field source observation is after build cutoff")
    registry = get_field_registry()
    if (
        checked.get("field_registry_id") != registry["registry_id"]
        or checked.get("field_registry_version") != registry["revision"]
        or checked.get("field_registry_hash") != registry["content_hash"]["value"]
    ):
        raise IdentityConflict("field source registry authority mismatch")
    identity = {
        "paper_id": checked.get("paper_id"),
        "card_id": checked.get("card_id"),
        "card_revision": checked.get("card_revision"),
        "card_content_hash": checked.get("card_content_hash"),
        "field_path": checked.get("field_path"),
        "value_locator": checked.get("value_locator"),
        "value_hash": checked.get("value_hash"),
        "doc_type": checked.get("doc_type"),
        "data_ownership": checked.get("data_ownership"),
        "field_registry_hash": checked.get("field_registry_hash"),
    }
    expected_id = "fsi1_" + sha256_bytes(canonical_json_bytes(identity)).lower()
    if checked.get("field_source_id") != expected_id:
        raise IdentityConflict("field_source_id does not bind the exact source identity")
    return checked


def verify_field_vector_record_against_source(
    record: Mapping[str, Any],
    source: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify a vector record against the exact typed snapshot member it represents."""

    checked = verify_field_vector_record(record)
    checked_source = verify_field_source_item(source, snapshot)
    snapshot_checked = verify_hashed_payload(snapshot, "source_snapshot")
    direct_fields = (
        "field_source_id",
        "paper_id",
        "card_artifact_ref",
        "card_id",
        "card_revision",
        "card_content_hash",
        "field_path",
        "value_locator",
        "canonical_value",
        "value_hash",
        "source_refs",
        "source_coverage",
        "provenance_refs",
        "doc_type",
        "data_ownership",
        "review_status_snapshot",
        "observed_at",
        "build_cutoff_at",
    )
    for field_name in direct_fields:
        if canonical_json_bytes(checked.get(field_name)) != canonical_json_bytes(
            checked_source.get(field_name)
        ):
            raise IdentityConflict(
                f"record.{field_name} differs from its exact FieldSourceItem"
            )
    expected_parent_refs = require_non_empty_refs(
        [*checked_source["parent_refs"], checked_source["field_source_id"]],
        "record.parent_refs",
    )
    if checked.get("parent_refs") != expected_parent_refs:
        raise IdentityConflict("record.parent_refs do not bind the exact FieldSourceItem")
    if checked.get("source_snapshot_id") != snapshot_checked.get("source_snapshot_id"):
        raise IdentityConflict("record source_snapshot_id mismatch")
    if canonical_json_bytes(checked.get("source_snapshot_hash")) != canonical_json_bytes(
        snapshot_checked.get("content_hash")
    ):
        raise IdentityConflict("record source_snapshot_hash mismatch")
    return checked


def build_field_vector_records(
    *,
    source_snapshot: Mapping[str, Any],
    profile: Mapping[str, Any],
    index_id: str,
    index_version: int,
    collection_id: str,
    embedder: OfflineEmbedder | None = None,
) -> dict[str, Any]:
    """Build verified in-memory records and successor tombstones without index writes."""

    snapshot = verify_hashed_payload(source_snapshot, "source_snapshot")
    checked_profile = validate_embedding_profile(profile)
    checked_index_id = require_storage_token(index_id, "index_id")
    checked_collection = require_storage_token(collection_id, "collection_id")
    if isinstance(index_version, bool) or not isinstance(index_version, int) or index_version < 1:
        raise ContractViolation("index_version must be a positive integer")
    if checked_profile["index_version"] != index_version:
        raise IdentityConflict("profile.index_version differs from index_version")
    if snapshot.get("object_type") != "SourceSnapshot" or snapshot.get("state") != "snapshot_ready":
        raise ContractViolation("source_snapshot is not snapshot_ready")
    if snapshot.get("production_eligible") is not False:
        raise ContractViolation("source_snapshot must be non-production")
    build_cutoff_at = require_utc_timestamp(snapshot.get("build_cutoff_at"), "snapshot.build_cutoff_at")
    raw_sources = snapshot.get("records")
    if not isinstance(raw_sources, list):
        raise ContractViolation("source_snapshot.records must be a list")
    sources = [verify_field_source_item(item, snapshot) for item in raw_sources]
    ownerships = {item["data_ownership"] for item in sources}
    if len(ownerships) > 1:
        raise IdentityConflict("self and entrusted fields must not share one vector build")
    if ownerships:
        validate_ownership_partition(
            next(iter(ownerships)), checked_profile, checked_index_id, checked_collection
        )
    if embedder is not None and getattr(embedder, "memorive_offline_fixture", False) is not True:
        raise EmbeddingProfileError("custom embedder must declare memorive_offline_fixture=True")

    sources.sort(key=lambda item: (item["paper_id"], item["card_id"], item["value_locator"], item["card_revision"]))
    records: list[dict[str, Any]] = []
    tombstones: list[dict[str, Any]] = []
    prior_by_slot: dict[tuple[Any, ...], dict[str, Any]] = {}
    for source in sources:
        slot = (
            source["paper_id"],
            source["card_id"],
            source["value_locator"],
            checked_profile["embedding_profile_id"],
            checked_profile["embedding_model_id"],
            checked_profile["embedding_dimension"],
            checked_profile["embedding_normalization"],
            checked_index_id,
            index_version,
            checked_collection,
        )
        prior = prior_by_slot.get(slot)
        if prior is not None and source["card_revision"] <= prior["card_revision"]:
            raise IdentityConflict("stable field slot has duplicate or non-monotonic revision")
        embedding_seed = canonical_json_bytes(
            {
                "canonical_value": source["canonical_value"],
                "embedding_profile_id": checked_profile["embedding_profile_id"],
                "embedding_model_id": checked_profile["embedding_model_id"],
            }
        ).decode("utf-8")
        if embedder is None:
            embedding = deterministic_fixture_embedding(
                embedding_seed,
                dimension=checked_profile["embedding_dimension"],
                normalization=checked_profile["embedding_normalization"],
            )
        else:
            embedding = embedder(embedding_seed, deepcopy(checked_profile))
        embedding = _validated_vector(
            embedding,
            checked_profile["embedding_dimension"],
            checked_profile["embedding_normalization"],
        )
        parent_refs = require_non_empty_refs(
            [*source["parent_refs"], source["field_source_id"]], "record.parent_refs"
        )
        base = {
            "schema_version": "1.0",
            "vector_type": "card_field",
            "identity_version": 1,
            "paper_id": source["paper_id"],
            "card_artifact_ref": source["card_artifact_ref"],
            "card_id": source["card_id"],
            "card_revision": source["card_revision"],
            "card_content_hash": deepcopy(source["card_content_hash"]),
            "field_source_id": source["field_source_id"],
            "field_path": source["field_path"],
            "value_locator": source["value_locator"],
            "canonical_value": source["canonical_value"],
            "value_hash": deepcopy(source["value_hash"]),
            "source_refs": deepcopy(source["source_refs"]),
            "source_coverage": source["source_coverage"],
            "provenance_refs": deepcopy(source["provenance_refs"]),
            "parent_refs": parent_refs,
            "doc_type": source["doc_type"],
            "data_ownership": source["data_ownership"],
            "review_status_snapshot": "active",
            "source_snapshot_id": snapshot["source_snapshot_id"],
            "source_snapshot_hash": deepcopy(snapshot["content_hash"]),
            "embedding_profile_id": checked_profile["embedding_profile_id"],
            "embedding_model_id": checked_profile["embedding_model_id"],
            "embedding_dimension": checked_profile["embedding_dimension"],
            "embedding_normalization": checked_profile["embedding_normalization"],
            "embedding": embedding,
            "index_id": checked_index_id,
            "index_version": index_version,
            "collection_id": checked_collection,
            "observed_at": source["observed_at"],
            "build_cutoff_at": build_cutoff_at,
            "hash_kind": "rfc8785_jcs_sha256",
            "supersedes": None if prior is None else prior["vector_id"],
            "invalidation_state": "active",
            "invalidated_by": None,
            "production_eligible": False,
        }
        vector_id = make_vector_id(field_vector_identity_payload(base))
        base["vector_id"] = vector_id
        base["storage_key"] = (
            f"field-vector/v1/{checked_index_id}/{checked_collection}/{vector_id}"
        )
        record = verify_field_vector_record(make_hashed_payload(base))
        records.append(record)
        if prior is not None:
            tombstone_payload = deepcopy(prior)
            tombstone_payload.pop("content_hash", None)
            tombstone_payload["invalidation_state"] = "tombstoned"
            tombstone_payload["invalidated_by"] = vector_id
            tombstones.append(verify_field_vector_record(make_hashed_payload(tombstone_payload)))
        prior_by_slot[slot] = record

    records.sort(key=lambda item: item["vector_id"])
    tombstones.sort(key=lambda item: item["vector_id"])
    return {
        "records": records,
        "tombstones": tombstones,
        "rejections": deepcopy(snapshot.get("rejections", [])),
        "overwrite_count": 0,
        "side_effects": {
            "external_calls": 0,
            "cost_cny": 0,
            "production_mutations": 0,
            "git_mutations": 0,
            "activation": False,
        },
    }
