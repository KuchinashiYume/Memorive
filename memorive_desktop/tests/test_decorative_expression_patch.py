from __future__ import annotations

from pathlib import Path
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "product" / "desktop"


def test_native_pet_adds_frames_without_changing_motion_contract() -> None:
    source = (DESKTOP / "app.py").read_text(encoding="utf-8")
    assert "ASSISTANT_FACES" in source
    assert '"save-success"' in source
    assert '"error"' in source
    assert "LOCAL_UI_SAVE_SUCCESS" in source
    assert "sample.done" in source
    sys.path.insert(0, str(DESKTOP))
    from assistant_native_motion import MOTION_DURATIONS_MS, sample_motion

    assert MOTION_DURATIONS_MS["SUCCESS"] == 1400
    assert MOTION_DURATIONS_MS["ERROR"] == 900
    assert sample_motion("SUCCESS", 0).frame == "success"
    assert sample_motion("ERROR", 0).frame == "idle"
    assert sample_motion("SUCCESS", 1400).done is True
    assert sample_motion("ERROR", 900).done is True


def test_local_ui_signal_is_allowlisted_and_delivered() -> None:
    sys.path.insert(0, str(DESKTOP))
    from memorive.desktop.assistant.desktop import AssistantDesktop

    captured: list[dict[str, object]] = []
    api = object.__new__(AssistantDesktop)
    api.lock = threading.RLock()
    api.presenter = lambda presentation: captured.append(presentation)

    receipt = api.ui_signal({"kind": "SAVE_SUCCESS", "event_id": "save-0001"})
    assert receipt["status"] == "APPLIED"
    assert captured[-1]["animation_state"] == "SUCCESS"
    assert captured[-1]["reason_code"] == "LOCAL_UI_SAVE_SUCCESS"
    assert captured[-1]["locator"] == "memorive://ui/save_success/save-0001"

    receipt = api.ui_signal({"kind": "ERROR_MESSAGE", "event_id": "error-0001"})
    assert receipt["status"] == "APPLIED"
    assert captured[-1]["animation_state"] == "ERROR"
    assert captured[-1]["reason_code"] == "LOCAL_UI_ERROR_MESSAGE"
    assert captured[-1]["locator"] == "memorive://ui/error_message/error-0001"


def test_local_ui_signal_fails_closed_for_unknown_input_and_presenter_errors() -> None:
    sys.path.insert(0, str(DESKTOP))
    from memorive.desktop.assistant.desktop import AssistantDesktop

    api = object.__new__(AssistantDesktop)
    api.lock = threading.RLock()
    api.presenter = None
    detached = api.ui_signal({"kind": "SAVE_SUCCESS", "event_id": "save-0002"})
    assert detached["status"] == "NOT_ATTACHED"
    assert detached["external_network_calls"] == 0
    assert detached["external_model_calls"] == 0
    with pytest.raises(ValueError, match="KIND_INVALID"):
        api.ui_signal({"kind": "UNKNOWN", "event_id": "event-1"})
    with pytest.raises(ValueError, match="FIELDS_INVALID"):
        api.ui_signal({"kind": "SAVE_SUCCESS", "event_id": "event-2", "copy": "private"})

    def fail(_presentation: dict[str, object]) -> None:
        raise RuntimeError("synthetic presenter failure")

    api.presenter = fail
    failed = api.ui_signal({"kind": "ERROR_MESSAGE", "event_id": "error-0002"})
    assert failed["status"] == "ERROR:RuntimeError"

    api.presenter = lambda presentation: {"durable_claim_status": "BLOCKED:OSError"}
    blocked = api.ui_signal({"kind": "SAVE_SUCCESS", "event_id": "save-0003"})
    assert blocked["status"] == "BLOCKED_NATIVE_DELIVERY"
