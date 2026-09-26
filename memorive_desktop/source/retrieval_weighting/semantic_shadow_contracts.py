"""Terminal-receipt-gated r0.2 contracts for the semantic Shadow successor."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .canonical import canonical_json_bytes, sha256_bytes
from .contracts import (
    make_hashed_payload,
    require_non_empty_string,
    require_sha256,
    require_storage_token,
    validate_hash_descriptor,
    verify_hashed_payload,
)
from .errors import ContractViolation, IdentityConflict
from .semantic_field_profiles import (
    validate_semantic_field_embedding_overlay,
    validate_semantic_field_profile,
)
from .shadow_contracts import (
    reject_scorer_or_oracle_fields,
    validate_retrieval_policy_snapshot,
)


TERMINAL_RECEIPT_SCHEMA = "SEMANTIC_AGGREGATION_SEMANTIC_PROFILE_QUALIFICATION_TERMINAL_RECEIPT_V1"
POLICY_SCHEMA_VERSION = "2.1"

_TERMINAL_KEYS = {
    "schema_version",
    "object_type",
    "receipt_id",
    "candidate_id",
    "terminalizer_identity",
    "profile_id",
    "profile_content_hash",
    "profile_raw_sha256",
    "overlay_id",
    "overlay_content_hash",
    "overlay_raw_sha256",
    "source_pack_raw_sha256",
    "query_pack_raw_sha256",
    "precomputed_query_vectors_raw_sha256",
    "observed_results_raw_sha256",
    "score_receipt_raw_sha256",
    "execution_authorization_raw_sha256",
    "lifecycle_status",
    "verification_result",
    "qualification_result",
    "acceptance_verdict",
    "eligible_for_B_freeze_candidate_construction",
    "semantic_B_eligible",
    "release_to_B",
    "semantic_shadow_eligible",
    "field_semantic_retrieval_quality",
    "retrieval_improvement_over_baseline",
    "production_eligible",
    "model_calls",
    "reranker_calls",
    "loopback_http_calls",
    "external_network_calls",
    "provider_tokens",
    "cost_cny",
    "create_only",
    "hash_kind",
    "content_hash",
}

_R02_POLICY_KEYS = {
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
    "semantic_profile_binding",
    "qualification_terminal_receipt_binding",
    "semantic_execution_policy",
    "field_semantic_retrieval_quality",
    "retrieval_improvement_over_baseline",
    "acceptance_verdict",
    "eligible_for_B_freeze_candidate_construction",
    "semantic_B_eligible",
    "release_to_B",
    "hash_kind",
    "content_hash",
}
_PROFILE_BINDING_KEYS = {
    "profile_id",
    "profile_content_hash",
    "profile_raw_sha256",
    "overlay_id",
    "overlay_content_hash",
    "overlay_raw_sha256",
    "source_pack_raw_sha256",
}
_RECEIPT_BINDING_KEYS = {
    "receipt_id",
    "receipt_content_hash",
    "receipt_raw_sha256",
    "qualification_result",
}
_SEMANTIC_EXECUTION_KEYS = {
    "model",
    "reranker",
    "model_calls",
    "reranker_calls",
    "network_calls",
    "provider_tokens",
    "cost_cny",
}
_R02_ADDITIVE_KEYS = {
    "semantic_profile_binding",
    "qualification_terminal_receipt_binding",
    "semantic_execution_policy",
    "field_semantic_retrieval_quality",
    "retrieval_improvement_over_baseline",
    "acceptance_verdict",
    "eligible_for_B_freeze_candidate_construction",
    "semantic_B_eligible",
    "release_to_B",
}


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractViolation(f"{field_name} must be an object")
    return deepcopy(dict(value))


def _exact(value: Mapping[str, Any], expected: set[str], field_name: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ContractViolation(
            f"{field_name} keys must match the frozen exact set",
            context={
                "missing_keys": sorted(expected - actual),
                "unexpected_keys": sorted(actual - expected),
            },
        )


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ContractViolation(f"{field_name} must be a boolean")
    return value


def _zero(value: Any, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractViolation(f"{field_name} must be numeric zero")
    if not math.isfinite(float(value)) or float(value) != 0.0:
        raise ContractViolation(f"{field_name} must remain zero")


def canonical_artifact_file_bytes(value: Mapping[str, Any]) -> bytes:
    """Return the frozen raw-file encoding used by qualification artifacts."""

    return canonical_json_bytes(deepcopy(dict(value))) + b"\n"


def canonical_artifact_raw_sha256(value: Mapping[str, Any]) -> str:
    """Hash the exact JCS-plus-LF qualification artifact representation."""

    return sha256_bytes(canonical_artifact_file_bytes(value))


def validate_semantic_profile_terminal_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify a create-only, non-acceptance terminal qualification receipt."""

    result = verify_hashed_payload(receipt, "semantic_profile_terminal_receipt")
    _exact(result, _TERMINAL_KEYS, "semantic_profile_terminal_receipt")
    if result["schema_version"] != TERMINAL_RECEIPT_SCHEMA:
        raise ContractViolation("semantic terminal receipt schema mismatch")
    if result["object_type"] != "SemanticProfileQualificationTerminalReceipt":
        raise ContractViolation("semantic terminal receipt object_type mismatch")
    require_storage_token(result["receipt_id"], "terminal_receipt.receipt_id")
    require_storage_token(result["candidate_id"], "terminal_receipt.candidate_id")
    if (
        result["terminalizer_identity"]
        != "LOCAL_MECHANICAL_SEMANTIC_PROFILE_QUALIFIER_NOT_ACCEPTANCE_AUTHORITY"
    ):
        raise ContractViolation("semantic terminalizer identity mismatch")
    require_storage_token(result["profile_id"], "terminal_receipt.profile_id")
    require_storage_token(result["overlay_id"], "terminal_receipt.overlay_id")
    validate_hash_descriptor(result["profile_content_hash"], "profile_content_hash")
    validate_hash_descriptor(result["overlay_content_hash"], "overlay_content_hash")
    for name in (
        "profile_raw_sha256",
        "overlay_raw_sha256",
        "source_pack_raw_sha256",
        "query_pack_raw_sha256",
        "precomputed_query_vectors_raw_sha256",
        "observed_results_raw_sha256",
        "score_receipt_raw_sha256",
        "execution_authorization_raw_sha256",
    ):
        require_sha256(result[name], f"terminal_receipt.{name}")
    if result["lifecycle_status"] != "completed":
        raise ContractViolation("semantic terminal receipt lifecycle must be completed")
    if result["verification_result"] != "PASS" or result["qualification_result"] != "PASS":
        raise ContractViolation("semantic terminal qualification did not pass")
    for name in (
        "acceptance_verdict",
        "field_semantic_retrieval_quality",
        "retrieval_improvement_over_baseline",
    ):
        if result[name] != "NOT_ASSESSED":
            raise ContractViolation(f"terminal_receipt.{name} must remain NOT_ASSESSED")
    if not _boolean(result["semantic_shadow_eligible"], "semantic_shadow_eligible"):
        raise ContractViolation("semantic terminal receipt must establish shadow eligibility")
    if not _boolean(
        result["eligible_for_B_freeze_candidate_construction"],
        "eligible_for_B_freeze_candidate_construction",
    ):
        raise ContractViolation("semantic terminal receipt must enable only B freeze construction")
    for name in ("semantic_B_eligible", "release_to_B"):
        if _boolean(result[name], f"terminal_receipt.{name}"):
            raise ContractViolation(f"terminal_receipt.{name} must remain false")
    if _boolean(result["production_eligible"], "production_eligible"):
        raise ContractViolation("semantic terminal receipt must remain non-production")
    if result["model_calls"] != 1:
        raise ContractViolation("semantic qualification must bind exactly one batch embed call")
    if result["loopback_http_calls"] != 2:
        raise ContractViolation("semantic qualification must bind tags plus one batch embed call")
    for name in (
        "reranker_calls",
        "external_network_calls",
        "provider_tokens",
        "cost_cny",
    ):
        _zero(result[name], f"terminal_receipt.{name}")
    if not _boolean(result["create_only"], "terminal_receipt.create_only"):
        raise ContractViolation("semantic terminal receipt must be create-only")
    if result["hash_kind"] != "rfc8785_jcs_sha256":
        raise ContractViolation("semantic terminal receipt hash_kind mismatch")
    return result


def validate_semantic_retrieval_policy_snapshot(
    snapshot: Mapping[str, Any],
    *,
    terminal_receipt: Mapping[str, Any],
    profile: Mapping[str, Any],
    overlay: Mapping[str, Any],
    source_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Join r0.2 policy eligibility to one qualified profile/overlay receipt.

    A valid result authorizes only construction of a B freeze candidate.  It
    never activates production or reports field quality, improvement or task
    acceptance.
    """

    checked_profile = validate_semantic_field_profile(profile)
    checked_overlay = validate_semantic_field_embedding_overlay(
        overlay,
        profile=checked_profile,
        source_records=source_records,
    )
    checked_receipt = validate_semantic_profile_terminal_receipt(terminal_receipt)
    result = verify_hashed_payload(snapshot, "semantic_retrieval_policy_snapshot")
    reject_scorer_or_oracle_fields(result, "semantic_retrieval_policy_snapshot")
    _exact(result, _R02_POLICY_KEYS, "semantic_retrieval_policy_snapshot")

    if result["schema_version"] != POLICY_SCHEMA_VERSION:
        raise ContractViolation("semantic retrieval policy schema_version mismatch")
    if isinstance(result["revision"], bool) or not isinstance(result["revision"], int) or result["revision"] < 2:
        raise ContractViolation("semantic retrieval policy revision must be >= 2")
    if result["state"] != "shadow_only":
        raise ContractViolation("semantic retrieval policy state must be shadow_only")
    if _boolean(result["activation_authorized"], "activation_authorized"):
        raise ContractViolation("semantic retrieval policy cannot authorize activation")
    if not _boolean(result["semantic_shadow_eligible"], "semantic_shadow_eligible"):
        raise ContractViolation("semantic retrieval policy must record qualified eligibility")
    if result["semantic_qualification"] != "QUALIFIED_FOR_SHADOW_B_INPUT":
        raise ContractViolation("semantic qualification state mismatch")
    if _boolean(result["structural_only"], "structural_only"):
        raise ContractViolation("semantic retrieval policy cannot remain structural_only")
    for name in (
        "field_semantic_retrieval_quality",
        "retrieval_improvement_over_baseline",
        "acceptance_verdict",
    ):
        if result[name] != "NOT_ASSESSED":
            raise ContractViolation(f"semantic retrieval policy {name} must remain NOT_ASSESSED")
    if not _boolean(
        result["eligible_for_B_freeze_candidate_construction"],
        "eligible_for_B_freeze_candidate_construction",
    ):
        raise ContractViolation("policy must allow only B freeze candidate construction")
    for name in ("semantic_B_eligible", "release_to_B"):
        if _boolean(result[name], name):
            raise ContractViolation(f"semantic retrieval policy {name} must remain false")

    binding = _mapping(result["semantic_profile_binding"], "semantic_profile_binding")
    _exact(binding, _PROFILE_BINDING_KEYS, "semantic_profile_binding")
    expected_profile_raw = canonical_artifact_raw_sha256(checked_profile)
    expected_overlay_raw = canonical_artifact_raw_sha256(checked_overlay)
    expected_binding = {
        "profile_id": checked_profile["profile_id"],
        "profile_content_hash": checked_profile["content_hash"],
        "profile_raw_sha256": expected_profile_raw,
        "overlay_id": checked_overlay["overlay_id"],
        "overlay_content_hash": checked_overlay["content_hash"],
        "overlay_raw_sha256": expected_overlay_raw,
        "source_pack_raw_sha256": checked_profile["source_pack_raw_sha256"],
    }
    if binding != expected_binding:
        raise IdentityConflict("semantic profile binding differs from profile/overlay artifacts")

    if (
        checked_receipt["profile_id"] != checked_profile["profile_id"]
        or checked_receipt["profile_content_hash"] != checked_profile["content_hash"]
        or checked_receipt["profile_raw_sha256"] != expected_profile_raw
        or checked_receipt["overlay_id"] != checked_overlay["overlay_id"]
        or checked_receipt["overlay_content_hash"] != checked_overlay["content_hash"]
        or checked_receipt["overlay_raw_sha256"] != expected_overlay_raw
        or checked_receipt["source_pack_raw_sha256"]
        != checked_profile["source_pack_raw_sha256"]
    ):
        raise IdentityConflict("semantic terminal receipt does not bind the supplied artifacts")

    receipt_binding = _mapping(
        result["qualification_terminal_receipt_binding"],
        "qualification_terminal_receipt_binding",
    )
    _exact(receipt_binding, _RECEIPT_BINDING_KEYS, "qualification_terminal_receipt_binding")
    expected_receipt_binding = {
        "receipt_id": checked_receipt["receipt_id"],
        "receipt_content_hash": checked_receipt["content_hash"],
        "receipt_raw_sha256": canonical_artifact_raw_sha256(checked_receipt),
        "qualification_result": "PASS",
    }
    if receipt_binding != expected_receipt_binding:
        raise IdentityConflict("semantic terminal receipt binding mismatch")

    execution = _mapping(result["semantic_execution_policy"], "semantic_execution_policy")
    _exact(execution, _SEMANTIC_EXECUTION_KEYS, "semantic_execution_policy")
    if execution["model"] != "NONE" or execution["reranker"] != "NONE":
        raise ContractViolation("B semantic policy model and reranker must remain NONE")
    for name in (
        "model_calls",
        "reranker_calls",
        "network_calls",
        "provider_tokens",
        "cost_cny",
    ):
        _zero(execution[name], f"semantic_execution_policy.{name}")

    export_identity = result["field_export_identity"]
    if (
        export_identity["field_registry_hash"] != checked_profile["field_registry_hash"]
        or export_identity["field_index_manifest_hash"]
        != checked_profile["content_hash"]
        or export_identity["field_export_hash"] != checked_overlay["content_hash"]
        or export_identity["embedding_profile_id"] != checked_profile["profile_id"]
        or export_identity["embedding_dimension"] != checked_profile["embedding_dimension"]
        or export_identity["embedding_normalization"] != "l2"
        or export_identity["production_eligible"] is not False
    ):
        raise IdentityConflict("field_export_identity does not bind semantic profile/overlay")

    predecessor_payload = {
        key: deepcopy(value)
        for key, value in result.items()
        if key not in _R02_ADDITIVE_KEYS and key != "content_hash"
    }
    predecessor_payload["schema_version"] = "2.0"
    predecessor_payload["semantic_shadow_eligible"] = False
    predecessor_payload["semantic_qualification"] = "NOT_ASSESSED"
    predecessor_payload["structural_only"] = True
    validate_retrieval_policy_snapshot(make_hashed_payload(predecessor_payload))

    if result["hash_kind"] != "rfc8785_jcs_sha256":
        raise ContractViolation("semantic retrieval policy hash_kind mismatch")
    return result


__all__ = [
    "POLICY_SCHEMA_VERSION",
    "TERMINAL_RECEIPT_SCHEMA",
    "canonical_artifact_file_bytes",
    "canonical_artifact_raw_sha256",
    "validate_semantic_profile_terminal_receipt",
    "validate_semantic_retrieval_policy_snapshot",
]
