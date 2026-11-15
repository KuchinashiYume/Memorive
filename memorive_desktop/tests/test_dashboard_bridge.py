"""Native bridge with an isolated profile and actual catalog/cache contracts."""
import json
from pathlib import Path
from types import SimpleNamespace
from runtime import ProductApi
from memorive_settings.contracts import canonical_sha256
from memorive_settings.external_sources import ExternalDataSources, default_sources
from memorive_settings.quality_new import validate_quality_new_catalog

ROOT=Path(__file__).resolve().parents[1]

def make_api(tmp_path):
    api=ProductApi.__new__(ProductApi)
    folders={k:tmp_path/k for k in ("SETTINGS","MODEL_VALIDATION","Core_JOBS")}
    api._profile_path=lambda key:folders[key]
    rows,_=validate_quality_new_catalog(json.loads((ROOT/"fixtures/workflow_model_exam_scores_phase2_new.json").read_text(encoding="utf-8")))
    calls=[]
    def call(method,params):
        calls.append((method,params))
        assert method=="settings.get_state" and params=={}
        return {"workflow_exam_score_catalog":{"entries":rows},"settings":{"secret":"DO_NOT_EXPORT"}}
    api._service=SimpleNamespace(call=call)
    return api,folders,calls


def test_external_cache_readonly_disable_replace_and_hash(tmp_path):
    source=default_sources()[1]
    store=ExternalDataSources(tmp_path,environment={})
    snapshot=dict(schema_version="ExternalModelReference-v1",
        source_identity_sha256=store._source_identity(source),retrieved_at="2026-09-06T00:00:00Z",
        records=[dict(source_id="epoch-ai",source_name="Epoch AI",upstream_source="epoch-ai",
          model_id="m",model_name="Model",metrics={"eci":162},version="v1",prompt="NO_EXPORT")])
    snapshot["sha256"]=canonical_sha256(snapshot)
    file=tmp_path/"snapshots"/"epoch-ai"/"one.json"
    file.parent.mkdir(parents=True);file.write_text(json.dumps(snapshot),encoding="utf-8")
    before=file.read_bytes()
    out=store.public_catalog()
    assert len(out["rows"])==1 and "NO_EXPORT" not in json.dumps(out)
    assert file.read_bytes()==before and not store.path.exists()
    disabled={**source,"enabled":False}
    store.save(disabled,expected_revision=0)
    assert store.public_catalog()["rows"]==[]
    changed={**source,"endpoint":"https://epoch.ai/different.zip"}
    store.save(changed,expected_revision=1)
    assert store.public_catalog()["rows"]==[]
    store.save(source,expected_revision=2)
    snapshot["records"][0]["metrics"]["eci"]=999
    file.write_text(json.dumps(snapshot),encoding="utf-8")
    out=store.public_catalog()
    assert not out["rows"] and out["unavailable_sources"]==["epoch-ai"]
