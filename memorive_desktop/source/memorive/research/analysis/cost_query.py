"""How much the model calls cost, read back from the runtime log, so a person can see whether the
spending limit (200 CNY) holds. It only reports; nothing is stopped or switched here.

It sums est_cost (CNY) of the cost records (record_type='accounting') by task_type and quota_account.
Enforcing the limit automatically is not built.
"""
from __future__ import annotations

import json

from memorive.platform.logs.config import get_log_path
from memorive.platform.logs import extract_module_logs

RED_LINE_CNY = 200.0


def cost_summary(*, log_path=None, month: str | None = None) -> dict:
    """The sum of the gateway's cost records (est_cost, CNY) by task_type and quota_account; month
    (a YYYY-MM prefix of ts) limits it to one month.

    Returns {total_cny, by_task_type, by_quota_account, red_line_cny, within_red_line, record_count}.
    Reports only. The limit covers research analysis and card review together."""
    lp = log_path or get_log_path()
    total = 0.0
    by_task: dict = {}
    by_acct: dict = {}
    n = 0
    for line in extract_module_logs("MODEL_GATEWAY", log_path=lp):
        try:
            r = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if r.get("record_type") != "accounting":
            continue
        if month and not str(r.get("ts", "")).startswith(month):
            continue
        n += 1
        data = r.get("data") or {}
        est = data.get("est_cost")
        val = est.get("value") if isinstance(est, dict) else None
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        total += float(val)
        tt = data.get("task_type")
        acct = data.get("quota_account")
        by_task[tt] = by_task.get(tt, 0.0) + float(val)
        by_acct[acct] = by_acct.get(acct, 0.0) + float(val)
    return {
        "total_cny": round(total, 6),
        "by_task_type": {k: round(v, 6) for k, v in by_task.items()},
        "by_quota_account": {k: round(v, 6) for k, v in by_acct.items()},
        "red_line_cny": RED_LINE_CNY,
        "within_red_line": total <= RED_LINE_CNY,
        "record_count": n,
    }
