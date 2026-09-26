"""Read-only adapter for the frozen Phase 1 round-7 evidence package."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .closure import validate_paper_outcome_closure
from .errors import EvidenceConflictError
from .lifecycle import RunClosure
from .schema import SCHEMA_VERSION, validate_record


@dataclass(frozen=True)
class AdaptationResult:
    attempts: tuple[dict[str, Any], ...]
    paper_outcomes: tuple[dict[str, Any], ...]
    publications: tuple[dict[str, Any], ...]
    run_terminal_event: dict[str, Any]
    run_closure: RunClosure
    run_facts: dict[str, Any]
    source_map: dict[str, str]

    def all_ledger_records(self) -> tuple[dict[str, Any], ...]:
        return self.attempts + self.paper_outcomes + self.publications

    def all_records(self) -> tuple[dict[str, Any], ...]:
        return self.all_ledger_records() + (self.run_terminal_event,)


class Phase1Round7Adapter:
    """Normalize only facts supported by the supplied manifests and ledgers."""

    def __init__(self, *, unpacked_root: Path | str, archive_root: Path | str):
        self.unpacked_root = Path(unpacked_root)
        self.archive_root = Path(archive_root)

    def adapt(self) -> AdaptationResult:
        paths = self._locate_sources()
        run_manifest = _read_json(paths["run_manifest"])
        sample_manifest = _read_json(paths["sample_manifest"])
        final_summary = _read_json(paths["final_summary"])
        receipt = _read_json(paths["archive_receipt"])
        call_rows = _read_jsonl(paths["call_ledger"])
        operation_rows = _read_jsonl(paths["operation_ledger"])
        acceptance_text = paths["acceptance_report"].read_text(encoding="utf-8")

        run_id = run_manifest.get("run_id")
        self._require_equal("sample_manifest.run_id", sample_manifest.get("run_id"), run_id)
        self._require_equal("final_summary.run_id", final_summary.get("run_id"), run_id)
        self._require_equal("archive_receipt.run_id", receipt.get("run_id"), run_id)
        self._verify_archive(paths["archive_zip"], receipt)

        actual_rows = [row for row in call_rows if row.get("started_at")]
        recovery_rows = [
            row
            for row in call_rows
            if row.get("recovery_code") == "INCOMPLETE_ACCOUNTING_RECOVERED_FROM_M11"
        ]
        expected_attempts = final_summary.get("experiment_total", {}).get("attempts")
        self._require_equal("experiment_total.attempts", expected_attempts, len(actual_rows))
        expected_success = final_summary.get("experiment_total", {}).get("successful_calls")
        self._require_equal(
            "experiment_total.successful_calls",
            expected_success,
            sum(row.get("status") == "success" for row in actual_rows),
        )
        expected_incomplete = final_summary.get("experiment_total", {}).get("incomplete_calls")
        self._require_equal(
            "experiment_total.incomplete_calls",
            expected_incomplete,
            sum("incomplete" in str(row.get("error", "")) for row in actual_rows),
        )
        self._require_equal("accounting recovery count", len(recovery_rows), 1)
        recovery_events = [
            row
            for row in operation_rows
            if row.get("event") == "INCOMPLETE_ACCOUNTING_RECOVERED_FROM_M11"
        ]
        self._require_equal("operation recovery count", len(recovery_events), 1)

        production_status = final_summary.get("production_status")
        self._require_equal(
            "receipt.production_status", receipt.get("production_status"), production_status
        )
        self._require_equal("production status", production_status, "KEEP_DISABLED")
        if "FULL_DUAL_CHANNEL_REJECT" not in acceptance_text:
            raise EvidenceConflictError(
                "acceptance report does not support FULL_DUAL_CHANNEL_REJECT"
            )
        if "KEEP_DISABLED" not in acceptance_text:
            raise EvidenceConflictError(
                "acceptance report does not support KEEP_DISABLED"
            )

        attempts = self._adapt_attempts(
            run_id=run_id,
            sample_manifest=sample_manifest,
            actual_rows=actual_rows,
            recovery_row=recovery_rows[0],
            recovery_event=recovery_events[0],
        )
        paper_outcomes = self._adapt_paper_outcomes(
            run_id=run_id,
            sample_manifest=sample_manifest,
            final_summary=final_summary,
            acceptance_report_name=paths["acceptance_report"].name,
        )
        validate_paper_outcome_closure(sample_manifest, list(paper_outcomes))
        publication = self._adapt_publication(
            run_id,
            final_summary,
            receipt,
            receipt_name=paths["archive_receipt"].name,
        )

        source_map = {
            key: str(path)
            for key, path in paths.items()
        }
        completed_at = final_summary["completed_at"]
        closure = RunClosure(
            run_id=run_id,
            lifecycle_status="completed",
            acceptance_verdict="FAIL",
            closed_at=completed_at,
            source_evidence_refs=(
                "unpacked/reports/final_summary.json",
                f"archive/{paths['acceptance_report'].name}",
            ),
        ).validate()
        run_terminal_event = closure.to_terminal_event(
            record_id=f"run-terminal:{run_id}:v1"
        ).as_dict()
        run_facts = {
            "archive_complete": bool(receipt.get("complete")),
            "archive_receipt_ref": f"archive/{paths['archive_receipt'].name}",
            "archive_sha256": receipt.get("archive_sha256"),
            "production_status": production_status,
            "acceptance_code": "FULL_DUAL_CHANNEL_REJECT",
            "external_api_calls_during_replay": 0,
        }
        if not run_facts["archive_complete"]:
            raise EvidenceConflictError("archive receipt does not assert complete=true")

        for record in (*attempts, *paper_outcomes, publication, run_terminal_event):
            validate_record(record)
        return AdaptationResult(
            attempts=attempts,
            paper_outcomes=paper_outcomes,
            publications=(publication,),
            run_terminal_event=run_terminal_event,
            run_closure=closure,
            run_facts=run_facts,
            source_map=source_map,
        )

    def _adapt_attempts(
        self,
        *,
        run_id: str,
        sample_manifest: dict,
        actual_rows: list[dict],
        recovery_row: dict,
        recovery_event: dict,
    ) -> tuple[dict[str, Any], ...]:
        task_index = {task["task_id"]: task for task in sample_manifest["tasks"]}
        counters: dict[str, int] = {}
        last_attempt: dict[str, str] = {}
        terminals: list[dict[str, Any]] = []
        terminal_index: dict[str, dict[str, Any]] = {}
        for row in actual_rows:
            task_id = row["task_id"]
            base_task_id = task_id.removesuffix("_RETRY")
            task = task_index.get(base_task_id)
            if task is None:
                raise EvidenceConflictError(
                    f"call ledger task absent from sample_manifest: {task_id}"
                )
            logical_operation_id = f"operation:{run_id}:{base_task_id}"
            counters[logical_operation_id] = counters.get(logical_operation_id, 0) + 1
            attempt_no = counters[logical_operation_id]
            attempt_id = f"attempt:{run_id}:{base_task_id}:{attempt_no}"
            retry_of = last_attempt.get(logical_operation_id)
            last_attempt[logical_operation_id] = attempt_id
            status = row.get("status")
            incomplete = "incomplete" in str(row.get("error", "")).lower()
            result_category = "success" if status == "success" else (
                "incomplete" if incomplete else "error"
            )
            record = {
                "schema_version": SCHEMA_VERSION,
                "record_type": "ledger_record",
                "ledger_type": "attempt",
                "record_id": attempt_id,
                "record_kind": "terminal",
                "run_id": run_id,
                "created_at": row.get("finished_at") or row.get("failed_at"),
                "source_evidence_refs": [
                    f"unpacked/costs/call_ledger.jsonl#L{row['__source_line__']}"
                ],
                "attempt_id": attempt_id,
                "logical_operation_id": logical_operation_id,
                "attempt_no": attempt_no,
                "retry_of_attempt_id": retry_of,
                "business_repair_of": None,
                "task_id": task_id,
                "stage": task["channel"],
                "paper_id": task["paper_id"],
                "route_snapshot_id": None,
                "execution_config_hash": row.get("execution_config_hash"),
                "started_at": row["started_at"],
                "finished_at": row.get("finished_at") or row.get("failed_at"),
                "result_category": result_category,
                "error_code": "MAX_TOKENS_INCOMPLETE" if incomplete else None,
                "provider_identity": {
                    "provider": row.get("provider"),
                    "requested_model": row.get("requested_model"),
                    "response_model": row.get("response_model"),
                },
            }
            terminals.append(record)
            terminal_index[task_id] = record

        target = terminal_index.get(recovery_row.get("task_id"))
        if target is None:
            raise EvidenceConflictError(
                "accounting recovery does not identify an observed actual attempt"
            )
        amendment_id = f"amendment:{target['attempt_id']}:accounting-recovery"
        amendment = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "ledger_record",
            "ledger_type": "attempt",
            "record_id": amendment_id,
            "record_kind": "amendment",
            "run_id": run_id,
            "created_at": recovery_row["recovered_at"],
            "source_evidence_refs": [
                f"unpacked/costs/call_ledger.jsonl#L{recovery_row['__source_line__']}",
                f"unpacked/ledgers/operation_ledger.jsonl#L{recovery_event['__source_line__']}",
            ],
            "attempt_id": target["attempt_id"],
            "amends_attempt_id": target["attempt_id"],
            "logical_operation_id": target["logical_operation_id"],
            "attempt_no": target["attempt_no"],
            "retry_of_attempt_id": target["retry_of_attempt_id"],
            "business_repair_of": None,
            "task_id": target["task_id"],
            "stage": target["stage"],
            "paper_id": target["paper_id"],
            "route_snapshot_id": None,
            "execution_config_hash": target["execution_config_hash"],
            "amendment_fields": {
                "accounting": {
                    key: recovery_row.get(key)
                    for key in (
                        "recovery_code",
                        "input_tokens",
                        "output_tokens",
                        "reasoning_tokens",
                        "total_tokens",
                        "actual_cost_cny",
                        "raw_response_recovered",
                        "request_id_recovered",
                    )
                }
            },
        }
        return tuple(terminals + [amendment])

    def _adapt_paper_outcomes(
        self,
        *,
        run_id: str,
        sample_manifest: dict,
        final_summary: dict,
        acceptance_report_name: str,
    ) -> tuple[dict[str, Any], ...]:
        papers = sorted({task["paper_id"] for task in sample_manifest["tasks"]})
        expected = {"Khafipour2020", "Yurtsever2020", "Zhang2024"}
        self._require_equal("round-7 frozen papers", set(papers), expected)
        completed_at = final_summary["completed_at"]
        outcomes: list[dict[str, Any]] = []
        for paper_id in papers:
            if paper_id == "Khafipour2020":
                category = "failure"
                code = "FULL_DUAL_CHANNEL_REJECT"
                reasons = ["FULL_DUAL_CHANNEL_REJECT", "UNSUPPORTED_ANCHOR_MISSED"]
            else:
                category = "not_run"
                code = "QUALITY_GATE_EARLY_STOP"
                reasons = ["QUALITY_GATE_EARLY_STOP"]
            outcome_id = f"paper-outcome:{run_id}:{paper_id}:v1"
            outcomes.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "record_type": "ledger_record",
                    "ledger_type": "paper_outcome",
                    "record_id": outcome_id,
                    "record_kind": "terminal",
                    "run_id": run_id,
                    "created_at": completed_at,
                    "source_evidence_refs": [
                        "unpacked/sample_manifest.json",
                        "unpacked/reports/final_summary.json",
                        f"archive/{acceptance_report_name}",
                    ],
                    "outcome_id": outcome_id,
                    "paper_id": paper_id,
                    "outcome_category": category,
                    "outcome_code": code,
                    "reason_codes": reasons,
                    "final_artifact_refs": [],
                    "supersedes_record_id": None,
                    "reprocesses_run_id": None,
                }
            )
        return tuple(outcomes)

    def _adapt_publication(
        self,
        run_id: str,
        final_summary: dict,
        receipt: dict,
        *,
        receipt_name: str,
    ) -> dict[str, Any]:
        event_id = f"publication:{run_id}:not-requested"
        return {
            "schema_version": SCHEMA_VERSION,
            "record_type": "ledger_record",
            "ledger_type": "publication",
            "record_id": event_id,
            "record_kind": "terminal",
            "run_id": run_id,
            "created_at": receipt["created_at"],
            "source_evidence_refs": [
                "unpacked/reports/final_summary.json",
                f"archive/{receipt_name}",
            ],
            "publication_event_id": event_id,
            "publication_status": "not_requested",
            "manifest_ref": None,
            "subject_artifact_refs": [],
            "receipt_ref": None,
            "absence_reason": "NO_PUBLICATION_REQUEST",
            "supersedes_record_id": None,
        }

    def _locate_sources(self) -> dict[str, Path]:
        sources = {
            "run_manifest": self.unpacked_root / "run_manifest.json",
            "sample_manifest": self.unpacked_root / "sample_manifest.json",
            "call_ledger": self.unpacked_root / "costs" / "call_ledger.jsonl",
            "operation_ledger": self.unpacked_root
            / "ledgers"
            / "operation_ledger.jsonl",
            "final_summary": self.unpacked_root / "reports" / "final_summary.json",
            "archive_receipt": _one(self.archive_root, "*归档回执*.json"),
            "acceptance_report": _one(self.archive_root, "*验收报告*.md"),
            "archive_zip": _one(self.archive_root, "*.zip"),
        }
        missing = [str(path) for path in sources.values() if not path.is_file()]
        if missing:
            raise FileNotFoundError("missing Phase 1 evidence: " + ", ".join(missing))
        return sources

    @staticmethod
    def _require_equal(label: str, observed: Any, expected: Any) -> None:
        if observed != expected:
            raise EvidenceConflictError(
                f"evidence conflict for {label}: observed={observed!r}, expected={expected!r}"
            )

    @staticmethod
    def _verify_archive(path: Path, receipt: dict) -> None:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != receipt.get("archive_sha256"):
            raise EvidenceConflictError("archive SHA-256 disagrees with receipt")
        if path.stat().st_size != receipt.get("archive_size"):
            raise EvidenceConflictError("archive size disagrees with receipt")


def _one(root: Path, pattern: str) -> Path:
    matches = sorted(root.glob(pattern))
    if len(matches) != 1:
        raise FileNotFoundError(
            f"expected exactly one {pattern!r} below {root}, found {len(matches)}"
        )
    return matches[0]


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise EvidenceConflictError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvidenceConflictError(
                    f"invalid source JSONL {path} line {line_no}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise EvidenceConflictError(
                    f"source JSONL row is not an object: {path} line {line_no}"
                )
            row["__source_line__"] = line_no
            rows.append(row)
    return rows
