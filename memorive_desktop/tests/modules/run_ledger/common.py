from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

RUN_ID = "model_evaluation-data_contracts-test-run"
TS = "2026-07-16T23:55:00+08:00"


class IsolatedLedgerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        base = Path(os.environ.get("Quality_T2_TEST_TMP", tempfile.gettempdir()))
        base.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(prefix="model_evaluation-data_contracts-", dir=base)
        self.temp_root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()


def attempt_record(
    *,
    attempt_id: str = "attempt:test:operation:1",
    run_id: str = RUN_ID,
    logical_operation_id: str = "operation:test",
    attempt_no: int = 1,
    retry_of_attempt_id: str | None = None,
    business_repair_of: str | None = None,
    result_category: str = "success",
    paper_id: str = "PaperA",
) -> dict:
    return {
        "schema_version": "2.0",
        "record_type": "ledger_record",
        "ledger_type": "attempt",
        "record_id": attempt_id,
        "record_kind": "terminal",
        "run_id": run_id,
        "created_at": TS,
        "source_evidence_refs": ["test://frozen-attempt-fixture"],
        "attempt_id": attempt_id,
        "logical_operation_id": logical_operation_id,
        "attempt_no": attempt_no,
        "retry_of_attempt_id": retry_of_attempt_id,
        "business_repair_of": business_repair_of,
        "task_id": "TASK_A",
        "stage": "verify",
        "paper_id": paper_id,
        "route_snapshot_id": None,
        "execution_config_hash": "a" * 64,
        "started_at": "2026-07-16T23:54:59+08:00",
        "finished_at": TS,
        "result_category": result_category,
        "error_code": None,
    }


def attempt_amendment(base: dict, *, fields: dict | None = None) -> dict:
    return {
        "schema_version": "2.0",
        "record_type": "ledger_record",
        "ledger_type": "attempt",
        "record_id": f"amendment:{base['attempt_id']}:accounting",
        "record_kind": "amendment",
        "run_id": base["run_id"],
        "created_at": "2026-07-16T23:56:00+08:00",
        "source_evidence_refs": ["test://late-accounting-fixture"],
        "attempt_id": base["attempt_id"],
        "amends_attempt_id": base["attempt_id"],
        "logical_operation_id": base["logical_operation_id"],
        "attempt_no": base["attempt_no"],
        "retry_of_attempt_id": base.get("retry_of_attempt_id"),
        "business_repair_of": base.get("business_repair_of"),
        "task_id": base["task_id"],
        "stage": base["stage"],
        "paper_id": base["paper_id"],
        "route_snapshot_id": base.get("route_snapshot_id"),
        "execution_config_hash": base.get("execution_config_hash"),
        "amendment_fields": fields
        or {"accounting": {"actual_cost_cny": 1.25, "total_tokens": 100}},
    }


def paper_outcome(
    paper_id: str = "PaperA",
    *,
    run_id: str = RUN_ID,
    category: str = "success",
    code: str | None = None,
    reasons: list[str] | None = None,
) -> dict:
    outcome_id = f"paper-outcome:{run_id}:{paper_id}:v1"
    return {
        "schema_version": "2.0",
        "record_type": "ledger_record",
        "ledger_type": "paper_outcome",
        "record_id": outcome_id,
        "record_kind": "terminal",
        "run_id": run_id,
        "created_at": TS,
        "source_evidence_refs": ["test://frozen-paper-fixture"],
        "outcome_id": outcome_id,
        "paper_id": paper_id,
        "outcome_category": category,
        "outcome_code": code or category.upper(),
        "reason_codes": reasons if reasons is not None else [code or category.upper()],
        "final_artifact_refs": [],
        "supersedes_record_id": None,
        "reprocesses_run_id": None,
    }


def publication_record(
    *,
    run_id: str = RUN_ID,
    status: str = "not_requested",
) -> dict:
    event_id = f"publication:{run_id}:{status}:v1"
    has_manifest = status in {"not_published", "published", "publication_failed"}
    published = status == "published"
    return {
        "schema_version": "2.0",
        "record_type": "ledger_record",
        "ledger_type": "publication",
        "record_id": event_id,
        "record_kind": "terminal",
        "run_id": run_id,
        "created_at": TS,
        "source_evidence_refs": ["test://publication-fixture"],
        "publication_event_id": event_id,
        "publication_status": status,
        "manifest_ref": "manifest://test" if has_manifest else None,
        "subject_artifact_refs": ["artifact://test"] if published else [],
        "receipt_ref": "publication-receipt://test" if published else None,
        "absence_reason": "NO_PUBLICATION_REQUEST" if status == "not_requested" else None,
        "supersedes_record_id": None,
    }


def run_terminal_event(
    *,
    run_id: str = RUN_ID,
    record_id: str | None = None,
    lifecycle_status: str = "completed",
    acceptance_verdict: str = "PASS",
    supersedes_record_id: str | None = None,
) -> dict:
    return {
        "schema_version": "2.0",
        "record_type": "run_terminal_event",
        "record_id": record_id or f"run-terminal:{run_id}:v1",
        "run_id": run_id,
        "lifecycle_status": lifecycle_status,
        "acceptance_verdict": acceptance_verdict,
        "terminal_at": TS,
        "source_evidence_refs": ["test://runner-supervisor-boundary"],
        "supersedes_record_id": supersedes_record_id,
    }
