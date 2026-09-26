from __future__ import annotations

from dataclasses import dataclass

from .models import AssistantWindowPlacement


@dataclass(frozen=True)
class WorkArea:
    monitor_id: str
    left: int
    top: int
    right: int
    bottom: int
    primary: bool = False
    dpi_percent: int = 100

    def __post_init__(self) -> None:
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("ASSISTANT_WORK_AREA_INVALID")
        if isinstance(self.dpi_percent, bool) or not isinstance(self.dpi_percent, int):
            raise ValueError("ASSISTANT_WORK_AREA_DPI_INVALID")
        if not 50 <= self.dpi_percent <= 500:
            raise ValueError("ASSISTANT_WORK_AREA_DPI_INVALID")


PYWEBVIEW_WINDOW_SPEC = {
    "transparent": True,
    "frameless": True,
    "on_top": True,
    "easy_drag": False,
    "resizable": False,
    "focus": False,
    "text_select": False,
    "zoomable": False,
    "background_color": "#00000000",
    "taskbar_visibility": "HIDDEN_NATIVE_ADAPTER_REQUIRED",
    "external_urls_allowed": False,
}


def _select_area(monitor_id: str | None, areas: tuple[WorkArea, ...]) -> WorkArea:
    if not areas:
        raise ValueError("ASSISTANT_WORK_AREAS_EMPTY")
    for area in areas:
        if area.monitor_id == monitor_id:
            return area
    for area in areas:
        if area.primary:
            return area
    return areas[0]


_HOST_REFERENCE_WIDTH = 224
_HOST_REFERENCE_HEIGHT = 244
_VISIBLE_REFERENCE_LEFT = 103
_VISIBLE_REFERENCE_TOP = 99
_VISIBLE_REFERENCE_WIDTH = 90
_VISIBLE_REFERENCE_HEIGHT = 90
PET_DISPLAY_PERCENT = 115


def visible_bounds(placement: AssistantWindowPlacement) -> tuple[int, int, int, int]:
    """Return the real pet artwork bounds inside the transparent host window."""
    dpi_scale = placement.dpi_percent / 100.0
    if (
        placement.width == _HOST_REFERENCE_WIDTH
        and placement.height == _HOST_REFERENCE_HEIGHT
    ):
        # Compatibility for positions persisted by build019 and earlier. The
        # legacy host was much larger than the artwork and its stored size was
        # expressed in logical pixels.
        left = round(_VISIBLE_REFERENCE_LEFT * dpi_scale)
        top = round(_VISIBLE_REFERENCE_TOP * dpi_scale)
        width = max(1, round(_VISIBLE_REFERENCE_WIDTH * dpi_scale))
        height = max(1, round(_VISIBLE_REFERENCE_HEIGHT * dpi_scale))
        return left, top, width, height
    # build020 uses a pet-sized host. Its native client rectangle and visible
    # hit surface are the same size, so there is no screenshot-selectable
    # 224x244 transparent frame around the artwork.
    left = 0
    top = 0
    # Preserve stored logical coordinates and legacy-host migration. Enlarge
    # the r50 physical footprint by exactly 115%, rounded to a whole pixel.
    # Both movement clamping and native window sizing consume these bounds.
    width = max(1, (int(placement.width * dpi_scale) * PET_DISPLAY_PERCENT + 50) // 100)
    height = max(1, (int(placement.height * dpi_scale) * PET_DISPLAY_PERCENT + 50) // 100)
    return left, top, width, height


def _distance_to_area_squared(x: int, y: int, area: WorkArea) -> int:
    nearest_x = min(max(x, area.left), area.right - 1)
    nearest_y = min(max(y, area.top), area.bottom - 1)
    return (x - nearest_x) ** 2 + (y - nearest_y) ** 2


def _select_visible_area(
    placement: AssistantWindowPlacement, areas: tuple[WorkArea, ...]
) -> WorkArea:
    if not areas:
        raise ValueError("ASSISTANT_WORK_AREAS_EMPTY")
    visible_left, visible_top, visible_width, visible_height = visible_bounds(placement)
    center_x = placement.x + visible_left + visible_width // 2
    center_y = placement.y + visible_top + visible_height // 2
    for area in areas:
        if area.left <= center_x < area.right and area.top <= center_y < area.bottom:
            return area
    return min(
        areas,
        key=lambda area: (
            _distance_to_area_squared(center_x, center_y, area),
            0 if area.monitor_id == placement.monitor_id else 1,
            0 if area.primary else 1,
            area.monitor_id,
        ),
    )


def restore_default_placement(
    areas: tuple[WorkArea, ...],
    *,
    dpi_percent: int | None = None,
    margin: int = 24,
) -> AssistantWindowPlacement:
    area = _select_area(None, areas)
    width, height = 90, 90
    effective_dpi = area.dpi_percent if dpi_percent is None else dpi_percent
    candidate = AssistantWindowPlacement(
        monitor_id=area.monitor_id,
        x=area.right - width - margin,
        y=area.bottom - height - margin,
        width=width,
        height=height,
        dpi_percent=effective_dpi,
    )
    visible_left, visible_top, visible_width, visible_height = visible_bounds(candidate)
    return AssistantWindowPlacement(
        monitor_id=area.monitor_id,
        x=area.right - visible_left - visible_width - margin,
        y=area.bottom - visible_top - visible_height - margin,
        width=width,
        height=height,
        dpi_percent=effective_dpi,
    )


def clamp_placement(
    placement: AssistantWindowPlacement,
    areas: tuple[WorkArea, ...],
    *,
    margin: int = 0,
) -> AssistantWindowPlacement:
    if isinstance(margin, bool) or not isinstance(margin, int) or margin < 0:
        raise ValueError("ASSISTANT_PLACEMENT_MARGIN_INVALID")
    area = _select_visible_area(placement, areas)
    visible_left, visible_top, visible_width, visible_height = visible_bounds(placement)
    minimum_x = area.left + margin - visible_left
    minimum_y = area.top + margin - visible_top
    maximum_x = max(minimum_x, area.right - margin - visible_left - visible_width)
    maximum_y = max(minimum_y, area.bottom - margin - visible_top - visible_height)
    return AssistantWindowPlacement(
        monitor_id=area.monitor_id,
        x=min(max(placement.x, minimum_x), maximum_x),
        y=min(max(placement.y, minimum_y), maximum_y),
        width=placement.width,
        height=placement.height,
        dpi_percent=placement.dpi_percent,
    )


__all__ = [
    "PYWEBVIEW_WINDOW_SPEC",
    "WorkArea",
    "clamp_placement",
    "restore_default_placement",
    "visible_bounds",
]
