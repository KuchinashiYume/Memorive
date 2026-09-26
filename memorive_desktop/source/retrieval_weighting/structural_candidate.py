"""Gold-free structural candidate builder for the SEMANTIC-AGGREGATION A fixture."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .contracts import require_non_empty_string, require_sha256
from .errors import ContractViolation
from .field_search import search_field_vectors
from .shadow_contracts import reject_scorer_or_oracle_fields, require_exact_keys


SAFETY_ZERO = {
    "scope_violations": 0,
    "inactive_candidates": 0,
    "rights_violations": 0,
    "raw_cross_index_score_additions": 0,
    "unknown_provider_acceptances": 0,
    "gold_runtime_reads": 0,
    "external_calls": 0,
    "local_model_calls": 0,
    "provider_tokens": 0,
    "cost_cny": 0,
    "git_mutations": 0,
    "production_mutations": 0,
}
_EVIDENCE_KEYS = {"field_path", "source_id", "provenance_state"}


def _strings(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ContractViolation(f"{field} must be an array")
    result = [require_non_empty_string(item, f"{field}[]") for item in value]
    if not allow_empty and not result:
        raise ContractViolation(f"{field} must not be empty")
    if len(result) != len(set(result)):
        raise ContractViolation(f"{field} must not contain duplicates")
    return result


def _injections(value: Mapping[str, Any]) -> dict[str, list[dict[str, str]]]:
    if not isinstance(value, Mapping):
        raise ContractViolation("structural injection fixture must be an object")
    fixture = deepcopy(dict(value))
    reject_scorer_or_oracle_fields(fixture, "structural_injection_fixture")
    require_exact_keys(
        fixture,
        {
            "schema_version",
            "fixture_id",
            "purpose",
            "public_safe",
            "synthetic_only",
            "semantic_claims_allowed",
            "injections",
        },
        "structural_injection_fixture",
    )
    if fixture["schema_version"] != "SEMANTIC_AGGREGATION_STRUCTURAL_INJECTION_FIXTURE_V1":
        raise ContractViolation("structural injection fixture schema mismatch")
    if fixture["public_safe"] is not True or fixture["synthetic_only"] is not True:
        raise ContractViolation("structural injection fixture must be public-safe synthetic")
    if fixture["semantic_claims_allowed"] is not False:
        raise ContractViolation("structural injection fixture cannot grant semantic claims")
    raw_items = fixture["injections"]
    if isinstance(raw_items, (str, bytes)) or not isinstance(raw_items, Sequence):
        raise ContractViolation("structural injections must be an array")
    result: dict[str, list[dict[str, str]]] = {}
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            raise ContractViolation("structural injection must be an object")
        item = deepcopy(dict(raw_item))
        require_exact_keys(item, {"query_id", "evidence"}, "structural_injection")
        query_id = require_non_empty_string(item["query_id"], "structural_injection.query_id")
        if query_id in result:
            raise ContractViolation("structural injection query IDs must be unique")
        raw_evidence = item["evidence"]
        if isinstance(raw_evidence, (str, bytes)) or not isinstance(raw_evidence, Sequence) or not raw_evidence:
            raise ContractViolation("structural injection evidence must be non-empty")
        evidence: list[dict[str, str]] = []
        for raw_record in raw_evidence:
            if not isinstance(raw_record, Mapping):
                raise ContractViolation("structural injection evidence must be an object")
            record = deepcopy(dict(raw_record))
            require_exact_keys(record, _EVIDENCE_KEYS, "structural_injection.evidence[]")
            checked = {
                key: require_non_empty_string(record[key], f"injection.{key}")
                for key in sorted(_EVIDENCE_KEYS)
            }
            if checked["provenance_state"] not in {"original", "derived", "unknown"}:
                raise ContractViolation("structural injection provenance is unsupported")
            evidence.append(checked)
        result[query_id] = evidence
    return result


def build_structural_observed(
    query_pack: Mapping[str, Any],
    field_records: Sequence[Mapping[str, Any]],
    injection_fixture: Mapping[str, Any],
    *,
    query_pack_sha256: str,
    field_export_sha256: str,
    injection_fixture_sha256: str,
) -> dict[str, Any]:
    """Build candidate evidence from public inputs only; no scorer/Gold argument exists."""

    for value, name in (
        (query_pack_sha256, "query_pack_sha256"),
        (field_export_sha256, "field_export_sha256"),
        (injection_fixture_sha256, "injection_fixture_sha256"),
    ):
        require_sha256(value, name)
    if not isinstance(query_pack, Mapping):
        raise ContractViolation("query pack must be an object")
    query_value = deepcopy(dict(query_pack))
    reject_scorer_or_oracle_fields(query_value, "query_pack")
    if query_value.get("structural_injection_fixture_sha256") != injection_fixture_sha256:
        raise ContractViolation("query pack injection hash drift")
    queries = query_value.get("queries")
    if isinstance(queries, (str, bytes)) or not isinstance(queries, Sequence) or not queries:
        raise ContractViolation("query pack queries must be non-empty")
    injections = _injections(injection_fixture)
    used_injections: set[str] = set()
    rows: list[dict[str, Any]] = []
    observed_query_ids: set[str] = set()
    required = {"query_id", "stratum", "query_text", "explicit_scope", "dimensions"}
    optional = {
        "counterevidence_requested",
        "inject_structural_derived_competitor",
        "inject_structural_multi_source_candidates",
        "exercise_filtered_underfill",
    }
    for ordinal, raw_query in enumerate(queries):
        if not isinstance(raw_query, Mapping):
            raise ContractViolation("query must be an object")
        query = deepcopy(dict(raw_query))
        if not required.issubset(query) or set(query) - required - optional:
            raise ContractViolation("query keys are invalid")
        query_id = require_non_empty_string(query["query_id"], f"queries[{ordinal}].query_id")
        if query_id in observed_query_ids:
            raise ContractViolation("query IDs must be unique")
        observed_query_ids.add(query_id)
        scope = query["explicit_scope"]
        if not isinstance(scope, Mapping):
            raise ContractViolation("query explicit_scope must be an object")
        require_exact_keys(scope, {"paper_ids"}, "query.explicit_scope")
        paper_ids = _strings(scope["paper_ids"], "query.paper_ids")
        dimensions = query["dimensions"]
        if isinstance(dimensions, (str, bytes)) or not isinstance(dimensions, Sequence) or not dimensions:
            raise ContractViolation("query dimensions must be non-empty")
        evidence: list[dict[str, str]] = []
        underfill_reasons: set[str] = set()
        for raw_dimension in dimensions:
            if not isinstance(raw_dimension, Mapping):
                raise ContractViolation("query dimension must be an object")
            dimension = deepcopy(dict(raw_dimension))
            require_exact_keys(
                dimension,
                {"dimension_id", "field_path_templates", "allocation", "query_vector"},
                "query.dimension",
            )
            result = search_field_vectors(
                field_records,
                query_vector=dimension["query_vector"],
                allowed_paper_ids=paper_ids,
                field_path_templates=dimension["field_path_templates"],
                top_k=1,
            )
            if result["selected_count"] == 0 and query.get("exercise_filtered_underfill") is True:
                underfill_reasons.update(
                    row["reason_code"] for row in result["rejection_counts"]
                )
                continue
            if result["selected_count"] != 1:
                raise ContractViolation("query dimension did not resolve exactly one field")
            hit = result["hits"][0]
            evidence.append(
                {
                    "field_path": hit["field_path"],
                    "source_id": hit["field_source_id"],
                    "provenance_state": "original",
                }
            )
        injection_requested = any(
            query.get(name) is True
            for name in (
                "inject_structural_derived_competitor",
                "inject_structural_multi_source_candidates",
            )
        )
        if injection_requested:
            if query_id not in injections:
                raise ContractViolation("query requests an absent structural injection")
            evidence.extend(deepcopy(injections[query_id]))
            used_injections.add(query_id)
        elif query_id in injections:
            raise ContractViolation("fixture contains an unrequested query injection")
        evidence.sort(key=lambda item: (item["field_path"], item["source_id"], item["provenance_state"]))
        if len(evidence) != len(
            {(row["field_path"], row["source_id"], row["provenance_state"]) for row in evidence}
        ):
            raise ContractViolation("candidate evidence contains duplicate identities")
        counter_requested = query.get("counterevidence_requested") is True
        counter_eligible = counter_requested or any(
            row["field_path"] == "author_conclusion"
            or row["field_path"].startswith(
                (
                    "boundary_conditions/",
                    "author_limitations_outlook/limitations/",
                    "author_limitations_outlook/outlook/",
                )
            )
            for row in evidence
        )
        rows.append(
            {
                "query_id": query_id,
                "retrieval_outcome": "UNDERFILLED" if underfill_reasons else "MATCH",
                "underfill_reasons": sorted(underfill_reasons),
                "observed_evidence": evidence,
                "observed_channels": ["counterevidence" if counter_requested else "field"],
                "counterevidence_state": "ELIGIBLE" if counter_eligible else "NOT_APPLICABLE",
                "distinct_sources": len({row["source_id"] for row in evidence}),
                "key_original_harmful_displacement": 0,
            }
        )
    if set(injections) != used_injections:
        raise ContractViolation("injection fixture exact set differs from requested injections")
    return {
        "schema_version": "SEMANTIC_AGGREGATION_STRUCTURAL_OBSERVED_V1",
        "query_pack_sha256": query_pack_sha256,
        "field_export_sha256": field_export_sha256,
        "structural_injection_fixture_sha256": injection_fixture_sha256,
        "semantic_B_eligible": False,
        "field_semantic_retrieval_quality": "NOT_ASSESSED",
        "queries": rows,
        "safety": deepcopy(SAFETY_ZERO),
    }


__all__ = ["SAFETY_ZERO", "build_structural_observed"]
