"""Deterministic QueryDimensionProfile construction for P03/T04."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .contracts import (
    make_hashed_payload,
    require_non_empty_string,
    validate_hash_descriptor,
    verify_hashed_payload,
)
from .errors import ContractViolation
from .shadow_contracts import (
    reject_scorer_or_oracle_fields,
    require_exact_keys,
    require_finite_number,
    require_string_list,
)


_PROFILE_KEYS = {
    "query_hash",
    "field_registry_hash",
    "allocations",
    "allocation_method",
    "fallback_floor",
    "missing_fields",
    "reason_codes",
    "policy_version",
    "content_hash",
}
_ALLOCATION_KEYS = {"field_id", "allocation", "reason_code", "evidence_ref"}


def _validate_allocations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ContractViolation("query_dimension_profile.allocations must be an array")
    result: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ContractViolation(f"allocations[{ordinal}] must be an object")
        allocation = deepcopy(dict(raw))
        require_exact_keys(allocation, _ALLOCATION_KEYS, f"allocations[{ordinal}]")
        require_non_empty_string(allocation["field_id"], f"allocations[{ordinal}].field_id")
        require_finite_number(
            allocation["allocation"],
            f"allocations[{ordinal}].allocation",
            minimum=0.0,
            maximum=1.0,
        )
        require_non_empty_string(
            allocation["reason_code"], f"allocations[{ordinal}].reason_code"
        )
        require_non_empty_string(
            allocation["evidence_ref"], f"allocations[{ordinal}].evidence_ref"
        )
        result.append(allocation)
    field_ids = [item["field_id"] for item in result]
    if len(field_ids) != len(set(field_ids)):
        raise ContractViolation("query_dimension_profile.allocations has duplicate field_id")
    if field_ids != sorted(field_ids):
        raise ContractViolation("query_dimension_profile.allocations must sort by field_id")
    total = sum(float(item["allocation"]) for item in result)
    expected = 1.0 if result else 0.0
    if not math.isclose(total, expected, rel_tol=0.0, abs_tol=1e-12):
        raise ContractViolation(
            "query_dimension_profile allocations must sum deterministically",
            context={"expected": expected, "observed": total},
        )
    return result


def _registry_templates(frozen_registry: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    registry = verify_hashed_payload(frozen_registry, "frozen_field_registry")
    fields = registry.get("fields")
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)) or not fields:
        raise ContractViolation("frozen_field_registry.fields must be a non-empty array")
    templates: list[str] = []
    for ordinal, field in enumerate(fields):
        if not isinstance(field, Mapping):
            raise ContractViolation(f"frozen_field_registry.fields[{ordinal}] must be an object")
        template = require_non_empty_string(
            field.get("field_path_template"),
            f"frozen_field_registry.fields[{ordinal}].field_path_template",
        )
        templates.append(template)
    if len(templates) != len(set(templates)):
        raise ContractViolation("frozen_field_registry has duplicate field templates")
    return registry, sorted(templates)


def _matches_registry_template(field_id: str, templates: Sequence[str]) -> bool:
    import re

    for template in templates:
        if field_id == template:
            return True
        pattern = "^" + re.escape(template).replace(r"\{item\}", r"[^/{}]+") + "$"
        if re.fullmatch(pattern, field_id):
            return True
    return False


def validate_query_dimension_profile(
    profile: Mapping[str, Any],
    *,
    frozen_registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and copy one exact QueryDimensionProfile."""

    if not isinstance(profile, Mapping):
        raise ContractViolation("query_dimension_profile must be an object")
    result = deepcopy(dict(profile))
    reject_scorer_or_oracle_fields(result, "query_dimension_profile")
    require_exact_keys(result, _PROFILE_KEYS, "query_dimension_profile")
    validate_hash_descriptor(result["query_hash"], "query_dimension_profile.query_hash")
    validate_hash_descriptor(
        result["field_registry_hash"], "query_dimension_profile.field_registry_hash"
    )
    allocations = _validate_allocations(result["allocations"])
    require_non_empty_string(
        result["allocation_method"], "query_dimension_profile.allocation_method"
    )
    floor = require_finite_number(
        result["fallback_floor"],
        "query_dimension_profile.fallback_floor",
        minimum=0.0,
        maximum=1.0,
    )
    missing_fields = require_string_list(
        result["missing_fields"],
        "query_dimension_profile.missing_fields",
        allow_empty=True,
    )
    reason_codes = require_string_list(
        result["reason_codes"],
        "query_dimension_profile.reason_codes",
        allow_empty=not allocations,
    )
    observed_reasons = {item["reason_code"] for item in allocations}
    if not observed_reasons.issubset(set(reason_codes)):
        raise ContractViolation("query_dimension_profile.reason_codes omits an allocation reason")
    allocated = {item["field_id"] for item in allocations}
    if allocated.intersection(missing_fields):
        raise ContractViolation("one field cannot be both allocated and missing")
    if not allocations and not missing_fields:
        raise ContractViolation("empty allocation requires explicit missing_fields")
    if allocations and floor > min(float(item["allocation"]) for item in allocations):
        raise ContractViolation("fallback_floor exceeds an observed allocation")
    registry, registry_templates = _registry_templates(frozen_registry)
    if result["field_registry_hash"] != registry["content_hash"]:
        raise ContractViolation("query_dimension_profile field_registry_hash drift")
    for field_id in sorted(allocated.union(missing_fields)):
        if not _matches_registry_template(field_id, registry_templates):
            raise ContractViolation(
                "query_dimension_profile contains an unknown registry field",
                context={"field_id": field_id},
            )
    require_non_empty_string(result["policy_version"], "query_dimension_profile.policy_version")
    validate_hash_descriptor(result["content_hash"], "query_dimension_profile.content_hash")
    return verify_hashed_payload(result, "query_dimension_profile")


def build_query_dimension_profile(
    *,
    query_hash: Mapping[str, Any],
    field_registry_hash: Mapping[str, Any],
    allocations: Sequence[Mapping[str, Any]],
    allocation_method: str,
    fallback_floor: float,
    missing_fields: Sequence[str],
    reason_codes: Sequence[str],
    policy_version: str,
    frozen_registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a canonical profile and bind it with the accepted typed JCS hash."""

    payload = {
        "query_hash": deepcopy(dict(query_hash)),
        "field_registry_hash": deepcopy(dict(field_registry_hash)),
        "allocations": [deepcopy(dict(item)) for item in allocations],
        "allocation_method": allocation_method,
        "fallback_floor": fallback_floor,
        "missing_fields": list(missing_fields),
        "reason_codes": list(reason_codes),
        "policy_version": policy_version,
    }
    return validate_query_dimension_profile(
        make_hashed_payload(payload),
        frozen_registry=frozen_registry,
    )


__all__ = ["build_query_dimension_profile", "validate_query_dimension_profile"]
