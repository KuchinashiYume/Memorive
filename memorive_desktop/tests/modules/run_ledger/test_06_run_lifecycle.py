from __future__ import annotations

from dataclasses import FrozenInstanceError

from run_ledger.lifecycle import RunTerminalEvent
from run_ledger.store import LedgerStore
from run_ledger.lifecycle import RunClosure
from run_ledger.errors import SchemaValidationError
from tests.modules.run_ledger.common import IsolatedLedgerTestCase, TS


def closure(run_id: str, lifecycle: str, verdict: str) -> RunClosure:
    return RunClosure(
        run_id=run_id,
        lifecycle_status=lifecycle,
        acceptance_verdict=verdict,
        closed_at=TS,
        source_evidence_refs=("test://runner-supervisor-boundary",),
    )


def event(
    run_id: str,
    lifecycle: str,
    verdict: str,
    *,
    record_id: str | None = None,
    supersedes_record_id: str | None = None,
) -> RunTerminalEvent:
    return RunTerminalEvent(
        record_id=record_id or f"run-terminal:{run_id}:v1",
        run_id=run_id,
        lifecycle_status=lifecycle,
        acceptance_verdict=verdict,
        terminal_at=TS,
        source_evidence_refs=("test://runner-supervisor-boundary",),
        supersedes_record_id=supersedes_record_id,
    )


class RunLifecycleTests(IsolatedLedgerTestCase):
    def test_completed_fail_is_valid_and_is_not_invalidated(self) -> None:
        round7 = event("core-round7", "completed", "FAIL").validate()
        self.assertEqual(round7.lifecycle_status, "completed")
        self.assertEqual(round7.acceptance_verdict, "FAIL")
        self.assertEqual(round7.as_dict()["record_type"], "run_terminal_event")

    def test_cancel_and_frozen_boundary_change_remain_distinct(self) -> None:
        aborted = event("cancelled-run", "aborted", "NOT_ASSESSED").validate()
        invalidated = event(
            "changed-frozen-input", "invalidated", "NOT_ASSESSED"
        ).validate()
        self.assertNotEqual(aborted.lifecycle_status, invalidated.lifecycle_status)

    def test_aborted_or_invalidated_cannot_claim_an_acceptance_result(self) -> None:
        for lifecycle in ("aborted", "invalidated"):
            with self.assertRaises(SchemaValidationError):
                event("bad-combination", lifecycle, "FAIL").validate()

    def test_terminal_event_is_persisted_idempotent_and_superseded_append_only(self) -> None:
        store = LedgerStore(self.temp_root / "ledger")
        first = event("run-corrected", "completed", "FAIL")
        self.assertEqual(store.append(first.as_dict()), "appended")
        self.assertEqual(store.append(first.as_dict()), "noop")
        self.assertEqual(store.current_run_terminal("run-corrected")["acceptance_verdict"], "FAIL")

        corrected = event(
            "run-corrected",
            "completed",
            "PASS",
            record_id="run-terminal:run-corrected:v2",
            supersedes_record_id=first.record_id,
        )
        self.assertEqual(store.append(corrected.as_dict()), "appended")
        self.assertEqual(store.current_run_terminal("run-corrected")["record_id"], corrected.record_id)
        self.assertEqual(len(store.run_terminal_events(run_id="run-corrected")), 2)

    def test_terminal_event_chain_rejects_orphan_and_parallel_head(self) -> None:
        store = LedgerStore(self.temp_root / "ledger")
        orphan = event(
            "orphan-run",
            "completed",
            "PASS",
            supersedes_record_id="run-terminal:missing",
        )
        with self.assertRaises(SchemaValidationError):
            store.append(orphan.as_dict())

        first = event("single-head", "completed", "FAIL")
        store.append(first.as_dict())
        parallel = event(
            "single-head",
            "completed",
            "PASS",
            record_id="run-terminal:single-head:v2",
        )
        with self.assertRaises(SchemaValidationError):
            store.append(parallel.as_dict())

    def test_old_run_closure_is_immutable_and_later_success_uses_new_run(self) -> None:
        old = event("run-failed", "completed", "FAIL").validate()
        new = event("run-fixed", "completed", "PASS").validate()
        self.assertNotEqual(old.run_id, new.run_id)
        with self.assertRaises(FrozenInstanceError):
            old.acceptance_verdict = "PASS"  # type: ignore[misc]

    def test_run_closure_compatibility_converts_to_persistent_event(self) -> None:
        legacy_value = closure("compat-run", "completed", "FAIL")
        persistent = legacy_value.to_terminal_event(
            record_id="run-terminal:compat-run:v1"
        )
        self.assertEqual(persistent.terminal_at, legacy_value.closed_at)
        self.assertEqual(persistent.acceptance_verdict, "FAIL")
