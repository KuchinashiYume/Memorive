from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import threading
from typing import Any, Callable, Mapping
import uuid

from pr_os_desktop_service.control_store import ControlStore
from pr_os_desktop_service.locator import StableLocator

from .contracts import MessageRecord, message_dedupe_key, stable_message_sort
from .controller import MessageActionError, MessageController
from .navigation import MessageNavigator
from .notifications import NotificationPlanner, SyntheticWindowsBridge
from .projection import MessageProjectionEngine
from .store import MessageStore, utc_now


MESSAGES_METHODS = frozenset(
    {
        "messages.get_contract",
        "messages.bootstrap",
        "messages.list",
        "messages.switch_mode",
        "messages.open_detail",
        "messages.close_detail",
        "messages.toggle_star",
        "messages.confirm",
        "messages.archive_confirmed",
        "messages.mark_all_read",
        "messages.bulk_apply",
        "messages.refresh_archive",
        "messages.navigate",
        "messages.notification_deliver",
        "messages.notification_activate",
        "messages.effect_metrics",
    }
)

_STATUS_AXES = {
    "lifecycle_status": "in_progress",
    "verification_result": "PASS",
    "acceptance_verdict": "NOT_ASSESSED",
    "capability_status": "AVAILABLE",
}

_SEED_ROWS = (
    ("运行证据写入失败", "T08 写入回执失败，任务已安全停止", "RED", "JOB_FAILED", "SYNTHETIC_EVIDENCE_WRITE", "pr-os://job/syn-job-001", True, True, "CURRENT"),
    ("独立审查等待人工批准", "run-042 已到达人工作业门", "YELLOW", "ACTION_REQUIRED", "SYNTHETIC_HUMAN_GATE", "pr-os://action/syn-job-001/syn-action-001", True, False, "CURRENT"),
    ("资料摄取流程已完成", "3 份产物已通过结构检查", "GREEN", "JOB_COMPLETED", "SYNTHETIC_INGEST_COMPLETE", None, False, False, "CURRENT"),
    ("应用更新说明已登记", "当前版本保持不变，仅记录可用更新", "BLUE", "USER_INFORMATION", "SYNTHETIC_UPDATE_NOTE", None, False, False, "CURRENT"),
    ("本周工作报告已生成", "报告消息需要逐条查看，不能一键已读", "GREEN", "REPORT_READY", "SYNTHETIC_WEEKLY_REPORT", None, True, False, "CURRENT"),
    ("旧运行节点已中止", "历史红色消息仍可定位有效节点", "RED", "JOB_ABORTED", "SYNTHETIC_OLD_ABORT", "pr-os://attempt/syn-attempt-001", True, True, "HISTORY"),
    ("昨日工作日志快照完成", "历史消息详情不再提供确认动作", "GREEN", "FINAL_ARTIFACT_READY", "SYNTHETIC_LOG_SNAPSHOT", "pr-os://event/syn-job-001/syn-event-001", False, False, "HISTORY"),
)


class _ApplicationServiceFirstResolver:
    def __init__(self, service: Any, catalog_path: Path | None):
        self.service = service
        catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path is not None else {"targets": []}
        self.targets: dict[str, dict[str, Any]] = {}
        for row in catalog["targets"]:
            locator = StableLocator.parse(row["locator"])
            self.targets[row["locator"]] = {
                "schema_version": "P08T07SyntheticCatalogResolution-v1",
                "locator": row["locator"],
                "kind": locator.kind,
                "object_id": locator.object_id,
                "job_id": locator.job_id or (locator.object_id if locator.kind == "job" else None),
                "route": row["route"],
                "target": {
                    "synthetic_only": True,
                    "kind": locator.kind,
                    "object_id": locator.object_id,
                    "job_id": locator.job_id,
                },
                "resolution_origin": "FROZEN_H0_SYNTHETIC_CATALOG",
            }

    def resolve(self, rendered: str) -> dict[str, Any]:
        try:
            return self.service.call("service.resolve_locator", {"locator": rendered})
        except Exception:
            if rendered not in self.targets:
                raise ValueError("LOCATOR_TARGET_NOT_FOUND")
            return deepcopy(self.targets[rendered])


def _event(sequence: int, row: tuple[Any, ...]) -> dict[str, Any]:
    title, summary, severity, event_type, root_cause, locator, protected, _starred, _collection = row
    return {
        "sequence": sequence,
        "event_id": f"syn-event-{sequence:04d}",
        "event_type": event_type,
        "job_id": "syn-job-001",
        "run_id": f"syn-run-{sequence:03d}",
        "attempt_id": "syn-attempt-001",
        "node_id": f"syn-node-{sequence:03d}",
        "root_cause": root_cause,
        "occurred_at": f"2026-08-{18 + sequence:02d}T{8 + sequence:02d}:12:09+09:00",
        "severity": severity,
        "message_required": True,
        "safe_title": title,
        "safe_summary": summary,
        "safe_body": summary,
        "target_locator": locator,
        "protected_bulk_read": protected,
        "attachment_refs": [],
        "status_axes": dict(_STATUS_AXES),
        "report_period": "2026-W34" if event_type == "REPORT_READY" else None,
    }


class MessagesProductController:
    adapter_kind = "t07_messages_local_projection_plus_application_service_first_locator"

    def __init__(
        self,
        service: Any,
        control_store: ControlStore,
        contract_root: Path,
        fixture_root: Path,
        state_root: Path,
        *,
        include_synthetic_projection: bool = True,
        phase1_job_provider: Callable[[], list[Mapping[str, Any]]] | None = None,
    ):
        self.service = service
        self.control_store = control_store
        self.contract_root = contract_root.resolve()
        self.fixture_root = fixture_root.resolve()
        self.state_root = state_root.resolve()
        self.include_synthetic_projection = bool(include_synthetic_projection)
        self._phase1_job_provider = phase1_job_provider
        self.store = MessageStore(self.state_root / "message_projection")
        self._runtime_event_lock = threading.RLock()
        self.controller = MessageController(self.store, self.control_store)
        self.resolver = _ApplicationServiceFirstResolver(
            self.service, self.fixture_root / "locator_catalog.json" if self.include_synthetic_projection else None
        )
        self.navigator = MessageNavigator(
            self.resolver,
            self.control_store,
            synthetic_only=self.include_synthetic_projection,
        )
        self.bridge = SyntheticWindowsBridge()
        self.notifications = NotificationPlanner(self.store, self.bridge)
        if self.include_synthetic_projection:
            self._seed_if_empty()

    @staticmethod
    def _require_keys(params: Mapping[str, Any], required: set[str], optional: set[str] | None = None) -> None:
        optional = optional or set()
        keys = set(params)
        if not required <= keys or keys - required - optional:
            raise MessageActionError("MESSAGES_PRODUCT_PARAMS_INVALID")

    def _seed_if_empty(self) -> None:
        if self.store.records():
            return
        receipt = MessageProjectionEngine(self.store, synthetic_only=True).ingest(
            [_event(index, row) for index, row in enumerate(_SEED_ROWS, start=1)]
        )
        if receipt["status"] != "PASS" or receipt["message_created_count"] != len(_SEED_ROWS):
            raise RuntimeError("T07_SYNTHETIC_MESSAGE_SEED_FAILED")
        by_title = {record.title: record.message_id for record in self.store.records()}

        def mutate(state: dict[str, Any]) -> None:
            for row in _SEED_ROWS:
                title, _summary, _severity, _event_type, _root, _locator, _protected, starred, collection = row
                raw = state["messages"][by_title[title]]
                if starred:
                    raw.update({"starred": True, "starred_at": raw["created_at"]})
                if collection == "HISTORY":
                    raw.update(
                        {
                            "read_state": "READ",
                            "read_at": raw["created_at"],
                            "confirmation_state": "CONFIRMED",
                            "confirmed_at": raw["created_at"],
                            "collection_state": "HISTORY",
                        }
                    )
                MessageRecord.from_mapping(raw)

        self.store.transaction(mutate)

    def _records(self, mode: str | None = None, search: str = "") -> list[dict[str, Any]]:
        self._sync_phase1_messages()
        self._migrate_public_failure_copy()
        rows = stable_message_sort(self.store.records()) if mode is None else self.controller.list_messages(mode, search=search)
        projected: list[dict[str, Any]] = []
        for row in rows:
            accepted = row.to_dict()
            source = accepted["source_event_ref"]
            target_node_id = source.get("node_id")
            target_available = source.get("target_unavailable_at") is None
            accepted.update(
                {
                    "target_node_id": target_node_id,
                    "target_view": (
                        "CURRENT_TASK_NODE_INSPECTOR"
                        if target_node_id and accepted.get("target_locator")
                        else None
                    ),
                    "target_available": bool(
                        accepted.get("target_locator") and target_available
                    ),
                    "bulk_read_eligible": bool(
                        accepted["collection_state"] == "CURRENT"
                        and accepted["read_state"] == "UNREAD"
                        and not accepted["protected_bulk_read"]
                    ),
                }
            )
            projected.append(accepted)
        return projected

    @staticmethod
    def _phase1_failure_body(error_code: str, failed_node_id: str = "") -> str:
        """Translate internal execution identifiers into stable product language."""

        marker = f"{failed_node_id} {error_code}".upper()
        node_label = "文献处理流程"
        # The frozen node id is authoritative.  Legacy combined-M1 error text
        # must never pull a node-02 embedding failure back onto the new node 01.
        exact_node_labels = {
            "01_DOCUMENT_INGEST": "文档处理",
            "02_CHUNK_EMBEDDING": "向量化",
            "03_CARD_DISTILL": "生成卡片",
            "04_CARD_CROSS_CHECK": "核对卡片",
            "05_CARD_ADMISSION": "卡片准入",
            "06_CONTEXT_PACK": "整理分析材料",
            "07_ANALYSIS": "分析",
            "08_JUDGMENT_CROSS_CHECK": "核对分析",
            "09_HUMAN_FINAL": "人工判断",
        }
        if str(failed_node_id).upper() in exact_node_labels:
            node_label = exact_node_labels[str(failed_node_id).upper()]
        for needles, label in (
            (("02_CHUNK", "EMBED"), "向量化"),
            (("01_DOCUMENT",), "文档处理"),
            (("03_CARD", "M2_FAILED", "CARDPARSE"), "生成卡片"),
            (("04_CARD", "ADMISSION"), "核对卡片"),
            (("05_CARD",), "卡片准入"),
            (("06_CONTEXT",), "整理分析材料"),
            (("07_ANALYSIS",), "分析"),
            (("08_JUDGMENT",), "核对分析"),
            (("09_HUMAN",), "人工判断"),
        ):
            if node_label == "文献处理流程" and any(needle in marker for needle in needles):
                node_label = label
                break
        length_exhausted = any(
            needle in marker
            for needle in (
                "FINISH_REASON_LENGTH",
                "FINISH_REASON=Length".upper(),
                "OUTPUT_TRUNCAT",
                "仍被截断",
                "8192_TO_16384",
            )
        )
        local_embedding_capacity = any(
            needle in marker
            for needle in ("PHYSICAL BATCH SIZE", "INPUT_TOO_LARGE", "TOO LARGE TO PROCESS")
        )
        if length_exhausted:
            problem = "输出不完整"
            next_action = "重新处理时将分段生成，可先调整模型"
        elif local_embedding_capacity:
            problem = "向量处理容量不足"
            next_action = "重新处理时将分段处理，可先更换向量模型"
        else:
            problem = "未生成有效结果"
            next_action = "可调整模型后重新处理"
        return f"{node_label}失败：{problem}。{next_action}。"

    def _migrate_public_failure_copy(self) -> None:
        """Repair already-projected r8 rows without changing their audit identity."""

        forbidden = re.compile(
            r"(?:PHASE\d*|PHASE1|P08B\d+|FINISH_REASON(?:_LENGTH|=length)|\bM\d+_FAILED\b)",
            re.IGNORECASE,
        )

        def mutate(state: dict[str, Any]) -> int:
            changed = 0
            for raw in state["messages"].values():
                if raw.get("severity") != "RED" or not str(raw.get("title") or "").endswith("处理失败"):
                    continue
                combined = f"{raw.get('summary', '')} {raw.get('body', '')}"
                if not forbidden.search(combined):
                    continue
                copy = self._phase1_failure_body(combined)
                raw.update({"summary": copy[:320], "body": copy})
                MessageRecord.from_mapping(raw)
                changed += 1
            return changed

        envelope = self.store.load(recover_corruption=False)
        if any(
            raw.get("severity") == "RED"
            and str(raw.get("title") or "").endswith("处理失败")
            and forbidden.search(f"{raw.get('summary', '')} {raw.get('body', '')}")
            for raw in envelope["state"]["messages"].values()
        ):
            self.store.transaction(mutate)

    def _sync_phase1_messages(self) -> None:
        """Project terminal Phase 1 lifecycle facts without polling noise."""

        if self.include_synthetic_projection or self._phase1_job_provider is None:
            return
        rows = self._phase1_job_provider()
        if not isinstance(rows, list):
            raise MessageActionError("MESSAGES_PHASE1_PROVIDER_RESULT_INVALID")
        # Recover from old versions too: use durable lineage and successful
        # execution, never titles or merely the creation of a retry request.
        by_id = {row["job_id"]: row for row in rows if isinstance(row, Mapping) and row.get("job_id")}
        resolved = {}
        for row in by_id.values():
            successor = by_id.get(row.get("superseded_by_job_id"))
            if successor and successor.get("control_state") == "SUCCEEDED":
                resolved[row["job_id"]] = successor["job_id"]
        self._remove_resolved_failure_messages(resolved)
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            state = str(row.get("control_state") or "").upper()
            if state not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                continue
            if state == "FAILED" and row.get("job_id") in resolved:
                continue
            self.ingest_runtime_event({**dict(row), "workflow_kind": "PHASE1_DOCUMENT"})

    def _remove_resolved_failure_messages(self, resolutions: Mapping[str, str]) -> list[str]:
        """Remove obsolete notification projections; job/attempt evidence is untouched."""
        if not resolutions:
            return []
        with self._runtime_event_lock:
            targets = [
                row.message_id for row in self.store.records()
                if row.severity == "RED"
                and row.status_axes.lifecycle_status == "failed"
                and row.source_event_ref.get("job_id") in resolutions
            ]
            if not targets:
                return []
            receipt = self.store.delete_records(targets)
            receipt.update({
                "reason": "SUCCESSFUL_RECOVERY_RESOLVED_FAILURE",
                "resolved_by_job_id": dict(resolutions),
                "backend_job_evidence_deleted": False,
            })
            self.store._atomic_write(
                self.store.profile_root / f"resolved_failures_{receipt['revision']:08d}.json",
                receipt,
            )
            if self.controller.selected_message_id in targets:
                self.controller.selected_message_id = None
            return targets

    def _mark_job_start_messages_read(self, job_id: str, read_at: str) -> list[str]:
        """Close the unread lifecycle loop for start messages of one finished job."""

        def mutate(state: dict[str, Any]) -> list[str]:
            changed: list[str] = []
            for message_id, raw in state["messages"].items():
                source = raw.get("source_event_ref")
                axes = raw.get("status_axes")
                if (
                    not isinstance(source, Mapping)
                    or source.get("job_id") != job_id
                    or not isinstance(axes, Mapping)
                    or axes.get("lifecycle_status") != "in_progress"
                    or raw.get("read_state") != "UNREAD"
                ):
                    continue
                raw.update({"read_state": "READ", "read_at": read_at})
                MessageRecord.from_mapping(raw)
                changed.append(message_id)
            return changed

        _, changed = self.store.transaction(mutate)
        return changed

    def ingest_runtime_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Project one local lifecycle transition through the existing message engine."""

        if not isinstance(event, Mapping):
            raise MessageActionError("MESSAGES_RUNTIME_EVENT_INVALID")
        job_id = str(event.get("job_id") or "")
        attempt_id = str(event.get("attempt_id") or "")
        control_state = str(event.get("control_state") or "").upper()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}", job_id):
            raise MessageActionError("MESSAGES_RUNTIME_JOB_ID_INVALID")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}", attempt_id):
            raise MessageActionError("MESSAGES_RUNTIME_ATTEMPT_ID_INVALID")
        workflow_kind = str(event.get("workflow_kind") or "SESSION_REFINEMENT").upper()
        phase1 = workflow_kind == "PHASE1_DOCUMENT"
        presentation = {
            "RUNNING": (
                "BLUE",
                "USER_INFORMATION",
                "PHASE1_DOCUMENT_STARTED" if phase1 else "SESSION_REFINEMENT_STARTED",
                "开始处理",
                "正在处理" if phase1 else "精炼中",
                "in_progress",
                "NOT_ASSESSED",
            ),
            "SUCCEEDED": (
                "GREEN",
                "JOB_COMPLETED",
                "PHASE1_DOCUMENT_COMPLETED" if phase1 else "SESSION_REFINEMENT_COMPLETED",
                "处理完成" if phase1 else "精炼完成",
                (
                    "文件已生成，待复核"
                    if phase1
                    else "精炼结果已生成，可在资料库查看。"
                ),
                "completed",
                "PASS",
            ),
            "FAILED": (
                "RED",
                "JOB_FAILED",
                "PHASE1_DOCUMENT_FAILED" if phase1 else "SESSION_REFINEMENT_FAILED",
                "处理失败" if phase1 else "执行失败",
                (
                    "处理失败，请查看任务。"
                    if phase1
                    else "精炼任务执行失败，可在会话管理中查看原因并重试。"
                ),
                "failed",
                "FAIL",
            ),
            "CANCELLED": (
                "BLUE",
                "USER_INFORMATION",
                "PHASE1_DOCUMENT_CANCELLED" if phase1 else "SESSION_REFINEMENT_CANCELLED",
                "任务已取消",
                "文献处理任务已由用户取消。" if phase1 else "精炼任务已由用户取消。",
                "aborted",
                "NOT_ASSESSED",
            ),
        }.get(control_state)
        if presentation is None:
            raise MessageActionError("MESSAGES_RUNTIME_STATE_INVALID")
        severity, event_type, root_cause, verb, body, lifecycle, verification = presentation
        display_name = str(
            event.get("display_name") or ("文献处理" if phase1 else "精炼对话")
        ).strip()[:120]
        raw_error = str(event.get("error_code") or "")
        with self._runtime_event_lock:
            sequence = int(self.store.load(recover_corruption=False)["state"]["projection"]["cursor"]) + 1
            failed_node_id = str(event.get("failed_node_id") or "").strip()
            if not failed_node_id:
                nodes = event.get("nodes") or event.get("workflow_nodes") or []
                if isinstance(nodes, list):
                    failed_node_id = next(
                        (
                            str(row.get("node_id") or "")
                            for row in nodes
                            if isinstance(row, Mapping) and row.get("state") == "FAILED"
                        ),
                        "",
                    )
            if control_state == "FAILED":
                body = self._phase1_failure_body(raw_error, failed_node_id)
            elif control_state == "SUCCEEDED" and phase1:
                context_pack = event.get("context_pack")
                if (
                    isinstance(context_pack, Mapping)
                    and context_pack.get("truncated") is True
                ):
                    body = (
                        "文献处理产物已生成；整理分析材料 按 Analysis 容量保留了 "
                        f"{int(context_pack.get('retained_estimated_tokens') or 0)} / "
                        f"{int(context_pack.get('original_estimated_tokens') or 0)} 估算 tokens，"
                        f"丢弃 {int(context_pack.get('dropped_evidence_block_count') or 0)} 个证据块。"
                        "可在资料库查看 整理分析材料；如需更多证据，可更换更大上下文模型后从该节点重新执行。"
                    )
            projected = {
                "sequence": sequence,
                "event_id": f"runtime-{uuid.uuid4().hex}",
                "event_type": event_type,
                "job_id": job_id,
                "run_id": job_id,
                "attempt_id": attempt_id,
                "node_id": failed_node_id or None,
                "root_cause": root_cause,
                "occurred_at": utc_now(),
                "severity": severity,
                "message_required": True,
                "safe_title": f"{display_name} {verb}",
                "safe_summary": body,
                "safe_body": body,
                "target_locator": f"pr-os://job/{job_id}",
                "protected_bulk_read": severity in {"RED", "YELLOW"},
                "attachment_refs": [],
                "status_axes": {
                    "lifecycle_status": lifecycle,
                    "verification_result": verification,
                    "acceptance_verdict": "NOT_ASSESSED",
                    "capability_status": "AVAILABLE",
                },
                "report_period": None,
            }
            dedupe_key = message_dedupe_key(projected)
            if any(row.dedupe_key == dedupe_key for row in self.store.records()):
                return {
                    "schema_version": "P08RuntimeMessageProjectionReceipt-v1",
                    "status": "ALREADY_PROJECTED",
                    "message_created_count": 0,
                    "message_updated_count": 0,
                    "auto_read_start_message_ids": [],
                    "auto_read_start_message_count": 0,
                }
            receipt = MessageProjectionEngine(
                self.store, synthetic_only=False
            ).ingest([projected])
            auto_read = (
                self._mark_job_start_messages_read(job_id, projected["occurred_at"])
                if control_state in {"SUCCEEDED", "FAILED", "CANCELLED"}
                else []
            )
            return {
                **receipt,
                "auto_read_start_message_ids": auto_read,
                "auto_read_start_message_count": len(auto_read),
            }

    def _contract(self) -> dict[str, Any]:
        names = (
            "MessageStateAxesContract.json",
            "MessageIdentityDedupeOrderRules.json",
            "LocatorRouteContract.json",
            "NotificationPrivacyPolicy.json",
        )
        contracts = {
            name: json.loads((self.contract_root / name).read_text(encoding="utf-8"))
            for name in names
        }
        return {
            "schema_version": "P08T07MessagesProductContract-v1",
            "method_count": len(MESSAGES_METHODS),
            "methods": sorted(MESSAGES_METHODS),
            "contracts": contracts,
            "message_projection_owned_by_desktop_product": True,
            "application_service_first_locator_resolution": True,
            "control_store_persistence": True,
            "synthetic_windows_bridge_only": True,
            "production_event_reads": 0,
            "production_message_reads": 0,
            "windows_notification_registrations": 0,
            "status": "PASS",
        }

    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        if method not in MESSAGES_METHODS:
            raise MessageActionError("MESSAGES_PRODUCT_METHOD_NOT_ALLOWLISTED")
        accepted = dict(params)
        if any(str(key).startswith("_") for key in accepted):
            raise MessageActionError("MESSAGES_PRODUCT_PRIVATE_PARAM_FORBIDDEN")
        if method == "messages.get_contract":
            self._require_keys(accepted, set())
            return self._contract()
        if method == "messages.bootstrap":
            self._require_keys(accepted, set())
            service = self.service.call("service.health", {})
            health_fields = {
                "schema_version", "protocol_version", "adapter_kind", "request_count",
                "auth_rejection_count", "connection_count", "uptime_seconds",
                "external_network_calls", "external_model_calls", "credential_value_reads",
            }
            counters = ("request_count", "auth_rejection_count", "connection_count")
            if (
                set(service) != health_fields
                or service.get("schema_version") != "ApplicationServiceHealth-v1"
                or service.get("protocol_version") != "1.0"
                or not isinstance(service.get("adapter_kind"), str)
                or not service["adapter_kind"]
                or any(isinstance(service.get(key), bool) or not isinstance(service.get(key), int) or service[key] < 0 for key in counters)
                or isinstance(service.get("uptime_seconds"), bool)
                or not isinstance(service.get("uptime_seconds"), (int, float))
                or service["uptime_seconds"] < 0
                or any(service.get(key) != 0 for key in ("external_network_calls", "external_model_calls", "credential_value_reads"))
            ):
                raise MessageActionError("MESSAGES_SERVICE_HEALTH_CONTRACT_INVALID")
            restored = self.controller.restore_ui_state()
            rows = self._records()
            return {
                "schema_version": "P08T07MessagesBootstrapProjection-v1",
                "records": rows,
                "record_count": len(rows),
                "current_count": sum(row["collection_state"] == "CURRENT" for row in rows),
                "history_count": sum(row["collection_state"] == "HISTORY" for row in rows),
                "unread_count": self.controller.unread_count(),
                "aggregate_action_state": self.controller.aggregate_action_state(),
                "restored_ui": restored,
                "service_status": "READY",
                "synthetic_only": self.include_synthetic_projection,
                "status": "PASS",
            }
        if method == "messages.list":
            self._require_keys(accepted, set(), {"mode", "search"})
            rows = self._records(accepted.get("mode"), str(accepted.get("search", "")))
            return {
                "schema_version": "P08T07MessageListProjection-v1",
                "mode": accepted.get("mode") or "ALL",
                "rows": rows,
                "row_count": len(rows),
                "unread_count": self.controller.unread_count(),
                "status": "PASS",
            }
        if method == "messages.switch_mode":
            self._require_keys(accepted, {"mode"})
            return {"schema_version": "P08T07MessageModeReceipt-v1", **self.controller.switch_mode(str(accepted["mode"])), "status": "PASS"}
        if method == "messages.open_detail":
            self._require_keys(accepted, {"message_id"})
            return self.controller.open_detail(str(accepted["message_id"])).to_dict()
        if method == "messages.close_detail":
            self._require_keys(accepted, set())
            return {"schema_version": "P08T07MessageDetailCloseReceipt-v1", **self.controller.close_detail(), "status": "PASS"}
        if method == "messages.toggle_star":
            self._require_keys(accepted, {"message_id"})
            return self.controller.toggle_star(str(accepted["message_id"])).to_dict()
        if method == "messages.confirm":
            self._require_keys(accepted, {"message_id"}, {"from_detail"})
            return self.controller.confirm(
                str(accepted["message_id"]), from_detail=bool(accepted.get("from_detail", False))
            ).to_dict()
        if method == "messages.archive_confirmed":
            self._require_keys(accepted, {"message_id"})
            return self.controller.archive_confirmed(str(accepted["message_id"]))
        if method == "messages.mark_all_read":
            self._require_keys(accepted, set())
            return self.controller.mark_all_read()
        if method == "messages.bulk_apply":
            self._require_keys(accepted, {"message_ids", "action"})
            message_ids = accepted["message_ids"]
            if not isinstance(message_ids, list) or not all(isinstance(value, str) for value in message_ids):
                raise MessageActionError("MESSAGES_PRODUCT_MESSAGE_IDS_INVALID")
            return self.controller.bulk_apply(message_ids, str(accepted["action"]))
        if method == "messages.refresh_archive":
            self._require_keys(accepted, set())
            return self.controller.refresh_archive()
        if method == "messages.navigate":
            self._require_keys(accepted, {"locator"}, {"message_id", "node_id"})
            message_id = accepted.get("message_id")
            node_id = accepted.get("node_id")
            if message_id is not None:
                record = self.store.get(str(message_id))
                expected_node_id = record.source_event_ref.get("node_id")
                if record.target_locator != str(accepted["locator"]):
                    raise MessageActionError("MESSAGES_NAVIGATION_LOCATOR_MISMATCH")
                if node_id != expected_node_id:
                    raise MessageActionError("MESSAGES_NAVIGATION_NODE_MISMATCH")
            receipt = self.navigator.navigate(
                str(accepted["locator"]),
                node_id=str(node_id) if node_id is not None else None,
            )
            if receipt.get("status") == "TARGET_UNAVAILABLE" and message_id is not None:
                unavailable_at = utc_now()
                self.store.update_record(
                    str(message_id),
                    lambda raw: (
                        raw.update({"protected_bulk_read": False}),
                        raw["source_event_ref"].update(
                            {"target_unavailable_at": unavailable_at}
                        ),
                    ),
                )
                receipt = {**receipt, "bulk_read_eligible": True}
            return receipt
        if method == "messages.notification_deliver":
            self._require_keys(accepted, {"message_id"})
            return self.notifications.deliver(str(accepted["message_id"]))
        if method == "messages.notification_activate":
            self._require_keys(accepted, {"message_id"})
            return self.notifications.activate(str(accepted["message_id"]), self.navigator)
        self._require_keys(accepted, set())
        return {
            "schema_version": "P08T07MessagesEffectMetrics-v1",
            "synthetic_messages": len(self.store.records()) if self.include_synthetic_projection else 0,
            "production_event_reads": 0,
            "production_message_reads": len(self.store.records()) if not self.include_synthetic_projection else 0,
            "production_writes": len(self.store.records()) if not self.include_synthetic_projection else 0,
            "external_network_calls": 0,
            "external_model_calls": 0,
            "provider_calls": 0,
            "credential_value_reads": 0,
            "windows_notification_api_calls": self.bridge.os_api_calls,
            "windows_notification_registration_calls": self.bridge.registration_calls,
            "permanent_deletes": 0,
            "status": "PASS",
        }


__all__ = ["MESSAGES_METHODS", "MessagesProductController"]
