"""Runs the Gemini CLI as a command-line model. Not used for the app's own requests: the window runs
command-line models through memorive/models/validation/ (see DEVELOPING.md, "Code the
window does not start").

The adapter binds an immutable launcher/entrypoint, exact ``--model`` argv,
frozen model catalog, access profile and entitlement snapshot.  Generated
answer text is never identity evidence.  It does not retry, fall back from an
expired subscription to a paid API key, activate a route, or write RUNTIME_LOG.

What it shares with the other command-line adapters (sidecar, entitlement or login checks, result
normalization, the receipt) is in cli_common.py; this module keeps what is specific to the
Gemini CLI: its policy, manifest, behavior hash, invocation and stream parsing.
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
    SAFE_MODEL_RE,
    SHA256_RE,
    integer_or_none,
)


class GeminiCliViolation(CommandLineAdapterViolation):
    """Errors of the Gemini CLI adapter."""


@dataclass(frozen=True)
class GeminiCliPolicy:
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
            raise GeminiCliViolation("GEMINI_LAUNCHER_INVALID")
        for field, value in (
            ("launcher_sha256", self.launcher_sha256),
            ("wrapper_sha256", self.wrapper_sha256),
            ("entrypoint_sha256", self.entrypoint_sha256),
            ("help_sha256", self.help_sha256),
            ("runner_sha256", self.runner_sha256),
            ("model_catalog_sha256", self.model_catalog_sha256),
        ):
            if not SHA256_RE.fullmatch(value.upper()):
                raise GeminiCliViolation("GEMINI_COMPATIBILITY_HASH_INVALID", [field])
        if hashlib.sha256(launcher.read_bytes()).hexdigest().upper() != self.launcher_sha256.upper():
            raise GeminiCliViolation("GEMINI_LAUNCHER_HASH_MISMATCH")
        if not re.fullmatch(r"\d+\.\d+\.\d+", self.exact_version):
            raise GeminiCliViolation("GEMINI_VERSION_INVALID")
        if not self.allowed_models or any(not SAFE_MODEL_RE.fullmatch(x) for x in self.allowed_models):
            raise GeminiCliViolation("GEMINI_MODEL_ALLOWLIST_INVALID")
        if set(self.allowed_profiles) != {self.subscription_profile, self.api_profile}:
            raise GeminiCliViolation("GEMINI_PROFILE_ALLOWLIST_INVALID")
        if set(self.profile_environment_names) != set(self.allowed_profiles):
            raise GeminiCliViolation("GEMINI_PROFILE_ENVIRONMENT_INVALID")
        if set(self.profile_evidence_classes) != set(self.allowed_profiles):
            raise GeminiCliViolation("GEMINI_PROFILE_EVIDENCE_CLASS_INVALID")
        allowed_env = set(self.environment_allowlist)
        if len(allowed_env) != len(self.environment_allowlist):
            raise GeminiCliViolation("GEMINI_ENV_ALLOWLIST_DUPLICATE")
        for profile, names in self.profile_environment_names.items():
            if len(set(names)) != len(names) or not set(names).issubset(allowed_env):
                raise GeminiCliViolation("GEMINI_PROFILE_ENVIRONMENT_INVALID", [profile])
            if "GEMINI_CLI_HOME" not in names:
                raise GeminiCliViolation("GEMINI_HOME_ENV_REQUIRED", [profile])
        subscription_names = set(self.profile_environment_names[self.subscription_profile])
        api_names = set(self.profile_environment_names[self.api_profile])
        if {"GEMINI_API_KEY", "GOOGLE_API_KEY"} & subscription_names:
            raise GeminiCliViolation("GEMINI_SUBSCRIPTION_API_FALLBACK_ENV_FORBIDDEN")
        if "GEMINI_API_KEY" not in api_names:
            raise GeminiCliViolation("GEMINI_API_CREDENTIAL_ENV_REQUIRED")
        subscription_home = Path(self.subscription_home)
        if not subscription_home.is_absolute() or not subscription_home.is_dir():
            raise GeminiCliViolation("GEMINI_SUBSCRIPTION_HOME_INVALID")
        if self.max_entitlement_ttl_seconds <= 0:
            raise GeminiCliViolation("GEMINI_ENTITLEMENT_TTL_POLICY_INVALID")
        if self.max_timeout_seconds <= 0 or self.max_raw_cap_bytes <= 0:
            raise GeminiCliViolation("GEMINI_RESOURCE_POLICY_INVALID")


@dataclass(frozen=True)
class GeminiCliInvocation:
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


class GeminiCliAdapter(CommandLineAdapter):
    ADAPTER_ID = "gemini_cli"
    VIOLATION = GeminiCliViolation
    CODE_PREFIX = "GEMINI"
    PRODUCER = "GEMINI_ADAPTER_GEMINI_CLI_ADAPTER"
    SIDECAR_TASK_ID = "Verification"
    PROVIDER = "Google"
    PARSER_VERSION = "GEMINI_ADAPTER_GEMINI_STREAM_JSON_V1"
    NORMALIZATION_VERSION = "GEMINI_ADAPTER_GEMINI_FINAL_MESSAGE_V1"
    FORBIDDEN_FLAGS = {
        "--yolo",
        "-y",
        "--worktree",
        "-w",
        "--skip-trust",
        "--include-directories",
        "--resume",
        "--session-file",
        "--session-id",
        "--acp",
        "--experimental-acp",
        "--raw-output",
        "--accept-raw-output-risk",
    }


    def build_manifest(self) -> dict[str, Any]:
        value = base_object(
            "AdapterManifest",
            "adaptermanifest_gemini_cli",
            producer="GEMINI_ADAPTER_GEMINI_CLI_ADAPTER",
            single_writer="GEMINI_ADAPTER_GEMINI_CLI_ADAPTER",
            consumers=("Execution_DISCOVERY_RUNNER", "Execution_JOB_RUNNER", "CHANNEL_ACCOUNTING_RUNTIME_LOG"),
            clock=self._clock,
            source_evidence_refs=(
                f"gemini-launcher-sha256:{self.policy.launcher_sha256.upper()}",
                f"gemini-wrapper-sha256:{self.policy.wrapper_sha256.upper()}",
                f"gemini-entrypoint-sha256:{self.policy.entrypoint_sha256.upper()}",
                f"gemini-help-sha256:{self.policy.help_sha256.upper()}",
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
                "version_discovery": "gemini --version",
                "auth_discovery": "ENTITLEMENT_SNAPSHOT_OR_API_ENV_NAME_PRESENCE_ONLY",
                "session_discovery": "PROFILE_SEPARATED_NO_SESSION_BODY_READ",
                "model_discovery": "EXACT_ARGV_PLUS_FROZEN_OFFICIAL_CATALOG",
                "capability_discovery": "gemini --help frozen hash",
                "permission_flags": ["APPROVAL_PLAN", "YOLO_FORBIDDEN", "TOOL_EVENTS_REJECTED"],
                "workspace_flags": ["ISOLATED_CWD", "WORKTREE_FORBIDDEN", "GEMINI_HOME_BOUND"],
                "network_flags": ["PROFILE_SEPARATED", "EXPIRED_SUBSCRIPTION_NO_API_FALLBACK"],
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
                "approval_mode": "plan",
                "yolo": False,
                "worktree": False,
                "fallback": "CROSS_PROFILE_FORBIDDEN",
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
    ) -> GeminiCliInvocation:
        if model not in self.policy.allowed_models:
            raise GeminiCliViolation("GEMINI_MODEL_NOT_ALLOWED", [model])
        if access_profile not in self.policy.allowed_profiles:
            raise GeminiCliViolation("GEMINI_PROFILE_NOT_ALLOWED", [access_profile])
        if not prompt or len(prompt) > 1024 * 1024:
            raise GeminiCliViolation("GEMINI_PROMPT_SIZE_INVALID")
        if timeout_seconds <= 0 or timeout_seconds > self.policy.max_timeout_seconds:
            raise GeminiCliViolation("GEMINI_TIMEOUT_POLICY_INVALID")
        if raw_cap_bytes <= 0 or raw_cap_bytes > self.policy.max_raw_cap_bytes:
            raise GeminiCliViolation("GEMINI_RAW_CAP_POLICY_INVALID")
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
            raise GeminiCliViolation("GEMINI_ENVIRONMENT_SET_MISMATCH")
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items()):
            raise GeminiCliViolation("GEMINI_ENVIRONMENT_VALUE_INVALID")
        if access_profile == self.policy.subscription_profile:
            if "GEMINI_API_KEY" in environment or "GOOGLE_API_KEY" in environment:
                raise GeminiCliViolation("GEMINI_SUBSCRIPTION_API_FALLBACK_FORBIDDEN")
            expected_home = Path(self.policy.subscription_home).resolve(strict=True)
            actual_home = Path(environment.get("GEMINI_CLI_HOME", "")).resolve(strict=True)
            if actual_home != expected_home:
                raise GeminiCliViolation("GEMINI_SUBSCRIPTION_HOME_MISMATCH")
            workspace.assert_read_path(actual_home)
            declared_home_read = (str(actual_home),)
            declared_home_write: tuple[str, ...] = ()
        else:
            if not environment.get("GEMINI_API_KEY"):
                raise GeminiCliViolation("GEMINI_API_CREDENTIAL_MISSING")
            expected_home = workspace.scratch_root / "gemini-home"
            actual_home = Path(environment.get("GEMINI_CLI_HOME", "")).resolve(strict=False)
            if actual_home != expected_home.resolve(strict=False):
                raise GeminiCliViolation("GEMINI_API_HOME_OUTSIDE_WORKSPACE")
            workspace.assert_write_path(actual_home)
            actual_home.mkdir(exist_ok=False)
            declared_home_read = ()
            declared_home_write = (str(actual_home),)
        for name in ("TEMP", "TMP"):
            if name in environment and Path(environment[name]).resolve(strict=False) != workspace.scratch_root.resolve(strict=False):
                raise GeminiCliViolation("GEMINI_TEMP_OUTSIDE_WORKSPACE", [name])

        argv = (
            self.policy.launcher,
            *self.policy.command_prefix,
            "--prompt",
            "",
            "--output-format",
            "stream-json",
            "--model",
            model,
            "--approval-mode",
            "plan",
            "--extensions",
            "",
        )
        if any(item in self.FORBIDDEN_FLAGS for item in argv):
            raise GeminiCliViolation("GEMINI_FORBIDDEN_FLAG")
        if "--yolo" in argv or "--worktree" in argv or "auto_edit" in argv:
            raise GeminiCliViolation("GEMINI_FORBIDDEN_FLAG")
        for credential_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            if environment.get(credential_name) and environment[credential_name] in argv:
                raise GeminiCliViolation("GEMINI_CREDENTIAL_IN_ARGV_FORBIDDEN")
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
                ("launcher_sha256", "wrapper_sha256", "entrypoint_sha256", "help_sha256", "runner_sha256", "model_catalog_sha256"),
            ),
        )
        return GeminiCliInvocation(
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
        invocation: GeminiCliInvocation,
    ) -> dict[str, Any]:
        if ANSI_RE.search(stdout):
            raise GeminiCliViolation("GEMINI_STREAM_ANSI_FORBIDDEN")
        try:
            text = stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise GeminiCliViolation("GEMINI_STREAM_UTF8_INVALID", [str(exc.start)]) from exc
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GeminiCliViolation("GEMINI_STREAM_JSON_INVALID", [f"line={line_number}"]) from exc
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise GeminiCliViolation("GEMINI_STREAM_EVENT_INVALID", [f"line={line_number}"])
            events.append(item)
        if not events:
            raise GeminiCliViolation("GEMINI_STREAM_EMPTY")
        init_events = [x for x in events if x.get("type") == "init"]
        terminals = [x for x in events if x.get("type") == "result"]
        if len(init_events) != 1 or init_events[0] is not events[0]:
            raise GeminiCliViolation("GEMINI_STREAM_INIT_INVALID")
        if len(terminals) != 1 or terminals[0] is not events[-1]:
            raise GeminiCliViolation("GEMINI_STREAM_TERMINAL_INVALID")
        if any(x.get("type") in {"tool_use", "tool_result"} for x in events):
            raise GeminiCliViolation("GEMINI_TOOL_EVENT_FORBIDDEN")

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
            raise GeminiCliViolation("GEMINI_REQUEST_MODEL_BINDING_MISMATCH")
        if invocation.model_allowlist_sha256 != self.model_allowlist_sha256():
            raise GeminiCliViolation("GEMINI_MODEL_ALLOWLIST_BINDING_MISMATCH")

        assistant_parts = [
            x.get("content")
            for x in events
            if x.get("type") == "message"
            and x.get("role") == "assistant"
            and isinstance(x.get("content"), str)
        ]
        final_message = assistant_parts[-1] if assistant_parts else None
        stats = terminal.get("stats") if isinstance(terminal.get("stats"), Mapping) else {}
        input_tokens = integer_or_none(stats.get("input_tokens"))
        output_tokens = integer_or_none(stats.get("output_tokens"))
        total_tokens = integer_or_none(stats.get("total_tokens"))
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
                raise GeminiCliViolation("GEMINI_TOKEN_TOTAL_CONFLICT")
            token_usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "reasoning_tokens": None,
                "total_tokens": computed,
            }
            usage_status = "ACTUAL"
        status = terminal.get("status")
        return {
            "parser_version": self.PARSER_VERSION,
            "event_count": len(events),
            "event_types": [x["type"] for x in events],
            "events_sha256": hashlib.sha256(stdout).hexdigest().upper(),
            "finish_status": "SUCCESS" if status == "success" else "ERROR",
            "external_request_started": any(x.get("type") == "message" for x in events),
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


