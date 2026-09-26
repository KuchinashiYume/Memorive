from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from memorive_desktop_service.locator import StableLocator


DISPLAY_STATES = frozenset(
    {"UNAVAILABLE", "DISABLED", "HIDDEN", "APPEARING", "IDLE", "NOTIFYING", "MENU_OPEN", "DRAGGING", "SUSPENDED", "CLOSING"}
)
ATTENTION_STATES = frozenset({"NONE", "INFO", "SUCCESS", "WARNING", "ERROR"})
ANIMATION_STATES = frozenset({"STATIC", "WAKE", "MESSAGE", "WORKING", "BLINK", "ATTENTION", "SUCCESS", "ERROR", "DRAG", "SLEEP"})
_SENSITIVE = re.compile(r"(?i)(api[_-]?key|authorization|cookie|credential|password|secret|token|private[_-]?payload|gold|holdout|sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{12,})")


@dataclass(frozen=True)
class AssistantPresentation:
    display_state: str
    attention_state: str
    animation_state: str
    safe_summary: str
    reason_code: str
    locator: str | None = None
    reduced_motion: bool = False

    def __post_init__(self) -> None:
        if self.display_state not in DISPLAY_STATES:
            raise ValueError("ASSISTANT_DISPLAY_STATE_INVALID")
        if self.attention_state not in ATTENTION_STATES:
            raise ValueError("ASSISTANT_ATTENTION_STATE_INVALID")
        if self.animation_state not in ANIMATION_STATES:
            raise ValueError("ASSISTANT_ANIMATION_STATE_INVALID")
        if not isinstance(self.safe_summary, str) or not 1 <= len(self.safe_summary) <= 160:
            raise ValueError("ASSISTANT_SAFE_SUMMARY_INVALID")
        if _SENSITIVE.search(self.safe_summary):
            raise ValueError("ASSISTANT_SAFE_SUMMARY_SENSITIVE")
        if not re.fullmatch(r"[A-Z0-9_]{1,96}", self.reason_code):
            raise ValueError("ASSISTANT_REASON_CODE_INVALID")
        if self.locator is not None:
            StableLocator.parse(self.locator)
        if not isinstance(self.reduced_motion, bool):
            raise ValueError("ASSISTANT_REDUCED_MOTION_INVALID")
        if self.reduced_motion and self.animation_state != "STATIC":
            raise ValueError("ASSISTANT_REDUCED_MOTION_NOT_STATIC")

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": "AssistantPresentation-v1", **asdict(self)}


@dataclass(frozen=True)
class AssistantWindowPlacement:
    monitor_id: str
    x: int
    y: int
    width: int = 90
    height: int = 90
    dpi_percent: int = 100

    def __post_init__(self) -> None:
        if not isinstance(self.monitor_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,96}", self.monitor_id):
            raise ValueError("ASSISTANT_MONITOR_ID_INVALID")
        for name in ("x", "y", "width", "height", "dpi_percent"):
            if isinstance(getattr(self, name), bool) or not isinstance(getattr(self, name), int):
                raise ValueError(f"ASSISTANT_PLACEMENT_{name.upper()}_INVALID")
        if not 60 <= self.width <= 640 or not 60 <= self.height <= 640:
            raise ValueError("ASSISTANT_PLACEMENT_SIZE_INVALID")
        if not 50 <= self.dpi_percent <= 500:
            raise ValueError("ASSISTANT_PLACEMENT_DPI_INVALID")
        if not -32768 <= self.x <= 32768 or not -32768 <= self.y <= 32768:
            raise ValueError("ASSISTANT_PLACEMENT_COORDINATE_INVALID")

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": "AssistantWindowPlacement-v1", **asdict(self)}


__all__ = [
    "ANIMATION_STATES",
    "ATTENTION_STATES",
    "DISPLAY_STATES",
    "AssistantPresentation",
    "AssistantWindowPlacement",
]
