"""User-requested sample contract; isolated state, synthetic records, no providers."""
from pathlib import Path
import json
import os
import sys
import threading
import pytest

ROOT = Path(os.environ.get("MEMORIVE_TESTS_TARGET", Path(__file__).resolve().parents[1]))
for p in (ROOT/"source", ROOT/"product/desktop"):
    sys.path.insert(0, str(p))
from runtime import ProductApi
from memorive_research_runtime.common import defaults, sealed, write, read


def test_wos_contract_and_secret_never_in_receipts(tmp_path,monkeypatch):
    from memorive_research_runtime.transport import MetadataClient, _last
    from memorive_settings.metadata_transport import MetadataResponse
    secret="synthetic-key-not-a-real-credential"
    monkeypatch.setenv("WOS_API_KEY",secret);_last.clear()
    class Synthetic:
        def request(self, **p):
            assert p["headers"]["X-ApiKey"]==secret
            assert p["url"].startswith("https://api.clarivate.com/apis/wos-starter/v1/documents?")
            return MetadataResponse(200,json.dumps({"metadata":{"total":1,"page":1,"limit":3},"hits":[{
                "uid":"WOS:SYNTHETIC","title":"Original English title",
                "names":{"authors":[{"displayName":"Test Author"}]},
                "identifiers":{"doi":"10.9999/synthetic"},"source":{"publishYear":2026},
                "links":{"record":"https://www.webofscience.com/wos/woscc/full-record/WOS:SYNTHETIC"}}]}).encode(),{})
    client=MetadataClient(tmp_path,lambda:{},transport=Synthetic())
    result=client.fetch("WEB-OF-SCIENCE","synthetic",3,"run","call",threading.Event())
    row=result["records"][0]
    assert row["bibliographic"]["title"]=="Original English title"
    assert row["bibliographic"]["published_date"] is None
    assert row["identifiers"]["doi"]=="10.9999/synthetic"
    for p in tmp_path.rglob("*"):
        if p.is_file():assert secret.encode() not in p.read_bytes()

def test_v2_source_migration_preserves_choices_and_bytes(tmp_path):
    from memorive_settings.literature_sources import projection
    from memorive.platform.json_files import canonical_sha256
    old={"schema_version":"LiteratureSourceSelection-v2","revision":7,"selected":["arxiv"],
        "slots":[{"slot_id":"literature-1","provider":"arxiv"},{"slot_id":"literature-2","provider":None},{"slot_id":"literature-3","provider":None}]}
    old["sha256"]=canonical_sha256(old)
    path=tmp_path/"literature_sources.json";write(path,old);before=path.read_bytes()
    upgraded=projection(tmp_path)
    assert upgraded["revision"]==7 and upgraded["selected"]==["arxiv"]
    assert [r["provider"] for r in upgraded["slots"]]==["arxiv","crossref","web-of-science"]
    assert path.read_bytes()==before

def test_readable_refinement_english_body_is_not_translated():
    from memorive_library.refinement_markdown import render
    item={"candidate_text":"Evidence must remain in its original language.",
          "refinement_type":"CLAIM","scope":"Selected discussion","evidence_state":"UNVERIFIED",
          "message_refs":["message-1"],"limitations":["No independent verification."]}
    draft={"display_name":"精炼对话 · Original English discussion","items":[item],
           "local_projection_id":"test","created_at":"2026-09-07","source_snapshot_sha256":"B"*64}
    md=render(draft,"A"*64)
    assert item["candidate_text"] in md and item["limitations"][0] in md
    assert not __import__("re").search(r"[\u3400-\u9fff]",md)

def test_refinement_tamper_is_not_overwritten(api):
    from tests.test_lifecycle_surfaces import _refinement_draft
    artifact=api._library.register_refinement_draft(_refinement_draft())["artifact_id"]
    path=api._library.resolve_refinement_readable_artifact(artifact)
    path.write_text("user edit",encoding="utf8")
    with pytest.raises(Exception,match="READABLE_HASH_MISMATCH"):api._library.resolve_refinement_readable_artifact(artifact)
    assert path.read_text()=="user edit"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORIVE_TEST_CONSOLE_AUTO_EXECUTION", "DISABLED")
    monkeypatch.setenv("MEMORIVE_TEST_CONSOLE_SESSION_ID", "review-sample-regression")
    monkeypatch.delenv("WOS_API_KEY", raising=False)
    instance = ProductApi(tmp_path/"localappdata/state", ROOT/"source", ROOT/"source/facade_method_binding.json",
        session_source_roots={"codex_home":tmp_path/"empty-codex", "claude_home":tmp_path/"empty-claude"})
    yield instance
    instance.close()


def test_three_presets_and_empty_wos_key(api):
    state=api.call("settings.external_sources_get", {})["literature"]
    assert [s["provider"] for s in state["slots"]] == ["arxiv","crossref","web-of-science"]
    wos=state["slots"][2]
    assert wos["credential_env"]=="WOS_API_KEY"
    assert wos["endpoint"]=="https://api.clarivate.com/apis/wos-starter/v1/documents"
    assert wos["configuration_status"]=="CREDENTIAL_REQUIRED"
    assert "web-of-science" not in state["selected"]
    assert "credential_value" not in json.dumps(state)


def test_matching_preference_is_saved_not_an_execution_filter(api):
    current=api.call("research.config_get", {})
    assert current["config"]["matching_preference"] == 50
    current["config"]["matching_preference"]=90
    saved=api.call("research.config_save", {"config":current["config"],"expected_revision":current["revision"]})
    assert saved["config"]["matching_preference"]==90
    runtime=(ROOT/"source/memorive_research_runtime/runtime.py").read_text(encoding="utf8")
    assert "matching_preference" not in runtime


def test_wos_no_key_fails_before_transport(tmp_path, monkeypatch):
    from memorive_research_runtime.transport import MetadataClient
    monkeypatch.delenv("WOS_API_KEY", raising=False)
    class NoNetwork:
        def request(self, **kwargs): raise AssertionError("No credential may perform I/O")
    client=MetadataClient(tmp_path, lambda:{}, transport=NoNetwork())
    with pytest.raises(ValueError, match="LITERATURE_CREDENTIAL_REQUIRED"):
        client.fetch("WEB-OF-SCIENCE", "synthetic", 3, "run", "call", threading.Event())


def test_refinement_external_open_is_md_preserves_json(api, monkeypatch):
    from tests.test_lifecycle_surfaces import _refinement_draft
    draft=_refinement_draft()
    registration=api._library.register_refinement_draft(draft)
    artifact=registration["artifact_id"]
    original=api._library.resolve_refinement_private_artifact(artifact)
    original_bytes=original.read_bytes()
    opened=[]
    monkeypatch.setattr(os, "startfile", lambda p,*args:opened.append(Path(p)))
    receipt=api.open_library_refinement_artifact(artifact)
    assert opened[0].suffix == ".md"
    assert original.read_bytes() == original_bytes
    text=opened[0].read_text(encoding="utf8")
    assert all(item["candidate_text"] in text for item in draft["items"])
    assert receipt["review_open_recorded"] is True
    again=api._library.resolve_refinement_readable_artifact(artifact)
    assert again==opened[0]


