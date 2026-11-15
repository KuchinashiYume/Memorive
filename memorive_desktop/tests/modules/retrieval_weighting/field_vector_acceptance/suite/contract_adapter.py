"""One explicit adaptation seam between frozen ServiceContracts tests and candidate code.

The acceptance scenarios intentionally call only exports resolved here.  This
keeps implementation signature changes out of the frozen expected assertions
and makes the governed run root, active-index baseline and test-output root
explicit at every write boundary.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence




def _governed_run_root(package_root: Path) -> Path:
    import shutil
    import tempfile

    # One run root per process: test runs from other sessions share this temp folder.
    parent = Path(tempfile.gettempdir()) / "Memorive-沙盒" / "Research_研究运营"
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="field_vectors_cross_field_vector_v1_", dir=parent))
    for name in ("fixtures", "10_contract", "00_manifest", "validator"):
        shutil.copytree(package_root / name, root / name)
    (root / "00_manifest" / "start_manifest.json").write_text(json.dumps(
        {"task_id": "ServiceContracts", "module_owner": "CROSS", "sandbox_root": str(root)}), encoding="utf-8")
    return root


ROOT = _governed_run_root(Path(__file__).resolve().parents[1])
CANDIDATE_ROOT = ROOT / "10_candidate"
FIXTURES = ROOT / "fixtures"
ORACLE_ROOT = FIXTURES / "reference_oracle_v1"
BUILD_CUTOFF = "2026-08-03T00:00:00Z"

if CANDIDATE_ROOT.exists() and str(CANDIDATE_ROOT) not in sys.path:
    sys.path.insert(0, str(CANDIDATE_ROOT))

CORE_MODULES = (
    "retrieval_weighting.source_snapshot",
    "retrieval_weighting.field_vectors",
)
INDEX_MODULES = (
    "retrieval_weighting.candidate_index",
    "retrieval_weighting.migration",
    "retrieval_weighting.locator",
    "retrieval_weighting",
)
CONTRACT_MODULES = (
    "retrieval_weighting.contracts",
    "retrieval_weighting.canonical",
    "retrieval_weighting.field_registry",
)


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_root_json(relative_path: str) -> Any:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def stable_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def fixture_records() -> list[dict[str, Any]]:
    return load_json("source_snapshot_public_safe.json")["records"]


def fixture_by_id(fixture_id: str) -> dict[str, Any]:
    matches = [row for row in fixture_records() if row["fixture_id"] == fixture_id]
    if len(matches) != 1:
        raise AssertionError(f"fixture identity must be exact: {fixture_id}")
    return matches[0]


def expected_by_id(fixture_id: str) -> dict[str, Any]:
    rows = load_json("expected_outcomes_v1.json")["expectations"]
    matches = [row for row in rows if row["fixture_id"] == fixture_id]
    if len(matches) != 1:
        raise AssertionError(f"expected identity must be exact: {fixture_id}")
    return matches[0]


def _resolve_export(module_names: tuple[str, ...], export: str) -> Callable[..., Any]:
    failures: list[str] = []
    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # diagnostics remain public-safe and fail closed
            failures.append(f"{module_name}:{type(exc).__name__}")
            continue
        candidate = getattr(module, export, None)
        if callable(candidate):
            return candidate
        failures.append(f"{module_name}:missing-{export}")
    raise AssertionError(
        f"required candidate export {export!r} unavailable; adapter attempts={failures}"
    )


def build_source_snapshot(**kwargs: Any) -> Any:
    return _resolve_export(CORE_MODULES, "build_source_snapshot")(**kwargs)


def build_field_vector_records(**kwargs: Any) -> Any:
    return _resolve_export(CORE_MODULES, "build_field_vector_records")(**kwargs)


def verify_field_vector_record(record: Mapping[str, Any]) -> Any:
    return _resolve_export(CORE_MODULES, "verify_field_vector_record")(record)


def verify_field_source_item(
    source: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> Any:
    return _resolve_export(CORE_MODULES, "verify_field_source_item")(source, snapshot)


def build_candidate_index(**kwargs: Any) -> Any:
    return _resolve_export(INDEX_MODULES, "build_candidate_index")(**kwargs)


def migrate_candidate(**kwargs: Any) -> Any:
    return _resolve_export(INDEX_MODULES, "migrate_candidate")(**kwargs)


def validate_locator(locator: Mapping[str, Any], owned_root: Path = ROOT) -> Any:
    return _resolve_export(INDEX_MODULES, "validate_locator")(locator, owned_root)


def ensure_owned_path(**kwargs: Any) -> Any:
    return _resolve_export(INDEX_MODULES, "ensure_owned_path")(**kwargs)


def make_hashed_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _resolve_export(CONTRACT_MODULES, "make_hashed_payload")(payload)


def typed_payload_hash(payload: Mapping[str, Any]) -> dict[str, str]:
    return _resolve_export(CONTRACT_MODULES, "typed_payload_hash")(payload)


def canonical_json_bytes(value: Any) -> bytes:
    return _resolve_export(CONTRACT_MODULES, "canonical_json_bytes")(value)


def field_registry() -> dict[str, Any]:
    return _resolve_export(CONTRACT_MODULES, "get_field_registry")()


def migration_failure_type() -> type[BaseException]:
    candidate = _resolve_export(INDEX_MODULES, "MigrationFailure")
    if not isinstance(candidate, type):
        raise AssertionError("MigrationFailure export is not an exception type")
    return candidate


def offline_profile(
    *,
    entrusted: bool = False,
    dimension: int = 4,
    profile_id: str | None = None,
    index_version: int = 1,
) -> dict[str, Any]:
    prefix = "local-only:" if entrusted else "offline:"
    return {
        "embedding_profile_id": profile_id or f"{prefix}fixture-v1-d{dimension}",
        "embedding_model_id": "fixture-hash-vector-v2",
        "embedding_dimension": dimension,
        "embedding_normalization": "l2",
        "index_version": index_version,
    }


def index_identity(
    *,
    entrusted: bool = False,
    dimension: int = 4,
    index_id: str | None = None,
    collection_id: str | None = None,
    index_version: int = 1,
    vector_type: str = "card_field",
) -> dict[str, Any]:
    prefix = "local-only:" if entrusted else "offline:"
    registry = field_registry()
    return make_hashed_payload(
        {
            "index_id": index_id or f"{prefix}index-v1-d{dimension}",
            "version": index_version,
            "collection_id": collection_id or f"{prefix}candidate-v1-d{dimension}",
            "build_cutoff_at": BUILD_CUTOFF,
            "vector_type": vector_type,
            "field_registry_id": registry["registry_id"],
            "field_registry_version": registry["revision"],
            "field_registry_hash": registry["content_hash"]["value"],
            "production_eligible": False,
        }
    )


def active_index_baseline() -> dict[str, Any]:
    """Return the frozen H0 authority as an explicit read-only measurement."""

    authority_path = ROOT / "00_manifest" / "active_index_authority.json"
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    active = authority["production_vault"]
    exists = active["exists"]
    if exists is not False:
        raise AssertionError("fixture baseline expects the declared production vault absent")
    declared_locator = Path(authority["declared_paths"]["production_vault"])
    if declared_locator.exists():
        raise AssertionError("declared production vault appeared after the frozen H0 authority")
    return {
        "authority_ref": "00_manifest/active_index_authority.json",
        "authority_raw_sha256": sha256_file(authority_path),
        "declared_locator": str(declared_locator),
        "exists": False,
        "member_count": 0,
        "bytes": 0,
        "tree_sha256": None,
        "read_only": True,
    }


def source_snapshot(
    fixture_ids: Sequence[str],
    *,
    records: Sequence[Mapping[str, Any]] | None = None,
    metadata: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    selected = (
        list(records)
        if records is not None
        else [fixture_by_id(fixture_id) for fixture_id in fixture_ids]
    )
    metadata_rows = (
        list(metadata)
        if metadata is not None
        else load_json("doc_type_metadata_public_safe.json")["records"]
    )
    return build_source_snapshot(
        records=selected,
        doc_type_metadata=metadata_rows,
        build_cutoff_at=BUILD_CUTOFF,
        public_safe=True,
    )


def vector_bundle(
    fixture_ids: Sequence[str],
    *,
    entrusted: bool = False,
    dimension: int = 4,
    profile_id: str | None = None,
    index_id: str | None = None,
    collection_id: str | None = None,
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    profile = offline_profile(
        entrusted=entrusted,
        dimension=dimension,
        profile_id=profile_id,
    )
    identity = index_identity(
        entrusted=entrusted,
        dimension=dimension,
        index_id=index_id,
        collection_id=collection_id,
    )
    snapshot = dict(source) if source is not None else source_snapshot(fixture_ids)
    vectors = build_field_vector_records(
        source_snapshot=snapshot,
        profile=profile,
        index_id=identity["index_id"],
        index_version=identity["version"],
        collection_id=identity["collection_id"],
    )
    return {
        "snapshot": snapshot,
        "profile": profile,
        "index_identity": identity,
        "vectors": vectors,
    }


def canonical_jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(dict(row)) + b"\n" for row in rows)


def field_vectors_test_root() -> Path:
    """Resolve the only scenario-output root and prove it is inside this run."""

    explicit = os.environ.get("FIELD_VECTORS_TEST_ROOT")
    product_output = os.environ.get("MEMORIVE_FIELD_VECTORS_OUTPUT_ROOT")
    if explicit:
        requested = Path(explicit)
    elif product_output:
        requested = Path(product_output) / "FIELD_VECTORS_TEST_ROOT"
    else:
        requested = ROOT / "FIELD_VECTORS_TEST_ROOT"
    if not requested.is_absolute():
        requested = ROOT / requested
    normalized = requested.resolve(strict=False)
    governed = ROOT.resolve(strict=True)
    try:
        normalized.relative_to(governed)
    except ValueError as exc:
        raise AssertionError("FIELD_VECTORS_TEST_ROOT must be inside the governed run root") from exc
    if normalized == governed:
        raise AssertionError("FIELD_VECTORS_TEST_ROOT may not equal the governed run root")
    return normalized
