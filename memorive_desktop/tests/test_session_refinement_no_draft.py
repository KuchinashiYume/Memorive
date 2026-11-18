"""sessions.refinement_get for a session that was never refined: an ordinary NO_DRAFT state, not an error.

Before, the call raised SESSION_REFINEMENT_DRAFT_NOT_FOUND for every such session; the page swallowed it, and the
window log printed a traceback each time a session detail opened.
"""
from __future__ import annotations

from pathlib import Path
import sys

ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from memorive.sessions.controller import SessionsProductController  # noqa: E402
from tests.test_session_refinement_tasks import (  # noqa: E402
    _FacadeService,
    _FakeExistingConversationRefinementChannel,
    _LocalModelRegistry,
    _SessionsService,
    _browser_bundle,
    _current_task,
)


def test_never_refined_session_reports_no_draft(tmp_path: Path) -> None:
    current_task = _current_task(tmp_path, _FacadeService(tmp_path / "facade"))
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
        conversation_refinement_channel=_FakeExistingConversationRefinementChannel(),
        refinement_task_manager=current_task,
    )
    sessions.import_browser_bundle(_browser_bundle())
    row = sessions.call("sessions.list", {"source": "deepseek"})["rows"][0]

    projection = sessions.call("sessions.refinement_get", {"local_projection_id": row["local_projection_id"]})

    assert projection["status"] == "NO_DRAFT"
    assert projection["local_projection_id"] == row["local_projection_id"]
    assert projection["draft"] is None
    assert projection["task"] is None
