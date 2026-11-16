"""Two settings saved from the desktop: an imported settings bundle and the AI briefing settings.

An imported bundle must fill exactly the existing source slots and may not move a source that
uses a credential to another site; the briefing settings accept only known sources, a model that
is configured and eligible, and a custom range of at most a year that ends no later than today.
"""
from __future__ import annotations

import copy
import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from memorive.research.reports import models as report_models
from memorive.research.briefing.jobs import DEFAULT_CONFIG, AIBriefing
from memorive.research.briefing.sources import SOURCES
from memorive.research.common import defaults as research_defaults
from memorive.desktop.settings.export_bundle import validate as validate_extensions
from memorive.desktop.settings.external_sources import ExternalDataSources
from memorive.desktop.settings.leaderboard import DEFAULTS as LEADERBOARD_DEFAULTS


# Imported settings bundle

@pytest.fixture
def owner(tmp_path):
    return SimpleNamespace(external_data_sources=ExternalDataSources(tmp_path / "sources"))


def _extensions(owner, **changes):
    value = {"external_sources": copy.deepcopy(owner.external_data_sources._load()["sources"]),
             "literature_selected": ["crossref", "arxiv"], "leaderboard": copy.deepcopy(LEADERBOARD_DEFAULTS),
             "research": research_defaults()}
    value.update(changes)
    return value


def test_a_bundle_filling_the_existing_slots_is_accepted(owner):
    result = validate_extensions(owner, _extensions(owner))
    assert result["literature_selected"] == ["arxiv", "crossref"]
    assert [s["source_id"] for s in result["external_sources"]] == [
        s["source_id"] for s in owner.external_data_sources._load()["sources"]]


def _sources(owner, change):
    rows = copy.deepcopy(owner.external_data_sources._load()["sources"])
    change(rows)
    return rows


def _rebound(rows):
    rows[1].update(credential_env="EPOCH_KEY", endpoint="https://data.example.org/epoch.zip",
                   credential_origin="https://data.example.org")


@pytest.mark.parametrize("make, code", [
    (lambda o: [], "SETTINGS_IMPORT_EXTENSIONS_INVALID"),
    (lambda o: {k: v for k, v in _extensions(o).items() if k != "research"}, "SETTINGS_IMPORT_EXTENSIONS_INVALID"),
    (lambda o: dict(_extensions(o), plugins=[]), "SETTINGS_IMPORT_EXTENSIONS_INVALID"),
    (lambda o: _extensions(o, external_sources={}), "SETTINGS_IMPORT_SOURCES_INVALID"),
    (lambda o: _extensions(o, external_sources=_sources(o, lambda r: r.pop())), "SETTINGS_IMPORT_SOURCE_SLOTS_INVALID"),
    (lambda o: _extensions(o, external_sources=_sources(o, lambda r: r.__setitem__(2, copy.deepcopy(r[1])))),
     "SETTINGS_IMPORT_SOURCE_SLOTS_INVALID"),
    (lambda o: _extensions(o, external_sources=_sources(o, lambda r: r.append(copy.deepcopy(r[1])))),
     "SETTINGS_IMPORT_SOURCE_SLOTS_INVALID"),
    (lambda o: _extensions(o, external_sources=_sources(o, lambda r: r[0].update(display_name=""))),
     "EXTERNAL_SOURCE_NAME_INVALID"),
    (lambda o: _extensions(o, external_sources=_sources(o, lambda r: r[0].update(source_id="my-source"))),
     "EXTERNAL_SOURCE_SLOT_INVALID"),
    (lambda o: _extensions(o, external_sources=_sources(o, _rebound)), "EXTERNAL_SOURCE_CREDENTIAL_ORIGIN_REBIND_REQUIRED"),
    (lambda o: _extensions(o, literature_selected="crossref"), "SETTINGS_IMPORT_LITERATURE_INVALID"),
    (lambda o: _extensions(o, literature_selected={"crossref": True}), "SETTINGS_IMPORT_LITERATURE_INVALID"),
    (lambda o: _extensions(o, literature_selected=["pubmed"]), "SETTINGS_IMPORT_LITERATURE_INVALID"),
    (lambda o: _extensions(o, literature_selected=["crossref", "crossref"]), "SETTINGS_IMPORT_LITERATURE_INVALID"),
    (lambda o: _extensions(o, literature_selected=[1]), "SETTINGS_IMPORT_LITERATURE_INVALID"),
    (lambda o: _extensions(o, leaderboard=dict(LEADERBOARD_DEFAULTS, currency="EUR")), "LEADERBOARD_PREFERENCES_INVALID"),
    (lambda o: _extensions(o, research=dict(research_defaults(), extra=1)), "RESEARCH_CONFIG_FIELDS_INVALID"),
])
def test_a_bundle_that_does_not_fit_this_installation_is_refused(owner, make, code):
    with pytest.raises(ValueError) as caught:
        validate_extensions(owner, make(owner))
    assert str(caught.value) == code


def test_a_credentialed_source_may_keep_its_site(owner):
    def bind(rows):
        rows[1].update(credential_env="EPOCH_KEY", credential_origin="https://epoch.ai")
    validate_extensions(owner, _extensions(owner, external_sources=_sources(owner, bind)))


def test_a_source_without_a_credential_may_move_to_another_site(owner):
    def move(rows):
        rows[2].update(endpoint="https://models.example.org/api/v1/models")
    result = validate_extensions(owner, _extensions(owner, external_sources=_sources(owner, move)))
    assert result["external_sources"][2]["endpoint"].startswith("https://models.example.org/")


# AI briefing settings

TODAY = datetime.now(timezone(timedelta(hours=9))).date()
OPTIONS = [{"profile_ref": "api:chat", "eligible": True}, {"profile_ref": "api:off", "eligible": False}]


@pytest.fixture
def briefing(tmp_path, monkeypatch):
    monkeypatch.setattr(report_models, "catalog", lambda runtime, capability="CHAT": copy.deepcopy(OPTIONS))
    runtime = SimpleNamespace(root=tmp_path / "research", lock=threading.RLock(),
                              host=SimpleNamespace(profile_path=lambda name: tmp_path / "profile"))
    runtime.root.mkdir()
    return AIBriefing(runtime)


def _briefing(**changes):
    value = copy.deepcopy(DEFAULT_CONFIG)
    value.update(changes)
    return value


@pytest.mark.parametrize("config", [
    _briefing(),
    _briefing(profile_ref="api:chat", range_mode="30", focus="agents"),
    _briefing(range_mode="custom", range_start=(TODAY - timedelta(days=365)).isoformat(), range_end=TODAY.isoformat()),
    _briefing(sources=[]),
])
def test_briefing_settings_within_bounds_are_saved(briefing, config):
    result = briefing.save(config, 0)
    assert (result["revision"], result["config"]) == (1, config)
    assert briefing.save(config, 1)["revision"] == 2


@pytest.mark.parametrize("config, code", [
    ([], "AI_SETTINGS_INVALID"),
    (dict(_briefing(), language="en"), "AI_SETTINGS_INVALID"),
    ({k: v for k, v in _briefing().items() if k != "focus"}, "AI_SETTINGS_INVALID"),
    (_briefing(range_mode="14"), "AI_SETTINGS_INVALID"),
    (_briefing(focus="f" * 501), "AI_SETTINGS_INVALID"),
    (_briefing(focus=None), "AI_SETTINGS_INVALID"),
    (_briefing(range_start=TODAY.isoformat()), "AI_SETTINGS_INVALID"),
    (_briefing(range_end=TODAY.isoformat()), "AI_SETTINGS_INVALID"),
    (_briefing(range_mode="custom", range_start=TODAY.isoformat(), range_end=(TODAY + timedelta(days=1)).isoformat()),
     "AI_RANGE_INVALID"),
    (_briefing(range_mode="custom", range_start=TODAY.isoformat(), range_end=(TODAY - timedelta(days=1)).isoformat()),
     "AI_RANGE_INVALID"),
    (_briefing(range_mode="custom", range_start=(TODAY - timedelta(days=366)).isoformat(), range_end=TODAY.isoformat()),
     "AI_RANGE_INVALID"),
    (_briefing(range_mode="custom", range_start="last week", range_end=TODAY.isoformat()), "AI_RANGE_INVALID"),
    (_briefing(range_mode="custom", range_start=TODAY.isoformat(), range_end=None), "AI_RANGE_INVALID"),
    (_briefing(sources="openai_news"), "AI_SETTINGS_INVALID"),
    (_briefing(sources={"openai_news": True}), "AI_SETTINGS_INVALID"),
    (_briefing(sources=["openai_news", "openai_news"]), "AI_SETTINGS_INVALID"),
    (_briefing(sources=["someone_blog"]), "AI_SETTINGS_INVALID"),
    (_briefing(profile_ref="api:missing"), "AI_MODEL_UNAVAILABLE"),
    (_briefing(profile_ref="api:off"), "AI_MODEL_UNAVAILABLE"),
])
def test_briefing_settings_out_of_bounds_are_refused(briefing, config, code):
    with pytest.raises(ValueError) as caught:
        briefing.save(config, 0)
    assert str(caught.value) == code
    assert not (briefing.r.root / "ai_briefing_settings.json").exists()


@pytest.mark.parametrize("revision", [1, True, False, "0", None])
def test_briefing_settings_need_the_current_revision(briefing, revision):
    with pytest.raises(ValueError, match="AI_SETTINGS_REVISION_CONFLICT"):
        briefing.save(_briefing(), revision)
    assert not (briefing.r.root / "ai_briefing_settings.json").exists()


def test_every_known_source_can_be_chosen(briefing):
    briefing.save(_briefing(sources=list(SOURCES)), 0)
    saved = json.loads((briefing.r.root / "ai_briefing_settings.json").read_text(encoding="utf-8"))
    assert saved["config"]["sources"] == list(SOURCES)
