from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jsonschema import Draft202012Validator

from research_analysis.review_orchestration_events import AnalysisCompletionEventError, build_analysis_completed_event, canonical_hash as analysis_hash, validate_analysis_completed_event
from memorive.documents.card_review.review_orchestration.contracts import ReviewOrchestrationContractError, build_analysis_review_receipt, canonical_hash, validate_review_job_event, validate_review_job_spec, validate_trigger_policy
from memorive.documents.card_review.review_orchestration.event_store import AppendOnlyReviewEventStore, ReviewEventDedupeConflict, StaleReviewHeadError
from memorive.documents.card_review.review_orchestration.operator import ReviewOrchestrationOperator
from memorive.documents.card_review.review_orchestration.projection import CorruptReviewEventError, ReviewTransitionError, project_review_job, projection_bytes
from memorive.documents.card_review.review_orchestration.qualification import EXPECTED_FAULT_GROUPS, qualify_offline_run
from memorive.documents.card_review.review_orchestration.recovery import diagnose_lock, diagnose_recovery
from memorive.documents.card_review.review_orchestration.trigger import TriggerRegistry, evaluate_trigger
STAMP = "2026-08-17T19:00:00+09:00"
LATE_STAMP = "2026-08-18T00:00:01+09:00"
HASH_A = "A" * 64
HASH_B = "B" * 64
HASH_C = "C" * 64
HASH_D = "D" * 64
HASH_E = "E" * 64
HASH_F = "F" * 64
ASSERT = unittest.TestCase()


def _policy() -> dict[str, object]:
    seed = "REVIEW_FROZEN_SEED"
    topology = [
        {"stage": "FULL_REVIEW", "max_semantic_calls": 1},
        {"stage": "TARGETED_REPAIR", "max_semantic_calls": 1},
        {"stage": "DELTA_REVIEW", "max_semantic_calls": 1},
    ]
    body: dict[str, object] = {
        "schema_version": "ReviewOrchestrationReviewTriggerPolicy-v1",
        "policy_id": "REVIEW_POLICY",
        "revision": 1,
        "effective_epoch_id": "REVIEW_EPOCH_001",
        "must_review_rules": [
            {"rule_id": "MUST_HIGH_RISK", "field": "risk_tier", "operator": "EQ", "value": "critical"},
            {"rule_id": "MUST_MANUAL", "field": "manual_review_required", "operator": "EQ", "value": True},
            {"rule_id": "MUST_UNSOURCED", "field": "flagged_unsourced_count", "operator": "GT", "value": 0},
            {
                "rule_id": "MUST_INTERNAL_CONFLICT",
                "field": "source_internal_inconsistency_count",
                "operator": "GT",
                "value": 0,
            },
        ],
        "explicit_exempt_rules": [
            {
                "rule_id": "EXEMPT_INTERNAL_ADMIN",
                "field": "analysis_kind",
                "operator": "EQ",
                "value": "internal_admin",
            }
        ],
        "sample_strata": [
            {"stratum_id": "general_50", "field": "stratum_id", "operator": "EQ", "value": "general_50", "rate_bps": 5000},
            {"stratum_id": "canary_25", "field": "stratum_id", "operator": "EQ", "value": "canary_25", "rate_bps": 2500},
        ],
        "sampling_seed": seed,
        "sampling_seed_sha256": __import__("hashlib").sha256(seed.encode("utf-8")).hexdigest().upper(),
        "cutoff_at": "2026-08-17T23:59:59+09:00",
        "max_dispatch_attempts": 2,
        "provider_retry_ceiling": 0,
        "stage_topology": topology,
        "review_contract_sha256": HASH_D,
        "stage_topology_sha256": canonical_hash(topology),
        "ownership_policy_sha256": HASH_F,
        "route_decision_separate_from_trigger": True,
        "default_action": "BLOCK",
        "default_activation": "DISABLED",
    }
    return {**body, "content_hash": canonical_hash(body)}


def _event(index: int, **policy_fields: object) -> dict[str, object]:
    return build_analysis_completed_event(
        analysis_artifact_ref=f"analysis://A{index:03d}",
        analysis_artifact_sha256=analysis_hash({"artifact": index}),
        analysis_schema_ref="schema://analysis/v1",
        analysis_revision=1,
        context_pack_ref=f"context://A{index:03d}",
        context_pack_sha256=analysis_hash({"context": index}),
        producer_binding_ref="producer://research_analysis/offline",
        producer_behavior_hash=HASH_A,
        ownership_snapshot_ref=f"ownership://A{index:03d}",
        ownership_snapshot_sha256=analysis_hash({"ownership": index}),
        ownership_value=str(policy_fields.pop("ownership_value", "self")),
        completed_at=STAMP,
        source_commit="a" * 40,
        source_tree="b" * 40,
        completion_receipt_ref=f"receipt://A{index:03d}",
        policy_fields=policy_fields,
    )


def _event_for_decision(decision: str, *, stratum_id: str = "general_50", start: int = 100) -> dict[str, object]:
    policy = _policy()
    for index in range(start, start + 500):
        event = _event(index, stratum_id=stratum_id)
        if evaluate_trigger(event, policy)["decision"] == decision:
            return event
    raise AssertionError(f"unable to find deterministic vector for {decision}")


def _operator_job(
    store: AppendOnlyReviewEventStore | None = None,
    *,
    index: int = 1,
    ownership_value: str = "self",
    safe_local_route_available: bool = False,
) -> tuple[ReviewOrchestrationOperator, dict[str, object], dict[str, object], dict[str, object]]:
    policy = _policy()
    event = _event(
        index,
        risk_tier="critical",
        ownership_value=ownership_value,
        safe_local_route_available=safe_local_route_available,
    )
    decision = evaluate_trigger(event, policy)
    operator = ReviewOrchestrationOperator(store or AppendOnlyReviewEventStore())
    spec, replay = operator.create_or_get_job(
        event=event,
        decision=decision,
        policy=policy,
        occurred_at=STAMP,
        workspace_ref="sandbox://capabilities-t09/job",
        input_ref="fixture://review-input",
        output_ref="fixture://review-output",
        trigger_decision_ref=f"decision://{decision['content_hash']}",
        producer_role_ref="role://research_analysis-producer",
        reviewer_role_ref="role://evidence_review-reviewer",
    )
    assert replay is False
    return operator, spec, event, decision


def _append_claim(store: AppendOnlyReviewEventStore, job_id: str, *, attempt: str = "ATTEMPT-001") -> dict[str, object]:
    event, replay = store.append(
        job_id=job_id,
        event_type="RUN_CLAIMED",
        expected_previous_head_hash=store.head(job_id),
        actor_kind="WORKER",
        actor_ref="OFFLINE_BOUNDED_WORKER",
        occurred_at=STAMP,
        payload={"attempt_id": attempt},
        dedupe_key=f"RUN_CLAIMED:{attempt}",
        reason_code="TEST_CLAIM",
        attempt_id=attempt,
    )
    assert replay is False
    return event


def _schema(name: str) -> dict[str, object]:
    path = (Path(__file__).resolve().parents[3] / "source" / "memorive" / "documents" / "card_review") / "contracts" / "review_orchestration" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_five_draft_2020_12_schemas_and_contract_vectors() -> None:
    schemas = {
        name: _schema(name)
        for name in (
            "analysis_completed_event_v1.schema.json",
            "review_trigger_decision_v1.schema.json",
            "review_job_spec_v1.schema.json",
            "review_job_event_v1.schema.json",
            "analysis_review_receipt_v1.schema.json",
        )
    }
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)

    operator, spec, event, decision = _operator_job()
    job_event = operator.store.events(spec["job_id"])[0]
    receipt = build_analysis_review_receipt(
        job_spec=spec,
        job_state="COMPLETED",
        review_verdict="PASS",
        final_artifact_ref="artifact://reviewed/A001",
        final_artifact_sha256=HASH_B,
        attempt_refs=["attempt://001"],
        finding_refs=[],
        ownership_snapshot_ref=event["ownership_snapshot_ref"],
        route_receipt_ref="route://offline-disabled",
        model_qualification_ref="qualification://offline-fixture",
        accounting_receipt_ref="accounting://zero",
        cost_cny=0,
        external_calls=0,
    )
    vectors = (event, decision, spec, job_event, receipt)
    for schema, vector in zip(schemas.values(), vectors, strict=True):
        Draft202012Validator(schema).validate(vector)


def test_analysis_event_is_immutable_and_hash_bound() -> None:
    event = _event(2)
    assert validate_analysis_completed_event(event) == event
    changed = dict(event)
    changed["analysis_artifact_ref"] = "analysis://tampered"
    with ASSERT.assertRaisesRegex(AnalysisCompletionEventError, "EVENT_IDENTITY_MISMATCH"):
        validate_analysis_completed_event(changed)
    extra = dict(event)
    extra["unexpected"] = "not-allowed"
    extra["content_hash"] = analysis_hash(
        {key: value for key, value in extra.items() if key != "content_hash"}
    )
    with ASSERT.assertRaisesRegex(AnalysisCompletionEventError, "EVENT_FIELD_SET_INVALID"):
        validate_analysis_completed_event(extra)


def test_trigger_precedence_sampling_exempt_invalid_and_route_separation() -> None:
    policy = _policy()
    must_over_exempt = _event(3, risk_tier="critical", analysis_kind="internal_admin")
    must = evaluate_trigger(must_over_exempt, policy)
    assert must["decision"] == "MUST_REVIEW"
    assert must["ownership_route_precheck"] == "BLOCKED_ROUTE_NOT_ACTIVATED"

    selected = evaluate_trigger(_event_for_decision("SAMPLED_REVIEW"), policy)
    unselected = evaluate_trigger(_event_for_decision("NOT_SAMPLED", start=700), policy)
    assert selected["sample_bucket"] < selected["rate_bps"]
    assert unselected["sample_bucket"] >= unselected["rate_bps"]
    assert evaluate_trigger(_event(4, analysis_kind="internal_admin"), policy)["decision"] == "POLICY_EXEMPT"

    no_rule = evaluate_trigger(_event(5, stratum_id="unconfigured"), policy)
    assert no_rule["decision"] == "INVALID_EVENT"
    assert no_rule["reason_codes"] == ["NO_POLICY_RULE_MATCH_DEFAULT_BLOCK"]
    malformed = dict(_event(6))
    malformed["content_hash"] = HASH_C
    assert evaluate_trigger(malformed, policy)["decision"] == "INVALID_EVENT"


def test_restricted_ownership_keeps_decision_but_blocks_route() -> None:
    for offset, ownership in enumerate(("entrusted", "unknown", "mixed")):
        event = _event(10 + offset, risk_tier="critical", ownership_value=ownership)
        decision = evaluate_trigger(event, _policy())
        assert decision["decision"] == "MUST_REVIEW"
        assert decision["review_job_key"]
        assert decision["ownership_route_precheck"] == "BLOCKED_NO_SAFE_LOCAL_ROUTE"


def test_registry_denominator_replay_order_and_cutoff() -> None:
    policy = _policy()
    events = [_event(index, risk_tier="critical" if index < 4 else "normal") for index in range(12)]
    registry = TriggerRegistry()
    for index in reversed(range(12)):
        registry.ingest(event=events[index], delivery_id=f"D-{index:02d}", delivered_at=STAMP, policy=policy)
    for index in (0, 1):
        receipt = registry.ingest(
            event=events[index],
            delivery_id=f"D-REPLAY-{index}",
            delivered_at=STAMP,
            policy=policy,
        )
        assert receipt["idempotent_subject_replay"] is True
    duplicate_delivery = registry.ingest(
        event=events[0], delivery_id="D-00", delivered_at=STAMP, policy=policy
    )
    duplicate_body = {
        key: value for key, value in duplicate_delivery.items() if key != "content_hash"
    }
    assert duplicate_delivery["duplicate_delivery_id"] is True
    assert duplicate_delivery["content_hash"] == canonical_hash(duplicate_body)
    assert registry.denominator()["raw_event_deliveries"] == 14
    assert registry.denominator()["unique_event_identities"] == 12
    assert registry.denominator()["unique_analysis_subjects"] == 12
    assert registry.denominator()["trigger_decisions"] == 12

    late = registry.ingest(event=_event(99), delivery_id="D-LATE", delivered_at=LATE_STAMP, policy=policy)
    assert late["delivery_disposition"] == "NEXT_EPOCH"
    assert registry.denominator()["raw_event_deliveries"] == 14
    assert registry.denominator()["next_epoch_deliveries"] == 1


def test_registry_rejects_same_event_id_with_different_hash() -> None:
    policy = _policy()
    event = _event(20)
    registry = TriggerRegistry()
    registry.ingest(event=event, delivery_id="D-1", delivered_at=STAMP, policy=policy)
    changed = dict(event)
    changed["content_hash"] = HASH_B
    with ASSERT.assertRaisesRegex(ReviewOrchestrationContractError, "EVENT_ID_REUSED_WITH_DIFFERENT_HASH"):
        registry.ingest(event=changed, delivery_id="D-2", delivered_at=STAMP, policy=policy)


def test_create_or_get_is_idempotent_and_conflicting_spec_fails() -> None:
    operator, spec, event, decision = _operator_job(index=30)
    policy = _policy()
    same, replay = operator.create_or_get_job(
        event=event,
        decision=decision,
        policy=policy,
        occurred_at=STAMP,
        workspace_ref="sandbox://capabilities-t09/job",
        input_ref="fixture://review-input",
        output_ref="fixture://review-output",
        trigger_decision_ref=f"decision://{decision['content_hash']}",
        producer_role_ref="role://research_analysis-producer",
        reviewer_role_ref="role://evidence_review-reviewer",
    )
    assert replay is True
    assert same == spec
    assert len(operator.store.events(spec["job_id"])) == 1

    with ASSERT.assertRaisesRegex(ReviewOrchestrationContractError, "JOB_ID_CREATE_CONFLICT"):
        operator.create_or_get_job(
            event=event,
            decision=decision,
            policy=policy,
            occurred_at=STAMP,
            workspace_ref="sandbox://different",
            input_ref="fixture://review-input",
            output_ref="fixture://review-output",
            trigger_decision_ref=f"decision://{decision['content_hash']}",
            producer_role_ref="role://research_analysis-producer",
            reviewer_role_ref="role://evidence_review-reviewer",
        )


def test_create_or_get_rehydrates_from_file_store_without_second_event() -> None:
    with tempfile.TemporaryDirectory(prefix="capabilities-t09-rehydrate-") as temporary:
        root = Path(temporary)
        journal = root / "events.jsonl"
        operator, spec, event, decision = _operator_job(
            AppendOnlyReviewEventStore(journal, sandbox_root=root), index=39
        )
        assert len(operator.store.events(spec["job_id"])) == 1
        reopened = ReviewOrchestrationOperator(
            AppendOnlyReviewEventStore(journal, sandbox_root=root)
        )
        restored, replay = reopened.create_or_get_job(
            event=event,
            decision=decision,
            policy=_policy(),
            occurred_at=STAMP,
            workspace_ref="sandbox://capabilities-t09/job",
            input_ref="fixture://review-input",
            output_ref="fixture://review-output",
            trigger_decision_ref=f"decision://{decision['content_hash']}",
            producer_role_ref="role://research_analysis-producer",
            reviewer_role_ref="role://evidence_review-reviewer",
        )
        assert replay is True
        assert restored == spec
        assert len(reopened.store.events(spec["job_id"])) == 1
        assert reopened.explain(spec["job_id"])["job_spec"] == spec


def test_expected_head_cas_allows_only_one_concurrent_claim() -> None:
    operator, spec, _, _ = _operator_job(index=31)
    store = operator.store
    head = store.head(spec["job_id"])

    def claim(revision: int) -> str:
        try:
            store.append(
                job_id=spec["job_id"],
                event_type="RUN_CLAIMED",
                expected_previous_head_hash=head,
                actor_kind="WORKER",
                actor_ref=f"worker-{revision}",
                occurred_at=STAMP,
                payload={"revision": revision},
                dedupe_key=f"CLAIM-{revision}",
                reason_code="CONCURRENT_CLAIM",
                attempt_id=f"ATTEMPT-{revision}",
            )
        except StaleReviewHeadError:
            return "CONFLICT"
        return "CLAIMED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, (1, 2)))
    assert sorted(outcomes) == ["CLAIMED", "CONFLICT"]
    assert store.projection(spec["job_id"])["job_state"] == "RUNNING"
    assert store.projection(spec["job_id"])["attempt_count"] == 1


def test_event_dedupe_and_terminal_immutability() -> None:
    operator, spec, _, _ = _operator_job(index=32)
    store = operator.store
    _append_claim(store, spec["job_id"])
    terminal, replay = store.append(
        job_id=spec["job_id"],
        event_type="RUN_COMPLETED",
        expected_previous_head_hash=store.head(spec["job_id"]),
        actor_kind="WORKER",
        actor_ref="OFFLINE_BOUNDED_WORKER",
        occurred_at=STAMP,
        payload={"accounting_settlements": 1},
        dedupe_key="TERMINAL-1",
        reason_code="PASS",
        attempt_id="ATTEMPT-001",
        review_verdict="PASS",
    )
    assert replay is False
    same, replay = store.append(
        job_id=spec["job_id"],
        event_type="RUN_COMPLETED",
        expected_previous_head_hash="GENESIS",
        actor_kind="WORKER",
        actor_ref="OFFLINE_BOUNDED_WORKER",
        occurred_at=STAMP,
        payload={"accounting_settlements": 1},
        dedupe_key="TERMINAL-1",
        reason_code="PASS",
        attempt_id="ATTEMPT-001",
        review_verdict="PASS",
    )
    assert replay is True and same == terminal
    with ASSERT.assertRaisesRegex(ReviewTransitionError, "TERMINAL_STATE_TRANSITION_FORBIDDEN"):
        store.append(
            job_id=spec["job_id"],
            event_type="RUN_COMPLETED",
            expected_previous_head_hash=store.head(spec["job_id"]),
            actor_kind="WORKER",
            actor_ref="OFFLINE_BOUNDED_WORKER",
            occurred_at=STAMP,
            payload={"accounting_settlements": 2},
            dedupe_key="TERMINAL-2",
            reason_code="DUPLICATE_TERMINAL",
            attempt_id="ATTEMPT-001",
            review_verdict="PASS",
        )


def test_pause_resume_cancel_and_orphan_guard() -> None:
    operator, spec, _, _ = _operator_job(index=33)
    job_id = spec["job_id"]
    operator.pause(job_id, occurred_at=STAMP, reason="operator-window")
    assert operator.status(job_id)["job_state"] == "PAUSED"
    resumed = operator.resume(job_id, occurred_at=STAMP, evidence_ref="evidence://resume")
    assert operator.status(job_id)["job_state"] == "PENDING"
    resumed_again = operator.resume(job_id, occurred_at=STAMP, evidence_ref="evidence://resume")
    assert resumed_again == resumed
    _append_claim(operator.store, job_id)
    cancel = operator.cancel(job_id, occurred_at=STAMP, reason="bounded-timeout")
    assert operator.cancel(job_id, occurred_at=STAMP, reason="bounded-timeout") == cancel
    with ASSERT.assertRaisesRegex(ReviewOrchestrationContractError, "ORPHAN_PROCESS_DETECTED"):
        operator.acknowledge_cancel(job_id, occurred_at=STAMP, kill_receipt_ref="kill://bad", orphan_count=1)
    operator.acknowledge_cancel(job_id, occurred_at=STAMP, kill_receipt_ref="kill://clean", orphan_count=0)
    assert operator.status(job_id)["job_state"] == "ABORTED"
    assert operator.cancel(job_id, occurred_at=STAMP, reason="bounded-timeout") == cancel
    with ASSERT.assertRaisesRegex(ReviewOrchestrationContractError, "TERMINAL_JOB_ACTION_FORBIDDEN"):
        operator.pause(job_id, occurred_at=STAMP, reason="too-late")


def test_projection_rebuild_is_byte_equivalent_and_corrupt_tail_fails() -> None:
    with tempfile.TemporaryDirectory(prefix="capabilities-t09-projection-") as temporary:
        tmp_path = Path(temporary)
        journal = tmp_path / "events.jsonl"
        store = AppendOnlyReviewEventStore(journal, sandbox_root=tmp_path)
        operator, spec, _, _ = _operator_job(store, index=34)
        _append_claim(store, spec["job_id"])
        before = projection_bytes(store.events(spec["job_id"]))
        reopened = AppendOnlyReviewEventStore(journal, sandbox_root=tmp_path)
        assert projection_bytes(reopened.events(spec["job_id"])) == before
        with journal.open("ab") as handle:
            handle.write(b"{")
        with ASSERT.assertRaisesRegex(CorruptReviewEventError, "CORRUPT_EVENT_TAIL"):
            AppendOnlyReviewEventStore(journal, sandbox_root=tmp_path)


def test_dispatch_pre_send_requeues_once_then_completes() -> None:
    operator, spec, _, _ = _operator_job(index=35)
    first = operator.dispatch_once(
        spec["job_id"],
        occurred_at=STAMP,
        executor=lambda _spec, _attempt: {
            "crash_classification": "PROVED_NOT_SENT",
            "pre_send_receipt_state": "PROVED_NOT_SENT",
            "provider_request_receipt_state": "ABSENT",
            "external_calls": 0,
            "cost_cny": "0",
            "orphan_count": 0,
            "evidence_refs": ["evidence://pre-send"],
        },
    )
    assert first["projection"]["job_state"] == "PENDING"
    assert first["projection"]["attempt_count"] == 1
    second = operator.dispatch_once(
        spec["job_id"],
        occurred_at=STAMP,
        executor=lambda _spec, _attempt: {
            "attempt_terminal": "COMPLETED",
            "review_verdict": "PASS",
            "external_calls": 0,
            "cost_cny": "0.00",
            "orphan_count": 0,
            "evidence_refs": ["fixture://full-pass"],
        },
    )
    assert second["projection"]["job_state"] == "COMPLETED"
    assert second["projection"]["attempt_count"] == 2
    assert second["projection"]["review_verdict"] == "PASS"


def test_dispatch_unknown_after_send_and_partial_output_fail_closed() -> None:
    operator, spec, _, _ = _operator_job(index=36)
    result = operator.dispatch_once(
        spec["job_id"],
        occurred_at=STAMP,
        executor=lambda _spec, _attempt: {
            "crash_classification": "UNKNOWN_AFTER_SEND",
            "provider_request_receipt_state": "UNKNOWN",
            "external_calls": 0,
            "cost_cny": "0",
            "orphan_count": 0,
            "evidence_refs": ["evidence://ambiguous"],
        },
    )
    assert result["projection"]["job_state"] == "BLOCKED"
    assert result["terminal_event"]["reason_code"] == "UNKNOWN_AFTER_SEND"

    plan = diagnose_recovery(
        {"partial_output_state": "PRESENT"}, attempt_count=1, attempt_limit=2
    )
    assert plan["crash_classification"] == "NON_CONSUMABLE_PARTIAL"
    assert plan["partial_output_consumable"] is False
    assert plan["automatic_resend_authorized"] is False


def test_dispatch_rejects_external_effect_and_never_activates_production() -> None:
    operator, spec, _, _ = _operator_job(index=37)
    with ASSERT.assertRaisesRegex(ReviewOrchestrationContractError, "OFFLINE_EXECUTOR_EXTERNAL_EFFECT_FORBIDDEN"):
        operator.dispatch_once(
            spec["job_id"],
            occurred_at=STAMP,
            executor=lambda _spec, _attempt: {
                "attempt_terminal": "COMPLETED",
                "review_verdict": "PASS",
                "external_calls": 1,
                "cost_cny": "0",
                "orphan_count": 0,
            },
        )
    assert operator.status(spec["job_id"])["job_state"] == "BLOCKED"
    assert operator.explain(spec["job_id"])["production_activation"] is False


def test_executor_exception_is_durably_blocked_without_resend() -> None:
    operator, spec, _, _ = _operator_job(index=40)

    def broken_executor(_spec: dict[str, object], _attempt: str) -> dict[str, object]:
        raise RuntimeError("synthetic executor crash")

    with ASSERT.assertRaisesRegex(
        ReviewOrchestrationContractError, "EXECUTOR_EXCEPTION_UNKNOWN_AFTER_SEND"
    ):
        operator.dispatch_once(
            spec["job_id"], occurred_at=STAMP, executor=broken_executor
        )
    projection = operator.status(spec["job_id"])
    assert projection["job_state"] == "BLOCKED"
    assert projection["attempt_count"] == 1
    assert operator.store.events(spec["job_id"])[-1]["reason_code"] == "EXECUTOR_EXCEPTION_UNKNOWN_AFTER_SEND"


def test_recovery_and_lock_matrix() -> None:
    cases = [
        ({"terminal_receipt_state": "DURABLE"}, "TERMINAL_DURABLE", False),
        ({"partial_output_state": "PRESENT"}, "NON_CONSUMABLE_PARTIAL", False),
        (
            {"pre_send_receipt_state": "PROVED_NOT_SENT", "provider_request_receipt_state": "ABSENT"},
            "PROVED_NOT_SENT",
            True,
        ),
        ({"pid_state": "MISSING"}, "UNKNOWN_AFTER_SEND", False),
    ]
    for evidence, expected, requeue in cases:
        plan = diagnose_recovery(evidence, attempt_count=1, attempt_limit=2)
        assert plan["crash_classification"] == expected
        assert plan["automatic_requeue_authorized"] is requeue
        assert plan["automatic_resend_authorized"] is False

    assert diagnose_lock({"kernel_lock_state": "LIVE"})["disposition"] == "BLOCKED_LIVE_LOCK"
    assert diagnose_lock({"kernel_lock_state": "UNKNOWN"})["disposition"] == "BLOCKED_LOCK_AUTHORITY_UNKNOWN"
    insufficient = diagnose_lock(
        {"kernel_lock_state": "STALE_PROVEN", "owner_liveness": "PROVED_DEAD", "ledger_integrity": "CORRUPT"}
    )
    assert insufficient["sandbox_takeover_authorized"] is False
    allowed = diagnose_lock(
        {"kernel_lock_state": "STALE_PROVEN", "owner_liveness": "PROVED_DEAD", "ledger_integrity": "VALID"}
    )
    assert allowed["sandbox_takeover_authorized"] is True
    assert allowed["production_takeover_authorized"] is False


def test_contract_validation_rejects_relaxed_attempt_or_activation_policy() -> None:
    policy = _policy()
    assert validate_trigger_policy(policy) == policy
    changed = dict(policy)
    changed["max_dispatch_attempts"] = 3
    changed["content_hash"] = canonical_hash({key: value for key, value in changed.items() if key != "content_hash"})
    with ASSERT.assertRaisesRegex(ReviewOrchestrationContractError, "ATTEMPT_CEILING_INVALID"):
        validate_trigger_policy(changed)
    topology_drift = dict(policy)
    topology_drift["stage_topology_sha256"] = HASH_E
    topology_drift["content_hash"] = canonical_hash(
        {key: value for key, value in topology_drift.items() if key != "content_hash"}
    )
    with ASSERT.assertRaisesRegex(ReviewOrchestrationContractError, "STAGE_TOPOLOGY_HASH_MISMATCH"):
        validate_trigger_policy(topology_drift)

    operator, spec, _, _ = _operator_job(index=38)
    assert validate_review_job_spec(spec) == spec
    relaxed = dict(spec)
    relaxed["default_activation"] = "ENABLED"
    relaxed["content_hash"] = canonical_hash({key: value for key, value in relaxed.items() if key != "content_hash"})
    with ASSERT.assertRaisesRegex(ReviewOrchestrationContractError, "JOB_SPEC_CEILING_OR_ACTIVATION_INVALID"):
        validate_review_job_spec(relaxed)
    job_event = operator.store.events(spec["job_id"])[0]
    validate_review_job_event(job_event)
    invalid_event = dict(job_event)
    invalid_event["payload_hash"] = "GENESIS"
    invalid_event["event_hash"] = canonical_hash(
        {key: value for key, value in invalid_event.items() if key != "event_hash"}
    )
    with ASSERT.assertRaisesRegex(ReviewOrchestrationContractError, "SHA256_INVALID"):
        validate_review_job_event(invalid_event)


def test_qualification_requires_exact_denominator_fault_matrix_and_zero_effects() -> None:
    denominator = {
        "raw_event_deliveries": 14,
        "unique_event_identities": 12,
        "unique_analysis_subjects": 12,
        "trigger_decisions": 12,
        "review_jobs": 6,
        "physical_attempts": 7,
    }
    scenarios = {f"SC-{index:02d}": True for index in range(1, 30)}
    faults = {name: True for name in EXPECTED_FAULT_GROUPS}
    replays = {"projection": True, "serialization": True, "order": True}
    effects = {
        "network_calls": 0,
        "external_model_api_cli_calls": 0,
        "credential_reads": 0,
        "cost_cny": "0.00",
        "knowledge_admission_mutation_count": 0,
        "card_mutation_count": 0,
        "formal_runtime_log_artifact_registry_analysis_mutation_count": 0,
        "production_scheduler_activation": False,
        "production_worker_activation": False,
    }
    result = qualify_offline_run(
        denominator=denominator,
        scenario_results=scenarios,
        fault_results=faults,
        replay_results=replays,
        effects=effects,
    )
    assert result["run"] == {
        "lifecycle_status": "completed",
        "verification_result": "PASS",
        "acceptance_verdict": "NOT_ASSESSED",
    }
    assert result["capability_verdict"] == "REVIEW_ELIGIBLE_PASS"
    assert result["human_gate_b"] == "PENDING"
    assert result["formalization"] is False
    assert result["production_activation"] is False

    bad = qualify_offline_run(
        denominator={**denominator, "raw_event_deliveries": 13},
        scenario_results=scenarios,
        fault_results=faults,
        replay_results=replays,
        effects=effects,
    )
    assert bad["run"]["verification_result"] == "FAIL"


def test_file_store_cannot_escape_declared_sandbox() -> None:
    with tempfile.TemporaryDirectory(prefix="capabilities-t09-boundary-") as temporary:
        tmp_path = Path(temporary)
        outside = tmp_path.parent / "capabilities-t09-outside.jsonl"
        with ASSERT.assertRaisesRegex(ReviewOrchestrationContractError, "EVENT_STORE_OUTSIDE_SANDBOX"):
            AppendOnlyReviewEventStore(outside, sandbox_root=tmp_path)


TEST_FUNCTIONS = (
    test_five_draft_2020_12_schemas_and_contract_vectors,
    test_analysis_event_is_immutable_and_hash_bound,
    test_trigger_precedence_sampling_exempt_invalid_and_route_separation,
    test_restricted_ownership_keeps_decision_but_blocks_route,
    test_registry_denominator_replay_order_and_cutoff,
    test_registry_rejects_same_event_id_with_different_hash,
    test_create_or_get_is_idempotent_and_conflicting_spec_fails,
    test_create_or_get_rehydrates_from_file_store_without_second_event,
    test_expected_head_cas_allows_only_one_concurrent_claim,
    test_event_dedupe_and_terminal_immutability,
    test_pause_resume_cancel_and_orphan_guard,
    test_projection_rebuild_is_byte_equivalent_and_corrupt_tail_fails,
    test_dispatch_pre_send_requeues_once_then_completes,
    test_dispatch_unknown_after_send_and_partial_output_fail_closed,
    test_dispatch_rejects_external_effect_and_never_activates_production,
    test_executor_exception_is_durably_blocked_without_resend,
    test_recovery_and_lock_matrix,
    test_contract_validation_rejects_relaxed_attempt_or_activation_policy,
    test_qualification_requires_exact_denominator_fault_matrix_and_zero_effects,
    test_file_store_cannot_escape_declared_sandbox,
)


def load_tests(
    _loader: unittest.TestLoader,
    _tests: unittest.TestSuite,
    _pattern: str | None,
) -> unittest.TestSuite:
    return unittest.TestSuite(unittest.FunctionTestCase(function) for function in TEST_FUNCTIONS)


if __name__ == "__main__":
    unittest.TextTestRunner(verbosity=2).run(load_tests(unittest.defaultTestLoader, unittest.TestSuite(), None))
