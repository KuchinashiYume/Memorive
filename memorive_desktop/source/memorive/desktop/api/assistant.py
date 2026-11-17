"""The desk assistant's window calls: AssistantCallsMixin.
"""
from __future__ import annotations

from typing import Any


class AssistantCallsMixin:
    """The desk assistant's window calls, forwarded to AssistantDesktop."""

    # The desk assistant lives in memorive/desktop/assistant/desktop.py (AssistantDesktop). The window,
    # app.py and the research runtime call these names on ProductApi.
    def assistant_bootstrap(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._desk_assistant.bootstrap(request)

    def assistant_ui_signal(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._desk_assistant.ui_signal(request)

    def assistant_initial_placement(self) -> dict[str, Any]:
        return self._desk_assistant.initial_placement()

    def assistant_move_window(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._desk_assistant.move_window(request)

    def assistant_shortcut(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._desk_assistant.shortcut(request)

    def assistant_launch_target(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._desk_assistant.launch_target(request)

    def assistant_target_status(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._desk_assistant.target_status(request)

    def claim_assistant_presentation_delivery(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._desk_assistant.claim_presentation_delivery(request)

    def record_assistant_presentation_receipt(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._desk_assistant.record_presentation_receipt(request)

    def assistant_ack_delivery(self, request):
        return self._desk_assistant.ack_delivery(request)

    def attach_assistant_window(self, window: Any) -> None:
        return self._desk_assistant.attach_window(window)

    def attach_assistant_presenter(self, presenter: Any) -> None:
        return self._desk_assistant.attach_presenter(presenter)
