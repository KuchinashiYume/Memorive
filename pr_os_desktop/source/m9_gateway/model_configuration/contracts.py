from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from m9_gateway.execution_core.contracts import canonical_sha256, immutable_copy


TIER_IDS = ("economy", "standard", "high_quality", "maximum")
RESOLUTION_STATUSES = {"RESOLVED_UNIQUE", "UNAVAILABLE", "COLLAPSED_DISCLOSED"}
CREDENTIAL_STATES = {
    "UNBOUND",
    "STORED_UNVERIFIED",
    "CONNECTION_VERIFIED",
    "CONNECTION_FAILED",
    "VERIFICATION_NOT_RUN",
    "REPLACED",
    "REVOKED",
}
QUALIFICATION_STATES = {"QUALIFIED_DISABLED", "NOT_ASSESSED", "BLOCKED"}
CONNECTION_STATES = {"CONNECTION_VERIFIED", "CONNECTION_FAILED", "VERIFICATION_NOT_RUN"}
KEY_STATES = {"KEY_SAVED", "KEY_MISSING", "KEY_REVOKED"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
REF_RE = re.compile(r"^REF:FAKE:[A-Za-z0-9._-]+:v[1-9][0-9]*$")
SHA_RE = re.compile(r"^[0-9A-F]{64}$")


class ModelConfigurationError(ValueError):
    def __init__(self, code: str, details: Sequence[str] | None = None):
        self.code = code
        self.details = tuple(details or ())
        suffix = f": {'; '.join(self.details)}" if self.details else ""
        super().__init__(f"{code}{suffix}")


def _object(value: Mapping[str, Any], fields: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelConfigurationError(f"{code}_NOT_OBJECT")
    missing = sorted(fields - set(value))
    extra = sorted(set(value) - fields)
    if missing or extra:
        raise ModelConfigurationError(
            f"{code}_FIELDS_INVALID", [f"missing={missing}", f"extra={extra}"]
        )
    return immutable_copy(value)


def _text(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ModelConfigurationError("FIELD_INVALID", [field])
    return value.strip()


def _identifier(value: Any, field: str) -> str:
    accepted = _text(value, field)
    assert accepted is not None
    if not ID_RE.fullmatch(accepted):
        raise ModelConfigurationError("IDENTIFIER_INVALID", [field])
    return accepted


def _timestamp(value: Any, field: str) -> datetime:
    accepted = _text(value, field)
    assert accepted is not None
    try:
        parsed = datetime.fromisoformat(accepted.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ModelConfigurationError("TIMESTAMP_INVALID", [field]) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ModelConfigurationError("TIMESTAMP_OFFSET_REQUIRED", [field])
    return parsed


def _sha(value: Any, field: str) -> str:
    accepted = _text(value, field)
    assert accepted is not None
    accepted = accepted.upper()
    if not SHA_RE.fullmatch(accepted):
        raise ModelConfigurationError("SHA256_INVALID", [field])
    return accepted


def _strings(value: Any, field: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ModelConfigurationError("STRING_LIST_INVALID", [field])
    result = [_text(item, field) for item in value]
    if len(set(result)) != len(result):
        raise ModelConfigurationError("STRING_LIST_DUPLICATE", [field])
    return [item for item in result if item is not None]


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ModelConfigurationError("BOOLEAN_INVALID", [field])
    return value


def _https_origin(value: Any, field: str) -> str:
    accepted = _text(value, field)
    assert accepted is not None
    parsed = urlsplit(accepted)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ModelConfigurationError("CUSTOM_ENDPOINT_ORIGIN_INVALID", [field])
    return accepted.rstrip("/")


CREDENTIAL_FIELDS = {
    "schema_version",
    "binding_id",
    "revision",
    "provider_id",
    "credential_ref",
    "account_label",
    "state",
    "backend_type",
    "secret_present",
    "created_at",
    "rotated_at",
    "revoked_at",
    "previous_credential_ref",
}


def validate_credential_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    accepted = _object(value, CREDENTIAL_FIELDS, "CREDENTIAL_BINDING")
    if accepted["schema_version"] != "P07_T08_CREDENTIAL_BINDING_V1":
        raise ModelConfigurationError("CREDENTIAL_BINDING_SCHEMA_VERSION")
    for field in ("binding_id", "revision", "provider_id"):
        accepted[field] = _identifier(accepted[field], field)
    ref = _text(accepted["credential_ref"], "credential_ref")
    assert ref is not None
    if not REF_RE.fullmatch(ref):
        raise ModelConfigurationError("CREDENTIAL_REF_INVALID")
    accepted["credential_ref"] = ref
    accepted["account_label"] = _text(accepted["account_label"], "account_label", optional=True)
    if accepted["state"] not in CREDENTIAL_STATES - {"UNBOUND"}:
        raise ModelConfigurationError("CREDENTIAL_STATE_INVALID")
    if accepted["backend_type"] != "FAKE_IN_MEMORY":
        raise ModelConfigurationError("REAL_CREDENTIAL_BACKEND_NOT_AUTHORIZED")
    _bool(accepted["secret_present"], "secret_present")
    _timestamp(accepted["created_at"], "created_at")
    for field in ("rotated_at", "revoked_at"):
        if accepted[field] is not None:
            _timestamp(accepted[field], field)
    previous = accepted["previous_credential_ref"]
    if previous is not None and not REF_RE.fullmatch(_text(previous, "previous_credential_ref") or ""):
        raise ModelConfigurationError("PREVIOUS_CREDENTIAL_REF_INVALID")
    if accepted["state"] in {"REPLACED", "REVOKED"} and accepted["secret_present"]:
        raise ModelConfigurationError("INACTIVE_CREDENTIAL_SECRET_PRESENT")
    return accepted


PROVIDER_FIELDS = {
    "schema_version",
    "provider_id",
    "revision",
    "display_name",
    "protocol_family",
    "endpoint_policy",
    "preset_origin",
    "credential_backend_type",
    "model_discovery_policy",
    "thinking_mapping_revision",
    "price_metadata_source",
    "price_metadata_observed_at",
    "price_metadata_expires_at",
    "allowed_data_classes",
    "allowed_regions",
    "egress_class",
    "supported_roles",
    "supported_capabilities",
    "connection_check_method",
    "default_enabled",
    "known_limits",
}


def validate_provider_preset(value: Mapping[str, Any]) -> dict[str, Any]:
    accepted = _object(value, PROVIDER_FIELDS, "PROVIDER_PRESET")
    if accepted["schema_version"] != "P07_T08_PROVIDER_PRESET_V1":
        raise ModelConfigurationError("PROVIDER_PRESET_SCHEMA_VERSION")
    for field in ("provider_id", "revision", "thinking_mapping_revision"):
        accepted[field] = _identifier(accepted[field], field)
    accepted["display_name"] = _text(accepted["display_name"], "display_name")
    if accepted["protocol_family"] not in {
        "OPENAI_CHAT_COMPLETIONS",
        "OPENAI_RESPONSES",
        "ANTHROPIC_MESSAGES",
        "GEMINI_GENERATE_CONTENT",
        "LOCAL_IN_PROCESS_FAKE",
    }:
        raise ModelConfigurationError("PROTOCOL_FAMILY_INVALID")
    if accepted["endpoint_policy"] not in {"PRESET_ONLY", "CUSTOM_ADVANCED_ONLY"}:
        raise ModelConfigurationError("ENDPOINT_POLICY_INVALID")
    accepted["preset_origin"] = _https_origin(accepted["preset_origin"], "preset_origin")
    if accepted["credential_backend_type"] != "FAKE_IN_MEMORY":
        raise ModelConfigurationError("REAL_CREDENTIAL_BACKEND_NOT_AUTHORIZED")
    if accepted["model_discovery_policy"] != "PINNED_PROFILE_ONLY":
        raise ModelConfigurationError("UNTRUSTED_MODEL_DISCOVERY_FORBIDDEN")
    accepted["price_metadata_source"] = _text(accepted["price_metadata_source"], "price_metadata_source")
    observed = _timestamp(accepted["price_metadata_observed_at"], "price_metadata_observed_at")
    expires = _timestamp(accepted["price_metadata_expires_at"], "price_metadata_expires_at")
    if observed >= expires:
        raise ModelConfigurationError("PRICE_METADATA_WINDOW_INVALID")
    for field in (
        "allowed_data_classes",
        "allowed_regions",
        "supported_roles",
        "supported_capabilities",
        "known_limits",
    ):
        accepted[field] = _strings(accepted[field], field, nonempty=field != "known_limits")
    if accepted["egress_class"] not in {"external", "local"}:
        raise ModelConfigurationError("EGRESS_CLASS_INVALID")
    if accepted["connection_check_method"] != "IN_PROCESS_FAKE":
        raise ModelConfigurationError("REAL_CONNECTION_CHECK_NOT_AUTHORIZED")
    if _bool(accepted["default_enabled"], "default_enabled"):
        raise ModelConfigurationError("PROVIDER_DEFAULT_ENABLE_FORBIDDEN")
    return accepted


PROFILE_FIELDS = {
    "schema_version",
    "profile_id",
    "revision",
    "provider_id",
    "role",
    "model_alias",
    "required_capability",
    "qualification_status",
    "behavior_hash",
    "thinking_supported",
    "thinking_can_be_disabled",
    "supported_native_efforts",
    "native_effort_order",
    "native_default_effort",
    "native_effort_projections",
    "price_status",
    "price_currency",
    "price_unit",
    "price_input",
    "price_output",
    "price_source",
    "price_observed_at",
    "price_expires_at",
    "latency_class",
    "resource_class",
    "capability_source",
    "capability_observed_at",
    "capability_expires_at",
    "allowed_data_classes",
    "region",
    "egress_class",
    "credential_binding_id",
}


def validate_model_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    accepted = _object(value, PROFILE_FIELDS, "MODEL_PROFILE")
    if accepted["schema_version"] != "P07_T08_MODEL_PROFILE_CAPABILITY_V1":
        raise ModelConfigurationError("MODEL_PROFILE_SCHEMA_VERSION")
    for field in (
        "profile_id",
        "revision",
        "provider_id",
        "role",
        "required_capability",
        "latency_class",
        "resource_class",
        "region",
        "credential_binding_id",
    ):
        accepted[field] = _identifier(accepted[field], field)
    accepted["model_alias"] = _text(accepted["model_alias"], "model_alias")
    if accepted["qualification_status"] not in QUALIFICATION_STATES:
        raise ModelConfigurationError("QUALIFICATION_STATUS_INVALID")
    accepted["behavior_hash"] = _sha(accepted["behavior_hash"], "behavior_hash")
    supported = _strings(accepted["supported_native_efforts"], "supported_native_efforts", nonempty=False)
    order = _strings(accepted["native_effort_order"], "native_effort_order", nonempty=False)
    if set(supported) != set(order) or len(supported) != len(order):
        raise ModelConfigurationError("NATIVE_EFFORT_ORDER_MISMATCH")
    supported_flag = _bool(accepted["thinking_supported"], "thinking_supported")
    disable_flag = _bool(accepted["thinking_can_be_disabled"], "thinking_can_be_disabled")
    projections = accepted["native_effort_projections"]
    if not isinstance(projections, dict):
        raise ModelConfigurationError("NATIVE_EFFORT_PROJECTIONS_INVALID")
    if supported_flag:
        if not supported or accepted["native_default_effort"] not in supported:
            raise ModelConfigurationError("NATIVE_EFFORT_CAPABILITY_INVALID")
        required_keys = set(supported) | ({"disabled"} if disable_flag else set())
        if set(projections) != required_keys:
            raise ModelConfigurationError("NATIVE_EFFORT_PROJECTION_SET_INVALID")
        if any(not isinstance(item, dict) or not item for item in projections.values()):
            raise ModelConfigurationError("NATIVE_EFFORT_PROJECTION_INVALID")
    else:
        if disable_flag or supported or order or accepted["native_default_effort"] is not None:
            raise ModelConfigurationError("NON_REASONING_EFFORT_MUST_BE_NOT_APPLICABLE")
        if projections != {"not_applicable": {"status": "NOT_APPLICABLE"}}:
            raise ModelConfigurationError("NON_REASONING_PROJECTION_INVALID")
    accepted["supported_native_efforts"] = supported
    accepted["native_effort_order"] = order
    if accepted["price_status"] not in {"KNOWN", "UNKNOWN"}:
        raise ModelConfigurationError("PRICE_STATUS_INVALID")
    for field in ("price_currency", "price_unit", "price_source", "capability_source"):
        accepted[field] = _text(accepted[field], field)
    for field in ("price_input", "price_output"):
        item = accepted[field]
        if accepted["price_status"] == "KNOWN":
            if isinstance(item, bool) or not isinstance(item, (int, float)) or item < 0:
                raise ModelConfigurationError("PRICE_VALUE_INVALID", [field])
        elif item is not None:
            raise ModelConfigurationError("UNKNOWN_PRICE_VALUE_MUST_BE_NULL", [field])
    for start, end in (
        ("price_observed_at", "price_expires_at"),
        ("capability_observed_at", "capability_expires_at"),
    ):
        if _timestamp(accepted[start], start) >= _timestamp(accepted[end], end):
            raise ModelConfigurationError("EVIDENCE_WINDOW_INVALID", [start, end])
    accepted["allowed_data_classes"] = _strings(accepted["allowed_data_classes"], "allowed_data_classes")
    if accepted["egress_class"] not in {"external", "local"}:
        raise ModelConfigurationError("EGRESS_CLASS_INVALID")
    return accepted


POLICY_FIELDS = {"schema_version", "revision", "observed_at", "expires_at", "roles"}
ENTRY_FIELDS = {
    "profile_id",
    "resolution_status",
    "thinking_on_native_effort",
    "thinking_off_native_effort",
    "collapse_target",
    "collapse_reason",
    "reopen_condition",
    "requires_explicit_selection",
    "fallback_profile_ids",
    "fallback_requires_user_confirmation",
}


def validate_tier_policy(
    value: Mapping[str, Any], profiles: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    accepted = _object(value, POLICY_FIELDS, "TIER_POLICY")
    if accepted["schema_version"] != "P07_T08_TIER_POLICY_MANIFEST_V1":
        raise ModelConfigurationError("TIER_POLICY_SCHEMA_VERSION")
    accepted["revision"] = _identifier(accepted["revision"], "revision")
    if _timestamp(accepted["observed_at"], "observed_at") >= _timestamp(accepted["expires_at"], "expires_at"):
        raise ModelConfigurationError("TIER_POLICY_WINDOW_INVALID")
    checked_profiles = [validate_model_profile(item) for item in profiles]
    profile_map = {item["profile_id"]: item for item in checked_profiles}
    if len(profile_map) != len(checked_profiles):
        raise ModelConfigurationError("MODEL_PROFILE_DUPLICATE")
    roles = accepted["roles"]
    if not isinstance(roles, dict) or not roles:
        raise ModelConfigurationError("TIER_POLICY_ROLES_INVALID")
    checked_roles: dict[str, Any] = {}
    for role, tiers in roles.items():
        _identifier(role, "role")
        if not isinstance(tiers, dict) or set(tiers) != set(TIER_IDS):
            raise ModelConfigurationError("TIER_SET_INVALID", [role])
        checked_tiers: dict[str, Any] = {}
        for tier in TIER_IDS:
            entry = _object(tiers[tier], ENTRY_FIELDS, "TIER_ENTRY")
            status = entry["resolution_status"]
            if status not in RESOLUTION_STATUSES:
                raise ModelConfigurationError("TIER_RESOLUTION_STATUS_INVALID", [role, tier])
            explicit = _bool(entry["requires_explicit_selection"], "requires_explicit_selection")
            if explicit != (tier == "maximum"):
                raise ModelConfigurationError("MAXIMUM_EXPLICIT_SELECTION_POLICY_INVALID", [role, tier])
            fallbacks = _strings(entry["fallback_profile_ids"], "fallback_profile_ids", nonempty=False)
            confirm = _bool(entry["fallback_requires_user_confirmation"], "fallback_requires_user_confirmation")
            if fallbacks and not confirm:
                raise ModelConfigurationError("FALLBACK_REQUIRES_EXPLICIT_POLICY", [role, tier])
            entry["fallback_profile_ids"] = fallbacks
            if status == "UNAVAILABLE":
                if any(
                    entry[field] is not None
                    for field in (
                        "profile_id",
                        "thinking_on_native_effort",
                        "thinking_off_native_effort",
                        "collapse_target",
                        "collapse_reason",
                        "reopen_condition",
                    )
                ):
                    raise ModelConfigurationError("UNAVAILABLE_TIER_FIELDS_INVALID", [role, tier])
            else:
                profile_id = _identifier(entry["profile_id"], "profile_id")
                profile = profile_map.get(profile_id)
                if profile is None or profile["role"] != role:
                    raise ModelConfigurationError("TIER_PROFILE_ROLE_MISMATCH", [role, tier, profile_id])
                on_effort = entry["thinking_on_native_effort"]
                off_effort = entry["thinking_off_native_effort"]
                if profile["thinking_supported"]:
                    if on_effort not in profile["supported_native_efforts"]:
                        raise ModelConfigurationError("NATIVE_EFFORT_UNSUPPORTED", [role, tier])
                    expected_off = "disabled" if profile["thinking_can_be_disabled"] else None
                    if off_effort != expected_off:
                        raise ModelConfigurationError("THINKING_OFF_MAPPING_INVALID", [role, tier])
                elif on_effort is not None or off_effort is not None:
                    raise ModelConfigurationError("NON_REASONING_TIER_MAPPING_INVALID", [role, tier])
                if tier == "maximum" and profile["thinking_supported"]:
                    if on_effort != profile["native_effort_order"][-1]:
                        raise ModelConfigurationError("MAXIMUM_NOT_PROFILE_HIGHEST_EFFORT", [role])
                if status == "RESOLVED_UNIQUE":
                    if any(entry[field] is not None for field in ("collapse_target", "collapse_reason", "reopen_condition")):
                        raise ModelConfigurationError("UNIQUE_TIER_COLLAPSE_FIELDS_FORBIDDEN", [role, tier])
                else:
                    target = entry["collapse_target"]
                    if target not in TIER_IDS or abs(TIER_IDS.index(target) - TIER_IDS.index(tier)) != 1:
                        raise ModelConfigurationError("TIER_COLLAPSE_TARGET_INVALID", [role, tier])
                    _text(entry["collapse_reason"], "collapse_reason")
                    _text(entry["reopen_condition"], "reopen_condition")
            checked_tiers[tier] = entry
        for left, right in zip(TIER_IDS, TIER_IDS[1:]):
            left_entry = checked_tiers[left]
            right_entry = checked_tiers[right]
            if (
                left_entry["profile_id"] is not None
                and left_entry["profile_id"] == right_entry["profile_id"]
                and "COLLAPSED_DISCLOSED"
                not in {left_entry["resolution_status"], right_entry["resolution_status"]}
            ):
                raise ModelConfigurationError("TIER_COLLAPSE_NOT_DECLARED", [role, left, right])
        checked_roles[role] = checked_tiers
    accepted["roles"] = checked_roles
    accepted["policy_sha256"] = canonical_sha256(accepted)
    return accepted


RECEIPT_FIELDS = {
    "schema_version",
    "check_id",
    "provider_id",
    "credential_ref",
    "requested_model_alias",
    "returned_model_alias",
    "key_state",
    "connection_state",
    "qualification_state",
    "enabled_state",
    "reason_code",
    "retryable",
    "checked_at",
    "network_requests",
    "external_model_requests",
}


def validate_connection_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    accepted = _object(value, RECEIPT_FIELDS, "CONNECTION_RECEIPT")
    if accepted["schema_version"] != "P07_T08_CONNECTION_CHECK_RECEIPT_V1":
        raise ModelConfigurationError("CONNECTION_RECEIPT_SCHEMA_VERSION")
    for field in ("check_id", "provider_id"):
        accepted[field] = _identifier(accepted[field], field)
    ref = _text(accepted["credential_ref"], "credential_ref")
    if not ref or not REF_RE.fullmatch(ref):
        raise ModelConfigurationError("CREDENTIAL_REF_INVALID")
    for field in ("requested_model_alias", "reason_code"):
        accepted[field] = _text(accepted[field], field)
    accepted["returned_model_alias"] = _text(accepted["returned_model_alias"], "returned_model_alias", optional=True)
    if accepted["key_state"] not in KEY_STATES:
        raise ModelConfigurationError("KEY_STATE_INVALID")
    if accepted["connection_state"] not in CONNECTION_STATES:
        raise ModelConfigurationError("CONNECTION_STATE_INVALID")
    if accepted["qualification_state"] != "NOT_ASSESSED":
        raise ModelConfigurationError("CONNECTION_CHECK_CANNOT_SET_QUALIFICATION")
    if accepted["enabled_state"] != "DISABLED":
        raise ModelConfigurationError("CONNECTION_CHECK_CANNOT_ENABLE")
    _bool(accepted["retryable"], "retryable")
    _timestamp(accepted["checked_at"], "checked_at")
    if accepted["network_requests"] != 0 or accepted["external_model_requests"] != 0:
        raise ModelConfigurationError("OFFLINE_CONNECTION_RECEIPT_REQUIRED")
    return accepted


__all__ = [
    "CREDENTIAL_STATES",
    "ModelConfigurationError",
    "RESOLUTION_STATUSES",
    "TIER_IDS",
    "validate_connection_receipt",
    "validate_credential_binding",
    "validate_model_profile",
    "validate_provider_preset",
    "validate_tier_policy",
]
