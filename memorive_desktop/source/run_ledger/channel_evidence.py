"""CHANNEL-ACCOUNTING channel-aware attempt receipts and accounting validation.

The module consumes the accepted DataContracts handoff without redefining its events.
It adds the GOV-Execution-01 four-axis classification and RUNTIME_LOG attempt projection;
it never sends a request or writes the real operations ledger.
"""

from __future__ import annotations

import copy
from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

from model_gateway.execution_core import HandoffContract, HandoffViolation
from model_gateway.execution_core.contracts import ContractViolation, utc_now

from .errors import ChannelEvidenceError


SCHEMA_VERSION = "CHANNEL_ACCOUNTING_CHANNEL_ATTEMPT_V1"
RECORD_TYPE = "channel_attempt_record"
TERMINAL_KIND = "terminal"
AMENDMENT_KIND = "accounting_amendment"
REQUIRED_HANDOFF_SCHEMA_VERSION = "EXECUTION_CORE_ServiceContracts_EVENT_HANDOFF_V1"
EVIDENCE_CLASSES = (
    "api",
    "subscription_cli",
    "local_compute",
    "non_model_local",
    "human",
    "blocked_before_start",
)
RESULT_CATEGORIES = (
    "success",
    "failure",
    "incomplete",
    "error",
    "cancelled",
    "not_assessed",
)
VERIFICATION_RESULTS = ("NOT_ASSESSED", "PASS", "FAIL", "ERROR")
ACCEPTANCE_VERDICTS = ("NOT_ASSESSED", "PASS", "FAIL")

_AXIS_KEYS = {
    "process_transport",
    "inference_location",
    "access_mode",
    "billing_mode",
}
_DERIVATION = {
    ("api_sdk", "external_provider", "api_key", "paygo_api"): "api",
    ("cli_process", "external_provider", "api_key", "paygo_api"): "api",
    (
        "cli_process",
        "external_provider",
        "subscription_session",
        "subscription_entitlement",
    ): "subscription_cli",
    ("local_process", "local_machine", "local_runtime", "local_compute"): "local_compute",
    ("local_process", "none", "local_runtime", "none"): "non_model_local",
    ("human", "none", "none", "none"): "human",
    ("none", "unknown", "unknown", "unknown"): "blocked_before_start",
}
_ACCOUNTING_KEYS = {
    "usage_status",
    "token_usage",
    "cost_status",
    "cost_value",
    "currency",
    "local_resource_status",
    "local_resource_metrics",
}
_TOKEN_KEYS = {
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
}
_BINDING_KEYS = {
    "attempt_id",
    "logical_operation_id",
    "attempt_no",
    "retry_of_attempt_id",
    "task_id",
    "stage",
    "paper_id",
    "profile_ref",
    "axes",
    "evidence_class",
    "provider",
    "requested_model",
    "route",
    "region",
    "egress",
    "behavior_hash",
    "input_hash",
    "accounting",
    "blocked_reason_codes",
    "source_evidence_refs",
}
_PROCESS_PROJECTION_KEYS = {
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
    "raw_truncated",
}

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "runtime_log_channel_attempt_v1.schema.json"
_SECRET_PREFIXES = (
    "sk-",
    "ghp_",
    "gho_",
    "github_pat_",
    "xox",
    "pat-",
)


def canonical_json(record: Mapping[str, Any]) -> str:
    return json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def immutable(value: Any) -> Any:
    return copy.deepcopy(value)


def derive_evidence_class(axes: Mapping[str, Any]) -> str:
    """Derive one evidence class from the exact four axes or fail closed."""
    if not isinstance(axes, Mapping) or set(axes) != _AXIS_KEYS:
        raise ChannelEvidenceError("AXIS_FIELDS_INVALID")
    key = (
        axes["process_transport"],
        axes["inference_location"],
        axes["access_mode"],
        axes["billing_mode"],
    )
    evidence_class = _DERIVATION.get(key)
    if evidence_class is None:
        raise ChannelEvidenceError("AXIS_COMBINATION_INVALID", [repr(key)])
    return evidence_class


def _validate_token_usage(value: Any) -> dict[str, int | None]:
    if not isinstance(value, Mapping) or set(value) != _TOKEN_KEYS:
        raise ChannelEvidenceError("TOKEN_USAGE_FIELDS_INVALID")
    accepted: dict[str, int | None] = {}
    for key in sorted(_TOKEN_KEYS):
        item = value[key]
        if item is not None and (
            isinstance(item, bool) or not isinstance(item, int) or item < 0
        ):
            raise ChannelEvidenceError("TOKEN_USAGE_VALUE_INVALID", [key])
        accepted[key] = item
    return accepted


def _require_all_null(tokens: Mapping[str, int | None], code: str) -> None:
    if any(value is not None for value in tokens.values()):
        raise ChannelEvidenceError(code)


def _is_nonnegative_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return False
    return not isinstance(value, float) or math.isfinite(value)


def _validate_metrics(value: Any, *, required: bool) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        raise ChannelEvidenceError("LOCAL_RESOURCE_METRICS_INVALID")
    if required and not value:
        raise ChannelEvidenceError("LOCAL_RESOURCE_METRICS_REQUIRED")
    result: dict[str, int | float] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ChannelEvidenceError("LOCAL_RESOURCE_METRIC_NAME_INVALID")
        if not _is_nonnegative_finite_number(item):
            raise ChannelEvidenceError("LOCAL_RESOURCE_METRIC_VALUE_INVALID", [key])
        result[key] = item
    return result


def validate_accounting(evidence_class: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate unknown, N/A and numeric zero as distinct accounting states."""
    if evidence_class not in EVIDENCE_CLASSES:
        raise ChannelEvidenceError("EVIDENCE_CLASS_INVALID")
    if not isinstance(value, Mapping) or set(value) != _ACCOUNTING_KEYS:
        raise ChannelEvidenceError("ACCOUNTING_FIELDS_INVALID")
    accepted = immutable(dict(value))
    tokens = _validate_token_usage(accepted["token_usage"])
    accepted["token_usage"] = tokens
    usage_status = accepted["usage_status"]
    cost_status = accepted["cost_status"]
    cost_value = accepted["cost_value"]
    currency = accepted["currency"]
    local_status = accepted["local_resource_status"]
    metrics = accepted["local_resource_metrics"]

    if evidence_class == "api":
        if usage_status not in {"ACTUAL", "UNKNOWN"} or cost_status not in {
            "ACTUAL",
            "ESTIMATED",
            "UNKNOWN",
        }:
            raise ChannelEvidenceError("ACCOUNTING_STATUS_INVALID")
        if usage_status == "ACTUAL":
            if any(item is None for item in tokens.values()):
                raise ChannelEvidenceError("TOKEN_USAGE_ACTUAL_INCOMPLETE")
            if tokens["total_tokens"] != tokens["input_tokens"] + tokens["output_tokens"]:
                raise ChannelEvidenceError("TOKEN_TOTAL_DOUBLE_COUNT_OR_MISMATCH")
            if tokens["reasoning_tokens"] > tokens["output_tokens"]:
                raise ChannelEvidenceError("REASONING_TOKENS_EXCEED_OUTPUT")
        else:
            _require_all_null(tokens, "ACCOUNTING_UNKNOWN_MUST_NOT_BE_ZERO")
        if cost_status in {"ACTUAL", "ESTIMATED"}:
            if (
                not _is_nonnegative_finite_number(cost_value)
                or not isinstance(currency, str)
                or not currency.strip()
            ):
                raise ChannelEvidenceError("COST_ACTUAL_INVALID")
        elif cost_value is not None or currency is not None:
            raise ChannelEvidenceError("ACCOUNTING_UNKNOWN_MUST_NOT_BE_ZERO")
        if local_status != "N/A_EXTERNAL" or metrics:
            raise ChannelEvidenceError("API_LOCAL_RESOURCE_STATUS_INVALID")
        accepted["local_resource_metrics"] = _validate_metrics(metrics, required=False)
        return accepted

    expected_status = {
        "subscription_cli": "N/A_SUBSCRIPTION_CLI",
        "local_compute": "N/A_LOCAL_COMPUTE",
        "non_model_local": "N/A_NON_MODEL_LOCAL",
        "human": "N/A_HUMAN",
        "blocked_before_start": "NOT_INCURRED_BEFORE_START",
    }[evidence_class]
    if usage_status != expected_status or cost_status != expected_status:
        raise ChannelEvidenceError("ACCOUNTING_STATUS_INVALID")
    if cost_value is not None or currency is not None or any(
        item is not None for item in tokens.values()
    ):
        raise ChannelEvidenceError("ACCOUNTING_NA_MUST_NOT_BE_ZERO")

    if evidence_class == "subscription_cli":
        if local_status != "N/A_EXTERNAL" or metrics:
            raise ChannelEvidenceError("SUBSCRIPTION_LOCAL_RESOURCE_STATUS_INVALID")
        accepted["local_resource_metrics"] = _validate_metrics(metrics, required=False)
    elif evidence_class in {"local_compute", "non_model_local"}:
        if local_status not in {"ACTUAL", "UNKNOWN"}:
            raise ChannelEvidenceError("LOCAL_RESOURCE_STATUS_INVALID")
        accepted["local_resource_metrics"] = _validate_metrics(
            metrics, required=local_status == "ACTUAL"
        )
        if local_status == "UNKNOWN" and metrics:
            raise ChannelEvidenceError("LOCAL_RESOURCE_UNKNOWN_HAS_VALUES")
    else:
        if local_status != expected_status or metrics:
            raise ChannelEvidenceError("LOCAL_RESOURCE_STATUS_INVALID")
        accepted["local_resource_metrics"] = _validate_metrics(metrics, required=False)
    return accepted


def _schema_validator() -> Draft202012Validator:
    try:
        schema = json.loads(_SCHEMA_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChannelEvidenceError("CHANNEL_MACHINE_SCHEMA_UNAVAILABLE", [str(exc)]) from exc
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_machine(record: Mapping[str, Any]) -> None:
    errors = sorted(
        _schema_validator().iter_errors(record),
        key=lambda item: (list(item.absolute_path), item.message),
    )
    if errors:
        details = [
            f"{'/'.join(str(part) for part in item.absolute_path) or '$'}: {item.message}"
            for item in errors[:20]
        ]
        raise ChannelEvidenceError("CHANNEL_SCHEMA_INVALID", details)


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChannelEvidenceError("CHANNEL_FIELD_INVALID", [name])
    return value


def _reject_secret_like(value: Any, name: str) -> None:
    """Reject credential-shaped values before they can enter an RUNTIME_LOG record."""
    if not isinstance(value, str):
        return
    lowered = value.strip().lower()
    if lowered.startswith(_SECRET_PREFIXES):
        raise ChannelEvidenceError("SECRET_LIKE_VALUE_FORBIDDEN", [name])


def _require_timestamp(value: Any, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_require_text(value, name).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ChannelEvidenceError("CHANNEL_TIMESTAMP_INVALID", [name]) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ChannelEvidenceError("CHANNEL_TIMESTAMP_INVALID", [name])
    return parsed


def validate_channel_attempt(record: Mapping[str, Any]) -> None:
    """Validate one immutable terminal channel attempt."""
    if not isinstance(record, Mapping):
        raise ChannelEvidenceError("CHANNEL_RECORD_NOT_OBJECT")
    _validate_machine(record)
    if record.get("record_kind") != TERMINAL_KIND:
        raise ChannelEvidenceError("CHANNEL_TERMINAL_KIND_REQUIRED")
    if record["record_id"] != record["attempt_id"]:
        raise ChannelEvidenceError("CHANNEL_ATTEMPT_IDENTITY_MISMATCH")
    _require_timestamp(record["created_at"], "created_at")
    started_at = None
    if record["started_at"] is not None:
        started_at = _require_timestamp(record["started_at"], "started_at")
    finished_at = _require_timestamp(record["finished_at"], "finished_at")
    if started_at is not None and started_at > finished_at:
        raise ChannelEvidenceError("ATTEMPT_TIME_ORDER_INVALID")
    if record["result_category"] not in RESULT_CATEGORIES:
        raise ChannelEvidenceError("RESULT_CATEGORY_INVALID")
    if record["verification_result"] not in VERIFICATION_RESULTS:
        raise ChannelEvidenceError("VERIFICATION_RESULT_INVALID")
    if record["acceptance_verdict"] not in ACCEPTANCE_VERDICTS:
        raise ChannelEvidenceError("ACCEPTANCE_VERDICT_INVALID")
    if record["verification_result"] != "NOT_ASSESSED" or record[
        "acceptance_verdict"
    ] != "NOT_ASSESSED":
        raise ChannelEvidenceError("RUNTIME_LOG_WRITER_CANNOT_JUDGE_VERIFICATION_OR_ACCEPTANCE")

    evidence = record["channel_evidence"]
    evidence_class = derive_evidence_class(evidence["axes"])
    if evidence_class != evidence["evidence_class"]:
        raise ChannelEvidenceError("EVIDENCE_CLASS_DERIVATION_MISMATCH")
    accounting = validate_accounting(evidence_class, record["accounting"])
    if accounting != record["accounting"]:
        raise ChannelEvidenceError("ACCOUNTING_CANONICALIZATION_MISMATCH")
    if record["token_usage"] != accounting["token_usage"]:
        raise ChannelEvidenceError("TOKEN_USAGE_PROJECTION_MISMATCH")
    expected_cost = {
        "status": accounting["cost_status"],
        "value": accounting["cost_value"],
        "currency": accounting["currency"],
    }
    if record["cost"] != expected_cost:
        raise ChannelEvidenceError("COST_PROJECTION_MISMATCH")

    process_started = evidence["process_started"]
    external_started = evidence["external_request_started"]
    process_projection = record["process_evidence"]
    if set(process_projection) != _PROCESS_PROJECTION_KEYS:
        raise ChannelEvidenceError("PROCESS_EVIDENCE_FIELDS_INVALID")
    process_receipt_present = evidence["process_receipt_ref"] is not None
    if process_receipt_present != (process_projection["adapter_id"] is not None):
        raise ChannelEvidenceError("PROCESS_EVIDENCE_PRESENCE_MISMATCH")
    if process_receipt_present:
        for key in (
            "adapter_id",
            "argv_redacted",
            "working_directory",
            "ended_at",
            "stdout_hash",
            "stderr_hash",
            "redaction_status",
        ):
            _require_text(process_projection[key], f"process_evidence.{key}")
        process_started_at = None
        if process_projection["started_at"] is not None:
            process_started_at = _require_timestamp(
                process_projection["started_at"], "process_evidence.started_at"
            )
        process_ended_at = _require_timestamp(
            process_projection["ended_at"], "process_evidence.ended_at"
        )
        if process_started_at is not None and process_started_at > process_ended_at:
            raise ChannelEvidenceError("PROCESS_TIME_ORDER_INVALID")
        if not _is_nonnegative_finite_number(process_projection["duration_ms"]):
            raise ChannelEvidenceError("PROCESS_DURATION_INVALID")
    elif any(
        value not in (None, [], False)
        for value in process_projection.values()
    ):
        raise ChannelEvidenceError("NON_STARTED_PROCESS_EVIDENCE_MUST_BE_EMPTY")

    retry_of = record["retry_of_attempt_id"]
    if retry_of == record["attempt_id"]:
        raise ChannelEvidenceError("RETRY_SELF_REFERENCE")
    if record["attempt_no"] == 1 and retry_of is not None:
        raise ChannelEvidenceError("FIRST_ATTEMPT_CANNOT_RETRY")
    if record["attempt_no"] > 1 and retry_of is None:
        raise ChannelEvidenceError("RETRY_LINEAGE_REQUIRED")

    if evidence["physical_process_start_count"] != (1 if process_started else 0):
        raise ChannelEvidenceError("PROCESS_START_COUNT_MISMATCH")
    if evidence["external_request_start_count"] != (1 if external_started else 0):
        raise ChannelEvidenceError("EXTERNAL_START_COUNT_MISMATCH")
    expected_attempt_started = bool(
        process_started or external_started or evidence_class == "human"
    )
    if record["attempt_started"] != expected_attempt_started:
        raise ChannelEvidenceError("ATTEMPT_START_STATE_MISMATCH")

    if evidence_class == "blocked_before_start":
        if (
            record["attempt_started"]
            or process_started
            or external_started
            or not evidence["blocked_reason_codes"]
            or record["result_category"] != "not_assessed"
            or record["error_code"] is None
        ):
            raise ChannelEvidenceError("BLOCKED_ATTEMPT_SEMANTICS_INVALID")
    elif evidence["blocked_reason_codes"]:
        raise ChannelEvidenceError("STARTED_ATTEMPT_HAS_BLOCK_REASONS")

    expected_category = {
        "BLOCKED": "not_assessed",
        "COMPLETED": "success",
        "ERROR": "error",
        "TIMEOUT": "incomplete",
        "KILLED": "cancelled",
        "ABORTED": "cancelled",
    }[evidence["terminal_state"]]
    if record["result_category"] != expected_category:
        raise ChannelEvidenceError("RESULT_TERMINAL_STATE_MISMATCH")
    if evidence["terminal_state"] == "BLOCKED" and evidence_class != "blocked_before_start":
        raise ChannelEvidenceError("BLOCKED_TERMINAL_CLASS_MISMATCH")
    if evidence["terminal_state"] == "COMPLETED" and record["error_code"] is not None:
        raise ChannelEvidenceError("COMPLETED_ATTEMPT_HAS_ERROR")
    if evidence["terminal_state"] not in {"BLOCKED", "COMPLETED"} and record[
        "error_code"
    ] is None:
        raise ChannelEvidenceError("FAILED_ATTEMPT_ERROR_REQUIRED")

    provider = record["provider_identity"]
    if evidence_class in {"non_model_local", "human", "blocked_before_start"}:
        if any(provider.values()):
            raise ChannelEvidenceError("NON_MODEL_PROVIDER_IDENTITY_FORBIDDEN")
    elif evidence_class in {"api", "subscription_cli", "local_compute"}:
        for key in ("provider", "requested_model", "model_identity_grade"):
            _require_text(provider[key], f"provider_identity.{key}")
        if provider["returned_model"] is None:
            if provider["model_identity_grade"] not in {
                "PROVIDER_NOT_EXPOSED",
                "UNKNOWN",
            }:
                raise ChannelEvidenceError("RETURNED_MODEL_STATUS_INVALID")
        else:
            _require_text(provider["returned_model"], "provider_identity.returned_model")
        for key in ("route", "region", "egress", "behavior_hash", "input_hash", "output_hash"):
            _require_text(evidence[key], f"channel_evidence.{key}")
        if not _is_nonnegative_finite_number(evidence["latency_ms"]):
            raise ChannelEvidenceError("MODEL_LATENCY_INVALID")

    if evidence["external_request_started"] and evidence_class not in {
        "api",
        "subscription_cli",
    }:
        raise ChannelEvidenceError("EXTERNAL_START_CLASS_INVALID")
    if (
        evidence["terminal_state"] == "COMPLETED"
        and evidence_class in {"api", "subscription_cli"}
        and not evidence["external_request_started"]
    ):
        raise ChannelEvidenceError("EXTERNAL_REQUEST_RECEIPT_REQUIRED")

    refs = list(record["source_evidence_refs"])
    refs.extend(
        value
        for value in (
            record["profile_ref"],
            *provider.values(),
            evidence["route"],
            evidence["region"],
            evidence["egress"],
        )
        if value is not None
    )
    for index, value in enumerate(refs):
        _reject_secret_like(value, f"record_ref_or_identity[{index}]")

    if evidence_class in {"local_compute", "non_model_local"}:
        if evidence["process_receipt_ref"] is None:
            raise ChannelEvidenceError("LOCAL_PROCESS_RECEIPT_REQUIRED")
        for key in (
            "route",
            "region",
            "egress",
            "behavior_hash",
            "input_hash",
            "output_hash",
        ):
            _require_text(evidence[key], f"channel_evidence.{key}")
        if not _is_nonnegative_finite_number(evidence["latency_ms"]):
            raise ChannelEvidenceError("LOCAL_PROCESS_LATENCY_INVALID")


def _validate_amendment(record: Mapping[str, Any]) -> None:
    _validate_machine(record)
    if record.get("record_kind") != AMENDMENT_KIND:
        raise ChannelEvidenceError("ACCOUNTING_AMENDMENT_KIND_REQUIRED")
    if record["attempt_id"] != record["amends_attempt_id"]:
        raise ChannelEvidenceError("ACCOUNTING_AMENDMENT_TARGET_MISMATCH")
    if record["record_id"] == record["attempt_id"]:
        raise ChannelEvidenceError("ACCOUNTING_AMENDMENT_ID_COLLISION")
    accounting = validate_accounting(
        record["evidence_class"], record["amendment_fields"]["accounting"]
    )
    if record["amendment_fields"]["token_usage"] != accounting["token_usage"]:
        raise ChannelEvidenceError("TOKEN_USAGE_PROJECTION_MISMATCH")
    if record["amendment_fields"]["cost"] != {
        "status": accounting["cost_status"],
        "value": accounting["cost_value"],
        "currency": accounting["currency"],
    }:
        raise ChannelEvidenceError("COST_PROJECTION_MISMATCH")
    _require_timestamp(record["created_at"], "created_at")


def validate_channel_record(record: Mapping[str, Any]) -> None:
    if record.get("record_kind") == TERMINAL_KIND:
        validate_channel_attempt(record)
    elif record.get("record_kind") == AMENDMENT_KIND:
        _validate_amendment(record)
    else:
        raise ChannelEvidenceError("CHANNEL_RECORD_KIND_INVALID")


class ChannelAttemptBuilder:
    """Read-only DataContracts handoff consumer that builds one RUNTIME_LOG terminal attempt."""

    def __init__(
        self,
        core_schema: Mapping[str, Any],
        *,
        clock: Callable[[], str] = utc_now,
    ):
        self._core_schema = immutable(core_schema)
        self._clock = clock

    def build(self, handoff: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
        accepted_binding = self._validate_binding(binding)
        try:
            accepted_handoff = HandoffContract(self._core_schema).validate(handoff)
        except (HandoffViolation, ContractViolation, TypeError, ValueError) as exc:
            raise ChannelEvidenceError("BLOCKED_HANDOFF", [str(exc)]) from exc

        if accepted_handoff["schema_version"] != REQUIRED_HANDOFF_SCHEMA_VERSION:
            raise ChannelEvidenceError("BLOCKED_HANDOFF_SCHEMA_VERSION")

        if accepted_binding["attempt_id"] != accepted_handoff["attempt_id"]:
            raise ChannelEvidenceError("HANDOFF_ATTEMPT_ID_MISMATCH")
        if accepted_binding["attempt_no"] == 1 and accepted_binding[
            "retry_of_attempt_id"
        ] is not None:
            raise ChannelEvidenceError("FIRST_ATTEMPT_CANNOT_RETRY")
        if accepted_binding["attempt_no"] > 1 and accepted_binding[
            "retry_of_attempt_id"
        ] is None:
            raise ChannelEvidenceError("RETRY_LINEAGE_REQUIRED")
        evidence_class = derive_evidence_class(accepted_binding["axes"])
        if evidence_class != accepted_binding["evidence_class"]:
            raise ChannelEvidenceError("EVIDENCE_CLASS_DERIVATION_MISMATCH")
        accounting = validate_accounting(evidence_class, accepted_binding["accounting"])

        events = {item["event_type"]: item for item in accepted_handoff["events"]}
        selection = events["selection"]
        selected_profile_ref = selection["payload"].get("selected_profile_ref")
        if selected_profile_ref != accepted_binding["profile_ref"]:
            raise ChannelEvidenceError("PROFILE_BINDING_MISMATCH")
        terminal_event = events["terminal"]
        terminal = terminal_event["payload"]

        blocked = evidence_class == "blocked_before_start"
        process_event = events.get("process")
        model_event = events.get("model")
        schema_event = events.get("schema")
        route_block_event = events.get("route_block")
        if blocked:
            if process_event or model_event or schema_event or route_block_event is None:
                raise ChannelEvidenceError("BLOCKED_HANDOFF_EVENT_SET_INVALID")
            reasons = route_block_event["payload"].get("reason_codes")
            if not isinstance(reasons, list) or not reasons:
                raise ChannelEvidenceError("BLOCKED_REASON_REQUIRED")
            if accepted_binding["blocked_reason_codes"] != reasons:
                raise ChannelEvidenceError("BLOCKED_REASON_BINDING_MISMATCH")
        else:
            if process_event is None or schema_event is None or route_block_event is not None:
                raise ChannelEvidenceError("STARTED_HANDOFF_EVENT_SET_INVALID")
            if accepted_binding["blocked_reason_codes"]:
                raise ChannelEvidenceError("STARTED_BINDING_HAS_BLOCK_REASONS")

        model_class = evidence_class in {"api", "subscription_cli", "local_compute"}
        if not model_class and model_event is not None:
            raise ChannelEvidenceError("MODEL_RECEIPT_PRESENCE_MISMATCH")
        if (
            model_class
            and terminal["terminal_state"] == "COMPLETED"
            and model_event is None
        ):
            raise ChannelEvidenceError("MODEL_RECEIPT_REQUIRED")
        if evidence_class == "human" and process_event is not None:
            raise ChannelEvidenceError("HUMAN_HANDOFF_PROCESS_FORBIDDEN")

        process = process_event["payload"] if process_event else None
        model = model_event["payload"] if model_event else None
        normalized = schema_event["payload"] if schema_event else None
        process_started = bool(process and process["process_started"])
        external_started = bool(model and model["external_request_started"])
        if terminal["physical_process_start_count"] != (1 if process_started else 0):
            raise ChannelEvidenceError("HANDOFF_PROCESS_START_COUNT_MISMATCH")
        if terminal["external_request_start_count"] != (1 if external_started else 0):
            raise ChannelEvidenceError("HANDOFF_EXTERNAL_START_COUNT_MISMATCH")
        expected_handoff_usage_status = (
            "N/A_BLOCKED" if blocked else accounting["usage_status"]
        )
        expected_handoff_cost_status = (
            "N/A_BLOCKED" if blocked else accounting["cost_status"]
        )
        if (
            terminal["token_status"] != expected_handoff_usage_status
            or terminal["cost_status"] != expected_handoff_cost_status
        ):
            raise ChannelEvidenceError("HANDOFF_ACCOUNTING_STATUS_MISMATCH")
        if (
            terminal["attempt_lineage"]["predecessor_attempt_ref"]
            != accepted_binding["retry_of_attempt_id"]
        ):
            raise ChannelEvidenceError("HANDOFF_RETRY_LINEAGE_MISMATCH")

        provider_identity = {
            "provider": None,
            "requested_model": None,
            "returned_model": None,
            "model_identity_grade": None,
        }
        output_hash = normalized["source_output_hash"] if normalized else None
        latency_ms = process["duration_ms"] if process else None
        process_evidence = {
            "adapter_id": process["adapter_id"] if process else None,
            "argv_redacted": process["argv_redacted"] if process else None,
            "working_directory": process["working_directory"] if process else None,
            "started_at": process["started_at"] if process else None,
            "ended_at": process["ended_at"] if process else None,
            "duration_ms": process["duration_ms"] if process else None,
            "exit_code": process["exit_code"] if process else None,
            "timeout": process["timeout"] if process else False,
            "killed": process["killed"] if process else False,
            "stdout_hash": process["stdout_hash"] if process else None,
            "stderr_hash": process["stderr_hash"] if process else None,
            "redaction_status": process["redaction_status"] if process else None,
            "output_files": immutable(process["output_files"]) if process else [],
            "environment_names": (
                immutable(process["environment_names"]) if process else []
            ),
            "error_code": process["error_code"] if process else None,
            "raw_truncated": (
                bool(process["extensions"].get("raw_truncated"))
                if process
                else False
            ),
        }
        if model is not None:
            comparisons = {
                "provider": accepted_binding["provider"],
                "requested_model": accepted_binding["requested_model"],
                "route": accepted_binding["route"],
                "region": accepted_binding["region"],
                "egress": accepted_binding["egress"],
                "behavior_hash": accepted_binding["behavior_hash"],
                "input_hash": accepted_binding["input_hash"],
            }
            mismatches = [key for key, expected in comparisons.items() if model[key] != expected]
            if mismatches:
                code = (
                    "MODEL_IDENTITY_MISMATCH"
                    if any(key in mismatches for key in ("provider", "requested_model"))
                    else "PRE_SEND_BINDING_MISMATCH"
                )
                raise ChannelEvidenceError(code, mismatches)
            if model["evidence_class"] != evidence_class:
                raise ChannelEvidenceError("MODEL_EVIDENCE_CLASS_MISMATCH")
            if (
                model["usage_status"] != accounting["usage_status"]
                or model["token_usage"] != accounting["token_usage"]
                or model["cost_status"] != accounting["cost_status"]
                or model["cost_value"] != accounting["cost_value"]
                or model["currency"] != accounting["currency"]
            ):
                raise ChannelEvidenceError("MODEL_ACCOUNTING_MISMATCH")
            provider_identity = {
                "provider": model["provider"],
                "requested_model": model["requested_model"],
                "returned_model": model["returned_model"],
                "model_identity_grade": model["model_identity_grade"],
            }
            output_hash = model["output_hash"]
            latency_ms = model["latency_ms"]
        elif model_class:
            provider_identity = {
                "provider": accepted_binding["provider"],
                "requested_model": accepted_binding["requested_model"],
                "returned_model": None,
                "model_identity_grade": "UNKNOWN",
            }
        elif any(
            accepted_binding[key] is not None
            for key in ("provider", "requested_model")
        ):
            raise ChannelEvidenceError("NON_MODEL_PROVIDER_BINDING_FORBIDDEN")

        result_category = {
            "BLOCKED": "not_assessed",
            "COMPLETED": "success" if terminal["execution_result"] == "SUCCESS" else "failure",
            "ERROR": "error",
            "TIMEOUT": "incomplete",
            "KILLED": "cancelled",
            "ABORTED": "cancelled",
        }[terminal["terminal_state"]]
        started_at = process["started_at"] if process and process_started else None
        finished_at = (
            process["ended_at"]
            if process and process["ended_at"] is not None
            else accepted_handoff["created_at"]
        )
        channel_evidence = {
            "axes": immutable(accepted_binding["axes"]),
            "evidence_class": evidence_class,
            "handoff_ref": accepted_handoff["handoff_id"],
            "selection_ref": selection["object_ref"],
            "route_block_ref": route_block_event["object_ref"] if route_block_event else None,
            "process_receipt_ref": process_event["object_ref"] if process_event else None,
            "model_receipt_ref": model_event["object_ref"] if model_event else None,
            "terminal_ref": terminal_event["object_ref"],
            "process_started": process_started,
            "external_request_started": external_started,
            "physical_process_start_count": terminal["physical_process_start_count"],
            "external_request_start_count": terminal["external_request_start_count"],
            "route": accepted_binding["route"],
            "region": accepted_binding["region"],
            "egress": accepted_binding["egress"],
            "behavior_hash": accepted_binding["behavior_hash"],
            "input_hash": accepted_binding["input_hash"],
            "output_hash": output_hash,
            "latency_ms": latency_ms,
            "terminal_state": terminal["terminal_state"],
            "execution_result": terminal["execution_result"],
            "blocked_reason_codes": immutable(accepted_binding["blocked_reason_codes"]),
        }
        record = {
            "schema_version": SCHEMA_VERSION,
            "record_type": RECORD_TYPE,
            "record_kind": TERMINAL_KIND,
            "record_id": accepted_handoff["attempt_id"],
            "run_id": accepted_handoff["job_id"],
            "created_at": self._clock(),
            "source_evidence_refs": immutable(accepted_binding["source_evidence_refs"]),
            "attempt_id": accepted_handoff["attempt_id"],
            "logical_operation_id": accepted_binding["logical_operation_id"],
            "attempt_no": accepted_binding["attempt_no"],
            "retry_of_attempt_id": accepted_binding["retry_of_attempt_id"],
            "task_id": accepted_binding["task_id"],
            "stage": accepted_binding["stage"],
            "paper_id": accepted_binding["paper_id"],
            "profile_ref": accepted_binding["profile_ref"],
            "attempt_started": bool(
                process_started or external_started or evidence_class == "human"
            ),
            "started_at": started_at,
            "finished_at": finished_at,
            "result_category": result_category,
            "error_code": terminal["error_code"],
            "verification_result": terminal["verification_result"],
            "acceptance_verdict": terminal["acceptance_verdict"],
            "handoff_identity": {
                "schema_version": accepted_handoff["schema_version"],
                "handoff_id": accepted_handoff["handoff_id"],
                "events_sha256": accepted_handoff["events_sha256"],
                "event_count": accepted_handoff["event_count"],
            },
            "provider_identity": provider_identity,
            "process_evidence": process_evidence,
            "channel_evidence": channel_evidence,
            "accounting": accounting,
            "token_usage": immutable(accounting["token_usage"]),
            "cost": {
                "status": accounting["cost_status"],
                "value": accounting["cost_value"],
                "currency": accounting["currency"],
            },
        }
        validate_channel_attempt(record)
        return record

    @staticmethod
    def _validate_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(binding, Mapping):
            raise ChannelEvidenceError("PRE_SEND_BINDING_INVALID")
        missing = sorted(_BINDING_KEYS - set(binding))
        extra = sorted(set(binding) - _BINDING_KEYS)
        if missing:
            raise ChannelEvidenceError("PRE_SEND_BINDING_MISSING", missing)
        if extra:
            raise ChannelEvidenceError("PRE_SEND_BINDING_FIELDS_INVALID", extra)
        accepted = immutable(dict(binding))
        for field in ("attempt_id", "logical_operation_id", "task_id", "stage"):
            _require_text(accepted[field], field)
        attempt_no = accepted["attempt_no"]
        if isinstance(attempt_no, bool) or not isinstance(attempt_no, int) or attempt_no < 1:
            raise ChannelEvidenceError("PRE_SEND_ATTEMPT_NO_INVALID")
        for field in ("retry_of_attempt_id", "paper_id", "profile_ref", "provider", "requested_model", "route", "region", "egress", "behavior_hash", "input_hash"):
            value = accepted[field]
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ChannelEvidenceError("PRE_SEND_BINDING_VALUE_INVALID", [field])
        if not isinstance(accepted["blocked_reason_codes"], list) or any(
            not isinstance(item, str) or not item.strip()
            for item in accepted["blocked_reason_codes"]
        ):
            raise ChannelEvidenceError("BLOCKED_REASON_BINDING_INVALID")
        if not isinstance(accepted["source_evidence_refs"], list) or not accepted[
            "source_evidence_refs"
        ] or any(
            not isinstance(item, str) or not item.strip()
            for item in accepted["source_evidence_refs"]
        ):
            raise ChannelEvidenceError("SOURCE_EVIDENCE_REFS_INVALID")
        return accepted


def build_accounting_amendment(
    base: Mapping[str, Any],
    accounting: Mapping[str, Any],
    *,
    record_id: str,
    created_at: str,
    source_evidence_refs: Sequence[str],
) -> dict[str, Any]:
    validate_channel_attempt(base)
    _require_text(record_id, "record_id")
    _require_timestamp(created_at, "created_at")
    if not source_evidence_refs or any(
        not isinstance(ref, str) or not ref.strip() for ref in source_evidence_refs
    ):
        raise ChannelEvidenceError("SOURCE_EVIDENCE_REFS_INVALID")
    evidence_class = base["channel_evidence"]["evidence_class"]
    accepted = validate_accounting(evidence_class, accounting)
    record = {
        "schema_version": SCHEMA_VERSION,
        "record_type": RECORD_TYPE,
        "record_kind": AMENDMENT_KIND,
        "record_id": record_id,
        "run_id": base["run_id"],
        "created_at": created_at,
        "source_evidence_refs": list(source_evidence_refs),
        "attempt_id": base["attempt_id"],
        "amends_attempt_id": base["attempt_id"],
        "logical_operation_id": base["logical_operation_id"],
        "attempt_no": base["attempt_no"],
        "retry_of_attempt_id": base["retry_of_attempt_id"],
        "task_id": base["task_id"],
        "stage": base["stage"],
        "paper_id": base["paper_id"],
        "evidence_class": evidence_class,
        "amendment_fields": {
            "accounting": accepted,
            "token_usage": immutable(accepted["token_usage"]),
            "cost": {
                "status": accepted["cost_status"],
                "value": accepted["cost_value"],
                "currency": accepted["currency"],
            },
        },
    }
    _validate_amendment(record)
    return record
