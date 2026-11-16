from __future__ import annotations

from pathlib import Path
import sys

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
for candidate in (ATTEMPT_ROOT / "source", ATTEMPT_ROOT / "product" / "desktop"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import runtime as runtime_module  # noqa: E402
from runtime import ProductApi  # noqa: E402
from browser_companion_support import companion  # noqa: E402


TRIGGER_URL = (
    "https://gemini.google.com/app"
    "?memorive_bridge_sync=v1#memorive-browser-session-preflight-2-v5"
)


def test_runtime_opens_approved_https_fragment_in_connected_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[list[str]] = []

    class _Process:
        pid = 41041

    def _popen(command: list[str], **_kwargs: object) -> _Process:
        launched.append(command)
        return _Process()

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
    monkeypatch.setattr(runtime_module.subprocess, "Popen", _popen)

    receipt = api.run_browser_companion_sync()

    assert launched == [["C:\\Program Files\\Edge\\msedge.exe", TRIGGER_URL]]
    assert receipt["trigger_transport"] == "HTTPS_APPROVED_FRAGMENT_PREFLIGHT"
    assert receipt["sync_mode"] == "PREFLIGHT"
    assert receipt["per_provider_limit"] == 2
    assert receipt["providers"] == ["deepseek", "gemini", "kimi"]
    assert receipt["status"] == "SYNC_TRIGGERED"


