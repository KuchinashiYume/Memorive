"""Fail-closed Codex CLI thin adapter for P05/T04.

This module only maps a frozen Codex CLI compatibility key onto the accepted
T01/T02/T03 seams.  It does not select routes, retry, activate a profile, write
the real M11 ledger, or read credential values for evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    validate_t01_object,
)
from ..runner import ProcessRun, ProcessSpec
from ..component_binding import bindings_from_policy
from ..workspace import JobWorkspace, WorkspaceViolation


SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
ANSI_RE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SECRET_PREFIXES = ("sk-", "ghp_", "gho_", "github_pat_", "xox", "pat-")
FORBIDDEN_TOOL_EVENT_TYPES = {
    "command_execution",
    "computer_use",
    "dynamic_tool_call",
    "file_change",
    "image_generation",
    "mcp_tool_call",
    "tool_call",
    "view_image",
    "web_search",
}
PROVIDER_EVENT_IDENTITY = "PROVIDER_EVENT"
DIRECT_CLI_REQUEST_BINDING = "DIRECT_CLI_REQUEST_BINDING_V1"
DIRECT_CLI_PROCESS = "direct_cli_process"


class CodexAdapterViolation(ValueError):
    def __init__(self, code: str, details: Sequence[str] = ()):
        self.code = code
        self.details = tuple(details)
        suffix = f": {'; '.join(self.details)}" if self.details else ""
        super().__init__(f"{code}{suffix}")


@dataclass(frozen=True)
class CodexCliPolicy:
    executable: str
    command_prefix: tuple[str, ...]
    exact_version: str
    binary_sha256: str
    exec_help_sha256: str
    runner_sha256: str
    allowed_models: tuple[str, ...]
    allowed_reasoning_efforts: tuple[str, ...]
    allowed_access_profiles: tuple[str, ...]
    environment_allowlist: tuple[str, ...]
    api_environment_names: tuple[str, ...]
    subscription_environment_names: tuple[str, ...]
    subscription_codex_home: str
    api_provider_id: str
    subscription_provider_id: str
    route_id: str
    provider_region: str
    egress_identity: str
    max_timeout_seconds: float = 900.0
    max_raw_cap_bytes: int = 8 * 1024 * 1024
    sandbox_mode: str = "read-only"
    component_paths: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        executable = Path(self.executable)
        if not executable.is_absolute() or not executable.is_file():
            raise CodexAdapterViolation("CODEX_EXECUTABLE_INVALID")
        for name, value in (
            ("binary_sha256", self.binary_sha256),
            ("exec_help_sha256", self.exec_help_sha256),
            ("runner_sha256", self.runner_sha256),
        ):
            if not SHA256_RE.fullmatch(value.upper()):
                raise CodexAdapterViolation("CODEX_COMPATIBILITY_HASH_INVALID", [name])
        if hashlib.sha256(executable.read_bytes()).hexdigest().upper() != self.binary_sha256.upper():
            raise CodexAdapterViolation("CODEX_BINARY_HASH_MISMATCH")
        if not self.exact_version.startswith("codex-cli "):
            raise CodexAdapterViolation("CODEX_VERSION_INVALID")
        if self.sandbox_mode != "read-only":
            raise CodexAdapterViolation("CODEX_SANDBOX_POLICY_INVALID")
        if not self.allowed_models or any(not SAFE_MODEL_RE.fullmatch(item) for item in self.allowed_models):
            raise CodexAdapterViolation("CODEX_MODEL_ALLOWLIST_INVALID")
        if not self.allowed_reasoning_efforts:
            raise CodexAdapterViolation("CODEX_REASONING_ALLOWLIST_INVALID")
        if not self.allowed_access_profiles:
            raise CodexAdapterViolation("CODEX_PROFILE_ALLOWLIST_INVALID")
        recognized_profiles = {"openai_api", "codex_chatgpt_session"}
        if not set(self.allowed_access_profiles).issubset(recognized_profiles):
            raise CodexAdapterViolation("CODEX_PROFILE_ALLOWLIST_INVALID")
        if len(set(self.environment_allowlist)) != len(self.environment_allowlist):
            raise CodexAdapterViolation("CODEX_ENV_ALLOWLIST_DUPLICATE")
        for profile, names in (
            ("openai_api", self.api_environment_names),
            ("codex_chatgpt_session", self.subscription_environment_names),
        ):
            if len(set(names)) != len(names) or not set(names).issubset(self.environment_allowlist):
                raise CodexAdapterViolation("CODEX_PROFILE_ENVIRONMENT_INVALID", [profile])
            if "CODEX_HOME" not in names:
                raise CodexAdapterViolation("CODEX_HOME_ENV_REQUIRED", [profile])
        if "OPENAI_API_KEY" not in self.api_environment_names:
            raise CodexAdapterViolation("CODEX_API_CREDENTIAL_ENV_REQUIRED")
        if "OPENAI_API_KEY" in self.subscription_environment_names:
            raise CodexAdapterViolation("CODEX_SUBSCRIPTION_API_CREDENTIAL_FORBIDDEN")
        subscription_home = Path(self.subscription_codex_home)
        if not subscription_home.is_absolute() or not subscription_home.is_dir():
            raise CodexAdapterViolation("CODEX_SUBSCRIPTION_HOME_INVALID")
        for provider_id in (self.api_provider_id, self.subscription_provider_id):
            if provider_id == "openai" or not SAFE_MODEL_RE.fullmatch(provider_id):
                raise CodexAdapterViolation("CODEX_CUSTOM_PROVIDER_ID_INVALID", [provider_id])
        if self.api_provider_id == self.subscription_provider_id:
            raise CodexAdapterViolation("CODEX_CUSTOM_PROVIDER_ID_COLLISION")
        if self.max_timeout_seconds <= 0 or self.max_raw_cap_bytes <= 0:
            raise CodexAdapterViolation("CODEX_RESOURCE_POLICY_INVALID")


@dataclass(frozen=True)
class CodexCliInvocation:
    process_spec: ProcessSpec
    behavior_hash: str
    prompt_sha256: str
    output_contract_sha256: str
    pre_send_sidecar_sha256: str
    final_output_path: str
    requested_model: str
    model_allowlist_sha256: str
    request_model_binding_sha256: str


def _secret_scan(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _secret_scan(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _secret_scan(item, f"{path}[{index}]")
    elif isinstance(value, str) and value.strip().lower().startswith(SECRET_PREFIXES):
        raise CodexAdapterViolation("SECRET_LIKE_VALUE_FORBIDDEN", [path])


def _strict_json_object(data: bytes, code: str) -> dict[str, Any]:
    try:
        decoded = json.loads(data.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodexAdapterViolation(code, [type(exc).__name__]) from exc
    if not isinstance(decoded, dict):
        raise CodexAdapterViolation(code, ["JSON_OBJECT_REQUIRED"])
    return decoded


def _schema_errors(schema: Mapping[str, Any], value: Any) -> list[str]:
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda item: (list(item.absolute_path), item.message),
    )
    return [
        f"{'/'.join(str(part) for part in item.absolute_path) or '$'}: {item.message}"
        for item in errors[:20]
    ]


def _walk_identity(value: Any, *, models: set[str], request_ids: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).casefold()
            if lowered in {"model", "model_name", "returned_model"} and isinstance(item, str):
                models.add(item)
            if lowered in {"request_id", "response_id"} and isinstance(item, str) and item:
                request_ids.add(item)
            _walk_identity(item, models=models, request_ids=request_ids)
    elif isinstance(value, list):
        for item in value:
            _walk_identity(item, models=models, request_ids=request_ids)


def _walk_tool_event_types(value: Any, *, found: set[str]) -> None:
    if isinstance(value, Mapping):
        event_type = value.get("type")
        if isinstance(event_type, str) and event_type.casefold() in FORBIDDEN_TOOL_EVENT_TYPES:
            found.add(event_type.casefold())
        for item in value.values():
            _walk_tool_event_types(item, found=found)
    elif isinstance(value, list):
        for item in value:
            _walk_tool_event_types(item, found=found)


class CodexCliAdapter:
    ADAPTER_ID = "codex_cli"
    NORMALIZATION_VERSION = "P05_T04_CODEX_FINAL_MESSAGE_V1"
    EVENT_PARSER_VERSION = "P05_T04_CODEX_JSONL_V1"
    FORBIDDEN_FLAGS = {
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
        "--approve-for-me",
        "--add-dir",
        "--oss",
        "--local-provider",
    }
    TOOL_DISABLE_CONFIG = (
        "features.shell_tool=false",
        "features.code_mode=false",
        "features.code_mode_host=false",
        "features.code_mode_only=false",
        "features.skill_mcp_dependency_install=false",
        "features.browser_use=false",
        "features.computer_use=false",
        "features.image_generation=false",
        "features.workspace_dependencies=false",
        "features.multi_agent=false",
        "agents.enabled=false",
        "tools.web_search=false",
    )

    def __init__(
        self,
        policy: CodexCliPolicy,
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

    def build_manifest(self) -> dict[str, Any]:
        value = base_object(
            "AdapterManifest",
            "adaptermanifest_codex_cli",
            producer="P05_T04_CODEX_ADAPTER",
            single_writer="P05_T04_CODEX_ADAPTER",
            consumers=("P05_DISCOVERY_RUNNER", "P05_JOB_RUNNER", "P05_T03_M11"),
            clock=self._clock,
            source_evidence_refs=(
                f"codex-binary-sha256:{self.policy.binary_sha256.upper()}",
                f"codex-exec-help-sha256:{self.policy.exec_help_sha256.upper()}",
            ),
            extensions={
                "read_roots": [self.policy.executable, *self.policy.command_prefix],
                "command_prefix_sha256": canonical_sha256(
                    [self.policy.executable, *self.policy.command_prefix]
                ),
                "runner_sha256": self.policy.runner_sha256.upper(),
                "access_profiles": list(self.policy.allowed_access_profiles),
                "qualification_only": True,
                "activation": "DISABLED",
            },
        )
        value.update(
            {
                "adapter_id": self.ADAPTER_ID,
                "adapter_type": "external_model_cli_process",
                "executable_name": str(Path(self.policy.executable).resolve()),
                "install_discovery": "FROZEN_ABSOLUTE_BINARY_AND_WRAPPER_HASH",
                "version_discovery": "codex --version",
                "auth_discovery": "codex login status plus credential env-name presence only",
                "session_discovery": "PROFILE_SEPARATED_NO_SESSION_BODY_READ",
                "model_discovery": "REQUESTED_AND_RETURNED_IDENTITIES_SEPARATE",
                "capability_discovery": "codex exec --help plus controlled metadata preflight",
                "permission_flags": ["NO_SHELL", "NO_APPROVAL_BYPASS", "NO_TOOLS"],
                "workspace_flags": ["ISOLATED_CREATE_ONLY", "EPHEMERAL", "IGNORE_USER_CONFIG"],
                "network_flags": [
                    "PROFILE_SEPARATED_OPENAI_API_OR_CHATGPT_SUBSCRIPTION",
                    "CUSTOM_PROVIDER_RETRY_ZERO",
                    "SCOPED_PROXY_IF_BOUND",
                ],
                "output_modes": ["JSONL_EVENTS", "STRICT_JSON_SCHEMA", "FINAL_MESSAGE_FILE"],
                "exit_code_mapping": [
                    {"exit_code": 0, "state": "REQUIRES_EVENT_AND_SCHEMA_VALIDATION"},
                    {"exit_code": "nonzero", "state": "ERROR"},
                ],
                "timeout_kill_policy": "T02_KILL_PROCESS_TREE",
                "redaction_policy": "ARGV_VALUES_AND_ENV_VALUES_OMITTED_CREDENTIAL_NAMES_ONLY",
                "version_range": self.policy.exact_version,
                "platforms": [os.name],
            }
        )
        return validate_t01_object(value, "AdapterManifest", self._core_schema)

    def behavior_hash(
        self,
        *,
        model: str,
        reasoning_effort: str,
        output_contract_sha256: str,
        access_profile: str,
    ) -> str:
        profile_config = self._provider_config(access_profile)
        return canonical_sha256(
            {
                "adapter_id": self.ADAPTER_ID,
                "binary_sha256": self.policy.binary_sha256.upper(),
                "exec_help_sha256": self.policy.exec_help_sha256.upper(),
                "runner_sha256": self.policy.runner_sha256.upper(),
                "model": model,
                "reasoning_effort": reasoning_effort,
                "access_profile": access_profile,
                "sandbox": self.policy.sandbox_mode,
                "output_contract_sha256": output_contract_sha256.upper(),
                "profile_config": list(profile_config),
                "argv_contract": [
                    "exec",
                    "--ignore-user-config",
                    "--strict-config",
                    "--ignore-rules",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--color=never",
                    "--sandbox=read-only",
                    "--model=<exact>",
                    "--config=model_reasoning_effort=<exact>",
                    "--config=shell_environment_policy.inherit=none",
                    "--config=custom-provider-profile-with-request-and-stream-retry-zero",
                    "--config=supported-model-tool-surfaces-disabled",
                    "--parser=reject-any-tool-event",
                    "--cd=<isolated-scratch>",
                    "--output-schema=<bound-input>",
                    "--json",
                    "--output-last-message=<bound-output>",
                    "stdin=-",
                ],
            }
        )

    def model_allowlist_sha256(self) -> str:
        """Bind the exact model set admitted by the frozen adapter policy."""
        return canonical_sha256({"allowed_models": sorted(self.policy.allowed_models)})

    def _request_model_binding_sha256(
        self,
        *,
        argv: Sequence[str],
        requested_model: str,
        behavior_hash: str,
    ) -> str:
        indices = [index for index, item in enumerate(argv) if item == "--model"]
        if len(indices) != 1 or indices[0] + 1 >= len(argv):
            raise CodexAdapterViolation("CODEX_REQUEST_MODEL_ARGV_INVALID")
        if argv[indices[0] + 1] != requested_model:
            raise CodexAdapterViolation("CODEX_REQUEST_MODEL_BINDING_MISMATCH")
        if requested_model not in self.policy.allowed_models:
            raise CodexAdapterViolation("CODEX_MODEL_NOT_ALLOWED", [requested_model])
        return canonical_sha256(
            {
                "adapter_id": self.ADAPTER_ID,
                "binary_sha256": self.policy.binary_sha256.upper(),
                "cli_version": self.policy.exact_version,
                "argv_sha256": canonical_sha256(list(argv)),
                "model_flag_index": indices[0],
                "requested_model": requested_model,
                "model_allowlist_sha256": self.model_allowlist_sha256(),
                "behavior_hash": behavior_hash,
            }
        )

    def _profile_environment_names(self, access_profile: str) -> tuple[str, ...]:
        if access_profile == "openai_api":
            return tuple(sorted(self.policy.api_environment_names))
        if access_profile == "codex_chatgpt_session":
            return tuple(sorted(self.policy.subscription_environment_names))
        raise CodexAdapterViolation("CODEX_ACCESS_PROFILE_NOT_ALLOWED", [access_profile])

    def _provider_config(self, access_profile: str) -> tuple[str, ...]:
        if access_profile == "openai_api":
            provider_id = self.policy.api_provider_id
            values = (
                f'model_provider="{provider_id}"',
                f'model_providers.{provider_id}.name="OpenAI"',
                f'model_providers.{provider_id}.base_url="https://api.openai.com/v1"',
                f'model_providers.{provider_id}.env_key="OPENAI_API_KEY"',
                f'model_providers.{provider_id}.wire_api="responses"',
                f"model_providers.{provider_id}.requires_openai_auth=false",
                f"model_providers.{provider_id}.request_max_retries=0",
                f"model_providers.{provider_id}.stream_max_retries=0",
                f"model_providers.{provider_id}.supports_websockets=false",
                f"model_providers.{provider_id}.supports_standalone_web_search=false",
            )
        elif access_profile == "codex_chatgpt_session":
            provider_id = self.policy.subscription_provider_id
            values = (
                f'model_provider="{provider_id}"',
                f'model_providers.{provider_id}.name="OpenAI"',
                f'model_providers.{provider_id}.wire_api="responses"',
                f"model_providers.{provider_id}.requires_openai_auth=true",
                f"model_providers.{provider_id}.request_max_retries=0",
                f"model_providers.{provider_id}.stream_max_retries=0",
                f"model_providers.{provider_id}.supports_websockets=false",
                f"model_providers.{provider_id}.supports_standalone_web_search=false",
            )
        else:
            raise CodexAdapterViolation("CODEX_ACCESS_PROFILE_NOT_ALLOWED", [access_profile])
        return (*values, *self.TOOL_DISABLE_CONFIG)

    def _validate_sidecar(
        self,
        sidecar: Mapping[str, Any],
        *,
        attempt_id: str,
        model: str,
        prompt_sha256: str,
        output_contract_sha256: str,
        behavior_hash: str,
        access_profile: str,
    ) -> dict[str, Any]:
        accepted = immutable_copy(sidecar)
        errors = sorted(
            Draft202012Validator(
                self._external_call_schema, format_checker=FormatChecker()
            ).iter_errors(accepted),
            key=lambda item: (list(item.absolute_path), item.message),
        )
        if errors:
            raise CodexAdapterViolation(
                "CALL_SIDECAR_SCHEMA_INVALID",
                [
                    f"{'/'.join(str(part) for part in item.absolute_path) or '$'}: {item.message}"
                    for item in errors[:20]
                ],
            )
        _secret_scan(accepted)
        expected = {
            "schema_version": "1.0",
            "record_type": "call_binding_receipt",
            "binding_mode": "PROSPECTIVE_STRICT",
            "attempt_id": attempt_id,
            "request_disposition": "BLOCKED_BEFORE_SEND",
        }
        mismatches = [key for key, value in expected.items() if accepted.get(key) != value]
        profile = accepted.get("profile_identity", {})
        model_identity = accepted.get("model_identity", {})
        route = accepted.get("route_identity", {})
        artifacts = accepted.get("artifact_bindings", {})
        extensions = accepted.get("extensions", {})
        if profile.get("profile_id") != access_profile:
            mismatches.append("profile_identity.profile_id")
        if model_identity.get("provider") != "OpenAI" or model_identity.get("requested_model") != model:
            mismatches.append("model_identity")
        if route.get("route_id") != self.policy.route_id:
            mismatches.append("route_identity.route_id")
        if route.get("provider_region") != self.policy.provider_region:
            mismatches.append("route_identity.provider_region")
        if route.get("egress_identity") != self.policy.egress_identity:
            mismatches.append("route_identity.egress_identity")
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
        if accepted.get("outcome", {}).get("call_status") != "BLOCKED_BEFORE_SEND":
            mismatches.append("outcome.call_status")
        if mismatches:
            raise CodexAdapterViolation("CALL_SIDECAR_BINDING_MISMATCH", sorted(set(mismatches)))
        return accepted

    def build_invocation(
        self,
        workspace: JobWorkspace,
        *,
        attempt_id: str,
        model: str,
        reasoning_effort: str,
        access_profile: str,
        prompt: bytes,
        output_schema_path: str | Path,
        final_output_path: str | Path,
        pre_send_sidecar: Mapping[str, Any],
        environment: Mapping[str, str],
        timeout_seconds: float,
        raw_cap_bytes: int,
    ) -> CodexCliInvocation:
        if model not in self.policy.allowed_models:
            raise CodexAdapterViolation("CODEX_MODEL_NOT_ALLOWED", [model])
        if reasoning_effort not in self.policy.allowed_reasoning_efforts:
            raise CodexAdapterViolation("CODEX_REASONING_NOT_ALLOWED", [reasoning_effort])
        if access_profile not in self.policy.allowed_access_profiles:
            raise CodexAdapterViolation("CODEX_ACCESS_PROFILE_NOT_ALLOWED", [access_profile])
        if not prompt or len(prompt) > 1024 * 1024:
            raise CodexAdapterViolation("CODEX_PROMPT_SIZE_INVALID")
        if timeout_seconds <= 0 or timeout_seconds > self.policy.max_timeout_seconds:
            raise CodexAdapterViolation("CODEX_TIMEOUT_POLICY_INVALID")
        if raw_cap_bytes <= 0 or raw_cap_bytes > self.policy.max_raw_cap_bytes:
            raise CodexAdapterViolation("CODEX_RAW_CAP_POLICY_INVALID")

        schema_path = workspace.assert_read_path(output_schema_path)
        final_path = workspace.assert_write_path(final_output_path)
        if final_path.exists():
            raise CodexAdapterViolation("CODEX_FINAL_OUTPUT_CREATE_ONLY")
        schema = _strict_json_object(schema_path.read_bytes(), "OUTPUT_SCHEMA_DOCUMENT_INVALID")
        output_contract_sha256 = canonical_sha256(schema)
        prompt_sha256 = hashlib.sha256(prompt).hexdigest().upper()
        behavior_hash = self.behavior_hash(
            model=model,
            reasoning_effort=reasoning_effort,
            output_contract_sha256=output_contract_sha256,
            access_profile=access_profile,
        )
        accepted_sidecar = self._validate_sidecar(
            pre_send_sidecar,
            attempt_id=attempt_id,
            model=model,
            prompt_sha256=prompt_sha256,
            output_contract_sha256=output_contract_sha256,
            behavior_hash=behavior_hash,
            access_profile=access_profile,
        )

        allowed_env = tuple(sorted(self.policy.environment_allowlist))
        expected_env = self._profile_environment_names(access_profile)
        if set(environment) != set(expected_env):
            raise CodexAdapterViolation("CODEX_ENVIRONMENT_SET_MISMATCH")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in environment.items()):
            raise CodexAdapterViolation("CODEX_ENVIRONMENT_VALUE_INVALID")
        codex_home = Path(environment.get("CODEX_HOME", ""))
        declared_profile_reads: list[str] = []
        declared_profile_writes: list[str] = []
        if access_profile == "openai_api":
            if not environment.get("OPENAI_API_KEY"):
                raise CodexAdapterViolation("CODEX_API_CREDENTIAL_MISSING")
            expected_codex_home = workspace.scratch_root / "codex_home"
            if codex_home.resolve(strict=False) != expected_codex_home.resolve(strict=False):
                raise CodexAdapterViolation("CODEX_HOME_OUTSIDE_WORKSPACE")
            workspace.assert_write_path(codex_home)
            codex_home.mkdir(exist_ok=False)
            declared_profile_writes.append(str(codex_home))
        else:
            if "OPENAI_API_KEY" in environment:
                raise CodexAdapterViolation("CODEX_SUBSCRIPTION_API_CREDENTIAL_FORBIDDEN")
            expected_codex_home = Path(self.policy.subscription_codex_home)
            if codex_home.resolve(strict=False) != expected_codex_home.resolve(strict=True):
                raise CodexAdapterViolation("CODEX_SUBSCRIPTION_HOME_MISMATCH")
            workspace.assert_read_path(codex_home)
            declared_profile_reads.append(str(codex_home))
        for name in ("TEMP", "TMP"):
            if name in environment and Path(environment[name]).resolve(strict=False) != workspace.scratch_root.resolve(strict=False):
                raise CodexAdapterViolation("CODEX_TEMP_OUTSIDE_WORKSPACE", [name])
        final_path.parent.mkdir(parents=True, exist_ok=True)

        provider_config = self._provider_config(access_profile)
        config_argv = tuple(part for value in provider_config for part in ("--config", value))

        argv = (
            self.policy.executable,
            *self.policy.command_prefix,
            "exec",
            "--ignore-user-config",
            "--strict-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--sandbox",
            self.policy.sandbox_mode,
            "--model",
            model,
            "--config",
            f'model_reasoning_effort="{reasoning_effort}"',
            "--config",
            'shell_environment_policy.inherit="none"',
            *config_argv,
            "--cd",
            str(workspace.scratch_root),
            "--output-schema",
            str(schema_path),
            "--json",
            "--output-last-message",
            str(final_path),
            "-",
        )
        if any(item in self.FORBIDDEN_FLAGS for item in argv):
            raise CodexAdapterViolation("FORBIDDEN_FLAG")
        if any("danger" in item.casefold() or "approve-for-me" in item.casefold() for item in argv):
            raise CodexAdapterViolation("FORBIDDEN_FLAG")
        if any(environment.get(name) and environment[name] in argv for name in ("OPENAI_API_KEY",)):
            raise CodexAdapterViolation("CREDENTIAL_IN_ARGV_FORBIDDEN")
        request_model_binding_sha256 = self._request_model_binding_sha256(
            argv=argv,
            requested_model=model,
            behavior_hash=behavior_hash,
        )

        read_paths = [
            str(schema_path),
            self.policy.executable,
            *self.policy.command_prefix,
            *declared_profile_reads,
        ]
        spec = ProcessSpec(
            adapter_id=self.ADAPTER_ID,
            attempt_id=attempt_id,
            argv=tuple(argv),
            cwd=str(workspace.scratch_root),
            environment=dict(environment),
            environment_allowlist=allowed_env,
            timeout_seconds=timeout_seconds,
            raw_cap_bytes=raw_cap_bytes,
            stdin_bytes=prompt,
            declared_read_paths=tuple(read_paths),
            declared_write_paths=(str(final_path), *declared_profile_writes),
            component_bindings=bindings_from_policy(
                self.policy, ("binary_sha256", "exec_help_sha256", "runner_sha256")
            ),
        )
        return CodexCliInvocation(
            process_spec=spec,
            behavior_hash=behavior_hash,
            prompt_sha256=prompt_sha256,
            output_contract_sha256=output_contract_sha256,
            pre_send_sidecar_sha256=hashlib.sha256(canonical_json_bytes(accepted_sidecar)).hexdigest().upper(),
            final_output_path=str(final_path),
            requested_model=model,
            model_allowlist_sha256=self.model_allowlist_sha256(),
            request_model_binding_sha256=request_model_binding_sha256,
        )

    def parse_event_stream(
        self,
        stdout: bytes,
        *,
        requested_model: str,
        invocation: CodexCliInvocation | None = None,
    ) -> dict[str, Any]:
        if ANSI_RE.search(stdout):
            raise CodexAdapterViolation("CODEX_JSONL_ANSI_FORBIDDEN")
        try:
            text = stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CodexAdapterViolation("CODEX_JSONL_UTF8_INVALID", [str(exc.start)]) from exc
        events: list[dict[str, Any]] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CodexAdapterViolation("CODEX_JSONL_INVALID", [f"line={line_no}", f"pos={exc.pos}"]) from exc
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise CodexAdapterViolation("CODEX_JSONL_EVENT_INVALID", [f"line={line_no}"])
            events.append(item)
        if not events:
            raise CodexAdapterViolation("CODEX_JSONL_EMPTY")
        tool_event_types: set[str] = set()
        _walk_tool_event_types(events, found=tool_event_types)
        if tool_event_types:
            raise CodexAdapterViolation(
                "CODEX_TOOL_EVENT_FORBIDDEN", sorted(tool_event_types)
            )
        turn_terminals = [
            item for item in events if item["type"] in {"turn.completed", "turn.failed"}
        ]
        if len(turn_terminals) == 1 and turn_terminals[0] is events[-1]:
            terminal_event = turn_terminals[0]
        elif not turn_terminals:
            error_terminals = [item for item in events if item["type"] == "error"]
            if len(error_terminals) != 1 or error_terminals[0] is not events[-1]:
                raise CodexAdapterViolation("CODEX_JSONL_TERMINAL_INVALID")
            terminal_event = error_terminals[0]
        else:
            raise CodexAdapterViolation("CODEX_JSONL_TERMINAL_INVALID")

        models: set[str] = set()
        request_ids: set[str] = set()
        _walk_identity(events, models=models, request_ids=request_ids)
        if not models:
            returned_model = None
            identity_grade = "PROVIDER_NOT_EXPOSED"
        elif len(models) == 1:
            returned_model = next(iter(models))
            identity_grade = "VERIFIED" if returned_model == requested_model else "CONFLICT"
        else:
            returned_model = None
            identity_grade = "CONFLICT"

        request_model_binding_status = "UNBOUND"
        request_model_binding_sha256: str | None = None
        model_allowlist_sha256: str | None = None
        if invocation is not None:
            if invocation.requested_model != requested_model:
                raise CodexAdapterViolation("CODEX_REQUEST_MODEL_BINDING_MISMATCH")
            expected_allowlist_sha256 = self.model_allowlist_sha256()
            if invocation.model_allowlist_sha256 != expected_allowlist_sha256:
                raise CodexAdapterViolation("CODEX_MODEL_ALLOWLIST_BINDING_MISMATCH")
            expected_binding_sha256 = self._request_model_binding_sha256(
                argv=invocation.process_spec.argv,
                requested_model=requested_model,
                behavior_hash=invocation.behavior_hash,
            )
            if invocation.request_model_binding_sha256 != expected_binding_sha256:
                raise CodexAdapterViolation("CODEX_REQUEST_MODEL_BINDING_MISMATCH")
            request_model_binding_status = "EXACT_ARGV_AND_FROZEN_ALLOWLIST"
            request_model_binding_sha256 = expected_binding_sha256
            model_allowlist_sha256 = expected_allowlist_sha256

        usage = terminal_event.get("usage")
        token_usage = {
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "total_tokens": None,
        }
        usage_status = "UNKNOWN"
        if isinstance(usage, Mapping):
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            reasoning_output_tokens = usage.get("reasoning_output_tokens")
            legacy_reasoning_tokens = usage.get("reasoning_tokens")
            if (
                reasoning_output_tokens is not None
                and legacy_reasoning_tokens is not None
                and reasoning_output_tokens != legacy_reasoning_tokens
            ):
                raise CodexAdapterViolation("CODEX_REASONING_TOKEN_FIELDS_CONFLICT")
            reasoning_tokens = (
                reasoning_output_tokens
                if reasoning_output_tokens is not None
                else legacy_reasoning_tokens
            )
            if reasoning_tokens is None:
                reasoning_tokens = 0
            values = (input_tokens, output_tokens, reasoning_tokens)
            if all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in values):
                token_usage = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_tokens": reasoning_tokens,
                    "total_tokens": input_tokens + output_tokens,
                }
                if reasoning_tokens > output_tokens:
                    raise CodexAdapterViolation("CODEX_REASONING_TOKENS_EXCEED_OUTPUT")
                usage_status = "ACTUAL"

        event_types = [item["type"] for item in events]
        external_started = any(
            item in event_types
            for item in ("turn.started", "response.started", "item.started", "item.completed")
        )
        finish_status = {
            "turn.completed": "SUCCESS",
            "turn.failed": "ERROR",
            "error": "ERROR",
        }[terminal_event["type"]]
        return {
            "parser_version": self.EVENT_PARSER_VERSION,
            "event_count": len(events),
            "event_types": event_types,
            "events_sha256": hashlib.sha256(stdout).hexdigest().upper(),
            "finish_status": finish_status,
            "external_request_started": external_started,
            "requested_model": requested_model,
            "returned_model": returned_model,
            "model_identity_grade": identity_grade,
            "request_model_binding_status": request_model_binding_status,
            "request_model_binding_sha256": request_model_binding_sha256,
            "model_allowlist_sha256": model_allowlist_sha256,
            "provider_request_id_hash": (
                hashlib.sha256(next(iter(request_ids)).encode("utf-8")).hexdigest().upper()
                if len(request_ids) == 1
                else None
            ),
            "usage_status": usage_status,
            "token_usage": token_usage,
        }

    def normalize_final_output(
        self,
        process_run: ProcessRun,
        *,
        final_output_path: str | Path,
        logical_role: str,
        payload_schema_ref: str,
        payload_schema: Mapping[str, Any],
    ) -> dict[str, Any]:
        receipt = validate_t01_object(process_run.receipt, "ProcessExecutionReceipt", self._core_schema)
        schema = immutable_copy(payload_schema)
        schema_hash = canonical_sha256(schema)
        warnings: list[str] = []
        error_code: str | None = None
        payload: dict[str, Any] = {}
        raw = b""
        path = Path(final_output_path)
        if not receipt["process_started"]:
            error_code = receipt.get("error_code") or "WORKSPACE_POLICY_VIOLATION"
        elif receipt.get("timeout"):
            error_code = "PROCESS_TIMEOUT"
        elif receipt.get("extensions", {}).get("raw_truncated"):
            error_code = "RAW_OUTPUT_TRUNCATED"
        elif receipt.get("exit_code") != 0:
            error_code = receipt.get("error_code") or "PROCESS_EXIT_NONZERO"
        elif not path.is_file():
            error_code = "FINAL_MESSAGE_MISSING"
        else:
            raw = path.read_bytes()
            try:
                payload = _strict_json_object(raw, "OUTPUT_SCHEMA_INVALID")
                errors = _schema_errors(schema, payload)
                if errors:
                    warnings.extend(errors)
                    payload = {}
                    error_code = "OUTPUT_SCHEMA_INVALID"
            except CodexAdapterViolation as exc:
                warnings.extend(exc.details or (exc.code,))
                payload = {}
                error_code = "OUTPUT_SCHEMA_INVALID"
        result = base_object(
            "NormalizedResult",
            f"normalizedresult_{receipt['job_id']}_{receipt['attempt_id']}",
            producer="P05_T04_CODEX_ADAPTER",
            single_writer="P05_T04_CODEX_ADAPTER",
            consumers=("M11", "M13", "CALLING_MODULE"),
            clock=self._clock,
            source_evidence_refs=(receipt["object_id"],),
            extensions={
                "raw_stdout_bytes": len(process_run.stdout),
                "raw_stderr_bytes": len(process_run.stderr),
                "final_message_bytes": len(raw),
                "event_stream_sha256": hashlib.sha256(process_run.stdout).hexdigest().upper(),
                "model_repair_attempted": False,
            },
        )
        result.update(
            {
                "job_id": receipt["job_id"],
                "attempt_id": receipt["attempt_id"],
                "logical_role": logical_role,
                "payload": payload,
                "payload_schema_ref": payload_schema_ref,
                "payload_schema_hash": schema_hash,
                "payload_valid": error_code is None,
                "source_output_hash": hashlib.sha256(raw).hexdigest().upper(),
                "normalization_version": self.NORMALIZATION_VERSION,
                "warnings": warnings,
                "error_code": error_code,
            }
        )
        return validate_t01_object(result, "NormalizedResult", self._core_schema)

    def build_model_receipt(
        self,
        process_run: ProcessRun,
        event_summary: Mapping[str, Any],
        *,
        behavior_hash: str,
        input_hash: str,
        output_hash: str | None,
        route: str,
        region: str,
        egress: str,
        latency_ms: float,
        access_profile: str,
        input_price_per_mtok: float | None = None,
        output_price_per_mtok: float | None = None,
        normalized_output: Mapping[str, Any] | None = None,
        identity_evidence_method: str = PROVIDER_EVENT_IDENTITY,
        identity_transport: str | None = None,
        identity_authority_ref: str | None = None,
    ) -> dict[str, Any]:
        process = validate_t01_object(process_run.receipt, "ProcessExecutionReceipt", self._core_schema)
        summary = immutable_copy(event_summary)
        identity = self._model_identity_projection(
            normalized_output or {},
            summary,
            access_profile=access_profile,
            identity_evidence_method=identity_evidence_method,
            identity_transport=identity_transport,
            identity_authority_ref=identity_authority_ref,
        )
        tokens = summary.get("token_usage", {})
        cost_value: float | None = None
        cost_status = "UNKNOWN"
        if access_profile == "codex_chatgpt_session":
            evidence_class = "subscription_cli"
            cost_status = "N/A_SUBSCRIPTION_CLI"
            receipt_usage_status = (
                summary.get("usage_status")
                if summary.get("usage_status") == "ACTUAL"
                else "N/A_SUBSCRIPTION_CLI"
            )
        elif access_profile == "openai_api":
            evidence_class = "api"
            receipt_usage_status = summary.get("usage_status")
        else:
            raise CodexAdapterViolation("CODEX_ACCESS_PROFILE_NOT_ALLOWED", [access_profile])
        if access_profile == "openai_api" and summary.get("usage_status") == "ACTUAL":
            if input_price_per_mtok is None or output_price_per_mtok is None:
                raise CodexAdapterViolation("CODEX_API_PRICE_BINDING_REQUIRED")
            # reasoning tokens are already included in output tokens and are never added again.
            cost_value = round(
                tokens["input_tokens"] * input_price_per_mtok / 1_000_000
                + tokens["output_tokens"] * output_price_per_mtok / 1_000_000,
                12,
            )
            cost_status = "ESTIMATED"
        value = base_object(
            "ModelExecutionReceipt",
            f"modelexecutionreceipt_{process['job_id']}_{process['attempt_id']}",
            producer="P05_T04_CODEX_ADAPTER",
            single_writer="P05_T04_CODEX_ADAPTER",
            consumers=("P05_T03_M11", "M14", "M13"),
            clock=self._clock,
            source_evidence_refs=(process["object_id"],),
            extensions={
                "parser_version": summary.get("parser_version"),
                "event_count": summary.get("event_count"),
                "events_sha256": summary.get("events_sha256"),
                "provider_request_id_hash": summary.get("provider_request_id_hash"),
                "reasoning_tokens_included_in_output_tokens": True,
                "provider_event_returned_model": summary.get("returned_model"),
                "provider_event_model_identity_grade": summary.get("model_identity_grade"),
                "model_text_self_report_used": False,
                "request_model_binding_status": summary.get("request_model_binding_status"),
                "request_model_binding_sha256": summary.get("request_model_binding_sha256"),
                "model_allowlist_sha256": summary.get("model_allowlist_sha256"),
                "identity_evidence_method": identity["identity_evidence_method"],
                "identity_transport": identity_transport,
                "identity_authority_ref": identity_authority_ref,
                "identity_reason_codes": identity["reason_codes"],
            },
        )
        value.update(
            {
                "job_id": process["job_id"],
                "attempt_id": process["attempt_id"],
                "evidence_class": evidence_class,
                "requested_model": summary.get("requested_model"),
                "returned_model": identity["effective_returned_model"],
                "model_identity_grade": identity["effective_model_identity_grade"],
                "provider": "OpenAI",
                "route": route,
                "region": region,
                "egress": egress,
                "behavior_hash": behavior_hash,
                "input_hash": input_hash,
                "output_hash": output_hash,
                "usage_status": receipt_usage_status,
                "token_usage": tokens,
                "cost_status": cost_status,
                "cost_value": cost_value,
                "currency": "USD" if cost_value is not None else None,
                "latency_ms": latency_ms,
                "finish_status": summary.get("finish_status"),
                "external_request_started": bool(summary.get("external_request_started")),
            }
        )
        return validate_t01_object(value, "ModelExecutionReceipt", self._core_schema)

    @staticmethod
    def _model_identity_projection(
        normalized: Mapping[str, Any],
        event_summary: Mapping[str, Any],
        *,
        access_profile: str,
        identity_evidence_method: str,
        identity_transport: str | None,
        identity_authority_ref: str | None,
    ) -> dict[str, Any]:
        grade = event_summary.get("model_identity_grade")
        requested_model = event_summary.get("requested_model")
        returned_model = event_summary.get("returned_model")
        effective_returned_model = returned_model
        effective_grade = grade
        reasons: list[str] = []

        if identity_evidence_method == PROVIDER_EVENT_IDENTITY:
            if grade == "PROVIDER_NOT_EXPOSED":
                reasons.append("RETURNED_MODEL_NOT_EXPOSED")
            elif grade != "VERIFIED":
                reasons.append("RETURNED_MODEL_CONFLICT")
        elif identity_evidence_method == DIRECT_CLI_REQUEST_BINDING:
            if access_profile != "codex_chatgpt_session":
                reasons.append("DIRECT_CLI_REQUEST_BINDING_PROFILE_INVALID")
            if identity_transport != DIRECT_CLI_PROCESS:
                reasons.append("DIRECT_CLI_REQUEST_BINDING_TRANSPORT_INVALID")
            if not isinstance(identity_authority_ref, str) or not identity_authority_ref.strip():
                reasons.append("DIRECT_CLI_REQUEST_BINDING_AUTHORITY_MISSING")
            if grade == "CONFLICT" or grade not in {"PROVIDER_NOT_EXPOSED", "VERIFIED"}:
                reasons.append("RETURNED_MODEL_CONFLICT")
            if not isinstance(requested_model, str) or not SAFE_MODEL_RE.fullmatch(requested_model):
                reasons.append("REQUESTED_MODEL_INVALID")
            if event_summary.get("request_model_binding_status") != "EXACT_ARGV_AND_FROZEN_ALLOWLIST":
                reasons.append("DIRECT_CLI_REQUEST_MODEL_UNBOUND")
            if not SHA256_RE.fullmatch(str(event_summary.get("request_model_binding_sha256", ""))):
                reasons.append("DIRECT_CLI_REQUEST_BINDING_HASH_MISSING")
            if not SHA256_RE.fullmatch(str(event_summary.get("model_allowlist_sha256", ""))):
                reasons.append("DIRECT_CLI_MODEL_ALLOWLIST_HASH_MISSING")

            if not reasons:
                # The CLI request is exactly bound, but the provider response did not
                # expose a returned model. Never promote generated answer text into
                # provider identity evidence.
                effective_grade = "VERIFIED" if grade == "VERIFIED" else "RECORDED_UNVERIFIED"
        else:
            reasons.append("MODEL_IDENTITY_EVIDENCE_METHOD_UNKNOWN")

        return {
            "identity_evidence_method": identity_evidence_method,
            "self_reported_model": None,
            "effective_returned_model": effective_returned_model,
            "effective_model_identity_grade": effective_grade,
            "reason_codes": reasons,
        }

    @staticmethod
    def qualification_projection(
        normalized: Mapping[str, Any],
        event_summary: Mapping[str, Any],
        *,
        access_profile: str = "openai_api",
        identity_evidence_method: str = PROVIDER_EVENT_IDENTITY,
        identity_transport: str | None = None,
        identity_authority_ref: str | None = None,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if not normalized.get("payload_valid"):
            reasons.append(str(normalized.get("error_code") or "OUTPUT_INVALID"))
        identity = CodexCliAdapter._model_identity_projection(
            normalized,
            event_summary,
            access_profile=access_profile,
            identity_evidence_method=identity_evidence_method,
            identity_transport=identity_transport,
            identity_authority_ref=identity_authority_ref,
        )
        reasons.extend(identity["reason_codes"])
        if (
            access_profile != "codex_chatgpt_session"
            and event_summary.get("usage_status") != "ACTUAL"
        ):
            reasons.append("TOKEN_USAGE_NOT_EXPOSED")
        if event_summary.get("finish_status") != "SUCCESS":
            reasons.append("CODEX_TURN_NOT_COMPLETED")
        return {
            "qualification_eligible": not reasons,
            "verification_result": "PASS" if not reasons else "NOT_ASSESSED",
            "acceptance_verdict": "NOT_ASSESSED",
            "reason_codes": reasons,
            "identity_evidence_method": identity["identity_evidence_method"],
            "self_reported_model": identity["self_reported_model"],
            "effective_returned_model": identity["effective_returned_model"],
            "effective_model_identity_grade": identity["effective_model_identity_grade"],
        }
