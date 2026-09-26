"""PR-OS P01/T07/M03 · M3 检索加权共享数据结构(T7 第 1 步立;M3b/M3c 后续步骤复用)。

承 T7 第 0 步契约 §一:M3a 只产「问题类型 + 检索策略参数」,不做召回/打包。
- QueryType   五类主类(主 qtype 恒为其一)。
- RetrievalStrategy 检索策略参数(含 counterexample_boost 信号,传 M3c)。
- QueryUnderstanding = M3a 完整输出(只含策略参数,无候选块、无 Context Pack)。

口径钉死:limitation_interest 是与主类**正交的副信号**(关注作者自陈局限),
**不新增第六类、不驱动 counterexample_boost**(承第 1 步口径③④)。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .ownership import OwnershipContributor


class QueryType(str, Enum):
    """问题主类(5 类,承契约 §一)。主 qtype 恒为其一;全不命中兜底到 LOOKUP_CONCEPT(带 is_fallback)。"""
    FIND_DATA = "find_data"                      # 找数据:某数值 / 指标
    FIND_METHOD = "find_method"                  # 找方法:怎么做 / 流程 / 技术
    FIND_COUNTEREXAMPLE = "find_counterexample"  # 找反例:反证 / 相反 / 证伪(→ counterexample_boost)
    REVIEW_SYNTHESIS = "review_synthesis"        # 综述脉络:演变 / 概览 / 对比
    LOOKUP_CONCEPT = "lookup_concept"            # 查概念:是什么 / 定义(亦作全不命中兜底占位)


@dataclass(frozen=True)
class RetrievalStrategy:
    """M3a 产出的检索策略参数(只输出、不执行;M3b/M3c 读)。

    counterexample_boost —— 「加大反例名额」信号,传 M3c(承契约 §四:找反例类整包以反例为主、名额放大)。
      **仅由 counterexample_intent 驱动**(即主类为 FIND_COUNTEREXAMPLE 时);limitation_interest 绝不置此位。
    limitation_interest —— 与主类正交的副信号:问句关注「作者自陈局限」时置 True,只影响 recall_emphasis、
      **不放大反例名额**(承口径③:别把「找局限」当「找反证」)。
    recall_emphasis —— ⚠ 仅「记录级粗提示」:Phase 1 无字段级向量、其实际检索效果有限(供 M11 记录
      + 将来 M3c/M7 用);M3a **绝不**据此造字段级加权 / 字段向量 / doc_type 来源加权 / M7 五维(留 Phase 3)。
    """
    qtype: QueryType
    counterexample_boost: bool = False           # 仅 counterexample_intent(主类 FIND_COUNTEREXAMPLE)→ True
    limitation_interest: bool = False            # 正交副信号:关注作者自陈局限,不放大反例名额
    recall_emphasis: str | None = None           # 粗提示:'key_data'/'method'/'diversity'/'counterexample'/'limitations'/None


@dataclass(frozen=True)
class QueryUnderstanding:
    """M3a 完整输出。**只含策略参数**——无候选块、无 Context Pack(守边界:召回=M3b、打包=M3c)。"""
    question: str
    qtype: QueryType
    strategy: RetrievalStrategy
    matched_signals: tuple = ()                  # 命中的主类信号词(保序去重,可复现/可测);兜底为空 ()
    is_fallback: bool = False                    # 主类无显式信号 → True(兜底中性策略,不裸标「判为查概念」)
    confidence: str = "rule"                     # 'rule'(命中规则)/ 'low'(兜底);Phase 1 规则后端的诚实标注


@dataclass(frozen=True)
class QueryFacet:
    """Phase 1 P0-A question facet used for independent retrieval coverage."""
    id: str
    required: bool = True
    query_terms: tuple[str, ...] = ()
    preferred_card_paths: tuple[str, ...] = ()
    preferred_sections: tuple[str, ...] = ()
    preferred_block_types: tuple[str, ...] = ()
    min_evidence_blocks: int = 1


@dataclass(frozen=True)
class Candidate:
    """M3b 候选块(承契约 §一;T7 第 2 步)。**只召回、不打包**——无 weight/token/反例字段(留 M3c)。

    ⚠ `distance` = Chroma `.query()` 原始距离(**越小越近**);排序按 distance **升序**,
      **绝不当 similarity 降序**(承第 2 步口径②)。若 M3c 需归一化 similarity,届时再由 distance 换算。
    admission 恒 `"active"`(准入通过)、**绝不 `"已校验"`**(承 §〇 准入⊥校验)。
    `field` best-effort:查不到卡片 by_field 归属则 None、**绝不因此剔除**(承口径⑤);必填仅前四 + admission。
    `verified_by_m6`:`require_verified=False` 时 None;True 仅当放行 `M8_log` 触发源 =
      `Trigger.M6_VERDICT.value`(实现比对枚举值、勿硬编码中文字面量;防标签漂移,承 §〇)。
    """
    chunk_id: str                                # 向量主键(= 向量 metadata.chunk_id)
    paper_id: str                                # 所属卡片(= 向量 metadata.paper_id)
    text: str                                    # chunk 原文(Chroma document)
    distance: float                              # 原始距离,越小越近(排序升序)
    admission: str = "active"                    # 恒 active 准入;绝不已校验
    field: str | None = None                     # best-effort 归并到的卡片字段名;None 不阻断
    verified_by_m6: bool | None = None           # require_verified 关时 None
    credibility: str | None = None               # v6.4 之四·上半:该字段的 M6 信用档(verified/anchor_uncertain/flagged/unknown);
                                                 # **先只透传**(从卡片 field_credibility 回读)、不改检索权重(承分级信任·上半)
    rerank_score: float | None = None            # A(v6.4·修跨篇混入):M3b cross-encoder 重排相关性分。
                                                 # rerank **关**时 None(权重退回 1/(1+distance));**开**时=该块 relevance_score
                                                 # (M3c 权重替换为本分——承拍板②;越大越相关,与 distance 越小越近相反)。
    ranking_rank: int | None = None              # 生成式 listwise 的序位；不是 relevance score。
    section_path: str | None = None               # P0-A: M1d structure metadata; missing stays unknown
    block_type: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    metadata_status: str = "unknown"              # vector/chunk_sidecar/unknown; never inferred silently
    facet_ids: tuple[str, ...] = ()
    retrieval_reasons: tuple[str, ...] = ()
    source_role: str | None = None
    member_chunk_ids: tuple[str, ...] = ()
    logical_span_id: str | None = None
    logical_role: str | None = None
    # P06/T08: one authoritative ownership fact and its immutable derived binding.
    ownership_subject_ref: str | None = None
    ownership_contract_version: str | None = None
    ownership_aggregation_policy: str | None = None
    data_ownership: str | None = None
    ownership_assertion_ref: str | None = None
    ownership_revision: int | None = None
    ownership_basis_ref: str | None = None
    ownership_projection_value: str | None = None
    ownership_projection_revision: int | None = None
    ownership_mix_state: str | None = None
    ownership_contributors: tuple[OwnershipContributor, ...] = ()
    ownership_escalated_by: tuple[str, ...] = ()
    ownership_digest: str | None = None
    ownership_resolution_status: str = "legacy_missing"
    ownership_error_codes: tuple[str, ...] = ()
    ownership_warnings: tuple[str, ...] = ()
    legacy_bucket: str = "L3_insufficient_authority"


@dataclass(frozen=True)
class CandidateSet:
    """M3b 产物:候选块集合 + 汇总(M11 亦记同口径计数)。**无 weight/token/反例名额字段**(留 M3c)。"""
    candidates: tuple                            # tuple[Candidate](按 distance 升序、每卡限额内、≤ target)
    target_active_count: int                     # 目标固定召回数(env 可配、不随库膨胀)
    raw_recall_count: int                        # raw Chroma 命中数(overfetch 实际取回)
    filtered_non_active_count: int               # 回读活卡滤掉的非 active(+ require_verified 未过)数
    final_active_count: int                      # 最终 active 候选数(≤ target)
    reached_overfetch_cap: bool                  # overfetch 到 MAX_OVERFETCH 仍不足(active<target 可能因封顶)
    require_verified: bool = False
    scope_mode: str = "shared"                   # shared / paper_id；默认共享，保持既有调用兼容
    scope_paper_id: str | None = None            # scope_mode=paper_id 时为目标论文，否则 None


@dataclass(frozen=True)
class ContextBlock:
    """M3c 打包块(承契约 §一 L41;T7 第 3 步)。**只含打包字段**——绝不混 M4/M5 字段
    (answer/analysis/claim/tension/judgement/final_conclusion 等)。"""
    content: str                                 # chunk 原文
    source: dict                                 # 来源(契约 §一)= {"chunk_id":…, "paper_id":…, "field":…};field 可 None
    weight: float                                # Phase 1 简化权重(1/(1+max(distance,0))×规则)
    is_counterexample: bool                      # **疑似反例**(强信号粗筛;不裁判真实张力,交 M4)
    tokens: int                                  # ceil(len(content)×ratio)+overhead(保守上界)
    admission: str = "active"                    # 恒 active 准入;**绝不已校验**
    verified_by_m6: bool | None = None           # 沿用 M3b(require_verified 过者 True;否则 None)
    credibility: str | None = None               # v6.4 之四·上半:沿用 M3b Candidate.credibility(该字段 M6 信用档);先只透传
    rerank_score: float | None = None            # A(v6.4·修跨篇混入):沿用 M3b Candidate.rerank_score(rerank 关时 None);留痕/诊断用
    member_chunk_ids: tuple[str, ...] = ()
    logical_span_id: str | None = None
    logical_role: str | None = None
    facet_ids: tuple[str, ...] = ()
    retrieval_reasons: tuple[str, ...] = ()
    source_role: str | None = None
    metadata_status: str = "unknown"
    section_path: str | None = None
    block_type: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    # P06/T08: block binding is derived from Candidate/member contributors.
    block_ref: str | None = None
    ownership_subject_ref: str | None = None
    ownership_contract_version: str | None = None
    ownership_aggregation_policy: str | None = None
    data_ownership: str | None = None
    ownership_assertion_ref: str | None = None
    ownership_revision: int | None = None
    ownership_basis_ref: str | None = None
    ownership_mix_state: str | None = None
    ownership_contributors: tuple[OwnershipContributor, ...] = ()
    ownership_escalated_by: tuple[str, ...] = ()
    ownership_digest: str | None = None
    ownership_resolution_status: str = "legacy_missing"
    ownership_error_codes: tuple[str, ...] = ()
    ownership_warnings: tuple[str, ...] = ()
    legacy_bucket: str = "L3_insufficient_authority"


@dataclass(frozen=True)
class ContextPack:
    """M3c 产物:交 M4 的唯一文献上下文。**只含打包字段**;M3b 召回计数原样透传、M3c 只加打包计数。
    不变量:`estimated_tokens ≤ token_hard_limit` **永远成立**(只整块取舍、不切块内文本;允许空 pack)。"""
    blocks: tuple                                # tuple[ContextBlock](反例受保护、按 weight 排序、总 token ≤ 硬顶)
    estimated_tokens: int                        # 总估算(含 overhead)≤ token_hard_limit
    token_hard_limit: int                        # env 可配硬顶(生死线)
    counterexample_slots: dict                   # 反例名额(契约 §一)= {"target": boost 后目标, "filled": 实际填几个};filled<target = underfilled、如实不硬凑
    truncated: bool                              # 是否因 token 上限丢弃/截断了块(明细在 M11)
    # ── M3b 召回计数原样透传(M3c 不改)──
    raw_recall_count: int
    filtered_non_active_count: int
    final_active_count: int
    require_verified: bool = False
    scope_mode: str = "shared"                   # 从 CandidateSet 原样透传，供 M4/留痕辨认来源边界
    scope_paper_id: str | None = None
    retrieval_mode: str = "multi_paper_synthesis"
    query_facets: tuple[QueryFacet, ...] = ()
    coverage_status: str = "not_assessed"
    missing_facets: tuple[str, ...] = ()
    coverage_by_facet: dict | None = None
    retry_count: int = 0
    fallback_used: bool = False
    answer_complete: bool | None = None
    bibliography_share: float = 0.0
    excluded_bibliography_count: int = 0
    # P06/T08: only selected blocks contribute to this immutable pack snapshot.
    context_pack_ref: str | None = None
    context_pack_hash: str | None = None
    ownership_subject_ref: str | None = None
    ownership_contract_version: str | None = None
    ownership_aggregation_policy: str | None = None
    effective_data_ownership: str | None = None
    ownership_mix_state: str | None = None
    ownership_contributors: tuple[OwnershipContributor, ...] = ()
    ownership_escalated_by: tuple[str, ...] = ()
    ownership_digest: str | None = None
    ownership_resolution_status: str = "legacy_missing"
    ownership_error_codes: tuple[str, ...] = ()
    ownership_warnings: tuple[str, ...] = ()
    legacy_bucket: str = "L3_insufficient_authority"
    original_estimated_tokens: int = 0
    dropped_evidence_block_count: int = 0
    dropped_reason_counts: dict | None = None
    ownership_propagation_receipt: dict | None = None
    card_review_qualifications: tuple[dict, ...] = ()


@dataclass(frozen=True)
class LogicalSpan:
    """Runtime-only group; original member IDs remain the citation boundary."""
    logical_span_id: str
    member_chunk_ids: tuple[str, ...]
    content: str
    paper_id: str
    section_path: str | None
    logical_role: str
    block_type: str | None
    page_start: int | None
    page_end: int | None
    distance: float
    facet_ids: tuple[str, ...] = ()
    retrieval_reasons: tuple[str, ...] = ()
    source_role: str | None = None
    metadata_status: str = "unknown"
    field: str | None = None
    admission: str = "active"
    verified_by_m6: bool | None = None
    credibility: str | None = None
    rerank_score: float | None = None
    # P06/T08: aggregate every member; never inherit only members[0].
    ownership_subject_ref: str | None = None
    ownership_contract_version: str | None = None
    ownership_aggregation_policy: str | None = None
    data_ownership: str | None = None
    ownership_assertion_ref: str | None = None
    ownership_revision: int | None = None
    ownership_basis_ref: str | None = None
    ownership_mix_state: str | None = None
    ownership_contributors: tuple[OwnershipContributor, ...] = ()
    ownership_escalated_by: tuple[str, ...] = ()
    ownership_digest: str | None = None
    ownership_resolution_status: str = "legacy_missing"
    ownership_error_codes: tuple[str, ...] = ()
    ownership_warnings: tuple[str, ...] = ()
    legacy_bucket: str = "L3_insufficient_authority"
