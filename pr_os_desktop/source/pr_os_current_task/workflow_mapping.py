from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from pr_os_settings.contracts import canonical_sha256, validate_settings
from pr_os_settings.model_capabilities import EMBEDDING, infer_api_model_capability
from pr_os_phase1_runtime.capacity import describe_capacity


PHASE1_NODES = (
    ("ingest", "01_DOCUMENT_INGEST", "文档处理", "转换、清洗、切块、内容理解与复杂版面/OCR"),
    ("chunk_embedding", "02_CHUNK_EMBEDDING", "向量化", "为 chunk 生成可检索的向量嵌入"),
    ("card_distill", "03_CARD_DISTILL", "生成卡片", "忠实抽取并生成待处理 Card"),
    ("transport_review", "04_CARD_CROSS_CHECK", "核对卡片", "回到原文核对字段、来源与搬运准确性"),
    ("card_admission", "05_CARD_ADMISSION", "卡片准入", "根据准入规则形成可用或隔离状态"),
    ("context_pack", "06_CONTEXT_PACK", "整理分析材料", "用 02 的同一嵌入空间检索并组装分析材料"),
    ("analysis", "07_ANALYSIS", "分析", "基于已选材料生成分析建议"),
    ("judgment_review", "08_JUDGMENT_CROSS_CHECK", "核对分析", "核对分析结论与引用证据"),
    ("human_judgment", "09_HUMAN_FINAL", "人工判断", "完成最终判断与写作"),
)

BUILTIN_MODELS = {
    "PARSER_PROFILE": ("[默认] 本地解析", "Memorive 内置"),
    "AUTO_HETEROGENEOUS": ("按上游生产模型自动选择异源校核者", "Memorive 选择器"),
}

_NODE_REQUESTED_OUTPUT_TOKENS = {
    "card_distill": 8_192,
    "transport_review": 8_192,
    "analysis": 65_536,
    "judgment_review": 8_192,
}


def _model_descriptor(service: Mapping[str, Any]) -> str:
    parts = [
        str(service.get("model_name") or "").strip(),
        str(service.get("tier") or "").strip(),
        str(service.get("thinking_mode") or "").strip(),
    ]
    rendered = "-".join(part for part in parts if part)
    return rendered or str(service.get("provider") or "未命名模型服务")


def build_phase1_execution_snapshot(
    settings: Mapping[str, Any], *, settings_revision: int,
    local_model_profiles: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Translate saved Settings v5 into the immutable Phase 1 nine-node job shape.

    This function binds configuration only.  It never reads credential values,
    enables a provider route, or sends a model request.
    """

    if isinstance(settings_revision, bool) or not isinstance(settings_revision, int) or settings_revision < 0:
        raise ValueError("SETTINGS_REVISION_INVALID")
    accepted = validate_settings(settings)
    services = {
        row["config_id"]: {
            **row,
            "_profile_kind": "API",
            "_execution_service": dict(row),
            "_execution_model": {
                "model_name": row["model_name"],
                "tier": row.get("tier", ""),
                "thinking_mode": row.get("thinking_mode", ""),
                "thinking": row.get("thinking", False),
            },
        }
        for row in accepted["model_services"]
    }
    configured_models = [
        {
            "profile_ref": row["config_id"],
            "provider": row["provider"],
            "model": _model_descriptor(row),
            "connection_status": row["connection_status"],
            "credential_reference_stored": any(
                reference["credential_ref"] == row["credential_ref"]
                and reference["status"] == "STORED_UNVERIFIED"
                for reference in accepted["credential_references"]
            ),
            "capability": infer_api_model_capability(
                row["provider"], row.get("model_name", "")
            ),
            "embedding_eligible": (
                infer_api_model_capability(row["provider"], row.get("model_name", ""))
                == EMBEDDING
                and row["connection_status"] == "AVAILABLE"
                and any(
                    reference["credential_ref"] == row["credential_ref"]
                    and reference["status"] == "STORED_UNVERIFIED"
                    for reference in accepted["credential_references"]
                )
            ),
        }
        for row in accepted["model_services"]
    ]
    cli_service_enabled: dict[str, bool] = {}
    for cli_service in accepted["cli_services"]:
        for model in cli_service["models"]:
            services[model["profile_ref"]] = {
                "provider": cli_service["display_name"],
                "model_name": model["display_name"] or model["model_name"],
                "tier": cli_service["adapter_id"],
                "thinking_mode": model["thinking_mode"],
                "connection_status": model["connection_status"],
                "cli_model": True,
                "_profile_kind": "CLI",
                "_execution_service": dict(cli_service),
                "_execution_model": dict(model),
            }
            cli_service_enabled[model["profile_ref"]] = cli_service["enabled"]
            if cli_service["enabled"]:
                configured_models.append(
                    {
                        "profile_ref": model["profile_ref"],
                        "provider": cli_service["display_name"],
                        "model": model["display_name"] or model["model_name"],
                        "connection_status": model["connection_status"],
                        "credential_reference_stored": True,
                        "capability": "CHAT",
                        "embedding_eligible": False,
                    }
                )
    for local_profile in local_model_profiles or ():
        profile_ref = str(local_profile.get("profile_ref") or "")
        model_name = str(local_profile.get("model_name") or "")
        display_name = str(local_profile.get("display_name") or "")
        provider = str(local_profile.get("provider") or "")
        if not profile_ref.startswith("local:") or not model_name or not provider:
            continue
        services[profile_ref] = {
            "provider": provider,
            "model_name": display_name or f"[本地] {model_name}",
            "connection_status": str(local_profile.get("connection_status") or "AVAILABLE"),
            "local_model": True,
            "capability": str(local_profile.get("capability") or "CHAT"),
            "endpoint_kind": local_profile.get("endpoint_kind"),
            "exact_identity_available": local_profile.get("exact_identity_available"),
            "execution_eligible": local_profile.get("execution_eligible"),
            "_profile_kind": "LOCAL",
            "_execution_service": dict(local_profile),
            "_execution_model": {
                "model_name": model_name,
                "display_name": display_name,
                "model_digest": local_profile.get("model_digest"),
            },
        }
        configured_models.append(
            {
                "profile_ref": profile_ref,
                "provider": provider,
                "model": display_name or f"[本地] {model_name}",
                "connection_status": str(local_profile.get("connection_status") or "AVAILABLE"),
                "credential_reference_stored": True,
                "capability": str(local_profile.get("capability") or "CHAT"),
                "embedding_eligible": (
                    local_profile.get("capability") == EMBEDDING
                    and local_profile.get("connection_status", "AVAILABLE") == "AVAILABLE"
                    and local_profile.get("endpoint_kind") == "ollama"
                    and local_profile.get("exact_identity_available") is True
                    and local_profile.get("execution_eligible") is True
                ),
            }
        )
    settings_nodes = {row["node_id"]: row for row in accepted["workflow"]["nodes"]}
    definitions = []
    execution_nodes: dict[str, dict[str, Any]] = {}
    required_model_nodes_ready = True
    for settings_id, runtime_id, name, purpose in PHASE1_NODES:
        row = settings_nodes[settings_id]
        profile_source_runtime_id = None
        binding_row = row
        if settings_id == "context_pack":
            binding_row = settings_nodes["chunk_embedding"]
            profile_source_runtime_id = "02_CHUNK_EMBEDDING"
        profile_ref = binding_row["profile_ref"]
        service = services.get(profile_ref)
        if service is not None:
            model = (
                str(service["model_name"])
                if service.get("cli_model") or service.get("local_model")
                else _model_descriptor(service)
            )
            provider = service["provider"]
        elif profile_ref in BUILTIN_MODELS:
            model, provider = BUILTIN_MODELS[profile_ref]
        elif profile_ref is None:
            model = "用户 / 不调用模型" if settings_id == "human_judgment" else "确定性状态机 / 不调用模型"
            provider = "Memorive 确定性执行"
        else:
            model = "尚未映射已配置模型"
            provider = "未配置"
        if settings_id in {"ingest", "chunk_embedding", "card_distill", "analysis"} and (
            (service is None and not (
                settings_id == "ingest" and profile_ref == "PARSER_PROFILE"
            ))
            or (profile_ref in cli_service_enabled and not cli_service_enabled[profile_ref])
        ):
            required_model_nodes_ready = False
        if settings_id == "chunk_embedding" and service is not None:
            if service.get("local_model"):
                embedding_ready = (
                    service.get("capability") == EMBEDDING
                    and service.get("connection_status") == "AVAILABLE"
                    and service.get("endpoint_kind") == "ollama"
                    and service.get("exact_identity_available") is True
                    and service.get("execution_eligible") is True
                )
            else:
                embedding_ready = (
                    not service.get("cli_model")
                    and infer_api_model_capability(
                        service.get("provider"), service.get("model_name")
                    ) == EMBEDDING
                    and service.get("connection_status") == "AVAILABLE"
                )
            if not embedding_ready:
                required_model_nodes_ready = False
        definitions.append(
            {
                "node_id": runtime_id,
                "name": name,
                "purpose": purpose,
                "model": model,
            }
        )
        execution_nodes[runtime_id] = {
            "enabled": row["enabled"],
            "retry_count": row["retry_count"],
            "profile_ref": profile_ref,
            "model": model,
            "provider": provider,
        }
        if service is not None:
            execution_profile = {
                "service": dict(service.get("_execution_service") or {}),
                "model": dict(service.get("_execution_model") or {}),
            }
            execution_nodes[runtime_id].update(
                {
                    "profile_kind": service.get("_profile_kind"),
                    "execution_profile": execution_profile,
                    "capacity": describe_capacity(
                        profile_kind=service.get("_profile_kind"),
                        execution_profile=execution_profile,
                        node_requested_output_tokens=_NODE_REQUESTED_OUTPUT_TOKENS.get(
                            settings_id
                        ),
                    ),
                }
            )
        if settings_id == "ingest":
            fallback_ref = row.get("fallback_profile_ref")
            execution_nodes[runtime_id]["retry_scope"] = "PAGE"
            execution_nodes[runtime_id]["fallback_profile_ref"] = fallback_ref
            fallback = services.get(fallback_ref)
            if fallback_ref and (fallback is None or
                    (fallback_ref in cli_service_enabled and not cli_service_enabled[fallback_ref])):
                required_model_nodes_ready = False
            if fallback is not None:
                execution_nodes[runtime_id]["fallback_profile"] = {
                    "profile_ref": fallback_ref,
                    "profile_kind": fallback.get("_profile_kind"),
                    "provider": fallback["provider"],
                    "model": str(fallback["model_name"]),
                    "execution_profile": {
                        "service": dict(fallback.get("_execution_service") or {}),
                        "model": dict(fallback.get("_execution_model") or {}),
                    },
                }
        if profile_source_runtime_id is not None:
            execution_nodes[runtime_id].update(
                {
                    "profile_source_node_id": profile_source_runtime_id,
                    "configuration_source": "INHERITED_NODE_PROFILE",
                }
            )
    # Node 06 has two distinct dependencies: vector retrieval stays in node
    # 02's exact embedding space, while the amount of material it may assemble
    # is planned against node 07's selected analysis capacity.  Keep both
    # sources explicit so the UI and frozen job never conflate them.
    if (
        isinstance(execution_nodes.get("06_CONTEXT_PACK"), dict)
        and isinstance(execution_nodes.get("07_ANALYSIS"), dict)
        and isinstance(execution_nodes["07_ANALYSIS"].get("capacity"), Mapping)
    ):
        execution_nodes["06_CONTEXT_PACK"]["capacity"] = deepcopy(
            execution_nodes["07_ANALYSIS"]["capacity"]
        )
        execution_nodes["06_CONTEXT_PACK"][
            "capacity_planning_source_node_id"
        ] = "07_ANALYSIS"
        execution_nodes["06_CONTEXT_PACK"][
            "retrieval_embedding_profile_source_node_id"
        ] = "02_CHUNK_EMBEDDING"
    configured_models.sort(key=lambda row: row["profile_ref"])
    profile_catalog = []
    for option in configured_models:
        service = services.get(option["profile_ref"])
        if service is None:
            continue
        profile_catalog.append(
            {
                **deepcopy(option),
                "profile_kind": service.get("_profile_kind"),
                "execution_profile": {
                    "service": deepcopy(dict(service.get("_execution_service") or {})),
                    "model": deepcopy(dict(service.get("_execution_model") or {})),
                },
                "capacity": describe_capacity(
                    profile_kind=service.get("_profile_kind"),
                    execution_profile={
                        "service": deepcopy(
                            dict(service.get("_execution_service") or {})
                        ),
                        "model": deepcopy(
                            dict(service.get("_execution_model") or {})
                        ),
                    },
                ),
            }
        )
    configuration_ready = required_model_nodes_ready and all(
        row["credential_reference_stored"] for row in configured_models
    )
    payload = {
        "schema_version": "PR-OSPhase1ExecutionSnapshot-v1",
        "settings_revision": settings_revision,
        "settings_sha256": canonical_sha256(accepted),
        "workflow_definition": {
            "schema_version": "PR-OSPhase1WorkflowDefinition-v1",
            "workflow_id": "pr-os-phase1-nine-node",
            "nodes": definitions,
        },
        "workflow_config": {
            "schema_version": "PR-OSPhase1WorkflowConfig-v1",
            "nodes": execution_nodes,
        },
        "configured_models": configured_models,
        # Used only by the native current-task controller to resolve a user's
        # task-scoped selection.  Product projections remove this field before
        # crossing into the web view; credential values are never present.
        "profile_catalog": profile_catalog,
        "configuration_ready": configuration_ready,
        "external_route_activated": False,
        "credential_values_read": 0,
        "external_model_calls": 0,
    }
    payload["snapshot_sha256"] = canonical_sha256(payload)
    return payload


__all__ = ["PHASE1_NODES", "build_phase1_execution_snapshot"]
