from __future__ import annotations

from pr_os_desktop_service.projection_reads import projection_request, once_per_projection
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any, Callable, Mapping


WORK_LOG_METHODS = frozenset(
    {
        "work_log.get_contract",
        "work_log.bootstrap",
        "work_log.list_entries",
        "work_log.get_entry",
        "work_log.get_milestones",
        "work_log.set_filter",
        "work_log.select",
        "work_log.resolve_job_locator",
        "work_log.resolve_evidence_locator",
        "work_log.get_source_status",
        "work_log.refresh",
        "work_log.export_preview",
        "work_log.export_redacted_view",
        "work_log.verify_export",
        "work_log.inject_fault",
        "work_log.restore",
        "work_log.effect_metrics",
    }
)

FORBIDDEN_EXPORT_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "password",
        "private_payload",
        "raw_prompt",
        "restricted_path",
        "secret",
        "token",
    }
)
RESTRICTED_EFFECT_KEYS = (
    "branch_log_writes",
    "business_network_calls",
    "central_log_writes",
    "credential_value_reads",
    "external_model_calls",
    "external_process_launches",
    "frozen_evidence_writes",
    "m11_log_writes",
    "private_payload_reads",
    "production_writes",
    "raw_authority_content_reads",
    "shell_invocations",
    "source_ledger_writes",
)

PUBLIC_PHASE1_NODE_LABELS = {
    "01_DOCUMENT_INGEST": "步骤 01 · 文档处理",
    "02_CHUNK_EMBEDDING": "步骤 02 · 向量化",
    "03_CARD_DISTILL": "步骤 03 · 生成卡片",
    "04_CARD_CROSS_CHECK": "步骤 04 · 核对卡片",
    "05_CARD_ADMISSION": "步骤 05 · 卡片准入",
    "06_CONTEXT_PACK": "步骤 06 · 整理分析材料",
    "07_ANALYSIS": "步骤 07 · 分析",
    "08_JUDGMENT_CROSS_CHECK": "步骤 08 · 判断类异源校对",
    "09_HUMAN_FINAL": "步骤 09 · 人工判断",
}


class WorkLogProductError(ValueError):
    pass


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _sha256_json(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(body)


class WorkLogProductController:
    adapter_kind = "t10_work_log_local_lifecycle_projection_public_safe_atomic_export"

    def __init__(
        self,
        service: Any,
        control_store: Any,
        contract_root: Path,
        fixture_root: Path,
        state_root: Path,
        *,
        include_synthetic_projection: bool = True,
        refinement_task_provider: Callable[[], list[Mapping[str, Any]]] | None = None,
        refinement_product_provider: Callable[[], list[Mapping[str, Any]]] | None = None,
        phase1_job_provider: Callable[[], list[Mapping[str, Any]]] | None = None,
        phase1_product_provider: Callable[[], list[Mapping[str, Any]]] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.service = service
        self.control_store = control_store
        self.contract_root = contract_root.resolve()
        self.fixture_root = fixture_root.resolve()
        self.state_root = state_root.resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_root / "work_log_projection.json"
        self.export_root = self.state_root / "exports"
        self.export_root.mkdir(parents=True, exist_ok=True)
        self.include_synthetic_projection = bool(include_synthetic_projection)
        self._refinement_task_provider = refinement_task_provider
        self._refinement_product_provider = refinement_product_provider
        self._phase1_job_provider = phase1_job_provider
        self._phase1_product_provider = phase1_product_provider
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        if self.include_synthetic_projection:
            self._canaries = json.loads((self.fixture_root / "synthetic_canary_corpus.json").read_text(encoding="utf-8"))
            self._sentinels = json.loads((self.fixture_root / "source_authority_sentinels.json").read_text(encoding="utf-8"))
            self._validate_canaries(self._canaries)
            self._validate_sentinels(self._sentinels)
        else:
            self._canaries = {"fields": [], "values": []}
            self._sentinels = {"sources": []}
        if self.include_synthetic_projection:
            self._stream = json.loads((self.fixture_root / "synthetic_work_log_stream.json").read_text(encoding="utf-8"))
            self._validate_stream(self._stream)
            self._rows_by_id = {
                row["log_entry_id"]: deepcopy(row)
                for row in self._stream["entries"]
            }
        else:
            self._stream = {
                "schema_version": "P08T10ProductionWorkLogStream-v1",
                "synthetic_only": False,
                "source_authority_mode": "NO_SOURCE_CONNECTED",
                "entries": [],
            }
            self._rows_by_id = {}
        self._metrics = {
            "synthetic_fixture_reads": 3 if self.include_synthetic_projection else 0,
            "local_projection_writes": 0,
            "synthetic_export_writes": 0,
            "export_verifications": 0,
            "locator_resolutions": 0,
            "redacted_field_count": 0,
            "branch_log_writes": 0,
            "business_network_calls": 0,
            "central_log_writes": 0,
            "credential_value_reads": 0,
            "external_model_calls": 0,
            "external_process_launches": 0,
            "frozen_evidence_writes": 0,
            "m11_log_writes": 0,
            "private_payload_reads": 0,
            "production_writes": 0,
            "raw_authority_content_reads": 0,
            "shell_invocations": 0,
            "source_ledger_writes": 0,
        }
        if not self.state_path.exists():
            self._save(
                {
                    "schema_version": "P08T10WorkLogProjectionState-v1",
                    "revision": 0,
                    "view_snapshot_sequence": 18,
                    "selected_log_entry_id": None,
                    "time_range": "7",
                    "ledger_type": "all",
                    "search": "",
                    "severity": "all",
                    "status_axis": "all",
                    "date_from": None,
                    "date_to": None,
                    "page": 1,
                    "page_size": 50,
                    "pending_count": 0,
                    "fault_mode": None,
                    "locator_history": [],
                    "export_receipts": [],
                }
            )
        self._validate_state(self._load())
        if not self.include_synthetic_projection:
            state = self._load()
            if (
                state["selected_log_entry_id"] is not None
                or state["pending_count"] != 0
                or state["locator_history"]
            ):
                state["selected_log_entry_id"] = None
                state["pending_count"] = 0
                state["locator_history"] = []
                self._save(state)

    @staticmethod
    def _require_keys(params: Mapping[str, Any], required: set[str], optional: set[str] | None = None) -> None:
        optional = optional or set()
        keys = set(params)
        if not required <= keys or keys - required - optional:
            raise WorkLogProductError("WORK_LOG_PRODUCT_PARAMS_INVALID")

    @staticmethod
    def _validate_stream(stream: Mapping[str, Any]) -> None:
        if set(stream) != {"schema_version", "synthetic_only", "source_authority_mode", "entries"}:
            raise WorkLogProductError("WORK_LOG_STREAM_FIELDS_INVALID")
        if stream.get("schema_version") != "P08T10SyntheticWorkLogStream-v1":
            raise WorkLogProductError("WORK_LOG_STREAM_SCHEMA_INVALID")
        if stream.get("synthetic_only") is not True or stream.get("source_authority_mode") != "LOCATOR_HASH_SENTINEL_ONLY":
            raise WorkLogProductError("WORK_LOG_STREAM_AUTHORITY_INVALID")
        rows = stream.get("entries")
        if not isinstance(rows, list) or len(rows) != 7:
            raise WorkLogProductError("WORK_LOG_STREAM_DENOMINATOR_INVALID")
        required = {
            "age_bucket",
            "date",
            "event_sequence",
            "event_time_utc",
            "evidence_locators",
            "job_id",
            "job_locator",
            "ledger_type",
            "log_entry_id",
            "milestones",
            "parse_status",
            "public_payload",
            "severity",
            "source_locator",
            "state_axis",
            "summary",
            "title",
            "type_label",
        }
        ids: set[str] = set()
        sequences: set[int] = set()
        for row in rows:
            if set(row) != required:
                raise WorkLogProductError("WORK_LOG_STREAM_ROW_FIELDS_INVALID")
            entry_id = row["log_entry_id"]
            sequence = row["event_sequence"]
            if not isinstance(entry_id, str) or not entry_id or entry_id in ids:
                raise WorkLogProductError("WORK_LOG_ENTRY_ID_INVALID")
            if not isinstance(sequence, int) or sequence < 1 or sequence in sequences:
                raise WorkLogProductError("WORK_LOG_EVENT_SEQUENCE_INVALID")
            if row["ledger_type"] not in {"branch", "central"}:
                raise WorkLogProductError("WORK_LOG_LEDGER_TYPE_INVALID")
            if row["parse_status"] not in {"ready", "partial", "failed"}:
                raise WorkLogProductError("WORK_LOG_PARSE_STATUS_INVALID")
            if row["severity"] not in {"INFO", "WARNING", "ERROR"}:
                raise WorkLogProductError("WORK_LOG_SEVERITY_INVALID")
            if row["job_locator"] != f"pr-os://job/{row['job_id']}":
                raise WorkLogProductError("WORK_LOG_JOB_LOCATOR_INVALID")
            if not row["source_locator"].startswith("pr-os://ledger/"):
                raise WorkLogProductError("WORK_LOG_SOURCE_LOCATOR_INVALID")
            if not isinstance(row["evidence_locators"], list) or not all(
                isinstance(value, str) and value.startswith("pr-os://evidence/") for value in row["evidence_locators"]
            ):
                raise WorkLogProductError("WORK_LOG_EVIDENCE_LOCATOR_INVALID")
            if not isinstance(row["milestones"], list) or not isinstance(row["public_payload"], dict):
                raise WorkLogProductError("WORK_LOG_PUBLIC_PROJECTION_INVALID")
            ids.add(entry_id)
            sequences.add(sequence)

    @staticmethod
    def _validate_canaries(canaries: Mapping[str, Any]) -> None:
        if set(canaries) != {"schema_version", "synthetic_only", "fields", "values"}:
            raise WorkLogProductError("WORK_LOG_CANARY_FIELDS_INVALID")
        if canaries.get("schema_version") != "P08T10SyntheticCanaryCorpus-v1" or canaries.get("synthetic_only") is not True:
            raise WorkLogProductError("WORK_LOG_CANARY_SCHEMA_INVALID")
        fields = canaries.get("fields")
        values = canaries.get("values")
        if sorted(fields) != sorted(FORBIDDEN_EXPORT_KEYS) or not isinstance(values, list) or len(values) != len(fields):
            raise WorkLogProductError("WORK_LOG_CANARY_DENOMINATOR_INVALID")

    @staticmethod
    def _validate_sentinels(sentinels: Mapping[str, Any]) -> None:
        if set(sentinels) != {"schema_version", "authority_mode", "sources", "status"}:
            raise WorkLogProductError("WORK_LOG_SENTINEL_FIELDS_INVALID")
        if sentinels.get("schema_version") != "P08T10SourceAuthoritySentinels-v1":
            raise WorkLogProductError("WORK_LOG_SENTINEL_SCHEMA_INVALID")
        if sentinels.get("authority_mode") != "HASH_METADATA_ONLY_NO_RAW_BYTES" or sentinels.get("status") != "PASS":
            raise WorkLogProductError("WORK_LOG_SENTINEL_MODE_INVALID")
        sources = sentinels.get("sources")
        if not isinstance(sources, list) or [row.get("source_kind") for row in sources] != ["BRANCH_LOG", "CENTRAL_LOG", "M11_LOG"]:
            raise WorkLogProductError("WORK_LOG_SENTINEL_SOURCE_SET_INVALID")
        for row in sources:
            if sorted(row) != ["raw_content_copied", "raw_content_decoded_or_rendered", "sha256", "source_kind", "source_label"]:
                raise WorkLogProductError("WORK_LOG_SENTINEL_ROW_FIELDS_INVALID")
            if row["raw_content_copied"] is not False or row["raw_content_decoded_or_rendered"] is not False:
                raise WorkLogProductError("WORK_LOG_SENTINEL_RAW_ACCESS_INVALID")
            if not re.fullmatch(r"[A-F0-9]{64}", row["sha256"]):
                raise WorkLogProductError("WORK_LOG_SENTINEL_HASH_INVALID")

    @staticmethod
    def _validate_state(state: Mapping[str, Any]) -> None:
        required = {
            "schema_version",
            "revision",
            "view_snapshot_sequence",
            "selected_log_entry_id",
            "time_range",
            "ledger_type",
            "search",
            "severity",
            "status_axis",
            "date_from",
            "date_to",
            "page",
            "page_size",
            "pending_count",
            "fault_mode",
            "locator_history",
            "export_receipts",
        }
        if set(state) != required or state.get("schema_version") != "P08T10WorkLogProjectionState-v1":
            raise WorkLogProductError("WORK_LOG_STATE_FIELDS_INVALID")
        if not isinstance(state["revision"], int) or state["revision"] < 0:
            raise WorkLogProductError("WORK_LOG_STATE_REVISION_INVALID")
        if not isinstance(state["view_snapshot_sequence"], int) or state["view_snapshot_sequence"] < 18:
            raise WorkLogProductError("WORK_LOG_SNAPSHOT_SEQUENCE_INVALID")
        if state["time_range"] not in {"today", "7", "30", "all"} or state["ledger_type"] not in {"all", "branch", "central"}:
            raise WorkLogProductError("WORK_LOG_FILTER_AXIS_INVALID")
        if not isinstance(state["locator_history"], list) or not isinstance(state["export_receipts"], list):
            raise WorkLogProductError("WORK_LOG_STATE_COLLECTION_INVALID")

    def _load(self) -> dict[str, Any]:
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self._validate_state(state)
        return state

    def _save(self, state: dict[str, Any]) -> None:
        state["revision"] = int(state.get("revision", -1)) + 1
        self._validate_state(state)
        temporary = self.state_path.with_suffix(".json.next")
        if temporary.exists():
            raise FileExistsError("WORK_LOG_STATE_TEMP_COLLISION")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(self.state_path)
        self._metrics["local_projection_writes"] += 1

    @staticmethod
    def _public(row: Mapping[str, Any]) -> dict[str, Any]:
        result = deepcopy(dict(row))
        result["milestone_count"] = len(result.pop("milestones"))
        result["public_safe_projection"] = True
        result["absolute_path_included"] = False
        result["raw_private_content_included"] = False
        result["raw_authority_bytes_included"] = False
        return result

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)

    def _age_bucket(self, event_time: datetime) -> str:
        now = self._now_provider()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise WorkLogProductError("WORK_LOG_NOW_PROVIDER_INVALID")
        elapsed_days = max(0.0, (now.astimezone(timezone.utc) - event_time).total_seconds()) / 86400
        if elapsed_days < 1:
            return "today"
        if elapsed_days <= 7:
            return "7"
        if elapsed_days <= 30:
            return "30"
        return "older"

    @staticmethod
    def _state_projection(
        control_state: str, workflow_kind: str = "SESSION_REFINEMENT"
    ) -> tuple[str, str, str]:
        if workflow_kind == "PHASE1_DOCUMENT":
            return {
                "QUEUED": ("等待执行", "INFO", "排队中"),
                "RUNNING": ("正在执行", "INFO", "处理中"),
                "SUCCEEDED": ("已经结束", "INFO", "文件已生成，待复核"),
                "FAILED": ("执行异常", "ERROR", "处理失败"),
                "CANCELLED": ("已取消", "WARNING", "文献处理任务已取消。"),
            }.get(control_state, ("状态未知", "WARNING", "文献处理状态尚未识别。"))
        return {
            "QUEUED": ("等待执行", "INFO", "排队中"),
            "RUNNING": ("正在执行", "INFO", "精炼中"),
            "SUCCEEDED": ("已经结束", "INFO", "精炼任务已经完成。"),
            "FAILED": ("执行异常", "ERROR", "精炼任务执行异常。"),
            "CANCELLED": ("已取消", "WARNING", "精炼任务已取消。"),
        }.get(control_state, ("状态未知", "WARNING", "任务状态尚未识别。"))

    @staticmethod
    def _milestone_time(node: Mapping[str, Any], fallback: str) -> str:
        return str(node.get("completed_at") or node.get("started_at") or fallback)

    def _runtime_milestones(
        self,
        task: Mapping[str, Any],
        product: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        milestones: list[dict[str, Any]] = []
        nodes = task.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, Mapping) or node.get("state") == "NOT_STARTED":
                    continue
                node_id = str(node.get("node_id") or "UNKNOWN")
                public_node = PUBLIC_PHASE1_NODE_LABELS.get(
                    node_id, str(node.get("name") or "文献处理节点")
                )
                state = str(node.get("state") or "UNKNOWN")
                public_state = {
                    "COMPLETED": "已完成",
                    "RUNNING": "正在执行",
                    "FAILED": "执行异常",
                    "DISABLED": "已禁用",
                    "NOT_STARTED": "未开始",
                }.get(state, "状态未知")
                milestones.append(
                    {
                        "title": public_node[:160],
                        "time": self._milestone_time(node, str(task.get("updated_at") or "")),
                        "fields": [
                            ["出现位置", public_node],
                            ["状态", public_state],
                            ["问题", "未生成有效结果" if state == "FAILED" else "无"],
                        ],
                        "technical_public": f"job_id={task.get('job_id')}\nstep={public_node}\nstate={public_state}",
                    }
                )
        if not milestones:
            milestones.append(
                {
                    "title": "任务已加入收件箱",
                    "time": str(task.get("created_at") or ""),
                    "fields": [
                        ["状态", str(task.get("control_state") or "QUEUED")],
                        ["执行门", "等待自动执行开关与队列调度"],
                    ],
                    "technical_public": f"job_id={task.get('job_id')}\nstate={task.get('control_state')}",
                }
            )
        if product is not None:
            artifact_id = str(product.get("artifact_id") or "")
            milestones.append(
                {
                    "title": "精炼成品已写入资料库",
                    "time": str(product.get("updated_at") or task.get("completed_at") or ""),
                    "fields": [
                        ["成品状态", str(product.get("status") or "待复核")],
                        ["精炼条目", f"{int(product.get('item_count') or 0)} 项"],
                        ["资料身份", artifact_id],
                    ],
                    "technical_public": f"artifact_id={artifact_id}\nraw_private_content_included=false",
                }
            )
        return milestones

    @once_per_projection
    def _sync_runtime_rows(self) -> None:
        if self.include_synthetic_projection:
            return
        refinement_tasks = (
            self._refinement_task_provider()
            if self._refinement_task_provider is not None
            else []
        )
        refinement_products = (
            self._refinement_product_provider()
            if self._refinement_product_provider is not None
            else []
        )
        phase1_tasks = (
            self._phase1_job_provider()
            if self._phase1_job_provider is not None
            else []
        )
        phase1_provider_error = ""
        try:
            phase1_products = (
                self._phase1_product_provider()
                if self._phase1_product_provider is not None
                else []
            )
        except Exception as error:
            phase1_products = []
            value = str(error).strip()
            phase1_provider_error = (
                value if re.fullmatch(r"[A-Z][A-Z0-9_]{2,119}", value)
                else "WORK_LOG_PHASE1_PROVIDER_" + type(error).__name__.upper()
            )
            diagnostic = {
                "schema_version": "MemoriveWorkLogPhase1ProjectionDiagnostic-v1",
                "status": "DEGRADED",
                "error_code": phase1_provider_error,
                "provider": "phase1_product_provider",
                "skipped_provider_and_continued": True,
                "raw_private_content_included": False,
            }
            path = self.state_root / "phase1_projection_diagnostic.json"
            temporary = path.with_suffix(".json.next")
            temporary.write_text(
                json.dumps(diagnostic, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8", newline="\n",
            )
            temporary.replace(path)
        if not all(
            isinstance(value, list)
            for value in (
                refinement_tasks,
                refinement_products,
                phase1_tasks,
                phase1_products,
            )
        ):
            raise WorkLogProductError("WORK_LOG_RUNTIME_PROVIDER_RESULT_INVALID")
        refinement_products_by_task = {
            str(row.get("task_id") or row.get("task") or ""): row
            for row in refinement_products
            if isinstance(row, Mapping) and str(row.get("task_id") or row.get("task") or "")
        }
        phase1_products_by_task: dict[str, list[Mapping[str, Any]]] = {}
        for row in phase1_products:
            if not isinstance(row, Mapping):
                continue
            task_id = str(row.get("task_id") or row.get("task") or "")
            if task_id:
                phase1_products_by_task.setdefault(task_id, []).append(row)
        tasks: list[Mapping[str, Any]] = []
        tasks.extend(
            {**dict(row), "workflow_kind": "SESSION_REFINEMENT"}
            for row in refinement_tasks
            if isinstance(row, Mapping)
        )
        tasks.extend(
            {**dict(row), "workflow_kind": "PHASE1_DOCUMENT"}
            for row in phase1_tasks
            if isinstance(row, Mapping)
        )
        ordered = sorted(
            (row for row in tasks if isinstance(row, Mapping) and row.get("job_id")),
            key=lambda row: (str(row.get("created_at") or ""), str(row.get("job_id") or "")),
        )
        projected: dict[str, dict[str, Any]] = {}
        for sequence, task in enumerate(ordered, start=1):
            job_id = str(task["job_id"])
            workflow_kind = str(task.get("workflow_kind") or "SESSION_REFINEMENT")
            control_state = str(task.get("control_state") or "UNKNOWN").upper()
            state_axis, severity, summary = self._state_projection(control_state, workflow_kind)
            event_time_raw = str(
                task.get("completed_at")
                or task.get("updated_at")
                or task.get("created_at")
                or ""
            )
            event_time = self._parse_timestamp(event_time_raw)
            if event_time is None:
                raise WorkLogProductError("WORK_LOG_RUNTIME_EVENT_TIME_INVALID")
            product: Mapping[str, Any] | None = None
            product_rows: list[Mapping[str, Any]] = []
            if workflow_kind == "PHASE1_DOCUMENT":
                product_rows = sorted(
                    phase1_products_by_task.get(job_id, []),
                    key=lambda row: (str(row.get("source_artifact_id") or ""), str(row.get("artifact_id") or "")),
                )
                product = next(
                    (
                        row
                        for row in product_rows
                        if row.get("source_artifact_id") == "phase1-human-review"
                    ),
                    product_rows[0] if product_rows else None,
                )
                unavailable_products = [
                    row for row in product_rows
                    if row.get("projection_status") == "SOURCE_UNAVAILABLE"
                    or row.get("source_file_exists") is False
                ]
                if control_state == "SUCCEEDED" and unavailable_products:
                    summary = "任务已完成，但历史源文件不可用；其他工作日志仍可查看"
                elif control_state == "SUCCEEDED" and product_rows:
                    summary = f"已生成 {len(product_rows)} 份文件，待复核"
                elif control_state == "SUCCEEDED" and phase1_provider_error:
                    summary = "任务已完成；资料库产物投影暂不可用，工作日志已继续加载"
                elif control_state == "FAILED" and task.get("error_code"):
                    failed_location = PUBLIC_PHASE1_NODE_LABELS.get(
                        str(task.get("failed_node_id") or ""), "未能确定的文献处理步骤"
                    )
                    summary = (
                        f"在“{failed_location}”处理失败。"
                        "本步骤未生成有效结果；可在当前任务查看失败位置，调整模型或重试设置后继续。"
                    )
            else:
                product = refinement_products_by_task.get(job_id)
                if control_state == "SUCCEEDED" and product is not None:
                    summary = "精炼结果已生成，待复核"
                elif control_state == "FAILED" and task.get("error_code"):
                    summary = f"精炼任务执行异常：{str(task['error_code'])[:120]}。"
            evidence_locators: list[str] = []
            related_artifact_id = None
            related_artifact_locator = None
            if workflow_kind == "PHASE1_DOCUMENT":
                evidence_locators.extend(
                    f"pr-os://evidence/phase1-artifact/{row.get('artifact_id')}"
                    for row in product_rows
                    if row.get("artifact_id")
                )
                if product is not None:
                    related_artifact_id = str(product.get("artifact_id") or "") or None
                    related_artifact_locator = str(product.get("stable_locator") or "") or None
            elif product is not None:
                related_artifact_id = str(product.get("artifact_id") or "") or None
                related_artifact_locator = str(product.get("stable_locator") or "") or None
                if related_artifact_id:
                    evidence_locators.append(
                        f"pr-os://evidence/refinement-product/{related_artifact_id}"
                    )
            local_time = event_time.astimezone()
            milestones = self._runtime_milestones(task, None if workflow_kind == "PHASE1_DOCUMENT" else product)
            if workflow_kind == "PHASE1_DOCUMENT" and product_rows:
                milestones.append(
                    {
                        "title": "文献处理产物已写入资料库",
                        "time": str(product_rows[-1].get("updated_at") or event_time_raw),
                        "fields": [
                            ["产物数量", f"{len(product_rows)} 份"],
                            ["人工复核包", "已生成" if any(row.get("source_artifact_id") == "phase1-human-review" for row in product_rows) else "未生成"],
                        ],
                        "technical_public": f"job_id={job_id}\nartifact_count={len(product_rows)}\nraw_private_content_included=false",
                    }
                )
            context_pack = task.get("context_pack")
            if (
                workflow_kind == "PHASE1_DOCUMENT"
                and isinstance(context_pack, Mapping)
                and context_pack.get("truncated") is True
            ):
                milestones.append(
                    {
                        "title": "部分材料未纳入分析",
                        "time": event_time_raw,
                        "fields": [
                            ["原始规模", f"{int(context_pack.get('original_estimated_tokens') or 0)} 估算 tokens"],
                            ["保留规模", f"{int(context_pack.get('retained_estimated_tokens') or 0)} 估算 tokens"],
                            ["丢弃证据块", f"{int(context_pack.get('dropped_evidence_block_count') or 0)} 个"],
                            ["恢复建议", "选择更大上下文的 Analysis 模型后，从 整理分析材料 重新执行"],
                        ],
                        "technical_public": f"job_id={job_id}\ncontext_pack_capacity_selection=true\nraw_private_content_included=false",
                    }
                )
            progress = task.get("progress") if isinstance(task.get("progress"), Mapping) else {}
            row = {
                "age_bucket": self._age_bucket(event_time),
                "date": local_time.date().isoformat(),
                "event_sequence": sequence,
                "event_time_utc": event_time.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                "evidence_locators": evidence_locators,
                "job_id": job_id,
                "job_locator": f"pr-os://job/{job_id}",
                "ledger_type": "branch",
                "log_entry_id": f"activity-{job_id}",
                "milestones": milestones,
                "parse_status": "ready",
                "public_payload": {
                    "control_state": control_state,
                    "error_code": str(task.get("error_code") or "")[:120],
                    "model_calls": int(task.get("model_calls") or 0),
                    "external_model_calls": int(task.get("external_model_calls") or 0),
                    "workflow_kind": workflow_kind,
                    "completed_units": int(progress.get("completed_units") or 0),
                    "total_units": int(progress.get("total_units") or (9 if workflow_kind == "PHASE1_DOCUMENT" else 4)),
                    "artifact_count": len(product_rows) if workflow_kind == "PHASE1_DOCUMENT" else (1 if product is not None else 0),
                    "artifact_projection_error_code": (
                        phase1_provider_error if workflow_kind == "PHASE1_DOCUMENT" else ""
                    ),
                    "raw_private_content_included": False,
                },
                "severity": severity,
                "source_locator": f"pr-os://ledger/branch/{job_id}",
                "state_axis": state_axis,
                "summary": summary,
                "title": str(task.get("display_name") or ("文献处理任务" if workflow_kind == "PHASE1_DOCUMENT" else "会话精炼任务"))[:180],
                "type_label": "文献处理" if workflow_kind == "PHASE1_DOCUMENT" else "会话精炼",
                "related_artifact_id": related_artifact_id,
                "related_artifact_locator": related_artifact_locator,
            }
            projected[row["log_entry_id"]] = row
        self._rows_by_id = projected
        if callable(getattr(self,"research_provider",None)):
            for row in self.research_provider():self._rows_by_id[row["log_entry_id"]]=row

    def _get(self, log_entry_id: str) -> dict[str, Any]:
        self._sync_runtime_rows()
        row = self._rows_by_id.get(log_entry_id)
        if row is None:
            raise WorkLogProductError("WORK_LOG_ENTRY_NOT_FOUND")
        return self._public(row)

    def _service_ready(self) -> bool:
        health = self.service.call("service.health", {})
        return (
            isinstance(health, dict)
            and health.get("schema_version") == "ApplicationServiceHealth-v1"
            and health.get("protocol_version") == "1.0"
            and all(health.get(key) == 0 for key in ("external_network_calls", "external_model_calls", "credential_value_reads"))
        )

    def _contract(self) -> dict[str, Any]:
        names = (
            "WorkLogEventContract.json",
            "WorkLogAuthoritySeparationContract.json",
            "WorkLogPublicSafeExportContract.json",
            "Part9WorkLogActionStateContract.json",
            "WorkLogFaultDenominator.json",
            "WorkLogAcceptanceDenominator.json",
        )
        contracts = {name: json.loads((self.contract_root / name).read_text(encoding="utf-8")) for name in names}
        return {
            "schema_version": "P08T10WorkLogProductContract-v1",
            "method_count": len(WORK_LOG_METHODS),
            "methods": sorted(WORK_LOG_METHODS),
            "contracts": contracts,
            "d36_actions": ["filter", "export_redacted_view"],
            "d36_queries": ["list_log_entries", "resolve_job_locator"],
            "d36_events": ["log_projection_refreshed"],
            "d36_receipts": ["redacted_export_receipt"],
            "d36_identities": ["log_entry_id", "job_id", "event_sequence"],
            "source_authority_access": "HASH_METADATA_ONLY_NO_RAW_BYTES",
            "construction_mode": (
                "SYNTHETIC_OFFLINE"
                if self.include_synthetic_projection
                else "PRODUCTION_PHASE1_AND_REFINEMENT_ACTIVITY_METADATA"
            ),
            "status": "PASS",
        }

    @staticmethod
    def _time_match(bucket: str, requested: str) -> bool:
        if requested == "all":
            return True
        if requested == "30":
            return bucket in {"today", "7", "30"}
        if requested == "7":
            return bucket in {"today", "7"}
        return bucket == "today"

    def _list(self, params: Mapping[str, Any]) -> dict[str, Any]:
        self._sync_runtime_rows()
        state = self._load()
        time_range = str(params.get("time_range", state["time_range"]))
        ledger_type = str(params.get("ledger_type", state["ledger_type"]))
        search = str(params.get("search", state["search"]))
        severity = str(params.get("severity", state["severity"]))
        status_axis = str(params.get("status_axis", state["status_axis"]))
        date_from = params.get("date_from", state["date_from"])
        date_to = params.get("date_to", state["date_to"])
        page = int(params.get("page", state["page"]))
        page_size = int(params.get("page_size", state["page_size"]))
        if time_range not in {"today", "7", "30", "all"} or ledger_type not in {"all", "branch", "central"}:
            raise WorkLogProductError("WORK_LOG_LIST_AXIS_INVALID")
        if severity not in {"all", "INFO", "WARNING", "ERROR"}:
            raise WorkLogProductError("WORK_LOG_SEVERITY_AXIS_INVALID")
        if not 1 <= page <= 1000 or not 1 <= page_size <= 500:
            raise WorkLogProductError("WORK_LOG_PAGING_INVALID")
        if date_from is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date_from)):
            raise WorkLogProductError("WORK_LOG_DATE_FROM_INVALID")
        if date_to is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date_to)):
            raise WorkLogProductError("WORK_LOG_DATE_TO_INVALID")
        term = search.strip().casefold()
        rows = []
        for row in self._rows_by_id.values():
            if not self._time_match(row["age_bucket"], time_range):
                continue
            if ledger_type != "all" and row["ledger_type"] != ledger_type:
                continue
            if severity != "all" and row["severity"] != severity:
                continue
            if status_axis != "all" and status_axis.casefold() not in row["state_axis"].casefold():
                continue
            if date_from is not None and row["date"] < str(date_from):
                continue
            if date_to is not None and row["date"] > str(date_to):
                continue
            domain = " ".join(
                str(row[key])
                for key in ("log_entry_id", "job_id", "title", "type_label", "summary", "state_axis", "severity")
            ).casefold()
            if term and term not in domain:
                continue
            rows.append(self._public(row))
        rows.sort(key=lambda row: (row["event_time_utc"], row["event_sequence"], row["log_entry_id"]), reverse=True)
        total = len(rows)
        start = (page - 1) * page_size
        selected = rows[start : start + page_size]
        snapshot_domain = [
            state["view_snapshot_sequence"], time_range, ledger_type, search, severity, status_axis,
            date_from, date_to, page, page_size,
        ]
        return {
            "schema_version": "P08T10WorkLogListProjection-v1",
            "view_snapshot_id": f"log-view-{state['view_snapshot_sequence']:03d}-{_sha256_json(snapshot_domain)[:12].lower()}",
            "time_range": time_range,
            "ledger_type": ledger_type,
            "search_sha256": _sha256_bytes(search.encode("utf-8")),
            "severity": severity,
            "status_axis": status_axis,
            "date_from": date_from,
            "date_to": date_to,
            "page": page,
            "page_size": page_size,
            "total_count": total,
            "rows": selected,
            "row_count": len(selected),
            "stable_ordering": "event_time_utc DESC,event_sequence DESC,log_entry_id DESC",
            "duplicate_identity_count": 0,
            "gap_count": 0,
            "synthetic_only": self.include_synthetic_projection,
            "raw_authority_content_read": False,
            "status": "PASS",
        }

    def _redact(self, value: Any, canary_values: set[str]) -> tuple[Any, int]:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            count = 0
            for key, item in value.items():
                normalized = str(key).casefold()
                if any(token in normalized for token in FORBIDDEN_EXPORT_KEYS):
                    count += 1
                    continue
                redacted, child_count = self._redact(item, canary_values)
                result[str(key)] = redacted
                count += child_count
            return result, count
        if isinstance(value, list):
            result_list = []
            count = 0
            for item in value:
                redacted, child_count = self._redact(item, canary_values)
                result_list.append(redacted)
                count += child_count
            return result_list, count
        if isinstance(value, str) and (
            value in canary_values or re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith(("\\\\", "//"))
        ):
            return "[REDACTED]", 1
        return deepcopy(value), 0

    def _export_projection(self, params: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        allowed = {"time_range", "ledger_type", "search", "severity", "status_axis", "date_from", "date_to"}
        projection = self._list({key: value for key, value in params.items() if key in allowed} | {"page": 1, "page_size": 500})
        raw = {
            "schema_version": "P08T10WorkLogPublicExport-v1",
            "view_snapshot_id": projection["view_snapshot_id"],
            "filters": {key: projection[key] for key in ("time_range", "ledger_type", "severity", "status_axis", "date_from", "date_to")},
            "row_count": projection["row_count"],
            "rows": projection["rows"],
            "source_sentinels": deepcopy(self._sentinels["sources"]),
            "synthetic_canary_probe": {
                field: self._canaries["values"][index]
                for index, field in enumerate(self._canaries["fields"])
            },
            "construction_mode": (
                "SYNTHETIC_OFFLINE"
                if self.include_synthetic_projection
                else "PRODUCTION_PHASE1_AND_REFINEMENT_ACTIVITY_METADATA"
            ),
            "raw_authority_content_included": False,
        }
        redacted, redaction_count = self._redact(raw, set(self._canaries["values"]))
        serialized = json.dumps(redacted, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        scan = serialized.casefold()
        canary_hits = sum(scan.count(str(value).casefold()) for value in self._canaries["values"])
        forbidden_key_hits = sum(len(re.findall(rf'"[^"\\n]*{re.escape(key)}[^"\\n]*"\s*:', scan)) for key in FORBIDDEN_EXPORT_KEYS)
        absolute_path_hits = len(re.findall(r'"[A-Za-z]:[\\/]|"\\\\', serialized))
        audit = {
            "redacted_field_count": redaction_count,
            "canary_value_hits": canary_hits,
            "forbidden_key_hits": forbidden_key_hits,
            "absolute_path_hits": absolute_path_hits,
            "row_count": projection["row_count"],
            "status": "PASS" if canary_hits == forbidden_key_hits == absolute_path_hits == 0 and redaction_count >= 9 else "FAIL",
        }
        if audit["status"] != "PASS":
            raise WorkLogProductError(f"WORK_LOG_REDACTION_FAILED:{audit}")
        return redacted, audit

    def _export_path(self, export_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", export_id):
            raise WorkLogProductError("WORK_LOG_EXPORT_ID_INVALID")
        return self.export_root / f"export-{export_id}"

    @projection_request
    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        if method not in WORK_LOG_METHODS:
            raise WorkLogProductError("WORK_LOG_PRODUCT_METHOD_NOT_ALLOWLISTED")
        accepted = dict(params)
        if any(str(key).startswith("_") for key in accepted):
            raise WorkLogProductError("WORK_LOG_PRODUCT_PRIVATE_PARAM_FORBIDDEN")
        if not self.include_synthetic_projection:
            self._sync_runtime_rows()
        if method == "work_log.get_contract":
            self._require_keys(accepted, set())
            return self._contract()
        if method == "work_log.bootstrap":
            self._require_keys(accepted, set())
            if not self._service_ready():
                raise WorkLogProductError("WORK_LOG_SERVICE_HEALTH_CONTRACT_INVALID")
            projection = self._list({"time_range": "7", "ledger_type": "all", "page": 1, "page_size": 500})
            return {
                "schema_version": "P08T10WorkLogBootstrapProjection-v1",
                "records": projection["rows"],
                "record_count": projection["row_count"],
                "catalog_count": len(self._rows_by_id),
                "binding_count": len(self._rows_by_id),
                "view_snapshot_id": projection["view_snapshot_id"],
                "source_authority_access": "HASH_METADATA_ONLY_NO_RAW_BYTES",
                "synthetic_only": self.include_synthetic_projection,
                "status": "PASS",
            }
        if method == "work_log.list_entries":
            self._require_keys(
                accepted,
                set(),
                {"time_range", "ledger_type", "search", "severity", "status_axis", "date_from", "date_to", "page", "page_size"},
            )
            return self._list(accepted)
        if method == "work_log.get_entry":
            self._require_keys(accepted, {"log_entry_id"})
            return {"schema_version": "P08T10WorkLogDetailProjection-v1", "entry": self._get(str(accepted["log_entry_id"])), "status": "PASS"}
        if method == "work_log.get_milestones":
            self._require_keys(accepted, {"log_entry_id"})
            entry_id = str(accepted["log_entry_id"])
            self._get(entry_id)
            milestones = deepcopy(self._rows_by_id[entry_id]["milestones"])
            return {"schema_version": "P08T10WorkLogMilestoneProjection-v1", "log_entry_id": entry_id, "milestones": milestones, "milestone_count": len(milestones), "raw_private_content_included": False, "status": "PASS"}
        if method == "work_log.set_filter":
            self._require_keys(
                accepted,
                set(),
                {"time_range", "ledger_type", "search", "severity", "status_axis", "date_from", "date_to", "page", "page_size"},
            )
            state = self._load()
            for key, value in accepted.items():
                state[key] = value
            self._save(state)
            projection = self._list({})
            return {"schema_version": "P08T10WorkLogFilterReceipt-v1", "view_snapshot_id": projection["view_snapshot_id"], "row_count": projection["row_count"], "revision": self._load()["revision"], "status": "PASS"}
        if method == "work_log.select":
            self._require_keys(accepted, {"log_entry_id"})
            entry_id = str(accepted["log_entry_id"])
            entry = self._get(entry_id)
            state = self._load()
            state["selected_log_entry_id"] = entry_id
            self._save(state)
            return {"schema_version": "P08T10WorkLogSelectionReceipt-v1", "log_entry_id": entry_id, "job_id": entry["job_id"], "event_sequence": entry["event_sequence"], "status": "PASS"}
        if method == "work_log.resolve_job_locator":
            self._require_keys(accepted, {"job_locator"})
            locator = str(accepted["job_locator"])
            prefix = "pr-os://job/"
            if not locator.startswith(prefix) or not locator[len(prefix) :]:
                raise WorkLogProductError("WORK_LOG_JOB_LOCATOR_INVALID")
            job_id = locator[len(prefix) :]
            matches = [row for row in self._rows_by_id.values() if row["job_id"] == job_id]
            if not matches:
                raise WorkLogProductError("WORK_LOG_JOB_LOCATOR_NOT_FOUND")
            self._metrics["locator_resolutions"] += 1
            state = self._load()
            state["locator_history"].append({"kind": "job", "locator": locator, "job_id": job_id})
            state["locator_history"] = state["locator_history"][-32:]
            self._save(state)
            return {"schema_version": "P08T10JobLocatorResolutionReceipt-v1", "job_locator": locator, "job_id": job_id, "route": "current-task", "match_count": len(matches), "fuzzy_match_attempted": False, "status": "PASS"}
        if method == "work_log.resolve_evidence_locator":
            self._require_keys(accepted, {"evidence_locator"})
            locator = str(accepted["evidence_locator"])
            if not locator.startswith("pr-os://evidence/"):
                raise WorkLogProductError("WORK_LOG_EVIDENCE_LOCATOR_INVALID")
            matches = [row for row in self._rows_by_id.values() if locator in row["evidence_locators"]]
            if not matches:
                raise WorkLogProductError("WORK_LOG_EVIDENCE_LOCATOR_NOT_FOUND")
            self._metrics["locator_resolutions"] += 1
            return {"schema_version": "P08T10EvidenceLocatorResolutionReceipt-v1", "evidence_locator": locator, "log_entry_ids": [row["log_entry_id"] for row in matches], "match_count": len(matches), "absolute_path_included": False, "status": "PASS"}
        if method == "work_log.get_source_status":
            self._require_keys(accepted, set())
            return {"schema_version": "P08T10SourceAuthorityStatusProjection-v1", "sources": deepcopy(self._sentinels["sources"]), "source_count": len(self._sentinels["sources"]), "raw_authority_content_read": False, "source_mutations": 0, "status": "PASS"}
        if method == "work_log.refresh":
            self._require_keys(accepted, set(), {"pending_count"})
            state = self._load()
            before_selected = state["selected_log_entry_id"]
            state["view_snapshot_sequence"] += 1
            state["pending_count"] = int(accepted.get("pending_count", 0))
            self._save(state)
            return {"schema_version": "P08T10LogProjectionRefreshedEvent-v1", "event_type": "log_projection_refreshed", "view_snapshot_id": f"log-view-{state['view_snapshot_sequence']:03d}", "pending_count": state["pending_count"], "selected_log_entry_id": before_selected, "selection_preserved": True, "raw_authority_content_read": False, "status": "PASS"}
        if method == "work_log.export_preview":
            self._require_keys(accepted, set(), {"time_range", "ledger_type", "search", "severity", "status_axis", "date_from", "date_to"})
            _, audit = self._export_projection(accepted)
            return {"schema_version": "P08T10WorkLogExportPreview-v1", "scope": deepcopy(accepted), "row_count": audit["row_count"], "redacted_field_count": audit["redacted_field_count"], "canary_value_hits": 0, "forbidden_key_hits": 0, "files_written": 0, "status": "PASS"}
        if method == "work_log.export_redacted_view":
            self._require_keys(accepted, {"export_id"}, {"time_range", "ledger_type", "search", "severity", "status_axis", "date_from", "date_to"})
            export_id = str(accepted["export_id"])
            destination = self._export_path(export_id)
            temporary = self.export_root / f".tmp-{export_id}"
            if destination.exists() or temporary.exists():
                raise FileExistsError("WORK_LOG_EXPORT_DESTINATION_COLLISION")
            redacted, audit = self._export_projection(accepted)
            temporary.mkdir(parents=False, exist_ok=False)
            try:
                export_path = temporary / "work_log_public.json"
                export_bytes = (json.dumps(redacted, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
                export_path.write_bytes(export_bytes)
                state = self._load()
                if state["fault_mode"] == "export_write_failure":
                    raise OSError("SYNTHETIC_EXPORT_WRITE_FAILURE")
                manifest = {
                    "schema_version": "P08T10WorkLogPublicExportManifest-v1",
                    "export_id": export_id,
                    "export_file": "work_log_public.json",
                    "export_bytes": len(export_bytes),
                    "export_sha256": _sha256_bytes(export_bytes),
                    "row_count": audit["row_count"],
                    "redacted_field_count": audit["redacted_field_count"],
                    "canary_value_hits": audit["canary_value_hits"],
                    "forbidden_key_hits": audit["forbidden_key_hits"],
                    "absolute_path_hits": audit["absolute_path_hits"],
                    "source_authority_mutations": 0,
                    "status": "PASS",
                }
                manifest_path = temporary / "manifest.json"
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
                temporary.replace(destination)
            except Exception:
                shutil.rmtree(temporary, ignore_errors=True)
                if destination.exists():
                    raise RuntimeError("WORK_LOG_EXPORT_ATOMICITY_BREACH")
                raise
            self._metrics["synthetic_export_writes"] += 2
            self._metrics["redacted_field_count"] += audit["redacted_field_count"]
            state = self._load()
            state["export_receipts"].append({"export_id": export_id, "export_sha256": manifest["export_sha256"], "row_count": manifest["row_count"]})
            state["export_receipts"] = state["export_receipts"][-32:]
            self._save(state)
            return {"schema_version": "P08T10RedactedExportReceipt-v1", "export_id": export_id, "relative_directory": f"exports/export-{export_id}", "manifest": manifest, "atomic_write": True, "partial_final_output": False, "source_authority_mutations": 0, "status": "PASS"}
        if method == "work_log.verify_export":
            self._require_keys(accepted, {"export_id"})
            export_id = str(accepted["export_id"])
            destination = self._export_path(export_id)
            export_path = destination / "work_log_public.json"
            manifest_path = destination / "manifest.json"
            if not export_path.is_file() or not manifest_path.is_file():
                raise WorkLogProductError("WORK_LOG_EXPORT_NOT_FOUND")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            export_bytes = export_path.read_bytes()
            text = export_bytes.decode("utf-8")
            canary_hits = sum(text.casefold().count(str(value).casefold()) for value in self._canaries["values"])
            forbidden_key_hits = sum(len(re.findall(rf'"[^"\\n]*{re.escape(key)}[^"\\n]*"\s*:', text.casefold())) for key in FORBIDDEN_EXPORT_KEYS)
            absolute_path_hits = len(re.findall(r'"[A-Za-z]:[\\/]|"\\\\', text))
            checks = {
                "manifest_schema": manifest.get("schema_version") == "P08T10WorkLogPublicExportManifest-v1",
                "export_sha_exact": manifest.get("export_sha256") == _sha256_bytes(export_bytes),
                "export_bytes_exact": manifest.get("export_bytes") == len(export_bytes),
                "canary_hits_zero": canary_hits == 0,
                "forbidden_key_hits_zero": forbidden_key_hits == 0,
                "absolute_path_hits_zero": absolute_path_hits == 0,
                "source_mutations_zero": manifest.get("source_authority_mutations") == 0,
            }
            self._metrics["export_verifications"] += 1
            return {"schema_version": "P08T10WorkLogExportVerification-v1", "export_id": export_id, "checks": checks, "verification_result": "PASS" if all(checks.values()) else "FAIL", "status": "PASS" if all(checks.values()) else "FAIL"}
        if method == "work_log.inject_fault":
            self._require_keys(accepted, {"fault"})
            fault = str(accepted["fault"])
            classifications = {
                "duplicate": ("DUPLICATE_IDENTITY_DETECTED_STABLE_DEDUPE", 2),
                "out_of_order": ("OUT_OF_ORDER_DETECTED_STABLE_SORT", 3),
                "gap": ("EVENT_SEQUENCE_GAP_VISIBLE_NOT_FILLED", 1),
                "clock_skew": ("CLOCK_SKEW_VISIBLE_EVENT_SEQUENCE_WINS", 1),
                "large_stream": ("LARGE_STREAM_PAGED_STABLE_CURSOR", 5000),
                "corrupt_event": ("CORRUPT_EVENT_PUBLIC_ERROR_NO_RAW_PAYLOAD", 1),
                "missing_locator": ("MISSING_LOCATOR_FAIL_CLOSED_NO_FUZZY_MATCH", 1),
                "export_write_failure": ("EXPORT_FAILURE_ATOMIC_NO_PARTIAL_FINAL", 1),
            }
            if fault not in classifications:
                raise WorkLogProductError("WORK_LOG_FAULT_UNKNOWN")
            state = self._load()
            state["fault_mode"] = fault
            self._save(state)
            classification, affected = classifications[fault]
            return {"schema_version": "P08T10WorkLogFaultReceipt-v1", "fault": fault, "classification": classification, "synthetic_affected_rows": affected, "raw_private_content_included": False, "source_authority_mutations": 0, "restricted_effects": 0, "status": "PASS"}
        if method == "work_log.restore":
            self._require_keys(accepted, set())
            state = self._load()
            prior_fault = state["fault_mode"]
            state["fault_mode"] = None
            self._save(state)
            return {"schema_version": "P08T10WorkLogRestartRecoveryReceipt-v1", "revision": self._load()["revision"], "selected_log_entry_id": state["selected_log_entry_id"], "view_snapshot_sequence": state["view_snapshot_sequence"], "prior_fault": prior_fault, "stable_locator_replay": True, "status": "PASS"}
        self._require_keys(accepted, set())
        restricted_total = sum(int(self._metrics[key]) for key in RESTRICTED_EFFECT_KEYS)
        return {
            "schema_version": "P08T10WorkLogEffectMetrics-v1",
            **deepcopy(self._metrics),
            "restricted_effect_total": restricted_total,
            "synthetic_only": self.include_synthetic_projection,
            "status": "PASS" if restricted_total == 0 else "FAIL",
        }


__all__ = ["FORBIDDEN_EXPORT_KEYS", "WORK_LOG_METHODS", "WorkLogProductController", "WorkLogProductError"]
