"""Memorive EVIDENCE-EXTRACTION/EVIDENCE_EXTRACTION · EVIDENCE_EXTRACTION 蒸馏受控错误(承 DocumentProcessingError 风格:受控、非崩溃;抛前已写 RUNTIME_LOG detail 日志)。

调用方(编排/自测)捕获后展示 message。非受控异常(疑 bug)不在此列、上抛不吞。
"""
from __future__ import annotations


class EvidenceExtractionError(Exception):
    """EVIDENCE_EXTRACTION 蒸馏通用受控错误。"""


class SourceNotFoundError(EvidenceExtractionError):
    """入口定位不到同篇 [CleanMD] 或 [Chunks].jsonl —— fail-closed,**绝不退回「直接用 CleanMD 蒸馏」**
    (退回=每字段 chunk_id 溯源根基当场崩,承 DocumentEmbed「不静默降级」同理)。"""


class SourceInvalidError(EvidenceExtractionError):
    """CleanMD frontmatter 非法 / 某 chunk 缺 chunk_id / 同篇 paper_id 或归属不一致 —— fail-closed 挡下。"""


class OwnershipBlockedError(EvidenceExtractionError):
    """归属红线:`data_ownership=entrusted`(须本地 LLM、Core 未落地)或**缺失/未知**(归属不明)→
    直接挡下、**不调 MODEL_GATEWAY**(蒸馏走 DeepSeek 云端,绝不发他人托付/归属不明数据,承 D0 / DocumentEmbed 安全阀同理)。"""


class ChunksTooLargeError(EvidenceExtractionError):
    """拼接输入超字符上限 → 受控报错,**绝不静默截断**(截断会破 R8 取材全范围、并让模型把没读到的误标未给破 R9)。
    分段策略是将来长文献的事,本步受控报错即可。"""


class CardParseError(EvidenceExtractionError):
    """模型响应剥围栏/提取首个 JSON 对象后仍非合法 JSON;原始响应已记 RUNTIME_LOG detail,**绝不吞成空卡**。"""


class CardLanguageDriftError(CardParseError):
    """核心叙述出现来源不支持的跨语言长片段；拒绝模型新增翻译或异语解释。"""


class CardFieldMissingError(EvidenceExtractionError):
    """模型输出缺核心字段,或字段结构/类型不符(缺 value/chunk_ids、类型不对)。"""


class CardAnchorInvalidError(EvidenceExtractionError):
    """核心字段假 chunk_id 经唯一一次定向修复后仍无法安全处置。

    普通情形先由 DeepSeek 只修受影响锚点，再跑完整本地门；返修契约失败时只删除假锚点记录，
    且每个字段仍须保留至少一个真实同篇锚点。若安全删除会令字段失去全部锚点，才以本错误整卡拒绝。
    """


class CardExistsError(EvidenceExtractionError):
    """目标 [Card] 已存在且未显式 `overwrite=True` → **默认不覆盖、受控挡下**
    (承 ARTIFACT_REGISTRY 数据永不覆盖 + 契约 §四「重蒸馏不得覆盖旧卡」;覆盖是危险动作,须显式 + 留痕,同 re_embed「唯一显式入口」模式)。"""


class KeyDataInvalidError(EvidenceExtractionError):
    """key_data 抽取的**结构层**校验不过 → fail-closed **整卡拒**(承决策③「失败不产半截卡」)。
    只管结构:裸数值(stat 空 / stat_type 无值,违 R3)、unit 空、quote 缺 / 空、sample 四槽模板不全(R4 无从落)、
    chunk_id 杜撰(∉ 真实 chunks)、缺字段。
    **不管语义真伪**:真锚配假值、原文有统计却写「未给」、作用域填错(把局部当总体)——明列为
    Verification / EVIDENCE_REVIEW 异源覆盖的已知缺口,validator 不越界判、不误报(守「只结构不语义」)。"""


class CardQuoteNotFoundError(EvidenceExtractionError):
    """`quote` **原文验真不过 → 整卡 fail-closed**(source_anchor v3;承之八「机器逐字符验真」):不写卡、卡走 pending,
    报错入 RUNTIME_LOG(paper_id / 定位 path / chunk_id / reason 供人核)。
    **收口口径(07-09 修一/二/三):此错只发生在唯一一种情形** —— `key_data`(数值,第一刀)+ **干净源**(无 U+FFFD / <br>)
    + quote 归一化后非该 chunk 原文子串 + targeted 重抄一次仍非子串。这是唯一像「整句臆造」的确定性信号。
    **其余一律不抛、不杀卡**:① 超长真子串 → 接受(长≠假、只记观测);② 烂码源致忠实 quote 对不上 → key_data 保留原 quote +
    记 DOCUMENT_PROCESSING 债、by_field 降级 null;③ by_field 任何字段(现无字段硬 gate:模型对含 boundary_conditions 在内的核心内容字段都会
    paraphrase / 综合、无一可靠逐字锚)quote 挂不上 → 降级 null、改写 / 综合忠实归 EVIDENCE_REVIEW 异源 + 人核。

    **诚实边界(承第一刀外部审核 F1)**:substring 证的是「**引文在原文在场**」这一确定性事实——挡下的是「引文根本
    不在原文」那类(跨源 merge / 整句臆造 / 连支撑句都编)。它**不**证「value 忠于该句 / 改写忠于原意」:真锚配假值
    (quote 真、value 假)、改写漂移(paraphrase 不忠,如 merge attach 了一句真的部分原文)**仍归 EVIDENCE_REVIEW 异源校核**
    (key_data 恒 needs_review=true、照进 EVIDENCE_REVIEW)。故本层是 EVIDENCE_REVIEW **之前的一道确定性预筛,不替代 EVIDENCE_REVIEW**。

    携结构化定位(异常对象带字段、不只 RUNTIME_LOG log context;承 F3):path 人读定位、chunk_id、reason、locator 结构化附加。"""

    def __init__(self, message: str, *, path: str | None = None, chunk_id: str | None = None,
                 reason: str | None = None, locator: dict | None = None):
        super().__init__(message)
        self.path = path                 # 人读定位:key_data[3].quote / by_field.key_results[1].quote
        self.chunk_id = chunk_id
        self.reason = reason
        self.locator = locator or {}     # 结构化附加:block / entry_index / entry_no / field / anchor_index …
