"""Fail-closed shared contracts for the P03/T04 structural Shadow candidate.

These validators intentionally accept only deterministic JSON-domain payloads.
Every object has an exact key set, carries the accepted T01 RFC 8785 typed
``content_hash``, and rejects scorer/Gold/oracle control-plane fields at any
nesting depth.  The module does not read files, call a model, or authorize a
semantic Shadow run.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .contracts import (
    require_non_empty_string,
    require_sha256,
    require_storage_token,
    validate_hash_descriptor,
    verify_hashed_payload,
)
from .errors import ContractViolation


_FORBIDDEN_FIELD_TOKENS = (
    "gold",
    "oracle",
    "answerkey",
    "expectedanswer",
    "holdout",
    "nonce",
    "scorer",
)
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_OVERRIDE_EVENT_RE = re.compile(r"^(?:M08|M11|M12|M13):[^\r\n]+$")
_P00_WEIGHTS = {
    "Similarity": 0.45,
    "Semantic": 0.20,
    "Manual": 0.15,
    "Rule": 0.10,
    "Note": 0.07,
    "Citation": 0.03,
}
_COMMON_SNAPSHOT_KEYS = {
    "object_id",
    "schema_version",
    "revision",
    "producer",
    "consumers",
    "state",
    "parent_refs",
    "provenance_refs",
    "supersedes",
    "immutable_fields",
    "failure_semantics",
    "activation_authorized",
    "semantic_shadow_eligible",
    "semantic_qualification",
    "structural_only",
    "evaluation_bindings",
    "field_export_identity",
    "legacy_baseline_identity",
    "query_policy",
    "provider_policy",
    "aggregation_policy",
    "derived_penalty_policy",
    "scope_policy",
    "side_effect_policy",
    "threshold_policy",
    "hash_kind",
    "content_hash",
}
_IMMUTABLE_FIELDS = [
    "object_id",
    "schema_version",
    "revision",
    "content_hash",
    "producer",
    "parent_refs",
    "provenance_refs",
    "supersedes",
]
_FAILURE_SEMANTICS = {
    "BLOCKED": "fail_closed",
    "ERROR": "quarantined",
    "FAIL": "fail_closed",
    "NOT_ASSESSED": "no_eligibility",
}


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractViolation(f"{field_name} must be an object")
    return deepcopy(dict(value))


def require_exact_keys(
    value: Mapping[str, Any], expected: set[str], field_name: str
) -> None:
    """Require one mapping to match its frozen key set byte-for-byte."""

    actual = set(value)
    if actual != expected:
        raise ContractViolation(
            f"{field_name} keys must match the frozen exact set",
            context={
                "missing_keys": sorted(expected - actual),
                "unexpected_keys": sorted(actual - expected),
            },
        )


def require_boolean(value: Any, field_name: str) -> bool:
    """Validate a JSON boolean without accepting integers."""

    if not isinstance(value, bool):
        raise ContractViolation(f"{field_name} must be a boolean")
    return value


def require_finite_number(
    value: Any,
    field_name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Validate a finite JSON number and optional inclusive bounds."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractViolation(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ContractViolation(f"{field_name} must be a finite number")
    if minimum is not None and number < minimum:
        raise ContractViolation(f"{field_name} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise ContractViolation(f"{field_name} must be <= {maximum}")
    return number


def require_string_list(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool,
    sorted_required: bool = True,
) -> list[str]:
    """Validate a duplicate-free deterministic list of exact references."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ContractViolation(f"{field_name} must be an array")
    result = [require_non_empty_string(item, f"{field_name}[]") for item in value]
    if not allow_empty and not result:
        raise ContractViolation(f"{field_name} must not be empty")
    if len(result) != len(set(result)):
        raise ContractViolation(f"{field_name} must not contain duplicates")
    if sorted_required and result != sorted(result):
        raise ContractViolation(f"{field_name} must use canonical ascending order")
    return result


def reject_scorer_or_oracle_fields(
    value: Any, field_name: str = "payload"
) -> None:
    """Recursively reject evaluator-only or secret-bearing field names.

    Values are deliberately not substring-scanned: the boundary is structural,
    not a lossy content classifier.  Every mapping key at every nesting depth is
    normalized to ASCII alphanumerics before comparison.
    """

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ContractViolation(f"{field_name} has a non-string object key")
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if any(token in normalized for token in _FORBIDDEN_FIELD_TOKENS):
                raise ContractViolation(
                    f"{field_name} contains a forbidden evaluator/private field",
                    context={"field_path": f"{field_name}.{key}"},
                )
            if isinstance(child, str) and any(
                marker in normalized for marker in ("locator", "ref", "path", "uri")
            ):
                normalized_value = re.sub(r"[^a-z0-9]", "", child.casefold())
                if any(token in normalized_value for token in _FORBIDDEN_FIELD_TOKENS):
                    raise ContractViolation(
                        f"{field_name} contains a forbidden evaluator/private locator",
                        context={"field_path": f"{field_name}.{key}"},
                    )
            reject_scorer_or_oracle_fields(child, f"{field_name}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for ordinal, child in enumerate(value):
            reject_scorer_or_oracle_fields(child, f"{field_name}[{ordinal}]")
    elif isinstance(value, str):
        normalized_path = re.sub(r"[^a-z0-9]", "", field_name.casefold())
        if any(marker in normalized_path for marker in ("locator", "ref", "path", "uri")):
            normalized_value = re.sub(r"[^a-z0-9]", "", value.casefold())
            if any(token in normalized_value for token in _FORBIDDEN_FIELD_TOKENS):
                raise ContractViolation(
                    f"{field_name} contains a forbidden evaluator/private reference"
                )


def _validate_hash_map(value: Any, field_name: str) -> dict[str, str]:
    result = _mapping(value, field_name)
    if not result:
        raise ContractViolation(f"{field_name} must not be empty")
    if list(result) != sorted(result):
        raise ContractViolation(f"{field_name} keys must use canonical ascending order")
    for key, digest in result.items():
        require_non_empty_string(key, f"{field_name}.key")
        require_sha256(digest, f"{field_name}.{key}")
    return result


def _validate_float_map(
    value: Any,
    field_name: str,
    *,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> dict[str, float]:
    result = _mapping(value, field_name)
    if list(result) != sorted(result):
        raise ContractViolation(f"{field_name} keys must use canonical ascending order")
    return {
        require_non_empty_string(key, f"{field_name}.key"): require_finite_number(
            item, f"{field_name}.{key}", minimum=minimum, maximum=maximum
        )
        for key, item in result.items()
    }


def _validate_field_export_identity(value: Any) -> dict[str, Any]:
    result = _mapping(value, "field_export_identity")
    require_exact_keys(
        result,
        {
            "field_registry_hash",
            "field_index_manifest_hash",
            "field_export_hash",
            "embedding_profile_id",
            "embedding_dimension",
            "embedding_normalization",
            "production_eligible",
        },
        "field_export_identity",
    )
    for name in ("field_registry_hash", "field_index_manifest_hash", "field_export_hash"):
        validate_hash_descriptor(result[name], f"field_export_identity.{name}")
    require_non_empty_string(
        result["embedding_profile_id"], "field_export_identity.embedding_profile_id"
    )
    if isinstance(result["embedding_dimension"], bool) or not isinstance(
        result["embedding_dimension"], int
    ) or result["embedding_dimension"] < 1:
        raise ContractViolation("field_export_identity.embedding_dimension must be positive")
    require_non_empty_string(
        result["embedding_normalization"],
        "field_export_identity.embedding_normalization",
    )
    if require_boolean(
        result["production_eligible"], "field_export_identity.production_eligible"
    ):
        raise ContractViolation("T04 structural field export cannot be production eligible")
    return result


def _validate_evaluation_bindings(value: Any) -> dict[str, Any]:
    result = _mapping(value, "evaluation_bindings")
    require_exact_keys(
        result,
        {
            "query_pack_hash",
            "reference_pack_hash",
            "evaluation_tool_hash",
            "threshold_function_hash",
        },
        "evaluation_bindings",
    )
    for name in (
        "query_pack_hash",
        "reference_pack_hash",
        "evaluation_tool_hash",
        "threshold_function_hash",
    ):
        validate_hash_descriptor(result[name], f"evaluation_bindings.{name}")
    return result


def _validate_legacy_identity(value: Any) -> dict[str, Any]:
    result = _mapping(value, "legacy_baseline_identity")
    require_exact_keys(
        result,
        {
            "baseline_commit",
            "code_hashes",
            "route_id",
            "index_ref",
            "index_manifest_hash",
            "cutoff_ref",
            "read_only",
        },
        "legacy_baseline_identity",
    )
    if not isinstance(result["baseline_commit"], str) or not _COMMIT_RE.fullmatch(
        result["baseline_commit"]
    ):
        raise ContractViolation("legacy_baseline_identity.baseline_commit must be lowercase Git SHA-1")
    _validate_hash_map(result["code_hashes"], "legacy_baseline_identity.code_hashes")
    for name in ("route_id", "index_ref", "cutoff_ref"):
        require_non_empty_string(result[name], f"legacy_baseline_identity.{name}")
    validate_hash_descriptor(
        result["index_manifest_hash"], "legacy_baseline_identity.index_manifest_hash"
    )
    if not require_boolean(result["read_only"], "legacy_baseline_identity.read_only"):
        raise ContractViolation("legacy baseline must be read-only")
    return result


def _validate_query_policy(value: Any) -> dict[str, Any]:
    result = _mapping(value, "query_policy")
    require_exact_keys(
        result,
        {
            "policy_version",
            "query_set_hash",
            "allocation_method",
            "fallback_floor",
            "missing_field_policy",
            "allocation_tie_break",
        },
        "query_policy",
    )
    for name in ("policy_version", "allocation_method", "missing_field_policy", "allocation_tie_break"):
        require_non_empty_string(result[name], f"query_policy.{name}")
    if result["missing_field_policy"] != "skip_and_renormalize":
        raise ContractViolation("query missing-field policy mismatch")
    if result["allocation_tie_break"] != "field_id_ascending":
        raise ContractViolation("query allocation tie-break mismatch")
    validate_hash_descriptor(result["query_set_hash"], "query_policy.query_set_hash")
    require_finite_number(
        result["fallback_floor"], "query_policy.fallback_floor", minimum=0.0, maximum=1.0
    )
    return result


def _validate_provider_policy(value: Any) -> dict[str, Any]:
    from .providers import PROVIDERS

    result = _mapping(value, "provider_policy")
    require_exact_keys(
        result,
        {
            "providers",
            "provider_versions",
            "normalization_profiles",
            "weights",
            "states",
            "reranker_state",
            "model_state",
        },
        "provider_policy",
    )
    if result["providers"] != list(PROVIDERS):
        raise ContractViolation("provider_policy.providers must be the frozen six-provider order")
    versions = _mapping(result["provider_versions"], "provider_policy.provider_versions")
    normalizations = _mapping(
        result["normalization_profiles"], "provider_policy.normalization_profiles"
    )
    weights = _mapping(result["weights"], "provider_policy.weights")
    provider_keys = set(PROVIDERS)
    for mapping, name in (
        (versions, "provider_versions"),
        (normalizations, "normalization_profiles"),
        (weights, "weights"),
    ):
        require_exact_keys(mapping, provider_keys, f"provider_policy.{name}")
    for provider in PROVIDERS:
        require_non_empty_string(versions[provider], f"provider_policy.provider_versions.{provider}")
        require_non_empty_string(
            normalizations[provider],
            f"provider_policy.normalization_profiles.{provider}",
        )
        require_finite_number(
            weights[provider], f"provider_policy.weights.{provider}", minimum=0.0, maximum=1.0
        )
    if weights != _P00_WEIGHTS:
        raise ContractViolation("provider_policy.weights must equal the frozen P00 exact values")
    if result["states"] != ["observed", "missing", "not_applicable", "invalid"]:
        raise ContractViolation("provider_policy.states must be the frozen four-state order")
    if result["reranker_state"] != "disabled" or result["model_state"] != "disabled":
        raise ContractViolation("T04 structural candidate requires model and reranker disabled")
    return result


def _validate_aggregation_policy(value: Any) -> dict[str, Any]:
    from .providers import AUTHORITY_PROVIDERS, RELEVANCE_PROVIDERS

    result = _mapping(value, "aggregation_policy")
    require_exact_keys(
        result,
        {
            "baseline_policy_id",
            "shadow_policy_id",
            "relevance_providers",
            "authority_providers",
            "missing_data_policy",
            "authority_mass_cap",
            "rrf_k",
            "channel_caps",
            "per_card_cap",
            "top_k",
            "mmr_lambda",
            "counterevidence_slots",
            "counter_minimum_relevance",
            "counter_per_source_cap",
            "counter_per_paper_cap",
            "tie_break",
            "seed",
            "token_budget",
        },
        "aggregation_policy",
    )
    for name in ("baseline_policy_id", "shadow_policy_id", "missing_data_policy", "tie_break"):
        require_non_empty_string(result[name], f"aggregation_policy.{name}")
    if result["baseline_policy_id"] != "policy_baseline_v6_1":
        raise ContractViolation("aggregation baseline policy ID mismatch")
    if result["shadow_policy_id"] != "policy_shadow_availability_aware_v1":
        raise ContractViolation("aggregation availability policy ID mismatch")
    if result["missing_data_policy"] != "availability_aware":
        raise ContractViolation("aggregation missing-data policy mismatch")
    if result["tie_break"] != "candidate_id_ascending":
        raise ContractViolation("aggregation tie-break policy mismatch")
    if result["relevance_providers"] != list(RELEVANCE_PROVIDERS):
        raise ContractViolation("aggregation_policy.relevance_providers exact order mismatch")
    if result["authority_providers"] != list(AUTHORITY_PROVIDERS):
        raise ContractViolation("aggregation_policy.authority_providers exact order mismatch")
    require_finite_number(
        result["authority_mass_cap"],
        "aggregation_policy.authority_mass_cap",
        minimum=0.0,
        maximum=0.35,
    )
    if not math.isclose(float(result["authority_mass_cap"]), 0.35, abs_tol=1e-12):
        raise ContractViolation("aggregation authority_mass_cap must equal 0.35")
    if isinstance(result["rrf_k"], bool) or not isinstance(result["rrf_k"], int) or result["rrf_k"] < 1:
        raise ContractViolation("aggregation_policy.rrf_k must be a positive integer")
    if result["rrf_k"] != 60:
        raise ContractViolation("aggregation_policy.rrf_k must equal frozen value 60")
    channel_caps = _mapping(result["channel_caps"], "aggregation_policy.channel_caps")
    if not channel_caps:
        raise ContractViolation("aggregation_policy.channel_caps must not be empty")
    if list(channel_caps) != sorted(channel_caps):
        raise ContractViolation("aggregation_policy.channel_caps keys must be sorted")
    for channel, cap in channel_caps.items():
        require_non_empty_string(channel, "aggregation_policy.channel_caps.key")
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            raise ContractViolation("aggregation_policy channel caps must be positive integers")
    for name in (
        "per_card_cap",
        "top_k",
        "counterevidence_slots",
        "counter_per_source_cap",
        "counter_per_paper_cap",
    ):
        if isinstance(result[name], bool) or not isinstance(result[name], int) or result[name] < 0:
            raise ContractViolation(f"aggregation_policy.{name} must be a non-negative integer")
    if any(
        result[name] < 1
        for name in (
            "per_card_cap",
            "top_k",
            "counter_per_source_cap",
            "counter_per_paper_cap",
        )
    ):
        raise ContractViolation("aggregation policy capacity caps must be positive")
    require_finite_number(
        result["mmr_lambda"], "aggregation_policy.mmr_lambda", minimum=0.0, maximum=1.0
    )
    if not math.isclose(float(result["mmr_lambda"]), 0.7, abs_tol=1e-12):
        raise ContractViolation("aggregation_policy.mmr_lambda must equal frozen value 0.7")
    require_finite_number(
        result["counter_minimum_relevance"],
        "aggregation_policy.counter_minimum_relevance",
        minimum=0.0,
        maximum=1.0,
    )
    if isinstance(result["seed"], bool) or not isinstance(result["seed"], int):
        raise ContractViolation("aggregation_policy.seed must be an integer")
    if result["token_budget"] != 0:
        raise ContractViolation("T04 structural candidate token_budget must be 0")
    return result


def validate_derived_penalty_policy(value: Any) -> dict[str, Any]:
    result = _mapping(value, "derived_penalty_policy")
    require_exact_keys(
        result,
        {
            "policy_version",
            "type_penalties",
            "unknown_provenance_issue",
            "override_contract_version",
            "override_event_allowlist",
            "authority_only",
        },
        "derived_penalty_policy",
    )
    for name in ("policy_version", "override_contract_version"):
        require_non_empty_string(result[name], f"derived_penalty_policy.{name}")
    type_penalties = _validate_float_map(
        result["type_penalties"], "derived_penalty_policy.type_penalties"
    )
    if not type_penalties:
        raise ContractViolation("derived_penalty_policy.type_penalties must not be empty")
    if result["unknown_provenance_issue"] != "UNKNOWN_PROVENANCE_AUTHORITY_BLOCKED":
        raise ContractViolation("derived_penalty_policy unknown issue code mismatch")
    if not require_boolean(result["authority_only"], "derived_penalty_policy.authority_only"):
        raise ContractViolation("DerivedPenalty must be authority-only")
    allowlist = result["override_event_allowlist"]
    if not isinstance(allowlist, Sequence) or isinstance(allowlist, (str, bytes)):
        raise ContractViolation("derived_penalty_policy.override_event_allowlist must be an array")
    observed_locators: list[str] = []
    for ordinal, raw in enumerate(allowlist):
        item = _mapping(raw, f"derived_penalty_policy.override_event_allowlist[{ordinal}]")
        require_exact_keys(
            item,
            {"event_locator", "event_hash"},
            f"derived_penalty_policy.override_event_allowlist[{ordinal}]",
        )
        locator = require_non_empty_string(
            item["event_locator"],
            f"derived_penalty_policy.override_event_allowlist[{ordinal}].event_locator",
        )
        if not _OVERRIDE_EVENT_RE.fullmatch(locator):
            raise ContractViolation("override allowlist locator must bind M08/M11/M12/M13")
        validate_hash_descriptor(
            item["event_hash"],
            f"derived_penalty_policy.override_event_allowlist[{ordinal}].event_hash",
        )
        observed_locators.append(locator)
    if observed_locators != sorted(set(observed_locators)):
        raise ContractViolation("override event allowlist must be sorted and locator-unique")
    return result


def _validate_scope_policy(value: Any) -> dict[str, Any]:
    result = _mapping(value, "scope_policy")
    require_exact_keys(
        result,
        {"active_only", "explicit_scope_required", "rights_required", "allowlist_refs"},
        "scope_policy",
    )
    for name in ("active_only", "explicit_scope_required", "rights_required"):
        if not require_boolean(result[name], f"scope_policy.{name}"):
            raise ContractViolation(f"scope_policy.{name} must remain true")
    require_string_list(result["allowlist_refs"], "scope_policy.allowlist_refs", allow_empty=False)
    return result


def _validate_side_effect_policy(value: Any) -> dict[str, Any]:
    result = _mapping(value, "side_effect_policy")
    require_exact_keys(
        result,
        {
            "shadow_only",
            "production_writes_allowed",
            "route_changes_allowed",
            "pointer_changes_allowed",
            "disable_switch",
            "environment_id",
            "output_manifest_ref",
        },
        "side_effect_policy",
    )
    if not require_boolean(result["shadow_only"], "side_effect_policy.shadow_only"):
        raise ContractViolation("side_effect_policy.shadow_only must remain true")
    for name in ("production_writes_allowed", "route_changes_allowed", "pointer_changes_allowed"):
        if require_boolean(result[name], f"side_effect_policy.{name}"):
            raise ContractViolation(f"side_effect_policy.{name} must remain false")
    for name in ("disable_switch", "environment_id", "output_manifest_ref"):
        require_non_empty_string(result[name], f"side_effect_policy.{name}")
    return result


def _validate_threshold_policy(value: Any) -> dict[str, Any]:
    result = _mapping(value, "threshold_policy")
    require_exact_keys(
        result,
        {
            "function_version",
            "function_hash",
            "baseline_relative",
            "candidate_output_not_consumed",
            "receipt_required_before_candidate",
        },
        "threshold_policy",
    )
    require_non_empty_string(result["function_version"], "threshold_policy.function_version")
    validate_hash_descriptor(result["function_hash"], "threshold_policy.function_hash")
    for name in (
        "baseline_relative",
        "candidate_output_not_consumed",
        "receipt_required_before_candidate",
    ):
        if not require_boolean(result[name], f"threshold_policy.{name}"):
            raise ContractViolation(f"threshold_policy.{name} must remain true")
    return result


def validate_retrieval_policy_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy the frozen structural-only RetrievalPolicySnapshot."""

    result = _mapping(snapshot, "retrieval_policy_snapshot")
    reject_scorer_or_oracle_fields(result, "retrieval_policy_snapshot")
    require_exact_keys(result, _COMMON_SNAPSHOT_KEYS, "retrieval_policy_snapshot")
    require_storage_token(result["object_id"], "retrieval_policy_snapshot.object_id")
    if result["schema_version"] != "2.0":
        raise ContractViolation("retrieval_policy_snapshot.schema_version must be 2.0")
    if isinstance(result["revision"], bool) or not isinstance(result["revision"], int) or result["revision"] < 1:
        raise ContractViolation("retrieval_policy_snapshot.revision must be positive")
    require_non_empty_string(result["producer"], "retrieval_policy_snapshot.producer")
    require_string_list(result["consumers"], "retrieval_policy_snapshot.consumers", allow_empty=False)
    if result["state"] not in {"candidate", "shadow_only"}:
        raise ContractViolation("retrieval_policy_snapshot.state is invalid")
    require_string_list(result["parent_refs"], "retrieval_policy_snapshot.parent_refs", allow_empty=True)
    require_string_list(
        result["provenance_refs"], "retrieval_policy_snapshot.provenance_refs", allow_empty=False
    )
    supersedes = result["supersedes"]
    if result["revision"] == 1:
        if supersedes is not None:
            raise ContractViolation("revision 1 retrieval policy must not supersede an object")
    else:
        require_non_empty_string(supersedes, "retrieval_policy_snapshot.supersedes")
        if supersedes == result["object_id"]:
            raise ContractViolation("retrieval policy must not supersede itself")
    if result["immutable_fields"] != _IMMUTABLE_FIELDS:
        raise ContractViolation("retrieval_policy_snapshot.immutable_fields mismatch")
    if result["failure_semantics"] != _FAILURE_SEMANTICS:
        raise ContractViolation("retrieval_policy_snapshot.failure_semantics mismatch")
    for name in ("activation_authorized", "semantic_shadow_eligible"):
        if require_boolean(result[name], f"retrieval_policy_snapshot.{name}"):
            raise ContractViolation(f"retrieval_policy_snapshot.{name} must remain false")
    if result["semantic_qualification"] != "NOT_ASSESSED":
        raise ContractViolation("semantic qualification must remain NOT_ASSESSED")
    if not require_boolean(result["structural_only"], "retrieval_policy_snapshot.structural_only"):
        raise ContractViolation("retrieval policy must remain structural-only")
    evaluation_bindings = _validate_evaluation_bindings(result["evaluation_bindings"])
    _validate_field_export_identity(result["field_export_identity"])
    _validate_legacy_identity(result["legacy_baseline_identity"])
    query_policy = _validate_query_policy(result["query_policy"])
    _validate_provider_policy(result["provider_policy"])
    _validate_aggregation_policy(result["aggregation_policy"])
    validate_derived_penalty_policy(result["derived_penalty_policy"])
    _validate_scope_policy(result["scope_policy"])
    _validate_side_effect_policy(result["side_effect_policy"])
    threshold_policy = _validate_threshold_policy(result["threshold_policy"])
    if evaluation_bindings["query_pack_hash"] != query_policy["query_set_hash"]:
        raise ContractViolation(
            "evaluation query pack hash differs from query_policy.query_set_hash"
        )
    if evaluation_bindings["threshold_function_hash"] != threshold_policy["function_hash"]:
        raise ContractViolation(
            "evaluation threshold hash differs from threshold_policy.function_hash"
        )
    if result["hash_kind"] != "rfc8785_jcs_sha256":
        raise ContractViolation("retrieval_policy_snapshot.hash_kind mismatch")
    validate_hash_descriptor(result["content_hash"], "retrieval_policy_snapshot.content_hash")
    return verify_hashed_payload(result, "retrieval_policy_snapshot")


__all__ = [
    "reject_scorer_or_oracle_fields",
    "require_boolean",
    "require_exact_keys",
    "require_finite_number",
    "require_string_list",
    "validate_derived_penalty_policy",
    "validate_retrieval_policy_snapshot",
]
