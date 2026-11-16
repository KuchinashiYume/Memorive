from __future__ import annotations

from pathlib import Path

from memorive_desktop_assistant.models import AssistantWindowPlacement
from memorive_desktop_assistant.placement import WorkArea, clamp_placement


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ATTEMPT_ROOT.parents[1]
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_runtime_enumerates_all_monitors_and_persists_visible_bounds() -> None:
    # The desk assistant's window code is in memorive_desktop_assistant/desktop.py.
    runtime = _read(DESKTOP_ROOT.parents[1] / "source" / "memorive_desktop_assistant" / "desktop.py")
    assert "EnumDisplayMonitors" in runtime
    assert "GetMonitorInfoW" in runtime
    assert "MONITORINFOEXW" in runtime
    assert "GetScaleFactorForMonitor" in runtime
    assert "GetDpiForMonitor" in runtime
    assert "_refresh_display_topology" in runtime
    assert "target_area.dpi_percent" in runtime
    assert "native.SetBounds" in runtime
    assert "visible_bounds" in runtime


