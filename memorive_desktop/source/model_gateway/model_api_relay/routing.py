from __future__ import annotations

from typing import Any, Mapping

from .contracts import EndpointProfileCatalog, RelayContractError


class RelayRoutePlanner:
    """Explicit direct/relay selection with no implicit fallback."""

    def __init__(self, routes: Mapping[str, Mapping[str, Any]]):
        self._routes = {role: dict(value) for role, value in routes.items()}

    def select(self, role: str, *, mode: str) -> str:
        route = self._routes.get(role)
        if route is None:
            raise RelayContractError("ROLE_ROUTE_NOT_FOUND", [role])
        normalized_mode = mode.replace("_", "-")
        key = {"direct-only": "direct", "relay-only": "relay"}.get(normalized_mode)
        if key is None or not route.get(key):
            raise RelayContractError("ROUTE_MODE_NOT_ELIGIBLE", [role, mode])
        return route[key]

    def decide(
        self,
        role: str,
        *,
        policy: str,
        qualified_profiles: Mapping[str, bool],
        local_exempt: bool = False,
    ) -> dict[str, Any]:
        route = self._routes.get(role)
        if route is None:
            raise RelayContractError("ROLE_ROUTE_NOT_FOUND", [role])
        if local_exempt:
            return {
                "role": role,
                "policy": policy,
                "selected_profile": None,
                "status": "BLOCKED_LOCAL_EXEMPT",
                "candidate_decisions": [],
                "external_attempts": 0,
                "reason_code": "LOCAL_EXEMPT_EXTERNAL_CANDIDATES_FORBIDDEN",
            }
        if policy == "blocked":
            candidates: list[str] = []
        elif policy == "direct_only":
            candidates = [route.get("direct")]
        elif policy == "relay_only":
            candidates = [route.get("relay")]
        elif policy == "explicit_ordered_candidates":
            candidates = list(route.get("ordered_candidates") or [])
        else:
            raise RelayContractError("ROUTE_POLICY_INVALID", [policy])
        candidates = [candidate for candidate in candidates if isinstance(candidate, str) and candidate]
        decisions = []
        selected = None
        for rank, candidate in enumerate(candidates):
            eligible = qualified_profiles.get(candidate) is True
            decisions.append({
                "profile_id": candidate,
                "rank": rank,
                "eligible": eligible,
                "reason_code": "QUALIFIED" if eligible else "PROFILE_NOT_QUALIFIED",
            })
            if selected is None and eligible:
                selected = candidate
        if policy == "relay_only" and selected and selected == route.get("direct"):
            raise RelayContractError("RELAY_ONLY_SELECTED_DIRECT")
        return {
            "role": role,
            "policy": policy,
            "selected_profile": selected,
            "status": "SELECTED" if selected else "BLOCKED",
            "candidate_decisions": decisions,
            "external_attempts": 0,
            "reason_code": None if selected else "NO_QUALIFIED_ROUTE",
        }

    @staticmethod
    def on_failure(profile_id: str, *, fallback_requested: bool) -> None:
        if fallback_requested:
            raise RelayContractError("SILENT_FALLBACK_FORBIDDEN", [profile_id])
        raise RelayContractError("ROUTE_FAILED_NO_FALLBACK", [profile_id])

    def rollback(self, role: str, *, qualified_profiles: Mapping[str, bool] | None = None) -> str:
        profile_id = self.select(role, mode="direct-only")
        if qualified_profiles is not None and qualified_profiles.get(profile_id) is not True:
            raise RelayContractError("ROLLBACK_TARGET_NOT_QUALIFIED", [profile_id])
        return profile_id


class ProfiledRouter:
    """Profile-aware Router variant retaining the existing dispatch signature.

    The class is opt-in and therefore cannot activate a profile by being imported.
    A caller injects an exact role->profile map and a transport client.
    """

    def __init__(self, config, catalog: EndpointProfileCatalog, route_map: Mapping[str, str], *, client, clock):
        self.config = config
        self.catalog = catalog
        self.route_map = dict(route_map)
        self.client = client
        self.clock = clock

    def dispatch(self, task_type: str, payload: dict, *, capability: str = "chat") -> dict:
        slot = self.config.slot(task_type)
        profile_id = self.route_map.get(task_type)
        if profile_id is None:
            raise RelayContractError("PROFILE_ROUTE_NOT_CONFIGURED", [task_type])
        resolved = self.catalog.resolve(profile_id, role=task_type, capability=capability, at=self.clock())
        return self.client.invoke(resolved, slot, payload, capability=capability)
