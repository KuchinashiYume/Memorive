from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
for candidate in (ATTEMPT_ROOT / "source", ATTEMPT_ROOT / "product" / "desktop"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive.desktop.api import ProductApi  # noqa: E402
from browser_companion_support import companion  # noqa: E402


RELOAD_URL = (
    "https://gemini.google.com/app"
    "?memorive_bridge_reload=v1#memorive-browser-companion-reload-v1"
)
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


def _prepared_api(tmp_path: Path) -> tuple[ProductApi, Path]:
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
                "browser_reload_required": True,
                "connected_browser": "EDGE",
                "extension_id": "abcdefghijklmnopabcdefghijklmnop",
                "import_verified": False,
            }
        ),
        encoding="utf-8",
    )
    return api, downloads


def _write_identity_receipt(downloads: Path, extension_version: str) -> Path:
    payload = {
        "schema_version": "MEMORIVE_BROWSER_COMPANION_IDENTITY_V1",
        # Identity receipts carry these since extension 3.6.0 / 3.10.1.
        "browser_family": "EDGE",
        "site_profile_sha256": __import__("memorive.sessions.companion.site_profiles", fromlist=["x"]).profile_hash(
            __import__("memorive.sessions.companion.site_profiles", fromlist=["x"]).default_sites()),
        "extension_id": "abcdefghijklmnopabcdefghijklmnop",
        "extension_version": extension_version,
        "observed_at": "2026-08-29T03:29:59Z",
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
    path = folder.joinpath(
        f"memorive-browser-companion-identity-{extension_version}.json"
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_preflight_report(downloads: Path, extension_version: str) -> None:
    payload = {
        "schema_version": "MEMORIVE_BROWSER_SESSION_SYNC_REPORT_V2",
        "source": "MEMORIVE_BROWSER_COMPANION_RUNNER_V4_DRAFT",
        "extension_id": "abcdefghijklmnopabcdefghijklmnop",
        "extension_version": extension_version,
        "per_provider_limit": 2,
        "sync_mode": "PREFLIGHT",
        "browser_family": "EDGE",
        "generated_at": "2026-08-29T03:30:00Z",
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
                "captured_count": 1,
                "failed_count": 0,
                "error_code": "",
                "safe_diagnostic": None,
            }
            for provider in ("deepseek", "gemini", "kimi")
        ],
        "cookie_value_reads": 0,
        "browser_storage_value_reads": 0,
        "hidden_provider_api_calls": 0,
        "remote_session_mutations": 0,
    }
    folder = downloads / "Memorive会话桥接"
    folder.mkdir(exist_ok=True)
    folder.joinpath("memorive-session-library-sync-report-reload-gate.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_reload_action_opens_only_reload_fragment_and_records_pending_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, _downloads = _prepared_api(tmp_path)
    api.system_effects_enabled = True
    api._browser_executables = lambda: [
        ("Microsoft Edge", Path("C:/Program Files/Edge/msedge.exe"), "edge://extensions")
    ]
    launched: list[list[str]] = []

    class _Process:
        pid = 41901

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **_kwargs: launched.append(command) or _Process(),
    )

    receipt = api.reload_browser_companion()

    assert launched == [["C:\\Program Files\\Edge\\msedge.exe", RELOAD_URL]]
    assert receipt["trigger_transport"] == "HTTPS_APPROVED_FRAGMENT_RELOAD"
    assert receipt["status"] == "BRIDGE_RELOAD_TRIGGERED"
    state = json.loads(api._browser_companion_state_path().read_text(encoding="utf-8"))
    assert state["browser_reload_required"] is True
    assert state["bridge_reload_requested_at_unix"] > 0
    assert "sync_mode" not in receipt


def test_manifest_report_identity_mismatch_keeps_preflight_and_full_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, downloads = _prepared_api(tmp_path)
    _write_preflight_report(downloads, "3.3.9")

    status = api.browser_companion_status()

    assert status["extension_version"] == CURRENT_EXTENSION_VERSION
    assert status["report_extension_version"] == "3.3.9"
    assert status["bridge_identity_verified"] is False
    assert status["bridge_reload_required"] is True
    assert status["extension_connected"] is False
    assert status["preflight_eligible"] is False
    assert status["full_sync_eligible"] is False

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("identity mismatch must not launch sync"),
    )
    receipt = api.run_browser_companion_sync()
    assert receipt["status"] == "BRIDGE_IDENTITY_REQUIRED"


def test_current_identity_supersedes_stale_sync_report_for_preflight_only(
    tmp_path: Path,
) -> None:
    api, downloads = _prepared_api(tmp_path)
    _write_identity_receipt(downloads, CURRENT_EXTENSION_VERSION)
    _write_preflight_report(downloads, "3.3.9")

    status = api.browser_companion_status()

    assert status["bridge_identity_verified"] is True
    assert status["bridge_reload_required"] is False
    assert status["preflight_eligible"] is True
    assert status["report_extension_version"] == "3.3.9"
    assert status["latest_sync_mode"] is None
    assert status["full_sync_eligible"] is False


def test_current_identity_alone_opens_v5_preflight_without_running_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, downloads = _prepared_api(tmp_path)
    _write_identity_receipt(downloads, CURRENT_EXTENSION_VERSION)
    api.system_effects_enabled = True
    api._browser_executables = lambda: [
        ("Microsoft Edge", Path("C:/Program Files/Edge/msedge.exe"), "edge://extensions")
    ]
    launched: list[list[str]] = []

    class _Process:
        pid = 41910

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **_kwargs: launched.append(command) or _Process(),
    )

    receipt = api.run_browser_companion_sync()

    assert launched == [["C:\\Program Files\\Edge\\msedge.exe", PREFLIGHT_URL]]
    assert receipt["status"] == "SYNC_TRIGGERED"
    assert receipt["sync_mode"] == "PREFLIGHT"
    assert receipt["per_provider_limit"] == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "MEMORIVE_BROWSER_COMPANION_IDENTITY_V0"),
        ("extension_id", "bcdefghijklmnopabcdefghijklmnopa"),
        ("extension_version", "3.3.9"),
        ("trigger_protocol", "HTTPS_APPROVED_FRAGMENT_V2"),
        ("preflight_limit", 6),
        ("full_limit", 49),
        ("incremental_limit", 9),
        ("providers", ["deepseek", "kimi", "gemini"]),
        ("cookie_value_reads", 1),
    ],
)
def test_identity_receipt_contract_fails_closed(
    tmp_path: Path, field: str, value: object
) -> None:
    api, downloads = _prepared_api(tmp_path)
    path = _write_identity_receipt(downloads, CURRENT_EXTENSION_VERSION)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    status = api.browser_companion_status()

    assert status["bridge_identity_verified"] is False
    assert status["preflight_eligible"] is False
    assert status["full_sync_eligible"] is False


def test_install_update_uses_extension_self_reload_not_browser_restart(
    tmp_path: Path,
) -> None:
    api, _downloads = _prepared_api(tmp_path)
    api.system_effects_enabled = True
    launched: list[str | None] = []
    api._launch_browser_companion_reload = lambda preferred=None: (
        launched.append(preferred)
        or {
            "schema_version": "DesktopBrowserCompanionReloadLaunchReceipt-v1",
            "selected_browser": "Microsoft Edge",
            "trigger_transport": "HTTPS_APPROVED_FRAGMENT_RELOAD",
            "trigger_url": RELOAD_URL,
            "process_id": 41902,
            "status": "BRIDGE_RELOAD_TRIGGERED",
        }
    )

    receipt = api.install_browser_companion()

    assert launched == ["EDGE"]
    assert receipt["bridge_reload_requested"] is True
    assert receipt["status"] == "BRIDGE_RELOAD_TRIGGERED"
    assert "restart_receipt" not in receipt
    assert "browser_restart_requested" not in receipt


def test_current_identity_recovers_extension_id_before_automatic_reload(
    tmp_path: Path,
) -> None:
    api, downloads = _prepared_api(tmp_path)
    state_path = api._browser_companion_state_path()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["extension_id"] = None
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _write_identity_receipt(downloads, CURRENT_EXTENSION_VERSION)
    api.system_effects_enabled = True
    launched: list[str | None] = []
    api._launch_browser_companion_reload = lambda preferred=None: (
        launched.append(preferred)
        or {
            "schema_version": "DesktopBrowserCompanionReloadLaunchReceipt-v1",
            "selected_browser": "Microsoft Edge",
            "trigger_transport": "HTTPS_APPROVED_FRAGMENT_RELOAD",
            "trigger_url": RELOAD_URL,
            "process_id": 41904,
            "status": "BRIDGE_RELOAD_TRIGGERED",
        }
    )

    receipt = api.install_browser_companion()
    updated_state = json.loads(state_path.read_text(encoding="utf-8"))

    assert launched == ["EDGE"]
    assert receipt["manual_load_required"] is False
    assert receipt["bridge_reload_requested"] is True
    assert receipt["extension_id"] == "abcdefghijklmnopabcdefghijklmnop"
    assert updated_state["extension_id"] == "abcdefghijklmnopabcdefghijklmnop"


