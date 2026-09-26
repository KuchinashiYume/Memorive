"""Immutable Registry and compatibility-policy snapshot helpers for ServiceContracts/Configuration."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import ArtifactConflictError, EnvelopeValidationError
from .registry_v2 import ArtifactRegistry
from .schema_v2 import (
    build_envelope,
    canonical_json,
    new_artifact_id,
    sha256_file,
    validate_envelope,
)

REGISTRY_SNAPSHOT_SCHEMA_VERSION = "registry-snapshot-v1"
POLICY_SNAPSHOT_SCHEMA_VERSION = "compatibility-policy-snapshot-v1"


def build_registry_snapshot_envelope(
    registry: ArtifactRegistry,
    payload_path: Path | str,
    *,
    captured_at: str,
    registered_at: str,
    run_ref: str,
    route_snapshot_ref: str | None = None,
    evidence_refs: Iterable[str] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a lightweight Artifact proving one normalized Registry prefix."""

    _require_datetime(captured_at, "captured_at")
    events = registry.read_events()
    if not events:
        raise EnvelopeValidationError("registry_snapshot requires at least one record")
    prefix = registry.normalized_prefix_bytes(events)
    prefix_hash = hashlib.sha256(prefix).hexdigest()
    last_record_id = events[-1]["event_id"]
    snapshot_seed = f"{last_record_id}|{len(events)}|{prefix_hash}|{captured_at}"
    snapshot_id = "snapshot_" + hashlib.sha256(snapshot_seed.encode("utf-8")).hexdigest()[:32]
    payload = {
        "artifact_type": "registry_snapshot",
        "snapshot_id": snapshot_id,
        "registry_content_hash": prefix_hash,
        "last_record_id": last_record_id,
        "record_count": len(events),
        "captured_at": captured_at,
        "schema_version": REGISTRY_SNAPSHOT_SCHEMA_VERSION,
    }
    target = Path(payload_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (canonical_json(payload) + "\n").encode("utf-8")
    with target.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
    refs = list(evidence_refs) or [f"artifact_registry-registry-event:{last_record_id}"]
    envelope = build_envelope(
        artifact_id=new_artifact_id(),
        artifact_type="registry_snapshot",
        schema_ref="memorive://artifact_registry/registry-snapshot/v1",
        content_hash=sha256_file(target),
        locator_path=target,
        registered_at=registered_at,
        run_ref=run_ref,
        route_snapshot_ref=route_snapshot_ref,
        created_at=captured_at,
        legacy_import=False,
        evidence_refs=refs,
        metadata={"snapshot_contract": REGISTRY_SNAPSHOT_SCHEMA_VERSION},
    )
    return envelope, payload


def verify_registry_snapshot(
    registry: ArtifactRegistry, envelope: Mapping[str, Any]
) -> dict[str, Any]:
    """Verify Artifact bytes plus hash/count/last-ID of the referenced prefix."""

    validate_envelope(envelope)
    if envelope["artifact_type"] != "registry_snapshot":
        raise EnvelopeValidationError("expected artifact_type registry_snapshot")
    path = Path(envelope["locator"]["path"])
    if not path.is_file():
        raise ArtifactConflictError(f"registry_snapshot payload is missing: {path}")
    actual_artifact_hash = sha256_file(path)
    if actual_artifact_hash != envelope["content_hash"]["value"]:
        raise ArtifactConflictError("registry_snapshot Artifact bytes changed")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvelopeValidationError("registry_snapshot payload is invalid JSON") from exc
    expected_fields = {
        "artifact_type",
        "snapshot_id",
        "registry_content_hash",
        "last_record_id",
        "record_count",
        "captured_at",
        "schema_version",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise EnvelopeValidationError("registry_snapshot payload fields mismatch")
    if payload["artifact_type"] != "registry_snapshot":
        raise EnvelopeValidationError("registry_snapshot payload artifact_type mismatch")
    if payload["schema_version"] != REGISTRY_SNAPSHOT_SCHEMA_VERSION:
        raise EnvelopeValidationError("registry_snapshot schema_version mismatch")
    _require_datetime(payload["captured_at"], "captured_at")
    if not isinstance(payload["record_count"], int) or payload["record_count"] <= 0:
        raise EnvelopeValidationError("registry_snapshot record_count must be positive")
    prefix_events = registry.events_through(payload["last_record_id"])
    if len(prefix_events) != payload["record_count"]:
        raise ArtifactConflictError("registry_snapshot record_count mismatch")
    if prefix_events[-1]["event_id"] != payload["last_record_id"]:
        raise ArtifactConflictError("registry_snapshot last_record_id mismatch")
    actual_prefix_hash = hashlib.sha256(
        registry.normalized_prefix_bytes(prefix_events)
    ).hexdigest()
    if actual_prefix_hash != payload["registry_content_hash"]:
        raise ArtifactConflictError("registry_snapshot Registry prefix hash mismatch")
    return payload


def build_compatibility_policy_snapshot_envelope(
    policy_path: Path | str,
    *,
    policy_id: str,
    policy_version: str,
    policy_schema_version: str,
    created_at: str,
    registered_at: str,
    run_ref: str,
    route_snapshot_ref: str | None = None,
    evidence_refs: Iterable[str],
) -> dict[str, Any]:
    """Wrap exact immutable policy bytes in a registered Artifact identity."""

    source = Path(policy_path)
    if not source.is_file():
        raise ArtifactConflictError(f"compatibility policy file is missing: {source}")
    for field, value in (
        ("policy_id", policy_id),
        ("policy_version", policy_version),
        ("policy_schema_version", policy_schema_version),
    ):
        if not isinstance(value, str) or not value.strip():
            raise EnvelopeValidationError(f"{field} must be a non-empty string")
    _require_datetime(created_at, "created_at")
    return build_envelope(
        artifact_id=new_artifact_id(),
        artifact_type="compatibility_policy_snapshot",
        schema_ref="memorive://artifact_registry/compatibility-policy-snapshot/v1",
        content_hash=sha256_file(source),
        locator_path=source,
        registered_at=registered_at,
        run_ref=run_ref,
        route_snapshot_ref=route_snapshot_ref,
        created_at=created_at,
        legacy_import=False,
        evidence_refs=evidence_refs,
        metadata={
            "snapshot_contract": POLICY_SNAPSHOT_SCHEMA_VERSION,
            "policy_id": policy_id,
            "policy_version": policy_version,
            "schema_version": policy_schema_version,
            "created_at": created_at,
        },
    )


def verify_compatibility_policy_snapshot(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Verify exact policy bytes and the immutable snapshot metadata contract."""

    validate_envelope(envelope)
    if envelope["artifact_type"] != "compatibility_policy_snapshot":
        raise EnvelopeValidationError(
            "expected artifact_type compatibility_policy_snapshot"
        )
    metadata = envelope["metadata"]
    required = {
        "snapshot_contract",
        "policy_id",
        "policy_version",
        "schema_version",
        "created_at",
    }
    if set(metadata) != required:
        raise EnvelopeValidationError("compatibility policy metadata fields mismatch")
    if metadata["snapshot_contract"] != POLICY_SNAPSHOT_SCHEMA_VERSION:
        raise EnvelopeValidationError("compatibility policy snapshot contract mismatch")
    for field in ("policy_id", "policy_version", "schema_version"):
        if not isinstance(metadata[field], str) or not metadata[field].strip():
            raise EnvelopeValidationError(f"compatibility policy {field} is invalid")
    _require_datetime(metadata["created_at"], "created_at")
    path = Path(envelope["locator"]["path"])
    if not path.is_file():
        raise ArtifactConflictError(f"compatibility policy bytes are missing: {path}")
    if sha256_file(path) != envelope["content_hash"]["value"]:
        raise ArtifactConflictError("compatibility policy bytes changed")
    return dict(metadata)


def _require_datetime(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise EnvelopeValidationError(f"{field} must be a non-empty ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EnvelopeValidationError(f"{field} must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EnvelopeValidationError(f"{field} must include a timezone")
