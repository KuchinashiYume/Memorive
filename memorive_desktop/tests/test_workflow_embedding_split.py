from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from memorive_current_task.projection import CurrentTaskProjector  # noqa: E402
from memorive_current_task.workflow_mapping import (  # noqa: E402
    build_core_execution_snapshot,
)
from memorive_settings.capability import CapabilityProjection  # noqa: E402
from memorive_settings.contracts import (  # noqa: E402
    SETTINGS_SCHEMA_VERSION,
    STORE_SCHEMA_VERSION,
    canonical_json_bytes,
    canonical_sha256,
    default_settings,
    validate_settings,
)
from memorive_settings.controller import SettingsController  # noqa: E402
from memorive_settings.credentials import CredentialReferenceManager  # noqa: E402
from memorive_settings.directory_policy import DirectoryPolicy  # noqa: E402
from memorive_settings.store import SettingsStore  # noqa: E402


def _node(settings: dict[str, object], node_id: str) -> dict[str, object]:
    return next(
        row
        for row in settings["workflow"]["nodes"]  # type: ignore[index]
        if row["node_id"] == node_id
    )


def _add_api_model(
    settings: dict[str, object],
    *,
    config_id: str,
    model_name: str,
    connection_status: str = "AVAILABLE",
    credential_status: str = "STORED_UNVERIFIED",
) -> None:
    credential_ref = f"REF:windows:Memorive/{config_id}/primary"
    settings["credential_references"].append(  # type: ignore[index]
        {
            "credential_ref": credential_ref,
            "provider": config_id,
            "logical_key": "primary",
            "status": credential_status,
            "created_at": "2026-08-31T00:00:00Z",
            "updated_at": "2026-08-31T00:00:00Z",
        }
    )
    settings["model_services"].append(  # type: ignore[index]
        {
            "config_id": config_id,
            "provider": config_id,
            "credential_ref": credential_ref,
            "plan": "STANDARD",
            "thinking": False,
            "model_name": model_name,
            "tier": "standard",
            "thinking_mode": "",
            "connection_status": connection_status,
        }
    )


def _controller(
    tmp_path: Path,
    *,
    local_profile_resolver=None,
) -> tuple[SettingsController, SettingsStore]:
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir(parents=True)
    artifacts.mkdir(parents=True)
    store = SettingsStore(
        tmp_path / "profile",
        default_factory=lambda: default_settings(
            workspace_root=str(workspace), artifact_root=str(artifacts)
        ),
    )
    controller = SettingsController(
        store=store,
        credentials=CredentialReferenceManager(),
        capabilities=CapabilityProjection([], revision="sample-test"),
        directory_policy=DirectoryPolicy([tmp_path]),
        local_profile_resolver=local_profile_resolver,
    )
    return controller, store


def test_embedding_and_context_accept_only_their_own_capabilities() -> None:
    embedding = default_settings()
    _add_api_model(embedding, config_id="embedding-api", model_name="BAAI/bge-m3")
    _node(embedding, "chunk_embedding")["profile_ref"] = "embedding-api"
    assert _node(validate_settings(embedding), "chunk_embedding")["profile_ref"] == "embedding-api"

    chat = default_settings()
    _add_api_model(chat, config_id="chat-api", model_name="qwen3.8:27b-q4_K_M")
    _node(chat, "chunk_embedding")["profile_ref"] = "chat-api"
    with pytest.raises(ValueError, match="WORKFLOW_MODEL_CAPABILITY_MISMATCH:chunk_embedding:CHAT"):
        validate_settings(chat)

    cli = default_settings()
    cli["cli_services"][0]["enabled"] = True
    cli["cli_services"][0]["models"].append(
        {
            "profile_ref": "cli:codex-cli:bge-m3",
            "display_name": "bge-m3",
            "model_name": "bge-m3",
            "thinking_mode": "",
            "connection_status": "AVAILABLE",
        }
    )
    _node(cli, "chunk_embedding")["profile_ref"] = "cli:codex-cli:bge-m3"
    with pytest.raises(ValueError, match="WORKFLOW_MODEL_CAPABILITY_MISMATCH:chunk_embedding:CLI"):
        validate_settings(cli)

    direct_context = deepcopy(embedding)
    _node(direct_context, "chunk_embedding")["profile_ref"] = None
    _node(direct_context, "context_pack")["profile_ref"] = "embedding-api"
    with pytest.raises(ValueError, match="WORKFLOW_MODEL_CAPABILITY_MISMATCH:context_pack:EMBEDDING"):
        validate_settings(direct_context)

    reranker = default_settings()
    _add_api_model(
        reranker,
        config_id="reranker-api",
        model_name="BAAI/bge-reranker-v2-m3",
    )
    _node(reranker, "context_pack")["profile_ref"] = "reranker-api"
    accepted = validate_settings(reranker)
    assert _node(accepted, "context_pack")["profile_ref"] == "reranker-api"
    assert _node(accepted, "context_pack")["profile_source_node_id"] is None


def test_node02_preview_requires_verified_api_or_exact_eligible_local_embedding(
    tmp_path: Path,
) -> None:
    local_ref = "local:" + "a" * 24
    local_row = {
        "profile_ref": local_ref,
        "config_id": local_ref,
        "provider": "Ollama",
        "model_name": "bge-m3:latest",
        "model_digest": "B" * 64,
        "endpoint_kind": "ollama",
        "capability": "EMBEDDING",
        "connection_status": "AVAILABLE",
        "exact_identity_available": True,
        "execution_eligible": True,
    }
    controller, _store = _controller(
        tmp_path, local_profile_resolver=lambda ref: local_row if ref == local_ref else None
    )

    local_settings = controller.get_state()["settings"]
    _node(local_settings, "chunk_embedding")["profile_ref"] = local_ref
    assert controller.preview(local_settings)["status"] == "PASS"

    missing_controller, _ = _controller(tmp_path / "missing")
    missing_settings = missing_controller.get_state()["settings"]
    _node(missing_settings, "chunk_embedding")["profile_ref"] = local_ref
    blocked = missing_controller.preview(missing_settings)
    assert blocked["status"] == "BLOCKED"
    assert "WORKFLOW_LOCAL_EMBEDDING_PROFILE_UNAVAILABLE" in blocked["errors"]

    api_settings = controller.get_state()["settings"]
    _add_api_model(
        api_settings,
        config_id="unverified-embedding",
        model_name="bge-m3",
        connection_status="UNVERIFIED",
    )
    _node(api_settings, "chunk_embedding")["profile_ref"] = "unverified-embedding"
    api_blocked = controller.preview(api_settings)
    assert api_blocked["status"] == "BLOCKED"
    assert "WORKFLOW_API_EMBEDDING_PROFILE_UNAVAILABLE" in api_blocked["errors"]


def _legacy_v4_settings(workspace: Path, artifacts: Path) -> dict[str, object]:
    settings = default_settings(
        workspace_root=str(workspace), artifact_root=str(artifacts)
    )
    settings["schema_version"] = "SettingsTemplate-v4"
    settings["workflow"] = {
        "nodes": [
            {"node_id": "ingest", "label": "文档摄取", "enabled": True, "retry_count": 2, "profile_ref": "PARSER_PROFILE", "test_score": None},
            {"node_id": "card_distill", "label": "Card 蒸馏", "enabled": True, "retry_count": 2, "profile_ref": "DISTILLATION_PROFILE", "test_score": None},
            {"node_id": "transport_review", "label": "Card 搬运类校核", "enabled": True, "retry_count": 2, "profile_ref": "AUTO_HETEROGENEOUS", "test_score": None},
            {"node_id": "card_admission", "label": "Card 准入", "enabled": True, "retry_count": 0, "profile_ref": None, "test_score": None},
            {"node_id": "context_pack", "label": "Context Pack", "enabled": True, "retry_count": 2, "profile_ref": "CONTEXT_PROFILE", "test_score": None},
            {"node_id": "analysis", "label": "Analysis 分析", "enabled": True, "retry_count": 2, "profile_ref": "ANALYSIS_PROFILE", "test_score": None},
            {"node_id": "judgment_review", "label": "判断类异源核对", "enabled": True, "retry_count": 2, "profile_ref": "AUTO_HETEROGENEOUS", "test_score": None},
            {"node_id": "human_judgment", "label": "人类最终判断", "enabled": True, "retry_count": 0, "profile_ref": None, "test_score": None},
        ]
    }
    return settings


@pytest.mark.parametrize(
    ("legacy_context_profile", "expected_embedding_profile", "moved"),
    [
        ("embedding-api", "embedding-api", True),
        ("CONTEXT_PROFILE", None, False),
    ],
)
def test_v4_to_v6_migration_moves_embedding_without_faking_a_reranker(
    tmp_path: Path,
    legacy_context_profile: str,
    expected_embedding_profile: str | None,
    moved: bool,
) -> None:
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifacts"
    profile = tmp_path / "profile"
    for path in (workspace, artifacts, profile):
        path.mkdir()
    legacy = _legacy_v4_settings(workspace, artifacts)
    if legacy_context_profile == "embedding-api":
        _add_api_model(legacy, config_id="embedding-api", model_name="bge-m3")
    _node(legacy, "context_pack")["profile_ref"] = legacy_context_profile
    envelope = {
        "schema_version": "SettingsStoreEnvelope-v4",
        "revision": 71,
        "template_id": "settings-template-000071",
        "updated_at": "2026-08-31T00:00:00Z",
        "settings": legacy,
        "settings_sha256": canonical_sha256(legacy),
    }
    (profile / "settings.json").write_bytes(canonical_json_bytes(envelope) + b"\n")

    migrated = SettingsStore(profile).load(recover_corruption=False)
    receipt = json.loads((profile / "settings_migration.json").read_text(encoding="utf-8"))

    assert migrated["schema_version"] == "SettingsStoreEnvelope-v7"
    assert migrated["revision"] == 72
    assert _node(migrated["settings"], "chunk_embedding")["profile_ref"] == expected_embedding_profile
    assert _node(migrated["settings"], "context_pack")["profile_ref"] is None
    assert _node(migrated["settings"], "context_pack")["profile_source_node_id"] is None
    assert receipt["from_schema"] == "SettingsTemplate-v4"
    assert receipt["embedding_profile_moved"] is moved


def test_v5_to_v6_migration_preserves_embedding_and_removes_derived_context(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile-v5"
    profile.mkdir()
    legacy = default_settings()
    legacy["schema_version"] = "SettingsTemplate-v5"
    _add_api_model(
        legacy,
        config_id="embedding-api",
        model_name="BAAI/bge-m3",
    )
    _node(legacy, "chunk_embedding")["profile_ref"] = "embedding-api"
    _node(legacy, "context_pack")["profile_ref"] = None
    _node(legacy, "context_pack")[
        "profile_source_node_id"
    ] = "chunk_embedding"
    envelope = {
        "schema_version": "SettingsStoreEnvelope-v5",
        "revision": 72,
        "template_id": "settings-template-000072",
        "updated_at": "2026-08-31T00:00:00Z",
        "settings": legacy,
        "settings_sha256": canonical_sha256(legacy),
    }
    (profile / "settings.json").write_bytes(
        canonical_json_bytes(envelope) + b"\n"
    )

    migrated = SettingsStore(profile).load(recover_corruption=False)
    receipt = json.loads(
        (profile / "settings_migration.json").read_text(encoding="utf-8")
    )

    assert migrated["schema_version"] == "SettingsStoreEnvelope-v7"
    assert migrated["revision"] == 73
    assert _node(migrated["settings"], "chunk_embedding")[
        "profile_ref"
    ] == "embedding-api"
    assert _node(migrated["settings"], "context_pack")["profile_ref"] is None
    assert _node(migrated["settings"], "context_pack")[
        "profile_source_node_id"
    ] is None
    assert receipt["legacy_derived_context_binding_removed"] is True
    assert receipt["context_pack_profile_ref"] is None


