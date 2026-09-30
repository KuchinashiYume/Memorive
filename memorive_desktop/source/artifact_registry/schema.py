"""Open Artifact Envelope schema and validation helpers for ARTIFACT_REGISTRY."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import EnvelopeValidationError

ENVELOPE_SCHEMA_VERSION = "artifact-envelope-v1"
EVENT_SCHEMA_VERSION = "artifact_registry-registry-event-v1"
HASH_ALGORITHM = "sha256"

_ARTIFACT_ID = re.compile(r"^art_[0-9a-f]{32}$")
_EVENT_ID = re.compile(r"^evt_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_NAMESPACE = uuid.UUID("9e2847e3-04f8-4aa9-aa14-b9f8360bd571")


def canonical_json(value: Mapping[str, Any]) -> str:
    """Return the stable byte-comparison representation used by the registry."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path | str) -> str:
    """Hash exact file bytes; no semantic normalization is performed."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def new_artifact_id() -> str:
    """Create a new opaque identity for one persistent artifact instance."""

    return f"art_{uuid.uuid4().hex}"


def legacy_artifact_id(
    *,
    run_ref: str,
    artifact_type: str,
    content_hash: str,
    identity_scope: str,
) -> str:
    """Create a stable legacy-import ID without using the mutable locator.

    The import authority/run, semantic artifact slot, source scope and exact
    bytes participate.  A different run or changed bytes therefore yields a
    different artifact ID, while moving the same registered file does not.
    """

    for field, value in (
        ("run_ref", run_ref),
        ("artifact_type", artifact_type),
        ("identity_scope", identity_scope),
    ):
        _require_text(value, field)
    _require_hash(content_hash, "content_hash")
    name = "|".join(("Memorive", "ARTIFACT_REGISTRY", run_ref, identity_scope, artifact_type, content_hash))
    return f"art_{uuid.uuid5(_LEGACY_NAMESPACE, name).hex}"


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def parent_link(
    *,
    parent_artifact_id: str,
    parent_content_hash: str,
    relation: str,
    evidence_kind: str,
    source_field: str,
    evidence_ref: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one resolved parent link with an explicit evidence basis."""

    link = {
        "parent_artifact_id": parent_artifact_id,
        "parent_content_hash": parent_content_hash,
        "relation": relation,
        "evidence_basis": {
            "kind": evidence_kind,
            "source_field": source_field,
            "evidence_ref": evidence_ref,
            "details": dict(details or {}),
        },
    }
    _validate_parent_link(link)
    return link


def unresolved_requirement(
    requirement: str,
    reason_code: str,
    *,
    evidence_refs: Iterable[str],
    candidate_artifact_ids: Iterable[str] = (),
    search_hints: Iterable[str] = (),
) -> dict[str, Any]:
    """Describe a missing relationship without guessing a resolved parent."""

    value = {
        "requirement": requirement,
        "reason_code": reason_code,
        "evidence_refs": list(evidence_refs),
        "candidate_artifact_ids": list(candidate_artifact_ids),
        "search_hints": list(search_hints),
    }
    _validate_unresolved(value)
    return value


def build_envelope(
    *,
    artifact_id: str,
    artifact_type: str,
    content_hash: str,
    locator_path: Path | str,
    registered_at: str,
    artifact_schema_ref: str | None = None,
    run_ref: str | None = None,
    route_snapshot_ref: str | None = None,
    paper_ids: Iterable[str] = (),
    source_artifact_ids: Iterable[str] = (),
    created_at: str | None = None,
    resolved_parent_links: Iterable[Mapping[str, Any]] = (),
    unresolved_parent_requirements: Iterable[Mapping[str, Any]] = (),
    conflicting_candidates: Iterable[Mapping[str, Any]] = (),
    legacy_import: bool,
    evidence_refs: Iterable[str],
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate an immutable Artifact Envelope."""

    links = [copy.deepcopy(dict(item)) for item in resolved_parent_links]
    envelope = {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "artifact_schema_ref": artifact_schema_ref,
        "content_hash": {"algorithm": HASH_ALGORITHM, "value": content_hash},
        "locator": {"path": str(locator_path)},
        "run_ref": run_ref,
        "route_snapshot_ref": route_snapshot_ref,
        "source_scope": {
            "paper_ids": list(paper_ids),
            "source_artifact_ids": list(source_artifact_ids),
        },
        "created_at": created_at,
        "registered_at": registered_at,
        "parents": copy.deepcopy(links),
        "lineage": {
            "resolved_parent_links": copy.deepcopy(links),
            "unresolved_parent_requirements": [
                copy.deepcopy(dict(item)) for item in unresolved_parent_requirements
            ],
            "conflicting_candidates": [
                copy.deepcopy(dict(item)) for item in conflicting_candidates
            ],
        },
        "legacy_import": legacy_import,
        "evidence_refs": list(evidence_refs),
        "metadata": copy.deepcopy(dict(metadata or {})),
    }
    validate_envelope(envelope)
    return envelope


def validate_envelope(envelope: Mapping[str, Any]) -> None:
    """Validate one envelope without coercing values or closing open types."""

    if not isinstance(envelope, Mapping):
        raise EnvelopeValidationError("envelope must be an object")
    expected = {
        "schema_version",
        "artifact_id",
        "artifact_type",
        "artifact_schema_ref",
        "content_hash",
        "locator",
        "run_ref",
        "route_snapshot_ref",
        "source_scope",
        "created_at",
        "registered_at",
        "parents",
        "lineage",
        "legacy_import",
        "evidence_refs",
        "metadata",
    }
    if set(envelope) != expected:
        raise EnvelopeValidationError(
            "envelope fields mismatch: "
            f"missing={sorted(expected - set(envelope))}, extra={sorted(set(envelope) - expected)}"
        )
    if envelope.get("schema_version") != ENVELOPE_SCHEMA_VERSION:
        raise EnvelopeValidationError(
            f"schema_version must equal {ENVELOPE_SCHEMA_VERSION!r}"
        )
    _require_pattern(envelope.get("artifact_id"), _ARTIFACT_ID, "artifact_id")
    _require_text(envelope.get("artifact_type"), "artifact_type")
    _require_optional_text(envelope.get("artifact_schema_ref"), "artifact_schema_ref")

    content_hash = envelope.get("content_hash")
    if not isinstance(content_hash, Mapping) or set(content_hash) != {"algorithm", "value"}:
        raise EnvelopeValidationError("content_hash must contain algorithm and value")
    if content_hash.get("algorithm") != HASH_ALGORITHM:
        raise EnvelopeValidationError("content_hash.algorithm must equal 'sha256'")
    _require_hash(content_hash.get("value"), "content_hash.value")

    locator = envelope.get("locator")
    if not isinstance(locator, Mapping) or set(locator) != {"path"}:
        raise EnvelopeValidationError("locator must contain only path")
    _require_text(locator.get("path"), "locator.path")
    _require_optional_text(envelope.get("run_ref"), "run_ref")
    _require_optional_text(envelope.get("route_snapshot_ref"), "route_snapshot_ref")

    scope = envelope.get("source_scope")
    if not isinstance(scope, Mapping) or set(scope) != {"paper_ids", "source_artifact_ids"}:
        raise EnvelopeValidationError(
            "source_scope must contain paper_ids and source_artifact_ids"
        )
    _require_text_list(scope.get("paper_ids"), "source_scope.paper_ids", unique=True)
    _require_text_list(
        scope.get("source_artifact_ids"),
        "source_scope.source_artifact_ids",
        unique=True,
        pattern=_ARTIFACT_ID,
    )
    _require_optional_datetime(envelope.get("created_at"), "created_at")
    _require_datetime(envelope.get("registered_at"), "registered_at")

    lineage = envelope.get("lineage")
    if not isinstance(lineage, Mapping) or set(lineage) != {
        "resolved_parent_links",
        "unresolved_parent_requirements",
        "conflicting_candidates",
    }:
        raise EnvelopeValidationError("lineage fields are incomplete or unexpected")
    links = lineage.get("resolved_parent_links")
    if not isinstance(links, list):
        raise EnvelopeValidationError("resolved_parent_links must be a list")
    for link in links:
        _validate_parent_link(link)
        if link["parent_artifact_id"] == envelope["artifact_id"]:
            raise EnvelopeValidationError("an artifact cannot be its own parent")
    parents = envelope.get("parents")
    if not isinstance(parents, list) or canonical_json({"x": parents}) != canonical_json({"x": links}):
        raise EnvelopeValidationError(
            "parents must exactly mirror lineage.resolved_parent_links"
        )
    unresolved = lineage.get("unresolved_parent_requirements")
    if not isinstance(unresolved, list):
        raise EnvelopeValidationError("unresolved_parent_requirements must be a list")
    for value in unresolved:
        _validate_unresolved(value)
    conflicts = lineage.get("conflicting_candidates")
    if not isinstance(conflicts, list):
        raise EnvelopeValidationError("conflicting_candidates must be a list")
    for value in conflicts:
        _validate_conflict(value)
    if not isinstance(envelope.get("legacy_import"), bool):
        raise EnvelopeValidationError("legacy_import must be boolean")
    _require_text_list(envelope.get("evidence_refs"), "evidence_refs", allow_empty=False)
    if not isinstance(envelope.get("metadata"), Mapping):
        raise EnvelopeValidationError("metadata must be an object")


def lineage_status(envelope: Mapping[str, Any]) -> str:
    """Derive a display value; lineage detail remains authoritative."""

    validate_envelope(envelope)
    lineage = envelope["lineage"]
    if lineage["conflicting_candidates"]:
        return "conflicted"
    if lineage["resolved_parent_links"] and lineage["unresolved_parent_requirements"]:
        return "partial"
    if lineage["resolved_parent_links"]:
        return "complete"
    if lineage["unresolved_parent_requirements"]:
        return "unknown"
    return "complete"


def validate_event(event: Mapping[str, Any]) -> None:
    """Validate an append-only registry event and its immutable payload."""

    if not isinstance(event, Mapping):
        raise EnvelopeValidationError("registry event must be an object")
    required = {"event_schema_version", "event_id", "event_type", "recorded_at", "payload"}
    if set(event) != required:
        raise EnvelopeValidationError("registry event fields mismatch")
    if event.get("event_schema_version") != EVENT_SCHEMA_VERSION:
        raise EnvelopeValidationError("unsupported event_schema_version")
    _require_pattern(event.get("event_id"), _EVENT_ID, "event_id")
    _require_text(event.get("event_type"), "event_type")
    _require_datetime(event.get("recorded_at"), "recorded_at")
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise EnvelopeValidationError("event payload must be an object")
    event_type = event["event_type"]
    if event_type == "artifact_registered":
        validate_envelope(payload)
    elif event_type == "lineage_resolution":
        _require_exact_fields(
            payload,
            {
                "artifact_id",
                "resolved_parent_links",
                "resolved_requirements",
                "evidence_refs",
            },
            "lineage_resolution payload",
        )
        _require_pattern(payload.get("artifact_id"), _ARTIFACT_ID, "artifact_id")
        if not isinstance(payload.get("resolved_parent_links"), list) or not payload["resolved_parent_links"]:
            raise EnvelopeValidationError("lineage_resolution requires parent links")
        for link in payload["resolved_parent_links"]:
            _validate_parent_link(link)
        _require_text_list(
            payload.get("resolved_requirements"),
            "resolved_requirements",
            allow_empty=False,
            unique=True,
        )
        _require_text_list(payload.get("evidence_refs"), "evidence_refs", allow_empty=False)
    elif event_type == "locator_updated":
        _require_exact_fields(
            payload,
            {"artifact_id", "content_hash", "locator", "evidence_refs"},
            "locator_updated payload",
        )
        _require_pattern(payload.get("artifact_id"), _ARTIFACT_ID, "artifact_id")
        _require_hash(payload.get("content_hash"), "content_hash")
        locator = payload.get("locator")
        if not isinstance(locator, Mapping) or set(locator) != {"path"}:
            raise EnvelopeValidationError("locator_updated locator must contain path")
        _require_text(locator.get("path"), "locator.path")
        _require_text_list(payload.get("evidence_refs"), "evidence_refs", allow_empty=False)
    elif event_type == "state_transition_observed":
        _require_exact_fields(
            payload,
            {"artifact_id", "state_owner", "event_ref", "from_status", "to_status"},
            "state_transition_observed payload",
        )
        _require_pattern(payload.get("artifact_id"), _ARTIFACT_ID, "artifact_id")
        for field in ("state_owner", "event_ref", "from_status", "to_status"):
            _require_text(payload.get(field), field)
        if payload["state_owner"] != "KNOWLEDGE_ADMISSION":
            raise EnvelopeValidationError("Card review state owner must remain KNOWLEDGE_ADMISSION")
    else:
        raise EnvelopeValidationError(f"unsupported event_type: {event_type!r}")


def _validate_parent_link(link: Any) -> None:
    if not isinstance(link, Mapping):
        raise EnvelopeValidationError("parent link must be an object")
    _require_exact_fields(
        link,
        {"parent_artifact_id", "parent_content_hash", "relation", "evidence_basis"},
        "parent link",
    )
    _require_pattern(link.get("parent_artifact_id"), _ARTIFACT_ID, "parent_artifact_id")
    _require_hash(link.get("parent_content_hash"), "parent_content_hash")
    _require_text(link.get("relation"), "relation")
    evidence = link.get("evidence_basis")
    if not isinstance(evidence, Mapping) or set(evidence) != {
        "kind",
        "source_field",
        "evidence_ref",
        "details",
    }:
        raise EnvelopeValidationError("evidence_basis fields mismatch")
    for field in ("kind", "source_field", "evidence_ref"):
        _require_text(evidence.get(field), f"evidence_basis.{field}")
    if not isinstance(evidence.get("details"), Mapping):
        raise EnvelopeValidationError("evidence_basis.details must be an object")


def _validate_unresolved(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise EnvelopeValidationError("unresolved requirement must be an object")
    _require_exact_fields(
        value,
        {"requirement", "reason_code", "evidence_refs", "candidate_artifact_ids", "search_hints"},
        "unresolved requirement",
    )
    _require_text(value.get("requirement"), "requirement")
    _require_text(value.get("reason_code"), "reason_code")
    _require_text_list(value.get("evidence_refs"), "evidence_refs", allow_empty=False)
    _require_text_list(
        value.get("candidate_artifact_ids"),
        "candidate_artifact_ids",
        pattern=_ARTIFACT_ID,
    )
    _require_text_list(value.get("search_hints"), "search_hints")


def _validate_conflict(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise EnvelopeValidationError("conflicting candidate must be an object")
    _require_exact_fields(
        value,
        {"requirement", "reason_code", "candidate_artifact_ids", "evidence_refs"},
        "conflicting candidate",
    )
    _require_text(value.get("requirement"), "requirement")
    _require_text(value.get("reason_code"), "reason_code")
    _require_text_list(
        value.get("candidate_artifact_ids"),
        "candidate_artifact_ids",
        allow_empty=False,
        unique=True,
        pattern=_ARTIFACT_ID,
    )
    _require_text_list(value.get("evidence_refs"), "evidence_refs", allow_empty=False)


def _require_exact_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise EnvelopeValidationError(f"{label} fields mismatch")


def _require_text(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise EnvelopeValidationError(f"{field} must be a non-empty string")


def _require_optional_text(value: Any, field: str) -> None:
    if value is not None:
        _require_text(value, field)


def _require_pattern(value: Any, pattern: re.Pattern[str], field: str) -> None:
    _require_text(value, field)
    if not pattern.fullmatch(value):
        raise EnvelopeValidationError(f"{field} has an invalid format")


def _require_hash(value: Any, field: str) -> None:
    _require_pattern(value, _SHA256, field)


def _require_text_list(
    value: Any,
    field: str,
    *,
    allow_empty: bool = True,
    unique: bool = False,
    pattern: re.Pattern[str] | None = None,
) -> None:
    if not isinstance(value, list):
        raise EnvelopeValidationError(f"{field} must be a list")
    if not allow_empty and not value:
        raise EnvelopeValidationError(f"{field} must not be empty")
    for item in value:
        _require_text(item, f"{field} entry")
        if pattern is not None and not pattern.fullmatch(item):
            raise EnvelopeValidationError(f"{field} entry has an invalid format")
    if unique and len(value) != len(set(value)):
        raise EnvelopeValidationError(f"{field} entries must be unique")


def _require_datetime(value: Any, field: str) -> None:
    _require_text(value, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EnvelopeValidationError(f"{field} must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EnvelopeValidationError(f"{field} must include a timezone")


def _require_optional_datetime(value: Any, field: str) -> None:
    if value is not None:
        _require_datetime(value, field)
