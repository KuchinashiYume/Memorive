"""Frozen FIELD-VECTORS construction-A scenario library.

Each public evaluator returns exactly ``test_id``, ``expected``, ``observed``
and ``result``.  Importing this module is read-only; scenario directories are
created only when an evaluator is explicitly called by the construction-A
product runner or its one-test-per-criterion unittest facade.
"""

from __future__ import annotations

import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import mkdtemp
from typing import Any, Callable, Mapping
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from tests.modules.retrieval_weighting.field_vector_acceptance.suite import contract_adapter as api
from tests.modules.retrieval_weighting.current_field_records import stable_fields
from tests.modules.retrieval_weighting.field_vector_acceptance.validator.raw_subprocess import decode_utf8_strict, run_raw


_EXPECTED = {
    "A-002": "active self literature Card yields complete field records, candidate index and lineage",
    "A-003": "two independent builds with the same input/profile/version have equal canonical exact-set bytes and tree SHA",
    "A-004": "profile/index mixing count is zero and a new profile requires a new index",
    "A-005": "successful migration contains snapshot, immutable manifest, complete hashes and activation=false",
    "A-006": "injected migration failure rolls back owned staging and leaves previous candidate/protected roots unchanged",
    "A-007": "Card/field revision creates successor plus tombstone without overwriting prior record",
    "A-008": "pending, quarantined, missing and bad-state Cards write zero normal-index records",
    "A-009": "missing, unknown or conflicting doc_type fails closed and never defaults to literature",
    "A-010": "unknown ownership fails closed and entrusted data cannot leave local-only boundary",
    "A-011": "mixed profile, dimension, vector_type or index is rejected with partial_write=0",
    "A-012": "duplicate identity plus same hash is idempotent; duplicate identity plus different hash is rejected",
    "A-013": "broken lineage, source, content hash or artifact authority is rejected",
    "A-014": "path escape, reparse point, symlink, junction and foreign root are rejected",
    "A-015": "required, enum, identity, hash, lineage, namespace and invalidation schema mutations are rejected",
    "A-017": "external calls, credentials, cost, Git mutation, production mutation and activation are zero",
    "A-018": "raw-byte runner handles Chinese, empty streams, nonzero exit, no newline, CRLF, invalid UTF-8, timeout and exception fail closed",
}


def _evaluation(
    test_id: str,
    exercise: Callable[[], tuple[Mapping[str, Any], bool]],
) -> dict[str, Any]:
    try:
        observed, passed = exercise()
        result = "PASS" if passed else "FAIL"
        serializable_observed: Any = dict(observed)
    except Exception as exc:
        result = "FAIL"
        serializable_observed = {
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }
    return {
        "test_id": test_id,
        "expected": _EXPECTED[test_id],
        "observed": serializable_observed,
        "result": result,
    }


def _workspace(test_id: str) -> Path:
    test_root = api.field_vectors_test_root()
    test_root.mkdir(parents=True, exist_ok=True)
    api.ensure_owned_path(
        path=test_root,
        owned_root=api.ROOT,
        must_exist=True,
        allow_root=False,
    )
    return Path(mkdtemp(prefix=test_id.replace("-", "_") + "_", dir=test_root))


def _load_manifest(locator: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads((Path(str(locator["root"])) / "manifest.json").read_text(encoding="utf-8"))


def _write_new_json(path: Path, value: Any) -> None:
    """Persist an A-owned semantic carrier without allowing overwrite."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(api.canonical_json_bytes(value) + b"\n")


def _build_candidate(
    bundle: Mapping[str, Any],
    destination: Path,
    *,
    records: list[dict[str, Any]] | None = None,
    predecessor_vector_ids: list[str] | None = None,
    tombstone_transitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return api.build_candidate_index(
        records=(
            bundle["vectors"]["records"]
            if records is None
            else records
        ),
        profile=bundle["profile"],
        index_identity=bundle["index_identity"],
        output_root=destination,
        owned_root=api.ROOT,
        source_snapshot=bundle["snapshot"],
        active_index_baseline=api.active_index_baseline(),
        predecessor_vector_ids=predecessor_vector_ids or (),
        tombstone_transitions=tombstone_transitions or (),
    )


def _rehashed(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(value))
    payload.pop("content_hash", None)
    return api.make_hashed_payload(payload)


def _rejected(call: Callable[[], Any]) -> tuple[bool, str | None]:
    try:
        call()
    except Exception as exc:
        return True, type(exc).__name__
    return False, None


def _remove_test_link_or_empty_directory(path: Path) -> bool:
    """Remove only the test-owned link itself or a synthetic empty probe directory."""

    if not os.path.lexists(path):
        return True
    if path.is_symlink():
        path.unlink()
    else:
        os.rmdir(path)
    return not os.path.lexists(path)


def _schema_errors(schema_name: str, value: Any) -> list[str]:
    schema = api.load_root_json(f"10_contract/schemas/{schema_name}")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [error.message for error in validator.iter_errors(value)]


def _metadata_by_id(metadata_id: str) -> dict[str, Any]:
    rows = api.load_json("doc_type_metadata_public_safe.json")["records"]
    matches = [row for row in rows if row["metadata_id"] == metadata_id]
    if len(matches) != 1:
        raise AssertionError(f"metadata identity must be exact: {metadata_id}")
    return matches[0]


def _state_mutation(
    *, status: str | None, fixture_id: str, metadata_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    fixture = deepcopy(api.fixture_by_id("FX-ACTIVE-SELF-LITERATURE-R1"))
    fixture["fixture_id"] = fixture_id
    fixture["scenario"] = fixture_id.lower()
    fixture["doc_type_metadata_id"] = metadata_id
    if status is None:
        fixture["card"].pop("review_status", None)
    else:
        fixture["card"]["review_status"] = status
    card_hash = api.typed_payload_hash(fixture["card"])
    fixture["authority"]["content_hash"] = card_hash

    metadata = deepcopy(_metadata_by_id("DTM-FIX-001"))
    metadata["metadata_id"] = metadata_id
    metadata["card_content_hash"] = deepcopy(card_hash)
    metadata["artifact_content_hash"] = api.typed_payload_hash(
        {
            "artifact_ref": fixture["authority"]["card_artifact_ref"],
            "card_id": fixture["authority"]["card_id"],
            "card_revision": fixture["authority"]["revision"],
            "card_content_hash": card_hash,
        }
    )
    return fixture, metadata


def evaluate_A_002() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        workspace = _workspace("A-002")
        bundle = api.vector_bundle(["FX-ACTIVE-SELF-LITERATURE-R1"])
        records_bytes = api.canonical_jsonl_bytes(bundle["vectors"]["records"])
        oracle_rows = [
            json.loads(line)
            for line in (api.ORACLE_ROOT / "expected_records.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        oracle_manifest = json.loads(
            (api.ORACLE_ROOT / "field_expected_manifest.json").read_text(encoding="utf-8")
        )
        locator = _build_candidate(bundle, workspace / "candidate")
        _write_new_json(workspace / "candidate_locator.json", locator)
        manifest = _load_manifest(locator)
        candidate_records = (Path(locator["root"]) / "records.jsonl").read_bytes()
        schema_errors = []
        schema_errors.extend(_schema_errors("source_snapshot.schema.json", bundle["snapshot"]))
        for row in bundle["vectors"]["records"]:
            schema_errors.extend(_schema_errors("field_vector_record.schema.json", row))
        schema_errors.extend(_schema_errors("candidate_index_manifest.schema.json", manifest))
        complete_lineage = all(
            row["source_refs"]
            and row["provenance_refs"]
            and row["parent_refs"]
            and row["field_source_id"]
            and row["source_snapshot_id"] == bundle["snapshot"]["source_snapshot_id"]
            for row in bundle["vectors"]["records"]
        )
        observed = {
            "record_count": len(bundle["vectors"]["records"]),
            "oracle_record_count": oracle_manifest["expected_record_count"],
            # The oracle predates the current vector algorithm; see current_field_records.REBUILT_FIELDS.
            "oracle_stable_fields_match": stable_fields(bundle["vectors"]["records"]) == stable_fields(oracle_rows),
            "candidate_matches_build": candidate_records == records_bytes,
            "candidate_member_names": sorted(path.name for path in Path(locator["root"]).iterdir()),
            "complete_lineage": complete_lineage,
            "schema_error_count": len(schema_errors),
            "candidate_state": manifest["state"],
            "activation": manifest["activation"],
        }
        passed = (
            observed["record_count"] == 17
            and observed["record_count"] == observed["oracle_record_count"]
            and observed["oracle_stable_fields_match"]
            and observed["candidate_matches_build"]
            and observed["candidate_member_names"]
            == sorted(api.load_json("expected_outcomes_v1.json")["reference_oracle_contract"]["expected_candidate_member_names"])
            and observed["complete_lineage"]
            and observed["schema_error_count"] == 0
            and observed["candidate_state"] == "immutable_candidate"
            and observed["activation"] is False
        )
        return observed, passed

    return _evaluation("A-002", exercise)


def evaluate_A_003() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        workspace = _workspace("A-003")
        bundle_a = api.vector_bundle(["FX-ACTIVE-SELF-LITERATURE-R1"])
        bundle_b = api.vector_bundle(["FX-ACTIVE-SELF-LITERATURE-R1"])
        locator_a = _build_candidate(bundle_a, workspace / "candidate_a")
        locator_b = _build_candidate(bundle_b, workspace / "candidate_b")
        root_a, root_b = Path(locator_a["root"]), Path(locator_b["root"])
        names_a = sorted(path.name for path in root_a.iterdir())
        names_b = sorted(path.name for path in root_b.iterdir())
        byte_equal = names_a == names_b and all(
            (root_a / name).read_bytes() == (root_b / name).read_bytes()
            for name in names_a
        )
        manifest_a, manifest_b = _load_manifest(locator_a), _load_manifest(locator_b)
        exact_set_equal = (
            api.canonical_json_bytes(manifest_a["record_exact_set"])
            == api.canonical_json_bytes(manifest_b["record_exact_set"])
        )
        observed = {
            "canonical_vector_bytes_equal": api.canonical_jsonl_bytes(bundle_a["vectors"]["records"])
            == api.canonical_jsonl_bytes(bundle_b["vectors"]["records"]),
            "candidate_all_member_bytes_equal": byte_equal,
            "canonical_exact_set_bytes_equal": exact_set_equal,
            "tree_sha256_equal": locator_a["tree_sha256"] == locator_b["tree_sha256"],
            "tree_sha256": locator_a["tree_sha256"],
        }
        return observed, all(
            value is True for key, value in observed.items() if key != "tree_sha256"
        )

    return _evaluation("A-003", exercise)


def evaluate_A_004() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        workspace = _workspace("A-004")
        base = api.vector_bundle(["FX-ACTIVE-SELF-LITERATURE-R1"])
        alternate = api.vector_bundle(
            ["FX-ACTIVE-SELF-LITERATURE-R1"],
            profile_id="offline:fixture-alt-v1-d4",
            index_id="offline:index-alt-v1-d4",
            collection_id="offline:candidate-alt-v1-d4",
        )
        locator_base = _build_candidate(base, workspace / "base")
        locator_alt = _build_candidate(alternate, workspace / "alternate")
        mixed_path = workspace / "mixed"
        mixed_rejected, mixed_error = _rejected(
            lambda: _build_candidate(
                base,
                mixed_path,
                records=base["vectors"]["records"] + alternate["vectors"]["records"],
            )
        )
        base_ids = {row["vector_id"] for row in base["vectors"]["records"]}
        alt_ids = {row["vector_id"] for row in alternate["vectors"]["records"]}
        observed = {
            "profile_ids_distinct": base["profile"]["embedding_profile_id"]
            != alternate["profile"]["embedding_profile_id"],
            "index_ids_distinct": base["index_identity"]["index_id"]
            != alternate["index_identity"]["index_id"],
            "collections_distinct": base["index_identity"]["collection_id"]
            != alternate["index_identity"]["collection_id"],
            "vector_id_intersection_count": len(base_ids & alt_ids),
            "candidate_tree_hashes_distinct": locator_base["tree_sha256"]
            != locator_alt["tree_sha256"],
            "mixed_rejected": mixed_rejected,
            "mixed_error_type": mixed_error,
            "partial_write_count": int(mixed_path.exists()),
        }
        passed = (
            observed["profile_ids_distinct"]
            and observed["index_ids_distinct"]
            and observed["collections_distinct"]
            and observed["vector_id_intersection_count"] == 0
            and observed["candidate_tree_hashes_distinct"]
            and observed["mixed_rejected"]
            and observed["partial_write_count"] == 0
        )
        return observed, passed

    return _evaluation("A-004", exercise)


def evaluate_A_005() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        workspace = _workspace("A-005")
        bundle = api.vector_bundle(["FX-ACTIVE-SELF-LITERATURE-R1"])
        baseline = api.active_index_baseline()
        receipt = api.migrate_candidate(
            records=bundle["vectors"]["records"],
            profile=bundle["profile"],
            index_identity=bundle["index_identity"],
            output_root=workspace / "migrated",
            owned_root=api.ROOT,
            source_snapshot=bundle["snapshot"],
            active_index_baseline=baseline,
            previous_locator=None,
            fail_at=None,
        )
        locator = api.validate_locator(receipt["locator"], api.ROOT)
        manifest = receipt["manifest"]
        _write_new_json(workspace / "candidate_locator.json", locator)
        _write_new_json(workspace / "migration_manifest.json", manifest)
        schema_errors = _schema_errors("migration_manifest.schema.json", manifest)
        observed = {
            "verification_result": receipt["verification_result"],
            "migration_state": manifest["migration_state"],
            "snapshot_id_bound": manifest["source_snapshot"]["source_snapshot_id"]
            == bundle["snapshot"]["source_snapshot_id"],
            "snapshot_hash_bound": manifest["source_snapshot"]["content_hash"]
            == bundle["snapshot"]["content_hash"],
            "record_exact_set_count": len(manifest["record_exact_set"]),
            "record_count": receipt["record_count"],
            "candidate_manifest_hash_present": bool(manifest["candidate_manifest_hash"]),
            "candidate_tree_hash_present": bool(manifest["candidate_tree_sha256"]),
            "locator_tree_hash_matches": locator["tree_sha256"]
            == manifest["candidate_tree_sha256"],
            "active_index_baseline_bound": manifest["active_index_baseline"] == baseline,
            "partial_write_count": receipt["partial_write_count"],
            "activation": manifest["activation"],
            "schema_error_count": len(schema_errors),
        }
        passed = (
            observed["verification_result"] == "PASS"
            and observed["migration_state"] == "validated"
            and observed["snapshot_id_bound"]
            and observed["snapshot_hash_bound"]
            and observed["record_exact_set_count"] == observed["record_count"] == 17
            and observed["candidate_manifest_hash_present"]
            and observed["candidate_tree_hash_present"]
            and observed["locator_tree_hash_matches"]
            and observed["active_index_baseline_bound"]
            and observed["partial_write_count"] == 0
            and observed["activation"] is False
            and observed["schema_error_count"] == 0
        )
        return observed, passed

    return _evaluation("A-005", exercise)


def evaluate_A_006() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        workspace = _workspace("A-006")
        bundle = api.vector_bundle(["FX-ACTIVE-SELF-LITERATURE-R1"])
        previous = _build_candidate(bundle, workspace / "previous")
        previous_tree_before = previous["tree_sha256"]
        baseline = api.active_index_baseline()
        destination = workspace / "failed_successor"
        failure_type = api.migration_failure_type()
        try:
            api.migrate_candidate(
                records=bundle["vectors"]["records"],
                profile=bundle["profile"],
                index_identity=bundle["index_identity"],
                output_root=destination,
                owned_root=api.ROOT,
                source_snapshot=bundle["snapshot"],
                active_index_baseline=baseline,
                previous_locator=previous,
                fail_at="after_candidate_build",
            )
            raise AssertionError("injected migration failure did not fail")
        except failure_type as exc:
            receipt = exc.receipt
        _write_new_json(workspace / "rollback_receipt.json", receipt["rollback_receipt"])
        previous_after = api.validate_locator(previous, api.ROOT)
        staging = Path(receipt["owned_staging"])
        observed = {
            "verification_result": receipt["verification_result"],
            "migration_state": receipt["manifest"]["migration_state"],
            "failure_type": receipt["failure_type"],
            "previous_tree_unchanged": previous_after["tree_sha256"] == previous_tree_before,
            "previous_candidate_unchanged": receipt["previous_candidate_unchanged"],
            "owned_staging_removed": receipt["owned_staging_removed"] and not staging.exists(),
            "removed_staging_only": receipt["rollback_receipt"]["removed_staging_only"],
            "destination_exists_after_error": receipt["destination_exists_after_error"],
            "partial_write_count": receipt["partial_write_count"],
            "protected_or_foreign_path_removed": receipt["protected_or_foreign_path_removed"],
            "active_index_baseline_bound": receipt["manifest"]["active_index_baseline"] == baseline,
            "external_protected_root_check": receipt["protected_roots_verification"],
        }
        passed = (
            observed["verification_result"] == "ERROR"
            and observed["migration_state"] == "rolled_back"
            and observed["previous_tree_unchanged"]
            and observed["previous_candidate_unchanged"]
            and observed["owned_staging_removed"]
            and observed["removed_staging_only"]
            and observed["destination_exists_after_error"] is False
            and observed["partial_write_count"] == 0
            and observed["protected_or_foreign_path_removed"] is False
            and observed["active_index_baseline_bound"]
            and observed["external_protected_root_check"]
            == "EXTERNAL_A_RUNNER_RECEIPT_REQUIRED"
        )
        return observed, passed

    return _evaluation("A-006", exercise)


def evaluate_A_007() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        workspace = _workspace("A-007")
        r1 = api.vector_bundle(["FX-ACTIVE-SELF-LITERATURE-R1"])
        r2 = api.vector_bundle(["FX-ACTIVE-SELF-LITERATURE-R2-SUCCESSOR"])
        combined = api.vector_bundle(
            [
                "FX-ACTIVE-SELF-LITERATURE-R1",
                "FX-ACTIVE-SELF-LITERATURE-R2-SUCCESSOR",
            ]
        )
        previous = _build_candidate(r1, workspace / "revision_1")
        prior_tree = previous["tree_sha256"]
        successor = api.migrate_candidate(
            records=r2["vectors"]["records"],
            profile=r2["profile"],
            index_identity=r2["index_identity"],
            output_root=workspace / "revision_2",
            owned_root=api.ROOT,
            source_snapshot=r2["snapshot"],
            active_index_baseline=api.active_index_baseline(),
            previous_locator=previous,
            fail_at=None,
        )
        previous_after = api.validate_locator(previous, api.ROOT)
        successor_manifest = _load_manifest(successor["locator"])
        tombstones_path = Path(successor["locator"]["root"]) / "tombstones.jsonl"
        tombstones = [json.loads(line) for line in tombstones_path.read_text(encoding="utf-8").splitlines()]
        tombstone_schema_errors = []
        for row in tombstones:
            tombstone_schema_errors.extend(
                _schema_errors("field_vector_tombstone.schema.json", row)
            )
        observed = {
            "combined_successor_count": sum(
                row["supersedes"] is not None for row in combined["vectors"]["records"]
            ),
            "combined_tombstone_count": len(combined["vectors"]["tombstones"]),
            "migration_tombstone_count": successor_manifest["tombstone_count"],
            "migration_predecessor_count": len(successor_manifest["predecessor_vector_ids"]),
            "tombstone_exact_set_count": len(successor_manifest["tombstone_exact_set"]),
            "all_tombstones_point_to_successor": all(
                row["invalidation_state"] == "tombstoned"
                and row["reason"] == "superseded"
                and row["invalidated_by"] is not None
                for row in tombstones
            ),
            "prior_tree_unchanged": previous_after["tree_sha256"] == prior_tree,
            "predecessor_preserved": successor["predecessor_candidate_preserved"],
            "overwrite_count": combined["vectors"]["overwrite_count"],
            "tombstone_schema_error_count": len(tombstone_schema_errors),
        }
        passed = (
            observed["combined_successor_count"] == 17
            and observed["combined_tombstone_count"] == 17
            and observed["migration_tombstone_count"] == 17
            and observed["migration_predecessor_count"] == 17
            and observed["tombstone_exact_set_count"] == 17
            and observed["all_tombstones_point_to_successor"]
            and observed["prior_tree_unchanged"]
            and observed["predecessor_preserved"]
            and observed["overwrite_count"] == 0
            and observed["tombstone_schema_error_count"] == 0
        )
        return observed, passed

    return _evaluation("A-007", exercise)


def evaluate_A_008() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        missing, missing_metadata = _state_mutation(
            status=None,
            fixture_id="FX-MISSING-STATE-SELF-LITERATURE-R1",
            metadata_id="DTM-FIX-MISSING-STATE-001",
        )
        bad, bad_metadata = _state_mutation(
            status="bad-state",
            fixture_id="FX-BAD-STATE-SELF-LITERATURE-R1",
            metadata_id="DTM-FIX-BAD-STATE-001",
        )
        metadata = api.load_json("doc_type_metadata_public_safe.json")["records"]
        snapshot = api.source_snapshot(
            [],
            records=[
                api.fixture_by_id("FX-PENDING-SELF-LITERATURE-R1"),
                api.fixture_by_id("FX-QUARANTINED-SELF-LITERATURE-R1"),
                missing,
                bad,
            ],
            metadata=[*metadata, missing_metadata, bad_metadata],
        )
        bundle = api.vector_bundle([], source=snapshot)
        reasons = [row["reason"] for row in snapshot["rejections"]]
        observed = {
            "normal_index_record_count": len(bundle["vectors"]["records"]),
            "rejection_count": len(snapshot["rejections"]),
            "rejection_reasons": reasons,
            "rejected_fixture_ids": sorted(
                row["fixture_id"] for row in snapshot["rejections"]
            ),
        }
        passed = (
            observed["normal_index_record_count"] == 0
            and observed["rejection_count"] == 4
            and reasons == ["REVIEW_STATUS_NOT_ACTIVE"] * 4
            and len(observed["rejected_fixture_ids"]) == 4
        )
        return observed, passed

    return _evaluation("A-008", exercise)


def evaluate_A_009() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        conflict = deepcopy(api.fixture_by_id("FX-ACTIVE-SELF-LITERATURE-R1"))
        conflict["fixture_id"] = "FX-ACTIVE-SELF-CONFLICTING-DOC-TYPE-R1"
        conflict["scenario"] = "active_self_conflicting_doc_type"
        conflict["doc_type_metadata_id"] = "DTM-FIX-CONFLICT-001"
        snapshot = api.source_snapshot(
            [],
            records=[
                api.fixture_by_id("FX-ACTIVE-SELF-MISSING-DOC-TYPE-R1"),
                api.fixture_by_id("FX-ACTIVE-SELF-UNKNOWN-DOC-TYPE-R1"),
                conflict,
            ],
        )
        bundle = api.vector_bundle([], source=snapshot)
        reasons = [row["reason"] for row in snapshot["rejections"]]
        observed = {
            "normal_index_record_count": len(bundle["vectors"]["records"]),
            "rejection_count": len(reasons),
            "rejection_reason_set": sorted(set(reasons)),
            "literature_default_count": sum(
                row.get("doc_type") == "literature" for row in snapshot["records"]
            ),
        }
        passed = (
            observed["normal_index_record_count"] == 0
            and observed["rejection_count"] == 3
            and set(reasons)
            == {"DOC_TYPE_MISSING", "DOC_TYPE_UNKNOWN", "DOC_TYPE_CONFLICT"}
            and observed["literature_default_count"] == 0
        )
        return observed, passed

    return _evaluation("A-009", exercise)


def evaluate_A_010() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        workspace = _workspace("A-010")
        unknown_snapshot = api.source_snapshot(["FX-ACTIVE-UNKNOWN-OWNERSHIP-R1"])
        unknown_bundle = api.vector_bundle([], source=unknown_snapshot)
        entrusted = api.vector_bundle(
            ["FX-ACTIVE-ENTRUSTED-NOTE-R1"], entrusted=True
        )
        entrusted_locator = _build_candidate(entrusted, workspace / "entrusted_local")
        entrusted_manifest = _load_manifest(entrusted_locator)
        ordinary_path = workspace / "entrusted_ordinary"
        ordinary = {
            **entrusted,
            "profile": api.offline_profile(),
            "index_identity": api.index_identity(),
        }
        ordinary_rejected, ordinary_error = _rejected(
            lambda: _build_candidate(ordinary, ordinary_path)
        )
        local_rows = entrusted["vectors"]["records"]
        observed = {
            "unknown_record_count": len(unknown_bundle["vectors"]["records"]),
            "unknown_reason": unknown_snapshot["rejections"][0]["reason"],
            "entrusted_record_count": len(local_rows),
            "all_profile_local_only": all(
                row["embedding_profile_id"].startswith("local-only:") for row in local_rows
            ),
            "all_index_local_only": all(
                row["index_id"].startswith("local-only:") for row in local_rows
            ),
            "all_collection_local_only": all(
                row["collection_id"].startswith("local-only:") for row in local_rows
            ),
            "ownership_partition": entrusted_manifest["ownership_partition"],
            "ordinary_partition_rejected": ordinary_rejected,
            "ordinary_partition_error_type": ordinary_error,
            "ordinary_partial_write_count": int(ordinary_path.exists()),
        }
        passed = (
            observed["unknown_record_count"] == 0
            and observed["unknown_reason"] == "DATA_OWNERSHIP_UNKNOWN"
            and observed["entrusted_record_count"] > 0
            and observed["all_profile_local_only"]
            and observed["all_index_local_only"]
            and observed["all_collection_local_only"]
            and observed["ownership_partition"] == "entrusted_local_only"
            and observed["ordinary_partition_rejected"]
            and observed["ordinary_partial_write_count"] == 0
        )
        return observed, passed

    return _evaluation("A-010", exercise)


def evaluate_A_011() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        workspace = _workspace("A-011")
        base = api.vector_bundle(["FX-ACTIVE-SELF-LITERATURE-R1"])
        profile_alt = api.vector_bundle(
            ["FX-ACTIVE-SELF-LITERATURE-R1"],
            profile_id="offline:fixture-alt-v1-d4",
            index_id="offline:index-alt-v1-d4",
            collection_id="offline:candidate-alt-v1-d4",
        )
        dimension_alt = api.vector_bundle(
            ["FX-ACTIVE-SELF-LITERATURE-R1"], dimension=8
        )
        index_alt = api.vector_bundle(
            ["FX-ACTIVE-SELF-LITERATURE-R1"],
            index_id="offline:index-other-v1-d4",
            collection_id="offline:candidate-other-v1-d4",
        )
        bad_vector_type = deepcopy(base["vectors"]["records"])
        bad_vector_type[0]["vector_type"] = "chunk"
        bad_vector_type[0] = _rehashed(bad_vector_type[0])
        cases = {
            "profile": profile_alt["vectors"]["records"],
            "dimension": dimension_alt["vectors"]["records"],
            "vector_type": bad_vector_type,
            "index": index_alt["vectors"]["records"],
        }
        rejected: dict[str, bool] = {}
        errors: dict[str, str | None] = {}
        partial_writes: dict[str, int] = {}
        for name, records in cases.items():
            destination = workspace / name
            rejected[name], errors[name] = _rejected(
                lambda records=records, destination=destination: _build_candidate(
                    base, destination, records=records
                )
            )
            partial_writes[name] = int(destination.exists())
        observed = {
            "rejected": rejected,
            "error_types": errors,
            "partial_writes": partial_writes,
            "rejected_count": sum(rejected.values()),
            "partial_write_count": sum(partial_writes.values()),
        }
        return observed, (
            set(rejected) == {"profile", "dimension", "vector_type", "index"}
            and all(rejected.values())
            and observed["partial_write_count"] == 0
        )

    return _evaluation("A-011", exercise)


def evaluate_A_012() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        workspace = _workspace("A-012")
        bundle = api.vector_bundle(["FX-ACTIVE-SELF-LITERATURE-R1"])
        records = bundle["vectors"]["records"]
        same_locator = _build_candidate(
            bundle,
            workspace / "same_hash",
            records=[*records, deepcopy(records[0])],
        )
        same_manifest = _load_manifest(same_locator)
        conflict = deepcopy(records[0])
        conflict["supersedes"] = records[1]["vector_id"]
        conflict = _rehashed(conflict)
        different_path = workspace / "different_hash"
        different_rejected, different_error = _rejected(
            lambda: _build_candidate(
                bundle,
                different_path,
                records=[*records, conflict],
            )
        )
        observed = {
            "same_hash_record_count": same_manifest["record_count"],
            "idempotent_duplicate_count": same_manifest["idempotent_duplicate_count"],
            "different_hash_rejected": different_rejected,
            "different_hash_error_type": different_error,
            "different_hash_partial_write_count": int(different_path.exists()),
            "same_vector_id": conflict["vector_id"] == records[0]["vector_id"],
            "different_content_hash": conflict["content_hash"] != records[0]["content_hash"],
        }
        passed = (
            observed["same_hash_record_count"] == 17
            and observed["idempotent_duplicate_count"] == 1
            and observed["different_hash_rejected"]
            and observed["different_hash_partial_write_count"] == 0
            and observed["same_vector_id"]
            and observed["different_content_hash"]
        )
        return observed, passed

    return _evaluation("A-012", exercise)


def evaluate_A_013() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        bundle = api.vector_bundle(["FX-ACTIVE-SELF-LITERATURE-R1"])
        base_record = bundle["vectors"]["records"][0]
        base_source = bundle["snapshot"]["records"][0]

        broken_lineage = deepcopy(base_record)
        broken_lineage["source_refs"] = []
        broken_lineage = _rehashed(broken_lineage)
        lineage_rejected, lineage_error = _rejected(
            lambda: api.verify_field_vector_record(broken_lineage)
        )

        broken_source = deepcopy(base_source)
        broken_source["value_hash"]["value"] = "0" * 64
        broken_source = _rehashed(broken_source)
        source_rejected, source_error = _rejected(
            lambda: api.verify_field_source_item(broken_source, bundle["snapshot"])
        )

        # A stale but well-formed content hash is accepted on purpose (own data corrected by
        # hand stays readable); a missing one is still refused.
        broken_hash = deepcopy(base_record)
        broken_hash.pop("content_hash")
        hash_rejected, hash_error = _rejected(
            lambda: api.verify_field_vector_record(broken_hash)
        )

        broken_authority = deepcopy(api.fixture_by_id("FX-ACTIVE-SELF-LITERATURE-R1"))
        broken_authority["authority"]["content_hash"]["value"] = "F" * 64
        authority_snapshot = api.source_snapshot([], records=[broken_authority])
        observed = {
            "lineage_rejected": lineage_rejected,
            "lineage_error_type": lineage_error,
            "source_rejected": source_rejected,
            "source_error_type": source_error,
            "content_hash_rejected": hash_rejected,
            "content_hash_error_type": hash_error,
            "artifact_authority_record_count": len(authority_snapshot["records"]),
            "artifact_authority_rejection_count": len(authority_snapshot["rejections"]),
            "artifact_authority_reason": authority_snapshot["rejections"][0]["reason"],
        }
        passed = (
            observed["lineage_rejected"]
            and observed["source_rejected"]
            and observed["content_hash_rejected"]
            and observed["artifact_authority_record_count"] == 0
            and observed["artifact_authority_rejection_count"] == 1
        )
        return observed, passed

    return _evaluation("A-013", exercise)


def evaluate_A_014() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        workspace = _workspace("A-014")
        # Point both real link types outside the governed run.  No target is
        # created or modified; only the link itself exists under the test root.
        target = api.ROOT.parent
        escape = api.ROOT / "FIELD_VECTORS_TEST_ROOT" / ".." / ".." / "escape"
        foreign = api.ROOT.parent / "FIELD_VECTORS_FOREIGN_ROOT"
        escape_rejected, escape_error = _rejected(
            lambda: api.ensure_owned_path(
                path=escape, owned_root=api.ROOT, must_exist=False
            )
        )
        foreign_rejected, foreign_error = _rejected(
            lambda: api.ensure_owned_path(
                path=foreign, owned_root=api.ROOT, must_exist=False
            )
        )

        symlink = workspace / "symlink_escape"
        symlink_probe = symlink
        symlink_mode = "actual"
        try:
            os.symlink(target, symlink, target_is_directory=True)
            symlink_rejected, symlink_error = _rejected(
                lambda: api.ensure_owned_path(
                    path=symlink, owned_root=api.ROOT, must_exist=True
                )
            )
        except OSError:
            symlink_mode = "branch_probe"
            synthetic = workspace / "synthetic_symlink_component"
            synthetic.mkdir()
            symlink_probe = synthetic
            import retrieval_weighting.locator as locator_module

            original = locator_module._is_reparse_point
            with patch.object(
                locator_module,
                "_is_reparse_point",
                side_effect=lambda path: (
                    True if Path(path) == synthetic else original(Path(path))
                ),
            ):
                symlink_rejected, symlink_error = _rejected(
                    lambda: api.ensure_owned_path(
                        path=synthetic, owned_root=api.ROOT, must_exist=True
                    )
                )

        junction = workspace / "junction_escape"
        junction_probe = junction
        junction_mode = "actual"
        if os.name == "nt":
            command = f'mklink /J "{junction}" "{target}"'
            creation = subprocess.run(
                ["cmd.exe", "/d", "/s", "/c", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        else:
            creation = None
        if creation is not None and creation.returncode == 0 and junction.exists():
            junction_rejected, junction_error = _rejected(
                lambda: api.ensure_owned_path(
                    path=junction, owned_root=api.ROOT, must_exist=True
                )
            )
        else:
            junction_mode = "branch_probe"
            synthetic_junction = workspace / "synthetic_junction_component"
            synthetic_junction.mkdir()
            junction_probe = synthetic_junction
            import retrieval_weighting.locator as locator_module

            original = locator_module._is_reparse_point
            with patch.object(
                locator_module,
                "_is_reparse_point",
                side_effect=lambda path: (
                    True if Path(path) == synthetic_junction else original(Path(path))
                ),
            ):
                junction_rejected, junction_error = _rejected(
                    lambda: api.ensure_owned_path(
                        path=synthetic_junction,
                        owned_root=api.ROOT,
                        must_exist=True,
                    )
                )
        observed = {
            "path_escape_rejected": escape_rejected,
            "path_escape_error_type": escape_error,
            "foreign_root_rejected": foreign_rejected,
            "foreign_root_error_type": foreign_error,
            "symlink_rejected": symlink_rejected,
            "symlink_error_type": symlink_error,
            "symlink_probe_mode": symlink_mode,
            "junction_rejected": junction_rejected,
            "junction_error_type": junction_error,
            "junction_probe_mode": junction_mode,
            "reparse_point_rejected": junction_rejected,
            "symlink_probe_removed": _remove_test_link_or_empty_directory(symlink_probe),
            "junction_probe_removed": _remove_test_link_or_empty_directory(junction_probe),
        }
        passed = all(
            (
                observed["path_escape_rejected"],
                observed["foreign_root_rejected"],
                observed["symlink_rejected"],
                observed["junction_rejected"],
                observed["reparse_point_rejected"],
                observed["symlink_probe_removed"],
                observed["junction_probe_removed"],
            )
        )
        return observed, passed

    return _evaluation("A-014", exercise)


def evaluate_A_015() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        schema_paths = sorted((api.ROOT / "10_contract" / "schemas").glob("*.schema.json"))
        for path in schema_paths:
            Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
        fixture_schema_errors = _schema_errors(
            "source_fixture_pack.schema.json",
            api.load_json("source_snapshot_public_safe.json"),
        )
        bundle = api.vector_bundle(["FX-ACTIVE-SELF-LITERATURE-R1"])
        base = bundle["vectors"]["records"][0]
        baseline_schema_errors = _schema_errors("field_vector_record.schema.json", base)

        mutations: dict[str, dict[str, Any]] = {}
        required = deepcopy(base)
        required.pop("field_path")
        mutations["required"] = _rehashed(required)
        enum = deepcopy(base)
        enum["doc_type"] = "dataset"
        mutations["enum"] = _rehashed(enum)
        identity = deepcopy(base)
        identity["vector_id"] = "fv1_" + "0" * 64
        mutations["identity"] = _rehashed(identity)
        damaged_hash = deepcopy(base)
        damaged_hash["content_hash"]["value"] = "0" * 63
        mutations["hash"] = damaged_hash
        lineage = deepcopy(base)
        lineage["parent_refs"] = []
        mutations["lineage"] = _rehashed(lineage)
        namespace = deepcopy(base)
        namespace["embedding_profile_id"] = "local-only:fixture-v1-d4"
        namespace["index_id"] = "local-only:index-v1-d4"
        namespace["collection_id"] = "local-only:candidate-v1-d4"
        mutations["namespace"] = _rehashed(namespace)
        invalidation = deepcopy(base)
        invalidation["invalidated_by"] = bundle["vectors"]["records"][1]["vector_id"]
        mutations["invalidation"] = _rehashed(invalidation)

        rejected: dict[str, bool] = {}
        errors: dict[str, str | None] = {}
        schema_rejected: dict[str, bool] = {}
        for name, mutation in mutations.items():
            rejected[name], errors[name] = _rejected(
                lambda mutation=mutation: api.verify_field_vector_record(mutation)
            )
            schema_rejected[name] = bool(
                _schema_errors("field_vector_record.schema.json", mutation)
            )
        observed = {
            "schema_file_count": len(schema_paths),
            "fixture_schema_error_count": len(fixture_schema_errors),
            "baseline_record_schema_error_count": len(baseline_schema_errors),
            "runtime_rejected": rejected,
            "runtime_error_types": errors,
            "schema_rejected": schema_rejected,
            "runtime_rejected_count": sum(rejected.values()),
        }
        passed = (
            observed["schema_file_count"] == 7
            and observed["fixture_schema_error_count"] == 0
            and observed["baseline_record_schema_error_count"] == 0
            and set(rejected)
            == {
                "required",
                "enum",
                "identity",
                "hash",
                "lineage",
                "namespace",
                "invalidation",
            }
            and all(rejected.values())
            and schema_rejected["required"]
            and schema_rejected["enum"]
            and schema_rejected["hash"]
        )
        return observed, passed

    return _evaluation("A-015", exercise)


def evaluate_A_017() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        workspace = _workspace("A-017")
        bundle = api.vector_bundle(["FX-ACTIVE-SELF-LITERATURE-R1"])
        baseline = api.active_index_baseline()
        locator = _build_candidate(bundle, workspace / "candidate")
        manifest = _load_manifest(locator)

        forbidden_modules = {
            "requests",
            "httpx",
            "urllib3",
            "openai",
            "anthropic",
            "boto3",
            "socket",
        }
        credential_names = {
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "API_KEY",
            "ACCESS_TOKEN",
            "SECRET_KEY",
            "PASSWORD",
        }
        imported_forbidden: list[str] = []
        credential_accesses: list[str] = []
        credential_literals: list[str] = []
        for path in sorted((api.ROOT / "10_candidate" / "retrieval_weighting").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".", 1)[0]
                        if top in forbidden_modules:
                            imported_forbidden.append(f"{path.name}:{top}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".", 1)[0]
                    if top in forbidden_modules:
                        imported_forbidden.append(f"{path.name}:{top}")
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "getenv":
                        credential_accesses.append(f"{path.name}:getenv")
                elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
                    if node.value.attr == "environ":
                        credential_accesses.append(f"{path.name}:environ")
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value in credential_names:
                        credential_literals.append(f"{path.name}:{node.value}")

        side_effects = bundle["vectors"]["side_effects"]
        zero_fields = {
            "external_calls": manifest["external_calls"],
            "cost_cny": manifest["cost_cny"],
            "production_mutations": manifest["production_mutations"],
            "git_mutations": manifest["git_mutations"],
        }
        observed = {
            "vector_side_effects": side_effects,
            "candidate_zero_fields": zero_fields,
            "candidate_activation": manifest["activation"],
            "candidate_production_eligible": manifest["production_eligible"],
            "active_index_baseline_unchanged": manifest["active_index_baseline"] == baseline,
            "forbidden_network_import_count": len(imported_forbidden),
            "forbidden_network_imports": imported_forbidden,
            "credential_access_count": len(credential_accesses),
            "credential_accesses": credential_accesses,
            "credential_literal_count": len(credential_literals),
            "credential_literals": credential_literals,
        }
        passed = (
            side_effects
            == {
                "external_calls": 0,
                "cost_cny": 0,
                "production_mutations": 0,
                "git_mutations": 0,
                "activation": False,
            }
            and all(value == 0 for value in zero_fields.values())
            and observed["candidate_activation"] is False
            and observed["candidate_production_eligible"] is False
            and observed["active_index_baseline_unchanged"]
            and observed["forbidden_network_import_count"] == 0
            and observed["credential_access_count"] == 0
            and observed["credential_literal_count"] == 0
        )
        return observed, passed

    return _evaluation("A-017", exercise)


def evaluate_A_018() -> dict[str, Any]:
    def exercise() -> tuple[Mapping[str, Any], bool]:
        workspace = _workspace("A-018")

        def invoke(case: str, code: str, *, timeout: float = 3.0):
            return run_raw(
                [sys.executable, "-B", "-c", code],
                cwd=workspace,
                stdout_path=workspace / f"{case}.stdout.bin",
                stderr_path=workspace / f"{case}.stderr.bin",
                allowed_output_root=workspace,
                timeout_seconds=timeout,
            )

        cases = {
            "chinese": (r"import sys;sys.stdout.buffer.write('\u4e2d\u6587'.encode('utf-8'))", 0),
            "empty": ("pass", 0),
            "nonzero": ("import sys;sys.stderr.buffer.write(b'bad');raise SystemExit(7)", 7),
            "no_lf": ("import sys;sys.stdout.buffer.write(b'no-newline')", 0),
            "crlf": ("import sys;sys.stdout.buffer.write(b'a\\r\\nb\\r\\n')", 0),
            "invalid_utf8": ("import sys;sys.stdout.buffer.write(bytes([255,254]))", 0),
        }
        receipts = {name: invoke(name, code) for name, (code, _) in cases.items()}
        base_receipts_ok = all(
            receipts[name].exit_code == expected_exit
            and receipts[name].timed_out is False
            and receipts[name].start_error_type is None
            for name, (_, expected_exit) in cases.items()
        )
        chinese_ok = decode_utf8_strict(
            Path(receipts["chinese"].stdout_path).read_bytes()
        ) == "\u4e2d\u6587"
        empty_ok = Path(receipts["empty"].stdout_path).read_bytes() == b""
        no_lf_ok = Path(receipts["no_lf"].stdout_path).read_bytes() == b"no-newline"
        crlf_ok = Path(receipts["crlf"].stdout_path).read_bytes() == b"a\r\nb\r\n"
        invalid_utf8_rejected, invalid_utf8_error = _rejected(
            lambda: decode_utf8_strict(
                Path(receipts["invalid_utf8"].stdout_path).read_bytes()
            )
        )
        timeout_receipt = invoke(
            "timeout", "import time;time.sleep(2)", timeout=0.05
        )
        start_error = run_raw(
            [str(workspace / "missing-executable")],
            cwd=workspace,
            stdout_path=workspace / "start-error.stdout.bin",
            stderr_path=workspace / "start-error.stderr.bin",
            allowed_output_root=workspace,
        )
        observed = {
            "base_receipts_ok": base_receipts_ok,
            "chinese_exact": chinese_ok,
            "empty_exact": empty_ok,
            "nonzero_exit_code": receipts["nonzero"].exit_code,
            "no_terminal_lf_preserved": no_lf_ok,
            "crlf_preserved": crlf_ok,
            "invalid_utf8_rejected": invalid_utf8_rejected,
            "invalid_utf8_error_type": invalid_utf8_error,
            "timeout_detected": timeout_receipt.timed_out,
            "timeout_exit_code": timeout_receipt.exit_code,
            "start_error_detected": start_error.start_error_type is not None,
            "start_error_stdout_bytes": start_error.stdout_bytes,
            "start_error_stderr_bytes": start_error.stderr_bytes,
        }
        passed = (
            observed["base_receipts_ok"]
            and observed["chinese_exact"]
            and observed["empty_exact"]
            and observed["nonzero_exit_code"] == 7
            and observed["no_terminal_lf_preserved"]
            and observed["crlf_preserved"]
            and observed["invalid_utf8_rejected"]
            and observed["timeout_detected"]
            and observed["timeout_exit_code"] is None
            and observed["start_error_detected"]
            and observed["start_error_stdout_bytes"] == 0
            and observed["start_error_stderr_bytes"] == 0
        )
        return observed, passed

    return _evaluation("A-018", exercise)


EVALUATORS = (
    evaluate_A_002,
    evaluate_A_003,
    evaluate_A_004,
    evaluate_A_005,
    evaluate_A_006,
    evaluate_A_007,
    evaluate_A_008,
    evaluate_A_009,
    evaluate_A_010,
    evaluate_A_011,
    evaluate_A_012,
    evaluate_A_013,
    evaluate_A_014,
    evaluate_A_015,
    evaluate_A_017,
    evaluate_A_018,
)

EVALUATOR_BY_ID = {"A-" + evaluator.__name__[-3:]: evaluator for evaluator in EVALUATORS}


__all__ = [
    *(evaluator.__name__ for evaluator in EVALUATORS),
    "EVALUATORS",
    "EVALUATOR_BY_ID",
]
