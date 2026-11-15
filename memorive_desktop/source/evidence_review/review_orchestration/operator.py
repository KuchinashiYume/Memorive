from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .contracts import (
    ReviewOrchestrationContractError,
    build_review_job_spec,
    canonical_hash,
    validate_review_job_spec,
)
from .event_store import AppendOnlyReviewEventStore
from .recovery import diagnose_recovery


FrozenExecutor = Callable[[dict[str, Any], str], Mapping[str, Any]]


@dataclass
class ReviewOrchestrationOperator:
    store: AppendOnlyReviewEventStore
    actor_ref: str = "REVIEW_ORCHESTRATION_OPERATOR"
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def create_or_get_job(
        self,
        *,
        event: Mapping[str, Any],
        decision: Mapping[str, Any],
        policy: Mapping[str, Any],
        occurred_at: str,
        workspace_ref: str,
        input_ref: str,
        output_ref: str,
        trigger_decision_ref: str,
        producer_role_ref: str,
        reviewer_role_ref: str,
    ) -> tuple[dict[str, Any], bool]:
        spec = build_review_job_spec(
            event=event,
            decision=decision,
            policy=policy,
            workspace_ref=workspace_ref,
            input_ref=input_ref,
            output_ref=output_ref,
            trigger_decision_ref=trigger_decision_ref,
            producer_role_ref=producer_role_ref,
            reviewer_role_ref=reviewer_role_ref,
        )
        job_id = spec["job_id"]
        if job_id in self.jobs:
            if self.jobs[job_id]["content_hash"] != spec["content_hash"]:
                raise ReviewOrchestrationContractError("JOB_ID_CREATE_CONFLICT")
            return deepcopy(self.jobs[job_id]), True
        _, replay = self.store.append(
            job_id=job_id,
            event_type="JOB_CREATED",
            expected_previous_head_hash="GENESIS",
            actor_kind="SYSTEM",
            actor_ref=self.actor_ref,
            occurred_at=occurred_at,
            payload={"job_spec_hash": spec["content_hash"]},
            dedupe_key=f"JOB_CREATED:{spec['review_job_key']}",
            reason_code="TRIGGER_DECISION_SELECTED",
            evidence_refs=[trigger_decision_ref],
        )
        self.jobs[job_id] = spec
        return deepcopy(spec), replay

    def status(self, job_id: str) -> dict[str, Any] | None:
        return self.store.projection(job_id)

    def explain(self, job_id: str) -> dict[str, Any]:
        if job_id not in self.jobs:
            raise ReviewOrchestrationContractError("JOB_NOT_FOUND")
        return {
            "job_spec": deepcopy(self.jobs[job_id]),
            "projection": self.store.projection(job_id),
            "event_count": len(self.store.events(job_id)),
            "production_activation": False,
        }

    def pause(self, job_id: str, *, occurred_at: str, reason: str) -> dict[str, Any]:
        dedupe_key = f"PAUSE:{job_id}:{reason}"
        existing = self.store.event_by_dedupe(job_id, dedupe_key)
        if existing is not None:
            return existing
        state = self._state(job_id)
        event_type = "PAUSE_REQUESTED" if state == "RUNNING" else "RUN_PAUSED"
        event, _ = self.store.append(
            job_id=job_id,
            event_type=event_type,
            expected_previous_head_hash=self.store.head(job_id),
            actor_kind="OPERATOR",
            actor_ref=self.actor_ref,
            occurred_at=occurred_at,
            payload={"reason": reason},
            dedupe_key=dedupe_key,
            reason_code="OPERATOR_PAUSE",
        )
        return event

    def acknowledge_pause(self, job_id: str, *, occurred_at: str, checkpoint_ref: str) -> dict[str, Any]:
        event, _ = self.store.append(
            job_id=job_id,
            event_type="RUN_PAUSED",
            expected_previous_head_hash=self.store.head(job_id),
            actor_kind="WORKER",
            actor_ref="OFFLINE_BOUNDED_WORKER",
            occurred_at=occurred_at,
            payload={"checkpoint_ref": checkpoint_ref},
            dedupe_key=f"RUN_PAUSED:{job_id}:{checkpoint_ref}",
            reason_code="SAFE_CHECKPOINT_REACHED",
            evidence_refs=[checkpoint_ref],
        )
        return event

    def resume(self, job_id: str, *, occurred_at: str, evidence_ref: str) -> dict[str, Any]:
        dedupe_key = f"RESUME:{job_id}:{evidence_ref}"
        existing = self.store.event_by_dedupe(job_id, dedupe_key)
        if existing is not None:
            return existing
        if self._state(job_id) not in {"PAUSED", "BLOCKED"}:
            raise ReviewOrchestrationContractError("RESUME_STATE_INVALID")
        event, _ = self.store.append(
            job_id=job_id,
            event_type="RUN_RESUMED",
            expected_previous_head_hash=self.store.head(job_id),
            actor_kind="OPERATOR",
            actor_ref=self.actor_ref,
            occurred_at=occurred_at,
            payload={"unblock_evidence_ref": evidence_ref},
            dedupe_key=dedupe_key,
            reason_code="NEW_EVIDENCE_BOUND",
            evidence_refs=[evidence_ref],
        )
        return event

    def cancel(self, job_id: str, *, occurred_at: str, reason: str) -> dict[str, Any]:
        dedupe_key = f"CANCEL:{job_id}:{reason}"
        existing = self.store.event_by_dedupe(job_id, dedupe_key)
        if existing is not None:
            return existing
        state = self._state(job_id)
        if state == "RUNNING":
            event_type = "CANCEL_REQUESTED"
        elif state in {"PENDING", "PAUSED", "BLOCKED"}:
            event_type = "RUN_ABORTED"
        else:
            raise ReviewOrchestrationContractError("CANCEL_STATE_INVALID")
        event, _ = self.store.append(
            job_id=job_id,
            event_type=event_type,
            expected_previous_head_hash=self.store.head(job_id),
            actor_kind="OPERATOR",
            actor_ref=self.actor_ref,
            occurred_at=occurred_at,
            payload={"reason": reason},
            dedupe_key=dedupe_key,
            reason_code="OPERATOR_CANCEL",
        )
        return event

    def acknowledge_cancel(self, job_id: str, *, occurred_at: str, kill_receipt_ref: str, orphan_count: int) -> dict[str, Any]:
        if orphan_count != 0:
            raise ReviewOrchestrationContractError("ORPHAN_PROCESS_DETECTED")
        event, _ = self.store.append(
            job_id=job_id,
            event_type="RUN_ABORTED",
            expected_previous_head_hash=self.store.head(job_id),
            actor_kind="WORKER",
            actor_ref="Execution_EXECUTION_CORE",
            occurred_at=occurred_at,
            payload={"kill_receipt_ref": kill_receipt_ref, "orphan_count": orphan_count},
            dedupe_key=f"RUN_ABORTED:{job_id}:{kill_receipt_ref}",
            reason_code="KILL_TREE_CONFIRMED_CLEAN",
            evidence_refs=[kill_receipt_ref],
        )
        return event

    def dispatch_once(
        self,
        job_id: str,
        *,
        occurred_at: str,
        executor: FrozenExecutor,
        mode: str = "OFFLINE_FROZEN_OUTPUT",
    ) -> dict[str, Any]:
        if mode != "OFFLINE_FROZEN_OUTPUT":
            raise ReviewOrchestrationContractError("PRODUCTION_DISPATCH_NOT_AUTHORIZED")
        spec = validate_review_job_spec(self.jobs.get(job_id, {}))
        projection = self.store.projection(job_id)
        if projection is None or projection["job_state"] != "PENDING":
            raise ReviewOrchestrationContractError("JOB_NOT_PENDING")
        attempt_ordinal = projection["attempt_count"] + 1
        if attempt_ordinal > spec["max_dispatch_attempts"]:
            raise ReviewOrchestrationContractError("DISPATCH_ATTEMPT_LIMIT_EXHAUSTED")
        attempt_id = f"{job_id}-ATTEMPT-{attempt_ordinal:03d}"
        self.store.append(
            job_id=job_id,
            event_type="RUN_CLAIMED",
            expected_previous_head_hash=self.store.head(job_id),
            actor_kind="WORKER",
            actor_ref="OFFLINE_BOUNDED_WORKER",
            occurred_at=occurred_at,
            payload={"attempt_id": attempt_id, "mode": mode},
            dedupe_key=f"RUN_CLAIMED:{attempt_id}",
            reason_code="SINGLE_BOUNDED_DISPATCH",
            attempt_id=attempt_id,
        )
        try:
            outcome = dict(executor(deepcopy(spec), attempt_id))
        except Exception as exc:
            self._block_guard_violation(
                job_id=job_id,
                attempt_id=attempt_id,
                occurred_at=occurred_at,
                reason_code="EXECUTOR_EXCEPTION_UNKNOWN_AFTER_SEND",
                outcome={"exception_type": type(exc).__name__},
            )
            raise ReviewOrchestrationContractError(
                "EXECUTOR_EXCEPTION_UNKNOWN_AFTER_SEND", type(exc).__name__
            ) from exc
        if outcome.get("external_calls") != 0 or str(outcome.get("cost_cny")) not in {"0", "0.0", "0.00"}:
            self._block_guard_violation(
                job_id=job_id,
                attempt_id=attempt_id,
                occurred_at=occurred_at,
                reason_code="OFFLINE_EXECUTOR_EXTERNAL_EFFECT_FORBIDDEN",
                outcome=outcome,
            )
            raise ReviewOrchestrationContractError("OFFLINE_EXECUTOR_EXTERNAL_EFFECT_FORBIDDEN")
        if outcome.get("orphan_count") != 0:
            self._block_guard_violation(
                job_id=job_id,
                attempt_id=attempt_id,
                occurred_at=occurred_at,
                reason_code="ORPHAN_PROCESS_DETECTED",
                outcome=outcome,
            )
            raise ReviewOrchestrationContractError("ORPHAN_PROCESS_DETECTED")
        classification = outcome.get("crash_classification")
        if classification in {"PROVED_NOT_SENT", "UNKNOWN_AFTER_SEND", "NON_CONSUMABLE_PARTIAL", "TERMINAL_DURABLE"}:
            plan = diagnose_recovery(
                outcome,
                attempt_count=attempt_ordinal,
                attempt_limit=spec["max_dispatch_attempts"],
            )
            if plan["crash_classification"] == "PROVED_NOT_SENT" and plan["automatic_requeue_authorized"]:
                event_type = "RUN_REQUEUED"
                guard = "PROVED_NOT_SENT_AND_ATTEMPT_BUDGET_REMAINS"
                reason_code = "PROVED_NOT_SENT_REQUEUE"
            elif plan["crash_classification"] == "TERMINAL_DURABLE":
                event_type = "RUN_COMPLETED"
                guard = None
                reason_code = "DURABLE_TERMINAL_FINALIZED"
            else:
                event_type = "RUN_BLOCKED"
                guard = None
                reason_code = plan["crash_classification"]
        else:
            terminal = outcome.get("attempt_terminal")
            if terminal == "COMPLETED":
                event_type, reason_code, guard = "RUN_COMPLETED", "FROZEN_OUTPUT_COMPLETED", None
            elif terminal == "BLOCKED":
                event_type, reason_code, guard = "RUN_BLOCKED", "FROZEN_OUTPUT_BLOCKED", None
            elif terminal in {"ABORTED", "CANCELLED"}:
                event_type, reason_code, guard = "RUN_ABORTED", "FROZEN_OUTPUT_ABORTED", None
            else:
                event_type, reason_code, guard = "RUN_FAILED", "FROZEN_OUTPUT_FAILED", None
        event, _ = self.store.append(
            job_id=job_id,
            event_type=event_type,
            expected_previous_head_hash=self.store.head(job_id),
            actor_kind="WORKER",
            actor_ref="OFFLINE_BOUNDED_WORKER",
            occurred_at=outcome.get("occurred_at", occurred_at),
            payload=outcome,
            dedupe_key=f"{event_type}:{attempt_id}",
            reason_code=reason_code,
            evidence_refs=list(outcome.get("evidence_refs", [])),
            attempt_id=attempt_id,
            transition_guard=guard,
            review_verdict=outcome.get("review_verdict"),
        )
        return {"attempt_id": attempt_id, "terminal_event": event, "projection": self.store.projection(job_id), "outcome": outcome}

    def recover_dry_run(self, evidence: Mapping[str, Any], *, attempt_count: int, attempt_limit: int) -> dict[str, Any]:
        return diagnose_recovery(evidence, attempt_count=attempt_count, attempt_limit=attempt_limit)

    def _block_guard_violation(
        self,
        *,
        job_id: str,
        attempt_id: str,
        occurred_at: str,
        reason_code: str,
        outcome: Mapping[str, Any],
    ) -> None:
        self.store.append(
            job_id=job_id,
            event_type="RUN_BLOCKED",
            expected_previous_head_hash=self.store.head(job_id),
            actor_kind="SYSTEM",
            actor_ref=self.actor_ref,
            occurred_at=occurred_at,
            payload={"guard_violation": reason_code, "outcome_hash": canonical_hash(dict(outcome))},
            dedupe_key=f"RUN_BLOCKED:{attempt_id}:{reason_code}",
            reason_code=reason_code,
            attempt_id=attempt_id,
            transition_guard="OFFLINE_ZERO_EFFECT_FAIL_CLOSED",
        )

    def _state(self, job_id: str) -> str:
        projection = self.store.projection(job_id)
        if projection is None:
            raise ReviewOrchestrationContractError("JOB_NOT_FOUND")
        if projection["terminal"]:
            raise ReviewOrchestrationContractError("TERMINAL_JOB_ACTION_FORBIDDEN")
        return projection["job_state"]


__all__ = ["ReviewOrchestrationOperator"]
