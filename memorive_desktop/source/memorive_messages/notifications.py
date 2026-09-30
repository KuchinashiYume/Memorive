from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from memorive_language.text import choose

from .contracts import MessageRecord, canonical_sha256
from .navigation import MessageNavigator
from .privacy import PrivacyBoundaryError, RedactionPolicy
from .store import MessageStore, utc_now


@dataclass(frozen=True)
class NotificationPreferences:
    enabled: bool = True
    quiet_mode: bool = False
    show_green: bool = False
    show_blue: bool = False
    capability_status: str = "AVAILABLE"
    blocked_reason: str | None = None

    def __post_init__(self) -> None:
        if self.capability_status not in {"AVAILABLE", "BLOCKED", "DISABLED"}:
            raise ValueError("NOTIFICATION_CAPABILITY_STATUS_INVALID")
        if self.capability_status != "AVAILABLE" and not self.blocked_reason:
            raise ValueError("NOTIFICATION_BLOCKED_REASON_REQUIRED")


class SyntheticWindowsBridge:
    """Offline bridge: records synthetic toast receipts and never calls Windows APIs."""

    def __init__(self, *, redaction_policy: RedactionPolicy | None = None):
        self.redaction_policy = redaction_policy or RedactionPolicy()
        self.sent: dict[str, dict[str, Any]] = {}
        self.os_api_calls = 0
        self.registration_calls = 0

    def emit(self, message: MessageRecord, *, language='zh-CN') -> dict[str, Any]:
        if message.message_id in self.sent:
            return {
                "schema_version": "MessagesSyntheticToastReceipt-v1",
                "message_id": message.message_id,
                "status": "DUPLICATE_SUPPRESSED",
                "os_api_calls": self.os_api_calls,
                "registration_calls": self.registration_calls,
            }
        title = "Memorive 需要注意" if message.severity == "RED" else "Memorive 等待处理"
        if message.severity in {"GREEN", "BLUE"}:
            title = "Memorive 有新消息"
        title = choose(language, title, "Memorive needs attention" if message.severity == "RED" else "Memorive has a new message", "Memorive の確認が必要です" if message.severity == "RED" else "Memorive に新しいメッセージがあります")
        body = choose(language, "打开应用查看详情。", "Open the app for details.", "アプリで詳細を確認してください。")
        self.redaction_policy.assert_public_safe({"title": title, "body": body})
        receipt = {
            "schema_version": "MessagesSyntheticToastReceipt-v1",
            "message_id": message.message_id,
            "severity": message.severity,
            "title": title,
            "body": body,
            "locator": message.target_locator,
            "created_at": utc_now(),
            "lockscreen_detail": "GENERIC_SUMMARY_ONLY",
            "private_body_included": False,
            "os_api_calls": 0,
            "registration_calls": 0,
            "status": "SYNTHETIC_TOAST_EMITTED",
        }
        self.sent[message.message_id] = receipt
        return dict(receipt)


class NotificationPlanner:
    def __init__(
        self,
        store: MessageStore,
        bridge: SyntheticWindowsBridge,
        *,
        preferences: NotificationPreferences | None = None,
        language_get=None,
    ):
        self.language_get = language_get or (lambda: 'zh-CN')
        self.store = store
        self.bridge = bridge
        self.preferences = preferences or NotificationPreferences()

    def _eligible(self, message: MessageRecord) -> bool:
        if message.severity in {"RED", "YELLOW"}:
            return True
        if message.severity == "GREEN":
            return self.preferences.show_green
        return self.preferences.show_blue

    def deliver(self, message_id: str) -> dict[str, Any]:
        message = self.store.get(message_id)
        if message.notification_state == "TOAST_SENT":
            return {
                "schema_version": "MessagesNotificationDeliveryReceipt-v1",
                "message_id": message_id,
                "status": "DUPLICATE_SUPPRESSED",
                "in_app_message_preserved": True,
                "toast_emitted": False,
            }
        state = "NOT_ELIGIBLE"
        toast: dict[str, Any] | None = None
        if not self._eligible(message):
            state = "IN_APP_ONLY"
        elif not self.preferences.enabled:
            state = "BLOCKED_DISABLED"
        elif self.preferences.quiet_mode:
            state = "BLOCKED_QUIET"
        elif self.preferences.capability_status != "AVAILABLE":
            state = "BLOCKED_UNAVAILABLE"
        else:
            toast = self.bridge.emit(message, language=self.language_get())
            state = "TOAST_SENT" if toast["status"] == "SYNTHETIC_TOAST_EMITTED" else "TOAST_SENT"
        updated = self.store.update_record(
            message_id,
            lambda raw: raw.update({"notification_state": state}),
        )
        return {
            "schema_version": "MessagesNotificationDeliveryReceipt-v1",
            "message_id": message_id,
            "severity": updated.severity,
            "notification_state": state,
            "blocked_reason": self.preferences.blocked_reason if state == "BLOCKED_UNAVAILABLE" else None,
            "in_app_message_preserved": True,
            "toast_emitted": toast is not None,
            "toast_receipt_sha256": canonical_sha256(toast) if toast is not None else None,
            "private_body_included": False,
            "os_api_calls": self.bridge.os_api_calls,
            "registration_calls": self.bridge.registration_calls,
            "status": "PASS",
        }

    def activate(self, message_id: str, navigator: MessageNavigator) -> dict[str, Any]:
        message = self.store.get(message_id)
        if message.target_locator is None:
            return {
                "schema_version": "MessagesNotificationActivationReceipt-v1",
                "message_id": message_id,
                "status": "TARGET_UNAVAILABLE",
                "task_or_object_mutation": False,
            }
        navigation = navigator.navigate(message.target_locator)
        return {
            "schema_version": "MessagesNotificationActivationReceipt-v1",
            "message_id": message_id,
            "locator_sha256": canonical_sha256(message.target_locator),
            "navigation": navigation,
            "message_confirmation_changed": False,
            "task_or_object_mutation": False,
            "status": navigation["status"],
        }


__all__ = [
    "NotificationPlanner",
    "NotificationPreferences",
    "SyntheticWindowsBridge",
]
