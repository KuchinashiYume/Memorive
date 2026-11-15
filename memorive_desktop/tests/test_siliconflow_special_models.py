from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from memorive_settings.capability import CapabilityProjection  # noqa: E402
from memorive_settings.contracts import default_settings, validate_settings  # noqa: E402
from memorive_settings.controller import SettingsController  # noqa: E402
from memorive_settings.credentials import CredentialReferenceManager  # noqa: E402
from memorive_settings.directory_policy import DirectoryPolicy  # noqa: E402
from memorive_settings.model_capabilities import (  # noqa: E402
    infer_api_model_capability,
    workflow_node_accepts_capability,
)
from memorive_settings.model_validation import (  # noqa: E402
    HttpResponse,
    LiveModelValidationRunner,
)
from memorive_settings.store import SettingsStore  # noqa: E402


SILICONFLOW_REFERENCE = "REF:windows:Memorive/siliconflow/primary"


class CatalogTransport:
    def __init__(self, model_ids: list[str]) -> None:
        self.model_ids = model_ids
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> HttpResponse:
        self.calls.append(dict(kwargs))
        return HttpResponse(
            status_code=200,
            body=json.dumps(
                {"data": [{"id": model_id} for model_id in self.model_ids]}
            ).encode("utf-8"),
        )


class CredentialBackend:
    def __init__(self) -> None:
        self.values = {"Memorive/siliconflow/primary": b"fixture-key"}
        self.delete_count = 0

    def write(self, target: str, secret: bytes) -> None:
        self.values[target] = bytes(secret)

    def read(self, target: str) -> bytes:
        return self.values[target]

    def delete(self, target: str) -> None:
        self.values.pop(target, None)
        self.delete_count += 1

    def exists(self, target: str) -> bool:
        return target in self.values


def _credential_reference() -> dict[str, object]:
    return {
        "credential_ref": SILICONFLOW_REFERENCE,
        "provider": "SILICONFLOW_API_KEY",
        "logical_key": "primary",
        "status": "STORED_UNVERIFIED",
        "created_at": "2026-08-29T00:00:00Z",
        "updated_at": "2026-08-29T00:00:00Z",
    }


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


def _settings(tmp_path: Path) -> dict[str, object]:
    settings = default_settings(
        workspace_root=str(tmp_path / "workspace"),
        artifact_root=str(tmp_path / "artifacts"),
    )
    settings["credential_references"].append(_credential_reference())
    settings["model_services"].extend(
        [
            _service("siliconflow-bge-m3", "Pro/BAAI/bge-m3", "Pro"),
            _service(
                "siliconflow-deepseek-ocr",
                "deepseek-ai/DeepSeek-OCR",
                "OCR",
            ),
        ]
    )
    for node in settings["workflow"]["nodes"]:
        if node["node_id"] == "chunk_embedding":
            node["profile_ref"] = "siliconflow-bge-m3"
    return settings


@pytest.mark.parametrize(
    ("model_name", "expected_capability"),
    [
        ("Pro/BAAI/bge-m3", "EMBEDDING"),
        ("BAAI/bge-m3", "EMBEDDING"),
        ("deepseek-ai/DeepSeek-OCR", "VISION_OCR"),
    ],
)
def test_siliconflow_official_auto_catalog_accepts_special_models_without_generation(
    tmp_path: Path,
    model_name: str,
    expected_capability: str,
) -> None:
    transport = CatalogTransport([model_name])
    runner = LiveModelValidationRunner(
        credential_resolver=lambda _reference: b"fixture-key",
        preferences_loader=lambda: {
            "proxy_mode": "NONE",
            "proxy_address": "",
            "request_timeout_seconds": 7,
        },
        scratch_root=tmp_path / "validation",
        http_transport=transport,
    )

    result = runner.verify_api_model(
        _service("siliconflow-model", model_name, "Pro")
    )

    assert result["status"] == "AVAILABLE"
    assert result["returned_model"] == model_name
    assert result["model_capability"] == expected_capability
    assert result["validation_endpoint_kind"] == "MODEL_CATALOG"
    assert result["external_model_calls"] == 0
    assert len(transport.calls) == 1
    assert transport.calls[0]["method"] == "GET"
    assert transport.calls[0]["url"] == "https://api.siliconflow.cn/v1/models"


def test_special_model_capabilities_are_restricted_to_the_correct_phase1_nodes() -> None:
    assert infer_api_model_capability(
        "SILICONFLOW_API_KEY", "Pro/BAAI/bge-m3"
    ) == "EMBEDDING"
    assert infer_api_model_capability(
        "SiliconFlow", "deepseek-ai/DeepSeek-OCR"
    ) == "VISION_OCR"
    assert infer_api_model_capability("OpenAI", "gpt-5.6") == "CHAT"
    assert workflow_node_accepts_capability("chunk_embedding", "EMBEDDING") is True
    assert workflow_node_accepts_capability("context_pack", "RERANKER") is True
    assert workflow_node_accepts_capability("context_pack", "EMBEDDING") is False
    assert workflow_node_accepts_capability("ingest", "CHAT") is True
    assert workflow_node_accepts_capability("ingest", "VISION_OCR") is True
    assert workflow_node_accepts_capability("analysis", "EMBEDDING") is False
    assert workflow_node_accepts_capability("card_distill", "VISION_OCR") is False


def test_two_siliconflow_models_share_one_credential_without_erasing_other_settings(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    preserved = {
        key: deepcopy(settings[key])
        for key in ("cli_services", "directories", "preferences")
    }

    accepted = validate_settings(settings)

    assert [row["model_name"] for row in accepted["model_services"]] == [
        "Pro/BAAI/bge-m3",
        "deepseek-ai/DeepSeek-OCR",
    ]
    assert {
        row["credential_ref"] for row in accepted["model_services"]
    } == {SILICONFLOW_REFERENCE}
    for key, value in preserved.items():
        assert accepted[key] == value


def test_special_models_cannot_be_mapped_to_chat_nodes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for node in settings["workflow"]["nodes"]:
        if node["node_id"] == "analysis":
            node["profile_ref"] = "siliconflow-bge-m3"

    with pytest.raises(
        ValueError,
        match="WORKFLOW_MODEL_CAPABILITY_MISMATCH:analysis:EMBEDDING",
    ):
        validate_settings(settings)


def test_removing_one_shared_model_keeps_the_other_model_credential_and_preferences(
    tmp_path: Path,
) -> None:
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
    settings = _settings(tmp_path)
    store.save(settings, expected_revision=initial["revision"])
    backend = CredentialBackend()
    controller = SettingsController(
        store=store,
        credentials=CredentialReferenceManager(backend=backend),
        capabilities=CapabilityProjection([], revision="fixture"),
        directory_policy=DirectoryPolicy([tmp_path]),
    )
    before = controller.get_state()["settings"]

    receipt = controller.model_service_remove(
        config_id="siliconflow-bge-m3",
        expected_revision=controller.get_state()["revision"],
    )
    after = controller.get_state()["settings"]

    assert receipt["removed_config_id"] == "siliconflow-bge-m3"
    assert receipt["credential_deleted"] is False
    assert backend.delete_count == 0
    assert [row["config_id"] for row in after["model_services"]] == [
        "siliconflow-deepseek-ocr"
    ]
    assert after["credential_references"] == before["credential_references"]
    assert after["cli_services"] == before["cli_services"]
    assert after["directories"] == before["directories"]
    assert after["preferences"] == before["preferences"]
    embedding = next(
        row for row in after["workflow"]["nodes"] if row["node_id"] == "chunk_embedding"
    )
    ingest = next(
        row for row in after["workflow"]["nodes"] if row["node_id"] == "ingest"
    )
    assert embedding["profile_ref"] is None
    assert ingest["profile_ref"] == "PARSER_PROFILE"


