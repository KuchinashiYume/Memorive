from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"
for candidate in (SOURCE_ROOT, DESKTOP_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive_settings.capability import CapabilityProjection  # noqa: E402
from memorive_settings.contracts import default_settings  # noqa: E402
from memorive_settings.controller import SettingsController  # noqa: E402
from memorive_settings.credentials import CredentialReferenceManager  # noqa: E402
from memorive_settings.directory_policy import DirectoryPolicy  # noqa: E402
from memorive_settings.service_adapter import SETTINGS_METHODS, SettingsServiceAdapter  # noqa: E402
from memorive_settings.store import SettingsStore  # noqa: E402
from runtime import ProductApi  # noqa: E402


def _controller(tmp_path: Path) -> SettingsController:
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifacts"
    profile = tmp_path / "profile"
    workspace.mkdir()
    artifacts.mkdir()
    store = SettingsStore(
        profile,
        default_factory=lambda: default_settings(
            workspace_root=str(workspace),
            artifact_root=str(artifacts),
        ),
    )
    store.load()
    return SettingsController(
        store=store,
        credentials=CredentialReferenceManager(),
        capabilities=CapabilityProjection([], revision="sample"),
        directory_policy=DirectoryPolicy([tmp_path]),
    )


def _configured_cli_services(controller: SettingsController) -> list[dict[str, object]]:
    services = deepcopy(controller.get_state()["settings"]["cli_services"])
    codex = services[0]
    codex["enabled"] = True
    codex["connection_status"] = "UNVERIFIED"
    codex["models"] = [
        {
            "profile_ref": "cli:codex-cli:GPT-5.6-sol",
            "display_name": "GPT-5.6-sol",
            "model_name": "GPT-5.6-sol-xhigh",
            "thinking_mode": "thinking",
            "connection_status": "UNVERIFIED",
        }
    ]
    return services


def test_cli_category_save_accepts_reported_model_and_preserves_other_settings(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    before = controller.get_state()
    protected = {
        key: deepcopy(before["settings"][key])
        for key in (
            "model_services",
            "credential_references",
            "directories",
            "workflow",
            "preferences",
        )
    }

    receipt = controller.save_cli_services(
        cli_services=_configured_cli_services(controller),
        mode="ADVANCED",
        expected_revision=before["revision"],
    )

    assert receipt["status"] == "PASS"
    assert receipt["action"] == "SAVE_CLI_SERVICES"
    assert receipt["mutation_kind"] == "SAVE_CLI_SERVICES"
    assert receipt["cli_only"] is True
    assert receipt["global_settings_preserved"] is True
    saved = controller.get_state()["settings"]
    assert saved["mode"] == "ADVANCED"
    assert saved["cli_services"][0]["models"][0] == {
        "profile_ref": "cli:codex-cli:GPT-5.6-sol",
        "display_name": "GPT-5.6-sol",
        "model_name": "GPT-5.6-sol-xhigh",
        "thinking_mode": "thinking",
        "connection_status": "UNVERIFIED",
    }
    for key, value in protected.items():
        assert saved[key] == value


def test_cli_category_save_returns_precise_blocked_receipt_without_mutation(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    before = controller.get_state()
    services = _configured_cli_services(controller)
    services[0]["models"][0]["model_name"] = "invalid model name"

    receipt = controller.save_cli_services(
        cli_services=services,
        mode="ADVANCED",
        expected_revision=before["revision"],
    )

    assert receipt == {
        "schema_version": "SettingsCliServicesSaveReceipt-v1",
        "status": "BLOCKED",
        "error_code": "CLI_MODEL_NAME_INVALID",
        "persistent_mutation": False,
        "cli_only": True,
        "global_settings_preserved": True,
    }
    after = controller.get_state()
    assert after["revision"] == before["revision"]
    assert after["settings"] == before["settings"]


def test_cli_category_save_forbids_normal_mode_and_adapter_exposes_method(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    before = controller.get_state()

    receipt = controller.save_cli_services(
        cli_services=_configured_cli_services(controller),
        mode="NORMAL",
        expected_revision=before["revision"],
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["error_code"] == "CLI_MODE_NORMAL_FORBIDDEN"
    assert controller.get_state()["revision"] == before["revision"]
    assert "settings.save_cli_services" in SETTINGS_METHODS
    adapter = SettingsServiceAdapter(controller)
    saved = adapter.call(
        "settings.save_cli_services",
        {
            "cli_services": _configured_cli_services(controller),
            "mode": "DEVELOPER",
            "expected_revision": before["revision"],
        },
    )
    assert saved["status"] == "PASS"
    assert controller.get_state()["settings"]["mode"] == "DEVELOPER"


def test_cli_product_save_does_not_report_persistence_failure_when_runtime_refresh_defers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    api = ProductApi(
        tmp_path / "product-state",
        SOURCE_ROOT,
        SOURCE_ROOT / "facade_method_binding.json",
    )
    try:
        before = api.call("settings.get_state", {})
        services = deepcopy(before["settings"]["cli_services"])
        services[0]["enabled"] = True
        services[0]["models"] = [
            {
                "profile_ref": "cli:codex-cli:gpt-5.6-sol",
                "display_name": "GPT 5.6 Sol",
                "model_name": "gpt-5.6-sol",
                "thinking_mode": "xhigh",
                "connection_status": "UNVERIFIED",
            }
        ]

        def fail_runtime_refresh(_state):
            raise RuntimeError("FIXTURE_RUNTIME_REFRESH_FAILURE")

        monkeypatch.setattr(
            api._current_task,
            "update_workflow_settings",
            fail_runtime_refresh,
        )
        receipt = api.call(
            "settings.save_cli_services",
            {
                "cli_services": services,
                "mode": "ADVANCED",
                "expected_revision": before["revision"],
            },
        )

        assert receipt["status"] == "PASS"
        assert receipt["settings_persisted"] is True
        assert receipt["runtime_effects_status"] == "WARNING"
        assert receipt["runtime_effect_error_code"] == "CLI_RUNTIME_REFRESH_DEFERRED"
        after = api.call("settings.get_state", {})
        assert after["revision"] == before["revision"] + 1
        assert after["settings"]["cli_services"][0]["models"][0]["model_name"] == "gpt-5.6-sol"
    finally:
        api.close()


