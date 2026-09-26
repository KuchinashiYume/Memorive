"""Strict locator validation for the P03/T03 manifest-backed candidate index.

There is deliberately no default root, fallback index, or activation path in
this module.  A caller must provide both a locator and the run-owned root.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping

from .canonical import canonical_json_bytes
from .contracts import (
    validate_embedding_profile,
    validate_index_identity,
    verify_hashed_payload,
)
from .errors import ContractViolation, FieldVectorError
from .field_vectors import (
    verify_field_source_item,
    verify_field_vector_record_against_source,
)


BACKEND = "canonical_manifest_index"
IMMUTABLE_STATE = "immutable_candidate"
REPARSE_POINT_ATTRIBUTE = 0x400
EXPECTED_MEMBER_NAMES = frozenset(
    {
        "IMMUTABLE_CANDIDATE.json",
        "SHA256SUMS",
        "manifest.json",
        "records.jsonl",
        "source_snapshot.json",
        "tombstones.jsonl",
    }
)
EXPECTED_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "backend",
        "state",
        "status",
        "index_id",
        "index_version",
        "collection_id",
        "ownership_partition",
        "profile",
        "index_identity",
        "build_cutoff_at",
        "created_at",
        "source_snapshot",
        "active_index_baseline",
        "record_count",
        "idempotent_duplicate_count",
        "record_exact_set",
        "record_exact_set_sha256",
        "predecessor_vector_ids",
        "payload_tree_sha256",
        "payload_tree_excludes",
        "records_file",
        "records_sha256",
        "source_snapshot_file",
        "source_snapshot_sha256",
        "tombstones_file",
        "tombstones_sha256",
        "tombstone_count",
        "tombstone_exact_set",
        "tombstone_exact_set_sha256",
        "exclusive_create",
        "write_scope",
        "external_calls",
        "cost_cny",
        "production_mutations",
        "git_mutations",
        "production_eligible",
        "activation",
        "manifest_id",
    }
)
TOMBSTONE_KEYS = frozenset(
    {
        "tombstone_id",
        "vector_id",
        "invalidation_state",
        "invalidated_by",
        "reason",
        "activation",
        "production_eligible",
        "content_hash",
    }
)


class LocatorError(FieldVectorError):
    """The locator or its immutable target failed a fail-closed check."""


class PathBoundaryError(LocatorError):
    """A path is outside the explicitly supplied owned root."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LocatorError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _load_json_bytes(data: bytes, *, source: str) -> dict[str, Any]:
    try:
        text = data.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocatorError(f"invalid UTF-8 JSON at {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise LocatorError(f"JSON object required at {source}")
    return value


def load_json_file(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load an explicit UTF-8 JSON object and reject duplicate keys."""

    candidate = Path(path)
    try:
        data = candidate.read_bytes()
    except OSError as exc:
        raise LocatorError(f"cannot read JSON file {candidate}: {exc}") from exc
    return _load_json_bytes(data, source=str(candidate))


def _is_reparse_point(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise PathBoundaryError(f"cannot lstat path component {path}: {exc}") from exc
    attributes = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & REPARSE_POINT_ATTRIBUTE)


def _common_path(left: Path, right: Path) -> Path:
    try:
        return Path(os.path.commonpath((str(left), str(right))))
    except (OSError, ValueError) as exc:
        raise PathBoundaryError(f"paths are on incompatible roots: {left} / {right}") from exc


def ensure_owned_path(
    path: str | os.PathLike[str],
    owned_root: str | os.PathLike[str],
    *,
    must_exist: bool | None = None,
    allow_root: bool = False,
) -> Path:
    """Return a normalized path only when it is a non-reparse descendant.

    ``owned_root`` is never inferred here.  Existing path components are
    checked for symlinks and Windows reparse points.  This prevents a lexical
    in-root path from escaping through a junction.
    """

    raw_root = Path(owned_root)
    raw_path = Path(path)
    if not raw_root.is_absolute() or not raw_path.is_absolute():
        raise PathBoundaryError("owned_root and path must both be absolute")
    if not raw_root.exists() or not raw_root.is_dir():
        raise PathBoundaryError(f"owned_root is not an existing directory: {raw_root}")

    root = raw_root.resolve(strict=True)
    if _is_reparse_point(root):
        raise PathBoundaryError(f"owned_root is a reparse point: {root}")

    normalized = raw_path.resolve(strict=False)
    if _common_path(root, normalized) != root:
        raise PathBoundaryError(f"path escapes owned_root: {raw_path}")
    if normalized == root and not allow_root:
        raise PathBoundaryError("operation target may not be the owned root itself")

    current = root
    relative = normalized.relative_to(root)
    for component in relative.parts:
        current = current / component
        if current.exists() and _is_reparse_point(current):
            raise PathBoundaryError(f"reparse path component is forbidden: {current}")

    if must_exist is True and not normalized.exists():
        raise PathBoundaryError(f"required path does not exist: {normalized}")
    if must_exist is False and normalized.exists():
        raise PathBoundaryError(f"exclusive-create path already exists: {normalized}")
    return normalized


def validate_run_root(root: str | os.PathLike[str]) -> Path:
    """Validate the explicit T03 run root against its frozen start manifest."""

    candidate = Path(root)
    if not candidate.is_absolute() or not candidate.exists() or not candidate.is_dir():
        raise PathBoundaryError(f"explicit run root is invalid: {candidate}")
    resolved = candidate.resolve(strict=True)
    if _is_reparse_point(resolved):
        raise PathBoundaryError(f"run root is a reparse point: {resolved}")

    expected_parent_parts = ("PR-OS-沙盒", "Phase03_研究运营")
    parts = tuple(resolved.parts)
    if len(parts) < 3 or tuple(parts[-3:-1]) != expected_parent_parts:
        raise PathBoundaryError("run root is outside the governed P03 sandbox parent")
    if not resolved.name.startswith("p03_t03_cross_field_vector_v1_"):
        raise PathBoundaryError("run root name is not the governed P03/T03/CROSS form")

    start_manifest_path = resolved / "00_manifest" / "start_manifest.json"
    start_manifest = load_json_file(start_manifest_path)
    if start_manifest.get("task_id") != "T03" or start_manifest.get("module_owner") != "CROSS":
        raise PathBoundaryError("start manifest is not P03/T03/CROSS")
    declared = start_manifest.get("sandbox_root")
    if not isinstance(declared, str) or Path(declared).resolve(strict=False) != resolved:
        raise PathBoundaryError("start manifest sandbox_root does not match --root")
    return resolved


def tree_sha256(root: str | os.PathLike[str]) -> str:
    """Hash a regular-file tree using sorted relative paths and raw bytes."""

    base = Path(root)
    if not base.exists() or not base.is_dir():
        raise LocatorError(f"tree root is not a directory: {base}")
    rows: list[bytes] = []
    for path in sorted(base.rglob("*"), key=lambda item: item.relative_to(base).as_posix()):
        if path.is_dir():
            if _is_reparse_point(path):
                raise LocatorError(f"reparse directory in candidate tree: {path}")
            continue
        if _is_reparse_point(path):
            raise LocatorError(f"reparse file in candidate tree: {path}")
        mode = os.lstat(path).st_mode
        if not stat.S_ISREG(mode):
            raise LocatorError(f"non-regular candidate member: {path}")
        relative = path.relative_to(base).as_posix()
        data = path.read_bytes()
        rows.append(
            relative.encode("utf-8")
            + b"\0"
            + str(len(data)).encode("ascii")
            + b"\0"
            + _sha256(data).encode("ascii")
            + b"\n"
        )
    return _sha256(b"".join(rows))


def _require_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LocatorError(f"{field} must be a JSON object")
    return value


def _require_sha(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise LocatorError(f"{field} must be a 64-character SHA-256")
    try:
        int(value, 16)
    except ValueError as exc:
        raise LocatorError(f"{field} is not hexadecimal") from exc
    return value.upper()


def _parse_jsonl(data: bytes, *, source: str) -> list[dict[str, Any]]:
    if data and not data.endswith(b"\n"):
        raise LocatorError(f"JSONL must end with LF: {source}")
    rows: list[dict[str, Any]] = []
    for number, raw_line in enumerate(data.splitlines(), start=1):
        if not raw_line:
            raise LocatorError(f"blank JSONL row at {source}:{number}")
        rows.append(_load_json_bytes(raw_line, source=f"{source}:{number}"))
    return rows


def _verify_sha256sums(root: Path) -> None:
    data = (root / "SHA256SUMS").read_bytes()
    if data and not data.endswith(b"\n"):
        raise LocatorError("SHA256SUMS must end with LF")
    observed_names: set[str] = set()
    for line in data.decode("ascii", errors="strict").splitlines():
        if "  " not in line:
            raise LocatorError("malformed SHA256SUMS row")
        digest, name = line.split("  ", 1)
        digest = _require_sha(digest, field=f"SHA256SUMS[{name}]")
        if name in observed_names or name == "SHA256SUMS" or "/" in name or "\\" in name:
            raise LocatorError(f"invalid SHA256SUMS member name: {name!r}")
        member = root / name
        if not member.exists() or not member.is_file() or _is_reparse_point(member):
            raise LocatorError(f"SHA256SUMS member missing or unsafe: {name}")
        if _sha256(member.read_bytes()) != digest:
            raise LocatorError(f"SHA256SUMS mismatch: {name}")
        observed_names.add(name)
    expected = EXPECTED_MEMBER_NAMES - {"SHA256SUMS"}
    if observed_names != expected:
        raise LocatorError(
            f"SHA256SUMS exact-set mismatch: expected={sorted(expected)} observed={sorted(observed_names)}"
        )


def make_locator(
    candidate_root: str | os.PathLike[str],
    owned_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Create a locator from an already materialized candidate directory."""

    root = ensure_owned_path(candidate_root, owned_root, must_exist=True)
    manifest_bytes = (root / "manifest.json").read_bytes()
    manifest = _load_json_bytes(manifest_bytes, source=str(root / "manifest.json"))
    snapshot_anchor = manifest.get("source_snapshot")
    if not isinstance(snapshot_anchor, dict):
        raise LocatorError("manifest.source_snapshot anchor is required")
    return {
        "locator_schema_version": "1.0",
        "root": str(root),
        "state": manifest.get("state"),
        "backend": manifest.get("backend"),
        "manifest_sha256": _sha256(manifest_bytes),
        "tree_sha256": tree_sha256(root),
        "profile": manifest.get("profile"),
        "index_identity": manifest.get("index_identity"),
        "snapshot": {
            "path": manifest.get("source_snapshot_file"),
            "sha256": manifest.get("source_snapshot_sha256"),
            "source_snapshot_id": snapshot_anchor.get("source_snapshot_id"),
            "content_hash": snapshot_anchor.get("content_hash"),
        },
        "build_cutoff_at": manifest.get("build_cutoff_at"),
        "production_eligible": False,
        "activation": False,
    }


def validate_locator(
    locator: Mapping[str, Any],
    owned_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Validate a locator and every member of its immutable candidate.

    The validated locator is returned as a plain dict.  No alternate root is
    attempted when any check fails.
    """

    if not isinstance(locator, Mapping):
        raise LocatorError("locator must be a mapping")
    root_value = locator.get("root")
    if not isinstance(root_value, str) or not root_value:
        raise LocatorError("locator.root is required; fallback is forbidden")
    root = ensure_owned_path(root_value, owned_root, must_exist=True)
    if set(path.name for path in root.iterdir()) != EXPECTED_MEMBER_NAMES:
        raise LocatorError("candidate member exact-set mismatch")
    if any(path.is_dir() for path in root.iterdir()):
        raise LocatorError("candidate root must contain regular files only")

    if locator.get("state") != IMMUTABLE_STATE:
        raise LocatorError("locator.state must be immutable_candidate")
    if locator.get("backend") != BACKEND:
        raise LocatorError("locator.backend must be canonical_manifest_index")
    if locator.get("production_eligible") is not False:
        raise LocatorError("production eligibility must be explicitly false")
    if locator.get("activation") is not False:
        raise LocatorError("activation must be explicitly false")

    manifest_path = root / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha = _sha256(manifest_bytes)
    if manifest_sha != _require_sha(locator.get("manifest_sha256"), field="manifest_sha256"):
        raise LocatorError("manifest hash mismatch")
    manifest = _load_json_bytes(manifest_bytes, source=str(manifest_path))
    if set(manifest) != EXPECTED_MANIFEST_KEYS:
        raise LocatorError("candidate manifest exact key set mismatch")
    if manifest.get("state") != IMMUTABLE_STATE or manifest.get("backend") != BACKEND:
        raise LocatorError("manifest state/backend mismatch")
    if manifest.get("production_eligible") is not False or manifest.get("activation") is not False:
        raise LocatorError("manifest attempts production eligibility or activation")
    for field_name in (
        "external_calls",
        "cost_cny",
        "production_mutations",
        "git_mutations",
    ):
        if field_name not in manifest or manifest[field_name] != 0:
            raise LocatorError(f"manifest {field_name} must be explicitly zero")
    if manifest.get("exclusive_create") is not True or manifest.get("write_scope") != "isolated_sandbox_only":
        raise LocatorError("manifest write-boundary contract mismatch")
    manifest_without_id = dict(manifest)
    observed_manifest_id = manifest_without_id.pop("manifest_id", None)
    expected_manifest_id = "P03_T03_CANDIDATE_INDEX_" + _sha256(
        canonical_json_bytes(manifest_without_id)
    )
    if observed_manifest_id != expected_manifest_id:
        raise LocatorError("manifest_id does not bind the exact manifest payload")

    active_baseline = _require_mapping(
        manifest.get("active_index_baseline"), field="manifest.active_index_baseline"
    )
    if set(active_baseline) != {
        "authority_ref",
        "authority_raw_sha256",
        "declared_locator",
        "exists",
        "member_count",
        "bytes",
        "tree_sha256",
        "read_only",
    }:
        raise LocatorError("active-index baseline exact key set mismatch")
    if active_baseline.get("read_only") is not True:
        raise LocatorError("active-index baseline must be read-only")
    if active_baseline.get("exists") is False and (
        active_baseline.get("member_count") != 0
        or active_baseline.get("bytes") != 0
        or active_baseline.get("tree_sha256") is not None
    ):
        raise LocatorError("absent active-index baseline has non-zero measures")

    profile = _require_mapping(locator.get("profile"), field="locator.profile")
    identity = _require_mapping(locator.get("index_identity"), field="locator.index_identity")
    try:
        profile = validate_embedding_profile(profile)
        identity = validate_index_identity(identity)
    except FieldVectorError as exc:
        raise LocatorError(f"profile/index identity failed: {exc}") from exc
    if canonical_json_bytes(profile) != canonical_json_bytes(manifest.get("profile")):
        raise LocatorError("profile mismatch between locator and manifest")
    if canonical_json_bytes(identity) != canonical_json_bytes(manifest.get("index_identity")):
        raise LocatorError("index identity mismatch between locator and manifest")
    if (
        manifest.get("index_id") != identity["index_id"]
        or manifest.get("index_version") != identity["version"]
        or manifest.get("collection_id") != identity["collection_id"]
        or profile["index_version"] != identity["version"]
    ):
        raise LocatorError("manifest top-level profile/index identity mismatch")
    if locator.get("build_cutoff_at") != manifest.get("build_cutoff_at"):
        raise LocatorError("build cutoff mismatch")

    snapshot = _require_mapping(locator.get("snapshot"), field="locator.snapshot")
    snapshot_name = manifest.get("source_snapshot_file")
    if snapshot_name != "source_snapshot.json" or snapshot.get("path") != snapshot_name:
        raise LocatorError("snapshot path must be explicit source_snapshot.json")
    snapshot_sha = _require_sha(snapshot.get("sha256"), field="snapshot.sha256")
    if snapshot_sha != _require_sha(manifest.get("source_snapshot_sha256"), field="manifest snapshot"):
        raise LocatorError("snapshot hash differs between locator and manifest")
    snapshot_bytes = (root / snapshot_name).read_bytes()
    if _sha256(snapshot_bytes) != snapshot_sha:
        raise LocatorError("source snapshot bytes do not match locator")
    source_snapshot = _load_json_bytes(snapshot_bytes, source=str(root / snapshot_name))
    try:
        source_snapshot = verify_hashed_payload(source_snapshot, "source_snapshot")
    except FieldVectorError as exc:
        raise LocatorError(f"source snapshot typed hash failed: {exc}") from exc
    if (
        source_snapshot.get("object_type") != "SourceSnapshot"
        or source_snapshot.get("state") != "snapshot_ready"
    ):
        raise LocatorError("source snapshot is not the typed runtime SourceSnapshot")
    if set(source_snapshot) != {
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
    }:
        raise LocatorError("source snapshot exact key set mismatch")
    snapshot_id = source_snapshot.get("source_snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise LocatorError("source snapshot id is missing")
    manifest_snapshot = _require_mapping(
        manifest.get("source_snapshot"), field="manifest.source_snapshot"
    )
    if snapshot.get("source_snapshot_id") != snapshot_id:
        raise LocatorError("locator source snapshot id mismatch")
    if manifest_snapshot.get("source_snapshot_id") != snapshot_id:
        raise LocatorError("manifest source snapshot id mismatch")
    if canonical_json_bytes(snapshot.get("content_hash")) != canonical_json_bytes(
        source_snapshot.get("content_hash")
    ):
        raise LocatorError("locator typed source snapshot hash mismatch")
    if canonical_json_bytes(manifest_snapshot.get("content_hash")) != canonical_json_bytes(
        source_snapshot.get("content_hash")
    ):
        raise LocatorError("manifest typed source snapshot hash mismatch")
    raw_field_sources = source_snapshot.get("records")
    if not isinstance(raw_field_sources, list):
        raise LocatorError("source snapshot records must be a list")
    field_sources: dict[str, dict[str, Any]] = {}
    for ordinal, raw_source in enumerate(raw_field_sources):
        if not isinstance(raw_source, Mapping):
            raise LocatorError(f"source snapshot member {ordinal} is not an object")
        try:
            checked_source = verify_field_source_item(raw_source, source_snapshot)
        except FieldVectorError as exc:
            raise LocatorError(f"source snapshot member failed at {ordinal}: {exc}") from exc
        source_id = checked_source["field_source_id"]
        if source_id in field_sources:
            raise LocatorError("duplicate field_source_id in source snapshot")
        field_sources[source_id] = checked_source
    member_ids = [item["field_source_id"] for item in raw_field_sources]
    expected_snapshot_id = "ss1_" + _sha256(
        canonical_json_bytes(
            {
                "build_cutoff_at": source_snapshot.get("build_cutoff_at"),
                "public_safe": True,
                "member_ids": member_ids,
                "rejections": source_snapshot.get("rejections"),
            }
        )
    ).lower()
    if snapshot_id != expected_snapshot_id:
        raise LocatorError("source_snapshot_id does not bind the exact member set")

    records_data = (root / "records.jsonl").read_bytes()
    records_sha = _require_sha(manifest.get("records_sha256"), field="records_sha256")
    if _sha256(records_data) != records_sha:
        raise LocatorError("records.jsonl hash mismatch")
    records = _parse_jsonl(records_data, source=str(root / "records.jsonl"))
    if len(records) != manifest.get("record_count"):
        raise LocatorError("record count mismatch")
    vector_ids = [row.get("vector_id") for row in records]
    if any(not isinstance(value, str) or not value for value in vector_ids):
        raise LocatorError("every record must have a non-empty vector_id")
    if len(vector_ids) != len(set(vector_ids)):
        raise LocatorError("duplicate vector_id remained in immutable records")
    anchors: list[dict[str, Any]] = []
    ownerships: set[str] = set()
    observed_source_ids: set[str] = set()
    for ordinal, record in enumerate(records):
        field_source_id = record.get("field_source_id")
        if field_source_id not in field_sources:
            raise LocatorError("record field_source_id is absent from source snapshot")
        try:
            checked_record = verify_field_vector_record_against_source(
                record, field_sources[field_source_id], source_snapshot
            )
        except FieldVectorError as exc:
            raise LocatorError(f"record semantic verification failed at {ordinal}: {exc}") from exc
        if checked_record.get("source_snapshot_id") != snapshot_id:
            raise LocatorError("record source snapshot id binding mismatch")
        if canonical_json_bytes(checked_record.get("source_snapshot_hash")) != canonical_json_bytes(
            source_snapshot.get("content_hash")
        ):
            raise LocatorError("record typed source snapshot hash binding mismatch")
        ownership = checked_record.get("data_ownership")
        if ownership not in {"self", "entrusted"}:
            raise LocatorError("record ownership is not normative")
        ownerships.add(ownership)
        observed_source_ids.add(str(field_source_id))
        if (
            checked_record.get("embedding_profile_id") != profile["embedding_profile_id"]
            or checked_record.get("embedding_model_id") != profile["embedding_model_id"]
            or checked_record.get("embedding_dimension") != profile["embedding_dimension"]
            or checked_record.get("embedding_normalization")
            != profile["embedding_normalization"]
            or checked_record.get("index_id") != identity["index_id"]
            or checked_record.get("index_version") != identity["version"]
            or checked_record.get("collection_id") != identity["collection_id"]
            or checked_record.get("build_cutoff_at") != identity["build_cutoff_at"]
        ):
            raise LocatorError("record profile/index identity differs from manifest")
        anchors.append(
            {
                "vector_id": checked_record.get("vector_id"),
                "content_hash": checked_record.get("content_hash"),
                "storage_key": checked_record.get("storage_key"),
                "card_id": checked_record.get("card_id"),
                "field_path": checked_record.get("field_path"),
                "data_ownership": ownership,
            }
        )
    anchors.sort(key=lambda item: str(item["vector_id"]))
    if canonical_json_bytes(anchors) != canonical_json_bytes(manifest.get("record_exact_set")):
        raise LocatorError("manifest record_exact_set differs from records.jsonl")
    if _sha256(canonical_json_bytes(anchors)) != _require_sha(
        manifest.get("record_exact_set_sha256"), field="record_exact_set_sha256"
    ):
        raise LocatorError("record exact-set hash mismatch")
    expected_partition = "self" if ownerships == {"self"} else "entrusted_local_only"
    if len(ownerships) != 1 or manifest.get("ownership_partition") != expected_partition:
        raise LocatorError("candidate ownership partition mismatch")
    ownership = next(iter(ownerships))
    expected_source_ids = {
        source_id
        for source_id, source in field_sources.items()
        if source["data_ownership"] == ownership
    }
    if observed_source_ids != expected_source_ids:
        raise LocatorError("candidate records differ from snapshot ownership exact set")

    payload_members = {
        "records.jsonl": _sha256(records_data),
        "source_snapshot.json": _sha256(snapshot_bytes),
        "tombstones.jsonl": _sha256((root / "tombstones.jsonl").read_bytes()),
    }
    if _sha256(canonical_json_bytes(payload_members)) != _require_sha(
        manifest.get("payload_tree_sha256"), field="payload_tree_sha256"
    ):
        raise LocatorError("candidate payload tree hash mismatch")
    if manifest.get("payload_tree_excludes") != [
        "manifest.json",
        "IMMUTABLE_CANDIDATE.json",
        "SHA256SUMS",
    ]:
        raise LocatorError("payload tree exclusion contract mismatch")

    tombstone_data = (root / "tombstones.jsonl").read_bytes()
    tombstone_sha = _require_sha(manifest.get("tombstones_sha256"), field="tombstones_sha256")
    if _sha256(tombstone_data) != tombstone_sha:
        raise LocatorError("tombstones.jsonl hash mismatch")
    tombstones = _parse_jsonl(tombstone_data, source=str(root / "tombstones.jsonl"))
    if len(tombstones) != manifest.get("tombstone_count"):
        raise LocatorError("tombstone count mismatch")
    predecessor_ids = manifest.get("predecessor_vector_ids")
    if (
        not isinstance(predecessor_ids, list)
        or predecessor_ids != sorted(set(predecessor_ids))
        or any(not isinstance(item, str) or not item for item in predecessor_ids)
    ):
        raise LocatorError("predecessor_vector_ids must be a sorted unique string list")
    known_predecessors = set(predecessor_ids) | set(vector_ids)
    tombstone_anchors: list[dict[str, Any]] = []
    seen_tombstone_ids: set[str] = set()
    seen_predecessors: set[str] = set()
    for ordinal, tombstone in enumerate(tombstones):
        try:
            checked_tombstone = verify_hashed_payload(tombstone, f"tombstones[{ordinal}]")
        except FieldVectorError as exc:
            raise LocatorError(f"tombstone typed hash failed at {ordinal}: {exc}") from exc
        if set(checked_tombstone) != TOMBSTONE_KEYS:
            raise LocatorError("tombstone exact key set mismatch")
        predecessor = checked_tombstone.get("vector_id")
        successor = checked_tombstone.get("invalidated_by")
        reason = checked_tombstone.get("reason")
        if predecessor not in known_predecessors:
            raise LocatorError("tombstone predecessor is not preserved")
        if predecessor in seen_predecessors:
            raise LocatorError("duplicate tombstone transition for predecessor")
        if reason == "superseded":
            if successor not in set(vector_ids) or successor == predecessor:
                raise LocatorError("superseded tombstone requires a current distinct successor")
        elif reason == "removed_from_source_snapshot":
            if successor is not None:
                raise LocatorError("field deletion tombstone must have invalidated_by=null")
        else:
            raise LocatorError("unsupported tombstone reason")
        if (
            checked_tombstone.get("invalidation_state") != "tombstoned"
            or checked_tombstone.get("activation") is not False
            or checked_tombstone.get("production_eligible") is not False
        ):
            raise LocatorError("tombstone state/activation contract mismatch")
        expected_tombstone_id = "tombstone_" + _sha256(
            canonical_json_bytes([predecessor, successor, reason])
        ).lower()
        if checked_tombstone.get("tombstone_id") != expected_tombstone_id:
            raise LocatorError("tombstone_id does not bind the transition")
        tombstone_id = str(checked_tombstone["tombstone_id"])
        if tombstone_id in seen_tombstone_ids:
            raise LocatorError("duplicate tombstone_id")
        seen_tombstone_ids.add(tombstone_id)
        seen_predecessors.add(str(predecessor))
        tombstone_anchors.append(
            {
                "tombstone_id": tombstone_id,
                "vector_id": predecessor,
                "invalidated_by": successor,
                "reason": reason,
                "content_hash": checked_tombstone["content_hash"],
            }
        )
    tombstone_anchors.sort(key=lambda item: item["tombstone_id"])
    if canonical_json_bytes(tombstone_anchors) != canonical_json_bytes(
        manifest.get("tombstone_exact_set")
    ):
        raise LocatorError("tombstone exact-set mismatch")
    if _sha256(canonical_json_bytes(tombstone_anchors)) != _require_sha(
        manifest.get("tombstone_exact_set_sha256"), field="tombstone_exact_set_sha256"
    ):
        raise LocatorError("tombstone exact-set hash mismatch")

    marker = load_json_file(root / "IMMUTABLE_CANDIDATE.json")
    if marker.get("state") != IMMUTABLE_STATE or marker.get("manifest_sha256") != manifest_sha:
        raise LocatorError("immutable marker mismatch")
    if marker.get("activation") is not False or marker.get("production_eligible") is not False:
        raise LocatorError("immutable marker attempts activation")

    _verify_sha256sums(root)
    candidate_tree = tree_sha256(root)
    if candidate_tree != _require_sha(locator.get("tree_sha256"), field="tree_sha256"):
        raise LocatorError("candidate tree hash mismatch")

    return dict(locator)


def load_and_validate_locator(
    locator_path: str | os.PathLike[str],
    owned_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Load one explicit locator path and validate it without fallback."""

    path = ensure_owned_path(locator_path, owned_root, must_exist=True)
    locator = load_json_file(path)
    return validate_locator(locator, owned_root)


__all__ = [
    "BACKEND",
    "EXPECTED_MEMBER_NAMES",
    "IMMUTABLE_STATE",
    "LocatorError",
    "PathBoundaryError",
    "ensure_owned_path",
    "load_and_validate_locator",
    "load_json_file",
    "make_locator",
    "tree_sha256",
    "validate_locator",
    "validate_run_root",
]
