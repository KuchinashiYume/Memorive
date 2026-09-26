"""Provider-neutral Atomic Review View and deterministic pre-review gates."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any


CORE_FIELDS = (
    "research_question",
    "research_object",
    "method",
    "key_results",
    "author_conclusion",
    "boundary_conditions",
)

ATOMIZATION_REVIEW_REQUIRED = "ATOMIZATION_REVIEW_REQUIRED"
ATOMIZATION_COMPLETE = "ATOMIZATION_COMPLETE"

_FIELD_ATOM = {
    "research_question": "research_relation",
    "research_object": "research_object",
    "method": "method_step",
    "key_results": "conclusion_scope",
    "author_conclusion": "conclusion_scope",
    "boundary_conditions": "applicability",
}
_FIELD_PREFIX = {
    "research_question": "RQ",
    "research_object": "RO",
    "method": "MT",
    "key_results": "KR",
    "author_conclusion": "AC",
    "boundary_conditions": "BC",
}
_PLACEHOLDERS = {
    "", "n/a", "na", "none", "null", "not given", "not available", "unknown",
    "待补充", "暂无", "未知", "未提供",
}
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:\s*(?:±|→|–|—|\bto\b)\s*[-+]?\d+(?:\.\d+)?)?\s*%?", re.I)
_UNIT = re.compile(
    r"(?i)(?<![A-Za-z])(?:mg|kg|µg|ug|mL|ml|µL|ul|mmol|mol|mbar|bar|kPa|MPa|Pa|atm|°C|℃|hr|hours?|days?|min)(?:\s*/\s*[A-Za-z0-9^−-]+)?(?![A-Za-z])"
)
_PHASE = re.compile(
    r"(?i)(?:Period\s*\d+(?:\s*\([^)]*\))?|Phase\s*\d+(?:\s*\([^)]*\))?|Day\s*\d+(?:\s*(?:–|—|-|to)\s*\d+)?|\b(?:inhibition|recovery|stabili[sz]ation|pre[- ]shock|post[- ]shock)\s+(?:phase|stage)\b)"
)
_SAMPLE_SIZE = re.compile(r"(?i)(?:\bn\s*[=:]\s*|sample size\s*(?:of|[=:])\s*)(\d+)")
_DIRECTION = re.compile(r"(?i)\b(increas(?:e|ed|ing)|decreas(?:e|ed|ing)|higher|lower|rise|rose|fall|fell|positive|negative)\b")
_CLAIM_STRENGTH = re.compile(r"(?i)\b(may|might|could|suggest(?:s|ed)?|associate(?:d|s)?|prove(?:s|d)?|cause(?:s|d)?|eliminate(?:s|d)?|definitive)\b")
_INSTRUMENT = re.compile(r"\b(?:PANDAseq|PERMANOVA|QIIME|UCHIME|UCLUST|MiSeq|HPLC|GC|AER-?200|Acorn\s+pH\s*5)\b", re.I)
_TRUNCATION = re.compile(r"(?i)(?:\[truncated\]|<truncated>|\.\.\.$|…$)")


def _quote_match_text(value: str) -> str:
    """Normalize presentation-only Markdown while preserving textual content."""
    return " ".join(value.replace("_", "").replace("*", "").split())


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def split_free_text(text: str) -> tuple[list[str], str]:
    """Split only on visible sentence/list boundaries; preserve uncertain text verbatim."""
    stripped = text.strip()
    if not stripped:
        return [], ATOMIZATION_COMPLETE
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    if any(stripped.count(left) != stripped.count(right) for left, right in pairs):
        return [stripped], ATOMIZATION_REVIEW_REQUIRED
    pieces = [piece.strip() for piece in re.split(r"(?<=[.!?])\s+|\s*;\s*|\n\s*(?:[-*]\s*)?", stripped) if piece.strip()]
    if not pieces:
        return [stripped], ATOMIZATION_REVIEW_REQUIRED
    return pieces, ATOMIZATION_COMPLETE


def _paper_id(card: dict[str, Any]) -> str:
    value = card.get("paper_id")
    if not value and isinstance(card.get("source_anchor"), dict):
        value = card["source_anchor"].get("paper_id")
    return value if isinstance(value, str) else ""


def _normalize_anchor_objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        if isinstance(value.get("chunk_ids"), list):
            return [
                {"chunk_id": chunk_id, "quote": None, "section_page": value.get("section_page")}
                for chunk_id in value["chunk_ids"]
            ]
        if "chunk_id" in value:
            return [dict(value)]
    return []


def _field_anchors(card: dict[str, Any], field: str) -> list[dict[str, Any]]:
    source_anchor = card.get("source_anchor")
    by_field = source_anchor.get("by_field") if isinstance(source_anchor, dict) else None
    return _normalize_anchor_objects(by_field.get(field)) if isinstance(by_field, dict) else []


def _new_atom(
    claim_id: str,
    atom_type: str,
    claim: str,
    anchors: list[dict[str, Any]],
    index: int,
    *,
    required: bool = True,
) -> dict[str, Any]:
    chunk_ids = [item.get("chunk_id") for item in anchors if isinstance(item.get("chunk_id"), str) and item.get("chunk_id")]
    return {
        "atom_id": f"{claim_id}-{atom_type}-{index:02d}",
        "atom_type": atom_type,
        "claim": claim,
        "source_chunk_ids": list(dict.fromkeys(chunk_ids)),
        "required": required,
    }


def _derived_atoms(claim_id: str, text: str, anchors: list[dict[str, Any]], start: int = 1) -> list[dict[str, Any]]:
    atoms: list[dict[str, Any]] = []
    index = start
    seen: set[tuple[str, str]] = set()
    patterns = (
        ("value", _NUMBER),
        ("unit", _UNIT),
        ("phase_time", _PHASE),
        ("direction", _DIRECTION),
        ("claim_strength", _CLAIM_STRENGTH),
        ("instrument", _INSTRUMENT),
    )
    for atom_type, pattern in patterns:
        for match in pattern.finditer(text):
            value = match.group(0).strip()
            key = (atom_type, value.casefold())
            if not value or key in seen:
                continue
            seen.add(key)
            atoms.append(_new_atom(claim_id, atom_type, value, anchors, index))
            index += 1
    for match in _SAMPLE_SIZE.finditer(text):
        value = match.group(1)
        key = ("sample_size", value)
        if key not in seen:
            seen.add(key)
            atoms.append(_new_atom(claim_id, "sample_size", value, anchors, index))
            index += 1
    return atoms


def _text_claims(card: dict[str, Any], field: str) -> list[dict[str, Any]]:
    raw = card.get(field)
    anchors = _field_anchors(card, field)
    values: Iterable[Any] = [] if raw is None or raw == [] else (raw if isinstance(raw, list) else [raw])
    claims: list[dict[str, Any]] = []
    counter = 0
    for item_index, value in enumerate(values):
        if not isinstance(value, str):
            text = "" if value is None else str(value)
            pieces, status = [text], ATOMIZATION_REVIEW_REQUIRED
        else:
            pieces, status = split_free_text(value)
            if not pieces:
                pieces = [value]
        for piece_index, piece in enumerate(pieces):
            claim_id = f"{_FIELD_PREFIX[field]}-{counter:03d}"
            counter += 1
            target = f"{field}[{item_index}]" if isinstance(raw, list) else field
            atoms = [_new_atom(claim_id, _FIELD_ATOM[field], piece, anchors, 0)]
            atoms.extend(_derived_atoms(claim_id, piece, anchors))
            claims.append(
                {
                    "claim_id": claim_id,
                    "target_path": target,
                    "claim_type": field,
                    "original_text": piece,
                    "item_index": item_index if isinstance(raw, list) else None,
                    "segment_index": piece_index,
                    "atomization_status": status,
                    "atoms": atoms,
                }
            )
    linked_claim_ids = [claim["claim_id"] for claim in claims]
    seen_anchors: set[tuple[str, str]] = set()
    for anchor_index, anchor in enumerate(anchors):
        chunk_id = anchor.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            continue
        quote = anchor.get("quote") if isinstance(anchor.get("quote"), str) else ""
        key = (chunk_id, quote)
        if key in seen_anchors:
            continue
        seen_anchors.add(key)
        claim_id = f"{_FIELD_PREFIX[field]}-A{anchor_index:03d}"
        atom = _new_atom(claim_id, "source_anchor", chunk_id, [anchor], 0)
        atom["source_quote"] = anchor.get("quote")
        atom["section_page"] = anchor.get("section_page")
        claims.append(
            {
                "claim_id": claim_id,
                "target_path": field,
                "claim_type": f"{field}_source_anchor",
                "original_text": chunk_id,
                "linked_claim_ids": linked_claim_ids,
                "item_index": None,
                "segment_index": 0,
                "atomization_status": ATOMIZATION_COMPLETE,
                "atoms": [atom],
            }
        )
    return claims


def _labeled_value(text: str, label: str) -> str | None:
    match = re.search(rf"(?i)(?:^|\|)\s*{re.escape(label)}\s*:\s*([^|]+)", text)
    if not match:
        return None
    value = match.group(1).strip()
    return None if value.casefold() in _PLACEHOLDERS else value


def _key_data_claim(entry: Any, index: int) -> dict[str, Any]:
    claim_id = f"KD-{index:03d}"
    data = entry if isinstance(entry, dict) else {"value": entry}
    anchor = {
        "chunk_id": data.get("chunk_id"),
        "quote": data.get("quote"),
        "section_page": data.get("section_page"),
    }
    anchors = [anchor] if anchor.get("chunk_id") else []
    atoms: list[dict[str, Any]] = []

    def add(atom_type: str, value: Any, *, required: bool = True) -> None:
        if value is None:
            return
        text = str(value).strip()
        if not text or text.casefold() in _PLACEHOLDERS:
            return
        atoms.append(_new_atom(claim_id, atom_type, text, anchors, len(atoms), required=required))

    add("value", data.get("value"))
    add("unit", data.get("unit"))
    add("parameter", data.get("metric"))
    sample = str(data.get("sample") or "")
    add("sample_control", _labeled_value(sample, "sample/control"))
    add("phase_time", _labeled_value(sample, "phase/time"))
    add("comparison", _labeled_value(sample, "comparator"))
    add("intervention", _labeled_value(sample, "subprocess"))
    stat = str(data.get("stat") or "")
    add("statistical_scope", _labeled_value(stat, "stat_type"))
    add("sample_size", _labeled_value(stat, "n"))
    add("statistical_scope", _labeled_value(stat, "significance"))
    if anchor.get("chunk_id"):
        atom = _new_atom(claim_id, "source_anchor", str(anchor["chunk_id"]), anchors, len(atoms))
        atom["source_quote"] = anchor.get("quote")
        atom["section_page"] = anchor.get("section_page")
        atoms.append(atom)
    return {
        "claim_id": claim_id,
        "target_path": f"key_data[{index}]",
        "claim_type": "key_data",
        "original_text": json.dumps(data, ensure_ascii=False, sort_keys=True),
        "item_index": index,
        "segment_index": 0,
        "atomization_status": ATOMIZATION_COMPLETE if isinstance(entry, dict) else ATOMIZATION_REVIEW_REQUIRED,
        "atoms": atoms,
    }


def build_atomic_review_view(card: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(card, dict):
        raise TypeError("card must be a mapping")
    if not isinstance(chunks, list):
        raise TypeError("chunks must be a list")
    claims: list[dict[str, Any]] = []
    for field in CORE_FIELDS:
        claims.extend(_text_claims(card, field))
    key_data = card.get("key_data")
    if isinstance(key_data, list):
        claims.extend(_key_data_claim(entry, index) for index, entry in enumerate(key_data))
    return {
        "schema_version": "atomic-review-view-v2",
        "review_contract_id": "card-review-atomic-v2",
        "paper_id": _paper_id(card),
        "card_version": str(card.get("card_version") or card.get("version") or "unknown"),
        "source_version": str(card.get("source_version") or _canonical_hash(chunks)),
        "card_sha256": _canonical_hash(card),
        "source_sha256": _canonical_hash(chunks),
        "claims": claims,
    }


def _issue(issue_type: str, target_path: str, detail: str, *, chunk_id: str | None = None) -> dict[str, Any]:
    result = {"issue_type": issue_type, "target_path": target_path, "detail": detail, "source": "local"}
    if chunk_id is not None:
        result["chunk_id"] = chunk_id
    return result


def deterministic_precheck(card: dict[str, Any], chunks: list[dict[str, Any]], view: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    paper_id = _paper_id(card)
    chunk_map: dict[str, dict[str, Any]] = {}
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            issues.append(_issue("MALFORMED_CHUNK", f"chunks[{index}]", "chunk is not a mapping"))
            continue
        chunk_id = chunk.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            issues.append(_issue("MALFORMED_CHUNK", f"chunks[{index}].chunk_id", "chunk_id missing"))
            continue
        if chunk_id in chunk_map:
            issues.append(_issue("DUPLICATE_CHUNK_ID", f"chunks[{index}].chunk_id", "duplicate chunk_id", chunk_id=chunk_id))
        chunk_map[chunk_id] = chunk

    for field in CORE_FIELDS:
        value = card.get(field)
        if value is None or (isinstance(value, str) and value.strip().casefold() in _PLACEHOLDERS):
            issues.append(_issue("PLACEHOLDER", field, "required field is empty or a placeholder"))
        if isinstance(value, list) and not value:
            issues.append(_issue("PLACEHOLDER", field, "required list field is empty"))

    for claim in view.get("claims", []):
        target = str(claim.get("target_path") or "unknown")
        if claim.get("atomization_status") == ATOMIZATION_REVIEW_REQUIRED:
            issues.append(_issue(ATOMIZATION_REVIEW_REQUIRED, target, "claim could not be uniquely atomized"))
        original_text = claim.get("original_text")
        if isinstance(original_text, str) and _TRUNCATION.search(original_text.strip()):
            issues.append(_issue("POSSIBLE_TRUNCATION", target, "claim ends with a truncation marker"))
        for atom in claim.get("atoms", []):
            atom_id = str(atom.get("atom_id") or target)
            source_ids = atom.get("source_chunk_ids") if isinstance(atom.get("source_chunk_ids"), list) else []
            if atom.get("required") is True and not source_ids:
                issues.append(_issue("REQUIRED_ATOM_NO_ANCHOR", atom_id, "required atom has no candidate source anchor"))
            for chunk_id in source_ids:
                chunk = chunk_map.get(chunk_id)
                if chunk is None:
                    issues.append(_issue("CHUNK_NOT_FOUND", atom_id, "anchor does not resolve", chunk_id=str(chunk_id)))
                    continue
                chunk_paper = chunk.get("paper_id")
                if paper_id and chunk_paper and chunk_paper != paper_id:
                    issues.append(_issue("CROSS_PAPER_CHUNK", atom_id, "anchor belongs to another paper", chunk_id=str(chunk_id)))
            if atom.get("atom_type") == "source_anchor":
                chunk_id = atom.get("claim")
                chunk = chunk_map.get(chunk_id)
                quote = atom.get("source_quote")
                if chunk is not None and isinstance(quote, str) and quote.strip():
                    text = chunk.get("text") if isinstance(chunk.get("text"), str) else ""
                    if quote not in text and _quote_match_text(quote) not in _quote_match_text(text):
                        issues.append(_issue("QUOTE_NOT_IN_CHUNK", atom_id, "quoted text is not a verbatim chunk substring", chunk_id=str(chunk_id)))
    return {
        "schema_version": "atomic-precheck-v1",
        "paper_id": paper_id,
        "ok": not issues,
        "issues": issues,
        "checked_claims": len(view.get("claims", [])),
        "checked_atoms": sum(len(claim.get("atoms", [])) for claim in view.get("claims", [])),
    }
