from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path
import sys

import pytest


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from memorive_settings.capability import CapabilityProjection
from memorive_settings.contracts import default_settings, validate_settings
from memorive_settings.controller import SettingsController
from memorive_settings.credentials import CredentialReferenceManager
from memorive_settings.directory_policy import DirectoryPolicy
from memorive_settings.store import SettingsStore


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]


def _available(model: str = "kimi-k3") -> dict[str, object]:
    return {
        "status": "AVAILABLE",
        "reason": "API_MODEL_CATALOG_MATCH",
        "requested_model": model,
        "returned_model": model,
        "external_network_calls": 1,
        "provider_calls": 1,
        "external_model_calls": 0,
    }


def _rejected() -> dict[str, object]:
    return {
        "status": "INVALID",
        "reason": "API_AUTHENTICATION_REJECTED",
        "requested_model": "kimi-k3",
        "returned_model": None,
        "external_network_calls": 1,
        "provider_calls": 1,
        "external_model_calls": 0,
    }


class _KimiCatalogRunner:
    def __init__(self) -> None:
        self.endpoints: list[str] = []

    def verify_api_model(self, service):
        endpoint = service["api_base_url"]
        self.endpoints.append(endpoint)
        if endpoint.endswith(".cn/v1"):
            return _rejected()
        return _available()


def test_settings_validation_resolves_and_persists_kimi_key_platform(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifacts"
    profile = tmp_path / "profile"
    workspace.mkdir()
    artifacts.mkdir()
    store = SettingsStore(
        profile,
        default_factory=lambda: default_settings(
            workspace_root=str(workspace), artifact_root=str(artifacts)
        ),
    )
    current = store.load()
    settings = deepcopy(current["settings"])
    credential_ref = "REF:windows:Memorive/kimi/primary"
    settings["credential_references"].append(
        {
            "credential_ref": credential_ref,
            "provider": "Kimi",
            "logical_key": "primary",
            "status": "STORED_UNVERIFIED",
            "created_at": "2026-09-03T00:00:00Z",
            "updated_at": "2026-09-03T00:00:00Z",
        }
    )
    settings["model_services"].append(
        {
            "config_id": "kimi-primary",
            "provider": "Kimi",
            "credential_ref": credential_ref,
            "plan": "STANDARD",
            "thinking": True,
            "model_name": "kimi-k3",
            "tier": "k3",
            "thinking_mode": "max",
            "api_protocol": "AUTO",
            "api_base_url": "",
            "api_platform": "AUTO",
            "connection_status": "UNVERIFIED",
        }
    )
    store.save(settings, expected_revision=current["revision"])
    runner = _KimiCatalogRunner()
    controller = SettingsController(
        store=store,
        credentials=CredentialReferenceManager(),
        capabilities=CapabilityProjection([], revision="fixture"),
        directory_policy=DirectoryPolicy([tmp_path]),
        model_validation_runner=runner,
    )

    receipt = controller.verify_api_model(config_id="kimi-primary")
    persisted = next(
        row
        for row in controller.get_state()["settings"]["model_services"]
        if row["config_id"] == "kimi-primary"
    )

    assert runner.endpoints == [
        "https://api.moonshot.cn/v1",
        "https://api.moonshot.ai/v1",
    ]
    assert receipt["status"] == "AVAILABLE"
    assert receipt["api_platform"] == "AI"
    assert receipt["external_model_calls"] == 0
    assert persisted["api_protocol"] == "OPENAI_COMPATIBLE"
    assert persisted["api_base_url"] == "https://api.moonshot.ai/v1"
    assert persisted["api_platform"] == "AUTO"

    second = controller.verify_api_model(config_id="kimi-primary")
    assert runner.endpoints == [
        "https://api.moonshot.cn/v1",
        "https://api.moonshot.ai/v1",
        "https://api.moonshot.cn/v1",
        "https://api.moonshot.ai/v1",
    ]
    assert second["api_platform"] == "AI"
    assert second["external_model_calls"] == 0


def _settings_with_kimi_service(tmp_path: Path) -> tuple[dict, str]:
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir(exist_ok=True)
    artifacts.mkdir(exist_ok=True)
    settings = default_settings(
        workspace_root=str(workspace), artifact_root=str(artifacts)
    )
    credential_ref = "REF:windows:Memorive/kimi/primary"
    settings["credential_references"].append(
        {
            "credential_ref": credential_ref,
            "provider": "Kimi",
            "logical_key": "primary",
            "status": "STORED_UNVERIFIED",
            "created_at": "2026-09-03T00:00:00Z",
            "updated_at": "2026-09-03T00:00:00Z",
        }
    )
    settings["model_services"].append(
        {
            "config_id": "kimi-primary",
            "provider": "Kimi",
            "credential_ref": credential_ref,
            "plan": "STANDARD",
            "thinking": True,
            "model_name": "kimi-k3",
            "tier": "k3",
            "thinking_mode": "max",
            "api_protocol": "OPENAI_COMPATIBLE",
            "api_base_url": "https://api.moonshot.cn/v1",
            "api_platform": "AUTO",
            "connection_status": "AVAILABLE",
        }
    )
    return settings, credential_ref


def test_kimi_platform_policy_contract_preserves_auto_resolution_cache(
    tmp_path: Path,
) -> None:
    settings, _ = _settings_with_kimi_service(tmp_path)

    accepted = validate_settings(settings)["model_services"][0]

    assert accepted["api_platform"] == "AUTO"
    assert accepted["api_base_url"] == "https://api.moonshot.cn/v1"


def test_kimi_platform_policy_contract_rejects_non_kimi_and_endpoint_mismatch(
    tmp_path: Path,
) -> None:
    settings, _ = _settings_with_kimi_service(tmp_path)
    settings["model_services"][0]["provider"] = "Example Relay"
    settings["model_services"][0]["credential_ref"] = (
        "REF:windows:Memorive/example-relay/primary"
    )
    settings["credential_references"][0]["credential_ref"] = (
        "REF:windows:Memorive/example-relay/primary"
    )
    settings["credential_references"][0]["provider"] = "Example Relay"
    with pytest.raises(ValueError, match="MODEL_API_PLATFORM_PROVIDER_INVALID"):
        validate_settings(settings)

    settings, _ = _settings_with_kimi_service(tmp_path)
    settings["model_services"][0]["api_platform"] = "AI"
    with pytest.raises(ValueError, match="MODEL_API_PLATFORM_CONNECTION_MISMATCH"):
        validate_settings(settings)


class _CredentialRecorder:
    def bind(
        self,
        *,
        provider: str,
        logical_key: str,
        secret_input: str,
        created_at: str,
    ) -> dict[str, str]:
        assert secret_input == "replacement-value"
        return {
            "credential_ref": f"REF:windows:Memorive/{provider.casefold()}/{logical_key}",
            "provider": provider,
            "logical_key": logical_key,
            "status": "STORED_UNVERIFIED",
            "created_at": created_at,
            "updated_at": "2026-09-03T01:00:00Z",
        }


def test_replacing_kimi_key_invalidates_auto_route_cache(tmp_path: Path) -> None:
    settings, credential_ref = _settings_with_kimi_service(tmp_path)
    explicit = deepcopy(settings["model_services"][0])
    explicit.update(
        config_id="kimi-explicit-cn",
        model_name="kimi-k3-explicit",
        tier="explicit",
        api_platform="CN",
    )
    settings["model_services"].append(explicit)
    profile = tmp_path / "profile"
    store = SettingsStore(
        profile,
        default_factory=lambda: deepcopy(settings),
    )
    current = store.load()
    controller = SettingsController(
        store=store,
        credentials=_CredentialRecorder(),
        capabilities=CapabilityProjection([], revision="fixture"),
        directory_policy=DirectoryPolicy([tmp_path]),
    )

    controller.credential_replace(
        credential_ref=credential_ref,
        secret_input="replacement-value",
        expected_revision=current["revision"],
    )
    persisted = {
        row["config_id"]: row
        for row in controller.get_state()["settings"]["model_services"]
    }

    assert set(persisted) == {"kimi-primary", "kimi-explicit-cn"}
    assert all(row["connection_status"] == "UNVERIFIED" for row in persisted.values())
    assert persisted["kimi-primary"]["api_platform"] == "AUTO"
    assert persisted["kimi-primary"]["api_protocol"] == "AUTO"
    assert persisted["kimi-primary"]["api_base_url"] == ""
    assert persisted["kimi-explicit-cn"]["api_platform"] == "CN"
    assert persisted["kimi-explicit-cn"]["api_protocol"] == "OPENAI_COMPATIBLE"
    assert persisted["kimi-explicit-cn"]["api_base_url"] == (
        "https://api.moonshot.cn/v1"
    )
