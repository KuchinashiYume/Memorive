"""Deterministic, manifest-backed FIELD-VECTORS candidate index.

This is not a Chroma adapter.  It creates a new directory exclusively under
the supplied run-owned root and materializes only canonical JSON/JSONL files.
The resulting candidate is never production eligible and never activated.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from .canonical import canonical_json_bytes, typed_payload_hash
from .contracts import (
    validate_embedding_profile,
    validate_index_identity,
    verify_hashed_payload,
)
from .errors import ContractViolation, FieldVectorError, IdentityConflict
from .field_vectors import (
    verify_field_source_item,
    verify_field_vector_record,
    verify_field_vector_record_against_source,
)
from .locator import (
    BACKEND,
    IMMUTABLE_STATE,
    PathBoundaryError,
    ensure_owned_path,
    make_locator,
    validate_locator,
    validate_run_root,
)


RECORDS_FILE = "records.jsonl"
SNAPSHOT_FILE = "source_snapshot.json"
TOMBSTONES_FILE = "tombstones.jsonl"
MANIFEST_FILE = "manifest.json"
MARKER_FILE = "IMMUTABLE_CANDIDATE.json"
SUMS_FILE = "SHA256SUMS"
VECTOR_ID_RE = re.compile(r"^fv1_[a-f0-9]{64}$")
_HASH_DESCRIPTOR_KEYS = {"algorithm", "hash_kind", "scope", "value"}
_SOURCE_SNAPSHOT_KEYS = {
    "object_type",
    "schema_version",
    "source_snapshot_id",
    "state",
    "observed_at",
    "build_cutoff_at",
    "public_safe",
    "records",
    "rejections",
    "production_eligible",
    "content_hash",
}
_ACTIVE_INDEX_BASELINE_KEYS = {
    "authority_ref",
    "authority_raw_sha256",
    "declared_locator",
    "exists",
    "member_count",
    "bytes",
    "tree_sha256",
    "read_only",
}


class CandidateIndexError(FieldVectorError):
    """Candidate index construction failed before qualification."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _canonical_file_bytes(value: Any) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(dict(row)) + b"\n" for row in rows)


def _write_exclusive(path: Path, data: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise CandidateIndexError(f"exclusive-create member already exists: {path}") from exc


def _safe_remove_created_tree(path: Path, owned_root: Path) -> None:
    """Remove only a just-created descendant, never a pre-existing candidate."""

    checked = ensure_owned_path(path, owned_root, must_exist=True)
    for member in checked.rglob("*"):
        attributes = getattr(os.lstat(member), "st_file_attributes", 0)
        if member.is_symlink() or attributes & 0x400:
            raise CandidateIndexError(
                f"refusing cleanup because a reparse member appeared: {member}"
            )
    shutil.rmtree(checked)


def _typed_hash_descriptor(value: Any, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ContractViolation(f"{field} must be a typed hash object")
    descriptor = dict(value)
    if set(descriptor) != _HASH_DESCRIPTOR_KEYS:
        raise ContractViolation(f"{field} typed hash keys do not match the exact set")
    if descriptor["algorithm"] != "sha256":
        raise ContractViolation(f"{field}.algorithm must be sha256")
    if descriptor["hash_kind"] != "rfc8785_jcs_sha256":
        raise ContractViolation(f"{field}.hash_kind must be rfc8785_jcs_sha256")
    if descriptor["scope"] != "PAYLOAD_EXCLUDING_CONTENT_HASH":
        raise ContractViolation(f"{field}.scope is not the frozen payload scope")
    digest = descriptor["value"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or digest.upper() != digest
        or any(char not in "0123456789ABCDEF" for char in digest)
    ):
        raise ContractViolation(f"{field}.value must be uppercase SHA-256")
    return descriptor


def _typed_hash_value(payload: Mapping[str, Any], *, field: str) -> str:
    return _typed_hash_descriptor(
        payload.get("content_hash"), field=f"{field}.content_hash"
    )["value"]


def _non_empty_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractViolation(f"{field} must be a non-empty exact string")
    if "\r" in value or "\n" in value:
        raise ContractViolation(f"{field} contains a forbidden line break")
    return value


def validate_source_snapshot(source_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the actual typed runtime SourceSnapshot, never a wrapper."""

    if not isinstance(source_snapshot, Mapping) or not source_snapshot:
        raise ContractViolation("a non-empty explicit source_snapshot is required")
    snapshot = verify_hashed_payload(source_snapshot, "source_snapshot")
    if set(snapshot) != _SOURCE_SNAPSHOT_KEYS:
        raise ContractViolation(
            "source_snapshot keys do not match the core typed SourceSnapshot exact set",
            context={
                "missing": sorted(_SOURCE_SNAPSHOT_KEYS - set(snapshot)),
                "unexpected": sorted(set(snapshot) - _SOURCE_SNAPSHOT_KEYS),
            },
        )
    if snapshot.get("object_type") != "SourceSnapshot":
        raise ContractViolation(
            "source_snapshot must itself be object_type=SourceSnapshot; wrappers are forbidden"
        )
    if snapshot.get("state") != "snapshot_ready":
        raise ContractViolation("source_snapshot.state must be snapshot_ready")
    _non_empty_string(snapshot.get("source_snapshot_id"), field="source_snapshot_id")
    _typed_hash_value(snapshot, field="source_snapshot")
    if snapshot.get("production_eligible", False) is not False:
        raise ContractViolation("source_snapshot may not be production eligible")
    if snapshot.get("activation", False) is not False:
        raise ContractViolation("source_snapshot may not request activation")
    raw_records = snapshot.get("records")
    if not isinstance(raw_records, list):
        raise ContractViolation("source_snapshot.records must be a list")
    verified_records: list[dict[str, Any]] = []
    member_ids: list[str] = []
    for ordinal, raw_record in enumerate(raw_records):
        if not isinstance(raw_record, Mapping):
            raise ContractViolation(f"source_snapshot.records[{ordinal}] must be an object")
        checked_record = verify_field_source_item(raw_record, snapshot)
        field_source_id = checked_record["field_source_id"]
        if field_source_id in member_ids:
            raise IdentityConflict("source_snapshot contains duplicate field_source_id")
        member_ids.append(field_source_id)
        verified_records.append(checked_record)
    if verified_records != sorted(
        verified_records,
        key=lambda item: (
            item["paper_id"],
            item["card_id"],
            item["card_revision"],
            item["value_locator"],
        ),
    ):
        raise ContractViolation("source_snapshot.records are not in canonical source order")
    expected_snapshot_id = "ss1_" + _sha256(
        canonical_json_bytes(
            {
                "build_cutoff_at": snapshot["build_cutoff_at"],
                "public_safe": True,
                "member_ids": member_ids,
                "rejections": snapshot["rejections"],
            }
        )
    ).lower()
    if snapshot["source_snapshot_id"] != expected_snapshot_id:
        raise IdentityConflict("source_snapshot_id does not bind the exact member set")
    snapshot["records"] = verified_records
    return snapshot


def validate_active_index_baseline(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the runner-measured active-index authority receipt."""

    if not isinstance(value, Mapping):
        raise ContractViolation("active_index_baseline must be an explicit object")
    checked = deepcopy(dict(value))
    if set(checked) != _ACTIVE_INDEX_BASELINE_KEYS:
        raise ContractViolation("active_index_baseline exact key set mismatch")
    _non_empty_string(checked["authority_ref"], field="active_index_baseline.authority_ref")
    authority_sha = checked["authority_raw_sha256"]
    if not isinstance(authority_sha, str) or re.fullmatch(r"[A-F0-9]{64}", authority_sha) is None:
        raise ContractViolation("active_index_baseline.authority_raw_sha256 is invalid")
    _non_empty_string(
        checked["declared_locator"], field="active_index_baseline.declared_locator"
    )
    if not isinstance(checked["exists"], bool):
        raise ContractViolation("active_index_baseline.exists must be boolean")
    for name in ("member_count", "bytes"):
        number = checked[name]
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise ContractViolation(f"active_index_baseline.{name} must be non-negative")
    if checked["read_only"] is not True:
        raise ContractViolation("active_index_baseline.read_only must be true")
    tree_hash = checked["tree_sha256"]
    if checked["exists"] is False:
        if checked["member_count"] != 0 or checked["bytes"] != 0 or tree_hash is not None:
            raise ContractViolation("absent active index baseline must have zero/null measures")
    elif not isinstance(tree_hash, str) or re.fullmatch(r"[A-F0-9]{64}", tree_hash) is None:
        raise ContractViolation("existing active index baseline requires tree_sha256")
    return checked


def validate_candidate_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    return validate_embedding_profile(profile)


def validate_candidate_index_identity(index_identity: Mapping[str, Any]) -> dict[str, Any]:
    return validate_index_identity(index_identity)


def _record_profile_fields(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("embedding_profile_id"),
        record.get("embedding_model_id"),
        record.get("embedding_dimension"),
        record.get("embedding_normalization"),
    )


def _record_index_fields(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("index_id"),
        record.get("index_version"),
        record.get("collection_id"),
        record.get("build_cutoff_at"),
    )


def _validate_record_binding(
    record: Mapping[str, Any],
    profile: Mapping[str, Any],
    index_identity: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
    field_sources: Mapping[str, Mapping[str, Any]],
    *,
    ordinal: int,
) -> dict[str, Any]:
    initial = verify_field_vector_record(record)
    field_source_id = initial.get("field_source_id")
    if field_source_id not in field_sources:
        raise IdentityConflict("record field_source_id is absent from SourceSnapshot")
    checked = verify_field_vector_record_against_source(
        initial, field_sources[field_source_id], source_snapshot
    )
    vector_id = checked.get("vector_id")
    if not isinstance(vector_id, str) or VECTOR_ID_RE.fullmatch(vector_id) is None:
        raise ContractViolation(
            f"records[{ordinal}].vector_id must match ^fv1_[a-f0-9]{{64}}$"
        )
    if checked.get("vector_type") != "card_field":
        raise ContractViolation(f"records[{ordinal}].vector_type must be card_field")
    if checked.get("hash_kind") != "rfc8785_jcs_sha256":
        raise ContractViolation(f"records[{ordinal}].hash_kind is not normative")

    observed_profile = _record_profile_fields(checked)
    expected_profile = (
        profile["embedding_profile_id"],
        profile["embedding_model_id"],
        profile["embedding_dimension"],
        profile["embedding_normalization"],
    )
    if observed_profile != expected_profile:
        raise IdentityConflict(
            "record attempts profile/model/dimension mixing",
            context={
                "vector_id": vector_id,
                "expected": list(expected_profile),
                "observed": list(observed_profile),
            },
        )

    observed_index = _record_index_fields(checked)
    expected_index = (
        index_identity["index_id"],
        index_identity["version"],
        index_identity["collection_id"],
        index_identity["build_cutoff_at"],
    )
    if observed_index != expected_index:
        raise IdentityConflict(
            "record attempts index/version/collection/vector_type mixing",
            context={
                "vector_id": vector_id,
                "expected": list(expected_index),
                "observed": list(observed_index),
            },
        )

    if profile["index_version"] != index_identity["version"]:
        raise IdentityConflict("profile.index_version differs from index_identity")

    snapshot_id = source_snapshot["source_snapshot_id"]
    if checked.get("source_snapshot_id") != snapshot_id:
        raise IdentityConflict(
            "record source_snapshot_id differs from the exact typed SourceSnapshot",
            context={"vector_id": vector_id},
        )
    record_snapshot_hash = _typed_hash_descriptor(
        checked.get("source_snapshot_hash"),
        field=f"records[{ordinal}].source_snapshot_hash",
    )
    if canonical_json_bytes(record_snapshot_hash) != canonical_json_bytes(
        source_snapshot["content_hash"]
    ):
        raise IdentityConflict(
            "record source_snapshot_hash differs from the exact typed SourceSnapshot",
            context={"vector_id": vector_id},
        )

    _typed_hash_descriptor(
        checked.get("card_content_hash"),
        field=f"records[{ordinal}].card_content_hash",
    )
    _typed_hash_descriptor(
        checked.get("value_hash"), field=f"records[{ordinal}].value_hash"
    )
    for field_name in ("paper_id", "card_id", "field_path", "value_locator"):
        _non_empty_string(
            checked.get(field_name), field=f"records[{ordinal}].{field_name}"
        )
    for field_name in ("source_refs", "provenance_refs", "parent_refs"):
        values = checked.get(field_name)
        if (
            not isinstance(values, list)
            or not values
            or len(values) != len(set(values))
            or any(not isinstance(item, str) or not item for item in values)
        ):
            raise ContractViolation(
                f"records[{ordinal}].{field_name} must be a unique non-empty string list"
            )

    embedding = checked.get("embedding")
    dimension = profile["embedding_dimension"]
    if not isinstance(embedding, list) or len(embedding) != dimension:
        raise IdentityConflict(
            "record embedding length differs from the frozen profile dimension",
            context={"vector_id": vector_id},
        )
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in embedding
    ):
        raise ContractViolation("record embedding must contain only finite numbers")

    expected_storage_key = (
        f"field-vector/v1/{index_identity['index_id']}/"
        f"{index_identity['collection_id']}/{vector_id}"
    )
    if checked.get("storage_key") != expected_storage_key:
        raise IdentityConflict(
            "record storage_key is not derived from the frozen index and vector identity",
            context={"vector_id": vector_id},
        )

    ownership = checked.get("data_ownership")
    if ownership not in {"self", "entrusted"}:
        raise ContractViolation("record data_ownership must be self or entrusted")
    local_values = (
        profile["embedding_profile_id"],
        index_identity["index_id"],
        index_identity["collection_id"],
    )
    if ownership == "entrusted" and any(
        not value.startswith("local-only:") for value in local_values
    ):
        raise IdentityConflict("entrusted records require a fully local-only partition")
    if ownership == "self" and any(value.startswith("local-only:") for value in local_values):
        raise IdentityConflict("self records may not enter an entrusted local-only partition")

    if checked.get("invalidation_state") != "active":
        raise ContractViolation("normal candidate records must have invalidation_state=active")
    if checked.get("invalidated_by") is not None:
        raise ContractViolation("active candidate records must have invalidated_by=null")
    if checked.get("production_eligible", False) is not False:
        raise ContractViolation("field-vector records may not be production eligible")
    if checked.get("activation", False) is not False:
        raise ContractViolation("field-vector records may not request activation")
    return checked


def _deduplicate_records(
    records: Iterable[Mapping[str, Any]],
    profile: Mapping[str, Any],
    index_identity: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    field_sources = {
        item["field_source_id"]: item for item in source_snapshot["records"]
    }
    by_identity: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    idempotent_duplicates = 0
    for ordinal, raw_record in enumerate(records):
        if not isinstance(raw_record, Mapping):
            raise ContractViolation(f"records[{ordinal}] must be an object")
        record = _validate_record_binding(
            raw_record,
            profile,
            index_identity,
            source_snapshot,
            field_sources,
            ordinal=ordinal,
        )
        vector_id = record["vector_id"]
        content_hash = _typed_hash_value(record, field=f"records[{ordinal}]")
        if vector_id not in by_identity:
            by_identity[vector_id] = record
            hashes[vector_id] = content_hash
            continue
        if hashes[vector_id] != content_hash:
            raise IdentityConflict(
                "same vector identity carries a different content hash",
                context={
                    "vector_id": vector_id,
                    "first_hash": hashes[vector_id],
                    "second_hash": content_hash,
                },
            )
        idempotent_duplicates += 1
    ordered = [by_identity[key] for key in sorted(by_identity)]
    if ordered:
        ownership = ordered[0]["data_ownership"]
        expected_source_ids = {
            item["field_source_id"]
            for item in source_snapshot["records"]
            if item["data_ownership"] == ownership
        }
        observed_source_ids = {item["field_source_id"] for item in ordered}
        if observed_source_ids != expected_source_ids:
            raise IdentityConflict(
                "candidate record field-source exact set differs from its ownership partition",
                context={
                    "missing": sorted(expected_source_ids - observed_source_ids),
                    "unexpected": sorted(observed_source_ids - expected_source_ids),
                },
            )
    return ordered, idempotent_duplicates


def _supersedes_values(record: Mapping[str, Any]) -> list[str]:
    value = record.get("supersedes")
    if value is None:
        return []
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        if len(value) != len(set(value)):
            raise ContractViolation("supersedes list must be unique")
        return sorted(value)
    raise ContractViolation("supersedes must be null, a non-empty string, or a unique string list")


def _build_tombstones(
    records: list[dict[str, Any]],
    predecessor_vector_ids: Iterable[str] = (),
    tombstone_transitions: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    candidate_ids = {record["vector_id"] for record in records}
    known = set(candidate_ids)
    predecessor_set = set(predecessor_vector_ids)
    if any(not isinstance(value, str) or not value for value in predecessor_set):
        raise ContractViolation("predecessor_vector_ids must contain non-empty strings")
    known.update(predecessor_set)
    transitions: dict[str, tuple[str | None, str]] = {}

    def add_transition(predecessor: str, successor: str | None, reason: str) -> None:
        if predecessor not in known:
            raise IdentityConflict(
                "tombstone target is absent from the preserved record set",
                context={"missing_predecessor": predecessor},
            )
        if successor is not None and successor not in candidate_ids:
            raise IdentityConflict(
                "tombstone successor is absent from the candidate record set",
                context={"missing_successor": successor},
            )
        if successor == predecessor:
            raise IdentityConflict("a vector may not supersede itself")
        proposed = (successor, reason)
        prior = transitions.get(predecessor)
        if prior is not None and prior != proposed:
            raise IdentityConflict(
                "one predecessor cannot have conflicting tombstone transitions",
                context={"predecessor": predecessor},
            )
        transitions[predecessor] = proposed

    for record in records:
        successor = record["vector_id"]
        for predecessor in _supersedes_values(record):
            add_transition(predecessor, successor, "superseded")

    expected_transition_keys = {
        "predecessor_vector_id",
        "successor_vector_id",
        "reason",
    }
    for ordinal, raw_transition in enumerate(tombstone_transitions):
        if not isinstance(raw_transition, Mapping):
            raise ContractViolation(f"tombstone_transitions[{ordinal}] must be an object")
        transition = dict(raw_transition)
        if set(transition) != expected_transition_keys:
            raise ContractViolation(
                f"tombstone_transitions[{ordinal}] has an invalid exact key set"
            )
        predecessor = transition["predecessor_vector_id"]
        successor = transition["successor_vector_id"]
        reason = transition["reason"]
        if not isinstance(predecessor, str) or not predecessor:
            raise ContractViolation("tombstone predecessor must be a non-empty string")
        if successor is not None and (not isinstance(successor, str) or not successor):
            raise ContractViolation("tombstone successor must be null or a non-empty string")
        if reason not in {"superseded", "removed_from_source_snapshot"}:
            raise ContractViolation("unsupported tombstone reason")
        add_transition(predecessor, successor, reason)

    tombstones: list[dict[str, Any]] = []
    for predecessor in sorted(transitions):
        successor, reason = transitions[predecessor]
        payload = {
            "tombstone_id": "tombstone_"
            + _sha256(canonical_json_bytes([predecessor, successor, reason])).lower(),
            "vector_id": predecessor,
            "invalidation_state": "tombstoned",
            "invalidated_by": successor,
            "reason": reason,
            "activation": False,
            "production_eligible": False,
        }
        payload["content_hash"] = typed_payload_hash(payload)
        tombstones.append(payload)
    return tombstones


def _exact_set_sha256(records: list[dict[str, Any]]) -> str:
    return _sha256(canonical_json_bytes(make_record_exact_set(records)))


def make_record_exact_set(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    for ordinal, record in enumerate(records):
        vector_id = record.get("vector_id")
        if not isinstance(vector_id, str) or VECTOR_ID_RE.fullmatch(vector_id) is None:
            raise ContractViolation(f"records[{ordinal}].vector_id is not normative")
        anchors.append(
            {
                "vector_id": vector_id,
                "content_hash": deepcopy(
                    _typed_hash_descriptor(
                        record.get("content_hash"),
                        field=f"records[{ordinal}].content_hash",
                    )
                ),
                "storage_key": _non_empty_string(
                    record.get("storage_key"), field=f"records[{ordinal}].storage_key"
                ),
                "card_id": _non_empty_string(
                    record.get("card_id"), field=f"records[{ordinal}].card_id"
                ),
                "field_path": _non_empty_string(
                    record.get("field_path"), field=f"records[{ordinal}].field_path"
                ),
                "data_ownership": record.get("data_ownership"),
            }
        )
    return sorted(anchors, key=lambda item: item["vector_id"])


def make_tombstone_exact_set(
    tombstones: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    for ordinal, tombstone in enumerate(tombstones):
        anchors.append(
            {
                "tombstone_id": _non_empty_string(
                    tombstone.get("tombstone_id"),
                    field=f"tombstones[{ordinal}].tombstone_id",
                ),
                "vector_id": _non_empty_string(
                    tombstone.get("vector_id"), field=f"tombstones[{ordinal}].vector_id"
                ),
                "invalidated_by": tombstone.get("invalidated_by"),
                "reason": tombstone.get("reason"),
                "content_hash": deepcopy(
                    _typed_hash_descriptor(
                        tombstone.get("content_hash"),
                        field=f"tombstones[{ordinal}].content_hash",
                    )
                ),
            }
        )
    return sorted(anchors, key=lambda item: item["tombstone_id"])


def _ownership_partition(
    records: list[dict[str, Any]],
    source_snapshot: Mapping[str, Any],
) -> str:
    ownerships = {record["data_ownership"] for record in records}
    if len(ownerships) != 1:
        raise IdentityConflict(
            "one candidate index may contain exactly one ownership partition",
            context={"observed": sorted(ownerships)},
        )
    ownership = next(iter(ownerships))
    snapshot_ownership = source_snapshot.get("data_ownership")
    if snapshot_ownership is not None and snapshot_ownership != ownership:
        raise IdentityConflict("record ownership differs from the typed SourceSnapshot")
    return "self" if ownership == "self" else "entrusted_local_only"


def _materialize_candidate(
    records: Iterable[Mapping[str, Any]],
    profile: Mapping[str, Any],
    index_identity: Mapping[str, Any],
    output_root: Path,
    owned_root: Path,
    *,
    source_snapshot: Mapping[str, Any],
    active_index_baseline: Mapping[str, Any],
    predecessor_vector_ids: Iterable[str],
    tombstone_transitions: Iterable[Mapping[str, Any]],
    precreated_empty: bool = False,
) -> dict[str, Any]:
    checked_snapshot = validate_source_snapshot(source_snapshot)
    checked_profile = validate_candidate_profile(profile)
    checked_identity = validate_candidate_index_identity(index_identity)
    checked_active_baseline = validate_active_index_baseline(active_index_baseline)

    deduplicated, idempotent_duplicates = _deduplicate_records(
        records, checked_profile, checked_identity, checked_snapshot
    )
    if not deduplicated:
        raise ContractViolation("an immutable candidate index requires at least one record")
    ownership_partition = _ownership_partition(deduplicated, checked_snapshot)
    predecessor_exact_set = sorted(set(predecessor_vector_ids))
    tombstones = _build_tombstones(
        deduplicated,
        predecessor_exact_set,
        tombstone_transitions,
    )
    record_exact_set = make_record_exact_set(deduplicated)
    tombstone_exact_set = make_tombstone_exact_set(tombstones)
    snapshot = deepcopy(checked_snapshot)

    if precreated_empty:
        output_root = ensure_owned_path(output_root, owned_root, must_exist=True)
        if not output_root.is_dir() or any(output_root.iterdir()):
            raise CandidateIndexError("owned staging must be an existing empty directory")
    else:
        output_root.mkdir(exist_ok=False)
    try:
        records_bytes = _jsonl_bytes(deduplicated)
        snapshot_bytes = _canonical_file_bytes(snapshot)
        tombstone_bytes = _jsonl_bytes(tombstones)
        _write_exclusive(output_root / RECORDS_FILE, records_bytes)
        _write_exclusive(output_root / SNAPSHOT_FILE, snapshot_bytes)
        _write_exclusive(output_root / TOMBSTONES_FILE, tombstone_bytes)

        payload_tree_sha256 = _sha256(
            canonical_json_bytes(
                {
                    RECORDS_FILE: _sha256(records_bytes),
                    SNAPSHOT_FILE: _sha256(snapshot_bytes),
                    TOMBSTONES_FILE: _sha256(tombstone_bytes),
                }
            )
        )
        manifest_core = {
            "schema_version": "1.0",
            "backend": BACKEND,
            "state": IMMUTABLE_STATE,
            "status": "candidate_immutable",
            "index_id": checked_identity["index_id"],
            "index_version": checked_identity["version"],
            "collection_id": checked_identity["collection_id"],
            "ownership_partition": ownership_partition,
            "profile": checked_profile,
            "index_identity": checked_identity,
            "build_cutoff_at": checked_identity["build_cutoff_at"],
            "created_at": checked_identity["build_cutoff_at"],
            "source_snapshot": {
                "object_type": "SourceSnapshot",
                "source_snapshot_id": checked_snapshot["source_snapshot_id"],
                "content_hash": deepcopy(checked_snapshot["content_hash"]),
            },
            "active_index_baseline": checked_active_baseline,
            "record_count": len(deduplicated),
            "idempotent_duplicate_count": idempotent_duplicates,
            "record_exact_set": record_exact_set,
            "record_exact_set_sha256": _exact_set_sha256(deduplicated),
            "predecessor_vector_ids": predecessor_exact_set,
            "payload_tree_sha256": payload_tree_sha256,
            "payload_tree_excludes": [MANIFEST_FILE, MARKER_FILE, SUMS_FILE],
            "records_file": RECORDS_FILE,
            "records_sha256": _sha256(records_bytes),
            "source_snapshot_file": SNAPSHOT_FILE,
            "source_snapshot_sha256": _sha256(snapshot_bytes),
            "tombstones_file": TOMBSTONES_FILE,
            "tombstones_sha256": _sha256(tombstone_bytes),
            "tombstone_count": len(tombstones),
            "tombstone_exact_set": tombstone_exact_set,
            "tombstone_exact_set_sha256": _sha256(
                canonical_json_bytes(tombstone_exact_set)
            ),
            "exclusive_create": True,
            "write_scope": "isolated_sandbox_only",
            "external_calls": 0,
            "cost_cny": 0,
            "production_mutations": 0,
            "git_mutations": 0,
            "production_eligible": False,
            "activation": False,
        }
        manifest_core["manifest_id"] = "FIELD_VECTORS_CANDIDATE_INDEX_" + _sha256(
            canonical_json_bytes(manifest_core)
        )
        manifest_bytes = _canonical_file_bytes(manifest_core)
        _write_exclusive(output_root / MANIFEST_FILE, manifest_bytes)

        marker = {
            "state": IMMUTABLE_STATE,
            "backend": BACKEND,
            "manifest_sha256": _sha256(manifest_bytes),
            "exclusive_create": True,
            "production_eligible": False,
            "activation": False,
        }
        marker_bytes = _canonical_file_bytes(marker)
        _write_exclusive(output_root / MARKER_FILE, marker_bytes)

        sums_members = {
            MANIFEST_FILE: manifest_bytes,
            MARKER_FILE: marker_bytes,
            RECORDS_FILE: records_bytes,
            SNAPSHOT_FILE: snapshot_bytes,
            TOMBSTONES_FILE: tombstone_bytes,
        }
        sums_bytes = b"".join(
            _sha256(data).encode("ascii") + b"  " + name.encode("ascii") + b"\n"
            for name, data in sorted(sums_members.items())
        )
        _write_exclusive(output_root / SUMS_FILE, sums_bytes)

        locator = make_locator(output_root, owned_root)
        return validate_locator(locator, owned_root)
    except BaseException:
        if output_root.exists():
            _safe_remove_created_tree(output_root, owned_root)
        raise


def build_candidate_index(
    records: Iterable[Mapping[str, Any]],
    profile: Mapping[str, Any],
    index_identity: Mapping[str, Any],
    output_root: str | os.PathLike[str],
    *,
    owned_root: str | os.PathLike[str],
    source_snapshot: Mapping[str, Any] | None = None,
    active_index_baseline: Mapping[str, Any] | None = None,
    predecessor_vector_ids: Iterable[str] = (),
    tombstone_transitions: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build one immutable candidate index by exclusive creation.

    Duplicate records with the same ``vector_id`` and typed content hash are
    idempotent.  A different hash for the same identity is rejected.  The
    returned object is a strict locator; it is not written automatically.
    """

    if not isinstance(source_snapshot, Mapping) or not source_snapshot:
        raise ContractViolation("a non-empty explicit source_snapshot is required")
    if not isinstance(active_index_baseline, Mapping):
        raise ContractViolation("an explicit measured active_index_baseline is required")
    checked_snapshot = validate_source_snapshot(source_snapshot)
    destination = Path(output_root)
    if not destination.is_absolute():
        raise PathBoundaryError("candidate output_root must be absolute")
    boundary = validate_run_root(owned_root)
    destination = ensure_owned_path(destination, boundary, must_exist=False)
    parent = ensure_owned_path(destination.parent, boundary, must_exist=True, allow_root=True)
    if not parent.is_dir():
        raise PathBoundaryError("candidate parent is not a directory")
    return _materialize_candidate(
        records,
        profile,
        index_identity,
        destination,
        boundary.resolve(strict=True),
        source_snapshot=checked_snapshot,
        active_index_baseline=active_index_baseline,
        predecessor_vector_ids=predecessor_vector_ids,
        tombstone_transitions=tombstone_transitions,
    )


__all__ = [
    "CandidateIndexError",
    "build_candidate_index",
    "make_record_exact_set",
    "make_tombstone_exact_set",
    "validate_candidate_index_identity",
    "validate_candidate_profile",
    "validate_source_snapshot",
    "validate_active_index_baseline",
]
