"""Additive Phase 1 claim-evidence sidecar and binding validator."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Iterable

from m11_log import log_reason

from .atomic_review import CORE_FIELDS, split_free_text


VIEW_SCHEMA_VERSION = "claim-evidence-view-v3"
REVIEW_CONTRACT_ID = "card-review-claim-evidence-v3"
FACT_VERDICTS = {"verified", "flagged", "removed", "not_reviewed"}
REVIEW_STATUSES = {"SUPPORTED", "PARTIAL", "UNSUPPORTED", "CONFLICT", "NEEDS_REVIEW"}
_RESOLVED_MAPPING = {
    "explicit",
    "exact",
    "token_match",
    "best_anchor_exact",
    "best_anchor_scope_exact",
}
_NUMBER = re.compile(r"(?<![\w.])[+\-−]?\d+(?:[.,]\d+)?(?:\s*[–—-]\s*\d+(?:[.,]\d+)?)?")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}")
_HIGH_RISK = re.compile(
    r"\d|%|±|because|cause|lead(?:s|ing)?\s+to|result(?:s|ed)?\s+in|"
    r"limit|exception|except|only|must|cannot|由于|导致|局限|例外|仅",
    re.I,
)
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "was",
    "were", "is", "are", "with", "by", "from", "that", "this", "as",
}
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+")
_CYCLE = re.compile(r"\b(first|second|third|fourth|fifth)\s+cycle\b", re.I)
_PHASE_MARKER = re.compile(
    r"\b(?:first|second|third|fourth|fifth)\s+cycle\b|"
    r"\b(?:phase|stage|period|cycle|day|days|hour|hours|week|weeks|month|months)\b|"
    r"阶段|时期|周期|第\s*\d+\s*(?:天|小时|周|月)",
    re.I,
)
_SCOPE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*|[\u4e00-\u9fff]+")
_SCOPE_GENERIC = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "for", "with",
    "sample", "samples", "control", "group", "groups", "culture", "cultures",
    "reactor", "reactors", "treatment", "treatments", "system", "systems",
    "condition", "conditions", "tested", "experimental", "value", "values",
    "l", "ml", "ul", "g", "mg", "kg", "mol", "mmol", "umol",
}
_SCOPE_PLACEHOLDERS = {
    "", "not given", "not reported", "unknown", "none", "n/a", "na",
    "未给", "未报告", "未知", "无",
}
_FACT_SCOPE_AXES = (
    "sample_scope",
    "phase_time",
    "comparator",
    "stat_type",
    "condition_signature",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_id(prefix: str, value: Any, *, length: int = 16) -> str:
    digest = hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:length]
    return f"{prefix}-{digest}"


def _paper_id(card: dict[str, Any]) -> str:
    value = card.get("paper_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    anchor = card.get("source_anchor")
    value = anchor.get("paper_id") if isinstance(anchor, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _normalise_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"[*_`]+", "", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _word_tokens(value: Any) -> set[str]:
    return {
        token.casefold()
        for token in _WORD.findall(_normalise_text(value))
        if token.casefold() not in _STOPWORDS
    }


def _number_tokens(value: Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.replace(" ", "") for item in _NUMBER.findall(str(value or ""))))


def _labeled_value(text: Any, *labels: str) -> str | None:
    source = str(text or "")
    for label in labels:
        match = re.search(
            rf"(?:^|\|)\s*{re.escape(label)}\s*[:：]\s*([^|]+)",
            source,
            flags=re.I,
        )
        if match:
            value = match.group(1).strip()
            return None if _normalise_text(value) in _SCOPE_PLACEHOLDERS else value
    return None


def _scope_identity_tokens(value: Any) -> set[str]:
    tokens = set()
    for token in _SCOPE_TOKEN.findall(_normalise_text(value)):
        folded = token.casefold()
        if folded in _SCOPE_GENERIC or re.fullmatch(r"\d+(?:[.,]\d+)?", folded):
            continue
        tokens.add(folded)
    return tokens


def _scope_phrase_supported(value: Any, quote: Any, *, identity: bool = False) -> bool:
    phrase = _normalise_text(value)
    quote_text = _normalise_text(quote)
    if not phrase:
        return True
    if phrase in quote_text:
        return True
    if identity:
        primary_phrase = re.split(r"\s*\(", phrase, maxsplit=1)[0].strip()
        primary_tokens = _scope_identity_tokens(primary_phrase)
        quote_tokens = {
            token.casefold() for token in _SCOPE_TOKEN.findall(quote_text)
        }
        if primary_tokens and primary_tokens.issubset(quote_tokens):
            return True
    phrase_tokens = _scope_identity_tokens(phrase) if identity else {
        token.casefold()
        for token in _SCOPE_TOKEN.findall(phrase)
        if token.casefold() not in _STOPWORDS
    }
    quote_tokens = {
        token.casefold() for token in _SCOPE_TOKEN.findall(quote_text)
    }
    return bool(phrase_tokens) and phrase_tokens.issubset(quote_tokens)


def _phase_scope_supported(value: Any, quote: Any) -> bool:
    phase_text = str(value or "")
    quote_text = str(quote or "")
    claim_cycles = {item.casefold() for item in _CYCLE.findall(phase_text)}
    if claim_cycles:
        quote_cycles = {item.casefold() for item in _CYCLE.findall(quote_text)}
        return bool(quote_cycles) and not claim_cycles.isdisjoint(quote_cycles)
    return _scope_phrase_supported(phase_text, quote_text)


def _canonical_stat_type(value: Any) -> str:
    text = _normalise_text(value)
    if not text or text in _SCOPE_PLACEHOLDERS:
        return ""
    if any(term in text for term in ("minimum", "lower bound", "min ", "最小")):
        return "minimum"
    if any(term in text for term in ("maximum", "upper bound", "max ", "最大")):
        return "maximum"
    if ("mean" in text or "average" in text or "平均" in text) and any(
        term in text for term in ("sd", "standard deviation", "±", "+/-", "标准差")
    ):
        return "mean_sd"
    if "mean" in text or "average" in text or "平均" in text:
        return "mean"
    if "median" in text or "中位" in text:
        return "median"
    if "range" in text or "范围" in text:
        return "range"
    if "single value" in text or "point" in text or "单值" in text:
        return "single_value"
    return text


def _stat_type_supported(value: Any, text: Any) -> bool:
    stat_type = _canonical_stat_type(value)
    folded = _normalise_text(text)
    if not stat_type:
        return True
    if stat_type == "mean_sd":
        return "±" in folded or "+/-" in folded or (
            ("mean" in folded or "average" in folded or "平均" in folded)
            and ("sd" in folded or "standard deviation" in folded or "标准差" in folded)
        )
    terms = {
        "minimum": ("minimum", "lower bound", "最小"),
        "maximum": ("maximum", "upper bound", "最大"),
        "mean": ("mean", "average", "平均", "±", "+/-"),
        "median": ("median", "中位"),
        "range": ("range", "范围", "–", "—"),
        "single_value": ("single value", "point estimate", "单值"),
    }.get(stat_type)
    return any(term in folded for term in terms) if terms else stat_type in folded


def _normal_scope_axis(name: str, value: Any) -> Any:
    if name == "stat_type":
        return _canonical_stat_type(value)
    if isinstance(value, dict):
        return tuple(sorted((str(key), _normal_scope_axis(str(key), item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_normal_scope_axis(name, item) for item in value)
    return _normalise_text(value)


def _implicit_fact_scope(text: Any) -> dict[str, Any]:
    source = str(text or "")
    folded = _normalise_text(source)
    phase = _labeled_value(source, "phase/time", "阶段/时间")
    if phase is None and (cycle_match := _CYCLE.search(source)):
        phase = cycle_match.group(0)
    stat_type = _labeled_value(source, "stat_type", "统计类型")
    if stat_type is None:
        if "±" in folded or "+/-" in folded:
            stat_type = "mean_sd"
        elif any(term in folded for term in ("minimum", "lower bound", "最小")):
            stat_type = "minimum"
        elif any(term in folded for term in ("maximum", "upper bound", "最大")):
            stat_type = "maximum"
        elif any(term in folded for term in ("mean", "average", "平均")):
            stat_type = "mean"
        elif "median" in folded or "中位" in folded:
            stat_type = "median"
    return {
        "sample_scope": _labeled_value(source, "sample/control", "样本/对照"),
        "phase_time": phase,
        "comparator": _labeled_value(source, "comparator", "对照"),
        "stat_type": stat_type,
        "condition_signature": _labeled_value(source, "subprocess", "子过程"),
    }


def _normalise_anchors(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict):
                result.append(dict(item))
            elif isinstance(item, str) and item.strip():
                result.append({"chunk_id": item.strip(), "quote": None})
        return result
    if isinstance(value, dict):
        if isinstance(value.get("chunk_ids"), list):
            return [
                {
                    "chunk_id": chunk_id,
                    "quote": None,
                    "section_page": value.get("section_page"),
                }
                for chunk_id in value["chunk_ids"]
                if isinstance(chunk_id, str) and chunk_id
            ]
        if isinstance(value.get("chunk_id"), str):
            return [dict(value)]
    return []


def _field_anchors(card: dict[str, Any], field: str) -> list[dict[str, Any]]:
    source_anchor = card.get("source_anchor")
    by_field = source_anchor.get("by_field") if isinstance(source_anchor, dict) else None
    return _normalise_anchors(by_field.get(field)) if isinstance(by_field, dict) else []


def _iter_claim_inputs(card: dict[str, Any], field: str) -> Iterable[dict[str, Any]]:
    raw = card.get(field)
    if field == "key_data":
        for item_index, item in enumerate(raw if isinstance(raw, list) else []):
            if not isinstance(item, dict):
                continue
            pieces = [
                str(item.get(key)).strip()
                for key in ("metric", "value", "unit", "sample", "stat")
                if item.get(key) not in (None, "")
            ]
            if pieces:
                target_path = f"key_data[{item_index}]"
                sample = str(item.get("sample") or "")
                stat = str(item.get("stat") or "")
                sample_scope = _labeled_value(sample, "sample/control", "样本/对照")
                phase_time = _labeled_value(sample, "phase/time", "阶段/时间")
                comparator = _labeled_value(sample, "comparator", "对照")
                subprocess = _labeled_value(sample, "subprocess", "子过程")
                stat_type = _labeled_value(stat, "stat_type", "统计类型")
                yield {
                    "field": field,
                    "target_path": target_path,
                    "item_index": item_index,
                    "segment_index": 0,
                    "text": " | ".join(pieces),
                    "anchors": _normalise_anchors(
                        {
                            "chunk_id": item.get("chunk_id"),
                            "quote": item.get("quote"),
                            "target_path": target_path,
                        }
                    ),
                    "semantic_scope": {
                        "metric": item.get("metric"),
                        "value": item.get("value"),
                        "unit": item.get("unit"),
                        "sample_scope": sample_scope,
                        "phase_time": phase_time,
                        "comparator": comparator,
                        "stat_type": stat_type,
                        "condition_signature": item.get("condition_signature") or subprocess,
                        "stat": item.get("stat"),
                    },
                }
        return
    values = raw if isinstance(raw, list) else [raw]
    anchors = _field_anchors(card, field)
    for item_index, value in enumerate(values):
        if value is None:
            continue
        text = value if isinstance(value, str) else str(value)
        pieces, _status = split_free_text(text)
        if not pieces and text.strip():
            pieces = [text.strip()]
        for segment_index, piece in enumerate(pieces):
            yield {
                "field": field,
                "target_path": f"{field}[{item_index}]" if isinstance(raw, list) else field,
                "item_index": item_index if isinstance(raw, list) else None,
                "segment_index": segment_index,
                "text": piece,
                "anchors": anchors,
                "semantic_scope": None,
            }


def _mapping_score(claim: dict[str, Any], anchor: dict[str, Any]) -> tuple[str, float] | None:
    explicit_ids = anchor.get("claim_ids")
    if not isinstance(explicit_ids, list):
        explicit_ids = anchor.get("linked_claim_ids")
    if not isinstance(explicit_ids, list):
        explicit_ids = [anchor.get("claim_id")] if anchor.get("claim_id") else []
    if claim["claim_id"] in explicit_ids or anchor.get("target_path") == claim["target_path"]:
        return "explicit", 1.0
    quote = anchor.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        return None
    claim_text, quote_text = _normalise_text(claim["original_text"]), _normalise_text(quote)
    if claim_text and quote_text and (
        claim_text == quote_text or claim_text in quote_text or quote_text in claim_text
    ):
        return "exact", 1.0
    claim_words, quote_words = _word_tokens(claim_text), _word_tokens(quote_text)
    common = claim_words & quote_words
    containment = len(common) / max(1, min(len(claim_words), len(quote_words)))
    claim_numbers, quote_numbers = set(_number_tokens(claim_text)), set(_number_tokens(quote_text))
    numbers_match = bool(claim_numbers) and claim_numbers.issubset(quote_numbers)
    if (numbers_match and containment >= 0.2) or (len(common) >= 2 and containment >= 0.55):
        return "token_match", min(0.99, 0.55 + 0.35 * containment + (0.09 if numbers_match else 0))
    return None


def _claim_atoms(claim_id: str, text: str) -> list[dict[str, Any]]:
    atoms = [{
        "atom_id": _hash_id("A", [claim_id, "claim_text", text]),
        "atom_type": "claim_text",
        "claim": text,
        "required": True,
        "allowed_link_ids": [],
    }]
    for index, number in enumerate(_number_tokens(text)):
        atoms.append({
            "atom_id": _hash_id("A", [claim_id, "numeric_value", index, number]),
            "atom_type": "numeric_value",
            "claim": number,
            "required": True,
            "allowed_link_ids": [],
        })
    return atoms


def _scope_compatibility(claim: dict[str, Any], quote: Any) -> dict[str, Any]:
    """Reject a real quote unless numeric, sample/control, and phase scope agree."""
    quote_text = str(quote or "")
    quote_numbers = set(_number_tokens(quote_text))
    claim_text = str(claim.get("original_text") or "")
    scope = claim.get("semantic_scope")
    scope = scope if isinstance(scope, dict) else {}
    primary_numbers = set(
        _number_tokens(scope.get("value") if scope else claim_text)
    )
    comparator_numbers = set(_number_tokens(scope.get("comparator"))) if scope else set()
    phase_text = str(scope.get("phase_time") or "")
    if not phase_text and (cycle_match := _CYCLE.search(claim_text)):
        phase_text = cycle_match.group(0)
    claim_cycles = {item.casefold() for item in _CYCLE.findall(phase_text)}
    quote_cycles = {item.casefold() for item in _CYCLE.findall(quote_text)}
    sample_scope = scope.get("sample_scope") if scope else None
    comparator_scope = scope.get("comparator") if scope else None
    reason_codes: list[str] = []

    if primary_numbers and not primary_numbers.issubset(quote_numbers):
        reason_codes.append("PRIMARY_VALUE_MISMATCH")
    if comparator_numbers and not comparator_numbers.issubset(quote_numbers):
        reason_codes.append("COMPARATOR_MISMATCH")
    if phase_text and not _phase_scope_supported(phase_text, quote_text):
        reason_codes.append(
            "PHASE_MISMATCH" if _PHASE_MARKER.search(quote_text) else "PHASE_MISSING"
        )
    if sample_scope and not _scope_phrase_supported(sample_scope, quote_text, identity=True):
        reason_codes.append("SAMPLE_SCOPE_MISMATCH_OR_MISSING")
    if comparator_scope and not _scope_phrase_supported(
        comparator_scope, quote_text, identity=True
    ):
        reason_codes.append("COMPARATOR_SCOPE_MISMATCH_OR_MISSING")

    unit = str(scope.get("unit") or "") if scope else ""
    if unit:
        compact_unit = re.sub(r"\s+", "", unit).casefold()
        compact_quote = re.sub(r"\s+", "", quote_text).casefold()
        if compact_unit not in compact_quote:
            reason_codes.append("UNIT_MISMATCH")

    metric_words = _word_tokens(scope.get("metric")) if scope else set()
    quote_words = _word_tokens(quote_text)
    if metric_words:
        common = metric_words & quote_words
        if len(common) < min(2, len(metric_words)):
            reason_codes.append("METRIC_MISMATCH")
    else:
        claim_words = _word_tokens(claim_text)
        common = claim_words & quote_words
        containment = len(common) / max(1, min(len(claim_words), len(quote_words)))
        if len(common) < 2 or containment < 0.25:
            reason_codes.append("CLAIM_TERMS_MISMATCH")

    return {
        "ok": not reason_codes,
        "reason_codes": reason_codes,
        "primary_numbers": sorted(primary_numbers),
        "comparator_numbers": sorted(comparator_numbers),
        "claim_cycles": sorted(claim_cycles),
        "quote_cycles": sorted(quote_cycles),
        "sample_scope": sample_scope,
        "phase_time": phase_text or None,
    }


def _fact_scopes_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Require five-axis agreement before two claim occurrences share a fact ID."""
    left_scope = left.get("semantic_scope")
    right_scope = right.get("semantic_scope")
    left_scope = left_scope if isinstance(left_scope, dict) else None
    right_scope = right_scope if isinstance(right_scope, dict) else None
    if left_scope is not None and right_scope is not None:
        return all(
            _normal_scope_axis(axis, left_scope.get(axis))
            == _normal_scope_axis(axis, right_scope.get(axis))
            for axis in _FACT_SCOPE_AXES
        )
    if left_scope is None and right_scope is None:
        left_implicit = _implicit_fact_scope(left.get("original_text"))
        right_implicit = _implicit_fact_scope(right.get("original_text"))
        return all(
            _normal_scope_axis(axis, left_implicit.get(axis))
            == _normal_scope_axis(axis, right_implicit.get(axis))
            for axis in _FACT_SCOPE_AXES
        )

    structured = left if left_scope is not None else right
    free_text = right if left_scope is not None else left
    structured_scope = structured["semantic_scope"]
    free_claim_text = free_text.get("original_text")
    if not _scope_compatibility(structured, free_claim_text)["ok"]:
        return False
    if not _stat_type_supported(structured_scope.get("stat_type"), free_claim_text):
        return False
    condition = structured_scope.get("condition_signature")
    return not condition or _scope_phrase_supported(condition, free_claim_text, identity=True)


def _registered_supporting_evidence(
    claim: dict[str, Any],
    evidence_records: list[dict[str, Any]],
    chunk_map: dict[str, dict[str, Any]],
    paper_id: str,
    *,
    excluded_evidence_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    excluded = excluded_evidence_ids or set()
    ranked = []
    for evidence in evidence_records:
        if evidence.get("evidence_id") in excluded or evidence.get("paper_id") != paper_id:
            continue
        quote, chunk_id = evidence.get("quote"), evidence.get("chunk_id")
        chunk = chunk_map.get(chunk_id)
        if not isinstance(quote, str) or not quote.strip() or not isinstance(chunk, dict):
            continue
        chunk_text = chunk.get("text")
        if not isinstance(chunk_text, str) or _normalise_text(quote) not in _normalise_text(chunk_text):
            continue
        compatibility = _scope_compatibility(claim, quote)
        if not compatibility["ok"]:
            continue
        section = str(chunk.get("section_path") or "").casefold()
        direct_rank = 0 if "result" in section else 1 if "method" in section else 2
        reselected_rank = 1 if evidence.get("anchor_reselected") else 0
        ranked.append((direct_rank, reselected_rank, str(chunk_id), evidence))
    return min(ranked, key=lambda item: item[:3])[3] if ranked else None


def _best_scope_anchor(
    claim: dict[str, Any], chunks: list[dict[str, Any]], paper_id: str
) -> dict[str, Any] | None:
    """Find a same-paper sentence matching numeric values and non-conflicting scope."""
    if not _number_tokens(claim.get("original_text")):
        return None
    ranked = []
    for chunk in chunks:
        if not isinstance(chunk, dict) or chunk.get("paper_id") not in (None, "", paper_id):
            continue
        chunk_id, text = chunk.get("chunk_id"), chunk.get("text")
        if not isinstance(chunk_id, str) or not isinstance(text, str):
            continue
        sentences = [item.strip() for item in _SENTENCE_SPLIT.split(text) if item.strip()]
        for sentence_index, sentence in enumerate(sentences):
            compatibility = _scope_compatibility(claim, sentence)
            if not compatibility["ok"]:
                continue
            section = str(chunk.get("section_path") or "").casefold()
            direct_rank = 0 if "result" in section else 1 if "method" in section else 2
            sequence = int(match.group(1)) if (match := re.search(r"#c(\d+)$", chunk_id)) else 10**12
            ranked.append((direct_rank, sequence, sentence_index, len(sentence), chunk, sentence))
    if not ranked:
        return None
    _rank, _sequence, _sentence_index, _length, selected, quote = min(
        ranked, key=lambda item: item[:4]
    )
    return {
        "chunk_id": selected["chunk_id"],
        "quote": quote,
        "source_role": selected.get("source_role") or "unknown",
        "anchor_reselected": True,
        "reselect_reason": "same_paper_numeric_scope_match",
    }


def _best_exact_anchor(claim: dict[str, Any], chunks: list[dict[str, Any]],
                       paper_id: str) -> dict[str, Any] | None:
    """Reselect only an exact same-paper occurrence; semantic guesses stay unresolved."""
    needle = _normalise_text(claim["original_text"])
    if not needle:
        return None
    ranked = []
    for chunk in chunks:
        if not isinstance(chunk, dict) or chunk.get("paper_id") not in (None, "", paper_id):
            continue
        chunk_id, text = chunk.get("chunk_id"), chunk.get("text")
        if not isinstance(chunk_id, str) or not isinstance(text, str):
            continue
        if needle not in _normalise_text(text):
            continue
        section = str(chunk.get("section_path") or "").casefold()
        block_type = str(chunk.get("block_type") or "").casefold()
        if "result" in section or block_type == "table":
            rank = 0
        elif "method" in section or "experimental" in section:
            rank = 1
        elif "abstract" in section:
            rank = 2
        elif "conclusion" in section:
            rank = 3
        else:
            rank = 4
        sequence = int(match.group(1)) if (match := re.search(r"#c(\d+)$", chunk_id)) else 10**12
        ranked.append((rank, sequence, chunk_id, chunk))
    if not ranked:
        return None
    _rank, _sequence, _chunk_id, selected = min(ranked, key=lambda item: item[:3])
    return {
        "chunk_id": selected["chunk_id"],
        "quote": claim["original_text"],
        "source_role": selected.get("source_role") or "unknown",
        "anchor_reselected": True,
        "reselect_reason": "exact_same_paper_claim_text",
    }


def _unify_fact_ids(claims: list[dict[str, Any]], links: list[dict[str, Any]]) -> None:
    """Conservatively unify differently worded occurrences sharing evidence and numbers."""
    parent = {claim["claim_id"]: claim["claim_id"] for claim in claims}
    claim_map = {claim["claim_id"]: claim for claim in claims}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        lroot, rroot = find(left), find(right)
        if lroot != rroot:
            parent[max(lroot, rroot)] = min(lroot, rroot)

    by_evidence: dict[str, list[str]] = {}
    for link in links:
        if link.get("mapping_status") in _RESOLVED_MAPPING:
            by_evidence.setdefault(str(link.get("evidence_id")), []).append(link["claim_id"])
    for claim_ids in by_evidence.values():
        for index, left_id in enumerate(claim_ids):
            left = claim_map[left_id]
            for right_id in claim_ids[index + 1:]:
                right = claim_map[right_id]
                if not _fact_scopes_compatible(left, right):
                    continue
                left_scope = left.get("semantic_scope")
                right_scope = right.get("semantic_scope")
                left_numbers = set(_number_tokens(left["original_text"]))
                right_numbers = set(_number_tokens(right["original_text"]))
                if isinstance(left_scope, dict):
                    left_numbers = set(_number_tokens(left_scope.get("value"))) | set(
                        _number_tokens(left_scope.get("comparator"))
                    )
                if isinstance(right_scope, dict):
                    right_numbers = set(_number_tokens(right_scope.get("value"))) | set(
                        _number_tokens(right_scope.get("comparator"))
                    )
                left_words, right_words = _word_tokens(left["original_text"]), _word_tokens(
                    right["original_text"]
                )
                common = left_words & right_words
                containment = len(common) / max(1, min(len(left_words), len(right_words)))
                same_numbers = bool(left_numbers) and left_numbers == right_numbers
                if _normalise_text(left["original_text"]) == _normalise_text(right["original_text"]):
                    union(left_id, right_id)
                elif same_numbers and len(common) >= 2 and containment >= 0.45:
                    union(left_id, right_id)
                elif len(common) >= 3 and containment >= 0.8:
                    union(left_id, right_id)

    groups: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        groups.setdefault(find(claim["claim_id"]), []).append(claim)
    fact_replacements = {}
    for group in groups.values():
        canonical_fact_id = min(claim["fact_id"] for claim in group)
        for claim in group:
            fact_replacements[claim["claim_id"]] = canonical_fact_id
            claim["fact_id"] = canonical_fact_id
            claim["occurrence_id"] = _hash_id(
                "O", [canonical_fact_id, claim["target_path"], claim["segment_index"]]
            )
    for link in links:
        link["fact_id"] = fact_replacements.get(link["claim_id"], link["fact_id"])


def build_claim_evidence_view(
    card: dict[str, Any],
    chunks: list[dict[str, Any]],
    *,
    fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a per-card v3 sidecar without changing Card Schema v6."""
    if not isinstance(card, dict):
        raise TypeError("card must be a mapping")
    if not isinstance(chunks, list):
        raise TypeError("chunks must be a list")
    selected_fields = tuple(fields) if fields is not None else (*CORE_FIELDS, "key_data")
    paper_id = _paper_id(card)
    chunk_map = {
        chunk.get("chunk_id"): chunk
        for chunk in chunks
        if isinstance(chunk, dict) and isinstance(chunk.get("chunk_id"), str)
    }
    claims, claim_anchors = [], {}
    for field in selected_fields:
        for item in _iter_claim_inputs(card, field):
            text, target_path = str(item["text"]).strip(), item["target_path"]
            claim_id = _hash_id(
                "C", [paper_id, field, target_path, item["segment_index"], _normalise_text(text)]
            )
            fact_id = _hash_id("F", [paper_id, _normalise_text(text)])
            occurrence_id = _hash_id("O", [fact_id, target_path, item["segment_index"]])
            claim = {
                "claim_id": claim_id,
                "fact_id": fact_id,
                "occurrence_id": occurrence_id,
                "field": field,
                "target_path": target_path,
                "item_index": item["item_index"],
                "segment_index": item["segment_index"],
                "order": len(claims),
                "original_text": text,
                "semantic_scope": item.get("semantic_scope"),
                "risk_level": "high" if _HIGH_RISK.search(text) else "normal",
                "mapping_status": "unresolved",
                "allowed_link_ids": [],
                "candidate_link_ids": [],
                "atoms": _claim_atoms(claim_id, text),
                "verdict": "not_reviewed",
            }
            claims.append(claim)
            claim_anchors[claim_id] = list(item["anchors"])

    evidence_records, evidence_by_key = [], {}
    anchors_by_field: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        anchors_by_field.setdefault(claim["field"], []).extend(claim_anchors[claim["claim_id"]])
    for field, anchors in anchors_by_field.items():
        seen_field = set()
        for anchor in anchors:
            chunk_id = anchor.get("chunk_id")
            if not isinstance(chunk_id, str) or not chunk_id:
                continue
            quote = anchor.get("quote") if isinstance(anchor.get("quote"), str) else ""
            key = (chunk_id, _normalise_text(quote))
            if key in seen_field:
                continue
            seen_field.add(key)
            if key not in evidence_by_key:
                chunk = chunk_map.get(chunk_id) or {}
                chunk_text = chunk.get("text") if isinstance(chunk.get("text"), str) else ""
                quality = (
                    "exact_quote" if quote and _normalise_text(quote) in _normalise_text(chunk_text)
                    else "quote_not_found" if quote else "quote_missing"
                )
                record = {
                    "evidence_id": _hash_id("E", [paper_id, chunk_id, _normalise_text(quote)]),
                    "paper_id": paper_id,
                    "chunk_id": chunk_id,
                    "quote": quote or None,
                    "quote_hash": hashlib.sha256(quote.encode("utf-8")).hexdigest() if quote else None,
                    "source_role": anchor.get("source_role") or "unknown",
                    "anchor_quality": quality,
                    "fields": [field],
                    "_anchor": dict(anchor),
                }
                evidence_by_key[key] = record
                evidence_records.append(record)
            elif field not in evidence_by_key[key]["fields"]:
                evidence_by_key[key]["fields"].append(field)

    claims_by_field: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        claims_by_field.setdefault(claim["field"], []).append(claim)
    links = []
    for evidence in evidence_records:
        anchor = evidence["_anchor"]
        for field in evidence["fields"]:
            matches = []
            for claim in claims_by_field.get(field, []):
                match = _mapping_score(claim, anchor)
                if match:
                    matches.append((claim, match[0], match[1]))
            if not matches:
                continue
            best_score = max(item[2] for item in matches)
            best = [item for item in matches if abs(item[2] - best_score) < 1e-9]
            ambiguous = len(best) > 1 and len({item[0]["fact_id"] for item in best}) > 1
            for claim, method, score in best:
                mapping_status = "ambiguous" if ambiguous else method
                if evidence.get("anchor_quality") != "exact_quote":
                    mapping_status = "source_quote_invalid"
                elif mapping_status in _RESOLVED_MAPPING:
                    compatibility = _scope_compatibility(claim, evidence.get("quote"))
                    if not compatibility["ok"]:
                        mapping_status = "scope_mismatch"
                link_id = _hash_id("L", [claim["claim_id"], evidence["evidence_id"]])
                links.append({
                    "link_id": link_id,
                    "claim_id": claim["claim_id"],
                    "fact_id": claim["fact_id"],
                    "evidence_id": evidence["evidence_id"],
                    "mapping_status": mapping_status,
                    "mapping_score": round(score, 6),
                })
                if mapping_status in _RESOLVED_MAPPING:
                    claim["allowed_link_ids"].append(link_id)
                else:
                    claim["candidate_link_ids"].append(link_id)

    # An unsupported current anchor is not deleted immediately.  First search
    # the same paper for an exact occurrence.  Anything less remains unresolved
    # for human/model review and cannot become a pass.
    for claim in claims:
        resolved = any(
            link["claim_id"] == claim["claim_id"]
            and link["mapping_status"] in _RESOLVED_MAPPING
            for link in links
        )
        if resolved:
            continue
        selected = _best_exact_anchor(claim, chunks, paper_id)
        reselect_status = "best_anchor_exact"
        if selected is None:
            registered = _registered_supporting_evidence(
                claim, evidence_records, chunk_map, paper_id
            )
            if registered is not None:
                link_id = _hash_id("L", [claim["claim_id"], registered["evidence_id"]])
                if not any(link["link_id"] == link_id for link in links):
                    links.append({
                        "link_id": link_id,
                        "claim_id": claim["claim_id"],
                        "fact_id": claim["fact_id"],
                        "evidence_id": registered["evidence_id"],
                        "mapping_status": "best_anchor_scope_exact",
                        "mapping_score": 1.0,
                        "anchor_reselected": True,
                    })
                claim["allowed_link_ids"].append(link_id)
                claim["anchor_reselected"] = True
                continue
            selected = _best_scope_anchor(claim, chunks, paper_id)
            reselect_status = "best_anchor_scope_exact"
        if selected is None:
            continue
        key = (selected["chunk_id"], _normalise_text(selected["quote"]))
        evidence = evidence_by_key.get(key)
        if evidence is None:
            quote = selected["quote"]
            evidence = {
                "evidence_id": _hash_id("E", [paper_id, selected["chunk_id"], _normalise_text(quote)]),
                "paper_id": paper_id,
                "chunk_id": selected["chunk_id"],
                "quote": quote,
                "quote_hash": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                "source_role": selected["source_role"],
                "anchor_quality": "exact_quote",
                "fields": [claim["field"]],
                "anchor_reselected": True,
                "reselect_reason": selected["reselect_reason"],
                "_anchor": selected,
            }
            evidence_by_key[key] = evidence
            evidence_records.append(evidence)
        link_id = _hash_id("L", [claim["claim_id"], evidence["evidence_id"]])
        if not any(link["link_id"] == link_id for link in links):
            links.append({
                "link_id": link_id,
                "claim_id": claim["claim_id"],
                "fact_id": claim["fact_id"],
                "evidence_id": evidence["evidence_id"],
                "mapping_status": reselect_status,
                "mapping_score": 1.0,
                "anchor_reselected": True,
            })
            claim["allowed_link_ids"].append(link_id)
            claim["anchor_reselected"] = True

    for claim in claims:
        claim["mapping_status"] = (
            "resolved" if claim["allowed_link_ids"] else "unresolved"
        )
        for atom in claim["atoms"]:
            atom["allowed_link_ids"] = list(claim["allowed_link_ids"])

    _unify_fact_ids(claims, links)

    occurrences = [{
        "occurrence_id": claim["occurrence_id"],
        "fact_id": claim["fact_id"],
        "claim_id": claim["claim_id"],
        "card_path": claim["target_path"],
        "field": claim["field"],
        "verdict": "not_reviewed",
    } for claim in claims]
    facts = []
    for fact_id in dict.fromkeys(claim["fact_id"] for claim in claims):
        fact_claims = [claim for claim in claims if claim["fact_id"] == fact_id]
        facts.append({
            "fact_id": fact_id,
            "canonical_signature": {
                "paper_id": paper_id,
                "normalised_claims": sorted(
                    {_normalise_text(claim["original_text"]) for claim in fact_claims}
                ),
            },
            "occurrence_ids": [claim["occurrence_id"] for claim in fact_claims],
            "verdict": "not_reviewed",
        })
    for evidence in evidence_records:
        evidence.pop("_anchor", None)
    view = {
        "schema_version": VIEW_SCHEMA_VERSION,
        "review_contract_id": REVIEW_CONTRACT_ID,
        "paper_id": paper_id,
        "card_schema_version": str(card.get("schema_version") or "unknown"),
        "card_sha256": hashlib.sha256(_canonical(card).encode("utf-8")).hexdigest(),
        "source_sha256": hashlib.sha256(_canonical(chunks).encode("utf-8")).hexdigest(),
        "claims": claims,
        "facts": facts,
        "occurrences": occurrences,
        "evidence_records": evidence_records,
        "claim_evidence_links": links,
    }
    log_reason(
        "M6",
        "M6 claim-evidence sidecar",
        f"claims={len(claims)} facts={len(facts)} evidence={len(evidence_records)} "
        f"links={len(links)} unresolved={sum(claim['mapping_status'] == 'unresolved' for claim in claims)}",
        event_category=None,
        context={
            "paper_id": paper_id,
            "schema_version": VIEW_SCHEMA_VERSION,
            "claim_count": len(claims),
            "fact_count": len(facts),
            "evidence_count": len(evidence_records),
            "link_count": len(links),
            "unresolved_mapping_count": sum(
                claim["mapping_status"] == "unresolved" for claim in claims
            ),
            "anchor_reselected_count": sum(
                bool(claim.get("anchor_reselected")) for claim in claims
            ),
        },
    )
    return view


def validate_claim_evidence_review(
    payload: Any,
    view: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate atom -> claim -> link -> evidence -> chunk binding."""
    if not isinstance(payload, dict) or set(payload) != {"claim_reviews"}:
        return {"ok": False, "errors": ["REVIEW_PAYLOAD_INVALID"], "model_verdict": "MODEL_REVIEW_INVALID"}
    errors, statuses = [], []
    claim_map = {item["claim_id"]: item for item in view.get("claims", []) if isinstance(item, dict)}
    link_map = {item["link_id"]: item for item in view.get("claim_evidence_links", []) if isinstance(item, dict)}
    evidence_map = {item["evidence_id"]: item for item in view.get("evidence_records", []) if isinstance(item, dict)}
    chunk_map = {item["chunk_id"]: item for item in chunks if isinstance(item, dict) and isinstance(item.get("chunk_id"), str)}
    paper_id = str(view.get("paper_id") or "")
    reviews = payload.get("claim_reviews")
    if not isinstance(reviews, list):
        reviews, errors = [], ["CLAIM_REVIEWS_NOT_LIST"]
    seen_claims, reviewed_atoms = set(), set()
    for review in reviews:
        if not isinstance(review, dict):
            errors.append("CLAIM_REVIEW_NOT_OBJECT")
            continue
        claim_id, claim = review.get("claim_id"), claim_map.get(review.get("claim_id"))
        if claim is None:
            errors.append("CLAIM_ID_UNKNOWN")
            continue
        if claim_id in seen_claims:
            errors.append("CLAIM_REVIEW_DUPLICATE")
            continue
        seen_claims.add(claim_id)
        atom_map = {atom["atom_id"]: atom for atom in claim.get("atoms", []) if atom.get("required") is True}
        atom_reviews = review.get("atom_reviews")
        if not isinstance(atom_reviews, list):
            errors.append("ATOM_REVIEWS_NOT_LIST")
            atom_reviews = []
        for atom_review in atom_reviews:
            if not isinstance(atom_review, dict):
                errors.append("ATOM_REVIEW_NOT_OBJECT")
                continue
            atom_id, atom = atom_review.get("atom_id"), atom_map.get(atom_review.get("atom_id"))
            if atom is None:
                errors.append("ATOM_ID_UNKNOWN")
                continue
            if atom_id in reviewed_atoms:
                errors.append("ATOM_REVIEW_DUPLICATE")
                continue
            reviewed_atoms.add(atom_id)
            status = atom_review.get("status")
            if status not in REVIEW_STATUSES:
                errors.append("ATOM_STATUS_INVALID")
                continue
            statuses.append(status)
            link_ids = atom_review.get("evidence_link_ids")
            if not isinstance(link_ids, list):
                errors.append("EVIDENCE_LINKS_NOT_LIST")
                link_ids = []
            if status == "SUPPORTED":
                if claim.get("mapping_status") != "resolved":
                    errors.append("MAPPING_UNRESOLVED")
                if not link_ids:
                    errors.append("EVIDENCE_LINK_REQUIRED")
            for link_id in link_ids:
                link = link_map.get(link_id)
                if link is None:
                    errors.append("EVIDENCE_LINK_NOT_FOUND")
                    continue
                if link.get("claim_id") != claim_id:
                    errors.append("EVIDENCE_LINK_CLAIM_MISMATCH")
                    continue
                if link_id not in atom.get("allowed_link_ids", []):
                    errors.append("EVIDENCE_LINK_NOT_ALLOWED_FOR_ATOM")
                if status == "SUPPORTED" and link.get("mapping_status") not in _RESOLVED_MAPPING:
                    errors.append("MAPPING_UNRESOLVED")
                evidence = evidence_map.get(link.get("evidence_id"))
                if evidence is None:
                    errors.append("EVIDENCE_RECORD_NOT_FOUND")
                    continue
                if evidence.get("anchor_quality") != "exact_quote":
                    errors.append("EVIDENCE_QUOTE_NOT_FOUND")
                chunk = chunk_map.get(evidence.get("chunk_id"))
                if chunk is None:
                    errors.append("EVIDENCE_CHUNK_NOT_FOUND")
                elif paper_id and chunk.get("paper_id") and chunk.get("paper_id") != paper_id:
                    errors.append("EVIDENCE_CROSS_PAPER")
                else:
                    quote = evidence.get("quote")
                    chunk_text = chunk.get("text")
                    if (
                        not isinstance(quote, str)
                        or not quote.strip()
                        or not isinstance(chunk_text, str)
                        or _normalise_text(quote) not in _normalise_text(chunk_text)
                    ):
                        errors.append("EVIDENCE_QUOTE_NOT_FOUND")
                    elif not _scope_compatibility(claim, quote)["ok"]:
                        errors.append("EVIDENCE_SCOPE_MISMATCH")
    required_atoms = {
        atom["atom_id"]
        for claim in claim_map.values()
        for atom in claim.get("atoms", [])
        if atom.get("required") is True
    }
    if seen_claims != set(claim_map):
        errors.append("REQUIRED_CLAIM_UNREVIEWED")
    if reviewed_atoms != required_atoms:
        errors.append("REQUIRED_ATOM_UNREVIEWED")
    errors = list(dict.fromkeys(errors))
    model_pass = not errors and statuses and all(status == "SUPPORTED" for status in statuses)
    receipt = {
        "ok": not errors,
        "errors": errors,
        "model_verdict": "MODEL_REVIEW_PASS" if model_pass else "MODEL_REVIEW_ISSUES",
        "required_claim_count": len(claim_map),
        "reviewed_claim_count": len(seen_claims),
        "required_atom_count": len(required_atoms),
        "reviewed_atom_count": len(reviewed_atoms),
        "unsupported_atom_count": sum(status != "SUPPORTED" for status in statuses),
        "payload": payload,
    }
    log_reason(
        "M6",
        "M6 claim-evidence binding validation",
        f"ok={receipt['ok']} claims={len(seen_claims)}/{len(claim_map)} "
        f"atoms={len(reviewed_atoms)}/{len(required_atoms)} errors={len(errors)}",
        event_category=None,
        context={
            "paper_id": paper_id,
            "ok": receipt["ok"],
            "model_verdict": receipt["model_verdict"],
            "error_codes": errors,
            "required_claim_count": len(claim_map),
            "reviewed_claim_count": len(seen_claims),
            "required_atom_count": len(required_atoms),
            "reviewed_atom_count": len(reviewed_atoms),
        },
    )
    return receipt


def repair_evidence_occurrence(
    view: dict[str, Any],
    chunks: list[dict[str, Any]],
    *,
    occurrence_id: str,
    replacement_chunk_id: str,
    replacement_quote: str,
    reason: str,
) -> dict[str, Any]:
    """Copy-on-write occurrence mutation, semantic demotion, and local reselect."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be a non-empty string")
    if not isinstance(replacement_quote, str) or not replacement_quote.strip():
        raise ValueError("replacement_quote must be a non-empty string")
    result = deepcopy(view)
    occurrences = {
        item.get("occurrence_id"): item
        for item in result.get("occurrences", [])
        if isinstance(item, dict)
    }
    occurrence = occurrences.get(occurrence_id)
    if occurrence is None:
        raise ValueError(f"unknown occurrence_id: {occurrence_id}")
    claims = {
        item.get("claim_id"): item
        for item in result.get("claims", [])
        if isinstance(item, dict)
    }
    claim = claims.get(occurrence.get("claim_id"))
    if claim is None:
        raise ValueError("occurrence claim is missing")
    paper_id = str(result.get("paper_id") or "")
    chunk_map = {
        item.get("chunk_id"): item
        for item in chunks
        if isinstance(item, dict) and isinstance(item.get("chunk_id"), str)
    }
    replacement_chunk = chunk_map.get(replacement_chunk_id)
    if replacement_chunk is None:
        raise ValueError("replacement chunk is missing")
    if replacement_chunk.get("paper_id") not in (None, "", paper_id):
        raise ValueError("replacement chunk belongs to another paper")
    chunk_text = replacement_chunk.get("text")
    if (
        not isinstance(chunk_text, str)
        or _normalise_text(replacement_quote) not in _normalise_text(chunk_text)
    ):
        raise ValueError("replacement quote is not present in replacement chunk")

    old_occurrence_id = occurrence_id
    new_occurrence_id = _hash_id(
        "O",
        [
            claim["fact_id"],
            claim["target_path"],
            claim["segment_index"],
            replacement_chunk_id,
            _normalise_text(replacement_quote),
        ],
    )
    occurrence["occurrence_id"] = new_occurrence_id
    occurrence["mutation_parent_occurrence_id"] = old_occurrence_id
    claim["occurrence_id"] = new_occurrence_id
    for fact in result.get("facts", []):
        if not isinstance(fact, dict) or fact.get("fact_id") != claim.get("fact_id"):
            continue
        fact["occurrence_ids"] = [
            new_occurrence_id if item == old_occurrence_id else item
            for item in fact.get("occurrence_ids", [])
        ]

    evidence_records = result.setdefault("evidence_records", [])
    links = result.setdefault("claim_evidence_links", [])
    evidence_map = {
        item.get("evidence_id"): item
        for item in evidence_records
        if isinstance(item, dict)
    }
    link_map = {
        item.get("link_id"): item
        for item in links
        if isinstance(item, dict)
    }
    old_allowed_link_ids = list(claim.get("allowed_link_ids", []))
    claim["allowed_link_ids"] = []
    for atom in claim.get("atoms", []):
        atom["allowed_link_ids"] = []

    injected_evidence_id = _hash_id(
        "E", [paper_id, replacement_chunk_id, _normalise_text(replacement_quote)]
    )
    injected_evidence = evidence_map.get(injected_evidence_id)
    compatibility = _scope_compatibility(claim, replacement_quote)
    if injected_evidence is None:
        injected_evidence = {
            "evidence_id": injected_evidence_id,
            "paper_id": paper_id,
            "chunk_id": replacement_chunk_id,
            "quote": replacement_quote,
            "quote_hash": hashlib.sha256(replacement_quote.encode("utf-8")).hexdigest(),
            "source_role": replacement_chunk.get("source_role") or "unknown",
            "anchor_quality": "exact_quote",
            "fields": [claim.get("field")],
            "scope_compatibility": compatibility,
            "mutation_candidate": True,
        }
        evidence_records.append(injected_evidence)
        evidence_map[injected_evidence_id] = injected_evidence
    injected_link_id = _hash_id("L", [claim["claim_id"], injected_evidence_id])
    injected_link = link_map.get(injected_link_id)
    if injected_link is None:
        injected_link = {
            "link_id": injected_link_id,
            "claim_id": claim["claim_id"],
            "fact_id": claim["fact_id"],
            "evidence_id": injected_evidence_id,
            "mapping_status": "explicit" if compatibility["ok"] else "scope_mismatch",
            "mapping_score": 1.0 if compatibility["ok"] else 0.0,
            "mutation_candidate": True,
            "scope_reason_codes": compatibility["reason_codes"],
        }
        links.append(injected_link)
        link_map[injected_link_id] = injected_link
    if compatibility["ok"]:
        claim["allowed_link_ids"].append(injected_link_id)
    else:
        claim.setdefault("candidate_link_ids", []).append(injected_link_id)
        claim["candidate_link_ids"] = list(dict.fromkeys(claim["candidate_link_ids"]))

    selected_evidence = None
    if not compatibility["ok"]:
        selected_evidence = _registered_supporting_evidence(
            claim,
            evidence_records,
            chunk_map,
            paper_id,
            excluded_evidence_ids={injected_evidence_id},
        )
        if selected_evidence is None:
            selected_anchor = _best_scope_anchor(claim, chunks, paper_id)
            if selected_anchor is not None:
                selected_key = (
                    selected_anchor["chunk_id"],
                    _normalise_text(selected_anchor["quote"]),
                )
                selected_evidence = next(
                    (
                        item
                        for item in evidence_records
                        if (
                            item.get("chunk_id"),
                            _normalise_text(item.get("quote")),
                        ) == selected_key
                    ),
                    None,
                )
                if selected_evidence is None:
                    quote = selected_anchor["quote"]
                    selected_evidence = {
                        "evidence_id": _hash_id(
                            "E", [paper_id, selected_anchor["chunk_id"], _normalise_text(quote)]
                        ),
                        "paper_id": paper_id,
                        "chunk_id": selected_anchor["chunk_id"],
                        "quote": quote,
                        "quote_hash": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                        "source_role": selected_anchor["source_role"],
                        "anchor_quality": "exact_quote",
                        "fields": [claim.get("field")],
                        "anchor_reselected": True,
                        "reselect_reason": selected_anchor["reselect_reason"],
                    }
                    evidence_records.append(selected_evidence)
                    evidence_map[selected_evidence["evidence_id"]] = selected_evidence

    selected_link_id = None
    if selected_evidence is not None:
        selected_link_id = _hash_id("L", [claim["claim_id"], selected_evidence["evidence_id"]])
        selected_link = link_map.get(selected_link_id)
        if selected_link is None:
            selected_link = {
                "link_id": selected_link_id,
                "claim_id": claim["claim_id"],
                "fact_id": claim["fact_id"],
                "evidence_id": selected_evidence["evidence_id"],
                "mapping_status": "best_anchor_scope_exact",
                "mapping_score": 1.0,
                "anchor_reselected": True,
            }
            links.append(selected_link)
            link_map[selected_link_id] = selected_link
        else:
            selected_link["mapping_status"] = "best_anchor_scope_exact"
            selected_link["anchor_reselected"] = True
        claim["allowed_link_ids"] = [selected_link_id]
        claim["anchor_reselected"] = True

    # Recheck every occurrence of the same fact against the supplied chunk snapshot.
    fact_claims = [
        item for item in result.get("claims", [])
        if isinstance(item, dict) and item.get("fact_id") == claim.get("fact_id")
    ]
    for fact_claim in fact_claims:
        usable = []
        for link_id in fact_claim.get("allowed_link_ids", []):
            link = link_map.get(link_id)
            evidence = evidence_map.get(link.get("evidence_id")) if link else None
            if evidence is None:
                continue
            if _registered_supporting_evidence(
                fact_claim,
                [evidence],
                chunk_map,
                paper_id,
            ) is not None:
                usable.append(link_id)
        fact_claim["allowed_link_ids"] = usable
        fact_claim["mapping_status"] = "resolved" if usable else "unresolved"
        for atom in fact_claim.get("atoms", []):
            atom["allowed_link_ids"] = list(usable)

    final_status = "RECOVERED" if claim["mapping_status"] == "resolved" else "FLAGGED_LOCAL"
    if final_status == "FLAGGED_LOCAL":
        result = apply_fact_verdicts(result, {claim["fact_id"]: "flagged"})

    diff = {
        "removed_allowed_link_ids": old_allowed_link_ids,
        "injected_candidate_link_id": injected_link_id,
        "injected_scope_reason_codes": compatibility["reason_codes"],
        "selected_allowed_link_id": selected_link_id,
    }
    audit = {
        "old_occurrence_id": old_occurrence_id,
        "new_occurrence_id": new_occurrence_id,
        "claim_id": claim["claim_id"],
        "fact_id": claim["fact_id"],
        "injected_link_id": injected_link_id,
        "selected_link_id": selected_link_id,
        "reason": reason,
        "diff": diff,
        "final_status": final_status,
    }
    log_reason(
        "M6",
        "M6 evidence occurrence mutation evaluated",
        f"occurrence={old_occurrence_id}->{new_occurrence_id} final={final_status}",
        event_category="verify",
        target=paper_id,
        context={
            "paper_id": paper_id,
            "claim_id": claim["claim_id"],
            "fact_id": claim["fact_id"],
            "old_occurrence_id": old_occurrence_id,
            "new_occurrence_id": new_occurrence_id,
            "reason_code": reason,
            "diff": diff,
            "final_status": final_status,
        },
    )
    return {"view": result, "audit": audit}


def apply_fact_verdicts(view: dict[str, Any], verdicts: dict[str, str]) -> dict[str, Any]:
    """Apply one verdict to every indexed occurrence of a per-card fact."""
    unknown = {value for value in verdicts.values() if value not in FACT_VERDICTS}
    if unknown:
        raise ValueError(f"unknown fact verdicts: {sorted(unknown)}")
    result = deepcopy(view)
    for collection in ("facts", "claims", "occurrences"):
        for item in result.get(collection, []):
            if item.get("fact_id") in verdicts:
                item["verdict"] = verdicts[item["fact_id"]]
    result["field_credibility"] = field_credibility_summaries(result)
    return result


def field_credibility_summaries(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for claim in view.get("claims", []):
        if isinstance(claim, dict):
            grouped.setdefault(str(claim.get("field") or "unknown"), []).append(claim)
    result = {}
    for field, claims in grouped.items():
        counts = {name: 0 for name in ("verified", "flagged", "removed", "not_reviewed")}
        for claim in claims:
            verdict = claim.get("verdict", "not_reviewed")
            if verdict in counts:
                counts[verdict] += 1
        status = (
            "verified_with_flags" if counts["flagged"]
            else "not_reviewed" if counts["not_reviewed"]
            else "verified_with_removals" if counts["verified"] and counts["removed"]
            else "verified" if counts["verified"]
            else "removed" if counts["removed"]
            else "unknown"
        )
        result[field] = {
            "field": field,
            "status": status,
            "counts": counts,
            "flagged_fact_ids": list(dict.fromkeys(
                claim["fact_id"] for claim in claims if claim.get("verdict") == "flagged"
            )),
        }
    return result


def render_reviewed_field(view: dict[str, Any], field: str) -> str:
    claims = sorted(
        (
            claim for claim in view.get("claims", [])
            if isinstance(claim, dict)
            and claim.get("field") == field
            and claim.get("verdict") in {"verified", "flagged"}
        ),
        key=lambda claim: (claim.get("order", 0), claim.get("claim_id", "")),
    )
    return " ".join(str(claim.get("original_text") or "").strip() for claim in claims).strip()
