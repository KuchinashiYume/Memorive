"""ARTIFACT_REGISTRY Artifact Envelope v2 schema and versioned event validation.

The v2 persistence contract has one authoritative direct-parent field:
``parent_artifacts``.  ``resolved_parent_links`` is exposed only as a derived
read helper and is never serialized by this module.
"""

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

ENVELOPE_SCHEMA_VERSION = "artifact-envelope-v2"
EVENT_SCHEMA_VERSION = "artifact_registry-registry-event-v2"
LEGACY_ENVELOPE_SCHEMA_VERSION = "artifact-envelope-v1"
LEGACY_EVENT_SCHEMA_VERSION = "artifact_registry-registry-event-v1"
HASH_ALGORITHM = "sha256"

_ARTIFACT_ID = re.compile(r"^art_[0-9a-f]{32}$")
_EVENT_ID = re.compile(r"^evt_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_NAMESPACE = uuid.UUID("9e2847e3-04f8-4aa9-aa14-b9f8360bd571")
_LEDGER_TYPES = {"attempt", "paper_outcome", "publication"}
_LEDGER_RECORD_TYPES = {"ledger_record", "run_terminal_event"}


def canonical_json(value: Any) -> str:
    """Return the stable UTF-8 comparison representation."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path | str) -> str:
    """Hash exact file bytes without semantic normalization."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def new_artifact_id() -> str:
    return f"art_{uuid.uuid4().hex}"


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def legacy_artifact_id(
    *,
    run_ref: str,
    artifact_type: str,
    content_hash: str,
    identity_scope: str,
) -> str:
    """Create a stable legacy-import identity without using a locator."""

    for field, value in (
        ("run_ref", run_ref),
        ("artifact_type", artifact_type),
        ("identity_scope", identity_scope),
    ):
        _require_text(value, field)
    _require_hash(content_hash, "content_hash")
    name = "|".join(("Memorive", "ARTIFACT_REGISTRY", run_ref, identity_scope, artifact_type, content_hash))
    return f"art_{uuid.uuid5(_LEGACY_NAMESPACE, name).hex}"


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
    """Build one verified direct-parent reference with explicit evidence."""

    value = {
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
    _validate_parent_link(value)
    return value


def unresolved_requirement(
    requirement: str,
    reason_code: str,
    *,
    evidence_refs: Iterable[str],
    candidate_artifact_ids: Iterable[str] = (),
    search_hints: Iterable[str] = (),
) -> dict[str, Any]:
    """Describe a required relationship without inventing a parent."""

    value = {
        "requirement": requirement,
        "reason_code": reason_code,
        "evidence_refs": list(evidence_refs),
        "candidate_artifact_ids": list(candidate_artifact_ids),
        "search_hints": list(search_hints),
    }
    _validate_unresolved(value)
    return value


def ledger_record_ref(
    *,
    record_type: str,
    record_id: str,
    run_id: str,
    contract_ref: str,
    ledger_type: str | None = None,
) -> dict[str, Any]:
    """Build a typed reference to DataContracts without pretending it is an Artifact."""

    value = {
        "record_type": record_type,
        "ledger_type": ledger_type,
        "record_id": record_id,
        "run_id": run_id,
        "contract_ref": contract_ref,
    }
    _validate_ledger_record_ref(value)
    return value


def build_envelope(
    *,
    artifact_id: str,
    artifact_type: str,
    content_hash: str,
    locator_path: Path | str,
    registered_at: str,
    schema_ref: str | None = None,
    run_ref: str | None = None,
    route_snapshot_ref: str | None = None,
    paper_ids: Iterable[str] = (),
    source_artifact_ids: Iterable[str] = (),
    created_at: str | None = None,
    parent_artifacts: Iterable[Mapping[str, Any]] = (),
    unresolved_parent_requirements: Iterable[Mapping[str, Any]] = (),
    conflicting_candidates: Iterable[Mapping[str, Any]] = (),
    ledger_record_refs: Iterable[Mapping[str, Any]] = (),
    legacy_import: bool,
    evidence_refs: Iterable[str],
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an immutable v2 Envelope with no duplicated lineage authority."""

    envelope = {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "schema_ref": schema_ref,
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
        "parent_artifacts": [copy.deepcopy(dict(item)) for item in parent_artifacts],
        "unresolved_parent_requirements": [
            copy.deepcopy(dict(item)) for item in unresolved_parent_requirements
        ],
        "conflicting_candidates": [
            copy.deepcopy(dict(item)) for item in conflicting_candidates
        ],
        "ledger_record_refs": [
            copy.deepcopy(dict(item)) for item in ledger_record_refs
        ],
        "legacy_import": legacy_import,
        "evidence_refs": list(evidence_refs),
        "metadata": copy.deepcopy(dict(metadata or {})),
    }
    validate_envelope(envelope)
    return envelope


def validate_envelope(envelope: Mapping[str, Any]) -> None:
    """Validate a writable v2 Envelope; legacy validation is read-only."""

    if not isinstance(envelope, Mapping):
        raise EnvelopeValidationError("envelope must be an object")
    expected = {
        "schema_version",
        "artifact_id",
        "artifact_type",
        "schema_ref",
        "content_hash",
        "locator",
        "run_ref",
        "route_snapshot_ref",
        "source_scope",
        "created_at",
        "registered_at",
        "parent_artifacts",
        "unresolved_parent_requirements",
        "conflicting_candidates",
        "ledger_record_refs",
        "legacy_import",
        "evidence_refs",
        "metadata",
    }
    _require_exact_fields(envelope, expected, "envelope")
    if envelope.get("schema_version") != ENVELOPE_SCHEMA_VERSION:
        raise EnvelopeValidationError(
            f"schema_version must equal {ENVELOPE_SCHEMA_VERSION!r}"
        )
    _require_pattern(envelope.get("artifact_id"), _ARTIFACT_ID, "artifact_id")
    _require_text(envelope.get("artifact_type"), "artifact_type")
    _require_optional_text(envelope.get("schema_ref"), "schema_ref")

    content_hash = envelope.get("content_hash")
    if not isinstance(content_hash, Mapping) or set(content_hash) != {"algorithm", "value"}:
        raise EnvelopeValidationError("content_hash must contain algorithm and value")
    if content_hash.get("algorithm") != HASH_ALGORITHM:
        raise EnvelopeValidationError("content_hash.algorithm must equal 'sha256'")
    _require_hash(content_hash.get("value"), "content_hash.value")

    locator = envelope.get("locator")
    if not isinstance(locator, Mapping) or set(locator) != {"path"}:
        raise EnvelopeValidationError("locator must contain only registration-time path")
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

    parents = envelope.get("parent_artifacts")
    if not isinstance(parents, list):
        raise EnvelopeValidationError("parent_artifacts must be a list")
    parent_keys: set[str] = set()
    for link in parents:
        _validate_parent_link(link)
        if link["parent_artifact_id"] == envelope["artifact_id"]:
            raise EnvelopeValidationError("an artifact cannot be its own parent")
        key = canonical_json(link)
        if key in parent_keys:
            raise EnvelopeValidationError("parent_artifacts must not contain duplicates")
        parent_keys.add(key)

    unresolved = envelope.get("unresolved_parent_requirements")
    if not isinstance(unresolved, list):
        raise EnvelopeValidationError("unresolved_parent_requirements must be a list")
    for value in unresolved:
        _validate_unresolved(value)
    conflicts = envelope.get("conflicting_candidates")
    if not isinstance(conflicts, list):
        raise EnvelopeValidationError("conflicting_candidates must be a list")
    for value in conflicts:
        _validate_conflict(value)
    unresolved_names = {value["requirement"] for value in unresolved}
    conflict_names = {value["requirement"] for value in conflicts}
    if unresolved_names & conflict_names:
        raise EnvelopeValidationError(
            "a parent requirement cannot be both unresolved and conflicted"
        )

    ledger_refs = envelope.get("ledger_record_refs")
    if not isinstance(ledger_refs, list):
        raise EnvelopeValidationError("ledger_record_refs must be a list")
    ledger_keys: set[str] = set()
    for value in ledger_refs:
        _validate_ledger_record_ref(value)
        key = canonical_json(value)
        if key in ledger_keys:
            raise EnvelopeValidationError("ledger_record_refs must not contain duplicates")
        ledger_keys.add(key)
    if not isinstance(envelope.get("legacy_import"), bool):
        raise EnvelopeValidationError("legacy_import must be boolean")
    _require_text_list(envelope.get("evidence_refs"), "evidence_refs", allow_empty=False)
    if not isinstance(envelope.get("metadata"), Mapping):
        raise EnvelopeValidationError("metadata must be an object")


def validate_readable_envelope(envelope: Mapping[str, Any]) -> None:
    """Validate either current v2 or historical v1 without rewriting it."""

    version = envelope.get("schema_version") if isinstance(envelope, Mapping) else None
    if version == ENVELOPE_SCHEMA_VERSION:
        validate_envelope(envelope)
        return
    if version == LEGACY_ENVELOPE_SCHEMA_VERSION:
        from .schema import validate_envelope as validate_v1_envelope

        validate_v1_envelope(envelope)
        return
    raise EnvelopeValidationError(f"unsupported envelope schema_version: {version!r}")


def resolved_parent_links(envelope: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return a derived read-only alias; never persist this result separately."""

    validate_readable_envelope(envelope)
    if envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION:
        return copy.deepcopy(envelope["parent_artifacts"])
    return copy.deepcopy(envelope["lineage"]["resolved_parent_links"])


def lineage_status(envelope: Mapping[str, Any]) -> str:
    """Derive display state from the authoritative lineage fields."""

    validate_readable_envelope(envelope)
    if envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION:
        parents = envelope["parent_artifacts"]
        unresolved = envelope["unresolved_parent_requirements"]
        conflicts = envelope["conflicting_candidates"]
    else:
        parents = envelope["lineage"]["resolved_parent_links"]
        unresolved = envelope["lineage"]["unresolved_parent_requirements"]
        conflicts = envelope["lineage"]["conflicting_candidates"]
    if conflicts:
        return "conflicted"
    if parents and unresolved:
        return "partial"
    if parents:
        return "complete"
    if unresolved:
        return "unknown"
    return "complete"


def content_hash_value(envelope: Mapping[str, Any]) -> str:
    validate_readable_envelope(envelope)
    return str(envelope["content_hash"]["value"])


def validate_event(event: Mapping[str, Any]) -> None:
    """Validate v2 events and historical v1 events for read compatibility."""

    version = event.get("event_schema_version") if isinstance(event, Mapping) else None
    if version == LEGACY_EVENT_SCHEMA_VERSION:
        from .schema import validate_event as validate_v1_event

        validate_v1_event(event)
        return
    if version != EVENT_SCHEMA_VERSION:
        raise EnvelopeValidationError(f"unsupported event_schema_version: {version!r}")
    _validate_v2_event(event)


def _validate_v2_event(event: Mapping[str, Any]) -> None:
    if not isinstance(event, Mapping):
        raise EnvelopeValidationError("registry event must be an object")
    required = {"event_schema_version", "event_id", "event_type", "recorded_at", "payload"}
    _require_exact_fields(event, required, "registry event")
    _require_pattern(event.get("event_id"), _EVENT_ID, "event_id")
    _require_text(event.get("event_type"), "event_type")
    _require_datetime(event.get("recorded_at"), "recorded_at")
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise EnvelopeValidationError("event payload must be an object")
    event_type = event["event_type"]
    if event_type == "artifact_registered":
        validate_envelope(payload)
    elif event_type == "lineage_resolution_event":
        _require_exact_fields(
            payload,
            {
                "artifact_id",
                "prior_head_event_id",
                "new_envelope",
                "resolved_requirements",
                "evidence_refs",
            },
            "lineage_resolution_event payload",
        )
        _require_pattern(payload.get("artifact_id"), _ARTIFACT_ID, "artifact_id")
        _require_pattern(payload.get("prior_head_event_id"), _EVENT_ID, "prior_head_event_id")
        validate_envelope(payload.get("new_envelope"))
        if payload["new_envelope"]["artifact_id"] != payload["artifact_id"]:
            raise EnvelopeValidationError("lineage head artifact_id mismatch")
        _require_text_list(
            payload.get("resolved_requirements"),
            "resolved_requirements",
            allow_empty=False,
            unique=True,
        )
        _require_text_list(payload.get("evidence_refs"), "evidence_refs", allow_empty=False)
    elif event_type == "artifact_location_event":
        _require_exact_fields(
            payload,
            {"artifact_id", "content_hash", "locator", "evidence_refs"},
            "artifact_location_event payload",
        )
        _require_pattern(payload.get("artifact_id"), _ARTIFACT_ID, "artifact_id")
        _require_hash(payload.get("content_hash"), "content_hash")
        locator = payload.get("locator")
        if not isinstance(locator, Mapping) or set(locator) != {"path"}:
            raise EnvelopeValidationError("artifact_location_event locator must contain path")
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
        raise EnvelopeValidationError(f"unsupported v2 event_type: {event_type!r}")


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


def _validate_ledger_record_ref(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise EnvelopeValidationError("ledger_record_ref must be an object")
    _require_exact_fields(
        value,
        {"record_type", "ledger_type", "record_id", "run_id", "contract_ref"},
        "ledger_record_ref",
    )
    if value.get("record_type") not in _LEDGER_RECORD_TYPES:
        raise EnvelopeValidationError("ledger_record_ref.record_type is unsupported")
    _require_text(value.get("record_id"), "ledger_record_ref.record_id")
    _require_text(value.get("run_id"), "ledger_record_ref.run_id")
    _require_text(value.get("contract_ref"), "ledger_record_ref.contract_ref")
    if value["record_type"] == "ledger_record":
        if value.get("ledger_type") not in _LEDGER_TYPES:
            raise EnvelopeValidationError("ledger_record requires a DataContracts ledger_type")
    elif value.get("ledger_type") is not None:
        raise EnvelopeValidationError("run_terminal_event ledger_type must be null")


def _require_exact_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise EnvelopeValidationError(
            f"{label} fields mismatch: missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


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
