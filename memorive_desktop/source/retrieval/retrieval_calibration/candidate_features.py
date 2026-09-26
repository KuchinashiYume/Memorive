"""Read-only candidate metadata enrichment for RETRIEVAL-CALIBRATION."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence


STRUCTURE_FIELDS = (
    "section_path",
    "block_type",
    "page_start",
    "page_end",
    "page_family",
    "page_family_modifiers",
    "source_role",
)

Retrieval_PAGE_FAMILIES = frozenset(
    {
        "digital",
        "clean_scan",
        "degraded_scan",
        "multicolumn",
        "role_dense",
        "formula",
        "table_numeric",
        "chart_mixed",
    }
)

_FEATURE_ALIASES = {
    "page_family": ("page_family", "page_family_primary"),
    "page_family_modifiers": ("page_family_modifiers", "modifiers"),
}


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def candidate_identity(candidate: Mapping[str, Any]) -> str:
    explicit = candidate.get("candidate_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    paper_id = candidate.get("paper_id")
    chunk_id = candidate.get("chunk_id")
    if not isinstance(paper_id, str) or not paper_id or not isinstance(chunk_id, str) or not chunk_id:
        raise ValueError("CANDIDATE_IDENTITY_INVALID")
    return f"{paper_id}::{chunk_id}"


def _validate_base(candidate: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(candidate)
    row["candidate_id"] = candidate_identity(candidate)
    for field in ("paper_id", "chunk_id", "text"):
        if not isinstance(row.get(field), str) or not row[field]:
            raise ValueError(f"CANDIDATE_{field.upper()}_INVALID")
    distance = row.get("distance")
    if not isinstance(distance, (int, float)) or isinstance(distance, bool) or not math.isfinite(distance):
        raise ValueError("CANDIDATE_DISTANCE_INVALID")
    row["distance"] = float(distance)
    return row


def _metadata_value(metadata: Mapping[str, Any], field: str) -> Any:
    for name in _FEATURE_ALIASES.get(field, (field,)):
        if name in metadata and metadata[name] is not None:
            value = metadata[name]
            if field == "page_family_modifiers":
                if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                    raise ValueError("PAGE_FAMILY_MODIFIERS_INVALID")
                normalized = sorted({item.strip() for item in value if isinstance(item, str) and item.strip()})
                if len(normalized) != len(value):
                    raise ValueError("PAGE_FAMILY_MODIFIERS_INVALID")
                return normalized
            return value
    return None


def resolve_feature_envelope(
    candidate: Mapping[str, Any],
    *,
    vector_metadata: Mapping[str, Any] | None = None,
    sidecar_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve structure fields without guessing through conflicts.

    Vector metadata has precedence only when the sidecar is absent or agrees.
    A conflict clears the affected value and marks the whole envelope
    ``inconsistent`` so the calibration policy remains neutral.
    """

    base = _validate_base(candidate)
    vector = dict(vector_metadata or {})
    sidecar = dict(sidecar_metadata or {})
    resolved: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    conflicts: list[str] = []
    for field in STRUCTURE_FIELDS:
        vector_value = _metadata_value(vector, field)
        sidecar_value = _metadata_value(sidecar, field)
        if vector_value is not None and sidecar_value is not None and vector_value != sidecar_value:
            resolved[field] = None
            provenance[field] = "inconsistent"
            conflicts.append(field)
        elif vector_value is not None:
            resolved[field] = vector_value
            provenance[field] = "vector"
        elif sidecar_value is not None:
            resolved[field] = sidecar_value
            provenance[field] = "chunk_sidecar"
        else:
            resolved[field] = None
            provenance[field] = "unknown"
    sources = {value for value in provenance.values() if value not in {"unknown", "inconsistent"}}
    if conflicts:
        metadata_status = "inconsistent"
    elif not sources:
        metadata_status = "unknown"
    elif sources == {"vector"}:
        metadata_status = "vector"
    elif sources == {"chunk_sidecar"}:
        metadata_status = "chunk_sidecar"
    else:
        metadata_status = "mixed"
    envelope = {
        "schema_version": "RetrievalCalibrationCandidateFeatureEnvelope-v1",
        "candidate_id": base["candidate_id"],
        "paper_id": base["paper_id"],
        "chunk_id": base["chunk_id"],
        "source_id": base.get("source_id"),
        "source_identity_sha256": canonical_sha256(
            {
                "candidate_id": base["candidate_id"],
                "paper_id": base["paper_id"],
                "chunk_id": base["chunk_id"],
                "source_id": base.get("source_id"),
            }
        ),
        "metadata_status": metadata_status,
        "field_provenance": provenance,
        "conflicting_fields": conflicts,
        **resolved,
    }
    envelope["feature_sha256"] = canonical_sha256(envelope)
    return envelope


def build_reranker_provenance(
    candidate: Mapping[str, Any],
    *,
    retrieval_terminal_sha256: str,
) -> dict[str, Any]:
    """Build the deterministic Retrieval/Installation provenance shown to the reranker.

    Missing metadata remains explicit ``unknown``.  Nothing in this envelope
    changes candidate order or score outside the model invocation.
    """

    if (
        not isinstance(retrieval_terminal_sha256, str)
        or len(retrieval_terminal_sha256) != 64
        or any(character not in "0123456789ABCDEF" for character in retrieval_terminal_sha256)
    ):
        raise ValueError("Retrieval_TERMINAL_SHA256_INVALID")

    envelope = candidate.get("feature_envelope")
    envelope = dict(envelope) if isinstance(envelope, Mapping) else {}
    family_value = candidate.get("page_family", candidate.get("page_family_primary"))
    if family_value is None:
        family_value = envelope.get("page_family")
    page_family = "unknown" if family_value is None else family_value
    if not isinstance(page_family, str) or page_family not in Retrieval_PAGE_FAMILIES | {"unknown"}:
        raise ValueError("PAGE_FAMILY_INVALID")

    modifiers_value = candidate.get("page_family_modifiers", candidate.get("modifiers"))
    if modifiers_value is None:
        modifiers_value = envelope.get("page_family_modifiers") or []
    if not isinstance(modifiers_value, Sequence) or isinstance(modifiers_value, (str, bytes)):
        raise ValueError("PAGE_FAMILY_MODIFIERS_INVALID")
    modifiers = sorted({item.strip() for item in modifiers_value if isinstance(item, str) and item.strip()})
    if len(modifiers) != len(modifiers_value):
        raise ValueError("PAGE_FAMILY_MODIFIERS_INVALID")

    page_start = candidate.get("page_start")
    page_end = candidate.get("page_end")
    if page_start is None:
        page_start = envelope.get("page_start")
    if page_end is None:
        page_end = envelope.get("page_end")
    for value in (page_start, page_end):
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            raise ValueError("PAGE_SPAN_INVALID")
    if page_start is not None and page_end is not None and page_end < page_start:
        raise ValueError("PAGE_SPAN_INVALID")

    source_role_value = candidate.get("source_role")
    if source_role_value is None:
        source_role_value = envelope.get("source_role")
    source_role = "unknown" if source_role_value is None else source_role_value
    if not isinstance(source_role, str) or not source_role.strip():
        raise ValueError("SOURCE_ROLE_INVALID")
    source_role = source_role.strip()

    metadata_status_value = candidate.get("metadata_status")
    if metadata_status_value is None:
        metadata_status_value = envelope.get("metadata_status", "unknown")
    if not isinstance(metadata_status_value, str) or not metadata_status_value.strip():
        raise ValueError("METADATA_STATUS_INVALID")
    metadata_status = metadata_status_value.strip()
    source_provenance = envelope.get("field_provenance")
    source_provenance = dict(source_provenance) if isinstance(source_provenance, Mapping) else {}
    field_provenance: dict[str, str] = {}
    for field, is_unknown in (
        ("page_family", page_family == "unknown"),
        ("source_role", source_role == "unknown"),
    ):
        provenance_value = source_provenance.get(field)
        if provenance_value is None:
            provenance_value = "unknown" if is_unknown else "candidate"
        if not isinstance(provenance_value, str) or not provenance_value.strip():
            raise ValueError("FIELD_PROVENANCE_INVALID")
        field_provenance[field] = provenance_value.strip()

    provenance = {
        "schema_version": "RetrievalCalibrationRerankerDocumentProvenance-v1",
        "retrieval_terminal_sha256": retrieval_terminal_sha256,
        "page_family": page_family,
        "page_family_modifiers": modifiers,
        "page_span": {"start": page_start, "end": page_end},
        "source_role": source_role,
        "metadata_status": metadata_status,
        "field_provenance": field_provenance,
    }
    provenance["provenance_sha256"] = canonical_sha256(provenance)
    return provenance


def enrich_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    vector_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    sidecar_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    vector_by_id = vector_by_id or {}
    sidecar_by_id = sidecar_by_id or {}
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        identity = candidate_identity(candidate)
        if identity in seen:
            raise ValueError("CANDIDATE_ID_DUPLICATE")
        seen.add(identity)
        row = _validate_base(candidate)
        envelope = resolve_feature_envelope(
            candidate,
            vector_metadata=vector_by_id.get(identity, candidate),
            sidecar_metadata=sidecar_by_id.get(identity),
        )
        row.update({field: envelope[field] for field in STRUCTURE_FIELDS})
        row.update(
            {
                "metadata_status": envelope["metadata_status"],
                "feature_envelope": envelope,
            }
        )
        output.append(row)
    return output
