"""Owned-root-only migration and rollback for ServiceContracts candidate indexes."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from .canonical import canonical_json_bytes
from .candidate_index import (
    _deduplicate_records,
    _materialize_candidate,
    make_record_exact_set,
    validate_active_index_baseline,
    validate_candidate_index_identity,
    validate_candidate_profile,
    validate_source_snapshot,
)
from .errors import ContractViolation, FieldVectorError, IdentityConflict
from .field_vectors import field_vector_identity_payload
from .locator import (
    PathBoundaryError,
    ensure_owned_path,
    validate_locator,
    validate_run_root,
)


FAIL_POINTS = frozenset(
    {
        "after_snapshot",
        "after_staging_create",
        "after_candidate_build",
        "after_validation",
        "before_finalize",
    }
)


class MigrationFailure(FieldVectorError):
    """A migration failed; ``receipt`` proves the bounded rollback result."""

    code = "CANDIDATE_MIGRATION_FAILED_ROLLED_BACK"

    def __init__(self, message: str, *, receipt: Mapping[str, Any]) -> None:
        self.receipt = deepcopy(dict(receipt))
        super().__init__(message, context={"rollback_receipt": self.receipt})


class InjectedMigrationFailure(RuntimeError):
    """Internal marker used only at an explicitly requested fail point."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractViolation(f"duplicate JSON key in previous records: {key}")
        result[key] = value
    return result


def _read_previous_records(locator: Mapping[str, Any]) -> list[dict[str, Any]]:
    root = Path(str(locator["root"]))
    data = (root / "records.jsonl").read_bytes()
    rows: list[dict[str, Any]] = []
    for ordinal, line in enumerate(data.splitlines()):
        if not line:
            raise ContractViolation(f"blank previous JSONL row at ordinal {ordinal}")
        value = json.loads(
            line.decode("utf-8", errors="strict"), object_pairs_hook=_strict_object
        )
        if not isinstance(value, dict):
            raise ContractViolation("previous record must be a JSON object")
        rows.append(value)
    return rows


def _content_hash_value(record: Mapping[str, Any]) -> str:
    descriptor = record.get("content_hash")
    if not isinstance(descriptor, Mapping):
        raise ContractViolation("record content_hash must be a typed descriptor")
    value = descriptor.get("value")
    if not isinstance(value, str) or len(value) != 64:
        raise ContractViolation("record content_hash.value must be SHA-256")
    return value.upper()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _check_cross_candidate_identity(
    incoming: Iterable[Mapping[str, Any]],
    previous: Iterable[Mapping[str, Any]],
) -> set[str]:
    incoming_rows = list(incoming)
    previous_rows = list(previous)
    prior_hashes: dict[str, str] = {}
    for record in previous_rows:
        vector_id = record.get("vector_id")
        if not isinstance(vector_id, str) or not vector_id:
            raise ContractViolation("previous candidate contains an invalid vector_id")
        prior_hashes[vector_id] = _content_hash_value(record)
    prior_records: dict[str, Mapping[str, Any]] = {}
    for record in previous_rows:
        vector_id = str(record["vector_id"])
        prior_records[vector_id] = record
    for record in incoming_rows:
        vector_id = record.get("vector_id")
        if not isinstance(vector_id, str) or not vector_id:
            raise ContractViolation("incoming candidate contains an invalid vector_id")
        if vector_id in prior_hashes:
            prior_identity = field_vector_identity_payload(prior_records[vector_id])
            incoming_identity = field_vector_identity_payload(record)
            if canonical_json_bytes(prior_identity) != canonical_json_bytes(incoming_identity):
                raise IdentityConflict(
                    "same vector_id carries a different identity payload across candidates",
                    context={"vector_id": vector_id},
                )
    return set(prior_hashes)


def _semantic_slot(record: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the revision-stable field slot used for successor matching."""

    slot = (
        record.get("paper_id"),
        record.get("card_id"),
        record.get("value_locator"),
    )
    if any(value is None or value == "" for value in slot):
        raise ContractViolation(
            "record lacks the explicit paper_id/card_id/value_locator successor slot"
        )
    return slot


def _derive_tombstone_transitions(
    incoming: Iterable[Mapping[str, Any]],
    previous: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    incoming_by_slot: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    incoming_ids: set[str] = set()
    for record in incoming:
        slot = _semantic_slot(record)
        vector_id = str(record["vector_id"])
        if slot in incoming_by_slot and incoming_by_slot[slot]["vector_id"] != vector_id:
            raise IdentityConflict("multiple incoming vectors occupy one semantic field slot")
        incoming_by_slot[slot] = record
        incoming_ids.add(vector_id)

    previous_by_slot: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for record in previous:
        slot = _semantic_slot(record)
        vector_id = str(record["vector_id"])
        if slot in previous_by_slot and previous_by_slot[slot]["vector_id"] != vector_id:
            raise IdentityConflict("multiple previous vectors occupy one semantic field slot")
        previous_by_slot[slot] = record

    transitions: list[dict[str, Any]] = []
    for slot, old_record in sorted(previous_by_slot.items(), key=lambda item: repr(item[0])):
        predecessor = str(old_record["vector_id"])
        if predecessor in incoming_ids:
            continue
        new_record = incoming_by_slot.get(slot)
        successor = None if new_record is None else str(new_record["vector_id"])
        transitions.append(
            {
                "predecessor_vector_id": predecessor,
                "successor_vector_id": successor,
                "reason": (
                    "removed_from_source_snapshot" if successor is None else "superseded"
                ),
            }
        )
    return transitions


def _fail_if_requested(fail_at: str | None, point: str) -> None:
    if fail_at == point:
        raise InjectedMigrationFailure(f"injected failure at {point}")


def _safe_remove_staging(staging: Path, owned_root: Path) -> bool:
    if not staging.exists():
        return True
    checked = ensure_owned_path(staging, owned_root, must_exist=True)
    if not checked.name.startswith(".") or not checked.name.endswith(".migration-staging"):
        raise PathBoundaryError("refusing to remove a non-staging path")
    for member in checked.rglob("*"):
        attributes = getattr(os.lstat(member), "st_file_attributes", 0)
        if member.is_symlink() or attributes & 0x400:
            raise PathBoundaryError(
                f"refusing staging rollback after reparse member appeared: {member}"
            )
    shutil.rmtree(checked)
    return not checked.exists()


def _previous_unchanged(
    previous_locator: Mapping[str, Any] | None,
    owned_root: Path,
    expected_tree: str | None,
    expected_manifest: str | None,
) -> tuple[bool, str | None, str | None]:
    if previous_locator is None:
        return True, None, None
    checked = validate_locator(previous_locator, owned_root)
    observed_tree = str(checked["tree_sha256"])
    observed_manifest = str(checked["manifest_sha256"])
    return (
        observed_tree == expected_tree and observed_manifest == expected_manifest,
        observed_tree,
        observed_manifest,
    )


def _base_receipt(
    *,
    destination: Path,
    staging: Path,
    fail_at: str | None,
    previous_tree: str | None,
    previous_manifest: str | None,
) -> dict[str, Any]:
    return {
        "receipt_schema_version": "1.0",
        "operation": "snapshot_to_immutable_candidate",
        "destination": str(destination),
        "owned_staging": str(staging),
        "requested_fail_at": fail_at,
        "previous_candidate_tree_sha256_before": previous_tree,
        "previous_candidate_manifest_sha256_before": previous_manifest,
        "previous_candidate_unchanged": False,
        "owned_staging_removed": False,
        "protected_or_foreign_path_removed": False,
        "partial_write_count": 0,
        "protected_roots_verification": "EXTERNAL_A_RUNNER_RECEIPT_REQUIRED",
        "production_eligible": False,
        "activation": False,
    }


def _previous_anchor(
    locator: Mapping[str, Any] | None,
    tree_sha256: str | None,
    manifest_sha256: str | None,
) -> dict[str, Any]:
    if locator is None:
        return {
            "exists": False,
            "locator": None,
            "tree_sha256": None,
            "manifest_sha256": None,
            "immutable": True,
        }
    return {
        "exists": True,
        "locator": str(locator["root"]),
        "tree_sha256": tree_sha256,
        "manifest_sha256": manifest_sha256,
        "immutable": True,
    }


def _machine_manifest(
    *,
    state: str,
    source_snapshot: Mapping[str, Any],
    profile: Mapping[str, Any],
    index_identity: Mapping[str, Any],
    previous: Mapping[str, Any],
    staging: Path,
    destination: Path,
    record_exact_set: list[dict[str, Any]],
    candidate_manifest_hash: str | None,
    candidate_tree_hash: str | None,
    rollback_receipt: Mapping[str, Any] | None,
    active_index_baseline: Mapping[str, Any],
) -> dict[str, Any]:
    identity = {
        "source_snapshot_hash": source_snapshot["content_hash"],
        "profile": profile,
        "index_identity": index_identity,
        "active_index_baseline": active_index_baseline,
        "previous": previous,
        "record_exact_set": record_exact_set,
        "destination": str(destination),
    }
    return {
        "schema_version": "1.0",
        "migration_id": "FIELD_VECTORS_MIGRATION_" + _sha256(canonical_json_bytes(identity)),
        "migration_state": state,
        "source_snapshot": {
            "object_type": "SourceSnapshot",
            "source_snapshot_id": source_snapshot["source_snapshot_id"],
            "content_hash": deepcopy(source_snapshot["content_hash"]),
        },
        "source_snapshot_canonical_sha256": _sha256(
            canonical_json_bytes(source_snapshot) + b"\n"
        ),
        "previous_candidate": deepcopy(dict(previous)),
        "candidate_manifest_ref": str(destination / "manifest.json"),
        "candidate_manifest_hash": candidate_manifest_hash,
        "candidate_tree_sha256": candidate_tree_hash,
        "owned_staging": str(staging),
        "rollback_target": deepcopy(dict(previous)) if previous["exists"] else None,
        "expected_record_count": len(record_exact_set),
        "record_exact_set": deepcopy(record_exact_set),
        "record_exact_set_sha256": _sha256(canonical_json_bytes(record_exact_set)),
        "profile": deepcopy(dict(profile)),
        "index_identity": deepcopy(dict(index_identity)),
        "active_index_baseline": deepcopy(dict(active_index_baseline)),
        "build_cutoff_at": index_identity["build_cutoff_at"],
        "partial_write_count": 0,
        "protected_roots_verification": "EXTERNAL_A_RUNNER_RECEIPT_REQUIRED",
        "activation": False,
        "rollback_receipt": (
            None if rollback_receipt is None else deepcopy(dict(rollback_receipt))
        ),
        "external_calls": 0,
        "cost_cny": 0,
        "production_mutations": 0,
        "git_mutations": 0,
    }


def migrate_candidate(
    records: Iterable[Mapping[str, Any]],
    profile: Mapping[str, Any],
    index_identity: Mapping[str, Any],
    output_root: str | os.PathLike[str],
    *,
    owned_root: str | os.PathLike[str],
    source_snapshot: Mapping[str, Any],
    active_index_baseline: Mapping[str, Any],
    previous_locator: Mapping[str, Any] | None = None,
    fail_at: str | None = None,
) -> dict[str, Any]:
    """Migrate through ``snapshot -> staging -> validate -> immutable``.

    All injectable failures occur before finalization.  Failure cleanup targets
    only the exact staging directory created by this invocation.  A prior
    immutable candidate is revalidated before promotion and on every rollback.
    """

    if fail_at is not None and fail_at not in FAIL_POINTS:
        raise ContractViolation(f"unsupported fail_at point: {fail_at}")
    checked_snapshot = validate_source_snapshot(source_snapshot)
    checked_profile = validate_candidate_profile(profile)
    checked_identity = validate_candidate_index_identity(index_identity)
    checked_active_baseline = validate_active_index_baseline(active_index_baseline)

    boundary = validate_run_root(owned_root)
    destination = ensure_owned_path(output_root, boundary, must_exist=False)
    parent = ensure_owned_path(destination.parent, boundary, must_exist=True, allow_root=True)
    if not parent.is_dir():
        raise PathBoundaryError("migration destination parent is not a directory")
    staging = parent / f".{destination.name}.migration-staging"
    staging = ensure_owned_path(staging, boundary, must_exist=False)

    checked_previous: dict[str, Any] | None = None
    previous_records: list[dict[str, Any]] = []
    if previous_locator is not None:
        checked_previous = validate_locator(previous_locator, boundary)
        previous_records = _read_previous_records(checked_previous)
        previous_tree = str(checked_previous["tree_sha256"])
        previous_manifest = str(checked_previous["manifest_sha256"])
    else:
        previous_tree = None
        previous_manifest = None

    incoming: list[dict[str, Any]] = []
    for ordinal, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ContractViolation(f"records[{ordinal}] must be an object")
        incoming.append(deepcopy(dict(record)))
    validated_incoming, _ = _deduplicate_records(
        incoming, checked_profile, checked_identity, checked_snapshot
    )
    predecessor_ids = _check_cross_candidate_identity(validated_incoming, previous_records)
    tombstone_transitions = _derive_tombstone_transitions(
        validated_incoming, previous_records
    )
    previous_anchor = _previous_anchor(
        checked_previous, previous_tree, previous_manifest
    )
    receipt = _base_receipt(
        destination=destination,
        staging=staging,
        fail_at=fail_at,
        previous_tree=previous_tree,
        previous_manifest=previous_manifest,
    )

    record_exact_set = make_record_exact_set(validated_incoming)
    candidate_manifest_hash: str | None = None
    candidate_tree_hash: str | None = None
    try:
        _fail_if_requested(fail_at, "after_snapshot")
        staging.mkdir(exist_ok=False)
        _fail_if_requested(fail_at, "after_staging_create")

        staged_locator = _materialize_candidate(
            validated_incoming,
            checked_profile,
            checked_identity,
            staging,
            boundary,
            source_snapshot=checked_snapshot,
            active_index_baseline=checked_active_baseline,
            predecessor_vector_ids=predecessor_ids,
            tombstone_transitions=tombstone_transitions,
            precreated_empty=True,
        )
        candidate_manifest_hash = str(staged_locator["manifest_sha256"])
        candidate_tree_hash = str(staged_locator["tree_sha256"])
        staged_manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
        record_exact_set = deepcopy(staged_manifest["record_exact_set"])
        _fail_if_requested(fail_at, "after_candidate_build")
        validate_locator(staged_locator, boundary)
        _fail_if_requested(fail_at, "after_validation")
        unchanged, observed_tree, observed_manifest = _previous_unchanged(
            checked_previous, boundary, previous_tree, previous_manifest
        )
        if not unchanged:
            raise IdentityConflict("previous immutable candidate changed before promotion")

        final_locator = deepcopy(staged_locator)
        final_locator["root"] = str(destination)
        manifest = _machine_manifest(
            state="validated",
            source_snapshot=checked_snapshot,
            profile=checked_profile,
            index_identity=checked_identity,
            previous=previous_anchor,
            staging=staging,
            destination=destination,
            record_exact_set=record_exact_set,
            candidate_manifest_hash=candidate_manifest_hash,
            candidate_tree_hash=candidate_tree_hash,
            rollback_receipt=None,
            active_index_baseline=checked_active_baseline,
        )
        success_receipt = {
            **receipt,
            "lifecycle_status": "completed",
            "verification_result": "PASS",
            "state": "immutable_candidate",
            "manifest": manifest,
            "locator": final_locator,
            "previous_candidate_tree_sha256_after": observed_tree,
            "previous_candidate_manifest_sha256_after": observed_manifest,
            "previous_candidate_unchanged": True,
            "owned_staging_removed": True,
            "owned_staging_promoted": True,
            "record_count": len(record_exact_set),
            "predecessor_record_count": len(previous_records),
            "tombstone_transition_count": len(tombstone_transitions),
            "predecessor_candidate_preserved": True,
        }
        _fail_if_requested(fail_at, "before_finalize")

        # On Windows an ordinary rename is exclusive: it fails when the
        # destination exists.  It is deliberately the last fallible operation;
        # no post-promotion validation can strand a partial successor.
        os.rename(staging, destination)
        return success_receipt
    except BaseException as exc:
        staging_removed = _safe_remove_staging(staging, boundary) if staging.exists() else True
        unchanged, observed_tree, observed_manifest = _previous_unchanged(
            checked_previous, boundary, previous_tree, previous_manifest
        )
        rollback_receipt = {
            "receipt_id": "FIELD_VECTORS_ROLLBACK_"
            + _sha256(
                canonical_json_bytes(
                    {
                        "destination": str(destination),
                        "failure_type": type(exc).__name__,
                        "previous_tree": previous_tree,
                    }
                )
            ),
            "reason": type(exc).__name__,
            "removed_staging_only": staging_removed,
            "rollback_target_unchanged": unchanged,
        }
        manifest = _machine_manifest(
            state="rolled_back" if staging_removed and unchanged else "failed",
            source_snapshot=checked_snapshot,
            profile=checked_profile,
            index_identity=checked_identity,
            previous=previous_anchor,
            staging=staging,
            destination=destination,
            record_exact_set=record_exact_set,
            candidate_manifest_hash=candidate_manifest_hash,
            candidate_tree_hash=candidate_tree_hash,
            rollback_receipt=rollback_receipt,
            active_index_baseline=checked_active_baseline,
        )
        failure_receipt = {
            **receipt,
            "lifecycle_status": "completed",
            "verification_result": "ERROR",
            "manifest": manifest,
            "rollback_receipt": rollback_receipt,
            "failure_type": type(exc).__name__,
            "failure_message": str(exc),
            "finalized_before_error": False,
            "owned_staging_removed": staging_removed,
            "previous_candidate_tree_sha256_after": observed_tree,
            "previous_candidate_manifest_sha256_after": observed_manifest,
            "previous_candidate_unchanged": unchanged,
            "destination_exists_after_error": destination.exists(),
            "partial_write_count": 0,
        }
        raise MigrationFailure(str(exc), receipt=failure_receipt) from exc


__all__ = [
    "FAIL_POINTS",
    "MigrationFailure",
    "migrate_candidate",
]
