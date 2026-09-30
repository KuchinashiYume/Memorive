"""EVIDENCE_REVIEW 搬运类蒸馏级校核(Verification 第 2 步-c):对照原文逐条过 R1–R9 → 组 ReviewDecision 驱动 KNOWLEDGE_ADMISSION。

流程:收集校核对象(`key_data.needs_review=true` 切入 + 遍历 by_field 实际字段)→ 全部对象与去重锚定
原文整批首审一次 → 同批最终复查一次 → parser + 本地逐字证据门 → 汇总 verdict(任一 R1–R9
违规→FAIL,否则 PASS)+ 命中 R 条 → 组 EvidenceReviewReport(含同源置信)
+ ReviewDecision → `auto_review(trigger=Trigger.EVIDENCE_REVIEW_VERDICT)` 驱动 KNOWLEDGE_ADMISSION。

守边界:**经 MODEL_GATEWAY 不直连**;**只蒸馏级、不为转换级单开**;**绝不自检**(回原文=独立异源子对话、非蒸馏模型自查);
**防同源自检**(卡 distill_model 供应商 vs verify 槽供应商同 → 同源低置信、核心字段升级异源待 Anthropic);
**坏响应 / 调用失败 = DecisionError**(auto_review 重跑、连错 2 次升级、卡留 pending),**绝不当无违规=PASS**;
粒度 = 每张 Card 固定两次(整批首审 + 整批最终复查);无字段值 / 无 chunk 的对象跳过并记 malformed、不发空 subject。
"""
from __future__ import annotations
from model_gateway.prompt_cache import policy_first, source_first

import json
import re
from difflib import SequenceMatcher
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from knowledge_admission import (AutoResult, DecisionError, ReviewDecision, Trigger, Verdict,
                              auto_review)
from model_gateway.errors import GatewayError
from runtime_log import log_reason

from . import report as _report
from .mechanical import _read_frontmatter
from .errors import BatchContractExhausted, ChunkStoreError, OwnershipFailClosed
from . import parser as _parser
from .parser import parse_violations
from .problems import Problem

# R1–R9 回原文 prompt。v10 是完整基线，v13 追加关系精度与计数召回约束；
# 重跑段只在 bypass_cache 灾难恢复轮启用。
_PROMPT_DIR = (
    Path(__file__).resolve().parent.parent
    / "model_gateway"
    / "prompts"
    / "verify"
)
_PROMPT_BASE_PATH = _PROMPT_DIR / "r1_r9_transcription_v10.md"
_PROMPT_DELTA_PATHS = (
    _PROMPT_DIR / "r1_r9_transcription_v13.md",
    _PROMPT_DIR / "r1_r9_transcription_v14.md",
    _PROMPT_DIR / "r1_r9_transcription_v15.md",
    _PROMPT_DIR / "r1_r9_transcription_v16.md",
)
_PROMPT_PATH = _PROMPT_DELTA_PATHS[-1]
_CROSS_FIELD_PROMPT_PATH = (
    _PROMPT_DIR / "r1_r9_cross_field_direct_v1.md"
)
_DIRECT_SEED_PROMPT_PATH = (
    _PROMPT_DIR / "r1_r9_direct_seed_discovery_v1.md"
)
_DIRECT_SEED_CARD_PROMPT_PATH = (
    _PROMPT_DIR / "r1_r9_direct_seed_card_expansion_v1.md"
)
_EVIDENCE_RECOVERY_PROMPT_PATH = (
    _PROMPT_DIR / "r1_r9_evidence_contract_recovery_v1.md"
)
_BATCH_INITIAL_PROMPT_PATH = (
    _PROMPT_DIR / "r1_r9_transcription_batch_v1.md"
)
_BATCH_REVIEW_PROMPT_PATH = (
    _PROMPT_DIR / "r1_r9_transcription_batch_review_v1.md"
)
_RECOVERY_START = "<!-- RECOVERY_ONLY_START -->"
_RECOVERY_END = "<!-- RECOVERY_ONLY_END -->"
_VALID_RULES = frozenset(f"R{i}" for i in range(1, 10))
# 核心字段(数值 / 结论):同源时挂「升级异源待 Anthropic」
_CORE_FIELDS = frozenset({"key_data", "key_results", "author_conclusion"})

NODE04_BATCH_MAX_SOURCE_CHARS = 48_000
NODE04_BATCH_MAX_SUBJECTS = 6
NODE04_BATCH_MAX_ITEMS = 24

_Subject = namedtuple("_Subject", "location field value_text chunk_ids")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _load_chunk_texts(card_path: Path) -> dict:
    """卡同目录唯一 [Chunks]*.jsonl → {chunk_id: text}。缺失 / 不可读 → ChunkStoreError(系统性、raise)。"""
    folder = card_path.parent
    matches = sorted(p for p in folder.glob("*.jsonl") if p.name.startswith("[Chunks]"))
    if not matches:
        raise ChunkStoreError(f"未找到 [Chunks]*.jsonl(卡同目录: {folder})")
    if len(matches) > 1:
        raise ChunkStoreError(f"卡同目录有多个 [Chunks]*.jsonl: {[m.name for m in matches]}")
    texts = {}
    try:
        for line in matches[0].read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            cid = rec.get("chunk_id")
            if isinstance(cid, str) and cid:
                texts[cid] = rec.get("text") or ""
    except (OSError, json.JSONDecodeError) as e:
        raise ChunkStoreError(f"chunk 库不可读 / 解析失败: {matches[0]}({e})") from e
    return texts


def _render_value(v) -> str:
    if isinstance(v, list):
        return "\n".join(f"- {x}" for x in v)
    if isinstance(v, dict):                      # key_data 条:JSON 保留字段边界，避免 value=>70 歧义
        rendered = {
            key: v.get(key)
            for key in ("value", "unit", "metric", "sample", "stat")
            if v.get(key) not in (None, "")
        }
        return json.dumps(rendered, ensure_ascii=False, indent=2)
    return "" if v is None else str(v)


def _by_field_chunk_ids(anchors):
    """从一个 by_field 锚点取 chunk_id 列表(v2 {chunk_ids:[...]} / v3 {chunk_id} 单对象或列表);
    形态不明 → None(上层记 malformed、不发 prompt)。与机械层同款形态识别、不猜。"""
    if isinstance(anchors, dict):
        cids = anchors.get("chunk_ids")
        if isinstance(cids, list):
            return [c for c in cids if isinstance(c, str) and c]
        if isinstance(anchors.get("chunk_id"), str):
            return [anchors["chunk_id"]]
        return None
    if isinstance(anchors, list):
        out = []
        for el in anchors:
            if isinstance(el, dict) and isinstance(el.get("chunk_id"), str):
                out.append(el["chunk_id"])
            else:
                return None
        return out
    return None


def _collect_subjects(fm: dict) -> list:
    """收集校核对象:key_data.needs_review=true 条 + 遍历 by_field 实际字段。
    粒度=每字段 / 每 key_data 条一次;只去重完全相同的(location, chunk 组)。"""
    subjects, seen = [], set()

    def add(location, field, value, chunk_ids):
        key = (location, frozenset(chunk_ids or []))
        if key in seen:                          # 只去重完全相同的(对象, chunk 组)
            return
        seen.add(key)
        subjects.append(_Subject(location, field, _render_value(value), list(chunk_ids or [])))

    # ① 切入口:key_data.needs_review=true 高风险条(每条一次)
    kd = fm.get("key_data")
    if isinstance(kd, list):
        for i, e in enumerate(kd):
            if isinstance(e, dict) and e.get("needs_review") is True:
                cid = e.get("chunk_id")
                add(f"key_data[{i}]", "key_data", e, [cid] if isinstance(cid, str) and cid else [])

    # ② 遍历 by_field 实际存在的字段(每字段一次、整组锚定 chunk)
    sa = fm.get("source_anchor")
    by_field = sa.get("by_field") if isinstance(sa, dict) else None
    if isinstance(by_field, dict):
        for fname, anchors in by_field.items():
            cids = _by_field_chunk_ids(anchors)
            add(f"by_field.{fname}", fname, fm.get(fname), cids if cids is not None else [])
    return subjects


def _vendor(model_id) -> str:
    """model_id 取供应商前缀(deepseek-v4-pro→deepseek)。Core 只 deepseek,前缀即 provider。"""
    return str(model_id or "").split("-")[0].lower()


def _expand_absence_scope(subjects, chunk_texts):
    """A claim of missing information needs the whole document, not one anchor.

    Only the review subject is widened. Original Card anchors and source text
    remain immutable; an accepted correction still needs explicit re-anchoring.
    """
    expanded = []
    for subject in subjects:
        text = subject.value_text
        absence = (
            re.search(r'\b(?:no|not|none|without)\b', text, re.I)
            and re.search(r'\b(?:report\w*|provid\w*|present\w*|given|available|contain\w*)\b', text, re.I)
            and re.search(r'\b(?:numeric\w*|data|values?|measure\w*|results?|flux|equation\w*|formula\w*)\b', text, re.I)
        ) or re.search(r'(?:未|没有|并无|不)(?:明确)?(?:报告|提供|给出|列出|包含).{0,24}(?:数据|数值|结果|公式|测量)', text)
        expanded.append(subject._replace(chunk_ids=list(chunk_texts)) if absence else subject)
    return expanded


def _same_source(distill_model, verify_provider, verify_model):
    """防同源自检(④/⑦):卡 distill_model 供应商 vs verify 槽供应商。同 → (True, reason);
    卡无 distill_model → 保守当同源(核心字段照挂升级异源)。flash 与 pro 同家族、不算异源。"""
    if not distill_model:
        return True, "卡无 distill_model 字段,保守按同源处理(核心字段照挂升级异源)"
    dv = _vendor(distill_model)
    vv = (str(verify_provider).lower() if verify_provider else "") or _vendor(verify_model)
    if dv and dv == vv:
        return True, f"distill={distill_model}({dv}) / verify={verify_model}({vv})、同供应商 / 家族(flash-pro 同家族不算异源)"
    return False, None


def _verify_slot():
    """读 verify 槽的 provider / model(防同源判定用);不发调用。"""
    from model_gateway.gateway import current_gateway
    runtime_identity = getattr(current_gateway(), 'review_identity', None)
    if callable(runtime_identity):
        return runtime_identity()
    from model_gateway.config import GatewayConfig
    import model_gateway
    cfg = GatewayConfig(Path(model_gateway.__file__).with_name("models.yaml"))
    s = cfg.slot("verify")
    return s.provider, s.model_id


_PROMPT_CACHE = {False: None, True: None}


def _prompt_template(*, recovery_mode: bool = False) -> str:
    cached = _PROMPT_CACHE[recovery_mode]
    if cached is None:
        parts = [_PROMPT_BASE_PATH.read_text(encoding="utf-8")]
        parts.extend(
            path.read_text(encoding="utf-8")
            for path in _PROMPT_DELTA_PATHS
        )
        combined = "\n\n".join(part.rstrip() for part in parts) + "\n"
        if (
            combined.count(_RECOVERY_START) != 1
            or combined.count(_RECOVERY_END) != 1
        ):
            raise RuntimeError("EVIDENCE_REVIEW prompt recovery markers must each appear once")
        if recovery_mode:
            combined = combined.replace(_RECOVERY_START, "").replace(
                _RECOVERY_END,
                "",
            )
        else:
            start = combined.index(_RECOVERY_START)
            end = combined.index(_RECOVERY_END) + len(_RECOVERY_END)
            combined = combined[:start] + combined[end:]
        cached = combined.strip() + "\n"
        _PROMPT_CACHE[recovery_mode] = cached
    return cached


def _build_prompt(
    field_name: str,
    value_text: str,
    chunk_text: str,
    *,
    recovery_mode: bool = False,
) -> str:
    return (_prompt_template(recovery_mode=recovery_mode)
            .replace("__FIELD_NAME__", field_name)
            .replace("__FIELD_VALUE__", value_text)
            .replace("__CHUNK_TEXT__", chunk_text))


_CROSS_FIELD_PROMPT_CACHE = None
_DIRECT_SEED_PROMPT_CACHE = None
_DIRECT_SEED_CARD_PROMPT_CACHE = None
_EVIDENCE_RECOVERY_PROMPT_CACHE = None
_BATCH_POLICY_CACHE = None
_BATCH_PROMPT_CACHE = {"initial": None, "review": None}


def _batch_policy_text() -> str:
    """Reuse the v10+v13+v14 rules without their single-subject I/O shell."""
    global _BATCH_POLICY_CACHE
    if _BATCH_POLICY_CACHE is None:
        base = _PROMPT_BASE_PATH.read_text(encoding="utf-8")
        start = base.index(_RECOVERY_START)
        end = base.index(_RECOVERY_END) + len(_RECOVERY_END)
        base = base[:start] + base[end:]
        input_start = base.index("## 输入")
        decision_start = base.index("## 单一判定顺序")
        output_start = base.index("## 输出")
        policy = (
            base[:input_start].rstrip()
            + "\n\n"
            + base[decision_start:output_start].rstrip()
        )
        deltas = "\n\n".join(
            path.read_text(encoding="utf-8").strip()
            for path in _PROMPT_DELTA_PATHS
        )
        policy = (policy + "\n\n" + deltas).replace(
            "__FIELD_NAME__",
            "subject.field",
        )
        unresolved = (
            "__FIELD_NAME__",
            "__FIELD_VALUE__",
            "__CHUNK_TEXT__",
        )
        if any(marker in policy for marker in unresolved):
            raise RuntimeError("EVIDENCE_REVIEW batch policy contains unresolved single-field marker")
        _BATCH_POLICY_CACHE = policy.strip()
    return _BATCH_POLICY_CACHE


def _batch_prompt_template(stage: str) -> str:
    if stage not in _BATCH_PROMPT_CACHE:
        raise ValueError(f"unknown EVIDENCE_REVIEW batch stage: {stage!r}")
    cached = _BATCH_PROMPT_CACHE[stage]
    if cached is None:
        path = (
            _BATCH_INITIAL_PROMPT_PATH
            if stage == "initial"
            else _BATCH_REVIEW_PROMPT_PATH
        )
        cached = path.read_text(encoding="utf-8")
        if cached.count("__POLICY_TEXT__") != 1:
            raise RuntimeError(f"EVIDENCE_REVIEW batch {stage} prompt policy marker must occur once")
        cached = policy_first(cached, "__POLICY_TEXT__", _batch_policy_text())
        _BATCH_PROMPT_CACHE[stage] = cached
    return cached


def _card_item_texts(value_text: str) -> list[str]:
    nonempty_lines = [line for line in value_text.splitlines() if line.strip()]
    list_items = [line[2:] for line in nonempty_lines if line.startswith("- ")]
    return (
        list_items
        if list_items and len(list_items) == len(nonempty_lines)
        else [value_text]
    )


def _build_batch_input(subjects: list, chunk_texts: dict) -> dict:
    """Build one subject list and one de-duplicated chunk map for the card."""
    source_ids = []
    seen = set()
    rendered_subjects = []
    for subject in subjects:
        item_texts = _card_item_texts(subject.value_text)
        rendered_subjects.append({
            "location": subject.location,
            "field": subject.field,
            "card_items": [
                {"item_index": index, "text": text}
                for index, text in enumerate(item_texts)
            ],
            "chunk_ids": list(subject.chunk_ids),
        })
        for chunk_id in subject.chunk_ids:
            if chunk_id not in seen:
                seen.add(chunk_id)
                source_ids.append(chunk_id)
    return {
        "subjects": rendered_subjects,
        "sources": {
            chunk_id: chunk_texts.get(chunk_id, "")
            for chunk_id in source_ids
        },
    }


def _wire_batch_input(batch_input: dict, source_context: dict) -> dict:
    """Compress repeated full-source ID lists without changing review scope.

    The validator retains the original subject IDs. Only the prompt projection
    uses a named scope, and only when it means exactly the transmitted catalog.
    A retained prior catalog must never broaden a narrower delta subject.
    """
    payload = {
        "subjects": batch_input["subjects"],
        "sources": "Use the source-id-to-text mapping in SOURCE_CONTEXT; each subject remains limited to its own chunk_ids.",
    }


def _partition_batch_subjects(
    subjects: list,
    chunk_texts: dict,
    *,
    max_source_chars: int = NODE04_BATCH_MAX_SOURCE_CHARS,
    max_subjects: int = NODE04_BATCH_MAX_SUBJECTS,
    max_items: int = NODE04_BATCH_MAX_ITEMS,
) -> list[list]:
    """Create deterministic, source-bounded review batches.

    A subject whose authorized source set exceeds the limit is reviewed in
    source shards. All shards must complete; violations from any shard are
    retained. This keeps whole-document absence checks fail-closed without one
    long connection carrying the full document.
    """
    if min(max_source_chars, max_subjects, max_items) < 1:
        raise ValueError("EVIDENCE_REVIEW_NODE04_BATCH_LIMIT_INVALID")
    per_subject_units = []
    for subject in subjects:
        shards, current, current_chars = [], [], 0
        for chunk_id in subject.chunk_ids:
            size = len(str(chunk_texts.get(chunk_id, "")))
            if current and current_chars + size > max_source_chars:
                shards.append(current)
                current, current_chars = [], 0
            current.append(chunk_id)
            current_chars += size
        if current:
            shards.append(current)
        per_subject_units.append([
            subject._replace(chunk_ids=ids) for ids in (shards or [[]])
        ])

    # Interleave equal-position source shards across fields. Absence-scope
    # subjects usually share the same whole-document chunks; source maps then
    # deduplicate inside one request and the provider can reuse the stable
    # prefix/cache instead of receiving one near-identical request per field.
    units = []
    for shard_index in range(max(map(len, per_subject_units), default=0)):
        units.extend(
            rows[shard_index]
            for rows in per_subject_units
            if shard_index < len(rows)
        )

    batches, current = [], []
    for subject in units:
        candidate = current + [subject]
        candidate_input = _build_batch_input(candidate, chunk_texts)
        source_chars = sum(len(str(text)) for text in candidate_input["sources"].values())
        item_count = sum(len(row["card_items"]) for row in candidate_input["subjects"])
        duplicate_location = any(row.location == subject.location for row in current)
        if current and (
            len(candidate) > max_subjects
            or item_count > max_items
            or source_chars > max_source_chars
            or duplicate_location
        ):
            batches.append(current)
            current = [subject]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches
    catalog_ids = set(source_context)
    projected = []
    for subject in batch_input["subjects"]:
        row = dict(subject)
        if catalog_ids and set(row["chunk_ids"]) == catalog_ids:
            row.pop("chunk_ids")
            row["source_scope_ref"] = "ALL_SOURCE_CONTEXT"
        projected.append(row)
    candidate = {
        "subjects": projected,
        "sources": "Resolve each subject's chunk_ids or source_scope_ref against SOURCE_CONTEXT. Do not widen its scope.",
        "source_scopes": {
            "ALL_SOURCE_CONTEXT": "Exactly all source IDs in the SOURCE_CONTEXT map; no omitted or external sources."
        },
    }
    encoded = lambda value: json.dumps(value, ensure_ascii=False, indent=2)
    return candidate if len(encoded(candidate)) < len(encoded(payload)) else payload


def _source_bound_batch_prompt(template: str, batch_input: dict, *, prior_source_context=None) -> str:
    """Reuse only this batch's already-authorized sources, keeping per-item anchors."""
    if set(batch_input) != {"subjects", "sources"}:
        raise ValueError("EVIDENCE_REVIEW_BATCH_CACHE_INPUT_SHAPE_INVALID")
    policy = _batch_policy_text()
    if not template.startswith(policy):
        raise ValueError("EVIDENCE_REVIEW_BATCH_CACHE_POLICY_PREFIX_MISMATCH")
    sources = batch_input["sources"]
    if prior_source_context is not None and all(
        cid in prior_source_context and prior_source_context[cid] == value
        for cid, value in sources.items()
    ):
        sources = prior_source_context
    task = template[len(policy):].lstrip().replace("__BATCH_INPUT_JSON__", json.dumps(
        _wire_batch_input(batch_input, sources), ensure_ascii=False, indent=2))
    coverage = {
        "checked_locations": [row["location"] for row in batch_input["subjects"]],
        "checked_items": [{"location": row["location"],
            "item_indexes": [item["item_index"] for item in row["card_items"]]}
            for row in batch_input["subjects"]],
    }
    task += ("\n\nMandatory output coverage: copy both metadata arrays below exactly into the result. "
        "Separately return one item_reviews judgment for every listed location/item_index pair, "
        "including research_question and subjects with empty anchor lists. The unchanged evidence "
        "policy governs each judgment. Metadata alone never substitutes for an item judgment.\n"
        + json.dumps(coverage, ensure_ascii=False, separators=(",", ":")))
    # The full unchanged policy and exact authorized source catalog are stable.
    # A targeted Delta can retain the exact catalog already sent in its Full
    # review. Its subjects and validators remain restricted to current chunk_ids.
    # New or changed anchors start a fresh catalog instead of inheriting stale text.
    source = json.dumps(sources, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return policy + "\n\n" + source_first("__CACHE_SOURCE_MAP__\n" + task, "__CACHE_SOURCE_MAP__", source)


def _build_batch_initial_prompt(batch_input: dict) -> str:
    template = _batch_prompt_template("initial")
    if template.count("__BATCH_INPUT_JSON__") != 1:
        raise RuntimeError("EVIDENCE_REVIEW batch initial input marker must occur once")
    return _source_bound_batch_prompt(template, batch_input)


def _build_batch_review_prompt(
    batch_input: dict,
    initial_rows: list[dict],
    *, prior_source_context=None,
) -> str:
    template = _batch_prompt_template("review")
    if (
        template.count("__BATCH_INPUT_JSON__") != 1
        or template.count("__INITIAL_RESULT_JSON__") != 1
    ):
        raise RuntimeError("EVIDENCE_REVIEW batch review markers must each occur once")
    violating_items = {
        (row["location"], row["item_index"])
        for row in initial_rows
    }
    initial_result = {
        "checked_locations": [
            subject["location"] for subject in batch_input["subjects"]
        ],
        "checked_items": [
            {
                "location": subject["location"],
                "item_indexes": [
                    item["item_index"] for item in subject["card_items"]
                ],
            }
            for subject in batch_input["subjects"]
        ],
        "item_reviews": [
            {
                "location": subject["location"],
                "item_index": item["item_index"],
                "status": (
                    "violation"
                    if (subject["location"], item["item_index"])
                    in violating_items
                    else "supported"
                ),
            }
            for subject in batch_input["subjects"]
            for item in subject["card_items"]
        ],
        "violations": [
            {
                key: row[key]
                for key in (
                    "location",
                    "item_index",
                    "rule",
                    "card_quote",
                    "source_quote",
                    "evidence_relation",
                )
            }
            for row in initial_rows
        ],
    }
    return _source_bound_batch_prompt(template.replace(
        "__INITIAL_RESULT_JSON__", json.dumps(initial_result, ensure_ascii=False, indent=2)), batch_input,
        prior_source_context=prior_source_context)


def _parse_batch_response(
    response_text: str,
    expected_locations: list[str],
    expected_items: dict[str, list[int]],
) -> list[dict]:
    """Strict batch shell plus the existing strict four-field evidence parser."""
    if not isinstance(response_text, str) or not response_text.strip():
        raise DecisionError("EVIDENCE_REVIEW 整批响应为空 / 非字符串")
    body = _parser._FENCE_RE.sub("", response_text.strip()).strip()
    obj_text = _parser._first_json_object(body)
    if obj_text is None:
        obj_text = body
    elif "{" in body[body.find(obj_text) + len(obj_text):]:
        raise DecisionError("EVIDENCE_REVIEW 整批响应在首个 JSON 对象后仍含第二对象")
    try:
        data = json.loads(obj_text, object_pairs_hook=_parser._reject_dup_keys)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise DecisionError(f"EVIDENCE_REVIEW 整批响应非合法 JSON / 含重复键:{exc}") from exc
    if not isinstance(data, dict) or set(data) != {
        "checked_locations",
        "checked_items",
        "item_reviews",
        "violations",
    }:
        raise DecisionError(
            "EVIDENCE_REVIEW 整批响应顶层须恰含 checked_locations / checked_items / item_reviews / violations"
        )
    checked = data["checked_locations"]
    if (
        not isinstance(checked, list)
        or any(not isinstance(location, str) for location in checked)
        or len(checked) != len(set(checked))
        or set(checked) != set(expected_locations)
        or len(checked) != len(expected_locations)
    ):
        raise DecisionError("EVIDENCE_REVIEW 整批响应未恰好覆盖全部 expected locations")
    checked_items = data["checked_items"]
    if not isinstance(checked_items, list):
        raise DecisionError("EVIDENCE_REVIEW 整批 checked_items 非列表")
    actual_items = {}
    for index, row in enumerate(checked_items):
        if (
            not isinstance(row, dict)
            or set(row) != {"location", "item_indexes"}
        ):
            raise DecisionError(
                f"EVIDENCE_REVIEW 整批 checked_items[{index}] 须恰含 location / item_indexes"
            )
        location = row["location"]
        item_indexes = row["item_indexes"]
        if (
            not isinstance(location, str)
            or location not in expected_items
            or location in actual_items
            or not isinstance(item_indexes, list)
            or any(
                not isinstance(item_index, int) or isinstance(item_index, bool)
                for item_index in item_indexes
            )
            or len(item_indexes) != len(set(item_indexes))
        ):
            raise DecisionError(
                f"EVIDENCE_REVIEW 整批 checked_items[{index}] location / item_indexes 非法"
            )
        actual_items[location] = item_indexes
    if set(actual_items) != set(expected_items) or any(
        actual_items[location] != expected_items[location]
        for location in expected_items
    ):
        raise DecisionError("EVIDENCE_REVIEW 整批响应未按顺序覆盖全部 item_indexes")
    expected_item_keys = {
        (location, item_index)
        for location, item_indexes in expected_items.items()
        for item_index in item_indexes
    }
    item_reviews = data["item_reviews"]
    if not isinstance(item_reviews, list):
        raise DecisionError("EVIDENCE_REVIEW 整批 item_reviews 非列表")
    actual_reviews = {}
    for index, row in enumerate(item_reviews):
        if (
            not isinstance(row, dict)
            or set(row) != {"location", "item_index", "status"}
        ):
            raise DecisionError(
                f"EVIDENCE_REVIEW 整批 item_reviews[{index}] 须恰含 location / item_index / status"
            )
        key = (row["location"], row["item_index"])
        if (
            key not in expected_item_keys
            or key in actual_reviews
            or row["status"] not in {"supported", "violation"}
        ):
            raise DecisionError(
                f"EVIDENCE_REVIEW 整批 item_reviews[{index}] location / item_index / status 非法"
            )
        actual_reviews[key] = row["status"]
    if set(actual_reviews) != expected_item_keys:
        raise DecisionError("EVIDENCE_REVIEW 整批 item_reviews 未恰好覆盖全部 items")
    rows = data["violations"]
    if not isinstance(rows, list):
        raise DecisionError("EVIDENCE_REVIEW 整批 violations 非列表")
    item_keys = []
    evidence_rows = []
    expected = set(expected_locations)
    evidence_keys = {
        "rule",
        "card_quote",
        "source_quote",
        "evidence_relation",
    }
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != evidence_keys | {
            "location",
            "item_index",
        }:
            raise DecisionError(
                f"EVIDENCE_REVIEW 整批 violations[{index}] 须恰含 location / item_index + 四字段证据"
            )
        location = row["location"]
        item_index = row["item_index"]
        if (
            not isinstance(location, str)
            or location not in expected
            or not isinstance(item_index, int)
            or isinstance(item_index, bool)
            or item_index not in expected_items[location]
        ):
            raise DecisionError(
                f"EVIDENCE_REVIEW 整批 violations[{index}] location / item_index 不属于待核集合"
            )
        item_keys.append((location, item_index))
        evidence = {key: row[key] for key in evidence_keys}
        if (
            evidence["evidence_relation"]
            in {"direct_conflict", "explicit_omission"}
            and not evidence["source_quote"]
        ):
            # A model verdict without literal source-side evidence cannot be
            # accepted as a direct conflict or omission. Preserve the item as
            # a violation, but contain the invalid evidence to this one item by
            # degrading it to the existing fail-closed R8 relation. The later
            # validator still binds the Card quote and drops the affected item.
            evidence["evidence_relation"] = "missing_from_given_chunks"
            evidence["source_quote"] = None
        evidence_rows.append(evidence)
    parsed = parse_violations(
        json.dumps({"violations": evidence_rows}, ensure_ascii=False),
        require_evidence=True,
    )
    violating_items = set(item_keys)
    if any(
        actual_reviews[key]
        != ("violation" if key in violating_items else "supported")
        for key in expected_item_keys
    ):
        raise DecisionError("EVIDENCE_REVIEW 整批 item_reviews 与 violations 不一致")
    parsed_rows = [
        {"location": key[0], "item_index": key[1], **violation}
        for key, violation in zip(item_keys, parsed)
    ]
    return parsed_rows


def _invalid_evidence_feedback(row, subject, chunk_texts, error) -> str:
    """Localize a failed contract; search hints never become accepted evidence."""
    quote = row.get("source_quote")
    candidates = []
    if isinstance(quote, str) and quote:
        for chunk_id in subject.chunk_ids:
            source = chunk_texts.get(chunk_id, "")
            match = SequenceMatcher(None, quote, source, autojunk=False).find_longest_match()
            if match.size < min(24, max(8, len(quote) // 2)):
                continue
            start = max(0, match.b - 160)
            end = min(len(source), match.b + match.size + 160)
            candidates.append((match.size, chunk_id, start, end, source[start:end]))
    hints = [
        {"chunk_id": cid, "start_offset": start, "end_offset": end, "literal_excerpt": text}
        for _, cid, start, end, text in sorted(candidates, key=lambda x: (-x[0], x[1]))[:3]
    ]
    detail = {
        "location": row.get("location"), "item_index": row.get("item_index"),
        "rejected_evidence": row, "anchored_source_search_hints": hints,
    }
    return (
        f"{error}. Invalid evidence details: {json.dumps(detail, ensure_ascii=False)}. "
        "Search hints are literal excerpts from this subject's authorized chunks, not a verdict "
        "or an automatically accepted replacement. Re-extract a continuous verbatim quote "
        "from the original source, with no inserted or omitted words, numbers or signs. "
        "Return the complete same review bundle; preserve genuine violations."
    )


def _validate_batch_rows(
    rows: list[dict],
    subjects_by_location: dict,
    chunk_texts: dict,
) -> list[Problem]:
    problems = []
    for row in rows:
        subject = subjects_by_location[row["location"]]
        chunk_text = "\n\n".join(
            chunk_texts.get(chunk_id, "")
            for chunk_id in subject.chunk_ids
        ).strip()
        try:
            validated = _validate_violation_evidence(row, subject.value_text, chunk_text)
        except DecisionError as error:
            raise DecisionError(_invalid_evidence_feedback(row, subject, chunk_texts, error)) from error
        item_texts = _card_item_texts(subject.value_text)
        item_index = row["item_index"]
        if validated["card_quote"] not in item_texts[item_index]:
            raise DecisionError(
                _invalid_evidence_feedback(row, subject, chunk_texts,
                    "EVIDENCE_REVIEW 整批 violation.card_quote 不属于声明的 item_index")
            )
        evidence_quote = json.dumps({
            "item_index": item_index,
            "card_quote": validated["card_quote"],
            "source_quote": validated["source_quote"],
            "evidence_relation": validated["evidence_relation"],
        }, ensure_ascii=False)
        problems.append(Problem(
            source="transcription",
            severity="error",
            kind=validated["rule"],
            location=subject.location,
            field=subject.field,
            chunk_id=subject.chunk_ids[0] if subject.chunk_ids else None,
            detail=f"item_index={item_index}；{validated['detail']}",
            quote=evidence_quote,
        ))
    return problems


def _build_cross_field_direct_prompt(
    rule: str,
    field_name: str,
    value_text: str,
    seed_card_quote: str,
    source_quote: str,
    chunk_text: str,
) -> str:
    """Build the narrow second-pass prompt for one validated source quote."""
    global _CROSS_FIELD_PROMPT_CACHE
    if _CROSS_FIELD_PROMPT_CACHE is None:
        _CROSS_FIELD_PROMPT_CACHE = _CROSS_FIELD_PROMPT_PATH.read_text(
            encoding="utf-8"
        )
    return (
        _CROSS_FIELD_PROMPT_CACHE
        .replace("__RULE__", rule)
        .replace("__FIELD_NAME__", field_name)
        .replace("__FIELD_VALUE__", value_text)
        .replace("__SEED_CARD_QUOTE__", seed_card_quote)
        .replace("__SOURCE_QUOTE__", source_quote)
        .replace("__CHUNK_TEXT__", chunk_text)
    )


def _build_direct_seed_discovery_prompt(
    subjects: list,
    chunk_texts: dict,
) -> str:
    """Build one card-level prompt from already checked by_field subjects."""
    global _DIRECT_SEED_PROMPT_CACHE
    if _DIRECT_SEED_PROMPT_CACHE is None:
        _DIRECT_SEED_PROMPT_CACHE = _DIRECT_SEED_PROMPT_PATH.read_text(
            encoding="utf-8"
        )
    blocks = []
    for index, subject in enumerate(subjects, start=1):
        chunk_text = "\n\n".join(
            chunk_texts.get(chunk_id, "")
            for chunk_id in subject.chunk_ids
        ).strip()
        blocks.append(
            f"### SUBJECT {index}\n"
            f"- location: `{subject.location}`\n"
            f"- field: `{subject.field}`\n\n"
            "#### 当前 Card 字段\n\n"
            f"{subject.value_text}\n\n"
            "#### 该字段自己的锚定原文 chunks\n\n"
            f"{chunk_text}"
        )
    return _DIRECT_SEED_PROMPT_CACHE.replace(
        "__SUBJECT_BLOCKS__",
        "\n\n".join(blocks),
    )


def _build_direct_seed_card_expansion_prompt(
    rule: str,
    seed_card_quote: str,
    source_quote: str,
    subjects: list,
    chunk_texts: dict,
) -> str:
    """Build one full-card expansion prompt for a discovered seed."""
    global _DIRECT_SEED_CARD_PROMPT_CACHE
    if _DIRECT_SEED_CARD_PROMPT_CACHE is None:
        _DIRECT_SEED_CARD_PROMPT_CACHE = (
            _DIRECT_SEED_CARD_PROMPT_PATH.read_text(encoding="utf-8")
        )
    blocks = []
    for index, subject in enumerate(subjects, start=1):
        chunk_text = "\n\n".join(
            chunk_texts.get(chunk_id, "")
            for chunk_id in subject.chunk_ids
        ).strip()
        blocks.append(
            f"### TARGET SUBJECT {index}\n"
            f"- location: `{subject.location}`\n"
            f"- field: `{subject.field}`\n\n"
            "#### 当前 Card 字段\n\n"
            f"{subject.value_text}\n\n"
            "#### 该字段自己的锚定原文 chunks\n\n"
            f"{chunk_text}"
        )
    return (
        _DIRECT_SEED_CARD_PROMPT_CACHE
        .replace("__RULE__", rule)
        .replace("__SEED_CARD_QUOTE__", seed_card_quote)
        .replace("__SOURCE_QUOTE__", source_quote)
        .replace("__TARGET_SUBJECT_BLOCKS__", "\n\n".join(blocks))
    )


def _build_evidence_contract_recovery_prompt(
    original_prompt: str,
    invalid_response: str,
    label: str,
) -> str:
    """Build one semantic-preserving retry for an invalid evidence schema."""
    global _EVIDENCE_RECOVERY_PROMPT_CACHE
    if _EVIDENCE_RECOVERY_PROMPT_CACHE is None:
        _EVIDENCE_RECOVERY_PROMPT_CACHE = (
            _EVIDENCE_RECOVERY_PROMPT_PATH.read_text(encoding="utf-8")
        )
    return (
        _EVIDENCE_RECOVERY_PROMPT_CACHE
        .replace("__RECOVERY_LABEL__", label)
        .replace("__ORIGINAL_TASK__", original_prompt)
        .replace("__INVALID_RESPONSE__", invalid_response)
    )


def _parse_with_one_evidence_contract_recovery(
    response: str,
    original_prompt: str,
    call,
    label: str,
) -> list[dict]:
    """Retry one malformed model contract once; never repair evidence locally."""
    try:
        return parse_violations(response, require_evidence=True)
    except DecisionError as first_error:
        recovery_response = call(_build_evidence_contract_recovery_prompt(
            original_prompt,
            response,
            label,
        ))
        try:
            return parse_violations(
                recovery_response,
                require_evidence=True,
            )
        except DecisionError as second_error:
            raise DecisionError(
                f"EVIDENCE_REVIEW {label} 证据契约恢复 1 次后仍失败: {second_error}"
            ) from second_error


def _is_cjk_character(char: str) -> bool:
    """仅识别 CJK 汉字；空格回填不得跨英文词界、数字或标点。"""
    if not char:
        return False
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def _normalize_cjk_pdf_whitespace(text: str) -> tuple[str, list[int]]:
    """只删除两侧均为汉字的 Unicode 空白，并保留规范化字符到原文偏移的映射。"""
    normalized, offsets = [], []
    index = 0
    while index < len(text):
        if not text[index].isspace():
            normalized.append(text[index])
            offsets.append(index)
            index += 1
            continue
        run_end = index + 1
        while run_end < len(text) and text[run_end].isspace():
            run_end += 1
        left = text[index - 1] if index > 0 else ""
        right = text[run_end] if run_end < len(text) else ""
        if not (_is_cjk_character(left) and _is_cjk_character(right)):
            for offset in range(index, run_end):
                normalized.append(text[offset])
                offsets.append(offset)
        index = run_end
    return "".join(normalized), offsets


def _recover_unique_cjk_pdf_quote(source_quote: str, chunk_text: str) -> str | None:
    """仅当中文词内空格规范化后在 chunk 唯一命中，回填原文真实连续子串。"""
    normalized_quote, _ = _normalize_cjk_pdf_whitespace(source_quote)
    normalized_chunk, offsets = _normalize_cjk_pdf_whitespace(chunk_text)
    if (
        not normalized_quote
        or sum(not char.isspace() for char in normalized_quote) < 8
    ):
        return None
    match_start = normalized_chunk.find(normalized_quote)
    if match_start < 0 or normalized_chunk.find(normalized_quote, match_start + 1) >= 0:
        return None
    match_end = match_start + len(normalized_quote) - 1
    if match_end >= len(offsets):
        return None
    candidate = chunk_text[offsets[match_start]:offsets[match_end] + 1]
    candidate_normalized, _ = _normalize_cjk_pdf_whitespace(candidate)
    if candidate_normalized != normalized_quote or candidate not in chunk_text:
        return None
    return candidate


def _recover_unique_pdf_whitespace_quote(source_quote: str, chunk_text: str) -> str | None:
    """Recover one literal source span differing only in whitespace runs.

    Keep word boundaries, every non-whitespace character, and source offsets.
    Never repair spelling, punctuation, numbers, signs, or a missing word.
    Ambiguous normalized matches stay invalid rather than choosing evidence.
    """
    def normalize(text):
        chars, offsets = [], []
        for index, char in enumerate(text):
            if char.isspace():
                if chars and chars[-1] == " ":
                    continue
                char = " "
            chars.append(char)
            offsets.append(index)
        return "".join(chars), offsets

    quote, _ = normalize(source_quote)
    source, offsets = normalize(chunk_text)
    if sum(not char.isspace() for char in quote) < 8:
        return None
    start = source.find(quote)
    if start < 0 or source.find(quote, start + 1) >= 0:
        return None
    candidate = chunk_text[offsets[start]:offsets[start + len(quote) - 1] + 1]
    return candidate if normalize(candidate)[0] == quote else None


def _validate_violation_evidence(violation: dict, value_text: str, chunk_text: str) -> dict:
    """验证模型的两侧逐字证据；仅当前 chunks 未见时强制降为 R8 疑锚点归位。"""
    card_quote = violation.get("card_quote")
    if not isinstance(card_quote, str) or not card_quote or card_quote not in value_text:
        raise DecisionError("EVIDENCE_REVIEW evidence.card_quote 不是卡上内容的连续逐字子串(不落错误信用档)")

    relation = violation.get("evidence_relation")
    source_quote = violation.get("source_quote")
    detail = str(violation.get("detail") or "").strip()
    if relation == "missing_from_given_chunks":
        if source_quote is not None:
            raise DecisionError("EVIDENCE_REVIEW missing_from_given_chunks 的 source_quote 必须为 null")
        if not detail.startswith("疑锚点归位"):
            detail = ("疑锚点归位·仅当前给定 chunks 未见，不能证明全篇不存在；"
                      + detail)
        return {
            "rule": "R8",
            "card_quote": card_quote,
            "source_quote": None,
            "evidence_relation": relation,
            "detail": detail,
        }

    if relation not in {"direct_conflict", "explicit_omission"}:
        raise DecisionError(f"EVIDENCE_REVIEW evidence_relation 非法:{relation!r}")
    if not isinstance(source_quote, str) or not source_quote:
        raise DecisionError("EVIDENCE_REVIEW evidence.source_quote 不是锚定原文的连续逐字子串(不落错误信用档)")
    if source_quote not in chunk_text:
        source_quote = (
            _recover_unique_cjk_pdf_quote(source_quote, chunk_text)
            or _recover_unique_pdf_whitespace_quote(source_quote, chunk_text)
        )
        if source_quote is None:
            raise DecisionError("EVIDENCE_REVIEW evidence.source_quote 不是锚定原文的连续逐字子串(不落错误信用档)")
    if relation == "explicit_omission":
        return {
            "rule": violation["rule"],
            "card_quote": card_quote,
            "source_quote": source_quote,
            "evidence_relation": relation,
            "detail": (
                f"{violation['rule']}·双侧逐字证据显示关键内容遗漏；"
                f"卡片摘录:{card_quote}；完整原文摘录:{source_quote}"
            ),
        }

    source_start = chunk_text.find(source_quote)
    card_elsewhere = (
        source_start >= 0
        and (
            card_quote in chunk_text[:source_start]
            or card_quote in chunk_text[source_start + len(source_quote):]
        )
    )
    if card_elsewhere:
        detail = (
            f"{violation['rule']}·锚定原文内部冲突；"
            "卡片摘录也逐字出现于冲突摘录之外的同一组锚定原文；"
            f"卡片摘录:{card_quote}；冲突原文摘录:{source_quote}"
        )
    else:
        detail = (
            f"{violation['rule']}·双侧逐字证据冲突；"
            f"卡片摘录:{card_quote}；原文摘录:{source_quote}"
        )
    return {
        "rule": violation["rule"],
        "card_quote": card_quote,
        "source_quote": source_quote,
        "evidence_relation": relation,
        "detail": detail,
    }


def _propagate_literal_direct_conflicts(
    problems: list[Problem],
    subjects: list,
    chunk_texts: dict,
    checked_locations: list[str],
) -> list[Problem]:
    """Reuse an already validated direct conflict across exact checked fields.

    Only by_field subjects participate. Both the same card_quote and the same
    source_quote must be literal substrings in the target field/value and its
    own anchored chunks. Omission/missing relations and key_data never spread.
    """
    out = list(problems)
    checked = set(checked_locations)
    existing = set()
    occupied_direct_source = set()
    seeds = []
    for problem in problems:
        if (
            problem.source != "transcription"
            or problem.kind not in _VALID_RULES
            or not isinstance(problem.quote, str)
        ):
            continue
        try:
            evidence = json.loads(problem.quote)
        except (json.JSONDecodeError, TypeError):
            continue
        key = (
            problem.location,
            problem.kind,
            evidence.get("card_quote"),
            evidence.get("source_quote"),
            evidence.get("evidence_relation"),
        )
        existing.add(key)
        if evidence.get("evidence_relation") == "direct_conflict":
            occupied_direct_source.add((
                problem.location,
                evidence.get("source_quote"),
            ))
            seeds.append((problem, evidence))

    for problem, evidence in seeds:
        card_quote = evidence.get("card_quote")
        source_quote = evidence.get("source_quote")
        if not isinstance(card_quote, str) or not isinstance(source_quote, str):
            continue
        for subject in subjects:
            if (
                subject.location not in checked
                or not subject.location.startswith("by_field.")
            ):
                continue
            chunk_text = "\n\n".join(
                chunk_texts.get(chunk_id, "")
                for chunk_id in subject.chunk_ids
            ).strip()
            if card_quote not in subject.value_text or source_quote not in chunk_text:
                continue
            if (subject.location, source_quote) in occupied_direct_source:
                continue
            key = (
                subject.location,
                problem.kind,
                card_quote,
                source_quote,
                "direct_conflict",
            )
            if key in existing:
                continue
            normalized = _validate_violation_evidence(
                {
                    "rule": problem.kind,
                    "card_quote": card_quote,
                    "source_quote": source_quote,
                    "evidence_relation": "direct_conflict",
                    "detail": problem.detail,
                },
                subject.value_text,
                chunk_text,
            )
            evidence_quote = json.dumps({
                "card_quote": normalized["card_quote"],
                "source_quote": normalized["source_quote"],
                "evidence_relation": normalized["evidence_relation"],
            }, ensure_ascii=False)
            out.append(Problem(
                source="transcription",
                severity="error",
                kind=problem.kind,
                location=subject.location,
                field=subject.field,
                chunk_id=subject.chunk_ids[0] if subject.chunk_ids else None,
                detail=(
                    "同卡跨字段逐字证据传播；" + normalized["detail"]
                ),
                quote=evidence_quote,
            ))
            existing.add(key)
            occupied_direct_source.add((subject.location, source_quote))
    return out


def _discover_direct_conflict_seed(
    problems: list[Problem],
    subjects: list,
    chunk_texts: dict,
    checked_locations: list[str],
    call,
) -> list[Problem]:
    """Make one card-level call for all conflicts when no by_field seed exists."""
    for problem in problems:
        if (
            problem.source != "transcription"
            or problem.kind not in _VALID_RULES
            or not problem.location.startswith("by_field.")
            or not isinstance(problem.quote, str)
        ):
            continue
        try:
            evidence = json.loads(problem.quote)
        except (json.JSONDecodeError, TypeError):
            continue
        if evidence.get("evidence_relation") == "direct_conflict":
            return list(problems)

    checked = set(checked_locations)
    eligible = []
    for subject in subjects:
        if (
            subject.location not in checked
            or not subject.location.startswith("by_field.")
        ):
            continue
        chunk_text = "\n\n".join(
            chunk_texts.get(chunk_id, "")
            for chunk_id in subject.chunk_ids
        ).strip()
        if subject.value_text.strip() and chunk_text:
            eligible.append(subject)
    if not eligible:
        return list(problems)

    prompt = _build_direct_seed_discovery_prompt(eligible, chunk_texts)
    response = call(prompt)
    parsed_rows = _parse_with_one_evidence_contract_recovery(
        response,
        prompt,
        call,
        "无 seed 卡级发现",
    )
    if len(parsed_rows) > 1:
        raise DecisionError("EVIDENCE_REVIEW 无 seed 卡级聚焦发现最多允许 1 条 violation")
    if not parsed_rows:
        return list(problems)

    out = list(problems)
    used_locations = set()
    for parsed in parsed_rows:
        if (
            parsed.get("rule") not in _VALID_RULES
            or parsed.get("evidence_relation") != "direct_conflict"
        ):
            raise DecisionError(
                "EVIDENCE_REVIEW 无 seed 卡级聚焦发现只允许 R1-R9 direct_conflict"
            )
        card_quote = parsed.get("card_quote")
        source_quote = parsed.get("source_quote")
        matching_subject = None
        matching_chunk_text = None
        if isinstance(card_quote, str) and isinstance(source_quote, str):
            for subject in eligible:
                if subject.location in used_locations:
                    continue
                chunk_text = "\n\n".join(
                    chunk_texts.get(chunk_id, "")
                    for chunk_id in subject.chunk_ids
                ).strip()
                if card_quote in subject.value_text and source_quote in chunk_text:
                    matching_subject = subject
                    matching_chunk_text = chunk_text
                    break
        if matching_subject is None or matching_chunk_text is None:
            raise DecisionError(
                "EVIDENCE_REVIEW 无 seed 卡级聚焦发现的双侧引语未逐字落在同一已校核字段及其锚定原文"
            )

        validated = _validate_violation_evidence(
            parsed,
            matching_subject.value_text,
            matching_chunk_text,
        )
        evidence_quote = json.dumps({
            "card_quote": validated["card_quote"],
            "source_quote": validated["source_quote"],
            "evidence_relation": validated["evidence_relation"],
        }, ensure_ascii=False)
        out.append(Problem(
            source="transcription",
            severity="error",
            kind=validated["rule"],
            location=matching_subject.location,
            field=matching_subject.field,
            chunk_id=(
                matching_subject.chunk_ids[0]
                if matching_subject.chunk_ids else None
            ),
            detail="无 seed 卡级聚焦发现；" + validated["detail"],
            quote=evidence_quote,
        ))
        used_locations.add(matching_subject.location)
    return out


def _expand_discovered_seed_card_level(
    problems: list[Problem],
    subjects: list,
    chunk_texts: dict,
    checked_locations: list[str],
    call,
) -> list[Problem]:
    """Expand one discovered seed across all remaining fields in one call."""
    seeds = []
    occupied_source = set()
    for problem in problems:
        if (
            problem.source != "transcription"
            or problem.kind not in _VALID_RULES
            or not problem.location.startswith("by_field.")
            or not isinstance(problem.quote, str)
        ):
            continue
        try:
            evidence = json.loads(problem.quote)
        except (json.JSONDecodeError, TypeError):
            continue
        if evidence.get("evidence_relation") != "direct_conflict":
            continue
        source_quote = evidence.get("source_quote")
        if isinstance(source_quote, str):
            occupied_source.add((problem.location, source_quote))
        if problem.detail.startswith("无 seed 卡级聚焦发现；"):
            seeds.append((problem, evidence))
    if not seeds:
        return list(problems)
    if len(seeds) != 1:
        raise DecisionError("EVIDENCE_REVIEW 无 seed 卡级扩展必须恰有 1 个已验证 seed")

    seed, evidence = seeds[0]
    seed_card_quote = evidence.get("card_quote")
    source_quote = evidence.get("source_quote")
    if not isinstance(seed_card_quote, str) or not isinstance(source_quote, str):
        raise DecisionError("EVIDENCE_REVIEW 无 seed 卡级扩展 seed 双侧证据非法")

    checked = set(checked_locations)
    eligible = []
    for subject in subjects:
        if (
            subject.location not in checked
            or not subject.location.startswith("by_field.")
            or (subject.location, source_quote) in occupied_source
        ):
            continue
        chunk_text = "\n\n".join(
            chunk_texts.get(chunk_id, "")
            for chunk_id in subject.chunk_ids
        ).strip()
        if (
            subject.value_text.strip()
            and chunk_text
            and source_quote in chunk_text
        ):
            eligible.append(subject)
    if not eligible:
        return list(problems)

    prompt = _build_direct_seed_card_expansion_prompt(
        seed.kind,
        seed_card_quote,
        source_quote,
        eligible,
        chunk_texts,
    )
    response = call(prompt)
    parsed_rows = _parse_with_one_evidence_contract_recovery(
        response,
        prompt,
        call,
        "无 seed 卡级全字段扩展",
    )
    if len(parsed_rows) > len(eligible):
        raise DecisionError(
            "EVIDENCE_REVIEW 无 seed 卡级扩展不得超过候选 by_field 数量"
        )

    out = list(problems)
    used_locations = set()
    for parsed in parsed_rows:
        if (
            parsed.get("rule") != seed.kind
            or parsed.get("evidence_relation") != "direct_conflict"
            or parsed.get("source_quote") != source_quote
        ):
            raise DecisionError(
                "EVIDENCE_REVIEW 无 seed 卡级扩展必须保持同规则、direct_conflict "
                "与完整已验证 source_quote"
            )
        card_quote = parsed.get("card_quote")
        matching_subject = None
        matching_chunk_text = None
        if isinstance(card_quote, str):
            for subject in eligible:
                if subject.location in used_locations:
                    continue
                chunk_text = "\n\n".join(
                    chunk_texts.get(chunk_id, "")
                    for chunk_id in subject.chunk_ids
                ).strip()
                if card_quote in subject.value_text and source_quote in chunk_text:
                    matching_subject = subject
                    matching_chunk_text = chunk_text
                    break
        if matching_subject is None or matching_chunk_text is None:
            raise DecisionError(
                "EVIDENCE_REVIEW 无 seed 卡级扩展的双侧引语未逐字落在同一候选字段及其锚定原文"
            )
        validated = _validate_violation_evidence(
            parsed,
            matching_subject.value_text,
            matching_chunk_text,
        )
        evidence_quote = json.dumps({
            "card_quote": validated["card_quote"],
            "source_quote": validated["source_quote"],
            "evidence_relation": validated["evidence_relation"],
        }, ensure_ascii=False)
        out.append(Problem(
            source="transcription",
            severity="error",
            kind=validated["rule"],
            location=matching_subject.location,
            field=matching_subject.field,
            chunk_id=(
                matching_subject.chunk_ids[0]
                if matching_subject.chunk_ids else None
            ),
            detail="无 seed 卡级全字段扩展；" + validated["detail"],
            quote=evidence_quote,
        ))
        used_locations.add(matching_subject.location)
    return out


def _verify_cross_field_direct_conflicts(
    problems: list[Problem],
    subjects: list,
    chunk_texts: dict,
    checked_locations: list[str],
    call,
) -> list[Problem]:
    """Focus-recheck semantic cross-field gaps without local synonym rules.

    A request is eligible only when an already literal-validated by_field
    direct conflict supplies the exact source quote, the target field was
    checked, and that same source quote occurs in the target's own anchors.
    Exact card-quote matches are left to literal propagation. The focused
    model may return either no violation or one same-rule direct conflict;
    both quotes are validated again before a Problem is admitted.
    """
    if any(
        problem.detail.startswith("无 seed 卡级聚焦发现；")
        for problem in problems
    ):
        return list(problems)
    out = list(problems)
    checked = set(checked_locations)
    occupied_source = set()
    seeds = []
    for problem in problems:
        if (
            problem.source != "transcription"
            or problem.kind not in _VALID_RULES
            or not problem.location.startswith("by_field.")
            or not isinstance(problem.quote, str)
        ):
            continue
        try:
            evidence = json.loads(problem.quote)
        except (json.JSONDecodeError, TypeError):
            continue
        if evidence.get("evidence_relation") != "direct_conflict":
            continue
        card_quote = evidence.get("card_quote")
        source_quote = evidence.get("source_quote")
        if not isinstance(card_quote, str) or not isinstance(source_quote, str):
            continue
        occupied_source.add((problem.location, source_quote))
        seeds.append((problem, card_quote, source_quote))

    requested = set()
    for seed, seed_card_quote, source_quote in seeds:
        for subject in subjects:
            if (
                subject.location not in checked
                or not subject.location.startswith("by_field.")
                or subject.location == seed.location
                or (subject.location, source_quote) in occupied_source
            ):
                continue
            chunk_text = "\n\n".join(
                chunk_texts.get(chunk_id, "")
                for chunk_id in subject.chunk_ids
            ).strip()
            if (
                source_quote not in chunk_text
                or seed_card_quote in subject.value_text
            ):
                continue
            request_key = (subject.location, seed.kind, source_quote)
            if request_key in requested:
                continue
            requested.add(request_key)
            response = call(_build_cross_field_direct_prompt(
                seed.kind,
                subject.field,
                subject.value_text,
                seed_card_quote,
                source_quote,
                chunk_text,
            ))
            parsed_rows = parse_violations(response, require_evidence=True)
            if len(parsed_rows) > 1:
                raise DecisionError(
                    "EVIDENCE_REVIEW 聚焦二次语义复核最多允许 1 条 violation"
                )
            for parsed in parsed_rows:
                if (
                    parsed.get("rule") != seed.kind
                    or parsed.get("evidence_relation") != "direct_conflict"
                    or parsed.get("source_quote") != source_quote
                ):
                    raise DecisionError(
                        "EVIDENCE_REVIEW 聚焦二次语义复核必须保持同规则、"
                        "direct_conflict 与完整已验证 source_quote"
                    )
                validated = _validate_violation_evidence(
                    parsed,
                    subject.value_text,
                    chunk_text,
                )
                evidence_quote = json.dumps({
                    "card_quote": validated["card_quote"],
                    "source_quote": validated["source_quote"],
                    "evidence_relation": validated["evidence_relation"],
                }, ensure_ascii=False)
                out.append(Problem(
                    source="transcription",
                    severity="error",
                    kind=validated["rule"],
                    location=subject.location,
                    field=subject.field,
                    chunk_id=(
                        subject.chunk_ids[0] if subject.chunk_ids else None
                    ),
                    detail=(
                        "同卡跨字段聚焦二次语义复核；"
                        + validated["detail"]
                    ),
                    quote=evidence_quote,
                ))
                occupied_source.add((subject.location, source_quote))
    return out


def _default_call_verify(prompt: str, *, bypass_cache: bool = False) -> str:
    """真实回原文异源复核:经 MODEL_GATEWAY 调 verify 槽(Anthropic Sonnet 5 细校、异源)。MODEL_GATEWAY 依赖失败(受控)→ DecisionError(可重跑)。"""
    from model_gateway import call
    from model_gateway.errors import GatewayError
    # Sonnet 5 使用 adaptive thinking + xhigh;max_tokens 同时覆盖思考与最终 JSON，
    # 不再沿用 Haiku 的 4096。区域门已在 transcription_verify 层每卡过一次。
    try:
        resp = call("verify", {"messages": [{"role": "user", "content": prompt}],
                               "max_tokens": 16384, "timeout": 180},
                    bypass_cache=bypass_cache)
    except GatewayError:
        raise
    return resp.get("text") or ""


# ── 区域门(承 RESEARCH_ANALYSIS §六 / v6.4 之二;升 Anthropic verify 后境外,发 verify 前必过)──────────
# 白名单放行:仅「非 gated 且 reason ∈ {domestic_skip, region_ok}」放行;gated / region_unknown /
# 将来新增未知 reason 一律默认挡(绝不静默放行)。挡下 → DecisionError:auto_review 升级、卡留 pending 报人工、绝不硬撞 403。
_REGION_PASS_REASONS = ("domestic_skip", "region_ok")


def _default_region_check() -> None:
    """区域门:经 region_preflight('verify')(由 verify 槽 provider 驱动)——国内 / 本地 → domestic_skip 放行;
    非陆 → region_ok 放行;大陆 → gated 挡下;查不到 → 交人(均挡下、绝不静默发调用)。
    verify 槽 = deepseek(国内)时自动放行;升 Anthropic(境外)才真进门。挡下抛 DecisionError(可重跑 / 升级留 pending)。"""
    from model_gateway import region_preflight
    from model_gateway.errors import GatewayError
    try:
        rc = region_preflight("verify")
    except GatewayError as e:
        raise DecisionError(f"EVIDENCE_REVIEW verify 区域门预检失败(可重跑):{type(e).__name__}: {e}") from e
    # 真 fail-closed · 白名单放行(承 RESEARCH_ANALYSIS 同款):仅「非 gated 且 reason ∈ {domestic_skip, region_ok}」放行。
    if (not rc.get("gated")) and rc.get("reason") in _REGION_PASS_REASONS:
        return
    msg = rc.get("message") or f"区域门挡下:reason={rc.get('reason')}, gated={rc.get('gated')}"
    raise DecisionError(f"EVIDENCE_REVIEW verify 区域门挡下(卡留 pending 报人工、绝不硬撞 403):{msg}")


def _evidence_review_local_ready() -> bool:
    """本地 EVIDENCE_REVIEW 异源校核槽是否就绪。Core:本地推理路占位、未建 → **恒 False**(entrusted 数据恒 fail-closed);待落地(债)。"""
    return False


def _ownership_gate(fm) -> None:
    """归属安全阀(承 Integration第2步④ + 复审③ 对齐 distill 口径):**非明确 self 一律 fail-closed**——
    self → 云端 EVIDENCE_REVIEW 校核 OK;entrusted → 须本地 EVIDENCE_REVIEW 校核(Core 未落地 → 恒挡);**缺失 / 未知 → 归属不明绝不上云(恒挡)**。
    比只挡 entrusted 更严、堵「归属不明放行」的 fail-open 方向(与 distill「非 self 一律挡、不调 MODEL_GATEWAY」同口径)。
    在任何外呼(区域探测 / Haiku)前判;挡下抛 OwnershipFailClosed,绝不把他人托付 / 归属不明数据送 Haiku 境外云。"""
    own = fm.get("data_ownership") if isinstance(fm, dict) else None
    if own == "self":
        return                                             # self:自有数据 → 云端校核 OK
    if own == "entrusted" and _evidence_review_local_ready():
        return                                             # (未来)本地 EVIDENCE_REVIEW 就绪 → entrusted 可本地校核;Core 恒 False、走不到
    msg = (f"数据归属非明确 self(得 {own!r};entrusted / 缺失 / 未知一律挡)+ 本地 EVIDENCE_REVIEW 校核槽未就绪(Core 占位)→ "
           "fail-closed 挡下;绝不静默送 Haiku 境外云、手动订阅档亦不可绕过(承归属安全阀·对齐 distill 口径)")
    title = fm.get("title") if isinstance(fm, dict) else None   # 复审[2]:非 dict fm 也不崩(与上面 own 取值同守卫)
    log_reason("EVIDENCE_REVIEW", "EVIDENCE_REVIEW 归属安全阀挡下", msg, event_category="verify", target=str(title or ""))
    raise OwnershipFailClosed(msg)


_BATCH_TECHNICAL_RETRY_MARKER = "EVIDENCE_REVIEW_BATCH_TECHNICAL_RETRY"


def _bind_batch_rows_to_exact_card_items(
    rows: list[dict],
    subjects_by_location: dict,
    chunk_texts: dict,
) -> list[dict]:
    """Replace model-written Card paraphrases with the indexed local Card item."""
    from .source_units import augment_review_rows

    augmented = augment_review_rows(
        rows, subjects_by_location, chunk_texts, _card_item_texts
    )
    bound = []
    for row in augmented:
        normalized = dict(row)
        subject = subjects_by_location.get(normalized.get("location"))
        item_index = normalized.get("item_index")
        items = _card_item_texts(subject.value_text) if subject is not None else []
        card_quote = normalized.get("card_quote")
        if (
            isinstance(item_index, int)
            and not isinstance(item_index, bool)
            and 0 <= item_index < len(items)
            and items[item_index].strip()
            and (
                not isinstance(card_quote, str)
                or card_quote not in items[item_index]
            )
        ):
            # The strict batch shell already binds location and item_index.
            # Use the exact indexed local text as Card-side evidence; source-side
            # evidence remains subject to the unchanged literal source validator.
            normalized["card_quote"] = items[item_index].strip()
        bound.append(normalized)
    return bound


def _build_batch_technical_retry_prompt(
    prompt: str,
    *,
    stage: str,
    error: Exception,
) -> str:
    """Retry only an invalid batch envelope; never resample a semantic verdict."""
    return (
        f"{prompt}\n\n{_BATCH_TECHNICAL_RETRY_MARKER}\n"
        f"The previous {stage} response failed the technical output contract: "
        f"{error}. Re-check the entire same bundle and return one complete JSON "
        "object matching the required schema. This is a technical retry, not "
        "permission to relax evidence rules or erase genuine violations. "
        "Every card_quote must be a non-empty continuous substring copied from "
        "the exact indexed Card item; never emit a field label, placeholder, or "
        "empty string. For direct_conflict or explicit_omission, source_quote "
        "must be one continuous verbatim substring copied from the authorized "
        "source text, preserving every character, number, sign, and space. If "
        "you cannot provide that exact source substring for a claimed violation, "
        "keep the item as a violation but use rule R8, evidence_relation "
        "missing_from_given_chunks, and source_quote null."
    )


def _contain_exhausted_batch_evidence(
    rows: list[dict],
    subjects_by_location: dict,
    chunk_texts: dict,
) -> tuple[list[dict], list[Problem]]:
    """Keep valid evidence and fail closed only the invalid indexed items.

    This is used only after the original response plus both bounded technical
    corrections have failed.  A structurally valid row is never promoted into
    literal evidence: if its evidence still cannot pass the unchanged local
    validator, the exact indexed Card item is marked as the existing R8
    ``missing_from_given_chunks`` relation.  That item is therefore removed by
    the repair flow, while other rows in the same batch retain their validated
    evidence and the whole document can continue.
    """
    contained = []
    for row in rows:
        try:
            _validate_batch_rows([row], subjects_by_location, chunk_texts)
            contained.append(dict(row))
            continue
        except DecisionError:
            pass

        subject = subjects_by_location.get(row.get("location"))
        item_index = row.get("item_index")
        items = _card_item_texts(subject.value_text) if subject is not None else []
        if (
            not isinstance(item_index, int)
            or isinstance(item_index, bool)
            or not 0 <= item_index < len(items)
            or not items[item_index].strip()
        ):
            raise DecisionError("EVIDENCE_REVIEW exhausted-batch item binding is invalid")
        contained.append({
            "location": row["location"],
            "item_index": item_index,
            "rule": "R8",
            "card_quote": items[item_index].strip(),
            "source_quote": None,
            "evidence_relation": "missing_from_given_chunks",
        })
    return contained, _validate_batch_rows(
        contained, subjects_by_location, chunk_texts
    )


def _run_batch_stage_with_one_contract_retry(
    prompt: str,
    *,
    stage: str,
    expected_locations: list[str],
    expected_items: dict[str, list[int]],
    subjects_by_location: dict,
    chunk_texts: dict,
    call,
    retry_call,
) -> tuple[list[dict], list[Problem]]:
    """Run one semantic stage with two bounded output-contract corrections."""
    last_bound_rows = None
    try:
        response = call(prompt)
        rows = _parse_batch_response(
            response,
            expected_locations,
            expected_items,
        )
        rows = _bind_batch_rows_to_exact_card_items(
            rows, subjects_by_location, chunk_texts
        )
        last_bound_rows = rows
        return rows, _validate_batch_rows(
            rows,
            subjects_by_location,
            chunk_texts,
        )
    except GatewayError as first_error:
        raise BatchContractExhausted(
            f"EVIDENCE_REVIEW {stage} transport retries exhausted for this bounded batch: {first_error}"
        ) from first_error
    except DecisionError as error:
        last_error = error

    for _ in range(2):
        retry_prompt = _build_batch_technical_retry_prompt(
            prompt,
            stage=stage,
            error=last_error,
        )
        try:
            retry_response = retry_call(retry_prompt)
            retry_rows = _parse_batch_response(
                retry_response,
                expected_locations,
                expected_items,
            )
            retry_rows = _bind_batch_rows_to_exact_card_items(
                retry_rows, subjects_by_location, chunk_texts
            )
            last_bound_rows = retry_rows
            return retry_rows, _validate_batch_rows(
                retry_rows,
                subjects_by_location,
                chunk_texts,
            )
        except GatewayError as transport_error:
            raise BatchContractExhausted(
                f"EVIDENCE_REVIEW {stage} transport retries exhausted for this bounded batch: "
                f"{transport_error}"
            ) from transport_error
        except DecisionError as error:
            last_error = error
    if last_bound_rows is not None:
        try:
            return _contain_exhausted_batch_evidence(
                last_bound_rows, subjects_by_location, chunk_texts
            )
        except DecisionError as containment_error:
            last_error = containment_error
    raise BatchContractExhausted(
        f"EVIDENCE_REVIEW {stage} controlled technical attempts failed three times; "
        f"stopped fail-closed: {last_error}"
    ) from last_error


def transcription_verify(
    card_path, *, call_verify=None, region_check=None, bypass_cache: bool = False
) -> "_report.EvidenceReviewReport":
    """跑一次搬运类校核 → 写 EvidenceReviewReport → 返回。**不驱动 KNOWLEDGE_ADMISSION**(驱动见 verify_and_drive)。
    call_verify 可注入(离线 mock;缺省 = 真实经 MODEL_GATEWAY)。bypass_cache 只供灾难恢复重试跳过旧坏响应；
    注入式 call_verify(prompt) 接口保持不变。MODEL_GATEWAY / 解析 / schema 失败 → DecisionError(可重跑)。
    region_check 可注入(默认查 verify 槽 provider 属境外才 preflight;离线测各区域场景用)。
    ④ 归属安全阀:entrusted 卡在**任何外呼前** fail-closed(OwnershipFailClosed),绝不送 Haiku 境外云。"""
    card_path = Path(card_path)
    if call_verify is None:
        def call(prompt: str) -> str:
            return _default_call_verify(prompt, bypass_cache=bypass_cache)

        def technical_retry_call(prompt: str) -> str:
            return _default_call_verify(prompt, bypass_cache=True)
    else:
        call = call_verify
        technical_retry_call = call_verify
    fm = _read_frontmatter(card_path)                      # 先读卡(系统性错误 → CardUnreadable/CardParseError)
    _ownership_gate(fm)                                    # ④ 归属安全阀:entrusted 绝不上云 —— 最先、任何外呼(区域探测/Haiku)前
    # 区域门:仅默认 MODEL_GATEWAY 真调用路径(境外 API)前挡;region_check 可注入(离线测各场景);
    # mock 路径(注入 call_verify、未注入 region_check)不发真调用 → 跳过区域门(承 RESEARCH_ANALYSIS 同款门控)。
    if region_check is not None:
        region_check()
    elif call_verify is None:
        _default_region_check()
    chunk_texts = _load_chunk_texts(card_path)             # 系统性错误 → ChunkStoreError(raise)
    verify_provider, verify_model = _verify_slot()
    distill_model = fm.get("distill_model")
    same_src, same_reason = _same_source(distill_model, verify_provider, verify_model)

    problems, checked_core, checked_locations = [], [], []
    subjects = _expand_absence_scope(_collect_subjects(fm), chunk_texts)
    eligible_subjects = []
    for subj in subjects:
        chunk_text = "\n\n".join(chunk_texts.get(c, "") for c in subj.chunk_ids).strip()
        if not subj.value_text.strip() or not chunk_text:  # 无字段值 / 无 chunk → 记 malformed、不发空 subject(控预算)
            problems.append(Problem(source="transcription", severity="warn", kind="malformed_subject",
                                    location=subj.location, field=subj.field,
                                    chunk_id=subj.chunk_ids[0] if subj.chunk_ids else None,
                                    detail="无字段值 / 无 chunk 原文,跳过回原文复核(未发空 subject)"))
            continue
        eligible_subjects.append(subj)
        checked_locations.append(subj.location)            # 真进整批 prompt、真核过的 subject.location(per-entry 守卫:未核→unknown 非 verified)
        if subj.field in _CORE_FIELDS:
            checked_core.append(subj.location)

    if eligible_subjects:
        expected_locations = [subject.location for subject in eligible_subjects]
        subjects_by_location = {
            subject.location: subject for subject in eligible_subjects
        }
        batch_input = _build_batch_input(eligible_subjects, chunk_texts)
        expected_items = {
            subject["location"]: [
                item["item_index"] for item in subject["card_items"]
            ]
            for subject in batch_input["subjects"]
        }
        from memorive_workflow.node_progress import scope
        with scope('CARD_REVIEW', ['initial', 'final']) as node_units:
            initial_rows, _ = _run_batch_stage_with_one_contract_retry(
                _build_batch_initial_prompt(batch_input),
                stage="initial review",
                expected_locations=expected_locations,
                expected_items=expected_items,
                subjects_by_location=subjects_by_location,
                chunk_texts=chunk_texts,
                call=call,
                retry_call=technical_retry_call,
            )
            node_units.complete("initial")
            final_rows, final_problems = _run_batch_stage_with_one_contract_retry(
                _build_batch_review_prompt(
                    batch_input,
                    initial_rows,
                ),
                stage="final review",
                expected_locations=expected_locations,
                expected_items=expected_items,
                subjects_by_location=subjects_by_location,
                chunk_texts=chunk_texts,
                call=call,
                retry_call=technical_retry_call,
            )
            node_units.complete("final")
            problems.extend(final_problems)

    # 精确相同的已验证 direct conflict 可在共享锚点字段间本地传播，不产生额外 API 调用。
    problems = _propagate_literal_direct_conflicts(
        problems,
        subjects,
        chunk_texts,
        checked_locations,
    )

    # ④ verdict:任一 R1–R9 违规 → FAIL,否则 PASS(malformed 只记录、不驱动 verdict;同源低置信不改此阈值)
    verdict = "FAIL" if any(p.kind in _VALID_RULES for p in problems) else "PASS"
    confidence = _report.CONFIDENCE_SAME_SOURCE if same_src else _report.CONFIDENCE_CROSS_SOURCE
    upgrade_pending = ([f"{loc}(核心字段,同供应商复核,可配置其他供应商再核)" for loc in checked_core]
                       if same_src else [])
    paper_id = (fm.get("source_anchor") or {}).get("paper_id") if isinstance(fm.get("source_anchor"), dict) else None
    rep = _report.EvidenceReviewReport(
        report_id=_report.gen_report_id(paper_id or "unknown"),
        paper_id=paper_id or "unknown", card=str(card_path), verdict=verdict, problems=problems,
        confidence=confidence, same_source_reason=same_reason, upgrade_pending=upgrade_pending,
        distill_model=distill_model, verify_model=verify_model, checked_at=_now_iso(),
        checked_count=len(checked_locations), checked_locations=checked_locations)
    _report.write_report(rep)
    return rep


def make_evidence_review_source(*, call_verify=None, region_check=None):
    """把搬运类校核包成 auto_review 的 decision_source:Callable[[Path], ReviewDecision]。"""
    def evidence_review_transcription_source(card_path):
        rep = transcription_verify(card_path, call_verify=call_verify, region_check=region_check)
        verdict = Verdict.PASS if rep.verdict == "PASS" else Verdict.FAIL
        rules = sorted({p.kind for p in rep.problems if p.kind in _VALID_RULES})
        return ReviewDecision(verdict, reason_summary=(",".join(rules) or None), report_id=rep.report_id)
    return evidence_review_transcription_source


def verify_and_drive(card_path, *, call_verify=None, region_check=None, escalate_after=2) -> AutoResult:
    """搬运类校核 → 经 auto_review(trigger=Trigger.EVIDENCE_REVIEW_VERDICT)驱动 KNOWLEDGE_ADMISSION。
    PASS→active / FAIL→quarantined;MODEL_GATEWAY / 解析 / 区域门失败→DecisionError→重跑、连错 2 次→escalated(卡留 pending)。
    **KNOWLEDGE_ADMISSION 不改**(2a 已激活 EVIDENCE_REVIEW_VERDICT + 认 ReviewDecision)。

    ⚠ **v6.4 之四·上半起 = 旧整卡-verdict 路径,非分级信任准入**:它 FAIL→整卡 quarantine(正是分级信任要替代的行为)、
    **不落 per-entry 信用档**。**分级信任准入统一走 `evidence_review.grade_and_admit`**(EVIDENCE_REVIEW 判→per-field 信用档→带档进 active、
    不整卡 quarantine)。本函数保留仅供「整卡 verdict」旧用途 / Verification 测试;分级信任管线**勿用它准入**(会绕过信用档标)。
    〔跨路径安全兜底:build_card 从建卡起给 unknown 落 ⚠未核标,故即便误经本路径进 active 也不会「unknown 却无标」。〕"""
    return auto_review(Path(card_path), make_evidence_review_source(call_verify=call_verify, region_check=region_check),
                       trigger=Trigger.EVIDENCE_REVIEW_VERDICT, escalate_after=escalate_after)
