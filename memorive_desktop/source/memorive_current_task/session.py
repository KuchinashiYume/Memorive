from __future__ import annotations

from typing import Any, Mapping

from memorive_desktop_service.locator import StableLocator

from .actions import ActionController
from .contracts import FIXED_NODE_IDS, LEGACY_FIXED_NODE_IDS
from .projection import CurrentTaskProjector
from .refinement_tasks import REFINEMENT_NODE_IDS


class SessionError(ValueError):
    pass


class CurrentTaskSession:
    NODE_FILTER = "ct_selected_node"

    def __init__(self, projector: CurrentTaskProjector, control_store: Any, *, actions: ActionController | None = None):
        self.projector = projector
        self.control_store = control_store
        self.actions = actions

    @staticmethod
    def _job_locator(job_id: str) -> str:
        return StableLocator("job", job_id).render()

    def view(self) -> dict[str, Any]:
        envelope = self.control_store.load()
        state = envelope["state"]
        locator = state["selected_locator"]
        job_id = None
        if locator is not None:
            parsed = StableLocator.parse(locator)
            if parsed.kind != "job":
                raise SessionError("CURRENT_TASK_SELECTED_LOCATOR_NOT_JOB")
            job_id = parsed.object_id
        return {
            "schema_version": "CurrentTaskSessionView-v1",
            "revision": envelope["revision"],
            "route": state["route"],
            "job_id": job_id,
            "selected_node_id": state["filters"].get(self.NODE_FILTER),
            "event_cursor": None if job_id is None else state["event_cursor_by_job"].get(job_id, 0),
            "second_level_open": job_id is not None,
            "third_level_open": job_id is not None and state["filters"].get(self.NODE_FILTER) is not None,
        }

    def select_task(self, job_id: str) -> dict[str, Any]:
        if self.actions is not None:
            self.actions.context_changed()
        def mutate(state: dict[str, Any]) -> None:
            state["route"] = "current-task"
            state["selected_locator"] = self._job_locator(job_id)
            state["filters"] = {key: value for key, value in state["filters"].items() if not key.startswith("ct_")}
            state["filters"][self.NODE_FILTER] = None
        self.control_store.update(mutate)
        return self.view()

    def select_node(self, node_id: str) -> dict[str, Any]:
        if node_id not in (*FIXED_NODE_IDS, *LEGACY_FIXED_NODE_IDS, *REFINEMENT_NODE_IDS):
            raise SessionError("CURRENT_TASK_NODE_UNKNOWN")
        current = self.view()
        if current["job_id"] is None:
            raise SessionError("CURRENT_TASK_SELECTION_REQUIRES_JOB")
        if self.actions is not None:
            self.actions.context_changed()
        def mutate(state: dict[str, Any]) -> None:
            state["filters"][self.NODE_FILTER] = node_id
        self.control_store.update(mutate)
        return self.view()

    def close_all(self) -> dict[str, Any]:
        if self.actions is not None:
            self.actions.context_changed()
        def mutate(state: dict[str, Any]) -> None:
            state["selected_locator"] = None
            state["filters"] = {key: value for key, value in state["filters"].items() if not key.startswith("ct_")}
        self.control_store.update(mutate)
        return self.view()

    @staticmethod
    def _override_keys(node_id: str) -> tuple[str, str, str]:
        return (
            f"ct_enabled_{node_id}",
            f"ct_model_{node_id}",
            f"ct_profile_{node_id}",
        )

    def overrides(self) -> dict[str, dict[str, Any]]:
        state = self.control_store.load()["state"]
        result: dict[str, dict[str, Any]] = {}
        for node_id in dict.fromkeys((*FIXED_NODE_IDS, *LEGACY_FIXED_NODE_IDS)):
            enabled_key, model_key, profile_key = self._override_keys(node_id)
            row: dict[str, Any] = {}
            if enabled_key in state["filters"]:
                row["enabled"] = state["filters"][enabled_key]
            if model_key in state["filters"]:
                row["model"] = state["filters"][model_key]
            if profile_key in state["filters"]:
                row["profile_ref"] = state["filters"][profile_key]
            if row:
                result[node_id] = row
        return result

    def set_override(
        self,
        node_id: str,
        *,
        enabled: bool | None = None,
        model: str | None = None,
        profile_ref: str | None = None,
    ) -> dict[str, Any]:
        current = self.view()
        if current["job_id"] is None:
            raise SessionError("CURRENT_TASK_OVERRIDE_REQUIRES_JOB")
        projection = self.projector.project(current["job_id"], selected_node_id=node_id, overrides=self.overrides())
        node = next(row for row in projection["graph"]["workflow_nodes"] if row["node_id"] == node_id)
        if node["state"] not in {"NOT_STARTED", "DISABLED"}:
            raise SessionError("CURRENT_TASK_NODE_ALREADY_STARTED")
        if enabled is None and model is None:
            raise SessionError("CURRENT_TASK_OVERRIDE_EMPTY")
        if enabled is not None and not node["enable_toggle_eligible"]:
            raise SessionError("CURRENT_TASK_NODE_ENABLE_LOCKED")
        if model is not None and (not node["model_change_eligible"] or not isinstance(model, str) or not model.strip()):
            raise SessionError("CURRENT_TASK_NODE_MODEL_LOCKED")
        if profile_ref is not None and (
            model is None or not isinstance(profile_ref, str) or not profile_ref.strip()
        ):
            raise SessionError("CURRENT_TASK_NODE_PROFILE_INVALID")
        enabled_key, model_key, profile_key = self._override_keys(node_id)
        def mutate(state: dict[str, Any]) -> None:
            if enabled is not None:
                state["filters"][enabled_key] = enabled
            if model is not None:
                state["filters"][model_key] = model.strip()
                if profile_ref is not None:
                    state["filters"][profile_key] = profile_ref.strip()
        self.control_store.update(mutate)
        return {"schema_version": "CurrentTaskNodeOverrideReceipt-v1", "job_id": current["job_id"], "node_id": node_id, "enabled": enabled, "model": model.strip() if isinstance(model, str) else None, "profile_ref": profile_ref.strip() if isinstance(profile_ref, str) else None, "scope": "TASK_ONLY", "global_template_write": False, "started_node_write": False}

    def restore_creation_snapshot(self, node_id: str) -> dict[str, Any]:
        enabled_key, model_key, profile_key = self._override_keys(node_id)
        def mutate(state: dict[str, Any]) -> None:
            state["filters"].pop(enabled_key, None)
            state["filters"].pop(model_key, None)
            state["filters"].pop(profile_key, None)
        self.control_store.update(mutate)
        return {"schema_version": "CurrentTaskNodeOverrideResetReceipt-v1", "node_id": node_id, "restored": True, "scope": "TASK_ONLY"}

    def persist_cursor(self, job_id: str, sequence: int) -> dict[str, Any]:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise SessionError("CURRENT_TASK_CURSOR_INVALID")
        def mutate(state: dict[str, Any]) -> None:
            state["event_cursor_by_job"][job_id] = sequence
        self.control_store.update(mutate)
        return self.view()

    def refresh(self) -> dict[str, Any]:
        current = self.view()
        if current["job_id"] is None:
            return current
        projection = self.projector.project(current["job_id"], selected_node_id=current["selected_node_id"], overrides=self.overrides())
        if projection["selection_removed"]:
            def mutate(state: dict[str, Any]) -> None:
                state["filters"][self.NODE_FILTER] = None
            self.control_store.update(mutate)
        return {"session": self.view(), "projection": projection}
