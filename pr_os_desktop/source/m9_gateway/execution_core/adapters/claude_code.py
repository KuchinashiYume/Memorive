"""Fail-closed Claude Code thin adapter for P05/T05.

The adapter proves what local executable and exact ``--model`` argument were
used.  It deliberately does *not* treat generated text, or a model label
returned through a third-party relay, as proof of the provider-served backend.
It does not activate routes, retry, fall back, write the M11 ledger, or read
credential values for evidence.
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
from ..workspace import JobWorkspace


SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\[\]-]{0,127}$")
ANSI_RE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
SECRET_PREFIXES = ("sk-", "ghp_", "github_pat_", "xox", "pat-")
RELAY_REPORTED_UNVERIFIED = "RELAY_REPORTED_UNVERIFIED"
DIRECT_REQUEST_CONTROL_PLANE = "DIRECT_REQUEST_CONTROL_PLANE_V1"
THIRD_PARTY_RELAY = "third_party_relay"
DIRECT_PROVIDER = "direct_provider"


class ClaudeCodeViolation(ValueError):
    def __init__(self, code: str, details: Sequence[str] = ()):
        self.code = code
        self.details = tuple(details)
        suffix = f": {'; '.join(self.details)}" if self.details else ""
        super().__init__(f"{code}{suffix}")


@dataclass(frozen=True)
class ClaudeCodePolicy:
    executable: str
    command_prefix: tuple[str, ...]
    exact_version: str
    binary_sha256: str
    help_sha256: str
    runner_sha256: str
    model_catalog_sha256: str
    allowed_models: tuple[str, ...]
    allowed_efforts: tuple[str, ...]
    allowed_profiles: tuple[str, ...]
    relay_profiles: tuple[str, ...]
    profile_environment_names: Mapping[str, tuple[str, ...]]
    profile_evidence_classes: Mapping[str, str]
    environment_allowlist: tuple[str, ...]
    route_id: str
    provider_region: str
    egress_identity: str
    max_timeout_seconds: float = 900.0
    max_raw_cap_bytes: int = 8 * 1024 * 1024
    component_paths: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        executable = Path(self.executable)
        if not executable.is_absolute() or not executable.is_file():
            raise ClaudeCodeViolation("CLAUDE_EXECUTABLE_INVALID")
        for field, value in (
            ("binary_sha256", self.binary_sha256),
            ("help_sha256", self.help_sha256),
            ("runner_sha256", self.runner_sha256),
            ("model_catalog_sha256", self.model_catalog_sha256),
        ):
            if not SHA256_RE.fullmatch(value.upper()):
                raise ClaudeCodeViolation("CLAUDE_COMPATIBILITY_HASH_INVALID", [field])
        actual = hashlib.sha256(executable.read_bytes()).hexdigest().upper()
        if actual != self.binary_sha256.upper():
            raise ClaudeCodeViolation("CLAUDE_BINARY_HASH_MISMATCH")
        if "Claude Code" not in self.exact_version:
            raise ClaudeCodeViolation("CLAUDE_VERSION_INVALID")
        if not self.allowed_models or any(not SAFE_NAME_RE.fullmatch(x) for x in self.allowed_models):
            raise ClaudeCodeViolation("CLAUDE_MODEL_ALLOWLIST_INVALID")
        if not self.allowed_efforts or not set(self.allowed_efforts).issubset(
            {"low", "medium", "high", "xhigh", "max"}
        ):
            raise ClaudeCodeViolation("CLAUDE_EFFORT_ALLOWLIST_INVALID")
        if not self.allowed_profiles or set(self.relay_profiles) - set(self.allowed_profiles):
            raise ClaudeCodeViolation("CLAUDE_PROFILE_ALLOWLIST_INVALID")
        if set(self.profile_environment_names) != set(self.allowed_profiles):
            raise ClaudeCodeViolation("CLAUDE_PROFILE_ENVIRONMENT_INVALID")
        if set(self.profile_evidence_classes) != set(self.allowed_profiles):
            raise ClaudeCodeViolation("CLAUDE_PROFILE_EVIDENCE_CLASS_INVALID")
        allowed_env = set(self.environment_allowlist)
        if len(allowed_env) != len(self.environment_allowlist):
            raise ClaudeCodeViolation("CLAUDE_ENV_ALLOWLIST_DUPLICATE")
        for profile, names in self.profile_environment_names.items():
            if len(set(names)) != len(names) or not set(names).issubset(allowed_env):
                raise ClaudeCodeViolation("CLAUDE_PROFILE_ENVIRONMENT_INVALID", [profile])
            if profile in self.relay_profiles:
                required = {"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"}
                if not required.issubset(names):
                    raise ClaudeCodeViolation("CLAUDE_RELAY_ENVIRONMENT_INVALID", [profile])
        if self.max_timeout_seconds <= 0 or self.max_raw_cap_bytes <= 0:
            raise ClaudeCodeViolation("CLAUDE_RESOURCE_POLICY_INVALID")


@dataclass(frozen=True)
class ClaudeCodeInvocation:
    process_spec: ProcessSpec
    behavior_hash: str
    prompt_sha256: str
    output_contract_sha256: str
    pre_send_sidecar_sha256: str
    requested_model: str
    model_allowlist_sha256: str
    request_model_binding_sha256: str
    access_profile: str
    transport_kind: str


def _secret_scan(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _secret_scan(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _secret_scan(item, f"{path}[{index}]")
    elif isinstance(value, str) and value.strip().lower().startswith(SECRET_PREFIXES):
        raise ClaudeCodeViolation("SECRET_LIKE_VALUE_FORBIDDEN", [path])


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


class ClaudeCodeAdapter:
    ADAPTER_ID = "claude_code"
    PARSER_VERSION = "P05_T05_CLAUDE_STREAM_JSON_V1"
    NORMALIZATION_VERSION = "P05_T05_CLAUDE_STRUCTURED_OUTPUT_V1"
    FORBIDDEN_FLAGS = {
        "--dangerously-skip-permissions",
        "--allow-dangerously-skip-permissions",
        "--fallback-model",
        "--worktree",
        "--add-dir",
        "--agent",
        "--agents",
        "--plugin-dir",
        "--plugin-url",
        "--continue",
        "--resume",
        "--fork-session",
        "--chrome",
        "--ide",
    }
    DISALLOWED_TOOLS = (
        "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task,"
        "Computer,NotebookEdit,AskUserQuestion"
    )

    def __init__(
        self,
        policy: ClaudeCodePolicy,
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
            "adaptermanifest_claude_code",
            producer="P05_T05_CLAUDE_CODE_ADAPTER",
            single_writer="P05_T05_CLAUDE_CODE_ADAPTER",
            consumers=("P05_DISCOVERY_RUNNER", "P05_JOB_RUNNER", "P05_T03_M11"),
            clock=self._clock,
            source_evidence_refs=(
                f"claude-binary-sha256:{self.policy.binary_sha256.upper()}",
                f"claude-help-sha256:{self.policy.help_sha256.upper()}",
                f"model-catalog-sha256:{self.policy.model_catalog_sha256.upper()}",
            ),
            extensions={
                "command_prefix_sha256": canonical_sha256(
                    [self.policy.executable, *self.policy.command_prefix]
                ),
                "runner_sha256": self.policy.runner_sha256.upper(),
                "access_profiles": list(self.policy.allowed_profiles),
                "relay_profiles": list(self.policy.relay_profiles),
                "model_text_self_report_used": False,
                "qualification_only": True,
                "activation": "DISABLED",
            },
        )
        value.update(
            {
                "adapter_id": self.ADAPTER_ID,
                "adapter_type": "external_model_cli_process",
                "executable_name": str(Path(self.policy.executable).resolve()),
                "install_discovery": "FROZEN_ABSOLUTE_BINARY_AND_HASH",
                "version_discovery": "claude --version",
                "auth_discovery": "ENV_NAME_PRESENCE_ONLY_NO_VALUE_IN_EVIDENCE",
                "session_discovery": "PROFILE_SEPARATED_NO_SESSION_BODY_READ",
                "model_discovery": "EXACT_ARGV_PLUS_CONTROL_PLANE_RELAY_RESTRICTED",
                "capability_discovery": "claude --help frozen hash",
                "permission_flags": ["PLAN", "TOOLS_EMPTY", "DISALLOWED_TOOLS_EXPLICIT"],
                "workspace_flags": ["ISOLATED_CWD", "NO_WORKTREE", "NO_SESSION_PERSISTENCE"],
                "network_flags": ["PROFILE_SEPARATED", "NO_FALLBACK", "NO_RETRY"],
                "output_modes": ["STREAM_JSON", "JSON_SCHEMA", "STRUCTURED_OUTPUT"],
                "exit_code_mapping": [
                    {"exit_code": 0, "state": "REQUIRES_EVENT_AND_SCHEMA_VALIDATION"},
                    {"exit_code": "nonzero", "state": "ERROR"},
                ],
                "timeout_kill_policy": "T02_KILL_PROCESS_TREE",
                "redaction_policy": "ARGV_CREDENTIAL_VALUES_FORBIDDEN_ENV_NAMES_ONLY",
                "version_range": self.policy.exact_version,
                "platforms": [os.name],
            }
        )
        return validate_t01_object(value, "AdapterManifest", self._core_schema)

    def behavior_hash(
        self,
        *,
        model: str,
        effort: str,
        output_contract_sha256: str,
        access_profile: str,
    ) -> str:
        return canonical_sha256(
            {
                "adapter_id": self.ADAPTER_ID,
                "binary_sha256": self.policy.binary_sha256.upper(),
                "help_sha256": self.policy.help_sha256.upper(),
                "runner_sha256": self.policy.runner_sha256.upper(),
                "model_catalog_sha256": self.policy.model_catalog_sha256.upper(),
                "model": model,
                "effort": effort,
                "output_contract_sha256": output_contract_sha256.upper(),
                "access_profile": access_profile,
                "permission_mode": "plan",
                "tools": "",
                "fallback": "DISABLED_BY_OMISSION_AND_VALIDATION",
                "model_text_self_report_used": False,
            }
        )

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
            raise ClaudeCodeViolation(
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
            "phase_id": "P05",
            "task_id": "T05",
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
            raise ClaudeCodeViolation("CALL_SIDECAR_BINDING_MISMATCH", sorted(set(mismatches)))
        return sidecar

    def _request_binding_sha256(
        self, argv: Sequence[str], requested_model: str, behavior_hash: str
    ) -> str:
        positions = [i for i, item in enumerate(argv) if item == "--model"]
        if len(positions) != 1 or positions[0] + 1 >= len(argv):
            raise ClaudeCodeViolation("CLAUDE_REQUEST_MODEL_ARGV_INVALID")
        if argv[positions[0] + 1] != requested_model:
            raise ClaudeCodeViolation("CLAUDE_REQUEST_MODEL_BINDING_MISMATCH")
        return canonical_sha256(
            {
                "adapter_id": self.ADAPTER_ID,
                "binary_sha256": self.policy.binary_sha256.upper(),
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
        effort: str,
        access_profile: str,
        prompt: bytes,
        output_schema: Mapping[str, Any],
        pre_send_sidecar: Mapping[str, Any],
        environment: Mapping[str, str],
        timeout_seconds: float,
        raw_cap_bytes: int,
    ) -> ClaudeCodeInvocation:
        if model not in self.policy.allowed_models:
            raise ClaudeCodeViolation("CLAUDE_MODEL_NOT_ALLOWED", [model])
        if effort not in self.policy.allowed_efforts:
            raise ClaudeCodeViolation("CLAUDE_EFFORT_NOT_ALLOWED", [effort])
        if access_profile not in self.policy.allowed_profiles:
            raise ClaudeCodeViolation("CLAUDE_PROFILE_NOT_ALLOWED", [access_profile])
        if not prompt or len(prompt) > 1024 * 1024:
            raise ClaudeCodeViolation("CLAUDE_PROMPT_SIZE_INVALID")
        if timeout_seconds <= 0 or timeout_seconds > self.policy.max_timeout_seconds:
            raise ClaudeCodeViolation("CLAUDE_TIMEOUT_POLICY_INVALID")
        if raw_cap_bytes <= 0 or raw_cap_bytes > self.policy.max_raw_cap_bytes:
            raise ClaudeCodeViolation("CLAUDE_RAW_CAP_POLICY_INVALID")
        schema = immutable_copy(output_schema)
        Draft202012Validator.check_schema(schema)
        schema_hash = canonical_sha256(schema)
        prompt_hash = hashlib.sha256(prompt).hexdigest().upper()
        behavior_hash = self.behavior_hash(
            model=model,
            effort=effort,
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
        expected_names = set(self.policy.profile_environment_names[access_profile])
        if set(environment) != expected_names:
            raise ClaudeCodeViolation("CLAUDE_ENVIRONMENT_SET_MISMATCH")
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items()):
            raise ClaudeCodeViolation("CLAUDE_ENVIRONMENT_VALUE_INVALID")
        for name in ("TEMP", "TMP"):
            if name in environment and Path(environment[name]).resolve(strict=False) != workspace.scratch_root.resolve(strict=False):
                raise ClaudeCodeViolation("CLAUDE_TEMP_OUTSIDE_WORKSPACE", [name])
        if access_profile in self.policy.relay_profiles:
            if not environment.get("ANTHROPIC_AUTH_TOKEN") or not environment.get("ANTHROPIC_BASE_URL"):
                raise ClaudeCodeViolation("CLAUDE_RELAY_CREDENTIAL_OR_ROUTE_MISSING")
            transport_kind = THIRD_PARTY_RELAY
        else:
            transport_kind = DIRECT_PROVIDER
            if "ANTHROPIC_AUTH_TOKEN" in environment and "ANTHROPIC_API_KEY" not in environment:
                raise ClaudeCodeViolation("CLAUDE_DIRECT_PROFILE_CREDENTIAL_INVALID")

        schema_text = canonical_json_bytes(schema).decode("utf-8")
        argv = (
            self.policy.executable,
            *self.policy.command_prefix,
            "--print",
            "--input-format",
            "text",
            "--output-format",
            "stream-json",
            "--verbose",
            "--json-schema",
            schema_text,
            "--model",
            model,
            "--effort",
            effort,
            "--permission-mode",
            "plan",
            "--tools",
            "",
            "--disallowedTools",
            self.DISALLOWED_TOOLS,
            "--safe-mode",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--mcp-config",
            "{}",
            "--no-chrome",
            "--no-session-persistence",
            "--prompt-suggestions",
            "false",
        )
        if any(item in self.FORBIDDEN_FLAGS for item in argv):
            raise ClaudeCodeViolation("CLAUDE_FORBIDDEN_FLAG")
        if "--fallback-model" in argv or "--worktree" in argv:
            raise ClaudeCodeViolation("CLAUDE_FORBIDDEN_FLAG")
        for credential_name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
            if environment.get(credential_name) and environment[credential_name] in argv:
                raise ClaudeCodeViolation("CLAUDE_CREDENTIAL_IN_ARGV_FORBIDDEN")
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
            declared_read_paths=(self.policy.executable, *self.policy.command_prefix),
            declared_write_paths=(),
            component_bindings=bindings_from_policy(
                self.policy,
                ("binary_sha256", "help_sha256", "runner_sha256", "model_catalog_sha256"),
            ),
        )
        return ClaudeCodeInvocation(
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
            transport_kind=transport_kind,
        )

    def parse_event_stream(
        self,
        stdout: bytes,
        *,
        invocation: ClaudeCodeInvocation,
    ) -> dict[str, Any]:
        if ANSI_RE.search(stdout):
            raise ClaudeCodeViolation("CLAUDE_STREAM_ANSI_FORBIDDEN")
        try:
            text = stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ClaudeCodeViolation("CLAUDE_STREAM_UTF8_INVALID", [str(exc.start)]) from exc
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ClaudeCodeViolation("CLAUDE_STREAM_JSON_INVALID", [f"line={line_number}"]) from exc
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise ClaudeCodeViolation("CLAUDE_STREAM_EVENT_INVALID", [f"line={line_number}"])
            events.append(item)
        if not events:
            raise ClaudeCodeViolation("CLAUDE_STREAM_EMPTY")
        terminals = [x for x in events if x.get("type") == "result"]
        if len(terminals) != 1 or terminals[0] is not events[-1]:
            raise ClaudeCodeViolation("CLAUDE_STREAM_TERMINAL_INVALID")
        for event in events:
            message = event.get("message")
            content = message.get("content", []) if isinstance(message, Mapping) else []
            if isinstance(content, list) and any(
                isinstance(block, Mapping) and block.get("type") in {"tool_use", "tool_result"}
                for block in content
            ):
                raise ClaudeCodeViolation("CLAUDE_TOOL_EVENT_FORBIDDEN")
            if event.get("type") in {"tool_use", "tool_result"}:
                raise ClaudeCodeViolation("CLAUDE_TOOL_EVENT_FORBIDDEN")

        terminal = terminals[0]
        trusted_labels: list[str] = []
        for event in events:
            if event.get("type") == "system" and event.get("subtype") == "init":
                if isinstance(event.get("model"), str):
                    trusted_labels.append(event["model"])
            elif event is terminal and isinstance(event.get("model"), str):
                trusted_labels.append(event["model"])
        unique_labels = sorted(set(trusted_labels))
        reported_model = unique_labels[0] if len(unique_labels) == 1 else None
        if invocation.transport_kind == THIRD_PARTY_RELAY:
            if len(unique_labels) == 1 and reported_model == invocation.requested_model:
                identity_grade = RELAY_REPORTED_UNVERIFIED
            elif not unique_labels:
                identity_grade = "RELAY_NOT_EXPOSED"
            else:
                identity_grade = "RELAY_REPORTED_CONFLICT"
        elif len(unique_labels) == 1 and reported_model == invocation.requested_model:
            identity_grade = "DIRECT_EVENT_MATCH"
        elif not unique_labels:
            identity_grade = "PROVIDER_NOT_EXPOSED"
        else:
            identity_grade = "CONFLICT"

        expected_binding = self._request_binding_sha256(
            invocation.process_spec.argv,
            invocation.requested_model,
            invocation.behavior_hash,
        )
        if invocation.request_model_binding_sha256 != expected_binding:
            raise ClaudeCodeViolation("CLAUDE_REQUEST_MODEL_BINDING_MISMATCH")
        if invocation.model_allowlist_sha256 != self.model_allowlist_sha256():
            raise ClaudeCodeViolation("CLAUDE_MODEL_ALLOWLIST_BINDING_MISMATCH")

        usage = terminal.get("usage") if isinstance(terminal.get("usage"), Mapping) else {}
        input_tokens = _integer_or_none(usage.get("input_tokens"))
        output_tokens = _integer_or_none(usage.get("output_tokens"))
        usage_status = "UNKNOWN"
        token_usage = {
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "total_tokens": None,
        }
        if input_tokens is not None and output_tokens is not None:
            token_usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "reasoning_tokens": None,
                "total_tokens": input_tokens + output_tokens,
            }
            usage_status = "ACTUAL"
        structured_output = terminal.get("structured_output")
        if not isinstance(structured_output, Mapping):
            structured_output = None
        return {
            "parser_version": self.PARSER_VERSION,
            "event_count": len(events),
            "event_types": [x["type"] for x in events],
            "events_sha256": hashlib.sha256(stdout).hexdigest().upper(),
            "finish_status": "ERROR" if terminal.get("is_error") else "SUCCESS",
            "external_request_started": any(x.get("type") == "assistant" for x in events),
            "requested_model": invocation.requested_model,
            "relay_or_provider_reported_model": reported_model,
            "returned_model": None if invocation.transport_kind == THIRD_PARTY_RELAY else reported_model,
            "model_identity_grade": identity_grade,
            "model_text_self_report_used": False,
            "request_model_binding_status": "EXACT_ARGV_AND_FROZEN_CONTROL_PLANE",
            "request_model_binding_sha256": expected_binding,
            "model_allowlist_sha256": self.model_allowlist_sha256(),
            "transport_kind": invocation.transport_kind,
            "usage_status": usage_status,
            "token_usage": token_usage,
            "structured_output": immutable_copy(structured_output) if structured_output is not None else None,
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
        process = validate_t01_object(
            process_run.receipt, "ProcessExecutionReceipt", self._core_schema
        )
        schema = immutable_copy(payload_schema)
        payload = immutable_copy(event_summary.get("structured_output") or {})
        warnings: list[str] = []
        error_code: str | None = None
        if not process["process_started"]:
            error_code = process.get("error_code") or "PROCESS_NOT_STARTED"
        elif process.get("timeout"):
            error_code = "PROCESS_TIMEOUT"
        elif process.get("extensions", {}).get("raw_truncated"):
            error_code = "RAW_OUTPUT_TRUNCATED"
        elif process.get("exit_code") != 0:
            error_code = process.get("error_code") or "PROCESS_EXIT_NONZERO"
        elif event_summary.get("finish_status") != "SUCCESS":
            error_code = "CLAUDE_RESULT_ERROR"
        elif not payload:
            error_code = "STRUCTURED_OUTPUT_MISSING"
        else:
            warnings = _schema_errors(schema, payload)
            if warnings:
                payload = {}
                error_code = "OUTPUT_SCHEMA_INVALID"
        value = base_object(
            "NormalizedResult",
            f"normalizedresult_{process['job_id']}_{process['attempt_id']}",
            producer="P05_T05_CLAUDE_CODE_ADAPTER",
            single_writer="P05_T05_CLAUDE_CODE_ADAPTER",
            consumers=("M11", "M13", "CALLING_MODULE"),
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
                    canonical_json_bytes(event_summary.get("structured_output"))
                ).hexdigest().upper(),
                "normalization_version": self.NORMALIZATION_VERSION,
                "warnings": warnings,
                "error_code": error_code,
            }
        )
        return validate_t01_object(value, "NormalizedResult", self._core_schema)

    @staticmethod
    def qualification_projection(
        normalized: Mapping[str, Any],
        event_summary: Mapping[str, Any],
        *,
        independent_backend_identity_status: str,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if not normalized.get("payload_valid"):
            reasons.append(str(normalized.get("error_code") or "OUTPUT_INVALID"))
        if event_summary.get("finish_status") != "SUCCESS":
            reasons.append("CLAUDE_RESULT_NOT_SUCCESS")
        if event_summary.get("request_model_binding_status") != "EXACT_ARGV_AND_FROZEN_CONTROL_PLANE":
            reasons.append("REQUEST_MODEL_NOT_CONTROL_PLANE_BOUND")
        if event_summary.get("transport_kind") == THIRD_PARTY_RELAY:
            reasons.append("RELAY_BACKEND_IDENTITY_NOT_INDEPENDENTLY_VERIFIED")
        elif independent_backend_identity_status != "VERIFIED":
            reasons.append("BACKEND_IDENTITY_NOT_INDEPENDENTLY_VERIFIED")
        if event_summary.get("usage_status") != "ACTUAL":
            reasons.append("TOKEN_USAGE_NOT_EXPOSED")
        return {
            "qualification_eligible": not reasons,
            "verification_result": "PASS" if not reasons else "NOT_ASSESSED",
            "acceptance_verdict": "NOT_ASSESSED",
            "reason_codes": reasons,
            "requested_model": event_summary.get("requested_model"),
            "returned_model": event_summary.get("returned_model"),
            "relay_reported_model": event_summary.get("relay_or_provider_reported_model"),
            "model_identity_grade": event_summary.get("model_identity_grade"),
            "model_text_self_report_used": False,
        }

    def build_model_receipt(
        self,
        process_run: ProcessRun,
        event_summary: Mapping[str, Any],
        *,
        invocation: ClaudeCodeInvocation,
        input_hash: str,
        output_hash: str | None,
        latency_ms: float,
    ) -> dict[str, Any]:
        process = validate_t01_object(
            process_run.receipt, "ProcessExecutionReceipt", self._core_schema
        )
        evidence_class = self.policy.profile_evidence_classes[invocation.access_profile]
        relay = invocation.transport_kind == THIRD_PARTY_RELAY
        value = base_object(
            "ModelExecutionReceipt",
            f"modelexecutionreceipt_{process['job_id']}_{process['attempt_id']}",
            producer="P05_T05_CLAUDE_CODE_ADAPTER",
            single_writer="P05_T05_CLAUDE_CODE_ADAPTER",
            consumers=("P05_T03_M11", "M14", "M13"),
            clock=self._clock,
            source_evidence_refs=(process["object_id"],),
            extensions={
                "parser_version": event_summary.get("parser_version"),
                "events_sha256": event_summary.get("events_sha256"),
                "relay_reported_model": event_summary.get("relay_or_provider_reported_model"),
                "relay_backend_identity_status": "UNVERIFIED" if relay else "NOT_APPLICABLE",
                "request_model_binding_sha256": invocation.request_model_binding_sha256,
                "model_allowlist_sha256": invocation.model_allowlist_sha256,
                "model_text_self_report_used": False,
            },
        )
        value.update(
            {
                "job_id": process["job_id"],
                "attempt_id": process["attempt_id"],
                "evidence_class": evidence_class,
                "requested_model": invocation.requested_model,
                "returned_model": event_summary.get("returned_model"),
                "model_identity_grade": event_summary.get("model_identity_grade"),
                "provider": "IntelAllocRelay" if relay else "Anthropic",
                "route": self.policy.route_id,
                "region": self.policy.provider_region,
                "egress": self.policy.egress_identity,
                "behavior_hash": invocation.behavior_hash,
                "input_hash": input_hash,
                "output_hash": output_hash,
                "usage_status": event_summary.get("usage_status"),
                "token_usage": immutable_copy(event_summary.get("token_usage", {})),
                "cost_status": "UNKNOWN_RELAY_BILLING" if relay else "UNKNOWN",
                "cost_value": None,
                "currency": None,
                "latency_ms": latency_ms,
                "finish_status": event_summary.get("finish_status"),
                "external_request_started": bool(event_summary.get("external_request_started")),
            }
        )
        return validate_t01_object(value, "ModelExecutionReceipt", self._core_schema)
