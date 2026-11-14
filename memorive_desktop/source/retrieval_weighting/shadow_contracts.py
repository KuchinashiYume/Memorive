"""Fail-closed shared contracts for the SEMANTIC-AGGREGATION structural Shadow candidate.

These validators intentionally accept only deterministic JSON-domain payloads.
Every object has an exact key set, carries the accepted Initialization RFC 8785 typed
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
_OVERRIDE_EVENT_RE = re.compile(r"^(?:KNOWLEDGE_ADMISSION|RUNTIME_LOG|DECISION_LOG|ARTIFACT_REGISTRY):[^\r\n]+$")
_Architecture_WEIGHTS = {
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
) -> list[str]:
    """Validate a duplicate-free deterministic list of exact references."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ContractViolation(f"{field_name} must be an array")
    result = [require_non_empty_string(item, f"{field_name}[]") for item in value]
    if not allow_empty and not result:
        raise ContractViolation(f"{field_name} must not be empty")
    if len(result) != len(set(result)):
        raise ContractViolation(f"{field_name} must not contain duplicates")
    if result != sorted(result):
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


__all__ = [
    "reject_scorer_or_oracle_fields",
    "require_exact_keys",
    "require_finite_number",
    "require_string_list",
]
