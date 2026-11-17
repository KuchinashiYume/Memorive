from copy import deepcopy
from page_sources import settings_scripts
import json
from pathlib import Path

import pytest
from memorive.desktop.settings.capability import CapabilityProjection
from memorive.desktop.settings.contracts import default_settings
from memorive.desktop.settings.controller import SettingsController
from memorive.desktop.settings.credentials import CredentialReferenceManager
from memorive.desktop.settings.directory_policy import DirectoryPolicy
from memorive.desktop.settings.external_sources import ExternalDataSources
from memorive.models.validation.transport import HttpResponse
from memorive.models.validation.runner import LiveModelValidationRunner
from memorive.desktop.settings.service_adapter import SettingsServiceAdapter, SETTINGS_METHODS
from memorive.desktop.settings.store import SettingsStore


def controller(root, transport=None):
    store = SettingsStore(root / "profile", default_factory=lambda: default_settings(
        workspace_root=str(root), artifact_root=str(root)))
    sources = ExternalDataSources(root / "profile/external_data", transport=transport, environment={})
    return SettingsController(store=store, credentials=CredentialReferenceManager(),
        capabilities=CapabilityProjection([], revision="external-test"), directory_policy=DirectoryPolicy([root]),
        external_data_sources=sources)


class MetadataTransport:
    def __init__(self): self.calls = []
    def request(self, **kw):
        self.calls.append(kw)
        return HttpResponse(200, json.dumps({"data":[{"id":"test-model", "name":"Test model",
            "context_length":32000,"pricing":{"prompt":"0.000001"},"architecture":{"input_modalities":["text"]},
            "benchmarks":{"artificial_analysis":{"intelligence_index":82}}}]}).encode())


def test_real_controller_bridge_roundtrip_without_legacy_settings_mutation(tmp_path):
    transport = MetadataTransport()
    c = controller(tmp_path, transport)
    adapter = SettingsServiceAdapter(c)
    before = c.store.path.read_bytes() if c.store.path.exists() else None
    original = c.get_state()
    before = c.store.path.read_bytes()
    state = adapter.call("settings.external_sources_get", {})
    row = deepcopy(state["sources"][2]["config"])
    row["display_name"] = "My OpenRouter"
    saved = adapter.call("settings.external_sources_save", {"source":row,"expected_revision":state["revision"]})
    receipt = adapter.call("settings.external_sources_refresh", {"source_id":"openrouter", "expected_revision":saved["revision"]})
    assert receipt["status"] == "PASS", receipt
    assert transport.calls[0]["method"] == "GET" and transport.calls[0]["body"] is None
    assert not transport.calls[0]["headers"].get("Authorization")
    assert c.store.path.read_bytes() == before
    reference = c.external_data_sources.references("ANALYSIS_PRIMARY", {"model_name":"test-model"})
    assert reference["reference_only"] and reference["internal_exam_score"] is None
    assert any(r["metrics"].get("artificial_analysis_intelligence_index") == 82 for r in reference["references"])
    assert original["workflow_exam_score_catalog"] == c.get_state()["workflow_exam_score_catalog"]
    assert c.metrics["external_model_calls"] == 0
    assert c.metrics["external_network_calls"] == 1
    with pytest.raises(ValueError): adapter.call("settings.external_sources_refresh", {"source_id":"openrouter", "expected_revision":0})


def test_endpoint_replacement_does_not_inherit_old_credentials_or_cache(tmp_path):
    c = controller(tmp_path, MetadataTransport())
    s = c.external_sources_get()
    row = s["sources"][0]["config"]
    row["endpoint"] = "https://example.org/reference"
    row["credential_origin"] = "https://example.org"
    row["credential_env"] = "FIXTURE_REFERENCE_KEY"
    with pytest.raises(ValueError, match="REBIND"):
        c.external_sources_save(source=row, expected_revision=0)
    row.update(credential_env="", credential_origin="", format="REFERENCE_JSON_V1")
    saved = c.external_sources_save(source=row, expected_revision=0)
    assert saved["sources"][0]["status"] == "NOT_FETCHED"


def test_new_methods_are_in_all_packaged_bridge_allowlists():
    root = Path(__file__).resolve().parents[1]
    for method in ("settings.external_sources_get", "settings.external_sources_save", "settings.external_sources_refresh", "settings.external_model_reference"):
        assert method in SETTINGS_METHODS
        assert method in settings_scripts()
        assert method in (root / "source/memorive/desktop/api/common.py").read_text(encoding="utf-8")


def test_os_lock_and_revision_protect_separate_controller_instances(tmp_path):
    a, b = controller(tmp_path), controller(tmp_path)
    row = a.external_sources_get()["sources"][0]["config"]
    with a.external_data_sources._write_lock():
        with pytest.raises(ValueError, match="WRITE_IN_PROGRESS"):
            b.external_sources_save(source=row, expected_revision=0)
    a.external_sources_save(source=row, expected_revision=0)
    with pytest.raises(ValueError, match="REVISION_CONFLICT"):
        b.external_sources_save(source=row, expected_revision=0)


def test_partial_observation_cannot_overwrite_existing_full_node_score(tmp_path):
    from memorive.exams.tiers import tier_manifest
    c = controller(tmp_path)
    current = c.store.load()
    settings = deepcopy(current["settings"])
    node = next(r for r in settings["workflow"]["nodes"] if r["node_id"] == "card_distill")
    node["test_score"] = 93
    c.store.save(settings, expected_revision=current["revision"])
    before = c.store.path.read_bytes()
    class Runner:
        def test_workflow_node(self, node, target, *, context, authorization):
            assert context["workflow_exam_tier"] == "LIGHT"
            return dict(status="PASS", reason="SYNTHETIC_SUBSET", score=42, duration_ms=0,
                        exam_tier=tier_manifest(role="CARD_DISTILLER", tier="LIGHT",
                                               full_ids=list("abcde"), selected_ids=["a"]))
    c.workflow_exams = Runner()
    result = c.test_workflow_node(node_id="card_distill", exam_tier="LIGHT")
    assert result["score"] == 42 and result["tier_result_is_provisional"]
    assert not result["persistent_mutation"] and c.store.path.read_bytes() == before


def test_metadata_only_call_cannot_be_dispatched_to_model_executor(tmp_path):
    c = controller(tmp_path, MetadataTransport())
    c.external_sources_refresh(source_id="openrouter", expected_revision=0)
    assert c.metrics["model_validation_requests"] == 0
    assert c.metrics["provider_calls"] == 0 and c.metrics["external_model_calls"] == 0


def test_unscored_tier_keeps_coverage_without_presenting_old_score_as_new(tmp_path):
    from memorive.exams.tiers import tier_manifest
    c = controller(tmp_path)
    current = c.store.load()
    settings = deepcopy(current["settings"])
    next(r for r in settings["workflow"]["nodes"] if r["node_id"] == "analysis")["test_score"] = 93
    c.store.save(settings, expected_revision=current["revision"])
    before = c.store.path.read_bytes()
    class Runner:
        def test_workflow_node(self, node, target, *, context, authorization):
            return dict(status="NOT_RUN", reason="PANEL_COHORT_PENDING", duration_ms=0,
                exam_tier=tier_manifest(role="ANALYSIS_PRIMARY", tier="LIGHT", full_ids=list("abcde"), selected_ids=["a"]))
    c.workflow_exams = Runner()
    result = c.test_workflow_node(node_id="analysis", exam_tier="LIGHT")
    assert result["exam_tier"]["tier"] == "LIGHT"
    assert result["attempt_score"] is None
    assert result["score"] == 93 and result["existing_score_preserved"] is True
    assert not result["persistent_mutation"] and c.store.path.read_bytes() == before


def test_corrupt_external_configuration_does_not_block_internal_settings(tmp_path):
    c = controller(tmp_path)
    original = c.get_state()["settings"]
    c.external_data_sources.path.write_text('{"sha256":"invalid"}', encoding="utf-8")
    state = c.get_state()
    assert state["settings"] == original
    assert state["external_data_sources"]["status"] == "UNAVAILABLE"


def test_corrupt_cache_is_not_used_as_score_and_can_be_refreshed(tmp_path):
    c = controller(tmp_path, MetadataTransport())
    c.external_sources_refresh(source_id="openrouter", expected_revision=0)
    cache = next(c.external_data_sources.root.glob('snapshots/openrouter/*.json'))
    cache.write_text('{}', encoding="utf-8")
    assert c.external_sources_get()["sources"][2]["status"] == "CACHE_INVALID"
    reference = c.external_data_sources.references("ANALYSIS_PRIMARY", {"model_name":"test-model"})
    assert not reference["references"] and reference["unavailable_sources"] == ["openrouter"]
    assert c.external_sources_refresh(source_id="openrouter", expected_revision=0)["status"] == "PASS"
