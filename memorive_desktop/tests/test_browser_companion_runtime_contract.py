from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "source", ROOT / "product" / "desktop"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from runtime import ProductApi  # noqa: E402
from browser_companion_support import companion  # noqa: E402


def _dedicated_report() -> dict[str, object]:
    return {
        "schema_version": "MEMORIVE_BROWSER_SESSION_SYNC_REPORT_V2",
        "source": "MEMORIVE_BROWSER_COMPANION_RUNNER_V4_DRAFT",
        "extension_id": "abcdefghijklmnopabcdefghijklmnop",
        "extension_version": "3.5.0",
        "per_provider_limit": 2,
        "sync_mode": "PREFLIGHT",
        "browser_family": "EDGE",
        "generated_at": "2026-08-29T10:20:00Z",
        "trigger": "APPROVED_PROVIDER_FRAGMENT_PREFLIGHT_V1",
        "status": "COMPLETE",
        "active_provider_count": 3,
        "provider_parallelism": True,
        "maximum_capture_windows": 1,
        "per_provider_capture_mode": "DEDICATED_MINIMIZED_WINDOW_OWNED_TABS",
        "window_mode": "DEDICATED_MINIMIZED",
        "user_tab_reused": False,
        "providers": [
            {
                "provider_id": provider,
                "status": "COMPLETE",
                "indexed_count": 2,
                "candidate_count": 2,
                "capture_target": 2,
                "body_read_count": 2,
                "captured_count": 2,
                "unchanged_count": 0,
                "failed_count": 0,
                "skipped_candidate_count": 0,
                "error_code": "",
                "skipped_error_codes": [],
                "safe_diagnostic": None,
            }
            for provider in ("deepseek", "gemini", "kimi")
        ],
        "cookie_value_reads": 0,
        "browser_storage_value_reads": 0,
        "hidden_provider_api_calls": 0,
        "remote_session_mutations": 0,
    }


def test_sync_report_accepts_only_coherent_dedicated_minimized_window_shape(
    tmp_path: Path,
) -> None:
    valid_root = tmp_path / "valid"
    valid_root.mkdir()
    valid_root.joinpath("memorive-session-library-sync-report-dedicated.json").write_text(
        json.dumps(_dedicated_report()),
        encoding="utf-8",
    )
    api = companion()
    api._browser_session_download_roots = lambda: [valid_root]

    loaded = api._latest_browser_companion_sync_report(
        expected_sync_mode="PREFLIGHT"
    )

    assert loaded is not None
    assert loaded["window_mode"] == "DEDICATED_MINIMIZED"
    assert loaded["user_tab_reused"] is False
    assert loaded["maximum_capture_windows"] == 1
    assert (
        loaded["per_provider_capture_mode"]
        == "DEDICATED_MINIMIZED_WINDOW_OWNED_TABS"
    )

    invalid_root = tmp_path / "invalid"
    invalid_root.mkdir()
    invalid = _dedicated_report()
    invalid["user_tab_reused"] = True
    invalid_root.joinpath("memorive-session-library-sync-report-reused.json").write_text(
        json.dumps(invalid),
        encoding="utf-8",
    )
    api._browser_session_download_roots = lambda: [invalid_root]

    assert (
        api._latest_browser_companion_sync_report(expected_sync_mode="PREFLIGHT")
        is None
    )


