"""Memorive RETRIEVAL-QUALITY/RETRIEVAL · immutable coverage-governance artifacts.

This module is deliberately layered above the Core runtime ``LogicalSpan``
and ``ContextPack`` types.  It does not mutate chunks, does not call a model,
and does not write the real ARTIFACT_REGISTRY registry.  Each payload is canonical JSON and
is paired with a validated ARTIFACT_REGISTRY Artifact Envelope sidecar; the envelope hash is
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

from artifact_registry import (
    build_envelope,
    canonical_json,
    parent_link,
    validate_envelope,
)

from .types import LogicalSpan


_ARTIFACT_ID = re.compile(r"^art_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

FACET_SOURCES = {"frozen_fixture", "approved_manual", "retrieval_query_understanding"}
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


class EvidenceArtifactValidationError(ValueError):
    """A Verification artifact violates a frozen contract or provenance boundary."""


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceArtifactValidationError(f"{field} must be a non-empty string")
    return value


def _require_hash(value: Any, field: str) -> str:
    _require_text(value, field)
    if not _SHA256.fullmatch(value):
        raise EvidenceArtifactValidationError(f"{field} must be lowercase SHA-256")
    return value


def _require_artifact_id(value: Any, field: str = "artifact_id") -> str:
    _require_text(value, field)
    if not _ARTIFACT_ID.fullmatch(value):
        raise EvidenceArtifactValidationError(f"{field} has invalid format")
    return value


def _require_iso8601(value: Any, field: str) -> str:
    _require_text(value, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceArtifactValidationError(f"{field} must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceArtifactValidationError(f"{field} must include timezone")
    return value


def _require_exact_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise EvidenceArtifactValidationError(
            f"{label} fields mismatch: missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _require_text_list(value: Any, field: str, *, unique: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise EvidenceArtifactValidationError(f"{field} must be a list")
    for item in value:
        _require_text(item, f"{field} entry")
    if unique and len(value) != len(set(value)):
        raise EvidenceArtifactValidationError(f"{field} entries must be unique")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return canonical_json(value).encode("utf-8")


@dataclass(frozen=True)
class ArtifactPointer:
    """Hash-bound pointer used in Verification payloads and parent links."""

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
            raise EvidenceArtifactValidationError("pointer frozen/approved must be boolean")
        if not isinstance(self.fixture_only, bool):
            raise EvidenceArtifactValidationError("pointer fixture_only must be boolean")

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
        raise EvidenceArtifactValidationError(f"{field} must be an artifact pointer")
    _require_exact_fields(value, _POINTER_FIELDS, field)
    pointer = ArtifactPointer(**dict(value))
    if expected_type is not None and pointer.artifact_type != expected_type:
        raise EvidenceArtifactValidationError(
            f"{field} must reference {expected_type!r}, got {pointer.artifact_type!r}"
        )


@dataclass(frozen=True)
class FrozenArtifactRecord:
    """Immutable canonical payload plus an immutable ARTIFACT_REGISTRY Envelope sidecar."""

    payload_json: str
    envelope_json: str

    def __post_init__(self) -> None:
        try:
            payload = json.loads(self.payload_json)
            envelope = json.loads(self.envelope_json)
        except json.JSONDecodeError as exc:
            raise EvidenceArtifactValidationError("artifact record JSON is invalid") from exc
        if canonical_json(payload) != self.payload_json:
            raise EvidenceArtifactValidationError("payload_json is not canonical JSON")
        if canonical_json(envelope) != self.envelope_json:
            raise EvidenceArtifactValidationError("envelope_json is not canonical JSON")
        validate_verification_payload(payload)
        validate_envelope(envelope)
        if payload["artifact_id"] != envelope["artifact_id"]:
            raise EvidenceArtifactValidationError("payload/envelope artifact_id mismatch")
        if payload["artifact_type"] != envelope["artifact_type"]:
            raise EvidenceArtifactValidationError("payload/envelope artifact_type mismatch")
        actual = _sha256_bytes(self.payload_json.encode("utf-8"))
        if actual != envelope["content_hash"]["value"]:
            raise EvidenceArtifactValidationError("envelope content_hash does not match payload bytes")

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

    def write(self, payload_path: Path | str, envelope_path: Path | str) -> None:
        """Write exact canonical bytes to an already approved sandbox location."""

        Path(payload_path).write_bytes(self.payload_json.encode("utf-8"))
        Path(envelope_path).write_bytes(self.envelope_json.encode("utf-8"))


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
        "fact_identity_sidecar_ref", "intake_context_status", "not_assessed_reason_codes",
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
        raise EvidenceArtifactValidationError(
            f"producer_module must be {expected_producer!r}"
        )
    _require_text(payload.get("producer_version"), "producer_version")
    _validate_pointer(payload.get("method_policy_ref"), "method_policy_ref")
    refs = payload.get("input_artifact_refs")
    if not isinstance(refs, list):
        raise EvidenceArtifactValidationError("input_artifact_refs must be a list")
    for index, ref in enumerate(refs):
        _validate_pointer(ref, f"input_artifact_refs[{index}]")
    _require_text(payload.get("run_ref"), "run_ref")
    _require_text(payload.get("route_snapshot_ref"), "route_snapshot_ref")
    _require_iso8601(payload.get("computed_at"), "computed_at")
    provenance = payload.get("field_provenance")
    if not isinstance(provenance, Mapping) or not provenance:
        raise EvidenceArtifactValidationError("field_provenance must be a non-empty object")
    for field, producer in provenance.items():
        _require_text(field, "field_provenance field")
        if producer != expected_producer:
            raise EvidenceArtifactValidationError(
                f"field_provenance.{field} must equal {expected_producer!r}"
            )


def validate_verification_payload(payload: Mapping[str, Any]) -> None:
    """Strict schema-layer validation for every Verification persistent payload."""

    if not isinstance(payload, Mapping):
        raise EvidenceArtifactValidationError("payload must be an object")
    artifact_type = payload.get("artifact_type")
    if artifact_type not in _SCHEMA_FIELDS:
        raise EvidenceArtifactValidationError(f"unsupported Verification artifact_type: {artifact_type!r}")
    _require_exact_fields(payload, _SCHEMA_FIELDS[artifact_type], artifact_type)
    _require_artifact_id(payload.get("artifact_id"))

    if artifact_type == "logical_span":
        _require_text(payload.get("logical_span_id"), "logical_span_id")
        members = _require_text_list(payload.get("member_chunk_ids"), "member_chunk_ids", unique=True)
        if not members:
            raise EvidenceArtifactValidationError("member_chunk_ids must not be empty")
        _require_text(payload.get("content"), "content")
        _require_hash(payload.get("text_hash"), "text_hash")
        if payload.get("primary_source_id") != members[0]:
            raise EvidenceArtifactValidationError("primary_source_id must be the first member alias")
        provenance = payload.get("member_provenance")
        if not isinstance(provenance, list) or len(provenance) != len(members):
            raise EvidenceArtifactValidationError("member_provenance must match member count")
        for index, item in enumerate(provenance):
            if not isinstance(item, Mapping):
                raise EvidenceArtifactValidationError("member_provenance entries must be objects")
            _require_exact_fields(item, {"chunk_ref", "original_boundary"}, "member_provenance entry")
            _validate_pointer(item["chunk_ref"], f"member_provenance[{index}].chunk_ref")
            boundary = item["original_boundary"]
            if not isinstance(boundary, Mapping):
                raise EvidenceArtifactValidationError("original_boundary must be an object")
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
        if payload.get("producer_module") != "RETRIEVAL":
            raise EvidenceArtifactValidationError("LogicalSpan producer_module must be RETRIEVAL")
        _require_iso8601(payload.get("created_at"), "created_at")

    elif artifact_type == "source_role_assessment":
        _validate_audit_fields(payload, "RETRIEVAL")
        _validate_pointer(payload.get("subject_source_set_ref"), "subject_source_set_ref")
        if payload.get("subject_kind") not in SUBJECT_KINDS:
            raise EvidenceArtifactValidationError("subject_kind is invalid")
        _validate_pointer(payload.get("source_role_policy_ref"), "source_role_policy_ref", expected_type="source_role_policy")
        roles = payload.get("member_roles")
        if not isinstance(roles, list) or not roles:
            raise EvidenceArtifactValidationError("member_roles must be a non-empty list")
        for index, item in enumerate(roles):
            if not isinstance(item, Mapping):
                raise EvidenceArtifactValidationError("member_roles entries must be objects")
            _require_exact_fields(item, {"chunk_ref", "role", "confidence", "evidence_refs"}, "member role")
            _validate_pointer(item["chunk_ref"], f"member_roles[{index}].chunk_ref")
            if item.get("role") not in SOURCE_ROLES:
                raise EvidenceArtifactValidationError("member role is invalid")
            confidence = item.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
                raise EvidenceArtifactValidationError("member role confidence must be within [0, 1]")
            _require_text_list(item.get("evidence_refs"), "member role evidence_refs")

    elif artifact_type == "facet_definition":
        _validate_pointer(payload.get("question_ref"), "question_ref")
        _validate_pointer(payload.get("policy_ref"), "policy_ref")
        _require_hash(payload.get("question_hash"), "question_hash")
        if payload.get("source") not in FACET_SOURCES:
            raise EvidenceArtifactValidationError("facet source is invalid")
        facets = payload.get("facets")
        if not isinstance(facets, list) or not facets:
            raise EvidenceArtifactValidationError("facets must be a non-empty list")
        ids: list[str] = []
        for facet in facets:
            if not isinstance(facet, Mapping):
                raise EvidenceArtifactValidationError("facet entries must be objects")
            _require_exact_fields(facet, {"facet_id", "text", "required"}, "facet")
            ids.append(_require_text(facet.get("facet_id"), "facet_id"))
            _require_text(facet.get("text"), "facet.text")
            if not isinstance(facet.get("required"), bool):
                raise EvidenceArtifactValidationError("facet.required must be boolean")
        if len(ids) != len(set(ids)):
            raise EvidenceArtifactValidationError("facet_id values must be unique")
        _require_iso8601(payload.get("created_at"), "created_at")

    elif artifact_type == "retrieval_coverage_assessment":
        _validate_audit_fields(payload, "RETRIEVAL")
        _validate_pointer(payload.get("facet_definition_ref"), "facet_definition_ref", expected_type="facet_definition")
        _validate_pointer(payload.get("answer_complete_policy_ref"), "answer_complete_policy_ref")
        _validate_pointer(payload.get("references_bias_policy_ref"), "references_bias_policy_ref", expected_type="references_bias_policy")
        _validate_pointer(payload.get("summary_policy_ref"), "summary_policy_ref")
        status = payload.get("retrieval_coverage_status")
        if status not in COVERAGE_STATUSES:
            raise EvidenceArtifactValidationError("retrieval_coverage_status is invalid")
        answer = payload.get("answer_complete")
        if status == "not_assessed":
            if answer is not None:
                raise EvidenceArtifactValidationError("answer_complete must be null for not_assessed")
        elif not isinstance(answer, bool):
            raise EvidenceArtifactValidationError("answer_complete must be boolean when assessed")
        coverage = payload.get("facet_coverage")
        if not isinstance(coverage, list):
            raise EvidenceArtifactValidationError("facet_coverage must be a list")
        for index, item in enumerate(coverage):
            if not isinstance(item, Mapping):
                raise EvidenceArtifactValidationError("facet coverage entries must be objects")
            _require_exact_fields(item, {"facet_id", "status", "supporting_source_refs"}, "facet coverage")
            _require_text(item.get("facet_id"), "facet_coverage.facet_id")
            if item.get("status") not in FACET_STATUSES:
                raise EvidenceArtifactValidationError("facet coverage status is invalid")
            refs = item.get("supporting_source_refs")
            if not isinstance(refs, list):
                raise EvidenceArtifactValidationError("supporting_source_refs must be a list")
            for ref in refs:
                _validate_pointer(ref, f"facet_coverage[{index}].supporting_source_refs")
        for field in ("unique_chunk_count", "unique_section_count"):
            value = payload.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise EvidenceArtifactValidationError(f"{field} must be a non-negative integer")
        if payload.get("intake_context_status") not in {"available", "fixture_only", "not_assessed"}:
            raise EvidenceArtifactValidationError("intake_context_status is invalid")

    elif artifact_type == "analysis_quality_assessment":
        _validate_audit_fields(payload, "RESEARCH_ANALYSIS")
        _validate_pointer(payload.get("retrieval_coverage_ref"), "retrieval_coverage_ref", expected_type="retrieval_coverage_assessment")
        _validate_pointer(payload.get("analysis_artifact_ref"), "analysis_artifact_ref")
        ratio = payload.get("direct_answer_ratio")
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 <= ratio <= 1:
            raise EvidenceArtifactValidationError("direct_answer_ratio must be within [0, 1]")
        if not isinstance(payload.get("subquestion_coverage"), Mapping):
            raise EvidenceArtifactValidationError("subquestion_coverage must be an object")
        if not isinstance(payload.get("source_dominance"), Mapping):
            raise EvidenceArtifactValidationError("source_dominance must be an object")

    else:
        if artifact_type.endswith("_policy"):
            _require_iso8601(payload.get("created_at"), "created_at")
        if payload.get("producer_module") != "RETRIEVAL":
            raise EvidenceArtifactValidationError(f"{artifact_type} producer_module must be RETRIEVAL")


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
            raise EvidenceArtifactValidationError("sequence_index must be an integer")
        _require_hash(self.text_hash, "chunk text_hash")
        if _sha256_text(self.content) != self.text_hash:
            raise EvidenceArtifactValidationError("chunk content no longer matches frozen text_hash")
        if self.chunk_ref.artifact_type != "chunk":
            raise EvidenceArtifactValidationError("chunk_ref must reference a chunk artifact")
        if self.chunk_ref.content_hash != self.text_hash:
            raise EvidenceArtifactValidationError("chunk_ref content_hash differs from frozen chunk text_hash")
        if (self.paragraph_start is None) != (self.paragraph_end is None):
            raise EvidenceArtifactValidationError("paragraph boundaries must be both present or both absent")
        if self.paragraph_start is not None:
            if (
                not isinstance(self.paragraph_start, int)
                or isinstance(self.paragraph_start, bool)
                or not isinstance(self.paragraph_end, int)
                or isinstance(self.paragraph_end, bool)
                or self.paragraph_start < 0
                or self.paragraph_end <= self.paragraph_start
            ):
                raise EvidenceArtifactValidationError("paragraph boundaries are invalid")

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
    if uncovered_status not in {"missing", "not_assessed"}:
        raise EvidenceArtifactValidationError("uncovered_status must be missing or not_assessed")
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
                raise EvidenceArtifactValidationError("facet evidence members do not form contiguous paragraph slices")
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
        if distinct_count >= 1 and total_count >= 2 and support:
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


__all__ = [
    "ArtifactPointer",
    "ChunkSnapshot",
    "FacetCoverageInput",
    "FacetSpec",
    "FrozenArtifactRecord",
    "MemberRole",
    "SourceIdDisposition",
    "EvidenceArtifactValidationError",
    "derive_facet_coverage_inputs",
    "validate_verification_payload",
]
