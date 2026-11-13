"""RESEARCH_ANALYSIS 成本红线「可查」读侧工具(Analysis 第2步)——只查不拦。

承 cost.py 边界:成本汇总 + ¥200 红线**强制**属 MODEL_GATEWAY 之上 / 将来(自动汇总 + 超线自动降档 = 债);
本模块只做「红线可查」:从 RUNTIME_LOG 记账(record_type='accounting')读回、按 task_type / quota_account
汇总 est_cost(CNY),人可查是否 ≤ ¥200。**不拦截、不降档**(那是将来)。
"""
from __future__ import annotations

import json

from runtime_log.config import get_log_path

RED_LINE_CNY = 200.0
