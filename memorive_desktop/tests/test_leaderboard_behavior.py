from copy import deepcopy
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from datetime import date, timedelta, datetime, timezone
import json
import math
import threading
import time
import pytest
from leaderboard_test_support import setup, payloads, seed_sources, wait_refresh, model, ROOT, BASE, TREE
from memorive_settings.leaderboard import DEFAULTS, project_rows, validate_preferences, identity, variant_id
from memorive_settings.leaderboard_fx import parse_ecb
from memorive_settings.contracts import canonical_sha256
from memorive_settings.external_sources import default_sources
from memorive_desktop_service.control_store import default_state, validate_state


def test_formula_independent_oracle_and_currency_does_not_affect_scores(tmp_path):
    api,c,s,t=setup(tmp_path)
    initial=api.call("settings.leaderboard_get", {})
    # Scores are sorted by raw LiveBench value; untied percentile is (index+.5)/N*100.
    rows=sorted((r for r in initial['rows'] if r['catalog_eligible']), key=lambda r:r["quality"]["artificial-analysis"])
    for i,row in enumerate(rows):
        assert row["ability"] == pytest.approx((i+.5)/len(rows)*100)
        cost=.8*row["price"]["input"]+.2*row["price"]["output"]
        k=100/(1+cost/initial["algorithm"]["reference_cost_usd"])
        assert row["value"] == pytest.approx(100*(row["ability"]/100)**.7*(k/100)**.3)
    snapshot=[(r["id"],r["ability"],r["value"]) for r in initial["rows"]]
    for currency in ("RMB","JPY","USD"):
        result=api.call("settings.leaderboard_save", {"expected_revision":c.leaderboard_get()["revision"],"changes":{"currency":currency}})
        assert [(r["id"],r["ability"],r["value"]) for r in result["rows"]] == snapshot


def test_persistent_stars_exact_keys_conflict_and_settings_unchanged(tmp_path):
    api,c,s,t=setup(tmp_path)
    before=c.get_state()
    settings_bytes=c.store.path.read_bytes()
    state=api.call("settings.leaderboard_get",{})
    picked=next(r for r in state["rows"] if r["effort"]=="low")
    saved=api.call("settings.leaderboard_save",{"expected_revision":0,"changes":{"stars":[picked["id"]]}})
    with pytest.raises(ValueError,match="REVISION_CONFLICT"):
        api.call("settings.leaderboard_save",{"expected_revision":0,"changes":{"currency":"JPY"}})
    c._leaderboard_instance=None
    del c._leaderboard_instance
    reloaded=c.leaderboard_get()
    assert reloaded["preferences"]["stars"]==[picked["id"]]
    assert sum(r["starred"] for r in reloaded["rows"])==1
    assert c.store.path.read_bytes()==settings_bytes
    assert c.get_state()["workflow_exam_score_catalog"]==before["workflow_exam_score_catalog"]


@pytest.mark.parametrize("changes",[
    {"weights":{"artificial-analysis":0}},{"weights":{"openrouter":100}},
    {"weights":{"exchange-rates":100}},{"ability_weight":-1},{"cost_weight":math.inf},
    {"ability_weight":0,"cost_weight":0},{"input_ratio":2},{"stars":["not-an-id"]},
    {"currency":"EUR"},{"preset":"invented"}, {"undeclared":True},
])
def test_reject_invalid_preferences_without_partial_write(tmp_path,changes):
    api,c,s,t=setup(tmp_path)
    path=c._leaderboard_service().path
    before=path.read_bytes() if path.exists() else None
    with pytest.raises((ValueError,TypeError)):
        api.call("settings.leaderboard_save",{"expected_revision":0,"changes":changes})
    assert (path.read_bytes() if path.exists() else None)==before


def test_missing_prices_and_free_prices_are_not_zero_cost_grades(tmp_path):
    api,c,s,t=setup(tmp_path)
    fixture=payloads()
    fixture["openrouter"]["data"][0]["pricing"]={}
    fixture["openrouter"]["data"][1]["pricing"]={"prompt":"0","completion":"0"}
    seed_sources(s,fixture)
    state=c.leaderboard_get()
    assert all(r["price"] is None and r["value"] is None for r in state["rows"] if r["base_model"]==model(0)[1])
    free=next(r for r in state["rows"] if r["base_model"]==model(1)[1])
    assert free["mixed_usd"]==0 and free["economy"] is None and free["value"] is None


def test_partial_refresh_preserves_old_failed_snapshot(tmp_path):
    api,c,s,t=setup(tmp_path,seed=False)
    c.leaderboard_refresh();before=wait_refresh(c)
    old=s._latest(s._load()["sources"][2])["sha256"]
    t.fail={"openrouter"};t.data=payloads(delta=1)
    c.leaderboard_refresh();after=wait_refresh(c)
    assert after["refresh"]["status"]=="PARTIAL"
    assert s._latest(s._load()["sources"][2])["sha256"]==old
    assert len(after["rows"])==len(before["rows"])
    assert after["updated_at"]==before["sources"][2]["retrieved_at"]


def test_invalid_prefs_hash_not_silently_reset(tmp_path):
    api,c,s,t=setup(tmp_path)
    service=c._leaderboard_service()
    service.save(expected_revision=0,changes={"currency":"CNY"})
    obj=json.loads(service.path.read_text());obj["preferences"]["currency"]="JPY"
    service.path.write_text(json.dumps(obj))
    with pytest.raises(ValueError,match="CORRUPT"):service.get()


def test_unknown_namespace_does_not_join_and_conflicting_effort_rejected():
    a=identity({"model_id":"vendor-a/demo-max","source_id":"openrouter"})
    b=identity({"model_id":"vendor-b/demo-max","source_id":"openrouter"})
    assert variant_id(a)!=variant_id(b)
    with pytest.raises(ValueError,match="CONFLICT"):
        identity({"model_id":"gpt-demo-ultra","benchmark_effort":"low","upstream_source":"livebench","source_id":"a"})


def test_fx_cross_rates_same_day_and_reject_bad_payloads():
    fixture=payloads()["exchange-rates"]
    today=datetime.now(timezone.utc).date()
    assert parse_ecb(fixture)["unitsPerUsd"]=={"USD":1.0,"CNY":7.0,"JPY":150.0}
    invalid=[fixture.replace(b'CNY',b'GBP'),fixture.replace(b'8.4',b'NaN'),
             fixture.replace(b'8.4',b'-1'),fixture.replace(b'8.4',b'0'),
             b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///not-read">]>'+fixture,
             fixture.replace(b'<Cube currency="CNY" rate="8.4"/>',b'<Cube currency="CNY" rate="8.4"/><Cube currency="CNY" rate="8.5"/>'),
             fixture.replace(today.isoformat().encode(),(today+timedelta(days=1)).isoformat().encode()),
             fixture.replace(today.isoformat().encode(),(today-timedelta(days=8)).isoformat().encode())]
    for value in invalid:
        with pytest.raises(ValueError):parse_ecb(value)


def test_fx_additive_default_preserves_disabled_slot_and_routes(tmp_path):
    api,c,s,t=setup(tmp_path)
    state=s._load()
    state["sources"]=state["sources"][:3];state["sha256"]=canonical_sha256(state)
    s.writer._atomic_write(s.path,state)
    old=s.path.read_bytes()
    assert len(s._load()["sources"])==4 and s.path.read_bytes()==old
    fx=s._load()["sources"][-1];fx["enabled"]=False
    s.save(fx,expected_revision=0)
    assert s._load()["sources"][-1]["enabled"] is False
    control=default_state();control["route"]="leaderboard"
    assert validate_state(control)["route"]=="leaderboard"


def test_overflowing_prices_and_fx_never_leak_into_model_reference(tmp_path):
    api,c,s,t=setup(tmp_path)
    data=payloads();data["openrouter"]["data"][0]["pricing"]["prompt"]="1e308"
    seed_sources(s,data)
    state=c.leaderboard_get()
    assert all(r["price"] is None for r in state["rows"] if r["base_model"]==model(0)[1])
    result=s.references("ANALYSIS_PRIMARY",{"model_name":model(0)[1],"thinking_mode":"max"})
    assert len(result["source_slots"])==3
    assert all(r.get("source_id")!="exchange-rates" for r in result["references"])


def test_atomic_preference_failure_keeps_last_saved_state(tmp_path):
    api,c,s,t=setup(tmp_path)
    c.leaderboard_save(expected_revision=0,changes={"currency":"CNY"})
    path=c._leaderboard_service().path;before=path.read_bytes()
    def fail(*args):
        raise OSError("SYNTHETIC_WRITE_FAILURE")
    s.writer.fault_injector=fail
    with pytest.raises(OSError):
        c.leaderboard_save(expected_revision=1,changes={"currency":"JPY"})
    assert path.read_bytes()==before
    assert c.leaderboard_get()["preferences"]["currency"]=="CNY"


def test_epoch_only_historical_reference_does_not_expand_current_catalog():
    source={"id":"epoch-ai","name":"Epoch","format":"EPOCH_CSV_ZIP","status":"CACHED","records":[
        {"model_id":"gpt-archived-fixture","model_name":"historical fixture","source_id":"epoch-ai",
         "identity_scope":"MODEL_CAPABILITY_UPPER_ENVELOPE","metrics":{"eci":120}}]}
    rows,_=project_rows([source],deepcopy(DEFAULTS))
    assert not any(r['catalog_eligible'] for r in rows)
