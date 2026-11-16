from __future__ import annotations

from pathlib import Path

import pytest

from memorive.models.gateway.local_capability.adapters.retrieval import plan_isolated_generation, validate_retrieval_output
from memorive.models.gateway.local_capability.errors import ContractViolation
def test_isolated_generation_plan_is_create_only_and_has_zero_filesystem_mutations(tmp_path: Path) -> None:
    run_root = tmp_path / "local_inference-run"
    plan = plan_isolated_generation(run_root, "generation-001", protected_roots=(tmp_path / "production",))
    assert Path(plan["generation_root"]).parent == run_root.resolve()
    assert plan["create_only"] is True
    assert plan["reuse_existing_generation"] is False
    assert plan["production_parent"] is False
    assert plan["filesystem_mutations"] == 0
    assert not run_root.exists()


@pytest.mark.parametrize("generation_id", ["", "../escape", "a/b", "a\\b", "space value"])
def test_generation_id_cannot_escape_or_be_ambiguous(tmp_path: Path, generation_id: str) -> None:
    with pytest.raises(ContractViolation):
        plan_isolated_generation(tmp_path / "run", generation_id, protected_roots=())


def test_generation_cannot_intersect_a_protected_root(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    with pytest.raises(ContractViolation, match="INTERSECTS_PROTECTED_ROOT"):
        plan_isolated_generation(run_root, "generation-001", protected_roots=(run_root,))


def test_retrieval_accepts_legacy_distance_fallback_with_deterministic_scores() -> None:
    validate_retrieval_output(
        {
            "candidates": [
                {"candidate_id": "a", "score": 0.9},
                {"candidate_id": "b", "score": 0.5},
            ],
            "route": "BASELINE_NO_RERANKER",
            "fallback_receipt": {"used": False},
        }
    )
    with pytest.raises(ContractViolation, match="RETRIEVAL_ORDER_INVALID"):
        validate_retrieval_output(
            {
                "candidates": [
                    {"candidate_id": "a", "score": 0.1},
                    {"candidate_id": "b", "score": 0.9},
                ],
                "route": "BASELINE_NO_RERANKER",
                "fallback_receipt": {},
            }
        )


def test_retrieval_accepts_multimodal_listwise_without_fabricated_scores() -> None:
    validate_retrieval_output(
        {
            "candidates": [
                {"candidate_id": "answer", "rank": 1},
                {"candidate_id": "support", "rank": 2},
                {"candidate_id": "references", "rank": 3},
            ],
            "route": "LOCAL_MULTIMODAL_LISTWISE",
            "ranking_mode": "GENERATIVE_LISTWISE_EXACT_SET",
            "fallback_receipt": {"used": False},
            "installation_receipt": {
                "schema_version": "RetrievalCalibrationLocalMultimodalListwiseRankReceipt-v1",
                "status": "PASS",
                "keep_alive": 0,
                "external_request_count": 0,
                "cloud_fallback_count": 0,
                "dedicated_reranker_fallback_count": 0,
                "production_mutation_count": 0,
            },
        }
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"candidate_index": 1, "field": "rank", "value": 3},
        {"candidate_index": 0, "field": "score", "value": 1.0},
        {"receipt_field": "external_request_count", "value": 1},
        {"receipt_field": "keep_alive", "value": 5},
    ],
)
def test_multimodal_listwise_fails_closed_on_rank_score_or_egress_drift(mutation: dict[str, object]) -> None:
    value = {
        "candidates": [
            {"candidate_id": "answer", "rank": 1},
            {"candidate_id": "support", "rank": 2},
        ],
        "route": "LOCAL_MULTIMODAL_LISTWISE",
        "ranking_mode": "GENERATIVE_LISTWISE_EXACT_SET",
        "fallback_receipt": {"used": False},
        "installation_receipt": {
            "schema_version": "RetrievalCalibrationLocalMultimodalListwiseRankReceipt-v1",
            "status": "PASS",
            "keep_alive": 0,
            "external_request_count": 0,
            "cloud_fallback_count": 0,
            "dedicated_reranker_fallback_count": 0,
            "production_mutation_count": 0,
        },
    }
    if "candidate_index" in mutation:
        value["candidates"][mutation["candidate_index"]][mutation["field"]] = mutation["value"]
    else:
        value["installation_receipt"][mutation["receipt_field"]] = mutation["value"]
    with pytest.raises(ContractViolation):
        validate_retrieval_output(value)


def test_dedicated_local_reranker_route_remains_forbidden() -> None:
    with pytest.raises(ContractViolation, match="DEDICATED_LOCAL_RERANKER_FORBIDDEN"):
        validate_retrieval_output({"candidates": [], "route": "LOCAL_RERANKER", "fallback_receipt": {}})
