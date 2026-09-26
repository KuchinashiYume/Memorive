"""Fail-closed, offline-only GitHub Copilot CLI adapter for P05/T10.

The Q2 contract binds an immutable launcher/entrypoint, exact ``--model``
argv, a frozen model catalog and a user-confirmed abstract login fact.  It
never reads credential material, sends a real CLI prompt, activates a route,
retries, falls back, or writes M11.  Generated answer text is never identity
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
    validate_t01_object,
)
from ..runner import ProcessRun, ProcessSpec
from ..component_binding import bindings_from_policy
from ..workspace import JobWorkspace


SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ANSI_RE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
SECRET_PREFIXES = ("sk-", "ghp_", "github_pat_", "xox", "pat-")
DIRECT_REQUEST_CONTROL_PLANE = "DIRECT_REQUEST_CONTROL_PLANE_V1"


class GitHubCopilotCLIViolation(ValueError):
    def __init__(self, code: str, details: Sequence[str] = ()):
        self.code = code
        self.details = tuple(details)
        suffix = f": {'; '.join(self.details)}" if self.details else ""
        super().__init__(f"{code}{suffix}")


@dataclass(frozen=True)
class GitHubCopilotCLIPolicy:
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
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_LAUNCHER_INVALID")
        for field, value in (
            ("launcher_sha256", self.launcher_sha256),
            ("wrapper_sha256", self.wrapper_sha256),
            ("entrypoint_sha256", self.entrypoint_sha256),
            ("help_sha256", self.help_sha256),
            ("runner_sha256", self.runner_sha256),
            ("model_catalog_sha256", self.model_catalog_sha256),
        ):
            if not SHA256_RE.fullmatch(value.upper()):
                raise GitHubCopilotCLIViolation("GITHUB_COPILOT_COMPATIBILITY_HASH_INVALID", [field])
        if hashlib.sha256(launcher.read_bytes()).hexdigest().upper() != self.launcher_sha256.upper():
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_LAUNCHER_HASH_MISMATCH")
        if not re.fullmatch(r"\d+\.\d+\.\d+", self.exact_version):
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_VERSION_INVALID")
        if not self.allowed_models or any(not SAFE_MODEL_RE.fullmatch(x) for x in self.allowed_models):
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_MODEL_ALLOWLIST_INVALID")
        if set(self.allowed_profiles) != {self.user_login_profile}:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_PROFILE_ALLOWLIST_INVALID")
        if set(self.profile_environment_names) != set(self.allowed_profiles):
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_PROFILE_ENVIRONMENT_INVALID")
        if set(self.profile_evidence_classes) != set(self.allowed_profiles):
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_PROFILE_EVIDENCE_CLASS_INVALID")
        allowed_env = set(self.environment_allowlist)
        if len(allowed_env) != len(self.environment_allowlist):
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_ENV_ALLOWLIST_DUPLICATE")
        for profile, names in self.profile_environment_names.items():
            if len(set(names)) != len(names) or not set(names).issubset(allowed_env):
                raise GitHubCopilotCLIViolation("GITHUB_COPILOT_PROFILE_ENVIRONMENT_INVALID", [profile])
            if not {"COPILOT_HOME", "COPILOT_CACHE_HOME"}.issubset(names):
                raise GitHubCopilotCLIViolation("GITHUB_COPILOT_EPHEMERAL_HOME_REQUIRED", [profile])
            forbidden = {
                "COPILOT_GITHUB_TOKEN",
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "COPILOT_ALLOW_ALL",
            }
            if forbidden & set(names):
                raise GitHubCopilotCLIViolation("GITHUB_COPILOT_CREDENTIAL_ENV_FORBIDDEN", [profile])
        if self.max_timeout_seconds <= 0 or self.max_raw_cap_bytes <= 0:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_RESOURCE_POLICY_INVALID")


@dataclass(frozen=True)
class GitHubCopilotCLIInvocation:
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
        raise GitHubCopilotCLIViolation(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GitHubCopilotCLIViolation(code) from exc
    if parsed.tzinfo is None:
        raise GitHubCopilotCLIViolation(code)
    return parsed.astimezone(dt.timezone.utc)


def _secret_scan(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _secret_scan(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _secret_scan(item, f"{path}[{index}]")
    elif isinstance(value, str) and value.strip().lower().startswith(SECRET_PREFIXES):
        raise GitHubCopilotCLIViolation("SECRET_LIKE_VALUE_FORBIDDEN", [path])


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


class GitHubCopilotCLIAdapter:
    ADAPTER_ID = "github_copilot_cli"
    PARSER_VERSION = "P05_T10_GITHUB_COPILOT_JSONL_V1"
    NORMALIZATION_VERSION = "P05_T10_GITHUB_COPILOT_JSONL_FINAL_MESSAGE_V1"
    FORBIDDEN_FLAGS = {
        "--allow-all",
        "--allow-all-tools",
        "--allow-all-paths",
        "--allow-all-urls",
        "--enable-all-github-mcp-tools",
        "--allow-all-mcp-server-instructions",
        "--yolo",
        "--worktree",
        "-w",
        "--add-dir",
        "--allow-tool",
        "--allow-url",
        "--share",
        "--share-gist",
        "--remote",
        "--remote-export",
        "--autopilot",
        "--connect",
        "--continue",
        "-c",
        "--resume",
        "-r",
        "--session-id",
        "--agent",
        "--plugin-dir",
        "--additional-mcp-config",
        "--attachment",
    }

    def __init__(
        self,
        policy: GitHubCopilotCLIPolicy,
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
            "adaptermanifest_github_copilot_cli",
            producer="P05_T10_GITHUB_COPILOT_CLI_ADAPTER",
            single_writer="P05_T10_GITHUB_COPILOT_CLI_ADAPTER",
            consumers=("P05_DISCOVERY_RUNNER", "P05_JOB_RUNNER", "P05_T03_M11"),
            clock=self._clock,
            source_evidence_refs=(
                f"copilot-launcher-sha256:{self.policy.launcher_sha256.upper()}",
                f"copilot-wrapper-sha256:{self.policy.wrapper_sha256.upper()}",
                f"copilot-entrypoint-sha256:{self.policy.entrypoint_sha256.upper()}",
                f"copilot-help-sha256:{self.policy.help_sha256.upper()}",
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
                "version_discovery": "copilot --version",
                "auth_discovery": "USER_CONFIRMED_ABSTRACT_STATUS_ONLY",
                "session_discovery": "NO_CREDENTIAL_OR_SESSION_BODY_READ",
                "model_discovery": "EXACT_ARGV_PLUS_FROZEN_OFFICIAL_CATALOG",
                "capability_discovery": "copilot --help frozen hash",
                "permission_flags": ["PLAN_MODE", "DENY_SHELL_WRITE_READ_URL_MEMORY_GITHUB", "BROAD_ALLOW_FORBIDDEN", "TOOL_EVENTS_REJECTED"],
                "workspace_flags": ["ISOLATED_CWD", "WORKTREE_FORBIDDEN", "EPHEMERAL_COPILOT_HOME"],
                "network_flags": ["OFFLINE_Q2", "EXTERNAL_PROMPT_REQUEST_LIMIT_ZERO"],
                "output_modes": ["JSONL", "UNKNOWN_EVENT_FAIL_CLOSED"],
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
        output_contract_sha256: str,
        access_profile: str,
        output_mode: str = "jsonl",
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
                "tool_policy": "DENY_ALL_KINDS_AND_DISABLE_BUILTIN_MCPS",
                "broad_allow": False,
                "prompt_transport": "ARGV_REQUIRED_BY_CLI_RECEIPT_REDACTED",
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
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_LOGIN_FACT_REQUIRED")
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
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_LOGIN_FACT_FIELDS_INVALID")
        if accepted["profile_id"] != access_profile:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_LOGIN_PROFILE_MISMATCH")
        if accepted["status"] != "LOGIN_USER_CONFIRMED_SUCCESS":
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_LOGIN_NOT_CONFIRMED")
        if accepted["source"] != "USER_COORDINATION_UPDATE":
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_LOGIN_SOURCE_INVALID")
        _parse_time(accepted["confirmed_at"], "GITHUB_COPILOT_LOGIN_TIME_INVALID")
        if accepted["login_domain"] != "NOT_SAFELY_OBSERVED":
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_LOGIN_DOMAIN_OVERCLAIMED")
        if accepted["credential_material_read"] is not False:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_CREDENTIAL_READ_FORBIDDEN")
        if accepted["entitlement_status"] != "ENTITLEMENT_UNVERIFIED/NOT_REQUIRED_FOR_Q2":
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_ENTITLEMENT_STATUS_INVALID")
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
            raise GitHubCopilotCLIViolation(
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
            "task_id": "T10",
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
            raise GitHubCopilotCLIViolation("CALL_SIDECAR_BINDING_MISMATCH", sorted(set(mismatches)))
        return sidecar

    def _request_binding_sha256(
        self, argv: Sequence[str], requested_model: str, behavior_hash: str
    ) -> str:
        positions = [i for i, item in enumerate(argv) if item.startswith("--model=")]
        if len(positions) != 1:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_REQUEST_MODEL_ARGV_INVALID")
        if argv[positions[0]].split("=", 1)[1] != requested_model:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_REQUEST_MODEL_BINDING_MISMATCH")
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
        output_mode: str = "jsonl",
    ) -> GitHubCopilotCLIInvocation:
        if model not in self.policy.allowed_models:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_MODEL_NOT_ALLOWED", [model])
        if access_profile not in self.policy.allowed_profiles:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_PROFILE_NOT_ALLOWED", [access_profile])
        if not prompt or len(prompt) > 1024 * 1024:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_PROMPT_SIZE_INVALID")
        if timeout_seconds <= 0 or timeout_seconds > self.policy.max_timeout_seconds:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_TIMEOUT_POLICY_INVALID")
        if raw_cap_bytes <= 0 or raw_cap_bytes > self.policy.max_raw_cap_bytes:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_RAW_CAP_POLICY_INVALID")
        if output_mode != "jsonl":
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_OUTPUT_MODE_NOT_ALLOWED")
        try:
            prompt_text = prompt.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_PROMPT_UTF8_INVALID") from exc
        if "\x00" in prompt_text:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_PROMPT_NUL_FORBIDDEN")
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
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_ENVIRONMENT_SET_MISMATCH")
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items()):
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_ENVIRONMENT_VALUE_INVALID")
        expected_home = (workspace.scratch_root / "copilot-home").resolve(strict=False)
        expected_cache = (workspace.scratch_root / "copilot-cache").resolve(strict=False)
        actual_home = Path(environment.get("COPILOT_HOME", "")).resolve(strict=False)
        actual_cache = Path(environment.get("COPILOT_CACHE_HOME", "")).resolve(strict=False)
        if actual_home != expected_home or actual_cache != expected_cache:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_HOME_OUTSIDE_WORKSPACE")
        for target in (actual_home, actual_cache):
            workspace.assert_write_path(target)
            target.mkdir(exist_ok=True)
        declared_runtime_write = (str(actual_home), str(actual_cache))
        for name in ("TEMP", "TMP"):
            if name in environment and Path(environment[name]).resolve(strict=False) != workspace.scratch_root.resolve(strict=False):
                raise GitHubCopilotCLIViolation("GITHUB_COPILOT_TEMP_OUTSIDE_WORKSPACE", [name])

        argv_parts = [
            self.policy.launcher,
            *self.policy.command_prefix,
            "-p",
            prompt_text,
            "--output-format=json",
            f"--model={model}",
            "--plan",
            "--deny-tool=shell,write,read,url,memory,github",
            "--disable-builtin-mcps",
            "--no-custom-instructions",
            "--no-experimental",
            "--no-remote",
            "--no-remote-export",
            "--no-auto-update",
            "--no-ask-user",
            "--no-color",
            "--stream=off",
            "--secret-env-vars=COPILOT_GITHUB_TOKEN,GH_TOKEN,GITHUB_TOKEN",
        ]
        argv = tuple(argv_parts)
        if any(item.split("=", 1)[0] in self.FORBIDDEN_FLAGS for item in argv):
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_FORBIDDEN_FLAG")
        if any(item.startswith(("--allow-all", "--yolo", "--worktree")) for item in argv):
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_FORBIDDEN_FLAG")
        if "--deny-tool=shell,write,read,url,memory,github" not in argv or "--plan" not in argv:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_PERMISSION_POLICY_INVALID")
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
            stdin_bytes=None,
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
        return GitHubCopilotCLIInvocation(
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

    def parse_jsonl(
        self,
        stdout: bytes,
        *,
        invocation: GitHubCopilotCLIInvocation,
    ) -> dict[str, Any]:
        """Parse the frozen Q2 JSONL event contract and reject unknown effects."""

        if ANSI_RE.search(stdout):
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_JSONL_ANSI_FORBIDDEN")
        try:
            text = stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise GitHubCopilotCLIViolation(
                "GITHUB_COPILOT_JSONL_UTF8_INVALID", [str(exc.start)]
            ) from exc
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GitHubCopilotCLIViolation(
                    "GITHUB_COPILOT_JSONL_INVALID", [f"line={line_number}"]
                ) from exc
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise GitHubCopilotCLIViolation(
                    "GITHUB_COPILOT_JSONL_EVENT_INVALID", [f"line={line_number}"]
                )
            events.append(item)
        if not events:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_JSONL_EMPTY")

        starts = [event for event in events if event.get("type") == "session.start"]
        terminals = [event for event in events if event.get("type") == "session.end"]
        if len(starts) != 1 or starts[0] is not events[0]:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_JSONL_START_INVALID")
        if len(terminals) != 1 or terminals[0] is not events[-1]:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_JSONL_TERMINAL_INVALID")

        error_types = {
            "auth.error": "AUTH_ERROR",
            "entitlement.error": "ENTITLEMENT_ABSENT",
            "organization_policy.error": "ORGANIZATION_POLICY_DENIED",
            "model.error": "MODEL_UNAVAILABLE",
            "permission.error": "TOOL_PERMISSION_DENIED",
            "rate_limit.error": "RATE_LIMIT",
            "quota.error": "QUOTA_EXCEEDED",
            "provider.error": "PROVIDER_ERROR",
        }
        allowed_types = {
            "session.start",
            "diagnostic",
            "assistant.message",
            "session.end",
            *error_types,
        }
        event_types = [str(event["type"]) for event in events]
        if any(kind in {"tool.request", "tool.execution", "tool.result"} for kind in event_types):
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_TOOL_PERMISSION_DENIED")
        if "network.request" in event_types:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_NETWORK_POLICY_VIOLATION")
        if "github.mutation" in event_types:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_GITHUB_MUTATION_FORBIDDEN")
        unknown = sorted(set(event_types) - allowed_types)
        if unknown:
            raise GitHubCopilotCLIViolation(
                "GITHUB_COPILOT_JSONL_EVENT_TYPE_UNKNOWN", unknown
            )

        start = starts[0]
        terminal = terminals[0]
        reported_model = start.get("model") if isinstance(start.get("model"), str) else None
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
            raise GitHubCopilotCLIViolation(
                "GITHUB_COPILOT_REQUEST_MODEL_BINDING_MISMATCH"
            )
        if invocation.model_allowlist_sha256 != self.model_allowlist_sha256():
            raise GitHubCopilotCLIViolation(
                "GITHUB_COPILOT_MODEL_ALLOWLIST_BINDING_MISMATCH"
            )

        assistant_parts: list[str] = []
        diagnostics: list[str] = []
        for event in events:
            if event.get("type") == "diagnostic":
                code = event.get("code")
                if isinstance(code, str):
                    diagnostics.append(code)
                continue
            if event.get("type") != "assistant.message":
                continue
            content = event.get("content")
            if not isinstance(content, str):
                data = event.get("data")
                content = data.get("content") if isinstance(data, Mapping) else None
            if isinstance(content, str):
                assistant_parts.append(content)
        final_message = assistant_parts[-1] if assistant_parts else None

        errors = [event for event in events if event.get("type") in error_types]
        if len(errors) > 1:
            raise GitHubCopilotCLIViolation("GITHUB_COPILOT_MULTIPLE_ERRORS")
        error_code = None
        if errors:
            error_event = errors[0]
            supplied_code = error_event.get("code")
            error_code = (
                supplied_code
                if isinstance(supplied_code, str) and supplied_code
                else error_types[str(error_event["type"])]
            )

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
                raise GitHubCopilotCLIViolation("GITHUB_COPILOT_TOKEN_TOTAL_CONFLICT")
            token_usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "reasoning_tokens": None,
                "total_tokens": computed,
            }
            usage_status = "ACTUAL"

        success = terminal.get("status") == "success" and error_code is None
        if not success and error_code is None:
            error_code = "GITHUB_COPILOT_RESULT_ERROR"
        return {
            "parser_version": self.PARSER_VERSION,
            "event_count": len(events),
            "event_types": event_types,
            "events_sha256": hashlib.sha256(stdout).hexdigest().upper(),
            "finish_status": "SUCCESS" if success else "ERROR",
            "error_code": error_code,
            "diagnostic_codes": diagnostics,
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
        process = validate_t01_object(
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
            error_code = str(
                event_summary.get("error_code") or "GITHUB_COPILOT_RESULT_ERROR"
            )
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
            producer="P05_T10_GITHUB_COPILOT_CLI_ADAPTER",
            single_writer="P05_T10_GITHUB_COPILOT_CLI_ADAPTER",
            consumers=("M11", "M13", "CALLING_MODULE"),
            clock=self._clock,
            source_evidence_refs=(process["object_id"],),
            extensions={
                "raw_stdout_bytes": len(process_run.stdout),
                "raw_stderr_bytes": len(process_run.stderr),
                "event_stream_sha256": hashlib.sha256(process_run.stdout).hexdigest().upper(),
                "diagnostic_codes": list(event_summary.get("diagnostic_codes", [])),
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
        return validate_t01_object(value, "NormalizedResult", self._core_schema)

    @staticmethod
    def offline_q2_projection(
        normalized: Mapping[str, Any],
        event_summary: Mapping[str, Any],
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if not normalized.get("payload_valid"):
            reasons.append(str(normalized.get("error_code") or "OUTPUT_INVALID"))
        if event_summary.get("finish_status") != "SUCCESS":
            reasons.append("GITHUB_COPILOT_RESULT_NOT_SUCCESS")
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
        invocation: GitHubCopilotCLIInvocation,
        input_hash: str,
        output_hash: str | None,
        latency_ms: float,
    ) -> dict[str, Any]:
        process = validate_t01_object(
            process_run.receipt, "ProcessExecutionReceipt", self._core_schema
        )
        value = base_object(
            "ModelExecutionReceipt",
            f"modelexecutionreceipt_{process['job_id']}_{process['attempt_id']}",
            producer="P05_T10_GITHUB_COPILOT_CLI_ADAPTER",
            single_writer="P05_T10_GITHUB_COPILOT_CLI_ADAPTER",
            consumers=("P05_T03_M11", "M14", "M13"),
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
                "provider": "GitHub",
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
        return validate_t01_object(value, "ModelExecutionReceipt", self._core_schema)
