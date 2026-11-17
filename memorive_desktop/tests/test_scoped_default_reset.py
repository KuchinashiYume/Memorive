from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"
for candidate in (SOURCE_ROOT, DESKTOP_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive.desktop.settings.capability import CapabilityProjection  # noqa: E402
from memorive.desktop.settings.contracts import default_settings  # noqa: E402
from memorive.desktop.settings.controller import SettingsController  # noqa: E402
from memorive.desktop.settings.credentials import CredentialReferenceManager  # noqa: E402
from memorive.desktop.settings.directory_policy import DirectoryPolicy  # noqa: E402
from memorive.desktop.settings.service_adapter import SETTINGS_METHODS, SettingsServiceAdapter  # noqa: E402
from memorive.desktop.settings.store import SettingsStore  # noqa: E402


SILICONFLOW_REFERENCE = "REF:windows:Memorive/siliconflow/primary"


class CredentialBackend:
    def __init__(self) -> None:
        self.values = {"Memorive/siliconflow/primary": b"fixture-key"}

    def write(self, target: str, secret: bytes) -> None:
        self.values[target] = bytes(secret)

    def read(self, target: str) -> bytes:
        return self.values[target]

    def delete(self, target: str) -> None:
        self.values.pop(target, None)

    def exists(self, target: str) -> bool:
        return target in self.values


def _service(config_id: str, model_name: str, tier: str) -> dict[str, object]:
    return {
        "config_id": config_id,
        "provider": "SILICONFLOW_API_KEY",
        "credential_ref": SILICONFLOW_REFERENCE,
        "plan": "STANDARD",
        "thinking": False,
        "model_name": model_name,
        "tier": tier,
        "thinking_mode": "",
        "connection_status": "UNVERIFIED",
    }


def _controller_with_saved_settings(
    tmp_path: Path,
) -> tuple[SettingsController, dict[str, object]]:
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir()
    artifacts.mkdir()
    store = SettingsStore(
        tmp_path / "settings",
        default_factory=lambda: default_settings(
            workspace_root=str(workspace), artifact_root=str(artifacts)
        ),
    )
    initial = store.load()
    settings = deepcopy(initial["settings"])
    settings["mode"] = "ADVANCED"
    settings["credential_references"].append(
        {
            "credential_ref": SILICONFLOW_REFERENCE,
            "provider": "SILICONFLOW_API_KEY",
            "logical_key": "primary",
            "status": "STORED_UNVERIFIED",
            "created_at": "2026-08-29T00:00:00Z",
            "updated_at": "2026-08-29T00:00:00Z",
        }
    )
    settings["model_services"].append(
        _service("siliconflow-bge-m3", "Pro/BAAI/bge-m3", "Pro")
    )
    settings["cli_services"][0]["enabled"] = True
    settings["cli_services"][0]["models"] = [
        {
            "profile_ref": "cli:codex-cli:fixture",
            "display_name": "Fixture chat model",
            "model_name": "fixture-chat-model",
            "thinking_mode": "",
            "connection_status": "UNVERIFIED",
        }
    ]
    for node in settings["workflow"]["nodes"]:
        if node["node_id"] == "chunk_embedding":
            node["profile_ref"] = "siliconflow-bge-m3"
    settings["directories"]["workspace_root"] = str(tmp_path / "custom-workspace")
    settings["directories"]["artifact_root"] = str(tmp_path / "custom-artifacts")
    settings["directories"]["external_library"] = {
        "enabled": True,
        "provider_kind": "CUSTOM",
        "display_name": "Preserved library",
        "root": str(tmp_path / "library"),
    }
    settings["preferences"].update(
        {
            "document_viewer": "FOLDER",
            "auto_open_preview": False,
            "external_refresh": False,
            "notifications_enabled": False,
            "task_complete_notification": False,
            "task_error_notification": False,
            "approval_notification": False,
            "weekly_report_notification": False,
            "notification_sound": True,
            "do_not_disturb_start": "20:00",
            "do_not_disturb_end": "09:00",
            "notification_open_task": False,
            "restore_last_view": True,
            "close_behavior": "TRAY",
            "warn_on_close_running": False,
            "launch_at_startup": True,
            "keep_tasks_in_background": False,
            "remember_panel_state": False,
            "proxy_mode": "CUSTOM",
            "proxy_address": "http://127.0.0.1:7890",
            "request_timeout_seconds": 17,
            "log_level": "DETAILED",
            "retention_days": 7,
            "cache_limit_mb": 1024,
            "telemetry_enabled": True,
            "language": "ja-JP",
            "font_scale_percent": 120,
            "show_full_tooltips": False,
        }
    )
    store.save(settings, expected_revision=initial["revision"])
    controller = SettingsController(
        store=store,
        credentials=CredentialReferenceManager(backend=CredentialBackend()),
        capabilities=CapabilityProjection([], revision="fixture"),
        directory_policy=DirectoryPolicy([tmp_path]),
    )
    return controller, default_settings(
        workspace_root=str(workspace), artifact_root=str(artifacts)
    )


@pytest.mark.parametrize(
    "scope",
    ["api", "cli", "local-models", "workflow", "refinement", "update", "all"],
)
def test_sensitive_or_global_default_reset_scopes_fail_closed(
    tmp_path: Path, scope: str
) -> None:
    controller, _defaults = _controller_with_saved_settings(tmp_path)
    before = controller.get_state()

    with pytest.raises(ValueError, match=f"SETTINGS_RESET_SCOPE_FORBIDDEN:{scope}"):
        controller.reset_scope(scope=scope, expected_revision=before["revision"])

    after = controller.get_state()
    assert after["revision"] == before["revision"]
    assert after["settings_sha256"] == before["settings_sha256"]
    assert after["settings"] == before["settings"]


@pytest.mark.parametrize(
    ("scope", "preference_keys", "reset_directories"),
    [
        ("viewer", ("document_viewer", "auto_open_preview", "external_refresh"), True),
        (
            "notice",
            (
                "notifications_enabled",
                "task_complete_notification",
                "task_error_notification",
                "approval_notification",
                "weekly_report_notification",
                "notification_sound",
                "do_not_disturb_start",
                "do_not_disturb_end",
                "notification_open_task",
            ),
            False,
        ),
        (
            "behavior",
            (
                "restore_last_view",
                "restore_last_document",
                "close_behavior",
                "warn_on_close_running",
                "launch_at_startup",
                "keep_tasks_in_background",
                "remember_panel_state",
            ),
            False,
        ),
        ("network", ("proxy_mode", "proxy_address", "request_timeout_seconds"), False),
        ("privacy", ("log_level", "retention_days", "cache_limit_mb", "telemetry_enabled"), False),
        ("appearance", ("language", "font_scale_percent", "show_full_tooltips"), False),
    ],
)
def test_allowed_default_reset_changes_only_the_open_subitem(
    tmp_path: Path,
    scope: str,
    preference_keys: tuple[str, ...],
    reset_directories: bool,
) -> None:
    controller, defaults = _controller_with_saved_settings(tmp_path)
    before = controller.get_state()["settings"]
    protected = {
        key: deepcopy(before[key])
        for key in ("mode", "model_services", "cli_services", "credential_references", "workflow")
    }
    expected_preferences = deepcopy(before["preferences"])
    for key in preference_keys:
        expected_preferences[key] = defaults["preferences"][key]

    receipt = controller.reset_scope(
        scope=scope,
        expected_revision=controller.get_state()["revision"],
    )
    after = controller.get_state()["settings"]

    assert receipt["scope"] == scope
    assert receipt["mutation_kind"] == "RESET_SETTINGS_SCOPE"
    assert after["preferences"] == expected_preferences
    assert after["directories"] == (
        defaults["directories"] if reset_directories else before["directories"]
    )
    for key, value in protected.items():
        assert after[key] == value


def test_service_contract_exposes_scoped_reset_but_not_global_reset(tmp_path: Path) -> None:
    controller, _defaults = _controller_with_saved_settings(tmp_path)
    adapter = SettingsServiceAdapter(controller)

    assert "settings.reset_scope" in SETTINGS_METHODS
    assert "settings.reset" not in SETTINGS_METHODS
    with pytest.raises(ValueError, match="SETTINGS_METHOD_NOT_ALLOWLISTED:settings.reset"):
        adapter.call(
            "settings.reset",
            {"expected_revision": controller.get_state()["revision"]},
        )


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (
            "notice",
            {
                "enabled": False,
                "shortcut_entries": [
                    {"name": "Keep", "target": "https://example.com/keep"},
                    {"name": "", "target": ""},
                    {"name": "", "target": ""},
                ],
                "reminder_scope": "IMPORTANT_AND_COMPLETE",
                "hide_sensitive_names": False,
            },
        ),
        (
            "behavior",
            {
                "enabled": True,
                "shortcut_entries": [
                    {"name": "", "target": ""},
                    {"name": "", "target": ""},
                    {"name": "", "target": ""},
                ],
                "reminder_scope": "INCLUDE_ORDINARY",
                "hide_sensitive_names": True,
            },
        ),
        (
            "appearance",
            {
                "enabled": True,
                "shortcut_entries": [
                    {"name": "Keep", "target": "https://example.com/keep"},
                    {"name": "", "target": ""},
                    {"name": "", "target": ""},
                ],
                "reminder_scope": "INCLUDE_ORDINARY",
                "hide_sensitive_names": True,
            },
        ),
    ],
)
def test_runtime_scoped_reset_covers_visible_assistant_fields_only(
    scope: str,
    expected: dict[str, object],
) -> None:
    current = {
        "schema_version": "DesktopAssistantPreferences-v3",
        "enabled": False,
        "shortcut_entries": [
            {"name": "Keep", "target": "https://example.com/keep"},
            {"name": "", "target": ""},
            {"name": "", "target": ""},
        ],
        "reminder_scope": "INCLUDE_ORDINARY",
        "hide_sensitive_names": True,
    }

    from memorive.desktop.assistant.desktop import AssistantDesktop

    candidate = AssistantDesktop.preferences_for_reset_scope(
        scope,
        current,
        default_shortcuts=[{"name": "", "target": ""} for _ in range(3)],
    )

    assert candidate == {"schema_version": "DesktopAssistantPreferences-v3", **expected}


def test_runtime_allowlist_and_postprocessing_use_scoped_reset_only() -> None:
    runtime_source = "\n".join(path.read_text(encoding="utf-8") for path in sorted((DESKTOP_ROOT.parents[1] / "source" / "memorive" / "desktop" / "api").glob("*.py")))

    assert '"settings.reset_scope"' in runtime_source
    assert '"settings.model_service_remove"' in runtime_source
    assert '"settings.reset",' not in runtime_source
    assert 'if method == "settings.reset_scope"' in runtime_source
    assert '"settings.import_redacted",' in runtime_source
    assert "self._desk_assistant.preferences_for_reset_scope(" in runtime_source
