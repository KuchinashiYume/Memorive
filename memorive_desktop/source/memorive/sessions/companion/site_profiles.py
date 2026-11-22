"""The official AI chat websites the browser extension records on (OFFICIAL_WEB_SITES), and what follows from
them: official_web_identity names a page's site (the sessions page's letter marks, the importer's check of a
recorded page's address), capture_patterns are the extension's host permissions, and extension_site_script
writes the extension's site_profiles.js with the same hosts and address check.

BUILTINS, GENERIC, default_sites, normalize_sites, active_sites, profile_hash and saved_site_settings read the
site list of the site sync of earlier versions, so the files that sync wrote still import
(memorive/sessions/archive/importers.py). Compatibility code; keep.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from memorive.platform.json_files import canonical_sha256

BUILTINS = {
    'deepseek': ('DeepSeek','https://chat.deepseek.com/', ['chat.deepseek.com']),
    'gemini': ('Gemini','https://gemini.google.com/app', ['gemini.google.com']),
    'kimi': ('Kimi','https://www.kimi.com/', ['kimi.com','www.kimi.com','kimi.moonshot.cn']),
}
GENERIC = dict(path_prefix='/chat/', history='nav a[href], aside a[href]',
    user='[data-message-author-role="user"], [data-role="user"]',
    assistant='[data-message-author-role="assistant"], [data-role="assistant"]')
# Security authority is bundled code reviewed against provider-owned pages.
# Replaceable benchmark feeds and user selectors cannot add trusted domains.
OFFICIAL_WEB_REVISION = "Desktop_OFFICIAL_CHAT_ORIGINS_20261122_V3"
OFFICIAL_WEB_SITES = (
    ("deepseek", "DeepSeek", ("chat.deepseek.com",), "https://www.deepseek.com/"),
    ("gemini", "Gemini", ("gemini.google.com",), "https://support.google.com/gemini/answer/14886647"),
    ("kimi", "Kimi", ("kimi.com", "www.kimi.com", "kimi.moonshot.cn"), "https://www.moonshot.cn/"),
    ("qwen", "Qwen", ("chat.qwen.ai",), "https://github.com/QwenLM/Qwen3"),
    ("chatgpt", "ChatGPT", ("chatgpt.com",), "https://help.openai.com/en/articles/9125172-the-chatgpt-home-page"),
    ("claude", "Claude", ("claude.ai",), "https://support.claude.com/en/articles/8114491-get-started-with-claude"),
    ("grok", "Grok", ("grok.com",), "https://docs.x.ai/grok/overview"),
    # 2026-11-22: more chat websites to record. 千问 is Alibaba's consumer assistant (formerly 通义), now on qianwen.com.
    ("qianwen", "千问", ("www.qianwen.com", "qianwen.com", "tongyi.aliyun.com"), "https://www.qianwen.com/"),
    ("doubao", "豆包", ("www.doubao.com", "doubao.com"), "https://www.doubao.com/"),
    ("yuanbao", "腾讯元宝", ("yuanbao.tencent.com",), "https://yuanbao.tencent.com/"),
    ("yiyan", "文心一言", ("yiyan.baidu.com",), "https://yiyan.baidu.com/"),
    ("chatglm", "智谱清言", ("chatglm.cn",), "https://chatglm.cn/"),
    ("copilot", "Copilot", ("copilot.microsoft.com",), "https://copilot.microsoft.com/"),
    ("perplexity", "Perplexity", ("www.perplexity.ai", "perplexity.ai"), "https://www.perplexity.ai/"),
    ("poe", "Poe", ("poe.com",), "https://poe.com/"),
    ("mistral", "Le Chat", ("chat.mistral.ai",), "https://mistral.ai/"),
)

def official_web_identity(raw):
    """Return a verified chat-site identity, never a suffix or name match."""
    if not isinstance(raw, str) or raw != raw.strip() or any(ord(c)<33 or ord(c)==127 for c in raw) or "\\" in raw:
        return None
    try:
        url = urlsplit(raw)
        if url.scheme != "https" or url.username or url.password or url.port not in (None, 443):
            return None
        if "%" in url.netloc or not url.netloc.isascii() or (url.hostname or "").endswith("."):
            return None
        for provider, name, hosts, proof in OFFICIAL_WEB_SITES:
            if url.hostname in hosts:
                return dict(provider=provider, name=name, hosts=list(hosts),
                            evidence_url=proof, registry_revision=OFFICIAL_WEB_REVISION)
    except ValueError:
        pass
    return None

def verified_site(row):
    identity = official_web_identity(row.get("url"))
    return bool(identity and row.get("hosts") and
                set(row["hosts"]).issubset(identity["hosts"]))

def default_sites():
    return [dict(slot=n, name=label, url=url, adapter=key, enabled=True, selectors=deepcopy(GENERIC))
            for n,(key,(label,url,_hosts)) in enumerate(BUILTINS.items(),1)]

def normalize_sites(rows):
    if not isinstance(rows,list) or len(rows)>12:
        raise ValueError('BROWSER_SITES_COUNT_INVALID')
    accepted=[]
    hosts_seen=set()
    for n,row in enumerate(rows,1):
        if not isinstance(row,dict) or row.get('slot')!=n or type(row.get('enabled')) is not bool:
            raise ValueError('BROWSER_SITE_FIELDS_INVALID')
        adapter=row.get('adapter')
        if adapter not in {*BUILTINS,'generic'}:
            raise ValueError('BROWSER_SITE_ADAPTER_INVALID')
        name=str(row.get('name','')).strip()
        if not name or len(name)>60 or any(ord(c)<32 for c in name):
            raise ValueError('BROWSER_SITE_NAME_INVALID')
        raw=str(row.get('url','')).strip()
        if any(ord(c)<33 or ord(c)==127 for c in raw):
            raise ValueError('BROWSER_SITE_HTTPS_PUBLIC_HOST_REQUIRED')
        url=urlsplit(raw)
        host=(url.hostname or '').lower()
        if (url.scheme!='https' or not host or url.username or url.password
                or ((url.query or url.fragment) and not official_web_identity(raw))
                or url.port not in (None,443) or '\\' in raw or '%' in url.netloc or host.endswith('.')
                or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',host)
                or host.endswith(('.local','.localhost','.internal','.lan','.home','.test','.invalid'))):
            raise ValueError('BROWSER_SITE_HTTPS_PUBLIC_HOST_REQUIRED')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError('BROWSER_SITE_IP_ADDRESS_FORBIDDEN')
        if adapter in BUILTINS:
            hosts=BUILTINS[adapter][2]
            if host not in hosts:
                raise ValueError('BROWSER_SITE_ADAPTER_ORIGIN_MISMATCH')
            provider_id=adapter
        else:
            if any(host in item[2] for item in BUILTINS.values()):
                raise ValueError('BROWSER_SITE_USE_BUILTIN_ADAPTER')
            hosts=[host]
            provider_id='web-'+hashlib.sha256(host.encode()).hexdigest()[:16]
        if hosts_seen.intersection(hosts):
            raise ValueError('BROWSER_SITE_DUPLICATE_ORIGIN')
        hosts_seen.update(hosts)
        selectors={**GENERIC,**(row.get('selectors') or {})}
        if set(selectors)!=set(GENERIC):
            raise ValueError('BROWSER_SITE_SELECTORS_INVALID')
        for key,value in selectors.items():
            if not isinstance(value,str) or not value.strip() or len(value)>1024 or any(ord(c)<32 for c in value):
                raise ValueError('BROWSER_SITE_SELECTORS_INVALID')
            selectors[key]=value.strip()
        prefix=selectors['path_prefix']
        if not re.fullmatch(r'/(?:[A-Za-z0-9._~-]+/)+',prefix) or '..' in prefix:
            raise ValueError('BROWSER_SITE_SESSION_PATH_INVALID')
        if selectors['user']==selectors['assistant']:
            raise ValueError('BROWSER_SITE_MESSAGE_ROLES_AMBIGUOUS')
        for key in ('history','user','assistant'):
            if selectors[key].lower() in {'*','body','html','input','textarea','form'}:
                raise ValueError('BROWSER_SITE_SELECTOR_TOO_BROAD')
        accepted.append(dict(slot=n,name=name,url=f'https://{host}{url.path or "/"}',adapter=adapter,
            enabled=row['enabled'],selectors=selectors,provider_id=provider_id,hosts=hosts))
    return accepted

def active_sites(rows):
    # Previously saved unknown sites stay editable but never receive permission.
    return [row for row in normalize_sites(rows) if row['enabled'] and verified_site(row)]
def profile_hash(rows): return canonical_sha256(normalize_sites(rows))

def saved_site_settings(root):
    """The site list the earlier site sync saved in the BROWSER_COMPANION folder (sites_v1.json), or None when
    there is none or it does not read; the importer accepts a custom-site file of that sync only when its
    site_profile_sha256 matches this list."""
    try:
        state = json.loads((Path(root) / 'sites_v1.json').read_text(encoding='utf-8'))
        return dict(state, sites=normalize_sites(state['sites']))
    except (OSError, ValueError, KeyError, TypeError):
        return None

def extension_site_script():
    """The extension's site_profiles.js: the official hosts, their hash and the address check the extension runs
    before it records a page (MEMORIVE_OFFICIAL_WEB.requireCaptureUrl). Write the file again when
    OFFICIAL_WEB_SITES changes; test_official_web checks that they agree."""
    hosts=sorted({host for _key,_name,origins,_proof in OFFICIAL_WEB_SITES for host in origins})
    data=dict(hosts=hosts,sha256=canonical_sha256(dict(hosts=hosts,revision=OFFICIAL_WEB_REVISION)),
              official_web_revision=OFFICIAL_WEB_REVISION)
    script='"use strict";\nglobalThis.MEMORIVE_SITE_PROFILES = Object.freeze('+json.dumps(data,ensure_ascii=True,separators=(',',':'))+');\n'
    script+=r"""(() => {
  const officialHosts = globalThis.MEMORIVE_SITE_PROFILES.hosts;
  const requireCaptureUrl = raw => {
    if (typeof raw !== "string" || !/^https:\/\/[^/]/i.test(raw) || /[\u0000-\u0020\u007f\\]/.test(raw)) throw new Error("BROWSER_SITE_NOT_VERIFIED_OFFICIAL");
    let url; try { url = new URL(raw); } catch (_) { throw new Error("BROWSER_SITE_NOT_VERIFIED_OFFICIAL"); }
    const authority = raw.split("/")[2] || "";
    if (url.protocol !== "https:" || url.username || url.password || url.port ||
        /[%\u0080-\uffff]/.test(authority) || url.hostname.endsWith(".") ||
        !officialHosts.includes(url.hostname)) {
      throw new Error("BROWSER_SITE_NOT_VERIFIED_OFFICIAL");
    }
    return url;
  };
  globalThis.MEMORIVE_OFFICIAL_WEB = Object.freeze({requireCaptureUrl});
})();
"""
    return script

def capture_patterns():
    """The official AI websites the extension records a conversation on (OFFICIAL_WEB_SITES). The extension may
    always read them, so recording does not depend on the toolbar click's temporary access."""
    return [f'https://{host}/*' for _key,_name,hosts,_proof in OFFICIAL_WEB_SITES for host in hosts]
