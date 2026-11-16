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
from memorive.sessions.companion.desktop import BrowserCompanionDesktop  # noqa: E402


GEMINI_SYNC_BASE = "https://gemini.google.com/app?memorive_bridge_sync=v1#"
PREFLIGHT_URL = f"{GEMINI_SYNC_BASE}memorive-browser-session-preflight-2-v5"
FULL_URL = f"{GEMINI_SYNC_BASE}memorive-browser-session-full-v4"
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"
CURRENT_EXTENSION_VERSION = json.loads(
    (
        ATTEMPT_ROOT
        / "source"
        / "memorive" / "sessions" / "companion"
        / "extension"
        / "manifest.json"
    ).read_text(encoding="utf-8")
)["version"]


def _api(tmp_path: Path) -> tuple[ProductApi, Path]:
    api = companion()
    api.source_root = ATTEMPT_ROOT / "source"
    api.profile_root = tmp_path / "profile"
    api.system_effects_enabled = False
    downloads = tmp_path / "Downloads"
    downloads.mkdir(parents=True)
    api._browser_session_download_roots = lambda: [downloads]
    _source, _required, package_sha256 = api._browser_companion_package()
    state_path = api._browser_companion_state_path()
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": "DesktopBrowserCompanionSetupState-v1",
                "prepared": True,
                "prepared_at_unix": int(time.time()) - 2,
                "package_sha256": package_sha256,
                "import_verified": False,
            }
        ),
        encoding="utf-8",
    )
    return api, downloads


def _write_report(
    downloads: Path,
    *,
    human_provider: str | None = None,
    failed_provider: str | None = None,
    extension_version: str | None = None,
) -> None:
    identity_payload = {
        "schema_version": "MEMORIVE_BROWSER_COMPANION_IDENTITY_V1",
        # Identity receipts carry these since extension 3.6.0 / 3.10.1.
        "browser_family": "EDGE",
        "site_profile_sha256": __import__("memorive.sessions.companion.site_profiles", fromlist=["x"]).profile_hash(
            __import__("memorive.sessions.companion.site_profiles", fromlist=["x"]).default_sites()),
        "extension_id": "abcdefghijklmnopabcdefghijklmnop",
        "extension_version": extension_version or CURRENT_EXTENSION_VERSION,
        "observed_at": "2026-08-29T02:29:59Z",
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
    folder = downloads / "Memorive会话桥接"
    folder.mkdir(exist_ok=True)
    folder.joinpath(
        f"memorive-browser-companion-identity-{extension_version or CURRENT_EXTENSION_VERSION}.json"
    ).write_text(json.dumps(identity_payload), encoding="utf-8")
    providers = []
    for provider in ("deepseek", "gemini", "kimi"):
        human = provider == human_provider
        failed = human or provider == failed_provider
        providers.append(
            {
                "provider_id": provider,
                "status": "COMPLETE_WITH_ERRORS" if failed else "COMPLETE",
                "indexed_count": 2,
                "captured_count": 1,
                "failed_count": 1 if failed else 0,
                "error_code": (
                    "PROVIDER_HUMAN_VERIFICATION_REQUIRED+"
                    "PROVIDER_CAPTURE_SKIPPED_AFTER_HUMAN_VERIFICATION"
                    if human
                    else "CONVERSATION_MESSAGES_NOT_FOUND"
                    if failed
                    else ""
                ),
                "safe_diagnostic": (
                    {"safe_state": "PROVIDER_HUMAN_VERIFICATION_REQUIRED"}
                    if human
                    else None
                ),
            }
        )
    payload = {
        "schema_version": "MEMORIVE_BROWSER_SESSION_SYNC_REPORT_V2",
        "source": "MEMORIVE_BROWSER_COMPANION_RUNNER_V4_DRAFT",
        "extension_id": "abcdefghijklmnopabcdefghijklmnop",
        "extension_version": extension_version or CURRENT_EXTENSION_VERSION,
        "per_provider_limit": 2,
        "sync_mode": "PREFLIGHT",
        "browser_family": "EDGE",
        "generated_at": "2026-08-29T02:30:00Z",
        "trigger": "APPROVED_PROVIDER_FRAGMENT_PREFLIGHT_V1",
        "status": "ERROR" if human_provider or failed_provider else "COMPLETE",
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
    folder.joinpath("memorive-session-library-sync-report-sample.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_preflight_receipt_is_bounded_to_two_per_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[list[str]] = []

    class _Process:
        pid = 41501

    api = companion()
    api.system_effects_enabled = True
    api.browser_companion_status = lambda: {
        "prepared": True,
        "extension_connected": True,
        "bridge_identity_verified": True,
        "preflight_eligible": True,
        "connected_browser": "EDGE",
        "extension_id": "abcdefghijklmnopabcdefghijklmnop",
    }
    api._browser_executables = lambda: [
        ("Microsoft Edge", Path("C:/Program Files/Edge/msedge.exe"), "edge://extensions")
    ]
    monkeypatch.setattr(
        runtime_module.subprocess,
        "Popen",
        lambda command, **_kwargs: launched.append(command) or _Process(),
    )

    receipt = api.run_browser_companion_sync()

    assert launched == [["C:\\Program Files\\Edge\\msedge.exe", PREFLIGHT_URL]]
    assert receipt["sync_mode"] == "PREFLIGHT"
    assert receipt["per_provider_limit"] == 2
    assert receipt["trigger_transport"] == "HTTPS_APPROVED_FRAGMENT_PREFLIGHT"


def test_pre_340_reports_are_rejected_and_future_semver_is_not_ceiling_locked(
    tmp_path: Path,
) -> None:
    api, downloads = _api(tmp_path)
    _write_report(downloads, extension_version="3.3.9")

    status = api.browser_companion_status()

    assert status["extension_version"] == CURRENT_EXTENSION_VERSION
    assert status["extension_connected"] is False
    assert status["full_sync_eligible"] is False
    assert status["bridge_identity_verified"] is False
    assert status["bridge_reload_required"] is True
    assert status["report_extension_version"] == "3.3.9"
    assert BrowserCompanionDesktop._browser_companion_semver_at_least("3.4.0", (3, 4, 0)) is True
    assert BrowserCompanionDesktop._browser_companion_semver_at_least("3.4.0-rc.1", (3, 4, 0)) is False


