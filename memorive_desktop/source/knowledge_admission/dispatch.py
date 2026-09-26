"""KNOWLEDGE_ADMISSION 自动分配器(dispatch)—— 让 KNOWLEDGE_ADMISSION 成为"自动分配器"而非"人工点通过"(Intake 第 3 步)。

auto_review(card, decision_source):对一张**送审的 pending 卡**自动走判定 → 据结论自动转移(active/quarantined)、
不需逐张手动点;判定"环节失败"(判定源 raise DecisionError)自动重跑当前环节并在 RUNTIME_LOG 留痕;同一环节连错
达阈值(默认 2)升级人工(返回 escalated、卡留 pending、不写 KNOWLEDGE_ADMISSION_log)。

边界:判定逻辑**不进 KNOWLEDGE_ADMISSION**——auto_review 只"重新调用 decision_source",KNOWLEDGE_ADMISSION 一个模型都不调(判定源后端若调模型,
那是未来 EVIDENCE_REVIEW 经 MODEL_GATEWAY 的事;本模块 import 无任何 SDK / model_gateway)。KNOWLEDGE_ADMISSION_log 只记成功的 review_status 转移;重跑/升级过程记 RUNTIME_LOG。
连错计数是**通用能力**,Verification 的 EVIDENCE_REVIEW 自动校核失败可复用同一套(承设计文档 §EVIDENCE_REVIEW 与 KNOWLEDGE_ADMISSION 联动)。
批量扫 pending 卡逐张送审属编排,本步**留挂接位、不做**。

decision_source 契约(结构化说明、非强制类型):Callable[[Path], Verdict | ReviewDecision]
  · return Verdict.PASS / FAIL —— 判定结论(通过 / 未通过),KNOWLEDGE_ADMISSION 据此转移(FAIL 是正常结论 → quarantined、非重跑)
  · return ReviewDecision      —— 向后兼容富返回(Verification EVIDENCE_REVIEW:verdict + 命中 R 条摘要 + 报告 id);KNOWLEDGE_ADMISSION 据 verdict 转移、
                                  把 summary/report 拼进 KNOWLEDGE_ADMISSION_log 依据(reason)。**旧只返回 Verdict 的判定源不受影响。**
  · raise DecisionError        —— 环节失败(瞬时 / 可重跑),KNOWLEDGE_ADMISSION 重跑当前环节
  · return 非 Verdict / 非 ReviewDecision —— 违约(bug),KNOWLEDGE_ADMISSION 抛 DecisionContractError 立即暴露(不重跑、不隔离、不转移)
  · raise 其他异常             —— 疑 bug,KNOWLEDGE_ADMISSION 不吞、上抛(防真 bug 伪装成瞬时错)

auto_review 的 trigger 参(默认 AUTO_RULE)= 本次转移触发源;EVIDENCE_REVIEW 驱动传 Trigger.EVIDENCE_REVIEW_VERDICT(承 Verification 决策 B、已激活预留)。
KNOWLEDGE_ADMISSION 三态 / 转移守卫逻辑 / KNOWLEDGE_ADMISSION_log 字段格式一律不变(仅激活预留触发源 + 加向后兼容 trigger 参 + 认 ReviewDecision)。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from runtime_log import log_reason

from .card_io import read_status
from .errors import DecisionContractError, DecisionError, IllegalTransitionError
from .machine import Trigger, Verdict, transition
from .states import ACTIVE, PENDING, QUARANTINED

ESCALATE_AFTER = 2   # 同一环节"连续报错"达此次数即升级(默认 2:1 次失败+重跑成功不打扰;连错 2 次升级)

@dataclass(frozen=True)
class AutoResult:
    """auto_review 结果。outcome ∈ {active, quarantined, escalated};attempts=判定环节尝试次数。"""
    outcome: str
    attempts: int


@dataclass(frozen=True)
class ReviewDecision:
    """EVIDENCE_REVIEW 校核判定源的**向后兼容**富返回(Verification 第 2 步)。旧判定源仍可只返回 Verdict,auto_review 两种都认。
    · verdict        —— PASS / FAIL,KNOWLEDGE_ADMISSION 据此转移
    · reason_summary —— 命中 R 条摘要(如 "R1,R9"),拼进 KNOWLEDGE_ADMISSION_log 依据(reason)
    · report_id      —— EVIDENCE_REVIEW 校核报告 id;KNOWLEDGE_ADMISSION_log 只留 report=<id> 链过去,**完整 Problem[] 在 EVIDENCE_REVIEW 报告 / RUNTIME_LOG、不塞 KNOWLEDGE_ADMISSION_log**。"""
    verdict: Verdict
    reason_summary: str | None = None
    report_id: str | None = None


# 判定源接口(结构化说明、非强制类型)。见模块 docstring 的契约:判定源返回 Verdict 或 ReviewDecision。
DecisionSource = Callable[[Path], Verdict | ReviewDecision]


def _source_name(decision_source) -> str:
    return getattr(decision_source, "__name__", None) or type(decision_source).__name__


def auto_review(card_path: Path, decision_source: DecisionSource, *,
                escalate_after: int = ESCALATE_AFTER,
                trigger: Trigger = Trigger.AUTO_RULE) -> AutoResult:
    """自动流转一张 pending 卡。**decision_source 必传**(无默认、绝无默认 PASS)。
    据判定结论自动转移(trigger=AUTO_RULE);环节失败(DecisionError)自动重跑并记 RUNTIME_LOG;连错达阈值升级人工。"""
    # escalate_after 非法 = 调用方误用 auto_review(编程 bug,同"缺 decision_source→TypeError")→ fail-loud、
    # 读卡 / 调 decision_source / 任何留痕之前就挡。(=1 合法 = 首错即升级、不重跑。)
    if not isinstance(escalate_after, int) or escalate_after < 1:
        raise ValueError("escalate_after 须为 >=1 的整数")
    current = read_status(card_path)
    if current != PENDING:
        raise IllegalTransitionError(
            f"auto_review 只处理送审的 pending 卡,当前 {current}: {card_path}")
    src = _source_name(decision_source)
    consecutive = 0
    while True:
        try:
            outcome = decision_source(card_path)          # 走判定(占位 / 手动 / EVIDENCE_REVIEW);KNOWLEDGE_ADMISSION 不调模型
        except DecisionError as e:                        # 只有显式 DecisionError = 可重跑环节失败
            consecutive += 1
            log_reason("KNOWLEDGE_ADMISSION", "auto_review_rerun",
                       f"判定环节失败第 {consecutive} 次(decision_source={src}):{e}",
                       event_category="verify", target=str(card_path))
            if consecutive >= escalate_after:             # 连错达阈值 → 升级人工、停重跑、卡留 pending、不写 KNOWLEDGE_ADMISSION_log
                log_reason("KNOWLEDGE_ADMISSION", "auto_review_escalate",
                           f"同一环节连错 {consecutive} 次达阈值 {escalate_after},升级人工(卡留 pending)",
                           event_category="verify", target=str(card_path))
                return AutoResult(outcome="escalated", attempts=consecutive)
            continue                                       # 否则自动重跑当前环节
        # 认两种返回:Verdict(旧、向后兼容)或 ReviewDecision(EVIDENCE_REVIEW:带命中摘要 + 报告 id)。
        if isinstance(outcome, ReviewDecision):
            verdict, summary, report_id = outcome.verdict, outcome.reason_summary, outcome.report_id
        elif isinstance(outcome, Verdict):
            verdict, summary, report_id = outcome, None, None
        else:                                             # 违约(非 Verdict / 非 ReviewDecision)= bug,立即暴露
            raise DecisionContractError(
                f"decision_source={src} 违约:返回非 Verdict / ReviewDecision({outcome!r});"
                f"立即暴露、不重跑不隔离: {card_path}")
        if not isinstance(verdict, Verdict):              # ReviewDecision.verdict 也须是 Verdict
            raise DecisionContractError(
                f"decision_source={src} 违约:ReviewDecision.verdict 非 Verdict({verdict!r}): {card_path}")
        # 据结论自动转移(trigger 默认 AUTO_RULE=自动流转器触发;EVIDENCE_REVIEW 传 EVIDENCE_REVIEW_VERDICT)。reason 保持旧格式,
        # **仅当 ReviewDecision 带 summary/report 时追加**(旧 Verdict 路径 reason 一字不变、向后兼容)。
        to_status = ACTIVE if verdict is Verdict.PASS else QUARANTINED
        reason = f"自动流转:decision_source={src}; verdict={verdict.value}"
        if summary:                                       # 命中 R 条摘要(完整 Problem[] 在 EVIDENCE_REVIEW 报告 / RUNTIME_LOG)
            reason += f"; summary={summary}"
        if report_id:                                     # EVIDENCE_REVIEW 校核报告 id,KNOWLEDGE_ADMISSION_log 只留 report=<id> 链过去
            reason += f"; report={report_id}"
        transition(card_path, to_status, trigger=trigger, reason=reason)
        return AutoResult(outcome=to_status, attempts=consecutive + 1)
