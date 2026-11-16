"""The desk assistant window (the desk pet): where it sits on screen (placement.py), its keyboard
shortcuts (shortcuts.py), what it shows (projection.py) and how notifications reach it (delivery.py).

desktop.py (AssistantDesktop) holds its desktop side: preferences, shortcuts, the window and the
notices. ProductApi in memorive/desktop/api.py builds it at start and forwards the window's assistant_* calls to it.
"""

from memorive.desktop.assistant.controller import DesktopAssistantController
from memorive.desktop.assistant.models import AssistantPresentation, AssistantWindowPlacement
from memorive.desktop.assistant.projection import project_assistant_state
from memorive.desktop.assistant.shortcuts import SafeShortcutRegistry

__all__ = [
    "AssistantPresentation",
    "AssistantWindowPlacement",
    "DesktopAssistantController",
    "SafeShortcutRegistry",
    "project_assistant_state",
]
