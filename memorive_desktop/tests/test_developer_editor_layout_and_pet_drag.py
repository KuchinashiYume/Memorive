from __future__ import annotations

from pathlib import Path


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ATTEMPT_ROOT.parents[1]
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_pet_drag_uses_pointer_capture_and_a_cursor_tracking_timer() -> None:
    app = _read(DESKTOP_ROOT / "app.py")
    drag = app.split("def on_mouse_down", 1)[1].split("picture.ContextMenuStrip", 1)[0]
    assert "drag_timer" in drag
    assert "picture.Capture = True" in drag
    assert "picture.Capture = False" in drag
    assert "Cursor.Position" in drag
    assert '"x": int(form.Left)' in drag
    assert '"y": int(form.Top)' in drag
    assert "ClientSize.Width) / 224.0" not in drag
    assert "invoke_assistant_physical_drag" in app


