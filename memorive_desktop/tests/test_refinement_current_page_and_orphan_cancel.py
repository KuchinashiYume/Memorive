from __future__ import annotations

from pathlib import Path
import sys
import time


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from memorive_inbox import InboxController  # noqa: E402
from memorive_sessions import SessionsProductController  # noqa: E402
from tests.test_universal_current_page_capture import (  # noqa: E402
    _current_page_bundle,
)
from tests.test_session_refinement_tasks import (  # noqa: E402
    LOCAL_PROFILE_REF,
    _FacadeService,
    _LocalModelRegistry,
    _current_task,
)
from tests.test_refinement_inbox_and_local_mapping import (  # noqa: E402
    _BlockingChannel,
    _QueuedSessionsService,
)


def _controller(tmp_path: Path, channel: _BlockingChannel):
    source_root = tmp_path / "source"
    source_root.mkdir()
    inbox = InboxController(source_roots=[source_root], data_root=tmp_path / "inbox")
    inbox.set_auto_run(True)
    facade_service = _FacadeService(tmp_path / "facade")
    current_task = _current_task(tmp_path, facade_service)
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
        refinement_task_manager=current_task._refinement_store,  # the runtime passes the store
    )
    return sessions, inbox, current_task


def test_orphan_refinement_error_card_can_still_be_soft_deleted(tmp_path: Path) -> None:
    sessions, inbox, _current_task_manager = _controller(tmp_path, _BlockingChannel())
    queued = inbox.enqueue_session_refinement(
        local_projection_id="universal:orphan-refinement-card",
        display_name="精炼对话 · 已失去任务记录的卡片",
        source_snapshot_sha256="A" * 64,
        selected_profile_ref=LOCAL_PROFILE_REF,
        message_count=2,
        idempotency_key="sample-orphan-refinement-card",
    )
    item_id = queued["item"]["item_id"]
    inbox.begin_session_refinement(item_id, job_id="refinement-orphan-sample")
    inbox.complete_session_refinement(
        item_id,
        succeeded=False,
        error_code="REFINEMENT_MESSAGE_FIELDS_INVALID",
    )

    receipt = sessions.call(
        "sessions.refinement_cancel",
        {"inbox_item_id": item_id},
    )

    assert receipt["status"] == "CANCELLED"
    assert receipt["task_state"] == "NOT_CREATED"
    assert receipt["permanent_deletes"] == 0
    assert inbox.projection()["cards"] == []


def test_other_partial_session_sources_remain_fail_closed() -> None:
    messages = [{"role": "user", "content": "partial"}]
    assert SessionsProductController._refinement_source_content_allowed(
        {
            "provider_id": "deepseek",
            "source_format": "browser_session_library_v4",
            "lineage": {"source_kind": "BROWSER_COMPANION_SESSION_LIBRARY_V4"},
        },
        messages,
        {"status": "PARTIAL"},
    ) is False
    assert SessionsProductController._refinement_source_content_allowed(
        {
            "provider_id": "universal",
            "source_format": "browser_current_page_v1",
            "lineage": {"source_kind": "BROWSER_COMPANION_ACTIVE_TAB_VISIBLE_DOM_V1"},
        },
        [],
        {"status": "PARTIAL"},
    ) is False
