"""Structural validation and local pass composition for card-review-atomic-v2."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


MODEL_REVIEW_PASS = "MODEL_REVIEW_PASS"
CARD_REVIEW_PASS = "CARD_REVIEW_PASS"
MODEL_REVIEW_ISSUES = "MODEL_REVIEW_ISSUES"

_FORBIDDEN_CONTRACT_TERMS = ("sonnet", "anthropic", "openai", "terra", "adaptive", "thinking", "price")


def load_reviewer_contract(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    lowered = text.casefold()
    found = [term for term in _FORBIDDEN_CONTRACT_TERMS if term in lowered]
    if found:
        raise ValueError(f"reviewer contract is not provider-neutral: {found}")
    value = json.loads(text)
    if not isinstance(value, dict) or value.get("review_contract_id") != "card-review-atomic-v2":
        raise ValueError("unexpected reviewer contract")
    return value


def _chunk_map(chunks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        item["chunk_id"]: item
        for item in chunks
        if isinstance(item, dict) and isinstance(item.get("chunk_id"), str) and item.get("chunk_id")
    }


def _required_atoms(view: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    result: dict[str, tuple[str, dict[str, Any]]] = {}
    for claim in view.get("claims", []):
        if not isinstance(claim, dict):
            continue
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str):
            continue
        for atom in claim.get("atoms", []):
            if isinstance(atom, dict) and atom.get("required") is True and isinstance(atom.get("atom_id"), str):
                result[atom["atom_id"]] = (claim_id, atom)
    return result


def required_atom_manifest(view: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the exact, stable Full-review coverage checklist."""
    return [
        {
            "claim_id": claim_id,
            "atom_id": atom_id,
            "atom_type": atom.get("atom_type"),
            "source_chunk_ids": list(atom.get("source_chunk_ids") or []),
        }
        for atom_id, (claim_id, atom) in sorted(_required_atoms(view).items())
    ]


def missing_required_atom_ids(payload: Any, view: dict[str, Any]) -> list[str]:
    """Find required atoms absent from both compact support groups and issue rows."""
    required = set(_required_atoms(view))
    reviewed: set[str] = set()
    if isinstance(payload, dict):
        for group in payload.get("supported_atom_groups", []):
            if isinstance(group, dict) and isinstance(group.get("atom_ids"), list):
                reviewed.update(item for item in group["atom_ids"] if isinstance(item, str))
        for row in payload.get("atom_reviews", []):
            if isinstance(row, dict) and isinstance(row.get("atom_id"), str):
                reviewed.add(row["atom_id"])
    return sorted(required - reviewed)


def build_full_review_completion_view(
    view: dict[str, Any],
    atom_ids: list[str],
) -> dict[str, Any]:
    """Create a Delta-like view containing only omitted Full-review atoms."""
    wanted = set(atom_ids)
    required = _required_atoms(view)
    if not wanted or not wanted.issubset(required):
        raise ValueError("completion atom IDs must be a non-empty required-atom subset")
    completion = copy.deepcopy(view)
    claims = []
    for claim in completion.get("claims", []):
        atoms = [atom for atom in claim.get("atoms", []) if atom.get("atom_id") in wanted]
        if atoms:
            claim["atoms"] = atoms
            claims.append(claim)
    completion["claims"] = claims
    completion["coverage_completion"] = {
        "whole_card_scope": False,
        "required_atom_ids": sorted(wanted),
        "required_atom_count": len(wanted),
    }
    return completion


def merge_full_review_completion_payload(
    base_payload: dict[str, Any],
    completion_payload: dict[str, Any],
) -> dict[str, Any]:
    """Merge only atom coverage from a bounded completion review."""
    if not isinstance(base_payload, dict) or not isinstance(completion_payload, dict):
        raise ValueError("Full-review completion payloads must be objects")
    merged = copy.deepcopy(base_payload)
    merged.setdefault("supported_atom_groups", []).extend(
        copy.deepcopy(completion_payload.get("supported_atom_groups") or [])
    )
    merged.setdefault("atom_reviews", []).extend(
        copy.deepcopy(completion_payload.get("atom_reviews") or [])
    )
    return merged


def _evidence_errors(
    evidence_ids: Any,
    chunks: dict[str, dict[str, Any]],
    paper_id: str,
) -> list[str]:
    if not isinstance(evidence_ids, list):
        return ["EVIDENCE_NOT_LIST"]
    errors: list[str] = []
    for chunk_id in evidence_ids:
        if not isinstance(chunk_id, str) or chunk_id not in chunks:
            errors.append("EVIDENCE_CHUNK_NOT_FOUND")
            continue
        chunk_paper = chunks[chunk_id].get("paper_id")
        if paper_id and chunk_paper and chunk_paper != paper_id:
            errors.append("EVIDENCE_CROSS_PAPER")
    return errors


def validate_full_review(
    payload: Any,
    view: dict[str, Any],
    chunks: list[dict[str, Any]],
    contract: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["FULL_NOT_OBJECT"], "model_verdict": "MODEL_REVIEW_INVALID"}
    allowed_top = {"coverage_complete", "coverage", "supported_atom_groups", "atom_reviews", "missing_claims", "cross_field_conflicts"}
    if set(payload) - allowed_top:
        errors.append("FULL_UNKNOWN_TOP_LEVEL_KEY")
    if payload.get("coverage_complete") is not True:
        errors.append("COVERAGE_NOT_COMPLETE")
    coverage = payload.get("coverage")
    core_fields = list(contract["core_fields"])
    if not isinstance(coverage, dict) or list(coverage) != core_fields:
        errors.append("COVERAGE_FIELDS_INCOMPLETE")
    else:
        for status in coverage.values():
            if status not in contract["coverage_statuses"]:
                errors.append("COVERAGE_STATUS_INVALID")

    required = _required_atoms(view)
    reviewed: dict[str, dict[str, Any]] = {}
    chunk_by_id = _chunk_map(chunks)
    paper_id = str(view.get("paper_id") or "")
    supported_groups = payload.get("supported_atom_groups")
    if not isinstance(supported_groups, list):
        errors.append("SUPPORTED_ATOM_GROUPS_NOT_LIST")
        supported_groups = []
    for group in supported_groups:
        if not isinstance(group, dict):
            errors.append("SUPPORTED_ATOM_GROUP_NOT_OBJECT")
            continue
        evidence_ids = group.get("evidence_chunk_ids")
        errors.extend(_evidence_errors(evidence_ids, chunk_by_id, paper_id))
        atom_ids = group.get("atom_ids")
        if not isinstance(atom_ids, list) or not atom_ids:
            errors.append("SUPPORTED_ATOM_IDS_INVALID")
            continue
        for atom_id in atom_ids:
            if atom_id not in required:
                errors.append("ATOM_ID_UNKNOWN")
                continue
            if atom_id in reviewed:
                errors.append("ATOM_REVIEW_DUPLICATE")
                continue
            reviewed[atom_id] = {
                "status": "SUPPORTED",
                "evidence_chunk_ids": evidence_ids,
            }
    reviews = payload.get("atom_reviews")
    if not isinstance(reviews, list):
        errors.append("ATOM_REVIEWS_NOT_LIST")
        reviews = []
    for review in reviews:
        if not isinstance(review, dict):
            errors.append("ATOM_REVIEW_NOT_OBJECT")
            continue
        atom_id = review.get("atom_id")
        claim_id = review.get("claim_id")
        if atom_id not in required:
            errors.append("ATOM_ID_UNKNOWN")
            continue
        if atom_id in reviewed:
            errors.append("ATOM_REVIEW_DUPLICATE")
            continue
        if claim_id != required[atom_id][0]:
            errors.append("ATOM_CLAIM_MISMATCH")
        if review.get("status") not in contract["atom_statuses"] or review.get("status") == "SUPPORTED":
            errors.append("ATOM_STATUS_INVALID")
        errors.extend(_evidence_errors(review.get("evidence_chunk_ids"), chunk_by_id, paper_id))
        reviewed[atom_id] = review
    if set(reviewed) != set(required):
        errors.append("REQUIRED_ATOM_UNREVIEWED")

    missing_claims = payload.get("missing_claims")
    conflicts = payload.get("cross_field_conflicts")
    if not isinstance(missing_claims, list):
        errors.append("MISSING_CLAIMS_NOT_LIST")
        missing_claims = []
    if not isinstance(conflicts, list):
        errors.append("CONFLICTS_NOT_LIST")
        conflicts = []
    for item in [*missing_claims, *conflicts]:
        if isinstance(item, dict):
            errors.extend(_evidence_errors(item.get("evidence_chunk_ids", []), chunk_by_id, paper_id))
        else:
            errors.append("ISSUE_NOT_OBJECT")

    all_supported = bool(required) and set(reviewed) == set(required) and all(
        review.get("status") == "SUPPORTED" for review in reviewed.values()
    )
    coverage_supported = isinstance(coverage, dict) and list(coverage) == core_fields and all(
        value == "SUPPORTED" for value in coverage.values()
    )
    model_pass = not errors and all_supported and coverage_supported and not missing_claims and not conflicts
    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "model_verdict": MODEL_REVIEW_PASS if model_pass else MODEL_REVIEW_ISSUES,
        "required_atom_count": len(required),
        "reviewed_atom_count": len(reviewed),
        "unsupported_atom_count": sum(1 for item in reviewed.values() if item.get("status") != "SUPPORTED"),
        "missing_claim_count": len(missing_claims),
        "cross_field_conflict_count": len(conflicts),
        "payload": payload,
    }


def compose_card_decision(full_receipt: dict[str, Any], local_receipt: dict[str, Any]) -> str:
    if not local_receipt.get("ok"):
        return "LOCAL_REPAIR"
    if not full_receipt.get("ok"):
        return "HUMAN_REQUIRED"
    if full_receipt.get("model_verdict") == MODEL_REVIEW_PASS:
        return CARD_REVIEW_PASS
    payload = full_receipt.get("payload") if isinstance(full_receipt.get("payload"), dict) else {}
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    missing = payload.get("missing_claims") if isinstance(payload.get("missing_claims"), list) else []
    missing_core = any(
        isinstance(item, dict)
        and item.get("issue_type") == "CORE_OMISSION"
        and item.get("field") in ("research_question", "research_object", "method", "key_results", "author_conclusion")
        for item in missing
    )
    systemic = sum(1 for value in coverage.values() if value in ("MISSING", "CONFLICT")) >= 2
    return "REGENERATE" if missing_core or systemic else "LOCAL_REPAIR"


def validate_delta_review(
    payload: Any,
    delta_view: dict[str, Any],
    chunks: list[dict[str, Any]],
    contract: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    false_resolved = 0
    unsafe_pass = 0
    conservative_overblock = 0
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["DELTA_NOT_OBJECT"], "false_resolved_count": 0, "unsafe_pass_count": 0, "safety_accuracy": 0.0}
    if set(payload) != {"claim_reviews"}:
        if any(key in payload for key in ("card_review_pass", "whole_card_pass", "verdict")):
            unsafe_pass += 1
            errors.append("DELTA_WHOLE_CARD_PASS_FORBIDDEN")
        if set(payload) - {"claim_reviews", "card_review_pass", "whole_card_pass", "verdict"}:
            errors.append("DELTA_UNKNOWN_TOP_LEVEL_KEY")

    expected_claims = {
        claim["claim_id"]: claim
        for claim in delta_view.get("claims", [])
        if isinstance(claim, dict) and isinstance(claim.get("claim_id"), str)
    }
    seen_claims: set[str] = set()
    chunk_by_id = _chunk_map(chunks)
    paper_id = str(delta_view.get("paper_id") or "")
    claim_reviews = payload.get("claim_reviews")
    if not isinstance(claim_reviews, list):
        errors.append("CLAIM_REVIEWS_NOT_LIST")
        claim_reviews = []

    for review in claim_reviews:
        if not isinstance(review, dict):
            errors.append("CLAIM_REVIEW_NOT_OBJECT")
            continue
        claim_id = review.get("claim_id")
        if claim_id not in expected_claims:
            errors.append("DELTA_CLAIM_UNKNOWN")
            continue
        if claim_id in seen_claims:
            errors.append("DELTA_CLAIM_DUPLICATE")
            continue
        seen_claims.add(claim_id)
        claim = expected_claims[claim_id]
        claim_paper_id = str(claim.get("paper_id") or paper_id)
        expected_atoms = {
            atom["atom_id"]: atom
            for atom in claim.get("atoms", [])
            if isinstance(atom, dict) and atom.get("required") is True and isinstance(atom.get("atom_id"), str)
        }
        actual_atoms: dict[str, dict[str, Any]] = {}
        atom_reviews = review.get("atom_reviews")
        if not isinstance(atom_reviews, list):
            errors.append("DELTA_ATOMS_NOT_LIST")
            atom_reviews = []
        for atom_review in atom_reviews:
            if not isinstance(atom_review, dict):
                errors.append("DELTA_ATOM_NOT_OBJECT")
                continue
            atom_id = atom_review.get("atom_id")
            if atom_id not in expected_atoms:
                errors.append("DELTA_ATOM_UNKNOWN")
                continue
            if atom_id in actual_atoms:
                errors.append("DELTA_ATOM_DUPLICATE")
                continue
            if atom_review.get("status") not in contract["atom_statuses"]:
                errors.append("DELTA_ATOM_STATUS_INVALID")
            errors.extend(_evidence_errors(atom_review.get("evidence_chunk_ids"), chunk_by_id, claim_paper_id))
            actual_atoms[atom_id] = atom_review
        complete = set(actual_atoms) == set(expected_atoms)
        if not complete:
            errors.append("DELTA_REQUIRED_ATOM_UNREVIEWED")
        conflicts = review.get("cross_field_conflicts", [])
        if not isinstance(conflicts, list):
            errors.append("DELTA_CONFLICTS_NOT_LIST")
            conflicts = []
        for conflict in conflicts:
            if isinstance(conflict, dict):
                errors.extend(_evidence_errors(conflict.get("evidence_chunk_ids", []), chunk_by_id, claim_paper_id))
            else:
                errors.append("DELTA_CONFLICT_NOT_OBJECT")
        final_status = review.get("final_status")
        if final_status not in contract["delta_statuses"]:
            errors.append("DELTA_FINAL_STATUS_INVALID")
        all_supported = bool(expected_atoms) and complete and all(item.get("status") == "SUPPORTED" for item in actual_atoms.values())
        safe_to_resolve = all_supported and not conflicts
        if final_status == "RESOLVED" and not safe_to_resolve:
            false_resolved += 1
            errors.append("FALSE_RESOLVED")
        if final_status != "RESOLVED" and safe_to_resolve:
            conservative_overblock += 1

    if seen_claims != set(expected_claims):
        errors.append("DELTA_CLAIM_UNREVIEWED")
    unsafe_total = false_resolved + unsafe_pass
    return {
        "ok": not errors and unsafe_total == 0,
        "errors": sorted(set(errors)),
        "false_resolved_count": false_resolved,
        "unsafe_pass_count": unsafe_pass,
        "conservative_overblock_count": conservative_overblock,
        "safety_accuracy": 1.0 if unsafe_total == 0 else 0.0,
        "reviewed_claim_count": len(seen_claims),
        "expected_claim_count": len(expected_claims),
        "payload": payload,
    }
