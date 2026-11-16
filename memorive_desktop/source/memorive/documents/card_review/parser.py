"""Parsing the model's reply in the transcription check (the card checked against the source).

Real models and test doubles go through the same parser:
- the legacy shape is {"violations": [{"rule": "R1".."R9", "quote": <optional short quote>, "detail": <not empty>}]};
- the production evidence shape requires card_quote, source_quote and evidence_relation in each
  violation; the model writes no free-text detail, the parser builds it from the four fields. Callers
  ask for it with require_evidence=True; tests still use the legacy shape by default.
- A well-formed reply with an empty violations list means no violations (the caller decides PASS).
- A bad reply never counts as "no violations": not a string, broken JSON, no violations key, a bad
  item, a rule outside R1 to R9, an empty detail all raise DecisionError (a retry; auto_review
  retries, hands over after two failures in a row, and the card stays pending).
The parser checks the shape only; transcription.py checks quotes against the real card and chunks.
"""
from __future__ import annotations

import json
import re

from memorive.documents.card_states import DecisionError   # a bad reply is a step failure that can be retried
from memorive.documents.card_distillation.distill import _first_json_object  # one copy, kept there

_VALID_RULES = frozenset(f"R{i}" for i in range(1, 10))          # R1..R9
_EVIDENCE_RELATIONS = frozenset({
    "direct_conflict",
    "explicit_omission",
    "missing_from_given_chunks",
})
_FENCE_RE = re.compile(r"\A\s*```(?:json)?\s*|\s*```\s*\Z", re.I)  # a ```json fence is allowed


def _reject_dup_keys(pairs):
    """object_pairs_hook for json.loads: a repeated key in any object raises ValueError (and so
    DecisionError). Plain json keeps the last one, so {"violations": [real], "violations": []} would pass."""
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate key {k!r}")
        seen[k] = v
    return seen


def parse_violations(response_text, *, require_evidence: bool = False) -> list:
    """Parse a transcription check reply. The legacy [{rule, quote, detail}] by default; production
    can require the evidence fields. An empty list means no violations; any bad reply raises DecisionError."""
    if not isinstance(response_text, str) or not response_text.strip():
        raise DecisionError("回原文复核响应为空 / 非字符串(不当无违规)")
    body = _FENCE_RE.sub("", response_text.strip()).strip()
    # Some models add prose after {"violations": []}, which json.loads of the whole text rejects as
    # "Extra data"; the first balanced JSON object is taken and trailing prose ignored (the reply must
    # still start with a valid object).
    obj_text = _first_json_object(body)
    if obj_text is None:                                    # no JSON object: json.loads raises, so DecisionError
        obj_text = body
    # Any '{' after the first JSON object makes the reply bad (DecisionError). A second violations
    #   object hidden after an empty first one must contain '{', and a plain character test cannot be
    #   fooled by escapes, decoys or unclosed strings. Prose with a decorative '{' is refused too (real
    #   replies without violations were seen to have none); the card then stays pending.
    elif "{" in body[body.find(obj_text) + len(obj_text):]:
        raise DecisionError("回原文复核响应在首个 JSON 对象后仍含 '{'(疑多对象 / 真违规被遮蔽),不当无违规")
    try:
        data = json.loads(obj_text, object_pairs_hook=_reject_dup_keys)   # repeated keys are refused (a second empty violations would win)
    except (json.JSONDecodeError, ValueError, RecursionError) as e:  # ValueError covers _reject_dup_keys; RecursionError deep nesting
        raise DecisionError(f"回原文复核响应非合法 JSON / 含重复键:{e}(不当无违规)") from e
    # The top level must be exactly {"violations": ...} with no other key, as the prompt asks. Another
    #   or look-alike key ("Violations", " violations", "extra") could carry the real violations while
    #   only data["violations"] is read.
    if not isinstance(data, dict) or set(data.keys()) != {"violations"}:
        raise DecisionError("回原文复核响应顶层须恰为 {'violations':[...]}、无其它 / 重复键(不当无违规)")
    vlist = data["violations"]
    if not isinstance(vlist, list):
        raise DecisionError("violations 非列表(不当无违规)")
    out = []
    for i, v in enumerate(vlist):
        if not isinstance(v, dict):
            raise DecisionError(f"violations[{i}] 非对象")
        if require_evidence:
            expected = {"rule", "card_quote", "source_quote", "evidence_relation"}
            if set(v.keys()) != expected:
                raise DecisionError(
                    f"violations[{i}] evidence 模式须恰含 {sorted(expected)}，"
                    f"得 {sorted(v.keys())}"
                )
        rule = v.get("rule")
        if not isinstance(rule, str) or rule not in _VALID_RULES:   # isinstance first: a list or dict would make `in frozenset` raise
            raise DecisionError(f"violations[{i}].rule 非法(须 R1–R9,得 {rule!r})")
        if require_evidence:
            card_quote = v.get("card_quote")
            if not isinstance(card_quote, str) or not card_quote:
                raise DecisionError(f"violations[{i}].card_quote 须为非空字符串")
            relation = v.get("evidence_relation")
            if not isinstance(relation, str) or relation not in _EVIDENCE_RELATIONS:
                raise DecisionError(
                    f"violations[{i}].evidence_relation 非法(得 {relation!r})"
                )
            source_quote = v.get("source_quote")
            if relation in {"direct_conflict", "explicit_omission"}:
                if not isinstance(source_quote, str) or not source_quote:
                    raise DecisionError(
                        f"violations[{i}] {relation} 的 source_quote 须为非空字符串"
                    )
                if relation == "direct_conflict":
                    detail = (
                        f"{rule}·双侧逐字证据冲突；"
                        f"卡片摘录:{card_quote}；原文摘录:{source_quote}"
                    )
                else:
                    detail = (
                        f"{rule}·双侧逐字证据显示关键内容遗漏；"
                        f"卡片摘录:{card_quote}；完整原文摘录:{source_quote}"
                    )
            elif source_quote is not None:
                raise DecisionError(
                    f"violations[{i}] missing_from_given_chunks 的 source_quote 必须为 null"
                )
            else:
                detail = (
                    "疑锚点归位·仅当前给定 chunks 未见，不能证明全篇不存在；"
                    f"卡片摘录:{card_quote}"
                )
            out.append({
                "rule": rule,
                "card_quote": card_quote,
                "source_quote": source_quote,
                "evidence_relation": relation,
                "detail": detail.strip(),
            })
            continue
        detail = v.get("detail")
        if not isinstance(detail, str) or not detail.strip():
            raise DecisionError(f"violations[{i}].detail 空 / 非字符串")
        quote = v.get("quote")
        if quote is not None and not isinstance(quote, str):
            raise DecisionError(f"violations[{i}].quote 须为字符串或缺省")
        out.append({"rule": rule, "quote": quote, "detail": detail.strip()})   # quote is stored, not checked
    return out
