from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _request(profile_ref: str) -> dict:
    return {
        "task_id": "refinement-test-001",
        "logical_slot": "conversation_refinement",
        "profile_ref": profile_ref,
        "explicit_user_action": True,
        "source_snapshot_sha256": "A" * 64,
        "source": {
            "schema_version": "DesktopSessionRefinementSourceBinding-v1",
            "local_projection_id": "session-001",
            "provider_id": "deepseek",
            "provider_session_id": "provider-session-001",
            "source_format": "MEMORIVE_BROWSER_SESSION_LIBRARY_V4",
        },
        "messages": [
            {"role": "user", "content": "请把这段会话整理为可复核条目。"},
            {"role": "assistant", "content": "建议先保留来源，再生成候选条目。"},
        ],
        "review_required": True,
        "auto_ingest": False,
    }


def _subject_from_schema(schema: dict) -> dict:
    item_schemas = schema["properties"]["items"]["prefixItems"]
    items = []
    for index, item_schema in enumerate(item_schemas):
        type_name = "PROVENANCE_ONLY" if index == 0 else "AI_SUGGESTION"
        items.append(
            {
                "message_id": item_schema["properties"]["message_id"]["const"],
                "candidate_text": "保留来源" if index == 0 else "先生成可复核候选条目",
                "refinement_type": type_name,
            }
        )
    return {"items": items}


class _LocalRegistry:
    def __init__(self, row: dict):
        self.row = deepcopy(row)
        self.calls = 0

    def call(self, method: str, params: dict) -> dict:
        assert method == "local_models.list"
        assert params == {}
        self.calls += 1
        return {"recognized_models": [deepcopy(self.row)]}


class _LocalAdapter:
    def __init__(self):
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        requested = kwargs["binding"]["requested_model"]
        digest = kwargs["binding"]["expected_model_digest"]
        return SimpleNamespace(
            success=True,
            error_code=None,
            response=_subject_from_schema(kwargs["schema"]),
            receipt={
                "schema_version": "EvaluationAssetsLocalStructuredChatReceipt-v1",
                "status": "PASS",
                "requested_model": requested,
                "returned_model": requested,
                "model_digest": digest,
                "local_model_request_count": 1,
                "external_request_count": 0,
                "cloud_fallback_count": 0,
                "api_cost_cny": 0.0,
                "receipt_sha256": "B" * 64,
            },
        )


def _local_row() -> dict:
    return {
        "profile_ref": "local:0123456789abcdef01234567",
        "endpoint_id": "ollama-default",
        "endpoint_name": "Ollama",
        "endpoint_kind": "ollama",
        "endpoint": "http://127.0.0.1:11434",
        "chat_endpoint": "http://127.0.0.1:11434/api/chat",
        "provider": "[本地] Ollama",
        "display_name": "[本地] qwen3.8:27b-q4_K_M",
        "model_name": "qwen3.8:27b-q4_K_M",
        "model_digest": "D" * 64,
        "exact_identity_available": True,
        "structured_chat_adapter": "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1",
        "execution_eligible": True,
        "capability": "GENERAL_CHAT",
        "connection_status": "AVAILABLE",
        "verified_at": "2026-08-29T00:00:00Z",
    }


def _channel(row: dict, adapter: _LocalAdapter | None = None):
    from memorive_workflow_bridge.conversation_refinement import (
        ExistingConversationRefinementChannel,
    )

    return ExistingConversationRefinementChannel(
        local_model_registry=_LocalRegistry(row),
        local_adapter=adapter or _LocalAdapter(),
        prompt_path=(
            ROOT
            / "source"
            / "model_gateway"
            / "prompts"
            / "conversation_refinement"
            / "v2.md"
        ),
    )


def test_channel_reuses_phase7_local_adapter_and_phase6_subject_contract():
    local = _local_row()
    adapter = _LocalAdapter()
    result = _channel(local, adapter).execute(_request(local["profile_ref"]))

    assert result["profile_ref"] == local["profile_ref"]
    assert result["requested_model"] == local["model_name"]
    assert result["returned_model"] == local["model_name"]
    assert result["subject_output"]["schema_version"] == "CONVERSATION_REFINEMENT_SUBJECT_OUTPUT_V1"
    assert result["model_calls"] == 1
    assert result["external_model_calls"] == 0
    assert result["external_network_calls"] == 0
    assert result["provider_calls"] == 0
    assert len(adapter.calls) == 1
    binding = adapter.calls[0]["binding"]
    assert binding["expected_model_digest"] == "D" * 64
    assert binding["cloud_fallback"] is False


def test_channel_fails_closed_for_remote_profiles_instead_of_reinventing_execution():
    from memorive_workflow_bridge.conversation_refinement import (
        ConversationRefinementBridgeError,
    )

    with pytest.raises(
        ConversationRefinementBridgeError,
        match="REFINEMENT_EXECUTION_CHANNEL_NOT_AVAILABLE",
    ):
        _channel(_local_row()).execute(_request("cli:codex_cli:gpt-5-6-luna"))
    with pytest.raises(
        ConversationRefinementBridgeError,
        match="REFINEMENT_EXECUTION_CHANNEL_NOT_AVAILABLE",
    ):
        _channel(_local_row()).execute(_request("api-config-001"))


def test_channel_fails_closed_for_local_identity_gap():
    from memorive_workflow_bridge.conversation_refinement import (
        ConversationRefinementBridgeError,
    )

    local = _local_row()
    local["model_digest"] = None
    local["execution_eligible"] = False
    with pytest.raises(
        ConversationRefinementBridgeError,
        match="LOCAL_MODEL_NOT_EXECUTION_ELIGIBLE",
    ):
        _channel(local).execute(_request(local["profile_ref"]))


def test_bridge_is_only_a_phase6_contract_and_phase7_local_composition():
    bridge = (
        ROOT / "source" / "memorive_workflow_bridge" / "conversation_refinement.py"
    ).read_text(encoding="utf-8")
    assert "model_gateway.local_structured_chat" in bridge
    assert "knowledge_feedback.conversation_refinement" in bridge
    assert "SafeRelayClient" not in bridge
    assert "Phase5ApiStructuredExecutor" not in bridge
    assert "Phase5CliStructuredExecutor" not in bridge
    assert "model_gateway.execution_core.adapters" not in bridge
    assert "memorive_settings.refinement_execution" not in bridge
