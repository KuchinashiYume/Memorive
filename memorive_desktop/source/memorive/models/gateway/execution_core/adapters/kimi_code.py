"""Runs the Kimi CLI as a command-line model. Not used for the app's own requests: the window runs
command-line models through memorive/models/validation/ (see DEVELOPING.md, "Code the
window does not start").

The adapter binds an immutable launcher/entrypoint, exact ``--model`` argv,
frozen model catalog, access profile and entitlement snapshot.  Generated
answer text is never identity evidence.  It does not retry, fall back from an
expired subscription to a paid API key, activate a route, or write RUNTIME_LOG.

What it shares with the other command-line adapters (sidecar, entitlement or login checks, result
normalization, the receipt) is in cli_common.py; this module keeps what is specific to the
Kimi Code: its policy, manifest, behavior hash, invocation, stream parsing and Q2 projection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from memorive.models.gateway.execution_core.contracts import (
    base_object,
    canonical_json_bytes,
    canonical_sha256,
    immutable_copy,
    validate_initialization_object,
)
from memorive.models.gateway.execution_core.runner import ProcessSpec
from memorive.models.gateway.execution_core.component_binding import bindings_from_policy
from memorive.models.gateway.execution_core.workspace import JobWorkspace
from memorive.models.gateway.execution_core.adapters.cli_common import (
    ANSI_RE,
    CommandLineAdapter,
    CommandLineAdapterViolation,
    SHA256_RE,
)


SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


class KimiCodeViolation(CommandLineAdapterViolation):
    """Errors of the Kimi Code adapter."""


@dataclass(frozen=True)
class KimiCodePolicy:
    launcher: str
    command_prefix: tuple[str, ...]
    exact_version: str
    launcher_sha256: str
    wrapper_sha256: str
    entrypoint_sha256: str
    help_sha256: str
    git_bash_path: str
    git_bash_sha256: str
    runner_sha256: str
    model_catalog_sha256: str
    allowed_models: tuple[str, ...]
    allowed_profiles: tuple[str, ...]
    profile_environment_names: Mapping[str, tuple[str, ...]]
    profile_evidence_classes: Mapping[str, str]
    environment_allowlist: tuple[str, ...]
    subscription_profile: str
    api_profile: str
    subscription_home: str
    route_id: str
    provider_region: str
    egress_identity: str
    max_entitlement_ttl_seconds: int = 86400
    max_timeout_seconds: float = 900.0
    max_raw_cap_bytes: int = 8 * 1024 * 1024
    component_paths: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        launcher = Path(self.launcher)
        if not launcher.is_absolute() or not launcher.is_file():
            raise KimiCodeViolation("KIMI_LAUNCHER_INVALID")
        for field, value in (
            ("launcher_sha256", self.launcher_sha256),
            ("wrapper_sha256", self.wrapper_sha256),
            ("entrypoint_sha256", self.entrypoint_sha256),
            ("help_sha256", self.help_sha256),
            ("git_bash_sha256", self.git_bash_sha256),
            ("runner_sha256", self.runner_sha256),
            ("model_catalog_sha256", self.model_catalog_sha256),
        ):
            if not SHA256_RE.fullmatch(value.upper()):
                raise KimiCodeViolation("KIMI_COMPATIBILITY_HASH_INVALID", [field])
        if hashlib.sha256(launcher.read_bytes()).hexdigest().upper() != self.launcher_sha256.upper():
            raise KimiCodeViolation("KIMI_LAUNCHER_HASH_MISMATCH")
        git_bash = Path(self.git_bash_path)
        if not git_bash.is_absolute() or not git_bash.is_file():
            raise KimiCodeViolation("KIMI_GIT_BASH_INVALID")
        if hashlib.sha256(git_bash.read_bytes()).hexdigest().upper() != self.git_bash_sha256.upper():
            raise KimiCodeViolation("KIMI_GIT_BASH_HASH_MISMATCH")
        if not re.fullmatch(r"\d+\.\d+\.\d+", self.exact_version):
            raise KimiCodeViolation("KIMI_VERSION_INVALID")
        if not self.allowed_models or any(not SAFE_MODEL_RE.fullmatch(x) for x in self.allowed_models):
            raise KimiCodeViolation("KIMI_MODEL_ALLOWLIST_INVALID")
        if set(self.allowed_profiles) != {self.subscription_profile, self.api_profile}:
            raise KimiCodeViolation("KIMI_PROFILE_ALLOWLIST_INVALID")
        if set(self.profile_environment_names) != set(self.allowed_profiles):
            raise KimiCodeViolation("KIMI_PROFILE_ENVIRONMENT_INVALID")
        if set(self.profile_evidence_classes) != set(self.allowed_profiles):
            raise KimiCodeViolation("KIMI_PROFILE_EVIDENCE_CLASS_INVALID")
        allowed_env = set(self.environment_allowlist)
        if len(allowed_env) != len(self.environment_allowlist):
            raise KimiCodeViolation("KIMI_ENV_ALLOWLIST_DUPLICATE")
        for profile, names in self.profile_environment_names.items():
            if len(set(names)) != len(names) or not set(names).issubset(allowed_env):
                raise KimiCodeViolation("KIMI_PROFILE_ENVIRONMENT_INVALID", [profile])
            if "KIMI_CODE_HOME" not in names:
                raise KimiCodeViolation("KIMI_HOME_ENV_REQUIRED", [profile])
        subscription_names = set(self.profile_environment_names[self.subscription_profile])
        api_names = set(self.profile_environment_names[self.api_profile])
        secret_env_names = {"MOONSHOT_API_KEY", "KIMI_API_KEY", "OPENAI_API_KEY"}
        if secret_env_names & subscription_names:
            raise KimiCodeViolation("KIMI_SUBSCRIPTION_API_FALLBACK_ENV_FORBIDDEN")
        if secret_env_names & api_names:
            raise KimiCodeViolation("KIMI_PLAINTEXT_CREDENTIAL_ENV_FORBIDDEN")
        subscription_home = Path(self.subscription_home)
        if not subscription_home.is_absolute() or not subscription_home.is_dir():
            raise KimiCodeViolation("KIMI_SUBSCRIPTION_HOME_INVALID")
        if self.max_entitlement_ttl_seconds <= 0:
            raise KimiCodeViolation("KIMI_ENTITLEMENT_TTL_POLICY_INVALID")
        if self.max_timeout_seconds <= 0 or self.max_raw_cap_bytes <= 0:
            raise KimiCodeViolation("KIMI_RESOURCE_POLICY_INVALID")


@dataclass(frozen=True)
class KimiCodeInvocation:
    process_spec: ProcessSpec
    behavior_hash: str
    prompt_sha256: str
    output_contract_sha256: str
    pre_send_sidecar_sha256: str
    requested_model: str
    model_allowlist_sha256: str
    request_model_binding_sha256: str
    access_profile: str
    entitlement_snapshot_sha256: str | None


class KimiCodeAdapter(CommandLineAdapter):
    ADAPTER_ID = "kimi_code"
    VIOLATION = KimiCodeViolation
    CODE_PREFIX = "KIMI"
    PRODUCER = "KIMI_ADAPTER_KIMI_CODE_ADAPTER"
    SIDECAR_TASK_ID = "Analysis"
    PROVIDER = "Moonshot AI"
    RESULT_SUCCESS_STATUS = "STREAM_COMPLETE_EXIT_PENDING"
    RESULT_ERROR_SUFFIX = "STREAM_INCOMPLETE"
    PARSER_VERSION = "KIMI_ADAPTER_KIMI_STREAM_JSON_V1"
    NORMALIZATION_VERSION = "KIMI_ADAPTER_KIMI_FINAL_MESSAGE_V1"
    FORBIDDEN_FLAGS = {
        "--yolo",
        "-y",
        "--session",
        "--continue",
        "--resume",
        "--auto",
    }


    def build_manifest(self) -> dict[str, Any]:
        value = base_object(
            "AdapterManifest",
            "adaptermanifest_kimi_code",
            producer="KIMI_ADAPTER_KIMI_CODE_ADAPTER",
            single_writer="KIMI_ADAPTER_KIMI_CODE_ADAPTER",
            consumers=("Execution_DISCOVERY_RUNNER", "Execution_JOB_RUNNER", "CHANNEL_ACCOUNTING_RUNTIME_LOG"),
            clock=self._clock,
            source_evidence_refs=(
                f"kimi-launcher-sha256:{self.policy.launcher_sha256.upper()}",
                f"kimi-wrapper-sha256:{self.policy.wrapper_sha256.upper()}",
                f"kimi-entrypoint-sha256:{self.policy.entrypoint_sha256.upper()}",
                f"kimi-help-sha256:{self.policy.help_sha256.upper()}",
                f"git-bash-sha256:{self.policy.git_bash_sha256.upper()}",
                f"model-catalog-sha256:{self.policy.model_catalog_sha256.upper()}",
            ),
            extensions={
                "command_prefix_sha256": canonical_sha256(
                    [self.policy.launcher, *self.policy.command_prefix]
                ),
                "runner_sha256": self.policy.runner_sha256.upper(),
                "access_profiles": list(self.policy.allowed_profiles),
                "entitlement_ttl_seconds": self.policy.max_entitlement_ttl_seconds,
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
                "version_discovery": "kimi --version",
                "auth_discovery": "CLI_MANAGED_DEVICE_LOGIN_METADATA_ONLY",
                "session_discovery": "KIMI_CODE_HOME_BOUND_PREVIOUS_SESSION_FORBIDDEN",
                "model_discovery": "EXACT_ARGV_PLUS_FROZEN_OFFICIAL_CATALOG",
                "capability_discovery": "kimi --help frozen hash",
                "permission_flags": ["NONINTERACTIVE_IMPLICIT_AUTO", "ROUTE_DISABLED", "TOOL_MESSAGES_REJECTED"],
                "workspace_flags": ["ISOLATED_CWD", "PREVIOUS_SESSION_FORBIDDEN", "KIMI_CODE_HOME_BOUND", "GIT_BASH_PINNED"],
                "network_flags": ["DEVICE_OAUTH_ONLY", "PLAINTEXT_PROVIDER_CONFIG_BLOCKED"],
                "output_modes": ["STREAM_JSON_MESSAGES", "STRICT_ASSISTANT_JSON"],
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
                "access_profile": access_profile,
                "permission_mode": "KIMI_IMPLICIT_AUTO_NONINTERACTIVE_ROUTE_DISABLED",
                "git_bash_sha256": self.policy.git_bash_sha256.upper(),
                "yolo": False,
                "worktree": False,
                "previous_session": False,
                "plaintext_provider_config": "BLOCKED",
                "model_text_self_report_used": False,
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
        entitlement_snapshot: Mapping[str, Any] | None,
        environment: Mapping[str, str],
        timeout_seconds: float,
        raw_cap_bytes: int,
    ) -> KimiCodeInvocation:
        if model not in self.policy.allowed_models:
            raise KimiCodeViolation("KIMI_MODEL_NOT_ALLOWED", [model])
        if access_profile not in self.policy.allowed_profiles:
            raise KimiCodeViolation("KIMI_PROFILE_NOT_ALLOWED", [access_profile])
        if access_profile == self.policy.api_profile:
            raise KimiCodeViolation("KIMI_PLAINTEXT_PROVIDER_CONFIG_BLOCKED")
        if not prompt or len(prompt) > 1024 * 1024:
            raise KimiCodeViolation("KIMI_PROMPT_SIZE_INVALID")
        if timeout_seconds <= 0 or timeout_seconds > self.policy.max_timeout_seconds:
            raise KimiCodeViolation("KIMI_TIMEOUT_POLICY_INVALID")
        if raw_cap_bytes <= 0 or raw_cap_bytes > self.policy.max_raw_cap_bytes:
            raise KimiCodeViolation("KIMI_RAW_CAP_POLICY_INVALID")
        schema = immutable_copy(output_schema)
        Draft202012Validator.check_schema(schema)
        schema_hash = canonical_sha256(schema)
        prompt_hash = hashlib.sha256(prompt).hexdigest().upper()
        behavior_hash = self.behavior_hash(
            model=model,
            output_contract_sha256=schema_hash,
            access_profile=access_profile,
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
        entitlement_hash = self._validate_entitlement(
            entitlement_snapshot, access_profile=access_profile
        )
        expected_names = set(self.policy.profile_environment_names[access_profile])
        if set(environment) != expected_names:
            raise KimiCodeViolation("KIMI_ENVIRONMENT_SET_MISMATCH")
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items()):
            raise KimiCodeViolation("KIMI_ENVIRONMENT_VALUE_INVALID")
        if {"MOONSHOT_API_KEY", "KIMI_API_KEY", "OPENAI_API_KEY"} & set(environment):
            raise KimiCodeViolation("KIMI_SUBSCRIPTION_API_FALLBACK_FORBIDDEN")
        expected_home = Path(self.policy.subscription_home).resolve(strict=True)
        actual_home = Path(environment.get("KIMI_CODE_HOME", "")).resolve(strict=True)
        if actual_home != expected_home:
            raise KimiCodeViolation("KIMI_SUBSCRIPTION_HOME_MISMATCH")
        workspace.assert_read_path(actual_home)
        declared_home_read = (str(actual_home), str(Path(self.policy.git_bash_path).resolve()))
        declared_home_write: tuple[str, ...] = ()
        for name in ("TEMP", "TMP"):
            if name in environment and Path(environment[name]).resolve(strict=False) != workspace.scratch_root.resolve(strict=False):
                raise KimiCodeViolation("KIMI_TEMP_OUTSIDE_WORKSPACE", [name])

        argv = (
            self.policy.launcher,
            *self.policy.command_prefix,
            "--prompt",
            "",
            "--output-format",
            "stream-json",
            "--model",
            model,
        )
        if any(item in self.FORBIDDEN_FLAGS for item in argv):
            raise KimiCodeViolation("KIMI_FORBIDDEN_FLAG")
        if "--yolo" in argv or "--auto" in argv or "--continue" in argv:
            raise KimiCodeViolation("KIMI_FORBIDDEN_FLAG")
        for credential_name in ("MOONSHOT_API_KEY", "KIMI_API_KEY", "OPENAI_API_KEY"):
            if environment.get(credential_name) and environment[credential_name] in argv:
                raise KimiCodeViolation("KIMI_CREDENTIAL_IN_ARGV_FORBIDDEN")
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
                *declared_home_read,
            ),
            declared_write_paths=declared_home_write,
            component_bindings=bindings_from_policy(
                self.policy,
                ("launcher_sha256", "wrapper_sha256", "entrypoint_sha256", "help_sha256", "git_bash_sha256", "runner_sha256", "model_catalog_sha256"),
            ),
        )
        return KimiCodeInvocation(
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
            entitlement_snapshot_sha256=entitlement_hash,
        )

    def parse_event_stream(
        self,
        stdout: bytes,
        *,
        invocation: KimiCodeInvocation,
    ) -> dict[str, Any]:
        if ANSI_RE.search(stdout):
            raise KimiCodeViolation("KIMI_STREAM_ANSI_FORBIDDEN")
        try:
            text = stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise KimiCodeViolation("KIMI_STREAM_UTF8_INVALID", [str(exc.start)]) from exc
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise KimiCodeViolation("KIMI_STREAM_JSON_INVALID", [f"line={line_number}"]) from exc
            if not isinstance(item, dict) or not isinstance(item.get("role"), str):
                raise KimiCodeViolation("KIMI_STREAM_MESSAGE_INVALID", [f"line={line_number}"])
            events.append(item)
        if not events:
            raise KimiCodeViolation("KIMI_STREAM_EMPTY")
        if any(x.get("role") != "assistant" for x in events):
            raise KimiCodeViolation("KIMI_NON_ASSISTANT_MESSAGE_FORBIDDEN")
        expected_binding = self._request_binding_sha256(
            invocation.process_spec.argv,
            invocation.requested_model,
            invocation.behavior_hash,
        )
        if invocation.request_model_binding_sha256 != expected_binding:
            raise KimiCodeViolation("KIMI_REQUEST_MODEL_BINDING_MISMATCH")
        if invocation.model_allowlist_sha256 != self.model_allowlist_sha256():
            raise KimiCodeViolation("KIMI_MODEL_ALLOWLIST_BINDING_MISMATCH")

        assistant_parts = [
            event.get("content") for event in events
            if isinstance(event.get("content"), str)
        ]
        final_message = assistant_parts[-1] if assistant_parts else None
        token_usage = {
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "total_tokens": None,
        }
        return {
            "parser_version": self.PARSER_VERSION,
            "event_count": len(events),
            "event_types": [x["role"] for x in events],
            "events_sha256": hashlib.sha256(stdout).hexdigest().upper(),
            "finish_status": "STREAM_COMPLETE_EXIT_PENDING",
            "external_request_started": True,
            "requested_model": invocation.requested_model,
            "returned_model": None,
            "model_identity_grade": "PROVIDER_NOT_EXPOSED",
            "model_text_self_report_used": False,
            "request_model_binding_status": "EXACT_ARGV_AND_FROZEN_CONTROL_PLANE",
            "request_model_binding_sha256": expected_binding,
            "model_allowlist_sha256": self.model_allowlist_sha256(),
            "usage_status": "UNKNOWN",
            "token_usage": token_usage,
            "final_message": final_message,
        }


    @staticmethod
    def offline_q2_projection(
        normalized: Mapping[str, Any],
        event_summary: Mapping[str, Any],
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if not normalized.get("payload_valid"):
            reasons.append(str(normalized.get("error_code") or "OUTPUT_INVALID"))
        if event_summary.get("finish_status") != "STREAM_COMPLETE_EXIT_PENDING":
            reasons.append("KIMI_STREAM_NOT_COMPLETE")
        if event_summary.get("request_model_binding_status") != "EXACT_ARGV_AND_FROZEN_CONTROL_PLANE":
            reasons.append("REQUEST_MODEL_NOT_CONTROL_PLANE_BOUND")
        if event_summary.get("model_identity_grade") != "PROVIDER_NOT_EXPOSED":
            reasons.append("MODEL_IDENTITY_GRADE_INVALID")
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

