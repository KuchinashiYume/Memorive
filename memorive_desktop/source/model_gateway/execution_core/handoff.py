from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator

from .contracts import (
    canonical_json_bytes,
    canonical_sha256,
    immutable_copy,
    object_ref,
    utc_now,
    validate_initialization_object,
)


HANDOFF_SCHEMA_VERSION = "EXECUTION_CORE_ServiceContracts_EVENT_HANDOFF_V1"
SAFE_ID_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
TERMINAL_STATES = {"BLOCKED", "COMPLETED", "ERROR", "TIMEOUT", "KILLED", "ABORTED"}
VERIFICATION_RESULTS = {"NOT_ASSESSED", "PASS", "FAIL", "ERROR"}
ACCEPTANCE_VERDICTS = {"NOT_ASSESSED", "PASS", "FAIL"}
EXECUTION_RESULTS = {"NOT_RUN", "SUCCESS", "FAILED"}
EVENT_ORDER = {
    "selection": 0,
    "route_block": 1,
    "workspace": 1,
    "process": 2,
    "model": 3,
    "schema": 4,
    "terminal": 5,
}
EVENT_OBJECT_TYPES = {
    "selection": "ChannelSelectionDecision",
    "route_block": "RouteBlockReceipt",
    "workspace": "JobWorkspaceManifest",
    "process": "ProcessExecutionReceipt",
    "model": "ModelExecutionReceipt",
    "schema": "NormalizedResult",
}


HANDOFF_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": HANDOFF_SCHEMA_VERSION,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "handoff_id",
        "producer",
        "single_writer",
        "consumer",
        "job_id",
        "attempt_id",
        "created_at",
        "event_count",
        "events",
        "events_sha256",
    ],
    "properties": {
        "schema_version": {"const": HANDOFF_SCHEMA_VERSION},
        "handoff_id": {"type": "string", "minLength": 1},
        "producer": {"const": "Execution_EXECUTION_CORE"},
        "single_writer": {"const": "Execution_EXECUTION_CORE"},
        "consumer": {"const": "CHANNEL_ACCOUNTING_RUNTIME_LOG"},
        "job_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
        "attempt_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
        "created_at": {"type": "string", "minLength": 1},
        "event_count": {"type": "integer", "minimum": 1},
        "events_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "events": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "schema_version",
                    "sequence",
                    "event_id",
                    "event_type",
                    "attempt_id",
                    "object_type",
                    "object_ref",
                    "payload_sha256",
                    "payload",
                ],
                "properties": {
                    "schema_version": {"const": HANDOFF_SCHEMA_VERSION},
                    "sequence": {"type": "integer", "minimum": 0},
                    "event_id": {"type": "string", "minLength": 1},
                    "event_type": {"enum": list(EVENT_ORDER)},
                    "attempt_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                    "object_type": {"type": "string", "minLength": 1},
                    "object_ref": {"type": "string", "minLength": 1},
                    "payload_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
                    "payload": {"type": "object"},
                },
            },
        },
    },
}


class HandoffViolation(ValueError):
    def __init__(self, code: str, details: Sequence[str] = ()):
        self.code = code
        self.details = tuple(details)
        suffix = f": {'; '.join(self.details)}" if self.details else ""
        super().__init__(f"{code}{suffix}")


class HandoffContract:
    """Versioned immutable event projection for ServiceContracts; never writes RUNTIME_LOG state."""

    def __init__(
        self,
        core_schema: Mapping[str, Any],
        *,
        clock: Callable[[], str] = utc_now,
    ):
        self._core_schema = immutable_copy(core_schema)
        self._clock = clock
        Draft202012Validator.check_schema(HANDOFF_SCHEMA)

    def build(
        self,
        *,
        job_id: str,
        attempt_id: str,
        objects: Sequence[tuple[str, Mapping[str, Any]]],
        terminal: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not SAFE_ID_COMPONENT.fullmatch(job_id) or not SAFE_ID_COMPONENT.fullmatch(
            attempt_id
        ):
            raise HandoffViolation("HANDOFF_IDENTITY_INVALID")
        events: list[dict[str, Any]] = []
        for event_type, value in objects:
            object_type = EVENT_OBJECT_TYPES.get(event_type)
            if object_type is None:
                raise HandoffViolation("HANDOFF_EVENT_TYPE_INVALID", [event_type])
            accepted = validate_initialization_object(value, object_type, self._core_schema)
            events.append(
                self._event(
                    len(events),
                    event_type,
                    attempt_id,
                    object_type,
                    object_ref(accepted),
                    accepted,
                )
            )
        terminal_payload = immutable_copy(terminal)
        self._validate_terminal(terminal_payload, attempt_id)
        events.append(
            self._event(
                len(events),
                "terminal",
                attempt_id,
                "ExecutionTerminalState",
                f"executionterminalstate_{job_id}_{attempt_id}@candidate001",
                terminal_payload,
            )
        )
        events_hash = canonical_sha256(events)
        handoff = {
            "schema_version": HANDOFF_SCHEMA_VERSION,
            "handoff_id": f"handoff_{job_id}_{attempt_id}_{events_hash[:16].lower()}",
            "producer": "Execution_EXECUTION_CORE",
            "single_writer": "Execution_EXECUTION_CORE",
            "consumer": "CHANNEL_ACCOUNTING_RUNTIME_LOG",
            "job_id": job_id,
            "attempt_id": attempt_id,
            "created_at": self._clock(),
            "event_count": len(events),
            "events": events,
            "events_sha256": events_hash,
        }
        return self.validate(handoff)

    def validate(self, value: Mapping[str, Any]) -> dict[str, Any]:
        accepted = immutable_copy(value)
        errors = sorted(
            Draft202012Validator(HANDOFF_SCHEMA).iter_errors(accepted),
            key=lambda item: (list(item.absolute_path), item.message),
        )
        if errors:
            raise HandoffViolation(
                "HANDOFF_SCHEMA_INVALID",
                [
                    f"{'/'.join(str(part) for part in item.absolute_path) or '$'}: {item.message}"
                    for item in errors[:20]
                ],
            )
        events = accepted["events"]
        if accepted["event_count"] != len(events):
            raise HandoffViolation("HANDOFF_EVENT_COUNT_MISMATCH")
        if accepted["events_sha256"] != canonical_sha256(events):
            raise HandoffViolation("HANDOFF_EVENTS_HASH_MISMATCH")
        expected_handoff_id = (
            f"handoff_{accepted['job_id']}_{accepted['attempt_id']}_"
            f"{accepted['events_sha256'][:16].lower()}"
        )
        if accepted["handoff_id"] != expected_handoff_id:
            raise HandoffViolation("HANDOFF_ID_MISMATCH")
        if [event["sequence"] for event in events] != list(range(len(events))):
            raise HandoffViolation("HANDOFF_SEQUENCE_INVALID")
        if events[0]["event_type"] != "selection" or events[-1]["event_type"] != "terminal":
            raise HandoffViolation("HANDOFF_ENDPOINTS_INVALID")
        if sum(event["event_type"] == "terminal" for event in events) != 1:
            raise HandoffViolation("HANDOFF_TERMINAL_COUNT_INVALID")
        ranks = [EVENT_ORDER[event["event_type"]] for event in events]
        if ranks != sorted(ranks):
            raise HandoffViolation("HANDOFF_EVENT_ORDER_INVALID")
        types = [event["event_type"] for event in events]
        blocked = "route_block" in types
        if blocked and any(name in types for name in ("workspace", "process", "model", "schema")):
            raise HandoffViolation("HANDOFF_BLOCKED_ROUTE_STARTED_PROCESS")
        allowed_success_sets = (
            ["selection", "workspace", "process", "schema", "terminal"],
            ["selection", "workspace", "process", "model", "schema", "terminal"],
        )
        if not blocked and types not in allowed_success_sets:
            raise HandoffViolation("HANDOFF_EXECUTION_EVENT_SET_INVALID")
        for event in events:
            if event["attempt_id"] != accepted["attempt_id"]:
                raise HandoffViolation("HANDOFF_ATTEMPT_MISMATCH")
            if event["payload_sha256"] != canonical_sha256(event["payload"]):
                raise HandoffViolation("HANDOFF_PAYLOAD_HASH_MISMATCH", [event["event_id"]])
            expected_event_id = self._event_id(
                event["attempt_id"],
                event["sequence"],
                event["event_type"],
                event["payload_sha256"],
            )
            if event["event_id"] != expected_event_id:
                raise HandoffViolation("HANDOFF_EVENT_ID_MISMATCH", [event["event_id"]])
            expected_type = EVENT_OBJECT_TYPES.get(event["event_type"])
            if expected_type is not None:
                accepted_payload = validate_initialization_object(
                    event["payload"], expected_type, self._core_schema
                )
                if event["object_type"] != expected_type:
                    raise HandoffViolation(
                        "HANDOFF_OBJECT_TYPE_MISMATCH", [event["event_id"]]
                    )
                if event["object_ref"] != object_ref(accepted_payload):
                    raise HandoffViolation(
                        "HANDOFF_OBJECT_REF_MISMATCH", [event["event_id"]]
                    )
            else:
                expected_terminal_ref = (
                    f"executionterminalstate_{accepted['job_id']}_"
                    f"{accepted['attempt_id']}@candidate001"
                )
                if event["object_type"] != "ExecutionTerminalState":
                    raise HandoffViolation("HANDOFF_OBJECT_TYPE_MISMATCH", [event["event_id"]])
                if event["object_ref"] != expected_terminal_ref:
                    raise HandoffViolation("HANDOFF_OBJECT_REF_MISMATCH", [event["event_id"]])
        self._validate_terminal(events[-1]["payload"], accepted["attempt_id"])
        if blocked != (events[-1]["payload"]["terminal_state"] == "BLOCKED"):
            raise HandoffViolation("HANDOFF_ROUTE_TERMINAL_MISMATCH")
        return accepted

    @staticmethod
    def _validate_terminal(payload: Mapping[str, Any], attempt_id: str) -> None:
        required = {
            "terminal_state",
            "verification_result",
            "acceptance_verdict",
            "execution_result",
            "error_code",
            "result_ref",
            "physical_process_start_count",
            "external_request_start_count",
            "token_status",
            "cost_status",
            "attempt_lineage",
        }
        missing = sorted(required - set(payload))
        extra = sorted(set(payload) - required)
        if missing or extra:
            raise HandoffViolation(
                "HANDOFF_TERMINAL_FIELDS_INVALID",
                [f"missing={missing}", f"extra={extra}"],
            )
        if payload["terminal_state"] not in TERMINAL_STATES:
            raise HandoffViolation("HANDOFF_TERMINAL_STATE_INVALID")
        if payload["verification_result"] not in VERIFICATION_RESULTS:
            raise HandoffViolation("HANDOFF_VERIFICATION_RESULT_INVALID")
        if payload["acceptance_verdict"] not in ACCEPTANCE_VERDICTS:
            raise HandoffViolation("HANDOFF_ACCEPTANCE_VERDICT_INVALID")
        if payload["execution_result"] not in EXECUTION_RESULTS:
            raise HandoffViolation("HANDOFF_EXECUTION_RESULT_INVALID")
        if payload["verification_result"] != "NOT_ASSESSED" or payload[
            "acceptance_verdict"
        ] != "NOT_ASSESSED":
            raise HandoffViolation("HANDOFF_WRITER_AUTHORITY_VIOLATION")
        for field in ("token_status", "cost_status"):
            if not isinstance(payload[field], str) or not payload[field]:
                raise HandoffViolation("HANDOFF_TERMINAL_FIELD_TYPE_INVALID", [field])
        if payload["error_code"] is not None and (
            not isinstance(payload["error_code"], str) or not payload["error_code"]
        ):
            raise HandoffViolation("HANDOFF_TERMINAL_FIELD_TYPE_INVALID", ["error_code"])
        if payload["result_ref"] is not None and (
            not isinstance(payload["result_ref"], str) or not payload["result_ref"]
        ):
            raise HandoffViolation("HANDOFF_TERMINAL_FIELD_TYPE_INVALID", ["result_ref"])
        for field in ("physical_process_start_count", "external_request_start_count"):
            if not isinstance(payload[field], int) or isinstance(payload[field], bool) or payload[field] < 0:
                raise HandoffViolation("HANDOFF_TERMINAL_COUNT_INVALID", [field])
        lineage = payload["attempt_lineage"]
        if not isinstance(lineage, Mapping) or set(lineage) != {
            "attempt_id",
            "predecessor_attempt_ref",
        }:
            raise HandoffViolation("HANDOFF_ATTEMPT_LINEAGE_INVALID")
        if lineage["attempt_id"] != attempt_id:
            raise HandoffViolation("HANDOFF_ATTEMPT_LINEAGE_MISMATCH")
        predecessor = lineage["predecessor_attempt_ref"]
        if predecessor is not None and (
            not isinstance(predecessor, str) or not predecessor or predecessor == attempt_id
        ):
            raise HandoffViolation("HANDOFF_ATTEMPT_LINEAGE_INVALID")
        if payload["terminal_state"] == "BLOCKED":
            if (
                payload["execution_result"] != "NOT_RUN"
                or payload["error_code"] is None
                or payload["result_ref"] is not None
                or payload["physical_process_start_count"] != 0
                or payload["external_request_start_count"] != 0
            ):
                raise HandoffViolation("HANDOFF_BLOCKED_TERMINAL_INVALID")
        elif payload["terminal_state"] == "COMPLETED":
            if (
                payload["execution_result"] != "SUCCESS"
                or payload["error_code"] is not None
                or payload["result_ref"] is None
                or payload["physical_process_start_count"] != 1
            ):
                raise HandoffViolation("HANDOFF_SUCCESS_TERMINAL_INVALID")
        elif (
            payload["execution_result"] != "FAILED"
            or payload["error_code"] is None
            or payload["result_ref"] is not None
        ):
            raise HandoffViolation("HANDOFF_FAILURE_TERMINAL_INVALID")

    @staticmethod
    def _event(
        sequence: int,
        event_type: str,
        attempt_id: str,
        object_type: str,
        reference: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload_copy = immutable_copy(payload)
        payload_hash = canonical_sha256(payload_copy)
        event_id = HandoffContract._event_id(
            attempt_id, sequence, event_type, payload_hash
        )
        return {
            "schema_version": HANDOFF_SCHEMA_VERSION,
            "sequence": sequence,
            "event_id": event_id,
            "event_type": event_type,
            "attempt_id": attempt_id,
            "object_type": object_type,
            "object_ref": reference,
            "payload_sha256": payload_hash,
            "payload": payload_copy,
        }

    @staticmethod
    def _event_id(
        attempt_id: str, sequence: int, event_type: str, payload_hash: str
    ) -> str:
        event_seed = f"{attempt_id}:{sequence}:{event_type}:{payload_hash}".encode(
            "utf-8"
        )
        return f"event_{hashlib.sha256(event_seed).hexdigest()[:24]}"
