from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"
for candidate in (SOURCE_ROOT, DESKTOP_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive.desktop.api import ProductApi  # noqa: E402
from app import extract_inbox_drop_paths  # noqa: E402
from memorive.desktop.settings.contracts import (  # noqa: E402
    STORE_SCHEMA_VERSION,
    default_settings,
    settings_field_contract,
    validate_settings,
)
from memorive.platform.json_files import canonical_json_bytes, canonical_sha256  # noqa: E402
from memorive.desktop.settings.credentials import CredentialReferenceManager  # noqa: E402
from memorive.desktop.settings.store import SettingsStore  # noqa: E402


def _make_api(state_root: Path) -> ProductApi:
    return ProductApi(
        state_root,
        SOURCE_ROOT,
        SOURCE_ROOT / "facade_method_binding.json",
    )


def test_native_drop_extracts_only_pywebview_full_paths_and_deduplicates(
    tmp_path: Path,
) -> None:
    first = tmp_path / "拖放文献.pdf"
    second = tmp_path / "研究笔记.md"
    event = {
        "type": "drop",
        "dataTransfer": {
            "files": [
                {
                    "name": first.name,
                    "path": str(tmp_path / "untrusted-browser-path.pdf"),
                    "pywebviewFullPath": str(first),
                },
                {"name": first.name, "pywebviewFullPath": str(first)},
                {"name": second.name, "pywebviewFullPath": str(second)},
            ]
        },
    }
    assert extract_inbox_drop_paths(event) == [str(first), str(second)]


def test_native_drop_rejects_browser_path_without_pywebview_locator(tmp_path: Path) -> None:
    event = {
        "type": "drop",
        "dataTransfer": {"files": [{"name": "unsafe.pdf", "path": str(tmp_path / "unsafe.pdf")}]},
    }
    with pytest.raises(RuntimeError, match="^INBOX_DROP_LOCATOR_MISSING$"):
        extract_inbox_drop_paths(event)


def test_native_drop_rejects_more_than_32_files(tmp_path: Path) -> None:
    event = {
        "type": "drop",
        "dataTransfer": {
            "files": [
                {"name": f"{index}.md", "pywebviewFullPath": str(tmp_path / f"{index}.md")}
                for index in range(33)
            ]
        },
    }
    with pytest.raises(RuntimeError, match="^INBOX_DROP_TOO_MANY_FILES$"):
        extract_inbox_drop_paths(event)


def test_settings_contract_covers_every_persisted_preference() -> None:
    defaults = default_settings()
    contract_paths = {row["path"] for row in settings_field_contract()["fields"]}
    assert {
        f"preferences.{name}" for name in defaults["preferences"]
    }.issubset(contract_paths)
    assert validate_settings(defaults) == defaults


def test_settings_model_service_descriptor_can_drive_workflow_mapping() -> None:
    settings = default_settings()
    credential_ref = "REF:windows:Memorive/custom-v1-test/primary"
    settings["credential_references"].append(
        {
            "credential_ref": credential_ref,
            "provider": "自定义服务",
            "logical_key": "primary",
            "status": "STORED_UNVERIFIED",
            "created_at": "2026-08-27T00:00:00Z",
            "updated_at": "2026-08-27T00:00:00Z",
        }
    )
    settings["model_services"].append(
        {
            "config_id": "custom-model-primary",
            "provider": "自定义服务",
            "credential_ref": credential_ref,
            "plan": "STANDARD",
            "thinking": False,
            "model_name": "sonnet5",
            "tier": "max",
            "thinking_mode": "thinking",
            "connection_status": "UNVERIFIED",
        }
    )
    nodes = {row["node_id"]: row for row in settings["workflow"]["nodes"]}
    nodes["analysis"]["profile_ref"] = "custom-model-primary"

    accepted = validate_settings(settings)

    service = accepted["model_services"][0]
    assert (service["model_name"], service["tier"], service["thinking_mode"]) == (
        "sonnet5",
        "max",
        "thinking",
    )
    accepted_nodes = {
        row["node_id"]: row for row in accepted["workflow"]["nodes"]
    }
    assert accepted_nodes["analysis"]["profile_ref"] == "custom-model-primary"


def test_settings_legacy_model_service_shape_remains_loadable_without_migration(tmp_path: Path) -> None:
    settings = default_settings()
    credential_ref = "REF:windows:Memorive/custom-v1-legacy/primary"
    settings["credential_references"].append(
        {
            "credential_ref": credential_ref,
            "provider": "Legacy Provider",
            "logical_key": "primary",
            "status": "STORED_UNVERIFIED",
            "created_at": "2026-08-26T00:00:00Z",
            "updated_at": "2026-08-26T00:00:00Z",
        }
    )
    settings["model_services"].append(
        {
            "config_id": "legacy-primary",
            "provider": "Legacy Provider",
            "credential_ref": credential_ref,
            "plan": "STANDARD",
            "thinking": True,
            "connection_status": "UNVERIFIED",
        }
    )

    assert validate_settings(settings) == settings
    envelope = {
        "schema_version": STORE_SCHEMA_VERSION,
        "revision": 25,
        "template_id": "settings-template-000025",
        "updated_at": "2026-08-26T00:00:00Z",
        "settings": settings,
        "settings_sha256": canonical_sha256(settings),
    }
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "settings.json").write_bytes(canonical_json_bytes(envelope) + b"\n")

    loaded = SettingsStore(profile).load(recover_corruption=False)

    assert loaded["revision"] == 25
    assert loaded["settings_sha256"] == envelope["settings_sha256"]
    assert set(loaded["settings"]["model_services"][0]) == {
        "config_id",
        "provider",
        "credential_ref",
        "plan",
        "thinking",
        "connection_status",
    }


def test_settings_rejects_workflow_reference_to_missing_model_service() -> None:
    settings = default_settings()
    # v1.02 added workflow nodes, so find the model node by id rather than position.
    node = next(n for n in settings["workflow"]["nodes"] if n["node_id"] == "context_pack")
    node["profile_ref"] = "missing-custom-model"

    with pytest.raises(ValueError, match="^WORKFLOW_PROFILE_REFERENCE_NOT_CONFIGURED$"):
        validate_settings(settings)


def test_settings_v2_migrates_to_v3_without_losing_existing_values(tmp_path: Path) -> None:
    settings = default_settings(
        workspace_root=str(tmp_path / "workspace"),
        artifact_root=str(tmp_path / "artifacts"),
    )
    settings["schema_version"] = "SettingsTemplate-v2"
    new_fields = {
        "auto_open_preview", "external_refresh", "task_complete_notification",
        "task_error_notification", "approval_notification", "weekly_report_notification",
        "notification_sound", "do_not_disturb_start", "do_not_disturb_end",
        "notification_open_task", "restore_last_document", "close_behavior",
        "warn_on_close_running", "launch_at_startup", "keep_tasks_in_background",
        "remember_panel_state", "proxy_address", "request_timeout_seconds",
        "show_full_tooltips",
    }
    for field in new_fields:
        settings["preferences"].pop(field)
    settings["preferences"]["font_scale_percent"] = 120
    profile = tmp_path / "profile"
    profile.mkdir()
    legacy = {
        "schema_version": "SettingsStoreEnvelope-v2",
        "revision": 7,
        "template_id": "settings-template-000007",
        "updated_at": "2026-08-26T00:00:00Z",
        "settings": settings,
        "settings_sha256": canonical_sha256(settings),
    }
    (profile / "settings.json").write_bytes(canonical_json_bytes(legacy) + b"\n")
    store = SettingsStore(
        profile,
        default_factory=lambda: default_settings(
            workspace_root=str(tmp_path / "workspace"),
            artifact_root=str(tmp_path / "artifacts"),
        ),
    )
    migrated = store.load(recover_corruption=False)
    assert migrated["schema_version"] == STORE_SCHEMA_VERSION
    assert migrated["revision"] == 8
    assert migrated["settings"]["preferences"]["font_scale_percent"] == 120
    assert set(migrated["settings"]["preferences"]) == set(default_settings()["preferences"])


def test_settings_save_persists_real_selected_directories(tmp_path: Path) -> None:
    workspace = tmp_path / "用户工作区"
    artifacts = tmp_path / "用户产物"
    library = tmp_path / "Research Vault"
    for path in (workspace, artifacts, library):
        path.mkdir()
    state_root = tmp_path / "state"
    api = _make_api(state_root)
    try:
        for role, path in (
            ("workspace_root", workspace),
            ("artifact_root", artifacts),
            ("external_library", library),
        ):
            receipt = api.call("settings.register_directory", {"role": role, "path": str(path)})
            assert receipt["status"] == "PASS"
        current = api.call("settings.get_state", {})
        settings = deepcopy(current["settings"])
        settings["directories"]["workspace_root"] = str(workspace)
        settings["directories"]["artifact_root"] = str(artifacts)
        settings["directories"]["external_library"] = {
            "enabled": True,
            "provider_kind": "CUSTOM",
            "display_name": "Research Vault",
            "root": str(library),
        }
        settings["preferences"]["font_scale_percent"] = 120
        settings["preferences"]["restore_last_document"] = True
        preview = api.call("settings.preview", {"settings": settings, "confirmed_risk_ids": []})
        assert preview["status"] == "PASS"
        saved = api.call(
            "settings.save",
            {
                "settings": settings,
                "expected_revision": current["revision"],
                "confirmed_risk_ids": [],
            },
        )
        assert saved["action"] == "SAVE"
        observed = api.call("settings.get_state", {})
        assert observed["settings"]["directories"]["external_library"]["root"] == str(library)
        assert observed["settings"]["preferences"]["font_scale_percent"] == 120
        assert observed["settings"]["preferences"]["restore_last_document"] is False
    finally:
        api.close()
    reopened = _make_api(state_root)
    try:
        observed = reopened.call("settings.get_state", {})
        assert observed["settings"]["directories"]["external_library"]["root"] == str(library)
        assert observed["settings"]["preferences"]["font_scale_percent"] == 120
        assert observed["settings"]["preferences"]["restore_last_document"] is False
    finally:
        reopened.close()


def test_settings_configured_api_maps_workflow_and_cleanup_resets_profile(
    tmp_path: Path,
) -> None:
    api = _make_api(tmp_path / "state")
    provider = f"DesktopMappingTest-{uuid.uuid4().hex[:12]}"
    credential_ref: str | None = None
    try:
        current = api.call("settings.get_state", {})
        created = api.call(
            "settings.credential_create",
            {
                "provider": provider,
                "logical_key": "primary",
                "secret_input": "local-functional-test-value",
                "expected_revision": current["revision"],
            },
        )
        credential_ref = created["credential_ref"]
        with_reference = api.call("settings.get_state", {})
        configured = deepcopy(with_reference["settings"])
        config_id = f"custom-model-{uuid.uuid4().hex[:12]}"
        configured["model_services"].append(
            {
                "config_id": config_id,
                "provider": provider,
                "credential_ref": credential_ref,
                "plan": "STANDARD",
                "thinking": True,
                "model_name": "sonnet5",
                "tier": "max",
                "thinking_mode": "thinking",
                "connection_status": "UNVERIFIED",
            }
        )
        preview = api.call(
            "settings.preview",
            {"settings": configured, "confirmed_risk_ids": []},
        )
        assert preview["status"] == "PASS"
        api.call(
            "settings.save",
            {
                "settings": configured,
                "expected_revision": with_reference["revision"],
                "confirmed_risk_ids": [],
            },
        )

        with_service = api.call("settings.get_state", {})
        mapped = deepcopy(with_service["settings"])
        analysis = next(row for row in mapped["workflow"]["nodes"] if row["node_id"] == "analysis")
        analysis["profile_ref"] = config_id
        preview = api.call(
            "settings.preview",
            {"settings": mapped, "confirmed_risk_ids": []},
        )
        assert preview["status"] == "PASS"
        api.call(
            "settings.save",
            {
                "settings": mapped,
                "expected_revision": with_service["revision"],
                "confirmed_risk_ids": [],
            },
        )

        mapped_state = api.call("settings.get_state", {})
        assert next(
            row for row in mapped_state["settings"]["workflow"]["nodes"] if row["node_id"] == "analysis"
        )["profile_ref"] == config_id
        api.call(
            "settings.credential_delete",
            {
                "credential_ref": credential_ref,
                "expected_revision": mapped_state["revision"],
            },
        )
        credential_ref = None
        cleaned = api.call("settings.get_state", {})
        assert cleaned["settings"]["credential_references"] == []
        assert cleaned["settings"]["model_services"] == []
        assert next(
            row for row in cleaned["settings"]["workflow"]["nodes"] if row["node_id"] == "analysis"
        )["profile_ref"] == "ANALYSIS_PROFILE"
    finally:
        if credential_ref:
            current = api.call("settings.get_state", {})
            if any(
                row["credential_ref"] == credential_ref
                for row in current["settings"]["credential_references"]
            ):
                api.call(
                    "settings.credential_delete",
                    {
                        "credential_ref": credential_ref,
                        "expected_revision": current["revision"],
                    },
                )
        api.close()


def test_custom_provider_name_is_preserved_with_safe_reference() -> None:
    manager = CredentialReferenceManager()
    provider = "研究模型服务 · 自定义 01"
    reference = manager.reference(provider, "primary")
    assert reference.startswith("REF:windows:Memorive/custom-v1-")
    assert reference.endswith("/primary")
    receipt = manager.bind(
        provider=provider,
        logical_key="primary",
        secret_input="synthetic-local-test-value",
    )
    assert receipt["provider"] == provider
    assert receipt["credential_ref"] == reference
    assert manager.status(reference)["status"] == "STORED_UNVERIFIED"
    manager.delete(reference)
    assert manager.status(reference)["status"] == "MISSING"


def test_application_exit_closes_both_windows_without_shell_or_external_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class WindowProbe:
        def __init__(self) -> None:
            self.destroy_count = 0
            self.hide_count = 0

        def destroy(self) -> None:
            self.destroy_count += 1

        def hide(self) -> None:
            self.hide_count += 1

    class ImmediateTimer:
        def __init__(self, _interval, function, args=None, kwargs=None) -> None:
            self.function = function
            self.args = tuple(args or ())
            self.kwargs = dict(kwargs or {})
            self.daemon = False

        def start(self) -> None:
            self.function(*self.args, **self.kwargs)

    api = _make_api(tmp_path / "state")
    main_window = WindowProbe()
    assistant_window = WindowProbe()
    try:
        api.attach_window(main_window)
        api.attach_assistant_window(assistant_window)
        monkeypatch.setattr("threading.Timer", ImmediateTimer)
        receipt = api.exit_application({})
        duplicate = api.exit_application({})
        assert receipt["status"] == "EXIT_SCHEDULED"
        assert receipt["external_process_launches"] == 0
        assert receipt["shell_command_calls"] == 0
        assert receipt["already_requested"] is False
        assert duplicate["already_requested"] is True
        assert main_window.destroy_count == 1
        assert assistant_window.destroy_count == 1
    finally:
        api.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows Credential Manager only")
def test_windows_credential_reference_is_real_and_cleaned() -> None:
    manager = CredentialReferenceManager(use_windows_backend=True)
    provider = f"DesktopLocalTest-{uuid.uuid4().hex[:12]}"
    reference = manager.reference(provider, "primary")
    try:
        receipt = manager.bind(
            provider=provider,
            logical_key="primary",
            secret_input="local-functional-test-value",
        )
        assert receipt["credential_ref"] == reference
        assert manager.status(reference)["status"] == "STORED_UNVERIFIED"
        assert manager.metrics["real_credential_writes"] == 1
        assert manager.metrics["credential_value_reads"] == 0
    finally:
        manager.delete(reference)
    assert manager.status(reference)["status"] == "MISSING"
    assert manager.metrics["real_credential_deletes"] == 1
