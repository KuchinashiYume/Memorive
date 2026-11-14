"""Read-only status, preflight and diagnosis helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .errors import LocalCapabilityError
from .types import (
    LocalExecutionReceipt,
    LocalRoleProfile,
    LocalRoleQualificationVerdict,
    LocalRuntimeAssetManifest,
    LogicalRole,
)


def local_status(
    manifest: LocalRuntimeAssetManifest | Mapping[str, LocalRuntimeAssetManifest],
    profiles: Mapping[LogicalRole, LocalRoleProfile],
    verdicts: Mapping[LogicalRole, LocalRoleQualificationVerdict] | None = None,
    *,
    latest_public_receipt_locator: str | None = None,
) -> dict[str, Any]:
    manifests = (
        {manifest.manifest_id: manifest}
        if isinstance(manifest, LocalRuntimeAssetManifest)
        else dict(manifest)
    )
    if not manifests:
        raise ValueError("local status requires at least one runtime asset manifest")
    verdicts = dict(verdicts or {})
    roles = []
    for role in LogicalRole:
        profile = profiles[role]
        role_manifest = manifests.get(profile.runtime_asset_manifest_id)
        if role_manifest is None:
            raise ValueError(f"missing runtime asset manifest: {profile.runtime_asset_manifest_id}")
        verdict = verdicts.get(role)
        roles.append(
            {
                "logical_role": role.value,
                "profile_id": profile.profile_id,
                "runtime": role_manifest.runtime_name,
                "model": role_manifest.model_tag,
                "manifest_id": role_manifest.manifest_id,
                "qualification": verdict.verdict.value if verdict else "NOT_ASSESSED_UPSTREAM",
                "route_eligibility": profile.route_eligibility,
                "activation_status": profile.activation_status.value,
                "why_disabled": None if profile.route_eligibility else "ROUTE_ELIGIBILITY_ZERO",
            }
        )
    only_manifest = next(iter(manifests.values())) if len(manifests) == 1 else None
    return {
        "runtime": only_manifest.runtime_name if only_manifest else "MULTIPLE_ROLE_BOUND",
        "model": only_manifest.model_tag if only_manifest else "MULTIPLE_ROLE_BOUND",
        "manifest_id": only_manifest.manifest_id if only_manifest else None,
        "runtime_assets": [
            {
                "manifest_id": manifests[manifest_id].manifest_id,
                "runtime": manifests[manifest_id].runtime_name,
                "model": manifests[manifest_id].model_tag,
            }
            for manifest_id in sorted(manifests)
        ],
        "roles": roles,
        "latest_public_receipt_locator": latest_public_receipt_locator,
        "model_process_started": False,
        "model_request_count": 0,
    }


def local_preflight(
    *,
    identity_ready: bool,
    ownership_contract_ready: bool,
    resource_headroom_ready: bool,
    isolated_root_ready: bool,
    zero_egress_observer_ready: bool,
    allowed_roots: Sequence[str],
    allowed_processes: Sequence[str],
    with_health: bool = False,
) -> dict[str, Any]:
    if with_health:
        raise LocalCapabilityError("HEALTH_MODE_NOT_AUTHORIZED", plane="OBSERVATION")
    checks = {
        "identity": identity_ready,
        "ownership_contract": ownership_contract_ready,
        "resource_headroom": resource_headroom_ready,
        "isolated_root": isolated_root_ready,
        "zero_egress_observer": zero_egress_observer_ready,
        "allowed_roots_nonempty": bool(allowed_roots),
        "allowed_processes_nonempty": bool(allowed_processes),
    }
    return {
        "result": "PASS" if all(checks.values()) else "BLOCKED",
        "checks": checks,
        "with_health": False,
        "model_process_started": False,
        "model_request_count": 0,
        "system_policy_mutations": 0,
    }


def local_diagnose(receipt: LocalExecutionReceipt) -> dict[str, Any]:
    if receipt.final_status.value == "COMPLETED":
        plane = "NONE"
        next_step = "NO_REPAIR_REQUIRED"
    elif receipt.error_class and any(token in receipt.error_class for token in ("IDENTITY", "OWNERSHIP", "PROFILE")):
        plane = "IDENTITY"
        next_step = "REPAIR_SOURCE_IDENTITY_OR_PROFILE_IN_NEW_A_ATTEMPT"
    elif receipt.error_class and any(token in receipt.error_class for token in ("EGRESS", "RESOURCE", "LIVE_LOCAL")):
        plane = "OBSERVATION"
        next_step = "FREEZE_ATTEMPT_AND_REPAIR_SECURITY_OR_RESOURCE_ENVELOPE"
    else:
        plane = "OUTPUT"
        next_step = "TARGETED_OUTPUT_CONTRACT_SUCCESSOR"
    return {
        "request_id": receipt.request_id,
        "receipt_id": receipt.receipt_id,
        "failure_plane": plane,
        "fallback_occurred": receipt.fallback_path is not None,
        "partial_generation": False,
        "next_step": next_step,
        "must_keep_disabled": receipt.final_status.value != "COMPLETED",
        "automatic_repair_performed": False,
        "model_process_started": False,
        "system_policy_mutations": 0,
    }
