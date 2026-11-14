"""ReviewDecision: the verdict an evidence review hands to knowledge admission, with the summary of
the rules it hit and the review report id."""
from __future__ import annotations

from dataclasses import dataclass

from .machine import Verdict


@dataclass(frozen=True)
class ReviewDecision:
    """EVIDENCE_REVIEW 校核判定源的富返回(Verification 第 2 步)。
    · verdict        —— PASS / FAIL,KNOWLEDGE_ADMISSION 据此转移
    · reason_summary —— 命中 R 条摘要(如 "R1,R9"),拼进 KNOWLEDGE_ADMISSION_log 依据(reason)
    · report_id      —— EVIDENCE_REVIEW 校核报告 id;KNOWLEDGE_ADMISSION_log 只留 report=<id> 链过去,**完整 Problem[] 在 EVIDENCE_REVIEW 报告 / RUNTIME_LOG、不塞 KNOWLEDGE_ADMISSION_log**。"""
    verdict: Verdict
    reason_summary: str | None = None
    report_id: str | None = None


