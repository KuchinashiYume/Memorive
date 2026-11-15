from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "source" / "memorive_browser_companion" / "extension"


def _runner() -> str:
    return (EXTENSION / "runner.js").read_text(encoding="utf-8")


def test_full_sync_uses_a_dedicated_long_running_budget() -> None:
    runner = _runner()

    assert "const SYNC_HARD_TIMEOUT_MS_BY_MODE = Object.freeze({" in runner
    assert "PREFLIGHT: 20 * 60 * 1000" in runner
    assert "INCREMENTAL: 30 * 60 * 1000" in runner
    assert "FULL: 90 * 60 * 1000" in runner
    assert "const SYNC_HARD_TIMEOUT_MS = SYNC_HARD_TIMEOUT_MS_BY_MODE[SYNC_MODE];" in runner
    assert "const SYNC_HARD_TIMEOUT_MS = 20 * 60 * 1000;" not in runner


