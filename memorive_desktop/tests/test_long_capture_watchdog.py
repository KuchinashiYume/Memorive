from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "source" / "memorive" / "sessions" / "companion" / "extension"


def test_real_watchdog_failure_writes_its_own_privacy_safe_report() -> None:
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")

    assert "const writeWatchdogSyncReport = async ()" in background
    assert 'schema_version: "MEMORIVE_BROWSER_SESSION_SYNC_REPORT_V2"' in background
    assert 'source: "MEMORIVE_BROWSER_COMPANION_BACKGROUND_WATCHDOG_V1"' in background
    assert 'error_code: "SYNC_RUNNER_WATCHDOG_TIMEOUT"' in background
    assert "cookie_value_reads: 0" in background
    assert "browser_storage_value_reads: 0" in background
    assert "hidden_provider_api_calls: 0" in background
    assert "remote_session_mutations: 0" in background
    assert "await writeWatchdogSyncReport();" in background


