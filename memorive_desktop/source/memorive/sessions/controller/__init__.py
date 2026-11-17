"""The sessions page's controller: SessionsProductController answers the sessions.* calls of the window.

  common.py              the method set, channels, public fields, text and timestamp helpers,
                         SessionProductError
  state.py               the page state file: its fields, migration and checks, source roots
  local_cli.py           sessions of local command-line tools (Codex, Claude Code) found on disk
  browser.py             browser conversations and the visible-UI reader: checks, import, archive
  listing.py             the session list and a session's messages as the page shows them
  refinement_queue.py    the queue of conversations waiting for refinement
  refinement_tasks.py    refinement tasks: settings, start, run, cancel
  product_controller.py  SessionsProductController: construction and call(), built from the parts above

SessionsProductController, SessionProductError, SESSIONS_METHODS and REAL_CHANNELS stay importable
from this package.
"""
from __future__ import annotations

from memorive.sessions.controller.common import (
    SESSIONS_METHODS,
    REAL_CHANNELS,
    SessionProductError,
)
from memorive.sessions.controller.product_controller import (
    SessionsProductController,
)

__all__ = [
    "REAL_CHANNELS",
    "SESSIONS_METHODS",
    "SessionProductError",
    "SessionsProductController",
]
