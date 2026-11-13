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
