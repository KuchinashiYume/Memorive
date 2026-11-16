from __future__ import annotations

from copy import deepcopy

import pytest

from memorive.documents.card_distillation.segmented_distill.assembly import build_analysis_lineage
from memorive.documents.card_review.segmented_distill_repair import BoundedRepairLedger, SegmentedRepairContractError, canonical_hash
from memorive.documents.card_review.segmented_distill_repair import apply_targeted_repair, freeze_targeted_repair_bundle
def card() -> dict:
    body = {
        "schema_version": "long_document-card-candidate-v1",
        "artifact_kind": "card_candidate",
        "paper_id": "synthetic-paper",
        "review_status": "pending",
        "fields": {
            "outcome": {"value": {"text": "12", "unit": "points"}, "source_refs": [{"chunk_id": "c1"}]},
            "dose": {"value": {"text": "50", "unit": "mg"}, "source_refs": [{"chunk_id": "c2"}]},
        },
    }
    return {**body, "content_hash": canonical_hash(body)}


def full_receipt(verdict: str = "REPAIRABLE") -> dict:
    return {
        "review_type": "FULL",
        "finish_reason": "stop",
        "verdict": verdict,
        "repairable_findings": [
            {
                "finding_id": "finding-1",
                "target_pointer": "/fields/outcome/value/text",
                "source_excerpts": [{"chunk_id": "c1", "quote": "13 points"}],
            }
        ],
    }


def bundle() -> dict:
    return freeze_targeted_repair_bundle(
        card_candidate=card(),
        full_review_receipt=full_receipt(),
        producer_model="deepseek-v4-flash",
        reviewer_model="claude-haiku-4-5",
        existing_atomic_bundle_ref="evidence_review.atomic_repair@frozen",
    )


def repair_response() -> dict:
    return {
        "finish_reason": "stop",
        "terminal_marker": "LONG_DOCUMENT_REPAIR_COMPLETE",
        "replacements": [
            {"target_pointer": "/fields/outcome/value/text", "replacement": "13"}
        ],
    }


def test_producer_and_reviewer_must_be_heterogeneous() -> None:
    with pytest.raises(SegmentedRepairContractError, match="PRODUCER_REVIEWER_FAMILY_MUST_DIFFER"):
        freeze_targeted_repair_bundle(
            card_candidate=card(), full_review_receipt=full_receipt(),
            producer_model="claude-haiku-4-5", reviewer_model="claude-sonnet-4-5",
            existing_atomic_bundle_ref="ref",
        )


def test_bundle_exposes_only_exact_findings_and_no_gold() -> None:
    frozen = bundle()
    assert frozen["max_repair_attempts"] == 1
    assert frozen["max_delta_reviews"] == 1
    assert frozen["retry_ceiling"] == 0
    assert frozen["gold_or_scorer_in_producer_payload"] is False
    assert set(frozen["targets"][0]) == {
        "finding_id", "target_pointer", "before_hash", "source_excerpts", "allowed_actions"
    }


def test_targeted_repair_changes_only_whitelisted_field() -> None:
    before = card()
    dose_before = canonical_hash(before["fields"]["dose"])
    successor, receipt = apply_targeted_repair(
        card_candidate=before, repair_bundle=bundle(), repair_response=repair_response()
    )
    assert successor["fields"]["outcome"]["value"]["text"] == "13"
    assert canonical_hash(successor["fields"]["dose"]) == dose_before
    assert receipt["unchanged_field_hash_checks"] == {"dose": True}
    assert successor["parent_card_candidate_hash"] == before["content_hash"]


def test_repair_requires_exact_target_set() -> None:
    response = repair_response()
    response["replacements"].append(
        {"target_pointer": "/fields/dose/value/text", "replacement": "60"}
    )
    with pytest.raises(SegmentedRepairContractError, match="REPAIR_TARGET_EXACT_SET_MISMATCH"):
        apply_targeted_repair(card_candidate=card(), repair_bundle=bundle(), repair_response=response)


def test_truncated_repair_never_applies_partial_patch() -> None:
    before = card()
    response = repair_response()
    response["finish_reason"] = "length"
    with pytest.raises(SegmentedRepairContractError, match="REPAIR_RESPONSE_TRUNCATED_OR_UNTERMINATED"):
        apply_targeted_repair(card_candidate=before, repair_bundle=bundle(), repair_response=response)
    assert before == card()


def test_bounded_loop_is_exactly_full_repair_delta() -> None:
    ledger = BoundedRepairLedger("deepseek-v4-flash", "claude-haiku-4-5")
    assert ledger.record_full(full_receipt()) == "REPAIRABLE"
    _, application = apply_targeted_repair(
        card_candidate=card(), repair_bundle=bundle(), repair_response=repair_response()
    )
    ledger.record_repair(application)
    assert ledger.record_delta({"review_type": "DELTA", "finish_reason": "stop", "verdict": "PASS"}) == "PASS"
    snapshot = ledger.snapshot()
    assert snapshot["counts"] == {"full": 1, "repair": 1, "delta": 1}
    assert snapshot["terminal_state"] == "PASS"
    with pytest.raises(SegmentedRepairContractError):
        ledger.record_full(full_receipt())
    with pytest.raises(SegmentedRepairContractError):
        ledger.record_repair(application)
    with pytest.raises(SegmentedRepairContractError):
        ledger.record_delta({"review_type": "DELTA", "finish_reason": "stop", "verdict": "PASS"})


def test_full_pass_closes_without_repair() -> None:
    ledger = BoundedRepairLedger("deepseek-v4-flash", "claude-haiku-4-5")
    receipt = full_receipt("PASS")
    receipt["repairable_findings"] = []
    assert ledger.record_full(receipt) == "PASS"
    assert ledger.snapshot()["counts"] == {"full": 1, "repair": 0, "delta": 0}


def test_card_successor_marks_prior_analysis_stale_without_mutation() -> None:
    before = card()
    successor, _ = apply_targeted_repair(
        card_candidate=before, repair_bundle=bundle(), repair_response=repair_response()
    )
    prior = {"analysis_id": "a1", "card_candidate_hash": before["content_hash"]}
    frozen_prior = deepcopy(prior)
    lineage = build_analysis_lineage(
        card_candidate=successor,
        context_pack_hash="C" * 64,
        producing_run_id="run-synthetic",
        prior_analysis=prior,
    )
    assert lineage["prior_analysis_state"] == "stale_needs_revalidation"
    assert lineage["source_identity_mode"] == "ORIGINAL_CHUNK_IDS"
    assert lineage["prior_analysis_mutated"] is False
    assert prior == frozen_prior
