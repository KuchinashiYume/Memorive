from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pr_os_desktop_service.control_store import ControlStore

from .models import AssistantPresentation, AssistantWindowPlacement
from .placement import WorkArea, clamp_placement, restore_default_placement
from .projection import project_assistant_state
from .shortcuts import SafeShortcutRegistry


_PLACEMENT_KEYS = {
    "monitor_id": "assistant.monitor_id",
    "x": "assistant.x",
    "y": "assistant.y",
    "width": "assistant.width",
    "height": "assistant.height",
    "dpi_percent": "assistant.dpi_percent",
}


class DesktopAssistantController:
    def __init__(self, control_store: ControlStore, shortcuts: SafeShortcutRegistry):
        self.control_store = control_store
        self.shortcuts = shortcuts
        self.enabled = True
        self.visible = True
        self.menu_open = False
        self.dragging = False
        self.closing = False
        self.reduced_motion = False
        self._presentation: AssistantPresentation | None = None
        self._projection_count = 0

    def refresh(self, *, service_health: Mapping[str, Any] | None, jobs: Sequence[Mapping[str, Any]] | None) -> dict[str, object]:
        self._presentation = project_assistant_state(
            service_health=service_health,
            jobs=jobs,
            assistant_enabled=self.enabled,
            visible=self.visible,
            menu_open=self.menu_open,
            dragging=self.dragging,
            closing=self.closing,
            reduced_motion=self.reduced_motion,
        )
        self._projection_count += 1
        return self._presentation.to_dict()

    def set_control(self, *, enabled: bool | None = None, visible: bool | None = None, menu_open: bool | None = None, dragging: bool | None = None, reduced_motion: bool | None = None) -> None:
        for name, value in {
            "enabled": enabled,
            "visible": visible,
            "menu_open": menu_open,
            "dragging": dragging,
            "reduced_motion": reduced_motion,
        }.items():
            if value is not None:
                if not isinstance(value, bool):
                    raise ValueError(f"ASSISTANT_CONTROL_{name.upper()}_INVALID")
                setattr(self, name, value)

    def persist_placement(self, placement: AssistantWindowPlacement, areas: tuple[WorkArea, ...]) -> dict[str, Any]:
        accepted = clamp_placement(placement, areas)

        def mutate(state: dict[str, Any]) -> None:
            filters = state["filters"]
            for field, key in _PLACEMENT_KEYS.items():
                filters[key] = getattr(accepted, field)

        envelope = self.control_store.update(mutate)
        return {"placement": accepted.to_dict(), "control_store_revision": envelope["revision"]}

    def load_placement(self, areas: tuple[WorkArea, ...]) -> AssistantWindowPlacement:
        envelope = self.control_store.load()
        filters = envelope["state"]["filters"]
        try:
            placement = AssistantWindowPlacement(**{field: filters[key] for field, key in _PLACEMENT_KEYS.items()})
        except (KeyError, TypeError, ValueError):
            return restore_default_placement(areas)
        return clamp_placement(placement, areas)

    def restore_default(
        self, areas: tuple[WorkArea, ...], *, dpi_percent: int | None = None
    ) -> dict[str, Any]:
        return self.persist_placement(restore_default_placement(areas, dpi_percent=dpi_percent), areas)

    def dispatch_shortcut(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self.shortcuts.dispatch(request)

    def effect_metrics(self) -> dict[str, int | str]:
        return {
            "schema_version": "AssistantEffectMetrics-v1",
            "projection_count": self._projection_count,
            "external_network_calls": 0,
            "external_model_calls": 0,
            "credential_value_reads": 0,
            "shell_command_calls": 0,
            "arbitrary_url_launches": 0,
            "system_autostart_writes": 0,
            "global_hook_writes": 0,
            "production_writes": 0,
            "central_log_writes": 0,
            "branch_log_writes": 0,
        }


__all__ = ["DesktopAssistantController"]
