"""Official origin restrictions through real settings, package and importer."""
import json
from pathlib import Path
import sys
import pytest
from memorive_browser_companion.site_profiles import (
    SiteProfiles, default_sites, normalize_sites, profile_hash, active_sites,
    official_web_identity, configured_extension, extension_profile_script,
)
from memorive_session_archive.importers import BrowserLibraryImporter
from memorive_session_archive.models import ArchiveInputError
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tests"))
from test_extension_upgrade_repair_gate import _api
from test_universal_current_page_capture import _current_page_bundle

GOOD=["https://chat.deepseek.com/chat/s/a","https://gemini.google.com/app/a",
      "https://www.kimi.com/chat/a","https://kimi.com/chat/a","https://kimi.moonshot.cn/chat/a",
      "https://chat.qwen.ai/c/a","https://chatgpt.com/c/a","https://claude.ai/chat/a",
      "https://CHATGPT.COM:443/c/a","https://chat.qwen.ai/c/a?q=中文"]
BAD=["https://unverified.example.org/","https://chatgpt.com.evil.example/",
     "https://evilchatgpt.com/","https://test.chatgpt.com/","http://chatgpt.com/",
     "https://chatgpt.com:444/","https://chatgpt.com./","https://chatgpt.com@evil.example/",
     "https://evil.example@chatgpt.com/","https://chatgpt.com\\@evil.example/",
     "https://%63hatgpt.com/","https://chatɡpt.com/","https://ｃhatgpt.com/",
     "https://xn--chatgpt-123.com/","https://openai.com/","https://api.openai.com/",
     "https://platform.deepseek.com/","https://huggingface.co/Qwen/","https://github.com/QwenLM/",
     "https://localhost/","https://127.0.0.1/","file:///chatgpt.com","javascript:alert(1)",
     " https://chatgpt.com/","https://chatgpt.com/\n","https:////chatgpt.com/",
     "https://cha\ntgpt.com/","https://chatgpt.com\t/"]

@pytest.mark.parametrize("url",GOOD)
def test_verified_identity(url):
    assert official_web_identity(url)

@pytest.mark.parametrize("url",BAD)
def test_unverified_identity(url):
    assert official_web_identity(url) is None

def custom(url="https://chat.qwen.ai/"):
    rows=default_sites()
    rows[0].update(name="Qwen",adapter="generic",url=url)
    rows[0]["selectors"]["path_prefix"]="/c/"
    rows[1]["enabled"]=rows[2]["enabled"]=False
    return rows

def test_save_rejects_unknown_origin_without_state_mutation(tmp_path):
    store=SiteProfiles(tmp_path)
    before=store.get()
    with pytest.raises(ValueError,match="NOT_VERIFIED_OFFICIAL"):
        store.save(custom("https://unverified.example.org/"),0)
    assert store.get()==before and not store.path.exists()

@pytest.mark.parametrize("url",["https://chat.qw\nen.ai/","https://chat.qwen.ai\t/"])
def test_save_rejects_control_characters(tmp_path,url):
    with pytest.raises(ValueError,match="HTTPS_PUBLIC_HOST_REQUIRED"):
        SiteProfiles(tmp_path).save(custom(url),0)

def test_legacy_unknown_config_remains_editable_but_cannot_issue_plugin(tmp_path):
    api,_,_= _api(tmp_path)
    store=SiteProfiles(api._profile_path("BROWSER_COMPANION"))
    rows=normalize_sites(custom("https://unverified.example.org/"))
    state=dict(schema_version="DesktopBrowserSites-v1",revision=1,sites=rows,profile_sha256=profile_hash(rows))
    store.root.mkdir(parents=True,exist_ok=True)
    store.path.write_text(json.dumps(state),encoding="utf-8")
    assert store.get()["sites"][0]["enabled"] is True
    assert active_sites(rows)==[]
    package=configured_extension(ROOT/"source/memorive_browser_companion/extension",tmp_path/"pack",rows)
    manifest=json.loads((package/"manifest.json").read_text(encoding="utf-8"))
    assert manifest["host_permissions"]==manifest["content_scripts"]==[]
    assert api.install_browser_companion()["status"]=="BROWSER_NO_VERIFIED_OFFICIAL_SITES"
    assert not (store.root/"extension-v1").exists()
    assert api.browser_companion_sites()["site_authorizations"][0]["verified_official"] is False
    rows[0]["enabled"]=False
    assert store.save(rows,1)["revision"]==2

def test_verified_custom_site_gets_only_its_exact_permission(tmp_path):
    saved=SiteProfiles(tmp_path/"state").save(custom(),0)
    package=configured_extension(ROOT/"source/memorive_browser_companion/extension",tmp_path/"pack",saved["sites"])
    manifest=json.loads((package/"manifest.json").read_text(encoding="utf-8"))
    assert manifest["host_permissions"]==["https://chat.qwen.ai/*"]
    assert manifest["content_scripts"][0]["matches"]==manifest["host_permissions"]
    assert "<all_urls>" not in json.dumps(manifest)

def test_external_source_fields_cannot_grant_permission(tmp_path):
    rows=custom("https://unverified.example.org/")
    rows[0].update(official=True,verified_official=True,website="https://chatgpt.com/",
                   evidence_url="https://openrouter.ai/api/v1/models")
    with pytest.raises(ValueError,match="NOT_VERIFIED_OFFICIAL"):
        SiteProfiles(tmp_path).save(rows,0)

@pytest.mark.parametrize("url",["https://unverified.example.org/c/a","https://chat.qwen.ai.evil.example/c/a"])
def test_import_rejects_unofficial_current_page(url):
    payload=_current_page_bundle(source_url=url)
    with pytest.raises(ArchiveInputError,match="NOT_VERIFIED_OFFICIAL"):
        BrowserLibraryImporter.parse_payload(payload,raw_bytes=json.dumps(payload).encode())


def test_generated_default_profile_has_no_drift():
    assert (ROOT/"source/memorive_browser_companion/extension/site_profiles.js").read_text(encoding="utf-8")==extension_profile_script(default_sites())

@pytest.mark.parametrize("url",[
    "https://gemini.google.com/u/0/app?hl=zh-cn&pageId=none",
    "https://gemini.google.com/u/1/app/abc123?hl=ja#display",
])
def test_gemini_full_link_keeps_account_path_not_temporary_parameters(tmp_path,url):
    rows=default_sites();rows[1]["url"]=url
    assert official_web_identity(url)["provider"]=="gemini"
    saved=SiteProfiles(tmp_path).save(rows,0)
    from urllib.parse import urlsplit
    assert saved["sites"][1]["url"]=="https://gemini.google.com"+urlsplit(url).path
    assert saved["sites"][1]["hosts"]==["gemini.google.com"]
    assert "?" not in saved["sites"][1]["url"] and "#" not in saved["sites"][1]["url"]
