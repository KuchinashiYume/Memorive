"""Public-safe synthetic fixtures for P07/T08 offline construction and B."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from m9_gateway.execution_core.contracts import canonical_sha256

from .credential import InMemoryFakeCredentialStore


OBSERVED_AT = "2026-08-01T00:00:00+00:00"
EXPIRES_AT = "2026-12-31T00:00:00+00:00"
RESOLVE_AT = "2026-08-22T00:00:00+00:00"


def provider_preset() -> dict[str, Any]:
    return {
        "schema_version": "P07_T08_PROVIDER_PRESET_V1",
        "provider_id": "provider_fake",
        "revision": "r1",
        "display_name": "Synthetic Provider",
        "protocol_family": "LOCAL_IN_PROCESS_FAKE",
        "endpoint_policy": "PRESET_ONLY",
        "preset_origin": "https://fake.invalid",
        "credential_backend_type": "FAKE_IN_MEMORY",
        "model_discovery_policy": "PINNED_PROFILE_ONLY",
        "thinking_mapping_revision": "r1",
        "price_metadata_source": "synthetic-fixed-fixture",
        "price_metadata_observed_at": OBSERVED_AT,
        "price_metadata_expires_at": EXPIRES_AT,
        "allowed_data_classes": ["public", "self"],
        "allowed_regions": ["synthetic"],
        "egress_class": "external",
        "supported_roles": ["analysis_primary"],
        "supported_capabilities": ["analysis"],
        "connection_check_method": "IN_PROCESS_FAKE",
        "default_enabled": False,
        "known_limits": ["synthetic-only", "real-provider-not-assessed"],
    }


def _profile(
    profile_id: str,
    model_alias: str,
    efforts: list[str],
    default_effort: str,
    *,
    price_input: float,
    price_output: float,
) -> dict[str, Any]:
    projections = {effort: {"thinking": "enabled", "effort": effort} for effort in efforts}
    projections["disabled"] = {"thinking": "disabled"}
    return {
        "schema_version": "P07_T08_MODEL_PROFILE_CAPABILITY_V1",
        "profile_id": profile_id,
        "revision": "r1",
        "provider_id": "provider_fake",
        "role": "analysis_primary",
        "model_alias": model_alias,
        "required_capability": "analysis",
        "qualification_status": "QUALIFIED_DISABLED",
        "behavior_hash": canonical_sha256({"profile": profile_id, "model": model_alias}),
        "thinking_supported": True,
        "thinking_can_be_disabled": True,
        "supported_native_efforts": efforts,
        "native_effort_order": efforts,
        "native_default_effort": default_effort,
        "native_effort_projections": projections,
        "price_status": "KNOWN",
        "price_currency": "CNY",
        "price_unit": "per_million_tokens",
        "price_input": price_input,
        "price_output": price_output,
        "price_source": "synthetic-fixed-fixture",
        "price_observed_at": OBSERVED_AT,
        "price_expires_at": EXPIRES_AT,
        "latency_class": "synthetic",
        "resource_class": "synthetic",
        "capability_source": "p07-t08-synthetic-contract-test",
        "capability_observed_at": OBSERVED_AT,
        "capability_expires_at": EXPIRES_AT,
        "allowed_data_classes": ["public", "self"],
        "region": "synthetic",
        "egress_class": "external",
        "credential_binding_id": "binding_main",
    }


def model_profiles() -> list[dict[str, Any]]:
    return [
        _profile("analysis_economy_v1", "model-fast", ["low", "medium"], "low", price_input=0.1, price_output=0.2),
        _profile("analysis_standard_v1", "model-balanced", ["low", "medium", "high"], "medium", price_input=0.5, price_output=1.0),
        _profile("analysis_high_quality_v1", "model-strong", ["high", "xhigh"], "high", price_input=2.0, price_output=4.0),
        _profile("analysis_maximum_v1", "model-strong-max", ["high", "xhigh", "max"], "xhigh", price_input=3.0, price_output=6.0),
    ]


def _entry(profile_id: str | None, status: str, on: str | None, *, maximum: bool = False) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "resolution_status": status,
        "thinking_on_native_effort": on,
        "thinking_off_native_effort": "disabled" if profile_id is not None else None,
        "collapse_target": None,
        "collapse_reason": None,
        "reopen_condition": None,
        "requires_explicit_selection": maximum,
        "fallback_profile_ids": [],
        "fallback_requires_user_confirmation": False,
    }


def tier_policy() -> dict[str, Any]:
    return {
        "schema_version": "P07_T08_TIER_POLICY_MANIFEST_V1",
        "revision": "r1",
        "observed_at": OBSERVED_AT,
        "expires_at": EXPIRES_AT,
        "roles": {
            "analysis_primary": {
                "economy": _entry("analysis_economy_v1", "RESOLVED_UNIQUE", "low"),
                "standard": _entry("analysis_standard_v1", "RESOLVED_UNIQUE", "medium"),
                "high_quality": _entry("analysis_high_quality_v1", "RESOLVED_UNIQUE", "high"),
                "maximum": _entry("analysis_maximum_v1", "RESOLVED_UNIQUE", "max", maximum=True),
            }
        },
    }


def unavailable_policy() -> dict[str, Any]:
    value = deepcopy(tier_policy())
    value["roles"]["analysis_primary"]["high_quality"] = _entry(None, "UNAVAILABLE", None)
    return value


def disclosed_collapse_policy() -> dict[str, Any]:
    value = deepcopy(tier_policy())
    entry = _entry("analysis_standard_v1", "COLLAPSED_DISCLOSED", "high")
    entry["collapse_target"] = "standard"
    entry["collapse_reason"] = "synthetic provider has no distinct high-quality profile"
    entry["reopen_condition"] = "a separately qualified profile becomes available"
    value["roles"]["analysis_primary"]["high_quality"] = entry
    return value


def verified_credential() -> tuple[InMemoryFakeCredentialStore, dict[str, Any]]:
    store = InMemoryFakeCredentialStore()
    binding = store.save(
        binding_id="binding_main",
        provider_id="provider_fake",
        secret_value="fixture-value-never-sent",
        account_label="Synthetic account",
        created_at=OBSERVED_AT,
    )
    binding = store.mark_connection(binding["credential_ref"], verified=True)
    return store, binding


def ordinary_config() -> dict[str, Any]:
    return {
        "schema_version": "P07_T08_ORDINARY_CONFIG_V1",
        "revision": "r1",
        "provider_preset_id": "provider_fake",
        "credential_binding_id": "binding_main",
        "user_tier": "economy",
        "thinking_enabled": True,
        "updated_at": RESOLVE_AT,
    }


def advanced_config() -> dict[str, Any]:
    return {
        "schema_version": "P07_T08_ADVANCED_CONFIG_BOUNDARY_V1",
        "revision": "r1",
        "exact_model_overrides": {"analysis_primary": "analysis_standard_v1"},
        "custom_endpoint_overrides": {},
        "role_mapping": {"analysis_primary": "analysis_primary"},
        "fallback_profile_ids": {"analysis_primary": []},
        "budget": {"currency": "CNY", "soft_limit": 1.0, "hard_limit": 2.0},
        "per_role_thinking": {"analysis_primary": True},
        "updated_at": RESOLVE_AT,
    }


__all__ = [
    "EXPIRES_AT",
    "OBSERVED_AT",
    "RESOLVE_AT",
    "advanced_config",
    "disclosed_collapse_policy",
    "model_profiles",
    "ordinary_config",
    "provider_preset",
    "tier_policy",
    "unavailable_policy",
    "verified_credential",
]
