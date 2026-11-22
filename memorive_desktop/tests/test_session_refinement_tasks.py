from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from memorive.desktop.service.facade import ApplicationFacade  # noqa: E402
from memorive.platform.jobs.sandbox_store import SandboxJobStore  # noqa: E402
from memorive.desktop.current_task import CurrentTaskProductController  # noqa: E402
from memorive.desktop.service.adapters import RealFacadeAdapter  # noqa: E402
from memorive.desktop.service.control_store import ControlStore  # noqa: E402
from memorive.sessions.controller import SessionsProductController  # noqa: E402
from refinement_answers import brief_for  # noqa: E402


REFINEMENT_NODE_IDS = [
    "01_SESSION_SELECTION_FREEZE",
    "02_DIALOGUE_CLEANUP",
    "03_REFINEMENT_MODEL_EXECUTION",
    "04_RESULT_VERIFY_PERSIST",
]
LOCAL_PROFILE_REF = "local:056056056056056056056056"


class _FacadeService:
    """Local-only Application Facade service used by current-task tests."""

    def __init__(self, root: Path):
        self.facade = ApplicationFacade(SandboxJobStore(root, owner_id="sample-current-task"))
        self.adapter = RealFacadeAdapter(self.facade)

    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        if method == "current_task.fixture_transition":
            updated = self.facade.transition(
                str(params["job_id"]),
                int(params["expected_version"]),
                str(params["target"]),
                str(params["event_type"]),
            )
            return {
                "job_id": updated["job_id"],
                "attempt_id": updated["attempt_id"],
                "control_state": updated["control_state"],
                "observed_version": updated["version"],
                "synthetic_only": True,
                "status": "PASS",
            }
        return self.adapter.call(method, params)


class _SessionsService:
    def call(self, method: str, _params: Mapping[str, Any]) -> Mapping[str, Any]:
        if method == "service.health":
            return {
                "protocol_version": "1.0",
                "external_network_calls": 0,
                "external_model_calls": 0,
                "credential_value_reads": 0,
            }
        if method == "settings.get_state":
            return {
                "api_profiles": [
                    {"profile_ref": "profile-refinement-fake", "display_name": "Fake Local Refinement"}
                ],
                "settings": {
                    "cli_services": [
                        {
                            "enabled": True,
                            "adapter_id": "qwen_code",
                            "display_name": "Qwen Code",
                            "models": [
                                {
                                    "profile_ref": "cli:qwen-code:qwen3-coder",
                                    "display_name": "Qwen3 Coder CLI",
                                }
                            ],
                        }
                    ]
                },
            }
        raise AssertionError(method)


class _LocalModelRegistry:
    def call(self, method: str, _params: Mapping[str, Any]) -> Mapping[str, Any]:
        assert method == "local_models.list"
        return {
            "recognized_models": [
                {
                    "profile_ref": LOCAL_PROFILE_REF,
                    "display_name": "Qwen local test",
                    "model_name": "qwen-local-test",
                    "endpoint_kind": "ollama",
                    "connection_status": "AVAILABLE",
                    "exact_identity_available": True,
                    "model_digest": "A" * 64,
                    "structured_chat_adapter": "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1",
                    "execution_eligible": True,
                }
            ]
        }


class _FakeExistingConversationRefinementChannel:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.requests: list[dict[str, Any]] = []

    def execute(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        accepted = dict(request)
        self.requests.append(accepted)
        if self.fail:
            raise RuntimeError("FAKE_LOCAL_REFINEMENT_FAILURE")
        return {
            "profile_ref": accepted["profile_ref"],
            "requested_model": "fake-local-refiner",
            "returned_model": "fake-local-refiner",
            "brief": brief_for(accepted, "本地 fake adapter 生成的精炼候选。"),
            "model_calls": 1,
            "external_model_calls": 0,
            "external_network_calls": 0,
            "provider_calls": 0,
            "execution_receipt": {
                "schema_version": "EvaluationAssetsLocalStructuredChatReceipt-v1",
                "status": "PASS",
                "purpose": "conversation_refinement",
                "requested_model": "fake-local-refiner",
                "returned_model": "fake-local-refiner",
                "external_request_count": 0,
                "local_model_request_count": 1,
            },
        }


def _current_task(tmp_path: Path, service: _FacadeService) -> CurrentTaskProductController:
    return CurrentTaskProductController(
        service,
        ControlStore(tmp_path / "control"),
        ATTEMPT_ROOT / "contracts" / "verification",
        state_root=tmp_path / "current-task-state",
    )


def _browser_bundle() -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "schema_version": "MEMORIVE_BROWSER_SESSION_BUNDLE_V1",
        "source": "MEMORIVE_BROWSER_COMPANION_V1",
        "generated_at": observed_at,
        "conversations": [
            {
                "provider_id": "deepseek",
                "provider_session_id": "sample-refinement-session",
                "title": "sample 精炼任务",
                "observed_at": observed_at,
                "source_url": "https://chat.deepseek.com/a/chat/s/sample-refinement-session",
                "capture_method": "VISIBLE_DOM_FULL_SCROLL_V1",
                "source_truncated": False,
                "messages": [
                    {"message_id": "u-056", "role": "user", "text": "请记录决定与下一步。"},
                    {"message_id": "a-056", "role": "assistant", "text": "决定采用四节点精炼流程，下一步校核落盘。"},
                ],
            }
        ],
    }


def test_explicit_refine_fails_closed_when_existing_channel_is_not_wired(
    tmp_path: Path,
) -> None:
    facade_service = _FacadeService(tmp_path / "facade")
    current_task = _current_task(tmp_path, facade_service)
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    codex_home.mkdir()
    claude_home.mkdir()
    sessions = SessionsProductController(
        _SessionsService(),
        object(),
        ATTEMPT_ROOT / "contracts" / "analysis",
        ATTEMPT_ROOT / "fixtures_t08",
        tmp_path / "sessions-state",
        source_roots={"codex_home": codex_home, "claude_home": claude_home},
        local_model_registry=_LocalModelRegistry(),
        refinement_task_manager=current_task,
    )
    sessions.import_browser_bundle(_browser_bundle())
    row = sessions.call("sessions.list", {"source": "deepseek"})["rows"][0]
    sessions.call(
        "sessions.refinement_settings_save",
        {"profile_ref": LOCAL_PROFILE_REF, "include_tasks": True, "include_risks": True},
    )

    with pytest.raises(Exception, match="SESSION_REFINEMENT_CHANNEL_UNAVAILABLE"):
        sessions.call("sessions.refine", {"local_projection_id": row["local_projection_id"]})

    assert current_task.call("current_task.list", {})["refinement_bindings"] == []


def test_only_execution_eligible_local_profile_can_create_refinement_task(
    tmp_path: Path,
) -> None:
    facade_service = _FacadeService(tmp_path / "facade")
    current_task = _current_task(tmp_path, facade_service)
    channel = _FakeExistingConversationRefinementChannel()
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    codex_home.mkdir()
    claude_home.mkdir()
    sessions = SessionsProductController(
        _SessionsService(),
        object(),
        ATTEMPT_ROOT / "contracts" / "analysis",
        ATTEMPT_ROOT / "fixtures_t08",
        tmp_path / "sessions-state",
        source_roots={"codex_home": codex_home, "claude_home": claude_home},
        local_model_registry=_LocalModelRegistry(),
        conversation_refinement_channel=channel,
        refinement_task_manager=current_task,
    )
    sessions.import_browser_bundle(_browser_bundle())
    row = sessions.call("sessions.list", {"source": "deepseek"})["rows"][0]

    projection = sessions.call("sessions.refinement_settings_get", {})
    by_ref = {item["profile_ref"]: item for item in projection["model_options"]}
    assert by_ref[LOCAL_PROFILE_REF]["execution_eligible"] is True
    assert by_ref[LOCAL_PROFILE_REF]["execution_status"] == "READY_LOCAL_STRUCTURED_CHAT"
    assert by_ref[LOCAL_PROFILE_REF]["display_name"].startswith("[本地]")
    for blocked_ref in ("profile-refinement-fake", "cli:qwen-code:qwen3-coder"):
        assert by_ref[blocked_ref]["execution_eligible"] is False
        assert by_ref[blocked_ref]["execution_status"] == "REFINEMENT_PROFILE_NOT_AVAILABLE"
        assert "尚未验证可用" in by_ref[blocked_ref]["display_name"]

    sessions.call(
        "sessions.refinement_settings_save",
        {
            "profile_ref": "profile-refinement-fake",
            "include_tasks": True,
            "include_risks": True,
        },
    )
    blocked = sessions.call("sessions.refinement_settings_get", {})
    assert blocked["selected_profile_kind"] == "API"
    assert blocked["selected_profile_execution_eligible"] is False
    assert blocked["execution_ready"] is False
    assert blocked["execution_mode"] == "REFINEMENT_PROFILE_EXECUTION_UNAVAILABLE"
    with pytest.raises(
        Exception, match="SESSION_REFINEMENT_PROFILE_EXECUTION_UNAVAILABLE"
    ):
        sessions.call(
            "sessions.refine", {"local_projection_id": row["local_projection_id"]}
        )
    assert current_task.call("current_task.list", {})["refinement_bindings"] == []
    assert channel.requests == []


def test_unstarted_refinement_model_node_accepts_task_local_model_change_and_locks_on_start(
    tmp_path: Path,
) -> None:
    facade_service = _FacadeService(tmp_path / "facade")

    class _TwoModels:
        def call(self, method: str, _params: Mapping[str, Any]) -> Mapping[str, Any]:
            assert method == "local_models.list"
            return {
                "recognized_models": [
                    {
                        "profile_ref": LOCAL_PROFILE_REF,
                        "display_name": "[本地] Qwen A",
                        "model_name": "qwen-a",
                        "endpoint_kind": "ollama",
                        "connection_status": "AVAILABLE",
                        "exact_identity_available": True,
                        "model_digest": "A" * 64,
                        "structured_chat_adapter": "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1",
                        "execution_eligible": True,
                    },
                    {
                        "profile_ref": "local:057057057057057057057057",
                        "display_name": "[本地] Qwen B",
                        "model_name": "qwen-b",
                        "endpoint_kind": "ollama",
                        "connection_status": "AVAILABLE",
                        "exact_identity_available": True,
                        "model_digest": "B" * 64,
                        "structured_chat_adapter": "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1",
                        "execution_eligible": True,
                    },
                ]
            }

    current_task = CurrentTaskProductController(
        facade_service,
        ControlStore(tmp_path / "control"),
        ATTEMPT_ROOT / "contracts" / "verification",
        local_model_registry=_TwoModels(),
        state_root=tmp_path / "current-task-state",
    )
    # The controller's create_session_refinement_task only forwarded to its store and has
    # no product caller (the runtime fills the same SessionRefinementTaskStore), so call the store.
    task = current_task._refinement_store.create_task(
        display_name="可调整精炼任务",
        local_projection_id="session-adjustable-057",
        source_snapshot_sha256="C" * 64,
        selected_profile_ref=LOCAL_PROFILE_REF,
        message_count=2,
    )
    current_task.call("current_task.select_task", {"job_id": task["job_id"]})
    before = current_task.call("current_task.view", {})["projection"]["graph"]["workflow_nodes"]
    model_node = next(row for row in before if row["node_id"] == REFINEMENT_NODE_IDS[2])
    assert model_node["model_change_eligible"] is True
    assert model_node["locked"] is False

    changed = current_task.call(
        "current_task.set_override",
        {
            "node_id": REFINEMENT_NODE_IDS[2],
            "model": "[本地] Qwen B",
            "profile_ref": "local:057057057057057057057057",
        },
    )
    assert changed["override_receipt"]["profile_ref"] == "local:057057057057057057057057"
    binding = current_task._refinement_store.get_session_refinement_task(task["job_id"])
    assert binding["selected_profile_ref"] == "local:057057057057057057057057"
    assert binding["nodes"][2]["model"] == "[本地] Qwen B"

    for node_id in REFINEMENT_NODE_IDS[:3]:
        current_task._refinement_store.begin_session_refinement_step(task["job_id"], node_id)
        if node_id != REFINEMENT_NODE_IDS[2]:
            current_task._refinement_store.complete_session_refinement_step(task["job_id"], node_id)
    with pytest.raises(Exception, match="REFINEMENT_TASK_NODE_ALREADY_STARTED"):
        current_task.call(
            "current_task.set_override",
            {
                "node_id": REFINEMENT_NODE_IDS[2],
                "model": "[本地] Qwen A",
                "profile_ref": LOCAL_PROFILE_REF,
            },
        )
