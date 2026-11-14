"""Fail-closed Codex CLI thin adapter for CODEX-ADAPTER.

This module only maps a frozen Codex CLI compatibility key onto the accepted
Initialization/DataContracts/ServiceContracts seams.  It does not select routes, retry, activate a profile, write
the real RUNTIME_LOG ledger, or read credential values for evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator

from ..contracts import (
    canonical_sha256,
    immutable_copy,
    utc_now,
)
from ..runner import ProcessSpec
from ..workspace import WorkspaceViolation


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
    NORMALIZATION_VERSION = "CODEX_ADAPTER_CODEX_FINAL_MESSAGE_V1"
    EVENT_PARSER_VERSION = "CODEX_ADAPTER_CODEX_JSONL_V1"
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
