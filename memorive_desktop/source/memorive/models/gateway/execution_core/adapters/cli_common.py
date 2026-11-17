"""What the command-line model adapters share: their error type, the checks of a pre-send sidecar,
an entitlement snapshot or a login fact, the request-to-model binding hash, result normalization,
the offline Q2 projection and the model execution receipt.

Each adapter (gemini_cli.py, qwen_code.py, kimi_code.py, codebuddy_code.py, github_copilot_cli.py,
claude_code.py) subclasses CommandLineAdapter and sets what differs as class attributes: VIOLATION,
CODE_PREFIX (error codes are "<CODE_PREFIX>_<NAME>", such as GEMINI_ENTITLEMENT_NOT_ACTIVE), PRODUCER,
SIDECAR_TASK_ID, PROVIDER and the result statuses. It keeps its own policy, manifest, behavior hash,
invocation and output parsing, and overrides a shared method where its CLI differs. codex_cli.py has
its own structure and does not use this module.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

from memorive.models.gateway.execution_core.contracts import (
    base_object,
    canonical_sha256,
    immutable_copy,
    utc_now,
    validate_initialization_object,
)
from memorive.models.gateway.execution_core.runner import ProcessRun


SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ANSI_RE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
SECRET_PREFIXES = ("sk-", "ghp_", "github_pat_", "xox", "pat-")
DIRECT_REQUEST_CONTROL_PLANE = "DIRECT_REQUEST_CONTROL_PLANE_V1"


class CommandLineAdapterViolation(ValueError):
    """An adapter refused something: code names the rule, details the fields or items concerned.
    Each adapter has its own subclass (GeminiCliViolation and so on)."""

    def __init__(self, code: str, details: Sequence[str] = ()):
        self.code = code
        self.details = tuple(details)
        suffix = f": {'; '.join(self.details)}" if self.details else ""
        super().__init__(f"{code}{suffix}")


def schema_errors(schema: Mapping[str, Any], value: Any) -> list[str]:
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda item: (list(item.absolute_path), item.message),
    )
    return [
        f"{'/'.join(str(x) for x in item.absolute_path) or '$'}: {item.message}"
        for item in errors[:20]
    ]


def integer_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class CommandLineAdapter:
    ADAPTER_ID: str
    VIOLATION: type[CommandLineAdapterViolation] = CommandLineAdapterViolation
    CODE_PREFIX: str
    PRODUCER: str
    SIDECAR_TASK_ID: str
    PROVIDER: str
    RESULT_SUCCESS_STATUS = "SUCCESS"
    RESULT_ERROR_SUFFIX = "RESULT_ERROR"

    def __init__(
        self,
        policy: Any,
        core_schema: Mapping[str, Any],
        external_call_schema: Mapping[str, Any],
        *,
        clock: Callable[[], str] = utc_now,
    ):
        self.policy = policy
        self._core_schema = immutable_copy(core_schema)
        self._external_call_schema = immutable_copy(external_call_schema)
        Draft202012Validator.check_schema(self._external_call_schema)
        self._clock = clock

    @classmethod
    def _code(cls, name: str) -> str:
        return f"{cls.CODE_PREFIX}_{name}"

    def _parse_time(self, value: Any, code: str) -> dt.datetime:
        if not isinstance(value, str) or not value:
            raise self.VIOLATION(code)
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise self.VIOLATION(code) from exc
        if parsed.tzinfo is None:
            raise self.VIOLATION(code)
        return parsed.astimezone(dt.timezone.utc)

    def _secret_scan(self, value: Any, path: str = "$") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                self._secret_scan(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self._secret_scan(item, f"{path}[{index}]")
        elif isinstance(value, str) and value.strip().lower().startswith(SECRET_PREFIXES):
            raise self.VIOLATION("SECRET_LIKE_VALUE_FORBIDDEN", [path])

    def model_allowlist_sha256(self) -> str:
        return canonical_sha256(
            {
                "allowed_models": sorted(self.policy.allowed_models),
                "model_catalog_sha256": self.policy.model_catalog_sha256.upper(),
            }
        )

    def _access_receipt_fields(self, invocation: Any) -> dict[str, Any]:
        """The receipt's proof of access: the entitlement snapshot hash. Adapters whose access is a
        login fact return its hash and the entitlement status instead."""
        return {"entitlement_snapshot_sha256": invocation.entitlement_snapshot_sha256}

    def _validate_entitlement(
        self,
        snapshot: Mapping[str, Any] | None,
        *,
        access_profile: str,
    ) -> str | None:
        if access_profile == self.policy.api_profile:
            if snapshot is not None:
                raise self.VIOLATION(self._code("API_PROFILE_ENTITLEMENT_FORBIDDEN"))
            return None
        if not isinstance(snapshot, Mapping):
            raise self.VIOLATION(self._code("ENTITLEMENT_SNAPSHOT_REQUIRED"))
        accepted = immutable_copy(snapshot)
        required = {
            "snapshot_id",
            "profile_id",
            "status",
            "observed_at",
            "expires_at",
            "source_sha256",
            "session_body_read",
        }
        if set(accepted) != required:
            raise self.VIOLATION(self._code("ENTITLEMENT_SNAPSHOT_FIELDS_INVALID"))
        if accepted["profile_id"] != access_profile or accepted["status"] != "ACTIVE":
            raise self.VIOLATION(self._code("ENTITLEMENT_NOT_ACTIVE"))
        if accepted["session_body_read"] is not False:
            raise self.VIOLATION(self._code("ENTITLEMENT_SESSION_BODY_READ_FORBIDDEN"))
        if not SHA256_RE.fullmatch(str(accepted["source_sha256"]).upper()):
            raise self.VIOLATION(self._code("ENTITLEMENT_SOURCE_HASH_INVALID"))
        now = self._parse_time(self._clock(), self._code("CLOCK_INVALID"))
        observed = self._parse_time(accepted["observed_at"], self._code("ENTITLEMENT_TIME_INVALID"))
        expires = self._parse_time(accepted["expires_at"], self._code("ENTITLEMENT_TIME_INVALID"))
        if observed > now or expires <= now:
            raise self.VIOLATION(self._code("ENTITLEMENT_EXPIRED_OR_NOT_YET_VALID"))
        ttl = (expires - observed).total_seconds()
        if ttl <= 0 or ttl > self.policy.max_entitlement_ttl_seconds:
            raise self.VIOLATION(self._code("ENTITLEMENT_TTL_EXCEEDED"))
        return canonical_sha256(accepted)

    def _validate_login_fact(
        self,
        fact: Mapping[str, Any] | None,
        *,
        access_profile: str,
    ) -> str:
        if not isinstance(fact, Mapping):
            raise self.VIOLATION(self._code("LOGIN_FACT_REQUIRED"))
        accepted = immutable_copy(fact)
        required = {
            "profile_id",
            "status",
            "source",
            "confirmed_at",
            "login_domain",
            "credential_material_read",
            "entitlement_status",
        }
        if set(accepted) != required:
            raise self.VIOLATION(self._code("LOGIN_FACT_FIELDS_INVALID"))
        if accepted["profile_id"] != access_profile:
            raise self.VIOLATION(self._code("LOGIN_PROFILE_MISMATCH"))
        if accepted["status"] != "LOGIN_USER_CONFIRMED_SUCCESS":
            raise self.VIOLATION(self._code("LOGIN_NOT_CONFIRMED"))
        if accepted["source"] != "USER_COORDINATION_UPDATE":
            raise self.VIOLATION(self._code("LOGIN_SOURCE_INVALID"))
        self._parse_time(accepted["confirmed_at"], self._code("LOGIN_TIME_INVALID"))
        if accepted["login_domain"] != "NOT_SAFELY_OBSERVED":
            raise self.VIOLATION(self._code("LOGIN_DOMAIN_OVERCLAIMED"))
        if accepted["credential_material_read"] is not False:
            raise self.VIOLATION(self._code("CREDENTIAL_READ_FORBIDDEN"))
        if accepted["entitlement_status"] != "ENTITLEMENT_UNVERIFIED/NOT_REQUIRED_FOR_Q2":
            raise self.VIOLATION(self._code("ENTITLEMENT_STATUS_INVALID"))
        self._secret_scan(accepted)
        return canonical_sha256(accepted)

    def _validate_sidecar(
        self,
        value: Mapping[str, Any],
        *,
        attempt_id: str,
        model: str,
        access_profile: str,
        prompt_sha256: str,
        output_contract_sha256: str,
        behavior_hash: str,
    ) -> dict[str, Any]:
        sidecar = immutable_copy(value)
        errors = sorted(
            Draft202012Validator(
                self._external_call_schema, format_checker=FormatChecker()
            ).iter_errors(sidecar),
            key=lambda item: (list(item.absolute_path), item.message),
        )
        if errors:
            raise self.VIOLATION(
                "CALL_SIDECAR_SCHEMA_INVALID",
                [
                    f"{'/'.join(str(x) for x in item.absolute_path) or '$'}: {item.message}"
                    for item in errors[:20]
                ],
            )
        self._secret_scan(sidecar)
        mismatches: list[str] = []
        expected = {
            "schema_version": "1.0",
            "record_type": "call_binding_receipt",
            "binding_mode": "PROSPECTIVE_STRICT",
            "phase_id": "Execution",
            "task_id": self.SIDECAR_TASK_ID,
            "attempt_id": attempt_id,
            "request_disposition": "BLOCKED_BEFORE_SEND",
        }
        mismatches.extend(key for key, expected_value in expected.items() if sidecar.get(key) != expected_value)
        profile = sidecar.get("profile_identity", {})
        identity = sidecar.get("model_identity", {})
        route = sidecar.get("route_identity", {})
        artifacts = sidecar.get("artifact_bindings", {})
        extensions = sidecar.get("extensions", {})
        if profile.get("profile_id") != access_profile:
            mismatches.append("profile_identity.profile_id")
        if identity.get("requested_model") != model:
            mismatches.append("model_identity.requested_model")
        if route.get("route_id") != self.policy.route_id:
            mismatches.append("route_identity.route_id")
        if artifacts.get("prompt_hash", "").upper() != prompt_sha256:
            mismatches.append("artifact_bindings.prompt_hash")
        if artifacts.get("output_contract_hash", "").upper() != output_contract_sha256:
            mismatches.append("artifact_bindings.output_contract_hash")
        if artifacts.get("runner_hash", "").upper() != self.policy.runner_sha256.upper():
            mismatches.append("artifact_bindings.runner_hash")
        if extensions.get("behavior_hash", "").upper() != behavior_hash:
            mismatches.append("extensions.behavior_hash")
        if extensions.get("pre_send_state") != "FROZEN_READY":
            mismatches.append("extensions.pre_send_state")
        if sidecar.get("outcome", {}).get("call_status") != "BLOCKED_BEFORE_SEND":
            mismatches.append("outcome.call_status")
        if mismatches:
            raise self.VIOLATION("CALL_SIDECAR_BINDING_MISMATCH", sorted(set(mismatches)))
        return sidecar

    def _request_binding_sha256(
        self, argv: Sequence[str], requested_model: str, behavior_hash: str
    ) -> str:
        positions = [i for i, item in enumerate(argv) if item == "--model"]
        if len(positions) != 1 or positions[0] + 1 >= len(argv):
            raise self.VIOLATION(self._code("REQUEST_MODEL_ARGV_INVALID"))
        if argv[positions[0] + 1] != requested_model:
            raise self.VIOLATION(self._code("REQUEST_MODEL_BINDING_MISMATCH"))
        return canonical_sha256(
            {
                "adapter_id": self.ADAPTER_ID,
                "launcher_sha256": self.policy.launcher_sha256.upper(),
                "entrypoint_sha256": self.policy.entrypoint_sha256.upper(),
                "cli_version": self.policy.exact_version,
                "argv_sha256": canonical_sha256(list(argv)),
                "model_flag_index": positions[0],
                "requested_model": requested_model,
                "model_allowlist_sha256": self.model_allowlist_sha256(),
                "behavior_hash": behavior_hash,
            }
        )

    def normalize_result(
        self,
        process_run: ProcessRun,
        event_summary: Mapping[str, Any],
        *,
        logical_role: str,
        payload_schema_ref: str,
        payload_schema: Mapping[str, Any],
    ) -> dict[str, Any]:
        process = validate_initialization_object(
            process_run.receipt, "ProcessExecutionReceipt", self._core_schema
        )
        schema = immutable_copy(payload_schema)
        payload: dict[str, Any] = {}
        warnings: list[str] = []
        error_code: str | None = None
        raw_message = event_summary.get("final_message")
        if not process["process_started"]:
            error_code = process.get("error_code") or "PROCESS_NOT_STARTED"
        elif process.get("timeout"):
            error_code = "PROCESS_TIMEOUT"
        elif process.get("extensions", {}).get("raw_truncated"):
            error_code = "RAW_OUTPUT_TRUNCATED"
        elif process.get("exit_code") != 0:
            error_code = process.get("error_code") or "PROCESS_EXIT_NONZERO"
        elif event_summary.get("finish_status") != self.RESULT_SUCCESS_STATUS:
            error_code = self._code(self.RESULT_ERROR_SUFFIX)
        elif not isinstance(raw_message, str):
            error_code = "FINAL_MESSAGE_MISSING"
        else:
            try:
                decoded = json.loads(raw_message)
            except json.JSONDecodeError as exc:
                warnings.append(f"JSON:{exc.pos}")
                error_code = "OUTPUT_JSON_INVALID"
            else:
                if not isinstance(decoded, dict):
                    error_code = "OUTPUT_JSON_OBJECT_REQUIRED"
                else:
                    payload = decoded
                    warnings = schema_errors(schema, payload)
                    if warnings:
                        payload = {}
                        error_code = "OUTPUT_SCHEMA_INVALID"
        value = base_object(
            "NormalizedResult",
            f"normalizedresult_{process['job_id']}_{process['attempt_id']}",
            producer=self.PRODUCER,
            single_writer=self.PRODUCER,
            consumers=("RUNTIME_LOG", "ARTIFACT_REGISTRY", "CALLING_MODULE"),
            clock=self._clock,
            source_evidence_refs=(process["object_id"],),
            extensions={
                "raw_stdout_bytes": len(process_run.stdout),
                "raw_stderr_bytes": len(process_run.stderr),
                "event_stream_sha256": hashlib.sha256(process_run.stdout).hexdigest().upper(),
                "model_repair_attempted": False,
                "model_text_self_report_used": False,
            },
        )
        value.update(
            {
                "job_id": process["job_id"],
                "attempt_id": process["attempt_id"],
                "logical_role": logical_role,
                "payload": payload,
                "payload_schema_ref": payload_schema_ref,
                "payload_schema_hash": canonical_sha256(schema),
                "payload_valid": error_code is None,
                "source_output_hash": hashlib.sha256(
                    (raw_message or "").encode("utf-8")
                ).hexdigest().upper(),
                "normalization_version": self.NORMALIZATION_VERSION,
                "warnings": warnings,
                "error_code": error_code,
            }
        )
        return validate_initialization_object(value, "NormalizedResult", self._core_schema)

    @classmethod
    def offline_q2_projection(
        cls,
        normalized: Mapping[str, Any],
        event_summary: Mapping[str, Any],
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if not normalized.get("payload_valid"):
            reasons.append(str(normalized.get("error_code") or "OUTPUT_INVALID"))
        if event_summary.get("finish_status") != cls.RESULT_SUCCESS_STATUS:
            reasons.append(cls._code("RESULT_NOT_SUCCESS"))
        if event_summary.get("request_model_binding_status") != "EXACT_ARGV_AND_FROZEN_CONTROL_PLANE":
            reasons.append("REQUEST_MODEL_NOT_CONTROL_PLANE_BOUND")
        if event_summary.get("model_identity_grade") not in {"DIRECT_EVENT_MATCH", "PROVIDER_NOT_EXPOSED"}:
            reasons.append("MODEL_EVENT_CONFLICT")
        return {
            "qualification_level": "Q2_OFFLINE",
            "qualification_eligible": not reasons,
            "verification_result": "PASS" if not reasons else "FAIL",
            "acceptance_verdict": "NOT_ASSESSED",
            "reason_codes": reasons,
            "real_model_request_count": 0,
            "requested_model": event_summary.get("requested_model"),
            "returned_model": event_summary.get("returned_model"),
            "model_identity_grade": event_summary.get("model_identity_grade"),
            "model_text_self_report_used": False,
        }

    def build_model_receipt(
        self,
        process_run: ProcessRun,
        event_summary: Mapping[str, Any],
        *,
        invocation: Any,
        input_hash: str,
        output_hash: str | None,
        latency_ms: float,
    ) -> dict[str, Any]:
        process = validate_initialization_object(
            process_run.receipt, "ProcessExecutionReceipt", self._core_schema
        )
        value = base_object(
            "ModelExecutionReceipt",
            f"modelexecutionreceipt_{process['job_id']}_{process['attempt_id']}",
            producer=self.PRODUCER,
            single_writer=self.PRODUCER,
            consumers=("CHANNEL_ACCOUNTING_RUNTIME_LOG", "MODEL_EVALUATION", "ARTIFACT_REGISTRY"),
            clock=self._clock,
            source_evidence_refs=(process["object_id"],),
            extensions={
                "parser_version": event_summary.get("parser_version"),
                "events_sha256": event_summary.get("events_sha256"),
                "request_model_binding_sha256": invocation.request_model_binding_sha256,
                "model_allowlist_sha256": invocation.model_allowlist_sha256,
                **self._access_receipt_fields(invocation),
                "model_text_self_report_used": False,
                "offline_q2_fixture": True,
            },
        )
        value.update(
            {
                "job_id": process["job_id"],
                "attempt_id": process["attempt_id"],
                "evidence_class": self.policy.profile_evidence_classes[invocation.access_profile],
                "requested_model": invocation.requested_model,
                "returned_model": event_summary.get("returned_model"),
                "model_identity_grade": event_summary.get("model_identity_grade"),
                "provider": self.PROVIDER,
                "route": self.policy.route_id,
                "region": self.policy.provider_region,
                "egress": self.policy.egress_identity,
                "behavior_hash": invocation.behavior_hash,
                "input_hash": input_hash,
                "output_hash": output_hash,
                "usage_status": event_summary.get("usage_status"),
                "token_usage": immutable_copy(event_summary.get("token_usage", {})),
                "cost_status": "N/A_OFFLINE_FIXTURE",
                "cost_value": None,
                "currency": None,
                "latency_ms": latency_ms,
                "finish_status": event_summary.get("finish_status"),
                "external_request_started": bool(event_summary.get("external_request_started")),
            }
        )
        return validate_initialization_object(value, "ModelExecutionReceipt", self._core_schema)
