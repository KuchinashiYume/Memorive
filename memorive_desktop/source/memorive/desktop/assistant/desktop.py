"""The desk assistant's desktop side: its window, its preferences and shortcuts, and the notices it
shows. AssistantDesktop holds them; ProductApi (memorive/desktop/api/) builds it at start; api/assistant.py keeps the names the
window, app.py and the research runtime call (assistant_bootstrap, assistant_move_window ...) as
forwarders to it.

  preferences   load_preferences, preference_get, preference_save; the file is
                assistant_preferences_v3.json in the profile's UI state, upgraded from v1 and v2
  shortcuts     shortcut, launch_target, target_status; default_shortcut_entries seeds a first run
  the window    attach_window, initial_placement, move_window, and showing or hiding it
  notices       bootstrap and ui_signal project the current state; claim_presentation_delivery,
                record_presentation_receipt and ack_delivery carry one notice to the window once

The controller, placement, shortcut registry and delivery queue it uses are the other modules of
this package.
"""
from __future__ import annotations

from copy import deepcopy
import ctypes
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping
from urllib.parse import urlsplit
import uuid
import webbrowser

from memorive.platform.clock import utc_now as _utc_now
from memorive.platform.json_files import lenient_sha256 as _sha256_json, write_json_indented


def assistant_visibility_after_preference_save(
    *, current_visible: bool, enabled: bool
) -> bool:
    """Saving preferences may disable the pet, but must never unhide it."""

    if not isinstance(current_visible, bool) or not isinstance(enabled, bool):
        raise ValueError("ASSISTANT_VISIBILITY_AXIS_INVALID")
    return current_visible and enabled


def _local_assistant_projection(*, visible: bool, refresh_unavailable: bool) -> dict[str, Any]:
    reason_code = (
        "SERVICE_REFRESH_UNAVAILABLE"
        if visible and refresh_unavailable
        else "NO_ACTIVE_REAL_JOB"
        if visible
        else "USER_HIDDEN"
    )
    summary = (
        "桌面助手已显示；任务状态将在服务恢复后自动刷新"
        if visible and refresh_unavailable
        else "桌面助手待命中"
        if visible
        else "桌面助手已隐藏"
    )
    return {
        "schema_version": "DesktopAssistantAssistantRuntimeProjection-v1",
        "presentation": {
            "display_state": "IDLE" if visible else "HIDDEN",
            "attention_state": "NONE",
            "animation_state": "STATIC",
            "safe_summary": summary,
            "reason_code": reason_code,
            "locator": None,
            "reduced_motion": False,
        },
        "source_track": "LOCAL_NATIVE_CONTROL",
        "real_job_count": 0,
        "sample_job_count": 0,
        "real_message_count": 0,
        "projected_input_count": 0,
        "sample_job_consumed": False,
        "sample_success_used_as_real_success": False,
        "native_presentation_status": "LOCAL_STATIC",
        "external_network_calls": 0,
        "external_model_calls": 0,
        "credential_value_reads": 0,
        "shell_command_calls": 0,
        "system_autostart_writes": 0,
        "status": "DEGRADED" if refresh_unavailable else "READY",
    }


def suppress_replayed_terminal_animation(
    presentation: Mapping[str, Any],
) -> dict[str, Any]:
    """Static snapshots may describe a terminal state but never replay its motion.

    SUCCESS/ERROR motion is reserved for a fresh durable transition delivery.
    Periodic bootstrap, restart and cosmetic blink refreshes therefore retain the
    terminal attention/base state while projecting a STATIC animation.
    """

    accepted = deepcopy(dict(presentation))
    terminal_reasons = {
        "JOB_TERMINAL_SUCCESS",
        "JOB_TERMINAL_ERROR",
        "MESSAGE_COMPLETED",
    }
    if (
        str(accepted.get("reason_code") or "") in terminal_reasons
        and str(accepted.get("animation_state") or "").upper() in {"SUCCESS", "ERROR"}
    ):
        accepted["animation_state"] = "STATIC"
    return accepted


class AssistantDesktop:
    """The desk assistant of one desktop run. ProductApi passes in what it shares: the control store,
    the service, its lock, the messages and current-task controllers, the refinement task store, and
    three of its own functions. ProductApi.attach_window sets main_window once the main window exists;
    attach_window here attaches the assistant's own window."""

    # Class methods write the preferences file before an instance exists.
    write_json_file = staticmethod(write_json_indented)

    def __init__(
        self,
        *,
        ui_root: Path,
        control_store: Any,
        service: Any,
        lock: Any,
        messages: Any,
        current_task: Any,
        refinement_tasks: Any,
        bring_main_window_to_foreground: Any,
        sample_job: Any,
        sample_message: Any,
    ) -> None:
        self.ui_root = ui_root
        self.control_store = control_store
        self.service = service
        self.lock = lock
        self.messages = messages
        self.current_task = current_task
        self.refinement_tasks = refinement_tasks
        self.bring_main_window_to_foreground = bring_main_window_to_foreground
        self.sample_job = sample_job
        self.sample_message = sample_message
        self.main_window: Any | None = None
        self.window: Any | None = None
        self.presenter: Any | None = None
        self.move_count = 0
        # deliveries and delivery_session are set by start_deliveries.
        self.presentation_receipt_path = ui_root / "assistant_presentation_receipt_v1.json"
        legacy_assistant_preferences_path = ui_root / "assistant_preferences_v1.json"
        legacy_assistant_preferences_v2_path = ui_root / "assistant_preferences_v2.json"
        self.preferences_path = ui_root / "assistant_preferences_v3.json"
        if not self.preferences_path.exists():
            enabled = True
            reminder_scope = "IMPORTANT_AND_COMPLETE"
            hide_sensitive_names = False
            shortcut_entries = self.default_shortcut_entries()
            legacy_preferences: dict[str, Any] = {}
            source_path = next(
                (
                    path
                    for path in (
                        legacy_assistant_preferences_v2_path,
                        legacy_assistant_preferences_path,
                    )
                    if path.is_file()
                ),
                None,
            )
            if source_path is not None:
                try:
                    legacy_preferences = json.loads(
                        source_path.read_text(encoding="utf-8")
                    )
                    if isinstance(legacy_preferences.get("enabled"), bool):
                        enabled = legacy_preferences["enabled"]
                    if legacy_preferences.get("reminder_scope") in {
                        "IMPORTANT_ONLY",
                        "IMPORTANT_AND_COMPLETE",
                        "INCLUDE_ORDINARY",
                    }:
                        reminder_scope = legacy_preferences["reminder_scope"]
                    if isinstance(legacy_preferences.get("hide_sensitive_names"), bool):
                        hide_sensitive_names = legacy_preferences["hide_sensitive_names"]
                except (OSError, json.JSONDecodeError, AttributeError):
                    legacy_preferences = {}
            legacy_labels = {
                "CODEX": "Codex",
                "CLAUDE_CODE": "Claude Code",
                "CC_SWITCH": "CC Switch",
            }
            legacy_slots = legacy_preferences.get("shortcut_slots", [])
            if not isinstance(legacy_slots, list):
                legacy_slots = []
            for index, target_id in enumerate(legacy_slots[:3]):
                if target_id not in legacy_labels:
                    continue
                target_path = self._legacy_target_path(target_id)
                if target_path is not None:
                    shortcut_entries[index] = {
                        "name": legacy_labels[target_id],
                        "target": str(target_path),
                    }
            self.write_json_file(
                self.preferences_path,
                {
                    "schema_version": "DesktopAssistantPreferences-v3",
                    "enabled": enabled,
                    "shortcut_entries": shortcut_entries,
                    "reminder_scope": reminder_scope,
                    "hide_sensitive_names": hide_sensitive_names,
                },
            )
        else:
            self._seed_blank_shortcut_file(self.preferences_path)
        assistant_module = importlib.import_module("memorive.desktop.assistant")
        placement_module = importlib.import_module("memorive.desktop.assistant.placement")
        shortcut_module = importlib.import_module("memorive.desktop.assistant.shortcuts")

        def assistant_handler(request: dict[str, Any]) -> dict[str, Any]:
            action_id = request["action_id"]
            routes = {
                "SHOW_DESKTOP_ASSISTANT": {"route": "assistant", "visibility": "visible"},
                "TOGGLE_DESKTOP_ASSISTANT": {"route": "assistant", "visibility": "toggle"},
                "OPEN_Desktop_MAIN": {"focus_existing_main": True, "preserve_route": True},
                "OPEN_DESKTOP_ASSISTANT_SETTINGS": {"route": "settings", "section": "behavior"},
                "HIDE_DESKTOP_ASSISTANT": {"route": "assistant", "visibility": "hidden"},
                "OPEN_MESSAGE_DETAIL": {"route": "messages", "locator": request.get("locator")},
                "RESTORE_DEFAULT_PLACEMENT": {"route": "assistant", "placement": "default_requested"},
            }
            return {"accepted": True, **routes[action_id]}

        handlers = {
            action_id: assistant_handler
            for action_id in shortcut_module.FIXED_INTERNAL_ACTION_IDS
        }
        shortcuts = assistant_module.SafeShortcutRegistry(handlers)
        self.controller = assistant_module.DesktopAssistantController(self.control_store, shortcuts)
        persisted_assistant = self.load_preferences()
        self.controller.set_control(
            enabled=bool(persisted_assistant["enabled"]),
            visible=bool(persisted_assistant["enabled"]),
        )
        self.placement_class = assistant_module.AssistantWindowPlacement
        self.clamp_placement = placement_module.clamp_placement
        self.visible_bounds = placement_module.visible_bounds
        self.areas = self._detect_work_areas(placement_module.WorkArea)

    def start_deliveries(self) -> None:
        """The queue that carries each notice to the window once. It starts with every message that
        already exists marked as known, so those are not delivered as new."""
        from memorive.desktop.assistant.delivery import AssistantDeliveryQueue
        self.delivery_session = uuid.uuid4().hex
        self.deliveries = AssistantDeliveryQueue(self.ui_root / "assistant_deliveries_v1.json")
        self.deliveries.seed('message:' + row['message_id'] for row in self.messages.call('messages.list',{})['rows'])

    @staticmethod
    def default_shortcut_entries(
        *,
        home: Path | None = None,
        workspace_candidates: tuple[Path, ...] | None = None,
    ) -> list[dict[str, str]]:
        """Seed useful first-run shortcuts without hard-coding another PC's paths."""

        resolved_home = (home or Path.home()).resolve(strict=False)
        candidates = workspace_candidates
        if candidates is None:
            candidates = tuple(
                candidate
                for candidate in (
                    Path(os.environ["MEMORIVE_WORKSPACE_ROOT"])
                    if os.environ.get("MEMORIVE_WORKSPACE_ROOT")
                    else None,
                    Path.cwd(),
                )
                if candidate is not None
            )
        workspace = next(
            (
                candidate.resolve(strict=False)
                for candidate in candidates
                if candidate.resolve(strict=False).is_dir()
            ),
            None,
        )
        cc_switch = (resolved_home / "CC switch").resolve(strict=False)
        return [
            {
                "name": "Memorive 文件夹" if workspace is not None else "",
                "target": str(workspace) if workspace is not None else "",
            },
            {
                "name": "CC SWITCH" if cc_switch.is_dir() else "",
                "target": str(cc_switch) if cc_switch.is_dir() else "",
            },
            {"name": "GPT网站", "target": "https://chatgpt.com/"},
        ]

    @classmethod
    def _seed_blank_shortcut_file(cls, path: Path) -> bool:
        """Fill the shipped empty three-slot state without replacing user edits."""

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        entries = payload.get("shortcut_entries")
        if not isinstance(entries, list) or len(entries) != 3:
            return False
        if any(
            isinstance(entry, dict)
            and (str(entry.get("name") or "").strip() or str(entry.get("target") or "").strip())
            for entry in entries
        ):
            return False
        payload["shortcut_entries"] = cls.default_shortcut_entries()
        cls.write_json_file(path, payload)
        return True

    @staticmethod
    def _legacy_target_path(target_id: str) -> Path | None:
        executable_names = {
            "CODEX": ("Codex.exe", "codex.exe"),
            "CLAUDE_CODE": ("claude.exe",),
            "CC_SWITCH": ("CC-Switch.exe", "cc-switch.exe", "CCSwitch.exe"),
        }
        if target_id not in executable_names:
            return None
        for name in executable_names[target_id]:
            resolved = shutil.which(name)
            if resolved and Path(resolved).is_file():
                return Path(resolved).resolve()
        local_app_data = os.environ.get("LOCALAPPDATA")
        program_files = [
            value
            for value in (
                os.environ.get("ProgramFiles"),
                os.environ.get("ProgramFiles(x86)"),
            )
            if value
        ]
        candidates: dict[str, list[Path]] = {
            "CODEX": [],
            "CLAUDE_CODE": [],
            "CC_SWITCH": [],
        }
        if local_app_data:
            local_root = Path(local_app_data)
            candidates["CODEX"].extend(
                (
                    local_root / "Programs" / "Codex" / "Codex.exe",
                    local_root / "Programs" / "OpenAI" / "Codex" / "Codex.exe",
                )
            )
            candidates["CLAUDE_CODE"].append(
                local_root / "Programs" / "Claude Code" / "claude.exe"
            )
            candidates["CC_SWITCH"].extend(
                (
                    local_root / "Programs" / "CC-Switch" / "CC-Switch.exe",
                    local_root / "Programs" / "CC Switch" / "CC Switch.exe",
                )
            )
        for root_value in program_files:
            root = Path(root_value)
            candidates["CODEX"].append(root / "Codex" / "Codex.exe")
            candidates["CLAUDE_CODE"].append(root / "Claude Code" / "claude.exe")
            candidates["CC_SWITCH"].append(root / "CC-Switch" / "CC-Switch.exe")
        return next(
            (candidate.resolve() for candidate in candidates[target_id] if candidate.is_file()),
            None,
        )

    def load_preferences(self) -> dict[str, Any]:
        try:
            value = json.loads(self.preferences_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("ASSISTANT_PREFERENCES_UNAVAILABLE") from error
        expected = {
            "schema_version",
            "enabled",
            "shortcut_entries",
            "reminder_scope",
            "hide_sensitive_names",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise RuntimeError("ASSISTANT_PREFERENCES_FIELDS_INVALID")
        if (
            value.get("schema_version") != "DesktopAssistantPreferences-v3"
            or not isinstance(value.get("enabled"), bool)
            or not isinstance(value.get("hide_sensitive_names"), bool)
        ):
            raise RuntimeError("ASSISTANT_PREFERENCES_INVALID")
        try:
            value["shortcut_entries"] = self._normalize_shortcut_entries(
                value.get("shortcut_entries")
            )
        except ValueError as error:
            raise RuntimeError("ASSISTANT_SHORTCUT_ENTRIES_INVALID") from error
        if value.get("reminder_scope") not in {
            "IMPORTANT_ONLY",
            "IMPORTANT_AND_COMPLETE",
            "INCLUDE_ORDINARY",
        }:
            raise RuntimeError("ASSISTANT_REMINDER_SCOPE_INVALID")
        return value

    @classmethod
    def preferences_for_reset_scope(
        cls,
        scope: str,
        current: Mapping[str, Any],
        *,
        default_shortcuts: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        candidate = deepcopy(dict(current))
        if scope == "notice":
            candidate["reminder_scope"] = "IMPORTANT_AND_COMPLETE"
            candidate["hide_sensitive_names"] = False
        elif scope == "behavior":
            candidate["enabled"] = True
            candidate["shortcut_entries"] = deepcopy(
                default_shortcuts
                if default_shortcuts is not None
                else cls.default_shortcut_entries()
            )
        elif scope == "appearance":
            candidate["enabled"] = True
        else:
            raise ValueError(f"ASSISTANT_RESET_SCOPE_INVALID:{scope}")
        return candidate

    @staticmethod
    def preference_save_params(
        preferences: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "enabled": preferences["enabled"],
            "shortcut_entries": deepcopy(preferences["shortcut_entries"]),
            "reminder_scope": preferences["reminder_scope"],
            "hide_sensitive_names": preferences["hide_sensitive_names"],
        }

    def preference_get(self) -> dict[str, Any]:
        return {
            "schema_version": "DesktopAssistantPreferencesProjection-v3",
            "preferences": self.load_preferences(),
            "assistant_visible": self._window_is_visible(),
            "status": "PASS",
        }

    def preference_save(self, params: dict[str, Any]) -> dict[str, Any]:
        if set(params) != {
            "enabled",
            "shortcut_entries",
            "reminder_scope",
            "hide_sensitive_names",
        }:
            raise ValueError("ASSISTANT_PREFERENCES_SAVE_PARAMS_INVALID")
        candidate = {
            "schema_version": "DesktopAssistantPreferences-v3",
            "enabled": params["enabled"],
            "shortcut_entries": self._normalize_shortcut_entries(
                params["shortcut_entries"]
            ),
            "reminder_scope": params["reminder_scope"],
            "hide_sensitive_names": params["hide_sensitive_names"],
        }
        if not isinstance(candidate["enabled"], bool) or not isinstance(
            candidate["hide_sensitive_names"], bool
        ):
            raise ValueError("ASSISTANT_ENABLED_INVALID")
        if candidate["reminder_scope"] not in {
            "IMPORTANT_ONLY",
            "IMPORTANT_AND_COMPLETE",
            "INCLUDE_ORDINARY",
        }:
            raise ValueError("ASSISTANT_REMINDER_SCOPE_INVALID")
        try:
            current_entries = self.load_preferences()["shortcut_entries"]
        except RuntimeError:
            # No readable file yet: there are no earlier names to retain.
            current_entries = []
        for index, entry in enumerate(candidate["shortcut_entries"]):
            # Retain existing 21-40 character labels; apply the new limit on edit.
            existing = current_entries[index]["name"] if index < len(current_entries) else None
            if len(entry["name"]) > 20 and entry["name"] != existing:
                raise ValueError("ASSISTANT_SHORTCUT_NAME_TOO_LONG")
        try:
            current_visible = self._window_is_visible()
        except (AttributeError, RuntimeError):
            current_visible = bool(
                getattr(self.controller, "visible", candidate["enabled"])
            )
        target_visible = assistant_visibility_after_preference_save(
            current_visible=current_visible,
            enabled=candidate["enabled"],
        )
        self.write_json_file(self.preferences_path, candidate)
        self.controller.set_control(
            enabled=candidate["enabled"],
            visible=target_visible,
        )
        if target_visible != current_visible:
            visibility = self._set_window_visibility(target_visible)
        else:
            visibility = {
                "schema_version": "DesktopAssistantAssistantNativeVisibilityReceipt-v1",
                "visible": target_visible,
                "native_window_ready": self.window is not None
                if hasattr(self, "window")
                else False,
                "brought_to_front": False,
                "preserved": True,
                "status": "PASS",
            }
        return {
            "schema_version": "DesktopAssistantPreferencesSaveReceipt-v3",
            "preferences": candidate,
            "assistant_window_visibility": visibility,
            "status": "PASS",
        }

    @classmethod
    def _normalize_shortcut_entries(cls, entries: object) -> list[dict[str, str]]:
        if not isinstance(entries, list) or len(entries) != 3:
            raise ValueError("ASSISTANT_SHORTCUT_ENTRIES_INVALID")
        normalized_entries: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"name", "target"}:
                raise ValueError("ASSISTANT_SHORTCUT_ENTRY_INVALID")
            name = entry.get("name")
            target = entry.get("target")
            if not isinstance(name, str) or not isinstance(target, str):
                raise ValueError("ASSISTANT_SHORTCUT_ENTRY_INVALID")
            name = name.strip()
            target = target.strip()
            if (
                len(name) > 40
                or any(character in name for character in ("\x00", "\r", "\n"))
                or bool(name) != bool(target)
            ):
                raise ValueError("ASSISTANT_SHORTCUT_ENTRY_INVALID")
            if target:
                target = str(cls._target_descriptor(target)["target"])
            normalized_entries.append({"name": name, "target": target})
        return normalized_entries

    @staticmethod
    def _target_descriptor(target: str) -> dict[str, Any]:
        if not isinstance(target, str):
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
        normalized = target.strip()
        if (
            not normalized
            or len(normalized) > 2048
            or any(character in normalized for character in ("\x00", "\r", "\n"))
        ):
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
        if normalized.startswith('"') and normalized.endswith('"') and normalized.count('"') == 2:
            normalized = normalized[1:-1].strip()
        if not normalized or '"' in normalized:
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")

        windows_path = bool(re.match(r"^[A-Za-z]:[\\/]", normalized)) or normalized.startswith("\\\\")
        parsed = urlsplit(normalized)
        if not windows_path and parsed.scheme:
            if (
                parsed.scheme.lower() not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
            try:
                parsed.port
            except ValueError as error:
                raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE") from error
            return {"kind": "URL", "target": normalized, "path": None}

        if re.search(
            r"(?i)\.(?:exe|com|bat|cmd|ps1|vbs|js|jse|wsf|wsh|hta|msi|reg|scr|lnk|url)['\"]?\s+",
            normalized,
        ):
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
        if any(character in normalized for character in ("<", ">", "|", "?", "*")):
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
        if len(normalized) > 2 and ":" in normalized[2:]:
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
        path = Path(normalized)
        if not path.is_absolute():
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
        resolved = path.resolve(strict=False)
        unsafe_suffixes = {
            ".bat", ".cmd", ".com", ".hta", ".js", ".jse", ".lnk", ".msi",
            ".ps1", ".reg", ".scr", ".url", ".vbs", ".wsf", ".wsh",
        }
        if resolved.suffix.lower() in unsafe_suffixes:
            raise ValueError("ASSISTANT_SHORTCUT_TARGET_UNSAFE")
        if resolved.exists() and resolved.is_dir():
            kind = "DIRECTORY"
        elif resolved.suffix.lower() == ".exe":
            kind = "PROGRAM"
        else:
            kind = "FILE"
        return {"kind": kind, "target": str(resolved), "path": resolved}

    @staticmethod
    def _detect_work_areas(work_area_class: type) -> tuple[Any, ...]:
        if os.name != "nt":
            return (work_area_class("DISPLAY1", 0, 0, 1920, 1040, primary=True),)

        from ctypes import wintypes

        class Rect(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", Rect),
                ("rcWork", Rect),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32),
            ]

        user32 = ctypes.windll.user32
        monitor_rows: list[Any] = []
        monitor_enum_proc = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HANDLE,
            wintypes.HDC,
            ctypes.POINTER(Rect),
            wintypes.LPARAM,
        )

        def monitor_dpi_percent(handle: Any) -> int:
            try:
                shcore = ctypes.windll.shcore
                scale_factor = ctypes.c_int(100)
                shcore.GetScaleFactorForMonitor.argtypes = [
                    wintypes.HANDLE,
                    ctypes.POINTER(ctypes.c_int),
                ]
                shcore.GetScaleFactorForMonitor.restype = ctypes.c_long
                if int(
                    shcore.GetScaleFactorForMonitor(
                        handle, ctypes.byref(scale_factor)
                    )
                ) == 0 and 50 <= int(scale_factor.value) <= 500:
                    return int(scale_factor.value)
                dpi_x = ctypes.c_uint(96)
                dpi_y = ctypes.c_uint(96)
                shcore.GetDpiForMonitor.argtypes = [
                    wintypes.HANDLE,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_uint),
                    ctypes.POINTER(ctypes.c_uint),
                ]
                shcore.GetDpiForMonitor.restype = ctypes.c_long
                if int(
                    shcore.GetDpiForMonitor(
                        handle, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
                    )
                ) == 0:
                    return max(50, min(500, int(round(dpi_x.value * 100 / 96))))
            except (AttributeError, OSError):
                pass
            try:
                user32.GetDpiForSystem.restype = wintypes.UINT
                dpi = int(user32.GetDpiForSystem())
                if dpi > 0:
                    return max(50, min(500, int(round(dpi * 100 / 96))))
            except (AttributeError, OSError):
                pass
            return 100

        def append_monitor(handle, _device_context, _bounds, _data) -> bool:
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                return True
            device = str(info.szDevice).strip("\\.\x00")
            monitor_id = device if device else f"DISPLAY{len(monitor_rows) + 1}"
            area = info.rcWork
            if area.right > area.left and area.bottom > area.top:
                monitor_rows.append(
                    work_area_class(
                        monitor_id,
                        int(area.left),
                        int(area.top),
                        int(area.right),
                        int(area.bottom),
                        primary=bool(info.dwFlags & 1),
                        dpi_percent=monitor_dpi_percent(handle),
                    )
                )
            return True

        callback = monitor_enum_proc(append_monitor)
        user32.EnumDisplayMonitors(None, None, callback, 0)
        if monitor_rows:
            return tuple(monitor_rows)

        rect = Rect()
        spi_get_work_area = 0x0030
        if user32.SystemParametersInfoW(spi_get_work_area, 0, ctypes.byref(rect), 0):
            return (
                work_area_class(
                    "DISPLAY1",
                    int(rect.left),
                    int(rect.top),
                    int(rect.right),
                    int(rect.bottom),
                    primary=True,
                    dpi_percent=monitor_dpi_percent(None),
                ),
            )
        width = int(user32.GetSystemMetrics(0)) or 1920
        height = int(user32.GetSystemMetrics(1)) or 1080
        return (
            work_area_class(
                "DISPLAY1",
                0,
                0,
                width,
                height,
                primary=True,
                dpi_percent=monitor_dpi_percent(None),
            ),
        )

    def _refresh_display_topology(self) -> tuple[Any, ...]:
        if not self.areas:
            raise RuntimeError("ASSISTANT_WORK_AREAS_EMPTY")
        work_area_class = type(self.areas[0])
        self.areas = self._detect_work_areas(work_area_class)
        return self.areas

    def _inputs(self) -> tuple[list[dict[str, Any]], int, int, int]:
        job_projection = self.service.call("list_jobs", {})
        rows = job_projection.get("jobs", []) if isinstance(job_projection, dict) else []
        sample_job_ids = frozenset(getattr(self.current_task, "sample_job_ids", ()))
        real_jobs: list[dict[str, Any]] = []
        sample_job_count = 0
        for raw in rows if isinstance(rows, list) else []:
            if not isinstance(raw, dict):
                continue
            if raw.get("job_id") in sample_job_ids or self.sample_job(raw):
                sample_job_count += 1
                continue
            projected = dict(raw)
            projected.setdefault("source_kind", "job")
            projected.setdefault("locator", self._job_locator(projected.get("job_id")))
            if projected.get("locator") is None:
                projected.pop("locator", None)
            real_jobs.append(projected)

        for raw in self.refinement_tasks.list_jobs():
            if not isinstance(raw, dict) or raw.get("control_state") != "RUNNING":
                continue
            projected = {
                "source_kind": "job",
                "job_id": raw.get("job_id"),
                "state": "RUNNING",
                "control_state": "RUNNING",
                "updated_at": raw.get("updated_at", ""),
                "event_sequence": int(raw.get("version", 0)),
                "locator": self._job_locator(raw.get("job_id")),
            }
            if projected["locator"] is None:
                projected.pop("locator")
            real_jobs.append(projected)

        message_projection = self.messages.call("messages.list", {})
        message_rows = (
            message_projection.get("rows", []) if isinstance(message_projection, dict) else []
        )
        reminder_scope = self.load_preferences()["reminder_scope"]
        allowed_severities = {
            "IMPORTANT_ONLY": {"RED", "YELLOW"},
            "IMPORTANT_AND_COMPLETE": {"RED", "YELLOW", "GREEN"},
            "INCLUDE_ORDINARY": {"RED", "YELLOW", "GREEN", "BLUE"},
        }[reminder_scope]
        real_messages: list[dict[str, Any]] = []
        for raw in message_rows if isinstance(message_rows, list) else []:
            if not isinstance(raw, dict) or self.sample_message(raw):
                continue
            unread = raw.get("read_state") == "UNREAD" or raw.get("read") is False
            current = raw.get("collection_state", "CURRENT") == "CURRENT"
            if not unread or not current:
                continue
            severity = str(raw.get("severity", "BLUE")).upper()
            if severity not in allowed_severities:
                continue
            if severity in {"BLUE", "GREEN"} and any(
                row.get("control_state") == "RUNNING" for row in real_jobs
            ):
                continue
            state = {
                "RED": "ERROR",
                "YELLOW": "WAITING_USER",
                "GREEN": "COMPLETED",
                "BLUE": "RUNNING",
            }.get(severity, "RUNNING")
            source = raw.get("source_event_ref")
            locator = raw.get("target_locator")
            if not locator and isinstance(source, dict):
                locator = source.get("locator")
            projected = {
                "source_kind": "message",
                "message_id": raw.get("message_id"),
                "state": state,
                "updated_at": raw.get("last_seen_at", raw.get("updated_at", "")),
                "event_sequence": raw.get("event_sequence", raw.get("sequence", 0)),
            }
            if isinstance(locator, str) and locator.startswith("memorive://"):
                projected["locator"] = locator
            real_messages.append(projected)
            deliveries = getattr(self, 'deliveries', None)
            if deliveries is not None and severity in {'BLUE','RED'} and projected.get('message_id'):
                deliveries.enqueue('message:' + projected['message_id'], {
                    'display_state':'NOTIFYING', 'animation_state':'ERROR' if severity=='RED' else 'MESSAGE',
                    'reason_code':'MESSAGE_NEEDS_ATTENTION' if severity=='RED' else 'MESSAGE_UPDATE',
                    'attention_state':'ERROR' if severity=='RED' else 'INFO',
                    'safe_summary':'有消息需要处理' if severity=='RED' else '收到新消息',
                    'locator':projected.get('locator'),
                })
        return real_jobs + real_messages, len(real_jobs), sample_job_count, len(real_messages)

    @staticmethod
    def _job_locator(job_id: Any) -> str | None:
        rendered = str(job_id or "")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", rendered):
            return f"memorive://job/{rendered}"
        return None

    def _activate_main_route(self, route: str) -> None:
        if route not in {"home", "settings", "messages"}:
            raise ValueError("ASSISTANT_MAIN_ROUTE_INVALID")
        if self.main_window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        self.main_window.evaluate_js(
            "window.__Desktop_INTEGRATED_APP__.activate(" + json.dumps(route) + ")"
        )
        self.bring_main_window_to_foreground()

    def _focus_main_window(self) -> dict[str, Any]:
        if self.main_window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        # Restoring the native window is the primary action.  Route inspection
        # is diagnostic only and must never keep a hidden main window hidden
        # when WebView2 is suspended, unhealthy, or still resuming.
        self.bring_main_window_to_foreground()
        route_projection: Any = None
        route_observation_status = "UNAVAILABLE"
        route_observation_error_type: str | None = None
        try:
            route_projection = self.main_window.evaluate_js(
                "window.__Desktop_INTEGRATED_APP__ && window.__Desktop_INTEGRATED_APP__.inspect()"
            )
            if isinstance(route_projection, dict) and isinstance(
                route_projection.get("active_route"), str
            ):
                route_observation_status = "OBSERVED"
        except Exception as error:
            route_observation_error_type = type(error).__name__
        route = (
            route_projection.get("active_route")
            if isinstance(route_projection, dict)
            else None
        )
        return {
            "schema_version": "DesktopAssistantMainWindowFocusReceipt-v1",
            "route_before": route,
            "route_after": route,
            "route_preserved": True,
            "route_observation_status": route_observation_status,
            "route_observation_error_type": route_observation_error_type,
            "navigation_invoked": False,
            "window_shown": True,
            "window_focused": True,
            "status": "PASS",
        }

    def attach_window(self, window: Any) -> None:
        if self.window is not None:
            raise RuntimeError("ASSISTANT_WINDOW_ALREADY_ATTACHED")
        self.window = window

    def attach_presenter(self, presenter: Any) -> None:
        if self.presenter is not None:
            raise RuntimeError("ASSISTANT_PRESENTER_ALREADY_ATTACHED")
        if not callable(presenter):
            raise ValueError("ASSISTANT_PRESENTER_INVALID")
        self.presenter = presenter

    def initial_placement(self) -> dict[str, Any]:
        with self.lock:
            native = (
                getattr(self.window, "native", None)
                if self.window is not None
                else None
            )
            if native is not None:
                self._refresh_display_topology()
            placement_keys = (
                "assistant.monitor_id",
                "assistant.x",
                "assistant.y",
                "assistant.width",
                "assistant.height",
                "assistant.dpi_percent",
            )
            placement_filters = self.control_store.load()["state"]["filters"]
            placement_persisted = all(
                key in placement_filters for key in placement_keys
            )
            stored = self.controller.load_placement(self.areas)
            area = next(
                (
                    row
                    for row in self.areas
                    if row.monitor_id == stored.monitor_id
                ),
                next(
                    (row for row in self.areas if row.primary),
                    self.areas[0],
                ),
            )
            x = stored.x
            y = stored.y
            if stored.width == 224 and stored.height == 244:
                # One-time migration from the assistant-placement transparent host. The
                # old persisted x/y referred to the 224x244 form; translate
                # them to its visible pet origin before removing that host.
                legacy_left, legacy_top, _, _ = self.visible_bounds(stored)
                x += legacy_left
                y += legacy_top
            candidate = self.placement_class(
                monitor_id=area.monitor_id,
                x=x,
                y=y,
                width=90,
                height=90,
                dpi_percent=int(area.dpi_percent),
            )
            accepted = self.clamp_placement(candidate, self.areas)
            if accepted != stored or not placement_persisted:
                self.controller.persist_placement(accepted, self.areas)
            return accepted.to_dict()

    def bootstrap(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        accepted = dict(request or {})
        if set(accepted) - {"reduced_motion"}:
            raise ValueError("ASSISTANT_BOOTSTRAP_FIELDS_INVALID")
        reduced_motion = accepted.get("reduced_motion", False)
        if not isinstance(reduced_motion, bool):
            raise ValueError("ASSISTANT_REDUCED_MOTION_INVALID")
        with self.lock:
            self.controller.set_control(reduced_motion=reduced_motion)
            health = self.service.call("service.health", {})
            inputs, real_job_count, sample_job_count, real_message_count = self._inputs()
            presentation = suppress_replayed_terminal_animation(
                self.controller.refresh(service_health=health, jobs=inputs)
            )
            assistant_preferences = self.load_preferences()
            deliveries = getattr(self, 'deliveries', None)
            pending = deliveries.peek() if deliveries is not None else None
            if assistant_preferences['enabled'] and presentation.get('display_state') not in {'HIDDEN','DISABLED'}:
                if pending:
                    presentation = {**presentation, **pending}
                elif deliveries is not None and presentation.get('reason_code') in {'MESSAGE_UPDATE','MESSAGE_NEEDS_ATTENTION'}:
                    presentation = dict(presentation, animation_state='STATIC', display_state='IDLE',
                                        reason_code='MESSAGE_ALREADY_PRESENTED', attention_state='NONE')
            if presentation.get("reason_code") == "NO_ACTIVE_JOB":
                presentation = {
                    **presentation,
                    "safe_summary": "桌面助手待命中",
                    "reason_code": "NO_ACTIVE_REAL_JOB",
                }
            presenter = self.presenter
            window_attached = self.window is not None
            move_count = self.move_count
        # The native presenter writes its durable STARTED/COMPLETED receipt
        # through this ProductApi.  Never invoke that external callback while
        # holding ``self._lock`` or the receipt write will deadlock trying to
        # reacquire the same lock from the WinForms UI thread.
        native_presentation_status = "NOT_ATTACHED"
        if presenter is not None:
            try:
                presenter(dict(presentation))
                native_presentation_status = "APPLIED"
            except Exception as error:
                native_presentation_status = "ERROR:" + type(error).__name__
        return {
            "schema_version": "DesktopAssistantAssistantRuntimeProjection-v1",
            "presentation": presentation,
            "source_track": "REAL_LOCAL_SERVICE",
            "real_job_count": real_job_count,
            "sample_job_count": sample_job_count,
            "real_message_count": real_message_count,
            "projected_input_count": len(inputs),
            "reminder_scope": assistant_preferences["reminder_scope"],
            "hide_sensitive_names": assistant_preferences["hide_sensitive_names"],
            "sample_job_count_available_in_test_track": sample_job_count,
            "sample_job_consumed": False,
            "sample_success_used_as_real_success": False,
            "native_presentation_status": native_presentation_status,
            "window_attached": window_attached,
            "move_count": move_count,
            "external_network_calls": 0,
            "external_model_calls": 0,
            "credential_value_reads": 0,
            "shell_command_calls": 0,
            "system_autostart_writes": 0,
            "status": "READY",
        }

    def ui_signal(self, request: dict[str, Any]) -> dict[str, Any]:
        """Project a bounded local UI acknowledgement through the existing pet presenter."""

        allowed = {"kind", "event_id", "reduced_motion"}
        if (
            not isinstance(request, dict)
            or set(request) - allowed
            or not {"kind", "event_id"} <= set(request)
        ):
            raise ValueError("ASSISTANT_UI_SIGNAL_FIELDS_INVALID")
        kind = request["kind"]
        event_id = request["event_id"]
        reduced_motion = request.get("reduced_motion", False)
        if kind not in {"SAVE_SUCCESS", "ERROR_MESSAGE"}:
            raise ValueError("ASSISTANT_UI_SIGNAL_KIND_INVALID")
        if (
            not isinstance(event_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}", event_id) is None
        ):
            raise ValueError("ASSISTANT_UI_SIGNAL_EVENT_ID_INVALID")
        if not isinstance(reduced_motion, bool):
            raise ValueError("ASSISTANT_UI_SIGNAL_REDUCED_MOTION_INVALID")
        mapping = {
            "SAVE_SUCCESS": (
                "SUCCESS", "SUCCESS", "LOCAL_UI_SAVE_SUCCESS", "设置已保存"
            ),
            "ERROR_MESSAGE": (
                "ERROR", "ERROR", "LOCAL_UI_ERROR_MESSAGE", "收到需要检查的错误消息"
            ),
        }
        animation, attention, reason_code, safe_summary = mapping[kind]
        presentation = {
            "schema_version": "DesktopAssistantAssistantPresentation-v1",
            "display_state": "NOTIFYING",
            "attention_state": attention,
            "animation_state": animation,
            "safe_summary": safe_summary,
            "reason_code": reason_code,
            "locator": "memorive://ui/" + kind.lower() + "/" + event_id,
            "delivery_id": event_id,
            "reduced_motion": reduced_motion,
        }
        with self.lock:
            presenter = self.presenter
        status = "NOT_ATTACHED"
        if presenter is not None:
            try:
                native_receipt = presenter(dict(presentation))
                if isinstance(native_receipt, dict) and str(
                    native_receipt.get("durable_claim_status", "")
                ).startswith("BLOCKED:"):
                    status = "BLOCKED_NATIVE_DELIVERY"
                else:
                    status = "APPLIED"
            except Exception as error:
                status = "ERROR:" + type(error).__name__
        return {
            "schema_version": "DesktopAssistantUiSignalReceipt-v1",
            "kind": kind,
            "event_id": event_id,
            "animation_state": animation,
            "reason_code": reason_code,
            "native_presentation_status": status,
            "external_network_calls": 0,
            "external_model_calls": 0,
            "credential_value_reads": 0,
            "status": status,
        }

    def _set_window_visibility(self, visible: bool) -> dict[str, Any]:
        if self.window is None:
            raise RuntimeError("ASSISTANT_WINDOW_NOT_ATTACHED")
        native = getattr(self.window, "native", None)
        if native is None:
            if visible:
                self.window.show()
            else:
                self.window.hide()
            return {
                "schema_version": "DesktopAssistantAssistantNativeVisibilityReceipt-v1",
                "visible": visible,
                "native_window_ready": False,
                "brought_to_front": visible,
                "status": "PASS",
            }

        import clr
        clr.AddReference("System.Windows.Forms")
        from System import Action

        observed: dict[str, Any] = {}

        def apply_visibility() -> None:
            if visible:
                native.Show()
                native.TopMost = True
                native.BringToFront()
            else:
                native.Hide()
            observed["visible"] = bool(native.Visible)

        native.Invoke(Action(apply_visibility))
        if observed.get("visible") is not visible:
            raise RuntimeError("ASSISTANT_NATIVE_VISIBILITY_MISMATCH")
        return {
            "schema_version": "DesktopAssistantAssistantNativeVisibilityReceipt-v1",
            "visible": observed["visible"],
            "native_window_ready": True,
            "brought_to_front": visible,
            "status": "PASS",
        }

    def _window_is_visible(self) -> bool:
        if self.window is None:
            return bool(self.controller.visible)
        native = getattr(self.window, "native", None)
        if native is None:
            return bool(self.controller.visible)
        import clr
        clr.AddReference("System.Windows.Forms")
        from System import Action

        observed: dict[str, bool] = {}

        def inspect_visibility() -> None:
            observed["visible"] = bool(native.Visible)

        native.Invoke(Action(inspect_visibility))
        return observed["visible"]

    def _move_window_native(
        self,
        x: int,
        y: int,
        *,
        native_width: int | None = None,
        native_height: int | None = None,
    ) -> dict[str, Any]:
        if self.window is None:
            raise RuntimeError("ASSISTANT_WINDOW_NOT_ATTACHED")
        native = getattr(self.window, "native", None)
        if native is None:
            self.window.move(x, y)
            return {
                "native_window_ready": False,
                "x": x,
                "y": y,
                "status": "PASS",
            }

        import clr
        clr.AddReference("System.Windows.Forms")
        from System import Action

        if (native_width is None) is not (native_height is None):
            raise ValueError("ASSISTANT_NATIVE_SIZE_INCOMPLETE")
        observed: dict[str, int] = {}

        def apply_move() -> None:
            if native_width is not None and native_height is not None:
                native.SetBounds(x, y, int(native_width), int(native_height))
            else:
                native.Left = x
                native.Top = y
            native.TopMost = True
            native.BringToFront()
            observed["x"] = int(native.Left)
            observed["y"] = int(native.Top)
            observed["width"] = int(native.Width)
            observed["height"] = int(native.Height)

        native.Invoke(Action(apply_move))
        if observed["x"] != x or observed["y"] != y:
            raise RuntimeError("ASSISTANT_NATIVE_MOVE_MISMATCH")
        if (
            native_width is not None
            and native_height is not None
            and (
                observed["width"] != native_width
                or observed["height"] != native_height
            )
        ):
            raise RuntimeError("ASSISTANT_NATIVE_SIZE_MISMATCH")
        return {
            "native_window_ready": True,
            "x": observed["x"],
            "y": observed["y"],
            "width": observed["width"],
            "height": observed["height"],
            "status": "PASS",
        }

    def move_window(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {"x", "y", "persist"}:
            raise ValueError("ASSISTANT_MOVE_FIELDS_INVALID")
        x = request["x"]
        y = request["y"]
        persist = request["persist"]
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (x, y)):
            raise ValueError("ASSISTANT_MOVE_COORDINATE_INVALID")
        if not isinstance(persist, bool):
            raise ValueError("ASSISTANT_MOVE_PERSIST_INVALID")
        if self.window is None:
            raise RuntimeError("ASSISTANT_WINDOW_NOT_ATTACHED")
        with self.lock:
            current = self.initial_placement()
            candidate = self.placement_class(
                monitor_id=str(current["monitor_id"]),
                x=x,
                y=y,
                width=90,
                height=90,
                dpi_percent=int(current["dpi_percent"]),
            )
            accepted = self.clamp_placement(candidate, self.areas)
            target_area = next(
                (
                    area
                    for area in self.areas
                    if area.monitor_id == accepted.monitor_id
                ),
                None,
            )
            if target_area is not None and accepted.dpi_percent != target_area.dpi_percent:
                accepted = self.clamp_placement(
                    self.placement_class(
                        monitor_id=accepted.monitor_id,
                        x=accepted.x,
                        y=accepted.y,
                        width=90,
                        height=90,
                        dpi_percent=int(target_area.dpi_percent),
                    ),
                    self.areas,
                )
            if persist:
                self.controller.persist_placement(accepted, self.areas)
            visible_left, visible_top, visible_width, visible_height = self.visible_bounds(accepted)
            native_move = self._move_window_native(
                accepted.x,
                accepted.y,
                native_width=visible_width,
                native_height=visible_height,
            )
            self.move_count += 1
            return {
                "schema_version": "DesktopAssistantAssistantWindowMoveReceipt-v1",
                "placement": accepted.to_dict(),
                "persisted": persist,
                "native_move": native_move,
                "visible_bounds": {
                    "left": accepted.x + visible_left,
                    "top": accepted.y + visible_top,
                    "width": visible_width,
                    "height": visible_height,
                    "right": accepted.x + visible_left + visible_width,
                    "bottom": accepted.y + visible_top + visible_height,
                },
                "move_count": self.move_count,
                "status": "PASS",
            }

    def target_status(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {"target"}:
            raise ValueError("ASSISTANT_TARGET_STATUS_REQUEST_INVALID")
        descriptor = self._target_descriptor(request["target"])
        path = descriptor["path"]
        available = descriptor["kind"] == "URL" or (
            isinstance(path, Path)
            and path.exists()
            and (
                (descriptor["kind"] == "DIRECTORY" and path.is_dir())
                or (descriptor["kind"] != "DIRECTORY" and path.is_file())
            )
        )
        return {
            "schema_version": "DesktopAssistantTargetStatus-v2",
            "target_kind": descriptor["kind"],
            "target_sha256": hashlib.sha256(descriptor["target"].encode("utf-8")).hexdigest().upper(),
            "available": available,
            "validation_state": "VALID" if available else "MISSING",
            "target_path_exposed": False,
            "status": "PASS",
        }

    def launch_target(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {"target"}:
            raise ValueError("ASSISTANT_TARGET_LAUNCH_REQUEST_INVALID")
        descriptor = self._target_descriptor(request["target"])
        path = descriptor["path"]
        process_id: int | None = None
        if descriptor["kind"] == "URL":
            if not webbrowser.open(descriptor["target"], new=2):
                raise FileNotFoundError("ASSISTANT_SHORTCUT_URL_OPEN_FAILED")
            launch_method = "DEFAULT_BROWSER"
        elif descriptor["kind"] == "PROGRAM":
            if not isinstance(path, Path) or not path.is_file():
                raise FileNotFoundError("ASSISTANT_SHORTCUT_TARGET_MISSING")
            process = subprocess.Popen(
                [str(path)],
                shell=False,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            process_id = int(process.pid)
            launch_method = "DIRECT_PROCESS"
        else:
            if not isinstance(path, Path) or not path.exists():
                raise FileNotFoundError("ASSISTANT_SHORTCUT_TARGET_MISSING")
            startfile = getattr(os, "startfile", None)
            if not callable(startfile):
                raise RuntimeError("ASSISTANT_SHORTCUT_OPEN_UNAVAILABLE")
            startfile(str(path))
            launch_method = "WINDOWS_ASSOCIATION"
        return {
            "schema_version": "DesktopAssistantTargetLaunchReceipt-v2",
            "target_kind": descriptor["kind"],
            "target_sha256": hashlib.sha256(descriptor["target"].encode("utf-8")).hexdigest().upper(),
            "process_id": process_id,
            "launch_method": launch_method,
            "shell_command_calls": 0,
            "freeform_argument_count": 0,
            "status": "LAUNCHED",
        }

    def shortcut(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("ASSISTANT_SHORTCUT_REQUEST_INVALID")
        main_window_focus = None
        assistant_window_visibility = None
        visibility_request: bool | None = None
        restore_native_move: tuple[int, int, int, int] | None = None
        with self.lock:
            receipt = self.controller.dispatch_shortcut(request)
            action_id = receipt["action_id"]
            if action_id == "SHOW_DESKTOP_ASSISTANT":
                preferences = self.load_preferences()
                if preferences["enabled"]:
                    self.controller.set_control(visible=True)
                    visibility_request = True
            elif action_id == "HIDE_DESKTOP_ASSISTANT":
                self.controller.set_control(visible=False)
                visibility_request = False
            elif action_id == "RESTORE_DEFAULT_PLACEMENT":
                self._refresh_display_topology()
                restored = self.controller.restore_default(self.areas)
                placement = restored["placement"]
                accepted = self.placement_class(
                    monitor_id=str(placement["monitor_id"]),
                    x=int(placement["x"]),
                    y=int(placement["y"]),
                    width=int(placement["width"]),
                    height=int(placement["height"]),
                    dpi_percent=int(placement["dpi_percent"]),
                )
                if self.window is None:
                    raise RuntimeError("ASSISTANT_WINDOW_NOT_ATTACHED")
                _, _, visible_width, visible_height = self.visible_bounds(accepted)
                restore_native_move = (
                    accepted.x,
                    accepted.y,
                    visible_width,
                    visible_height,
                )

        # pywebview/WinForms presenters can synchronously call back into this
        # ProductApi to persist receipts.  Native window work must therefore
        # happen after releasing ``self._lock``; otherwise the UI thread and
        # the API worker can wait on one another indefinitely.
        if action_id == "TOGGLE_DESKTOP_ASSISTANT":
            target_visible = not self._window_is_visible()
            with self.lock:
                self.controller.set_control(visible=target_visible)
            visibility_request = target_visible
        if visibility_request is not None:
            assistant_window_visibility = self._set_window_visibility(
                visibility_request
            )
        elif action_id == "OPEN_Desktop_MAIN":
            main_window_focus = self._focus_main_window()
        elif action_id == "OPEN_DESKTOP_ASSISTANT_SETTINGS":
            self._activate_main_route("settings")
            self.main_window.evaluate_js(
                "window.DesktopPreview_settings?.root?.querySelector('[data-category=\"behavior\"]')?.click()"
            )
        elif action_id == "OPEN_MESSAGE_DETAIL":
            self._activate_main_route("messages")
        elif restore_native_move is not None:
            move_x, move_y, visible_width, visible_height = restore_native_move
            self._move_window_native(
                move_x,
                move_y,
                native_width=visible_width,
                native_height=visible_height,
            )

        with self.lock:
            assistant_visible = bool(self.controller.visible)
            assistant_window = self.window
        presentation_refresh_status = "READY"
        if not assistant_visible:
            # Hiding is a complete native action.  It must not be converted
            # into a failure merely because the background service is down.
            projection = _local_assistant_projection(
                visible=False,
                refresh_unavailable=False,
            )
            presentation_refresh_status = "LOCAL_ONLY"
        else:
            try:
                projection = self.bootstrap({})
            except Exception:
                # Showing/moving the already-created native window does not
                # depend on Application Service.  Keep the successful local
                # action and use a safe static projection until the event pump
                # reconnects; never leak an internal IPC code to the product.
                projection = _local_assistant_projection(
                    visible=True,
                    refresh_unavailable=True,
                )
                presentation_refresh_status = "DEGRADED"
        if (
            action_id in {"SHOW_DESKTOP_ASSISTANT", "TOGGLE_DESKTOP_ASSISTANT"}
            and assistant_visible
            and assistant_window is not None
            and hasattr(assistant_window, "evaluate_js")
        ):
            assistant_window.evaluate_js(
                "window.__Desktop_ASSISTANT_RUNTIME__?.applyPresentation("
                + json.dumps(
                    projection["presentation"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + ")"
            )
        return {
            **receipt,
            "main_window_focus": main_window_focus,
            "assistant_window_visibility": assistant_window_visibility,
            "presentation": projection,
            "presentation_refresh_status": presentation_refresh_status,
            "sample_job_consumed": False,
            "sample_success_used_as_real_success": False,
        }

    def claim_presentation_delivery(
        self, request: dict[str, Any]
    ) -> dict[str, Any]:
        """Atomically claim one durable pet delivery before any motion starts."""

        required = {
            "locator", "reason_code", "animation_state", "delivery_id", "reduced_motion"
        }
        if not isinstance(request, dict) or set(request) != required:
            raise ValueError("ASSISTANT_PRESENTATION_CLAIM_FIELDS_INVALID")
        locator = request["locator"]
        if (
            not isinstance(locator, str)
            or not locator.startswith("memorive://")
            or len(locator) > 320
        ):
            raise ValueError("ASSISTANT_PRESENTATION_CLAIM_LOCATOR_INVALID")
        reason_code = str(request["reason_code"])
        animation_state = str(request["animation_state"]).upper()
        delivery_id = request["delivery_id"]
        if not re.fullmatch(r"[A-Z0-9_]{1,96}", reason_code):
            raise ValueError("ASSISTANT_PRESENTATION_CLAIM_REASON_INVALID")
        if not re.fullmatch(r"[A-Z0-9_]{1,32}", animation_state):
            raise ValueError("ASSISTANT_PRESENTATION_CLAIM_ANIMATION_INVALID")
        if (
            not isinstance(delivery_id, str)
            or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", delivery_id) is None
        ):
            raise ValueError("ASSISTANT_PRESENTATION_CLAIM_DELIVERY_INVALID")
        if not isinstance(request["reduced_motion"], bool):
            raise ValueError("ASSISTANT_PRESENTATION_CLAIM_REDUCED_MOTION_INVALID")
        key_material = {
            "locator": locator,
            "reason_code": reason_code,
            "animation_state": animation_state,
            "delivery_id": delivery_id,
        }
        entry_key = _sha256_json(key_material)
        now = _utc_now()
        with self.lock:
            envelope: dict[str, Any] = {
                "schema_version": "DesktopAssistantPresentationReceiptStore-v1",
                "revision": 0,
                "updated_at": now,
                "entries": {},
                "raw_private_content_included": False,
            }
            if self.presentation_receipt_path.is_file():
                try:
                    loaded = json.loads(
                        self.presentation_receipt_path.read_text(encoding="utf-8")
                    )
                    if (
                        isinstance(loaded, dict)
                        and loaded.get("schema_version") == envelope["schema_version"]
                        and isinstance(loaded.get("entries"), dict)
                    ):
                        envelope = loaded
                except (OSError, json.JSONDecodeError):
                    pass
            entries = dict(envelope.get("entries") or {})
            existing = entries.get(entry_key)
            if delivery_id.startswith('message:') and isinstance(existing, Mapping):
                if existing.get('completed_at'):
                    self.deliveries.ack(delivery_id)
                elif existing.get('delivery_session') != self.delivery_session:
                    # Only an unacknowledged message is recoverable after a
                    # presenter restart. Historical completion dedupe is intact.
                    existing = None
            already_consumed = bool(
                isinstance(existing, Mapping)
                and (
                    existing.get("claimed_at")
                    or existing.get("started_at")
                    or existing.get("completed_at")
                )
            )
            if already_consumed:
                return {
                    "schema_version": "DesktopAssistantPresentationDeliveryClaim-v1",
                    "receipt_key": entry_key,
                    "already_consumed": True,
                    "status": "ALREADY_CONSUMED",
                }
            entry = {
                "locator": locator,
                "reason_code": reason_code,
                "animation_state": animation_state,
                "delivery_id": delivery_id,
                "requested_at": now,
                "claimed_at": now,
                "delivery_session": self.delivery_session,
                "started_at": None,
                "completed_at": None,
                "deduplicated_count": 0,
                "last_event": "CLAIMED",
                "last_observed_at": now,
                "reduced_motion": request["reduced_motion"],
                "deduplicated": False,
                "single_shot_started": False,
            }
            entries[entry_key] = entry
            if len(entries) > 128:
                entries = dict(
                    sorted(
                        entries.items(),
                        key=lambda row: str(row[1].get("last_observed_at") or ""),
                        reverse=True,
                    )[:128]
                )
            envelope.update(
                {
                    "revision": int(envelope.get("revision", 0)) + 1,
                    "updated_at": now,
                    "entries": entries,
                    "raw_private_content_included": False,
                }
            )
            self.write_json_file(self.presentation_receipt_path, envelope)
            return {
                "schema_version": "DesktopAssistantPresentationDeliveryClaim-v1",
                "receipt_key": entry_key,
                "already_consumed": False,
                "status": "CLAIMED",
            }

    def ack_delivery(self, request):
        if not isinstance(request,dict) or set(request) != {'delivery_id'} or not isinstance(request['delivery_id'],str):
            raise ValueError('ASSISTANT_DELIVERY_ACK_INVALID')
        return self.deliveries.ack(request['delivery_id'])

    def record_presentation_receipt(
        self, request: dict[str, Any]
    ) -> dict[str, Any]:
        allowed = {
            "event",
            "locator",
            "reason_code",
            "animation_state",
            "reduced_motion",
            "deduplicated",
            "single_shot_started",
        }
        if (
            not isinstance(request, dict)
            or not allowed <= set(request)
            or set(request) - allowed - {"delivery_id"}
        ):
            raise ValueError("ASSISTANT_PRESENTATION_RECEIPT_FIELDS_INVALID")
        event = str(request["event"])
        if event not in {
            "STARTED",
            "COMPLETED",
            "DEDUPLICATED",
            "STATIC",
            "QUEUED",
            "BLINK_QUEUE_SUPPRESSED",
            "CLAIMED",
            "COALESCED",
            "INTERRUPTED",
        }:
            raise ValueError("ASSISTANT_PRESENTATION_RECEIPT_EVENT_INVALID")
        locator = request.get("locator")
        if locator is not None and (
            not isinstance(locator, str)
            or not locator.startswith("memorive://")
            or len(locator) > 320
        ):
            raise ValueError("ASSISTANT_PRESENTATION_RECEIPT_LOCATOR_INVALID")
        reason_code = str(request.get("reason_code") or "")
        animation_state = str(request.get("animation_state") or "").upper()
        delivery_id = request.get("delivery_id")
        if delivery_id is not None and (
            not isinstance(delivery_id, str)
            or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", delivery_id) is None
        ):
            raise ValueError("ASSISTANT_PRESENTATION_RECEIPT_DELIVERY_INVALID")
        if not re.fullmatch(r"[A-Z0-9_]{1,96}", reason_code):
            raise ValueError("ASSISTANT_PRESENTATION_RECEIPT_REASON_INVALID")
        if not re.fullmatch(r"[A-Z0-9_]{1,32}", animation_state):
            raise ValueError("ASSISTANT_PRESENTATION_RECEIPT_ANIMATION_INVALID")
        for field in ("reduced_motion", "deduplicated", "single_shot_started"):
            if not isinstance(request[field], bool):
                raise ValueError("ASSISTANT_PRESENTATION_RECEIPT_BOOLEAN_INVALID")
        key_material = {
            "locator": locator,
            "reason_code": reason_code,
            "animation_state": animation_state,
            "delivery_id": delivery_id,
        }
        entry_key = _sha256_json(key_material)
        now = _utc_now()
        with self.lock:
            envelope: dict[str, Any] = {
                "schema_version": "DesktopAssistantPresentationReceiptStore-v1",
                "revision": 0,
                "updated_at": now,
                "entries": {},
                "raw_private_content_included": False,
            }
            if self.presentation_receipt_path.is_file():
                try:
                    loaded = json.loads(
                        self.presentation_receipt_path.read_text(encoding="utf-8")
                    )
                    if (
                        isinstance(loaded, dict)
                        and loaded.get("schema_version") == envelope["schema_version"]
                        and isinstance(loaded.get("entries"), dict)
                    ):
                        envelope = loaded
                except (OSError, json.JSONDecodeError):
                    pass
            entries = dict(envelope.get("entries") or {})
            entry = dict(
                entries.get(entry_key)
                or {
                    "locator": locator,
                    "reason_code": reason_code,
                    "animation_state": animation_state,
                    "delivery_id": delivery_id,
                    "requested_at": now,
                    "started_at": None,
                    "completed_at": None,
                    "deduplicated_count": 0,
                }
            )
            if event == "STARTED":
                entry["started_at"] = entry.get("started_at") or now
            elif event == "COMPLETED":
                entry["completed_at"] = now
                if delivery_id and hasattr(self, 'deliveries'):
                    self.deliveries.ack(delivery_id)
            elif event == "STATIC" and request["reduced_motion"] and delivery_id and hasattr(self, 'deliveries'):
                self.deliveries.ack(delivery_id)
            elif event == "DEDUPLICATED":
                entry["deduplicated_count"] = int(entry.get("deduplicated_count", 0)) + 1
            entry.update(
                {
                    "last_event": event,
                    "last_observed_at": now,
                    "reduced_motion": request["reduced_motion"],
                    "deduplicated": request["deduplicated"],
                    "single_shot_started": request["single_shot_started"],
                }
            )
            entries[entry_key] = entry
            if len(entries) > 128:
                entries = dict(
                    sorted(
                        entries.items(),
                        key=lambda row: str(row[1].get("last_observed_at") or ""),
                        reverse=True,
                    )[:128]
                )
            envelope.update(
                {
                    "revision": int(envelope.get("revision", 0)) + 1,
                    "updated_at": now,
                    "entries": entries,
                    "raw_private_content_included": False,
                }
            )
            self.write_json_file(self.presentation_receipt_path, envelope)
            return {
                "schema_version": "DesktopAssistantPresentationReceipt-v1",
                "receipt_key": entry_key,
                "revision": envelope["revision"],
                "entry": deepcopy(entry),
                "status": "PASS",
            }
