from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import re
from typing import Any

from memorive_desktop_service.locator import StableLocator


FIXED_INTERNAL_ACTION_IDS = (
    "SHOW_DESKTOP_ASSISTANT",
    "TOGGLE_DESKTOP_ASSISTANT",
    "OPEN_Desktop_MAIN",
    "OPEN_DESKTOP_ASSISTANT_SETTINGS",
    "HIDE_DESKTOP_ASSISTANT",
    "OPEN_MESSAGE_DETAIL",
    "RESTORE_DEFAULT_PLACEMENT",
)
_FORBIDDEN = re.compile(r"(?i)(?:^|\s)(?:cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh(?:\.exe)?|bash|sh)(?:\s|$)|https?://|file://|javascript:|shell:")


def _sha(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


class SafeShortcutRegistry:
    def __init__(self, handlers: Mapping[str, Callable[[dict[str, Any]], Mapping[str, Any]]]):
        if set(handlers) != set(FIXED_INTERNAL_ACTION_IDS):
            raise ValueError("ASSISTANT_SHORTCUT_HANDLER_SET_INVALID")
        if any(not callable(handler) for handler in handlers.values()):
            raise ValueError("ASSISTANT_SHORTCUT_HANDLER_INVALID")
        self._handlers = dict(handlers)
        self._sequence = 0

    @property
    def action_ids(self) -> tuple[str, ...]:
        return FIXED_INTERNAL_ACTION_IDS

    def dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping) or set(request) - {"action_id", "locator"}:
            raise ValueError("ASSISTANT_SHORTCUT_REQUEST_FIELDS_INVALID")
        action_id = request.get("action_id")
        if action_id not in FIXED_INTERNAL_ACTION_IDS:
            raise ValueError("ASSISTANT_SHORTCUT_ACTION_NOT_ALLOWLISTED")
        locator = request.get("locator")
        if action_id == "OPEN_MESSAGE_DETAIL":
            parsed = StableLocator.parse(locator)
            if parsed.kind not in {"job", "event", "action"}:
                raise ValueError("ASSISTANT_SHORTCUT_MESSAGE_LOCATOR_KIND_INVALID")
        elif locator is not None:
            raise ValueError("ASSISTANT_SHORTCUT_LOCATOR_FORBIDDEN")
        if any(_FORBIDDEN.search(str(value)) for value in request.values() if value is not None):
            raise ValueError("ASSISTANT_SHORTCUT_EXTERNAL_OR_SHELL_TARGET_FORBIDDEN")
        accepted = {"action_id": action_id, "locator": locator}
        self._sequence += 1
        result = dict(self._handlers[action_id](accepted))
        if any(_FORBIDDEN.search(str(value)) for value in result.values() if value is not None):
            raise ValueError("ASSISTANT_SHORTCUT_HANDLER_RESULT_UNSAFE")
        return {
            "schema_version": "AssistantShortcutReceipt-v1",
            "sequence": self._sequence,
            "action_id": action_id,
            "request_sha256": _sha(accepted),
            "result": result,
            "external_launch_calls": 0,
            "shell_command_calls": 0,
            "system_autostart_writes": 0,
            "status": "ACKNOWLEDGED",
        }


__all__ = ["FIXED_INTERNAL_ACTION_IDS", "SafeShortcutRegistry"]
