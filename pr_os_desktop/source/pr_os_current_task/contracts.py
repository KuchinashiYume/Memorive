from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping


VIEW_STATES = (
    "LOADING",
    "EMPTY",
    "QUEUED",
    "RUNNING",
    "PAUSED",
    "WAITING_HUMAN",
    "CANCELING",
    "CANCELED",
    "FAILED",
    "RECOVERING",
    "COMPLETED",
)

CONTROL_STATE_TO_VIEW = {
    "CREATED": "QUEUED",
    "PREFLIGHT": "QUEUED",
    "QUEUED": "QUEUED",
    "BLOCKED_BEFORE_START": "FAILED",
    "RUNNING": "RUNNING",
    "PAUSE_REQUESTED": "RUNNING",
    "PAUSED": "PAUSED",
    "RESUME_REQUESTED": "RECOVERING",
    "CANCEL_REQUESTED": "CANCELING",
    "CANCELLED": "CANCELED",
    "RECOVERY_REQUIRED": "RECOVERING",
    "RECOVERING": "RECOVERING",
    "FAILED": "FAILED",
    "SUCCEEDED": "COMPLETED",
}

TERMINAL_CONTROL_STATES = frozenset({"CANCELLED", "FAILED", "SUCCEEDED"})
KNOWN_CONTROL_STATES = frozenset(CONTROL_STATE_TO_VIEW)
ACTION_NAMES = ("pause", "cancel", "resume", "retry", "respond_to_action")
ZERO_EFFECT_KEYS = (
    "external_network_calls",
    "external_model_calls",
    "provider_calls",
    "production_job_reads",
    "production_writes",
    "production_ingestion",
    "route_or_profile_enablements",
    "real_credential_reads",
    "real_credential_writes",
    "permanent_deletes",
    "cost_usd",
    "cost_cny",
)

STATE_PRESENTATION = {
    "QUEUED": {"label": "排队中", "tone": "neutral", "icon": "clock"},
    "RUNNING": {"label": "正在执行", "tone": "blue", "icon": "play"},
    "PAUSED": {"label": "已暂停", "tone": "neutral", "icon": "pause"},
    "WAITING_HUMAN": {"label": "等待批准", "tone": "yellow", "icon": "person"},
    "CANCELING": {"label": "正在取消", "tone": "neutral", "icon": "stop"},
    "CANCELED": {"label": "已取消", "tone": "neutral", "icon": "cancel"},
    "FAILED": {"label": "执行异常", "tone": "red", "icon": "error"},
    "RECOVERING": {"label": "正在恢复", "tone": "blue", "icon": "recover"},
    "COMPLETED": {"label": "已完成", "tone": "green", "icon": "check"},
}

LEGACY_FIXED_NODES = (
    {"node_id": "01_DOCUMENT_INGEST", "number": "01", "name": "文献导入", "purpose": "建立可追溯输入", "model": "系统解析", "optional": False, "model_change": True, "locked": False},
    {"node_id": "02_CARD_DISTILL", "number": "02", "name": "卡片提炼", "purpose": "形成结构化卡片", "model": "任务模型", "optional": False, "model_change": True, "locked": False},
    {"node_id": "03_CARD_CROSS_CHECK", "number": "03", "name": "卡片交叉检查", "purpose": "复核卡片一致性", "model": "复核模型", "optional": True, "model_change": True, "locked": False},
    {"node_id": "04_CARD_ADMISSION", "number": "04", "name": "卡片准入", "purpose": "执行确定性准入", "model": "确定性规则", "optional": False, "model_change": False, "locked": True},
    {"node_id": "05_CONTEXT_PACK", "number": "05", "name": "上下文组装", "purpose": "冻结分析上下文", "model": "系统编排", "optional": False, "model_change": True, "locked": False},
    {"node_id": "06_ANALYSIS", "number": "06", "name": "分析", "purpose": "生成分析结果", "model": "任务模型", "optional": False, "model_change": True, "locked": False},
    {"node_id": "07_JUDGMENT_CROSS_CHECK", "number": "07", "name": "判断交叉检查", "purpose": "复核判断一致性", "model": "复核模型", "optional": True, "model_change": True, "locked": False},
    {"node_id": "08_HUMAN_FINAL", "number": "08", "name": "人工终点", "purpose": "等待人工最终决定", "model": "人工", "optional": False, "model_change": False, "locked": True},
)
FIXED_NODES = (
    {"node_id": "01_DOCUMENT_INGEST", "number": "01", "name": "文档处理与内容理解", "purpose": "转换、清洗、切块、内容理解与复杂版面/OCR", "model": "系统解析", "optional": False, "model_change": True, "locked": False},
    {"node_id": "02_CHUNK_EMBEDDING", "number": "02", "name": "Chunk Embedding", "purpose": "生成 chunk 向量嵌入", "model": "尚未选择向量嵌入模型", "optional": False, "model_change": True, "locked": False},
    {"node_id": "03_CARD_DISTILL", "number": "03", "name": "卡片提炼", "purpose": "形成结构化卡片", "model": "任务模型", "optional": False, "model_change": True, "locked": False},
    {"node_id": "04_CARD_CROSS_CHECK", "number": "04", "name": "卡片交叉检查", "purpose": "复核卡片一致性", "model": "复核模型", "optional": True, "model_change": True, "locked": False},
    {"node_id": "05_CARD_ADMISSION", "number": "05", "name": "卡片准入", "purpose": "执行确定性准入", "model": "确定性规则", "optional": False, "model_change": False, "locked": True},
    {"node_id": "06_CONTEXT_PACK", "number": "06", "name": "上下文组装", "purpose": "以 02 的同一嵌入空间检索并冻结分析上下文", "model": "同步 02", "optional": False, "model_change": False, "locked": True, "profile_source_node_id": "02_CHUNK_EMBEDDING"},
    {"node_id": "07_ANALYSIS", "number": "07", "name": "分析", "purpose": "生成分析结果", "model": "任务模型", "optional": False, "model_change": True, "locked": False},
    {"node_id": "08_JUDGMENT_CROSS_CHECK", "number": "08", "name": "判断交叉检查", "purpose": "复核判断一致性", "model": "复核模型", "optional": True, "model_change": True, "locked": False},
    {"node_id": "09_HUMAN_FINAL", "number": "09", "name": "人工终点", "purpose": "等待人工最终决定", "model": "人工", "optional": False, "model_change": False, "locked": True},
)
FIXED_NODE_IDS = tuple(row["node_id"] for row in FIXED_NODES)
LEGACY_FIXED_NODE_IDS = tuple(row["node_id"] for row in LEGACY_FIXED_NODES)


class ContractError(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractError("CURRENT_TASK_JSON_INVALID") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def immutable(value: Any) -> Any:
    return json.loads(canonical_json_bytes(value).decode("utf-8"))


def require_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 192:
        raise ContractError(f"CURRENT_TASK_{field.upper()}_INVALID")
    if any(character.isspace() for character in value):
        raise ContractError(f"CURRENT_TASK_{field.upper()}_INVALID")
    return value


def view_state(control_state: str, *, pending_action: bool = False) -> str:
    if control_state not in CONTROL_STATE_TO_VIEW:
        raise ContractError(f"CURRENT_TASK_UNKNOWN_CONTROL_STATE:{control_state}")
    projected = CONTROL_STATE_TO_VIEW[control_state]
    if pending_action and control_state not in TERMINAL_CONTROL_STATES:
        return "WAITING_HUMAN"
    return projected


def state_presentation(control_state: str, *, pending_action: bool = False) -> dict[str, Any]:
    projected = view_state(control_state, pending_action=pending_action)
    value = deepcopy(STATE_PRESENTATION[projected])
    value.update({"view_state": projected, "control_state": control_state, "accessible_name": value["label"]})
    return value


def exact_axes(job: Mapping[str, Any]) -> dict[str, str]:
    control = job.get("control_state", job.get("job_control_state"))
    if control not in KNOWN_CONTROL_STATES:
        raise ContractError(f"CURRENT_TASK_UNKNOWN_CONTROL_STATE:{control}")
    return {
        "job_control_state": str(control),
        "verification_result": str(job.get("verification_result", "NOT_ASSESSED")),
        "acceptance_verdict": str(job.get("acceptance_verdict", "NOT_ASSESSED")),
        "technical_handoff_status": str(job.get("technical_handoff_status", "NOT_READY")),
        "formalization_status": str(job.get("formalization_status", "NOT_AUTHORIZED")),
        "deployment_status": str(job.get("deployment_status", "disabled")),
    }


def default_nodes(
    workflow_definition: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    configured = (
        workflow_definition.get("nodes", [])
        if isinstance(workflow_definition, Mapping)
        else []
    )
    configured_ids = {
        row.get("node_id")
        for row in configured
        if isinstance(row, Mapping)
    }
    topology = (
        FIXED_NODES
        if "02_CHUNK_EMBEDDING" in configured_ids or not configured_ids
        else LEGACY_FIXED_NODES
    )
    return deepcopy(list(topology))


def assert_zero_effects(effects: Mapping[str, Any]) -> None:
    nonzero = {key: effects.get(key) for key in ZERO_EFFECT_KEYS if effects.get(key, 0) != 0}
    if nonzero:
        raise ContractError("EXTERNAL_EFFECT_FORBIDDEN:" + ",".join(sorted(nonzero)))
