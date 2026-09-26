"""Frozen report-window and section policy."""

from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .canonical import parse_datetime
from .errors import WindowContractError

REPORT_TIMEZONE = "Asia/Shanghai"
try:
    ZONE = ZoneInfo(REPORT_TIMEZONE)
    TIMEZONE_IMPLEMENTATION = "stdlib-zoneinfo"
except ZoneInfoNotFoundError:
    # Windows Python installations may not ship an IANA database.  RESEARCH-REPORTS
    # only admits post-1991 research-operation timestamps, for which
    # Asia/Shanghai is fixed UTC+08:00.  The fallback is explicit and frozen.
    ZONE = timezone(timedelta(hours=8), REPORT_TIMEZONE)
    TIMEZONE_IMPLEMENTATION = "fixed-utc-plus-08-post-1991"

SECTION_TITLES = {
    "daily": ("新增", "修改", "待审", "失败"),
    "weekly": ("研究进展", "明确标记的重要变化", "未验证项", "明确来源的潜在张力", "风险与待办"),
    "monthly": ("知识库结构变化", "明确记录的研究方向变化", "长期待办", "风险与未评估维度"),
}


def _next_month(value: datetime) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    return value.replace(year=year, month=month, day=1)


def validate_window(kind: str, start_text: str, end_text: str, cutoff_text: str):
    if kind not in SECTION_TITLES:
        raise WindowContractError(f"unsupported digest kind: {kind}")
    try:
        start = parse_datetime(start_text, field_name="window_start").astimezone(ZONE)
        end = parse_datetime(end_text, field_name="window_end").astimezone(ZONE)
        cutoff = parse_datetime(cutoff_text, field_name="cutoff_at").astimezone(ZONE)
    except ValueError as exc:
        raise WindowContractError(str(exc)) from exc
    if min(start.year, end.year, cutoff.year) < 1992:
        raise WindowContractError("DataContracts v1 admits Asia/Shanghai windows from 1992 onward")
    for name, value in (("window_start", start), ("window_end", end), ("cutoff_at", cutoff)):
        if value.utcoffset() != timedelta(hours=8):
            raise WindowContractError(f"{name} must resolve to Asia/Shanghai UTC+08:00")
    if start.hour or start.minute or start.second or start.microsecond:
        raise WindowContractError("window_start must be local midnight")
    if end.hour or end.minute or end.second or end.microsecond:
        raise WindowContractError("window_end must be local midnight")
    if kind == "daily" and end != start + timedelta(days=1):
        raise WindowContractError("daily window must be exactly one local day")
    if kind == "weekly" and (start.weekday() != 0 or end != start + timedelta(days=7)):
        raise WindowContractError("weekly window must be Monday through next Monday")
    if kind == "monthly" and (start.day != 1 or end != _next_month(start)):
        raise WindowContractError("monthly window must be calendar-month aligned")
    if cutoff < end:
        raise WindowContractError("cutoff_at must not precede window_end")
    return start, end, cutoff


def original_window_ref(kind: str, occurred_at: datetime) -> str:
    local = occurred_at.astimezone(ZONE)
    if kind == "daily":
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif kind == "weekly":
        start = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=local.weekday())
        end = start + timedelta(days=7)
    else:
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = _next_month(start)
    return f"{kind}:{start.isoformat()}..{end.isoformat()}"
