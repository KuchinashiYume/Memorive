"""Canonical serialization and semantic validation for Runtime contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from .errors import ContractViolation
from .types import (
    ActivationStatus,
    CombinedLocalChainVerdict,
    LocalEgressEvidence,
    LocalExecutionEnvelope,
    LocalExecutionReceipt,
    LocalOwnershipRouteDecision,
    LocalResourceEnvelope,
    LocalRoleProfile,
    LocalRoleQualificationVerdict,
    LocalRuntimeAssetManifest,
    LogicalRole,
    QualificationStatus,
    to_primitive,
)


SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

SCHEMA_BY_TYPE = {
    LocalRuntimeAssetManifest: "local_runtime_asset_manifest_v1.schema.json",
    LocalRoleProfile: "local_role_profile_v1.schema.json",
    LocalExecutionEnvelope: "local_execution_envelope_v1.schema.json",
    LocalExecutionReceipt: "local_execution_receipt_v1.schema.json",
    LocalOwnershipRouteDecision: "local_ownership_route_decision_v1.schema.json",
    LocalResourceEnvelope: "local_resource_envelope_v1.schema.json",
    LocalEgressEvidence: "local_egress_evidence_v1.schema.json",
    LocalRoleQualificationVerdict: "local_role_qualification_verdict_v1.schema.json",
    CombinedLocalChainVerdict: "combined_local_chain_verdict_v1.schema.json",
}


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        to_primitive(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def require_safe_id(value: str, field: str) -> None:
    if not SAFE_ID_RE.fullmatch(value):
        raise ContractViolation("SAFE_IDENTIFIER_INVALID", details={"field": field})


def require_sha256(value: str, field: str) -> None:
    if not SHA256_RE.fullmatch(value):
        raise ContractViolation("SHA256_INVALID", details={"field": field})


def load_schema(name: str) -> dict[str, Any]:
    schema = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def validate_schema(value: Any, schema_name: str) -> dict[str, Any]:
    primitive = to_primitive(value)
    errors = sorted(
        Draft202012Validator(load_schema(schema_name)).iter_errors(primitive),
        key=lambda item: (list(item.absolute_path), item.message),
    )
    if errors:
        raise ContractViolation(
            "SCHEMA_INVALID",
            details={
                "schema": schema_name,
                "errors": [
                    f"{'/'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
                    for error in errors[:20]
                ],
            },
        )
    return primitive


def validate_contract(value: Any) -> dict[str, Any]:
    schema_name = SCHEMA_BY_TYPE.get(type(value))
    if schema_name is None:
        raise ContractViolation("UNSUPPORTED_CONTRACT_TYPE", details={"type": type(value).__name__})
    if isinstance(value, LocalRuntimeAssetManifest) and value.product_bundling_allowed:
        raise ContractViolation("PRODUCT_BUNDLING_NOT_ADMITTED")
    primitive = validate_schema(value, schema_name)

    if isinstance(value, LocalRuntimeAssetManifest):
        require_safe_id(value.manifest_id, "manifest_id")
        parsed = urlsplit(value.endpoint)
        if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS or parsed.username or parsed.password:
            raise ContractViolation("RUNTIME_ENDPOINT_NOT_EXACT_LOOPBACK")
        for name in ("executable", "model_manifest", "model_blob", "license_file"):
            require_sha256(getattr(value, name).sha256, f"{name}.sha256")
        if value.projector is not None:
            require_sha256(value.projector.sha256, "projector.sha256")
    elif isinstance(value, LocalRoleProfile):
        require_safe_id(value.profile_id, "profile_id")
        if value.route_eligibility not in {0, 1}:
            raise ContractViolation("ROUTE_ELIGIBILITY_INVALID")
    elif isinstance(value, LocalExecutionEnvelope):
        require_safe_id(value.request_id, "request_id")
        require_sha256(value.input_sha256, "input_sha256")
        require_sha256(value.expected_output_schema_sha256, "expected_output_schema_sha256")
        if value.payload_persisted:
            raise ContractViolation("PAYLOAD_PERSISTENCE_FORBIDDEN")
        if value.max_attempts != 1 or value.attempt != 1:
            raise ContractViolation("ATTEMPT_ENVELOPE_NOT_FROZEN_ONE_SHOT")
    elif isinstance(value, LocalExecutionReceipt):
        if value.cloud_eligibility or value.payload_persisted or value.provider_cost_cny != "0":
            raise ContractViolation("LOCAL_RECEIPT_BOUNDARY_VIOLATION")
        if value.output_sha256 is not None:
            require_sha256(value.output_sha256, "output_sha256")
    elif isinstance(value, LocalOwnershipRouteDecision):
        require_sha256(value.provenance_sha256, "provenance_sha256")
        if value.cloud_eligibility:
            raise ContractViolation("CLOUD_ELIGIBILITY_MUST_BE_FALSE")
    elif isinstance(value, LocalResourceEnvelope):
        if value.automatic_expansion_allowed or value.implicit_truncation_allowed:
            raise ContractViolation("RESOURCE_ENVELOPE_MUST_FAIL_CLOSED")
        if value.max_attempts != 1:
            raise ContractViolation("RESOURCE_ATTEMPT_LIMIT_MUST_BE_ONE")
    elif isinstance(value, LocalEgressEvidence):
        if value.forbidden_destination_count or value.unattributed_event_count:
            raise ContractViolation("EGRESS_EVIDENCE_CONTAINS_FORBIDDEN_EVENT")
    elif isinstance(value, LocalRoleQualificationVerdict):
        if value.route_eligibility not in {0, 1}:
            raise ContractViolation("QUALIFICATION_ROUTE_ELIGIBILITY_INVALID")
    elif isinstance(value, CombinedLocalChainVerdict):
        roles = [row.logical_role.value for row in value.role_verdicts]
        if len(roles) != 6 or len(set(roles)) != 6:
            raise ContractViolation("CHAIN_ROLE_DENOMINATOR_NOT_SIX")
        if value.production_route != "KEEP_DISABLED" or value.activation_credit:
            raise ContractViolation("CHAIN_ACTIVATION_NOT_AUTHORIZED")
    return primitive


def contract_schema_sha256(value_type: type[Any]) -> str:
    schema_name = SCHEMA_BY_TYPE[value_type]
    return file_sha256(SCHEMA_DIR / schema_name)


def public_safe_ref(value: Any, locator: str) -> dict[str, str]:
    return {"locator": locator, "sha256": canonical_sha256(value)}


def combine_role_verdicts(
    chain_id: str,
    verdicts: list[LocalRoleQualificationVerdict] | tuple[LocalRoleQualificationVerdict, ...],
    *,
    ownership_hard_gate: str,
    zero_egress_hard_gate: str,
) -> CombinedLocalChainVerdict:
    """Combine six role verdicts without averaging away a hard failure."""

    ordered = tuple(sorted(verdicts, key=lambda row: row.logical_role.value))
    if len(ordered) != 6 or {row.logical_role for row in ordered} != set(LogicalRole):
        raise ContractViolation("CHAIN_ROLE_DENOMINATOR_NOT_SIX")
    blocked = {
        QualificationStatus.BLOCKED_IDENTITY,
        QualificationStatus.BLOCKED_SECURITY,
        QualificationStatus.NOT_ASSESSED_UPSTREAM,
    }
    qualified = {
        QualificationStatus.QUALIFIED_LOCAL_ONLY,
        QualificationStatus.QUALIFIED_WITH_LIMITS,
    }
    verify_row = next(row for row in ordered if row.logical_role is LogicalRole.LOCAL_VERIFY)
    structural_only = "STRUCTURAL_ONLY" in verify_row.limits
    if ownership_hard_gate != "PASS" or zero_egress_hard_gate != "PASS" or any(row.verdict in blocked for row in ordered):
        chain_verdict = "LOCAL_CHAIN_BLOCKED_REQUIRED"
    elif all(row.verdict in qualified for row in ordered) and not structural_only:
        chain_verdict = "FULL_LOCAL_CHAIN_QUALIFIED"
    else:
        chain_verdict = "PARTIAL_LOCAL_CHAIN_KEEP_DISABLED"
    result = CombinedLocalChainVerdict(
        chain_id=chain_id,
        verdict=chain_verdict,
        role_verdicts=ordered,
        ownership_hard_gate=ownership_hard_gate,
        zero_egress_hard_gate=zero_egress_hard_gate,
        production_route="KEEP_DISABLED",
        activation_credit=False,
    )
    validate_contract(result)
    return result
