from __future__ import annotations

from pathlib import Path
import json
import sys
import threading
import time
from typing import Any, Mapping


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from memorive_current_task.refinement_tasks import SessionRefinementTaskStore  # noqa: E402
from memorive_inbox import InboxController  # noqa: E402
from memorive_inbox import InboxServiceAdapter  # noqa: E402
from memorive_sessions import SessionsProductController  # noqa: E402
from memorive_settings.contracts import default_settings, validate_settings  # noqa: E402
from memorive.models.capabilities import (  # noqa: E402
    CHAT,
    EMBEDDING,
    RERANKER,
    VISION_OCR,
    workflow_node_accepts_capability,
)
from memorive_workflow_bridge.conversation_refinement import (  # noqa: E402
    ExistingConversationRefinementChannel,
)
from memorive.models.validation import (  # noqa: E402
    HttpResponse,
    LiveModelValidationRunner,
    ProcessResult,
)
from tests.test_session_refinement_tasks import (  # noqa: E402
    LOCAL_PROFILE_REF,
    _FacadeService,
    _FakeExistingConversationRefinementChannel,
    _LocalModelRegistry,
    _SessionsService,
    _browser_bundle,
    _current_task,
)


def test_refinement_task_store_finds_one_active_task_per_session(tmp_path: Path) -> None:
    store = SessionRefinementTaskStore(tmp_path / "current-task")
    first = store.create_task(
        display_name="旧精炼任务",
        local_projection_id="session-refinement-idempotent",
        source_snapshot_sha256="A" * 64,
        selected_profile_ref="local:111111111111111111111111",
        message_count=2,
    )
    store.fail(first["job_id"], error_code="EXPECTED_TEST_FAILURE")
    active = store.create_task(
        display_name="活动精炼任务",
        local_projection_id="session-refinement-idempotent",
        source_snapshot_sha256="B" * 64,
        selected_profile_ref="local:222222222222222222222222",
        message_count=2,
    )

    found = store.find_session_refinement_task("session-refinement-idempotent")

    assert found is not None
    assert found["job_id"] == active["job_id"]
    assert found["control_state"] == "QUEUED"


def test_inbox_refinement_queue_is_visible_and_idempotent(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    inbox = InboxController(source_roots=[source_root], data_root=tmp_path / "inbox")
    payload = {
        "local_projection_id": "session-refinement-inbox",
        "display_name": "精炼对话 · 需要排队的会话",
        "source_snapshot_sha256": "C" * 64,
        "selected_profile_ref": "local:333333333333333333333333",
        "message_count": 4,
        "idempotency_key": "refinement-session-refinement-inbox-candidate",
    }

    first = inbox.enqueue_session_refinement(**payload)
    replay = inbox.enqueue_session_refinement(**payload)
    projection = inbox.projection()

    assert first["status"] == "QUEUED"
    assert replay["status"] == "ALREADY_QUEUED"
    assert replay["item"]["item_id"] == first["item"]["item_id"]
    assert projection["queued_count"] == 1
    assert projection["cards"][0]["item_kind"] == "SESSION_REFINEMENT"
    assert projection["cards"][0]["local_projection_id"] == payload["local_projection_id"]


def test_settings_workflow_publishes_recognized_local_models_first() -> None:
    bridge = (ATTEMPT_ROOT / "product" / "web" / "settings_bridge.js").read_text(encoding="utf-8")

    assert "return [...localOptions, ...apiOptions, ...cliOptions];" in bridge
    assert "model: localModelDisplayName(model)" in bridge
    assert "const capabilities = normalizeLocalCapabilities(model);" in bridge
    assert "capabilities," in bridge


class _QueuedSessionsService(_SessionsService):
    def __init__(self, inbox: InboxController):
        self._inbox = InboxServiceAdapter(inbox)

    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        if method.startswith("inbox."):
            return self._inbox.call(method, params)
        return super().call(method, params)


class _BlockingChannel(_FakeExistingConversationRefinementChannel):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def execute(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        self.entered.set()
        if not self.release.wait(3):
            raise RuntimeError("TEST_REFINEMENT_RELEASE_TIMEOUT")
        return super().execute(request)


class _NoLocalModels:
    def call(self, _method: str, _params: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"recognized_models": []}


class _ExistingProfileExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        accepted = dict(params)
        self.calls.append((method, accepted))
        schema = accepted["response_schema"]
        properties = schema["properties"]
        items = []
        for item_schema in properties["items"]["prefixItems"]:
            message_id = item_schema["properties"]["message_id"]["const"]
            items.append(
                {
                    "message_id": message_id,
                    "candidate_text": "复用现有执行通道生成的候选。",
                    "refinement_type": "USER_DECISION_CLAIM",
                }
            )
        response = {"items": items}
        is_cli = str(accepted["profile_ref"]).startswith("cli:")
        return {
            "schema_version": "SettingsStructuredChatExecutionResult-v1",
            "profile_ref": accepted["profile_ref"],
            "requested_model": "gpt-5.6-sol" if is_cli else "deepseek-v4-flash",
            "returned_model": "gpt-5.6-sol" if is_cli else "deepseek-v4-flash",
            "response": response,
            "external_model_calls": 1,
            "external_network_calls": 0 if is_cli else 1,
            "provider_calls": 0 if is_cli else 1,
            "execution_receipt": {"status": "PASS", "route": "EXISTING_SETTINGS_EXECUTION"},
            "status": "PASS",
        }


def _refinement_request(profile_ref: str) -> dict[str, Any]:
    return {
        "task_id": "refinement-existing-executor",
        "logical_slot": "conversation_refinement",
        "profile_ref": profile_ref,
        "explicit_user_action": True,
        "source_snapshot_sha256": "D" * 64,
        "source": {"provider_id": "universal", "provider_session_id": "session-1"},
        "messages": [
            {"role": "user", "content": "请提炼决定。"},
            {"role": "assistant", "content": "决定使用现有执行组件。"},
        ],
        "review_required": True,
        "auto_ingest": False,
    }


def test_existing_refinement_channel_reuses_saved_api_and_cli_executor() -> None:
    executor = _ExistingProfileExecutor()
    channel = ExistingConversationRefinementChannel(
        local_model_registry=_NoLocalModels(), profile_executor=executor
    )

    api = channel.execute(_refinement_request("DEEPSEEK_API_KEY_00-primary"))
    cli = channel.execute(_refinement_request("cli:codex-cli:gpt-5.6-sol"))

    assert [call[0] for call in executor.calls] == [
        "settings.execute_structured_chat",
        "settings.execute_structured_chat",
    ]
    assert api["external_network_calls"] == 1
    assert cli["external_network_calls"] == 0
    assert api["subject_output"]["schema_version"] == "CONVERSATION_REFINEMENT_SUBJECT_OUTPUT_V1"
    assert cli["subject_output"]["schema_version"] == "CONVERSATION_REFINEMENT_SUBJECT_OUTPUT_V1"


def test_refinement_queue_is_not_projected_as_current_task(tmp_path: Path) -> None:
    facade_service = _FacadeService(tmp_path / "facade")
    current_task = _current_task(tmp_path, facade_service)
    # The controller's create_session_refinement_task only forwarded to its store and has
    # no product caller (the runtime fills the same SessionRefinementTaskStore), so call the store.
    current_task._refinement_store.create_task(
        display_name="精炼对话 · 仅在收件箱显示",
        local_projection_id="session-inbox-only",
        source_snapshot_sha256="E" * 64,
        selected_profile_ref=LOCAL_PROFILE_REF,
        message_count=2,
    )

    projection = current_task.call("current_task.list", {})

    assert projection["refinement_bindings"] == []
    assert projection["refinement_binding_count"] == 0
    assert all(row.get("workflow_kind") != "SESSION_REFINEMENT" for row in projection["tasks"])


def test_runtime_uses_a_sessions_owned_refinement_queue_store() -> None:
    runtime = (ATTEMPT_ROOT / "source" / "runtime.py").read_text(encoding="utf-8")

    assert 'state_root=self._state_dir / "current_task"' not in runtime
    assert 'self._state_dir / "sessions" / "refinement_queue_tasks"' in runtime
    assert "refinement_store=self._refinement_queue_tasks" in runtime
    assert "refinement_task_manager=self._refinement_queue_tasks" in runtime


