"""The desk assistant's first placement is saved once, without the user restoring it
(memorive/desktop/assistant/desktop.py AssistantDesktop.initial_placement)."""
from __future__ import annotations

from pathlib import Path
import sys
import threading

ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
for candidate in (ATTEMPT_ROOT / "source", ATTEMPT_ROOT / "product" / "desktop"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive.desktop.assistant.desktop import AssistantDesktop  # noqa: E402
from memorive.desktop.assistant.models import AssistantWindowPlacement  # noqa: E402
from memorive.desktop.assistant.placement import (  # noqa: E402
    WorkArea,
    clamp_placement,
    restore_default_placement,
    visible_bounds,
)


def test_first_assistant_placement_is_persisted_without_restore_action() -> None:
    area = WorkArea(
        monitor_id="display-primary",
        left=0,
        top=0,
        right=1920,
        bottom=1080,
        primary=True,
        dpi_percent=100,
    )

    class _ControlStore:
        def __init__(self) -> None:
            self.filters: dict[str, object] = {}

        def load(self) -> dict[str, object]:
            return {"state": {"filters": dict(self.filters)}}

    class _Assistant:
        def __init__(self, store: _ControlStore) -> None:
            self.store = store
            self.persisted: list[AssistantWindowPlacement] = []

        def load_placement(
            self, areas: tuple[WorkArea, ...]
        ) -> AssistantWindowPlacement:
            if not self.store.filters:
                return restore_default_placement(areas)
            return AssistantWindowPlacement(
                monitor_id=str(self.store.filters["assistant.monitor_id"]),
                x=int(self.store.filters["assistant.x"]),
                y=int(self.store.filters["assistant.y"]),
                width=int(self.store.filters["assistant.width"]),
                height=int(self.store.filters["assistant.height"]),
                dpi_percent=int(self.store.filters["assistant.dpi_percent"]),
            )

        def persist_placement(
            self,
            placement: AssistantWindowPlacement,
            _areas: tuple[WorkArea, ...],
        ) -> dict[str, object]:
            self.persisted.append(placement)
            for field in ("monitor_id", "x", "y", "width", "height", "dpi_percent"):
                self.store.filters[f"assistant.{field}"] = getattr(placement, field)
            return {"placement": placement.to_dict(), "control_store_revision": 1}

    store = _ControlStore()
    assistant = _Assistant(store)
    api = object.__new__(AssistantDesktop)
    api.lock = threading.RLock()
    api.window = None
    api.control_store = store
    api.controller = assistant
    api.areas = (area,)
    api.placement_class = AssistantWindowPlacement
    api.clamp_placement = clamp_placement
    api.visible_bounds = visible_bounds

    first = api.initial_placement()
    second = api.initial_placement()

    assert first == second
    assert len(assistant.persisted) == 1
    assert store.filters["assistant.width"] == 90
    assert store.filters["assistant.height"] == 90
