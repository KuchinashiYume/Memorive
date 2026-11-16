from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
for candidate in (ATTEMPT_ROOT / "source", ATTEMPT_ROOT / "product" / "desktop"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import runtime as runtime_module  # noqa: E402
from runtime import ProductApi  # noqa: E402
from browser_companion_support import companion  # noqa: E402


CURRENT_VERSION = json.loads(
    (
        ATTEMPT_ROOT
        / "source"
        / "memorive_browser_companion"
        / "extension"
        / "manifest.json"
    ).read_text(encoding="utf-8")
)["version"]
PREFLIGHT_URL = (
    "https://gemini.google.com/app"
    "?memorive_bridge_sync=v1#memorive-browser-session-preflight-2-v5"
)


def _api(tmp_path: Path) -> tuple[ProductApi, Path, Path]:
    api = companion()
    api.source_root = ATTEMPT_ROOT / "source"
    api.profile_root = tmp_path / "profile"
    api.system_effects_enabled = False
    downloads = tmp_path / "Downloads"
    downloads.mkdir(parents=True)
    api._browser_session_download_roots = lambda: [downloads]
    state_path = api._browser_companion_state_path()
    state_path.parent.mkdir(parents=True)
    return api, downloads, state_path


def _write_state(api: ProductApi, state_path: Path, **overrides: object) -> None:
    _source, _required, package_sha256 = api._browser_companion_package()
    state = {
        "schema_version": "DesktopBrowserCompanionSetupState-v1",
        "prepared": True,
        "prepared_at_unix": int(time.time()) - 2,
        "package_sha256": package_sha256,
        "browser_reload_required": False,
        "connected_browser": "EDGE",
        "extension_id": "abcdefghijklmnopabcdefghijklmnop",
        "import_verified": True,
    }
    state.update(overrides)
    state_path.write_text(json.dumps(state), encoding="utf-8")


def _write_identity(downloads: Path) -> None:
    folder = downloads / "Memorive会话桥接"
    folder.mkdir(exist_ok=True)
    payload = {
        "schema_version": "MEMORIVE_BROWSER_COMPANION_IDENTITY_V1",
        # Identity receipts carry these since extension 3.6.0 / 3.10.1.
        "browser_family": "EDGE",
        "site_profile_sha256": __import__("memorive_browser_companion.site_profiles", fromlist=["x"]).profile_hash(
            __import__("memorive_browser_companion.site_profiles", fromlist=["x"]).default_sites()),
        "extension_id": "abcdefghijklmnopabcdefghijklmnop",
        "extension_version": CURRENT_VERSION,
        "observed_at": "2026-08-29T11:00:00Z",
        "lifecycle_reason": "UPDATE",
        "trigger_protocol": "HTTPS_APPROVED_FRAGMENT_V3",
        "preflight_limit": 2,
        "full_limit": 50,
        "incremental_limit": 10,
        "providers": ["deepseek", "gemini", "kimi"],
        "cookie_value_reads": 0,
        "browser_storage_value_reads": 0,
        "hidden_provider_api_calls": 0,
    }
    (folder / f"memorive-browser-companion-identity-{CURRENT_VERSION}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_preflight(
    downloads: Path,
    *,
    failed_provider: str | None = None,
) -> None:
    folder = downloads / "Memorive会话桥接"
    folder.mkdir(exist_ok=True)
    providers = []
    for provider_id in ("deepseek", "gemini", "kimi"):
        failed = provider_id == failed_provider
        providers.append(
            {
                "provider_id": provider_id,
                "status": "FAILED" if failed else "COMPLETE",
                "indexed_count": 2,
                "candidate_count": 2,
                "capture_target": 2,
                "body_read_count": 2,
                "captured_count": 0 if failed else 2,
                "unchanged_count": 0,
                "failed_count": 1 if failed else 0,
                "skipped_candidate_count": 0,
                "error_code": "CONVERSATION_MESSAGES_NOT_FOUND" if failed else "",
                "skipped_error_codes": [],
                "safe_diagnostic": None,
            }
        )
    payload = {
        "schema_version": "MEMORIVE_BROWSER_SESSION_SYNC_REPORT_V2",
        "source": "MEMORIVE_BROWSER_COMPANION_RUNNER_V4_DRAFT",
        "extension_id": "abcdefghijklmnopabcdefghijklmnop",
        "extension_version": CURRENT_VERSION,
        "per_provider_limit": 2,
        "sync_mode": "PREFLIGHT",
        "browser_family": "EDGE",
        "generated_at": "2026-08-29T11:01:00Z",
        "trigger": "APPROVED_PROVIDER_FRAGMENT_PREFLIGHT_V1",
        "status": "COMPLETE" if failed_provider is None else "PARTIAL",
        "active_provider_count": 3,
        "provider_parallelism": True,
        "maximum_capture_windows": 1,
        "per_provider_capture_mode": "DEDICATED_MINIMIZED_WINDOW_OWNED_TABS",
        "window_mode": "DEDICATED_MINIMIZED",
        "user_tab_reused": False,
        "providers": providers,
        "cookie_value_reads": 0,
        "browser_storage_value_reads": 0,
        "hidden_provider_api_calls": 0,
        "remote_session_mutations": 0,
    }
    (folder / "memorive-session-library-sync-report-repair-gate.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _eligible_cursor() -> dict[str, object]:
    return {
        "eligible": True,
        "provider_counts": {"deepseek": 5, "gemini": 5, "kimi": 5},
        "known_session_count": 15,
        "overlap_fingerprint_count": 6,
        "cursor_token": "fixture-token",
        "cursor_schema_version": "MEMORIVE_BROWSER_SESSION_CURSOR_V1",
        "cursor_token_sha256": "A" * 64,
    }


def test_auto_refresh_marks_one_repair_preflight_when_extension_version_changes(
    tmp_path: Path,
) -> None:
    api, _downloads, state_path = _api(tmp_path)
    _write_state(api, state_path, package_sha256="OLD")
    destination = api.profile_root / "browser_companion" / "extension-v1"
    destination.mkdir(parents=True)
    (destination / "manifest.json").write_text(
        json.dumps({"manifest_version": 3, "version": "3.4.1"}),
        encoding="utf-8",
    )

    receipt = api.refresh_prepared_package()
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert receipt["status"] == "UPDATED"
    assert receipt["capture_repair_preflight_required"] is True
    assert state["capture_repair_preflight_required"] is True
    assert state["capture_repair_target_version"] == CURRENT_VERSION


def test_explicit_install_preserves_upgrade_repair_gate_without_auto_sync(
    tmp_path: Path,
) -> None:
    api, _downloads, state_path = _api(tmp_path)
    _write_state(api, state_path, package_sha256="OLD")
    destination = api.profile_root / "browser_companion" / "extension-v1"
    destination.mkdir(parents=True)
    (destination / "manifest.json").write_text(
        json.dumps({"manifest_version": 3, "version": "3.4.1"}),
        encoding="utf-8",
    )

    receipt = api.install_browser_companion()
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert receipt["capture_repair_preflight_required"] is True
    assert receipt["bridge_reload_requested"] is False
    assert state["capture_repair_preflight_required"] is True
    assert state["capture_repair_target_version"] == CURRENT_VERSION


def test_repair_gate_forces_preflight_even_with_an_incremental_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, downloads, state_path = _api(tmp_path)
    _write_state(
        api,
        state_path,
        capture_repair_preflight_required=True,
        capture_repair_target_version=CURRENT_VERSION,
    )
    _write_identity(downloads)
    api._browser_companion_incremental_cursor = _eligible_cursor
    api.system_effects_enabled = True
    api._browser_executables = lambda: [
        ("Microsoft Edge", Path("C:/Program Files/Edge/msedge.exe"), "edge://extensions")
    ]
    launched: list[list[str]] = []

    class _Process:
        pid = 46001

    monkeypatch.setattr(
        runtime_module.subprocess,
        "Popen",
        lambda command, **_kwargs: launched.append(command) or _Process(),
    )

    status = api.browser_companion_status()
    receipt = api.run_browser_companion_sync()

    assert status["capture_repair_preflight_required"] is True
    assert status["next_sync_mode"] == "PREFLIGHT"
    assert status["incremental_sync_eligible"] is False
    assert status["full_sync_eligible"] is False
    assert launched == [["C:\\Program Files\\Edge\\msedge.exe", PREFLIGHT_URL]]
    assert receipt["sync_mode"] == "PREFLIGHT"
    assert receipt["per_provider_limit"] == 2


def test_failed_provider_does_not_clear_repair_gate(tmp_path: Path) -> None:
    api, downloads, state_path = _api(tmp_path)
    _write_state(
        api,
        state_path,
        capture_repair_preflight_required=True,
        capture_repair_target_version=CURRENT_VERSION,
    )
    _write_identity(downloads)
    _write_preflight(downloads, failed_provider="deepseek")
    api._browser_companion_incremental_cursor = _eligible_cursor

    status = api.browser_companion_status()
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert status["capture_repair_preflight_required"] is True
    assert status["next_sync_mode"] == "PREFLIGHT"
    assert state["capture_repair_preflight_required"] is True


