from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
from typing import Any


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"
for candidate in (SOURCE_ROOT, DESKTOP_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive.sessions.refinement.brief import validate_brief  # noqa: E402
from memorive.desktop.service.control_store import ControlStore  # noqa: E402
from memorive.documents.inbox import InboxController  # noqa: E402
from memorive.documents.library import LibraryProductController  # noqa: E402
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


def test_bridge_returns_a_checked_brief() -> None:
    local = _local_row()
    result = ExistingConversationRefinementChannel(
        local_model_registry=_LocalRegistry(local), local_adapter=_LocalAdapter(),
    ).execute(_request(local["profile_ref"]))

    assert validate_brief(result["brief"]) == result["brief"]
    assert result["brief"]["items"] and result["brief"]["quotes"]


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


