from pathlib import Path
import json
import re

import pytest

from build_drop import dropped_build_path
from manager import build_info

ROOT = Path(__file__).resolve().parents[1]


def drop(path: Path):
    return {"dataTransfer": {"files": [{"pywebviewFullPath": str(path)}]}}


def test_public_identity_and_version_are_memorive_only():
    files = [
        ROOT / "source" / "index.html",
        ROOT / "source" / "app.css",
        ROOT / "console.spec",
        ROOT / "console_version_info.txt",
        ROOT / "installer.iss",
        ROOT / "README.txt",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "Memorive" in text, path.name
        assert "Memorive" in text, path.name
    assert '#define AppVersion "1.02"' in (ROOT / "installer.iss").read_text(encoding="utf-8")
    assert "Memorive-Test-Console.exe" in (ROOT / "installer.iss").read_text(encoding="utf-8")
    assert "Memorive-Test-Console-Setup-{#AppVersion}-x64" in (ROOT / "installer.iss").read_text(encoding="utf-8")


def test_sidebar_event_lab_and_logo_are_present():
    html = (ROOT / "source" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "source" / "app.css").read_text(encoding="utf-8")
    js = (ROOT / "source" / "app.js").read_text(encoding="utf-8")
    assert 'class="sidebar"' in html
    assert 'data-view="events"' in html
    assert 'data-view-panel="events"' in html
    assert 'id="event-catalog"' in html
    assert 'id="event-run"' in html
    assert "/memorive_test_console_icon.png" in html
    assert ".event-workspace" in css
    assert "renderEventLab" in js
    assert (ROOT / "assets" / "memorive_test_console_icon.png").is_file()
    assert (ROOT / "assets" / "memorive_test_console.ico").is_file()


def test_javascript_simple_id_selectors_exist_in_html():
    html = (ROOT / "source" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "source" / "app.js").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))
    selected = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", js))
    assert selected - ids == set()


def test_event_lab_has_core_zero_model_scenarios():
    js = (ROOT / "source" / "app.js").read_text(encoding="utf-8")
    for event_id in [
        "message.info", "message.error", "task.import", "task.complete",
        "task.fail", "task.review", "task.cancel", "task.stall",
        "content.daily", "content.weekly", "content.monthly",
        "content.discovery", "content.imported", "scenario.research-qa",
        "scenario.workflow-success", "scenario.workflow-failure",
    ]:
        assert f"id:'{event_id}'" in js
    assert "allowModel=false" in js


def test_build_drop_accepts_memorive_main_program(tmp_path):
    executable = tmp_path / "Memorive.exe"
    executable.write_bytes(b"MZ")
    assert Path(dropped_build_path(drop(executable))) == executable.resolve()


def test_build_drop_rejects_unrelated_executable(tmp_path):
    executable = tmp_path / "other.exe"
    executable.write_bytes(b"MZ")
    with pytest.raises(ValueError, match="DROP_BUILD_EXE_REQUIRED"):
        dropped_build_path(drop(executable))


def test_memorive_build_sidecar_is_discovered(tmp_path):
    executable = tmp_path / "Memorive.exe"
    executable.write_bytes(b"MZ")
    (tmp_path / "memorive-console-capabilities.json").write_text(json.dumps({
        "schema_version": "Memorive-ConsoleBuildCapabilities-v1",
        "protocol_version": "1.0",
        "launch_transport": "ENV_V1",
        "session_mode": "EPHEMERAL_ONLY",
        "build_id": "build-next",
    }), encoding="utf-8")
    (tmp_path / "release_identity_binding.json").write_text(json.dumps({
        "schema_version": "DesktopReleaseIdentityBinding-v1",
        "release_version": "1.02",
        "display_version": "v1.02",
        "channel": "TEST_ONLY",
        "main_executable": "Memorive.exe",
        "release_authorized": False,
        "acceptance_verdict": "NOT_ASSESSED",
    }), encoding="utf-8")
    result = build_info(executable)
    assert result["bridge_declared"] is True
    assert result["status"] == "READY_TO_CONNECT"
