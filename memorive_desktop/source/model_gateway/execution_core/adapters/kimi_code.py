"""Fail-closed Kimi CLI thin adapter for KIMI-ADAPTER.

The adapter binds an immutable launcher/entrypoint, exact ``--model`` argv,
frozen model catalog, access profile and entitlement snapshot.  Generated
answer text is never identity evidence.  It does not retry, fall back from an
expired subscription to a paid API key, activate a route, or write RUNTIME_LOG.
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
SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
ANSI_RE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
SECRET_PREFIXES = ("sk-", "ghp_", "github_pat_", "xox", "pat-")
DIRECT_REQUEST_CONTROL_PLANE = "DIRECT_REQUEST_CONTROL_PLANE_V1"


class KimiCodeViolation(ValueError):
    def __init__(self, code: str, details: Sequence[str] = ()):
        self.code = code
        self.details = tuple(details)
        suffix = f": {'; '.join(self.details)}" if self.details else ""
        super().__init__(f"{code}{suffix}")


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


def _parse_time(value: Any, code: str) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise KimiCodeViolation(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KimiCodeViolation(code) from exc
    if parsed.tzinfo is None:
        raise KimiCodeViolation(code)
    return parsed.astimezone(dt.timezone.utc)


def _secret_scan(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _secret_scan(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _secret_scan(item, f"{path}[{index}]")
    elif isinstance(value, str) and value.strip().lower().startswith(SECRET_PREFIXES):
        raise KimiCodeViolation("SECRET_LIKE_VALUE_FORBIDDEN", [path])


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
