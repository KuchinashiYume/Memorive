from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any, Mapping

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"
for candidate in (SOURCE_ROOT, DESKTOP_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive.desktop.current_task import CurrentTaskProductController  # noqa: E402
from memorive.desktop.current_task.refinement_tasks import REFINEMENT_NODE_IDS  # noqa: E402
from memorive.desktop.service.control_store import ControlStore  # noqa: E402
from memorive.documents.library import LibraryProductController  # noqa: E402
from memorive.desktop.messages import MessagesProductController  # noqa: E402
from memorive.desktop.work_log import WorkLogProductController  # noqa: E402
from tests.test_session_refinement_tasks import (  # noqa: E402
    LOCAL_PROFILE_REF,
    _FacadeService,
)
from tests.test_refinement_runtime_coordination import (  # noqa: E402
    _HealthyService,
)
from tests.test_refinement_result_and_inbox_review import _formal_subject  # noqa: E402
from refinement_answers import brief_for  # noqa: E402


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _messages(tmp_path: Path) -> MessagesProductController:
    return MessagesProductController(
        _HealthyService(),
        ControlStore(tmp_path / "ui"),
        ATTEMPT_ROOT / "contracts" / "retrieval",
        ATTEMPT_ROOT / "fixtures_t07",
        tmp_path / "messages",
        include_synthetic_projection=False,
    )


def _runtime_event(state: str, *, job_id: str = "refinement-sample") -> dict[str, Any]:
    return {
        "job_id": job_id,
        "attempt_id": "attempt-sample",
        "control_state": state,
        "display_name": "精炼对话 · sample 生命周期",
        "error_code": None,
    }


def test_message_store_deletes_only_exact_ids_and_fails_closed(tmp_path: Path) -> None:
    controller = _messages(tmp_path)
    controller.ingest_runtime_event(_runtime_event("RUNNING", job_id="refinement-delete-a"))
    controller.ingest_runtime_event(_runtime_event("RUNNING", job_id="refinement-keep-b"))
    rows = controller.call("messages.list", {})["rows"]
    target = next(
        row for row in rows
        if row["source_event_ref"]["job_id"] == "refinement-delete-a"
    )
    survivor = next(
        row for row in rows
        if row["source_event_ref"]["job_id"] == "refinement-keep-b"
    )

    receipt = controller.store.delete_records([target["message_id"]])

    assert receipt["status"] == "PASS"
    assert receipt["removed_message_ids"] == [target["message_id"]]
    assert receipt["removed_count"] == 1
    assert controller.store.get(survivor["message_id"]).message_id == survivor["message_id"]
    before_missing = controller.store.load(recover_corruption=False)
    with pytest.raises(ValueError, match="MESSAGE_STORE_DELETE_TARGET_MISSING"):
        controller.store.delete_records(["msg-does-not-exist"])
    assert controller.store.load(recover_corruption=False) == before_missing


def test_successful_current_task_remains_for_three_minutes_then_expires(tmp_path: Path) -> None:
    clock = [datetime.now(timezone.utc)]
    controller = CurrentTaskProductController(
        _FacadeService(tmp_path / "facade"),
        ControlStore(tmp_path / "control"),
        ATTEMPT_ROOT / "contracts" / "verification",
        state_root=tmp_path / "current-task",
        completed_success_retention_seconds=180,
        now_provider=lambda: clock[0],
    )
    # The controller's create_session_refinement_task only forwarded to its store and has
    # no product caller (the runtime fills the same SessionRefinementTaskStore), so call the store.
    task = controller._refinement_store.create_task(
        display_name="精炼对话 · sample 终态保留",
        local_projection_id="session-sample-retention",
        source_snapshot_sha256="A" * 64,
        selected_profile_ref=LOCAL_PROFILE_REF,
        message_count=2,
    )
    for node_id in REFINEMENT_NODE_IDS:
        controller._refinement_store.begin_session_refinement_step(task["job_id"], node_id)
        controller._refinement_store.complete_session_refinement_step(task["job_id"], node_id)
    terminal = controller._refinement_store.succeed_session_refinement_task(
        task["job_id"], draft_id="draft-sample", model_calls=1, external_model_calls=0
    )
    completed_at = _parse_utc(terminal["completed_at"])

    clock[0] = completed_at + timedelta(seconds=1)
    retained = controller.call("current_task.list", {})["refinement_bindings"]
    assert len(retained) == 1
    assert retained[0]["control_state"] == "SUCCEEDED"
    assert retained[0]["completion_retention_seconds"] == 180
    assert retained[0]["retention_seconds_remaining"] == 179

    clock[0] = completed_at + timedelta(seconds=180)
    assert controller.call("current_task.list", {})["refinement_bindings"] == []


def _refinement_draft(version: int = 4) -> dict[str, Any]:
    """A refinement draft as the library receives it; version 3 is the per-message layout made before v2.00."""
    task_id = "refinement-sample-library"
    source_hash = "B" * 64
    draft = {
        "schema_version": f"DesktopSessionRefinementDraft-v{version}",
        "draft_id": "draft-sample-library",
        "task_id": task_id,
        "display_name": "精炼对话 · sample 资料库详情",
        "local_projection_id": "session-sample-library",
        "source_snapshot_sha256": source_hash,
        "created_at": "2026-08-30T12:00:00Z",
    }
    if version == 3:
        return {**draft, "items": _formal_subject({"task_id": task_id, "source_snapshot_sha256": source_hash})["items"]}
    messages = [{"role": "user", "content": "验收会话需要生成可复核的本地精炼成品。"}]
    return {**draft, "brief": brief_for({"messages": messages, "options": {"include_tasks": True, "include_risks": True}},
                                        "生成可复核的本地精炼成品。", "decision")}


def test_work_log_projects_live_refinement_task_and_persisted_product(tmp_path: Path) -> None:
    task = {
        "job_id": "refinement-sample-work-log",
        "display_name": "精炼对话 · sample 工作日志",
        "control_state": "SUCCEEDED",
        "created_at": "2026-08-30T11:57:00Z",
        "updated_at": "2026-08-30T12:00:00Z",
        "completed_at": "2026-08-30T12:00:00Z",
        "error_code": "",
        "model_calls": 1,
        "external_model_calls": 0,
        "nodes": [
            {
                "node_id": node_id,
                "revision": f"{index:02d}",
                "name": f"节点 {index}",
                "state": "COMPLETED",
                "started_at": f"2026-08-30T11:5{index}:00Z",
                "completed_at": f"2026-08-30T11:5{index}:30Z",
                "error_code": "",
            }
            for index, node_id in enumerate(REFINEMENT_NODE_IDS, start=1)
        ],
    }
    product = {
        "artifact_id": "refinement-product-sample",
        "stable_locator": "memorive://artifact/refinement-product-sample",
        "task_id": task["job_id"],
        "status": "待复核",
        "item_count": 4,
        "updated_at": "2026-08-30T12:00:00Z",
    }
    controller = WorkLogProductController(
        _HealthyService(),
        ControlStore(tmp_path / "ui"),
        ATTEMPT_ROOT / "contracts" / "t10",
        ATTEMPT_ROOT / "fixtures_t10",
        tmp_path / "work-log",
        include_synthetic_projection=False,
        refinement_task_provider=lambda: [task],
        refinement_product_provider=lambda: [product],
        now_provider=lambda: datetime(2026, 8, 30, 12, 1, tzinfo=timezone.utc),
    )

    projection = controller.call("work_log.bootstrap", {})
    assert projection["record_count"] == 1
    row = projection["records"][0]
    assert row["type_label"] == "会话精炼"
    assert row["state_axis"] == "已经结束"
    assert row["related_artifact_locator"] == product["stable_locator"]
    milestones = controller.call(
        "work_log.get_milestones", {"log_entry_id": row["log_entry_id"]}
    )
    assert milestones["milestone_count"] == 5
    assert milestones["milestones"][-1]["title"] == "精炼成品已写入资料库"
    assert controller.call(
        "work_log.resolve_job_locator", {"job_locator": row["job_locator"]}
    )["route"] == "current-task"


