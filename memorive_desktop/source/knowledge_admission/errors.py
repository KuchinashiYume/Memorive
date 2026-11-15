"""Memorive KNOWLEDGE-ADMISSION/KNOWLEDGE_ADMISSION · KNOWLEDGE_ADMISSION 状态机受控错误(承 EvidenceExtractionError/DocumentProcessingError 风格:受控、非崩溃;调用方捕获后展示 message)。

非受控异常(疑 bug)不在此列、上抛不吞。
"""
from __future__ import annotations


class KnowledgeAdmissionError(Exception):
    """KNOWLEDGE_ADMISSION 状态机通用受控错误。"""


class AdmissionBlocked(KnowledgeAdmissionError):
    """ANALYSIS-ADMISSION Analysis 准入请求未满足 fail-closed 条件。"""


class RealStateTransitionForbidden(KnowledgeAdmissionError):
    """ANALYSIS-ADMISSION build-only adapter 被误用来执行真实 KNOWLEDGE_ADMISSION 状态迁移。"""


class IllegalTransitionError(KnowledgeAdmissionError):
    """**根本非法**的转移 —— 守卫挡下、状态不被改动,绝不默默执行:
    未经 pending 直接 active、从 active 无依据跳回 pending、目标态不在 review_status 三态枚举
    (如 archive,属 ARTIFACT_REGISTRY Usage 另一轴)、同态自转等。"""


class TransitionNotEnabledError(KnowledgeAdmissionError):
    """转移在 review_status 轴上**机制合法**(is_legal=True)、但 **Core 不启用**:
    典型 active→quarantined(事后隔离)——机制归 KNOWLEDGE_ADMISSION、触发时机归 ARTIFACT_REGISTRY,Core 不主动发起。
    区别于 IllegalTransitionError(那是根本非法);此为「合法但本阶段不执行」,亦绝不默默执行。"""


class TriggerNotAcceptedError(KnowledgeAdmissionError):
    """触发源非 Core 执行路径可接受(仅 MANUAL/PLACEHOLDER):AUTO_RULE 留第 3 步、EVIDENCE_REVIEW_VERDICT 留 Verification。
    (RETRIEVAL_WEIGHTING 解除降权改 DerivedPenalty/不改准入态、是 KNOWLEDGE_ADMISSION_log 治理轴触发源(第 2 步),根本不在 transition 的
    Trigger 枚举里 —— 传字符串会先被 isinstance 挡成 MissingBasisError。)"""


class MissingBasisError(KnowledgeAdmissionError):
    """转移缺依据 —— 承契约「无依据地悄悄改状态」须挡下:
    reason 空 / 空白,或 trigger 非 Trigger 枚举成员(如传字符串 "RETRIEVAL_WEIGHTING_UNPENALIZE")。
    (留痕依据须齐;KNOWLEDGE_ADMISSION_log 实际落痕在第 2 步。)"""


class CardStateError(KnowledgeAdmissionError):
    """卡片状态载体异常 —— fail-closed 不猜、不腐蚀文件:
    frontmatter 非 --- … --- / 解析非 dict、review_status 缺失或不在三态枚举、
    frontmatter 内 review_status 行非唯一无引号 plain scalar(0 或 >1 行)、落盘自证不符;
    以及数据 ID 严格读:source_anchor.paper_id 缺失 / 空 / 类型不对(read_data_id,不回退文件名)。"""


class KnowledgeAdmissionLogAppendError(KnowledgeAdmissionError):
    """KNOWLEDGE_ADMISSION_log 追加受控错误:reason / data_id 空、event_category 非枚举、目录不可建 / 不可写(pre-flight)、
    append-only 被破坏(写后变小);或 write_status 成功而 append 失败 —— 暴露「状态已变、留痕失败」的
    审计缺口,绝不静默(本步不做事务回滚)。"""


class DecisionError(KnowledgeAdmissionError):
    """判定源「环节失败」信号(**可重跑**):判定过程本身失败(瞬时错 / 模型调用炸 / 异常),**不是**「判定说未通过」。
    auto_review 收到它才重跑当前环节;连错达阈值升级人工。(判定说 FAIL 是正常结论 → quarantined、非重跑。)
    只有判定源**显式 raise 本类**才算可重跑环节失败;其他异常一律上抛不吞不重跑(防真 bug 伪装成瞬时错)。"""


class DecisionContractError(KnowledgeAdmissionError):
    """判定源**违约**(当 bug 立即暴露、不吞):decision_source 返回了非 Verdict 的东西。
    不重跑、不隔离、不转移 —— 违约是 bug、上抛;绝不让非 Verdict 走到 PASS/FAIL 分支
    (防 Step 1 apply_verdict("pass") 那类同型反向放错)。"""
