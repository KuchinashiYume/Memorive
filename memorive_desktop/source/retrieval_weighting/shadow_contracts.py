"""Checks shared by the structural shadow ranking steps of retrieval_weighting.

The ranking steps (aggregation, candidate_union, counterevidence, derived_penalty) all take a
retrieval policy snapshot. validate_retrieval_policy_snapshot() returns a copy of it after checking
the parts that a step reads without checking them itself: the provider tables, the channel caps
and the snapshot's content_hash. Each step checks the numbers it uses where it uses them, and
derived_penalty checks the derived-penalty policy. Other fields of a snapshot are carried along.

The require_* helpers and reject_scorer_or_oracle_fields() are used by the other modules of the
package.

Switches such as activation_authorized or production_writes_allowed in a snapshot are declarations
only: nothing here acts on them. If the shadow ranking is ever wired into the product, the check
belongs where the product decides to use its result.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .contracts import require_non_empty_string, verify_hashed_payload
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


def validate_derived_penalty_policy(value: Any) -> dict[str, Any]:
    """A copy of a derived-penalty policy, with the fields that derived_penalty.py reads checked."""

    result = _mapping(value, "derived_penalty_policy")
    require_non_empty_string(result.get("policy_version"), "derived_penalty_policy.policy_version")
    penalties = _mapping(result.get("type_penalties"), "derived_penalty_policy.type_penalties")
    if not penalties:
        raise ContractViolation("derived_penalty_policy.type_penalties must not be empty")
    for name, penalty in penalties.items():
        require_finite_number(penalty, f"derived_penalty_policy.type_penalties.{name}", minimum=0.0, maximum=1.0)
    allowlist = result.get("override_event_allowlist")
    if not isinstance(allowlist, Sequence) or isinstance(allowlist, (str, bytes)):
        raise ContractViolation("derived_penalty_policy.override_event_allowlist must be an array")
    for ordinal, item in enumerate(allowlist):
        entry = _mapping(item, f"derived_penalty_policy.override_event_allowlist[{ordinal}]")
        require_non_empty_string(entry.get("event_locator"),
                                 f"derived_penalty_policy.override_event_allowlist[{ordinal}].event_locator")
        if "event_hash" not in entry:
            raise ContractViolation(f"derived_penalty_policy.override_event_allowlist[{ordinal}].event_hash is required")
    return result


def validate_retrieval_policy_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of a retrieval policy snapshot, after checking what the steps read without checking it
    themselves: a version, a normalization profile and a weight for every provider, the channel caps,
    the authority mass cap, the derived-penalty policy (an object), and the content_hash."""
    from .providers import PROVIDERS  # imported here: providers.py imports this module

    result = _mapping(snapshot, "retrieval_policy_snapshot")
    providers = _mapping(result.get("provider_policy"), "provider_policy")
    for table in ("provider_versions", "normalization_profiles", "weights"):
        entries = _mapping(providers.get(table), f"provider_policy.{table}")
        missing = [provider for provider in PROVIDERS if provider not in entries]
        if missing:
            raise ContractViolation(f"provider_policy.{table} has no entry for " + ", ".join(missing))
    aggregation = _mapping(result.get("aggregation_policy"), "aggregation_policy")
    channel_caps = _mapping(aggregation.get("channel_caps"), "aggregation_policy.channel_caps")
    for channel, cap in channel_caps.items():
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            raise ContractViolation("aggregation_policy channel caps must be positive integers")
    if "authority_mass_cap" not in aggregation:
        raise ContractViolation("aggregation_policy.authority_mass_cap is required")
    _mapping(result.get("derived_penalty_policy"), "derived_penalty_policy")
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
