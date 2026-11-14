"""Fail-closed, offline-only CodeBuddy Code adapter candidate for DEEPCODE-ADAPTER.

The Q2 contract binds an immutable launcher/entrypoint, exact ``--model``
argv, a frozen model catalog and a user-confirmed abstract login fact.  It
never reads credential material, sends a real CLI prompt, activates a route,
retries, falls back, or writes RUNTIME_LOG.  Generated answer text is never identity
evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

from ..contracts import (
    base_object,
    canonical_json_bytes,
    canonical_sha256,
    immutable_copy,
    utc_now,
    validate_initialization_object,
)
from ..runner import ProcessRun, ProcessSpec
from ..component_binding import bindings_from_policy
from ..workspace import JobWorkspace


SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ANSI_RE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
SECRET_PREFIXES = ("sk-", "ghp_", "github_pat_", "xox", "pat-")
DIRECT_REQUEST_CONTROL_PLANE = "DIRECT_REQUEST_CONTROL_PLANE_V1"


class CodeBuddyCodeViolation(ValueError):
    def __init__(self, code: str, details: Sequence[str] = ()):
        self.code = code
        self.details = tuple(details)
        suffix = f": {'; '.join(self.details)}" if self.details else ""
        super().__init__(f"{code}{suffix}")


@dataclass(frozen=True)
class CodeBuddyCodePolicy:
    launcher: str
    command_prefix: tuple[str, ...]
    exact_version: str
    launcher_sha256: str
    wrapper_sha256: str
    entrypoint_sha256: str
    help_sha256: str
    runner_sha256: str
    model_catalog_sha256: str
    allowed_models: tuple[str, ...]
    allowed_profiles: tuple[str, ...]
    profile_environment_names: Mapping[str, tuple[str, ...]]
    profile_evidence_classes: Mapping[str, str]
    environment_allowlist: tuple[str, ...]
    user_login_profile: str
    route_id: str
    provider_region: str
    egress_identity: str
    max_timeout_seconds: float = 900.0
    max_raw_cap_bytes: int = 8 * 1024 * 1024
    component_paths: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        launcher = Path(self.launcher)
        if not launcher.is_absolute() or not launcher.is_file():
            raise CodeBuddyCodeViolation("CODEBUDDY_LAUNCHER_INVALID")
        for field, value in (
            ("launcher_sha256", self.launcher_sha256),
            ("wrapper_sha256", self.wrapper_sha256),
            ("entrypoint_sha256", self.entrypoint_sha256),
            ("help_sha256", self.help_sha256),
            ("runner_sha256", self.runner_sha256),
            ("model_catalog_sha256", self.model_catalog_sha256),
        ):
            if not SHA256_RE.fullmatch(value.upper()):
                raise CodeBuddyCodeViolation("CODEBUDDY_COMPATIBILITY_HASH_INVALID", [field])
        if hashlib.sha256(launcher.read_bytes()).hexdigest().upper() != self.launcher_sha256.upper():
            raise CodeBuddyCodeViolation("CODEBUDDY_LAUNCHER_HASH_MISMATCH")
        if not re.fullmatch(r"\d+\.\d+\.\d+", self.exact_version):
            raise CodeBuddyCodeViolation("CODEBUDDY_VERSION_INVALID")
        if not self.allowed_models or any(not SAFE_MODEL_RE.fullmatch(x) for x in self.allowed_models):
            raise CodeBuddyCodeViolation("CODEBUDDY_MODEL_ALLOWLIST_INVALID")
        if set(self.allowed_profiles) != {self.user_login_profile}:
            raise CodeBuddyCodeViolation("CODEBUDDY_PROFILE_ALLOWLIST_INVALID")
        if set(self.profile_environment_names) != set(self.allowed_profiles):
            raise CodeBuddyCodeViolation("CODEBUDDY_PROFILE_ENVIRONMENT_INVALID")
        if set(self.profile_evidence_classes) != set(self.allowed_profiles):
            raise CodeBuddyCodeViolation("CODEBUDDY_PROFILE_EVIDENCE_CLASS_INVALID")
        allowed_env = set(self.environment_allowlist)
        if len(allowed_env) != len(self.environment_allowlist):
            raise CodeBuddyCodeViolation("CODEBUDDY_ENV_ALLOWLIST_DUPLICATE")
        for profile, names in self.profile_environment_names.items():
            if len(set(names)) != len(names) or not set(names).issubset(allowed_env):
                raise CodeBuddyCodeViolation("CODEBUDDY_PROFILE_ENVIRONMENT_INVALID", [profile])
            if "CODEBUDDY_RUNTIME_DIR" not in names:
                raise CodeBuddyCodeViolation("CODEBUDDY_RUNTIME_ENV_REQUIRED", [profile])
            forbidden = {"CODEBUDDY_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
            if forbidden & set(names):
                raise CodeBuddyCodeViolation("CODEBUDDY_CREDENTIAL_ENV_FORBIDDEN", [profile])
        if self.max_timeout_seconds <= 0 or self.max_raw_cap_bytes <= 0:
            raise CodeBuddyCodeViolation("CODEBUDDY_RESOURCE_POLICY_INVALID")


@dataclass(frozen=True)
class CodeBuddyCodeInvocation:
    process_spec: ProcessSpec
    behavior_hash: str
    prompt_sha256: str
    output_contract_sha256: str
    pre_send_sidecar_sha256: str
    requested_model: str
    model_allowlist_sha256: str
    request_model_binding_sha256: str
    access_profile: str
    login_fact_sha256: str


def _parse_time(value: Any, code: str) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise CodeBuddyCodeViolation(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CodeBuddyCodeViolation(code) from exc
    if parsed.tzinfo is None:
        raise CodeBuddyCodeViolation(code)
    return parsed.astimezone(dt.timezone.utc)


def _secret_scan(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _secret_scan(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _secret_scan(item, f"{path}[{index}]")
    elif isinstance(value, str) and value.strip().lower().startswith(SECRET_PREFIXES):
        raise CodeBuddyCodeViolation("SECRET_LIKE_VALUE_FORBIDDEN", [path])


def _schema_errors(schema: Mapping[str, Any], value: Any) -> list[str]:
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda item: (list(item.absolute_path), item.message),
    )
    return [
        f"{'/'.join(str(x) for x in item.absolute_path) or '$'}: {item.message}"
        for item in errors[:20]
    ]


def _integer_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class CodeBuddyCodeAdapter:
    ADAPTER_ID = "codebuddy_code"
    PARSER_VERSION = "DEEPCODE_ADAPTER_CODEBUDDY_STREAM_JSON_V1"
    NORMALIZATION_VERSION = "DEEPCODE_ADAPTER_CODEBUDDY_FINAL_MESSAGE_V1"
    FORBIDDEN_FLAGS = {
        "-y",
        "--dangerously-skip-permissions",
        "--yolo",
        "--worktree",
        "-w",
        "--add-dir",
        "--allowedTools",
        "--fallback-model",
        "--continue",
        "-c",
        "--resume",
        "-r",
        "--settings",
        "--setting-sources",
        "--permission-prompt-tool",
        "--mcp-config",
    }

    def __init__(
        self,
        policy: CodeBuddyCodePolicy,
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

    def model_allowlist_sha256(self) -> str:
        return canonical_sha256(
            {
                "allowed_models": sorted(self.policy.allowed_models),
                "model_catalog_sha256": self.policy.model_catalog_sha256.upper(),
            }
        )

    def build_manifest(self) -> dict[str, Any]:
        value = base_object(
            "AdapterManifest",
            "adaptermanifest_codebuddy_code",
            producer="DEEPCODE_ADAPTER_CODEBUDDY_CODE_ADAPTER",
            single_writer="DEEPCODE_ADAPTER_CODEBUDDY_CODE_ADAPTER",
            consumers=("Execution_DISCOVERY_RUNNER", "Execution_JOB_RUNNER", "CHANNEL_ACCOUNTING_RUNTIME_LOG"),
            clock=self._clock,
            source_evidence_refs=(
                f"codebuddy-launcher-sha256:{self.policy.launcher_sha256.upper()}",
                f"codebuddy-wrapper-sha256:{self.policy.wrapper_sha256.upper()}",
                f"codebuddy-entrypoint-sha256:{self.policy.entrypoint_sha256.upper()}",
                f"codebuddy-help-sha256:{self.policy.help_sha256.upper()}",
                f"model-catalog-sha256:{self.policy.model_catalog_sha256.upper()}",
            ),
            extensions={
                "command_prefix_sha256": canonical_sha256(
                    [self.policy.launcher, *self.policy.command_prefix]
                ),
                "runner_sha256": self.policy.runner_sha256.upper(),
                "access_profiles": list(self.policy.allowed_profiles),
                "login_fact": "LOGIN_USER_CONFIRMED_SUCCESS",
                "entitlement_status": "ENTITLEMENT_UNVERIFIED/NOT_REQUIRED_FOR_Q2",
                "external_prompt_request_limit": 0,
                "model_text_self_report_used": False,
                "qualification_only": True,
                "activation": "DISABLED",
            },
        )
        value.update(
            {
                "adapter_id": self.ADAPTER_ID,
                "adapter_type": "external_model_cli_process",
                "executable_name": str(Path(self.policy.launcher).resolve()),
                "install_discovery": "FROZEN_LAUNCHER_WRAPPER_ENTRYPOINT_HASHES",
                "version_discovery": "codebuddy --version",
                "auth_discovery": "USER_CONFIRMED_ABSTRACT_STATUS_ONLY",
                "session_discovery": "NO_CREDENTIAL_OR_SESSION_BODY_READ",
                "model_discovery": "EXACT_ARGV_PLUS_FROZEN_OFFICIAL_CATALOG",
                "capability_discovery": "codebuddy --help frozen hash",
                "permission_flags": ["PLAN_MODE", "EMPTY_TOOL_SET", "SKIP_PERMISSIONS_FORBIDDEN", "TOOL_EVENTS_REJECTED"],
                "workspace_flags": ["ISOLATED_CWD", "WORKTREE_FORBIDDEN", "RUNTIME_BOUND_TO_SCRATCH"],
                "network_flags": ["OFFLINE_Q2", "EXTERNAL_PROMPT_REQUEST_LIMIT_ZERO"],
                "output_modes": ["STREAM_JSON", "STRICT_FINAL_MESSAGE_JSON"],
                "exit_code_mapping": [
                    {"exit_code": 0, "state": "REQUIRES_EVENT_AND_SCHEMA_VALIDATION"},
                    {"exit_code": "nonzero", "state": "ERROR"},
                ],
                "timeout_kill_policy": "DataContracts_KILL_PROCESS_TREE",
                "redaction_policy": "ARGV_CREDENTIAL_VALUES_FORBIDDEN_ENV_NAMES_ONLY",
                "version_range": self.policy.exact_version,
                "platforms": [os.name],
            }
        )
        return validate_initialization_object(value, "AdapterManifest", self._core_schema)

    def behavior_hash(
        self,
        *,
        model: str,
        output_contract_sha256: str,
        access_profile: str,
        output_mode: str = "stream-json",
    ) -> str:
        return canonical_sha256(
            {
                "adapter_id": self.ADAPTER_ID,
                "launcher_sha256": self.policy.launcher_sha256.upper(),
                "wrapper_sha256": self.policy.wrapper_sha256.upper(),
                "entrypoint_sha256": self.policy.entrypoint_sha256.upper(),
                "help_sha256": self.policy.help_sha256.upper(),
                "runner_sha256": self.policy.runner_sha256.upper(),
                "model_catalog_sha256": self.policy.model_catalog_sha256.upper(),
                "model": model,
                "output_contract_sha256": output_contract_sha256.upper(),
                "output_mode": output_mode,
                "access_profile": access_profile,
                "permission_mode": "plan",
                "tools": [],
                "dangerously_skip_permissions": False,
                "worktree": False,
                "fallback": "FORBIDDEN",
                "external_prompt_request_limit": 0,
                "model_text_self_report_used": False,
            }
        )

    def _validate_login_fact(
        self,
        fact: Mapping[str, Any] | None,
        *,
        access_profile: str,
    ) -> str:
        if not isinstance(fact, Mapping):
            raise CodeBuddyCodeViolation("CODEBUDDY_LOGIN_FACT_REQUIRED")
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
            raise CodeBuddyCodeViolation("CODEBUDDY_LOGIN_FACT_FIELDS_INVALID")
        if accepted["profile_id"] != access_profile:
            raise CodeBuddyCodeViolation("CODEBUDDY_LOGIN_PROFILE_MISMATCH")
        if accepted["status"] != "LOGIN_USER_CONFIRMED_SUCCESS":
            raise CodeBuddyCodeViolation("CODEBUDDY_LOGIN_NOT_CONFIRMED")
        if accepted["source"] != "USER_COORDINATION_UPDATE":
            raise CodeBuddyCodeViolation("CODEBUDDY_LOGIN_SOURCE_INVALID")
        _parse_time(accepted["confirmed_at"], "CODEBUDDY_LOGIN_TIME_INVALID")
        if accepted["login_domain"] != "NOT_SAFELY_OBSERVED":
            raise CodeBuddyCodeViolation("CODEBUDDY_LOGIN_DOMAIN_OVERCLAIMED")
        if accepted["credential_material_read"] is not False:
            raise CodeBuddyCodeViolation("CODEBUDDY_CREDENTIAL_READ_FORBIDDEN")
        if accepted["entitlement_status"] != "ENTITLEMENT_UNVERIFIED/NOT_REQUIRED_FOR_Q2":
            raise CodeBuddyCodeViolation("CODEBUDDY_ENTITLEMENT_STATUS_INVALID")
        _secret_scan(accepted)
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
            raise CodeBuddyCodeViolation(
                "CALL_SIDECAR_SCHEMA_INVALID",
                [
                    f"{'/'.join(str(x) for x in item.absolute_path) or '$'}: {item.message}"
                    for item in errors[:20]
                ],
            )
        _secret_scan(sidecar)
        mismatches: list[str] = []
        expected = {
            "schema_version": "1.0",
            "record_type": "call_binding_receipt",
            "binding_mode": "PROSPECTIVE_STRICT",
            "phase_id": "Execution",
            "task_id": "Integration",
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
            raise CodeBuddyCodeViolation("CALL_SIDECAR_BINDING_MISMATCH", sorted(set(mismatches)))
        return sidecar

    def _request_binding_sha256(
        self, argv: Sequence[str], requested_model: str, behavior_hash: str
    ) -> str:
        positions = [i for i, item in enumerate(argv) if item == "--model"]
        if len(positions) != 1 or positions[0] + 1 >= len(argv):
            raise CodeBuddyCodeViolation("CODEBUDDY_REQUEST_MODEL_ARGV_INVALID")
        if argv[positions[0] + 1] != requested_model:
            raise CodeBuddyCodeViolation("CODEBUDDY_REQUEST_MODEL_BINDING_MISMATCH")
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

    def build_invocation(
        self,
        workspace: JobWorkspace,
        *,
        attempt_id: str,
        model: str,
        access_profile: str,
        prompt: bytes,
        output_schema: Mapping[str, Any],
        pre_send_sidecar: Mapping[str, Any],
        login_fact: Mapping[str, Any],
        environment: Mapping[str, str],
        timeout_seconds: float,
        raw_cap_bytes: int,
        output_mode: str = "stream-json",
    ) -> CodeBuddyCodeInvocation:
        if model not in self.policy.allowed_models:
            raise CodeBuddyCodeViolation("CODEBUDDY_MODEL_NOT_ALLOWED", [model])
        if access_profile not in self.policy.allowed_profiles:
            raise CodeBuddyCodeViolation("CODEBUDDY_PROFILE_NOT_ALLOWED", [access_profile])
        if not prompt or len(prompt) > 1024 * 1024:
            raise CodeBuddyCodeViolation("CODEBUDDY_PROMPT_SIZE_INVALID")
        if timeout_seconds <= 0 or timeout_seconds > self.policy.max_timeout_seconds:
            raise CodeBuddyCodeViolation("CODEBUDDY_TIMEOUT_POLICY_INVALID")
        if raw_cap_bytes <= 0 or raw_cap_bytes > self.policy.max_raw_cap_bytes:
            raise CodeBuddyCodeViolation("CODEBUDDY_RAW_CAP_POLICY_INVALID")
        if output_mode not in {"json", "stream-json"}:
            raise CodeBuddyCodeViolation("CODEBUDDY_OUTPUT_MODE_NOT_ALLOWED")
        schema = immutable_copy(output_schema)
        Draft202012Validator.check_schema(schema)
        schema_hash = canonical_sha256(schema)
        prompt_hash = hashlib.sha256(prompt).hexdigest().upper()
        behavior_hash = self.behavior_hash(
            model=model,
            output_contract_sha256=schema_hash,
            access_profile=access_profile,
            output_mode=output_mode,
        )
        accepted_sidecar = self._validate_sidecar(
            pre_send_sidecar,
            attempt_id=attempt_id,
            model=model,
            access_profile=access_profile,
            prompt_sha256=prompt_hash,
            output_contract_sha256=schema_hash,
            behavior_hash=behavior_hash,
        )
        login_fact_hash = self._validate_login_fact(
            login_fact, access_profile=access_profile
        )
        expected_names = set(self.policy.profile_environment_names[access_profile])
        if set(environment) != expected_names:
            raise CodeBuddyCodeViolation("CODEBUDDY_ENVIRONMENT_SET_MISMATCH")
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items()):
            raise CodeBuddyCodeViolation("CODEBUDDY_ENVIRONMENT_VALUE_INVALID")
        expected_runtime = (workspace.scratch_root / "codebuddy-runtime").resolve(strict=False)
        actual_runtime = Path(environment.get("CODEBUDDY_RUNTIME_DIR", "")).resolve(strict=False)
        if actual_runtime != expected_runtime:
            raise CodeBuddyCodeViolation("CODEBUDDY_RUNTIME_OUTSIDE_WORKSPACE")
        workspace.assert_write_path(actual_runtime)
        actual_runtime.mkdir(exist_ok=True)
        declared_runtime_write = (str(actual_runtime),)
        for name in ("TEMP", "TMP"):
            if name in environment and Path(environment[name]).resolve(strict=False) != workspace.scratch_root.resolve(strict=False):
                raise CodeBuddyCodeViolation("CODEBUDDY_TEMP_OUTSIDE_WORKSPACE", [name])

        argv_parts = [
            self.policy.launcher,
            *self.policy.command_prefix,
            "-p",
            "--output-format",
            output_mode,
            "--model",
            model,
            "--tools",
            "",
            "--permission-mode",
            "plan",
            "--strict-mcp-config",
            "--no-session-persistence",
        ]
        if output_mode == "json":
            argv_parts.extend(
                ["--json-schema", canonical_json_bytes(schema).decode("utf-8")]
            )
        argv = tuple(argv_parts)
        if any(item in self.FORBIDDEN_FLAGS for item in argv):
            raise CodeBuddyCodeViolation("CODEBUDDY_FORBIDDEN_FLAG")
        if "--yolo" in argv or "--worktree" in argv or "bypassPermissions" in argv:
            raise CodeBuddyCodeViolation("CODEBUDDY_FORBIDDEN_FLAG")
        if argv[argv.index("--tools") + 1] != "" or argv[argv.index("--permission-mode") + 1] != "plan":
            raise CodeBuddyCodeViolation("CODEBUDDY_PERMISSION_POLICY_INVALID")
        for credential_name in ("CODEBUDDY_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            if environment.get(credential_name) and environment[credential_name] in argv:
                raise CodeBuddyCodeViolation("CODEBUDDY_CREDENTIAL_IN_ARGV_FORBIDDEN")
        binding_hash = self._request_binding_sha256(argv, model, behavior_hash)
        spec = ProcessSpec(
            adapter_id=self.ADAPTER_ID,
            attempt_id=attempt_id,
            argv=tuple(argv),
            cwd=str(workspace.scratch_root),
            environment=dict(environment),
            environment_allowlist=tuple(sorted(self.policy.environment_allowlist)),
            timeout_seconds=timeout_seconds,
            raw_cap_bytes=raw_cap_bytes,
            stdin_bytes=prompt,
            declared_read_paths=(
                self.policy.launcher,
                *self.policy.command_prefix,
            ),
            declared_write_paths=declared_runtime_write,
            component_bindings=bindings_from_policy(
                self.policy,
                ("launcher_sha256", "wrapper_sha256", "entrypoint_sha256", "help_sha256", "runner_sha256", "model_catalog_sha256"),
            ),
        )
        return CodeBuddyCodeInvocation(
            process_spec=spec,
            behavior_hash=behavior_hash,
            prompt_sha256=prompt_hash,
            output_contract_sha256=schema_hash,
            pre_send_sidecar_sha256=hashlib.sha256(
                canonical_json_bytes(accepted_sidecar)
            ).hexdigest().upper(),
            requested_model=model,
            model_allowlist_sha256=self.model_allowlist_sha256(),
            request_model_binding_sha256=binding_hash,
            access_profile=access_profile,
            login_fact_sha256=login_fact_hash,
        )

    def parse_json_output(
        self,
        stdout: bytes,
        *,
        invocation: CodeBuddyCodeInvocation,
    ) -> dict[str, Any]:
        """Parse CodeBuddy ``--output-format json`` without trusting prose identity."""

        if ANSI_RE.search(stdout):
            raise CodeBuddyCodeViolation("CODEBUDDY_JSON_ANSI_FORBIDDEN")
        try:
            value = json.loads(stdout.decode("utf-8", errors="strict"))
        except UnicodeDecodeError as exc:
            raise CodeBuddyCodeViolation("CODEBUDDY_JSON_UTF8_INVALID", [str(exc.start)]) from exc
        except json.JSONDecodeError as exc:
            raise CodeBuddyCodeViolation("CODEBUDDY_JSON_INVALID", [str(exc.pos)]) from exc
        if not isinstance(value, Mapping) or value.get("type") != "result":
            raise CodeBuddyCodeViolation("CODEBUDDY_JSON_RESULT_INVALID")
        structured = value.get("structured_output")
        if not isinstance(structured, Mapping):
            raise CodeBuddyCodeViolation("CODEBUDDY_STRUCTURED_OUTPUT_MISSING")
        reported_model = value.get("model") if isinstance(value.get("model"), str) else None
        if reported_model == invocation.requested_model:
            identity_grade = "DIRECT_EVENT_MATCH"
        elif reported_model is None:
            identity_grade = "PROVIDER_NOT_EXPOSED"
        else:
            identity_grade = "CONFLICT"
        expected_binding = self._request_binding_sha256(
            invocation.process_spec.argv,
            invocation.requested_model,
            invocation.behavior_hash,
        )
        if invocation.request_model_binding_sha256 != expected_binding:
            raise CodeBuddyCodeViolation("CODEBUDDY_REQUEST_MODEL_BINDING_MISMATCH")
        usage = value.get("usage") if isinstance(value.get("usage"), Mapping) else {}
        input_tokens = _integer_or_none(usage.get("input_tokens"))
        output_tokens = _integer_or_none(usage.get("output_tokens"))
        token_usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": None,
            "total_tokens": (
                input_tokens + output_tokens
                if input_tokens is not None and output_tokens is not None
                else None
            ),
        }
        return {
            "parser_version": "DEEPCODE_ADAPTER_CODEBUDDY_JSON_SCHEMA_V1",
            "event_count": 1,
            "event_types": ["result"],
            "events_sha256": hashlib.sha256(stdout).hexdigest().upper(),
            "finish_status": "SUCCESS" if value.get("subtype") == "success" else "ERROR",
            "external_request_started": False,
            "fixture_process_started": True,
            "requested_model": invocation.requested_model,
            "returned_model": reported_model,
            "model_identity_grade": identity_grade,
            "model_text_self_report_used": False,
            "request_model_binding_status": "EXACT_ARGV_AND_FROZEN_CONTROL_PLANE",
            "request_model_binding_sha256": expected_binding,
            "model_allowlist_sha256": self.model_allowlist_sha256(),
            "usage_status": "ACTUAL" if token_usage["total_tokens"] is not None else "UNKNOWN",
            "token_usage": token_usage,
            "final_message": json.dumps(structured, ensure_ascii=False, separators=(",", ":")),
        }

    def parse_event_stream(
        self,
        stdout: bytes,
        *,
        invocation: CodeBuddyCodeInvocation,
    ) -> dict[str, Any]:
        if ANSI_RE.search(stdout):
            raise CodeBuddyCodeViolation("CODEBUDDY_STREAM_ANSI_FORBIDDEN")
        try:
            text = stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CodeBuddyCodeViolation("CODEBUDDY_STREAM_UTF8_INVALID", [str(exc.start)]) from exc
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CodeBuddyCodeViolation("CODEBUDDY_STREAM_JSON_INVALID", [f"line={line_number}"]) from exc
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise CodeBuddyCodeViolation("CODEBUDDY_STREAM_EVENT_INVALID", [f"line={line_number}"])
            events.append(item)
        if not events:
            raise CodeBuddyCodeViolation("CODEBUDDY_STREAM_EMPTY")
        init_events = [
            x for x in events
            if x.get("type") == "system" and x.get("subtype") == "init"
        ]
        terminals = [x for x in events if x.get("type") == "result"]
        if len(init_events) != 1 or init_events[0] is not events[0]:
            raise CodeBuddyCodeViolation("CODEBUDDY_STREAM_INIT_INVALID")
        if len(terminals) != 1 or terminals[0] is not events[-1]:
            raise CodeBuddyCodeViolation("CODEBUDDY_STREAM_TERMINAL_INVALID")
        allowed_event_types = {"system", "user", "assistant", "result"}
        unknown_event_types = sorted({str(x.get("type")) for x in events} - allowed_event_types)
        if unknown_event_types:
            if any(value in {"tool", "tool_use", "tool_result"} for value in unknown_event_types):
                raise CodeBuddyCodeViolation("CODEBUDDY_TOOL_EVENT_FORBIDDEN")
            raise CodeBuddyCodeViolation("CODEBUDDY_STREAM_EVENT_TYPE_UNKNOWN", unknown_event_types)
        if any(x.get("type") in {"tool", "tool_use", "tool_result"} for x in events):
            raise CodeBuddyCodeViolation("CODEBUDDY_TOOL_EVENT_FORBIDDEN")

        init = init_events[0]
        terminal = terminals[0]
        reported_model = init.get("model") if isinstance(init.get("model"), str) else None
        if reported_model == invocation.requested_model:
            identity_grade = "DIRECT_EVENT_MATCH"
        elif reported_model is None:
            identity_grade = "PROVIDER_NOT_EXPOSED"
        else:
            identity_grade = "CONFLICT"
        expected_binding = self._request_binding_sha256(
            invocation.process_spec.argv,
            invocation.requested_model,
            invocation.behavior_hash,
        )
        if invocation.request_model_binding_sha256 != expected_binding:
            raise CodeBuddyCodeViolation("CODEBUDDY_REQUEST_MODEL_BINDING_MISMATCH")
        if invocation.model_allowlist_sha256 != self.model_allowlist_sha256():
            raise CodeBuddyCodeViolation("CODEBUDDY_MODEL_ALLOWLIST_BINDING_MISMATCH")

        assistant_parts = []
        for event in events:
            message = event.get("message")
            if event.get("type") != "assistant" or not isinstance(message, Mapping):
                continue
            content = message.get("content")
            if isinstance(content, str):
                assistant_parts.append(content)
            elif isinstance(content, list):
                text_parts = [
                    item.get("text")
                    for item in content
                    if isinstance(item, Mapping)
                    and item.get("type") == "text"
                    and isinstance(item.get("text"), str)
                ]
                if text_parts:
                    assistant_parts.append("".join(text_parts))
        final_message = assistant_parts[-1] if assistant_parts else None
        stats = terminal.get("usage") if isinstance(terminal.get("usage"), Mapping) else {}
        input_tokens = _integer_or_none(stats.get("input_tokens"))
        output_tokens = _integer_or_none(stats.get("output_tokens"))
        total_tokens = _integer_or_none(stats.get("total_tokens"))
        usage_status = "UNKNOWN"
        token_usage = {
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "total_tokens": None,
        }
        if input_tokens is not None and output_tokens is not None:
            computed = input_tokens + output_tokens
            if total_tokens is not None and total_tokens != computed:
                raise CodeBuddyCodeViolation("CODEBUDDY_TOKEN_TOTAL_CONFLICT")
            token_usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "reasoning_tokens": None,
                "total_tokens": computed,
            }
            usage_status = "ACTUAL"
        status = terminal.get("subtype")
        return {
            "parser_version": self.PARSER_VERSION,
            "event_count": len(events),
            "event_types": [x["type"] for x in events],
            "events_sha256": hashlib.sha256(stdout).hexdigest().upper(),
            "finish_status": "SUCCESS" if status == "success" else "ERROR",
            "external_request_started": False,
            "fixture_process_started": True,
            "requested_model": invocation.requested_model,
            "returned_model": reported_model,
            "model_identity_grade": identity_grade,
            "model_text_self_report_used": False,
            "request_model_binding_status": "EXACT_ARGV_AND_FROZEN_CONTROL_PLANE",
            "request_model_binding_sha256": expected_binding,
            "model_allowlist_sha256": self.model_allowlist_sha256(),
            "usage_status": usage_status,
            "token_usage": token_usage,
            "final_message": final_message,
        }

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
        elif event_summary.get("finish_status") != "SUCCESS":
            error_code = "CODEBUDDY_RESULT_ERROR"
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
                    warnings = _schema_errors(schema, payload)
                    if warnings:
                        payload = {}
                        error_code = "OUTPUT_SCHEMA_INVALID"
        value = base_object(
            "NormalizedResult",
            f"normalizedresult_{process['job_id']}_{process['attempt_id']}",
            producer="DEEPCODE_ADAPTER_CODEBUDDY_CODE_ADAPTER",
            single_writer="DEEPCODE_ADAPTER_CODEBUDDY_CODE_ADAPTER",
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

    @staticmethod
    def offline_q2_projection(
        normalized: Mapping[str, Any],
        event_summary: Mapping[str, Any],
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if not normalized.get("payload_valid"):
            reasons.append(str(normalized.get("error_code") or "OUTPUT_INVALID"))
        if event_summary.get("finish_status") != "SUCCESS":
            reasons.append("CODEBUDDY_RESULT_NOT_SUCCESS")
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
        invocation: CodeBuddyCodeInvocation,
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
            producer="DEEPCODE_ADAPTER_CODEBUDDY_CODE_ADAPTER",
            single_writer="DEEPCODE_ADAPTER_CODEBUDDY_CODE_ADAPTER",
            consumers=("CHANNEL_ACCOUNTING_RUNTIME_LOG", "MODEL_EVALUATION", "ARTIFACT_REGISTRY"),
            clock=self._clock,
            source_evidence_refs=(process["object_id"],),
            extensions={
                "parser_version": event_summary.get("parser_version"),
                "events_sha256": event_summary.get("events_sha256"),
                "request_model_binding_sha256": invocation.request_model_binding_sha256,
                "model_allowlist_sha256": invocation.model_allowlist_sha256,
                "login_fact_sha256": invocation.login_fact_sha256,
                "entitlement_status": "ENTITLEMENT_UNVERIFIED/NOT_REQUIRED_FOR_Q2",
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
                "provider": "Tencent Cloud",
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
