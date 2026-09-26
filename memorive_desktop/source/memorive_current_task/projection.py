from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import (
    FIXED_NODES,
    TERMINAL_CONTROL_STATES,
    ContractError,
    default_nodes,
    exact_axes,
    immutable,
    state_presentation,
)
from .events import TimelineReconciler
from .locators import CurrentTaskLocator


class ProjectionError(ValueError):
    pass


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class CurrentTaskProjector:
    """Read-only DTO projection. Every business read goes through a facade adapter."""

    def __init__(self, adapter: Any, *, stalled_after_seconds: int = 120):
        self.adapter = adapter
        self.stalled_after_seconds = stalled_after_seconds

    def _call(self, method: str, **params: Any) -> Any:
        return self.adapter.call(method, params)

    def list_projection(self) -> dict[str, Any]:
        page = self._call("list_jobs")
        tasks = []
        for row in page.get("jobs", []):
            control_state = row.get("job_control_state", row.get("control_state"))
            try:
                presentation = state_presentation(str(control_state))
            except ContractError as exc:
                raise ProjectionError(str(exc)) from exc
            job_id = str(row["job_id"])
            tasks.append(
                {
                    "job_id": job_id,
                    "attempt_id": str(row["attempt_id"]),
                    "name": str(row.get("display_name") or row.get("job_name") or job_id),
                    "locator": CurrentTaskLocator("job", job_id).render(),
                    "state": presentation,
                    "job_type": row.get("job_type"),
                    "observed_version": int(row.get("observed_version", row.get("version", 0))),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )
        state_priority = {
            "RUNNING": 0,
            "PAUSE_REQUESTED": 0,
            "RESUME_REQUESTED": 0,
            "RECOVERY_REQUIRED": 0,
            "RECOVERING": 0,
            "PAUSED": 1,
            "CREATED": 2,
            "PREFLIGHT": 2,
            "QUEUED": 2,
            "CANCEL_REQUESTED": 3,
            "BLOCKED_BEFORE_START": 4,
            "FAILED": 4,
            "CANCELLED": 5,
            "SUCCEEDED": 5,
        }

        def task_order(row: Mapping[str, Any]) -> tuple[int, float, str]:
            control_state = str(row["state"]["control_state"])
            updated = _parse_time(row.get("updated_at"))
            updated_epoch = updated.timestamp() if updated is not None else 0.0
            return (
                state_priority.get(control_state, 6),
                -updated_epoch,
                str(row["job_id"]),
            )

        tasks.sort(key=task_order)
        return {
            "schema_version": "CurrentTaskListProjection-v1",
            "active_navigation_item": "CURRENT_TASK",
            "active_task_id": None,
            "selected_node_id": None,
            "second_level_open": False,
            "third_level_open": False,
            "view_state": "EMPTY" if not tasks else "QUEUED",
            "tasks": tasks,
        }

    @staticmethod
    def _workflow_nodes(job: Mapping[str, Any]) -> list[dict[str, Any]]:
        definition = job.get("snapshots", {}).get("workflow_definition", {}).get("content", {})
        nodes = default_nodes(definition if isinstance(definition, Mapping) else None)
        configured = definition.get("nodes", []) if isinstance(definition, Mapping) else []
        configured_by_id = {row.get("node_id"): row for row in configured if isinstance(row, Mapping)}
        config = job.get("snapshots", {}).get("workflow_config", {}).get("content", {})
        config_nodes = config.get("nodes", {}) if isinstance(config, Mapping) else {}
        for node in nodes:
            source = configured_by_id.get(node["node_id"], {})
            settings = config_nodes.get(node["node_id"], {}) if isinstance(config_nodes, Mapping) else {}
            if isinstance(source, Mapping):
                for field in ("name", "purpose", "model"):
                    if isinstance(source.get(field), str) and source[field]:
                        node[field] = source[field]
            if isinstance(settings, Mapping):
                if node["optional"] and isinstance(settings.get("enabled"), bool):
                    node["enabled"] = settings["enabled"]
                if node["model_change"] and isinstance(settings.get("model"), str) and settings["model"]:
                    node["model"] = settings["model"]
                for field in (
                    "profile_ref",
                    "profile_source_node_id",
                    "configuration_source",
                    "retry_count",
                    "capacity",
                ):
                    if field in settings:
                        node[field] = deepcopy(settings[field])
            node.setdefault("enabled", True)
        return nodes

    def _progress(self, job: Mapping[str, Any], control_state: str, *, now: datetime) -> dict[str, Any]:
        progress: Mapping[str, Any]
        if "get_progress" in getattr(self.adapter, "allowed_methods", ()):
            progress = self._call("get_progress", job_id=job["job_id"])
        else:
            progress = job.get("progress", {}) if isinstance(job.get("progress"), Mapping) else {}
        completed = int(progress.get("completed_units", 0))
        total = progress.get("total_units")
        if total is not None and (isinstance(total, bool) or not isinstance(total, int) or total <= 0 or completed > total):
            raise ProjectionError("PROGRESS_TOTAL_INVALID")
        indeterminate = total is None
        percent = None if indeterminate else round(completed * 100.0 / total, 6)
        updated = _parse_time(progress.get("updated_at"))
        stalled = False
        waiting = progress.get('phase_code') == 'QUEUED' or str(progress.get('phase_code', '')).startswith('WAITING_') or progress.get('waiting_reason') == 'scheduler'
        if control_state == "RUNNING" and not waiting and updated is not None:
            accepted_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
            stalled = (accepted_now - updated).total_seconds() >= self.stalled_after_seconds
        reported_phase_code = progress.get("phase_code")
        reported_phase_label = progress.get("phase_label")
        phase_code = reported_phase_code.removeprefix('WAITING_') if isinstance(reported_phase_code, str) else reported_phase_code
        phase_label = reported_phase_label
        request = job.get("request") if isinstance(job.get("request"), Mapping) else {}
        job_type = str(job.get("job_type") or request.get("job_type") or "").casefold()
        definition = job.get("snapshots", {}).get("workflow_definition", {}).get("content", {})
        configured_nodes = definition.get("nodes", []) if isinstance(definition, Mapping) else []
        configured_by_id = {
            str(row.get("node_id") or ""): row
            for row in configured_nodes
            if isinstance(row, Mapping)
        }
        configured_ids = set(configured_by_id)
        is_core = job_type in {"memorive-core", "core_document", "core-document"} or (
            "02_CHUNK_EMBEDDING" in configured_ids and total == len(FIXED_NODES)
        )
        if is_core and control_state != "SUCCEEDED" and completed < len(FIXED_NODES):
            reported_index = next(
                (
                    index
                    for index, row in enumerate(FIXED_NODES)
                    if row["node_id"] == phase_code
                ),
                None,
            )
            current = (
                FIXED_NODES[reported_index]
                if reported_index is not None and reported_index >= completed
                else FIXED_NODES[max(0, completed)]
            )
            phase_code = current["node_id"]
            configured_current = configured_by_id.get(phase_code, {})
            phase_label = str(configured_current.get("name") or current["name"])
        return {
            "schema_version": "CurrentTaskProgressProjection-v1",
            "job_id": job["job_id"],
            "attempt_id": job["attempt_id"],
            "mode": "INDETERMINATE" if indeterminate else "KNOWN_DENOMINATOR",
            "completed_units": completed,
            "total_units": total,
            "percent": percent,
            "estimated_completion_at": None,
            "phase_code": phase_code,
            "phase_label": phase_label,
            "reported_phase_code": reported_phase_code,
            "reported_phase_label": reported_phase_label,
            "sequence": int(progress.get("sequence", 0)),
            "stalled": stalled,
            "waiting": waiting and control_state not in TERMINAL_CONTROL_STATES,
            "resume_from_node_id": (request.get('core_retry') or {}).get('resume_from_node_id'),
            "recovering": control_state in {"RECOVERY_REQUIRED", "RECOVERING", "RESUME_REQUESTED"},
            "updated_at": progress.get("updated_at"),
        }

    @staticmethod
    def _apply_overrides(nodes: list[dict[str, Any]], overrides: Mapping[str, Any]) -> None:
        for node in nodes:
            row = overrides.get(node["node_id"], {}) if isinstance(overrides, Mapping) else {}
            if not isinstance(row, Mapping):
                continue
            if node["optional"] and isinstance(row.get("enabled"), bool):
                node["enabled"] = row["enabled"]
                node["configuration_source"] = "TASK_OVERRIDE"
            if node["model_change"] and isinstance(row.get("model"), str) and row["model"]:
                node["model"] = row["model"]
                node["configuration_source"] = "TASK_OVERRIDE"
                if isinstance(row.get("profile_ref"), str) and row["profile_ref"]:
                    node["profile_ref"] = row["profile_ref"]
            if isinstance(row.get("retry_count"), int) and not isinstance(row.get("retry_count"), bool):
                node["retry_count"] = row["retry_count"]
                node["configuration_source"] = "TASK_OVERRIDE"
        embedding = next(
            (row for row in nodes if row["node_id"] == "02_CHUNK_EMBEDDING"),
            None,
        )
        if embedding is not None:
            for node in nodes:
                if node.get("profile_source_node_id") == "02_CHUNK_EMBEDDING":
                    node["model"] = embedding["model"]
                    node["profile_ref"] = embedding.get("profile_ref")
                    node["configuration_source"] = "INHERITED_NODE_PROFILE"

    @staticmethod
    def _validate_artifact(row: Mapping[str, Any]) -> None:
        classification = str(row.get("classification", "PUBLIC_SAFE")).upper()
        locator = str(row.get("locator", ""))
        if classification in {"PRIVATE", "RESTRICTED", "GOLD", "HOLDOUT"} or row.get("private") is True:
            raise ProjectionError("PRIVATE_ARTIFACT_FORBIDDEN")
        lowered = locator.lower()
        if lowered.startswith("private:") or any(marker in lowered for marker in ("lane_a_private", "lane_b_private", "holdout", "private_gold")):
            raise ProjectionError("PRIVATE_ARTIFACT_FORBIDDEN")

    def project(
        self,
        job_id: str,
        *,
        selected_node_id: str | None = None,
        overrides: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        job = self._call("get_job", job_id=job_id)
        attempt = self._call("get_attempt", attempt_id=job["attempt_id"])
        if attempt.get("job_id") != job_id:
            raise ProjectionError("ATTEMPT_JOB_MISMATCH")
        attempt_id = str(job["attempt_id"])
        action_page = self._call("list_action_requests", job_id=job_id)
        pending_actions = [row for row in action_page.get("action_requests", []) if row.get("state") == "PENDING"]
        control_state = str(job.get("control_state", job.get("job_control_state")))
        try:
            presentation = state_presentation(control_state, pending_action=bool(pending_actions))
            axes = exact_axes(job)
        except ContractError as exc:
            raise ProjectionError(str(exc)) from exc
        nodes = self._workflow_nodes(job)
        self._apply_overrides(nodes, overrides or {})
        progress = self._progress(job, control_state, now=now or datetime.now(timezone.utc))
        if progress.get('waiting') and control_state == 'RUNNING':
            presentation = state_presentation('QUEUED', pending_action=bool(pending_actions))
        active_phase = progress.get("phase_code")
        node_ids_in_order = [row["node_id"] for row in nodes]
        active_index = (
            node_ids_in_order.index(active_phase)
            if active_phase in node_ids_in_order
            else None
        )
        for index, node in enumerate(nodes):
            if not node["enabled"]:
                node_state = "DISABLED"
            elif control_state == "SUCCEEDED":
                node_state = "COMPLETED"
            elif index < int(progress.get("completed_units", 0)):
                node_state = "COMPLETED"
            elif active_index is None:
                node_state = "FAILED" if control_state == "FAILED" and index == 0 else "NOT_STARTED"
            elif index < active_index:
                node_state = "COMPLETED"
            elif index > active_index:
                node_state = "NOT_STARTED"
            elif control_state == "PAUSED":
                node_state = "PAUSED"
            elif progress.get('waiting'):
                node_state = "NOT_STARTED"
            elif pending_actions:
                node_state = "WAITING_APPROVAL"
            elif control_state == "FAILED":
                node_state = "FAILED"
            elif control_state in {"RUNNING", "PAUSE_REQUESTED", "RESUME_REQUESTED", "RECOVERY_REQUIRED", "RECOVERING"}:
                node_state = "RUNNING"
            elif control_state == "PAUSED":
                node_state = "PAUSED"
            elif control_state in TERMINAL_CONTROL_STATES:
                node_state = "COMPLETED" if control_state == "SUCCEEDED" else "FAILED"
            else:
                node_state = "NOT_STARTED"
            node_adjustable = node_state in {"NOT_STARTED", "DISABLED"} or (
                node_state == "FAILED" and control_state in TERMINAL_CONTROL_STATES
            )
            node.update(
                {
                    "state": node_state,
                    # Runtime evidence does not yet expose a durable consumed
                    # retry counter.  Zero is only provable before execution;
                    # later states stay unknown instead of fabricating usage.
                    "retry_consumed": 0 if node_state in {"NOT_STARTED", "DISABLED"} else None,
                    "retry_remaining": (
                        int(node.get("retry_count", 0))
                        if node_state in {"NOT_STARTED", "DISABLED"}
                        else None
                    ),
                    "configuration_source": node.get("configuration_source", "TASK_CREATION_SNAPSHOT"),
                    "locator": CurrentTaskLocator("step", node["node_id"], job_id=job_id, attempt_id=attempt_id).render(),
                    "adjustment_eligible": node_adjustable and (node["optional"] or node["model_change"]),
                    "enable_toggle_eligible": node_adjustable and node["optional"],
                    "model_change_eligible": node_adjustable and node["model_change"],
                    "retry_change_eligible": node_adjustable and node["model_change"],
                    "rollback_eligible": (
                        node_state == "COMPLETED"
                        and control_state in TERMINAL_CONTROL_STATES
                    ),
                }
            )
        node_ids = {row["node_id"] for row in nodes}
        selection_removed = selected_node_id is not None and selected_node_id not in node_ids
        accepted_selection = None if selection_removed else selected_node_id

        event_page = self._call("get_job_events", job_id=job_id, after_sequence=0)
        reconciler = TimelineReconciler(job_id, attempt_id)
        event_receipt = reconciler.ingest(event_page.get("events", []))
        timeline = []
        for event in reconciler.timeline():
            event_id = str(event.get("event_id") or f"event_{event['sequence']:08d}")
            timeline.append({**event, "event_id": event_id, "locator": CurrentTaskLocator("event", event_id, job_id=job_id).render()})
        artifacts_page = self._call("get_artifact_bindings", job_id=job_id)
        artifacts = []
        for row in artifacts_page.get("artifacts", []):
            self._validate_artifact(row)
            artifact_id = str(row["artifact_id"])
            artifacts.append({**row, "stable_locator": CurrentTaskLocator("artifact", artifact_id, job_id=job_id).render()})
        action_rows = []
        for row in action_page.get("action_requests", []):
            action_id = str(row["action_request_id"])
            action_rows.append({**row, "stable_locator": CurrentTaskLocator("action", action_id, job_id=job_id).render()})
        graph = self._call("get_job_graph", job_id=job_id)
        return immutable(
            {
                "schema_version": "CurrentTaskProjection-v1",
                "route": "current-task",
                "job_id": job_id,
                "attempt_id": attempt_id,
                "job_locator": CurrentTaskLocator("job", job_id).render(),
                "attempt_locator": CurrentTaskLocator("attempt", attempt_id).render(),
                "view_state": presentation["view_state"],
                "state_presentation": presentation,
                "state_axes": axes,
                "graph": {"schema_version": "CurrentTaskJobGraphProjection-v1", "retry_lineage": graph.get("nodes", []), "workflow_nodes": nodes},
                "timeline": {"schema_version": "CurrentTaskTimelineProjection-v1", "events": timeline, "reconciliation": event_receipt},
                "progress": progress,
                "actions": {"schema_version": "CurrentTaskActionProjection-v1", "pending_count": len(pending_actions), "rows": action_rows},
                "artifacts": {"schema_version": "CurrentTaskArtifactProjection-v1", "rows": artifacts},
                "selected_node_id": accepted_selection,
                "selection_removed": selection_removed,
                "second_level_open": True,
                "third_level_open": accepted_selection is not None,
                "direct_store_access": False,
                "direct_filesystem_access": False,
                "estimated_completion_fabricated": False,
            }
        )
