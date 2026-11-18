"""Page state saves of the work log and library controllers.

A save writes <state>.json.next and then moves it over <state>.json. A save that stopped between the two
steps leaves the .next file behind; the next save must still go through (it used to fail for good with
"temporary file already exists"). A value from the window that the page cannot use is still refused
before anything is saved.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
if str(DESKTOP / "source") not in sys.path:
    sys.path.insert(0, str(DESKTOP / "source"))

from memorive.desktop.service.control_store import ControlStore  # noqa: E402
from memorive.desktop.work_log.product_controller import WorkLogProductController, WorkLogProductError  # noqa: E402
from memorive.documents.library.product_controller import LibraryProductController  # noqa: E402


class Service:
    def call(self, method, _params):
        return {"schema_version": "ApplicationServiceHealth-v1", "protocol_version": "1.0", "adapter_kind": "fixture",
                "request_count": 0, "auth_rejection_count": 0, "connection_count": 0, "uptime_seconds": 0,
                "external_network_calls": 0, "external_model_calls": 0, "credential_value_reads": 0}


def work_log(tmp_path):
    return WorkLogProductController(
        Service(), ControlStore(tmp_path / "ui"), DESKTOP / "contracts" / "logging", DESKTOP / "fixtures_logging",
        tmp_path / "work-log", include_synthetic_projection=False,
        now_provider=lambda: datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc))


def library(tmp_path):
    return LibraryProductController(
        Service(), ControlStore(tmp_path / "ui"), DESKTOP / "contracts" / "integration", DESKTOP / "fixtures_integration",
        tmp_path / "library", include_synthetic_projection=False)


def test_a_save_left_half_done_does_not_block_the_work_log(tmp_path):
    controller = work_log(tmp_path)
    leftover = controller.state_path.with_suffix(".json.next")
    leftover.write_text("{half written", encoding="utf-8")
    controller.call("work_log.set_filter", {"time_range": "30"})
    assert json.loads(controller.state_path.read_text(encoding="utf-8"))["time_range"] == "30"
    assert not leftover.exists()


def test_a_save_left_half_done_does_not_block_the_library(tmp_path):
    controller = library(tmp_path)
    leftover = controller.state_path.with_suffix(".json.next")
    leftover.write_text("{half written", encoding="utf-8")
    controller.call("library.set_view", {"view": "memorive"})
    assert json.loads(controller.state_path.read_text(encoding="utf-8"))["view"] == "memorive"
    assert not leftover.exists()


def test_a_filter_the_page_cannot_use_is_refused_before_it_is_saved(tmp_path):
    controller = work_log(tmp_path)
    before = controller.state_path.read_text(encoding="utf-8")
    with pytest.raises(WorkLogProductError, match="WORK_LOG_FILTER_AXIS_INVALID"):
        controller.call("work_log.set_filter", {"time_range": "forever"})
    assert controller.state_path.read_text(encoding="utf-8") == before
    assert controller.call("work_log.effect_metrics", {})["status"] == "PASS"
