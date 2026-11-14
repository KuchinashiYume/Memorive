"""Strict read-only adapter for the formal ARTIFACT_REGISTRY v1/v2 registry event wire.

This module deliberately does not import ``pathlib``.  A registry locator is
data, not a filesystem capability: it is validated as opaque text and only an
exact UTF-8 hash is projected.
"""

from __future__ import annotations

import re
from typing import Any

from .canonical import parse_datetime, require_safe_text, sha256_bytes
from .errors import SourceRecordError

ARTIFACT_ID = re.compile(r"^art_[0-9a-f]{32}$")
EVENT_ID = re.compile(r"^evt_[0-9a-f]{32}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _fail(code: str, message: str, *fields: str) -> None:
    raise SourceRecordError(code, message, field_names=fields)


def _exact(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", f"{label} fields mismatch", label)
    return value


def _text(value: Any, field: str) -> str:
    try:
        return require_safe_text(value, field)
    except (TypeError, ValueError) as exc:
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", str(exc), field)
    raise AssertionError("unreachable")


def _pattern(value: Any, pattern: re.Pattern[str], field: str) -> str:
    result = _text(value, field)
    if not pattern.fullmatch(result):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", f"{field} has an invalid format", field)
    return result


def _timestamp(value: Any, field: str) -> str:
    try:
        return parse_datetime(_text(value, field), field_name=field).isoformat()
    except ValueError as exc:
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", str(exc), field)
    raise AssertionError("unreachable")


def _optional_text(value: Any, field: str) -> str | None:
    return None if value is None else _text(value, field)


def _text_list(
    value: Any,
    field: str,
    *,
    allow_empty: bool = True,
    unique: bool = False,
    pattern: re.Pattern[str] | None = None,
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", f"{field} must be a valid list", field)
    result = []
    for index, item in enumerate(value):
        text = _text(item, f"{field}[{index}]")
        if pattern is not None and not pattern.fullmatch(text):
            _fail("INVALID_ARTIFACT_REGISTRY_WIRE", f"{field} entry has an invalid format", field)
        result.append(text)
    if unique and len(result) != len(set(result)):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", f"{field} entries must be unique", field)
    return result


def _content_hash(value: Any, field: str) -> str:
    if isinstance(value, str):
        return _pattern(value, SHA256, field)
    mapping = _exact(value, {"algorithm", "value"}, field)
    if mapping["algorithm"] != "sha256":
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", f"{field}.algorithm must be sha256", field)
    return _pattern(mapping["value"], SHA256, f"{field}.value")


def _locator(value: Any, field: str) -> dict[str, str]:
    mapping = _exact(value, {"path"}, field)
    opaque = _text(mapping["path"], f"{field}.path")
    return {
        "kind": "opaque_registry_locator",
        "path": opaque,
        "canonical_hash": "sha256:opaque_utf8:" + sha256_bytes(opaque.encode("utf-8")),
    }


def _evidence_basis(value: Any, field: str) -> dict[str, Any]:
    mapping = _exact(value, {"kind", "source_field", "evidence_ref", "details"}, field)
    if not isinstance(mapping["details"], dict):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", f"{field}.details must be an object", field)
    return {
        "kind": _text(mapping["kind"], f"{field}.kind"),
        "source_field": _text(mapping["source_field"], f"{field}.source_field"),
        "evidence_ref": _text(mapping["evidence_ref"], f"{field}.evidence_ref"),
        "details_present": bool(mapping["details"]),
    }


def _parent(value: Any, field: str) -> dict[str, Any]:
    mapping = _exact(
        value,
        {"parent_artifact_id", "parent_content_hash", "relation", "evidence_basis"},
        field,
    )
    return {
        "parent_artifact_id": _pattern(
            mapping["parent_artifact_id"], ARTIFACT_ID, f"{field}.parent_artifact_id"
        ),
        "parent_content_hash": _pattern(
            mapping["parent_content_hash"], SHA256, f"{field}.parent_content_hash"
        ),
        "relation": _text(mapping["relation"], f"{field}.relation"),
        "evidence_basis": _evidence_basis(mapping["evidence_basis"], f"{field}.evidence_basis"),
    }


def _parents(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", f"{field} must be a list", field)
    result = [_parent(item, f"{field}[{index}]") for index, item in enumerate(value)]
    if len({repr(item) for item in result}) != len(result):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", f"{field} must not contain duplicates", field)
    return result


def _unresolved(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", f"{field} must be a list", field)
    result = []
    expected = {
        "requirement",
        "reason_code",
        "evidence_refs",
        "candidate_artifact_ids",
        "search_hints",
    }
    for index, item in enumerate(value):
        label = f"{field}[{index}]"
        mapping = _exact(item, expected, label)
        search_hints = _text_list(mapping["search_hints"], f"{label}.search_hints")
        result.append(
            {
                "requirement": _text(mapping["requirement"], f"{label}.requirement"),
                "reason_code": _text(mapping["reason_code"], f"{label}.reason_code"),
                "evidence_refs": _text_list(
                    mapping["evidence_refs"], f"{label}.evidence_refs", allow_empty=False
                ),
                "candidate_artifact_ids": _text_list(
                    mapping["candidate_artifact_ids"],
                    f"{label}.candidate_artifact_ids",
                    pattern=ARTIFACT_ID,
                ),
                "search_hint_count": len(search_hints),
            }
        )
    return result


def _conflicts(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", f"{field} must be a list", field)
    result = []
    expected = {"requirement", "reason_code", "candidate_artifact_ids", "evidence_refs"}
    for index, item in enumerate(value):
        label = f"{field}[{index}]"
        mapping = _exact(item, expected, label)
        result.append(
            {
                "requirement": _text(mapping["requirement"], f"{label}.requirement"),
                "reason_code": _text(mapping["reason_code"], f"{label}.reason_code"),
                "candidate_artifact_ids": _text_list(
                    mapping["candidate_artifact_ids"],
                    f"{label}.candidate_artifact_ids",
                    allow_empty=False,
                    unique=True,
                    pattern=ARTIFACT_ID,
                ),
                "evidence_refs": _text_list(
                    mapping["evidence_refs"], f"{label}.evidence_refs", allow_empty=False
                ),
            }
        )
    return result


def _scope(value: Any, field: str) -> dict[str, list[str]]:
    mapping = _exact(value, {"paper_ids", "source_artifact_ids"}, field)
    return {
        "paper_ids": _text_list(mapping["paper_ids"], f"{field}.paper_ids", unique=True),
        "source_artifact_ids": _text_list(
            mapping["source_artifact_ids"],
            f"{field}.source_artifact_ids",
            unique=True,
            pattern=ARTIFACT_ID,
        ),
    }


def _ledger_refs(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", f"{field} must be a list", field)
    result = []
    for index, item in enumerate(value):
        label = f"{field}[{index}]"
        mapping = _exact(
            item,
            {"record_type", "ledger_type", "record_id", "run_id", "contract_ref"},
            label,
        )
        record_type = _text(mapping["record_type"], f"{label}.record_type")
        ledger_type = mapping["ledger_type"]
        if record_type == "ledger_record":
            if ledger_type not in {"attempt", "paper_outcome", "publication"}:
                _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "invalid ledger_type", f"{label}.ledger_type")
        elif record_type == "run_terminal_event":
            if ledger_type is not None:
                _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "run terminal ledger_type must be null", label)
        else:
            _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "invalid ledger record_type", label)
        result.append(
            {
                "record_type": record_type,
                "ledger_type": ledger_type,
                "record_id": _text(mapping["record_id"], f"{label}.record_id"),
                "run_id": _text(mapping["run_id"], f"{label}.run_id"),
                "contract_ref": _text(mapping["contract_ref"], f"{label}.contract_ref"),
            }
        )
    if len({repr(item) for item in result}) != len(result):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", f"{field} must not contain duplicates", field)
    return result


def _envelope_v1(value: Any) -> dict[str, Any]:
    expected = {
        "schema_version", "artifact_id", "artifact_type", "artifact_schema_ref",
        "content_hash", "locator", "run_ref", "route_snapshot_ref", "source_scope",
        "created_at", "registered_at", "parents", "lineage", "legacy_import",
        "evidence_refs", "metadata",
    }
    envelope = _exact(value, expected, "payload")
    if envelope["schema_version"] != "artifact-envelope-v1":
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "invalid v1 envelope version", "schema_version")
    artifact_id = _pattern(envelope["artifact_id"], ARTIFACT_ID, "artifact_id")
    lineage = _exact(
        envelope["lineage"],
        {"resolved_parent_links", "unresolved_parent_requirements", "conflicting_candidates"},
        "lineage",
    )
    parents = _parents(lineage["resolved_parent_links"], "lineage.resolved_parent_links")
    mirrored = _parents(envelope["parents"], "parents")
    if parents != mirrored:
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "parents must mirror lineage", "parents", "lineage")
    if any(item["parent_artifact_id"] == artifact_id for item in parents):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "artifact cannot parent itself", "parents")
    if not isinstance(envelope["legacy_import"], bool) or not isinstance(envelope["metadata"], dict):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "invalid envelope metadata types", "legacy_import", "metadata")
    metadata_tags = envelope["metadata"].get("explicit_tags", [])
    tags = _text_list(metadata_tags, "metadata.explicit_tags", unique=True)
    created = envelope["created_at"]
    created_at = None if created is None else _timestamp(created, "created_at")
    return {
        "schema_version": "artifact-envelope-v1",
        "artifact_id": artifact_id,
        "artifact_type": _text(envelope["artifact_type"], "artifact_type"),
        "artifact_schema_ref": _optional_text(envelope["artifact_schema_ref"], "artifact_schema_ref"),
        "content_hash": _content_hash(envelope["content_hash"], "content_hash"),
        "locator": _locator(envelope["locator"], "locator"),
        "run_ref": _optional_text(envelope["run_ref"], "run_ref"),
        "route_snapshot_ref": _optional_text(envelope["route_snapshot_ref"], "route_snapshot_ref"),
        "source_scope": _scope(envelope["source_scope"], "source_scope"),
        "created_at": created_at,
        "registered_at": _timestamp(envelope["registered_at"], "registered_at"),
        "parents": parents,
        "unresolved": _unresolved(
            lineage["unresolved_parent_requirements"], "lineage.unresolved_parent_requirements"
        ),
        "conflicts": _conflicts(lineage["conflicting_candidates"], "lineage.conflicting_candidates"),
        "ledger_refs": [],
        "evidence_refs": _text_list(envelope["evidence_refs"], "evidence_refs", allow_empty=False),
        "explicit_tags": tags,
    }


def _envelope_v2(value: Any) -> dict[str, Any]:
    expected = {
        "schema_version", "artifact_id", "artifact_type", "schema_ref", "content_hash",
        "locator", "run_ref", "route_snapshot_ref", "source_scope", "created_at",
        "registered_at", "parent_artifacts", "unresolved_parent_requirements",
        "conflicting_candidates", "ledger_record_refs", "legacy_import", "evidence_refs",
        "metadata",
    }
    envelope = _exact(value, expected, "payload")
    if envelope["schema_version"] != "artifact-envelope-v2":
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "invalid v2 envelope version", "schema_version")
    artifact_id = _pattern(envelope["artifact_id"], ARTIFACT_ID, "artifact_id")
    parents = _parents(envelope["parent_artifacts"], "parent_artifacts")
    if any(item["parent_artifact_id"] == artifact_id for item in parents):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "artifact cannot parent itself", "parent_artifacts")
    unresolved = _unresolved(
        envelope["unresolved_parent_requirements"], "unresolved_parent_requirements"
    )
    conflicts = _conflicts(envelope["conflicting_candidates"], "conflicting_candidates")
    if {item["requirement"] for item in unresolved} & {item["requirement"] for item in conflicts}:
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "requirement cannot be unresolved and conflicted", "payload")
    if not isinstance(envelope["legacy_import"], bool) or not isinstance(envelope["metadata"], dict):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "invalid envelope metadata types", "legacy_import", "metadata")
    metadata_tags = envelope["metadata"].get("explicit_tags", [])
    tags = _text_list(metadata_tags, "metadata.explicit_tags", unique=True)
    created = envelope["created_at"]
    created_at = None if created is None else _timestamp(created, "created_at")
    return {
        "schema_version": "artifact-envelope-v2",
        "artifact_id": artifact_id,
        "artifact_type": _text(envelope["artifact_type"], "artifact_type"),
        "schema_ref": _optional_text(envelope["schema_ref"], "schema_ref"),
        "content_hash": _content_hash(envelope["content_hash"], "content_hash"),
        "locator": _locator(envelope["locator"], "locator"),
        "run_ref": _optional_text(envelope["run_ref"], "run_ref"),
        "route_snapshot_ref": _optional_text(envelope["route_snapshot_ref"], "route_snapshot_ref"),
        "source_scope": _scope(envelope["source_scope"], "source_scope"),
        "created_at": created_at,
        "registered_at": _timestamp(envelope["registered_at"], "registered_at"),
        "parents": parents,
        "unresolved": unresolved,
        "conflicts": conflicts,
        "ledger_refs": _ledger_refs(envelope["ledger_record_refs"], "ledger_record_refs"),
        "evidence_refs": _text_list(envelope["evidence_refs"], "evidence_refs", allow_empty=False),
        "explicit_tags": tags,
    }


def _registered(value: Any, version: str) -> dict[str, Any]:
    return _envelope_v1(value) if version == "artifact_registry-registry-event-v1" else _envelope_v2(value)


def extract_artifact_registry_event(record: dict[str, Any]) -> dict[str, Any]:
    """Validate a formal Registry wrapper and return a safe domain fact set."""

    event = _exact(
        record,
        {"event_schema_version", "event_id", "event_type", "recorded_at", "payload"},
        "registry event",
    )
    version = event["event_schema_version"]
    if version not in {"artifact_registry-registry-event-v1", "artifact_registry-registry-event-v2"}:
        _fail("UNSUPPORTED_ARTIFACT_REGISTRY_SCHEMA", "unsupported ARTIFACT_REGISTRY event schema", "event_schema_version")
    event_id = _pattern(event["event_id"], EVENT_ID, "event_id")
    event_type = _text(event["event_type"], "event_type")
    recorded_at = _timestamp(event["recorded_at"], "recorded_at")
    payload = event["payload"]
    if not isinstance(payload, dict):
        _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "payload must be an object", "payload")

    facts: dict[str, Any] = {
        "stable_source_identity": event_id,
        "registry_event_id": event_id,
        "event_schema_version": version,
        "registry_event_type": event_type,
        "observed_at": recorded_at,
        "occurred_at": recorded_at,
        "artifact_type": None,
        "artifact_content_hash": None,
        "parents": [],
        "unresolved": [],
        "conflicts": [],
        "ledger_refs": [],
        "evidence_refs": [],
        "explicit_tags": [],
        "locator": None,
        "prior_head_event_id": None,
        "from_state": None,
        "to_state": "recorded",
    }
    if event_type == "artifact_registered":
        envelope = _registered(payload, version)
        facts.update(
            {
                "artifact_id": envelope["artifact_id"],
                "artifact_type": envelope["artifact_type"],
                "artifact_content_hash": envelope["content_hash"],
                "occurred_at": envelope["created_at"] or envelope["registered_at"],
                "parents": envelope["parents"],
                "unresolved": envelope["unresolved"],
                "conflicts": envelope["conflicts"],
                "ledger_refs": envelope["ledger_refs"],
                "evidence_refs": envelope["evidence_refs"],
                "explicit_tags": envelope["explicit_tags"],
                "locator": envelope["locator"],
            }
        )
        superseding = any(item["relation"] == "supersedes" for item in envelope["parents"])
        facts["event_kind"] = "artifact_registry.artifact_superseded" if superseding else "artifact_registry.artifact_registered"
        facts["daily_category"] = "修改" if superseding else "新增"
    elif version == "artifact_registry-registry-event-v1" and event_type == "lineage_resolution":
        mapping = _exact(
            payload,
            {"artifact_id", "resolved_parent_links", "resolved_requirements", "evidence_refs"},
            "lineage_resolution payload",
        )
        facts.update(
            {
                "artifact_id": _pattern(mapping["artifact_id"], ARTIFACT_ID, "artifact_id"),
                "parents": _parents(mapping["resolved_parent_links"], "resolved_parent_links"),
                "resolved_requirements": _text_list(
                    mapping["resolved_requirements"], "resolved_requirements", allow_empty=False, unique=True
                ),
                "evidence_refs": _text_list(mapping["evidence_refs"], "evidence_refs", allow_empty=False),
                "explicit_tags": [],
                "event_kind": "artifact_registry.lineage_resolved",
                "daily_category": "修改",
            }
        )
        if not facts["parents"]:
            _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "lineage resolution requires a parent", "resolved_parent_links")
    elif version == "artifact_registry-registry-event-v2" and event_type == "lineage_resolution_event":
        mapping = _exact(
            payload,
            {"artifact_id", "prior_head_event_id", "new_envelope", "resolved_requirements", "evidence_refs"},
            "lineage_resolution_event payload",
        )
        envelope = _envelope_v2(mapping["new_envelope"])
        artifact_id = _pattern(mapping["artifact_id"], ARTIFACT_ID, "artifact_id")
        if envelope["artifact_id"] != artifact_id:
            _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "lineage head artifact mismatch", "artifact_id", "new_envelope")
        facts.update(
            {
                "artifact_id": artifact_id,
                "artifact_type": envelope["artifact_type"],
                "artifact_content_hash": envelope["content_hash"],
                "parents": envelope["parents"],
                "unresolved": envelope["unresolved"],
                "conflicts": envelope["conflicts"],
                "ledger_refs": envelope["ledger_refs"],
                "locator": envelope["locator"],
                "prior_head_event_id": _pattern(
                    mapping["prior_head_event_id"], EVENT_ID, "prior_head_event_id"
                ),
                "resolved_requirements": _text_list(
                    mapping["resolved_requirements"], "resolved_requirements", allow_empty=False, unique=True
                ),
                "evidence_refs": _text_list(mapping["evidence_refs"], "evidence_refs", allow_empty=False),
                "explicit_tags": envelope["explicit_tags"],
                "event_kind": "artifact_registry.lineage_resolved",
                "daily_category": "修改",
            }
        )
    elif (version == "artifact_registry-registry-event-v1" and event_type == "locator_updated") or (
        version == "artifact_registry-registry-event-v2" and event_type == "artifact_location_event"
    ):
        mapping = _exact(payload, {"artifact_id", "content_hash", "locator", "evidence_refs"}, "location payload")
        facts.update(
            {
                "artifact_id": _pattern(mapping["artifact_id"], ARTIFACT_ID, "artifact_id"),
                "artifact_content_hash": _content_hash(mapping["content_hash"], "content_hash"),
                "locator": _locator(mapping["locator"], "locator"),
                "evidence_refs": _text_list(mapping["evidence_refs"], "evidence_refs", allow_empty=False),
                "event_kind": "artifact_registry.artifact_location_updated",
                "daily_category": "修改",
            }
        )
    elif event_type == "state_transition_observed":
        mapping = _exact(
            payload,
            {"artifact_id", "state_owner", "event_ref", "from_status", "to_status"},
            "state_transition_observed payload",
        )
        if mapping["state_owner"] != "KNOWLEDGE_ADMISSION":
            _fail("INVALID_ARTIFACT_REGISTRY_WIRE", "state_owner must remain KNOWLEDGE_ADMISSION", "state_owner")
        facts.update(
            {
                "artifact_id": _pattern(mapping["artifact_id"], ARTIFACT_ID, "artifact_id"),
                "evidence_refs": [_text(mapping["event_ref"], "event_ref")],
                "from_state": _text(mapping["from_status"], "from_status"),
                "to_state": _text(mapping["to_status"], "to_status"),
                "event_kind": "artifact_registry.state_transition_observed",
                "daily_category": "修改",
            }
        )
    else:
        _fail("UNSUPPORTED_ARTIFACT_REGISTRY_EVENT_TYPE", "unsupported event type for version", "event_type")
    return facts
