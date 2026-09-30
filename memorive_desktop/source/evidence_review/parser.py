"""EVIDENCE_REVIEW 搬运类回原文复核 · DS 响应解析(Verification 第 2 步)。

**mock 与真模型共用同一解析器。** 契约(②,最要紧):
- legacy schema:`{"violations":[{"rule":"R1..R9","quote":<可选短摘>,"detail":<非空>}]}`；
- production evidence schema 在每条 violation 中强制 `card_quote` / `source_quote` / `evidence_relation`，
  模型不得生成自由文本 detail；解析器由四字段确定性生成 detail。由调用方传 `require_evidence=True`
  启用；旧 mock 默认仍兼容 legacy schema。
- **well-formed 且 violations 为空列表 = 无违规**(交由上层判 PASS)。
- **坏响应绝不静默当「无违规=PASS」**:非 str / 坏 JSON / 缺 violations / 元素非法 / rule 不在 R1–R9 /
  detail 空 → **DecisionError**(可重跑环节失败;auto_review 重跑、连错 2 次升级、卡留 pending)。
- parser 只校 schema；生产 evidence 的 literal substring 门由 transcription 对真实卡值 / chunk 执行。
"""
from __future__ import annotations

import json
import re

from knowledge_admission import DecisionError   # 坏响应 = 可重跑环节失败(复用 KNOWLEDGE_ADMISSION 连错升级)

_VALID_RULES = frozenset(f"R{i}" for i in range(1, 10))          # R1..R9
_EVIDENCE_RELATIONS = frozenset({
    "direct_conflict",
    "explicit_omission",
    "missing_from_given_chunks",
})
_FENCE_RE = re.compile(r"\A\s*```(?:json)?\s*|\s*```\s*\Z", re.I)  # 容错 markdown ```json 围栏


def _first_json_object(s: str):
    """从第一个 '{' 起括号配平(跳过字符串内的括号),返回首个完整 JSON 对象文本;无 '{' → None。
    与 EVIDENCE_EXTRACTION/RESEARCH_ANALYSIS 同款——容忍模型(如 Anthropic Haiku 4.5)在 JSON 对象后追加散文说明:只取首个配平对象、忽略尾随。"""
    start = s.find("{")
    if start < 0:
        return None
    depth, instr, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def _reject_dup_keys(pairs):
    """json.loads object_pairs_hook:任一对象内出现重复键 → ValueError(已在 except → DecisionError)。
    堵独立对抗审计发现的洞:`{"violations":[真违规],"violations":[]}` 标准 json 保留**最后一个**(空)→ 假 PASS。"""
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate key {k!r}")
        seen[k] = v
    return seen


def parse_violations(response_text, *, require_evidence: bool = False) -> list:
    """解析回原文复核响应。默认兼容 legacy [{rule, quote, detail}]；生产路径可强制结构证据。
    空列表合法(= 无违规)；任何坏响应 → DecisionError(绝不当 PASS)。"""
    if not isinstance(response_text, str) or not response_text.strip():
        raise DecisionError("回原文复核响应为空 / 非字符串(不当无违规)")
    body = _FENCE_RE.sub("", response_text.strip()).strip()
    # 容错:异源 Haiku 无违规时常在 `{"violations":[]}` 后追加 markdown 说明段,严格 json.loads(整串) 会撞
    # "Extra data";取首个配平 JSON 对象、忽略尾随**纯散文**(仍须开头是合法对象,不削弱「坏响应绝不当 PASS」)。
    obj_text = _first_json_object(body)
    if obj_text is None:                                    # 无 JSON 对象(纯散文 / 空 / 括号汤)→ 交 json.loads 抛 → DecisionError
        obj_text = body
    # ⚠ 安全红线(经对抗性扫盲二次加固,airtight):首个 JSON 对象**之后一旦再出现 '{'**,一律判坏响应 → DecisionError。
    #   理由:任何藏在尾部的第二个真 violations 对象都必含 '{';纯字符存在性检测**无状态机可被转义 / 诱饵 / 未闭合串欺骗**
    #   ——`{"violations":[]} {诱饵} {"violations":[真违规]}` 之类「首个空对象遮蔽真违规」的假 PASS 无一能绕过。
    #   代价:尾随散文若含装饰性 '{'(真 Haiku 无违规输出实测不含)→ 亦判 DecisionError(升级留 pending、绝不静默 PASS)。
    elif "{" in body[body.find(obj_text) + len(obj_text):]:
        raise DecisionError("回原文复核响应在首个 JSON 对象后仍含 '{'(疑多对象 / 真违规被遮蔽),不当无违规")
    try:
        data = json.loads(obj_text, object_pairs_hook=_reject_dup_keys)   # 拒绝重复键(dup "violations" 会保留最后一个空列表 → 假 PASS)
    except (json.JSONDecodeError, ValueError, RecursionError) as e:  # ValueError 含 _reject_dup_keys 抛的重复键;RecursionError:超深嵌套
        raise DecisionError(f"回原文复核响应非合法 JSON / 含重复键:{e}(不当无违规)") from e
    # ⚠ 安全红线(独立对抗审计加固):顶层必须**恰为** {"violations": ...}、无任何其它键。
    #   契约(r1_r9_transcription_v1.md 输出段)即只此一键;若放行额外 / 别名键(如 "Violations" / " violations" /
    #   "extra"),真违规可挂旁支键下、而本函数只读 data["violations"] → 假 PASS(独立审计发现的 84 洞之一类)。
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
        if not isinstance(rule, str) or rule not in _VALID_RULES:   # isinstance 先行:防 rule 为 list/dict(unhashable)时 `in frozenset` 抛 TypeError
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
        out.append({"rule": rule, "quote": quote, "detail": detail.strip()})   # quote 只存、不做 substring 校验
    return out
