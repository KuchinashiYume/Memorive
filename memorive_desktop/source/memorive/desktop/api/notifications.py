"""Windows notifications: NotificationsMixin.
"""
from __future__ import annotations

import json
from typing import Any
from notification_runtime import NOTIFICATION_KINDS, plan_windows_notification


class NotificationsMixin:
    """Windows notifications: delivery, the test notification, activation, and bringing
    the main window to the front."""

    def attach_notification_presenter(self, presenter: Any) -> None:
        if self._notification_presenter is not None:
            raise RuntimeError("NOTIFICATION_PRESENTER_ALREADY_ATTACHED")
        if not callable(presenter):
            raise ValueError("NOTIFICATION_PRESENTER_INVALID")
        self._notification_presenter = presenter

    def _deliver_windows_notification(
        self,
        *,
        kind: str,
        locator: str | None = None,
        source_message_id: str | None = None,
    ) -> dict[str, Any]:
        notification_preferences=dict(self._current_preferences())
        if kind=='LITERATURE':
            notification_preferences['external_literature_notification']=self._service.call('settings.research_get',{})['config']['push_enabled']
        plan = plan_windows_notification(
            notification_preferences,
            kind=kind,
            locator=locator,
            source_message_id=source_message_id,
        )
        self._notification_delivery_count += 1
        if plan["status"] != "DELIVER":
            receipt = {
                **plan,
                "schema_version": "DesktopNativeNotificationDeliveryReceipt-v1",
                "delivery_sequence": self._notification_delivery_count,
                "windows_presenter_called": False,
                "status": plan["status"],
            }
            self._last_notification_receipt = receipt
            return receipt
        if self._notification_presenter is None:
            receipt = {
                **plan,
                "schema_version": "DesktopNativeNotificationDeliveryReceipt-v1",
                "delivery_sequence": self._notification_delivery_count,
                "windows_presenter_called": False,
                "reason_code": "WINDOWS_PRESENTER_NOT_ATTACHED",
                "status": "BLOCKED_UNAVAILABLE",
            }
            self._last_notification_receipt = receipt
            return receipt
        presenter_result = self._notification_presenter(dict(plan))
        receipt = {
            **plan,
            "schema_version": "DesktopNativeNotificationDeliveryReceipt-v1",
            "delivery_sequence": self._notification_delivery_count,
            "windows_presenter_called": True,
            "presenter_result": presenter_result if isinstance(presenter_result, dict) else None,
            "status": "DELIVERED",
        }
        self._last_notification_receipt = receipt
        return receipt

    def test_windows_notification(
        self, request: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        accepted = dict(request or {})
        if set(accepted) - {"kind"}:
            raise ValueError("NOTIFICATION_TEST_FIELDS_INVALID")
        kind = str(accepted.get("kind", "COMPLETE")).upper()
        if kind not in NOTIFICATION_KINDS:
            raise ValueError("NOTIFICATION_KIND_INVALID")
        return {
            **self._deliver_windows_notification(kind=kind),
            "test_notification": True,
            "real_windows_presentation_requested": True,
        }

    def activate_notification(
        self, request: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        accepted = dict(request or {})
        if set(accepted) - {"source_message_id", "activation_locator"}:
            raise ValueError("NOTIFICATION_ACTIVATION_FIELDS_INVALID")
        message_id = accepted.get("source_message_id")
        locator = accepted.get("activation_locator")
        navigation: dict[str, Any] | None = None
        if message_id:
            navigation = self._messages.call(
                "messages.notification_activate", {"message_id": str(message_id)}
            )
        elif locator:
            navigation = self._messages.call(
                "messages.navigate", {"locator": str(locator)}
            )
        self._bring_main_window_to_foreground()
        route = "current-task" if locator and "/job/" in str(locator) else "messages"
        if self._window is not None:
            try:
                self._window.evaluate_js(
                    "window.Memorive?.app?.activate("
                    + json.dumps(route)
                    + ")"
                )
            except Exception:
                pass
        return {
            "schema_version": "DesktopNativeNotificationActivationReceipt-v1",
            "source_message_id": message_id,
            "route": route,
            "navigation": navigation,
            "task_or_object_mutation": False,
            "status": "PASS",
        }

    def _bring_main_window_to_foreground(self) -> None:
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        self._window.show()
        native = getattr(self._window, "native", None)
        if native is None:
            raise RuntimeError("PRODUCT_NATIVE_WINDOW_NOT_READY")
        import clr
        clr.AddReference("System.Windows.Forms")
        from System import Action
        from System.Windows.Forms import FormWindowState

        def activate() -> None:
            if native.WindowState == FormWindowState.Minimized:
                native.WindowState = FormWindowState.Normal
            native.Show()
            native.BringToFront()
            native.Activate()

        native.Invoke(Action(activate))
