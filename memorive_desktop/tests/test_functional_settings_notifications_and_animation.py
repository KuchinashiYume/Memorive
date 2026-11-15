from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
import types

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"
for candidate in (SOURCE_ROOT, DESKTOP_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from runtime import ProductApi  # noqa: E402
from memorive_settings.contracts import (  # noqa: E402
    SETTINGS_SCHEMA_VERSION,
    STORE_SCHEMA_VERSION,
    canonical_json_bytes,
    canonical_sha256,
    default_settings,
    validate_settings,
)
from memorive_settings.store import SettingsStore  # noqa: E402


def _make_api(state_root: Path) -> ProductApi:
    return ProductApi(
        state_root,
        SOURCE_ROOT,
        SOURCE_ROOT / "facade_method_binding.json",
    )


def _save_directories(api: ProductApi, workspace: Path, artifacts: Path) -> None:
    for role, path in (("workspace_root", workspace), ("artifact_root", artifacts)):
        receipt = api.call("settings.register_directory", {"role": role, "path": str(path)})
        assert receipt["status"] == "PASS"
    current = api.call("settings.get_state", {})
    settings = deepcopy(current["settings"])
    settings["directories"]["workspace_root"] = str(workspace)
    settings["directories"]["artifact_root"] = str(artifacts)
    saved = api.call(
        "settings.save",
        {
            "settings": settings,
            "expected_revision": current["revision"],
            "confirmed_risk_ids": [],
        },
    )
    assert saved["action"] == "SAVE"


def test_external_library_contract_persists_provider_kind() -> None:
    settings = default_settings()
    assert SETTINGS_SCHEMA_VERSION == "SettingsTemplate-v7"
    assert STORE_SCHEMA_VERSION == "SettingsStoreEnvelope-v7"
    assert settings["directories"]["external_library"] == {
        "enabled": False,
        "provider_kind": "NONE",
        "display_name": "",
        "root": None,
    }
    assert validate_settings(settings) == settings


def test_v3_settings_migrate_provider_kind_without_losing_values(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifacts"
    vault = tmp_path / "旧资料库"
    for path in (workspace, artifacts, vault):
        path.mkdir()
    legacy_settings = default_settings(
        workspace_root=str(workspace), artifact_root=str(artifacts)
    )
    legacy_settings["schema_version"] = "SettingsTemplate-v3"
    legacy_settings["directories"]["external_library"] = {
        "enabled": True,
        "display_name": "旧资料库",
        "root": str(vault),
    }
    legacy = {
        "schema_version": "SettingsStoreEnvelope-v3",
        "revision": 12,
        "template_id": "settings-template-000012",
        "updated_at": "2026-08-27T00:00:00Z",
        "settings": legacy_settings,
        "settings_sha256": canonical_sha256(legacy_settings),
    }
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "settings.json").write_bytes(canonical_json_bytes(legacy) + b"\n")

    migrated = SettingsStore(
        profile,
        default_factory=lambda: default_settings(
            workspace_root=str(workspace), artifact_root=str(artifacts)
        ),
    ).load(recover_corruption=False)

    assert migrated["schema_version"] == "SettingsStoreEnvelope-v7"
    assert migrated["revision"] == 13
    assert migrated["settings"]["directories"]["external_library"] == {
        "enabled": True,
        "provider_kind": "CUSTOM",
        "display_name": "旧资料库",
        "root": str(vault),
    }


def test_obsidian_vault_test_requires_marker_and_returns_clear_receipt(tmp_path: Path) -> None:
    api = _make_api(tmp_path / "state")
    vault = tmp_path / "Research Vault"
    vault.mkdir()
    try:
        with pytest.raises(ValueError, match="^OBSIDIAN_VAULT_MARKER_MISSING$"):
            api.test_external_library(
                {
                    "provider_kind": "OBSIDIAN",
                    "display_name": "研究资料库",
                    "root": str(vault),
                }
            )
        (vault / ".obsidian").mkdir()
        receipt = api.test_external_library(
            {
                "provider_kind": "OBSIDIAN",
                "display_name": "研究资料库",
                "root": str(vault),
            }
        )
        assert receipt["provider_kind"] == "OBSIDIAN"
        assert receipt["display_name"] == "研究资料库"
        assert receipt["marker"] == ".obsidian"
        assert receipt["status"] == "PASS"
    finally:
        api.close()


def test_saved_workspace_and_artifact_directories_drive_native_dialogs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class WindowProbe:
        def __init__(self) -> None:
            self.dialogs: list[dict[str, object]] = []

        def create_file_dialog(self, dialog_type, **kwargs):
            self.dialogs.append({"dialog_type": dialog_type, **kwargs})
            return None

    workspace = tmp_path / "用户工作区"
    artifacts = tmp_path / "用户产物"
    workspace.mkdir()
    artifacts.mkdir()
    api = _make_api(tmp_path / "state")
    window = WindowProbe()
    try:
        fake_webview = types.ModuleType("webview")
        fake_webview.FileDialog = types.SimpleNamespace(OPEN="OPEN", SAVE="SAVE", FOLDER="FOLDER")
        monkeypatch.setitem(sys.modules, "webview", fake_webview)
        _save_directories(api, workspace, artifacts)
        api.attach_window(window)
        assert api.pick_inbox_files()["status"] == "CANCELLED"
        assert api.import_redacted_settings_file()["status"] == "CANCELLED"
        assert api._save_json_dialog({"test": True}, "test.json")["status"] == "CANCELLED"
        assert [row["directory"] for row in window.dialogs] == [
            str(workspace.resolve()),
            str(workspace.resolve()),
            str(artifacts.resolve()),
        ]
    finally:
        api.close()


@pytest.mark.parametrize(
    ("kind", "switch_name"),
    [
        ("COMPLETE", "task_complete_notification"),
        ("ERROR", "task_error_notification"),
        ("APPROVAL", "approval_notification"),
        ("WEEKLY", "weekly_report_notification"),
    ],
)
def test_each_notification_setting_controls_real_delivery_candidate(
    kind: str, switch_name: str
) -> None:
    from notification_runtime import plan_windows_notification

    preferences = default_settings()["preferences"]
    preferences.update(
        {
            "do_not_disturb_start": "00:00",
            "do_not_disturb_end": "00:00",
            "notification_sound": True,
            "notification_open_task": True,
        }
    )
    delivered = plan_windows_notification(
        preferences,
        kind=kind,
        now=datetime(2026, 8, 27, 12, 0),
        locator="memorive://job/job-real-1",
    )
    assert delivered["status"] == "DELIVER"
    assert delivered["play_sound"] is True
    assert delivered["click_opens_task"] is True
    assert delivered["private_body_included"] is False

    disabled = deepcopy(preferences)
    disabled[switch_name] = False
    blocked = plan_windows_notification(
        disabled,
        kind=kind,
        now=datetime(2026, 8, 27, 12, 0),
        locator="memorive://job/job-real-1",
    )
    assert blocked["status"] == "BLOCKED_DISABLED"


def test_notification_master_switch_blocks_every_category() -> None:
    from notification_runtime import NOTIFICATION_KINDS, plan_windows_notification

    preferences = default_settings()["preferences"]
    preferences["notifications_enabled"] = False
    preferences["do_not_disturb_start"] = "00:00"
    preferences["do_not_disturb_end"] = "00:00"
    for kind in sorted(NOTIFICATION_KINDS):
        blocked = plan_windows_notification(
            preferences,
            kind=kind,
            now=datetime(2026, 8, 27, 12, 0),
            locator="memorive://job/job-real-1",
        )
        assert blocked["status"] == "BLOCKED_DISABLED"
        assert blocked["reason_code"] == "GLOBAL_OR_KIND_SWITCH_DISABLED"


def test_notification_quiet_hours_and_click_setting_are_real() -> None:
    from notification_runtime import plan_windows_notification

    preferences = default_settings()["preferences"]
    quiet = plan_windows_notification(
        preferences,
        kind="ERROR",
        now=datetime(2026, 8, 27, 23, 30),
        locator="memorive://job/job-real-1",
    )
    assert quiet["status"] == "BLOCKED_QUIET"
    preferences["notification_open_task"] = False
    preferences["do_not_disturb_start"] = "00:00"
    preferences["do_not_disturb_end"] = "00:00"
    delivered = plan_windows_notification(
        preferences,
        kind="ERROR",
        now=datetime(2026, 8, 27, 23, 30),
        locator="memorive://job/job-real-1",
    )
    assert delivered["status"] == "DELIVER"
    assert delivered["click_opens_task"] is False
    assert delivered["activation_locator"] is None


def test_product_notification_test_calls_attached_windows_presenter(tmp_path: Path) -> None:
    presented: list[dict[str, object]] = []
    api = _make_api(tmp_path / "state")
    try:
        current = api.call("settings.get_state", {})
        settings = deepcopy(current["settings"])
        settings["preferences"]["do_not_disturb_start"] = "00:00"
        settings["preferences"]["do_not_disturb_end"] = "00:00"
        api.call(
            "settings.save",
            {
                "settings": settings,
                "expected_revision": current["revision"],
                "confirmed_risk_ids": [],
            },
        )
        api.attach_notification_presenter(lambda request: presented.append(dict(request)))
        receipt = api.test_windows_notification({"kind": "COMPLETE"})
        assert receipt["status"] == "DELIVERED"
        assert receipt["windows_presenter_called"] is True
        assert len(presented) == 1
        assert presented[0]["kind"] == "COMPLETE"
        assert presented[0]["private_body_included"] is False
    finally:
        api.close()


def test_part11_blink_is_scheduled_on_visible_native_overlay() -> None:
    app = (DESKTOP_ROOT / "app.py").read_text(encoding="utf-8")
    reveal_block = app.split("form.Invoke(Action(configure))", 1)[1].split(
        "def apply_assistant_native_presentation", 1
    )[0]
    assert "schedule_assistant_auto_blink" in reveal_block
    assert '"current_frame_sha256"' in app
    assert '"frame_sha256s"' in app


