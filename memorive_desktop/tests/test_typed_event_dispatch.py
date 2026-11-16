"""ProductApi.dispatch answers the page's typed events with a receipt and writes no event files.

The receipt fields are what the page gets back; the state folder gains no per-event files
(navigation still saves the selected route in the control state).
"""
from pathlib import Path

import pytest

from memorive.desktop.api import ProductApi

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "source"
RECEIPT_FIELDS = {
    "schema_version", "sequence", "request_id", "event_type", "source", "payload_sha256",
    "transport_status", "control_state_mutated", "control_store_revision", "business_state_mutated",
}


def _api(state_root):
    return ProductApi(state_root, SOURCE_ROOT, SOURCE_ROOT / "facade_method_binding.json")


def _files(root):
    return {p.relative_to(root).as_posix(): p.stat().st_size for p in root.rglob("*") if p.is_file()}


def test_dispatch_receipts_and_no_event_files(tmp_path):
    api = _api(tmp_path / "state")
    try:
        before = _files(tmp_path / "state")
        first = api.dispatch({"event_type": "navigation.select", "payload": {"route": "library"},
                              "request_id": "desktop-ui-00000001", "source": "test"})
        second = api.dispatch({"event_type": "window.sidebar.toggle", "payload": {"open": False},
                               "request_id": "desktop-ui-00000002", "source": "test"})
        after = _files(tmp_path / "state")
    finally:
        api.close()
    assert set(first) == set(second) == RECEIPT_FIELDS
    assert first["schema_version"] == "WorkLogTypedEventReceipt-v1"
    assert (first["sequence"], second["sequence"]) == (1, 2)
    assert first["transport_status"] == "LOCAL_PYWEBVIEW_DISPATCHED"
    assert first["control_state_mutated"] is True and second["control_state_mutated"] is False
    assert first["business_state_mutated"] is False
    assert first["control_store_revision"] + 0 == second["control_store_revision"]
    assert len(first["payload_sha256"]) == 64
    new_files = set(after) - set(before)
    assert not any("event" in name for name in new_files), new_files
    assert not (tmp_path / "state" / "events").exists()


@pytest.mark.parametrize("request_value, message", [
    ({"event_type": "not.allowed", "payload": {}, "request_id": "desktop-ui-1", "source": "test"}, "not allowlisted"),
    ({"event_type": "navigation.select", "payload": {}, "request_id": "wrong-prefix", "source": "test"}, "request_id invalid"),
    ({"event_type": "navigation.select", "payload": "text", "request_id": "desktop-ui-1", "source": "test"}, "payload must be an object"),
    ({"event_type": "navigation.select", "payload": {}, "request_id": "desktop-ui-1"}, "key set mismatch"),
])
def test_dispatch_refuses_malformed_events(tmp_path, request_value, message):
    api = _api(tmp_path / "state")
    try:
        with pytest.raises(ValueError, match=message):
            api.dispatch(request_value)
    finally:
        api.close()
