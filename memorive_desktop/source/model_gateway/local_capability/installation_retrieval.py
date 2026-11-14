"""Explicit Runtime worker binding for the accepted Installation multimodal listwise ranker."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from model_gateway.local_multimodal_ranker import LocalMultimodalRankerAdapter

from .errors import ContractViolation
from .types import AdapterResult, LocalExecutionEnvelope, LogicalRole


class InstallationMultimodalRetrievalRunner:
    """Adapt a frozen Installation exact-set order to the Runtime LOCAL_RETRIEVE contract.

    This worker is injected into ``RetrievalAdapter``.  It does not synthesize
    relevance scores and it has no dedicated-reranker or cloud fallback.
    """

    def __init__(
        self,
        binding: Mapping[str, Any],
        *,
        ranker: LocalMultimodalRankerAdapter | None = None,
    ) -> None:
        self._binding = deepcopy(dict(binding))
        self._ranker = ranker or LocalMultimodalRankerAdapter()

    def __call__(self, envelope: LocalExecutionEnvelope, payload: Mapping[str, Any]) -> AdapterResult:
        if envelope.logical_role is not LogicalRole.LOCAL_RETRIEVE:
            raise ContractViolation("Installation_RETRIEVAL_ROLE_MISMATCH")
        required = {"query_id", "query", "documents"}
        if not required.issubset(payload):
            raise ContractViolation("Installation_RETRIEVAL_PAYLOAD_INCOMPLETE")
        batch = {
            "query_id": payload["query_id"],
            "query": payload["query"],
            "query_intent": payload.get("query_intent", "unknown"),
            "documents": payload["documents"],
        }
        outcome = self._ranker.execute(
            [batch],
            self._binding,
            allowed_image_roots=envelope.allowed_roots,
            timeout_seconds=envelope.timeout_seconds,
        )
        if not outcome.success:
            raise ContractViolation(
                "Installation_MULTIMODAL_LISTWISE_FAILED",
                details={"error_code": outcome.error_code or "UNKNOWN"},
            )
        if len(outcome.rankings) != 1 or outcome.rankings[0].get("query_id") != payload["query_id"]:
            raise ContractViolation("Installation_MULTIMODAL_QUERY_ID_MISMATCH")
        receipt = outcome.receipt
        if not isinstance(receipt, Mapping):
            raise ContractViolation("Installation_MULTIMODAL_RECEIPT_MISSING")
        ordered = outcome.rankings[0].get("candidate_ids")
        if not isinstance(ordered, list):
            raise ContractViolation("Installation_MULTIMODAL_ORDER_MISSING")
        input_ids = [row.get("candidate_id") for row in payload["documents"] if isinstance(row, Mapping)]
        if (
            len(input_ids) != len(payload["documents"])
            or any(not isinstance(candidate_id, str) or not candidate_id for candidate_id in input_ids)
            or len(set(input_ids)) != len(input_ids)
            or len(ordered) != len(input_ids)
            or any(not isinstance(candidate_id, str) or not candidate_id for candidate_id in ordered)
            or set(ordered) != set(input_ids)
        ):
            raise ContractViolation("Installation_MULTIMODAL_EXACT_SET_MISMATCH")
        output = {
            "candidates": [
                {"candidate_id": candidate_id, "rank": rank}
                for rank, candidate_id in enumerate(ordered, start=1)
            ],
            "route": "LOCAL_MULTIMODAL_LISTWISE",
            "ranking_mode": "GENERATIVE_LISTWISE_EXACT_SET",
            "fallback_receipt": {
                "used": False,
                "dedicated_reranker_used": False,
                "cloud_used": False,
            },
            "installation_receipt": dict(receipt),
        }
        return AdapterResult(
            output=output,
            metrics={
                "query_count": 1,
                "candidate_count": len(ordered),
                "local_metadata_request_count": int(receipt.get("local_metadata_request_count", 0)),
                "local_model_request_count": int(receipt.get("local_model_request_count", 0)),
                "external_request_count": 0,
                "latency_ms": float(receipt.get("latency_ms", 0)),
            },
        )
