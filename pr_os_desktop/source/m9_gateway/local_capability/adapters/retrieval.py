"""LOCAL_RETRIEVE adapter output validation."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..errors import ContractViolation
from ..types import LogicalRole
from . import InjectedRoleAdapter, Runner


def validate_retrieval_output(value: Mapping[str, Any]) -> None:
    candidates = value.get("candidates")
    if not isinstance(candidates, list):
        raise ContractViolation("RETRIEVAL_CANDIDATES_MISSING")
    if not isinstance(value.get("fallback_receipt"), Mapping):
        raise ContractViolation("RETRIEVAL_FALLBACK_RECEIPT_MISSING")
    route = value.get("route")
    if route == "BASELINE_NO_RERANKER":
        scores: list[float] = []
        for row in candidates:
            if not isinstance(row, Mapping) or not isinstance(row.get("candidate_id"), str):
                raise ContractViolation("RETRIEVAL_CANDIDATE_INVALID")
            score = row.get("score")
            if not isinstance(score, int | float) or isinstance(score, bool) or not math.isfinite(score):
                raise ContractViolation("RETRIEVAL_SCORE_INVALID")
            scores.append(float(score))
        if scores != sorted(scores, reverse=True):
            raise ContractViolation("RETRIEVAL_ORDER_INVALID")
        return
    if route == "LOCAL_RERANKER":
        raise ContractViolation("DEDICATED_LOCAL_RERANKER_FORBIDDEN")
    if route != "LOCAL_MULTIMODAL_LISTWISE":
        raise ContractViolation("RETRIEVAL_ROUTE_INVALID")
    if value.get("ranking_mode") != "GENERATIVE_LISTWISE_EXACT_SET":
        raise ContractViolation("RETRIEVAL_RANKING_MODE_INVALID")
    if len(candidates) < 2:
        raise ContractViolation("RETRIEVAL_LISTWISE_CANDIDATES_INSUFFICIENT")
    candidate_ids: list[str] = []
    ranks: list[int] = []
    for row in candidates:
        if not isinstance(row, Mapping) or set(row) != {"candidate_id", "rank"}:
            raise ContractViolation("RETRIEVAL_LISTWISE_CANDIDATE_INVALID")
        candidate_id = row.get("candidate_id")
        rank = row.get("rank")
        if not isinstance(candidate_id, str) or not candidate_id or not isinstance(rank, int) or isinstance(rank, bool):
            raise ContractViolation("RETRIEVAL_LISTWISE_CANDIDATE_INVALID")
        candidate_ids.append(candidate_id)
        ranks.append(rank)
    if len(set(candidate_ids)) != len(candidate_ids) or ranks != list(range(1, len(candidates) + 1)):
        raise ContractViolation("RETRIEVAL_LISTWISE_EXACT_ORDER_INVALID")
    if value["fallback_receipt"].get("used") is not False:
        raise ContractViolation("RETRIEVAL_LISTWISE_FALLBACK_FORBIDDEN")
    receipt = value.get("t13_receipt")
    if not isinstance(receipt, Mapping):
        raise ContractViolation("RETRIEVAL_T13_RECEIPT_MISSING")
    if (
        receipt.get("schema_version") != "P06T13LocalMultimodalListwiseRankReceipt-v1"
        or receipt.get("status") != "PASS"
        or receipt.get("keep_alive") != 0
        or any(
            receipt.get(field) != 0
            for field in (
                "external_request_count",
                "cloud_fallback_count",
                "dedicated_reranker_fallback_count",
                "production_mutation_count",
            )
        )
    ):
        raise ContractViolation("RETRIEVAL_T13_RECEIPT_INVALID")


class RetrievalAdapter(InjectedRoleAdapter):
    logical_role = LogicalRole.LOCAL_RETRIEVE

    def __init__(self, runner: Runner) -> None:
        super().__init__(runner, validate_retrieval_output)


def plan_isolated_generation(
    run_root: str | Path,
    generation_id: str,
    *,
    protected_roots: tuple[str | Path, ...],
) -> dict[str, Any]:
    """Return a create-only isolated generation plan without touching disk."""

    if not generation_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for character in generation_id):
        raise ContractViolation("ISOLATED_GENERATION_ID_INVALID")
    root = Path(run_root).resolve(strict=False)
    generation = (root / generation_id).resolve(strict=False)
    if generation.parent != root:
        raise ContractViolation("ISOLATED_GENERATION_ESCAPES_RUN_ROOT")
    for protected in protected_roots:
        protected_path = Path(protected).resolve(strict=False)
        if generation == protected_path or generation in protected_path.parents or protected_path in generation.parents:
            raise ContractViolation("ISOLATED_GENERATION_INTERSECTS_PROTECTED_ROOT")
    return {
        "generation_id": generation_id,
        "generation_root": str(generation),
        "parent_generation": None,
        "create_only": True,
        "reuse_existing_generation": False,
        "production_parent": False,
        "rollback_action": "DISCARD_NEW_FAILED_GENERATION_ONLY",
        "filesystem_mutations": 0,
    }
