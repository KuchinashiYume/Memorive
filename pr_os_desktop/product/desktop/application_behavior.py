from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any


_CLOSE_BEHAVIORS = frozenset({"ASK", "EXIT", "TRAY"})
_USER_CHOICES = frozenset({"EXIT", "HIDE", "CANCEL"})


def decide_close_action(
    *,
    close_behavior: str,
    warn_on_close_running: bool,
    keep_tasks_in_background: bool,
    active_job_count: int,
    user_choice: str | None = None,
) -> str:
    """Return ASK, EXIT, HIDE, or CANCEL for a main-window close request."""
    if close_behavior not in _CLOSE_BEHAVIORS:
        raise ValueError("CLOSE_BEHAVIOR_INVALID")
    if not isinstance(warn_on_close_running, bool):
        raise ValueError("WARN_ON_CLOSE_RUNNING_INVALID")
    if not isinstance(keep_tasks_in_background, bool):
        raise ValueError("KEEP_TASKS_IN_BACKGROUND_INVALID")
    if isinstance(active_job_count, bool) or not isinstance(active_job_count, int) or active_job_count < 0:
        raise ValueError("ACTIVE_JOB_COUNT_INVALID")
    if user_choice is not None and user_choice not in _USER_CHOICES:
        raise ValueError("CLOSE_USER_CHOICE_INVALID")

    if user_choice == "CANCEL":
        return "CANCEL"
    if user_choice == "EXIT":
        return "EXIT"
    if user_choice == "HIDE":
        if active_job_count and not keep_tasks_in_background:
            return "ASK"
        return "HIDE"

    if close_behavior == "ASK":
        return "ASK"
    if close_behavior == "TRAY":
        return "ASK" if active_job_count and not keep_tasks_in_background else "HIDE"
    if warn_on_close_running and active_job_count:
        return "ASK"
    return "EXIT"


class WindowsAutostartManager:
    KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
    VALUE_NAME = "PR-OS"

    def __init__(
        self,
        executable: Path | str | None = None,
        *,
        registry: Any | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.executable = Path(executable or sys.executable).resolve()
        self.platform_name = platform_name or os.name
        if registry is None and self.platform_name == "nt":
            import winreg

            registry = winreg
        self.registry = registry

    @property
    def command(self) -> str:
        return subprocess.list2cmdline([str(self.executable)])

    def apply(self, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ValueError("AUTOSTART_ENABLED_INVALID")
        base = {
            "schema_version": "P08AutostartMutationReceipt-v1",
            "enabled_requested": enabled,
            "value_name": self.VALUE_NAME,
            "credential_value_reads": 0,
            "external_network_calls": 0,
        }
        if self.platform_name != "nt" or self.registry is None:
            return {**base, "mutation_count": 0, "status": "UNSUPPORTED_PLATFORM"}

        registry = self.registry
        access = int(registry.KEY_QUERY_VALUE) | int(registry.KEY_SET_VALUE)
        key = registry.CreateKeyEx(registry.HKEY_CURRENT_USER, self.KEY_PATH, 0, access)
        try:
            current: str | None
            try:
                current, _kind = registry.QueryValueEx(key, self.VALUE_NAME)
            except FileNotFoundError:
                current = None
            if enabled:
                if current == self.command:
                    return {**base, "mutation_count": 0, "status": "ALREADY_CONFIGURED"}
                registry.SetValueEx(key, self.VALUE_NAME, 0, registry.REG_SZ, self.command)
                return {**base, "mutation_count": 1, "status": "ENABLED"}
            if current is None:
                return {**base, "mutation_count": 0, "status": "ALREADY_DISABLED"}
            if current != self.command:
                return {
                    **base,
                    "mutation_count": 0,
                    "status": "CONFLICT_PRESERVED",
                    "reason_code": "AUTOSTART_VALUE_OWNERSHIP_MISMATCH",
                }
            registry.DeleteValue(key, self.VALUE_NAME)
            return {**base, "mutation_count": 1, "status": "DISABLED"}
        finally:
            registry.CloseKey(key)


__all__ = ["WindowsAutostartManager", "decide_close_action"]
