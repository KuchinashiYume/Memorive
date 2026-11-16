"""The desk assistant window (the desk pet): where it sits on screen (placement.py), its keyboard
shortcuts (shortcuts.py), what it shows (projection.py) and how notifications reach it (delivery.py).

ProductApi in runtime.py builds it at start; the window reaches it through assistant.* methods.
"""

from .controller import DesktopAssistantController
from .models import AssistantPresentation, AssistantWindowPlacement
from .projection import project_assistant_state
from .shortcuts import SafeShortcutRegistry

__all__ = [
    "AssistantPresentation",
    "AssistantWindowPlacement",
    "DesktopAssistantController",
    "SafeShortcutRegistry",
    "project_assistant_state",
]
