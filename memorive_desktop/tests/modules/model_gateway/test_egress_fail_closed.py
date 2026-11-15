from __future__ import annotations

from dataclasses import replace

import pytest

from model_gateway.local_capability import LocalEgressEvidence
from model_gateway.local_capability.egress import evaluate_zero_egress
from model_gateway.local_capability.errors import LocalCapabilityError
def evidence(mode: str = "OFFLINE_FAKE_A1") -> LocalEgressEvidence:
    return LocalEgressEvidence(
        evidence_id="egress-001",
        execution_mode=mode,
        prevention_policy="NO_RUNTIME" if mode == "OFFLINE_FAKE_A1" else "EXACT_PROCESS_NON_LOOPBACK_DENY",
        prevention_active=mode == "LOCAL_RUNTIME",
        observer_method="STATIC" if mode == "OFFLINE_FAKE_A1" else "INDEPENDENT_PROCESS_SCOPED_OBSERVER",
        observer_complete=True,
        window_started_at="2026-08-18T00:00:00Z",
        window_ended_at="2026-08-18T00:00:01Z",
        process_tree_identity_match=True,
        interface_set=() if mode == "OFFLINE_FAKE_A1" else ("loopback", "ethernet"),
        dns_events=0,
        tcp_events=0,
        udp_events=0,
        http_events=0,
        localhost_request_count=0 if mode == "OFFLINE_FAKE_A1" else 1,
        forbidden_destination_count=0,
        unattributed_event_count=0,
        runtime_request_count=0 if mode == "OFFLINE_FAKE_A1" else 1,
        final_zero_egress_verdict="PASS_OFFLINE_NO_RUNTIME" if mode == "OFFLINE_FAKE_A1" else "PASS_ZERO_EGRESS",
    )


def test_offline_fake_requires_zero_runtime_requests() -> None:
    result = evaluate_zero_egress(evidence())
    assert result == {
        "evidence_id": "egress-001",
        "result": "PASS_OFFLINE_NO_RUNTIME",
        "runtime_request_count": 0,
        "cloud_requests": 0,
    }
    with pytest.raises(LocalCapabilityError):
        evaluate_zero_egress(replace(evidence(), runtime_request_count=1))


def test_live_evidence_requires_both_prevention_and_complete_observer() -> None:
    assert evaluate_zero_egress(evidence("LOCAL_RUNTIME"))["result"] == "PASS_ZERO_EGRESS"
    with pytest.raises(LocalCapabilityError):
        evaluate_zero_egress(replace(evidence("LOCAL_RUNTIME"), prevention_active=False))
    with pytest.raises(LocalCapabilityError):
        evaluate_zero_egress(replace(evidence("LOCAL_RUNTIME"), observer_complete=False))


def test_process_identity_mismatch_fails_closed() -> None:
    with pytest.raises(LocalCapabilityError):
        evaluate_zero_egress(replace(evidence(), process_tree_identity_match=False))


def test_forbidden_or_unattributed_event_cannot_validate() -> None:
    with pytest.raises(LocalCapabilityError):
        evaluate_zero_egress(replace(evidence(), forbidden_destination_count=1))
    with pytest.raises(LocalCapabilityError):
        evaluate_zero_egress(replace(evidence(), unattributed_event_count=1))
