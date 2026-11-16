from __future__ import annotations

import pytest

from memorive.models.gateway.local_capability import ActivationStatus, LocalRoleProfile, LogicalRole, OwnershipClass
from memorive.models.gateway.local_capability.errors import OwnershipBlocked
from memorive.models.gateway.local_capability.ownership import require_local_route, resolve_ownership, route_ownership
def profile(*, allowed: tuple[OwnershipClass, ...] = tuple(OwnershipClass), qualification: bool = True) -> LocalRoleProfile:
    return LocalRoleProfile(
        profile_id="profile-local-analysis",
        logical_role=LogicalRole.LOCAL_ANALYSIS,
        profile_revision="v1",
        runtime_asset_manifest_id="manifest-001",
        input_contract_version="v1",
        output_contract_version="v1",
        allowed_ownership_classes=allowed,
        resource_envelope_id="resource-001",
        route_eligibility=0,
        activation_status=ActivationStatus.DISABLED,
        qualification_mode_allowed=qualification,
    )


def identity(kind: str, components: list[str] | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "identity_id": "owner-001",
        "ownership_class": kind,
        "provenance_sha256": "A" * 64,
    }
    if components is not None:
        result["components"] = components
    return result


@pytest.mark.parametrize("kind", ["self", "entrusted", "unknown"])
def test_direct_ownership_is_preserved_and_never_cloud_eligible(kind: str) -> None:
    decision = route_ownership(identity(kind), profile(), qualification_mode=True)
    assert decision.effective_ownership.value == kind
    assert decision.local_route_eligibility is True
    assert decision.cloud_eligibility is False


def test_mixed_uses_strictest_proven_component() -> None:
    effective, reason = resolve_ownership(identity("mixed", ["self", "entrusted"]))
    assert effective is OwnershipClass.ENTRUSTED
    assert "STRICTEST" in reason
    effective, _ = resolve_ownership(identity("mixed", ["self", "unknown"]))
    assert effective is OwnershipClass.UNKNOWN


def test_empty_mixed_fails_to_unknown_instead_of_self() -> None:
    effective, reason = resolve_ownership(identity("mixed", []))
    assert effective is OwnershipClass.UNKNOWN
    assert reason == "MIXED_WITHOUT_COMPONENTS_STRICTEST_UNKNOWN"


def test_missing_identity_fails_before_any_route() -> None:
    with pytest.raises(OwnershipBlocked, match="OWNERSHIP_IDENTITY_MISSING"):
        route_ownership({"ownership_class": "self"}, profile(), qualification_mode=True)


def test_profile_rejects_unlisted_effective_ownership() -> None:
    decision = route_ownership(
        identity("unknown"),
        profile(allowed=(OwnershipClass.SELF,)),
        qualification_mode=True,
    )
    assert decision.local_route_eligibility is False
    assert decision.selected_profile_id is None
    assert decision.fail_closed_reason == "OWNERSHIP_CLASS_NOT_ALLOWED_BY_PROFILE"
    with pytest.raises(OwnershipBlocked):
        require_local_route(decision, LogicalRole.LOCAL_ANALYSIS)


def test_disabled_profile_is_not_a_live_route() -> None:
    decision = route_ownership(identity("self"), profile(), qualification_mode=False)
    assert decision.local_route_eligibility is False
    assert decision.fail_closed_reason == "PROFILE_DISABLED_OR_ROUTE_INELIGIBLE"
