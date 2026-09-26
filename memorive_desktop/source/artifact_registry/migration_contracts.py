"""Closed ASSET-MIGRATION contracts for exact-manifest migration and receipts.

The module deliberately has no filesystem discovery helpers.  A caller must
name every source item explicitly; identity is derived from frozen
``identity_basis`` bytes and never from a mutable path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .schema_v2 import build_envelope, canonical_json, validate_envelope


BATCH_SCHEMA_VERSION = "asset_migration-migration-batch-v1"
RECEIPT_SCHEMA_VERSION = "asset_migration-migration-receipt-v1"
PROJECTION_SCHEMA_VERSION = "asset_migration-query-projection-v1"
QUERY_RESULT_SCHEMA_VERSION = "asset_migration-query-result-v1"

CAPABILITY_MODE = "CAPABILITY_SANDBOX"
PRODUCTION_MODE = "PRODUCTION_EXACT_BATCH"
ISOLATED_ROOT_CLASS = "ISOLATED_SANDBOX"
PRODUCTION_ROOT_CLASS = "PRODUCTION_ARTIFACT_REGISTRY"

ITEM_DISPOSITIONS = frozenset(
    {
        "IMPORTED",
        "NOOP_EXACT",
        "UNRESOLVED_ACCEPTED",
        "REJECTED_BY_CONTRACT",
        "CONFLICT_BLOCKED",
        "ERROR_PARTIAL",
        "NOT_RUN",
    }
)
BATCH_STATUSES = frozenset(
    {
        "PLANNED",
        "DRY_RUN_PASS",
        "APPLYING",
        "PARTIAL_BLOCKED",
        "COMPLETED",
        "FAILED_PREFLIGHT",
        "ABORTED",
    }
)
RECEIPT_PHASES = frozenset(
    {
        "ITEM_PREPARED",
        "REGISTRY_OUTCOME",
        "RECEIPT_COMMITTED",
        "RECEIPT_AMENDMENT",
    }
)

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_ARTIFACT_ID = re.compile(r"^art_[0-9a-f]{32}$")
_STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MODE_TO_ROOT = {
    CAPABILITY_MODE: ISOLATED_ROOT_CLASS,
    PRODUCTION_MODE: PRODUCTION_ROOT_CLASS,
}
_BATCH_REQUIRED = {
    "batch_schema_version",
    "batch_id",
    "mode",
    "authority_ref",
    "source_snapshot",
    "source_inventory_sha256",
    "target_registry_root_class",
    "target_registry_baseline",
    "items",
    "failure_policy",
    "external_side_effect_ceiling",
    "created_at",
}
_ITEM_REQUIRED = {
    "item_id",
    "item_order",
    "source_locator_class",
    "source_locator",
    "expected_bytes",
    "expected_sha256",
    "rights",
    "classification",
    "artifact_type",
    "identity_basis",
    "expected_artifact_id",
    "run_ref",
    "schema_ref",
    "evidence_refs",
    "parent_artifacts",
    "unresolved_parent_requirements",
    "conflicting_candidates",
    "expected_disposition",
    "adapter_key",
    "adapter_version",
}
_ITEM_OPTIONAL = {"paper_ids", "created_at", "registered_at", "metadata"}


class MigrationContractError(ValueError):
    """A batch, item or receipt violates the closed Logging contract."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.retryable = retryable
        self.blocking = True


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def canonical_bytes(value: Any) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def canonical_digest(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def artifact_id_from_identity(identity_basis: str) -> str:
    _require_text(identity_basis, "identity_basis")
    digest = hashlib.sha256(identity_basis.encode("utf-8")).hexdigest()
    return "art_" + digest[:32]


def inventory_digest(items: Iterable[Mapping[str, Any]]) -> str:
    ordered = [copy.deepcopy(dict(item)) for item in items]
    ordered.sort(key=lambda item: (item.get("item_order"), item.get("item_id")))
    return canonical_digest(ordered)


def validate_batch(batch: Mapping[str, Any], *, allow_production: bool = False) -> dict[str, Any]:
    if not isinstance(batch, Mapping):
        raise MigrationContractError("BATCH_NOT_OBJECT", "batch must be an object")
    candidate = copy.deepcopy(dict(batch))
    _require_exact_fields(candidate, _BATCH_REQUIRED, "batch")
    if candidate["batch_schema_version"] != BATCH_SCHEMA_VERSION:
        raise MigrationContractError("BATCH_VERSION_UNSUPPORTED", "batch_schema_version mismatch")
    _require_stable_id(candidate["batch_id"], "batch_id")
    _require_text(candidate["authority_ref"], "authority_ref")
    _require_datetime(candidate["created_at"], "created_at")
    mode = candidate["mode"]
    if mode not in _MODE_TO_ROOT:
        raise MigrationContractError("MODE_UNSUPPORTED", f"unsupported mode {mode!r}")
    if mode == PRODUCTION_MODE and not allow_production:
        raise MigrationContractError(
            "PRODUCTION_AUTHORITY_REQUIRED",
            "PRODUCTION_EXACT_BATCH is disabled without an exact-batch authority overlay",
        )
    expected_root_class = _MODE_TO_ROOT[mode]
    if candidate["target_registry_root_class"] != expected_root_class:
        raise MigrationContractError(
            "TARGET_ROOT_CLASS_MISMATCH",
            f"{mode} requires target root class {expected_root_class}",
        )
    if candidate["external_side_effect_ceiling"] != 0:
        raise MigrationContractError(
            "EXTERNAL_SIDE_EFFECT_CEILING_NONZERO", "external_side_effect_ceiling must equal 0"
        )
    if not isinstance(candidate["source_snapshot"], Mapping):
        raise MigrationContractError("SOURCE_SNAPSHOT_INVALID", "source_snapshot must be an object")
    if not isinstance(candidate["target_registry_baseline"], Mapping):
        raise MigrationContractError(
            "TARGET_BASELINE_INVALID", "target_registry_baseline must be an object"
        )
    failure_policy = candidate["failure_policy"]
    expected_policy = {
        "preflight_failure": "STOP_BEFORE_FIRST_APPEND",
        "unexpected_conflict": "STOP_AND_MARK_NOT_RUN",
        "runtime_error": "STOP_AND_PRESERVE_PARTIAL",
    }
    if failure_policy != expected_policy:
        raise MigrationContractError("FAILURE_POLICY_MISMATCH", "failure_policy is not the frozen policy")
    items = candidate["items"]
    if not isinstance(items, list) or not items:
        raise MigrationContractError("ITEMS_EMPTY", "items must be a non-empty list")
    normalized_items = [validate_item(item) for item in items]
    orders = [item["item_order"] for item in normalized_items]
    item_ids = [item["item_id"] for item in normalized_items]
    artifact_ids = [item["expected_artifact_id"] for item in normalized_items]
    if orders != sorted(orders) or orders != list(range(1, len(orders) + 1)):
        raise MigrationContractError(
            "ITEM_ORDER_INVALID", "items must be sorted and use contiguous item_order starting at 1"
        )
    _require_unique(item_ids, "item_id")
    _require_unique(artifact_ids, "expected_artifact_id")
    actual_inventory = inventory_digest(normalized_items)
    _require_hash(candidate["source_inventory_sha256"], "source_inventory_sha256")
    if candidate["source_inventory_sha256"].upper() != actual_inventory:
        raise MigrationContractError(
            "INVENTORY_HASH_MISMATCH",
            f"source_inventory_sha256 mismatch: expected {actual_inventory}",
        )
    candidate["source_inventory_sha256"] = actual_inventory
    candidate["items"] = normalized_items
    return candidate


def validate_item(item: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise MigrationContractError("ITEM_NOT_OBJECT", "migration item must be an object")
    candidate = copy.deepcopy(dict(item))
    unknown = set(candidate) - (_ITEM_REQUIRED | _ITEM_OPTIONAL)
    missing = _ITEM_REQUIRED - set(candidate)
    if missing or unknown:
        raise MigrationContractError(
            "ITEM_FIELDS_MISMATCH",
            f"missing={sorted(missing)}, unknown={sorted(unknown)}",
        )
    _require_stable_id(candidate["item_id"], "item_id")
    if not isinstance(candidate["item_order"], int) or isinstance(candidate["item_order"], bool) or candidate["item_order"] < 1:
        raise MigrationContractError("ITEM_ORDER_INVALID", "item_order must be a positive integer")
    _require_text(candidate["source_locator_class"], "source_locator_class")
    _require_text(candidate["source_locator"], "source_locator")
    if not isinstance(candidate["expected_bytes"], int) or isinstance(candidate["expected_bytes"], bool) or candidate["expected_bytes"] < 0:
        raise MigrationContractError("EXPECTED_BYTES_INVALID", "expected_bytes must be a non-negative integer")
    _require_hash(candidate["expected_sha256"], "expected_sha256")
    candidate["expected_sha256"] = candidate["expected_sha256"].upper()
    if candidate["rights"] not in {"self", "entrusted", "unknown", "blocked"}:
        raise MigrationContractError("RIGHTS_INVALID", "rights value is unsupported")
    if candidate["classification"] not in {"public", "internal", "restricted", "private"}:
        raise MigrationContractError("CLASSIFICATION_INVALID", "classification value is unsupported")
    _require_text(candidate["artifact_type"], "artifact_type")
    _require_text(candidate["identity_basis"], "identity_basis")
    expected_id = artifact_id_from_identity(candidate["identity_basis"])
    if candidate["expected_artifact_id"] != expected_id:
        raise MigrationContractError(
            "ARTIFACT_ID_DERIVATION_MISMATCH",
            f"expected_artifact_id must equal deterministic identity {expected_id}",
        )
    _require_optional_text(candidate["run_ref"], "run_ref")
    _require_optional_text(candidate["schema_ref"], "schema_ref")
    _require_text_list(candidate["evidence_refs"], "evidence_refs", allow_empty=False)
    for field in (
        "parent_artifacts",
        "unresolved_parent_requirements",
        "conflicting_candidates",
    ):
        if not isinstance(candidate[field], list):
            raise MigrationContractError("LINEAGE_FIELD_INVALID", f"{field} must be a list")
    if candidate["expected_disposition"] not in ITEM_DISPOSITIONS:
        raise MigrationContractError("DISPOSITION_INVALID", "expected_disposition is unsupported")
    _require_text(candidate["adapter_key"], "adapter_key")
    _require_text(candidate["adapter_version"], "adapter_version")
    _require_text_list(candidate.get("paper_ids", []), "paper_ids")
    _require_optional_datetime(candidate.get("created_at"), "created_at")
    _require_optional_datetime(candidate.get("registered_at"), "registered_at")
    if not isinstance(candidate.get("metadata", {}), Mapping):
        raise MigrationContractError("METADATA_INVALID", "metadata must be an object")
    candidate.setdefault("paper_ids", [])
    candidate.setdefault("created_at", None)
    candidate.setdefault("registered_at", None)
    candidate.setdefault("metadata", {})
    return candidate


def item_contract_rejection(item: Mapping[str, Any]) -> str | None:
    """Return the frozen capability-mode rejection code without reading source bytes."""

    rights = item["rights"]
    classification = item["classification"]
    if rights not in {"self", "entrusted"}:
        return "RIGHTS_NOT_AUTHORIZED"
    if classification not in {"public", "internal"}:
        return "CLASSIFICATION_NOT_AUTHORIZED"
    if item["source_locator_class"] not in {
        "RUN_LOCAL_PUBLIC_SAFE_FIXTURE",
        "FROZEN_PUBLIC_SAFE_FILE",
        "AUTHORIZED_LOCAL_METADATA",
    }:
        return "SOURCE_LOCATOR_CLASS_NOT_AUTHORIZED"
    return None


def build_item_envelope(batch: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    registered_at = item.get("registered_at") or batch["created_at"]
    metadata = copy.deepcopy(dict(item.get("metadata", {})))
    # Batch/item execution identities belong in receipts, not immutable
    # Artifact facts.  Keeping them out makes the same exact source proposal
    # idempotent across later batches.
    metadata["asset_migration_migration_identity"] = {
        "identity_basis": item["identity_basis"],
        "adapter_key": item["adapter_key"],
        "adapter_version": item["adapter_version"],
    }
    parent_ids = [link.get("parent_artifact_id") for link in item["parent_artifacts"]]
    envelope = build_envelope(
        artifact_id=item["expected_artifact_id"],
        artifact_type=item["artifact_type"],
        content_hash=item["expected_sha256"].lower(),
        locator_path=Path(item["source_locator"]),
        registered_at=registered_at,
        schema_ref=item["schema_ref"],
        run_ref=item["run_ref"],
        paper_ids=item.get("paper_ids", []),
        source_artifact_ids=[value for value in parent_ids if isinstance(value, str)],
        created_at=item.get("created_at"),
        parent_artifacts=item["parent_artifacts"],
        unresolved_parent_requirements=item["unresolved_parent_requirements"],
        conflicting_candidates=item["conflicting_candidates"],
        legacy_import=True,
        evidence_refs=item["evidence_refs"],
        metadata=metadata,
    )
    validate_envelope(envelope)
    return envelope


def planned_new_disposition(item: Mapping[str, Any]) -> str:
    if item["unresolved_parent_requirements"] or item["conflicting_candidates"]:
        return "UNRESOLVED_ACCEPTED"
    return "IMPORTED"


def validate_receipt_record(record: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "receipt_schema_version",
        "receipt_id",
        "batch_id",
        "item_id",
        "item_order",
        "phase",
        "recorded_at",
        "manifest_sha256",
        "item_sha256",
        "target_baseline_sha256",
        "outcome",
        "disposition",
        "event_id",
        "proof",
        "recovery_class",
        "error_code",
        "previous_receipt_sha256",
    }
    if not isinstance(record, Mapping):
        raise MigrationContractError("RECEIPT_NOT_OBJECT", "receipt must be an object")
    candidate = copy.deepcopy(dict(record))
    _require_exact_fields(candidate, expected, "receipt")
    if candidate["receipt_schema_version"] != RECEIPT_SCHEMA_VERSION:
        raise MigrationContractError("RECEIPT_VERSION_UNSUPPORTED", "receipt version mismatch")
    _require_stable_id(candidate["receipt_id"], "receipt_id")
    _require_stable_id(candidate["batch_id"], "batch_id")
    _require_stable_id(candidate["item_id"], "item_id")
    if not isinstance(candidate["item_order"], int) or candidate["item_order"] < 1:
        raise MigrationContractError("RECEIPT_ITEM_ORDER_INVALID", "receipt item_order invalid")
    if candidate["phase"] not in RECEIPT_PHASES:
        raise MigrationContractError("RECEIPT_PHASE_INVALID", "receipt phase invalid")
    _require_datetime(candidate["recorded_at"], "recorded_at")
    for field in ("manifest_sha256", "item_sha256", "target_baseline_sha256"):
        _require_hash(candidate[field], field)
        candidate[field] = candidate[field].upper()
    if candidate["previous_receipt_sha256"] is not None:
        _require_hash(candidate["previous_receipt_sha256"], "previous_receipt_sha256")
        candidate["previous_receipt_sha256"] = candidate["previous_receipt_sha256"].upper()
    for field in ("outcome", "event_id", "recovery_class", "error_code"):
        _require_optional_text(candidate[field], field)
    if candidate["disposition"] is not None and candidate["disposition"] not in ITEM_DISPOSITIONS:
        raise MigrationContractError("RECEIPT_DISPOSITION_INVALID", "receipt disposition invalid")
    if not isinstance(candidate["proof"], Mapping):
        raise MigrationContractError("RECEIPT_PROOF_INVALID", "receipt proof must be an object")
    if candidate["phase"] == "RECEIPT_COMMITTED" and candidate["disposition"] is None:
        raise MigrationContractError("RECEIPT_TERMINAL_MISSING", "committed receipt needs disposition")
    return candidate


def load_jsonl_receipts(path: Path | str) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    raw = target.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise MigrationContractError("RECEIPT_TRUNCATED", "receipt chain lacks final newline")
    records: list[dict[str, Any]] = []
    previous_sha: str | None = None
    seen_ids: set[str] = set()
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MigrationContractError(
                "RECEIPT_INVALID_JSON", f"invalid receipt JSON at line {line_number}"
            ) from exc
        record = validate_receipt_record(value)
        if record["receipt_id"] in seen_ids:
            raise MigrationContractError("RECEIPT_DUPLICATE_ID", "duplicate receipt_id")
        if record["previous_receipt_sha256"] != previous_sha:
            raise MigrationContractError("RECEIPT_CHAIN_BROKEN", "previous receipt hash mismatch")
        seen_ids.add(record["receipt_id"])
        previous_sha = sha256_bytes(canonical_bytes(record))
        records.append(record)
    return records


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _require_exact_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise MigrationContractError(
            f"{label.upper()}_FIELDS_MISMATCH",
            f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}",
        )


def _require_text(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MigrationContractError("TEXT_FIELD_INVALID", f"{field} must be non-empty text")


def _require_optional_text(value: Any, field: str) -> None:
    if value is not None:
        _require_text(value, field)


def _require_text_list(value: Any, field: str, *, allow_empty: bool = True) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise MigrationContractError("TEXT_LIST_INVALID", f"{field} must be a text list")
    if not allow_empty and not value:
        raise MigrationContractError("TEXT_LIST_EMPTY", f"{field} must not be empty")
    _require_unique(value, field)


def _require_hash(value: Any, field: str) -> None:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise MigrationContractError("HASH_INVALID", f"{field} must be a 64-character SHA-256")


def _require_datetime(value: Any, field: str) -> None:
    _require_text(value, field)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MigrationContractError("DATETIME_INVALID", f"{field} must be ISO-8601") from exc


def _require_optional_datetime(value: Any, field: str) -> None:
    if value is not None:
        _require_datetime(value, field)


def _require_stable_id(value: Any, field: str) -> None:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise MigrationContractError("STABLE_ID_INVALID", f"{field} is not a stable identifier")


def _require_unique(values: Iterable[Any], field: str) -> None:
    rendered = [canonical_json(value) for value in values]
    if len(rendered) != len(set(rendered)):
        raise MigrationContractError("DUPLICATE_VALUE", f"{field} contains duplicates")
