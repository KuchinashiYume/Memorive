"""M3a Query Understanding —— 识别问题主类 + 切检索策略参数(T7 第 1 步)。

承 T7 第 0 步契约 §一:问题 → 问题类型 + 检索策略参数(含「是否加大反例名额」信号),
**只输出策略参数、不做召回 / 打包**(召回=M3b、打包=M3c,守边界)。

分类后端(Phase 1)= 规则 + 关键词(**不调模型、零额度**)。分类封在 `_classify()` 后留**可换后端 seam**:
将来若换 M9 便宜模型做意图识别,只换 `_classify`、对外签名不变——**且必经 M9、绝不直连 SDK**(承契约 §八)。

⚠ recall_emphasis 只作「记录级粗提示」:Phase 1 无字段级向量、实际检索效果有限;
M3a **绝不**造字段级加权 / 字段向量 / doc_type 来源加权 / M7 五维(留 Phase 3)。
⚠ 规则/关键词分类是**粗筛**:英文按**词首锚定 + 前缀匹配**(吃下复数/屈折:limitation→limitations、
   method→methods、controvers→controversy/controversial;词首锚定使 'rate' 仍不误命中 'accurate'),
   中文按子串;仍可能过/欠命中,属 Phase 1 已知局限,换 M9 模型后端可改善。
"""
from __future__ import annotations

import re

from m11_log import log_reason

from .types import QueryType, QueryUnderstanding, RetrievalStrategy

# ── 关键词表(小写匹配;中/英)。counterexample_intent 与 limitation_interest **严格分开**(口径③):
#    前者=找反证(→ FIND_COUNTEREXAMPLE + counterexample_boost);后者=作者自陈局限(→ 仅 recall_emphasis)。──
_COUNTEREXAMPLE_INTENT = (        # 找反证 / 相反证据
    "反例", "反证", "相反", "反对", "证伪", "反驳", "驳斥", "争议", "不成立", "站不住",
    "counterexample", "counter-example", "against", "contradict", "refute", "rebut",
    "dispute", "disprove", "controvers", "conflicting",
)
_LIMITATION_INTEREST = (          # 正交副信号:作者自陈「局限 / 不足」(≠ 找反证)
    "局限", "不足", "短板", "缺陷", "适用范围", "边界条件",
    "limitation", "shortcoming", "drawback", "weakness", "caveat",
)
_FIND_DATA = (
    "多少", "数值", "指标", "百分比", "几个", "多大", "比例", "数据是",
    "how much", "how many", "value", "rate", "percentage",
)
_FIND_METHOD = (
    "怎么做", "如何", "方法", "流程", "步骤", "技术", "手段", "怎样", "如何做",
    "method", "approach", "protocol", "procedure", "how to", "technique",
)
_REVIEW_SYNTHESIS = (
    "综述", "脉络", "演变", "发展历程", "概览", "全景", "对比各", "梳理", "研究现状",
    "review", "overview", "evolution", "landscape", "survey", "synthesis",
)
_LOOKUP_CONCEPT = (
    "是什么", "定义", "含义", "概念", "指的是", "什么是",
    "what is", "define", "definition", "meaning",
)

# 主类优先级(高→低;**确定、可复现、可测**)。找反例最高(反回音室是命门,宁可多留反例)。
_PRIORITY = (
    (QueryType.FIND_COUNTEREXAMPLE, _COUNTEREXAMPLE_INTENT),
    (QueryType.FIND_DATA, _FIND_DATA),
    (QueryType.FIND_METHOD, _FIND_METHOD),
    (QueryType.REVIEW_SYNTHESIS, _REVIEW_SYNTHESIS),
    (QueryType.LOOKUP_CONCEPT, _LOOKUP_CONCEPT),
)

# 类型 → recall_emphasis 粗提示(记录级、非真实加权)。
_EMPHASIS = {
    QueryType.FIND_DATA: "key_data",
    QueryType.FIND_METHOD: "method",
    QueryType.REVIEW_SYNTHESIS: "diversity",
    QueryType.FIND_COUNTEREXAMPLE: "counterexample",
}


def _hits(text_lower: str, keywords) -> list[str]:
    """text(已小写)中命中的关键词(保序去重)。英文/短语按**词首锚定 + 前缀匹配**
    (吃复数/屈折:limitation→limitations、method→methods、controvers→controversy;
    词首锚定 `(?<![a-z0-9])` 使 'rate' 不误命中 'accurate'——'rate' 前是 'u'),中文/含符号按子串。
    用于 matched_signals —— 结果确定、可复现、可测。"""
    out: list[str] = []
    for kw in keywords:
        if kw.replace(" ", "").replace("-", "").isalnum() and kw.isascii():
            # 词首锚定、**不锚词尾** → 前缀匹配吃下复数/屈折;'rate' 仍不命中 'accurate'(前有字母)。
            matched = re.search(rf"(?<![a-z0-9]){re.escape(kw)}", text_lower) is not None
        else:
            matched = kw in text_lower                      # 中文无词边界 → 子串
        if matched and kw not in out:
            out.append(kw)
    return out


def _classify(question: str):
    """Phase 1 规则/关键词分类。返回 (qtype, matched_signals, limitation_interest, is_fallback)。
    **可换后端 seam**:将来换 M9 模型只改本函数、签名不变(且经 M9、绝不直连 SDK)。"""
    text = question.lower()
    limitation = bool(_hits(text, _LIMITATION_INTEREST))    # 正交副信号,不参与主类优先级裁决
    for qtype, keywords in _PRIORITY:                        # 固定优先级取第一个命中的主类
        hit = _hits(text, keywords)
        if hit:
            return qtype, tuple(hit), limitation, False
    return QueryType.LOOKUP_CONCEPT, (), limitation, True    # 全不命中 → 兜底(中性、is_fallback=True)


def understand_query(question: str) -> QueryUnderstanding:
    """M3a 主入口:问题 → QueryUnderstanding(**只出策略参数,不召回 / 不打包**)。

    空 / 纯空白问题 → ValueError(fail-loud;且**不写一次正常分类日志**)。
    正常 / 兜底分类均经 M11 `log_reason` 留痕(兜底 reason 写「无显式信号→fallback」,不写「判为查概念」)。
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("M3a: question 必须是非空字符串(空 / 纯空白 fail-loud,不做分类、不写日志)")

    qtype, matched, limitation, is_fallback = _classify(question)
    strategy = RetrievalStrategy(
        qtype=qtype,
        counterexample_boost=(qtype is QueryType.FIND_COUNTEREXAMPLE),   # 仅 counterexample_intent 驱动
        limitation_interest=limitation,
        recall_emphasis=("limitations" if limitation else _EMPHASIS.get(qtype)),
    )
    result = QueryUnderstanding(
        question=question, qtype=qtype, strategy=strategy,
        matched_signals=matched, is_fallback=is_fallback,
        confidence=("low" if is_fallback else "rule"),
    )

    if is_fallback:
        reason = "主类无显式信号 → fallback 中性策略(qtype=lookup_concept 仅占位、非判定)"
    else:
        reason = f"命中 {list(matched)} → 判为 {qtype.value}"
    if limitation:
        reason += ";副信号 limitation_interest → recall_emphasis=limitations(不放大反例名额)"

    log_reason("M3", "M3a 问题类型识别", reason, event_category=None, context={
        "question": question,
        "qtype": qtype.value,
        "counterexample_boost": strategy.counterexample_boost,
        "limitation_interest": strategy.limitation_interest,
        "recall_emphasis": strategy.recall_emphasis,
        "matched_signals": list(matched),
        "is_fallback": is_fallback,
        "confidence": result.confidence,
    })
    return result
