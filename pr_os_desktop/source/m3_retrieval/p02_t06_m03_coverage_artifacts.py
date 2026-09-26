"""PR-OS P02/T06/M03 · immutable coverage-governance artifacts.

This module is deliberately layered above the Phase 1 runtime ``LogicalSpan``
and ``ContextPack`` types.  It does not mutate chunks, does not call a model,
and does not write the real M13 registry.  Each payload is canonical JSON and
is paired with a validated M13 Artifact Envelope sidecar; the envelope hash is
the SHA-256 of the exact payload bytes, avoiding an impossible self-hash.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from m13_artifact_registry import (
    build_envelope,
    canonical_json,
    new_artifact_id,
    parent_link,
    validate_envelope,
)

from .types import LogicalSpan


_ARTIFACT_ID = re.compile(r"^art_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

FACET_SOURCES = {"frozen_fixture", "approved_manual", "m3_query_understanding"}
FACET_STATUSES = {"covered", "partial", "missing", "not_assessed"}
COVERAGE_STATUSES = {"sufficient", "partial", "insufficient", "not_assessed"}
SUBJECT_KINDS = {"logical_span", "context_pack", "candidate_chunk_set"}
SOURCE_ROLES = {
    "body_text",
    "table",
    "table_caption",
    "figure",
    "figure_caption",
    "heading",
    "footnote",
    "references",
    "metadata",
    "unknown",
}


class T6ArtifactValidationError(ValueError):
    """A T6 artifact violates a frozen contract or provenance boundary."""


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise T6ArtifactValidationError(f"{field} must be a non-empty string")
    return value


def _require_hash(value: Any, field: str) -> str:
    _require_text(value, field)
    if not _SHA256.fullmatch(value):
        raise T6ArtifactValidationError(f"{field} must be lowercase SHA-256")
    return value


def _require_artifact_id(value: Any, field: str = "artifact_id") -> str:
    _require_text(value, field)
    if not _ARTIFACT_ID.fullmatch(value):
        raise T6ArtifactValidationError(f"{field} has invalid format")
    return value


def _require_iso8601(value: Any, field: str) -> str:
    _require_text(value, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise T6ArtifactValidationError(f"{field} must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise T6ArtifactValidationError(f"{field} must include timezone")
    return value


def _require_exact_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise T6ArtifactValidationError(
            f"{label} fields mismatch: missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _require_text_list(value: Any, field: str, *, unique: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise T6ArtifactValidationError(f"{field} must be a list")
    for item in value:
        _require_text(item, f"{field} entry")
    if unique and len(value) != len(set(value)):
        raise T6ArtifactValidationError(f"{field} entries must be unique")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return canonical_json(value).encode("utf-8")


@dataclass(frozen=True)
class ArtifactPointer:
    """Hash-bound pointer used in T6 payloads and parent links."""

    artifact_id: str
    content_hash: str
    artifact_type: str
    authority: str
    frozen: bool
    approved: bool
    fixture_only: bool = False

    def __post_init__(self) -> None:
        _require_artifact_id(self.artifact_id)
        _require_hash(self.content_hash, "content_hash")
        _require_text(self.artifact_type, "artifact_type")
        _require_text(self.authority, "authority")
        if not isinstance(self.frozen, bool) or not isinstance(self.approved, bool):
            raise T6ArtifactValidationError("pointer frozen/approved must be boolean")
        if not isinstance(self.fixture_only, bool):
            raise T6ArtifactValidationError("pointer fixture_only must be boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "content_hash": self.content_hash,
            "artifact_type": self.artifact_type,
            "authority": self.authority,
            "frozen": self.frozen,
            "approved": self.approved,
            "fixture_only": self.fixture_only,
        }


_POINTER_FIELDS = {
    "artifact_id",
    "content_hash",
    "artifact_type",
    "authority",
    "frozen",
    "approved",
    "fixture_only",
}


def _validate_pointer(value: Any, field: str, *, expected_type: str | None = None) -> None:
    if not isinstance(value, Mapping):
        raise T6ArtifactValidationError(f"{field} must be an artifact pointer")
    _require_exact_fields(value, _POINTER_FIELDS, field)
    pointer = ArtifactPointer(**dict(value))
    if expected_type is not None and pointer.artifact_type != expected_type:
        raise T6ArtifactValidationError(
            f"{field} must reference {expected_type!r}, got {pointer.artifact_type!r}"
        )


def pointer_from_payload(value: Mapping[str, Any]) -> ArtifactPointer:
    _validate_pointer(value, "artifact pointer")
    return ArtifactPointer(**dict(value))


@dataclass(frozen=True)
class FrozenArtifactRecord:
    """Immutable canonical payload plus an immutable M13 Envelope sidecar."""

    payload_json: str
    envelope_json: str

    def __post_init__(self) -> None:
        try:
            payload = json.loads(self.payload_json)
            envelope = json.loads(self.envelope_json)
        except json.JSONDecodeError as exc:
            raise T6ArtifactValidationError("artifact record JSON is invalid") from exc
        if canonical_json(payload) != self.payload_json:
            raise T6ArtifactValidationError("payload_json is not canonical JSON")
        if canonical_json(envelope) != self.envelope_json:
            raise T6ArtifactValidationError("envelope_json is not canonical JSON")
        validate_t6_payload(payload)
        validate_envelope(envelope)
        if payload["artifact_id"] != envelope["artifact_id"]:
            raise T6ArtifactValidationError("payload/envelope artifact_id mismatch")
        if payload["artifact_type"] != envelope["artifact_type"]:
            raise T6ArtifactValidationError("payload/envelope artifact_type mismatch")
        actual = _sha256_bytes(self.payload_json.encode("utf-8"))
        if actual != envelope["content_hash"]["value"]:
            raise T6ArtifactValidationError("envelope content_hash does not match payload bytes")

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)

    @property
    def envelope(self) -> dict[str, Any]:
        return json.loads(self.envelope_json)

    @property
    def artifact_id(self) -> str:
        return self.payload["artifact_id"]

    @property
    def artifact_type(self) -> str:
        return self.payload["artifact_type"]

    @property
    def content_hash(self) -> str:
        return self.envelope["content_hash"]["value"]

    def as_pointer(
        self,
        *,
        authority: str = "t6-sandbox-candidate",
        frozen: bool = True,
        approved: bool = False,
        fixture_only: bool = False,
    ) -> ArtifactPointer:
        return ArtifactPointer(
            artifact_id=self.artifact_id,
            content_hash=self.content_hash,
            artifact_type=self.artifact_type,
            authority=authority,
            frozen=frozen,
            approved=approved,
            fixture_only=fixture_only,
        )

    def write(self, payload_path: Path | str, envelope_path: Path | str) -> None:
        """Write exact canonical bytes to an already approved sandbox location."""

        Path(payload_path).write_bytes(self.payload_json.encode("utf-8"))
        Path(envelope_path).write_bytes(self.envelope_json.encode("utf-8"))


def resolved_parent_link(
    pointer: ArtifactPointer,
    *,
    relation: str,
    source_field: str,
    evidence_ref: str,
) -> dict[str, Any]:
    return parent_link(
        parent_artifact_id=pointer.artifact_id,
        parent_content_hash=pointer.content_hash,
        relation=relation,
        evidence_kind="artifact_ref",
        source_field=source_field,
        evidence_ref=evidence_ref,
        details={"artifact_type": pointer.artifact_type},
    )


def build_t6_artifact_record(
    payload: Mapping[str, Any],
    *,
    locator_path: Path | str,
    parent_links: Iterable[Mapping[str, Any]] = (),
    paper_ids: Iterable[str] = (),
    source_artifact_ids: Iterable[str] = (),
    artifact_schema_ref: str,
    evidence_refs: Iterable[str],
) -> FrozenArtifactRecord:
    """Canonicalize one T6 payload and wrap it in the existing T3 Envelope."""

    body = dict(payload)
    validate_t6_payload(body)
    payload_json = canonical_json(body)
    created_at = body.get("created_at") or body.get("computed_at")
    _require_iso8601(created_at, "artifact timestamp")
    links = [dict(item) for item in parent_links]
    source_ids = list(dict.fromkeys(source_artifact_ids))
    envelope = build_envelope(
        artifact_id=body["artifact_id"],
        artifact_type=body["artifact_type"],
        schema_ref=artifact_schema_ref,
        content_hash=_sha256_bytes(payload_json.encode("utf-8")),
        locator_path=locator_path,
        run_ref=body.get("run_ref"),
        route_snapshot_ref=body.get("route_snapshot_ref"),
        paper_ids=list(dict.fromkeys(paper_ids)),
        source_artifact_ids=source_ids,
        created_at=created_at,
        registered_at=created_at,
        parent_artifacts=links,
        unresolved_parent_requirements=(),
        conflicting_candidates=(),
        legacy_import=False,
        evidence_refs=list(evidence_refs),
        metadata={
            "phase": "P02",
            "task_id": "T06",
            "real_registry_write": False,
            "producer_module": body.get("producer_module"),
        },
    )
    return FrozenArtifactRecord(payload_json, canonical_json(envelope))


_SCHEMA_FIELDS: dict[str, set[str]] = {
    "span_assembly_policy": {
        "artifact_type", "artifact_id", "policy_version", "assembly_separator",
        "allowed_logical_roles", "max_member_count", "require_same_paper",
        "require_same_section", "require_consecutive_sequence", "producer_module",
        "producer_version", "method_policy_ref", "run_ref", "route_snapshot_ref",
        "created_at", "supersedes",
    },
    "logical_span": {
        "artifact_type", "artifact_id", "logical_span_id", "member_chunk_ids",
        "content", "paper_id", "section_path", "logical_role", "block_type",
        "page_start", "page_end", "facet_ids", "retrieval_reasons",
        "primary_source_id", "span_reason", "text_hash", "member_provenance",
        "producer_module", "producer_version", "method_policy_ref",
        "input_artifact_refs", "run_ref", "route_snapshot_ref", "created_at",
    },
    "source_role_policy": {
        "artifact_type", "artifact_id", "policy_version", "roles",
        "classification_rules", "confidence_rules", "producer_module",
        "producer_version", "method_policy_ref", "run_ref", "route_snapshot_ref",
        "created_at", "supersedes",
    },
    "source_role_assessment": {
        "artifact_type", "artifact_id", "subject_source_set_ref", "subject_kind",
        "source_role_policy_ref", "member_roles", "producer_module",
        "producer_version", "method_policy_ref", "input_artifact_refs", "run_ref",
        "route_snapshot_ref", "computed_at", "field_provenance",
    },
    "references_bias_policy": {
        "artifact_type", "artifact_id", "policy_version",
        "section_confidence_threshold", "entry_pattern_confidence_threshold",
        "exclusion_rules", "warning_rule", "metadata_unknown_behavior",
        "producer_module", "producer_version", "method_policy_ref", "run_ref",
        "route_snapshot_ref", "created_at", "supersedes",
    },
    "facet_definition": {
        "artifact_type", "artifact_id", "question_ref", "question_hash", "source",
        "policy_ref", "facets", "producer_module", "producer_version",
        "input_artifact_refs", "run_ref", "route_snapshot_ref", "created_at",
    },
    "retrieval_coverage_assessment": {
        "artifact_type", "artifact_id", "producer_module", "facet_definition_ref",
        "query_facets_projection", "retrieval_coverage_status", "facet_coverage",
        "missing_facet_ids", "answer_complete", "answer_complete_policy_ref",
        "unique_chunk_count", "unique_section_count", "references_only_warning",
        "references_bias_policy_ref", "excluded_candidate_refs",
        "unused_capacity_reason", "summary_policy_ref", "required_slot_assessment_ref",
        "fact_identity_sidecar_ref", "t5_context_status", "not_assessed_reason_codes",
        "producer_version", "method_policy_ref", "input_artifact_refs", "run_ref",
        "route_snapshot_ref", "computed_at", "field_provenance",
        "verification_scope", "non_authoritative",
    },
    "analysis_quality_assessment": {
        "artifact_type", "artifact_id", "producer_module", "retrieval_coverage_ref",
        "analysis_artifact_ref", "direct_answer_ratio", "subquestion_coverage",
        "source_dominance", "producer_version", "method_policy_ref",
        "input_artifact_refs", "run_ref", "route_snapshot_ref", "computed_at",
        "field_provenance", "verification_scope", "non_authoritative",
    },
}


def _validate_audit_fields(payload: Mapping[str, Any], expected_producer: str) -> None:
    if payload.get("producer_module") != expected_producer:
        raise T6ArtifactValidationError(
            f"producer_module must be {expected_producer!r}"
        )
    _require_text(payload.get("producer_version"), "producer_version")
    _validate_pointer(payload.get("method_policy_ref"), "method_policy_ref")
    refs = payload.get("input_artifact_refs")
    if not isinstance(refs, list):
        raise T6ArtifactValidationError("input_artifact_refs must be a list")
    for index, ref in enumerate(refs):
        _validate_pointer(ref, f"input_artifact_refs[{index}]")
    _require_text(payload.get("run_ref"), "run_ref")
    _require_text(payload.get("route_snapshot_ref"), "route_snapshot_ref")
    _require_iso8601(payload.get("computed_at"), "computed_at")
    provenance = payload.get("field_provenance")
    if not isinstance(provenance, Mapping) or not provenance:
        raise T6ArtifactValidationError("field_provenance must be a non-empty object")
    for field, producer in provenance.items():
        _require_text(field, "field_provenance field")
        if producer != expected_producer:
            raise T6ArtifactValidationError(
                f"field_provenance.{field} must equal {expected_producer!r}"
            )


def validate_t6_payload(payload: Mapping[str, Any]) -> None:
    """Strict schema-layer validation for every T6 persistent payload."""

    if not isinstance(payload, Mapping):
        raise T6ArtifactValidationError("payload must be an object")
    artifact_type = payload.get("artifact_type")
    if artifact_type not in _SCHEMA_FIELDS:
        raise T6ArtifactValidationError(f"unsupported T6 artifact_type: {artifact_type!r}")
    _require_exact_fields(payload, _SCHEMA_FIELDS[artifact_type], artifact_type)
    _require_artifact_id(payload.get("artifact_id"))

    if artifact_type == "logical_span":
        _require_text(payload.get("logical_span_id"), "logical_span_id")
        members = _require_text_list(payload.get("member_chunk_ids"), "member_chunk_ids", unique=True)
        if not members:
            raise T6ArtifactValidationError("member_chunk_ids must not be empty")
        _require_text(payload.get("content"), "content")
        _require_hash(payload.get("text_hash"), "text_hash")
        if payload.get("primary_source_id") != members[0]:
            raise T6ArtifactValidationError("primary_source_id must be the first member alias")
        provenance = payload.get("member_provenance")
        if not isinstance(provenance, list) or len(provenance) != len(members):
            raise T6ArtifactValidationError("member_provenance must match member count")
        for index, item in enumerate(provenance):
            if not isinstance(item, Mapping):
                raise T6ArtifactValidationError("member_provenance entries must be objects")
            _require_exact_fields(item, {"chunk_ref", "original_boundary"}, "member_provenance entry")
            _validate_pointer(item["chunk_ref"], f"member_provenance[{index}].chunk_ref")
            boundary = item["original_boundary"]
            if not isinstance(boundary, Mapping):
                raise T6ArtifactValidationError("original_boundary must be an object")
            _require_exact_fields(
                boundary,
                {
                    "chunk_id", "paper_id", "section_path", "block_type", "page_start",
                    "page_end", "sequence_index", "text_hash",
                },
                "original_boundary",
            )
            _require_hash(boundary.get("text_hash"), "original_boundary.text_hash")
        _validate_pointer(payload.get("method_policy_ref"), "method_policy_ref", expected_type="span_assembly_policy")
        if payload.get("producer_module") != "M3":
            raise T6ArtifactValidationError("LogicalSpan producer_module must be M3")
        _require_iso8601(payload.get("created_at"), "created_at")

    elif artifact_type == "source_role_assessment":
        _validate_audit_fields(payload, "M3")
        _validate_pointer(payload.get("subject_source_set_ref"), "subject_source_set_ref")
        if payload.get("subject_kind") not in SUBJECT_KINDS:
            raise T6ArtifactValidationError("subject_kind is invalid")
        _validate_pointer(payload.get("source_role_policy_ref"), "source_role_policy_ref", expected_type="source_role_policy")
        roles = payload.get("member_roles")
        if not isinstance(roles, list) or not roles:
            raise T6ArtifactValidationError("member_roles must be a non-empty list")
        for index, item in enumerate(roles):
            if not isinstance(item, Mapping):
                raise T6ArtifactValidationError("member_roles entries must be objects")
            _require_exact_fields(item, {"chunk_ref", "role", "confidence", "evidence_refs"}, "member role")
            _validate_pointer(item["chunk_ref"], f"member_roles[{index}].chunk_ref")
            if item.get("role") not in SOURCE_ROLES:
                raise T6ArtifactValidationError("member role is invalid")
            confidence = item.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
                raise T6ArtifactValidationError("member role confidence must be within [0, 1]")
            _require_text_list(item.get("evidence_refs"), "member role evidence_refs")

    elif artifact_type == "facet_definition":
        _validate_pointer(payload.get("question_ref"), "question_ref")
        _validate_pointer(payload.get("policy_ref"), "policy_ref")
        _require_hash(payload.get("question_hash"), "question_hash")
        if payload.get("source") not in FACET_SOURCES:
            raise T6ArtifactValidationError("facet source is invalid")
        facets = payload.get("facets")
        if not isinstance(facets, list) or not facets:
            raise T6ArtifactValidationError("facets must be a non-empty list")
        ids: list[str] = []
        for facet in facets:
            if not isinstance(facet, Mapping):
                raise T6ArtifactValidationError("facet entries must be objects")
            _require_exact_fields(facet, {"facet_id", "text", "required"}, "facet")
            ids.append(_require_text(facet.get("facet_id"), "facet_id"))
            _require_text(facet.get("text"), "facet.text")
            if not isinstance(facet.get("required"), bool):
                raise T6ArtifactValidationError("facet.required must be boolean")
        if len(ids) != len(set(ids)):
            raise T6ArtifactValidationError("facet_id values must be unique")
        _require_iso8601(payload.get("created_at"), "created_at")

    elif artifact_type == "retrieval_coverage_assessment":
        _validate_audit_fields(payload, "M3")
        _validate_pointer(payload.get("facet_definition_ref"), "facet_definition_ref", expected_type="facet_definition")
        _validate_pointer(payload.get("answer_complete_policy_ref"), "answer_complete_policy_ref")
        _validate_pointer(payload.get("references_bias_policy_ref"), "references_bias_policy_ref", expected_type="references_bias_policy")
        _validate_pointer(payload.get("summary_policy_ref"), "summary_policy_ref")
        status = payload.get("retrieval_coverage_status")
        if status not in COVERAGE_STATUSES:
            raise T6ArtifactValidationError("retrieval_coverage_status is invalid")
        answer = payload.get("answer_complete")
        if status == "not_assessed":
            if answer is not None:
                raise T6ArtifactValidationError("answer_complete must be null for not_assessed")
        elif not isinstance(answer, bool):
            raise T6ArtifactValidationError("answer_complete must be boolean when assessed")
        coverage = payload.get("facet_coverage")
        if not isinstance(coverage, list):
            raise T6ArtifactValidationError("facet_coverage must be a list")
        for index, item in enumerate(coverage):
            if not isinstance(item, Mapping):
                raise T6ArtifactValidationError("facet coverage entries must be objects")
            _require_exact_fields(item, {"facet_id", "status", "supporting_source_refs"}, "facet coverage")
            _require_text(item.get("facet_id"), "facet_coverage.facet_id")
            if item.get("status") not in FACET_STATUSES:
                raise T6ArtifactValidationError("facet coverage status is invalid")
            refs = item.get("supporting_source_refs")
            if not isinstance(refs, list):
                raise T6ArtifactValidationError("supporting_source_refs must be a list")
            for ref in refs:
                _validate_pointer(ref, f"facet_coverage[{index}].supporting_source_refs")
        for field in ("unique_chunk_count", "unique_section_count"):
            value = payload.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise T6ArtifactValidationError(f"{field} must be a non-negative integer")
        if payload.get("t5_context_status") not in {"available", "fixture_only", "not_assessed"}:
            raise T6ArtifactValidationError("t5_context_status is invalid")

    elif artifact_type == "analysis_quality_assessment":
        _validate_audit_fields(payload, "M4")
        _validate_pointer(payload.get("retrieval_coverage_ref"), "retrieval_coverage_ref", expected_type="retrieval_coverage_assessment")
        _validate_pointer(payload.get("analysis_artifact_ref"), "analysis_artifact_ref")
        ratio = payload.get("direct_answer_ratio")
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 <= ratio <= 1:
            raise T6ArtifactValidationError("direct_answer_ratio must be within [0, 1]")
        if not isinstance(payload.get("subquestion_coverage"), Mapping):
            raise T6ArtifactValidationError("subquestion_coverage must be an object")
        if not isinstance(payload.get("source_dominance"), Mapping):
            raise T6ArtifactValidationError("source_dominance must be an object")

    else:
        if artifact_type.endswith("_policy"):
            _require_iso8601(payload.get("created_at"), "created_at")
        if payload.get("producer_module") != "M3":
            raise T6ArtifactValidationError(f"{artifact_type} producer_module must be M3")


@dataclass(frozen=True)
class ChunkSnapshot:
    chunk_ref: ArtifactPointer
    chunk_id: str
    paper_id: str
    content: str
    section_path: str | None
    block_type: str | None
    page_start: int | None
    page_end: int | None
    sequence_index: int
    text_hash: str
    paragraph_start: int | None = None
    paragraph_end: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.chunk_id, "chunk_id")
        _require_text(self.paper_id, "paper_id")
        _require_text(self.content, "chunk content")
        if not isinstance(self.sequence_index, int) or isinstance(self.sequence_index, bool):
            raise T6ArtifactValidationError("sequence_index must be an integer")
        _require_hash(self.text_hash, "chunk text_hash")
        if _sha256_text(self.content) != self.text_hash:
            raise T6ArtifactValidationError("chunk content no longer matches frozen text_hash")
        if self.chunk_ref.artifact_type != "chunk":
            raise T6ArtifactValidationError("chunk_ref must reference a chunk artifact")
        if self.chunk_ref.content_hash != self.text_hash:
            raise T6ArtifactValidationError("chunk_ref content_hash differs from frozen chunk text_hash")
        if (self.paragraph_start is None) != (self.paragraph_end is None):
            raise T6ArtifactValidationError("paragraph boundaries must be both present or both absent")
        if self.paragraph_start is not None:
            if (
                not isinstance(self.paragraph_start, int)
                or isinstance(self.paragraph_start, bool)
                or not isinstance(self.paragraph_end, int)
                or isinstance(self.paragraph_end, bool)
                or self.paragraph_start < 0
                or self.paragraph_end <= self.paragraph_start
            ):
                raise T6ArtifactValidationError("paragraph boundaries are invalid")

    @classmethod
    def from_content(
        cls,
        *,
        chunk_ref: ArtifactPointer,
        chunk_id: str,
        paper_id: str,
        content: str,
        section_path: str | None,
        block_type: str | None,
        page_start: int | None,
        page_end: int | None,
        sequence_index: int,
        paragraph_start: int | None = None,
        paragraph_end: int | None = None,
    ) -> "ChunkSnapshot":
        return cls(
            chunk_ref=chunk_ref,
            chunk_id=chunk_id,
            paper_id=paper_id,
            content=content,
            section_path=section_path,
            block_type=block_type,
            page_start=page_start,
            page_end=page_end,
            sequence_index=sequence_index,
            text_hash=_sha256_text(content),
            paragraph_start=paragraph_start,
            paragraph_end=paragraph_end,
        )


@dataclass(frozen=True)
class MemberRole:
    chunk_ref: ArtifactPointer
    role: str
    confidence: float
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class FacetSpec:
    facet_id: str
    text: str
    required: bool = True


@dataclass(frozen=True)
class FacetCoverageInput:
    facet_id: str
    status: str
    supporting_source_refs: tuple[ArtifactPointer, ...] = ()
    matched_terms: tuple[str, ...] = ()
    evidence_char_ranges: tuple[tuple[int, int], ...] = ()
    decision_reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceIdDisposition:
    chunk_id: str
    status: str
    reason_codes: tuple[str, ...]


def admit_chunk_snapshots(
    candidates: Sequence[ChunkSnapshot],
    *,
    expected_paper_id: str,
    frozen_inventory: Sequence[Mapping[str, str]],
) -> tuple[tuple[ChunkSnapshot, ...], tuple[SourceIdDisposition, ...]]:
    """Admit only hash-bound candidates present in the frozen source inventory.

    The inventory is mechanical provenance, not semantic Gold.  Raw candidate
    declarations never widen the admitted source set.
    """

    _require_text(expected_paper_id, "expected_paper_id")
    inventory: dict[str, tuple[str, str]] = {}
    for item in frozen_inventory:
        if set(item) != {"chunk_id", "paper_id", "text_sha256"}:
            raise T6ArtifactValidationError("frozen inventory entry fields mismatch")
        chunk_id = _require_text(item.get("chunk_id"), "inventory.chunk_id")
        paper_id = _require_text(item.get("paper_id"), "inventory.paper_id")
        text_hash = _require_hash(item.get("text_sha256"), "inventory.text_sha256")
        if chunk_id in inventory:
            raise T6ArtifactValidationError("frozen inventory chunk IDs must be unique")
        inventory[chunk_id] = (paper_id, text_hash)

    admitted: list[ChunkSnapshot] = []
    dispositions: list[SourceIdDisposition] = []
    seen: set[str] = set()
    for candidate in candidates:
        reasons: list[str] = []
        if candidate.chunk_id in seen:
            reasons.append("DUPLICATE_SOURCE_ID")
        seen.add(candidate.chunk_id)
        expected = inventory.get(candidate.chunk_id)
        if expected is None:
            reasons.append("SOURCE_ID_NOT_IN_FROZEN_INVENTORY")
        else:
            inventory_paper_id, inventory_hash = expected
            if inventory_paper_id != expected_paper_id or candidate.paper_id != expected_paper_id:
                reasons.append("PAPER_SCOPE_MISMATCH")
            if inventory_paper_id != candidate.paper_id:
                reasons.append("SOURCE_ID_PAPER_BINDING_MISMATCH")
            if inventory_hash != candidate.text_hash:
                reasons.append("SOURCE_ID_TEXT_HASH_MISMATCH")
        if reasons:
            dispositions.append(SourceIdDisposition(candidate.chunk_id, "rejected", tuple(dict.fromkeys(reasons))))
        else:
            admitted.append(candidate)
            dispositions.append(SourceIdDisposition(candidate.chunk_id, "admitted", ("FROZEN_INVENTORY_MATCH",)))
    return tuple(admitted), tuple(dispositions)


_DEFAULT_FACET_STOPWORDS = frozenset({
    "and", "the", "for", "with", "from", "what", "reported", "report",
    "evidence", "result", "results", "comparison", "pattern", "author",
})

_RESULT_SIGNAL_TOKENS = frozenset({
    "above", "always", "average", "below", "compare", "conversion",
    "decrease", "difference", "divid", "effective", "enhanc", "higher",
    "increase", "lag", "less", "lower", "more", "rate", "reduc",
    "resist", "show", "significant", "similar", "yield",
})
_NUMERIC_EVIDENCE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")
_SENTENCE_BOUNDARY = re.compile(r"[.!?](?=\s+[A-Z]|$)")
_EVIDENCE_PREFIXES = tuple(re.compile(pattern, re.IGNORECASE | re.DOTALL) for pattern in (
    r"^Compared to [^,]+,\s*",
    r"^It is noteworthy that\s*",
    r"^Specifically,\s*(?:from [^,]+,\s*)?",
    r"^Therefore,\s*",
    r"^(?:However,\s*)?In the case of .*?(?=the average\b)",
))


def _facet_token(value: str) -> str:
    token = value.casefold()
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[:-len(suffix)]
    return token


def _facet_tokens(
    value: str,
    *,
    token_pattern: str,
    minimum_token_length: int,
    stopwords: set[str],
) -> set[str]:
    return set(_facet_token_sequence(
        value,
        token_pattern=token_pattern,
        minimum_token_length=minimum_token_length,
        stopwords=stopwords,
    ))


def _facet_token_sequence(
    value: str,
    *,
    token_pattern: str,
    minimum_token_length: int,
    stopwords: set[str],
) -> tuple[str, ...]:
    pattern = re.compile(token_pattern)
    return tuple(
        _facet_token(token)
        for token in pattern.findall(value.replace("_", " ").replace("-", " "))
        if len(token) >= minimum_token_length and token.casefold() not in stopwords
    )


def _minimal_evidence_units(paragraph: str) -> tuple[tuple[int, int, str], ...]:
    """Return sentence/semicolon units without joining remote keyword hits."""

    units: list[tuple[int, int, str]] = []
    sentence_start = 0
    boundaries = list(_SENTENCE_BOUNDARY.finditer(paragraph))
    sentence_ranges: list[tuple[int, int]] = []
    for item in boundaries:
        sentence_ranges.append((sentence_start, item.end()))
        sentence_start = item.end()
    if sentence_start < len(paragraph):
        sentence_ranges.append((sentence_start, len(paragraph)))
    for start, end in sentence_ranges:
        sentence = paragraph[start:end]
        for clause in re.finditer(r"[^;]+", sentence):
            clause_start = start + clause.start()
            clause_end = start + clause.end()
            while clause_start < clause_end and paragraph[clause_start].isspace():
                clause_start += 1
            while clause_end > clause_start and paragraph[clause_end - 1].isspace():
                clause_end -= 1
            if clause_start < clause_end:
                units.append((clause_start, clause_end, paragraph[clause_start:clause_end]))
    return tuple(units)


def _trim_evidence_prefix(start: int, end: int, text: str) -> tuple[int, int, str]:
    """Remove non-evidentiary discourse/context prefixes from one local unit."""

    for pattern in _EVIDENCE_PREFIXES:
        match = pattern.match(text)
        if match:
            return start + match.end(), end, text[match.end():]
    return start, end, text


def derive_facet_coverage_inputs(
    facets: Sequence[FacetSpec],
    members: Sequence[ChunkSnapshot],
    *,
    token_pattern: str = r"[A-Za-z0-9_]+",
    minimum_token_length: int = 3,
    stopwords: Iterable[str] = (),
    covered_min_distinct_matches: int = 2,
    uncovered_status: str = "missing",
) -> tuple[FacetCoverageInput, ...]:
    """Derive conservative facet coverage from minimal discriminative ranges.

    Shared/generic tokens cannot establish coverage by themselves.  Evidence is
    mapped back to the smallest contiguous member range that contains the
    matched facet-specific terms.  Ambiguous cases downgrade instead of
    borrowing evidence from a sibling facet.
    """

    if not facets or not members:
        return ()
    if covered_min_distinct_matches < 1:
        raise T6ArtifactValidationError("covered_min_distinct_matches must be positive")
    if uncovered_status not in {"missing", "not_assessed"}:
        raise T6ArtifactValidationError("uncovered_status must be missing or not_assessed")
    normalized_stopwords = {_facet_token(item) for item in _DEFAULT_FACET_STOPWORDS}
    normalized_stopwords.update(_facet_token(item) for item in stopwords)
    facet_vocab: dict[str, set[str]] = {}
    for facet in facets:
        facet_vocab[facet.facet_id] = _facet_tokens(
            f"{facet.facet_id} {facet.text}",
            token_pattern=token_pattern,
            minimum_token_length=minimum_token_length,
            stopwords=normalized_stopwords,
        )

    boundaries_present = all(
        member.paragraph_start is not None and member.paragraph_end is not None
        for member in members
    )
    if boundaries_present:
        ordered = sorted(members, key=lambda item: item.paragraph_start)
        cursor = 0
        pieces: list[str] = []
        for member in ordered:
            if member.paragraph_start != cursor or member.paragraph_end - member.paragraph_start != len(member.content):
                raise T6ArtifactValidationError("facet evidence members do not form contiguous paragraph slices")
            pieces.append(member.content)
            cursor = member.paragraph_end
        paragraph = "".join(pieces)
    else:
        ordered = list(members)
        paragraph = "\n".join(member.content for member in ordered)

    token_re = re.compile(token_pattern)
    occurrences: list[tuple[int, int, str]] = []
    for match in token_re.finditer(paragraph.replace("_", " ").replace("-", " ")):
        token = _facet_token(match.group(0))
        if len(match.group(0)) >= minimum_token_length and token not in normalized_stopwords:
            occurrences.append((match.start(), match.end(), token))

    results: list[FacetCoverageInput] = []
    all_ids = [facet.facet_id for facet in facets]
    for facet in facets:
        sibling_tokens: set[str] = set()
        for other_id in all_ids:
            if other_id != facet.facet_id:
                sibling_tokens.update(facet_vocab[other_id])
        distinctive = facet_vocab[facet.facet_id] - sibling_tokens
        matches = [item for item in occurrences if item[2] in facet_vocab[facet.facet_id]]
        distinctive_matches = [item for item in matches if item[2] in distinctive]
        if not distinctive_matches:
            results.append(FacetCoverageInput(
                facet.facet_id,
                uncovered_status,
                (),
                (),
                (),
                (("SINGLE_PARAGRAPH_HOLDOUT_NOT_ASSESSED",)
                 if uncovered_status == "not_assessed"
                 else ("NO_DISCRIMINATIVE_FACET_ANCHOR",)),
            ))
            continue

        primary_tokens = _facet_tokens(
            facet.facet_id,
            token_pattern=token_pattern,
            minimum_token_length=minimum_token_length,
            stopwords=normalized_stopwords,
        )
        facet_sequence = _facet_token_sequence(
            facet.text,
            token_pattern=token_pattern,
            minimum_token_length=minimum_token_length,
            stopwords=normalized_stopwords,
        )
        facet_bigrams = set(zip(facet_sequence, facet_sequence[1:]))
        comparison_facet = bool(primary_tokens & {"compare", "comparison", "difference"})
        selected_ranges: list[tuple[int, int]] = []
        seen_bigrams: set[tuple[str, str]] = set()
        numeric_counts: dict[tuple[str, str], int] = {}
        for unit_start, unit_end, unit_text in _minimal_evidence_units(paragraph):
            evidence_start, evidence_end, evidence_text = _trim_evidence_prefix(
                unit_start, unit_end, unit_text
            )
            unit_sequence = _facet_token_sequence(
                evidence_text,
                token_pattern=token_pattern,
                minimum_token_length=minimum_token_length,
                stopwords=normalized_stopwords,
            )
            unit_tokens = set(unit_sequence)
            matched_bigrams = set(zip(unit_sequence, unit_sequence[1:])) & facet_bigrams
            if not (unit_tokens & primary_tokens):
                continue
            if not matched_bigrams:
                continue
            if not (unit_tokens & _RESULT_SIGNAL_TOKENS):
                continue
            numeric = bool(_NUMERIC_EVIDENCE.search(evidence_text))
            new_bigrams = matched_bigrams - seen_bigrams
            counts = [numeric_counts.get(item, 0) for item in matched_bigrams]
            keep = bool(new_bigrams)
            if numeric and any(count == 0 for count in counts):
                keep = True
            if numeric and comparison_facet and counts and min(counts) < 2:
                keep = True
            if not keep:
                continue
            selected_ranges.append((evidence_start, evidence_end))
            seen_bigrams.update(matched_bigrams)
            if numeric:
                for item in matched_bigrams:
                    numeric_counts[item] = numeric_counts.get(item, 0) + 1

        if not selected_ranges:
            results.append(FacetCoverageInput(
                facet.facet_id,
                uncovered_status,
                (),
                (),
                (),
                (("SINGLE_PARAGRAPH_HOLDOUT_NOT_ASSESSED",)
                 if uncovered_status == "not_assessed"
                 else ("NO_DISCRIMINATIVE_FACET_EVIDENCE_UNIT",)),
            ))
            continue

        matched_terms = tuple(sorted({item[2] for item in matches}))

        support: list[ArtifactPointer] = []
        if boundaries_present:
            for member in ordered:
                if any(
                    member.paragraph_end > evidence_start and member.paragraph_start < evidence_end
                    for evidence_start, evidence_end in selected_ranges
                ):
                    support.append(member.chunk_ref)
        else:
            support = [
                member.chunk_ref for member in ordered
                if any(token in _facet_tokens(
                    member.content,
                    token_pattern=token_pattern,
                    minimum_token_length=minimum_token_length,
                    stopwords=normalized_stopwords,
                ) for token in matched_terms)
            ]

        distinct_count = len({item[2] for item in distinctive_matches})
        total_count = len({item[2] for item in matches})
        if distinct_count >= 1 and total_count >= covered_min_distinct_matches and support:
            status = "covered"
            reasons = ("DISCRIMINATIVE_ANCHOR_AND_MINIMUM_MATCHES",)
        else:
            status = "partial"
            reasons = ("DISCRIMINATIVE_ANCHOR_WITH_INCOMPLETE_MATCHES",)
        results.append(FacetCoverageInput(
            facet.facet_id,
            status,
            tuple(support),
            matched_terms,
            tuple(selected_ranges),
            reasons,
        ))
    return tuple(results)


def build_span_assembly_policy(
    *,
    policy_version: str,
    producer_version: str,
    run_ref: str,
    route_snapshot_ref: str,
    created_at: str,
    locator_path: Path | str,
    assembly_separator: str = "\n",
    artifact_id: str | None = None,
    supersedes: ArtifactPointer | None = None,
) -> FrozenArtifactRecord:
    artifact_id = artifact_id or new_artifact_id()
    if not isinstance(assembly_separator, str):
        raise T6ArtifactValidationError("assembly_separator must be a string")
    if supersedes is not None and supersedes.artifact_id == artifact_id:
        raise T6ArtifactValidationError("policy update must use a new artifact_id")
    payload = {
        "artifact_type": "span_assembly_policy",
        "artifact_id": artifact_id,
        "policy_version": _require_text(policy_version, "policy_version"),
        "assembly_separator": assembly_separator,
        "allowed_logical_roles": ["single_chunk", "table_with_footnotes", "paragraph_continuation"],
        "max_member_count": 32,
        "require_same_paper": True,
        "require_same_section": True,
        "require_consecutive_sequence": True,
        "producer_module": "M3",
        "producer_version": producer_version,
        "method_policy_ref": "PR-OS-P02-T06-SPAN-ASSEMBLY-v1",
        "run_ref": run_ref,
        "route_snapshot_ref": route_snapshot_ref,
        "created_at": created_at,
        "supersedes": supersedes.to_dict() if supersedes else None,
    }
    links = (
        [resolved_parent_link(supersedes, relation="supersedes", source_field="supersedes", evidence_ref=run_ref)]
        if supersedes else []
    )
    return build_t6_artifact_record(
        payload,
        locator_path=locator_path,
        parent_links=links,
        source_artifact_ids=[supersedes.artifact_id] if supersedes else [],
        artifact_schema_ref="PR-OS-P02-T06-SPAN-ASSEMBLY-POLICY@r1",
        evidence_refs=[run_ref],
    )


def build_logical_span_artifact(
    runtime_span: LogicalSpan,
    *,
    members: Sequence[ChunkSnapshot],
    span_policy: FrozenArtifactRecord,
    producer_version: str,
    run_ref: str,
    route_snapshot_ref: str,
    created_at: str,
    locator_path: Path | str,
    artifact_id: str | None = None,
) -> FrozenArtifactRecord:
    if span_policy.artifact_type != "span_assembly_policy":
        raise T6ArtifactValidationError("span_policy must be a span_assembly_policy")
    if not members:
        raise T6ArtifactValidationError("logical span requires at least one frozen member")
    policy = span_policy.payload
    member_ids = tuple(member.chunk_id for member in members)
    if member_ids != tuple(runtime_span.member_chunk_ids):
        raise T6ArtifactValidationError("runtime member IDs differ from frozen member order")
    if len(member_ids) != len(set(member_ids)):
        raise T6ArtifactValidationError("logical span members must be unique")
    if len(members) > policy["max_member_count"]:
        raise T6ArtifactValidationError("logical span exceeds policy max_member_count")
    if runtime_span.logical_role not in policy["allowed_logical_roles"]:
        raise T6ArtifactValidationError("logical span role is not structurally approved")
    if policy["require_same_paper"] and len({item.paper_id for item in members}) != 1:
        raise T6ArtifactValidationError("semantic/cross-paper span fusion is forbidden")
    if policy["require_same_section"] and len({item.section_path for item in members}) != 1:
        raise T6ArtifactValidationError("semantic/cross-section span fusion is forbidden")
    if policy["require_consecutive_sequence"] and len(members) > 1:
        sequences = [item.sequence_index for item in members]
        if sequences != list(range(sequences[0], sequences[0] + len(sequences))):
            raise T6ArtifactValidationError("span members must be strict structural neighbours")
    separator = policy["assembly_separator"]
    assembled = separator.join(member.content for member in members if member.content)
    if runtime_span.content != assembled:
        raise T6ArtifactValidationError(
            "runtime span content modifies members or uses an ungoverned assembly"
        )
    if runtime_span.paper_id != members[0].paper_id or runtime_span.section_path != members[0].section_path:
        raise T6ArtifactValidationError("runtime span scope differs from frozen members")
    span_policy_ref = span_policy.as_pointer()
    provenance = [
        {
            "chunk_ref": member.chunk_ref.to_dict(),
            "original_boundary": {
                "chunk_id": member.chunk_id,
                "paper_id": member.paper_id,
                "section_path": member.section_path,
                "block_type": member.block_type,
                "page_start": member.page_start,
                "page_end": member.page_end,
                "sequence_index": member.sequence_index,
                "text_hash": member.text_hash,
            },
        }
        for member in members
    ]
    payload = {
        "artifact_type": "logical_span",
        "artifact_id": artifact_id or new_artifact_id(),
        "logical_span_id": runtime_span.logical_span_id,
        "member_chunk_ids": list(member_ids),
        "content": assembled,
        "paper_id": runtime_span.paper_id,
        "section_path": runtime_span.section_path,
        "logical_role": runtime_span.logical_role,
        "block_type": runtime_span.block_type,
        "page_start": runtime_span.page_start,
        "page_end": runtime_span.page_end,
        "facet_ids": list(runtime_span.facet_ids),
        "retrieval_reasons": list(runtime_span.retrieval_reasons),
        "primary_source_id": member_ids[0],
        "span_reason": runtime_span.logical_role,
        "text_hash": _sha256_text(assembled),
        "member_provenance": provenance,
        "producer_module": "M3",
        "producer_version": producer_version,
        "method_policy_ref": span_policy_ref.to_dict(),
        "input_artifact_refs": [member.chunk_ref.to_dict() for member in members],
        "run_ref": run_ref,
        "route_snapshot_ref": route_snapshot_ref,
        "created_at": created_at,
    }
    links = [
        resolved_parent_link(member.chunk_ref, relation="assembled_from", source_field="member_chunk_ids", evidence_ref=run_ref)
        for member in members
    ]
    links.append(
        resolved_parent_link(span_policy_ref, relation="governed_by", source_field="method_policy_ref", evidence_ref=run_ref)
    )
    return build_t6_artifact_record(
        payload,
        locator_path=locator_path,
        parent_links=links,
        paper_ids=[runtime_span.paper_id],
        source_artifact_ids=[member.chunk_ref.artifact_id for member in members] + [span_policy_ref.artifact_id],
        artifact_schema_ref="PR-OS-P02-T06-LOGICAL-SPAN@r1",
        evidence_refs=[run_ref],
    )


def build_source_role_policy(
    *,
    policy_version: str,
    classification_rules: Sequence[Mapping[str, Any]],
    confidence_rules: Sequence[Mapping[str, Any]],
    producer_version: str,
    run_ref: str,
    route_snapshot_ref: str,
    created_at: str,
    locator_path: Path | str,
    artifact_id: str | None = None,
    supersedes: ArtifactPointer | None = None,
) -> FrozenArtifactRecord:
    artifact_id = artifact_id or new_artifact_id()
    if supersedes is not None and supersedes.artifact_id == artifact_id:
        raise T6ArtifactValidationError("policy update must create a new artifact")
    payload = {
        "artifact_type": "source_role_policy",
        "artifact_id": artifact_id,
        "policy_version": policy_version,
        "roles": sorted(SOURCE_ROLES),
        "classification_rules": [dict(item) for item in classification_rules],
        "confidence_rules": [dict(item) for item in confidence_rules],
        "producer_module": "M3",
        "producer_version": producer_version,
        "method_policy_ref": "PR-OS-P02-T06-SOURCE-ROLE-POLICY-v1",
        "run_ref": run_ref,
        "route_snapshot_ref": route_snapshot_ref,
        "created_at": created_at,
        "supersedes": supersedes.to_dict() if supersedes else None,
    }
    links = (
        [resolved_parent_link(supersedes, relation="supersedes", source_field="supersedes", evidence_ref=run_ref)]
        if supersedes else []
    )
    return build_t6_artifact_record(
        payload,
        locator_path=locator_path,
        parent_links=links,
        source_artifact_ids=[supersedes.artifact_id] if supersedes else [],
        artifact_schema_ref="PR-OS-P02-T06-SOURCE-ROLE-POLICY@r1",
        evidence_refs=[run_ref],
    )


def build_references_bias_policy(
    *,
    policy_version: str,
    section_confidence_threshold: float,
    entry_pattern_confidence_threshold: float,
    exclusion_rules: Sequence[Mapping[str, Any]],
    producer_version: str,
    run_ref: str,
    route_snapshot_ref: str,
    created_at: str,
    locator_path: Path | str,
    artifact_id: str | None = None,
    supersedes: ArtifactPointer | None = None,
) -> FrozenArtifactRecord:
    for field, value in (
        ("section_confidence_threshold", section_confidence_threshold),
        ("entry_pattern_confidence_threshold", entry_pattern_confidence_threshold),
    ):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
            raise T6ArtifactValidationError(f"{field} must be within [0, 1]")
    artifact_id = artifact_id or new_artifact_id()
    if supersedes is not None and supersedes.artifact_id == artifact_id:
        raise T6ArtifactValidationError("policy update must create a new artifact")
    payload = {
        "artifact_type": "references_bias_policy",
        "artifact_id": artifact_id,
        "policy_version": policy_version,
        "section_confidence_threshold": section_confidence_threshold,
        "entry_pattern_confidence_threshold": entry_pattern_confidence_threshold,
        "exclusion_rules": [dict(item) for item in exclusion_rules],
        "warning_rule": "non_references_valid_facet_support = 0 AND references_support > 0",
        "metadata_unknown_behavior": "keep_and_warn",
        "producer_module": "M3",
        "producer_version": producer_version,
        "method_policy_ref": "PR-OS-P02-T06-REFERENCES-BIAS-POLICY-v1",
        "run_ref": run_ref,
        "route_snapshot_ref": route_snapshot_ref,
        "created_at": created_at,
        "supersedes": supersedes.to_dict() if supersedes else None,
    }
    links = (
        [resolved_parent_link(supersedes, relation="supersedes", source_field="supersedes", evidence_ref=run_ref)]
        if supersedes else []
    )
    return build_t6_artifact_record(
        payload,
        locator_path=locator_path,
        parent_links=links,
        source_artifact_ids=[supersedes.artifact_id] if supersedes else [],
        artifact_schema_ref="PR-OS-P02-T06-REFERENCES-BIAS-POLICY@r1",
        evidence_refs=[run_ref],
    )


def build_source_role_assessment(
    *,
    subject_ref: ArtifactPointer,
    subject_kind: str,
    subject_artifact: FrozenArtifactRecord | None = None,
    policy: FrozenArtifactRecord,
    member_roles: Sequence[MemberRole],
    producer_version: str,
    run_ref: str,
    route_snapshot_ref: str,
    computed_at: str,
    locator_path: Path | str,
    artifact_id: str | None = None,
) -> FrozenArtifactRecord:
    if subject_kind not in SUBJECT_KINDS:
        raise T6ArtifactValidationError("subject_kind is invalid")
    if policy.artifact_type != "source_role_policy":
        raise T6ArtifactValidationError("SourceRoleAssessment must reference SourceRolePolicy")
    if not member_roles:
        raise T6ArtifactValidationError("SourceRoleAssessment requires member roles")
    if subject_kind == "logical_span":
        if not isinstance(subject_artifact, FrozenArtifactRecord) or subject_artifact.artifact_type != "logical_span":
            raise T6ArtifactValidationError("logical_span role assessment requires the frozen subject artifact")
        if subject_artifact.as_pointer() != subject_ref:
            raise T6ArtifactValidationError("subject_ref does not match frozen logical_span artifact")
        expected_member_refs = [
            item["chunk_ref"] for item in subject_artifact.payload["member_provenance"]
        ]
        supplied_member_refs = [item.chunk_ref.to_dict() for item in member_roles]
        if supplied_member_refs != expected_member_refs:
            raise T6ArtifactValidationError(
                "member roles must exactly match admitted logical_span members and order"
            )
    allowed_roles = set(policy.payload["roles"])
    seen: set[str] = set()
    role_payload = []
    for item in member_roles:
        if item.chunk_ref.artifact_id in seen:
            raise T6ArtifactValidationError("member role chunk refs must be unique")
        seen.add(item.chunk_ref.artifact_id)
        if item.role not in allowed_roles:
            raise T6ArtifactValidationError("role is not allowed by SourceRolePolicy")
        if not 0 <= item.confidence <= 1:
            raise T6ArtifactValidationError("role confidence must be within [0, 1]")
        role_payload.append(
            {
                "chunk_ref": item.chunk_ref.to_dict(),
                "role": item.role,
                "confidence": item.confidence,
                "evidence_refs": list(item.evidence_refs),
            }
        )
    policy_ref = policy.as_pointer()
    inputs = [subject_ref, policy_ref, *(item.chunk_ref for item in member_roles)]
    payload = {
        "artifact_type": "source_role_assessment",
        "artifact_id": artifact_id or new_artifact_id(),
        "subject_source_set_ref": subject_ref.to_dict(),
        "subject_kind": subject_kind,
        "source_role_policy_ref": policy_ref.to_dict(),
        "member_roles": role_payload,
        "producer_module": "M3",
        "producer_version": producer_version,
        "method_policy_ref": policy_ref.to_dict(),
        "input_artifact_refs": [item.to_dict() for item in inputs],
        "run_ref": run_ref,
        "route_snapshot_ref": route_snapshot_ref,
        "computed_at": computed_at,
        "field_provenance": {"member_roles": "M3"},
    }
    links = [
        resolved_parent_link(subject_ref, relation="assesses", source_field="subject_source_set_ref", evidence_ref=run_ref),
        resolved_parent_link(policy_ref, relation="governed_by", source_field="source_role_policy_ref", evidence_ref=run_ref),
    ]
    return build_t6_artifact_record(
        payload,
        locator_path=locator_path,
        parent_links=links,
        source_artifact_ids=[item.artifact_id for item in inputs],
        artifact_schema_ref="PR-OS-P02-T06-SOURCE-ROLE-ASSESSMENT@r1",
        evidence_refs=[run_ref],
    )


def build_facet_definition_artifact(
    *,
    question_ref: ArtifactPointer,
    question: str,
    source: str,
    policy_ref: ArtifactPointer,
    facets: Sequence[FacetSpec],
    producer_version: str,
    run_ref: str,
    route_snapshot_ref: str,
    created_at: str,
    locator_path: Path | str,
    artifact_id: str | None = None,
) -> FrozenArtifactRecord:
    if source not in FACET_SOURCES:
        raise T6ArtifactValidationError("facet source is not approved")
    if source == "m3_query_understanding" and not question_ref.frozen:
        raise T6ArtifactValidationError("m3_query_understanding must be a frozen replay")
    if not facets:
        raise T6ArtifactValidationError("facet definition must not be empty")
    ids = [item.facet_id for item in facets]
    if len(ids) != len(set(ids)):
        raise T6ArtifactValidationError("facet IDs must be unique")
    payload = {
        "artifact_type": "facet_definition",
        "artifact_id": artifact_id or new_artifact_id(),
        "question_ref": question_ref.to_dict(),
        "question_hash": _sha256_text(_require_text(question, "question")),
        "source": source,
        "policy_ref": policy_ref.to_dict(),
        "facets": [
            {"facet_id": item.facet_id, "text": item.text, "required": item.required}
            for item in facets
        ],
        "producer_module": "M3",
        "producer_version": producer_version,
        "input_artifact_refs": [question_ref.to_dict(), policy_ref.to_dict()],
        "run_ref": run_ref,
        "route_snapshot_ref": route_snapshot_ref,
        "created_at": created_at,
    }
    links = [
        resolved_parent_link(question_ref, relation="defines_facets_for", source_field="question_ref", evidence_ref=run_ref),
        resolved_parent_link(policy_ref, relation="governed_by", source_field="policy_ref", evidence_ref=run_ref),
    ]
    return build_t6_artifact_record(
        payload,
        locator_path=locator_path,
        parent_links=links,
        source_artifact_ids=[question_ref.artifact_id, policy_ref.artifact_id],
        artifact_schema_ref="PR-OS-P02-T06-FACET-DEFINITION@r1",
        evidence_refs=[run_ref],
    )


def _t5_ref_ready(
    pointer: ArtifactPointer | None,
    expected_type: str,
    *,
    allow_fixture_refs: bool,
) -> tuple[bool, str | None]:
    if pointer is None:
        return False, f"{expected_type.upper()}_MISSING"
    if pointer.artifact_type != expected_type:
        return False, f"{expected_type.upper()}_TYPE_MISMATCH"
    if not pointer.frozen or not pointer.approved:
        return False, f"{expected_type.upper()}_NOT_FROZEN_APPROVED"
    if pointer.fixture_only and not allow_fixture_refs:
        return False, f"{expected_type.upper()}_FIXTURE_NOT_ALLOWED"
    return True, None


def build_retrieval_coverage_assessment(
    *,
    context_pack_ref: ArtifactPointer,
    facet_definition: FrozenArtifactRecord,
    source_role_assessment: FrozenArtifactRecord,
    references_bias_policy: FrozenArtifactRecord,
    summary_policy_ref: ArtifactPointer,
    answer_complete_policy_ref: ArtifactPointer,
    facet_results: Sequence[FacetCoverageInput],
    unique_chunk_count: int,
    unique_section_count: int,
    excluded_candidate_refs: Sequence[ArtifactPointer],
    unused_capacity_reason: str | None,
    required_slot_assessment_ref: ArtifactPointer | None,
    fact_identity_sidecar_ref: ArtifactPointer | None,
    producer_version: str,
    run_ref: str,
    route_snapshot_ref: str,
    computed_at: str,
    locator_path: Path | str,
    query_facets_projection: Sequence[Mapping[str, Any]] | None = None,
    allow_fixture_refs: bool = False,
    source_non_authoritative: bool | None = None,
    artifact_id: str | None = None,
) -> FrozenArtifactRecord:
    if not isinstance(facet_definition, FrozenArtifactRecord) or facet_definition.artifact_type != "facet_definition":
        raise T6ArtifactValidationError("coverage requires a valid FacetDefinitionArtifact")
    if source_role_assessment.artifact_type != "source_role_assessment":
        raise T6ArtifactValidationError("coverage requires a SourceRoleAssessment")
    if references_bias_policy.artifact_type != "references_bias_policy":
        raise T6ArtifactValidationError("coverage requires a distinct ReferencesBiasPolicy")
    if summary_policy_ref.artifact_type == "source_role_policy":
        raise T6ArtifactValidationError("SourceRolePolicy cannot be used as summary policy")
    if answer_complete_policy_ref.artifact_type == "references_bias_policy":
        raise T6ArtifactValidationError("ReferencesBiasPolicy cannot produce answer_complete")
    facets = facet_definition.payload["facets"]
    projection = [dict(item) for item in (query_facets_projection if query_facets_projection is not None else facets)]
    if canonical_json({"facets": projection}) != canonical_json({"facets": facets}):
        raise T6ArtifactValidationError("query_facets_projection drifted from FacetDefinitionArtifact")

    required_ok, required_reason = _t5_ref_ready(
        required_slot_assessment_ref,
        "required_slot_assessment",
        allow_fixture_refs=allow_fixture_refs,
    )
    sidecar_ok, sidecar_reason = _t5_ref_ready(
        fact_identity_sidecar_ref,
        "fact_identity_sidecar",
        allow_fixture_refs=allow_fixture_refs,
    )
    t5_ready = required_ok and sidecar_ok
    reasons = [item for item in (required_reason, sidecar_reason) if item]
    t5_fixture = bool(
        t5_ready
        and (required_slot_assessment_ref.fixture_only or fact_identity_sidecar_ref.fixture_only)
    )

    expected_ids = [item["facet_id"] for item in facets]
    supplied = {item.facet_id: item for item in facet_results}
    if len(supplied) != len(facet_results):
        raise T6ArtifactValidationError("facet_results contain duplicate facet IDs")
    if t5_ready and set(supplied) != set(expected_ids):
        raise T6ArtifactValidationError("facet_results must exactly cover frozen facet IDs")

    role_by_source: dict[str, str] = {}
    pointer_by_source: dict[str, dict[str, Any]] = {}
    source_role_payload = source_role_assessment.payload
    source_role_policy_ref = source_role_payload["source_role_policy_ref"]
    if source_role_policy_ref["artifact_id"] == references_bias_policy.artifact_id:
        raise T6ArtifactValidationError("SourceRolePolicy/ReferencesBiasPolicy mixup")
    for item in source_role_payload["member_roles"]:
        role_by_source[item["chunk_ref"]["artifact_id"]] = item["role"]
        pointer_by_source[item["chunk_ref"]["artifact_id"]] = item["chunk_ref"]

    coverage_rows: list[dict[str, Any]] = []
    references_only_warning = False
    if not t5_ready:
        for facet in facets:
            coverage_rows.append(
                {"facet_id": facet["facet_id"], "status": "not_assessed", "supporting_source_refs": []}
            )
        overall = "not_assessed"
        missing_ids: list[str] = []
        answer_complete: bool | None = None
    else:
        for facet in facets:
            item = supplied[facet["facet_id"]]
            if item.status not in FACET_STATUSES:
                raise T6ArtifactValidationError("assessed facet status is invalid")
            refs = list(item.supporting_source_refs)
            status = item.status
            if len({ref.artifact_id for ref in refs}) != len(refs):
                raise T6ArtifactValidationError("supporting_source_refs must be unique")
            for ref in refs:
                expected_pointer = pointer_by_source.get(ref.artifact_id)
                if expected_pointer is None or expected_pointer != ref.to_dict():
                    raise T6ArtifactValidationError(
                        "supporting source is not an exact admitted SourceRoleAssessment member"
                    )
            if status in {"covered", "partial"} and not refs:
                raise T6ArtifactValidationError("covered/partial facet requires admitted supporting sources")
            if status in {"missing", "not_assessed"} and refs:
                raise T6ArtifactValidationError("missing/not_assessed facet cannot retain supporting sources")
            roles = [role_by_source[ref.artifact_id] for ref in refs]
            if status == "covered" and any(role == "unknown" for role in roles):
                status = "partial"
            if refs and all(role == "references" for role in roles):
                references_only_warning = True
                if status == "covered":
                    status = "partial"
            coverage_rows.append(
                {
                    "facet_id": facet["facet_id"],
                    "status": status,
                    "supporting_source_refs": [ref.to_dict() for ref in refs],
                }
            )
        required_statuses = [
            row["status"]
            for row, facet in zip(coverage_rows, facets)
            if facet["required"]
        ]
        if required_statuses and all(item == "covered" for item in required_statuses):
            overall = "sufficient"
        elif any(item in {"covered", "partial"} for item in required_statuses):
            overall = "partial"
        elif required_statuses and all(item == "not_assessed" for item in required_statuses):
            overall = "not_assessed"
        else:
            overall = "insufficient"
        missing_ids = [
            row["facet_id"]
            for row, facet in zip(coverage_rows, facets)
            if facet["required"] and row["status"] in {"missing", "partial"}
        ]
        answer_complete = None if overall == "not_assessed" else overall == "sufficient"

    if source_non_authoritative is not None and not isinstance(source_non_authoritative, bool):
        raise T6ArtifactValidationError("source_non_authoritative must be boolean or null")

    facet_ref = facet_definition.as_pointer()
    source_role_ref = source_role_assessment.as_pointer()
    references_ref = references_bias_policy.as_pointer()
    input_pointers: list[ArtifactPointer] = [
        context_pack_ref,
        facet_ref,
        source_role_ref,
        references_ref,
        summary_policy_ref,
        answer_complete_policy_ref,
    ]
    if required_slot_assessment_ref is not None:
        input_pointers.append(required_slot_assessment_ref)
    if fact_identity_sidecar_ref is not None:
        input_pointers.append(fact_identity_sidecar_ref)
    input_pointers.extend(excluded_candidate_refs)
    payload = {
        "artifact_type": "retrieval_coverage_assessment",
        "artifact_id": artifact_id or new_artifact_id(),
        "producer_module": "M3",
        "facet_definition_ref": facet_ref.to_dict(),
        "query_facets_projection": projection,
        "retrieval_coverage_status": overall,
        "facet_coverage": coverage_rows,
        "missing_facet_ids": missing_ids,
        "answer_complete": answer_complete,
        "answer_complete_policy_ref": answer_complete_policy_ref.to_dict(),
        "unique_chunk_count": unique_chunk_count,
        "unique_section_count": unique_section_count,
        "references_only_warning": references_only_warning,
        "references_bias_policy_ref": references_ref.to_dict(),
        "excluded_candidate_refs": [item.to_dict() for item in excluded_candidate_refs],
        "unused_capacity_reason": unused_capacity_reason,
        "summary_policy_ref": summary_policy_ref.to_dict(),
        "required_slot_assessment_ref": required_slot_assessment_ref.to_dict() if required_slot_assessment_ref else None,
        "fact_identity_sidecar_ref": fact_identity_sidecar_ref.to_dict() if fact_identity_sidecar_ref else None,
        "t5_context_status": "fixture_only" if t5_fixture else "available" if t5_ready else "not_assessed",
        "not_assessed_reason_codes": reasons,
        "producer_version": producer_version,
        "method_policy_ref": summary_policy_ref.to_dict(),
        "input_artifact_refs": [item.to_dict() for item in input_pointers],
        "run_ref": run_ref,
        "route_snapshot_ref": route_snapshot_ref,
        "computed_at": computed_at,
        "field_provenance": {
            "retrieval_coverage_status": "M3",
            "facet_coverage": "M3",
            "missing_facet_ids": "M3",
            "answer_complete": "M3",
        },
        "verification_scope": "governance_diagnostic",
        "non_authoritative": t5_fixture if source_non_authoritative is None else source_non_authoritative,
    }
    parents = [
        (context_pack_ref, "assesses", "context_pack_ref"),
        (facet_ref, "uses_facets", "facet_definition_ref"),
        (source_role_ref, "uses_roles", "source_role_assessment_ref"),
        (references_ref, "governed_by", "references_bias_policy_ref"),
        (summary_policy_ref, "summarized_by", "summary_policy_ref"),
        (answer_complete_policy_ref, "completed_by", "answer_complete_policy_ref"),
    ]
    if t5_ready:
        parents.extend(
            [
                (required_slot_assessment_ref, "uses_background", "required_slot_assessment_ref"),
                (fact_identity_sidecar_ref, "uses_background", "fact_identity_sidecar_ref"),
            ]
        )
    links = [
        resolved_parent_link(pointer, relation=relation, source_field=field, evidence_ref=run_ref)
        for pointer, relation, field in parents
    ]
    return build_t6_artifact_record(
        payload,
        locator_path=locator_path,
        parent_links=links,
        source_artifact_ids=[item.artifact_id for item in input_pointers],
        artifact_schema_ref="PR-OS-P02-T06-RETRIEVAL-COVERAGE-ASSESSMENT@r1",
        evidence_refs=[run_ref],
    )


def legacy_analysis_projection(coverage: FrozenArtifactRecord) -> dict[str, Any]:
    if coverage.artifact_type != "retrieval_coverage_assessment":
        raise T6ArtifactValidationError("legacy projection requires retrieval coverage")
    payload = coverage.payload
    return {
        "coverage_status": payload["retrieval_coverage_status"],
        "missing_facets": tuple(payload["missing_facet_ids"]),
        "answer_complete": payload["answer_complete"],
        "coverage_assessment_ref": coverage.artifact_id,
        "coverage_assessment_hash": coverage.content_hash,
    }


def validate_artifact_graph(
    records: Iterable[FrozenArtifactRecord],
    *,
    external_pointers: Iterable[ArtifactPointer] = (),
) -> None:
    """Verify parent hashes and reject cycles without touching the real Registry."""

    record_list = tuple(records)
    record_map = {record.artifact_id: record for record in record_list}
    if len(record_map) != len(record_list):
        raise T6ArtifactValidationError("artifact graph contains duplicate artifact IDs")
    external = {item.artifact_id: item for item in external_pointers}
    graph: dict[str, list[str]] = {artifact_id: [] for artifact_id in record_map}
    for artifact_id, record in record_map.items():
        for link in record.envelope["parent_artifacts"]:
            parent_id = link["parent_artifact_id"]
            parent = record_map.get(parent_id)
            if parent is not None:
                expected_hash = parent.content_hash
                graph[artifact_id].append(parent_id)
            elif parent_id in external:
                expected_hash = external[parent_id].content_hash
            else:
                raise T6ArtifactValidationError(f"unknown parent artifact: {parent_id}")
            if link["parent_content_hash"] != expected_hash:
                raise T6ArtifactValidationError(f"parent hash mismatch: {parent_id}")
            if record.artifact_type == "logical_span":
                parent_type = parent.artifact_type if parent is not None else external[parent_id].artifact_type
                if parent_type == "context_pack":
                    raise T6ArtifactValidationError("LogicalSpan cannot depend on ContextPack")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise T6ArtifactValidationError("artifact parent cycle detected")
        if node in visited:
            return
        visiting.add(node)
        for parent in graph[node]:
            visit(parent)
        visiting.remove(node)
        visited.add(node)

    for artifact_id in graph:
        visit(artifact_id)


_HOLDOUT_FIELDS = {
    "manifest_id",
    "opaque_fixture_ids",
    "count",
    "commitment_policy_ref",
    "commitment_algorithm",
    "input_package_commitment",
    "gold_package_commitment",
    "curator_role",
    "sealed_at",
}


def validate_holdout_public_manifest(manifest: Mapping[str, Any]) -> None:
    """Validate only the public commitment envelope; never opens Input or Gold."""

    if not isinstance(manifest, Mapping):
        raise T6ArtifactValidationError("HoldoutPublicManifest must be an object")
    _require_exact_fields(manifest, _HOLDOUT_FIELDS, "HoldoutPublicManifest")
    _require_text(manifest.get("manifest_id"), "manifest_id")
    opaque_ids = _require_text_list(manifest.get("opaque_fixture_ids"), "opaque_fixture_ids", unique=True)
    if not opaque_ids or any(not _OPAQUE_ID.fullmatch(item) for item in opaque_ids):
        raise T6ArtifactValidationError("opaque_fixture_ids must be random opaque tokens")
    count = manifest.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count != len(opaque_ids):
        raise T6ArtifactValidationError("holdout count must match opaque_fixture_ids")
    if manifest.get("commitment_policy_ref") != "PR-OS-HOLDOUT-COMMITMENT-v1":
        raise T6ArtifactValidationError("holdout commitment policy is invalid")
    if manifest.get("commitment_algorithm") != "sha256-canonical-bytes-plus-secret-nonce-v1":
        raise T6ArtifactValidationError("holdout commitment algorithm is invalid")
    _require_hash(manifest.get("input_package_commitment"), "input_package_commitment")
    _require_hash(manifest.get("gold_package_commitment"), "gold_package_commitment")
    _require_text(manifest.get("curator_role"), "curator_role")
    _require_iso8601(manifest.get("sealed_at"), "sealed_at")


__all__ = [
    "ArtifactPointer",
    "ChunkSnapshot",
    "FacetCoverageInput",
    "FacetSpec",
    "FrozenArtifactRecord",
    "MemberRole",
    "SourceIdDisposition",
    "T6ArtifactValidationError",
    "admit_chunk_snapshots",
    "build_facet_definition_artifact",
    "build_logical_span_artifact",
    "build_references_bias_policy",
    "build_retrieval_coverage_assessment",
    "build_source_role_assessment",
    "build_source_role_policy",
    "build_span_assembly_policy",
    "build_t6_artifact_record",
    "derive_facet_coverage_inputs",
    "legacy_analysis_projection",
    "pointer_from_payload",
    "resolved_parent_link",
    "validate_artifact_graph",
    "validate_holdout_public_manifest",
    "validate_t6_payload",
]
