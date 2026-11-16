"""Bounded regressions for the seven sample feedback items; no inference."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from memorive_settings.external_sources import ExternalDataSources, default_sources, role_references
from memorive.models.validation.transport import HttpResponse
from memorive_settings.contracts import default_settings
from memorive.platform.json_files import canonical_sha256
from memorive_settings.controller import SettingsController
from memorive_settings.store import SettingsStore

ROOT = Path(__file__).resolve().parents[1]

class Transport:
    def __init__(self):
        self.calls = []
        self.status = 200
    def request(self, **kwargs):
        self.calls.append(kwargs)
        return HttpResponse(status_code=self.status, body=json.dumps({"schema_version":"ExternalModelReference-v1", "data":[
            {"model_id":"fixture-model", "metrics":{"eci":42}}]}).encode())

def test_weekly_defaults_and_state():
    assert all(row["refresh_mode"] == "WEEKLY" for row in default_sources())

def test_disabled_or_missing_credentials_never_make_automatic_requests(tmp_path):
    transport = Transport()
    service = ExternalDataSources(tmp_path, transport=transport, environment={})
    from legacy_aa_fixture import configure_aa
    configure_aa(service)
    for item in service.get_state()["sources"]:
        config = deepcopy(item["config"])
        if config["source_id"] != "artificial-analysis":
            config["enabled"] = False
            service.save(config, expected_revision=service.get_state()["revision"])
    assert service.refresh_due()["updated"] == []
    assert not transport.calls


def epoch(model_id, value):
    return dict(source_id="epoch-ai", upstream_source="epoch-ai", model_id=model_id,
                model_slug=model_id, model_name=model_id, metrics={"eci":value},
                identity_scope="MODEL_CAPABILITY_UPPER_ENVELOPE")

def test_epoch_reasoning_suffix_matches_selected_model_not_a_dated_revision():
    rows = [epoch("gpt-5.6-sol_high", 162.03), epoch("gpt-5.6-sol_max", 162.03), epoch("gpt-5.6-sol-9999_high", 170)]
    result = role_references("ANALYSIS_PRIMARY", {"model_name":"gpt-5.6-sol", "thinking_mode":"high"}, rows)
    assert result["references"][0]["model_id"] == "gpt-5.6-sol_high"
    assert result["references"][0]["metrics"] == {"eci":162.03}
    assert result["exact_execution_profile_assessed"] is False

def test_epoch_envelope_can_match_without_falsely_claiming_effort_equivalence():
    rows = [epoch("deepseek-v4-flash_max", 146.26), epoch("deepseek-v4-flash_unknown", 146.26)]
    result = role_references("CARD_DISTILLER", {"model_name":"deepseek-v4-flash", "thinking":True}, rows)
    assert result["references"][0]["metrics"] == {"eci":146.26}
    assert result["references"][0]["match_kind"] == "MODEL_ENVELOPE_REFERENCE"

def test_conflicting_effort_scores_do_not_get_guessed_without_exact_effort():
    rows = [epoch("gpt-5.6-sol_low", 150), epoch("gpt-5.6-sol_max", 162)]
    assert not role_references("ANALYSIS_PRIMARY", {"model_name":"gpt-5.6-sol"}, rows)["references"]

def test_openrouter_official_provider_prefix_matches_without_price_as_score():
    row = dict(source_id="openrouter", upstream_source="openrouter", model_id="openai/gpt-5.6-sol", model_name="OpenAI: GPT-5.6 Sol",
               metrics={}, operational={"context_length":1000})
    result = role_references("ANALYSIS_PRIMARY", {"model_name":"gpt-5.6-sol"}, [row])
    assert result["references"][0]["metrics"] == {}
    assert result["reference_only"] is True

def test_existing_scores_import_is_idempotent_and_preserves_all_other_settings(tmp_path):
    store = SettingsStore(tmp_path, default_factory=lambda:default_settings(workspace_root=str(tmp_path/'workspace'), artifact_root=str(tmp_path/'artifacts')))
    state = store.load()
    next(n for n in state["settings"]["workflow"]["nodes"] if n["node_id"] == "card_distill")["test_score"] = 59
    store.save(state["settings"], expected_revision=state["revision"])
    class Runner:
        def cached_workflow_node_score(self, node, target):
            return {"score":90} if node["node_id"] == "card_distill" else None
    controller = SettingsController(store=store, credentials=SimpleNamespace(), capabilities=SimpleNamespace(snapshot=lambda:{}), directory_policy=SimpleNamespace(), workflow_exams=Runner())
    before = store.load()
    result = controller.import_cached_exam_scores()
    after = store.load()
    assert result["status"] == "IMPORTED"
    expected = deepcopy(before["settings"])
    next(n for n in expected["workflow"]["nodes"] if n["node_id"] == "card_distill")["test_score"] = 90
    assert after["settings"] == expected
    assert controller.import_cached_exam_scores()["status"] == "UNCHANGED"
    assert store.load()["revision"] == after["revision"]
    assert list((tmp_path/'exam_score_imports').glob('*.json'))

