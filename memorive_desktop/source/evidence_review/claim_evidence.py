"""Additive Core claim-evidence sidecar and binding validator."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Iterable

from runtime_log import log_reason

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
        "EVIDENCE_REVIEW",
        "EVIDENCE_REVIEW claim-evidence binding validation",
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
