from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from bridge_client import BridgeClient
from manager import Manager


SESSION_ID = "review-20260910T000000-111111111111"


def stopped_manager(tmp_path: Path) -> Manager:
    root = tmp_path / "console"
    root.mkdir()
    source = Path(__file__).parent
    for name in ("reference_bridge.py", "common.py"):
        shutil.copy2(source / name, root / name)
    manager = Manager(root)
    manager.stop.set()
    manager.thread.join(timeout=3)
    manager.session = {"session_id": SESSION_ID, "mode": "build"}
    manager.state = "CONNECTED"
    manager.epoch += 1
    manager._reset_connection_monitor("STABLE")
    manager._reset_native_projection_monitor("CURRENT")
    return manager


class AliveOwner:
    def poll(self):
        return None


class OrderedProjectionTimeoutClient:
    def __init__(self):
        self.calls = []

    def events(self, cursor):
        self.calls.append("events")
        return {"session_id": SESSION_ID, "events": []}

    def snapshot(self):
        self.calls.append("snapshot")
        raise TimeoutError("shared bridge lock remained busy")


def test_events_probe_precedes_projection_and_projection_timeout_starts_backoff(tmp_path):
    manager = stopped_manager(tmp_path)
    client = OrderedProjectionTimeoutClient()
    manager.client = client
    manager.owner = AliveOwner()
    try:
        assert manager._poll_once() is True
        assert client.calls == ["events", "snapshot"]
        assert manager.snapshot()["native_projection_monitor"]["health"] == "STALE"

        # A client-side timeout does not prove that the server-side handler has
        # released local-release's shared route lock.  The next tick must not enqueue
        # another request behind that unknown handler.
        assert manager._poll_once() is True
        assert client.calls == ["events", "snapshot"]
    finally:
        manager.session = None
        manager.client = None
        manager.owner = None
        manager.close()


class FailedProbeClient:
    def events(self, cursor):
        raise TimeoutError("bridge route timed out")

    def snapshot(self):
        raise AssertionError("projection must not run after liveness failure")


def test_route_timeouts_do_not_claim_owned_process_exit(tmp_path):
    manager = stopped_manager(tmp_path)
    manager.client = FailedProbeClient()
    manager.owner = AliveOwner()
    try:
        for _ in range(3):
            manager._next_bridge_probe_at = 0.0
            assert manager._poll_once() is False
        observed = manager.snapshot()
        assert observed["state"] == "CONNECTED"
        assert observed["connection_monitor"]["health"] == "DEGRADED"
        assert observed["connection_monitor"]["consecutive_failures"] == 3
        assert observed["connection_monitor"]["owned_process_alive"] is True
    finally:
        manager.session = None
        manager.client = None
        manager.owner = None
        manager.close()


class Response:
    def __init__(self, value):
        self.raw = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit):
        return self.raw


class RecordingOpener:
    def __init__(self):
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, timeout))
        return Response(
            {
                "session_id": SESSION_ID,
                "state": "QUIESCED",
                "all_work_stopped": True,
            }
        )


def test_close_gate_rejects_new_reads_and_gives_native_quiesce_a_bounded_timeout():
    client = BridgeClient(
        {
            "protocol_version": "1.0",
            "host": "127.0.0.1",
            "port": 49152,
            "session_id": SESSION_ID,
        },
        "fixture-token",
        SESSION_ID,
        "A" * 64,
        "MEMORIVE_BUILD",
    )
    opener = RecordingOpener()
    client.opener = opener

    client.begin_close()
    with pytest.raises(RuntimeError, match="BRIDGE_CLIENT_CLOSING"):
        client.events(0)
    assert opener.calls == []

    assert client.end()["state"] == "QUIESCED"
    assert len(opener.calls) == 1
    assert opener.calls[0][0].endswith("/session/end")
    assert opener.calls[0][1] == 15


class SuccessfulProbeClient:
    def __init__(self):
        self.calls = []

    def events(self, cursor):
        self.calls.append("events")
        return {"session_id": SESSION_ID, "events": []}

    def snapshot(self):
        self.calls.append("snapshot")
        return {
            "session_id": SESSION_ID,
            "observed_at": "2026-09-10T00:00:30Z",
            "automatic_execution": False,
            "automatic_execution_locked": True,
        }


@pytest.mark.parametrize("busy_kind", ["suite", "command"])
def test_native_projection_is_deferred_while_business_work_is_active(tmp_path, busy_kind):
    manager = stopped_manager(tmp_path)
    client = SuccessfulProbeClient()
    manager.client = client
    manager.owner = AliveOwner()
    manager._next_snapshot_probe_at = 0.0
    if busy_kind == "suite":
        manager.active_suite_id = "suite-running"
    else:
        manager.commands["request-running"] = {"state": "RUNNING"}
    try:
        assert manager._poll_once() is True
        assert client.calls == ["events"]
        observed = manager.snapshot()["native_projection_monitor"]
        assert observed["probe_deferred"] is True
        assert observed["probe_deferred_reason"] == "ACTIVE_BUSINESS_WORK"
    finally:
        manager.active_suite_id = None
        manager.commands = {}
        manager.session = None
        manager.client = None
        manager.owner = None
        manager.close()
