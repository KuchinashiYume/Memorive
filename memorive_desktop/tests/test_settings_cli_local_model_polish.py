from __future__ import annotations

from copy import deepcopy
from page_sources import settings_scripts
import json
from pathlib import Path
import sys


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
for candidate in (SOURCE_ROOT, ATTEMPT_ROOT / "product" / "desktop"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive.desktop.current_task.workflow_mapping import build_core_execution_snapshot  # noqa: E402
from memorive.models.local.product_controller import LocalModelProductController  # noqa: E402
from memorive.desktop.settings.contracts import default_settings, validate_settings  # noqa: E402


def _recognized_local_model(tmp_path: Path) -> tuple[LocalModelProductController, dict[str, object]]:
    controller = LocalModelProductController(tmp_path / "local-models")
    state = json.loads(controller.state_path.read_text(encoding="utf-8"))
    endpoint = next(
        row for row in state["endpoints"].values() if row["kind"] == "ollama"
    )
    endpoint["last_verification"] = {
        "endpoint_id": endpoint["endpoint_id"],
        "verified_at": "2026-08-29T00:00:00Z",
        "service_reachable": True,
        "model_listed": True,
        "models": ["qwen3:8b"],
        "inference_run": False,
        "status": "MODEL_LISTED_INFERENCE_NOT_RUN",
    }
    controller.state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    projection = controller.call("local_models.list", {})
    return controller, projection["recognized_models"][0]


def test_cli_save_retries_one_fresh_revision_without_losing_collected_draft() -> None:
    bridge = settings_scripts()
    save_block = bridge.split("const saveCliServices = async", 1)[1].split(
        "const save = async", 1
    )[0]

    assert "EXPECTED_REVISION_CONFLICT" in save_block
    assert "settings.get_state" in save_block
    assert "retry_revision" in save_block
    assert "cli_services: cliServices" in save_block
    assert save_block.count("settings.save_cli_services") >= 2


def test_cli_validation_is_serialized_and_save_conflict_stays_not_run() -> None:
    bridge = settings_scripts()
    validation = bridge.split("const runCliValidation = async", 1)[1].split(
        "const runWorkflowNodeTest", 1
    )[0]

    assert "cliValidationInFlight" in validation
    assert "CLI_VALIDATION_IN_PROGRESS" in validation
    assert "status: 'NOT_RUN'" in validation


def test_recognized_local_models_have_stable_profiles_and_phase1_mapping(
    tmp_path: Path,
) -> None:
    _controller, local_profile = _recognized_local_model(tmp_path)
    profile_ref = str(local_profile["profile_ref"])

    assert profile_ref.startswith("local:")
    assert local_profile["display_name"] == "[本地] qwen3:8b"
    assert local_profile["model_name"] == "qwen3:8b"
    assert local_profile["capability"] == "CHAT"

    settings = default_settings(
        workspace_root=str(tmp_path / "workspace"),
        artifact_root=str(tmp_path / "artifacts"),
    )
    for node in settings["workflow"]["nodes"]:
        if node["node_id"] in {"card_distill", "analysis"}:
            node["profile_ref"] = profile_ref
    accepted = validate_settings(settings)
    snapshot = build_core_execution_snapshot(
        accepted,
        settings_revision=7,
        local_model_profiles=[deepcopy(local_profile)],
    )

    assert snapshot["workflow_config"]["nodes"]["03_CARD_DISTILL"]["profile_ref"] == profile_ref
    assert snapshot["workflow_config"]["nodes"]["03_CARD_DISTILL"]["model"] == "[本地] qwen3:8b"
    assert snapshot["workflow_config"]["nodes"]["03_CARD_DISTILL"]["provider"] == "[本地] Ollama"
    assert snapshot["configuration_ready"] is False

