"""Strict locator validation for the FIELD-VECTORS manifest-backed candidate index.

There is deliberately no default root, fallback index, or activation path in
this module.  A caller must provide both a locator and the run-owned root.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping

from .canonical import canonical_json_bytes
from .contracts import (
    verify_hashed_payload,
)
from .errors import ContractViolation, FieldVectorError


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


def _is_reparse_point(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise PathBoundaryError(f"cannot lstat path component {path}: {exc}") from exc
    attributes = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & REPARSE_POINT_ATTRIBUTE)


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


__all__ = [
    "BACKEND",
    "EXPECTED_MEMBER_NAMES",
    "IMMUTABLE_STATE",
    "LocatorError",
    "PathBoundaryError",
]
