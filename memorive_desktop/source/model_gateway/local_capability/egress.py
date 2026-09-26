"""Zero-egress evidence evaluation for offline and future live windows."""

from __future__ import annotations

from .contracts import validate_contract
from .errors import EgressBlocked
from .types import LocalEgressEvidence


def evaluate_zero_egress(evidence: LocalEgressEvidence) -> dict[str, object]:
    validate_contract(evidence)
    common = (
        evidence.process_tree_identity_match
        and evidence.forbidden_destination_count == 0
        and evidence.unattributed_event_count == 0
    )
    if evidence.execution_mode == "OFFLINE_FAKE_A1":
        passed = (
            common
            and evidence.runtime_request_count == 0
            and evidence.final_zero_egress_verdict == "PASS_OFFLINE_NO_RUNTIME"
        )
    elif evidence.execution_mode in {
        "LOCAL_QUALIFICATION_A",
        "LOCAL_ONLY_QUALIFICATION_B",
        "LOCAL_RUNTIME",
    }:
        passed = (
            common
            and evidence.prevention_active
            and evidence.observer_complete
            and evidence.final_zero_egress_verdict == "PASS_ZERO_EGRESS"
        )
    else:
        passed = False
    if not passed:
        raise EgressBlocked(
            details={
                "evidence_id": evidence.evidence_id,
                "execution_mode": evidence.execution_mode,
                "forbidden": evidence.forbidden_destination_count,
                "unattributed": evidence.unattributed_event_count,
                "observer_complete": evidence.observer_complete,
                "prevention_active": evidence.prevention_active,
            }
        )
    return {
        "evidence_id": evidence.evidence_id,
        "result": evidence.final_zero_egress_verdict,
        "runtime_request_count": evidence.runtime_request_count,
        "cloud_requests": 0,
    }
