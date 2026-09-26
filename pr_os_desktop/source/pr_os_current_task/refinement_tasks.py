from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Mapping
import uuid

from .contracts import canonical_json_bytes, state_presentation
from pr_os_folder_management.policy import windows_io_path


REFINEMENT_NODES = (
    {
        "node_id": "01_SESSION_SELECTION_FREEZE",
        "number": "01",
        "name": "会话选择与冻结",
        "purpose": "确认所选会话并生成只读内容快照",
        "model": "[默认] 本地快照",
    },
    {
        "node_id": "02_DIALOGUE_CLEANUP",
        "number": "02",
        "name": "对话清理",
        "purpose": "清理空白与重复结构，保留原始语义",
        "model": "[默认] 本地清理",
    },
    {
        "node_id": "03_REFINEMENT_MODEL_EXECUTION",
        "number": "03",
        "name": "精炼模型执行",
        "purpose": "通过用户已配置的精炼模型生成结构化草稿",
        "model": "已配置精炼模型",
    },
    {
        "node_id": "04_RESULT_VERIFY_PERSIST",
        "number": "04",
        "name": "结果校核/落盘",
        "purpose": "核对精炼结果与执行回执，并保存为待复核草稿",
        "model": "本地校核",
    },
)
REFINEMENT_NODE_IDS = tuple(row["node_id"] for row in REFINEMENT_NODES)
_TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
_HEX_64 = re.compile(r"[0-9A-Fa-f]{64}")


class RefinementTaskError(ValueError):
    pass


class RefinementTaskCancelled(RefinementTaskError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _safe_text(value: Any, maximum: int) -> str:
    return str(value or "").replace("\x00", "").strip()[:maximum]


def safe_error_code(value: Any) -> str:
    rendered = _safe_text(value, 320).upper()
    return re.sub(r"[^A-Z0-9_]+", "_", rendered).strip("_")[:120] or "REFINEMENT_TASK_FAILED"


class SessionRefinementTaskStore:
    """Durable metadata-only store for the dedicated session-refinement workflow."""

    def __init__(self, state_root: Path):
        self.state_root = Path(state_root).resolve()
        windows_io_path(self.state_root).mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_root / "session_refinement_tasks_v1.json"
        self._lock = threading.RLock()
        if not windows_io_path(self.state_path).exists():
            self._write_state(
                {
                    "schema_version": "P08SessionRefinementTaskState-v1",
                    "revision": 0,
                    "tasks": {},
                }
            )
        self._validate_state(self._read_state())

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(windows_io_path(self.state_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RefinementTaskError("REFINEMENT_TASK_STATE_UNAVAILABLE") from exc
        self._validate_state(value)
        return value

    def _write_state(self, value: Mapping[str, Any]) -> None:
        accepted = deepcopy(dict(value))
        self._validate_state(accepted)
        target = windows_io_path(self.state_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".aw.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            with temporary.open("wb") as stream:
                stream.write(canonical_json_bytes(accepted))
                stream.write(b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _validate_state(value: Any) -> None:
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "revision", "tasks"}:
            raise RefinementTaskError("REFINEMENT_TASK_STATE_FIELDS_INVALID")
        if value.get("schema_version") != "P08SessionRefinementTaskState-v1":
            raise RefinementTaskError("REFINEMENT_TASK_STATE_SCHEMA_INVALID")
        if isinstance(value.get("revision"), bool) or not isinstance(value.get("revision"), int):
            raise RefinementTaskError("REFINEMENT_TASK_STATE_REVISION_INVALID")
        tasks = value.get("tasks")
        if not isinstance(tasks, Mapping):
            raise RefinementTaskError("REFINEMENT_TASK_STATE_TASKS_INVALID")
        for job_id, task in tasks.items():
            if not isinstance(job_id, str) or not isinstance(task, Mapping) or task.get("job_id") != job_id:
                raise RefinementTaskError("REFINEMENT_TASK_RECORD_INVALID")
            if task.get("workflow_kind") != "SESSION_REFINEMENT":
                raise RefinementTaskError("REFINEMENT_TASK_WORKFLOW_KIND_INVALID")
            if task.get("control_state") not in {"QUEUED", "RUNNING", *_TERMINAL_STATES}:
                raise RefinementTaskError("REFINEMENT_TASK_CONTROL_STATE_INVALID")
            nodes = task.get("nodes")
            if not isinstance(nodes, list) or [row.get("node_id") for row in nodes if isinstance(row, Mapping)] != list(REFINEMENT_NODE_IDS):
                raise RefinementTaskError("REFINEMENT_TASK_NODES_INVALID")

    def _mutate(self, callback: Any) -> Any:
        with self._lock:
            state = self._read_state()
            result = callback(state)
            state["revision"] += 1
            self._write_state(state)
            return deepcopy(result)

    @staticmethod
    def _task(state: Mapping[str, Any], job_id: str) -> dict[str, Any]:
        task = state["tasks"].get(job_id)
        if not isinstance(task, dict):
            raise RefinementTaskError("REFINEMENT_TASK_NOT_FOUND")
        return task

    def has(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._read_state()["tasks"]

    def create_task(
        self,
        *,
        display_name: str,
        local_projection_id: str,
        source_snapshot_sha256: str,
        selected_profile_ref: str,
        message_count: int,
    ) -> dict[str, Any]:
        accepted_name = _safe_text(display_name, 160)
        projection_id = _safe_text(local_projection_id, 192)
        profile_ref = _safe_text(selected_profile_ref, 192)
        if not accepted_name or not projection_id or not profile_ref:
            raise RefinementTaskError("REFINEMENT_TASK_INPUT_INVALID")
        if not _HEX_64.fullmatch(str(source_snapshot_sha256)):
            raise RefinementTaskError("REFINEMENT_TASK_SNAPSHOT_HASH_INVALID")
        if isinstance(message_count, bool) or not isinstance(message_count, int) or not 1 <= message_count <= 100000:
            raise RefinementTaskError("REFINEMENT_TASK_MESSAGE_COUNT_INVALID")
        job_id = f"refinement-{uuid.uuid4().hex}"
        attempt_id = f"attempt-{uuid.uuid4().hex}"
        now = _utc_now()
        nodes = []
        for definition in REFINEMENT_NODES:
            node = dict(definition)
            if node["node_id"] == "03_REFINEMENT_MODEL_EXECUTION":
                node["model"] = profile_ref
            node.update(
                {
                    "state": "NOT_STARTED",
                    "started_at": None,
                    "completed_at": None,
                    "error_code": "",
                    "optional": False,
                    "model_change": False,
                    "locked": node["node_id"] != "03_REFINEMENT_MODEL_EXECUTION",
                    "adjustment_eligible": node["node_id"] == "03_REFINEMENT_MODEL_EXECUTION",
                    "enable_toggle_eligible": False,
                    "model_change_eligible": node["node_id"] == "03_REFINEMENT_MODEL_EXECUTION",
                }
            )
            nodes.append(node)
        task = {
            "schema_version": "P08SessionRefinementTask-v1",
            "job_id": job_id,
            "attempt_id": attempt_id,
            "workflow_kind": "SESSION_REFINEMENT",
            "display_name": accepted_name,
            "local_projection_id": projection_id,
            "source_snapshot_sha256": str(source_snapshot_sha256).upper(),
            "selected_profile_ref": profile_ref,
            "message_count": message_count,
            "control_state": "QUEUED",
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "current_node_id": None,
            "nodes": nodes,
            "draft_id": "",
            "error_code": "",
            "model_calls": 0,
            "external_model_calls": 0,
            "pause_supported": False,
            "safe_checkpoint": True,
            "frozen_input_hashes": {"session_snapshot": str(source_snapshot_sha256).upper()},
            "current_input_hashes": {"session_snapshot": str(source_snapshot_sha256).upper()},
            "action_requests": [],
        }

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            state["tasks"][job_id] = task
            return task

        return self._mutate(mutate)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._task(self._read_state(), job_id))

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            values = list(self._read_state()["tasks"].values())
        return sorted((deepcopy(row) for row in values), key=lambda row: row["created_at"], reverse=True)

    def remove_tasks(self, job_ids: list[str] | tuple[str, ...]) -> dict[str, Any]:
        accepted = tuple(dict.fromkeys(_safe_text(value, 160) for value in job_ids))
        if not accepted or any(not value for value in accepted):
            raise RefinementTaskError("REFINEMENT_TASK_REMOVAL_IDS_INVALID")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            missing = [job_id for job_id in accepted if job_id not in state["tasks"]]
            if missing:
                raise RefinementTaskError("REFINEMENT_TASK_REMOVAL_TARGET_MISSING")
            removed = [deepcopy(state["tasks"].pop(job_id)) for job_id in accepted]
            return {
                "schema_version": "SessionRefinementTaskRemovalReceipt-v1",
                "removed_job_ids": list(accepted),
                "removed_count": len(removed),
                "remaining_count": len(state["tasks"]),
                "status": "PASS",
            }

        return self._mutate(mutate)

    # Sessions owns the refinement queue.  These aliases preserve the existing
    # execution protocol without routing queue records through Current Task.
    def create_session_refinement_task(self, **params: Any) -> dict[str, Any]:
        return self.create_task(**params)

    def begin_session_refinement_step(self, job_id: str, node_id: str) -> dict[str, Any]:
        return self.begin_step(job_id, node_id)

    def get_session_refinement_task(self, job_id: str) -> dict[str, Any]:
        return self.get_job(job_id)

    def complete_session_refinement_step(self, job_id: str, node_id: str) -> dict[str, Any]:
        return self.complete_step(job_id, node_id)

    def succeed_session_refinement_task(self, job_id: str, **params: Any) -> dict[str, Any]:
        return self.succeed(job_id, **params)

    def fail_session_refinement_task(self, job_id: str, **params: Any) -> dict[str, Any]:
        return self.fail(job_id, **params)

    def session_refinement_cancelled(self, job_id: str) -> bool:
        return self.is_cancelled(job_id)

    def find_session_refinement_task(
        self, local_projection_id: str
    ) -> dict[str, Any] | None:
        projection_id = _safe_text(local_projection_id, 192)
        if not projection_id:
            raise RefinementTaskError("REFINEMENT_TASK_PROJECTION_ID_INVALID")
        matches = [
            task
            for task in self.list_jobs()
            if task.get("local_projection_id") == projection_id
        ]
        if not matches:
            return None
        active = next(
            (
                task
                for task in matches
                if task.get("control_state") in {"QUEUED", "RUNNING"}
            ),
            None,
        )
        return deepcopy(active or matches[0])

    def is_cancelled(self, job_id: str) -> bool:
        return self.get_job(job_id)["control_state"] == "CANCELLED"

    def begin_step(self, job_id: str, node_id: str) -> dict[str, Any]:
        if node_id not in REFINEMENT_NODE_IDS:
            raise RefinementTaskError("REFINEMENT_TASK_NODE_UNKNOWN")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            task = self._task(state, job_id)
            if task["control_state"] == "CANCELLED":
                raise RefinementTaskCancelled("REFINEMENT_TASK_CANCELLED")
            if task["control_state"] in {"SUCCEEDED", "FAILED"}:
                raise RefinementTaskError("REFINEMENT_TASK_TERMINAL")
            index = REFINEMENT_NODE_IDS.index(node_id)
            if any(row["state"] != "COMPLETED" for row in task["nodes"][:index]):
                raise RefinementTaskError("REFINEMENT_TASK_NODE_ORDER_INVALID")
            node = task["nodes"][index]
            if node["state"] == "COMPLETED":
                return task
            if node["state"] not in {"NOT_STARTED", "RUNNING"}:
                raise RefinementTaskError("REFINEMENT_TASK_NODE_STATE_INVALID")
            now = _utc_now()
            node["state"] = "RUNNING"
            node["started_at"] = node["started_at"] or now
            node["locked"] = True
            node["adjustment_eligible"] = False
            node["model_change_eligible"] = False
            task["control_state"] = "RUNNING"
            task["started_at"] = task["started_at"] or now
            task["current_node_id"] = node_id
            task["updated_at"] = now
            task["version"] += 1
            return task

        return self._mutate(mutate)

    def set_model(
        self,
        job_id: str,
        *,
        node_id: str,
        profile_ref: str,
        display_name: str,
    ) -> dict[str, Any]:
        if node_id != "03_REFINEMENT_MODEL_EXECUTION":
            raise RefinementTaskError("REFINEMENT_TASK_NODE_MODEL_LOCKED")
        accepted_ref = _safe_text(profile_ref, 192)
        accepted_name = _safe_text(display_name, 192)
        if not accepted_ref or not accepted_name:
            raise RefinementTaskError("REFINEMENT_TASK_MODEL_INVALID")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            task = self._task(state, job_id)
            if task["control_state"] != "QUEUED":
                raise RefinementTaskError("REFINEMENT_TASK_NODE_ALREADY_STARTED")
            node = task["nodes"][REFINEMENT_NODE_IDS.index(node_id)]
            if node["state"] != "NOT_STARTED" or node.get("model_change_eligible") is not True:
                raise RefinementTaskError("REFINEMENT_TASK_NODE_ALREADY_STARTED")
            now = _utc_now()
            task["selected_profile_ref"] = accepted_ref
            node["model"] = accepted_name
            node["model_change"] = True
            task["updated_at"] = now
            task["version"] += 1
            return task

        return self._mutate(mutate)

    def complete_step(self, job_id: str, node_id: str) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            task = self._task(state, job_id)
            if task["control_state"] == "CANCELLED":
                raise RefinementTaskCancelled("REFINEMENT_TASK_CANCELLED")
            if node_id not in REFINEMENT_NODE_IDS:
                raise RefinementTaskError("REFINEMENT_TASK_NODE_UNKNOWN")
            node = task["nodes"][REFINEMENT_NODE_IDS.index(node_id)]
            if node["state"] == "COMPLETED":
                return task
            if node["state"] != "RUNNING":
                raise RefinementTaskError("REFINEMENT_TASK_NODE_NOT_RUNNING")
            now = _utc_now()
            node["state"] = "COMPLETED"
            node["completed_at"] = now
            task["current_node_id"] = None
            task["updated_at"] = now
            task["version"] += 1
            return task

        return self._mutate(mutate)

    def succeed(
        self,
        job_id: str,
        *,
        draft_id: str,
        model_calls: int,
        external_model_calls: int,
    ) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            task = self._task(state, job_id)
            if task["control_state"] == "CANCELLED":
                raise RefinementTaskCancelled("REFINEMENT_TASK_CANCELLED")
            if any(row["state"] != "COMPLETED" for row in task["nodes"]):
                raise RefinementTaskError("REFINEMENT_TASK_INCOMPLETE")
            now = _utc_now()
            task.update(
                {
                    "control_state": "SUCCEEDED",
                    "completed_at": now,
                    "updated_at": now,
                    "current_node_id": None,
                    "draft_id": _safe_text(draft_id, 192),
                    "model_calls": int(model_calls),
                    "external_model_calls": int(external_model_calls),
                    "version": task["version"] + 1,
                }
            )
            return task

        return self._mutate(mutate)

    def fail(self, job_id: str, *, error_code: str, model_calls: int = 0) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            task = self._task(state, job_id)
            if task["control_state"] == "CANCELLED":
                return task
            now = _utc_now()
            accepted_error = safe_error_code(error_code)
            for node in task["nodes"]:
                if node["state"] == "RUNNING":
                    node.update({"state": "FAILED", "completed_at": now, "error_code": accepted_error})
            task.update(
                {
                    "control_state": "FAILED",
                    "completed_at": now,
                    "updated_at": now,
                    "current_node_id": None,
                    "error_code": accepted_error,
                    "model_calls": int(model_calls),
                    "version": task["version"] + 1,
                }
            )
            return task

        return self._mutate(mutate)

    def cancel(self, job_id: str, *, expected_version: int | None = None) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            task = self._task(state, job_id)
            if expected_version is not None and task["version"] != expected_version:
                raise RefinementTaskError("REFINEMENT_TASK_VERSION_CONFLICT")
            if task["control_state"] == "CANCELLED":
                return task
            if task["control_state"] not in {"QUEUED", "RUNNING"}:
                raise RefinementTaskError("REFINEMENT_TASK_CANCEL_NOT_ELIGIBLE")
            now = _utc_now()
            for node in task["nodes"]:
                if node["state"] != "COMPLETED":
                    node.update({"state": "CANCELLED", "completed_at": now})
            task.update(
                {
                    "control_state": "CANCELLED",
                    "completed_at": now,
                    "updated_at": now,
                    "current_node_id": None,
                    "version": task["version"] + 1,
                }
            )
            return task

        return self._mutate(mutate)

    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        if method == "get_job":
            return self.get_job(str(params["job_id"]))
        if method == "cancel_job":
            task = self.cancel(str(params["job_id"]), expected_version=int(params["expected_version"]))
            return {
                "schema_version": "P08SessionRefinementCancelReceipt-v1",
                "job_id": task["job_id"],
                "attempt_id": task["attempt_id"],
                "state": task["control_state"],
                "observed_version": task["version"],
                "status": "PASS",
            }
        raise RefinementTaskError(f"REFINEMENT_TASK_METHOD_NOT_ALLOWLISTED:{method}")

    def bindings(self) -> list[dict[str, Any]]:
        return [
            {
                "run": task["job_id"],
                "display_name": task["display_name"],
                "job_id": task["job_id"],
                "attempt_id": task["attempt_id"],
                "control_state": task["control_state"],
                "workflow_kind": "SESSION_REFINEMENT",
                "local_projection_id": task["local_projection_id"],
                "selected_profile_ref": task["selected_profile_ref"],
                "error_code": task["error_code"],
                "workflow_nodes": deepcopy(task["nodes"]),
                "model_calls": task["model_calls"],
                "external_model_calls": task["external_model_calls"],
                "created_at": task["created_at"],
                "updated_at": task["updated_at"],
                "started_at": task["started_at"],
                "completed_at": task["completed_at"],
                "locator": f"pr-os://job/{task['job_id']}",
                "synthetic_only": False,
            }
            for task in self.list_jobs()
        ]

    def project(self, job_id: str, *, selected_node_id: str | None = None) -> dict[str, Any]:
        task = self.get_job(job_id)
        nodes = deepcopy(task["nodes"])
        for node in nodes:
            node["configuration_source"] = "SESSION_REFINEMENT_CREATION_SNAPSHOT"
            node["locator"] = f"pr-os://step/{node['node_id']}?job_id={job_id}&attempt_id={task['attempt_id']}"
        accepted_selection = selected_node_id if selected_node_id in REFINEMENT_NODE_IDS else None
        completed = sum(row["state"] == "COMPLETED" for row in nodes)
        return {
            "schema_version": "CurrentTaskProjection-v1",
            "route": "current-task",
            "workflow_kind": "SESSION_REFINEMENT",
            "job_id": job_id,
            "attempt_id": task["attempt_id"],
            "job_locator": f"pr-os://job/{job_id}",
            "attempt_locator": f"pr-os://attempt/{task['attempt_id']}?job_id={job_id}",
            "view_state": state_presentation(task["control_state"])["view_state"],
            "state_presentation": state_presentation(task["control_state"]),
            "state_axes": {
                "job_control_state": task["control_state"],
                "verification_result": "PASS" if task["control_state"] == "SUCCEEDED" else "NOT_ASSESSED",
                "acceptance_verdict": "NOT_ASSESSED",
                "technical_handoff_status": "NOT_READY",
                "formalization_status": "NOT_AUTHORIZED",
                "deployment_status": "disabled",
            },
            "graph": {
                "schema_version": "CurrentTaskJobGraphProjection-v1",
                "retry_lineage": [],
                "workflow_nodes": nodes,
            },
            "timeline": {"schema_version": "CurrentTaskTimelineProjection-v1", "events": [], "reconciliation": None},
            "progress": {
                "completed_units": completed,
                "total_units": len(nodes),
                "percent": int(completed * 100 / len(nodes)),
                "phase_code": task["current_node_id"] or task["control_state"],
                "phase_label": next((row["name"] for row in nodes if row["node_id"] == task["current_node_id"]), task["control_state"]),
            },
            "actions": {"schema_version": "CurrentTaskActionProjection-v1", "pending_count": 0, "rows": []},
            "artifacts": {"schema_version": "CurrentTaskArtifactProjection-v1", "rows": []},
            "selected_node_id": accepted_selection,
            "selection_removed": selected_node_id is not None and accepted_selection is None,
            "second_level_open": True,
            "third_level_open": accepted_selection is not None,
            "display_name": task["display_name"],
            "local_projection_id": task["local_projection_id"],
            "selected_profile_ref": task["selected_profile_ref"],
            "draft_id": task["draft_id"],
            "error_code": task["error_code"],
            "model_calls": task["model_calls"],
            "external_model_calls": task["external_model_calls"],
            "direct_store_access": False,
            "direct_filesystem_access": False,
            "estimated_completion_fabricated": False,
        }


__all__ = [
    "REFINEMENT_NODES",
    "REFINEMENT_NODE_IDS",
    "RefinementTaskCancelled",
    "RefinementTaskError",
    "SessionRefinementTaskStore",
    "safe_error_code",
]
