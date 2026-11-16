"""Query understanding: the question's type and the retrieval strategy parameters.

A question becomes a question type and strategy parameters (including whether to give
counterexamples more room). It only outputs parameters; recall is candidate_retrieval.py's job and
packing context_pack.py's.

Classification uses rules and keywords: no model, no cost. It sits behind _classify(), so a model
could replace it later without changing the signature, called through the gateway like every model.

recall_emphasis is a coarse hint for the log only: there are no per-field vectors, and no field
weights are built from it.
The keyword match is coarse: English words match at the start of a word with any ending (limitation
matches limitations, controvers matches controversy and controversial; 'rate' does not match
'accurate'), Chinese as substrings. It can still miss or over-match.
"""
from __future__ import annotations

import re

from memorive.platform.logs import log_reason

from memorive.research.retrieval.types import QueryType, QueryUnderstanding, RetrievalStrategy

# ── keywords (matched in lower case; Chinese and English). Looking for counter-evidence
#    (FIND_COUNTEREXAMPLE and counterexample_boost) is kept apart from interest in the limitations
#    the authors state (recall_emphasis only). ──
_COUNTEREXAMPLE_INTENT = (        # counter-evidence
    "反例", "反证", "相反", "反对", "证伪", "反驳", "驳斥", "争议", "不成立", "站不住",
    "counterexample", "counter-example", "against", "contradict", "refute", "rebut",
    "dispute", "disprove", "controvers", "conflicting",
)
_LIMITATION_INTEREST = (          # limitations the authors state (not counter-evidence)
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

# The order of the question types, highest first. Counterexamples come first: rather keep too many
# than let the analysis echo one side.
_PRIORITY = (
    (QueryType.FIND_COUNTEREXAMPLE, _COUNTEREXAMPLE_INTENT),
    (QueryType.FIND_DATA, _FIND_DATA),
    (QueryType.FIND_METHOD, _FIND_METHOD),
    (QueryType.REVIEW_SYNTHESIS, _REVIEW_SYNTHESIS),
    (QueryType.LOOKUP_CONCEPT, _LOOKUP_CONCEPT),
)

# Question type -> recall_emphasis (a hint for the log, not a weight).
_EMPHASIS = {
    QueryType.FIND_DATA: "key_data",
    QueryType.FIND_METHOD: "method",
    QueryType.REVIEW_SYNTHESIS: "diversity",
    QueryType.FIND_COUNTEREXAMPLE: "counterexample",
}


def _hits(text_lower: str, keywords) -> list[str]:
    """The keywords found in text (lower case), in order, once each. English words and phrases match
    at the start of a word with any ending (limitation matches limitations; the anchor
    (?<![a-z0-9]) keeps 'rate' from matching 'accurate'); Chinese and words with symbols match as
    substrings. Used for matched_signals, so the result is the same every time."""
    out: list[str] = []
    for kw in keywords:
        if kw.replace(" ", "").replace("-", "").isalnum() and kw.isascii():
            # Anchored at the start of a word only, so endings match; 'rate' still misses 'accurate'.
            matched = re.search(rf"(?<![a-z0-9]){re.escape(kw)}", text_lower) is not None
        else:
            matched = kw in text_lower                      # Chinese has no word boundaries: substring
        if matched and kw not in out:
            out.append(kw)
    return out


def _classify(question: str):
    """Classify by rules and keywords. Returns (qtype, matched_signals, limitation_interest, is_fallback).
    A model could replace this function later with the same signature."""
    text = question.lower()
    limitation = bool(_hits(text, _LIMITATION_INTEREST))    # a side signal; not part of choosing the type
    for qtype, keywords in _PRIORITY:                        # the first type that matches, in the fixed order
        hit = _hits(text, keywords)
        if hit:
            return qtype, tuple(hit), limitation, False
    return QueryType.LOOKUP_CONCEPT, (), limitation, True    # nothing matched: the neutral fallback


def understand_query(question: str) -> QueryUnderstanding:
    """A question as QueryUnderstanding: strategy parameters only, no recall, no packing.

    An empty or blank question raises ValueError and writes no log entry. Every classification,
    fallback included, writes a reason record (a fallback says no signal matched).
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("QueryUnderstanding: question 必须是非空字符串(空 / 纯空白 fail-loud,不做分类、不写日志)")

    qtype, matched, limitation, is_fallback = _classify(question)
    strategy = RetrievalStrategy(
        qtype=qtype,
        counterexample_boost=(qtype is QueryType.FIND_COUNTEREXAMPLE),   # only FIND_COUNTEREXAMPLE sets it
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

    log_reason("RETRIEVAL", "QueryUnderstanding 问题类型识别", reason, event_category=None, context={
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
