from __future__ import annotations

import http.client
import json
from pathlib import Path
import shutil
import subprocess

from manager import Manager


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
        "session_id": "review-20260910T000000-000000000000",
        "mode": "build",
    }
    manager.state = "CONNECTED"
    manager.epoch += 1
    manager._reset_connection_monitor("STABLE")
    manager._reset_native_projection_monitor("CURRENT")
    return manager


class ProjectionFailureSessionAliveClient:
    def __init__(self, failing_snapshots: int):
        self.failing_snapshots = failing_snapshots
        self.snapshot_calls = 0
        self.event_calls = 0

    def snapshot(self):
        self.snapshot_calls += 1
        if self.snapshot_calls <= self.failing_snapshots:
            raise http.client.RemoteDisconnected("remote end closed connection")
        return {
            "session_id": "review-20260910T000000-000000000000",
            "observed_at": "2026-09-10T00:00:10Z",
            "automatic_execution": False,
            "automatic_execution_locked": True,
            "messages": [],
        }

    def events(self, cursor):
        self.event_calls += 1
        return {
            "session_id": "review-20260910T000000-000000000000",
            "events": [],
        }


class BothChannelsFailClient(ProjectionFailureSessionAliveClient):
    def events(self, cursor):
        self.event_calls += 1
        raise ConnectionResetError("bridge unavailable")


def test_snapshot_failure_does_not_claim_session_disconnect_when_event_probe_is_live(tmp_path):
    manager = stopped_manager(tmp_path)
    manager.client = ProjectionFailureSessionAliveClient(failing_snapshots=3)
    try:
        for _ in range(3):
            assert manager._poll_once() is True
            observed = manager.snapshot()
            assert observed["state"] == "CONNECTED"
            assert observed["connection_monitor"]["health"] == "STABLE"
            assert observed["native_projection_monitor"]["health"] == "STALE"

        stale = manager.snapshot()
        assert stale["native_projection_monitor"]["consecutive_failures"] == 3
        assert stale["native_projection_monitor"]["last_failure_error"] == "RemoteDisconnected"
        assert manager.client.event_calls == 3

        assert manager._poll_once() is True
        recovered = manager.snapshot()
        assert recovered["state"] == "CONNECTED"
        assert recovered["native_projection_monitor"]["health"] == "CURRENT"
        assert recovered["native_projection_monitor"]["consecutive_failures"] == 0
        assert recovered["native_projection_monitor"]["failure_count"] == 3
        assert recovered["native"]["observed_at"] == "2026-09-10T00:00:10Z"
    finally:
        manager.session = None
        manager.client = None
        manager.close()


def test_session_disconnect_still_requires_failed_independent_session_probe(tmp_path):
    manager = stopped_manager(tmp_path)
    manager.client = BothChannelsFailClient(failing_snapshots=99)
    try:
        for _ in range(3):
            assert manager._poll_once() is False
        observed = manager.snapshot()
        assert observed["state"] == "DISCONNECTED"
        assert observed["connection_monitor"]["health"] == "DISCONNECTED"
        assert observed["connection_monitor"]["consecutive_failures"] == 3
        assert observed["native_projection_monitor"]["health"] == "STALE"
        assert manager.client.event_calls == 3
    finally:
        manager.session = None
        manager.client = None
        manager.close()


class ProductFailureClient:
    def __init__(self):
        self.command_calls = 0

    def command(self, value):
        self.command_calls += 1
        return {
            "session_id": "review-20260910T000000-000000000000",
            "request_id": value["request_id"],
            "command_id": "memorive-command-00000000000000000000000000000000",
            "operation": value["operation"],
            "state": "FAILED",
            "error_code": "PRODUCT_OPERATION_FAILED",
        }


def test_product_operation_failure_remains_explicit_and_is_never_retried(tmp_path):
    manager = stopped_manager(tmp_path)
    client = ProductFailureClient()
    manager.client = client
    value = {
        "request_id": "repair-message-imported",
        "operation": "message.test",
        "params": {"kind": "imported", "title": "test", "body": "test"},
        "allow_model_calls": False,
    }
    manager.commands[value["request_id"]] = {
        "command_id": "console-command-00000000000000000000000000000000",
        "request_id": value["request_id"],
        "operation": value["operation"],
        "state": "QUEUED",
    }
    try:
        manager._command(value, manager.epoch, client)
        record = manager.commands[value["request_id"]]
        assert record["state"] == "FAILED"
        assert record["error_code"] == "PRODUCT_OPERATION_FAILED"
        assert record["result"]["error_code"] == "PRODUCT_OPERATION_FAILED"
        assert client.command_calls == 1
    finally:
        manager.session = None
        manager.client = None
        manager.close()


def test_ui_marks_retained_suite_result_as_historical_until_session_is_live():
    source = (Path(__file__).parent / "app.js").read_text(encoding="utf-8")
    start = source.index("function suiteSessionView")
    end = source.index("\nfunction selectedPreset", start)
    function = source[start:end]
    helper = (Path(__file__).parent / "i18n.js").read_text(encoding="utf-8").split("function tr(", 1)[1].split("function localizeInitialPage", 1)[0]
    translator = "function tr(" + helper
    script = f"""
const window={{CONSOLE_I18N:{{messages:{{}}}}}};
{translator}
{function}
const suite={{state:'COMPLETED',verdict:'PASS',session_id:'session-a'}};
const values=[
  suiteSessionView(suite,{{state:'CONNECTED',session:{{session_id:'session-a'}},connection_monitor:{{health:'STABLE'}}}}),
  suiteSessionView(suite,{{state:'DISCONNECTED',session:{{session_id:'session-a'}},connection_monitor:{{health:'DISCONNECTED'}}}}),
  suiteSessionView(suite,{{state:'CONNECTED',session:{{session_id:'session-b'}},connection_monitor:{{health:'STABLE'}}}}),
  suiteSessionView(suite,{{state:'CONNECTED',session:{{session_id:'session-a'}},connection_monitor:{{health:'DEGRADED'}}}})
];
process.stdout.write(JSON.stringify(values));
"""
    result = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    live, disconnected, different, degraded = json.loads(result.stdout)
    assert live == {"live": True, "suffix": ""}
    assert disconnected == {"live": False, "suffix": " · 历史结果（会话未确认）"}
    assert different == {"live": False, "suffix": " · 历史结果（其他会话）"}
    assert degraded == {"live": False, "suffix": " · 结果保留（会话待确认）"}
