from __future__ import annotations

import uuid

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from .actions import ActionController
from .projection import CurrentTaskProjector
from .recovery import retry_counters, lineage_partition, TERMINAL as RECOVERY_TERMINAL
from .refinement_tasks import REFINEMENT_NODE_IDS, SessionRefinementTaskStore
from .session import CurrentTaskSession
from .workflow_mapping import build_core_execution_snapshot
from memorive_workflow.task_controls import TaskControlStore
from memorive_desktop_service.client import RemoteCallError


RUN_ID = "CURRENT_TASK_CROSS_html_baseline_current_task_replay_20260825_run001"
CURRENT_TASK_METHODS = frozenset({
    "current_task.get_contract",
    "current_task.list",
    "current_task.select_task",
    "current_task.select_node",
    "current_task.view",
    "current_task.start_core",
    "current_task.refresh",
    "current_task.pause",
    "current_task.pause_node",
    "current_task.resume_node",
    "current_task.resume",
    "current_task.begin_cancel",
    "current_task.confirm_cancel",
    "current_task.delete_failed",
    "current_task.retry",
    "current_task.rollback_node",
    "current_task.restart_status",
    "current_task.respond",
    "current_task.set_override",
    "current_task.restore_snapshot",
    "current_task.close_all",
    "current_task.effect_metrics",
})

NODE_BY_UI = {
    "ingest": "01_DOCUMENT_INGEST",
    "embedding": "02_CHUNK_EMBEDDING",
    "card": "03_CARD_DISTILL",
    "card-review": "04_CARD_CROSS_CHECK",
    "admission": "05_CARD_ADMISSION",
    "context": "06_CONTEXT_PACK",
    "analysis": "07_ANALYSIS",
    "judgment-review": "08_JUDGMENT_CROSS_CHECK",
    "human": "09_HUMAN_FINAL",
}

LEGACY_NODE_BY_UI = {
    "ingest": "01_DOCUMENT_INGEST",
    "card": "02_CARD_DISTILL",
    "card-review": "03_CARD_CROSS_CHECK",
    "admission": "04_CARD_ADMISSION",
    "context": "05_CONTEXT_PACK",
    "analysis": "06_ANALYSIS",
    "judgment-review": "07_JUDGMENT_CROSS_CHECK",
    "human": "08_HUMAN_FINAL",
}

REFINEMENT_NODE_BY_UI = {
    "ingest": "01_SESSION_SELECTION_FREEZE",
    "card": "02_DIALOGUE_CLEANUP",
    "card-review": "03_REFINEMENT_MODEL_EXECUTION",
    "admission": "04_RESULT_VERIFY_PERSIST",
}

VISUAL_FIXTURES = (
    ("run-042", "文献摄取质量闭环", "WAITING_HUMAN", "01_DOCUMENT_INGEST", 0),
    ("run-043", "设置快照验证", "RUNNING", "07_ANALYSIS", 6),
    ("run-041", "会话索引一致性复核", "FAILED", "04_CARD_CROSS_CHECK", 3),
    ("run-038", "完成资料核对", "SUCCEEDED", "09_HUMAN_FINAL", 9),
)


class _ApplicationServiceAdapter:
    allowed_methods = (
        "list_jobs", "get_job", "get_attempt", "get_job_graph", "get_job_events",
        "watch_job_events", "list_action_requests", "respond_to_action",
        "get_artifact_bindings", "get_terminal_receipt", "resolve_job_locator",
        "lookup_start_by_idempotency_key", "start_job", "get_status", "get_progress",
        "record_action_request", "bind_artifact", "mark_running", "set_safe_checkpoint",
        "pause_job", "cancel_job", "resume_job", "record_progress", "retry_job",
        "list_capabilities", "run_doctor", "build_support_bundle",
        "current_task.fixture_transition",
        "console.control_task",
    )

    def __init__(self, service: Any):
        self.service = service

    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        if method not in self.allowed_methods:
            raise ValueError(f"CURRENT_TASK_SERVICE_METHOD_NOT_ALLOWLISTED:{method}")
        return self.service.call(method, dict(params))


def _profile() -> dict[str, Any]:
    return {
        "profile_id": "current_task-offline-synthetic",
        "role": "analysis",
        "capability_revision": "current_task-synthetic-v1",
        "config_revision": "current_task-synthetic-config-v1",
        "config_sha256": "A" * 64,
        "credential_refs": [],
        "execution_channel": "offline_synthetic_application_service",
        "fallback_policy": "FAIL_CLOSED",
        "resource_policy": {},
        "budget_policy": {"cost_cny": 0, "cost_usd": 0},
        "egress_policy": "ZERO_EGRESS",
        "data_classification": "SYNTHETIC_PUBLIC_SAFE",
        "module_revisions": {"M02": "current", "M08": "current", "RUNTIME_LOG": "current", "ARTIFACT_REGISTRY": "current"},
        "source_hashes": {"input": "B" * 64},
        "created_at": "2026-08-25T00:00:00+00:00",
        "resolver_revision": "CURRENT_TASK_SYNTHETIC_V1",
    }


def _workflow_definition() -> dict[str, Any]:
    rows = (
        ("01_DOCUMENT_INGEST", "文档处理与内容理解", "转换、清洗、切块、内容理解与复杂版面/OCR", "[默认] 本地解析"),
        ("02_CHUNK_EMBEDDING", "Chunk Embedding", "为 chunk 生成可检索的向量嵌入", "尚未选择向量嵌入模型"),
        ("03_CARD_DISTILL", "Card 蒸馏", "忠实抽取并生成待处理 Card", "DeepSeek V4 Pro"),
        ("04_CARD_CROSS_CHECK", "Card 搬运类校核", "回到原文核对字段、来源与搬运准确性", "按蒸馏模型自动选择异源校核者"),
        ("05_CARD_ADMISSION", "Card 准入", "根据准入规则形成可用或隔离状态", "确定性状态机 / 不调用模型"),
        ("06_CONTEXT_PACK", "Context Pack", "用 02 的同一嵌入空间检索并组装分析材料", "同步 02 · 尚未选择向量嵌入模型"),
        ("07_ANALYSIS", "Analysis 分析", "基于已选材料生成分析建议", "Claude Sonnet 5"),
        ("08_JUDGMENT_CROSS_CHECK", "判断类异源核对", "核对分析结论与引用证据", "按 Analysis 生产者自动选择异源校核者"),
        ("09_HUMAN_FINAL", "人类最终判断", "完成最终判断与写作", "用户 / 不调用模型"),
    )
    return {
        "schema_version": "CurrentTaskSyntheticWorkflowDefinition-v1",
        "workflow_id": "current_task-nine-node",
        "nodes": [
            {"node_id": node_id, "name": name, "purpose": purpose, "model": model}
            for node_id, name, purpose, model in rows
        ],
    }


def _request(
    run: str,
    name: str,
    execution_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    suffix = run.replace("-", "_")
    workflow_definition = (
        deepcopy(execution_snapshot["workflow_definition"])
        if execution_snapshot is not None
        else _workflow_definition()
    )
    workflow_config = (
        deepcopy(execution_snapshot["workflow_config"])
        if execution_snapshot is not None
        else {
            "schema_version": "CurrentTaskSyntheticWorkflowConfig-v1",
            "nodes": {
                "04_CARD_CROSS_CHECK": {"enabled": True, "model": "按蒸馏模型自动选择异源校核者"},
                "06_CONTEXT_PACK": {"enabled": True, "model": "同步 02 · 尚未选择向量嵌入模型", "profile_source_node_id": "02_CHUNK_EMBEDDING", "configuration_source": "INHERITED_NODE_PROFILE"},
                "08_JUDGMENT_CROSS_CHECK": {"enabled": False, "model": "按 Analysis 生产者自动选择异源校核者"},
            },
        }
    )
    return {
        "contract_revision": "1.0",
        "request_id": f"syn-request-{suffix}",
        "correlation_id": f"syn-correlation-{suffix}",
        "job_type": "synthetic-current_task-current-task",
        "payload_ref": f"fixture:desktop:current-task:{run}",
        "input_hashes": {"input": "B" * 64},
        "profile_snapshot": _profile(),
        "capabilities": [
            {
                "capability_id": "current-task-core",
                "requirement": "required",
                "availability": "ready",
                "qualification": "qualified",
                "effective_enabled": True,
                "reason_code": "NONE",
                "required_action": "none",
            }
        ],
        "pause_supported": True,
        "resolved_task_input": {
            "classification": "SYNTHETIC_PUBLIC_SAFE",
            "fixture_id": run,
            "display_name": name,
        },
        "workflow_definition": workflow_definition,
        "workflow_config": workflow_config,
    }


class CurrentTaskProductController:
    """Thin UI controller; all business reads and commands cross Application Service."""

    def __init__(
        self,
        service: Any,
        control_store: Any,
        contract_root: Path,
        *,
        settings_provider: Callable[[], Mapping[str, Any]] | None = None,
        local_model_registry: Any | None = None,
        state_root: Path | None = None,
        refinement_store: SessionRefinementTaskStore | None = None,
        include_synthetic_projection: bool = False,
        completed_success_retention_seconds: int = 0,
        cancelled_retention_seconds: int = 60,
        history_retention_seconds: int = 30 * 24 * 60 * 60,
        task_lifecycle_root: Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
        core_task_control_root: Path | None = None,
    ):
        self.adapter = _ApplicationServiceAdapter(service)
        self.projector = CurrentTaskProjector(self.adapter)
        self.actions = ActionController(self.adapter, confirmation_seconds=10)
        self.session = CurrentTaskSession(self.projector, control_store, actions=self.actions)
        self.contract_root = Path(contract_root).resolve()
        self._jobs_by_run: dict[str, str] = {}
        self._names_by_run = {run: name for run, name, *_ in VISUAL_FIXTURES}
        self._pending_cancel: dict[str, Any] | None = None
        self._action_receipts = 0
        self._workflow_execution_snapshot: dict[str, Any] | None = None
        self._core_task_controls = (
            TaskControlStore(core_task_control_root)
            if core_task_control_root is not None
            else None
        )
        self._core_run_root = (
            Path(core_task_control_root).resolve().parent / "core_jobs"
            if core_task_control_root is not None
            else None
        )
        self._local_model_registry = local_model_registry
        self._settings_provider = settings_provider
        self._include_synthetic_projection = bool(include_synthetic_projection)
        if (
            isinstance(completed_success_retention_seconds, bool)
            or not isinstance(completed_success_retention_seconds, int)
            or completed_success_retention_seconds < 0
        ):
            raise ValueError("CURRENT_TASK_COMPLETED_RETENTION_INVALID")
        self._completed_success_retention_seconds = completed_success_retention_seconds
        for value, code in (
            (cancelled_retention_seconds, "CURRENT_TASK_CANCELLED_RETENTION_INVALID"),
            (history_retention_seconds, "CURRENT_TASK_HISTORY_RETENTION_INVALID"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(code)
        self._cancelled_retention_seconds = cancelled_retention_seconds
        self._history_retention_seconds = history_retention_seconds
        self._task_lifecycle_path: Path | None = None
        self._dismissed_jobs: dict[str, dict[str, Any]] = {}
        if task_lifecycle_root is not None:
            lifecycle_root = Path(task_lifecycle_root).resolve()
            lifecycle_root.mkdir(parents=True, exist_ok=True)
            self._task_lifecycle_path = lifecycle_root / "dismissed_failed_tasks.json"
            self._dismissed_jobs = self._load_dismissed_jobs()
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        if state_root is not None and refinement_store is not None:
            raise ValueError("CURRENT_TASK_REFINEMENT_STORE_CONFLICT")
        self._refinement_store = refinement_store or (
            SessionRefinementTaskStore(Path(state_root)) if state_root is not None else None
        )
        self._refinement_actions = (
            ActionController(self._refinement_store, confirmation_seconds=10)
            if self._refinement_store is not None
            else None
        )
        if settings_provider is not None:
            self.update_workflow_settings(settings_provider())
        if self._include_synthetic_projection:
            self._seed_synthetic_jobs()

    def update_workflow_settings(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """Refresh the mapping used by future Core job starts.

        Existing jobs retain their immutable snapshots; only subsequent starts
        receive the newly saved Settings revision.
        """

        if not isinstance(state, Mapping) or not isinstance(state.get("settings"), Mapping):
            raise RuntimeError("CURRENT_TASK_SETTINGS_STATE_INVALID")
        local_model_profiles: list[Mapping[str, Any]] = []
        if self._local_model_registry is not None:
            projection = self._local_model_registry.call("local_models.list", {})
            values = projection.get("recognized_models", []) if isinstance(projection, Mapping) else []
            if isinstance(values, list):
                local_model_profiles = [row for row in values if isinstance(row, Mapping)]
        snapshot = build_core_execution_snapshot(
            state["settings"],
            settings_revision=int(state.get("revision", 0)),
            local_model_profiles=local_model_profiles,
        )
        self._workflow_execution_snapshot = snapshot
        return deepcopy(snapshot)

    def bind_core_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Bind the latest saved mapping to a new Core facade request."""

        accepted = deepcopy(dict(request))
        if str(accepted.get("job_type") or "").casefold() not in {
            "core", "core", "memorive-core", "memorive_core"
        }:
            return accepted
        snapshot = self._workflow_execution_snapshot
        if snapshot is None or snapshot["configuration_ready"] is not True:
            raise RuntimeError("Core_WORKFLOW_MAPPING_NOT_READY")
        accepted["workflow_definition"] = deepcopy(snapshot["workflow_definition"])
        accepted["workflow_config"] = deepcopy(snapshot["workflow_config"])
        return accepted

    @property
    def sample_job_ids(self) -> frozenset[str]:
        """Return the opaque job ids owned by the visual sample track.

        The Application Facade deliberately issues normal, content-derived job ids,
        so callers must not infer sample ownership from an id prefix.  Exposing this
        read-only set keeps the sample cards available without allowing them to drive
        real runtime notifications such as the desktop assistant animation.
        """

        return frozenset(self._jobs_by_run.values())

    @staticmethod
    def _assert_public_params(params: Mapping[str, Any]) -> dict[str, Any]:
        accepted = deepcopy(dict(params))
        if any(str(key).startswith("_") for key in accepted):
            raise ValueError("CURRENT_TASK_PRIVATE_PARAM_FORBIDDEN")
        return accepted

    def _seed_synthetic_jobs(self) -> None:
        for run, name, target, phase_code, completed in VISUAL_FIXTURES:
            handle = self.adapter.call(
                "start_job",
                {
                    "request": _request(run, name, self._workflow_execution_snapshot),
                    "idempotency_key": (
                        f"current_task-seed-{run}"
                        if self._workflow_execution_snapshot is None
                        else f"current_task-seed-{run}-{self._workflow_execution_snapshot['settings_sha256'][:16].lower()}"
                    ),
                },
            )
            job_id = str(handle["job_id"])
            self._jobs_by_run[run] = job_id
            job = self.adapter.call("get_job", {"job_id": job_id})
            if job["control_state"] != "QUEUED":
                continue
            status = self.adapter.call(
                "mark_running", {"job_id": job_id, "expected_version": job["version"]}
            )
            status = self.adapter.call(
                "set_safe_checkpoint",
                {"job_id": job_id, "expected_version": status["observed_version"], "safe": True},
            )
            self.adapter.call(
                "record_progress",
                {
                    "job_id": job_id,
                    "completed_units": completed,
                    "total_units": 9,
                    "phase_code": phase_code,
                    "phase_label": name,
                    "last_checkpoint_ref": f"fixture:{run}:checkpoint",
                },
            )
            job = self.adapter.call("get_job", {"job_id": job_id})
            if target == "WAITING_HUMAN":
                self.adapter.call(
                    "record_action_request",
                    {
                        "job_id": job_id,
                        "expected_version": job["version"],
                        "action_request_id": f"action_fixture_{run.replace('-', '_')}",
                        "action": "APPROVE_SYNTHETIC_CHECKPOINT",
                        "payload": {"classification": "SYNTHETIC_PUBLIC_SAFE", "run": run},
                    },
                )
            elif target in {"FAILED", "SUCCEEDED"}:
                self.adapter.call(
                    "current_task.fixture_transition",
                    {
                        "job_id": job_id,
                        "expected_version": job["version"],
                        "target": target,
                        "event_type": f"CURRENT_TASK_SYNTHETIC_{target}",
                    },
                )

    def _job_id(self, params: Mapping[str, Any], *, allow_selected: bool = True) -> str:
        run = params.get("run")
        if isinstance(run, str):
            if run in self._jobs_by_run:
                return self._jobs_by_run[run]
            if self._is_refinement_job(run):
                return run
            if self._is_core_job(run):
                return run
            raise ValueError("CURRENT_TASK_VISUAL_RUN_UNKNOWN")
        job_id = params.get("job_id")
        if isinstance(job_id, str) and (
            job_id in self._jobs_by_run.values()
            or self._is_refinement_job(job_id)
            or self._is_core_job(job_id)
        ):
            return job_id
        if allow_selected:
            selected = self.session.view()["job_id"]
            if isinstance(selected, str) and (
                selected in self._jobs_by_run.values()
                or self._is_refinement_job(selected)
                or self._is_core_job(selected)
            ):
                return selected
        raise ValueError("CURRENT_TASK_JOB_REQUIRED")

    def _is_refinement_job(self, job_id: Any) -> bool:
        return bool(
            isinstance(job_id, str)
            and self._refinement_store is not None
            and self._refinement_store.has(job_id)
        )

    @staticmethod
    def _is_core_job_type(value: Any) -> bool:
        return str(value or "").casefold() in {
            "core",
            "core",
            "memorive-core",
            "memorive_core",
        }

    def _is_core_job(
        self, job_id: Any, list_snapshot: Mapping[str, Any] | None = None
    ) -> bool:
        if not isinstance(job_id, str) or not job_id:
            return False
        if isinstance(list_snapshot, Mapping) and "job_type" in list_snapshot:
            return self._is_core_job_type(list_snapshot.get("job_type"))
        # A transport failure is not evidence that a listed job stopped being a
        # Core task.  Let the complete list refresh fail so the UI keeps its
        # previous atomic projection instead of publishing a partial list.
        try:
            job = self.adapter.call("get_job", {"job_id": job_id})
        except RemoteCallError as error:
            if error.remote_code == "JOB_NOT_FOUND":
                return False
            raise
        request = job.get("request", {}) if isinstance(job, Mapping) else {}
        return self._is_core_job_type(request.get("job_type"))

    @staticmethod
    def _node_id(value: Any) -> str:
        if not isinstance(value, str) or value not in NODE_BY_UI:
            raise ValueError("CURRENT_TASK_UI_NODE_UNKNOWN")
        return NODE_BY_UI[value]

    def _node_id_for_job(self, job_id: str, value: Any) -> str:
        if not self._is_refinement_job(job_id):
            job = self.adapter.call("get_job", {"job_id": job_id})
            definition = job.get("snapshots", {}).get("workflow_definition", {}).get("content", {})
            nodes = definition.get("nodes", []) if isinstance(definition, Mapping) else []
            node_ids = {
                row.get("node_id") for row in nodes if isinstance(row, Mapping)
            }
            mapping = NODE_BY_UI if "02_CHUNK_EMBEDDING" in node_ids else LEGACY_NODE_BY_UI
            if not isinstance(value, str) or value not in mapping:
                raise ValueError("CURRENT_TASK_UI_NODE_UNKNOWN")
            return mapping[value]
        if isinstance(value, str) and value in REFINEMENT_NODE_IDS:
            return value
        if not isinstance(value, str) or value not in REFINEMENT_NODE_BY_UI:
            raise ValueError("CURRENT_TASK_REFINEMENT_NODE_UNKNOWN")
        return REFINEMENT_NODE_BY_UI[value]

    def _validate_core_model_override(
        self, node_id: str, *, profile_ref: Any, model: Any
    ) -> None:
        """Resolve only a currently configured and node-compatible profile."""

        if not isinstance(profile_ref, str) or not profile_ref.strip():
            raise ValueError(
                "CURRENT_TASK_EMBEDDING_PROFILE_REQUIRED"
                if node_id == "02_CHUNK_EMBEDDING"
                else "CURRENT_TASK_MODEL_PROFILE_REQUIRED"
            )
        if not isinstance(model, str) or not model.strip():
            raise ValueError(
                "CURRENT_TASK_EMBEDDING_MODEL_REQUIRED"
                if node_id == "02_CHUNK_EMBEDDING"
                else "CURRENT_TASK_MODEL_REQUIRED"
            )
        snapshot = self._workflow_execution_snapshot
        configured = snapshot.get("profile_catalog", []) if isinstance(snapshot, Mapping) else []
        option = next(
            (
                row
                for row in configured
                if isinstance(row, Mapping)
                and row.get("profile_ref") == profile_ref.strip()
            ),
            None,
        )
        if option is None:
            raise ValueError("CURRENT_TASK_MODEL_PROFILE_UNAVAILABLE")
        # A task override is configuration, not execution.  Keep every
        # credential-backed profile selectable here even when its connection
        # has not yet been verified; the execution adapter remains responsible
        # for failing closed before an actual request is sent.  Requiring
        # AVAILABLE at this layer made the UI advertise a saved model and then
        # reject the user's selection.
        if option.get("credential_reference_stored") is not True:
            raise ValueError("CURRENT_TASK_MODEL_PROFILE_UNAVAILABLE")
        if node_id == "02_CHUNK_EMBEDDING":
            if option.get("embedding_eligible") is not True:
                raise ValueError("CURRENT_TASK_EMBEDDING_PROFILE_INELIGIBLE")
        elif option.get("capability") == "EMBEDDING":
            raise ValueError("CURRENT_TASK_CHAT_PROFILE_REQUIRED")
        if str(option.get("model") or "").strip() != model.strip():
            raise ValueError(
                "CURRENT_TASK_EMBEDDING_MODEL_MISMATCH"
                if node_id == "02_CHUNK_EMBEDDING"
                else "CURRENT_TASK_MODEL_MISMATCH"
            )

    def _core_overrides(self, job_id: str) -> dict[str, dict[str, Any]]:
        if self._core_task_controls is None:
            return self.session.overrides()
        return self._core_task_controls.overrides(job_id)

    def _recovery_status(self, request: Mapping[str, Any]) -> dict[str, Any]:
        result = {k: deepcopy(v) for k,v in request.items() if k != 'workflow_config_json'}
        successor = result.get('successor_job_id')
        if successor:
            job = self.adapter.call('get_job', {'job_id':successor})
            result['successor_state'] = job['control_state']
            result['successor_progress'] = deepcopy(job.get('progress',{}))
        return result

    def _recovery_conflict(self, job_id: str) -> dict[str, Any] | None:
        if self._core_task_controls is None:return None
        job=self.adapter.call('get_job',{'job_id':job_id})
        item=job.get('snapshots',{}).get('resolved_task_input',{}).get('content',{}).get('item_id')
        if not item:return None
        for row in reversed(self._core_task_controls.recovery_requests(item)):
            status=self._recovery_status(row)
            if status.get('successor_job_id')==job_id:
                continue
            if status['status']=='PENDING' or (status.get('successor_job_id') and status.get('successor_state') not in RECOVERY_TERMINAL):
                return status
        return None

    def _project(self, job_id: str, **kwargs: Any) -> dict[str, Any]:
        result=self.projector.project(job_id,**kwargs)
        if self._core_run_root is None:return result
        if not hasattr(self,'_retry_receipt_cache'):self._retry_receipt_cache={}
        counters=retry_counters(self._core_run_root,job_id,self._retry_receipt_cache)
        conflict=self._recovery_conflict(job_id)
        result['recovery_request']=conflict
        job = self.adapter.call('get_job', {'job_id': job_id})
        config = job.get('snapshots', {}).get('workflow_config', {}).get('content', {})
        ingest = deepcopy(config.get('nodes', {}).get('01_DOCUMENT_INGEST', {}))
        ingest.update(self._core_overrides(job_id).get('01_DOCUMENT_INGEST', {}))
        controls = self._core_task_controls
        point = controls.pause_point(job_id) if controls else None
        control_state = job.get('control_state')
        started = controls.started_node_ids(job_id) if controls else ()
        result['pause_node_id'] = point
        for node in result['graph']['workflow_nodes']:
            original_state = node['state']
            scheduled = node['node_id'] == point
            node['pause_scheduled'] = scheduled
            node['pause_eligible'] = bool(controls and not conflict and not point
                and control_state in {'RUNNING','QUEUED'}
                and original_state == 'NOT_STARTED' and node['node_id'] not in started)
            node['resume_eligible'] = bool(controls and not conflict and (
                scheduled or (control_state == 'PAUSED' and original_state == 'PAUSED')))
            if scheduled:
                node['state'] = 'PAUSED'
                node['enable_toggle_eligible'] = False
                node['summary'] = '已设置暂停点；执行到此节点前停止。' if control_state != 'PAUSED' else '任务已暂停，点击继续后从此处接续。'
            if original_state == 'COMPLETED' and control_state in {'RUNNING','PAUSED'}:
                node['rollback_eligible'] = True
            node.update(counters.get(node['node_id'],{}))
            if node['node_id'] == '01_DOCUMENT_INGEST':
                node['fallback_profile_ref'] = ingest.get('fallback_profile_ref')
                node['fallback_model'] = (ingest.get('fallback_profile') or {}).get('model')
                node['retry_scope'] = 'PAGE' 
            node['retry_eligible']=node['state']=='FAILED' and conflict is None
            if conflict:
                node['rollback_eligible']=False
                node['retry_change_eligible']=False
                node['model_change_eligible']=False
                node['enable_toggle_eligible']=False
                node['recovery_request']=deepcopy(conflict)
        return result

    def _apply_node_control(self, job_id: str, action: str) -> None:
        identity = f"node-{action}-{uuid.uuid4().hex}"
        for attempt in range(3):
            job = self.adapter.call("get_job", {"job_id":job_id})
            if action == "resume" and job["control_state"] in {"RUNNING","QUEUED"}:
                return
            if action == "cancel" and job["control_state"] in RECOVERY_TERMINAL:
                return
            try:
                self.adapter.call(action+"_job", {"job_id":job_id,
                    "expected_version":job["version"],"idempotency_key":identity})
                return
            except Exception as error:
                if attempt == 2 or (getattr(error,"code",None) != "EXPECTED_VERSION_CONFLICT"
                                   and str(error).strip() != "EXPECTED_VERSION_CONFLICT"):
                    raise

    def _core_context_pack_summary(self, job_id: str) -> dict[str, Any] | None:
        if self._core_run_root is None or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}", job_id
        ):
            return None
        candidate = (self._core_run_root / job_id / "core_execution_summary.json").resolve(
            strict=False
        )
        expected_parent = (self._core_run_root / job_id).resolve(strict=False)
        if candidate.parent != expected_parent or not candidate.is_file():
            return None
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        summary = value.get("context_pack") if isinstance(value, Mapping) else None
        if not isinstance(summary, Mapping):
            return None
        return {
            "truncated": summary.get("truncated") is True,
            "original_estimated_tokens": int(summary.get("original_estimated_tokens") or 0),
            "retained_estimated_tokens": int(summary.get("retained_estimated_tokens") or 0),
            "token_budget": int(summary.get("token_budget") or 0),
            "retained_block_count": int(summary.get("retained_block_count") or 0),
            "dropped_evidence_block_count": int(
                summary.get("dropped_evidence_block_count") or 0
            ),
            "recovery_action": (
                str(summary.get("recovery_action") or "")[:240] or None
            ),
        }

    def _resolved_profile_override(self, profile_ref: str) -> dict[str, Any]:
        snapshot = self._workflow_execution_snapshot
        catalog = snapshot.get("profile_catalog", []) if isinstance(snapshot, Mapping) else []
        option = next(
            (
                row for row in catalog
                if isinstance(row, Mapping) and row.get("profile_ref") == profile_ref
            ),
            None,
        )
        if option is None:
            raise ValueError("CURRENT_TASK_MODEL_PROFILE_UNAVAILABLE")
        return {
            "model": str(option["model"]),
            "profile_ref": str(option["profile_ref"]),
            "profile_kind": str(option["profile_kind"]),
            "provider": str(option["provider"]),
            "execution_profile": deepcopy(dict(option["execution_profile"])),
        }

    def create_session_refinement_task(
        self,
        *,
        display_name: str,
        local_projection_id: str,
        source_snapshot_sha256: str,
        selected_profile_ref: str,
        message_count: int,
    ) -> dict[str, Any]:
        if self._refinement_store is None:
            raise RuntimeError("SESSION_REFINEMENT_TASK_MANAGER_NOT_CONFIGURED")
        return self._refinement_store.create_task(
            display_name=display_name,
            local_projection_id=local_projection_id,
            source_snapshot_sha256=source_snapshot_sha256,
            selected_profile_ref=selected_profile_ref,
            message_count=message_count,
        )

    def begin_session_refinement_step(self, job_id: str, node_id: str) -> dict[str, Any]:
        if self._refinement_store is None:
            raise RuntimeError("SESSION_REFINEMENT_TASK_MANAGER_NOT_CONFIGURED")
        return self._refinement_store.begin_step(job_id, node_id)

    def get_session_refinement_task(self, job_id: str) -> dict[str, Any]:
        if self._refinement_store is None:
            raise RuntimeError("SESSION_REFINEMENT_TASK_MANAGER_NOT_CONFIGURED")
        return self._refinement_store.get_job(job_id)

    def find_session_refinement_task(
        self, local_projection_id: str
    ) -> dict[str, Any] | None:
        if self._refinement_store is None:
            raise RuntimeError("SESSION_REFINEMENT_TASK_MANAGER_NOT_CONFIGURED")
        return self._refinement_store.find_session_refinement_task(
            local_projection_id
        )

    def set_session_refinement_model(
        self,
        job_id: str,
        *,
        node_id: str,
        profile_ref: str,
        display_name: str,
    ) -> dict[str, Any]:
        if self._refinement_store is None:
            raise RuntimeError("SESSION_REFINEMENT_TASK_MANAGER_NOT_CONFIGURED")
        return self._refinement_store.set_model(
            job_id,
            node_id=node_id,
            profile_ref=profile_ref,
            display_name=display_name,
        )

    def complete_session_refinement_step(self, job_id: str, node_id: str) -> dict[str, Any]:
        if self._refinement_store is None:
            raise RuntimeError("SESSION_REFINEMENT_TASK_MANAGER_NOT_CONFIGURED")
        return self._refinement_store.complete_step(job_id, node_id)

    def succeed_session_refinement_task(
        self,
        job_id: str,
        *,
        draft_id: str,
        model_calls: int,
        external_model_calls: int,
    ) -> dict[str, Any]:
        if self._refinement_store is None:
            raise RuntimeError("SESSION_REFINEMENT_TASK_MANAGER_NOT_CONFIGURED")
        return self._refinement_store.succeed(
            job_id,
            draft_id=draft_id,
            model_calls=model_calls,
            external_model_calls=external_model_calls,
        )

    def fail_session_refinement_task(
        self,
        job_id: str,
        *,
        error_code: str,
        model_calls: int = 0,
    ) -> dict[str, Any]:
        if self._refinement_store is None:
            raise RuntimeError("SESSION_REFINEMENT_TASK_MANAGER_NOT_CONFIGURED")
        return self._refinement_store.fail(
            job_id, error_code=error_code, model_calls=model_calls
        )

    def session_refinement_cancelled(self, job_id: str) -> bool:
        if self._refinement_store is None:
            raise RuntimeError("SESSION_REFINEMENT_TASK_MANAGER_NOT_CONFIGURED")
        return self._refinement_store.is_cancelled(job_id)

    def cancel_session_refinement_task(self, job_id: str) -> dict[str, Any]:
        if self._refinement_store is None:
            raise RuntimeError("SESSION_REFINEMENT_TASK_MANAGER_NOT_CONFIGURED")
        return self._refinement_store.cancel(job_id)

    def _view(self) -> dict[str, Any]:
        session = self.session.view()
        if session["job_id"] is None:
            return {
                "schema_version": "CurrentTaskCurrentTaskProductView-v1",
                "session": session,
                "list": self._list(),
                "projection": None,
                "available_actions": None,
                "status": "READY_NO_SELECTION",
            }
        if self._is_refinement_job(session["job_id"]):
            assert self._refinement_store is not None
            assert self._refinement_actions is not None
            projection = self._refinement_store.project(
                session["job_id"], selected_node_id=session["selected_node_id"]
            )
            job = self._refinement_store.get_job(session["job_id"])
            return {
                "schema_version": "CurrentTaskCurrentTaskProductView-v1",
                "session": session,
                "projection": projection,
                "available_actions": self._refinement_actions.available_actions(job),
                "status": "READY_SESSION_REFINEMENT",
            }
        projection = self._project(
            session["job_id"],
            selected_node_id=session["selected_node_id"],
            overrides=self._core_overrides(session["job_id"]),
        )
        job = self.adapter.call("get_job", {"job_id": session["job_id"]})
        available_actions = self.actions.available_actions(job)
        if projection.get("recovery_request"):
            available_actions["retry"].update(
                enabled=False, disabled_reason="EXISTING_SUCCESSOR"
            )
        return {
            "schema_version": "CurrentTaskCurrentTaskProductView-v1",
            "session": session,
            "projection": projection,
            "available_actions": available_actions,
            "status": "READY",
        }

    def _list(self, *, history_details: bool = True) -> dict[str, Any]:
        projection = self.projector.list_projection()
        if self._dismissed_jobs:
            projection["tasks"] = [
                row for row in projection["tasks"]
                if str(row.get("job_id") or "") not in self._dismissed_jobs
            ]
        console_bindings=[]
        for row in projection['tasks']:
            if row.get('job_type')!='memorive-console-simulation':continue
            self._jobs_by_run[row['job_id']]=row['job_id']
            job=self.adapter.call('get_job',{'job_id':row['job_id']})
            detail=self._project(row['job_id'])
            console_bindings.append({'run':row['job_id'],'job_id':row['job_id'],'display_name':'[控制台模拟] '+str(row.get('name') or row['job_id']),'attempt_id':job['attempt_id'],'control_state':job['control_state'],'workflow_kind':'CONSOLE_SIMULATION','workflow_nodes':detail['graph']['workflow_nodes'],'progress':detail['progress'],'artifact_count':0,'created_at':job.get('created_at'),'updated_at':job.get('updated_at'),'synthetic_only':True,'collection_state':'CURRENT'})
        if not self._include_synthetic_projection:
            projection["tasks"] = [
                row
                for row in projection["tasks"]
                if not self._is_legacy_visual_fixture(row["job_id"], row)
            ]
            projection["view_state"] = "EMPTY" if not projection["tasks"] else "QUEUED"
        by_id = {row["job_id"]: row for row in projection["tasks"]}
        bindings = []
        for run, name, expected_state, _, _ in VISUAL_FIXTURES:
            if run not in self._jobs_by_run:
                continue
            job_id = self._jobs_by_run[run]
            if job_id not in by_id:
                continue
            job = self.adapter.call("get_job", {"job_id": job_id})
            bindings.append(
                {
                    "run": run,
                    "display_name": name,
                    "job_id": job_id,
                    "attempt_id": job["attempt_id"],
                    "control_state": job["control_state"],
                    "expected_visual_fixture_state": expected_state,
                    "locator": by_id[job_id]["locator"],
                    "synthetic_only": True,
                }
            )
        refinement_bindings, refinement_history = self._partition_refinement_bindings()
        production_bindings = []
        production_history = []
        for row in projection["tasks"]:
            job_id = str(row["job_id"])
            if not self._is_core_job(job_id, row):
                continue
            job = self.adapter.call("get_job", {"job_id": job_id})
            resolved = job.get("snapshots", {}).get("resolved_task_input", {}).get("content", {})
            # Metadata-only consumers of current jobs do not need graphs,
            # artifacts or retry receipts belonging to expired history. Keep
            # the lineage identifiers: a historical successor still supersedes
            # its predecessor even when its detail is not requested.
            summary = {
                "job_id": job_id,
                "attempt_id": job["attempt_id"],
                "control_state": job["control_state"],
                "predecessor_job_id": (job.get("request", {}).get("core_retry") or {}).get("predecessor_job_id"),
                "created_at": job.get("created_at"),
                "updated_at": job.get("updated_at"),
                "workflow_kind": "CORE_DOCUMENT",
            }
            collection = self._binding_collection(
                summary, preserve_terminal_when_retention_disabled=True
            )
            if not history_details and collection and collection["collection_state"] == "HISTORY":
                production_history.append({**summary, **collection})
                continue
            task_projection = self._project(
                job_id,
                overrides=self._core_overrides(job_id),
            )
            workflow_nodes = deepcopy(task_projection["graph"]["workflow_nodes"])
            context_pack_summary = self._core_context_pack_summary(job_id)
            if context_pack_summary is not None:
                for node in workflow_nodes:
                    if node.get("node_id") == "06_CONTEXT_PACK":
                        node["context_pack_runtime"] = deepcopy(context_pack_summary)
                        break
            binding = {
                    "run": job_id,
                    "display_name": str(
                        resolved.get("display_name")
                        if isinstance(resolved, Mapping)
                        else row.get("name") or job_id
                    ),
                    "job_id": job_id,
                    "attempt_id": job["attempt_id"],
                    "control_state": job["control_state"],
                    "locator": row["locator"],
                    "workflow_kind": "CORE_DOCUMENT",
                    "predecessor_job_id": (job.get("request",{}).get("core_retry") or {}).get("predecessor_job_id"),
                    "recovery_request": task_projection.get("recovery_request"),
                    "workflow_nodes": workflow_nodes,
                    "context_pack": deepcopy(context_pack_summary),
                    "progress": deepcopy(task_projection["progress"]),
                    "artifact_count": len(task_projection["artifacts"]["rows"]),
                    "created_at": job.get("created_at"),
                    "updated_at": job.get("updated_at"),
                    "synthetic_only": False,
                }
            collection = self._binding_collection(
                binding, preserve_terminal_when_retention_disabled=True
            )
            if collection is None:
                continue
            binding.update(collection)
            if binding["collection_state"] == "HISTORY":
                production_history.append(binding)
            else:
                production_bindings.append(binding)
        production_bindings, production_history = lineage_partition(production_bindings, production_history)
        history_bindings = [
            row for row in [*refinement_history, *production_history]
            if self._history_row_within_retention(row)
        ]
        return {
            **projection,
            "tasks": list(projection["tasks"]),
            "task_count": len(projection["tasks"]),
            "visual_bindings": bindings,
            "visual_binding_count": len(bindings),
            "refinement_bindings": refinement_bindings,
            "refinement_binding_count": len(refinement_bindings),
            "production_bindings": production_bindings,
            "production_binding_count": len(production_bindings),
            "history_bindings": history_bindings,
            "history_binding_count": len(history_bindings),
            "production_job_count": (
                len(refinement_bindings) + len(production_bindings) + len(history_bindings)
            ),
            "console_bindings": console_bindings,
            "workflow_execution_snapshot": (
                {
                    key: deepcopy(value)
                    for key, value in self._workflow_execution_snapshot.items()
                    if key != "profile_catalog"
                }
                if isinstance(self._workflow_execution_snapshot, Mapping)
                else None
            ),
            "status": (
                "PASS_SYNTHETIC_APPLICATION_SERVICE_PROJECTION"
                if self._include_synthetic_projection
                else "PASS_PRODUCTION_APPLICATION_SERVICE_PROJECTION"
            ),
        }

    @staticmethod
    def _parse_utc_timestamp(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)

    def _load_dismissed_jobs(self) -> dict[str, dict[str, Any]]:
        path = self._task_lifecycle_path
        if path is None or not path.is_file():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("CURRENT_TASK_DISMISSED_STATE_INVALID")
        rows = value.get("jobs")
        if value.get("schema_version") != "CurrentTaskDismissedFailedJobs-v1" or not isinstance(rows, Mapping):
            raise ValueError("CURRENT_TASK_DISMISSED_STATE_INVALID")
        accepted: dict[str, dict[str, Any]] = {}
        for job_id, detail in rows.items():
            if (
                not isinstance(job_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}", job_id)
                or not isinstance(detail, Mapping)
                or detail.get("control_state") != "FAILED"
                or self._parse_utc_timestamp(detail.get("dismissed_at")) is None
            ):
                raise ValueError("CURRENT_TASK_DISMISSED_STATE_INVALID")
            accepted[job_id] = dict(detail)
        return accepted

    def _persist_dismissed_jobs(self) -> None:
        path = self._task_lifecycle_path
        if path is None:
            raise ValueError("CURRENT_TASK_DISMISSED_STATE_UNAVAILABLE")
        payload = {
            "schema_version": "CurrentTaskDismissedFailedJobs-v1",
            "jobs": self._dismissed_jobs,
        }
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _dismiss_failed_job(self, job_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}", job_id):
            raise ValueError("CURRENT_TASK_FAILED_DELETE_ID_INVALID")
        dismissed_at = self._now_provider()
        if not isinstance(dismissed_at, datetime) or dismissed_at.tzinfo is None:
            raise ValueError("CURRENT_TASK_NOW_PROVIDER_INVALID")
        dismissed_at = dismissed_at.astimezone(timezone.utc)
        self._dismissed_jobs[job_id] = {
            "control_state": "FAILED",
            "dismissed_at": dismissed_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "audit_record_preserved": True,
        }
        self._persist_dismissed_jobs()
        if self.session.view().get("job_id") == job_id:
            self.session.close_all()
        return {
            "schema_version": "CurrentTaskFailedDeleteReceipt-v1",
            "job_id": job_id,
            "removed_from_task_projection": True,
            "audit_record_preserved": True,
            "status": "PASS",
        }

    def _history_row_within_retention(self, row: Mapping[str, Any]) -> bool:
        timestamp = self._parse_utc_timestamp(
            row.get("history_at") or row.get("completed_at") or row.get("updated_at")
        )
        if timestamp is None:
            return True
        now = self._now_provider()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("CURRENT_TASK_NOW_PROVIDER_INVALID")
        return (now.astimezone(timezone.utc) - timestamp).total_seconds() < self._history_retention_seconds

    def _binding_collection(
        self,
        source: Mapping[str, Any],
        *,
        preserve_terminal_when_retention_disabled: bool = False,
    ) -> dict[str, Any] | None:
        """Classify one task without deleting its completed audit projection."""

        row = dict(source)
        state = str(row.get("control_state") or "").upper()
        if state not in {
            "SUCCEEDED", "FAILED", "CANCELLED"
        }:
            return {"collection_state": "CURRENT"}
        if state == "FAILED":
            return {"collection_state": "CURRENT"}
        retention_seconds = (
            self._cancelled_retention_seconds
            if state == "CANCELLED"
            else self._completed_success_retention_seconds
        )
        if retention_seconds <= 0:
            return (
                {"collection_state": "CURRENT"}
                if preserve_terminal_when_retention_disabled
                else None
            )
        completed_at = self._parse_utc_timestamp(
            row.get("completed_at") or row.get("updated_at")
        )
        if completed_at is None:
            return {"collection_state": "CURRENT"}
        now = self._now_provider()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("CURRENT_TASK_NOW_PROVIDER_INVALID")
        elapsed = max(0.0, (now.astimezone(timezone.utc) - completed_at).total_seconds())
        if elapsed >= retention_seconds + self._history_retention_seconds:
            return None
        history_at = completed_at + timedelta(
            seconds=retention_seconds
        )
        common = {
            "completion_retention_seconds": retention_seconds,
            "history_at": history_at.isoformat(timespec="microseconds").replace(
                "+00:00", "Z"
            ),
            # Compatibility alias for the task-view UI while the product now
            # names the destination explicitly as History Tasks.
            "removal_at": history_at.isoformat(timespec="microseconds").replace(
                "+00:00", "Z"
            ),
        }
        if elapsed >= retention_seconds:
            return {
                **common,
                "collection_state": "HISTORY",
                "retention_seconds_remaining": 0,
            }
        return {
            **common,
            "collection_state": "CURRENT",
            "retention_seconds_remaining": max(
                1,
                math.ceil(retention_seconds - elapsed),
            ),
        }

    def _partition_refinement_bindings(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if self._refinement_store is None:
            return [], []
        current: list[dict[str, Any]] = []
        history: list[dict[str, Any]] = []
        for source in self._refinement_store.bindings():
            row = deepcopy(source)
            if str(row.get("job_id") or "") in self._dismissed_jobs:
                continue
            state = str(row.get("control_state") or "").upper()
            if state not in {"RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"}:
                continue
            collection = self._binding_collection(row)
            if collection is None:
                continue
            row.update(collection)
            (history if row["collection_state"] == "HISTORY" else current).append(row)
        return current, history

    def _visible_refinement_bindings(self) -> list[dict[str, Any]]:
        """Compatibility helper retained for callers that only need Current."""

        current, _history = self._partition_refinement_bindings()
        return current

    def _is_legacy_visual_fixture(
        self, job_id: str, list_snapshot: Mapping[str, Any] | None = None
    ) -> bool:
        if isinstance(list_snapshot, Mapping) and "job_type" in list_snapshot:
            return str(list_snapshot.get("job_type") or "").casefold() == (
                "synthetic-current_task-current-task"
            )
        job = self.adapter.call("get_job", {"job_id": job_id})
        request = job.get("request", {})
        return bool(
            isinstance(request, Mapping)
            and request.get("job_type") == "synthetic-current_task-current-task"
            and str(request.get("payload_ref") or "").startswith("fixture:desktop:current-task:")
        )

    def _record(self, receipt: Any) -> Any:
        self._action_receipts += 1
        return receipt

    def call(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        if method not in CURRENT_TASK_METHODS:
            raise ValueError("CURRENT_TASK_PRODUCT_METHOD_NOT_ALLOWLISTED")
        accepted = self._assert_public_params(params or {})
        if method == "current_task.get_contract":
            state = json.loads((self.contract_root / "CurrentTaskStateContract.json").read_text(encoding="utf-8"))
            route = json.loads((self.contract_root / "RouteContract.json").read_text(encoding="utf-8"))
            return {
                "schema_version": "CurrentTaskCurrentTaskProductContract-v1",
                "state_contract": state,
                "route_contract": route,
                "method_count": len(CURRENT_TASK_METHODS),
                "all_business_calls_via_application_service": True,
                "session_refinement_task_store": self._refinement_store is not None,
                "synthetic_only": self._refinement_store is None,
                "status": "PASS",
            }
        if method == "current_task.restart_status":
            if set(accepted) != {"request_id"} or self._core_task_controls is None:
                raise ValueError("CURRENT_TASK_RESTART_STATUS_PARAMS_INVALID")
            return self._recovery_status(self._core_task_controls.recovery_request(accepted["request_id"]))
        if method == "current_task.list":
            return self._list()
        if method == "current_task.select_task":
            self.session.select_task(self._job_id(accepted, allow_selected=False))
            return self._view()
        if method == "current_task.select_node":
            job_id = self._job_id({}, allow_selected=True)
            self.session.select_node(
                self._node_id_for_job(job_id, accepted.get("node_id"))
            )
            return self._view()
        if method == "current_task.view":
            return self._view()
        if method == "current_task.start_core":
            if set(accepted) != {"request", "idempotency_key"}:
                raise ValueError("CURRENT_TASK_Core_START_PARAMS_INVALID")
            request = accepted["request"]
            idempotency_key = accepted["idempotency_key"]
            if not isinstance(request, Mapping) or not isinstance(idempotency_key, str) or not idempotency_key:
                raise ValueError("CURRENT_TASK_Core_START_REQUEST_INVALID")
            bound_request = self.bind_core_request(request)
            if bound_request.get("workflow_definition") is None:
                raise ValueError("CURRENT_TASK_Core_JOB_TYPE_REQUIRED")
            handle = self.adapter.call(
                "start_job",
                {"request": bound_request, "idempotency_key": idempotency_key},
            )
            job = self.adapter.call("get_job", {"job_id": handle["job_id"]})
            return {
                "schema_version": "MemoriveCoreStartReceipt-v1",
                "handle": handle,
                "workflow_definition_sha256": job["snapshots"]["workflow_definition"]["sha256"],
                "workflow_config_sha256": job["snapshots"]["workflow_config"]["sha256"],
                "settings_revision": self._workflow_execution_snapshot["settings_revision"],
                "settings_sha256": self._workflow_execution_snapshot["settings_sha256"],
                "external_route_activated": False,
                "status": "STARTED_WITH_SAVED_Core_MAPPING",
            }
        if method == "current_task.refresh":
            selected_job_id = self.session.view()["job_id"]
            if self._is_refinement_job(selected_job_id):
                return {"session": self.session.view(), "projection": self._view()["projection"]}
            if self._is_core_job(selected_job_id):
                return {"session": self.session.view(), "projection": self._view()["projection"]}
            return self.session.refresh()
        if method in {"current_task.pause_node", "current_task.resume_node"}:
            job_id = self._job_id(accepted)
            if set(accepted) - {"job_id","run","node_id","idempotency_key"}:
                raise ValueError("CURRENT_TASK_NODE_CONTROL_PARAMS_INVALID")
            if not self._is_core_job(job_id) or self._core_task_controls is None:
                raise ValueError("CURRENT_TASK_NODE_CONTROL_UNAVAILABLE")
            node_id = self._node_id_for_job(job_id, accepted.get("node_id"))
            projection = self._project(job_id, overrides=self._core_overrides(job_id))
            node = next(n for n in projection["graph"]["workflow_nodes"] if n["node_id"] == node_id)
            if method.endswith("pause_node"):
                if not node.get("pause_eligible"):
                    raise ValueError("CURRENT_TASK_NODE_PAUSE_DISABLED")
                receipt = self._core_task_controls.set_pause_point(job_id,node_id)
            else:
                if not node.get("resume_eligible"):
                    raise ValueError("CURRENT_TASK_NODE_RESUME_DISABLED")
                def resume():
                    self._apply_node_control(job_id,"resume")
                receipt = self._core_task_controls.clear_pause_point(job_id,node_id,resume=resume)
            return self._record({"node_control_receipt":receipt,"view":self._view()})
        if method in {"current_task.pause", "current_task.resume"}:
            job_id = self._job_id(accepted)
            if self._is_refinement_job(job_id):
                raise ValueError("CURRENT_TASK_REFINEMENT_ACTION_NOT_SUPPORTED")
            job = self.adapter.call("get_job", {"job_id": job_id})
            action = method.rsplit(".", 1)[-1]
            # Progress updates may advance the version between this read and
            # the control command. Retry only that typed optimistic conflict,
            # retaining the same idempotency key and reading a fresh version.
            identity = str(accepted.get("idempotency_key") or f"current_task-{action}-{self._action_receipts + 1}")
            for control_attempt in range(3):
                try:
                    receipt = self.actions.execute(action, job_id,
                        expected_version=job["version"], idempotency_key=identity)
                    break
                except Exception as error:
                    if control_attempt == 2 or (
                        getattr(error, 'code', None) != 'EXPECTED_VERSION_CONFLICT'
                        and str(error).strip() != 'EXPECTED_VERSION_CONFLICT'
                    ):
                        raise
                    job = self.adapter.call("get_job", {"job_id": job_id})
            return self._record({"action_receipt": receipt, "view": self._view()})
        if method == "current_task.begin_cancel":
            job_id = self._job_id(accepted)
            if self._is_refinement_job(job_id):
                assert self._refinement_store is not None
                assert self._refinement_actions is not None
                job = self._refinement_store.get_job(job_id)
                receipt = self._refinement_actions.begin_cancel(
                    job_id, expected_version=job["version"]
                )
                self._pending_cancel = {**dict(receipt), "workflow_kind": "SESSION_REFINEMENT"}
            else:
                job = self.adapter.call("get_job", {"job_id": job_id})
                receipt = self.actions.begin_cancel(job_id, expected_version=job["version"])
                self._pending_cancel = {**dict(receipt), "workflow_kind": "Core_SYNTHETIC"}
            return receipt
        if method == "current_task.confirm_cancel":
            if self._pending_cancel is None:
                raise ValueError("CURRENT_TASK_CANCEL_CONFIRMATION_MISSING")
            pending = dict(self._pending_cancel)
            token = str(accepted.get("token") or pending["token"])
            action_controller = (
                self._refinement_actions
                if pending.get("workflow_kind") == "SESSION_REFINEMENT"
                else self.actions
            )
            assert action_controller is not None
            receipt = action_controller.confirm_cancel(
                token,
                pending["job_id"],
                expected_version=int(pending["expected_version"]),
                idempotency_key=str(accepted.get("idempotency_key") or f"current_task-cancel-{self._action_receipts + 1}"),
            )
            self._pending_cancel = None
            return self._record({"action_receipt": receipt, "view": self._view()})
        if method == "current_task.delete_failed":
            job_id = self._job_id(accepted)
            if self._is_refinement_job(job_id):
                assert self._refinement_store is not None
                job = self._refinement_store.get_job(job_id)
            else:
                job = self.adapter.call("get_job", {"job_id": job_id})
            if str(job.get("control_state") or "").upper() != "FAILED":
                raise ValueError("CURRENT_TASK_DELETE_REQUIRES_FAILED_JOB")
            return self._record(self._dismiss_failed_job(job_id))
        if method == "current_task.retry":
            job_id = self._job_id(accepted)
            conflict=self._recovery_conflict(job_id)
            if conflict:
                return self._record({"status":"EXISTING_SUCCESSOR","retry_receipt":conflict})
            if self._is_refinement_job(job_id):
                raise ValueError("CURRENT_TASK_REFINEMENT_RETRY_REQUIRES_NEW_USER_ACTION")
            job = self.adapter.call("get_job", {"job_id": job_id})
            if job.get('request',{}).get('job_type')=='memorive-console-simulation':
                receipt=self.adapter.call('console.control_task',{'job_id':job_id,'action':'retry','request_id':str(accepted.get('request_id') or uuid.uuid4().hex),'allow_model_calls':True})
                return self._record({'action_receipt':receipt,'status':'SIMULATION_REPLAY_COMPLETED'})
            if self._is_core_job(job_id) and self._core_task_controls is not None:
                if job.get("control_state") != "FAILED":
                    raise ValueError("CURRENT_TASK_Core_RETRY_REQUIRES_FAILED_JOB")
                projection = self._project(
                    job_id,
                    overrides=self._core_overrides(job_id),
                )
                failed_node = next(
                    (
                        row
                        for row in projection["graph"]["workflow_nodes"]
                        if row.get("state") == "FAILED"
                    ),
                    None,
                )
                if not isinstance(failed_node, Mapping):
                    raise ValueError("CURRENT_TASK_Core_FAILED_NODE_UNRESOLVED")
                resolved = job.get("snapshots", {}).get(
                    "resolved_task_input", {}
                ).get("content", {})
                item_id = resolved.get("item_id") if isinstance(resolved, Mapping) else None
                if not isinstance(item_id, str) or not item_id:
                    raise ValueError("CURRENT_TASK_Core_RETRY_ITEM_MISSING")
                frozen = job.get("snapshots", {}).get("workflow_config", {}).get(
                    "content", {}
                )
                effective = self._core_task_controls.effective_workflow_config(
                    job_id, frozen
                )
                queued = self._core_task_controls.request_rollback(
                    predecessor_job_id=job_id,
                    item_id=item_id,
                    resume_from_node_id=str(failed_node["node_id"]),
                    workflow_config=effective,
                )
                return self._record(
                    {
                        "schema_version": "DesktopCoreFailedNodeRetryReceipt-v1",
                        "retry_receipt": {
                            key: deepcopy(value)
                            for key, value in queued.items()
                            if key != "workflow_config_json"
                        },
                        "resume_from_node_id": failed_node["node_id"],
                        "task_overrides_applied": True,
                        "status": "PENDING",
                    }
                )
            run = next(key for key, value in self._jobs_by_run.items() if value == job_id)
            policy = {
                "request_id": str(accepted.get("request_id") or f"syn-retry-request-{run}"),
                "correlation_id": str(accepted.get("correlation_id") or f"syn-retry-correlation-{run}"),
            }
            receipt = self.actions.execute(
                "retry",
                job_id,
                expected_version=job["version"],
                idempotency_key=str(accepted.get("idempotency_key") or f"current_task-retry-{run}"),
                payload={"retry_policy": policy},
            )
            successor = receipt["facade_receipt"]
            self._jobs_by_run[run] = successor["job_id"]
            self.session.select_task(successor["job_id"])
            return self._record({"action_receipt": receipt, "view": self._view()})
        if method == "current_task.rollback_node":
            job_id = self._job_id(accepted)
            conflict=self._recovery_conflict(job_id)
            if conflict:
                return self._record({"status":"EXISTING_SUCCESSOR","rollback_receipt":conflict})
            if not self._is_core_job(job_id) or self._core_task_controls is None:
                raise ValueError("CURRENT_TASK_Core_ROLLBACK_UNAVAILABLE")
            if set(accepted) - {"node_id", "job_id", "run"}:
                raise ValueError("CURRENT_TASK_Core_ROLLBACK_PARAMS_INVALID")
            node_id = self._node_id_for_job(job_id, accepted.get("node_id"))
            job = self.adapter.call("get_job", {"job_id": job_id})
            if job.get("control_state") not in {"FAILED", "SUCCEEDED", "CANCELLED", "PAUSED", "RUNNING"}:
                raise ValueError("CURRENT_TASK_Core_ROLLBACK_REQUIRES_STABLE_TASK")
            projection = self._project(
                job_id,
                selected_node_id=node_id,
                overrides=self._core_overrides(job_id),
            )
            node = next(
                row for row in projection["graph"]["workflow_nodes"]
                if row["node_id"] == node_id
            )
            if node.get("rollback_eligible") is not True:
                raise ValueError("CURRENT_TASK_Core_ROLLBACK_NODE_NOT_COMPLETED")
            resolved = job.get("snapshots", {}).get("resolved_task_input", {}).get("content", {})
            item_id = resolved.get("item_id") if isinstance(resolved, Mapping) else None
            if not isinstance(item_id, str) or not item_id:
                raise ValueError("CURRENT_TASK_Core_ROLLBACK_ITEM_MISSING")
            frozen_config = job.get("snapshots", {}).get("workflow_config", {}).get("content", {})
            effective_config = self._core_task_controls.effective_workflow_config(
                job_id, frozen_config
            )
            # Confirmed rewind retires the old execution before dispatch.
            # Source-specific pipeline locking prevents overlapping successors.
            if job.get("control_state") in {"PAUSED","RUNNING"}:
                self._apply_node_control(job_id,"cancel")
                self._core_task_controls.clear_pause_point(job_id)
            queued = self._core_task_controls.request_rollback(
                predecessor_job_id=job_id,
                item_id=item_id,
                resume_from_node_id=node_id,
                workflow_config=effective_config,
            )
            return self._record(
                {
                    "schema_version": "DesktopCoreRollbackRequestReceipt-v1",
                    "rollback_receipt": {
                        key: deepcopy(value)
                        for key, value in queued.items()
                        if key != "workflow_config_json"
                    },
                    "predecessor_immutable": True,
                    "new_successor_required": True,
                    "status": "PENDING",
                }
            )
        if method == "current_task.respond":
            job_id = self._job_id(accepted)
            if self._is_refinement_job(job_id):
                raise ValueError("CURRENT_TASK_REFINEMENT_ACTION_NOT_SUPPORTED")
            job = self.adapter.call("get_job", {"job_id": job_id})
            pending = [row for row in job.get("action_requests", []) if row.get("state") == "PENDING"]
            if not pending:
                raise ValueError("CURRENT_TASK_PENDING_ACTION_REQUIRED")
            response = accepted.get("response") or {"decision": "APPROVE_SYNTHETIC_CHECKPOINT"}
            receipt = self.actions.execute(
                "respond_to_action",
                job_id,
                expected_version=job["version"],
                idempotency_key=str(accepted.get("idempotency_key") or f"current_task-respond-{self._action_receipts + 1}"),
                payload={"action_request_id": pending[0]["action_request_id"], "response": response},
            )
            return self._record({"action_receipt": receipt, "view": self._view()})
        if method == "current_task.set_override":
            selected_job_id = self._job_id(accepted, allow_selected=True)
            if self._is_refinement_job(selected_job_id):
                if set(accepted) - {"job_id", "run", "node_id", "model", "profile_ref"}:
                    raise ValueError("CURRENT_TASK_REFINEMENT_MODEL_OVERRIDE_INVALID")
                node_id = self._node_id_for_job(selected_job_id, accepted.get("node_id"))
                profile_ref = str(accepted.get("profile_ref") or "").strip()
                from memorive_settings.refinement_models import configured_refinement_models
                if not profile_ref:
                    raise ValueError("CURRENT_TASK_REFINEMENT_MODEL_PROFILE_INVALID")
                state = self._settings_provider() if self._settings_provider is not None else {}
                options = configured_refinement_models(state, self._local_model_registry)
                option = next((row for row in options if row['profile_ref'] == profile_ref
                               and row['execution_eligible'] is True), None)
                if option is None:
                    raise ValueError("CURRENT_TASK_REFINEMENT_MODEL_PROFILE_UNAVAILABLE")
                display_name = option['display_name']
                receipt = self.set_session_refinement_model(
                    selected_job_id,
                    node_id=node_id,
                    profile_ref=profile_ref,
                    display_name=display_name,
                )
                return self._record(
                    {
                        "override_receipt": {
                            "schema_version": "DesktopSessionRefinementModelOverrideReceipt-v1",
                            "job_id": selected_job_id,
                            "node_id": node_id,
                            "profile_ref": receipt["selected_profile_ref"],
                            "model": display_name,
                            "scope": "TASK_ONLY",
                            "status": "PASS",
                        },
                        "view": self._view(),
                    }
                )
            node_id = self._node_id_for_job(selected_job_id, accepted.get("node_id"))
            if self._is_core_job(selected_job_id) and self._core_task_controls is not None:
                if set(accepted) - {"job_id", "run", "node_id", "enabled", "model", "profile_ref", "retry_count", "fallback_profile_ref"}:
                    raise ValueError("CURRENT_TASK_Core_OVERRIDE_PARAMS_INVALID")
                projection = self._project(
                    selected_job_id,
                    selected_node_id=node_id,
                    overrides=self._core_overrides(selected_job_id),
                )
                node = next(
                    row for row in projection["graph"]["workflow_nodes"]
                    if row["node_id"] == node_id
                )
                override: dict[str, Any] = {}
                if "enabled" in accepted:
                    if not isinstance(accepted["enabled"], bool) or node.get("enable_toggle_eligible") is not True:
                        raise ValueError("CURRENT_TASK_NODE_ENABLE_LOCKED")
                    override["enabled"] = accepted["enabled"]
                if "retry_count" in accepted:
                    retry_count = accepted["retry_count"]
                    if (
                        node.get("retry_change_eligible") is not True
                        or isinstance(retry_count, bool)
                        or not isinstance(retry_count, int)
                        or not 0 <= retry_count <= 10
                    ):
                        raise ValueError("CURRENT_TASK_NODE_RETRY_LOCKED")
                    override["retry_count"] = retry_count
                if "model" in accepted or "profile_ref" in accepted:
                    if node.get("model_change_eligible") is not True:
                        raise ValueError("CURRENT_TASK_NODE_MODEL_LOCKED")
                    self._validate_core_model_override(
                        node_id,
                        profile_ref=accepted.get("profile_ref"),
                        model=accepted.get("model"),
                    )
                    override.update(
                        self._resolved_profile_override(str(accepted["profile_ref"]).strip())
                    )
                    producer_id = {
                        "04_CARD_CROSS_CHECK": "03_CARD_DISTILL",
                        "08_JUDGMENT_CROSS_CHECK": "07_ANALYSIS",
                    }.get(node_id)
                    if producer_id is not None:
                        effective = self._core_task_controls.effective_workflow_config(
                            selected_job_id,
                            self.adapter.call("get_job", {"job_id": selected_job_id})
                            ["snapshots"]["workflow_config"]["content"],
                        )
                        if effective["nodes"][producer_id].get("profile_ref") == override["profile_ref"]:
                            raise ValueError("CURRENT_TASK_HETEROGENEOUS_PROFILE_CONFLICT")
                if node_id == '01_DOCUMENT_INGEST':
                    effective = self._core_task_controls.effective_workflow_config(selected_job_id,
                        self.adapter.call('get_job', {'job_id': selected_job_id})['snapshots']['workflow_config']['content'])
                    primary_ref = override.get('profile_ref', effective['nodes'][node_id].get('profile_ref'))
                    if 'fallback_profile_ref' in accepted:
                        if node.get('model_change_eligible') is not True:
                            raise ValueError('CURRENT_TASK_NODE_MODEL_LOCKED')
                        ref = accepted['fallback_profile_ref']
                        if ref is not None and (not isinstance(ref, str) or not ref.strip() or ref == primary_ref):
                            raise ValueError('CURRENT_TASK_FALLBACK_INVALID')
                        fallback = None
                        if ref is not None:
                            fallback = self._resolved_profile_override(ref)
                            self._validate_core_model_override(node_id, profile_ref=ref, model=fallback['model'])
                        override.update(fallback_profile_ref=ref, fallback_profile=fallback)
                    elif primary_ref == effective['nodes'][node_id].get('fallback_profile_ref'):
                        override.update(fallback_profile_ref=None, fallback_profile=None)
                elif 'fallback_profile_ref' in accepted:
                    raise ValueError('CURRENT_TASK_FALLBACK_NODE_INVALID')
                if not override:
                    raise ValueError("CURRENT_TASK_OVERRIDE_EMPTY")
                receipt = self._core_task_controls.set_override(
                    selected_job_id,
                    node_id,
                    override,
                    allow_started_failed_node=(node.get("state") == "FAILED"),
                )
            else:
                self._validate_core_model_override(
                    node_id,
                    profile_ref=accepted.get("profile_ref"),
                    model=accepted.get("model"),
                )
                receipt = self.session.set_override(
                    node_id,
                    enabled=accepted.get("enabled"),
                    model=accepted.get("model"),
                    profile_ref=accepted.get("profile_ref"),
                )
            return self._record({"override_receipt": receipt, "view": self._view()})
        if method == "current_task.restore_snapshot":
            selected_job_id = self._job_id(accepted, allow_selected=True)
            if set(accepted) - {"job_id", "run", "node_id"}:
                raise ValueError("CURRENT_TASK_RESTORE_PARAMS_INVALID")
            if self._is_refinement_job(selected_job_id):
                raise ValueError("CURRENT_TASK_REFINEMENT_SNAPSHOT_LOCKED")
            node_id = self._node_id_for_job(selected_job_id, accepted.get("node_id"))
            receipt = (
                self._core_task_controls.clear_override(selected_job_id, node_id)
                if self._is_core_job(selected_job_id)
                and self._core_task_controls is not None
                else self.session.restore_creation_snapshot(node_id)
            )
            return self._record({"override_receipt": receipt, "view": self._view()})
        if method == "current_task.close_all":
            return self.session.close_all()
        if method == "current_task.effect_metrics":
            refinement_jobs = (
                self._refinement_store.list_jobs()
                if self._refinement_store is not None
                else []
            )
            return {
                "schema_version": "CurrentTaskCurrentTaskEffectMetrics-v1",
                "synthetic_jobs_started": len(self._jobs_by_run),
                "synthetic_action_receipts": self._action_receipts,
                "production_jobs_started": len(refinement_jobs),
                "session_refinement_jobs": len(refinement_jobs),
                "session_refinement_model_calls": sum(row["model_calls"] for row in refinement_jobs),
                "production_job_access": len(refinement_jobs),
                "production_writes": len(refinement_jobs),
                "external_network_calls": sum(row["external_model_calls"] for row in refinement_jobs),
                "external_model_calls": sum(row["external_model_calls"] for row in refinement_jobs),
                "provider_calls": sum(row["external_model_calls"] for row in refinement_jobs),
                "credential_value_reads": 0,
                "real_user_files_imported": 0,
                "permanent_deletes": 0,
                "cost_cny": 0,
                "cost_usd": 0,
                "status": "PASS",
            }
        raise AssertionError(method)


__all__ = ["CURRENT_TASK_METHODS", "CurrentTaskProductController"]
