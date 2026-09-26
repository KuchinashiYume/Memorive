from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pr_os_desktop_service.locator import StableLocator

from .models import AssistantPresentation


_ERROR = frozenset({"ERROR", "FAILED", "FAIL", "BLOCKED", "CANCELLED", "ABORTED", "INVALIDATED"})
_WAITING = frozenset({"WAITING", "WAITING_USER", "AWAITING_ACTION", "PAUSED", "NEEDS_INPUT"})
_RUNNING = frozenset({"RUNNING", "IN_PROGRESS", "STARTED", "QUEUED"})
_SUCCESS = frozenset({"COMPLETED", "COMPLETE", "SUCCEEDED", "SUCCESS", "PASS"})


def _blocked(reason: str, *, reduced_motion: bool) -> AssistantPresentation:
    return AssistantPresentation(
        display_state="SUSPENDED",
        attention_state="ERROR",
        animation_state="STATIC",
        safe_summary="桌面助手已暂停",
        reason_code=reason,
        reduced_motion=reduced_motion,
    )


def _accepted_health(value: Mapping[str, Any] | None) -> bool:
    if not isinstance(value, Mapping) or value.get("schema_version") != "ApplicationServiceHealth-v1":
        return False
    for key in ("external_network_calls", "external_model_calls", "credential_value_reads"):
        observed = value.get(key, 0)
        if isinstance(observed, bool) or not isinstance(observed, int) or observed != 0:
            return False
    return True


def _normalized_state(job: Mapping[str, Any]) -> str:
    values = []
    for key in (
        "job_control_state",
        "control_state",
        "lifecycle_status",
        "state",
        "status",
        "verification_result",
    ):
        value = job.get(key)
        if isinstance(value, str) and value:
            values.append(value.upper())
    if not values:
        return "UNKNOWN"
    if any(value in _ERROR for value in values) and any(value in _SUCCESS for value in values):
        return "CONTRADICTORY"
    for domain in (_ERROR, _WAITING, _RUNNING, _SUCCESS):
        for value in values:
            if value in domain:
                return value
    return "UNKNOWN"


def _latest(jobs: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    if not jobs:
        return None

    def key(row: Mapping[str, Any]) -> tuple[int, str, str]:
        sequence = row.get("event_sequence", row.get("sequence", 0))
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            sequence = 0
        return (sequence, str(row.get("updated_at", "")), str(row.get("job_id", "")))

    return max(jobs, key=key)


def project_assistant_state(
    *,
    service_health: Mapping[str, Any] | None,
    jobs: Sequence[Mapping[str, Any]] | None,
    assistant_enabled: bool = True,
    visible: bool = True,
    menu_open: bool = False,
    dragging: bool = False,
    closing: bool = False,
    reduced_motion: bool = False,
) -> AssistantPresentation:
    flags = (assistant_enabled, visible, menu_open, dragging, closing, reduced_motion)
    if not all(isinstance(value, bool) for value in flags):
        return _blocked("INVALID_BOOLEAN_CONTROL", reduced_motion=bool(reduced_motion))
    if not assistant_enabled:
        return AssistantPresentation("DISABLED", "NONE", "STATIC", "桌面助手已关闭", "USER_DISABLED", reduced_motion=reduced_motion)
    if closing:
        return AssistantPresentation("CLOSING", "NONE", "STATIC", "桌面助手正在关闭", "CLOSING", reduced_motion=reduced_motion)
    if not visible:
        return AssistantPresentation("HIDDEN", "NONE", "STATIC", "桌面助手已隐藏", "USER_HIDDEN", reduced_motion=reduced_motion)
    if not _accepted_health(service_health):
        return _blocked("SERVICE_HEALTH_UNSAFE_OR_UNAVAILABLE", reduced_motion=reduced_motion)
    if jobs is None or isinstance(jobs, (str, bytes)) or not isinstance(jobs, Sequence):
        return _blocked("JOB_PROJECTION_INVALID", reduced_motion=reduced_motion)
    if any(not isinstance(row, Mapping) for row in jobs):
        return _blocked("JOB_ROW_INVALID", reduced_motion=reduced_motion)
    if dragging:
        return AssistantPresentation("DRAGGING", "NONE", "STATIC" if reduced_motion else "DRAG", "正在调整桌面助手位置", "DRAGGING", reduced_motion=reduced_motion)
    if menu_open:
        return AssistantPresentation("MENU_OPEN", "NONE", "STATIC", "桌面助手菜单已打开", "MENU_OPEN", reduced_motion=reduced_motion)

    current = _latest(jobs)
    if current is None:
        return AssistantPresentation("IDLE", "NONE", "STATIC", "桌面助手待命中", "NO_ACTIVE_JOB", reduced_motion=reduced_motion)
    state = _normalized_state(current)
    locator = current.get("locator")
    if locator is not None:
        try:
            StableLocator.parse(locator)
        except Exception:
            return _blocked("JOB_LOCATOR_INVALID", reduced_motion=reduced_motion)
    if state in {"CONTRADICTORY", "UNKNOWN"}:
        return _blocked("JOB_STATE_UNKNOWN_OR_CONTRADICTORY", reduced_motion=reduced_motion)
    source_kind = str(current.get("source_kind", "job"))
    animation = "STATIC" if reduced_motion else "WORKING"
    if state in _ERROR:
        if source_kind == "message":
            return AssistantPresentation(
                "NOTIFYING", "ERROR", "STATIC" if reduced_motion else "ERROR",
                "收到需要检查的消息", "MESSAGE_NEEDS_ATTENTION", locator, reduced_motion,
            )
        return AssistantPresentation("NOTIFYING", "ERROR", "STATIC" if reduced_motion else "ERROR", "任务需要检查", "JOB_TERMINAL_ERROR", locator, reduced_motion)
    if state in _WAITING:
        if source_kind == "message":
            return AssistantPresentation(
                "NOTIFYING", "WARNING", "STATIC" if reduced_motion else "ATTENTION",
                "收到等待处理的消息", "MESSAGE_WAITING_ACTION", locator, reduced_motion,
            )
        return AssistantPresentation("NOTIFYING", "WARNING", "STATIC" if reduced_motion else "ATTENTION", "任务等待处理", "JOB_WAITING_ACTION", locator, reduced_motion)
    if state in _SUCCESS:
        if source_kind == "message":
            return AssistantPresentation(
                "NOTIFYING", "SUCCESS", "STATIC" if reduced_motion else "SUCCESS",
                "收到完成消息", "MESSAGE_COMPLETED", locator, reduced_motion,
            )
        return AssistantPresentation("NOTIFYING", "SUCCESS", "STATIC" if reduced_motion else "SUCCESS", "任务已完成", "JOB_TERMINAL_SUCCESS", locator, reduced_motion)
    if source_kind == "message":
        return AssistantPresentation(
            "NOTIFYING", "INFO", "STATIC" if reduced_motion else "MESSAGE",
            "收到新消息", "MESSAGE_UPDATE", locator, reduced_motion,
        )
    return AssistantPresentation("IDLE", "INFO", animation, "任务正在进行", "JOB_RUNNING", locator, reduced_motion)


__all__ = ["project_assistant_state"]
