from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from bridge_client import BridgeClient
from common import validate_command
from manager import Manager


SESSION_ID = "review-20260911T000000-222222222222"


def stopped_manager(tmp_path: Path) -> Manager:
    root = tmp_path / "console"
    root.mkdir()
    source = Path(__file__).parent
    for name in ("reference_bridge.py", "common.py"):
        shutil.copy2(source / name, root / name)
    manager = Manager(root)
    manager.stop.set()
    manager.thread.join(timeout=3)
    manager.session = {
        "session_id": SESSION_ID,
        "mode": "build",
        "build": {"path": str(tmp_path / "dist" / "Memorive.exe"), "label": "local-release"},
    }
    manager.epoch += 1
    manager.capabilities = ["diagnostics", "expression.trigger", "expression.cancel"]
    manager.client = None
    return manager


def expression_command(operation: str, params: dict) -> dict:
    return {
        "request_id": "expression-test-request",
        "operation": operation,
        "params": params,
        "allow_model_calls": False,
    }


def test_expression_commands_have_fixed_zero_model_contract():
    assert validate_command(expression_command("expression.trigger", {
        "event_id": "motion.blink", "duration_ms": 1800,
    }))
    assert validate_command(expression_command("expression.cancel", {}))
    with pytest.raises(ValueError, match="EXPRESSION_DURATION_INVALID"):
        validate_command(expression_command("expression.trigger", {
            "event_id": "motion.blink", "duration_ms": 499,
        }))
    with pytest.raises(ValueError, match="EXPRESSION_MODEL_PERMISSION_FORBIDDEN"):
        validate_command({**expression_command("expression.cancel", {}), "allow_model_calls": True})


def test_handshake_accepts_permission_gate_and_client_uses_light_expression_routes():
    client = BridgeClient(
        {"protocol_version": "1.0", "host": "127.0.0.1", "port": 49152, "session_id": SESSION_ID},
        "fixture-token", SESSION_ID, "A" * 64, "MEMORIVE_BUILD",
    )
    calls = []

    def request(method, route, body=None, **_):
        calls.append((method, route, body))
        if route == "/session/start":
            return {
                "session_id": SESSION_ID, "protocol_version": "1.0", "state": "READY",
                "implementation_kind": "MEMORIVE_BUILD", "build_sha256": "A" * 64,
                "data_root": "C:\\temp\\session", "automatic_execution": False,
                "automatic_execution_locked": True, "all_writes_inside_data_root": True,
                "cleanup_supported": True, "operations": ["diagnostics", "expression.trigger", "expression.cancel"],
                "console_access_required": True, "console_access_enabled": False,
            }
        return {"session_id": SESSION_ID, "schema_version": "Memorive-ExpressionConsole-v1"}

    client.request = request
    ready = client.handshake(Path("C:/temp/session"))
    assert ready["console_access_required"] is True
    assert ready["console_access_enabled"] is False
    client.expression_catalog()
    client.expression_state()
    assert [(method, route) for method, route, _ in calls[-2:]] == [
        ("GET", "/expressions/catalog"), ("GET", "/expressions/state")
    ]


class PermissionClient:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.event_calls = 0
        self.snapshot_calls = 0
        self.expression_calls = 0

    def expression_state(self):
        self.expression_calls += 1
        return {
            "session_id": SESSION_ID,
            "schema_version": "Memorive-ExpressionConsole-v1",
            "renderer_ready": True,
            "access_enabled": self.enabled,
            "active": None,
            "history": [],
            "events": [],
            "last_sequence": 0,
            "preview_only": True,
            "business_event_injected": False,
        }

    def events(self, _cursor):
        self.event_calls += 1
        return {"session_id": SESSION_ID, "events": []}

    def snapshot(self):
        self.snapshot_calls += 1
        if not self.enabled:
            raise RuntimeError("CONSOLE_ACCESS_DISABLED")
        return {
            "session_id": SESSION_ID,
            "observed_at": "2026-09-11T00:00:00Z",
            "automatic_execution": False,
            "automatic_execution_locked": True,
            "console_access_enabled": True,
            "messages": [], "reports": [], "jobs": [], "inbox": [],
        }


def test_permission_off_is_stable_waiting_state_and_never_probes_heavy_snapshot(tmp_path):
    manager = stopped_manager(tmp_path)
    client = PermissionClient(False)
    manager.client = client
    manager.state = "WAITING_FOR_ACCESS"
    manager.console_access_required = True
    manager.console_access_enabled = False
    manager._reset_connection_monitor("STABLE")
    manager._reset_native_projection_monitor("WAITING_FOR_ACCESS")
    try:
        assert manager._poll_expression_once() is True
        assert manager._poll_once() is True
        observed = manager.snapshot()
        assert observed["state"] == "WAITING_FOR_ACCESS"
        assert observed["connected_to_memorive"] is True
        assert observed["console_access_enabled"] is False
        assert observed["native_projection_monitor"]["health"] == "WAITING_FOR_ACCESS"
        assert client.event_calls == 1
        assert client.snapshot_calls == 0
    finally:
        manager.session = None
        manager.client = None
        manager.close()


def test_permission_transition_enables_business_projection_without_reconnect(tmp_path):
    manager = stopped_manager(tmp_path)
    client = PermissionClient(False)
    manager.client = client
    manager.state = "WAITING_FOR_ACCESS"
    manager.console_access_required = True
    manager.console_access_enabled = False
    manager._reset_connection_monitor("STABLE")
    manager._reset_native_projection_monitor("WAITING_FOR_ACCESS")
    try:
        manager._poll_expression_once()
        client.enabled = True
        manager._next_expression_probe_at = 0.0
        assert manager._poll_expression_once() is True
        assert manager.snapshot()["state"] == "CONNECTED"
        assert manager._poll_once() is True
        assert client.snapshot_calls == 1
        assert manager.snapshot()["native_projection_monitor"]["health"] == "CURRENT"
    finally:
        manager.session = None
        manager.client = None
        manager.close()


def test_expected_snapshot_403_returns_to_waiting_without_disconnect(tmp_path):
    manager = stopped_manager(tmp_path)
    client = PermissionClient(False)
    manager.client = client
    manager.state = "CONNECTED"
    manager.console_access_required = True
    manager.console_access_enabled = True
    manager._reset_connection_monitor("STABLE")
    manager._reset_native_projection_monitor("CURRENT")
    try:
        assert manager._poll_once() is True
        observed = manager.snapshot()
        assert observed["state"] == "WAITING_FOR_ACCESS"
        assert observed["error_code"] is None
        assert observed["connection_monitor"]["health"] == "STABLE"
        assert observed["native_projection_monitor"]["health"] == "WAITING_FOR_ACCESS"
    finally:
        manager.session = None
        manager.client = None
        manager.close()


def test_expression_asset_route_is_exact_allowlist_from_connected_build(tmp_path):
    manager = stopped_manager(tmp_path)
    asset_root = tmp_path / "dist" / "_internal" / "assets" / "illustrations"
    asset_root.mkdir(parents=True)
    (tmp_path / "dist" / "Memorive.exe").write_bytes(b"fixture")
    (asset_root / "wink.svg").write_text("<svg/>", encoding="utf-8")
    manager.expression_catalog = {
        "events": [{"event_id": "asset.wink", "asset": "wink", "group": "static_asset"}]
    }
    try:
        data, mime = manager.expression_asset("wink")
        assert data == b"<svg/>" and mime == "image/svg+xml"
        with pytest.raises(ValueError, match="EXPRESSION_ASSET_NOT_ALLOWED"):
            manager.expression_asset("manifest")
        with pytest.raises(ValueError, match="EXPRESSION_ASSET_NOT_ALLOWED"):
            manager.expression_asset("../wink")
    finally:
        manager.session = None
        manager.close()


def test_ui_mirrors_native_samples_and_declares_real_animation_groups():
    source = (Path(__file__).parent / "app.js").read_text(encoding="utf-8")
    start = source.index("function nativePreviewStyle")
    end = source.index("\nfunction ", start + 10)
    function = source[start:end]
    script = f"""
{function}
process.stdout.write(JSON.stringify(nativePreviewStyle({{transform:['working',4,-2,10,1.2,.7]}},180)));
"""
    result = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    style = json.loads(result.stdout)
    assert style == {
        "asset": "shovel",
        "transform": "translate(8px, -4px) rotate(10deg) scale(1.2)",
        "opacity": "0.7",
    }
    html = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
    assert all(group in html for group in ("static_asset", "native_pose", "native_animation", "web_animation"))
    assert "expression-cancel" in html
