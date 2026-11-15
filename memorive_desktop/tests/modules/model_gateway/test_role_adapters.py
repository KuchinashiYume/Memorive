from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from model_gateway.local_capability import LocalExecutionEnvelope, LogicalRole
from model_gateway.local_capability.adapters import AnalysisAdapter, DistillAdapter, EmbeddingAdapter, OCRAdapter, RetrievalAdapter, VerifyAdapter
from model_gateway.local_capability.errors import ContractViolation
def envelope(role: LogicalRole) -> LocalExecutionEnvelope:
    return LocalExecutionEnvelope(
        request_id="request-001",
        attempt=1,
        logical_role=role,
        profile_id=f"profile-{role.value.lower()}",
        ownership_identity={"identity_id": "x", "ownership_class": "self", "provenance_sha256": "A" * 64},
        input_locator="sandbox://input",
        input_sha256="B" * 64,
        limits={"items": 1},
        timeout_seconds=10,
        max_attempts=1,
        allowed_roots=("G:/sandbox",),
        allowed_processes=("worker.exe",),
        network_policy="NO_RUNTIME",
        expected_output_schema="schema://output",
        expected_output_schema_sha256="C" * 64,
    )


CASES = [
    (OCRAdapter, LogicalRole.LOCAL_OCR, {"transcription": "x", "anchors": [{"source_id": "p1"}], "diagnostics": {}}),
    (DistillAdapter, LogicalRole.LOCAL_DISTILL, {"facts": [], "source_identity": "s1", "uncertainty": 0.2}),
    (AnalysisAdapter, LogicalRole.LOCAL_ANALYSIS, {"candidate": {}, "citations": ["a1"], "abstain": False}),
    (VerifyAdapter, LogicalRole.LOCAL_VERIFY, {"findings": [], "verdict": "PASS", "structural_only": True}),
    (EmbeddingAdapter, LogicalRole.LOCAL_EMBED, {"vectors": [[0.6, 0.8]], "dimension": 2, "normalization": "L2_UNIT"}),
    (
        RetrievalAdapter,
        LogicalRole.LOCAL_RETRIEVE,
        {"candidates": [{"candidate_id": "c1", "score": 1.0}], "route": "BASELINE_NO_RERANKER", "fallback_receipt": {}},
    ),
]


@pytest.mark.parametrize(("adapter_type", "role", "output"), CASES)
def test_each_role_adapter_accepts_only_its_provider_neutral_output_contract(adapter_type: Any, role: LogicalRole, output: dict[str, Any]) -> None:
    adapter = adapter_type(lambda request, payload: output)
    result = adapter.execute(envelope(role), {"fixture": True})
    assert result.output == output
    assert adapter.call_count == 1


INVALID_CASES = [
    (OCRAdapter, LogicalRole.LOCAL_OCR, {"transcription": "x", "anchors": [], "diagnostics": {}}),
    (DistillAdapter, LogicalRole.LOCAL_DISTILL, {"facts": [], "source_identity": "s1", "uncertainty": 2}),
    (AnalysisAdapter, LogicalRole.LOCAL_ANALYSIS, {"candidate": {}, "citations": [], "abstain": False}),
    (VerifyAdapter, LogicalRole.LOCAL_VERIFY, {"findings": [], "verdict": "MAYBE", "structural_only": True}),
    (EmbeddingAdapter, LogicalRole.LOCAL_EMBED, {"vectors": [[1.0, float("nan")]], "dimension": 2, "normalization": "L2_UNIT"}),
    (
        RetrievalAdapter,
        LogicalRole.LOCAL_RETRIEVE,
        {"candidates": [], "route": "LOCAL_RERANKER", "fallback_receipt": {}},
    ),
]


@pytest.mark.parametrize(("adapter_type", "role", "output"), INVALID_CASES)
def test_each_role_adapter_rejects_invalid_output_without_repair(adapter_type: Any, role: LogicalRole, output: dict[str, Any]) -> None:
    adapter = adapter_type(lambda request, payload: output)
    with pytest.raises(ContractViolation):
        adapter.execute(envelope(role), {})
    assert adapter.call_count == 1


def test_adapter_rejects_operation_role_mismatch_before_worker_call() -> None:
    calls = 0

    def runner(request: LocalExecutionEnvelope, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"transcription": "x", "anchors": [{"source_id": "p1"}], "diagnostics": {}}

    adapter = OCRAdapter(runner)
    with pytest.raises(ContractViolation, match="ADAPTER_ROLE_MISMATCH"):
        adapter.execute(replace(envelope(LogicalRole.LOCAL_OCR), logical_role=LogicalRole.LOCAL_ANALYSIS), {})
    assert calls == 0


def test_analysis_can_keep_supported_claims_and_abstain_on_one_missing_target() -> None:
    output = {
        "candidate": {
            "claims": [
                {
                    "text": "Catalyst ALPHA-7 was held at 37 C for 20 minutes.",
                    "source_refs": ["P3-DOCUMENT_PROCESSING", "P3-EVIDENCE_EXTRACTION"],
                }
            ]
        },
        "citations": ["P3-DOCUMENT_PROCESSING", "P3-EVIDENCE_EXTRACTION", "P3-RESEARCH_ANALYSIS"],
        "abstain": False,
        "abstentions": [
            {
                "target": "reaction_mechanism",
                "reason": "ABSENT_IN_SOURCE",
                "source_refs": ["P3-RESEARCH_ANALYSIS"],
            }
        ],
    }
    result = AnalysisAdapter(lambda request, payload: output).execute(envelope(LogicalRole.LOCAL_ANALYSIS), {})
    assert result.output == output


@pytest.mark.parametrize(
    "output",
    [
        {
            "candidate": {"claims": [{"text": "unsupported", "source_refs": ["CORE"]}]},
            "citations": ["CORE"],
            "abstain": True,
            "abstentions": [],
        },
        {
            "candidate": {"claims": []},
            "citations": ["CORE"],
            "abstain": False,
            "abstentions": [{"target": "reaction_mechanism", "reason": "UNKNOWN", "source_refs": ["CORE"]}],
        },
        {
            "candidate": {"claims": []},
            "citations": ["CORE"],
            "abstain": False,
            "abstentions": [{"target": "reaction_mechanism", "reason": "ABSENT_IN_SOURCE", "source_refs": []}],
        },
    ],
)
def test_analysis_abstention_shape_fails_closed(output: dict[str, Any]) -> None:
    with pytest.raises(ContractViolation):
        AnalysisAdapter(lambda request, payload: output).execute(envelope(LogicalRole.LOCAL_ANALYSIS), {})
