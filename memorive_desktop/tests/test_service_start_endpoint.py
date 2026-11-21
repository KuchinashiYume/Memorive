"""Starting the application service: the window waits for the endpoint file the service writes.

The service writes the file to a temporary name and replaces the endpoint file with it. On Windows a read
at that moment is refused with PermissionError, and start() used to stop the app there (seen once in a
test run). The service here is a stand-in process that writes the endpoint file when it is started.
"""
from __future__ import annotations

import json
from pathlib import Path

from memorive.desktop.api import service_process
from memorive.desktop.api.service_process import SECRET_ENV_NAME, ServiceProcessManager


def test_a_read_refused_while_the_service_replaces_the_endpoint_file_is_tried_again(tmp_path, monkeypatch):
    manager = ServiceProcessManager(tmp_path / "source", tmp_path / "profile", tmp_path / "runtime")

    class Service:
        pid = 4321

        def __init__(self, *args, **kwargs):
            manager._endpoint_file.write_text(json.dumps({
                "schema_version": "ApplicationServiceEndpoint-v1", "protocol_version": "1.0",
                "host": "127.0.0.1", "port": 50000, "pid": self.pid, "profile_root": str(manager.profile_root),
                "secret_env_name": SECRET_ENV_NAME, "secret_value_recorded": False, "external_network_calls": 0,
                "external_model_calls": 0, "credential_value_reads": 0, "parent_pid": manager.parent_pid,
                "service_revision": manager.service_revision, "status": "READY"}), encoding="utf-8")

        def poll(self):
            return None

        def terminate(self):  # start() ends the service when it gives up
            pass

        kill = terminate

        def wait(self, timeout=None):
            return 0

    refused = []
    read_text = Path.read_text

    def read_once_refused(path, *args, **kwargs):
        if path == manager._endpoint_file and not refused:
            refused.append(path)
            raise PermissionError(13, "The process cannot access the file")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(service_process.subprocess, "Popen", Service)
    monkeypatch.setattr(Path, "read_text", read_once_refused)
    try:
        manager.start(timeout_seconds=5)
        assert manager._descriptor["port"] == 50000 and len(refused) == 1
    finally:
        for stream in (manager._stdout_handle, manager._stderr_handle, manager._owner_stream):
            if stream is not None:
                stream.close()
