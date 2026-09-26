from __future__ import annotations

from typing import Any

from pr_os_desktop_service.control_store import ControlStore
from pr_os_desktop_service.locator import StableLocator, route_for_locator
from pr_os_desktop_service.protocol import canonical_json_bytes

from .contracts import canonical_sha256, require_identifier


_SYNTHETIC_PREFIXES = (
    "syn-",
    "job_fixture_",
    "attempt_fixture_",
    "artifact_fixture_",
    "event_fixture_",
    "action_fixture_",
)


class LocatorNavigationError(ValueError):
    pass


def _assert_synthetic(locator: StableLocator) -> None:
    values = [locator.object_id] + ([locator.job_id] if locator.job_id is not None else [])
    if not all(value.startswith(_SYNTHETIC_PREFIXES) for value in values):
        raise LocatorNavigationError("PRODUCTION_LOCATOR_FORBIDDEN")


def _focus_target(locator: StableLocator) -> str:
    return {
        "job": f"task:{locator.object_id}",
        "attempt": f"attempt:{locator.object_id}",
        "artifact": f"artifact:{locator.object_id}",
        "event": f"event:{locator.object_id}",
        "action": f"action:{locator.object_id}",
    }[locator.kind]


def _navigation_sequence(locator: StableLocator, route: str, node_id: str | None = None) -> list[str]:
    if locator.kind in {"job", "attempt"}:
        sequence = ["GLOBAL_CURRENT_TASK", "SELECT_EXACT_TASK", "OPEN_TASK_FLOW"]
        if node_id is not None:
            return [*sequence, "SELECT_EXACT_NODE", "OPEN_NODE_INSPECTOR"]
        return [*sequence, "FOCUS_EXACT_OBJECT"]
    if locator.kind == "artifact":
        return ["GLOBAL_LIBRARY", "SELECT_EXACT_ARTIFACT", "OPEN_ARTIFACT_DETAIL"]
    if locator.kind == "event":
        return ["GLOBAL_WORK_LOG", "SELECT_EXACT_EVENT", "OPEN_EVENT_DETAIL"]
    return [f"GLOBAL_{route.upper().replace('-', '_')}", "SELECT_EXACT_ACTION", "OPEN_ACTION_DETAIL"]


class MessageNavigator:
    def __init__(
        self,
        resolver: Any,
        control_store: ControlStore,
        *,
        synthetic_only: bool = True,
    ):
        self.resolver = resolver
        self.control_store = control_store
        self.synthetic_only = synthetic_only

    def navigate(self, rendered: str, *, node_id: str | None = None) -> dict[str, Any]:
        before = self.control_store.load()
        before_sha = canonical_sha256(before["state"])
        try:
            locator = StableLocator.parse(rendered)
            if self.synthetic_only:
                _assert_synthetic(locator)
            resolved = self.resolver.resolve(rendered)
            if resolved.get("kind") != locator.kind or resolved.get("object_id") != locator.object_id:
                raise LocatorNavigationError("LOCATOR_RESOLVER_IDENTITY_MISMATCH")
            expected_route = route_for_locator(locator)
            if resolved.get("route") != expected_route:
                raise LocatorNavigationError("LOCATOR_RESOLVER_ROUTE_MISMATCH")
            accepted_node_id = (
                require_identifier(node_id, "navigation_node_id")
                if node_id is not None
                else None
            )
            if accepted_node_id is not None:
                target = resolved.get("target")
                snapshots = target.get("snapshots", {}) if isinstance(target, dict) else {}
                definition = snapshots.get("workflow_definition", {}) if isinstance(snapshots, dict) else {}
                content = definition.get("content", {}) if isinstance(definition, dict) else {}
                nodes = content.get("nodes", []) if isinstance(content, dict) else []
                available_node_ids = {
                    row.get("node_id") for row in nodes if isinstance(row, dict)
                }
                if accepted_node_id not in available_node_ids:
                    raise LocatorNavigationError("LOCATOR_TARGET_NODE_NOT_FOUND")
        except Exception as exc:
            after = self.control_store.load()
            return {
                "schema_version": "P08T07LocatorNavigationReceipt-v1",
                "locator_sha256": canonical_sha256(rendered),
                "status": "TARGET_UNAVAILABLE",
                "reason_code": str(exc).split(":", 1)[0] or type(exc).__name__,
                "fallback": "对应对象已不可用；消息保持原状态，可查看形成时定位信息。",
                "fuzzy_match_attempted": False,
                "task_or_object_mutation": False,
                "control_store_before_sha256": before_sha,
                "control_store_after_sha256": canonical_sha256(after["state"]),
                "control_store_unchanged": canonical_json_bytes(before["state"]) == canonical_json_bytes(after["state"]),
            }

        def mutate(state: dict[str, Any]) -> None:
            state["route"] = expected_route
            state["selected_locator"] = rendered
            state["filters"]["navigation.focus_target"] = _focus_target(locator)
            if accepted_node_id is not None:
                state["filters"]["navigation.node_id"] = accepted_node_id
                state["filters"]["navigation.target_view"] = "CURRENT_TASK_NODE_INSPECTOR"

        saved = self.control_store.update(mutate)
        return {
            "schema_version": "P08T07LocatorNavigationReceipt-v1",
            "locator": rendered,
            "kind": locator.kind,
            "object_id": locator.object_id,
            "job_id": locator.job_id,
            "route": expected_route,
            "focus_target": _focus_target(locator),
            "navigation_sequence": _navigation_sequence(locator, expected_route, accepted_node_id),
            "target_node_id": accepted_node_id,
            "target_view": (
                "CURRENT_TASK_NODE_INSPECTOR" if accepted_node_id is not None else None
            ),
            "resolved_target_sha256": canonical_sha256(resolved["target"]),
            "fuzzy_match_attempted": False,
            "task_or_object_mutation": False,
            "control_store_before_sha256": before_sha,
            "control_store_after_sha256": canonical_sha256(saved["state"]),
            "status": "PASS",
        }


__all__ = ["LocatorNavigationError", "MessageNavigator"]
