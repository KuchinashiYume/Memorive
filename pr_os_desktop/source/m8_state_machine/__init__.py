"""PR-OS P01/T05/M08 · M8 状态机 —— 准入闸门。

Phase 1:三态转移核心 + 状态内嵌卡片 + M8_log 留痕 + 自动流转。

公共入口:
  转移       transition(card_path, to_status, *, trigger, reason)
  据判定转移 apply_verdict(card_path, verdict, *, trigger, reason)   # 只作用 pending 卡
  人工入口   manual_approve / manual_reject(card_path, *, reason)
  自动流转   auto_review(card_path, decision_source, *, escalate_after=2, trigger=AUTO_RULE)
             # decision_source 必传;M6 传 trigger=Trigger.M6_VERDICT、可返回 ReviewDecision(向后兼容)
  读现态     read_status(card_path) / read_data_id(card_path)
  读留痕     read_records(year)                                     # 读某年 M8_log(校核视图、只读)
  常量/类型  PENDING / ACTIVE / QUARANTINED、Trigger、Verdict、AutoResult、ReviewDecision
  受控错误   M8Error 及子类(含 M8LogAppendError / DecisionError / DecisionContractError)

Phase 1 边界:判定逻辑**不进 M8**——判定源占位/手动(真实 R1–R9 判定归 M6/T6);**本步已接入自动流转**
(auto_review:自动分配 + 失败自动重跑 + 连错 2 次升级)+ M8_log 留痕(成功转移后 append);M8 一个模型都不调
(判定源后端若调模型经 M9);不碰 M13 生命周期;不直连任何 SDK。
placeholder 判定源仅离线自测用、**不在本入口导出**(守 active≠已校验、绝不作生产默认)。
"""
from .states import PENDING, ACTIVE, QUARANTINED, STATES, is_legal, is_phase1_enabled
from .card_io import read_status, read_data_id
from .machine import (transition, apply_verdict, manual_approve, manual_reject,
                      Trigger, Verdict)
from .m8_log import read_records
from .dispatch import auto_review, AutoResult, ReviewDecision
from .errors import (M8Error, IllegalTransitionError, TransitionNotEnabledError,
                      TriggerNotAcceptedError, MissingBasisError, CardStateError,
                      M8LogAppendError, DecisionError, DecisionContractError,
                      AdmissionBlocked, RealStateTransitionForbidden)
__all__ = [
    "PENDING", "ACTIVE", "QUARANTINED", "STATES", "is_legal", "is_phase1_enabled",
    "read_status", "read_data_id", "transition", "apply_verdict", "manual_approve", "manual_reject",
    "auto_review", "AutoResult", "ReviewDecision", "Trigger", "Verdict", "read_records",
    "M8Error", "IllegalTransitionError", "TransitionNotEnabledError",
    "TriggerNotAcceptedError", "MissingBasisError", "CardStateError", "M8LogAppendError",
    "DecisionError", "DecisionContractError",
    "AdmissionBlocked", "RealStateTransitionForbidden",
]
