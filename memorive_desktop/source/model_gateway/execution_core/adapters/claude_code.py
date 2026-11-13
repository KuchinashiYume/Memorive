"""Fail-closed Claude Code thin adapter for CLAUDE-ADAPTER.

The adapter proves what local executable and exact ``--model`` argument were
used.  It deliberately does *not* treat generated text, or a model label
returned through a third-party relay, as proof of the provider-served backend.
It does not activate routes, retry, fall back, write the RUNTIME_LOG ledger, or read
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
    validate_initialization_object,
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
