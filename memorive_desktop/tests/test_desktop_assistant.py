from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memorive.desktop.assistant import DesktopAssistantController, SafeShortcutRegistry, project_assistant_state
from memorive.desktop.assistant.models import AssistantPresentation, AssistantWindowPlacement
from memorive.desktop.assistant.placement import PYWEBVIEW_WINDOW_SPEC, WorkArea, clamp_placement, restore_default_placement, visible_bounds
from memorive.desktop.assistant.shortcuts import FIXED_INTERNAL_ACTION_IDS
from memorive.desktop.service.control_store import ControlStore


HEALTH = {
    "schema_version": "ApplicationServiceHealth-v1",
    "external_network_calls": 0,
    "external_model_calls": 0,
    "credential_value_reads": 0,
}


def registry() -> SafeShortcutRegistry:
    def handler(request):
        return {"route": "messages" if request["action_id"] == "OPEN_MESSAGE_DETAIL" else "assistant", "accepted": True}

    return SafeShortcutRegistry({action_id: handler for action_id in FIXED_INTERNAL_ACTION_IDS})


class ProjectionTests(unittest.TestCase):
    def test_no_job_is_idle(self):
        value = project_assistant_state(service_health=HEALTH, jobs=[])
        # The durable projection is static while idle. Cosmetic blinking is
        # scheduled independently by assistant_native_motion.
        self.assertEqual((value.display_state, value.attention_state, value.animation_state), ("IDLE", "NONE", "STATIC"))

    def test_running_job(self):
        value = project_assistant_state(service_health=HEALTH, jobs=[{"status": "RUNNING", "locator": "memorive://job/job-1"}])
        self.assertEqual((value.display_state, value.attention_state, value.reason_code), ("IDLE", "INFO", "JOB_RUNNING"))
        self.assertEqual(value.animation_state, "WORKING")

    def test_waiting_job(self):
        value = project_assistant_state(service_health=HEALTH, jobs=[{"state": "WAITING_USER", "locator": "memorive://action/job-1/action-1"}])
        self.assertEqual((value.attention_state, value.animation_state), ("WARNING", "ATTENTION"))

    def test_success_job(self):
        value = project_assistant_state(service_health=HEALTH, jobs=[{"lifecycle_status": "COMPLETED", "verification_result": "PASS"}])
        self.assertEqual((value.attention_state, value.animation_state), ("SUCCESS", "SUCCESS"))

    def test_error_job(self):
        value = project_assistant_state(service_health=HEALTH, jobs=[{"lifecycle_status": "ERROR"}])
        self.assertEqual((value.attention_state, value.animation_state), ("ERROR", "ERROR"))

    def test_contradictory_job_fails_closed(self):
        value = project_assistant_state(service_health=HEALTH, jobs=[{"lifecycle_status": "ERROR", "verification_result": "PASS"}])
        self.assertEqual((value.display_state, value.reason_code), ("SUSPENDED", "JOB_STATE_UNKNOWN_OR_CONTRADICTORY"))

    def test_unknown_job_fails_closed(self):
        value = project_assistant_state(service_health=HEALTH, jobs=[{"state": "MYSTERY"}])
        self.assertEqual(value.display_state, "SUSPENDED")

    def test_invalid_locator_fails_closed(self):
        value = project_assistant_state(service_health=HEALTH, jobs=[{"state": "RUNNING", "locator": "https://example.invalid"}])
        self.assertEqual(value.reason_code, "JOB_LOCATOR_INVALID")

    def test_unavailable_health_fails_closed(self):
        value = project_assistant_state(service_health=None, jobs=[])
        self.assertEqual(value.reason_code, "SERVICE_HEALTH_UNSAFE_OR_UNAVAILABLE")

    def test_effectful_health_fails_closed(self):
        health = dict(HEALTH, external_network_calls=1)
        value = project_assistant_state(service_health=health, jobs=[])
        self.assertEqual(value.display_state, "SUSPENDED")

    def test_reduced_motion_forces_static(self):
        value = project_assistant_state(service_health=HEALTH, jobs=[{"state": "RUNNING"}], reduced_motion=True)
        self.assertEqual(value.animation_state, "STATIC")

    def test_hidden_stops_animation(self):
        value = project_assistant_state(service_health=HEALTH, jobs=[{"state": "RUNNING"}], visible=False)
        self.assertEqual((value.display_state, value.animation_state), ("HIDDEN", "STATIC"))

    def test_disabled_stops_animation(self):
        value = project_assistant_state(service_health=HEALTH, jobs=[], assistant_enabled=False)
        self.assertEqual((value.display_state, value.animation_state), ("DISABLED", "STATIC"))

    def test_menu_override(self):
        value = project_assistant_state(service_health=HEALTH, jobs=[], menu_open=True)
        self.assertEqual(value.display_state, "MENU_OPEN")

    def test_drag_override(self):
        value = project_assistant_state(service_health=HEALTH, jobs=[], dragging=True)
        self.assertEqual((value.display_state, value.animation_state), ("DRAGGING", "DRAG"))

    def test_latest_sequence_wins(self):
        value = project_assistant_state(
            service_health=HEALTH,
            jobs=[{"state": "ERROR", "sequence": 1}, {"state": "RUNNING", "sequence": 2}],
        )
        self.assertEqual(value.reason_code, "JOB_RUNNING")

    def test_sensitive_summary_rejected(self):
        with self.assertRaisesRegex(ValueError, "SENSITIVE"):
            AssistantPresentation("IDLE", "NONE", "STATIC", "token leaked", "TEST")


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.areas = (
            WorkArea("DISPLAY1", 0, 0, 1920, 1040, primary=True),
            WorkArea("DISPLAY2", 1920, 0, 4480, 1400),
        )

    def test_missing_monitor_selects_the_area_nearest_the_visible_pet(self):
        value = clamp_placement(AssistantWindowPlacement("MISSING", 2000, 1200), self.areas)
        self.assertEqual(value.monitor_id, "DISPLAY2")

    def test_dpi_denominator(self):
        for dpi in (100, 125, 150, 175, 200):
            self.assertEqual(AssistantWindowPlacement("DISPLAY1", 1, 1, dpi_percent=dpi).dpi_percent, dpi)
        with self.assertRaisesRegex(ValueError, "DPI"):
            AssistantWindowPlacement("DISPLAY1", 1, 1, dpi_percent=501)

    def test_legacy_host_bounds_are_dpi_aware_for_migration(self):
        value = AssistantWindowPlacement(
            "DISPLAY1", 0, 0, width=224, height=244, dpi_percent=150
        )
        self.assertEqual(visible_bounds(value), (154, 148, 135, 135))

    def test_window_spec_is_safe(self):
        self.assertTrue(PYWEBVIEW_WINDOW_SPEC["transparent"])
        self.assertTrue(PYWEBVIEW_WINDOW_SPEC["on_top"])
        self.assertFalse(PYWEBVIEW_WINDOW_SPEC["external_urls_allowed"])
        self.assertEqual(PYWEBVIEW_WINDOW_SPEC["taskbar_visibility"], "HIDDEN_NATIVE_ADAPTER_REQUIRED")


class ShortcutTests(unittest.TestCase):
    def test_exact_allowlist(self):
        self.assertEqual(registry().action_ids, FIXED_INTERNAL_ACTION_IDS)

    def test_internal_action_receipt(self):
        value = registry().dispatch({"action_id": "SHOW_DESKTOP_ASSISTANT"})
        self.assertEqual(value["status"], "ACKNOWLEDGED")
        self.assertEqual(value["external_launch_calls"], 0)
        self.assertEqual(value["shell_command_calls"], 0)

    def test_message_locator(self):
        value = registry().dispatch({"action_id": "OPEN_MESSAGE_DETAIL", "locator": "memorive://action/job-1/action-1"})
        self.assertEqual(value["action_id"], "OPEN_MESSAGE_DETAIL")

    def test_unknown_action_rejected(self):
        with self.assertRaisesRegex(ValueError, "NOT_ALLOWLISTED"):
            registry().dispatch({"action_id": "RUN_POWERSHELL"})

    def test_arbitrary_field_rejected(self):
        with self.assertRaisesRegex(ValueError, "FIELDS"):
            registry().dispatch({"action_id": "SHOW_DESKTOP_ASSISTANT", "args": "whoami"})

    def test_locator_forbidden_for_other_actions(self):
        with self.assertRaisesRegex(ValueError, "LOCATOR_FORBIDDEN"):
            registry().dispatch({"action_id": "OPEN_Desktop_MAIN", "locator": "memorive://job/job-1"})

    def test_external_url_rejected(self):
        with self.assertRaises(Exception):
            registry().dispatch({"action_id": "OPEN_MESSAGE_DETAIL", "locator": "https://example.invalid"})

    def test_handler_set_must_be_exact(self):
        with self.assertRaisesRegex(ValueError, "HANDLER_SET"):
            SafeShortcutRegistry({})


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.controller = DesktopAssistantController(ControlStore(Path(self.temp.name)), registry())
        self.areas = (WorkArea("DISPLAY1", 0, 0, 1920, 1040, primary=True),)

    def tearDown(self):
        self.temp.cleanup()

    def test_refresh_and_metrics(self):
        value = self.controller.refresh(service_health=HEALTH, jobs=[])
        self.assertEqual(value["display_state"], "IDLE")
        metrics = self.controller.effect_metrics()
        self.assertEqual(metrics["projection_count"], 1)
        self.assertTrue(all(metrics[key] == 0 for key in metrics if key.endswith(("calls", "reads", "writes", "launches"))))

    def test_persist_and_load_placement(self):
        stored = self.controller.persist_placement(AssistantWindowPlacement("DISPLAY1", 100, 120, dpi_percent=125), self.areas)
        self.assertEqual(stored["placement"]["dpi_percent"], 125)
        self.assertEqual(self.controller.load_placement(self.areas).x, 100)

    def test_default_placement_on_empty_store(self):
        value = self.controller.load_placement(self.areas)
        self.assertEqual(value.monitor_id, "DISPLAY1")

    def test_restore_default(self):
        value = self.controller.restore_default(self.areas, dpi_percent=200)
        self.assertEqual(value["placement"]["dpi_percent"], 200)

    def test_control_validation(self):
        with self.assertRaisesRegex(ValueError, "INVALID"):
            self.controller.set_control(visible="yes")

    def test_controller_dispatch(self):
        receipt = self.controller.dispatch_shortcut({"action_id": "HIDE_DESKTOP_ASSISTANT"})
        self.assertEqual(receipt["system_autostart_writes"], 0)


if __name__ == "__main__":
    unittest.main()
