from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from m9_gateway.execution_core.contracts import canonical_sha256, immutable_copy

from .contracts import (
    ModelConfigurationError,
    TIER_IDS,
    validate_credential_binding,
    validate_model_profile,
    validate_provider_preset,
    validate_tier_policy,
)


def _at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ModelConfigurationError("RESOLUTION_TIME_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ModelConfigurationError("RESOLUTION_TIME_OFFSET_REQUIRED")
    return parsed


class TierResolver:
    def __init__(
        self,
        *,
        policy: Mapping[str, Any],
        profiles: Sequence[Mapping[str, Any]],
        provider_presets: Sequence[Mapping[str, Any]],
        credential_bindings: Sequence[Mapping[str, Any]],
        resolver_revision: str,
    ) -> None:
        self._profiles = self._unique(profiles, validate_model_profile, "profile_id", "MODEL_PROFILE_DUPLICATE")
        self._presets = self._unique(provider_presets, validate_provider_preset, "provider_id", "PROVIDER_PRESET_DUPLICATE")
        self._bindings = self._unique(credential_bindings, validate_credential_binding, "binding_id", "CREDENTIAL_BINDING_DUPLICATE")
        self._policy = validate_tier_policy(policy, list(self._profiles.values()))
        self.resolver_revision = resolver_revision

    @staticmethod
    def _unique(values, validator, key, code):
        result = {}
        for value in values:
            accepted = validator(value)
            identity = accepted[key]
            if identity in result:
                raise ModelConfigurationError(code, [identity])
            result[identity] = accepted
        return result

    def resolve(
        self,
        *,
        role: str,
        requested_user_tier: str,
        thinking_enabled: bool,
        data_classification: str,
        explicit_selection: bool,
        at: str,
    ) -> dict[str, Any]:
        instant = _at(at)
        if requested_user_tier not in TIER_IDS:
            raise ModelConfigurationError("TIER_PROFILE_UNAVAILABLE", [requested_user_tier])
        role_policy = self._policy["roles"].get(role)
        if role_policy is None:
            raise ModelConfigurationError("ROLE_MAPPING_UNRESOLVED", [role])
        if instant >= _at(self._policy["expires_at"]):
            raise ModelConfigurationError("TIER_POLICY_STALE")
        entry = role_policy[requested_user_tier]
        if requested_user_tier == "maximum" and not explicit_selection:
            raise ModelConfigurationError("MAXIMUM_REQUIRES_EXPLICIT_SELECTION")
        if entry["resolution_status"] == "UNAVAILABLE":
            raise ModelConfigurationError("TIER_PROFILE_UNAVAILABLE", [role, requested_user_tier])
        profile = self._profiles[entry["profile_id"]]
        preset = self._presets.get(profile["provider_id"])
        binding = self._bindings.get(profile["credential_binding_id"])
        if preset is None:
            raise ModelConfigurationError("PROVIDER_PRESET_UNKNOWN", [profile["provider_id"]])
        if binding is None:
            raise ModelConfigurationError("CREDENTIAL_REF_MISSING", [profile["credential_binding_id"]])
        if role not in preset["supported_roles"] or profile["required_capability"] not in preset["supported_capabilities"]:
            raise ModelConfigurationError("PROVIDER_PRESET_CAPABILITY_MISMATCH")
        if profile["qualification_status"] != "QUALIFIED_DISABLED":
            raise ModelConfigurationError("QUALIFICATION_NOT_ASSESSED")
        if binding["provider_id"] != profile["provider_id"]:
            raise ModelConfigurationError("CREDENTIAL_PROVIDER_MISMATCH")
        if binding["state"] == "REVOKED":
            raise ModelConfigurationError("CREDENTIAL_REVOKED")
        if binding["state"] != "CONNECTION_VERIFIED":
            raise ModelConfigurationError("CONNECTION_NOT_VERIFIED")
        if data_classification not in profile["allowed_data_classes"] or data_classification not in preset["allowed_data_classes"]:
            raise ModelConfigurationError("DATA_BOUNDARY_BLOCKS_ROUTE")
        if instant >= _at(profile["capability_expires_at"]):
            raise ModelConfigurationError("NATIVE_EFFORT_CAPABILITY_STALE")
        if requested_user_tier == "economy":
            if profile["price_status"] != "KNOWN" or instant >= _at(profile["price_expires_at"]):
                raise ModelConfigurationError("TIER_PRICE_UNKNOWN_NOT_ECONOMY")
        thinking_status: str
        native_effort: str | None
        native_projection: dict[str, Any] | None
        if thinking_enabled:
            if not profile["thinking_supported"]:
                raise ModelConfigurationError("THINKING_UNSUPPORTED")
            native_effort = entry["thinking_on_native_effort"]
            thinking_status = "RESOLVED"
            native_projection = immutable_copy(profile["native_effort_projections"][native_effort])
        elif not profile["thinking_supported"]:
            native_effort = None
            thinking_status = "NOT_APPLICABLE"
            native_projection = immutable_copy(profile["native_effort_projections"]["not_applicable"])
        elif not profile["thinking_can_be_disabled"]:
            raise ModelConfigurationError("THINKING_OFF_UNSUPPORTED")
        else:
            native_effort = "disabled"
            thinking_status = "RESOLVED"
            native_projection = immutable_copy(profile["native_effort_projections"]["disabled"])
        disclosure = None
        if entry["resolution_status"] == "COLLAPSED_DISCLOSED":
            disclosure = {
                "collapse_target": entry["collapse_target"],
                "reason": entry["collapse_reason"],
                "reopen_condition": entry["reopen_condition"],
            }
        payload = {
            "schema_version": "P07_T08_TIER_RESOLUTION_V1",
            "resolver_revision": self.resolver_revision,
            "policy_revision": self._policy["revision"],
            "role": role,
            "requested_user_tier": requested_user_tier,
            "requested_thinking_enabled": thinking_enabled,
            "explicit_selection": explicit_selection,
            "resolution_status": entry["resolution_status"],
            "profile_id": profile["profile_id"],
            "provider_id": profile["provider_id"],
            "model_alias": profile["model_alias"],
            "credential_ref": binding["credential_ref"],
            "thinking_status": thinking_status,
            "native_effort": native_effort,
            "native_projection": native_projection,
            "data_classification": data_classification,
            "egress_class": profile["egress_class"],
            "region": profile["region"],
            "fallback_used": False,
            "fallback_requires_user_confirmation": bool(entry["fallback_profile_ids"]),
            "collapse_disclosure": disclosure,
            "resolved_at": at,
        }
        payload["resolution_sha256"] = canonical_sha256(payload)
        return payload


__all__ = ["TierResolver"]
