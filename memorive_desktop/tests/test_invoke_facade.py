"""ProductApi.invoke_facade: the earlier desktop interface to the application facade's allowlisted methods.

A command (respond_to_action) used to stop before the service with AttributeError: it was recorded in a
command outbox whose construction 1232c35 had removed. The facade itself answers a repeated command
with its first result by the command's idempotency key. ProductApi here is built without its window and
service; only what invoke_facade uses is set.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from memorive.desktop.api import ProductApi

SOURCE = Path(__file__).resolve().parents[1] / "source"


class Service:
    def __init__(self):
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        return {"method": method}


def facade_api():
    api = ProductApi.__new__(ProductApi)
    binding = json.loads((SOURCE / "facade_method_binding.json").read_text(encoding="utf-8"))
    api._method_binding = binding
    api._facade_methods = frozenset(binding["required_methods"])
    api._lock = threading.RLock()
    api._service = Service()
    return api


def test_a_query_and_a_command_both_reach_the_service():
    api = facade_api()
    api.invoke_facade({"method": "get_artifact_bindings", "args": ["job-1"], "kwargs": {}, "request_id": "r-1"})
    response = {"choice": "approve"}
    api.invoke_facade({"method": "respond_to_action", "args": ["job-1", "action-1", response],
                       "kwargs": {"expected_version": 3, "idempotency_key": "key-1"}, "request_id": "r-2"})
    assert api._service.calls == [
        ("get_artifact_bindings", {"job_id": "job-1"}),
        ("respond_to_action", {"job_id": "job-1", "action_request_id": "action-1", "response": response,
                               "expected_version": 3, "idempotency_key": "key-1"})]


def test_a_method_outside_the_binding_is_refused():
    api = facade_api()
    with pytest.raises(ValueError, match="not allowlisted"):
        api.invoke_facade({"method": "transition", "args": [], "kwargs": {}, "request_id": "r-3"})
    assert api._service.calls == []
