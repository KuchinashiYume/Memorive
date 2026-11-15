from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from model_gateway.local_capability import LocalExecutionEnvelope, LogicalRole, InstallationMultimodalRetrievalRunner
from model_gateway.local_capability.adapters.retrieval import validate_retrieval_output
from model_gateway.local_capability.errors import ContractViolation
@dataclass
class Outcome:
    success: bool
    error_code: str | None
    rankings: tuple[dict[str, Any], ...]
    receipt: dict[str, Any] | None


class FakeRanker:
    def __init__(self, outcome: Outcome) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def execute(self, batches, binding, *, allowed_image_roots, timeout_seconds):
        self.calls.append(
            {
                "batches": batches,
                "binding": binding,
                "allowed_image_roots": allowed_image_roots,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.outcome


def envelope() -> LocalExecutionEnvelope:
    return LocalExecutionEnvelope(
        request_id="request-local-retrieve-001",
        attempt=1,
        logical_role=LogicalRole.LOCAL_RETRIEVE,
        profile_id="profile-local_retrieve",
        ownership_identity={"identity_id": "x", "ownership_class": "entrusted", "provenance_sha256": "A" * 64},
        input_locator="sandbox://query",
        input_sha256="B" * 64,
        limits={"items": 3},
        timeout_seconds=45,
        max_attempts=1,
        allowed_roots=("G:/sandbox/images",),
        allowed_processes=("broker-worker.exe",),
        network_policy="EXACT_PROCESS_NON_LOOPBACK_DENY",
        expected_output_schema="schema://retrieval-v2",
        expected_output_schema_sha256="C" * 64,
        execution_mode="LOCAL_QUALIFICATION_A",
    )


def payload() -> dict[str, Any]:
    return {
        "query_id": "Q1",
        "query": "What temperature and duration were used?",
        "query_intent": "method_condition",
        "documents": [
            {"candidate_id": "answer", "text": "37 C for 20 minutes", "provenance": {}},
            {"candidate_id": "support", "text": "temperature-controlled", "provenance": {}},
            {"candidate_id": "references", "text": "References", "provenance": {}},
        ],
    }


def receipt() -> dict[str, Any]:
    return {
        "schema_version": "RetrievalCalibrationLocalMultimodalListwiseRankReceipt-v1",
        "status": "PASS",
        "keep_alive": 0,
        "latency_ms": 12.5,
        "local_metadata_request_count": 1,
        "local_model_request_count": 1,
        "external_request_count": 0,
        "cloud_fallback_count": 0,
        "dedicated_reranker_fallback_count": 0,
        "production_mutation_count": 0,
    }


def test_runner_maps_exact_set_order_without_scores_or_fallback() -> None:
    ranker = FakeRanker(Outcome(True, None, ({"query_id": "Q1", "candidate_ids": ["answer", "support", "references"]},), receipt()))
    runner = InstallationMultimodalRetrievalRunner({"binding": "frozen"}, ranker=ranker)
    result = runner(envelope(), payload())

    validate_retrieval_output(result.output)
    assert result.output["candidates"] == [
        {"candidate_id": "answer", "rank": 1},
        {"candidate_id": "support", "rank": 2},
        {"candidate_id": "references", "rank": 3},
    ]
    assert all("score" not in row for row in result.output["candidates"])
    assert ranker.calls[0]["allowed_image_roots"] == envelope().allowed_roots
    assert ranker.calls[0]["timeout_seconds"] == 45


def test_runner_freezes_failure_without_hidden_fallback() -> None:
    ranker = FakeRanker(Outcome(False, "MODEL_DIGEST_MISMATCH", (), None))
    runner = InstallationMultimodalRetrievalRunner({"binding": "frozen"}, ranker=ranker)
    with pytest.raises(ContractViolation, match="Installation_MULTIMODAL_LISTWISE_FAILED"):
        runner(envelope(), payload())
    assert len(ranker.calls) == 1


def test_runner_rejects_success_receipt_with_incomplete_candidate_set() -> None:
    ranker = FakeRanker(Outcome(True, None, ({"query_id": "Q1", "candidate_ids": ["answer", "support"]},), receipt()))
    runner = InstallationMultimodalRetrievalRunner({"binding": "frozen"}, ranker=ranker)
    with pytest.raises(ContractViolation, match="Installation_MULTIMODAL_EXACT_SET_MISMATCH"):
        runner(envelope(), payload())
