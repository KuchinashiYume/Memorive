from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import threading
import time
from typing import Any, Mapping


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"
for candidate in (SOURCE_ROOT, DESKTOP_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from knowledge_feedback.canonical import make_hashed_payload, typed_payload_hash  # noqa: E402
from memorive.sessions.refinement.refinement import make_typed_refinement_item  # noqa: E402
from memorive.sessions.refinement import (  # noqa: E402
    make_context_capsule,
    validate_normalized_graph,
    validate_typed_refinement_item,
)
from memorive_desktop_service.control_store import ControlStore  # noqa: E402
from memorive_inbox import InboxController  # noqa: E402
from memorive_library import LibraryProductController  # noqa: E402
from memorive.sessions.refinement.model_channel import (  # noqa: E402
    ExistingConversationRefinementChannel,
)
from memorive.sessions.controller import SessionsProductController  # noqa: E402
from memorive.models.validation.runner import LiveModelValidationRunner  # noqa: E402
from memorive.exams.runner import WorkflowExams  # noqa: E402
from tests.test_existing_phase_refinement_bridge import (  # noqa: E402
    _LocalAdapter,
    _LocalRegistry,
    _local_row,
    _request,
)
from tests.test_session_refinement_tasks import (  # noqa: E402
    LOCAL_PROFILE_REF,
    _FacadeService,
    _LocalModelRegistry,
    _browser_bundle,
    _current_task,
)
from tests.test_refinement_inbox_and_local_mapping import (  # noqa: E402
    _QueuedSessionsService,
)
from tests.test_refinement_runtime_coordination import (  # noqa: E402
    _HealthyService,
)


def _formal_subject(request: Mapping[str, Any]) -> dict[str, Any]:
    conversation_id = "conversation:sample"
    node = {
        "message_id": "message-00000000",
        "conversation_id": conversation_id,
        "parent_id": None,
        "role": "user",
        "blocks": [
            {
                "type": "text",
                "text": "验收会话需要生成可复核的本地精炼成品。",
                "metadata": {},
            }
        ],
        "created_at": "SEQUENCE:00000000",
        "status": "VISIBLE_COMPLETE",
        "edit_of": None,
        "regeneration_group": None,
        "provider_sequence": 0,
    }
    graph = validate_normalized_graph(
        make_hashed_payload(
            {
                "schema_version": "CONVERSATION_REFINEMENT_NORMALIZED_CONVERSATION_GRAPH_V1",
                "graph_id": f"graph:{request['task_id']}",
                "revision": 1,
                "snapshot_ref": f"desktop-session-snapshot:{request['task_id']}@1",
                "snapshot_hash": typed_payload_hash(
                    {"source_snapshot_sha256": request["source_snapshot_sha256"]}
                ),
                "conversation_ids": [conversation_id],
                "nodes": [node],
                "orphan_message_ids": [],
                "edge_policy": "EXPLICIT_PARENT_ONLY_NO_TIMESTAMP_INFERENCE",
                "deterministic": True,
            }
        )
    )
    capsule = make_context_capsule(
        capsule_id=f"capsule:{request['task_id']}:message-00000000",
        required_message_refs=[node["message_id"]],
        scope="SELECTED_SESSION_MESSAGES",
    )
    item = make_typed_refinement_item(
        item_id=f"item:{request['task_id']}:message-00000000",
        graph=graph,
        node=node,
        capsule=capsule,
        scope="SELECTED_SESSION_MESSAGES",
    )
    return {
        "schema_version": "CONVERSATION_REFINEMENT_SUBJECT_OUTPUT_V1",
        "pack_id": request["task_id"],
        "graph_hash": deepcopy(graph["content_hash"]),
        "items": [item],
    }


def _sessions(
    tmp_path: Path,
    *,
    channel: Any,
    result_observer: Any | None = None,
) -> tuple[SessionsProductController, InboxController, Any, str]:
    source_root = tmp_path / "source"
    source_root.mkdir(parents=True)
    inbox = InboxController(source_roots=[source_root], data_root=tmp_path / "inbox")
    current_task = _current_task(tmp_path, _FacadeService(tmp_path / "facade"))
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    codex_home.mkdir()
    claude_home.mkdir()
    sessions = SessionsProductController(
        _QueuedSessionsService(inbox),
        object(),
        ATTEMPT_ROOT / "contracts" / "analysis",
        ATTEMPT_ROOT / "fixtures_t08",
        tmp_path / "sessions-state",
        source_roots={"codex_home": codex_home, "claude_home": claude_home},
        local_model_registry=_LocalModelRegistry(),
        conversation_refinement_channel=channel,
        refinement_task_manager=current_task,
        refinement_result_observer=result_observer,
    )
    sessions.import_browser_bundle(_browser_bundle())
    projection_id = sessions.call("sessions.list", {"source": "deepseek"})["rows"][0][
        "local_projection_id"
    ]
    sessions.call(
        "sessions.refinement_settings_save",
        {"profile_ref": LOCAL_PROFILE_REF, "include_tasks": True, "include_risks": True},
    )
    return sessions, inbox, current_task, projection_id


def test_bridge_returns_existing_formal_typed_items_not_parallel_simplified_items() -> None:
    local = _local_row()
    adapter = _LocalAdapter()
    result = ExistingConversationRefinementChannel(
        local_model_registry=_LocalRegistry(local),
        local_adapter=adapter,
        prompt_path=(
            ATTEMPT_ROOT
            / "source"
            / "memorive" / "models" / "gateway"
            / "prompts"
            / "conversation_refinement"
            / "v2.md"
        ),
    ).execute(_request(local["profile_ref"]))

    assert result["subject_output"]["items"]
    for item in result["subject_output"]["items"]:
        assert item["schema_version"] == "CONVERSATION_REFINEMENT_TYPED_REFINEMENT_ITEM_V1"
        assert validate_typed_refinement_item(item) == item
        assert item["truth_decided_by_refiner"] is False


def test_workflow_model_test_reuses_exact_frozen_exam_score_without_new_call(
    tmp_path: Path,
) -> None:
    runner = LiveModelValidationRunner(
        credential_resolver=lambda _ref: (_ for _ in ()).throw(
            AssertionError("frozen score lookup must not resolve a credential")
        ),
        preferences_loader=lambda: {},
        scratch_root=tmp_path,
    )
    runner = WorkflowExams(
        runner=runner,
        catalog={
            "schema_version": "WorkflowModelExamScoreCatalog-v1",
            "entries": [
                {
                    "node_id": "card_distill",
                    "profile_kind": "API",
                    "provider_family": "OPENAI_COMPATIBLE",
                    "model_name": "deepseek-v4-flash",
                    "thinking": True,
                    "tier": "flash",
                    "verdict": "FAIL",
                    "score": 59,
                    "evidence_sha256": "A" * 64,
                }
            ],
        },
    )

    result = runner.test_workflow_node(
        {"node_id": "card_distill"},
        {
            "kind": "API",
            "provider": "DEEPSEEK_API_KEY_00",
            "model_name": "deepseek-v4-flash",
            "thinking": True,
            "tier": "flash",
        },
    )

    assert result["status"] == "FAIL"
    assert result["score"] == 59
    assert result["reason"] == "FROZEN_MODEL_EXAM_SCORE_REUSED"
    assert result["external_model_calls"] == 0
    assert result["provider_calls"] == 0


def test_workflow_model_test_fails_closed_when_profile_does_not_exactly_match(
    tmp_path: Path,
) -> None:
    runner = LiveModelValidationRunner(
        credential_resolver=lambda _ref: b"unused",
        preferences_loader=lambda: {},
        scratch_root=tmp_path,
    )
    runner = WorkflowExams(
        runner=runner,
        catalog={
            "schema_version": "WorkflowModelExamScoreCatalog-v1",
            "entries": [],
        },
    )

    result = runner.test_workflow_node(
        {"node_id": "card_distill"},
        {
            "kind": "API",
            "provider": "DEEPSEEK_API_KEY_00",
            "model_name": "unexamined-model",
            "thinking": True,
            "tier": "flash",
        },
    )

    assert result["status"] == "NOT_RUN"
    assert result["score"] is None
    assert result["external_model_calls"] == 0


