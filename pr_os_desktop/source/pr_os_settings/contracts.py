from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import re
from typing import Any, Mapping, Sequence
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from .model_capabilities import (
    infer_api_model_capability,
    workflow_node_accepts_capability,
)
from .kimi_endpoint import KIMI_ENDPOINTS, is_kimi_service


SETTINGS_SCHEMA_VERSION = "SettingsTemplate-v6"
STORE_SCHEMA_VERSION = "SettingsStoreEnvelope-v6"
MODES = frozenset({"NORMAL", "ADVANCED", "DEVELOPER"})
PLANS = frozenset({"ECONOMY", "STANDARD", "HIGH_QUALITY", "MAXIMUM"})
CONNECTION_STATES = frozenset({"UNVERIFIED", "AVAILABLE", "INVALID"})
API_PROTOCOLS = frozenset({"AUTO", "OPENAI_COMPATIBLE", "ANTHROPIC", "GEMINI"})
PROXY_MODES = frozenset({"SYSTEM", "NONE", "CUSTOM"})
VIEWERS = frozenset({"INTERNAL", "FOLDER", "EXTERNAL_LIBRARY"})
EXTERNAL_LIBRARY_PROVIDERS = frozenset(
    {"NONE", "OBSIDIAN", "LOGSEQ", "ZOTERO", "CUSTOM"}
)
LANGUAGES = frozenset({"zh-CN", "en-US", "ja-JP"})
CLOSE_BEHAVIORS = frozenset({"ASK", "EXIT", "TRAY"})
REFERENCE = re.compile(
    r"^REF:windows:(?:PR-OS|PR-OS-T04-SYNTHETIC)/[A-Za-z0-9._-]{1,64}/[A-Za-z0-9._-]{1,64}$"
)
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
LOCAL_MODEL_PROFILE = re.compile(r"^local:[0-9a-f]{24}$")
MODEL_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
API_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
CLI_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
SENSITIVE_KEY = re.compile(
    r"(?i)^(api[_-]?key|authorization|cookie|credential[_-]?value|password|secret|token|private[_-]?payload|gold|holdout)$"
)
SENSITIVE_VALUE = (
    re.compile(r"(?i)(?:^|[^A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

WORKFLOW_DEFINITIONS = (
    ("ingest", "文档处理与内容理解", True, True, "PARSER_PROFILE"),
    ("chunk_embedding", "Chunk Embedding", True, True, None),
    ("card_distill", "Card 蒸馏", True, True, "DISTILLATION_PROFILE"),
    ("transport_review", "Card 搬运类校核", False, True, "AUTO_HETEROGENEOUS"),
    ("card_admission", "Card 准入", True, False, None),
    ("context_pack", "Context Pack", True, True, None),
    ("analysis", "Analysis 分析", True, True, "ANALYSIS_PROFILE"),
    ("judgment_review", "判断类异源核对", False, True, "AUTO_HETEROGENEOUS"),
    ("human_judgment", "人类最终判断", True, False, None),
)
WORKFLOW_IDS = tuple(row[0] for row in WORKFLOW_DEFINITIONS)
DEFAULT_PROFILE_BY_NODE = {row[0]: row[4] for row in WORKFLOW_DEFINITIONS}
PROFILE_SOURCE_BY_NODE: dict[str, str] = {}
BUILTIN_PROFILE_REFS = frozenset(
    profile_ref for profile_ref in DEFAULT_PROFILE_BY_NODE.values() if profile_ref
) | frozenset(
    {
        "OCR_RESCUE_PROFILE",
        "DISTILLATION_FLASH_PROFILE",
        "CONTEXT_PROFILE",
        "CONTEXT_NO_RERANK_PROFILE",
        "ANALYSIS_OPUS_PROFILE",
        "ANALYSIS_FALLBACK_PROFILE",
    }
)
EXCLUSIVE_BUILTIN_PROFILE_OWNERS = {
    "PARSER_PROFILE": "ingest",
    "OCR_RESCUE_PROFILE": "ingest",
    "CONTEXT_PROFILE": "context_pack",
    "CONTEXT_NO_RERANK_PROFILE": "context_pack",
}
LOCKED_ENABLED_IDS = frozenset(
    row[0] for row in WORKFLOW_DEFINITIONS if row[2]
)
OPTIONAL_IDS = frozenset({"transport_review", "judgment_review"})
NO_MODEL_IDS = frozenset({"card_admission", "human_judgment"})
DERIVED_PROFILE_IDS = frozenset(PROFILE_SOURCE_BY_NODE)

CLI_DEFINITIONS = (
    ("codex-cli", "codex_cli", "Codex CLI", "codex"),
    ("claude-code", "claude_code", "Claude Code", "claude"),
    ("gemini-cli", "gemini_cli", "Gemini CLI", "gemini"),
    ("qwen-code", "qwen_code", "Qwen Code", "qwen"),
    ("kimi-code", "kimi_code", "Kimi Code", "kimi"),
    ("codebuddy-code", "codebuddy_code", "CodeBuddy Code", "codebuddy"),
    ("github-copilot-cli", "github_copilot_cli", "GitHub Copilot CLI", "copilot"),
)
CLI_CONFIG_IDS = tuple(row[0] for row in CLI_DEFINITIONS)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def immutable(value: Any) -> Any:
    return json.loads(canonical_json_bytes(value).decode("utf-8"))


def scan_sensitive(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            rendered = str(key)
            if SENSITIVE_KEY.fullmatch(rendered):
                raise ValueError(f"SENSITIVE_KEY_REJECTED:{path}.{rendered}")
            scan_sensitive(child, f"{path}.{rendered}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_sensitive(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in SENSITIVE_VALUE):
            raise ValueError(f"SENSITIVE_VALUE_REJECTED:{path}")


def _exact_fields(
    value: Mapping[str, Any], required: set[str], label: str
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise ValueError(f"{label}_MISSING_FIELD:{','.join(missing)}")
    if unknown:
        raise ValueError(f"{label}_UNKNOWN_FIELD:{','.join(unknown)}")


def _text(value: Any, label: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label}_INVALID")
    return value.strip()


def _display_name_identity(value: str) -> str:
    """Return the comparison identity for user-visible provider/model names."""
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def normalize_external_api_base_url(value: Any) -> str:
    """Validate a user-authorized public HTTPS API base without resolving DNS."""
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("MODEL_API_BASE_URL_INVALID")
    normalized = value.strip().rstrip("/")
    if normalized != value.rstrip("/") or not normalized:
        raise ValueError("MODEL_API_BASE_URL_INVALID")
    if any(character.isspace() or ord(character) < 32 for character in normalized):
        raise ValueError("MODEL_API_BASE_URL_INVALID")
    if "\\" in normalized:
        raise ValueError("MODEL_API_BASE_URL_INVALID")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as error:
        raise ValueError("MODEL_API_BASE_URL_INVALID") from error
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("MODEL_API_BASE_URL_INVALID")
    host = parsed.hostname.casefold().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("MODEL_API_BASE_URL_INVALID")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("MODEL_API_BASE_URL_INVALID")
    netloc = parsed.hostname
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit(("https", netloc, parsed.path.rstrip("/"), "", ""))


def _integer(
    value: Any, label: str, minimum: int, maximum: int
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{label}_INVALID")
    return value


def default_settings(
    *,
    workspace_root: str = "sandbox://workspace",
    artifact_root: str = "sandbox://artifacts",
) -> dict[str, Any]:
    nodes = []
    for node_id, label, locked, configurable, profile_ref in WORKFLOW_DEFINITIONS:
        nodes.append(
            {
                "node_id": node_id,
                "label": label,
                "enabled": True,
                "retry_count": 2 if configurable else 0,
                "profile_ref": profile_ref,
                "profile_source_node_id": PROFILE_SOURCE_BY_NODE.get(node_id),
                "test_score": None,
            }
        )
    cli_services = [
        {
            "config_id": config_id,
            "adapter_id": adapter_id,
            "display_name": display_name,
            "executable": executable,
            "enabled": False,
            "connection_status": "UNVERIFIED",
            "models": [],
        }
        for config_id, adapter_id, display_name, executable in CLI_DEFINITIONS
    ]
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "mode": "NORMAL",
        "directories": {
            "workspace_root": workspace_root,
            "artifact_root": artifact_root,
            "external_library": {
                "enabled": False,
                "provider_kind": "NONE",
                "display_name": "",
                "root": None,
            },
        },
        "model_services": [],
        "cli_services": cli_services,
        "credential_references": [],
        "workflow": {"nodes": nodes},
        "preferences": {
            "document_viewer": "INTERNAL",
            "auto_open_preview": True,
            "external_refresh": True,
            "notifications_enabled": True,
            "task_complete_notification": True,
            "task_error_notification": True,
            "approval_notification": True,
            "weekly_report_notification": True,
            "notification_sound": False,
            "do_not_disturb_start": "22:00",
            "do_not_disturb_end": "08:00",
            "notification_open_task": True,
            "restore_last_view": False,
            "restore_last_document": False,
            "close_behavior": "ASK",
            "warn_on_close_running": True,
            "launch_at_startup": False,
            "keep_tasks_in_background": True,
            "remember_panel_state": True,
            "proxy_mode": "SYSTEM",
            "proxy_address": "",
            "request_timeout_seconds": 60,
            "log_level": "NORMAL",
            "retention_days": 30,
            "cache_limit_mb": 5120,
            "telemetry_enabled": False,
            "language": "zh-CN",
            "font_scale_percent": 100,
            "show_full_tooltips": True,
        },
    }


def validate_settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("SETTINGS_TYPE_INVALID")
    _exact_fields(
        value,
        {
            "schema_version",
            "mode",
            "directories",
            "model_services",
            "cli_services",
            "credential_references",
            "workflow",
            "preferences",
        },
        "SETTINGS",
    )
    if value["schema_version"] != SETTINGS_SCHEMA_VERSION:
        raise ValueError("SETTINGS_SCHEMA_UNSUPPORTED")
    scan_sensitive(value)
    mode = value["mode"]
    if mode not in MODES:
        raise ValueError("SETTINGS_MODE_INVALID")
    directories = _validate_directories(value["directories"])
    model_services = _validate_model_services(value["model_services"])
    cli_services = _validate_cli_services(value["cli_services"])
    references = _validate_credential_references(value["credential_references"])
    reference_ids = {row["credential_ref"] for row in references}
    for row in model_services:
        if row["credential_ref"] not in reference_ids:
            raise ValueError("MODEL_SERVICE_CREDENTIAL_REFERENCE_MISSING")
    workflow = _validate_workflow(value["workflow"])
    configured_profile_refs = {row["config_id"] for row in model_services}
    configured_profile_refs.update(
        model["profile_ref"]
        for service in cli_services
        for model in service["models"]
    )
    allowed_profile_refs = BUILTIN_PROFILE_REFS | configured_profile_refs
    for node in workflow["nodes"]:
        profile_ref = node["profile_ref"]
        if (
            profile_ref is not None
            and profile_ref not in allowed_profile_refs
            and LOCAL_MODEL_PROFILE.fullmatch(profile_ref) is None
        ):
            raise ValueError("WORKFLOW_PROFILE_REFERENCE_NOT_CONFIGURED")
        exclusive_owner = EXCLUSIVE_BUILTIN_PROFILE_OWNERS.get(profile_ref)
        if exclusive_owner is not None and node["node_id"] != exclusive_owner:
            raise ValueError(
                f"WORKFLOW_EXCLUSIVE_PROFILE_REFERENCE_INVALID:{profile_ref}:{node['node_id']}"
            )
    model_services_by_id = {row["config_id"]: row for row in model_services}
    cli_profile_refs = {
        model["profile_ref"]
        for service in cli_services
        for model in service["models"]
    }
    for node in workflow["nodes"]:
        if node["profile_ref"] in cli_profile_refs and node["node_id"] in {
            "chunk_embedding",
        }:
            raise ValueError(
                f"WORKFLOW_MODEL_CAPABILITY_MISMATCH:{node['node_id']}:CLI"
            )
        service = model_services_by_id.get(node["profile_ref"])
        if service is None:
            continue
        capability = infer_api_model_capability(
            service["provider"], service.get("model_name", "")
        )
        if not workflow_node_accepts_capability(node["node_id"], capability):
            raise ValueError(
                f"WORKFLOW_MODEL_CAPABILITY_MISMATCH:{node['node_id']}:{capability}"
            )
    fallback_ref = workflow["nodes"][0].get("fallback_profile_ref")
    if fallback_ref is not None:
        if fallback_ref not in configured_profile_refs and LOCAL_MODEL_PROFILE.fullmatch(fallback_ref) is None:
            raise ValueError("WORKFLOW_FALLBACK_REFERENCE_NOT_CONFIGURED")
        fallback_service = model_services_by_id.get(fallback_ref)
        if fallback_service and not workflow_node_accepts_capability("ingest", infer_api_model_capability(
            fallback_service["provider"], fallback_service.get("model_name", "")
        )):
            raise ValueError("WORKFLOW_FALLBACK_CAPABILITY_MISMATCH")
    _validate_heterogeneous_model_relations(workflow, model_services, cli_services)
    preferences = _validate_preferences(value["preferences"], mode)
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "mode": mode,
        "directories": directories,
        "model_services": model_services,
        "cli_services": cli_services,
        "credential_references": references,
        "workflow": workflow,
        "preferences": preferences,
    }


def _validate_directories(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("DIRECTORIES_TYPE_INVALID")
    _exact_fields(
        value,
        {"workspace_root", "artifact_root", "external_library"},
        "DIRECTORIES",
    )
    workspace = _text(value["workspace_root"], "WORKSPACE_ROOT")
    artifacts = _text(value["artifact_root"], "ARTIFACT_ROOT")
    if ".." in workspace.replace("\\", "/").split("/"):
        raise ValueError("WORKSPACE_TRAVERSAL_REJECTED")
    if ".." in artifacts.replace("\\", "/").split("/"):
        raise ValueError("ARTIFACT_TRAVERSAL_REJECTED")
    external = value["external_library"]
    if not isinstance(external, Mapping):
        raise ValueError("EXTERNAL_LIBRARY_TYPE_INVALID")
    _exact_fields(
        external,
        {"enabled", "provider_kind", "display_name", "root"},
        "EXTERNAL_LIBRARY",
    )
    if not isinstance(external["enabled"], bool):
        raise ValueError("EXTERNAL_LIBRARY_ENABLED_INVALID")
    provider_kind = external["provider_kind"]
    if provider_kind not in EXTERNAL_LIBRARY_PROVIDERS:
        raise ValueError("EXTERNAL_LIBRARY_PROVIDER_INVALID")
    display_name = external["display_name"]
    if not isinstance(display_name, str) or len(display_name) > 80:
        raise ValueError("EXTERNAL_LIBRARY_NAME_INVALID")
    root = external["root"]
    if root is not None and (not isinstance(root, str) or not root or len(root) > 512):
        raise ValueError("EXTERNAL_LIBRARY_ROOT_INVALID")
    if external["enabled"] and (
        provider_kind == "NONE" or not display_name.strip() or not root
    ):
        raise ValueError("EXTERNAL_LIBRARY_INCOMPLETE")
    if not external["enabled"] and provider_kind != "NONE":
        raise ValueError("EXTERNAL_LIBRARY_DISABLED_PROVIDER_INVALID")
    return {
        "workspace_root": workspace,
        "artifact_root": artifacts,
        "external_library": {
            "enabled": external["enabled"],
            "provider_kind": provider_kind,
            "display_name": display_name.strip(),
            "root": root,
        },
    }


def _validate_model_services(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 16:
        raise ValueError("MODEL_SERVICES_INVALID")
    accepted = []
    seen = set()
    seen_profiles = set()
    for row in value:
        if not isinstance(row, Mapping):
            raise ValueError("MODEL_SERVICE_TYPE_INVALID")
        required = {
            "config_id",
            "provider",
            "credential_ref",
            "plan",
            "thinking",
            "connection_status",
        }
        descriptor_fields = {"model_name", "tier", "thinking_mode"}
        connection_fields = {"api_protocol", "api_base_url"}
        platform_fields = {"api_platform"}
        missing = sorted(required - set(row))
        unknown = sorted(
            set(row)
            - required
            - descriptor_fields
            - connection_fields
            - platform_fields
        )
        if missing:
            raise ValueError(f"MODEL_SERVICE_MISSING_FIELD:{','.join(missing)}")
        if unknown:
            raise ValueError(f"MODEL_SERVICE_UNKNOWN_FIELD:{','.join(unknown)}")
        config_id = _text(row["config_id"], "MODEL_CONFIG_ID", 128)
        if not IDENTIFIER.fullmatch(config_id) or config_id in seen:
            raise ValueError("MODEL_CONFIG_ID_INVALID")
        seen.add(config_id)
        provider = _text(row["provider"], "MODEL_PROVIDER", 64)
        provider_identity = _display_name_identity(provider)
        credential_ref = row["credential_ref"]
        if not isinstance(credential_ref, str) or not REFERENCE.fullmatch(
            credential_ref
        ):
            raise ValueError("MODEL_CREDENTIAL_REFERENCE_INVALID")
        if row["plan"] not in PLANS:
            raise ValueError("MODEL_PLAN_INVALID")
        if not isinstance(row["thinking"], bool):
            raise ValueError("MODEL_THINKING_INVALID")
        if row["connection_status"] not in CONNECTION_STATES:
            raise ValueError("MODEL_CONNECTION_STATUS_INVALID")
        accepted_row = {
            "config_id": config_id,
            "provider": provider,
            "credential_ref": credential_ref,
            "plan": row["plan"],
            "thinking": row["thinking"],
            "connection_status": row["connection_status"],
        }
        if descriptor_fields & set(row):
            descriptor = {}
            error_labels = {
                "model_name": "MODEL_NAME_INVALID",
                "tier": "MODEL_TIER_INVALID",
                "thinking_mode": "MODEL_THINKING_MODE_INVALID",
            }
            for field in ("model_name", "tier", "thinking_mode"):
                segment = row.get(field, "")
                maximum = 128 if field == "model_name" else 80
                pattern = API_MODEL_NAME if field == "model_name" else MODEL_SEGMENT
                if not isinstance(segment, str) or len(segment) > maximum:
                    raise ValueError(error_labels[field])
                segment = segment.strip()
                if segment and not pattern.fullmatch(segment):
                    raise ValueError(error_labels[field])
                descriptor[field] = segment
            if bool(descriptor["model_name"]) != bool(descriptor["tier"]):
                raise ValueError("MODEL_DESCRIPTOR_INCOMPLETE")
            if descriptor["thinking_mode"] and not descriptor["model_name"]:
                raise ValueError("MODEL_DESCRIPTOR_INCOMPLETE")
            accepted_row.update(descriptor)
        if connection_fields & set(row):
            if not connection_fields.issubset(row):
                raise ValueError("MODEL_API_CONNECTION_INCOMPLETE")
            protocol = row["api_protocol"]
            base_url = row["api_base_url"]
            if (
                not isinstance(protocol, str)
                or protocol not in API_PROTOCOLS
                or not isinstance(base_url, str)
            ):
                raise ValueError("MODEL_API_CONNECTION_INVALID")
            base_url = base_url.strip()
            if protocol == "AUTO":
                if base_url:
                    raise ValueError("MODEL_API_CONNECTION_INCOMPLETE")
            else:
                if not base_url:
                    raise ValueError("MODEL_API_CONNECTION_INCOMPLETE")
                base_url = normalize_external_api_base_url(base_url)
            accepted_row.update(api_protocol=protocol, api_base_url=base_url)
        if "api_platform" in row:
            platform = row["api_platform"]
            if platform not in {"AUTO", *KIMI_ENDPOINTS}:
                raise ValueError("MODEL_API_PLATFORM_INVALID")
            if not is_kimi_service(accepted_row):
                raise ValueError("MODEL_API_PLATFORM_PROVIDER_INVALID")
            protocol = accepted_row.get("api_protocol", "AUTO")
            base_url = accepted_row.get("api_base_url", "")
            if platform == "AUTO":
                unresolved = protocol == "AUTO" and not base_url
                resolved_cache = (
                    protocol == "OPENAI_COMPATIBLE"
                    and base_url in KIMI_ENDPOINTS.values()
                )
                if not (unresolved or resolved_cache):
                    raise ValueError("MODEL_API_PLATFORM_CONNECTION_MISMATCH")
            elif (
                protocol != "OPENAI_COMPATIBLE"
                or base_url != KIMI_ENDPOINTS[platform]
            ):
                raise ValueError("MODEL_API_PLATFORM_CONNECTION_MISMATCH")
            accepted_row["api_platform"] = platform
        profile_identity = (
            provider_identity,
            _display_name_identity(accepted_row.get("model_name", "")),
            _display_name_identity(accepted_row.get("tier", "")),
            _display_name_identity(accepted_row.get("thinking_mode", "")),
        )
        if profile_identity in seen_profiles:
            raise ValueError("MODEL_SERVICE_PROFILE_DUPLICATE")
        seen_profiles.add(profile_identity)
        accepted.append(accepted_row)
    return sorted(accepted, key=lambda row: row["config_id"])


def _validate_cli_services(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(CLI_DEFINITIONS):
        raise ValueError("CLI_SERVICES_INVALID")
    rows_by_id: dict[str, Mapping[str, Any]] = {}
    for row in value:
        if not isinstance(row, Mapping):
            raise ValueError("CLI_SERVICE_TYPE_INVALID")
        _exact_fields(
            row,
            {
                "config_id",
                "adapter_id",
                "display_name",
                "executable",
                "enabled",
                "connection_status",
                "models",
            },
            "CLI_SERVICE",
        )
        config_id = _text(row["config_id"], "CLI_CONFIG_ID", 128)
        if config_id in rows_by_id:
            raise ValueError("CLI_CONFIG_ID_INVALID")
        rows_by_id[config_id] = row
    if set(rows_by_id) != set(CLI_CONFIG_IDS):
        raise ValueError("CLI_ADAPTER_SET_MISMATCH")

    accepted: list[dict[str, Any]] = []
    seen_profile_refs: set[str] = set()
    for config_id, adapter_id, display_name, default_executable in CLI_DEFINITIONS:
        row = rows_by_id[config_id]
        if row["adapter_id"] != adapter_id or row["display_name"] != display_name:
            raise ValueError("CLI_ADAPTER_IDENTITY_DRIFT")
        executable = _text(row["executable"], "CLI_EXECUTABLE", 260)
        if any(ord(character) < 32 or ord(character) == 127 for character in executable):
            raise ValueError("CLI_EXECUTABLE_INVALID")
        if not isinstance(row["enabled"], bool):
            raise ValueError("CLI_ENABLED_INVALID")
        if row["connection_status"] not in CONNECTION_STATES:
            raise ValueError("CLI_CONNECTION_STATUS_INVALID")
        models = row["models"]
        if not isinstance(models, list) or len(models) > 32:
            raise ValueError("CLI_MODELS_INVALID")
        accepted_models = []
        seen_model_names: set[str] = set()
        for model in models:
            if not isinstance(model, Mapping):
                raise ValueError("CLI_MODEL_TYPE_INVALID")
            _exact_fields(
                model,
                {
                    "profile_ref",
                    "display_name",
                    "model_name",
                    "thinking_mode",
                    "connection_status",
                },
                "CLI_MODEL",
            )
            profile_ref = _text(model["profile_ref"], "CLI_MODEL_PROFILE_REF", 128)
            if (
                not IDENTIFIER.fullmatch(profile_ref)
                or not profile_ref.startswith(f"cli:{config_id}:")
                or profile_ref in seen_profile_refs
            ):
                raise ValueError("CLI_MODEL_PROFILE_REF_INVALID")
            seen_profile_refs.add(profile_ref)
            model_name = _text(model["model_name"], "CLI_MODEL_NAME", 128)
            if not CLI_MODEL_NAME.fullmatch(model_name):
                raise ValueError("CLI_MODEL_NAME_INVALID")
            model_identity = _display_name_identity(model_name)
            if model_identity in seen_model_names:
                raise ValueError("CLI_MODEL_NAME_DUPLICATE")
            seen_model_names.add(model_identity)
            thinking_mode = model["thinking_mode"]
            if not isinstance(thinking_mode, str) or len(thinking_mode) > 80:
                raise ValueError("CLI_MODEL_THINKING_MODE_INVALID")
            thinking_mode = thinking_mode.strip()
            if thinking_mode and not MODEL_SEGMENT.fullmatch(thinking_mode):
                raise ValueError("CLI_MODEL_THINKING_MODE_INVALID")
            if model["connection_status"] not in CONNECTION_STATES:
                raise ValueError("CLI_MODEL_CONNECTION_STATUS_INVALID")
            accepted_models.append(
                {
                    "profile_ref": profile_ref,
                    "display_name": _text(model["display_name"], "CLI_MODEL_DISPLAY_NAME", 80),
                    "model_name": model_name,
                    "thinking_mode": thinking_mode,
                    "connection_status": model["connection_status"],
                }
            )
        accepted.append(
            {
                "config_id": config_id,
                "adapter_id": adapter_id,
                "display_name": display_name,
                "executable": executable or default_executable,
                "enabled": row["enabled"],
                "connection_status": row["connection_status"],
                "models": sorted(accepted_models, key=lambda item: item["profile_ref"]),
            }
        )
    return accepted


def _validate_credential_references(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("CREDENTIAL_REFERENCES_INVALID")
    accepted = []
    seen = set()
    for row in value:
        if not isinstance(row, Mapping):
            raise ValueError("CREDENTIAL_REFERENCE_ROW_INVALID")
        _exact_fields(
            row,
            {
                "credential_ref",
                "provider",
                "logical_key",
                "status",
                "created_at",
                "updated_at",
            },
            "CREDENTIAL_REFERENCE",
        )
        reference = row["credential_ref"]
        if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
            raise ValueError("CREDENTIAL_REFERENCE_INVALID")
        if reference in seen:
            raise ValueError("CREDENTIAL_REFERENCE_DUPLICATE")
        seen.add(reference)
        status = row["status"]
        if status not in {"STORED_UNVERIFIED", "MISSING", "DELETED"}:
            raise ValueError("CREDENTIAL_REFERENCE_STATUS_INVALID")
        accepted.append(
            {
                "credential_ref": reference,
                "provider": _text(row["provider"], "CREDENTIAL_PROVIDER", 64),
                "logical_key": _text(
                    row["logical_key"], "CREDENTIAL_LOGICAL_KEY", 64
                ),
                "status": status,
                "created_at": _text(row["created_at"], "CREDENTIAL_CREATED_AT", 64),
                "updated_at": _text(row["updated_at"], "CREDENTIAL_UPDATED_AT", 64),
            }
        )
    return sorted(accepted, key=lambda row: row["credential_ref"])


def _validate_workflow(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("WORKFLOW_TYPE_INVALID")
    _exact_fields(value, {"nodes"}, "WORKFLOW")
    nodes = value["nodes"]
    if not isinstance(nodes, list) or [row.get("node_id") for row in nodes] != list(
        WORKFLOW_IDS
    ):
        raise ValueError("WORKFLOW_NODE_DENOMINATOR_DRIFT")
    accepted = []
    definitions = {row[0]: row for row in WORKFLOW_DEFINITIONS}
    for row in nodes:
        if not isinstance(row, Mapping):
            raise ValueError("WORKFLOW_NODE_TYPE_INVALID")
        _exact_fields(
            {key: value for key, value in row.items() if key != "fallback_profile_ref"},
            {
                "node_id",
                "label",
                "enabled",
                "retry_count",
                "profile_ref",
                "profile_source_node_id",
                "test_score",
            },
            "WORKFLOW_NODE",
        )
        node_id = row["node_id"]
        _, label, locked_enabled, configurable, default_profile = definitions[node_id]
        if row["label"] != label:
            raise ValueError("WORKFLOW_PUBLIC_LABEL_DRIFT")
        if not isinstance(row["enabled"], bool):
            raise ValueError("WORKFLOW_NODE_ENABLED_INVALID")
        if locked_enabled and not row["enabled"]:
            raise ValueError("WORKFLOW_LOCKED_NODE_DISABLE_REJECTED")
        retry_count = _integer(row["retry_count"], "WORKFLOW_RETRY", 0, 10)
        profile_ref = row["profile_ref"]
        if profile_ref is not None and (
            not isinstance(profile_ref, str)
            or not IDENTIFIER.fullmatch(profile_ref)
        ):
            raise ValueError("WORKFLOW_PROFILE_REFERENCE_INVALID")
        fallback_ref = row.get("fallback_profile_ref")
        if fallback_ref is not None and (
            node_id != "ingest" or not isinstance(fallback_ref, str)
            or not IDENTIFIER.fullmatch(fallback_ref) or fallback_ref == profile_ref
        ):
            raise ValueError("WORKFLOW_INGEST_FALLBACK_INVALID")
        if node_id == "ingest" and profile_ref is None:
            raise ValueError("WORKFLOW_INGEST_PRIMARY_REQUIRED")
        profile_source_node_id = row["profile_source_node_id"]
        expected_profile_source = PROFILE_SOURCE_BY_NODE.get(node_id)
        if profile_source_node_id != expected_profile_source:
            raise ValueError(
                f"WORKFLOW_PROFILE_SOURCE_INVALID:{node_id}"
            )
        if node_id in DERIVED_PROFILE_IDS and profile_ref is not None:
            raise ValueError(
                f"WORKFLOW_DERIVED_PROFILE_REFERENCE_REJECTED:{node_id}"
            )
        if not configurable and (retry_count != 0 or profile_ref is not None):
            raise ValueError("WORKFLOW_NON_MODEL_NODE_CONFIG_REJECTED")
        test_score = row["test_score"]
        if test_score is not None:
            test_score = _integer(test_score, "WORKFLOW_TEST_SCORE", 0, 100)
        if not configurable and test_score is not None:
            raise ValueError("WORKFLOW_NON_MODEL_NODE_TEST_SCORE_REJECTED")
        if node_id in DERIVED_PROFILE_IDS and test_score is not None:
            raise ValueError("WORKFLOW_DERIVED_PROFILE_TEST_SCORE_REJECTED")
        if node_id in NO_MODEL_IDS and default_profile is not None:
            raise ValueError("WORKFLOW_DEFINITION_INTERNAL_ERROR")
        accepted.append(
            {
                "node_id": node_id,
                "label": label,
                "enabled": row["enabled"],
                "retry_count": retry_count,
                "profile_ref": profile_ref,
                "profile_source_node_id": profile_source_node_id,
                "test_score": test_score,
            }
        )
        if node_id == "ingest" and "fallback_profile_ref" in row:
            accepted[-1]["fallback_profile_ref"] = fallback_ref
    return {"nodes": accepted}


def _configured_model_identity(
    profile_ref: str | None,
    services_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...] | None:
    if profile_ref is None or profile_ref == "AUTO_HETEROGENEOUS":
        return None
    service = services_by_id.get(profile_ref)
    if service is None:
        return ("PROFILE", profile_ref.casefold())
    model_name = str(service.get("model_name") or "").strip()
    tier = str(service.get("tier") or "").strip()
    thinking_mode = str(service.get("thinking_mode") or "").strip()
    if not model_name or not tier:
        return ("CONFIG", profile_ref.casefold())
    return (
        "MODEL",
        _display_name_identity(model_name),
        _display_name_identity(tier),
        _display_name_identity(thinking_mode),
    )


def _validate_heterogeneous_model_relations(
    workflow: Mapping[str, Any],
    model_services: Sequence[Mapping[str, Any]],
    cli_services: Sequence[Mapping[str, Any]],
) -> None:
    nodes = {row["node_id"]: row for row in workflow["nodes"]}
    services_by_id = {row["config_id"]: row for row in model_services}
    for service in cli_services:
        for model in service["models"]:
            services_by_id[model["profile_ref"]] = {
                "config_id": model["profile_ref"],
                "model_name": model["model_name"],
                "tier": service["adapter_id"],
                "thinking_mode": model["thinking_mode"],
            }
    for producer_id, reviewer_id in (
        ("card_distill", "transport_review"),
        ("analysis", "judgment_review"),
    ):
        producer_identity = _configured_model_identity(
            nodes[producer_id]["profile_ref"], services_by_id
        )
        reviewer_identity = _configured_model_identity(
            nodes[reviewer_id]["profile_ref"], services_by_id
        )
        if (
            producer_identity is not None
            and reviewer_identity is not None
            and producer_identity == reviewer_identity
        ):
            raise ValueError(
                f"WORKFLOW_HETEROGENEOUS_MODEL_CONFLICT:{reviewer_id}:{producer_id}"
            )


def _validate_preferences(value: Any, mode: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("PREFERENCES_TYPE_INVALID")
    fields = {
        "document_viewer",
        "auto_open_preview",
        "external_refresh",
        "notifications_enabled",
        "task_complete_notification",
        "task_error_notification",
        "approval_notification",
        "weekly_report_notification",
        "notification_sound",
        "do_not_disturb_start",
        "do_not_disturb_end",
        "notification_open_task",
        "restore_last_view",
        "restore_last_document",
        "close_behavior",
        "warn_on_close_running",
        "launch_at_startup",
        "keep_tasks_in_background",
        "remember_panel_state",
        "proxy_mode",
        "proxy_address",
        "request_timeout_seconds",
        "log_level",
        "retention_days",
        "cache_limit_mb",
        "telemetry_enabled",
        "language",
        "font_scale_percent",
        "show_full_tooltips",
    }
    _exact_fields(value, fields, "PREFERENCES")
    if value["document_viewer"] not in VIEWERS:
        raise ValueError("DOCUMENT_VIEWER_INVALID")
    if value["proxy_mode"] not in PROXY_MODES:
        raise ValueError("PROXY_MODE_INVALID")
    if mode == "NORMAL" and value["proxy_mode"] == "CUSTOM":
        raise ValueError("CUSTOM_PROXY_REQUIRES_ADVANCED_MODE")
    proxy_address = value["proxy_address"]
    if not isinstance(proxy_address, str) or len(proxy_address) > 256:
        raise ValueError("PROXY_ADDRESS_INVALID")
    if value["proxy_mode"] == "CUSTOM" and not proxy_address.strip():
        raise ValueError("CUSTOM_PROXY_ADDRESS_REQUIRED")
    if value["close_behavior"] not in CLOSE_BEHAVIORS:
        raise ValueError("CLOSE_BEHAVIOR_INVALID")
    if value["log_level"] not in {"NORMAL", "DETAILED"}:
        raise ValueError("LOG_LEVEL_INVALID")
    if value["language"] not in LANGUAGES:
        raise ValueError("LANGUAGE_INVALID")
    for key in (
        "notifications_enabled",
        "auto_open_preview",
        "external_refresh",
        "task_complete_notification",
        "task_error_notification",
        "approval_notification",
        "weekly_report_notification",
        "notification_sound",
        "notification_open_task",
        "restore_last_view",
        "restore_last_document",
        "warn_on_close_running",
        "launch_at_startup",
        "keep_tasks_in_background",
        "remember_panel_state",
        "telemetry_enabled",
        "show_full_tooltips",
    ):
        if not isinstance(value[key], bool):
            raise ValueError(f"{key.upper()}_INVALID")
    for key in ("do_not_disturb_start", "do_not_disturb_end"):
        if not isinstance(value[key], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value[key]):
            raise ValueError(f"{key.upper()}_INVALID")
    return {
        "document_viewer": value["document_viewer"],
        "auto_open_preview": value["auto_open_preview"],
        "external_refresh": value["external_refresh"],
        "notifications_enabled": value["notifications_enabled"],
        "task_complete_notification": value["task_complete_notification"],
        "task_error_notification": value["task_error_notification"],
        "approval_notification": value["approval_notification"],
        "weekly_report_notification": value["weekly_report_notification"],
        "notification_sound": value["notification_sound"],
        "do_not_disturb_start": value["do_not_disturb_start"],
        "do_not_disturb_end": value["do_not_disturb_end"],
        "notification_open_task": value["notification_open_task"],
        "restore_last_view": value["restore_last_view"],
        "restore_last_document": value["restore_last_document"],
        "close_behavior": value["close_behavior"],
        "warn_on_close_running": value["warn_on_close_running"],
        "launch_at_startup": value["launch_at_startup"],
        "keep_tasks_in_background": value["keep_tasks_in_background"],
        "remember_panel_state": value["remember_panel_state"],
        "proxy_mode": value["proxy_mode"],
        "proxy_address": proxy_address.strip(),
        "request_timeout_seconds": _integer(
            value["request_timeout_seconds"], "REQUEST_TIMEOUT_SECONDS", 1, 600
        ),
        "log_level": value["log_level"],
        "retention_days": _integer(
            value["retention_days"], "RETENTION_DAYS", 1, 365
        ),
        "cache_limit_mb": _integer(
            value["cache_limit_mb"], "CACHE_LIMIT_MB", 128, 102400
        ),
        "telemetry_enabled": value["telemetry_enabled"],
        "language": value["language"],
        "font_scale_percent": _integer(
            value["font_scale_percent"], "FONT_SCALE_PERCENT", 80, 200
        ),
        "show_full_tooltips": value["show_full_tooltips"],
    }


def risk_ids(settings: Mapping[str, Any]) -> list[str]:
    accepted = validate_settings(settings)
    risks = []
    for node in accepted["workflow"]["nodes"]:
        if node["node_id"] in NO_MODEL_IDS:
            continue
        retry = node["retry_count"]
        if retry <= 1:
            risks.append(f"RETRY_LOW:{node['node_id']}:{retry}")
        elif retry > 5:
            risks.append(f"RETRY_HIGH:{node['node_id']}:{retry}")
    return sorted(risks)


def settings_field_contract() -> dict[str, Any]:
    fields = [
        ("mode", "enum", "NORMAL", "USER", "PUBLIC", "immediate"),
        ("directories.workspace_root", "path", None, "USER", "PATH", "next_job"),
        ("directories.artifact_root", "path", None, "USER", "PATH", "next_job"),
        (
            "directories.external_library",
            "object",
            {"enabled": False},
            "USER",
            "PATH",
            "immediate",
        ),
        ("model_services[].provider", "string", None, "USER", "PUBLIC", "next_job"),
        (
            "model_services[].api_protocol",
            "enum_optional",
            "AUTO",
            "USER",
            "PUBLIC",
            "next_job",
        ),
        (
            "model_services[].api_base_url",
            "url_optional",
            "",
            "USER",
            "PUBLIC",
            "next_job",
        ),
        (
            "model_services[].credential_ref",
            "reference",
            None,
            "CREDENTIAL_MANAGER",
            "REFERENCE_ONLY",
            "next_job",
        ),
        (
            "model_services[].plan",
            "enum",
            "ECONOMY",
            "USER",
            "PUBLIC",
            "next_job",
        ),
        (
            "model_services[].thinking",
            "boolean",
            False,
            "USER",
            "PUBLIC",
            "next_job",
        ),
        (
            "model_services[].model_name",
            "string",
            "",
            "USER",
            "PUBLIC",
            "next_job",
        ),
        (
            "model_services[].tier",
            "string",
            "",
            "USER",
            "PUBLIC",
            "next_job",
        ),
        (
            "model_services[].thinking_mode",
            "string_optional",
            "",
            "USER",
            "PUBLIC",
            "next_job",
        ),
        (
            "model_services[].connection_status",
            "enum",
            "UNVERIFIED",
            "DIAGNOSTIC",
            "PUBLIC",
            "diagnostic_only",
        ),
        ("cli_services[].enabled", "boolean", False, "USER", "PUBLIC", "next_job"),
        ("cli_services[].executable", "string", None, "USER", "PATH", "next_job"),
        (
            "cli_services[].connection_status",
            "enum",
            "UNVERIFIED",
            "DIAGNOSTIC",
            "PUBLIC",
            "diagnostic_only",
        ),
        ("cli_services[].models[].display_name", "string", None, "USER", "PUBLIC", "next_job"),
        ("cli_services[].models[].model_name", "string", None, "USER", "PUBLIC", "next_job"),
        ("cli_services[].models[].thinking_mode", "string_optional", "", "USER", "PUBLIC", "next_job"),
        (
            "cli_services[].models[].connection_status",
            "enum",
            "UNVERIFIED",
            "DIAGNOSTIC",
            "PUBLIC",
            "diagnostic_only",
        ),
        (
            "workflow.nodes[].enabled",
            "boolean",
            True,
            "USER_WITH_LOCKS",
            "PUBLIC",
            "next_job",
        ),
        (
            "workflow.nodes[].retry_count",
            "integer",
            2,
            "USER_WITH_RISK_CONFIRMATION",
            "PUBLIC",
            "next_job",
        ),
        (
            "workflow.nodes[].profile_ref",
            "reference",
            None,
            "PROFILE_PROJECTION",
            "REFERENCE_ONLY",
            "next_job",
        ),
        (
            "workflow.nodes[].profile_source_node_id",
            "reference_optional",
            None,
            "FROZEN_CORE",
            "REFERENCE_ONLY",
            "next_job",
        ),
        (
            "workflow.nodes[].test_score",
            "integer_optional",
            None,
            "DIAGNOSTIC",
            "PUBLIC",
            "diagnostic_only",
        ),
    ]
    preference_fields = [
        ("document_viewer", "enum", "INTERNAL", "immediate"),
        ("auto_open_preview", "boolean", True, "immediate"),
        ("external_refresh", "boolean", True, "immediate"),
        ("notifications_enabled", "boolean", True, "immediate"),
        ("task_complete_notification", "boolean", True, "immediate"),
        ("task_error_notification", "boolean", True, "immediate"),
        ("approval_notification", "boolean", True, "immediate"),
        ("weekly_report_notification", "boolean", True, "immediate"),
        ("notification_sound", "boolean", False, "immediate"),
        ("do_not_disturb_start", "time", "22:00", "immediate"),
        ("do_not_disturb_end", "time", "08:00", "immediate"),
        ("notification_open_task", "boolean", True, "immediate"),
        ("restore_last_view", "boolean", False, "next_launch"),
        ("restore_last_document", "boolean", False, "next_launch"),
        ("close_behavior", "enum", "ASK", "immediate"),
        ("warn_on_close_running", "boolean", True, "immediate"),
        ("launch_at_startup", "boolean", False, "os_integration"),
        ("keep_tasks_in_background", "boolean", True, "immediate"),
        ("remember_panel_state", "boolean", True, "immediate"),
        ("proxy_mode", "enum", "SYSTEM", "restart_may_be_required"),
        ("proxy_address", "string", "", "restart_may_be_required"),
        ("request_timeout_seconds", "integer", 60, "next_request"),
        ("log_level", "enum", "NORMAL", "next_launch"),
        ("retention_days", "integer", 30, "maintenance"),
        ("cache_limit_mb", "integer", 5120, "maintenance"),
        ("telemetry_enabled", "boolean", False, "immediate"),
        ("language", "enum", "zh-CN", "immediate"),
        ("font_scale_percent", "integer", 100, "immediate"),
        ("show_full_tooltips", "boolean", True, "immediate"),
    ]
    fields.extend(
        (
            f"preferences.{name}",
            field_type,
            default,
            "USER",
            "PUBLIC",
            restart_effect,
        )
        for name, field_type, default, restart_effect in preference_fields
    )
    return {
        "schema_version": "SettingsFieldContract-v1",
        "settings_schema_version": SETTINGS_SCHEMA_VERSION,
        "fields": [
            {
                "path": path,
                "type": field_type,
                "default": default,
                "source": source,
                "sensitivity": sensitivity,
                "restart_effect": restart_effect,
            }
            for path, field_type, default, source, sensitivity, restart_effect in fields
        ],
        "field_count": len(fields),
        "source_precedence": [
            "FROZEN_CORE",
            "USER_TEMPLATE",
            "NEW_JOB_SNAPSHOT",
            "RUN_FROZEN_SNAPSHOT",
        ],
        "credential_value_fields": 0,
    }


__all__ = [
    "API_PROTOCOLS",
    "CLI_DEFINITIONS",
    "CONNECTION_STATES",
    "LOCKED_ENABLED_IDS",
    "MODES",
    "NO_MODEL_IDS",
    "OPTIONAL_IDS",
    "REFERENCE",
    "SETTINGS_SCHEMA_VERSION",
    "STORE_SCHEMA_VERSION",
    "WORKFLOW_DEFINITIONS",
    "WORKFLOW_IDS",
    "canonical_json_bytes",
    "canonical_sha256",
    "default_settings",
    "immutable",
    "normalize_external_api_base_url",
    "risk_ids",
    "scan_sensitive",
    "settings_field_contract",
    "utc_now",
    "validate_settings",
]
