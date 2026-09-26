"""T08-bound, cloud-ineligible ownership routing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import canonical_sha256, require_sha256
from .errors import OwnershipBlocked
from .types import LocalOwnershipRouteDecision, LocalRoleProfile, LogicalRole, OwnershipClass


POLICY_REVISION = "P06_T11_OWNERSHIP_ROUTE_V1"


def _ownership(value: Any) -> OwnershipClass:
    try:
        return OwnershipClass(str(value).lower())
    except ValueError as exc:
        raise OwnershipBlocked("OWNERSHIP_CLASS_INVALID") from exc


def resolve_ownership(identity: Mapping[str, Any]) -> tuple[OwnershipClass, str]:
    if not identity.get("identity_id") or not identity.get("provenance_sha256"):
        raise OwnershipBlocked("OWNERSHIP_IDENTITY_MISSING")
    require_sha256(str(identity["provenance_sha256"]), "ownership.provenance_sha256")
    source = _ownership(identity.get("ownership_class"))
    if source is OwnershipClass.MIXED:
        components = tuple(_ownership(value) for value in identity.get("components", ()))
        if not components:
            return OwnershipClass.UNKNOWN, "MIXED_WITHOUT_COMPONENTS_STRICTEST_UNKNOWN"
        if all(item is OwnershipClass.SELF for item in components):
            return OwnershipClass.SELF, "MIXED_ALL_SELF_PROVEN"
        if OwnershipClass.UNKNOWN in components:
            return OwnershipClass.UNKNOWN, "MIXED_CONTAINS_UNKNOWN_STRICTEST"
        return OwnershipClass.ENTRUSTED, "MIXED_CONTAINS_ENTRUSTED_STRICTEST"
    if source is OwnershipClass.UNKNOWN:
        return OwnershipClass.UNKNOWN, "UNKNOWN_STRICTEST_LOCAL_ONLY"
    return source, "DIRECT_IDENTITY"


def route_ownership(
    identity: Mapping[str, Any],
    profile: LocalRoleProfile,
    *,
    qualification_mode: bool,
) -> LocalOwnershipRouteDecision:
    source = _ownership(identity.get("ownership_class"))
    effective, reason = resolve_ownership(identity)
    allowed = effective in set(profile.allowed_ownership_classes)
    profile_eligible = (
        qualification_mode and profile.qualification_mode_allowed
    ) or (profile.route_eligibility == 1 and profile.activation_status.value == "ENABLED")
    local_eligible = allowed and profile_eligible
    fail_reason = None
    if not allowed:
        fail_reason = "OWNERSHIP_CLASS_NOT_ALLOWED_BY_PROFILE"
    elif not profile_eligible:
        fail_reason = "PROFILE_DISABLED_OR_ROUTE_INELIGIBLE"
    decision_id = f"decision-{canonical_sha256({'identity': dict(identity), 'profile': profile.profile_id})[:24].lower()}"
    return LocalOwnershipRouteDecision(
        decision_id=decision_id,
        source_identity_id=str(identity["identity_id"]),
        source_ownership=source,
        provenance_sha256=str(identity["provenance_sha256"]),
        effective_ownership=effective,
        merge_reason=reason,
        selected_role=profile.logical_role,
        selected_profile_id=profile.profile_id if local_eligible else None,
        fail_closed_reason=fail_reason,
        cloud_eligibility=False,
        local_route_eligibility=local_eligible,
        decision_policy_revision=POLICY_REVISION,
    )


def require_local_route(decision: LocalOwnershipRouteDecision, role: LogicalRole) -> None:
    if decision.selected_role is not role or not decision.local_route_eligibility:
        raise OwnershipBlocked(
            decision.fail_closed_reason or "OWNERSHIP_ROUTE_NOT_ELIGIBLE",
            details={"decision_id": decision.decision_id, "role": role.value},
        )
