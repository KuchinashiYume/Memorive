"""RESEARCH_ANALYSIS 成本红线「可查」读侧工具(Analysis 第2步)——只查不拦。

承 cost.py 边界:成本汇总 + ¥200 红线**强制**属 MODEL_GATEWAY 之上 / 将来(自动汇总 + 超线自动降档 = 债);
本模块只做「红线可查」:从 RUNTIME_LOG 记账(record_type='accounting')读回、按 task_type / quota_account
汇总 est_cost(CNY),人可查是否 ≤ ¥200。**不拦截、不降档**(那是将来)。
"""
from __future__ import annotations

import json

from runtime_log import extract_module_logs
from runtime_log.config import get_log_path

RED_LINE_CNY = 200.0


def cost_summary(*, log_path=None, month: str | None = None) -> dict:
    """汇总 MODEL_GATEWAY 记账的 est_cost(CNY):按 task_type + quota_account;可选按月(YYYY-MM 前缀)过滤 ts。

    返回 {total_cny, by_task_type, by_quota_account, red_line_cny, within_red_line, record_count}。
    **只查不拦**(承 cost.py:「汇总 + ¥200 红线强制在 MODEL_GATEWAY 之上 / 将来」);红线为 RESEARCH_ANALYSIS 分析 + EVIDENCE_REVIEW 校核叠加口径。"""
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
