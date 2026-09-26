"""NOVELTY-ASSESSMENT/LITERATURE_DISCOVERY deterministic, pure-offline library relation resolver.

The module consumes only a hash-bound ``LocalLibraryReadSnapshot`` and
hash-bound ServiceContracts-compatible candidate projections.  It performs no filesystem,
network, environment, clock, random, model, embedding, registry, or production
library operation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from datetime import date
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_REVISION = "0.1"
ALGORITHM_REVISION = "discovery.configuration.relation-resolver.0.1"
PRODUCER = "NOVELTY-ASSESSMENT/LITERATURE_DISCOVERY"
SINGLE_WRITER = "LITERATURE_DISCOVERY.CANDIDATE_REGISTRY"

RELATION_TYPES = {
    "EXACT_SAME_WORK",
    "ALTERNATE_VERSION",
    "POSSIBLE_DUPLICATE",
    "DIRECT_CITATION_RELATION",
    "TOPIC_SIMILAR",
    "METHOD_SIMILAR",
    "OBJECT_SIMILAR",
    "BRIDGE_BETWEEN_DIRECTIONS",
    "NEW_TO_LIBRARY",
}

IDENTITY_RELATION_TYPES = {
    "EXACT_SAME_WORK",
    "ALTERNATE_VERSION",
    "POSSIBLE_DUPLICATE",
}

WRITE_METHOD_NAMES = {
    "add", "append", "delete", "insert", "put", "register", "remove",
    "replace", "save", "set", "update", "upsert", "write",
}

PROHIBITED_RANKING_KEYS = {
    "authority_weight", "derived_penalty", "final_ranking_score",
    "ranking_contribution", "rrf_score",
}

ZERO_SIDE_EFFECTS = {
    "network_calls": 0,
    "external_source_calls": 0,
    "external_api_calls": 0,
    "model_calls": 0,
    "tokens": 0,
    "cost_cny": 0,
    "production_library_reads": 0,
    "production_library_writes": 0,
    "production_chroma_queries": 0,
    "production_chroma_writes": 0,
    "artifact_registry_reads": 0,
    "artifact_registry_writes": 0,
    "candidate_card_writes": 0,
    "card_analysis_reads": 0,
    "card_analysis_writes": 0,
    "formal_writes": 0,
}


class RelationResolutionError(RuntimeError):
    """Fail-closed error with a stable reason code and evidence references."""

    def __init__(self, code: str, evidence_refs: Iterable[str] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.evidence_refs = sorted(set(str(value) for value in evidence_refs))

    def as_record(self) -> dict[str, Any]:
        return {"code": self.code, "evidence_refs": self.evidence_refs}


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest().upper()


def seal_object(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("content_hash", None)
    result["content_hash"] = sha256_json(result)
    return result


def _verify_content_hash(value: Mapping[str, Any], evidence_ref: str) -> None:
    body = copy.deepcopy(dict(value))
    recorded = body.pop("content_hash", None)
    if not isinstance(recorded, str) or not re.fullmatch(r"[A-F0-9]{64}", recorded):
        raise RelationResolutionError("CONTENT_HASH_MISSING_OR_INVALID", [evidence_ref])
    if sha256_json(body) != recorded:
        raise RelationResolutionError("CONTENT_HASH_MISMATCH", [evidence_ref])


def _stable_id(prefix: str, payload: Any) -> str:
    return prefix + hashlib.sha256(canonical_json(payload)).hexdigest()[:24]


def _normalized_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"[\w]+", normalized, flags=re.UNICODE))


def _parse_date(value: Any, evidence_ref: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise RelationResolutionError("INVALID_DATE", [evidence_ref])
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RelationResolutionError("INVALID_DATE", [evidence_ref]) from exc


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    evidence_ref: str,
) -> None:
    actual = set(value)
    if actual != expected:
        refs = [evidence_ref, *sorted(actual ^ expected)]
        raise RelationResolutionError("UNKNOWN_OR_MISSING_FIELD", refs)


def _scan_prohibited_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if key_text.casefold() in PROHIBITED_RANKING_KEYS:
                raise RelationResolutionError(
                    "PROHIBITED_AUTHORITY_OR_RANKING_SIGNAL", [f"{path}.{key_text}"]
                )
            _scan_prohibited_keys(item, f"{path}.{key_text}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _scan_prohibited_keys(item, f"{path}[{index}]")


def _validate_vector(vector: Any, expected_dimension: int, evidence_ref: str) -> list[float]:
    if not isinstance(vector, list) or len(vector) != expected_dimension:
        raise RelationResolutionError("VECTOR_DIMENSION_MISMATCH", [evidence_ref])
    values: list[float] = []
    for item in vector:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise RelationResolutionError("VECTOR_VALUE_INVALID", [evidence_ref])
        number = float(item)
        if not math.isfinite(number):
            raise RelationResolutionError("VECTOR_VALUE_INVALID", [evidence_ref])
        values.append(number)
    return values


def _validate_profile(dimension_name: str, profile: Mapping[str, Any]) -> None:
    ref = str(profile.get("profile_id", f"profile:{dimension_name}"))
    _require_exact_keys(
        profile,
        {
            "profile_id", "dimension_name", "metric", "dimension", "threshold",
            "match_rule", "profile_hash",
        },
        ref,
    )
    if profile.get("dimension_name") != dimension_name:
        raise RelationResolutionError("VECTOR_PROFILE_DIMENSION_NAME_MISMATCH", [ref])
    dimension = profile.get("dimension")
    threshold = profile.get("threshold")
    if (
        isinstance(dimension, bool)
        or not isinstance(dimension, int)
        or not 1 <= dimension <= 64
    ):
        raise RelationResolutionError("VECTOR_PROFILE_DIMENSION_INVALID", [ref])
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise RelationResolutionError("VECTOR_PROFILE_THRESHOLD_INVALID", [ref])
    if not math.isfinite(float(threshold)):
        raise RelationResolutionError("VECTOR_PROFILE_THRESHOLD_INVALID", [ref])
    metric = profile.get("metric")
    match_rule = profile.get("match_rule")
    expected_rule = {
        "COSINE_SIMILARITY": "GTE",
        "EUCLIDEAN_DISTANCE": "LTE",
    }.get(str(metric))
    if expected_rule is None or match_rule != expected_rule:
        raise RelationResolutionError("VECTOR_METRIC_DIRECTION_MISMATCH", [ref])
    body = copy.deepcopy(dict(profile))
    recorded = body.pop("profile_hash", None)
    if recorded != sha256_json(body):
        raise RelationResolutionError("VECTOR_PROFILE_HASH_MISMATCH", [ref])


def validate_local_library_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Validate a complete, hash-bound, synthetic query-only snapshot."""

    _scan_prohibited_keys(snapshot)
    _require_exact_keys(
        snapshot,
        {
            "schema_version", "object_type", "snapshot_id", "cutoff",
            "allowed_paper_ids", "coverage", "seen_candidate_ids",
            "vector_profiles", "records", "citation_edges",
            "direction_snapshot", "content_hash",
        },
        str(snapshot.get("snapshot_id", "snapshot")),
    )
    if snapshot.get("schema_version") != "discovery.configuration.local-library-read-snapshot.0.1":
        raise RelationResolutionError("SNAPSHOT_SCHEMA_MISMATCH")
    if snapshot.get("object_type") != "LocalLibraryReadSnapshot":
        raise RelationResolutionError("SNAPSHOT_OBJECT_TYPE_MISMATCH")
    snapshot_id = str(snapshot.get("snapshot_id", ""))
    if not re.fullmatch(r"local_library_snapshot_[a-f0-9]{24}", snapshot_id):
        raise RelationResolutionError("SNAPSHOT_ID_INVALID", [snapshot_id])

    cutoff = snapshot.get("cutoff")
    if not isinstance(cutoff, Mapping) or set(cutoff) != {"publication_date", "observed_at"}:
        raise RelationResolutionError("SNAPSHOT_CUTOFF_INVALID", [snapshot_id])
    _parse_date(cutoff.get("publication_date"), snapshot_id)
    observed_at = cutoff.get("observed_at")
    if not isinstance(observed_at, str) or "T" not in observed_at:
        raise RelationResolutionError("SNAPSHOT_OBSERVED_AT_INVALID", [snapshot_id])

    allowed = snapshot.get("allowed_paper_ids")
    if not isinstance(allowed, list) or not allowed or len(allowed) != len(set(allowed)):
        raise RelationResolutionError("ALLOWED_PAPER_SCOPE_INVALID", [snapshot_id])
    if allowed != sorted(allowed):
        raise RelationResolutionError("ALLOWED_PAPER_SCOPE_NOT_CANONICAL", [snapshot_id])

    coverage = snapshot.get("coverage")
    coverage_keys = {
        "library_identity_complete", "first_seen_complete", "citation_complete",
        "direction_complete", "vector_dimensions_complete",
    }
    if not isinstance(coverage, Mapping) or set(coverage) != coverage_keys:
        raise RelationResolutionError("COVERAGE_SCHEMA_INVALID", [snapshot_id])
    for key in coverage_keys - {"vector_dimensions_complete"}:
        if not isinstance(coverage[key], bool):
            raise RelationResolutionError("COVERAGE_VALUE_INVALID", [snapshot_id, key])
    vector_coverage = coverage["vector_dimensions_complete"]
    if (
        not isinstance(vector_coverage, list)
        or len(vector_coverage) != len(set(vector_coverage))
        or any(item not in {"topic", "method", "object"} for item in vector_coverage)
    ):
        raise RelationResolutionError("VECTOR_COVERAGE_INVALID", [snapshot_id])

    profiles = snapshot.get("vector_profiles")
    if not isinstance(profiles, Mapping) or set(profiles) != {"topic", "method", "object"}:
        raise RelationResolutionError("VECTOR_PROFILES_INVALID", [snapshot_id])
    for dimension_name in ("topic", "method", "object"):
        profile = profiles[dimension_name]
        if not isinstance(profile, Mapping):
            raise RelationResolutionError("VECTOR_PROFILE_INVALID", [dimension_name])
        _validate_profile(dimension_name, profile)

    records = snapshot.get("records")
    if not isinstance(records, list) or not records:
        raise RelationResolutionError("LOCAL_RECORDS_EMPTY", [snapshot_id])
    paper_ids: list[str] = []
    direction_ids: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise RelationResolutionError("LOCAL_RECORD_INVALID", [snapshot_id])
        paper_id = str(record.get("paper_id", ""))
        _require_exact_keys(
            record,
            {
                "paper_id", "title", "active", "identity_status", "work_cluster_id",
                "manifestation_ids", "identifiers", "publication_date", "directions",
                "fields", "vectors", "vector_profile_refs", "version_links", "content_hash",
            },
            paper_id,
        )
        _verify_content_hash(record, paper_id)
        if not re.fullmatch(r"PAPER:[A-Z0-9_-]+", paper_id):
            raise RelationResolutionError("PAPER_ID_INVALID", [paper_id])
        if record.get("active") is not True:
            raise RelationResolutionError("INACTIVE_LOCAL_RECORD_REJECTED", [paper_id])
        if record.get("identity_status") not in {"VERIFIED", "CONFLICT", "UNRESOLVED"}:
            raise RelationResolutionError("LOCAL_IDENTITY_STATUS_INVALID", [paper_id])
        if not isinstance(record.get("title"), str) or not record["title"].strip():
            raise RelationResolutionError("LOCAL_TITLE_INVALID", [paper_id])
        _parse_date(record.get("publication_date"), paper_id)
        manifestations = record.get("manifestation_ids")
        identifiers = record.get("identifiers")
        if not isinstance(manifestations, list) or not manifestations or len(manifestations) != len(set(manifestations)):
            raise RelationResolutionError("LOCAL_MANIFESTATIONS_INVALID", [paper_id])
        if not isinstance(identifiers, list) or len(identifiers) != len(set(identifiers)):
            raise RelationResolutionError("LOCAL_IDENTIFIERS_INVALID", [paper_id])
        fields = record.get("fields")
        vectors = record.get("vectors")
        refs = record.get("vector_profile_refs")
        if not isinstance(fields, Mapping) or set(fields) != {"topic", "method", "object", "abstract"}:
            raise RelationResolutionError("LOCAL_FIELDS_INVALID", [paper_id])
        if not isinstance(vectors, Mapping) or set(vectors) != {"topic", "method", "object"}:
            raise RelationResolutionError("LOCAL_VECTORS_INVALID", [paper_id])
        if not isinstance(refs, Mapping) or set(refs) != {"topic", "method", "object"}:
            raise RelationResolutionError("LOCAL_VECTOR_PROFILE_REFS_INVALID", [paper_id])
        for dimension_name in ("topic", "method", "object"):
            profile = profiles[dimension_name]
            if refs[dimension_name] != profile["profile_id"]:
                raise RelationResolutionError("VECTOR_PROFILE_REF_MISMATCH", [paper_id, dimension_name])
            _validate_vector(vectors[dimension_name], profile["dimension"], f"{paper_id}:{dimension_name}")
        record_directions = record.get("directions")
        if not isinstance(record_directions, list) or len(record_directions) != len(set(record_directions)):
            raise RelationResolutionError("LOCAL_DIRECTIONS_INVALID", [paper_id])
        direction_ids.update(str(item) for item in record_directions)
        version_links = record.get("version_links")
        if not isinstance(version_links, list):
            raise RelationResolutionError("VERSION_LINKS_INVALID", [paper_id])
        for link in version_links:
            if not isinstance(link, Mapping) or set(link) != {
                "candidate_manifestation_id", "local_manifestation_id", "evidence_ref"
            }:
                raise RelationResolutionError("VERSION_LINK_INVALID", [paper_id])
            if any(
                not isinstance(link[field], str) or not link[field].strip()
                for field in (
                    "candidate_manifestation_id", "local_manifestation_id", "evidence_ref"
                )
            ):
                raise RelationResolutionError("VERSION_LINK_INVALID", [paper_id])
            if link["local_manifestation_id"] not in manifestations:
                raise RelationResolutionError("VERSION_LINK_LOCAL_MANIFESTATION_MISMATCH", [paper_id])
        paper_ids.append(paper_id)
    if len(paper_ids) != len(set(paper_ids)):
        raise RelationResolutionError("DUPLICATE_LOCAL_PAPER_ID", paper_ids)
    if not set(allowed).issubset(set(paper_ids)):
        raise RelationResolutionError("ALLOWED_PAPER_OUT_OF_SNAPSHOT", sorted(set(allowed) - set(paper_ids)))

    seen = snapshot.get("seen_candidate_ids")
    if not isinstance(seen, list) or len(seen) != len(set(seen)):
        raise RelationResolutionError("FIRST_SEEN_SNAPSHOT_INVALID", [snapshot_id])

    direction_snapshot = snapshot.get("direction_snapshot")
    if not isinstance(direction_snapshot, Mapping) or set(direction_snapshot) != {"directions", "content_hash"}:
        raise RelationResolutionError("DIRECTION_SNAPSHOT_INVALID", [snapshot_id])
    _verify_content_hash(direction_snapshot, f"{snapshot_id}:directions")
    direction_items = direction_snapshot.get("directions")
    if not isinstance(direction_items, list):
        raise RelationResolutionError("DIRECTION_ITEMS_INVALID", [snapshot_id])
    declared_direction_ids: list[str] = []
    for item in direction_items:
        if not isinstance(item, Mapping) or set(item) != {"direction_id", "label", "evidence_scope"}:
            raise RelationResolutionError("DIRECTION_ITEM_INVALID", [snapshot_id])
        if item.get("evidence_scope") not in {"METADATA_ONLY", "ABSTRACT_LEVEL"}:
            raise RelationResolutionError("DIRECTION_SCOPE_INVALID", [str(item.get("direction_id"))])
        declared_direction_ids.append(str(item.get("direction_id")))
    if len(declared_direction_ids) != len(set(declared_direction_ids)):
        raise RelationResolutionError("DUPLICATE_DIRECTION_ID", declared_direction_ids)
    if not direction_ids.issubset(set(declared_direction_ids)):
        raise RelationResolutionError("LOCAL_DIRECTION_OUT_OF_SNAPSHOT", sorted(direction_ids - set(declared_direction_ids)))

    edges = snapshot.get("citation_edges")
    if not isinstance(edges, list):
        raise RelationResolutionError("CITATION_EDGES_INVALID", [snapshot_id])
    edge_ids: list[str] = []
    for edge in edges:
        if not isinstance(edge, Mapping):
            raise RelationResolutionError("CITATION_EDGE_INVALID", [snapshot_id])
        edge_id = str(edge.get("edge_id", ""))
        _require_exact_keys(
            edge,
            {
                "edge_id", "source_ref", "target_ref", "source_identifier",
                "target_identifier", "provenance_ref", "content_hash",
            },
            edge_id,
        )
        _verify_content_hash(edge, edge_id)
        if not edge.get("provenance_ref"):
            raise RelationResolutionError("CITATION_PROVENANCE_MISSING", [edge_id])
        edge_ids.append(edge_id)
    if len(edge_ids) != len(set(edge_ids)):
        raise RelationResolutionError("DUPLICATE_CITATION_EDGE_ID", edge_ids)

    _verify_content_hash(snapshot, snapshot_id)


def validate_candidate(candidate: Mapping[str, Any], profiles: Mapping[str, Any]) -> None:
    _scan_prohibited_keys(candidate)
    candidate_id = str(candidate.get("candidate_id", ""))
    _require_exact_keys(
        candidate,
        {
            "schema_version", "object_type", "candidate_id", "identity_status",
            "access_status", "decision_status", "candidate_card_status",
            "promotion_status", "configuration_input_eligible", "work_cluster_id",
            "manifestation_ids", "identifiers", "title", "publication_date",
            "fields", "query_vectors", "vector_profile_refs", "citation_claims",
            "content_hash",
        },
        candidate_id,
    )
    if candidate.get("schema_version") != "discovery.configuration.candidate-input.0.1":
        raise RelationResolutionError("CANDIDATE_SCHEMA_MISMATCH", [candidate_id])
    if candidate.get("object_type") != "CandidateRecord":
        raise RelationResolutionError("CANDIDATE_OBJECT_TYPE_MISMATCH", [candidate_id])
    if not re.fullmatch(r"CANDIDATE:[A-Z0-9_-]+", candidate_id):
        raise RelationResolutionError("CANDIDATE_ID_INVALID", [candidate_id])
    status = candidate.get("identity_status")
    if status not in {"VERIFIED", "CONFLICT", "UNRESOLVED"}:
        raise RelationResolutionError("CANDIDATE_IDENTITY_STATUS_INVALID", [candidate_id])
    if candidate.get("configuration_input_eligible") is not (status == "VERIFIED"):
        raise RelationResolutionError("Configuration_ELIGIBILITY_STATUS_MISMATCH", [candidate_id])
    manifestations = candidate.get("manifestation_ids")
    identifiers = candidate.get("identifiers")
    if not isinstance(manifestations, list) or not manifestations or len(manifestations) != len(set(manifestations)):
        raise RelationResolutionError("CANDIDATE_MANIFESTATIONS_INVALID", [candidate_id])
    if not isinstance(identifiers, list) or len(identifiers) != len(set(identifiers)):
        raise RelationResolutionError("CANDIDATE_IDENTIFIERS_INVALID", [candidate_id])
    if not isinstance(candidate.get("title"), str) or not candidate["title"].strip():
        raise RelationResolutionError("CANDIDATE_TITLE_INVALID", [candidate_id])
    if candidate.get("publication_date") is not None:
        _parse_date(candidate.get("publication_date"), candidate_id)
    fields = candidate.get("fields")
    vectors = candidate.get("query_vectors")
    refs = candidate.get("vector_profile_refs")
    if not isinstance(fields, Mapping) or set(fields) != {"topic", "method", "object", "abstract"}:
        raise RelationResolutionError("CANDIDATE_FIELDS_INVALID", [candidate_id])
    if not isinstance(vectors, Mapping) or set(vectors) != {"topic", "method", "object"}:
        raise RelationResolutionError("CANDIDATE_VECTORS_INVALID", [candidate_id])
    if not isinstance(refs, Mapping) or set(refs) != {"topic", "method", "object"}:
        raise RelationResolutionError("CANDIDATE_VECTOR_PROFILE_REFS_INVALID", [candidate_id])
    for dimension_name in ("topic", "method", "object"):
        profile = profiles[dimension_name]
        if refs[dimension_name] != profile["profile_id"]:
            raise RelationResolutionError("CANDIDATE_VECTOR_PROFILE_REF_MISMATCH", [candidate_id, dimension_name])
        _validate_vector(vectors[dimension_name], profile["dimension"], f"{candidate_id}:{dimension_name}")
    claims = candidate.get("citation_claims")
    if not isinstance(claims, list) or any(not isinstance(value, str) for value in claims):
        raise RelationResolutionError("CANDIDATE_CITATION_CLAIMS_INVALID", [candidate_id])
    _verify_content_hash(candidate, candidate_id)


class LocalLibraryQueryAdapter:
    """In-memory query-only adapter with explicit paper scope."""

    def __init__(self, snapshot: Mapping[str, Any]) -> None:
        validate_local_library_snapshot(snapshot)
        self._snapshot = copy.deepcopy(dict(snapshot))
        self._records = {
            str(record["paper_id"]): copy.deepcopy(dict(record))
            for record in snapshot["records"]
        }
        self.read_count = 0
        self.out_of_scope_reads = 0
        self.write_attempts = 0

    def query(self, allowed_paper_ids: Sequence[str]) -> list[dict[str, Any]]:
        if (
            not isinstance(allowed_paper_ids, Sequence)
            or isinstance(allowed_paper_ids, (str, bytes, bytearray))
        ):
            raise RelationResolutionError("QUERY_SCOPE_INVALID")
        scope = [str(value) for value in allowed_paper_ids]
        if not scope or len(scope) != len(set(scope)):
            raise RelationResolutionError("QUERY_SCOPE_INVALID", scope)
        if scope != sorted(scope):
            raise RelationResolutionError("QUERY_SCOPE_NOT_CANONICAL", scope)
        frozen_scope = set(self._snapshot["allowed_paper_ids"])
        requested = set(scope)
        if not requested.issubset(frozen_scope):
            self.out_of_scope_reads += len(requested - frozen_scope)
            raise RelationResolutionError("QUERY_SCOPE_OUT_OF_BOUNDS", sorted(requested - frozen_scope))
        self.read_count += len(scope)
        return [copy.deepcopy(self._records[paper_id]) for paper_id in scope]

    def __getattr__(self, name: str) -> Any:
        if name.casefold() in WRITE_METHOD_NAMES:
            self.write_attempts += 1
            raise RelationResolutionError("QUERY_ONLY_WRITE_METHOD_FORBIDDEN", [name])
        raise AttributeError(name)


def _measurement(
    dimension_name: str,
    candidate_vector: Sequence[float],
    local_vector: Sequence[float],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    metric = profile["metric"]
    if metric == "COSINE_SIMILARITY":
        left_norm = math.sqrt(sum(value * value for value in candidate_vector))
        right_norm = math.sqrt(sum(value * value for value in local_vector))
        if left_norm == 0.0 or right_norm == 0.0:
            value = 0.0
        else:
            value = sum(
                left * right for left, right in zip(candidate_vector, local_vector)
            ) / (left_norm * right_norm)
    elif metric == "EUCLIDEAN_DISTANCE":
        value = math.sqrt(sum(
            (left - right) ** 2 for left, right in zip(candidate_vector, local_vector)
        ))
    else:  # guarded by profile validation
        raise RelationResolutionError("UNSUPPORTED_VECTOR_METRIC", [str(metric)])
    value = round(value, 12)
    threshold = float(profile["threshold"])
    match = value >= threshold if profile["match_rule"] == "GTE" else value <= threshold
    return {
        "dimension": dimension_name,
        "profile_id": profile["profile_id"],
        "metric": metric,
        "value": value,
        "threshold": threshold,
        "match_rule": profile["match_rule"],
        "match": match,
    }


def _different_dimensions(candidate: Mapping[str, Any], record: Mapping[str, Any]) -> list[str]:
    different = [
        dimension_name
        for dimension_name in ("topic", "method", "object")
        if _normalized_text(candidate["fields"][dimension_name])
        != _normalized_text(record["fields"][dimension_name])
    ]
    if candidate.get("publication_date") != record.get("publication_date"):
        different.append("publication_date")
    if not different:
        different.append("source_record")
    return sorted(set(different))


def _make_relation(
    candidate: Mapping[str, Any],
    record: Mapping[str, Any],
    relation_type: str,
    *,
    evidence_method: str,
    evidence_refs: Iterable[str],
    similar_dimensions: Iterable[str],
    different_dimensions: Iterable[str],
    evidence_level: str,
    evidence_scope: str,
    profile_refs: Iterable[str] = (),
    measurements: Iterable[Mapping[str, Any]] = (),
    reason_codes: Iterable[str],
) -> dict[str, Any]:
    if relation_type not in RELATION_TYPES:
        raise RelationResolutionError("RELATION_TYPE_INVALID", [relation_type])
    payload = {
        "candidate_id": candidate["candidate_id"],
        "local_paper_id": record["paper_id"],
        "relation_type": relation_type,
        "evidence_refs": sorted(set(str(value) for value in evidence_refs)),
    }
    relation = {
        "schema_version": "discovery.configuration.library-relation.0.1",
        "object_type": "LibraryRelation",
        "relation_id": _stable_id("library_relation_", payload),
        "candidate_id": candidate["candidate_id"],
        "local_paper_id": record["paper_id"],
        "local_title": record["title"],
        "relation_type": relation_type,
        "evidence_method": evidence_method,
        "evidence_refs": sorted(set(str(value) for value in evidence_refs)),
        "similar_dimensions": sorted(set(str(value) for value in similar_dimensions)),
        "different_dimensions": sorted(set(str(value) for value in different_dimensions)),
        "evidence_level": evidence_level,
        "evidence_scope": evidence_scope,
        "algorithm_revision": ALGORITHM_REVISION,
        "profile_refs": sorted(set(str(value) for value in profile_refs)),
        "measurements": sorted(
            (copy.deepcopy(dict(value)) for value in measurements),
            key=lambda value: (value["dimension"], value["profile_id"]),
        ),
        "reason_codes": sorted(set(str(value) for value in reason_codes)),
    }
    if not relation["evidence_refs"] or not relation["similar_dimensions"]:
        raise RelationResolutionError("RELATION_EVIDENCE_INCOMPLETE", [relation["relation_id"]])
    if not relation["different_dimensions"] or not relation["reason_codes"]:
        raise RelationResolutionError("RELATION_EXPLANATION_INCOMPLETE", [relation["relation_id"]])
    return seal_object(relation)


def _pair_analysis(
    candidate: Mapping[str, Any],
    record: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    relations: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    different = _different_dimensions(candidate, record)
    candidate_id = candidate["candidate_id"]
    paper_id = record["paper_id"]
    candidate_verified = candidate["identity_status"] == "VERIFIED"
    local_verified = record["identity_status"] == "VERIFIED"
    same_cluster = candidate["work_cluster_id"] == record["work_cluster_id"]
    manifestation_overlap = bool(
        set(candidate["manifestation_ids"]) & set(record["manifestation_ids"])
    )
    identifier_overlap = sorted(set(candidate["identifiers"]) & set(record["identifiers"]))
    version_evidence = sorted(
        str(link["evidence_ref"])
        for link in record["version_links"]
        if link["candidate_manifestation_id"] in candidate["manifestation_ids"]
        and link["local_manifestation_id"] in record["manifestation_ids"]
    )

    if candidate_verified and local_verified:
        if (same_cluster and manifestation_overlap) or identifier_overlap:
            refs = [candidate["content_hash"], record["content_hash"], *identifier_overlap]
            relations.append(_make_relation(
                candidate,
                record,
                "EXACT_SAME_WORK",
                evidence_method="ServiceContracts_VERIFIED_IDENTITY_OR_EXACT_IDENTIFIER",
                evidence_refs=refs,
                similar_dimensions=["work_identity"],
                different_dimensions=different,
                evidence_level="EXACT_IDENTITY",
                evidence_scope="IDENTITY_ONLY",
                reason_codes=["VERIFIED_IDENTITY_MATCH"],
            ))
        elif same_cluster and version_evidence:
            relations.append(_make_relation(
                candidate,
                record,
                "ALTERNATE_VERSION",
                evidence_method="ServiceContracts_WORK_CLUSTER_WITH_EXPLICIT_VERSION_EDGE",
                evidence_refs=[candidate["content_hash"], record["content_hash"], *version_evidence],
                similar_dimensions=["work_identity"],
                different_dimensions=["manifestation", *different],
                evidence_level="EXPLICIT_VERSION",
                evidence_scope="IDENTITY_ONLY",
                reason_codes=["SAME_WORK_DIFFERENT_MANIFESTATION"],
            ))
        elif same_cluster:
            unknowns.append({
                "reason_code": "SAME_WORK_CLUSTER_WITHOUT_VERSION_EVIDENCE",
                "evidence_refs": sorted([candidate_id, paper_id]),
            })
        elif _normalized_text(candidate["title"]) == _normalized_text(record["title"]):
            relations.append(_make_relation(
                candidate,
                record,
                "POSSIBLE_DUPLICATE",
                evidence_method="TITLE_ONLY_REVIEW_SIGNAL",
                evidence_refs=[candidate["content_hash"], record["content_hash"]],
                similar_dimensions=["title"],
                different_dimensions=["work_identity", *different],
                evidence_level="FIELD_VECTOR",
                evidence_scope="METADATA_ONLY",
                reason_codes=["TITLE_MATCH_NOT_IDENTITY_PROOF"],
            ))
    else:
        unknowns.append({
            "reason_code": "IDENTITY_NOT_VERIFIED",
            "evidence_refs": sorted([candidate_id, paper_id]),
        })

    for edge in snapshot["citation_edges"]:
        endpoints = {(edge["source_ref"], edge["target_ref"])}
        if (candidate_id, paper_id) in endpoints or (paper_id, candidate_id) in endpoints:
            if edge["source_ref"] == candidate_id:
                source_identifiers = set(candidate["identifiers"])
                target_identifiers = set(record["identifiers"])
            else:
                source_identifiers = set(record["identifiers"])
                target_identifiers = set(candidate["identifiers"])
            if (
                edge["source_identifier"] not in source_identifiers
                or edge["target_identifier"] not in target_identifiers
            ):
                raise RelationResolutionError(
                    "CITATION_EDGE_INVALID", [edge["edge_id"], candidate_id, paper_id]
                )
            direction = (
                "CANDIDATE_CITES_LOCAL"
                if edge["source_ref"] == candidate_id
                else "LOCAL_CITES_CANDIDATE"
            )
            relations.append(_make_relation(
                candidate,
                record,
                "DIRECT_CITATION_RELATION",
                evidence_method="FROZEN_EXPLICIT_CITATION_EDGE",
                evidence_refs=[edge["edge_id"], edge["provenance_ref"], edge["content_hash"]],
                similar_dimensions=["citation_graph"],
                different_dimensions=different,
                evidence_level="EXPLICIT_EDGE",
                evidence_scope="FROZEN_CITATION_GRAPH",
                reason_codes=[direction],
            ))

    measurements: dict[str, dict[str, Any]] = {}
    for dimension_name in ("topic", "method", "object"):
        profile = snapshot["vector_profiles"][dimension_name]
        item = _measurement(
            dimension_name,
            candidate["query_vectors"][dimension_name],
            record["vectors"][dimension_name],
            profile,
        )
        measurements[dimension_name] = item
        if item["match"]:
            relation_type = {
                "topic": "TOPIC_SIMILAR",
                "method": "METHOD_SIMILAR",
                "object": "OBJECT_SIMILAR",
            }[dimension_name]
            public_measurement = {key: item[key] for key in item if key != "match"}
            relations.append(_make_relation(
                candidate,
                record,
                relation_type,
                evidence_method="FROZEN_PRECOMPUTED_VECTOR_PROFILE",
                evidence_refs=[
                    snapshot["snapshot_id"], profile["profile_id"], profile["profile_hash"],
                    candidate["content_hash"], record["content_hash"],
                ],
                similar_dimensions=[dimension_name],
                different_dimensions=different,
                evidence_level="FIELD_VECTOR",
                evidence_scope="ABSTRACT_LEVEL",
                profile_refs=[profile["profile_id"]],
                measurements=[public_measurement],
                reason_codes=["PROFILE_THRESHOLD_MET"],
            ))
    return relations, measurements, unknowns


def _axis(
    status: str,
    coverage_complete: bool,
    cutoff_or_reference: str | None,
    reason_codes: Iterable[str],
    evidence_refs: Iterable[str],
    evidence_scope: str,
) -> dict[str, Any]:
    if status not in {"TRUE", "FALSE", "UNKNOWN"}:
        raise RelationResolutionError("NOVELTY_STATUS_INVALID", [status])
    return {
        "status": status,
        "coverage_complete": coverage_complete,
        "cutoff_or_reference": cutoff_or_reference,
        "reason_codes": sorted(set(reason_codes)),
        "evidence_refs": sorted(set(evidence_refs)),
        "evidence_scope": evidence_scope,
    }


def _build_novelty(
    candidate: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    relations: Sequence[Mapping[str, Any]],
    direction_match_ids: set[str],
    unknowns: Sequence[Mapping[str, Any]],
    scope_complete: bool,
) -> dict[str, Any]:
    candidate_id = candidate["candidate_id"]
    snapshot_id = snapshot["snapshot_id"]
    coverage = snapshot["coverage"]
    cutoff_value = snapshot["cutoff"]["publication_date"]

    if candidate.get("publication_date") is None:
        publication = _axis(
            "UNKNOWN", False, cutoff_value,
            ["PUBLICATION_DATE_MISSING"], [candidate_id, snapshot_id], "INCOMPLETE_SCOPE",
        )
    else:
        publication_is_new = _parse_date(candidate["publication_date"], candidate_id) >= _parse_date(cutoff_value, snapshot_id)
        publication = _axis(
            "TRUE" if publication_is_new else "FALSE",
            True,
            cutoff_value,
            ["PUBLICATION_DATE_AT_OR_AFTER_CUTOFF" if publication_is_new else "PUBLICATION_DATE_BEFORE_CUTOFF"],
            [candidate["content_hash"], snapshot["content_hash"]],
            "METADATA_ONLY",
        )

    if not coverage["first_seen_complete"]:
        first_seen = _axis(
            "UNKNOWN", False, snapshot_id,
            ["FIRST_SEEN_COVERAGE_INCOMPLETE"], [snapshot_id], "INCOMPLETE_SCOPE",
        )
    elif candidate_id in snapshot["seen_candidate_ids"]:
        first_seen = _axis(
            "FALSE", True, snapshot_id,
            ["CANDIDATE_PREVIOUSLY_SEEN"], [candidate_id, snapshot["content_hash"]], "COMPLETE_LIBRARY_SCOPE",
        )
    else:
        first_seen = _axis(
            "TRUE", True, snapshot_id,
            ["CANDIDATE_NOT_IN_COMPLETE_FIRST_SEEN_SNAPSHOT"],
            [candidate_id, snapshot["content_hash"]], "COMPLETE_LIBRARY_SCOPE",
        )

    relation_types = {str(item["relation_type"]) for item in relations}
    identity_unknowns = [
        item
        for item in unknowns
        if item["reason_code"] in {
            "SAME_WORK_CLUSTER_WITHOUT_VERSION_EVIDENCE",
            "IDENTITY_NOT_VERIFIED",
        }
    ]
    if candidate["identity_status"] != "VERIFIED":
        library = _axis(
            "UNKNOWN", False, snapshot_id,
            ["CANDIDATE_IDENTITY_NOT_VERIFIED"], [candidate_id], "INCOMPLETE_SCOPE",
        )
    elif relation_types & {"EXACT_SAME_WORK", "ALTERNATE_VERSION"}:
        complete_scope = bool(coverage["library_identity_complete"] and scope_complete)
        library = _axis(
            "FALSE", complete_scope, snapshot_id,
            ["LOCAL_EXACT_OR_VERSION_RELATION_EXISTS"],
            [item["relation_id"] for item in relations if item["relation_type"] in {"EXACT_SAME_WORK", "ALTERNATE_VERSION"}],
            "COMPLETE_LIBRARY_SCOPE" if complete_scope else "INCOMPLETE_SCOPE",
        )
    elif not coverage["library_identity_complete"]:
        library = _axis(
            "UNKNOWN", False, snapshot_id,
            ["LOCAL_IDENTITY_COVERAGE_INCOMPLETE"], [snapshot_id], "INCOMPLETE_SCOPE",
        )
    elif "POSSIBLE_DUPLICATE" in relation_types:
        library = _axis(
            "UNKNOWN", True, snapshot_id,
            ["POSSIBLE_DUPLICATE_REQUIRES_REVIEW"],
            [item["relation_id"] for item in relations if item["relation_type"] == "POSSIBLE_DUPLICATE"],
            "COMPLETE_LIBRARY_SCOPE",
        )
    elif identity_unknowns:
        library = _axis(
            "UNKNOWN", False, snapshot_id,
            [item["reason_code"] for item in identity_unknowns],
            [ref for item in identity_unknowns for ref in item["evidence_refs"]],
            "INCOMPLETE_SCOPE",
        )
    elif not scope_complete:
        library = _axis(
            "UNKNOWN", False, snapshot_id,
            ["LOCAL_IDENTITY_COVERAGE_INCOMPLETE"], [snapshot_id], "INCOMPLETE_SCOPE",
        )
    else:
        library = _axis(
            "TRUE", True, snapshot_id,
            ["NO_IDENTITY_RELATION_IN_COMPLETE_SCOPE"],
            [snapshot["content_hash"], candidate["content_hash"]],
            "COMPLETE_LIBRARY_SCOPE",
        )

    if not coverage["direction_complete"]:
        direction = _axis(
            "UNKNOWN", False, snapshot_id,
            ["DIRECTION_COVERAGE_INCOMPLETE"], [snapshot_id], "INCOMPLETE_SCOPE",
        )
    elif len(direction_match_ids) >= 2:
        direction = _axis(
            "TRUE", True, snapshot_id,
            ["QUALIFIED_CROSS_DIRECTION_BRIDGE"], sorted(direction_match_ids), "ABSTRACT_LEVEL_ESTIMATE",
        )
    elif len(direction_match_ids) == 1:
        direction = _axis(
            "FALSE", True, snapshot_id,
            ["ALIGNS_WITH_ONE_EXISTING_DIRECTION"], sorted(direction_match_ids), "ABSTRACT_LEVEL_ESTIMATE",
        )
    elif not scope_complete:
        direction = _axis(
            "UNKNOWN", False, snapshot_id,
            ["DIRECTION_COVERAGE_INCOMPLETE"], [snapshot_id], "INCOMPLETE_SCOPE",
        )
    else:
        direction = _axis(
            "TRUE", True, snapshot_id,
            ["NO_EXISTING_DIRECTION_MATCH_IN_COMPLETE_SNAPSHOT"],
            [snapshot["direction_snapshot"]["content_hash"], candidate["content_hash"]],
            "ABSTRACT_LEVEL_ESTIMATE",
        )

    novelty = {
        "schema_version": "discovery.configuration.novelty-evidence.0.1",
        "object_type": "NoveltyEvidence",
        "novelty_id": _stable_id("novelty_evidence_", [candidate_id, snapshot_id]),
        "candidate_id": candidate_id,
        "axes": {
            "publication_new": publication,
            "first_seen_new": first_seen,
            "library_new": library,
            "direction_new": direction,
        },
    }
    return seal_object(novelty)


def resolve_candidate_comparison(
    candidate: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    allowed_paper_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Resolve one candidate against an explicit frozen snapshot scope."""

    candidate_copy = copy.deepcopy(dict(candidate))
    snapshot_copy = copy.deepcopy(dict(snapshot))
    validate_local_library_snapshot(snapshot_copy)
    validate_candidate(candidate_copy, snapshot_copy["vector_profiles"])

    scope = (
        sorted(str(value) for value in allowed_paper_ids)
        if allowed_paper_ids is not None
        else list(snapshot_copy["allowed_paper_ids"])
    )
    adapter = LocalLibraryQueryAdapter(snapshot_copy)
    records = adapter.query(scope)
    relations: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    direction_support: dict[str, set[str]] = {}

    for record in records:
        pair_relations, measurements, pair_unknowns = _pair_analysis(candidate_copy, record, snapshot_copy)
        relations.extend(pair_relations)
        unknowns.extend(pair_unknowns)
        matched_dimensions = {
            dimension_name for dimension_name, item in measurements.items() if item["match"]
        }
        if matched_dimensions:
            for direction_id in record["directions"]:
                direction_support.setdefault(direction_id, set()).update(matched_dimensions)

    direction_match_ids = {direction_id for direction_id, dimensions in direction_support.items() if dimensions}
    if len(direction_match_ids) >= 2:
        supporting_records = [
            record for record in records
            if set(record["directions"]) & direction_match_ids
        ]
        anchor = sorted(supporting_records, key=lambda item: item["paper_id"])[0]
        supporting_dimensions = sorted({
            dimension_name
            for direction_id in direction_match_ids
            for dimension_name in direction_support[direction_id]
        })
        relations.append(_make_relation(
            candidate_copy,
            anchor,
            "BRIDGE_BETWEEN_DIRECTIONS",
            evidence_method="MULTI_DIRECTION_FROZEN_FIELD_VECTOR",
            evidence_refs=[
                snapshot_copy["direction_snapshot"]["content_hash"],
                *sorted(direction_match_ids),
                *sorted(record["content_hash"] for record in supporting_records),
            ],
            similar_dimensions=supporting_dimensions,
            different_dimensions=["direction_membership"],
            evidence_level="ABSTRACT_LEVEL_ESTIMATE",
            evidence_scope="ABSTRACT_LEVEL",
            profile_refs=[
                snapshot_copy["vector_profiles"][name]["profile_id"]
                for name in supporting_dimensions
            ],
            reason_codes=["AT_LEAST_TWO_EXPLICIT_DIRECTIONS_WITH_QUALIFIED_EVIDENCE"],
        ))

    if candidate_copy["citation_claims"]:
        cited_paper_ids = {
            relation["local_paper_id"]
            for relation in relations
            if relation["relation_type"] == "DIRECT_CITATION_RELATION"
        }
        if not cited_paper_ids:
            unknowns.append({
                "reason_code": "UNVERIFIED_CITATION_CLAIM_IGNORED",
                "evidence_refs": sorted([candidate_copy["candidate_id"], *candidate_copy["citation_claims"]]),
            })

    novelty = _build_novelty(
        candidate_copy,
        snapshot_copy,
        relations,
        direction_match_ids,
        unknowns,
        scope == snapshot_copy["allowed_paper_ids"],
    )
    if novelty["axes"]["library_new"]["status"] == "TRUE":
        anchor = sorted(records, key=lambda item: item["paper_id"])[0]
        relations.append(_make_relation(
            candidate_copy,
            anchor,
            "NEW_TO_LIBRARY",
            evidence_method="COMPLETE_SCOPE_IDENTITY_ABSENCE",
            evidence_refs=[snapshot_copy["content_hash"], candidate_copy["content_hash"]],
            similar_dimensions=["library_scope"],
            different_dimensions=["work_identity"],
            evidence_level="SCOPE_COVERAGE",
            evidence_scope="COMPLETE_LIBRARY_SCOPE",
            reason_codes=["LIBRARY_NEW_TRUE_ONLY"],
        ))

    relation_keys: set[tuple[str, str]] = set()
    for relation in relations:
        key = (relation["local_paper_id"], relation["relation_type"])
        if key in relation_keys:
            raise RelationResolutionError("DUPLICATE_PAIR_RELATION_TYPE", [*key])
        relation_keys.add(key)
    for paper_id in scope:
        identity_count = sum(
            1 for relation in relations
            if relation["local_paper_id"] == paper_id
            and relation["relation_type"] in IDENTITY_RELATION_TYPES
        )
        if identity_count > 1:
            raise RelationResolutionError("IDENTITY_AXIS_NOT_MUTUALLY_EXCLUSIVE", [candidate_copy["candidate_id"], paper_id])

    unknowns = sorted(
        {
            canonical_json({
                "reason_code": item["reason_code"],
                "evidence_refs": sorted(set(item["evidence_refs"])),
            }): {
                "reason_code": item["reason_code"],
                "evidence_refs": sorted(set(item["evidence_refs"])),
            }
            for item in unknowns
        }.values(),
        key=canonical_json,
    )
    state_axes = {
        "identity_status": candidate_copy["identity_status"],
        "access_status": candidate_copy["access_status"],
        "decision_status": candidate_copy["decision_status"],
        "candidate_card_status": candidate_copy["candidate_card_status"],
        "promotion_status": candidate_copy["promotion_status"],
    }
    comparison = {
        "schema_version": "discovery.configuration.candidate-comparison.0.1",
        "object_type": "CandidateComparison",
        "comparison_id": _stable_id(
            "candidate_comparison_",
            [candidate_copy["candidate_id"], snapshot_copy["snapshot_id"], scope],
        ),
        "candidate_id": candidate_copy["candidate_id"],
        "snapshot_id": snapshot_copy["snapshot_id"],
        "single_writer": SINGLE_WRITER,
        "state_axes": state_axes,
        "state_axes_unchanged": True,
        "scope_receipt": {
            "allowed_paper_ids": scope,
            "records_read": adapter.read_count,
            "out_of_scope_reads": adapter.out_of_scope_reads,
            "write_attempts": adapter.write_attempts,
            "snapshot_hash": snapshot_copy["content_hash"],
        },
        "relations": sorted(
            relations,
            key=lambda item: (item["local_paper_id"], item["relation_type"], item["relation_id"]),
        ),
        "novelty": novelty,
        "unknown_conflict_queue": unknowns,
        "side_effects": copy.deepcopy(ZERO_SIDE_EFFECTS),
    }
    return seal_object(comparison)


def resolve_comparison_batch(
    candidates: Sequence[Mapping[str, Any]],
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve a deterministic batch; candidate input order is semantically irrelevant."""

    candidate_copies = [copy.deepcopy(dict(candidate)) for candidate in candidates]
    candidate_ids = [str(candidate.get("candidate_id", "")) for candidate in candidate_copies]
    if not candidate_copies or len(candidate_ids) != len(set(candidate_ids)):
        raise RelationResolutionError("CANDIDATE_BATCH_INVALID", candidate_ids)
    comparisons = [
        resolve_candidate_comparison(candidate, snapshot)
        for candidate in sorted(candidate_copies, key=lambda item: str(item.get("candidate_id", "")))
    ]
    result = {
        "schema_version": "discovery.configuration.comparison-batch.0.1",
        "producer": PRODUCER,
        "comparisons": comparisons,
        "statistics": {
            "candidates": len(comparisons),
            "relations": sum(len(item["relations"]) for item in comparisons),
            "unknown_conflict_items": sum(len(item["unknown_conflict_queue"]) for item in comparisons),
            **copy.deepcopy(ZERO_SIDE_EFFECTS),
        },
    }
    result["replay_fingerprint"] = sha256_json(result)
    return result


__all__ = [
    "ALGORITHM_REVISION",
    "LocalLibraryQueryAdapter",
    "RelationResolutionError",
    "ZERO_SIDE_EFFECTS",
    "canonical_json",
    "resolve_candidate_comparison",
    "resolve_comparison_batch",
    "seal_object",
    "sha256_json",
    "validate_candidate",
    "validate_local_library_snapshot",
]
