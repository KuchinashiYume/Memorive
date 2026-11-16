from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ATTEMPT_ROOT.parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"
for candidate in (SOURCE_ROOT, DESKTOP_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from application_behavior import decide_close_action  # noqa: E402
from network_diagnostics import parse_cloudflare_trace, run_network_diagnostic  # noqa: E402
from memorive.desktop.assistant import project_assistant_state  # noqa: E402
from memorive.desktop.api import ProductApi  # noqa: E402


HEALTH = {
    "schema_version": "ApplicationServiceHealth-v1",
    "external_network_calls": 0,
    "external_model_calls": 0,
    "credential_value_reads": 0,
}


def _make_api(state_root: Path) -> ProductApi:
    return ProductApi(
        state_root,
        SOURCE_ROOT,
        SOURCE_ROOT / "facade_method_binding.json",
    )


def test_assistant_understands_real_job_control_state() -> None:
    value = project_assistant_state(
        service_health=HEALTH,
        jobs=[{
            "job_id": "job-real-1",
            "job_control_state": "RUNNING",
            "locator": "memorive://job/job-real-1",
        }],
    )
    assert value.reason_code == "JOB_RUNNING"
    assert value.animation_state == "WORKING"


def test_showing_hidden_assistant_resynchronizes_html_presentation(
    tmp_path: Path,
) -> None:
    class AssistantWindowProbe:
        def __init__(self) -> None:
            self.visible = True
            self.scripts: list[str] = []

        def show(self) -> None:
            self.visible = True

        def hide(self) -> None:
            self.visible = False

        def evaluate_js(self, script: str) -> None:
            self.scripts.append(script)

    api = _make_api(tmp_path / "state")
    assistant = AssistantWindowProbe()
    try:
        api.attach_assistant_window(assistant)
        api.assistant_shortcut({"action_id": "HIDE_DESKTOP_ASSISTANT"})
        shown = api.assistant_shortcut({"action_id": "SHOW_DESKTOP_ASSISTANT"})
        assert assistant.visible is True
        assert shown["presentation"]["presentation"]["reason_code"] == "NO_ACTIVE_REAL_JOB"
        assert len(assistant.scripts) == 1
        assert "__Desktop_ASSISTANT_RUNTIME__?.applyPresentation(" in assistant.scripts[0]
        assert '"display_state":"IDLE"' in assistant.scripts[0]
    finally:
        api.close()


def test_assistant_bootstrap_projects_real_unread_message(
    tmp_path: Path,
) -> None:
    api = _make_api(tmp_path / "state")
    original_service_call = api._service.call
    original_message_call = api._messages.call

    def fake_service_call(method: str, params: dict):
        if method == "service.health":
            return HEALTH
        if method == "list_jobs":
            return {"schema_version": "JobList-v1", "jobs": []}
        return original_service_call(method, params)

    def fake_message_call(method: str, params: dict):
        if method == "messages.list":
            return {
                "schema_version": "MessagesMessageListProjection-v1",
                "rows": [{
                    "message_id": "msg-real-1",
                    "read": False,
                    "severity": "RED",
                    "updated_at": "2026-08-27T03:00:00Z",
                    "source_event_ref": {
                        "source_id": "evt-real-1",
                        "locator": "memorive://action/job-real-1/action-real-1",
                    },
                    "target_locator": "memorive://action/job-real-1/action-real-1",
                }],
                "unread_count": 1,
                "status": "PASS",
            }
        return original_message_call(method, params)

    api._service.call = fake_service_call
    api._messages.call = fake_message_call
    try:
        receipt = api.assistant_bootstrap({"reduced_motion": False})
        assert receipt["real_message_count"] == 1
        assert receipt["presentation"]["reason_code"] == "MESSAGE_NEEDS_ATTENTION"
        assert receipt["presentation"]["animation_state"] == "ERROR"
    finally:
        api.close()


def test_cloudflare_trace_parser_returns_validated_egress_details() -> None:
    assert parse_cloudflare_trace("fl=29f1\nip=203.0.113.8\nloc=JP\ncolo=NRT\n") == {
        "egress_ip": "203.0.113.8",
        "country_code": "JP",
        "colo": "NRT",
    }
    with pytest.raises(ValueError, match="NETWORK_TRACE_IP_INVALID"):
        parse_cloudflare_trace("ip=not-an-ip\nloc=JP\n")


def test_network_test_checks_local_and_egress_ip_without_credentials() -> None:
    class Response:
        status = 200

        def geturl(self) -> str:
            return "https://www.cloudflare.com/cdn-cgi/trace"

        def read(self, limit: int) -> bytes:
            assert limit == 16385
            return b"ip=198.51.100.42\nloc=JP\ncolo=NRT\n"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    observed: dict[str, object] = {}

    def open_url(request, *, timeout: float, proxy_mode: str, proxy_address: str):
        observed.update({
            "url": request.full_url,
            "timeout": timeout,
            "proxy_mode": proxy_mode,
            "proxy_address": proxy_address,
        })
        return Response()

    receipt = run_network_diagnostic(
        proxy_mode="NONE",
        proxy_address="",
        timeout_seconds=9,
        open_url=open_url,
        local_address_provider=lambda: ["192.168.10.23", "10.0.0.5"],
    )

    assert receipt["status"] == "PASS"
    assert receipt["local_ip"] == "192.168.10.23"
    assert receipt["local_addresses"] == ["192.168.10.23", "10.0.0.5"]
    assert receipt["egress_ip"] == "198.51.100.42"
    assert receipt["country_code"] == "JP"
    assert receipt["external_network_calls"] == 1
    assert receipt["credential_value_reads"] == 0
    assert observed == {
        "url": "https://www.cloudflare.com/cdn-cgi/trace",
        "timeout": 9.0,
        "proxy_mode": "NONE",
        "proxy_address": "",
    }


@pytest.mark.parametrize(
    ("close_behavior", "warn", "background", "active", "choice", "expected"),
    [
        ("EXIT", False, True, 0, None, "EXIT"),
        ("EXIT", True, True, 2, None, "ASK"),
        ("EXIT", True, True, 2, "EXIT", "EXIT"),
        ("TRAY", False, True, 2, None, "HIDE"),
        ("TRAY", False, False, 2, None, "ASK"),
        ("ASK", False, True, 0, None, "ASK"),
        ("ASK", False, True, 0, "HIDE", "HIDE"),
        ("ASK", False, True, 0, "CANCEL", "CANCEL"),
    ],
)
def test_close_behavior_decision_is_connected(
    close_behavior: str,
    warn: bool,
    background: bool,
    active: int,
    choice: str | None,
    expected: str,
) -> None:
    assert decide_close_action(
        close_behavior=close_behavior,
        warn_on_close_running=warn,
        keep_tasks_in_background=background,
        active_job_count=active,
        user_choice=choice,
    ) == expected


def test_settings_save_applies_live_application_behavior_effects(tmp_path: Path) -> None:
    class AutostartProbe:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        def apply(self, enabled: bool) -> dict[str, object]:
            self.calls.append(enabled)
            return {
                "schema_version": "DesktopAutostartMutationReceipt-v1",
                "enabled_requested": enabled,
                "mutation_count": 1,
                "status": "PASS",
            }

    autostart = AutostartProbe()
    api = ProductApi(
        tmp_path / "state",
        SOURCE_ROOT,
        SOURCE_ROOT / "facade_method_binding.json",
        system_effects_enabled=True,
        autostart_manager=autostart,
    )
    try:
        current = api.call("settings.get_state", {})
        next_settings = deepcopy(current["settings"])
        next_settings["preferences"].update({
            "launch_at_startup": True,
            "restore_last_document": True,
            "remember_panel_state": False,
        })
        saved = api.call(
            "settings.save",
            {
                "settings": next_settings,
                "expected_revision": current["revision"],
                "confirmed_risk_ids": [],
            },
        )
        assert autostart.calls == [True]
        assert saved["restart_required"] is False
        assert saved["runtime_effects"]["autostart"]["status"] == "PASS"
        assert saved["runtime_effects"]["remember_panel_state"] is False
        observed = api.call("settings.get_state", {})
        assert observed["settings"]["preferences"]["restore_last_document"] is False
    finally:
        api.close()


def test_settings_save_forces_removed_restore_document_off(tmp_path: Path) -> None:
    api = _make_api(tmp_path / "state")
    try:
        current = api.call("settings.get_state", {})
        next_settings = deepcopy(current["settings"])
        next_settings["preferences"]["restore_last_document"] = True
        saved = api.call(
            "settings.save",
            {
                "settings": next_settings,
                "expected_revision": current["revision"],
                "confirmed_risk_ids": [],
            },
        )
        assert saved["action"] == "SAVE"
        observed = api.call("settings.get_state", {})
        assert observed["settings"]["preferences"]["restore_last_document"] is False
    finally:
        api.close()
