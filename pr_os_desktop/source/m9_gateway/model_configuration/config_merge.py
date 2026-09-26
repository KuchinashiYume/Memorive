from __future__ import annotations

from typing import Any, Mapping

from m9_gateway.execution_core.contracts import canonical_sha256, immutable_copy

from .contracts import ModelConfigurationError, TIER_IDS, _https_origin, _identifier, _object, _text, _timestamp


ORDINARY_FIELDS = {
    "schema_version",
    "revision",
    "provider_preset_id",
    "credential_binding_id",
    "user_tier",
    "thinking_enabled",
    "updated_at",
}
ADVANCED_FIELDS = {
    "schema_version",
    "revision",
    "exact_model_overrides",
    "custom_endpoint_overrides",
    "role_mapping",
    "fallback_profile_ids",
    "budget",
    "per_role_thinking",
    "updated_at",
}
FORBIDDEN_KEYS = {"api_key", "secret", "password", "token", "authorization", "cookie", "credential_ref"}


def _walk_forbidden(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in FORBIDDEN_KEYS or normalized.endswith("_secret") or normalized.endswith("_token"):
                raise ModelConfigurationError("SECRET_VALUE_FIELD_FORBIDDEN", [f"{path}.{key}"])
            _walk_forbidden(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_forbidden(item, f"{path}[{index}]")


def validate_ordinary_config(value: Mapping[str, Any]) -> dict[str, Any]:
    accepted = _object(value, ORDINARY_FIELDS, "ORDINARY_CONFIG")
    if accepted["schema_version"] != "P07_T08_ORDINARY_CONFIG_V1":
        raise ModelConfigurationError("ORDINARY_CONFIG_SCHEMA_VERSION")
    for field in ("revision", "provider_preset_id", "credential_binding_id"):
        accepted[field] = _identifier(accepted[field], field)
    if accepted["user_tier"] not in TIER_IDS:
        raise ModelConfigurationError("TIER_PROFILE_UNAVAILABLE")
    if not isinstance(accepted["thinking_enabled"], bool):
        raise ModelConfigurationError("THINKING_TOGGLE_INVALID")
    _timestamp(accepted["updated_at"], "updated_at")
    return accepted


def validate_advanced_config(value: Mapping[str, Any]) -> dict[str, Any]:
    _walk_forbidden(value)
    accepted = _object(value, ADVANCED_FIELDS, "ADVANCED_CONFIG")
    if accepted["schema_version"] != "P07_T08_ADVANCED_CONFIG_BOUNDARY_V1":
        raise ModelConfigurationError("ADVANCED_CONFIG_SCHEMA_VERSION")
    accepted["revision"] = _identifier(accepted["revision"], "revision")
    _timestamp(accepted["updated_at"], "updated_at")
    for field in ("exact_model_overrides", "role_mapping"):
        if not isinstance(accepted[field], dict):
            raise ModelConfigurationError("ADVANCED_MAPPING_INVALID", [field])
        for key, item in accepted[field].items():
            _identifier(key, field)
            _identifier(item, field)
    endpoints = accepted["custom_endpoint_overrides"]
    if not isinstance(endpoints, dict):
        raise ModelConfigurationError("ADVANCED_MAPPING_INVALID", ["custom_endpoint_overrides"])
    accepted["custom_endpoint_overrides"] = {
        _identifier(key, "provider_id"): _https_origin(item, "custom_endpoint")
        for key, item in endpoints.items()
    }
    fallback = accepted["fallback_profile_ids"]
    if not isinstance(fallback, dict):
        raise ModelConfigurationError("ADVANCED_MAPPING_INVALID", ["fallback_profile_ids"])
    for role, values in fallback.items():
        _identifier(role, "fallback_role")
        if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
            raise ModelConfigurationError("ADVANCED_FALLBACK_INVALID", [role])
    if not isinstance(accepted["budget"], dict):
        raise ModelConfigurationError("ADVANCED_BUDGET_INVALID")
    allowed_budget = {"currency", "soft_limit", "hard_limit"}
    if set(accepted["budget"]) != allowed_budget:
        raise ModelConfigurationError("ADVANCED_BUDGET_FIELDS_INVALID")
    _text(accepted["budget"]["currency"], "currency")
    for field in ("soft_limit", "hard_limit"):
        item = accepted["budget"][field]
        if isinstance(item, bool) or not isinstance(item, (int, float)) or item < 0:
            raise ModelConfigurationError("ADVANCED_BUDGET_VALUE_INVALID", [field])
    if accepted["budget"]["soft_limit"] > accepted["budget"]["hard_limit"]:
        raise ModelConfigurationError("ADVANCED_BUDGET_ORDER_INVALID")
    thinking = accepted["per_role_thinking"]
    if not isinstance(thinking, dict) or any(not isinstance(item, bool) for item in thinking.values()):
        raise ModelConfigurationError("ADVANCED_THINKING_OVERRIDE_INVALID")
    return accepted


def merge_configuration(ordinary: Mapping[str, Any], advanced: Mapping[str, Any]) -> dict[str, Any]:
    base = validate_ordinary_config(ordinary)
    overlay = validate_advanced_config(advanced)
    declared_roles = set(overlay["role_mapping"])
    referenced_roles = (
        set(overlay["exact_model_overrides"])
        | set(overlay["fallback_profile_ids"])
        | set(overlay["per_role_thinking"])
    )
    if not referenced_roles.issubset(declared_roles):
        raise ModelConfigurationError(
            "CONFIG_OVERLAY_CONFLICT", sorted(referenced_roles - declared_roles)
        )
    effective = {
        "provider_preset_id": base["provider_preset_id"],
        "credential_binding_id": base["credential_binding_id"],
        "user_tier": base["user_tier"],
        "thinking_enabled": base["thinking_enabled"],
        "exact_model_overrides": overlay["exact_model_overrides"],
        "custom_endpoint_overrides": overlay["custom_endpoint_overrides"],
        "role_mapping": overlay["role_mapping"],
        "fallback_profile_ids": overlay["fallback_profile_ids"],
        "budget": overlay["budget"],
        "per_role_thinking": overlay["per_role_thinking"],
    }
    diff = [
        {"path": f"advanced.{key}", "value": immutable_copy(value)}
        for key, value in sorted(overlay.items())
        if key not in {"schema_version", "revision", "updated_at"} and value not in ({}, [])
    ]
    payload = {
        "schema_version": "P07_T08_MODEL_CONFIG_SNAPSHOT_V1",
        "ordinary_revision": base["revision"],
        "advanced_revision": overlay["revision"],
        "effective": effective,
        "deterministic_diff": diff,
    }
    payload["snapshot_sha256"] = canonical_sha256(payload)
    return payload


__all__ = ["merge_configuration", "validate_advanced_config", "validate_ordinary_config"]
