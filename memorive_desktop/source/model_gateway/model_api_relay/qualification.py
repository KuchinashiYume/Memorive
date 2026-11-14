from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from model_gateway.execution_core.contracts import canonical_sha256, immutable_copy

from .contracts import RelayContractError, ResolvedEndpointProfile, validate_endpoint_profile


QUALIFICATION_KEY_FIELDS = {
    "logical_role",
    "execution_profile_revision",
    "protocol_adapter_id",
    "protocol_revision",
    "endpoint_operator",
    "endpoint_origin_hash",
    "path_contract_hash",
    "credential_binding_id",
    "credential_binding_revision",
    "requested_model",
    "model_alias_revision",
    "access_mode",
    "billing_mode",
    "region",
    "egress",
    "behavior_hash",
    "output_contract_hash",
    "platform",
    "runtime",
}


def build_relay_qualification_key(
    *,
    logical_role: str,
    execution_profile_revision: str,
    resolved: ResolvedEndpointProfile,
    platform: str,
    runtime: str,
) -> dict[str, Any]:
    """Build the canonical Extensions qualification key without payload or secret data."""

    profile = validate_endpoint_profile(
        {key: value for key, value in resolved.profile.items() if key != "profile_fingerprint"}
    )
    if profile["profile_fingerprint"] != resolved.profile.get("profile_fingerprint"):
        raise RelayContractError("PROFILE_FINGERPRINT_DRIFT")
    manifest = resolved.protocol_manifest
    binding = resolved.credential_binding
    value = {
        "logical_role": logical_role,
        "execution_profile_revision": execution_profile_revision,
        "protocol_adapter_id": manifest["adapter_id"],
        "protocol_revision": manifest["protocol_revision"],
        "endpoint_operator": profile["endpoint_operator"],
        "endpoint_origin_hash": canonical_sha256(profile["origin"]),
        "path_contract_hash": canonical_sha256(profile["path_template"]),
        "credential_binding_id": binding["credential_binding_id"],
        "credential_binding_revision": binding["revision"],
        "requested_model": profile["request_model_alias"],
        "model_alias_revision": profile["alias_revision"],
        "access_mode": profile["access_mode"],
        "billing_mode": profile["billing_mode"],
        "region": profile["region"],
        "egress": profile["egress"],
        "behavior_hash": profile["behavior_hash"],
        "output_contract_hash": profile["output_contract_hash"],
        "platform": platform,
        "runtime": runtime,
    }
    for field, item in value.items():
        if not isinstance(item, str) or not item:
            raise RelayContractError("QUALIFICATION_KEY_FIELD_INVALID", [field])
    if set(value) != QUALIFICATION_KEY_FIELDS:
        raise RelayContractError("QUALIFICATION_KEY_FIELDS_INVALID")
    return {
        "schema_version": "MODEL_API_RELAY_RELAY_QUALIFICATION_KEY_V1",
        "canonical_input": value,
        "qualification_key": canonical_sha256(value),
    }


def _labels(values: Iterable[Any]) -> tuple[str, ...]:
    accepted = []
    for value in values:
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise RelayContractError("MODEL_LABEL_INVALID")
        accepted.append(value.strip())
    return tuple(accepted)


def classify_backend_identity(
    *,
    transport_kind: str,
    requested_model: str,
    relay_reported_model: str | None,
    response_body_models: Sequence[str] = (),
    response_header_models: Sequence[str] = (),
    response_event_models: Sequence[str] = (),
    independent_backend_identity: str | None = None,
    generated_text_claim: str | None = None,
) -> dict[str, Any]:
    """Keep operator labels, returned identity and model-generated claims separate."""

    del generated_text_claim  # Model prose is deliberately never identity evidence.
    labels = _labels(
        [relay_reported_model, *response_body_models, *response_header_models, *response_event_models]
    )
    distinct = tuple(sorted(set(labels)))
    independent = _labels([independent_backend_identity])
    if len(distinct) > 1:
        grade = "RELAY_REPORTED_CONFLICT"
        returned_model = None
        reason = "MODEL_LABEL_CONFLICT"
    elif transport_kind == "DIRECT_PROVIDER" and distinct and distinct[0] == requested_model:
        grade = "DIRECT_PROVIDER_VERIFIED"
        returned_model = distinct[0]
        reason = "DIRECT_ENDPOINT_AND_LABEL_MATCH"
    elif independent and independent[0] == requested_model and len(distinct) <= 1:
        grade = "RELAY_BACKEND_VERIFIED"
        returned_model = independent[0]
        reason = "INDEPENDENT_BACKEND_IDENTITY_MATCH"
    elif distinct:
        grade = "RELAY_REPORTED_UNVERIFIED"
        returned_model = None
        reason = "ONLY_RELAY_REPORTED_LABEL"
    else:
        grade = "PROVIDER_NOT_EXPOSED"
        returned_model = None
        reason = "MODEL_LABEL_ABSENT"
    return {
        "requested_model": requested_model,
        "relay_reported_model": relay_reported_model,
        "response_body_models": list(response_body_models),
        "response_header_models": list(response_header_models),
        "response_event_models": list(response_event_models),
        "independent_backend_identity": independent[0] if independent else None,
        "identity_grade": grade,
        "returned_model": returned_model,
        "reason_code": reason,
    }


def build_capability_loss_map(
    *,
    source_protocol: str,
    target_protocol: str,
    required_capabilities: Sequence[str],
    preserved_capabilities: Sequence[str],
) -> dict[str, Any]:
    required = tuple(dict.fromkeys(required_capabilities))
    preserved = tuple(dict.fromkeys(preserved_capabilities))
    extra = sorted(set(preserved) - set(required))
    if extra:
        raise RelayContractError("CAPABILITY_LOSS_MAP_UNKNOWN_CAPABILITY", extra)
    losses = sorted(set(required) - set(preserved))
    translating = source_protocol != target_protocol
    return {
        "schema_version": "MODEL_API_RELAY_CAPABILITY_LOSS_MAP_V1",
        "source_protocol": source_protocol,
        "target_protocol": target_protocol,
        "relay_mode": "PROTOCOL_TRANSLATING" if translating else "PROTOCOL_PRESERVING",
        "required_capabilities": list(required),
        "preserved_capabilities": list(preserved),
        "lost_capabilities": losses,
        "native_interface_replacement": not translating and not losses,
    }


def evaluate_qualification(
    *,
    q2_offline_pass: bool,
    q4_transport_pass: bool,
    role: str | None,
    identity_grade: str,
    identity_threshold: str,
    usage_status: str,
    billing_status: str,
    region_egress_verified: bool,
    role_contract_pass: bool,
    allowed_usage_statuses: Sequence[str] = ("ACTUAL_REPORTED", "ACTUAL_VERIFIED"),
    allowed_billing_statuses: Sequence[str] = ("ESTIMATED", "ACTUAL_VERIFIED"),
) -> dict[str, Any]:
    reasons: list[str] = []
    if not q2_offline_pass:
        verdict = "BLOCKED"
        reasons.append("Q2_OFFLINE_NOT_PASS")
    elif not q4_transport_pass:
        verdict = "OFFLINE_CONTRACT_READY"
        reasons.append("Q4_TRANSPORT_NOT_PASS")
    elif role is None:
        verdict = "TRANSPORT_COMPATIBLE"
    else:
        if identity_grade == "RELAY_REPORTED_CONFLICT":
            reasons.append("MODEL_IDENTITY_CONFLICT")
        if identity_grade != identity_threshold:
            reasons.append("IDENTITY_THRESHOLD_NOT_MET")
        if usage_status not in set(allowed_usage_statuses):
            reasons.append("USAGE_NOT_VERIFIED")
        if billing_status not in set(allowed_billing_statuses):
            reasons.append("BILLING_NOT_ADMISSIBLE")
        if not region_egress_verified:
            reasons.append("REGION_EGRESS_NOT_VERIFIED")
        if not role_contract_pass:
            reasons.append("ROLE_CONTRACT_NOT_PASS")
        if not reasons:
            verdict = "ROLE_QUALIFIED"
        elif identity_grade in {"RELAY_REPORTED_UNVERIFIED", "PROVIDER_NOT_EXPOSED"}:
            verdict = "RESTRICTED_IDENTITY_UNVERIFIED"
        else:
            verdict = "BLOCKED"
    return {
        "verdict": verdict,
        "role": role,
        "identity_grade": identity_grade,
        "identity_threshold": identity_threshold,
        "reason_codes": reasons,
    }


def build_compatibility_report(
    *,
    report_id: str,
    required_units: Sequence[str],
    entries: Sequence[Mapping[str, Any]],
    evidence_refs: Sequence[str],
) -> dict[str, Any]:
    accepted_entries = [immutable_copy(item) for item in entries]
    seen = [item.get("unit") for item in accepted_entries]
    if len(seen) != len(set(seen)) or set(seen) != set(required_units):
        raise RelayContractError("COMPATIBILITY_REPORT_DENOMINATOR_MISMATCH")
    allowed = {
        "OFFLINE_CONTRACT_READY", "TRANSPORT_COMPATIBLE", "ROLE_QUALIFIED",
        "RESTRICTED_IDENTITY_UNVERIFIED", "BLOCKED",
    }
    for item in accepted_entries:
        if item.get("verdict") not in allowed:
            raise RelayContractError("COMPATIBILITY_REPORT_VERDICT_INVALID")
        if not isinstance(item.get("qualification_key"), str):
            raise RelayContractError("COMPATIBILITY_REPORT_KEY_MISSING")
    report = {
        "schema_version": "MODEL_API_RELAY_RELAY_COMPATIBILITY_REPORT_V1",
        "report_id": report_id,
        "required_units": list(required_units),
        "entries": accepted_entries,
        "evidence_refs": list(evidence_refs),
    }
    report["report_sha256"] = canonical_sha256(report)
    return report
