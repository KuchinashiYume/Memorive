from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from memorive_settings.capability import CapabilityProjection  # noqa: E402
from memorive_settings.contracts import (  # noqa: E402
    canonical_json_bytes,
    canonical_sha256,
    default_settings,
    validate_settings,
)
from memorive_settings.controller import SettingsController  # noqa: E402
from memorive_settings.credentials import CredentialReferenceManager  # noqa: E402
from memorive_settings.directory_policy import DirectoryPolicy  # noqa: E402
from memorive.models.validation import FailClosedModelValidationRunner  # noqa: E402
from memorive_settings.workflow_exam_runner import FailClosedWorkflowExams  # noqa: E402
from memorive_settings.store import SettingsStore  # noqa: E402


EXPECTED_ADAPTERS = {
    "codex_cli",
    "claude_code",
    "gemini_cli",
    "qwen_code",
    "kimi_code",
    "codebuddy_code",
    "github_copilot_cli",
}


def _cli_model(profile_ref: str = "cli:codex-cli:gpt-5.6") -> dict[str, object]:
    return {
        "profile_ref": profile_ref,
        "display_name": "GPT 5.6",
        "model_name": "gpt-5.6",
        "thinking_mode": "high",
        "connection_status": "UNVERIFIED",
    }


def _settings_for_paths(workspace: Path, artifacts: Path) -> dict[str, object]:
    settings = default_settings(
        workspace_root=str(workspace),
        artifact_root=str(artifacts),
    )
    settings["cli_services"][0]["enabled"] = True
    settings["cli_services"][0]["models"].append(_cli_model())
    nodes = {row["node_id"]: row for row in settings["workflow"]["nodes"]}
    nodes["analysis"]["profile_ref"] = "cli:codex-cli:gpt-5.6"
    return settings


def test_cli_contract_seeds_all_adapted_clis_and_accepts_many_models() -> None:
    settings = default_settings()

    assert {row["adapter_id"] for row in settings["cli_services"]} == EXPECTED_ADAPTERS
    assert all(row["models"] == [] for row in settings["cli_services"])

    codex = settings["cli_services"][0]
    codex["enabled"] = True
    codex["models"] = [
        _cli_model(),
        {
            **_cli_model("cli:codex-cli:o3"),
            "display_name": "O3",
            "model_name": "o3",
            "thinking_mode": "",
        },
    ]
    nodes = {row["node_id"]: row for row in settings["workflow"]["nodes"]}
    nodes["card_distill"]["profile_ref"] = "cli:codex-cli:o3"
    nodes["card_distill"]["test_score"] = 91

    accepted = validate_settings(settings)

    assert len(accepted["cli_services"][0]["models"]) == 2
    accepted_nodes = {
        row["node_id"]: row for row in accepted["workflow"]["nodes"]
    }
    assert accepted_nodes["card_distill"]["test_score"] == 91


def test_cli_contract_rejects_duplicate_profile_and_invalid_score() -> None:
    settings = default_settings()
    settings["cli_services"][0]["models"] = [_cli_model(), _cli_model()]
    with pytest.raises(ValueError, match="CLI_MODEL_PROFILE_REF_INVALID"):
        validate_settings(settings)

    settings = default_settings()
    next(
        row for row in settings["workflow"]["nodes"]
        if row["node_id"] == "card_distill"
    )["test_score"] = 101
    with pytest.raises(ValueError, match="WORKFLOW_TEST_SCORE_INVALID"):
        validate_settings(settings)


def test_phase1_execution_snapshot_recognizes_enabled_cli_model() -> None:
    from memorive_current_task.workflow_mapping import build_core_execution_snapshot

    settings = default_settings()
    settings["cli_services"][0]["enabled"] = True
    settings["cli_services"][0]["models"].append(_cli_model())
    nodes = {row["node_id"]: row for row in settings["workflow"]["nodes"]}
    nodes["card_distill"]["profile_ref"] = "cli:codex-cli:gpt-5.6"
    nodes["analysis"]["profile_ref"] = "cli:codex-cli:gpt-5.6"

    snapshot = build_core_execution_snapshot(settings, settings_revision=42)

    node = snapshot["workflow_config"]["nodes"]["03_CARD_DISTILL"]
    assert node["profile_ref"] == "cli:codex-cli:gpt-5.6"
    assert node["model"] == "GPT 5.6"
    assert node["provider"] == "Codex CLI"
    assert snapshot["configuration_ready"] is False


def test_existing_v4_store_shape_migrates_cli_and_node_score(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    old_settings = default_settings()
    old_settings["schema_version"] = "SettingsTemplate-v4"
    old_settings.pop("cli_services")
    old_settings["workflow"]["nodes"] = [
        node for node in old_settings["workflow"]["nodes"]
        if node["node_id"] != "chunk_embedding"
    ]
    for node in old_settings["workflow"]["nodes"]:
        node.pop("test_score")
        node.pop("profile_source_node_id")
    envelope = {
        "schema_version": "SettingsStoreEnvelope-v4",
        "revision": 7,
        "template_id": "settings-template-000007",
        "updated_at": "2026-08-29T00:00:00Z",
        "settings": old_settings,
        "settings_sha256": canonical_sha256(old_settings),
    }
    (profile / "settings.json").write_bytes(canonical_json_bytes(envelope) + b"\n")

    loaded = SettingsStore(profile).load(recover_corruption=False)

    assert loaded["revision"] == 8
    assert {row["adapter_id"] for row in loaded["settings"]["cli_services"]} == EXPECTED_ADAPTERS
    assert all(node["test_score"] is None for node in loaded["settings"]["workflow"]["nodes"])
    receipt = json.loads((profile / "settings_migration.json").read_text(encoding="utf-8"))
    assert receipt["from_schema"] == "SettingsTemplate-v4"


class FakeValidationRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def verify_api_model(self, service: dict[str, object]) -> dict[str, object]:
        self.calls.append(("api", str(service["config_id"])))
        return {"status": "AVAILABLE", "reason": "MOCK_MINIMAL_PROBE_PASS"}

    def verify_cli_model(
        self,
        cli_service: dict[str, object],
        model: dict[str, object],
    ) -> dict[str, object]:
        self.calls.append(("cli", str(model["profile_ref"])))
        return {"status": "AVAILABLE", "reason": "MOCK_MINIMAL_PROBE_PASS"}

    def test_workflow_node(
        self,
        node: dict[str, object],
        target: dict[str, object],
    ) -> dict[str, object]:
        self.calls.append(("node", str(node["node_id"])))
        return {"status": "PASS", "score": 87, "reason": "MOCK_SCORE"}


def test_validation_methods_persist_api_cli_status_and_node_score(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifacts"
    profile = tmp_path / "profile"
    workspace.mkdir()
    artifacts.mkdir()
    factory = lambda: _settings_for_paths(workspace, artifacts)
    store = SettingsStore(profile, default_factory=factory)
    state = store.load()
    settings = deepcopy(state["settings"])
    credential_ref = "REF:windows:Memorive/provider-fixture/primary"
    settings["credential_references"].append(
        {
            "credential_ref": credential_ref,
            "provider": "Provider Fixture",
            "logical_key": "primary",
            "status": "STORED_UNVERIFIED",
            "created_at": "2026-08-29T00:00:00Z",
            "updated_at": "2026-08-29T00:00:00Z",
        }
    )
    settings["model_services"].append(
        {
            "config_id": "provider-fixture-primary",
            "provider": "Provider Fixture",
            "credential_ref": credential_ref,
            "plan": "STANDARD",
            "thinking": False,
            "model_name": "fixture-model",
            "tier": "standard",
            "thinking_mode": "",
            "connection_status": "UNVERIFIED",
        }
    )
    store.save(settings, expected_revision=state["revision"])
    runner = FakeValidationRunner()
    controller = SettingsController(
        store=store,
        credentials=CredentialReferenceManager(),
        capabilities=CapabilityProjection([], revision="fixture"),
        directory_policy=DirectoryPolicy([tmp_path]),
        model_validation_runner=runner,
        workflow_exams=runner,
    )

    api_receipt = controller.verify_api_model(config_id="provider-fixture-primary")
    cli_receipt = controller.verify_cli_model(
        config_id="codex-cli",
        profile_ref="cli:codex-cli:gpt-5.6",
    )
    node_receipt = controller.test_workflow_node(node_id="analysis")
    persisted = controller.get_state()["settings"]

    assert api_receipt["connection_status"] == "AVAILABLE"
    assert cli_receipt["connection_status"] == "AVAILABLE"
    assert node_receipt["score"] == 87
    assert persisted["model_services"][0]["connection_status"] == "AVAILABLE"
    assert persisted["cli_services"][0]["models"][0]["connection_status"] == "AVAILABLE"
    persisted_nodes = {
        row["node_id"]: row for row in persisted["workflow"]["nodes"]
    }
    assert persisted_nodes["analysis"]["test_score"] == 87
    assert runner.calls == [
        ("api", "provider-fixture-primary"),
        ("cli", "cli:codex-cli:gpt-5.6"),
        ("node", "analysis"),
    ]
    assert controller.effect_metrics()["external_network_calls"] == 0
    assert controller.effect_metrics()["provider_calls"] == 0
    assert controller.effect_metrics()["external_model_calls"] == 0


def test_default_validation_boundary_is_fail_closed_and_never_calls_a_model() -> None:
    runner = FailClosedModelValidationRunner()

    api = runner.verify_api_model({"config_id": "api-fixture"})
    cli = runner.verify_cli_model(
        {"config_id": "codex-cli"},
        {"profile_ref": "cli:codex-cli:gpt-5.6"},
    )
    node = FailClosedWorkflowExams().test_workflow_node(
        {"node_id": "analysis"},
        {"kind": "CLI", "profile_ref": "cli:codex-cli:gpt-5.6"},
    )

    assert {api["status"], cli["status"], node["status"]} == {"NOT_RUN"}
    for receipt in (api, cli, node):
        assert receipt["external_network_calls"] == 0
        assert receipt["provider_calls"] == 0
        assert receipt["external_model_calls"] == 0


