from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from run_ledger import ConcurrentWriterError
from run_ledger.channel_evidence import ChannelAttemptBuilder, build_accounting_amendment, derive_evidence_class, validate_accounting, validate_channel_attempt
from run_ledger.channel_store import ChannelLedgerStore
from run_ledger.errors import AppendOnlyViolation, ChannelEvidenceError, RecordConflictError
from run_ledger.channel_store import SANDBOX_MARKER_BYTES, SANDBOX_MARKER_NAME
from memorive.models.gateway.execution_core import (
    ExecutionCore,
    HandoffContract,
    IsolatedJobRunner,
    OutputNormalizer,
    canonical_sha256,
)
from memorive.models.gateway.execution_core.contracts import base_object
from tests.modules.model_gateway.test_execution_core import (
    CORE_SCHEMA,
    FIXED_CLOCK,
    PAYLOAD_SCHEMA,
    execution_request,
    process_spec,
    registry_with_profile,
    role_binding,
)


HERE = Path(__file__).resolve().parent
MATRIX_PATH = HERE / "fixtures" / "accounting_matrix.json"
CHANNEL_SCHEMA_PATH = HERE.parents[2] / "source" / "run_ledger" / "schemas" / "runtime_log_channel_attempt_v1.schema.json"
TS = "2026-08-13T11:00:00Z"
HASH_A = "A" * 64
HASH_B = "B" * 64


def matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text("utf-8"))


def case(case_id: str) -> dict:
    return next(row for row in matrix()["cases"] if row["case_id"] == case_id)


def successful_handoff(
    *,
    job_id: str = "job_t03",
    attempt_id: str = "attempt_001",
    predecessor_attempt_ref: str | None = None,
) -> dict:
    registry, profile = registry_with_profile()
    selector = __import__(
        "memorive.models.gateway.execution_core", fromlist=["ChannelSelector"]
    ).ChannelSelector(CORE_SCHEMA, registry, clock=FIXED_CLOCK)
    handoff = HandoffContract(CORE_SCHEMA, clock=FIXED_CLOCK)
    core = ExecutionCore(
        CORE_SCHEMA,
        selector,
        runner=IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK),
        normalizer=OutputNormalizer(CORE_SCHEMA, clock=FIXED_CLOCK),
        handoff=handoff,
    )
    with tempfile.TemporaryDirectory(prefix="ledger-") as temp:
        outcome = core.execute(
            execution_request(),
            role_binding("profile_fake"),
            [profile],
            workspace_parent=temp,
            job_id=job_id,
            attempt_id=attempt_id,
            predecessor_attempt_ref=predecessor_attempt_ref,
            input_materials={},
            protected_roots=((Path(__file__).resolve().parents[3] / "source"),),
            process_spec_factory=lambda workspace, selected, manifest: process_spec(
                workspace, mode="valid", attempt_id=attempt_id, value="service_contracts"
            ),
            payload_schema_ref="schema:test-output@v1",
            payload_schema=PAYLOAD_SCHEMA,
        )
    return outcome.handoff


def blocked_handoff(*, job_id: str = "job_blocked", attempt_id: str = "attempt_001") -> dict:
    registry, profile = registry_with_profile(status="DISABLED")
    selector = __import__(
        "memorive.models.gateway.execution_core", fromlist=["ChannelSelector"]
    ).ChannelSelector(CORE_SCHEMA, registry, clock=FIXED_CLOCK)
    outcome = selector.select(execution_request(), role_binding("profile_fake"), [profile])
    return HandoffContract(CORE_SCHEMA, clock=FIXED_CLOCK).build(
        job_id=job_id,
        attempt_id=attempt_id,
        objects=(("selection", outcome.decision), ("route_block", outcome.block_receipt)),
        terminal={
            "terminal_state": "BLOCKED",
            "verification_result": "NOT_ASSESSED",
            "acceptance_verdict": "NOT_ASSESSED",
            "execution_result": "NOT_RUN",
            "error_code": "ROUTE_POLICY_BLOCKED",
            "result_ref": None,
            "physical_process_start_count": 0,
            "external_request_start_count": 0,
            "token_status": "N/A_BLOCKED",
            "cost_status": "N/A_BLOCKED",
            "attempt_lineage": {"attempt_id": attempt_id, "predecessor_attempt_ref": None},
        },
    )


def not_started_handoff(
    *, job_id: str = "job_not_started", attempt_id: str = "attempt_001"
) -> dict:
    registry, profile = registry_with_profile()
    selector = __import__(
        "memorive.models.gateway.execution_core", fromlist=["ChannelSelector"]
    ).ChannelSelector(CORE_SCHEMA, registry, clock=FIXED_CLOCK)
    core = ExecutionCore(
        CORE_SCHEMA,
        selector,
        runner=IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK),
        normalizer=OutputNormalizer(CORE_SCHEMA, clock=FIXED_CLOCK),
        handoff=HandoffContract(CORE_SCHEMA, clock=FIXED_CLOCK),
    )
    with tempfile.TemporaryDirectory(prefix="ledger-not-started-") as temp:
        outcome = core.execute(
            execution_request(),
            role_binding("profile_fake"),
            [profile],
            workspace_parent=temp,
            job_id=job_id,
            attempt_id=attempt_id,
            predecessor_attempt_ref=None,
            input_materials={},
            protected_roots=((Path(__file__).resolve().parents[3] / "source"),),
            process_spec_factory=lambda workspace, selected, manifest: process_spec(
                workspace,
                mode="valid",
                attempt_id=attempt_id,
                cwd=str(Path(temp).resolve()),
            ),
            payload_schema_ref="schema:test-output@v1",
            payload_schema=PAYLOAD_SCHEMA,
        )
    self_contained = deepcopy(outcome.handoff)
    terminal = event(self_contained, "terminal")["payload"]
    terminal["token_status"] = "UNKNOWN"
    terminal["cost_status"] = "UNKNOWN"
    return HandoffContract(CORE_SCHEMA, clock=FIXED_CLOCK).build(
        job_id=self_contained["job_id"],
        attempt_id=self_contained["attempt_id"],
        objects=tuple(
            (item["event_type"], item["payload"])
            for item in self_contained["events"]
            if item["event_type"] != "terminal"
        ),
        terminal=terminal,
    )


def event(handoff: dict, event_type: str) -> dict:
    return next(item for item in handoff["events"] if item["event_type"] == event_type)


def with_model_event(
    handoff: dict,
    accounting_case: dict,
    *,
    returned_model: str | None = "model-test-v1",
    model_identity_grade: str = "EXACT",
) -> dict:
    selection = event(handoff, "selection")["payload"]
    workspace = event(handoff, "workspace")["payload"]
    process = event(handoff, "process")["payload"]
    normalized = event(handoff, "schema")["payload"]
    attempt_id = handoff["attempt_id"]
    model = base_object(
        "ModelExecutionReceipt",
        f"modelexecutionreceipt_{handoff['job_id']}_{attempt_id}",
        producer="Execution_EVIDENCE_ADAPTER",
        single_writer="Execution_EVIDENCE_ADAPTER",
        consumers=("RUNTIME_LOG",),
        clock=FIXED_CLOCK,
        source_evidence_refs=(process["object_id"],),
    )
    accounting = accounting_case["accounting"]
    model.update(
        {
            "job_id": handoff["job_id"],
            "attempt_id": attempt_id,
            "evidence_class": accounting_case["evidence_class"],
            "requested_model": "model-test-v1",
            "returned_model": returned_model,
            "model_identity_grade": model_identity_grade,
            "provider": "provider-test",
            "route": "synthetic-offline-route",
            "region": "test-region",
            "egress": "disabled-fixture",
            "behavior_hash": HASH_A,
            "input_hash": canonical_sha256(workspace["input_hashes"]),
            "output_hash": normalized["source_output_hash"],
            "usage_status": accounting["usage_status"],
            "token_usage": accounting["token_usage"],
            "cost_status": accounting["cost_status"],
            "cost_value": accounting["cost_value"],
            "currency": accounting["currency"],
            "latency_ms": process["duration_ms"],
            "finish_status": "COMPLETED",
            "external_request_started": accounting_case["evidence_class"] != "local_compute",
        }
    )
    terminal = deepcopy(event(handoff, "terminal")["payload"])
    terminal["external_request_start_count"] = (
        0 if accounting_case["evidence_class"] == "local_compute" else 1
    )
    terminal["token_status"] = accounting["usage_status"]
    terminal["cost_status"] = accounting["cost_status"]
    return HandoffContract(CORE_SCHEMA, clock=FIXED_CLOCK).build(
        job_id=handoff["job_id"],
        attempt_id=attempt_id,
        objects=(
            ("selection", selection),
            ("workspace", workspace),
            ("process", process),
            ("model", model),
            ("schema", normalized),
        ),
        terminal=terminal,
    )


def human_record(*, attempt_id: str = "attempt_human_001") -> dict:
    row = case("HUMAN")
    return {
        "schema_version": "CHANNEL_ACCOUNTING_CHANNEL_ATTEMPT_V1",
        "record_type": "channel_attempt_record",
        "record_kind": "terminal",
        "record_id": attempt_id,
        "run_id": "job_human",
        "created_at": TS,
        "source_evidence_refs": ["human-authority://gate-fixture"],
        "attempt_id": attempt_id,
        "logical_operation_id": "operation_human_gate",
        "attempt_no": 1,
        "retry_of_attempt_id": None,
        "task_id": "LEDGER_OFFLINE",
        "stage": "construction_a",
        "paper_id": None,
        "profile_ref": None,
        "attempt_started": True,
        "started_at": TS,
        "finished_at": TS,
        "result_category": "success",
        "error_code": None,
        "verification_result": "NOT_ASSESSED",
        "acceptance_verdict": "NOT_ASSESSED",
        "handoff_identity": {
            "schema_version": "EXECUTION_CORE_ServiceContracts_EVENT_HANDOFF_V1",
            "handoff_id": "human_authority_event_fixture",
            "events_sha256": HASH_B,
            "event_count": 1,
        },
        "provider_identity": {
            "provider": None,
            "requested_model": None,
            "returned_model": None,
            "model_identity_grade": None,
        },
        "process_evidence": {
            "adapter_id": None,
            "argv_redacted": None,
            "working_directory": None,
            "started_at": None,
            "ended_at": None,
            "duration_ms": None,
            "exit_code": None,
            "timeout": False,
            "killed": False,
            "stdout_hash": None,
            "stderr_hash": None,
            "redaction_status": None,
            "output_files": [],
            "environment_names": [],
            "error_code": None,
            "raw_truncated": False,
        },
        "channel_evidence": {
            "axes": deepcopy(row["axes"]),
            "evidence_class": "human",
            "handoff_ref": "human_authority_event_fixture",
            "selection_ref": "human-authority://selection-fixture",
            "route_block_ref": None,
            "process_receipt_ref": None,
            "model_receipt_ref": None,
            "terminal_ref": "human-authority://terminal-fixture",
            "process_started": False,
            "external_request_started": False,
            "physical_process_start_count": 0,
            "external_request_start_count": 0,
            "route": None,
            "region": None,
            "egress": None,
            "behavior_hash": None,
            "input_hash": None,
            "output_hash": None,
            "latency_ms": None,
            "terminal_state": "COMPLETED",
            "execution_result": "SUCCESS",
            "blocked_reason_codes": [],
        },
        "accounting": deepcopy(row["accounting"]),
        "token_usage": deepcopy(row["accounting"]["token_usage"]),
        "cost": {
            "status": row["accounting"]["cost_status"],
            "value": None,
            "currency": None,
        },
    }


def binding(case_id: str, handoff: dict, *, attempt_no: int = 1, retry_of: str | None = None) -> dict:
    row = case(case_id)
    blocked = row["evidence_class"] == "blocked_before_start"
    workspace_events = [item for item in handoff["events"] if item["event_type"] == "workspace"]
    selection = event(handoff, "selection")
    return {
        "attempt_id": handoff["attempt_id"],
        "logical_operation_id": "operation_matrix",
        "attempt_no": attempt_no,
        "retry_of_attempt_id": retry_of,
        "task_id": "LEDGER_OFFLINE",
        "stage": "construction_a",
        "paper_id": None,
        "profile_ref": selection["payload"].get("selected_profile_ref"),
        "axes": deepcopy(row["axes"]),
        "evidence_class": row["evidence_class"],
        "provider": None if blocked or row["evidence_class"] == "non_model_local" else "provider-test",
        "requested_model": None if blocked or row["evidence_class"] == "non_model_local" else "model-test-v1",
        "route": None if blocked else "synthetic-offline-route",
        "region": None if blocked else "test-region",
        "egress": None if blocked else "disabled-fixture",
        "behavior_hash": None if blocked else HASH_A,
        "input_hash": (
            canonical_sha256(workspace_events[0]["payload"]["input_hashes"])
            if workspace_events
            else None
        ),
        "accounting": deepcopy(row["accounting"]),
        "blocked_reason_codes": (
            event(handoff, "route_block")["payload"]["reason_codes"] if blocked else []
        ),
        "source_evidence_refs": [f"handoff://{handoff['handoff_id']}", "fixture://accounting-matrix"],
    }


class ClassificationAndAccountingTests(unittest.TestCase):
    def test_all_accounting_matrix_cases_derive_and_validate(self) -> None:
        rows = matrix()["cases"]
        self.assertEqual(len(rows), 8)
        for row in rows:
            with self.subTest(row["case_id"]):
                self.assertEqual(derive_evidence_class(row["axes"]), row["evidence_class"])
                self.assertEqual(validate_accounting(row["evidence_class"], row["accounting"]), row["accounting"])

    def test_invalid_axis_combination_fails_closed(self) -> None:
        axes = deepcopy(case("SUBSCRIPTION_CLI")["axes"])
        axes["billing_mode"] = "paygo_api"
        with self.assertRaises(ChannelEvidenceError) as caught:
            derive_evidence_class(axes)
        self.assertEqual(caught.exception.code, "AXIS_COMBINATION_INVALID")

    def test_subscription_na_and_unknown_are_not_numeric_zero(self) -> None:
        bad = deepcopy(case("SUBSCRIPTION_CLI")["accounting"])
        bad["token_usage"]["total_tokens"] = 0
        bad["cost_value"] = 0
        with self.assertRaises(ChannelEvidenceError) as caught:
            validate_accounting("subscription_cli", bad)
        self.assertEqual(caught.exception.code, "ACCOUNTING_NA_MUST_NOT_BE_ZERO")

        unknown = deepcopy(case("API_VIA_CLI_UNKNOWN")["accounting"])
        self.assertEqual(validate_accounting("api", unknown)["usage_status"], "UNKNOWN")
        unknown["token_usage"]["total_tokens"] = 0
        with self.assertRaises(ChannelEvidenceError):
            validate_accounting("api", unknown)

    def test_api_via_cli_cannot_use_subscription_accounting(self) -> None:
        with self.assertRaises(ChannelEvidenceError) as caught:
            validate_accounting("api", case("SUBSCRIPTION_CLI")["accounting"])
        self.assertEqual(caught.exception.code, "ACCOUNTING_STATUS_INVALID")

    def test_api_actual_zero_is_distinct_from_unknown_or_na(self) -> None:
        actual = deepcopy(case("DIRECT_API_ACTUAL")["accounting"])
        actual["token_usage"] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }
        actual["cost_value"] = 0
        self.assertEqual(validate_accounting("api", actual)["cost_value"], 0)

    def test_reasoning_tokens_are_not_double_counted(self) -> None:
        bad = deepcopy(case("DIRECT_API_ACTUAL")["accounting"])
        bad["token_usage"]["total_tokens"] = 28
        with self.assertRaises(ChannelEvidenceError) as caught:
            validate_accounting("api", bad)
        self.assertEqual(caught.exception.code, "TOKEN_TOTAL_DOUBLE_COUNT_OR_MISMATCH")

    def test_estimated_api_cost_remains_estimated_not_actual(self) -> None:
        value = validate_accounting(
            "api", case("DIRECT_API_ESTIMATED")["accounting"]
        )
        self.assertEqual(value["cost_status"], "ESTIMATED")
        self.assertEqual(value["cost_value"], 0.025)

    def test_non_finite_cost_and_local_metrics_fail_closed(self) -> None:
        bad_cost = deepcopy(case("DIRECT_API_ACTUAL")["accounting"])
        bad_cost["cost_value"] = float("nan")
        with self.assertRaises(ChannelEvidenceError) as caught:
            validate_accounting("api", bad_cost)
        self.assertEqual(caught.exception.code, "COST_ACTUAL_INVALID")

        bad_metric = deepcopy(case("LOCAL_MODEL")["accounting"])
        bad_metric["local_resource_metrics"]["duration_ms"] = float("inf")
        with self.assertRaises(ChannelEvidenceError) as caught:
            validate_accounting("local_compute", bad_metric)
        self.assertEqual(caught.exception.code, "LOCAL_RESOURCE_METRIC_VALUE_INVALID")


class HandoffConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = ChannelAttemptBuilder(CORE_SCHEMA, clock=lambda: TS)

    def test_non_model_local_handoff_builds_strict_attempt(self) -> None:
        handoff = successful_handoff()
        record = self.builder.build(handoff, binding("NON_MODEL_LOCAL", handoff))
        validate_channel_attempt(record)
        self.assertEqual(record["channel_evidence"]["evidence_class"], "non_model_local")
        self.assertTrue(record["channel_evidence"]["process_started"])
        self.assertFalse(record["channel_evidence"]["external_request_started"])
        self.assertEqual(record["accounting"]["cost_status"], "N/A_NON_MODEL_LOCAL")
        self.assertEqual(record["acceptance_verdict"], "NOT_ASSESSED")
        self.assertEqual(record["verification_result"], "NOT_ASSESSED")

    def test_model_handoff_preserves_api_identity_route_usage_and_latency(self) -> None:
        base = successful_handoff(job_id="job_api")
        handoff = with_model_event(base, case("DIRECT_API_ACTUAL"))
        record = self.builder.build(handoff, binding("DIRECT_API_ACTUAL", handoff))
        self.assertEqual(record["provider_identity"]["returned_model"], "model-test-v1")
        self.assertEqual(record["channel_evidence"]["route"], "synthetic-offline-route")
        self.assertEqual(record["token_usage"]["total_tokens"], 20)
        self.assertEqual(record["cost"]["value"], 0.03)
        self.assertGreaterEqual(record["channel_evidence"]["latency_ms"], 0)

    def test_blocked_handoff_is_not_started_and_requires_reasons(self) -> None:
        handoff = blocked_handoff()
        record = self.builder.build(handoff, binding("BLOCKED_BEFORE_START", handoff))
        self.assertFalse(record["attempt_started"])
        self.assertFalse(record["channel_evidence"]["process_started"])
        self.assertFalse(record["channel_evidence"]["external_request_started"])
        self.assertEqual(record["result_category"], "not_assessed")
        self.assertTrue(record["channel_evidence"]["blocked_reason_codes"])

    def test_handoff_tamper_and_missing_pre_send_binding_fail_before_write(self) -> None:
        handoff = successful_handoff(job_id="job_tamper")
        tampered = deepcopy(handoff)
        tampered["events"][-1]["payload"]["terminal_state"] = "ERROR"
        with self.assertRaises(ChannelEvidenceError) as caught:
            self.builder.build(tampered, binding("NON_MODEL_LOCAL", handoff))
        self.assertEqual(caught.exception.code, "BLOCKED_HANDOFF")

        incomplete = binding("NON_MODEL_LOCAL", handoff)
        incomplete.pop("behavior_hash")
        with self.assertRaises(ChannelEvidenceError) as caught:
            self.builder.build(handoff, incomplete)
        self.assertEqual(caught.exception.code, "PRE_SEND_BINDING_MISSING")

    def test_profile_and_model_mismatch_fail_closed_without_guessing(self) -> None:
        base = successful_handoff(job_id="job_mismatch")
        handoff = with_model_event(base, case("DIRECT_API_ACTUAL"))
        value = binding("DIRECT_API_ACTUAL", handoff)
        value["requested_model"] = "wrong-model"
        with self.assertRaises(ChannelEvidenceError) as caught:
            self.builder.build(handoff, value)
        self.assertEqual(caught.exception.code, "MODEL_IDENTITY_MISMATCH")

    def test_terminal_axes_remain_orthogonal(self) -> None:
        handoff = successful_handoff(job_id="job_axes")
        record = self.builder.build(handoff, binding("NON_MODEL_LOCAL", handoff))
        self.assertEqual(record["result_category"], "success")
        self.assertEqual(record["verification_result"], "NOT_ASSESSED")
        self.assertEqual(record["acceptance_verdict"], "NOT_ASSESSED")

    def test_provider_not_exposed_returned_model_is_explicit_and_not_guessed(self) -> None:
        base = successful_handoff(job_id="job_not_exposed")
        handoff = with_model_event(
            base,
            case("DIRECT_API_ACTUAL"),
            returned_model=None,
            model_identity_grade="PROVIDER_NOT_EXPOSED",
        )
        record = self.builder.build(
            handoff, binding("DIRECT_API_ACTUAL", handoff)
        )
        self.assertIsNone(record["provider_identity"]["returned_model"])
        self.assertEqual(
            record["provider_identity"]["model_identity_grade"],
            "PROVIDER_NOT_EXPOSED",
        )

    def test_model_completion_requires_model_receipt(self) -> None:
        handoff = successful_handoff(job_id="job_missing_model_receipt")
        terminal = deepcopy(event(handoff, "terminal")["payload"])
        accounting = case("DIRECT_API_ACTUAL")["accounting"]
        terminal["token_status"] = accounting["usage_status"]
        terminal["cost_status"] = accounting["cost_status"]
        rebuilt = HandoffContract(CORE_SCHEMA, clock=FIXED_CLOCK).build(
            job_id=handoff["job_id"],
            attempt_id=handoff["attempt_id"],
            objects=tuple(
                (item["event_type"], item["payload"])
                for item in handoff["events"]
                if item["event_type"] != "terminal"
            ),
            terminal=terminal,
        )
        with self.assertRaises(ChannelEvidenceError) as caught:
            self.builder.build(rebuilt, binding("DIRECT_API_ACTUAL", rebuilt))
        self.assertEqual(caught.exception.code, "MODEL_RECEIPT_REQUIRED")

    def test_api_pre_request_process_failure_closes_without_model_receipt(self) -> None:
        handoff = not_started_handoff()
        record = self.builder.build(
            handoff, binding("API_VIA_CLI_UNKNOWN", handoff)
        )
        self.assertFalse(record["attempt_started"])
        self.assertFalse(record["channel_evidence"]["process_started"])
        self.assertFalse(record["channel_evidence"]["external_request_started"])
        self.assertIsNotNone(record["channel_evidence"]["process_receipt_ref"])
        self.assertIsNone(record["channel_evidence"]["model_receipt_ref"])
        self.assertEqual(record["result_category"], "error")
        self.assertEqual(record["accounting"]["usage_status"], "UNKNOWN")
        self.assertEqual(record["provider_identity"]["model_identity_grade"], "UNKNOWN")
        self.assertEqual(record["process_evidence"]["duration_ms"], 0)

    def test_process_receipt_projection_preserves_replay_fields(self) -> None:
        handoff = successful_handoff(job_id="job_process_projection")
        source = event(handoff, "process")["payload"]
        record = self.builder.build(handoff, binding("NON_MODEL_LOCAL", handoff))
        projection = record["process_evidence"]
        for key in (
            "adapter_id",
            "argv_redacted",
            "working_directory",
            "started_at",
            "ended_at",
            "duration_ms",
            "exit_code",
            "timeout",
            "killed",
            "stdout_hash",
            "stderr_hash",
            "redaction_status",
            "output_files",
            "environment_names",
            "error_code",
        ):
            self.assertEqual(projection[key], source[key])

    def test_api_via_cli_subscription_and_local_model_build_end_to_end(self) -> None:
        for case_id in ("API_VIA_CLI_UNKNOWN", "SUBSCRIPTION_CLI", "LOCAL_MODEL"):
            with self.subTest(case_id=case_id):
                base = successful_handoff(job_id=f"job_{case_id.lower()}")
                handoff = with_model_event(base, case(case_id))
                record = self.builder.build(handoff, binding(case_id, handoff))
                self.assertEqual(
                    record["channel_evidence"]["evidence_class"],
                    case(case_id)["evidence_class"],
                )
                self.assertEqual(record["accounting"], case(case_id)["accounting"])

    def test_human_record_is_non_model_not_started_and_not_assessed(self) -> None:
        record = human_record()
        validate_channel_attempt(record)
        self.assertTrue(record["attempt_started"])
        self.assertEqual(record["channel_evidence"]["evidence_class"], "human")
        self.assertEqual(record["acceptance_verdict"], "NOT_ASSESSED")

    def test_retry_binding_must_match_handoff_lineage(self) -> None:
        handoff = successful_handoff(job_id="job_retry_mismatch", attempt_id="attempt_002")
        value = binding(
            "NON_MODEL_LOCAL", handoff, attempt_no=2, retry_of="attempt_001"
        )
        with self.assertRaises(ChannelEvidenceError) as caught:
            self.builder.build(handoff, value)
        self.assertEqual(caught.exception.code, "HANDOFF_RETRY_LINEAGE_MISMATCH")

    def test_secret_shaped_profile_reference_is_rejected(self) -> None:
        handoff = successful_handoff(job_id="job_secret")
        value = binding("NON_MODEL_LOCAL", handoff)
        value["source_evidence_refs"].append("sk-test-secret")
        with self.assertRaises(ChannelEvidenceError) as caught:
            self.builder.build(handoff, value)
        self.assertEqual(caught.exception.code, "SECRET_LIKE_VALUE_FORBIDDEN")


class ChannelStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="channel-evidence-store-")
        self.sandbox = Path(self._temporary.name)
        (self.sandbox / SANDBOX_MARKER_NAME).write_bytes(SANDBOX_MARKER_BYTES)
        self.builder = ChannelAttemptBuilder(CORE_SCHEMA, clock=lambda: TS)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def store(self) -> ChannelLedgerStore:
        return ChannelLedgerStore(self.sandbox / "ledger", sandbox_root=self.sandbox)

    def test_append_is_idempotent_and_conflict_preserves_old_bytes(self) -> None:
        handoff = successful_handoff(job_id="job_store")
        record = self.builder.build(handoff, binding("NON_MODEL_LOCAL", handoff))
        store = self.store()
        self.assertEqual(store.append(record), "appended")
        ledger = self.sandbox / "ledger" / "2026.jsonl"
        before = ledger.read_bytes()
        self.assertEqual(store.append(deepcopy(record)), "noop")
        self.assertEqual(ledger.read_bytes(), before)
        conflict = deepcopy(record)
        conflict["source_evidence_refs"].append("fixture://different-content")
        with self.assertRaises(RecordConflictError):
            store.append(conflict)
        self.assertEqual(ledger.read_bytes(), before)

    def test_retry_is_new_identity_and_predecessor_failure_is_not_overwritten(self) -> None:
        store = self.store()
        first_handoff = successful_handoff(job_id="job_retry", attempt_id="attempt_001")
        terminal = deepcopy(event(first_handoff, "terminal")["payload"])
        terminal.update(
            {
                "terminal_state": "ERROR",
                "execution_result": "FAILED",
                "error_code": "SYNTHETIC_FAILURE",
                "result_ref": None,
            }
        )
        first_handoff = HandoffContract(CORE_SCHEMA, clock=FIXED_CLOCK).build(
            job_id=first_handoff["job_id"],
            attempt_id=first_handoff["attempt_id"],
            objects=tuple(
                (item["event_type"], item["payload"])
                for item in first_handoff["events"]
                if item["event_type"] != "terminal"
            ),
            terminal=terminal,
        )
        first = self.builder.build(first_handoff, binding("NON_MODEL_LOCAL", first_handoff))
        validate_channel_attempt(first)
        store.append(first)
        ledger = self.sandbox / "ledger" / "2026.jsonl"
        prefix = ledger.read_bytes()

        second_handoff = successful_handoff(
            job_id="job_retry",
            attempt_id="attempt_002",
            predecessor_attempt_ref="attempt_001",
        )
        second = self.builder.build(
            second_handoff,
            binding("NON_MODEL_LOCAL", second_handoff, attempt_no=2, retry_of="attempt_001"),
        )
        store.append(second)
        self.assertTrue(ledger.read_bytes().startswith(prefix))
        self.assertEqual([row["attempt_id"] for row in store.attempts()], ["attempt_001", "attempt_002"])

    def test_accounting_amendment_is_append_only_and_does_not_add_attempt(self) -> None:
        base_handoff = successful_handoff(job_id="job_amend")
        handoff = with_model_event(base_handoff, case("API_VIA_CLI_UNKNOWN"))
        record = self.builder.build(handoff, binding("API_VIA_CLI_UNKNOWN", handoff))
        store = self.store()
        store.append(record)
        base_bytes = (self.sandbox / "ledger" / "2026.jsonl").read_bytes()
        actual = deepcopy(case("DIRECT_API_ACTUAL")["accounting"])
        amendment = build_accounting_amendment(
            record,
            actual,
            record_id="amendment:attempt_001:accounting-v1",
            created_at="2026-08-13T11:01:00Z",
            source_evidence_refs=("fixture://late-accounting",),
        )
        store.append_accounting_amendment(amendment)
        self.assertTrue((self.sandbox / "ledger" / "2026.jsonl").read_bytes().startswith(base_bytes))
        self.assertEqual(len(store.attempts()), 1)
        self.assertEqual(store.current_attempt("attempt_001")["accounting"]["cost_value"], 0.03)

    def test_writer_lock_and_mutation_apis_fail_closed(self) -> None:
        handoff = successful_handoff(job_id="job_lock")
        record = self.builder.build(handoff, binding("NON_MODEL_LOCAL", handoff))
        store = self.store()
        store.root.mkdir(parents=True)
        (store.root / ".writer.lock").write_text("pid=test\n", encoding="ascii")
        with self.assertRaises(ConcurrentWriterError):
            store.append(record)
        (store.root / ".writer.lock").unlink()
        for method in (store.overwrite, store.update, store.delete):
            with self.assertRaises(AppendOnlyViolation):
                method({})

    def test_store_is_a_thin_successor_of_the_run_ledger_store(self) -> None:
        from run_ledger.store import LedgerStore

        self.assertTrue(issubclass(ChannelLedgerStore, LedgerStore))
        for method_name in (
            "_single_writer_lock",
            "_year_files",
            "_raise_corruption",
            "overwrite",
            "update",
        ):
            self.assertIs(
                getattr(ChannelLedgerStore, method_name),
                getattr(LedgerStore, method_name),
            )

    def test_two_concurrent_appends_are_serialized_without_interleaving(self) -> None:
        store = self.store()
        first_handoff = successful_handoff(job_id="job_concurrent_1")
        second_handoff = successful_handoff(
            job_id="job_concurrent_2", attempt_id="attempt_002"
        )
        records = [
            self.builder.build(first_handoff, binding("NON_MODEL_LOCAL", first_handoff)),
            self.builder.build(second_handoff, binding("NON_MODEL_LOCAL", second_handoff)),
        ]
        outcomes: list[str] = []
        barrier = threading.Barrier(3)

        def append(record: dict) -> None:
            barrier.wait()
            try:
                outcomes.append(store.append(record))
            except ConcurrentWriterError:
                outcomes.append("locked")

        threads = [threading.Thread(target=append, args=(record,)) for record in records]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertIn(
            sorted(outcomes),
            (["appended", "appended"], ["appended", "locked"]),
        )
        ledger = self.sandbox / "ledger" / "2026.jsonl"
        self.assertTrue(ledger.read_bytes().endswith(b"\n"))
        self.assertEqual(len(store.read_records()), outcomes.count("appended"))

    def test_truncated_tail_fails_closed_and_preserves_original_bytes(self) -> None:
        from run_ledger.errors import TruncatedLedgerError

        store = self.store()
        store.root.mkdir(parents=True)
        ledger = store.root / "2026.jsonl"
        ledger.write_bytes(b'{"partial":true}')
        before = ledger.read_bytes()
        with self.assertRaises(TruncatedLedgerError) as caught:
            store.read_records()
        self.assertEqual(ledger.read_bytes(), before)
        self.assertTrue(caught.exception.evidence_path.exists())

    def test_amendment_cannot_change_sealed_identity_or_outcome(self) -> None:
        base_handoff = successful_handoff(job_id="job_sealed")
        record = self.builder.build(base_handoff, binding("NON_MODEL_LOCAL", base_handoff))
        amendment = {
            "schema_version": "CHANNEL_ACCOUNTING_CHANNEL_ATTEMPT_V1",
            "record_type": "channel_attempt_record",
            "record_kind": "accounting_amendment",
            "record_id": "amendment:attempt_001:invalid",
            "run_id": record["run_id"],
            "created_at": TS,
            "source_evidence_refs": ["fixture://invalid-amendment"],
            "attempt_id": record["attempt_id"],
            "amends_attempt_id": record["attempt_id"],
            "logical_operation_id": record["logical_operation_id"],
            "attempt_no": record["attempt_no"],
            "retry_of_attempt_id": record["retry_of_attempt_id"],
            "task_id": record["task_id"],
            "stage": record["stage"],
            "paper_id": record["paper_id"],
            "evidence_class": "non_model_local",
            "amendment_fields": {
                "accounting": record["accounting"],
                "token_usage": record["token_usage"],
                "cost": record["cost"],
                "result_category": "success",
            },
        }
        with self.assertRaises(ChannelEvidenceError) as caught:
            self.store().append_accounting_amendment(amendment)
        self.assertEqual(caught.exception.code, "CHANNEL_SCHEMA_INVALID")

    def test_real_or_formal_root_is_rejected_before_directory_creation(self) -> None:
        outside = self.sandbox.parent / f"{self.sandbox.name}-outside-ledger"
        self.assertFalse(outside.exists())
        with self.assertRaises(ChannelEvidenceError) as caught:
            ChannelLedgerStore(outside, sandbox_root=self.sandbox)
        self.assertEqual(caught.exception.code, "CHANNEL_STORE_OUTSIDE_SANDBOX")
        self.assertFalse(outside.exists())

    def test_missing_or_tampered_sandbox_marker_fails_before_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="channel-evidence-unmarked-") as temp:
            sandbox = Path(temp)
            ledger = sandbox / "ledger"
            with self.assertRaises(ChannelEvidenceError) as caught:
                ChannelLedgerStore(ledger, sandbox_root=sandbox)
            self.assertEqual(caught.exception.code, "CHANNEL_SANDBOX_MARKER_REQUIRED")
            self.assertFalse(ledger.exists())

            (sandbox / SANDBOX_MARKER_NAME).write_bytes(b"wrong\n")
            with self.assertRaises(ChannelEvidenceError) as caught:
                ChannelLedgerStore(ledger, sandbox_root=sandbox)
            self.assertEqual(caught.exception.code, "CHANNEL_SANDBOX_MARKER_INVALID")
            self.assertFalse(ledger.exists())


class MachineSchemaAndCompatibilityTests(unittest.TestCase):
    def test_machine_schema_is_strict_and_accepts_builder_record(self) -> None:
        handoff = successful_handoff(job_id="job_schema")
        record = ChannelAttemptBuilder(CORE_SCHEMA, clock=lambda: TS).build(
            handoff, binding("NON_MODEL_LOCAL", handoff)
        )
        schema = json.loads(CHANNEL_SCHEMA_PATH.read_text("utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        self.assertEqual(list(validator.iter_errors(record)), [])
        invalid = deepcopy(record)
        invalid["guessed_field"] = True
        self.assertTrue(list(validator.iter_errors(invalid)))

    def test_crash_terminal_is_closed_without_claiming_acceptance(self) -> None:
        handoff = successful_handoff(job_id="job_crash")
        terminal = event(handoff, "terminal")["payload"]
        terminal["terminal_state"] = "ERROR"
        terminal["execution_result"] = "FAILED"
        terminal["error_code"] = "PROCESS_CRASH"
        terminal["result_ref"] = None
        rebuilt = HandoffContract(CORE_SCHEMA, clock=FIXED_CLOCK).build(
            job_id=handoff["job_id"],
            attempt_id=handoff["attempt_id"],
            objects=tuple(
                (item["event_type"], item["payload"])
                for item in handoff["events"]
                if item["event_type"] != "terminal"
            ),
            terminal=terminal,
        )
        record = ChannelAttemptBuilder(CORE_SCHEMA, clock=lambda: TS).build(
            rebuilt, binding("NON_MODEL_LOCAL", rebuilt)
        )
        self.assertEqual(record["result_category"], "error")
        self.assertEqual(record["acceptance_verdict"], "NOT_ASSESSED")


if __name__ == "__main__":
    unittest.main()
