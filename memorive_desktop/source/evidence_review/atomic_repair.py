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


def route_full_review_to_closure(full_receipt: dict[str, Any]) -> str:
    """Choose the only legal next step after one atomic Full review."""
    if not isinstance(full_receipt, dict):
        return HUMAN_REQUIRED
    if full_receipt.get("ok") is not True:
        errors = full_receipt.get("errors")
        payload = full_receipt.get("payload")
        if (
            isinstance(errors, list)
            and errors
            and set(errors).issubset(_RECOVERABLE_FULL_REVIEW_ERRORS)
            and isinstance(payload, dict)
        ):
            required_count = full_receipt.get("required_atom_count")
            reviewed_count = full_receipt.get("reviewed_atom_count")
            if (
                isinstance(required_count, int)
                and isinstance(reviewed_count, int)
                and 0 < required_count - reviewed_count <= _MAX_RECOVERABLE_UNREVIEWED_ATOMS
            ):
                return TARGETED_REPAIR
        return HUMAN_REQUIRED
    if full_receipt.get("model_verdict") == MODEL_REVIEW_PASS:
        return DELTA_PASS
    return TARGETED_REPAIR


def _payload(full_receipt: dict[str, Any]) -> dict[str, Any]:
    value = full_receipt.get("payload") if isinstance(full_receipt, dict) else None
    if isinstance(value, dict):
        return value
    if isinstance(full_receipt, dict) and "atom_reviews" in full_receipt:
        return full_receipt
    raise AtomicRepairContractError("full review receipt has no payload")


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


def _finding_id(kind: str, identity: str, row: dict[str, Any]) -> str:
    digest = canonical_hash({"kind": kind, "identity": identity, "row": row})[:16]
    return f"F-{digest}"


def _target_id(group_key: str) -> str:
    return f"T-{hashlib.sha256(group_key.encode('utf-8')).hexdigest()[:16]}"


def _validate_evidence_ids(ids: Any, chunks: dict[str, dict[str, Any]], label: str) -> list[str]:
    if not isinstance(ids, list):
        raise AtomicRepairContractError(f"{label}.evidence_chunk_ids must be a list")
    result: list[str] = []
    for chunk_id in ids:
        if not isinstance(chunk_id, str) or chunk_id not in chunks:
            raise AtomicRepairContractError(f"{label} cites a missing or cross-paper chunk: {chunk_id!r}")
        if chunk_id not in result:
            result.append(chunk_id)
    return result


def build_atomic_repair_bundle(
    card: dict[str, Any],
    full_view: dict[str, Any],
    full_receipt: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Convert one validated Full receipt into one strict repair bundle."""
    if route_full_review_to_closure(full_receipt) != TARGETED_REPAIR:
        raise AtomicRepairContractError("Full receipt is not eligible for targeted repair")
    paper_id = _paper_id(card)
    if full_view.get("paper_id") != paper_id:
        raise AtomicRepairContractError("Full review paper_id does not match Card")
    if full_view.get("card_sha256") != canonical_hash(card):
        raise AtomicRepairContractError("Full review authoritative Card hash does not match")
    chunk_by_id = _chunk_map(chunks, paper_id)
    claims = {
        item.get("claim_id"): item
        for item in full_view.get("claims", [])
        if isinstance(item, dict) and isinstance(item.get("claim_id"), str)
    }
    atoms = {
        atom.get("atom_id"): (claim, atom)
        for claim in claims.values()
        for atom in claim.get("atoms", [])
        if isinstance(atom, dict) and isinstance(atom.get("atom_id"), str)
    }
    payload = _payload(full_receipt)
    grouped: dict[str, dict[str, Any]] = {}

    def add(group_key: str, path: str, kind: str, finding: dict[str, Any]) -> None:
        target = grouped.setdefault(
            group_key,
            {
                "target_id": _target_id(group_key),
                "target_path": path,
                "target_kind": kind,
                "findings": [],
                "candidate_chunk_ids": [],
            },
        )
        target["findings"].append(finding)
        for chunk_id in finding["evidence_chunk_ids"]:
            if chunk_id not in target["candidate_chunk_ids"]:
                target["candidate_chunk_ids"].append(chunk_id)

    reviewed_atom_ids: set[tuple[str, str]] = set()
    for group in payload.get("supported_atom_groups", []):
        if not isinstance(group, dict) or not isinstance(group.get("atom_ids"), list):
            continue
        for atom_id in group["atom_ids"]:
            if atom_id in atoms:
                reviewed_atom_ids.add((atoms[atom_id][0]["claim_id"], atom_id))
    for index, review in enumerate(payload.get("atom_reviews", [])):
        if not isinstance(review, dict):
            raise AtomicRepairContractError(f"atom_reviews[{index}] is not an object")
        atom_id = review.get("atom_id")
        claim_id = review.get("claim_id")
        if atom_id not in atoms or claim_id not in claims or atoms[atom_id][0].get("claim_id") != claim_id:
            raise AtomicRepairContractError(f"atom review target is unknown or mismatched: {claim_id}/{atom_id}")
        claim, atom = atoms[atom_id]
        reviewed_atom_ids.add((claim_id, atom_id))
        path = str(claim.get("target_path") or "")
        claim_type = str(claim.get("claim_type") or "")
        is_anchor = claim_type.endswith("_source_anchor") or atom.get("atom_type") == "source_anchor"
        if is_anchor and _KEY_DATA_TARGET.fullmatch(path):
            group_key, kind = f"value:{path}", "key_data"
        elif is_anchor:
            field = _field_for_path(path)
            if field is None:
                raise AtomicRepairContractError(f"source-anchor issue has no field target: {path}")
            group_key, path, kind = f"anchor:{field}", field, "field_anchor"
        elif _LIST_TARGET.fullmatch(path):
            group_key, kind = f"value:{path}", "list_item"
        elif _KEY_DATA_TARGET.fullmatch(path):
            group_key, kind = f"value:{path}", "key_data"
        elif path in SCALAR_FIELDS:
            group_key, kind = f"value:{path}", "scalar"
        else:
            raise AtomicRepairContractError(f"failed atom has unsupported target: {path}")
        evidence_ids = _validate_evidence_ids(review.get("evidence_chunk_ids"), chunk_by_id, f"atom_reviews[{index}]")
        source_ids = atom.get("source_chunk_ids") if isinstance(atom.get("source_chunk_ids"), list) else []
        for chunk_id in source_ids:
            if chunk_id in chunk_by_id and chunk_id not in evidence_ids:
                evidence_ids.append(chunk_id)
        finding = {
            "finding_id": _finding_id("atom", f"{claim_id}/{atom_id}", review),
            "finding_type": "FAILED_ATOM",
            "claim_id": claim_id,
            "atom_id": atom_id,
            "atom_type": atom.get("atom_type"),
            "status": review.get("status"),
            "claim_text": claim.get("original_text"),
            "atom_text": atom.get("claim"),
            "reason": review.get("reason"),
            "evidence_chunk_ids": evidence_ids,
        }
        add(group_key, path, kind, finding)

    if "REQUIRED_ATOM_UNREVIEWED" in set(full_receipt.get("errors") or []):
        for claim_id, claim in sorted(claims.items()):
            for atom in claim.get("atoms", []):
                atom_id = atom.get("atom_id") if isinstance(atom, dict) else None
                if (
                    not isinstance(atom_id, str)
                    or atom.get("required") is not True
                    or (claim_id, atom_id) in reviewed_atom_ids
                ):
                    continue
                path = str(claim.get("target_path") or "")
                claim_type = str(claim.get("claim_type") or "")
                is_anchor = claim_type.endswith("_source_anchor") or atom.get("atom_type") == "source_anchor"
                if is_anchor and _KEY_DATA_TARGET.fullmatch(path):
                    group_key, target_path, kind = f"value:{path}", path, "key_data"
                elif is_anchor:
                    field = _field_for_path(path)
                    if field is None:
                        raise AtomicRepairContractError(f"unreviewed source-anchor atom has no field target: {path}")
                    group_key, target_path, kind = f"anchor:{field}", field, "field_anchor"
                elif _LIST_TARGET.fullmatch(path):
                    group_key, target_path, kind = f"value:{path}", path, "list_item"
                elif _KEY_DATA_TARGET.fullmatch(path):
                    group_key, target_path, kind = f"value:{path}", path, "key_data"
                elif path in SCALAR_FIELDS:
                    group_key, target_path, kind = f"value:{path}", path, "scalar"
                else:
                    raise AtomicRepairContractError(f"unreviewed required atom has unsupported target: {path}")
                evidence_ids = [
                    chunk_id
                    for chunk_id in atom.get("source_chunk_ids", [])
                    if isinstance(chunk_id, str) and chunk_id in chunk_by_id
                ]
                finding = {
                    "finding_id": _finding_id("unreviewed", f"{claim_id}/{atom_id}", atom),
                    "finding_type": "UNREVIEWED_REQUIRED_ATOM",
                    "claim_id": claim_id,
                    "atom_id": atom_id,
                    "atom_type": atom.get("atom_type"),
                    "status": "UNREVIEWED",
                    "claim_text": claim.get("original_text"),
                    "atom_text": atom.get("claim"),
                    "reason": "Full reviewer omitted a required atom; repair or explicit no_change is required.",
                    "evidence_chunk_ids": evidence_ids,
                }
                add(group_key, target_path, kind, finding)

    for index, row in enumerate(payload.get("missing_claims", [])):
        if not isinstance(row, dict):
            raise AtomicRepairContractError(f"missing_claims[{index}] is not an object")
        field = row.get("field")
        if field not in CORE_FIELDS:
            raise AtomicRepairContractError(f"missing_claims[{index}].field is invalid")
        evidence_ids = _validate_evidence_ids(row.get("evidence_chunk_ids"), chunk_by_id, f"missing_claims[{index}]")
        finding = {
            "finding_id": _finding_id("missing", f"{field}/{index}", row),
            "finding_type": "MISSING_CLAIM",
            "field": field,
            "issue_type": row.get("issue_type"),
            "description": row.get("description"),
            "evidence_chunk_ids": evidence_ids,
        }
        if field in LIST_FIELDS:
            add(f"append:{field}:{index}", field, "append_list_item", finding)
        else:
            add(f"value:{field}", field, "scalar", finding)

    for index, row in enumerate(payload.get("cross_field_conflicts", [])):
        if not isinstance(row, dict):
            raise AtomicRepairContractError(f"cross_field_conflicts[{index}] is not an object")
        evidence_ids = _validate_evidence_ids(row.get("evidence_chunk_ids"), chunk_by_id, f"cross_field_conflicts[{index}]")
        fields = row.get("fields") if isinstance(row.get("fields"), list) else []
        finding = {
            "finding_id": _finding_id("conflict", str(index), row),
            "finding_type": "CROSS_FIELD_CONFLICT",
            "fields": fields,
            "description": row.get("description"),
            "evidence_chunk_ids": evidence_ids,
        }
        add(f"conflict:{index}", "__cross_field__", "cross_field_conflict", finding)

    if not grouped:
        raise AtomicRepairContractError("Full receipt contains no repairable findings")

    action_map = {
        "list_item": ["replace", "reanchor", "no_change"],
        "key_data": ["replace", "reanchor", "no_change"],
        "scalar": ["replace", "reanchor", "no_change"],
        "field_anchor": ["reanchor", "no_change"],
        "append_list_item": ["append", "no_change"],
        "cross_field_conflict": ["no_change"],
    }
    targets = []
    source_ids: list[str] = []
    for group_key in sorted(grouped):
        target = grouped[group_key]
        target["current_value"] = _target_value(card, target["target_path"], target["target_kind"])
        field = _field_for_path(target["target_path"])
        target["current_anchors"] = _anchors(card, field) if field else []
        target["allowed_actions"] = action_map[target["target_kind"]]
        for chunk_id in target["current_anchors"]:
            if isinstance(chunk_id, dict):
                value = chunk_id.get("chunk_id")
                if value in chunk_by_id and value not in target["candidate_chunk_ids"]:
                    target["candidate_chunk_ids"].append(value)
        for chunk_id in target["candidate_chunk_ids"]:
            if chunk_id not in source_ids:
                source_ids.append(chunk_id)
        targets.append(target)

    return {
        "contract_version": "atomic-targeted-repair-v1",
        "paper_id": paper_id,
        "authoritative_card_sha256": canonical_hash(card),
        "full_review_receipt_sha256": canonical_hash(payload),
        "rules": {
            "single_consolidated_repair": True,
            "target_whitelist_only": True,
            "same_paper_literal_evidence_only": True,
            "second_full_review_forbidden": True,
            "next_review": "DELTA_ONLY",
        },
        "targets": targets,
        "source_chunks": {
            chunk_id: {
                "chunk_id": chunk_id,
                "section": chunk_by_id[chunk_id].get("section"),
                "page": chunk_by_id[chunk_id].get("page"),
                "text": str(chunk_by_id[chunk_id].get("text") or ""),
            }
            for chunk_id in source_ids
        },
    }


def build_atomic_repair_prompt(bundle: dict[str, Any], template: str) -> str:
    marker = "__ATOMIC_REPAIR_BUNDLE_JSON__"
    if template.count(marker) != 1:
        raise AtomicRepairContractError("atomic repair prompt must contain exactly one bundle marker")
    return template.replace(marker, json.dumps(bundle, ensure_ascii=False, indent=2))


def build_atomic_repair_request(prompt: str) -> dict[str, Any]:
    """Build the single frozen DeepSeek request for an atomic repair bundle."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise AtomicRepairContractError("atomic repair prompt must be non-empty")
    return {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": ATOMIC_REPAIR_MAX_TOKENS,
        "timeout": ATOMIC_REPAIR_TIMEOUT_SECONDS,
    }


def atomic_repair_response_is_truncated(
    response: dict[str, Any],
    *,
    max_tokens: int = ATOMIC_REPAIR_MAX_TOKENS,
) -> bool:
    """Only provider termination metadata proves output truncation.

    max_tokens is a legacy suggestion, not the actual dispatched limit.
    A complete response can legitimately exceed that suggestion.
    """
    if not isinstance(response, dict):
        return False
    raw = response.get("raw") if isinstance(response.get("raw"), dict) else {}
    choices = raw.get("choices") if isinstance(raw.get("choices"), list) else []
    if choices and isinstance(choices[0], dict) and choices[0].get("finish_reason") == "length":
        return True
    return raw.get('stop_reason') == 'max_tokens' or raw.get('done_reason') == 'length'


def _parse_atomic_repair_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    if start < 0:
        raise AtomicRepairContractError("DeepSeek atomic repair response contains no JSON object")
    depth = 0
    quoted = False
    escaped = False
    end = None
    for index, char in enumerate(text[start:], start=start):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise AtomicRepairContractError("DeepSeek atomic repair JSON object is truncated")
    try:
        value = json.loads(text[start:end])
    except json.JSONDecodeError as exc:
        raise AtomicRepairContractError(f"DeepSeek atomic repair JSON is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise AtomicRepairContractError("DeepSeek atomic repair response must be a JSON object")
    return value


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


def validate_atomic_repairs(
    response: dict[str, Any],
    bundle: dict[str, Any],
    chunks: list[dict[str, Any]],
    *,
    degrade_invalid_targets: bool = False,
) -> list[dict[str, Any]]:
    """Validate exact target/finding coverage and literal same-paper anchors."""
    if not isinstance(response, dict) or set(response) != {"repairs"} or not isinstance(response.get("repairs"), list):
        raise AtomicRepairContractError("DeepSeek response must contain only a repairs list")
    paper_id = str(bundle.get("paper_id") or "")
    chunk_by_id = _chunk_map(chunks, paper_id)
    targets = {target["target_id"]: target for target in bundle.get("targets", [])}
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for index, raw in enumerate(response["repairs"]):
        if not isinstance(raw, dict) or set(raw) != _REPAIR_KEYS:
            raise AtomicRepairContractError(f"repairs[{index}] has an invalid shape")
        target_id = raw.get("target_id")
        if target_id not in targets or target_id in seen:
            raise AtomicRepairContractError(f"repairs[{index}] target is unknown or duplicated")
        seen.add(target_id)
        target = targets[target_id]
        expected_findings = {item["finding_id"] for item in target["findings"]}
        finding_ids = raw.get("finding_ids")
        if not isinstance(finding_ids, list) or set(finding_ids) != expected_findings or len(finding_ids) != len(expected_findings):
            raise AtomicRepairContractError(f"repairs[{index}] does not cover the exact original findings")
        action = raw.get("action")
        reason = raw.get("reason")
        anchors_raw = raw.get("anchors")
        replacement = raw.get("replacement")
        anchor_errors: list[str] = []
        anchors: list[dict[str, Any]] = []
        contract_downgrade = None
        try:
            if action not in target["allowed_actions"]:
                raise AtomicRepairContractError(f"repairs[{index}].action is not allowed for this target")
            if not isinstance(reason, str) or not reason.strip():
                raise AtomicRepairContractError(f"repairs[{index}].reason must be non-empty")
            if not isinstance(anchors_raw, list):
                raise AtomicRepairContractError(f"repairs[{index}].anchors must be a list")
            for anchor_index, item in enumerate(anchors_raw):
                try:
                    anchors.append(_validate_anchor(item, target, chunk_by_id, anchor_index))
                except AtomicRepairContractError as exc:
                    if not degrade_invalid_targets:
                        raise
                    anchor_errors.append(str(exc))
            if action == "no_change":
                if replacement is not None or anchors:
                    raise AtomicRepairContractError("no_change requires replacement=null and anchors=[]")
            elif action == "reanchor":
                if replacement is not None or not anchors:
                    raise AtomicRepairContractError("reanchor requires replacement=null and literal anchors")
            elif action in ("replace", "append"):
                if not anchors:
                    raise AtomicRepairContractError(f"{action} requires at least one literal anchor")
                if target["target_kind"] == "key_data":
                    if not isinstance(replacement, dict):
                        raise AtomicRepairContractError("key_data replacement must be an object")
                    for key in _KEY_DATA_REQUIRED:
                        if not isinstance(replacement.get(key), str) or not replacement[key].strip():
                            raise AtomicRepairContractError(f"key_data replacement.{key} must be non-empty")
                    chunk_id = replacement["chunk_id"]
                    if chunk_id not in target["candidate_chunk_ids"] or chunk_id not in chunk_by_id:
                        raise AtomicRepairContractError("key_data replacement chunk is outside the evidence whitelist")
                    if replacement["quote"] not in str(chunk_by_id[chunk_id].get("text") or ""):
                        raise AtomicRepairContractError("key_data replacement quote is not literal evidence")
                    replacement = copy.deepcopy(replacement)
                    replacement["needs_review"] = True
                elif not isinstance(replacement, str) or not replacement.strip():
                    raise AtomicRepairContractError(f"{action} replacement must be a non-empty string")
            if anchor_errors:
                contract_downgrade = {
                    "kind": "INVALID_ANCHORS_DROPPED",
                    "count": len(anchor_errors),
                    "errors": anchor_errors,
                }
        except AtomicRepairContractError as exc:
            if not degrade_invalid_targets:
                raise
            contract_downgrade = {
                "kind": "TARGET_DOWNGRADED_TO_NO_CHANGE",
                "original_action": action,
                "errors": [*anchor_errors, str(exc)],
            }
            action = "no_change"
            replacement = None
            anchors = []
            reason = f"Local contract downgrade: {exc}"
        validated.append(
            {
                "target_id": target_id,
                "finding_ids": list(finding_ids),
                "action": action,
                "replacement": copy.deepcopy(replacement),
                "anchors": anchors,
                "reason": reason.strip(),
                "contract_downgrade": contract_downgrade,
            }
        )
    if seen != set(targets):
        raise AtomicRepairContractError("DeepSeek repair response does not cover every target exactly once")
    return validated


def validate_atomic_repair_gateway_response(
    response: dict[str, Any],
    bundle: dict[str, Any],
    chunks: list[dict[str, Any]],
    *,
    max_tokens: int = ATOMIC_REPAIR_MAX_TOKENS,
) -> list[dict[str, Any]]:
    """Fail closed on truncation, then parse and validate the exact repair set."""
    if not isinstance(response, dict):
        raise AtomicRepairContractError("DeepSeek atomic repair gateway response must be an object")
    if atomic_repair_response_is_truncated(response, max_tokens=max_tokens):
        message = f"DeepSeek atomic repair response was truncated at max_tokens={max_tokens}"
        repairs = []
        for target in bundle.get("targets", []):
            repairs.append(
                {
                    "target_id": target["target_id"],
                    "finding_ids": [item["finding_id"] for item in target.get("findings", [])],
                    "action": "no_change",
                    "replacement": None,
                    "anchors": [],
                    "reason": message,
                    "contract_downgrade": {
                        "kind": "TRUNCATED_RESPONSE_ALL_TARGETS_NO_CHANGE",
                        "errors": [message],
                    },
                }
            )
        if not repairs:
            raise AtomicRepairContractError("truncated repair response has no frozen targets")
        return repairs
    return validate_atomic_repairs(
        _parse_atomic_repair_json(str(response.get("text") or "")),
        bundle,
        chunks,
        degrade_invalid_targets=True,
    )


def _set_field_anchors(card: dict[str, Any], field: str, anchors: list[dict[str, Any]], *, replace: bool) -> None:
    source_anchor = card.setdefault("source_anchor", {})
    by_field = source_anchor.setdefault("by_field", {})
    if replace:
        by_field[field] = copy.deepcopy(anchors)
        return
    current = by_field.setdefault(field, [])
    known = {(item.get("chunk_id"), item.get("quote")) for item in current if isinstance(item, dict)}
    for anchor in anchors:
        key = (anchor.get("chunk_id"), anchor.get("quote"))
        if key not in known:
            current.append(copy.deepcopy(anchor))
            known.add(key)


def apply_atomic_repairs(
    card: dict[str, Any],
    bundle: dict[str, Any],
    repairs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply only validated whitelist targets and produce an authoritative diff."""
    if canonical_hash(card) != bundle.get("authoritative_card_sha256"):
        raise AtomicRepairContractError("Card changed after the Full-review freeze")
    targets = {target["target_id"]: target for target in bundle.get("targets", [])}
    working = copy.deepcopy(card)
    changes: list[dict[str, Any]] = []
    for repair in repairs:
        target = targets.get(repair.get("target_id"))
        if target is None:
            raise AtomicRepairContractError("validated repair references an unknown target")
        path = target["target_path"]
        kind = target["target_kind"]
        before = _target_value(working, path, kind)
        realized_path = path
        action = repair["action"]
        if action == "replace":
            list_match = _LIST_TARGET.fullmatch(path)
            key_match = _KEY_DATA_TARGET.fullmatch(path)
            if list_match:
                working[list_match.group(1)][int(list_match.group(2))] = repair["replacement"]
                _set_field_anchors(working, list_match.group(1), repair["anchors"], replace=False)
            elif key_match:
                working["key_data"][int(key_match.group(1))] = copy.deepcopy(repair["replacement"])
            elif path in SCALAR_FIELDS:
                working[path] = repair["replacement"]
                _set_field_anchors(working, path, repair["anchors"], replace=False)
            else:
                raise AtomicRepairContractError(f"replace cannot map target: {path}")
        elif action == "append":
            values = working.get(path)
            if kind != "append_list_item" or not isinstance(values, list):
                raise AtomicRepairContractError(f"append cannot map target: {path}")
            realized_path = f"{path}[{len(values)}]"
            values.append(repair["replacement"])
            _set_field_anchors(working, path, repair["anchors"], replace=False)
        elif action == "reanchor":
            key_match = _KEY_DATA_TARGET.fullmatch(path)
            if key_match:
                first = repair["anchors"][0]
                entry = working["key_data"][int(key_match.group(1))]
                entry["chunk_id"] = first["chunk_id"]
                entry["quote"] = first["quote"]
            else:
                field = _field_for_path(path)
                if field is None:
                    raise AtomicRepairContractError(f"reanchor cannot map target: {path}")
                _set_field_anchors(working, field, repair["anchors"], replace=True)
        elif action != "no_change":
            raise AtomicRepairContractError(f"unsupported validated action: {action}")
        after = _target_value(working, realized_path if action == "append" else path, "list_item" if action == "append" else kind)
        change = {
                "target_id": target["target_id"],
                "target_path": path,
                "realized_target_path": realized_path,
                "target_kind": kind,
                "finding_ids": list(repair["finding_ids"]),
                "action": action,
                "reason": repair["reason"],
                "before": before,
                "after": after,
            }
        if repair.get("contract_downgrade") is not None:
            change["contract_downgrade"] = copy.deepcopy(repair["contract_downgrade"])
        changes.append(change)
    before_text = json.dumps(card, ensure_ascii=False, sort_keys=True, indent=2).splitlines()
    after_text = json.dumps(working, ensure_ascii=False, sort_keys=True, indent=2).splitlines()
    authoritative_diff = "\n".join(
        difflib.unified_diff(before_text, after_text, fromfile="card.before.json", tofile="card.after.json", lineterm="")
    )
    return {
        "card": working,
        "before_card_sha256": canonical_hash(card),
        "after_card_sha256": canonical_hash(working),
        "authoritative_diff": authoritative_diff,
        "changes": changes,
    }


def _claim_field(claim: dict[str, Any]) -> str | None:
    claim_type = str(claim.get("claim_type") or "")
    if claim_type.endswith("_source_anchor"):
        candidate = claim_type[: -len("_source_anchor")]
        return candidate if candidate in CORE_FIELDS else None
    target = str(claim.get("target_path") or "")
    return _field_for_path(target)


def build_atomic_delta_view(
    before_card: dict[str, Any],
    application: dict[str, Any],
    full_view: dict[str, Any],
    full_receipt: dict[str, Any],
    bundle: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a closure-aware Delta package; never a second whole-Card review."""
    after_card = application.get("card")
    if not isinstance(after_card, dict):
        raise AtomicRepairContractError("repair application has no Card")
    if application.get("before_card_sha256") != canonical_hash(before_card):
        raise AtomicRepairContractError("repair application before hash mismatch")
    current_view = build_atomic_review_view(after_card, chunks)
    targets = {target["target_id"]: target for target in bundle.get("targets", [])}
    selected: dict[str, dict[str, Any]] = {}
    claim_target_map: dict[str, list[str]] = defaultdict(list)
    synthetic_claims: list[dict[str, Any]] = []

    for change in application.get("changes", []):
        target_id = change["target_id"]
        target = targets[target_id]
        kind = target["target_kind"]
        realized_path = change.get("realized_target_path") or target["target_path"]
        field = _field_for_path(realized_path)
        matched = []
        for claim in current_view.get("claims", []):
            claim_path = str(claim.get("target_path") or "")
            claim_type = str(claim.get("claim_type") or "")
            take = False
            if kind in ("scalar", "field_anchor") and field is not None:
                take = claim_path == field or claim_type == f"{field}_source_anchor"
            elif kind in ("list_item", "append_list_item"):
                take = claim_path == realized_path or (field is not None and claim_type == f"{field}_source_anchor")
            elif kind == "key_data":
                take = claim_path == realized_path
            elif kind == "cross_field_conflict":
                conflict_fields = {
                    value
                    for finding in target["findings"]
                    for value in finding.get("fields", [])
                    if value in CORE_FIELDS
                }
                take = _claim_field(claim) in conflict_fields
            if take:
                claim_id = claim["claim_id"]
                selected[claim_id] = copy.deepcopy(claim)
                if target_id not in claim_target_map[claim_id]:
                    claim_target_map[claim_id].append(target_id)
                matched.append(claim_id)
        requires_pending_claim = change.get("action") == "no_change" and any(
            finding.get("finding_type") in {"MISSING_CLAIM", "CROSS_FIELD_CONFLICT"}
            for finding in target["findings"]
        )
        if not matched or requires_pending_claim:
            finding_text = "; ".join(str(item.get("description") or item.get("reason") or "") for item in target["findings"])
            claim_id = f"PENDING-{target_id}"
            source_ids = list(dict.fromkeys(target.get("candidate_chunk_ids", [])))
            synthetic = {
                "claim_id": claim_id,
                "target_path": target["target_path"],
                "claim_type": "pending_missing_claim",
                "original_text": finding_text or "Required repair remains unapplied.",
                "item_index": None,
                "segment_index": 0,
                "atomization_status": "ATOMIZATION_COMPLETE",
                "atoms": [
                    {
                        "atom_id": f"{claim_id}-resolution-00",
                        "atom_type": "omission_resolution",
                        "claim": finding_text or "Required repair remains unapplied.",
                        "source_chunk_ids": source_ids,
                        "required": True,
                    }
                ],
            }
            synthetic_claims.append(synthetic)
            selected[claim_id] = synthetic
            claim_target_map[claim_id].append(target_id)

    source_ids: list[str] = []
    for claim in selected.values():
        for atom in claim.get("atoms", []):
            for chunk_id in atom.get("source_chunk_ids", []):
                if chunk_id not in source_ids:
                    source_ids.append(chunk_id)
    for target in bundle.get("targets", []):
        for chunk_id in target.get("candidate_chunk_ids", []):
            if chunk_id not in source_ids:
                source_ids.append(chunk_id)
    chunk_by_id = _chunk_map(chunks, _paper_id(after_card))
    return {
        "schema_version": "atomic-delta-view-v2",
        "review_contract_id": "card-review-atomic-v2",
        "paper_id": _paper_id(after_card),
        "whole_card_scope": False,
        "second_full_review_forbidden": True,
        "before_card_sha256": application["before_card_sha256"],
        "after_card_sha256": application["after_card_sha256"],
        "full_review_receipt_sha256": bundle["full_review_receipt_sha256"],
        "original_findings": [copy.deepcopy(item) for target in bundle["targets"] for item in target["findings"]],
        "changes": copy.deepcopy(application.get("changes", [])),
        "authoritative_diff": application.get("authoritative_diff", ""),
        "claims": list(selected.values()),
        "claim_target_map": dict(claim_target_map),
        "direct_source_chunks": {
            chunk_id: copy.deepcopy(chunk_by_id[chunk_id])
            for chunk_id in source_ids
            if chunk_id in chunk_by_id
        },
        "synthetic_pending_claim_ids": [item["claim_id"] for item in synthetic_claims],
        "full_view_sha256": canonical_hash(full_view),
        "full_review_payload_sha256": canonical_hash(_payload(full_receipt)),
    }


def classify_delta_resolution(
    delta_payload: dict[str, Any],
    delta_view: dict[str, Any],
    bundle: dict[str, Any],
    application: dict[str, Any],
) -> dict[str, Any]:
    """Map Delta results back to original repair targets deterministically."""
    reviews = {
        item.get("claim_id"): item
        for item in delta_payload.get("claim_reviews", [])
        if isinstance(item, dict) and isinstance(item.get("claim_id"), str)
    }
    targets = {target["target_id"]: target for target in bundle.get("targets", [])}
    change_by_target = {item["target_id"]: item for item in application.get("changes", [])}
    target_claims: dict[str, list[str]] = defaultdict(list)
    for claim_id, target_ids in delta_view.get("claim_target_map", {}).items():
        for target_id in target_ids:
            target_claims[target_id].append(claim_id)
    resolved = []
    unresolved = []
    for target_id, target in targets.items():
        claim_ids = target_claims.get(target_id, [])
        failed_claim_ids = [claim_id for claim_id in claim_ids if reviews.get(claim_id, {}).get("final_status") != "RESOLVED"]
        change = change_by_target.get(target_id, {})
        pending_claim_ids = [claim_id for claim_id in claim_ids if claim_id.startswith("PENDING-")]
        if pending_claim_ids:
            failed_claim_ids = list(dict.fromkeys([*failed_claim_ids, *pending_claim_ids]))
        elif not claim_ids and not failed_claim_ids:
            failed_claim_ids = [f"PENDING-{target_id}"]
        row = {
            "target_id": target_id,
            "target_path": target["target_path"],
            "target_kind": target["target_kind"],
            "realized_target_path": change.get("realized_target_path", target["target_path"]),
            "finding_ids": [item["finding_id"] for item in target["findings"]],
            "failed_claim_ids": failed_claim_ids,
            "final_statuses": {claim_id: reviews.get(claim_id, {}).get("final_status") for claim_id in claim_ids},
        }
        (unresolved if failed_claim_ids else resolved).append(row)
    return {"resolved_targets": resolved, "unresolved_targets": unresolved}


def governed_omission_precheck(
    card: dict[str, Any],
    chunks: list[dict[str, Any]],
    view: dict[str, Any],
    *,
    omitted_fields: set[str] | None = None,
) -> dict[str, Any]:
    """Allow only explicitly tombstoned, fully omitted fields past the local gate."""
    if omitted_fields is None:
        omission_locations = {
            item.get("location")
            for item in card.get("omissions", [])
            if isinstance(item, dict)
        }
        governed = {
            field
            for field in CORE_FIELDS
            if (card.get(field) is None or card.get(field) == [])
            and f"by_field.{field}" in omission_locations
        }
    else:
        governed = set(omitted_fields)
    if not governed.issubset(CORE_FIELDS):
        raise AtomicRepairContractError("governed omissions contain an unknown core field")
    if any(card.get(field) is not None and card.get(field) != [] for field in governed):
        raise AtomicRepairContractError("governed omission field still contains Card data")
    raw = deterministic_precheck(card, chunks, view)
    governed_issues = [
        issue
        for issue in raw["issues"]
        if issue.get("issue_type") == "PLACEHOLDER" and issue.get("target_path") in governed
    ]
    issues = [issue for issue in raw["issues"] if issue not in governed_issues]
    return {
        **raw,
        "ok": not issues,
        "issues": issues,
        "governed_omitted_fields": sorted(governed),
        "governed_omission_issues": governed_issues,
    }


def prune_unresolved_targets(
    card: dict[str, Any],
    resolution: dict[str, Any],
    delta_payload: dict[str, Any],
    delta_view: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Logically remove separable residuals while retaining complete tombstones."""
    working = copy.deepcopy(card)
    claim_by_id = {
        item.get("claim_id"): item
        for item in delta_view.get("claims", [])
        if isinstance(item, dict) and isinstance(item.get("claim_id"), str)
    }
    tombstones: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    list_drops: dict[str, set[int]] = defaultdict(set)
    key_drops: set[int] = set()
    anchor_drops: dict[str, set[tuple[str, str | None]]] = defaultdict(set)
    scalar_drops: dict[str, list[tuple[str, str]]] = defaultdict(list)
    governed_omitted_fields: set[str] = set()

    for target in resolution.get("unresolved_targets", []):
        kind = target.get("target_kind")
        path = str(target.get("realized_target_path") or target.get("target_path") or "")
        handled = False
        for claim_id in target.get("failed_claim_ids", []):
            claim = claim_by_id.get(claim_id)
            if not claim:
                continue
            claim_type = str(claim.get("claim_type") or "")
            claim_path = str(claim.get("target_path") or path)
            if claim_type.endswith("_source_anchor"):
                field = claim_type[: -len("_source_anchor")]
                source_quote = None
                atoms = claim.get("atoms") if isinstance(claim.get("atoms"), list) else []
                if atoms and isinstance(atoms[0], dict):
                    source_quote = atoms[0].get("source_quote")
                anchor_drops[field].add((str(claim.get("original_text") or ""), source_quote))
                handled = True
            elif _LIST_TARGET.fullmatch(claim_path):
                match = _LIST_TARGET.fullmatch(claim_path)
                list_drops[match.group(1)].add(int(match.group(2)))
                handled = True
            elif _KEY_DATA_TARGET.fullmatch(claim_path):
                key_drops.add(int(_KEY_DATA_TARGET.fullmatch(claim_path).group(1)))
                handled = True
            elif claim_type == "pending_missing_claim":
                issue_types = set()
                for finding_id in target.get("finding_ids", []):
                    for finding in delta_view.get("original_findings", []):
                        if finding.get("finding_id") == finding_id:
                            issue_types.add(finding.get("issue_type"))
                if "CORE_OMISSION" in issue_types:
                    blockers.append({"target": path, "reason": "unresolved core omission", "claim_id": claim_id})
                else:
                    tombstones.append({"target": path, "kind": "unresolved_omission", "claim_id": claim_id, "finding_ids": target.get("finding_ids", [])})
                handled = True
            elif claim_path in SCALAR_FIELDS:
                original_text = claim.get("original_text")
                if not isinstance(original_text, str) or not original_text.strip():
                    blockers.append({
                        "target": claim_path,
                        "reason": "failed scalar claim has no exact deletion text",
                        "claim_id": claim_id,
                    })
                else:
                    scalar_drops[claim_path].append((claim_id, original_text))
                handled = True
        if handled:
            continue
        list_match = _LIST_TARGET.fullmatch(path)
        key_match = _KEY_DATA_TARGET.fullmatch(path)
        if kind in ("list_item", "append_list_item") and list_match:
            list_drops[list_match.group(1)].add(int(list_match.group(2)))
        elif kind == "key_data" and key_match:
            key_drops.add(int(key_match.group(1)))
        else:
            blockers.append({"target": path, "reason": "unresolved target is not safely separable"})

    for field, refs in anchor_drops.items():
        current = _anchors(working, field)
        retained = [
            item for item in current
            if (str(item.get("chunk_id") or ""), item.get("quote")) not in refs
        ]
        removed = [item for item in current if item not in retained]
        if not removed:
            blockers.append({"target": field, "reason": "failed source anchor could not be mapped for deletion"})
            continue
        _set_field_anchors(working, field, retained, replace=True)
        for item in removed:
            tombstones.append({"target": field, "kind": "source_anchor", "removed_value": item})

    for field, indexes in list_drops.items():
        values = working.get(field)
        if not isinstance(values, list):
            blockers.append({"target": field, "reason": "list residual target is no longer a list"})
            continue
        for index in sorted(indexes, reverse=True):
            if index >= len(values):
                blockers.append({"target": f"{field}[{index}]", "reason": "list residual index is out of range"})
                continue
            removed = values.pop(index)
            tombstones.append({"target": f"{field}[{index}]", "kind": "list_item", "removed_value": removed})
        if not values:
            working[field] = None
            governed_omitted_fields.add(field)

    values = working.get("key_data")
    if key_drops and not isinstance(values, list):
        blockers.append({"target": "key_data", "reason": "key_data residual target is no longer a list"})
    elif isinstance(values, list):
        for index in sorted(key_drops, reverse=True):
            if index >= len(values):
                blockers.append({"target": f"key_data[{index}]", "reason": "key_data residual index is out of range"})
                continue
            removed = values.pop(index)
            tombstones.append({"target": f"key_data[{index}]", "kind": "key_data", "removed_value": removed})

    for field, claims in scalar_drops.items():
        current = working.get(field)
        if not isinstance(current, str):
            blockers.append({"target": field, "reason": "scalar residual target is no longer text"})
            continue
        unique_claims: list[tuple[str, str]] = []
        seen_claims: set[tuple[str, str]] = set()
        for claim_id, original_text in claims:
            key = (claim_id, original_text)
            if key not in seen_claims:
                seen_claims.add(key)
                unique_claims.append(key)
        spans: list[tuple[int, int, str, str]] = []
        field_blockers: list[dict[str, Any]] = []
        for claim_id, original_text in unique_claims:
            start = current.find(original_text)
            if start < 0 or current.find(original_text, start + 1) >= 0:
                field_blockers.append({
                    "target": field,
                    "reason": "failed scalar claim could not be uniquely mapped for deletion",
                    "claim_id": claim_id,
                })
                continue
            spans.append((start, start + len(original_text), claim_id, original_text))
        ordered_spans = sorted(spans)
        if any(left[1] > right[0] for left, right in zip(ordered_spans, ordered_spans[1:])):
            field_blockers.append({
                "target": field,
                "reason": "failed scalar claim deletion spans overlap",
            })
        if field_blockers:
            blockers.extend(field_blockers)
            continue
        candidate = current
        for start, end, _claim_id, _original_text in reversed(ordered_spans):
            candidate = candidate[:start] + candidate[end:]
        candidate = re.sub(r"[ \t]{2,}", " ", candidate).strip()
        if not candidate or not any(character.isalnum() for character in candidate):
            working[field] = None
            governed_omitted_fields.add(field)
        else:
            working[field] = candidate
        for _start, _end, claim_id, original_text in ordered_spans:
            tombstones.append({
                "target": field,
                "kind": "scalar_claim",
                "claim_id": claim_id,
                "removed_value": original_text,
            })

    if not any(
        (isinstance(value, str) and value.strip())
        or (isinstance(value, list) and any(isinstance(item, str) and item.strip() for item in value))
        for value in (working.get(field) for field in CORE_FIELDS)
    ):
        blockers.append({"target": "card", "reason": "residual deletion would empty every core field"})

    current_view = build_atomic_review_view(working, chunks)
    precheck = governed_omission_precheck(
        working,
        chunks,
        current_view,
        omitted_fields=governed_omitted_fields,
    )
    if not precheck["ok"]:
        blockers.append({"target": "local_precheck", "reason": "post-prune deterministic gate failed", "issues": precheck["issues"]})
    for tombstone in tombstones:
        if tombstone.get("kind") == "unresolved_omission":
            tombstone.setdefault("status", "not_added_to_active_card")
        else:
            tombstone.setdefault("status", "logically_removed_from_active_card")
        tombstone.setdefault(
            "retention",
            "Original value and provenance are retained in this tombstone; source and review artifacts are unchanged.",
        )
    outcome = HUMAN_REQUIRED if blockers else (PASS_WITH_TOMBSTONES if tombstones else DELTA_PASS)
    return {
        "outcome": outcome,
        "card": working,
        "tombstones": tombstones,
        "blockers": blockers,
        "governed_omitted_fields": sorted(governed_omitted_fields),
        "deletion_semantics": {
            "mode": "logical_removal_only",
            "active_card_effect": "Failed content is excluded from usable Card fields.",
            "retention": "Original content remains in tombstones, omissions, diffs, Cards, and review artifacts.",
        },
        "post_prune_precheck": precheck,
        "card_sha256": canonical_hash(working),
    }
