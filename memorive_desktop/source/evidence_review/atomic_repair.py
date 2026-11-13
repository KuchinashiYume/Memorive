"""Closed-loop repair helpers for provider-neutral atomic Card reviews.

The full reviewer runs once.  Its complete finding set is sent to the original
distillation model in one whitelisted repair bundle.  The same reviewer then
receives a Delta package containing the original findings, before/after
values, an authoritative diff, current atoms, and source evidence.  This
module deliberately has no whole-Card regeneration or second Full-review
route.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from .atomic_review import CORE_FIELDS, build_atomic_review_view, deterministic_precheck
from .reviewer_contract import MODEL_REVIEW_PASS


TARGETED_REPAIR = "TARGETED_REPAIR"
DELTA_PASS = "DELTA_PASS"
PASS_WITH_TOMBSTONES = "PASS_WITH_TOMBSTONES"
HUMAN_REQUIRED = "HUMAN_REQUIRED"

# DeepSeek reasoning tokens and the visible JSON share the same output budget.
# The 8k budget used by the first acceptance runner was exhausted by a real
# 23-target repair (finish_reason=length), so atomic repair uses the existing
# EVIDENCE_EXTRACTION long-output tier and a matching network allowance.
ATOMIC_REPAIR_MAX_TOKENS = 32768
ATOMIC_REPAIR_TIMEOUT_SECONDS = 600

LIST_FIELDS = frozenset(("research_object", "method", "boundary_conditions"))
SCALAR_FIELDS = frozenset(set(CORE_FIELDS) - set(LIST_FIELDS))
_LIST_TARGET = re.compile(r"^(research_object|method|boundary_conditions)\[(\d+)\]$")
_KEY_DATA_TARGET = re.compile(r"^key_data\[(\d+)\]$")
_REPAIR_KEYS = frozenset(("target_id", "finding_ids", "action", "replacement", "anchors", "reason"))
_KEY_DATA_REQUIRED = ("value", "unit", "metric", "sample", "stat", "quote", "chunk_id")
_RECOVERABLE_FULL_REVIEW_ERRORS = frozenset({"REQUIRED_ATOM_UNREVIEWED"})
_MAX_RECOVERABLE_UNREVIEWED_ATOMS = 8


class AtomicRepairContractError(ValueError):
    """Raised when the targeted repair contract is incomplete or unsafe."""


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _paper_id(card: dict[str, Any]) -> str:
    value = card.get("paper_id")
    if not value and isinstance(card.get("source_anchor"), dict):
        value = card["source_anchor"].get("paper_id")
    if not isinstance(value, str) or not value:
        raise AtomicRepairContractError("Card paper_id is missing")
    return value


def _chunk_map(chunks: list[dict[str, Any]], paper_id: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(chunks):
        if not isinstance(item, dict):
            raise AtomicRepairContractError(f"chunks[{index}] is not an object")
        chunk_id = item.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise AtomicRepairContractError(f"chunks[{index}].chunk_id is missing")
        if chunk_id in result:
            raise AtomicRepairContractError(f"duplicate chunk_id: {chunk_id}")
        chunk_paper = item.get("paper_id")
        if chunk_paper and chunk_paper != paper_id:
            continue
        result[chunk_id] = item
    return result


def _anchors(card: dict[str, Any], field: str) -> list[dict[str, Any]]:
    source_anchor = card.get("source_anchor")
    by_field = source_anchor.get("by_field") if isinstance(source_anchor, dict) else None
    value = by_field.get(field) if isinstance(by_field, dict) else None
    return copy.deepcopy(value) if isinstance(value, list) else []


def _field_for_path(path: str) -> str | None:
    if path in CORE_FIELDS:
        return path
    match = _LIST_TARGET.fullmatch(path)
    if match:
        return match.group(1)
    return None


def _target_value(card: dict[str, Any], path: str, kind: str) -> Any:
    if kind == "field_anchor":
        field = _field_for_path(path)
        return {"field_value": copy.deepcopy(card.get(field)), "anchors": _anchors(card, str(field))}
    if path in CORE_FIELDS:
        return copy.deepcopy(card.get(path))
    list_match = _LIST_TARGET.fullmatch(path)
    if list_match:
        values = card.get(list_match.group(1))
        index = int(list_match.group(2))
        if not isinstance(values, list) or index >= len(values):
            raise AtomicRepairContractError(f"review target does not exist: {path}")
        return copy.deepcopy(values[index])
    key_match = _KEY_DATA_TARGET.fullmatch(path)
    if key_match:
        values = card.get("key_data")
        index = int(key_match.group(1))
        if not isinstance(values, list) or index >= len(values):
            raise AtomicRepairContractError(f"review target does not exist: {path}")
        return copy.deepcopy(values[index])
    if kind == "append_list_item" and path in LIST_FIELDS:
        return None
    if kind == "cross_field_conflict":
        return None
    raise AtomicRepairContractError(f"unsupported target path: {path}")


def _validate_anchor(anchor: Any, target: dict[str, Any], chunks: dict[str, dict[str, Any]], index: int) -> dict[str, Any]:
    if not isinstance(anchor, dict) or set(anchor) != {"chunk_id", "quote", "section_page"}:
        raise AtomicRepairContractError(f"anchors[{index}] has an invalid shape")
    chunk_id = anchor.get("chunk_id")
    quote = anchor.get("quote")
    if chunk_id not in target["candidate_chunk_ids"] or chunk_id not in chunks:
        raise AtomicRepairContractError(f"anchors[{index}] is outside the target evidence whitelist")
    text = str(chunks[chunk_id].get("text") or "")
    if not isinstance(quote, str) or not quote or quote not in text:
        raise AtomicRepairContractError(f"anchors[{index}].quote is not a literal source substring")
    section_page = anchor.get("section_page")
    if section_page is not None and not isinstance(section_page, str):
        raise AtomicRepairContractError(f"anchors[{index}].section_page must be string or null")
    return {"chunk_id": chunk_id, "quote": quote, "section_page": section_page}
