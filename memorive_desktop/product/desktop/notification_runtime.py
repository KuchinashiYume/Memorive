from __future__ import annotations

from datetime import datetime, time as clock_time
import re
from typing import Any, Mapping
from memorive_language.text import choose


NOTIFICATION_KINDS = frozenset({"COMPLETE", "ERROR", "APPROVAL", "WEEKLY","LITERATURE"})
_KIND_SWITCH = {
    "COMPLETE": "task_complete_notification",
    "ERROR": "task_error_notification",
    "APPROVAL": "approval_notification",
    "WEEKLY": "weekly_report_notification",
    "LITERATURE":"external_literature_notification",
}
_KIND_COPY = {
    "COMPLETE": ("Memorive 任务已完成", "打开 Memorive 查看完成回执。"),
    "ERROR": ("Memorive 任务需要注意", "打开 Memorive 查看错误详情。"),
    "APPROVAL": ("Memorive 等待确认", "打开 Memorive 查看待确认事项。"),
    "WEEKLY": ("Memorive 周报已生成", "打开 Memorive 查看周报。"),
    "LITERATURE":("Memorive 外部文献已获取","打开 Memorive 查看文献推荐。"),
}
_LOCATOR = re.compile(r"^memorive://[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")


def _parse_clock(value: Any, label: str) -> clock_time:
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[01]\d|2[0-3]):[0-5]\d", value
    ):
        raise ValueError(f"{label}_INVALID")
    hours, minutes = (int(part) for part in value.split(":"))
    return clock_time(hours, minutes)


def in_quiet_hours(
    current: clock_time, start_value: str, end_value: str
) -> bool:
    start = _parse_clock(start_value, "DO_NOT_DISTURB_START")
    end = _parse_clock(end_value, "DO_NOT_DISTURB_END")
    # Equal endpoints explicitly disable the quiet interval. This gives the
    # settings test button and users a deterministic way to request no DND.
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def plan_windows_notification(
    preferences: Mapping[str, Any],
    *,
    kind: str,
    now: datetime | None = None,
    locator: str | None = None,
    source_message_id: str | None = None,
) -> dict[str, Any]:
    normalized = str(kind).upper()
    if normalized not in NOTIFICATION_KINDS:
        raise ValueError("NOTIFICATION_KIND_INVALID")
    if locator is not None and (
        not isinstance(locator, str) or not _LOCATOR.fullmatch(locator)
    ):
        raise ValueError("NOTIFICATION_LOCATOR_INVALID")
    if source_message_id is not None and (
        not isinstance(source_message_id, str) or not source_message_id.strip()
    ):
        raise ValueError("NOTIFICATION_MESSAGE_ID_INVALID")
    required = {
        "notifications_enabled",
        *[v for k,v in _KIND_SWITCH.items() if k!='LITERATURE'],
        "notification_sound",
        "do_not_disturb_start",
        "do_not_disturb_end",
        "notification_open_task",
    }
    missing = sorted(required - set(preferences))
    if missing:
        raise ValueError("NOTIFICATION_PREFERENCES_MISSING:" + ",".join(missing))
    switch_name = _KIND_SWITCH[normalized]
    enabled = bool(preferences["notifications_enabled"]) and bool(
        preferences.get(switch_name,False)
    )
    observed_now = now or datetime.now().astimezone()
    quiet = in_quiet_hours(
        observed_now.timetz().replace(tzinfo=None),
        str(preferences["do_not_disturb_start"]),
        str(preferences["do_not_disturb_end"]),
    )
    status = "DELIVER"
    reason_code = "NONE"
    if not enabled:
        status = "BLOCKED_DISABLED"
        reason_code = "GLOBAL_OR_KIND_SWITCH_DISABLED"
    elif quiet:
        status = "BLOCKED_QUIET"
        reason_code = "DO_NOT_DISTURB_ACTIVE"
    english = {
        'COMPLETE': ('Memorive task complete', 'Open Memorive to view the result.'),
        'ERROR': ('Memorive needs attention', 'Open Memorive for error details.'),
        'APPROVAL': ('Memorive awaits confirmation', 'Open Memorive to review the pending item.'),
        'WEEKLY': ('Memorive weekly report ready', 'Open Memorive to read the report.'),
        'LITERATURE': ('Memorive literature recommendations ready', 'Open Memorive to view the recommendations.'),
    }
    japanese = {
        'COMPLETE': ('Memorive のタスクが完了しました', 'Memorive で結果を確認してください。'),
        'ERROR': ('Memorive の確認が必要です', 'Memorive でエラーの詳細を確認してください。'),
        'APPROVAL': ('Memorive で確認をお待ちしています', 'Memorive で確認事項を開いてください。'),
        'WEEKLY': ('Memorive の週報ができました', 'Memorive で週報をご覧ください。'),
        'LITERATURE': ('Memorive の文献推薦ができました', 'Memorive で文献の推薦を確認してください。'),
    }
    title, body = choose(preferences.get('language'), _KIND_COPY[normalized], english[normalized], japanese[normalized])
    click_opens_task = bool(preferences["notification_open_task"] and locator)
    return {
        "schema_version": "DesktopWindowsNotificationPlan-v1",
        "kind": normalized,
        "preference_key": switch_name,
        "title": title,
        "body": body,
        "play_sound": bool(preferences["notification_sound"]),
        "click_opens_task": click_opens_task,
        "activation_locator": locator if click_opens_task else None,
        "source_message_id": source_message_id,
        "quiet_hours_active": quiet,
        "lockscreen_detail": "GENERIC_SUMMARY_ONLY",
        "private_body_included": False,
        "reason_code": reason_code,
        "status": status,
    }


__all__ = [
    "NOTIFICATION_KINDS",
    "in_quiet_hours",
    "plan_windows_notification",
]
