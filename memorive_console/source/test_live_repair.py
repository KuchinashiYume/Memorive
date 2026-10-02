from __future__ import annotations

from pathlib import Path
import hashlib
import io
import json
import shutil
import subprocess
import time
import urllib.error
import uuid

import pytest

from manager import Manager
from bridge_client import BridgeClient


def new_manager(tmp_path: Path) -> Manager:
    root = tmp_path / "console"
    root.mkdir()
    source = Path(__file__).parent
    for name in ("reference_bridge.py", "common.py"):
        shutil.copy2(source / name, root / name)
    return Manager(root)


def command(operation: str, params: dict[str, object]) -> dict[str, object]:
    return {
        "request_id": "repair-" + uuid.uuid4().hex,
        "operation": operation,
        "params": params,
        "allow_model_calls": False,
    }


def wait_for_command(manager: Manager, command_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        row = next(
            item
            for item in manager.snapshot()["commands"]
            if item["command_id"] == command_id
        )
        if row["state"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            assert row["state"] == "SUCCEEDED", row
            return row
        time.sleep(0.05)
    pytest.fail("command did not settle")


def test_external_import_is_staged_inside_owned_allowlist_and_cleaned(tmp_path):
    manager = new_manager(tmp_path)
    source = tmp_path / "author-selected" / "研究资料.txt"
    source.parent.mkdir()
    source.write_text("external fixture", encoding="utf-8")
    original = source.read_bytes()
    try:
        session = manager.start("reference")["session"]
        captured = {}
        original_command = manager.client.command

        def capture_staged_command(value):
            captured["paths"] = list(value["params"]["paths"])
            return original_command(value)

        manager.client.command = capture_staged_command
        queued = manager.submit(command("inbox.import", {"paths": [str(source)]}))
        row = wait_for_command(manager, queued["command_id"])

        staging = row["import_staging"]
        assert staging["status"] == "STAGED"
        assert staging["source_paths_recorded"] is False
        assert staging["cleanup"] == "SESSION_CLOSE"
        assert len(staging["files"]) == 1
        staged = Path(session["data_root"]) / staging["files"][0]["staged_relative_path"]
        assert staged.is_file()
        assert staged.read_bytes() == original
        assert staged.is_relative_to(
            Path(session["data_root"]) / "state" / "profile" / "inbox_source"
        )
        assert staging["files"][0]["sha256"] == hashlib.sha256(original).hexdigest()
        assert captured["paths"] == [str(staged)]
        assert captured["paths"] != [str(source)]
        assert source.read_bytes() == original

        data_root = Path(session["data_root"])
        receipt = manager.end()
        assert receipt["status"] == "CLEANED"
        assert not data_root.exists()
        assert source.read_bytes() == original
    finally:
        manager.close()


def test_same_named_files_get_distinct_staged_paths(tmp_path):
    manager = new_manager(tmp_path)
    first = tmp_path / "a" / "report.txt"
    second = tmp_path / "b" / "report.txt"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    try:
        session = manager.start("reference")["session"]
        queued = manager.submit(
            command("inbox.import", {"paths": [str(first), str(second)]})
        )
        row = wait_for_command(manager, queued["command_id"])
        files = row["import_staging"]["files"]
        staged = [Path(session["data_root"]) / item["staged_relative_path"] for item in files]
        assert [path.name for path in staged] == ["report.txt", "report.txt"]
        assert staged[0] != staged[1]
        assert [path.read_text(encoding="utf-8") for path in staged] == ["first", "second"]
    finally:
        manager.close()


def test_import_staging_rejects_directory_and_missing_source(tmp_path):
    manager = new_manager(tmp_path)
    folder = tmp_path / "folder"
    folder.mkdir()
    try:
        manager.start("reference")
        with pytest.raises(ValueError, match="IMPORT_STAGE_FILE_REQUIRED"):
            manager.submit(command("inbox.import", {"paths": [str(folder)]}))
        with pytest.raises(ValueError, match="IMPORT_STAGE_SOURCE_NOT_FOUND"):
            manager.submit(
                command("inbox.import", {"paths": [str(tmp_path / "missing.txt")]})
            )
    finally:
        manager.close()


def test_import_staging_rejects_symbolic_link_when_supported(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("target", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("symbolic links are unavailable on this Windows host")
    manager = new_manager(tmp_path)
    try:
        manager.start("reference")
        with pytest.raises(ValueError, match="IMPORT_STAGE_REPARSE_REJECTED"):
            manager.submit(command("inbox.import", {"paths": [str(link)]}))
    finally:
        manager.close()


class SequenceClient:
    def __init__(self, steps):
        self.steps = list(steps)

    def snapshot(self):
        return {
            "automatic_execution": False,
            "automatic_execution_locked": True,
            "runtime_status": "READY",
        }

    def events(self, cursor):
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return {"events": [], "next_cursor": cursor}


def test_connection_stabilizer_debounces_flap_and_requires_stable_recovery(tmp_path):
    manager = new_manager(tmp_path)
    manager.stop.set()
    manager.thread.join(timeout=3)
    try:
        manager.session = {"mode": "build"}
        manager.state = "CONNECTED"
        manager.epoch += 1
        manager._reset_connection_monitor("STABLE")
        manager.client = SequenceClient(
            [
                ConnectionResetError("one"),
                object(),
                ConnectionResetError("two"),
                object(),
                ConnectionResetError("down-1"),
                ConnectionResetError("down-2"),
                ConnectionResetError("down-3"),
                object(),
                object(),
            ]
        )

        manager._poll_once()
        assert manager.snapshot()["state"] == "CONNECTED"
        assert manager.snapshot()["connection_monitor"]["health"] == "DEGRADED"
        manager._poll_once()
        assert manager.snapshot()["connection_monitor"]["health"] == "DEGRADED"
        manager._poll_once()
        manager._poll_once()
        assert manager.snapshot()["state"] == "CONNECTED"
        assert manager.snapshot()["connection_monitor"]["health"] == "DEGRADED"

        manager._poll_once()
        manager._poll_once()
        assert manager.snapshot()["state"] == "CONNECTED"
        manager._poll_once()
        disconnected = manager.snapshot()
        assert disconnected["state"] == "DISCONNECTED"
        assert disconnected["connection_monitor"]["health"] == "DISCONNECTED"
        assert disconnected["connection_monitor"]["consecutive_failures"] == 3

        manager._poll_once()
        recovering = manager.snapshot()
        assert recovering["state"] == "DISCONNECTED"
        assert recovering["connection_monitor"]["health"] == "RECOVERING"
        manager._poll_once()
        recovered = manager.snapshot()
        assert recovered["state"] == "CONNECTED"
        assert recovered["connection_monitor"]["health"] == "STABLE"
        assert recovered["connection_monitor"]["consecutive_successes"] >= 2
        assert recovered["error_code"] is None
    finally:
        manager.session = None
        manager.client = None
        manager.close()


def test_connection_ui_keeps_degraded_and_recovering_states_explicit():
    source = (Path(__file__).parent / "app.js").read_text(encoding="utf-8")
    start = source.index("function connectionView")
    end = source.index("\nfunction syncCapabilities", start)
    function = source[start:end]
    helper = (Path(__file__).parent / "i18n.js").read_text(encoding="utf-8").split("function tr(", 1)[1].split("function localizeInitialPage", 1)[0]
    translator = "function tr(" + helper
    script = f"""
const window={{CONSOLE_I18N:{{messages:{{}}}}}};
{translator}
const names={{CONNECTED:'connected',REFERENCE_ONLY:'reference',DISCONNECTED:'disconnected',CLEANUP_FAILED:'cleanup'}};
{function}
const info={{diagnosticsOnly:false,supported:['diagnostics'],missing:[]}};
const values=[
  connectionView({{state:'CONNECTED',connection_monitor:{{health:'DEGRADED',consecutive_failures:1,disconnect_failure_threshold:3}}}},info),
  connectionView({{state:'DISCONNECTED',connection_monitor:{{health:'RECOVERING',consecutive_successes:1,recovery_success_threshold:2}}}},info),
  connectionView({{state:'DISCONNECTED',connection_monitor:{{health:'DISCONNECTED'}}}},info),
  connectionView({{state:'CLEANUP_FAILED',connection_monitor:{{health:'DEGRADED'}}}},info)
];
process.stdout.write(JSON.stringify(values));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    degraded, recovering, disconnected, cleanup_failed = json.loads(result.stdout)
    assert degraded == {"text": "连接待确认", "className": "badge degraded"}
    assert recovering["text"].startswith("正在恢复连接")
    assert recovering["className"] == "badge degraded"
    assert disconnected == {"text": "disconnected", "className": "badge failed"}
    assert cleanup_failed == {"text": "cleanup", "className": "badge failed"}


class DelayedRuntimeClient:
    def __init__(self):
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return {
            "automatic_execution": False,
            "automatic_execution_locked": True,
            "product_runtime_state": "INITIALIZING" if self.calls == 1 else "READY",
        }


class TransientRuntimeClient(DelayedRuntimeClient):
    def snapshot(self):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionResetError("native bridge warm-up")
        return {
            "automatic_execution": False,
            "automatic_execution_locked": True,
            "product_runtime_state": "READY",
        }


def test_start_waits_for_product_runtime_before_enabling_commands(tmp_path):
    manager = new_manager(tmp_path)
    manager.stop.set()
    manager.thread.join(timeout=3)
    try:
        client = DelayedRuntimeClient()
        value = manager._await_product_runtime(
            client, "build", time.monotonic() + 2
        )
        assert client.calls == 2
        assert value["product_runtime_state"] == "READY"
    finally:
        manager.close()


def test_start_tolerates_transient_local_bridge_reset_while_runtime_warms(tmp_path):
    manager = new_manager(tmp_path)
    manager.stop.set()
    manager.thread.join(timeout=3)
    try:
        client = TransientRuntimeClient()
        value = manager._await_product_runtime(
            client, "build", time.monotonic() + 2
        )
        assert client.calls == 2
        assert value["product_runtime_state"] == "READY"
    finally:
        manager.close()


class ErrorOpener:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    def open(self, request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            self.status,
            "conflict",
            {},
            io.BytesIO(json.dumps(self.payload).encode("utf-8")),
        )


def test_bridge_preserves_valid_product_error_code_from_http_response():
    session_id = "review-20260907T000000-000000000000"
    client = BridgeClient(
        {
            "protocol_version": "1.0",
            "host": "127.0.0.1",
            "port": 1,
            "session_id": session_id,
        },
        "token",
        session_id,
        "build-sha",
        "MEMORIVE_BUILD",
    )
    client.opener = ErrorOpener(
        409,
        {
            "session_id": session_id,
            "error_code": "PRODUCT_RUNTIME_INITIALIZING",
        },
    )
    with pytest.raises(RuntimeError, match="^PRODUCT_RUNTIME_INITIALIZING$"):
        client.request("GET", "/snapshot")
