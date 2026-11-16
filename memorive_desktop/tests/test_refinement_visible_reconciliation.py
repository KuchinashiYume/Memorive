from __future__ import annotations

from pathlib import Path
import sys
import time
from typing import Any, Mapping


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"
SOURCE_ROOT = ATTEMPT_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from memorive.documents.inbox import InboxController  # noqa: E402
from memorive.sessions.controller import SessionsProductController  # noqa: E402
from tests.test_session_refinement_tasks import (  # noqa: E402
    LOCAL_PROFILE_REF,
    _FacadeService,
    _LocalModelRegistry,
    _browser_bundle,
    _current_task,
)
from tests.test_refinement_inbox_and_local_mapping import (  # noqa: E402
    _BlockingChannel,
    _QueuedSessionsService,
)


def test_queue_terminal_transition_has_a_dedicated_ui_projection_observer() -> None:
    sessions = (
        ATTEMPT_ROOT / "source" / "memorive" / "sessions" / "controller.py"
    ).read_text(encoding="utf-8")
    runtime = (ATTEMPT_ROOT / "source" / "runtime.py").read_text(
        encoding="utf-8"
    )

    assert "refinement_queue_observer" in sessions
    assert "_notify_refinement_queue_projection" in sessions
    assert "_on_refinement_queue_projection" in runtime


