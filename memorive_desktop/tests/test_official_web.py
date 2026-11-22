"""The official AI websites: the address check, the importer's check of a recorded page and the extension's
generated site file (memorive/sessions/companion/site_profiles.py)."""
import json
from pathlib import Path
import sys
import pytest
from memorive.sessions.companion.site_profiles import (
    default_sites, normalize_sites, official_web_identity, extension_site_script, capture_patterns,
)
from memorive.sessions.archive.importers import BrowserLibraryImporter
from memorive.sessions.archive.models import ArchiveInputError
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tests"))
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

@pytest.mark.parametrize("url",["https://unverified.example.org/c/a","https://chat.qwen.ai.evil.example/c/a"])
def test_import_rejects_unofficial_current_page(url):
    payload=_current_page_bundle(source_url=url)
    with pytest.raises(ArchiveInputError,match="NOT_VERIFIED_OFFICIAL"):
        BrowserLibraryImporter.parse_payload(payload,raw_bytes=json.dumps(payload).encode())


def test_the_extension_site_file_is_the_generated_one():
    assert (ROOT/"source/memorive/sessions/companion/extension/site_profiles.js").read_text(encoding="utf-8")==extension_site_script()

@pytest.mark.parametrize("url",[
    "https://gemini.google.com/u/0/app?hl=zh-cn&pageId=none",
    "https://gemini.google.com/u/1/app/abc123?hl=ja#display",
])
def test_gemini_full_link_keeps_account_path_not_temporary_parameters(url):
    # The site list of the earlier sync, as it is read for its old files.
    rows=default_sites();rows[1]["url"]=url
    assert official_web_identity(url)["provider"]=="gemini"
    saved=normalize_sites(rows)
    from urllib.parse import urlsplit
    assert saved[1]["url"]=="https://gemini.google.com"+urlsplit(url).path
    assert saved[1]["hosts"]==["gemini.google.com"]
    assert "?" not in saved[1]["url"] and "#" not in saved[1]["url"]


def test_the_extension_may_read_every_official_ai_website_it_records():
    # ChatGPT, Claude, Qwen and Grok as well: recording must not depend on the toolbar click's
    # temporary access, which Edge did not give on chatgpt.com. Nothing beyond the official list is granted.
    manifest=json.loads((ROOT/"source/memorive/sessions/companion/extension/manifest.json").read_text(encoding="utf-8"))
    assert manifest["host_permissions"]==manifest["content_scripts"][0]["matches"]==capture_patterns()
    assert {"https://chatgpt.com/*","https://claude.ai/*","https://grok.com/*","https://chat.qwen.ai/*"}<=set(capture_patterns())


@pytest.mark.parametrize(("url", "name"), [
    ("https://www.qianwen.com/chat/1", "千问"), ("https://www.doubao.com/chat/1", "豆包"),
    ("https://yuanbao.tencent.com/chat/1", "腾讯元宝"), ("https://yiyan.baidu.com/chat/1", "文心一言"),
    ("https://chatglm.cn/main/1", "智谱清言"), ("https://copilot.microsoft.com/chats/1", "Copilot"),
    ("https://www.perplexity.ai/search/1", "Perplexity"), ("https://poe.com/chat/1", "Poe"),
    ("https://chat.mistral.ai/chat/1", "Le Chat"),
])
def test_more_official_ai_websites_are_recognised(url, name):
    assert official_web_identity(url)["name"] == name
    assert f"https://{official_web_identity(url)['hosts'][0]}/*" in capture_patterns()
