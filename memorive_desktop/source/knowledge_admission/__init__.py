"""Memorive KNOWLEDGE-ADMISSION/KNOWLEDGE_ADMISSION · KNOWLEDGE_ADMISSION 状态机 —— 准入闸门。

Core:三态转移核心 + 状态内嵌卡片 + KNOWLEDGE_ADMISSION_log 留痕。

公共入口:
  转移       transition(card_path, to_status, *, trigger, reason)
  人工入口   manual_approve / manual_reject(card_path, *, reason)
  读现态     read_status(card_path) / read_data_id(card_path)
  读留痕     read_records(year)                                     # 读某年 KNOWLEDGE_ADMISSION_log(校核视图、只读)
  常量/类型  PENDING / ACTIVE / QUARANTINED、Trigger、Verdict、ReviewDecision
  受控错误   KnowledgeAdmissionError 及子类(含 KnowledgeAdmissionLogAppendError / DecisionError)

Core 边界:判定逻辑**不进 KNOWLEDGE_ADMISSION**——判定源占位/手动(真实 R1–R9 判定归 EVIDENCE_REVIEW/Verification);KNOWLEDGE_ADMISSION_log 留痕(成功转移后 append);KNOWLEDGE_ADMISSION 一个模型都不调
(判定源后端若调模型经 MODEL_GATEWAY);不碰 ARTIFACT_REGISTRY 生命周期;不直连任何 SDK。
placeholder 判定源仅离线自测用、**不在本入口导出**(守 active≠已校验、绝不作生产默认)。
"""
from .states import PENDING, ACTIVE, QUARANTINED, STATES, is_legal, is_core_enabled
from .card_io import read_status, read_data_id
from .machine import (
    transition,
    Trigger,
    Verdict,
)
from .knowledge_admission_log import read_records
from .dispatch import ReviewDecision
from .errors import (
    KnowledgeAdmissionError,
    IllegalTransitionError,
    TransitionNotEnabledError,
    TriggerNotAcceptedError,
    MissingBasisError,
    CardStateError,
    KnowledgeAdmissionLogAppendError,
    DecisionError,
)
__all__ = [
    "PENDING",
    "ACTIVE",
    "QUARANTINED",
    "STATES",
    "is_legal",
    "is_core_enabled",
    "read_status",
    "read_data_id",
    "transition",
    "ReviewDecision",
    "Trigger",
    "Verdict",
    "read_records",
    "KnowledgeAdmissionError",
    "IllegalTransitionError",
    "TransitionNotEnabledError",
    "TriggerNotAcceptedError",
    "MissingBasisError",
    "CardStateError",
    "KnowledgeAdmissionLogAppendError",
    "DecisionError",
]
