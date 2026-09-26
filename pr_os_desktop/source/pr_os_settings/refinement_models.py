"""Shared configured choices for starting and changing a refinement task."""
from __future__ import annotations
import re
from typing import Any, Mapping
from .model_capabilities import infer_api_model_capability

def _text(value: Any, limit: int = 320) -> str:
    if value is None: return ""
    rendered = " ".join(str(value).replace("\x00", " ").split())
    return rendered if len(rendered) <= limit else rendered[:max(1,limit-1)].rstrip()+"…"

def configured_refinement_models(projection: Mapping[str, Any], local_model_registry: Any = None) -> list[dict[str, Any]]:
    settings = (
        projection.get("settings")
        if isinstance(projection, Mapping)
        and isinstance(projection.get("settings"), Mapping)
        else projection
    )
    raw_rows: list[dict[str, Any]] = []
    if isinstance(settings, Mapping) and isinstance(settings.get("model_services"), list):
        raw_rows.extend(
            {
                **dict(row),
                "_refinement_profile_kind": "API",
            }
            for row in settings["model_services"]
            if isinstance(row, Mapping)
        )
    if isinstance(projection, Mapping) and isinstance(projection.get("api_profiles"), list):
        raw_rows.extend(
            {
                **dict(row),
                "_refinement_profile_kind": "API",
            }
            for row in projection["api_profiles"]
            if isinstance(row, Mapping)
        )
    if isinstance(settings, Mapping) and isinstance(settings.get("cli_services"), list):
        for service in settings["cli_services"]:
            if not isinstance(service, Mapping) or service.get("enabled") is not True:
                continue
            service_name = _text(service.get("display_name") or service.get("adapter_id"), 160)
            for model in service.get("models", []) if isinstance(service.get("models"), list) else []:
                if isinstance(model, Mapping):
                    raw_rows.append(
                        {
                            **dict(model),
                            "_refinement_profile_kind": "CLI",
                            "display_name": _text(model.get("display_name") or model.get("model_name"), 160)
                            or service_name,
                        }
                    )
    models_by_ref: dict[str, dict[str, Any]] = {}
    profile_order: list[str] = []
    for row in raw_rows if isinstance(raw_rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        profile_ref = _text(row.get("config_id") or row.get("profile_ref"), 160)
        display_name = _text(
            row.get("model_name") or row.get("display_name") or row.get("provider_name"),
            160,
        )
        if not profile_ref or profile_ref in models_by_ref:
            continue
        profile_kind = (
            "LOCAL"
            if re.fullmatch(r"local:[0-9a-f]{24}", profile_ref, re.IGNORECASE)
            else "CLI"
            if row.get("_refinement_profile_kind") == "CLI"
            or profile_ref.casefold().startswith("cli:")
            else "API"
        )
        label = display_name or profile_ref
        if profile_kind == "LOCAL" and not label.startswith("[本地]"):
            label = f"[本地] {label}"
        connection_available = row.get("connection_status") == "AVAILABLE"
        execution_eligible = bool(
            profile_kind == "CLI" and connection_available
            or profile_kind == "API"
            and connection_available
            and infer_api_model_capability(
                str(row.get("provider") or ""),
                str(row.get("model_name") or ""),
            )
            == "CHAT"
        )
        reason = None
        if not execution_eligible:
            reason = (
                "本地模型当前未通过精炼执行资格校验"
                if profile_kind == "LOCAL"
                else "该模型尚未验证可用"
                if not connection_available
                else "该 API 模型不具备对话精炼能力"
            )
        models_by_ref[profile_ref] = {
            "profile_ref": profile_ref,
            "display_name": label if execution_eligible else f"{label}（{reason}）",
            "profile_kind": profile_kind,
            "execution_eligible": execution_eligible,
            "execution_status": (
                "READY_SETTINGS_CLI_STRUCTURED_CHAT"
                if execution_eligible and profile_kind == "CLI"
                else "READY_SETTINGS_API_STRUCTURED_CHAT"
                if execution_eligible and profile_kind == "API"
                else "LOCAL_REFINEMENT_EXECUTION_NOT_ELIGIBLE"
                if profile_kind == "LOCAL"
                else "REFINEMENT_PROFILE_NOT_AVAILABLE"
            ),
            "disabled_reason": reason,
        }
        profile_order.append(profile_ref)
    if local_model_registry is not None:
        try:
            local_projection = local_model_registry.call("local_models.list", {})
        except Exception:
            local_projection = {}
        recognized = (
            local_projection.get("recognized_models", [])
            if isinstance(local_projection, Mapping)
            else []
        )
        for local_model in recognized if isinstance(recognized, list) else []:
            if not isinstance(local_model, Mapping):
                continue
            profile_ref = _text(local_model.get("profile_ref"), 160)
            if not re.fullmatch(r"local:[0-9a-f]{24}", profile_ref, re.IGNORECASE):
                continue
            display_name = _text(local_model.get("display_name") or local_model.get("model_name"), 160)
            if not display_name.startswith("[本地]"):
                display_name = f"[本地] {display_name or profile_ref}"
            model_digest = _text(local_model.get("model_digest"), 64)
            execution_eligible = bool(
                local_model.get("execution_eligible") is True
                and local_model.get("connection_status") == "AVAILABLE"
                and local_model.get("exact_identity_available") is True
                and local_model.get("endpoint_kind") == "ollama"
                and local_model.get("structured_chat_adapter")
                == "P07T09_LOCAL_STRUCTURED_CHAT_V1"
                and re.fullmatch(r"[0-9a-f]{64}", model_digest, re.IGNORECASE)
            )
            reason = (
                None
                if execution_eligible
                else "本地模型当前未通过精炼执行资格校验"
            )
            if profile_ref not in models_by_ref:
                profile_order.append(profile_ref)
            models_by_ref[profile_ref] = {
                "profile_ref": profile_ref,
                "display_name": (
                    display_name
                    if execution_eligible
                    else f"{display_name}（{reason}）"
                ),
                "profile_kind": "LOCAL",
                "endpoint_id": _text(local_model.get("endpoint_id"), 120),
                "model_name": _text(local_model.get("model_name"), 160),
                "identity_refresh_supported": bool(
                    local_model_registry is not None
                    and _text(local_model.get("endpoint_id"), 120)
                    and _text(local_model.get("model_name"), 160)
                ),
                "execution_eligible": execution_eligible,
                "execution_status": (
                    "READY_LOCAL_STRUCTURED_CHAT"
                    if execution_eligible
                    else "LOCAL_REFINEMENT_EXECUTION_NOT_ELIGIBLE"
                ),
                "disabled_reason": reason,
            }
    ordered_refs = [
        profile_ref
        for profile_ref in profile_order
        if models_by_ref[profile_ref].get("profile_kind") == "LOCAL"
    ] + [
        profile_ref
        for profile_ref in profile_order
        if models_by_ref[profile_ref].get("profile_kind") != "LOCAL"
    ]
    return [models_by_ref[profile_ref] for profile_ref in ordered_refs]

