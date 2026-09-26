"""SHARED three local, exact-origin DOM profiles; no page scripts or secrets."""
from __future__ import annotations
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from memorive_settings.contracts import canonical_sha256
from memorive_settings.store import SettingsStore

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
OFFICIAL_WEB_REVISION = "Desktop_OFFICIAL_CHAT_ORIGINS_20260914_V2"
OFFICIAL_WEB_SITES = (
    ("deepseek", "DeepSeek", ("chat.deepseek.com",), "https://www.deepseek.com/"),
    ("gemini", "Gemini", ("gemini.google.com",), "https://support.google.com/gemini/answer/14886647"),
    ("kimi", "Kimi", ("kimi.com", "www.kimi.com", "kimi.moonshot.cn"), "https://www.moonshot.cn/"),
    ("qwen", "Qwen", ("chat.qwen.ai",), "https://github.com/QwenLM/Qwen3"),
    ("chatgpt", "ChatGPT", ("chatgpt.com",), "https://help.openai.com/en/articles/9125172-the-chatgpt-home-page"),
    ("claude", "Claude", ("claude.ai",), "https://support.claude.com/en/articles/8114491-get-started-with-claude"),
    ("grok", "Grok", ("grok.com",), "https://docs.x.ai/grok/overview"),
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

def site_authorizations(rows):
    return [dict(slot=row["slot"], verified_official=verified_site(row),
                 status="VERIFIED_OFFICIAL" if verified_site(row) else "UNVERIFIED_OFFICIAL",
                 identity=official_web_identity(row["url"]))
            for row in normalize_sites(rows)]

def default_sites():
    return [dict(slot=n, name=label, url=url, adapter=key, enabled=True, selectors=deepcopy(GENERIC))
            for n,(key,(label,url,_hosts)) in enumerate(BUILTINS.items(),1)]

def normalize_sites(rows):
    if not isinstance(rows,list) or len(rows)>12: raise ValueError('BROWSER_SITES_COUNT_INVALID')
    accepted=[]; hosts_seen=set()
    for n,row in enumerate(rows,1):
        if not isinstance(row,dict) or row.get('slot')!=n or type(row.get('enabled')) is not bool:
            raise ValueError('BROWSER_SITE_FIELDS_INVALID')
        adapter=row.get('adapter')
        if adapter not in {*BUILTINS,'generic'}: raise ValueError('BROWSER_SITE_ADAPTER_INVALID')
        name=str(row.get('name','')).strip()
        if not name or len(name)>60 or any(ord(c)<32 for c in name): raise ValueError('BROWSER_SITE_NAME_INVALID')
        raw=str(row.get('url','')).strip()
        if any(ord(c)<33 or ord(c)==127 for c in raw):
            raise ValueError('BROWSER_SITE_HTTPS_PUBLIC_HOST_REQUIRED')
        url=urlsplit(raw); host=(url.hostname or '').lower()
        if (url.scheme!='https' or not host or url.username or url.password
                or ((url.query or url.fragment) and not official_web_identity(raw))
                or url.port not in (None,443) or '\\' in raw or '%' in url.netloc or host.endswith('.')
                or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',host)
                or host.endswith(('.local','.localhost','.internal','.lan','.home','.test','.invalid'))):
            raise ValueError('BROWSER_SITE_HTTPS_PUBLIC_HOST_REQUIRED')
        try: ipaddress.ip_address(host)
        except ValueError: pass
        else: raise ValueError('BROWSER_SITE_IP_ADDRESS_FORBIDDEN')
        if adapter in BUILTINS:
            hosts=BUILTINS[adapter][2]
            if host not in hosts: raise ValueError('BROWSER_SITE_ADAPTER_ORIGIN_MISMATCH')
            provider_id=adapter
        else:
            if any(host in item[2] for item in BUILTINS.values()):
                raise ValueError('BROWSER_SITE_USE_BUILTIN_ADAPTER')
            hosts=[host]
            provider_id='web-'+hashlib.sha256(host.encode()).hexdigest()[:16]
        if hosts_seen.intersection(hosts): raise ValueError('BROWSER_SITE_DUPLICATE_ORIGIN')
        hosts_seen.update(hosts)
        selectors={**GENERIC,**(row.get('selectors') or {})}
        if set(selectors)!=set(GENERIC): raise ValueError('BROWSER_SITE_SELECTORS_INVALID')
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

class SiteProfiles:
    def __init__(self,root): self.root=Path(root); self.path=self.root/'sites_v1.json'
    def get(self):
        if not self.path.exists():
            rows=normalize_sites(default_sites())
            return dict(schema_version='DesktopBrowserSites-v1',revision=0,sites=rows,profile_sha256=profile_hash(rows))
        state=json.loads(self.path.read_text(encoding='utf-8'))
        rows=normalize_sites(state['sites'])
        if (state.get('schema_version')!='DesktopBrowserSites-v1' or type(state.get('revision')) is not int
                or state['revision']<0 or state.get('profile_sha256')!=profile_hash(rows)):
            raise ValueError('BROWSER_SITE_SETTINGS_INVALID')
        return dict(state,sites=rows)
    @contextmanager
    def _lock(self):
        self.root.mkdir(parents=True,exist_ok=True)
        with (self.root/'sites.lock').open('a+b') as stream:
            stream.seek(0)
            if not stream.read(1): stream.write(b'0'); stream.flush()
            stream.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as error: raise ValueError('BROWSER_SITES_BUSY') from error
            try: yield
            finally:
                stream.seek(0)
                if os.name=='nt': msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
    def save(self,rows,expected_revision):
        accepted=normalize_sites(rows)
        if any(row["enabled"] and not verified_site(row) for row in accepted):
            raise ValueError("BROWSER_SITE_NOT_VERIFIED_OFFICIAL")
        with self._lock():
            current=self.get()
            if type(expected_revision) is not int or expected_revision!=current['revision']:
                raise ValueError('BROWSER_SITES_REVISION_CONFLICT')
            if accepted==current['sites']: return current
            state=dict(schema_version='DesktopBrowserSites-v1',revision=current['revision']+1,
                       sites=accepted,profile_sha256=profile_hash(accepted))
            writer=SettingsStore(self.root)
            writer._atomic_write(self.root/f'sites_before_{current["revision"]:06d}_{current["profile_sha256"]}.json',current)
            writer._atomic_write(self.path,state)
            return state

def extension_profile_script(rows):
    effective=[dict(row,enabled=row["enabled"] and verified_site(row)) for row in normalize_sites(rows)]
    data=dict(sites=effective,sha256=profile_hash(rows),official_web_revision=OFFICIAL_WEB_REVISION)
    hosts=sorted({host for _key,_name,origins,_proof in OFFICIAL_WEB_SITES for host in origins})
    script='"use strict";\nglobalThis.MEMORIVE_SITE_PROFILES = Object.freeze('+json.dumps(data,ensure_ascii=True,separators=(',',':'))+');\n'
    script+='(() => { const officialHosts = Object.freeze('+json.dumps(hosts)+');\n'
    script+=r"""
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
  const requireAllowedUrl = raw => {
    const url = requireCaptureUrl(raw);
    if (!globalThis.MEMORIVE_SITE_PROFILES.sites.some(site => site.enabled &&
        officialHosts.includes(new URL(site.url).hostname) && site.hosts.includes(url.hostname)))
      throw new Error("BROWSER_SITE_NOT_VERIFIED_OFFICIAL");
    return url;
  };
  globalThis.MEMORIVE_OFFICIAL_WEB = Object.freeze({requireAllowedUrl,requireCaptureUrl});
})();
"""
    return script

def configured_extension(source, destination, rows):
    """Derive a reloadable package with only explicitly configured origins."""
    source=Path(source); destination=Path(destination)
    manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    patterns=[f'https://{host}/*' for row in active_sites(rows) for host in row['hosts']]
    manifest['host_permissions']=patterns
    manifest['content_scripts']=[dict(matches=patterns,js=['site_profiles.js','recording.js','content.js'],run_at='document_start')] if patterns else []
    material={p.name:p.read_bytes() for p in source.iterdir() if p.is_file()}
    material['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    material['site_profiles.js']=extension_profile_script(rows).encode('utf-8')
    digest=canonical_sha256({k:hashlib.sha256(v).hexdigest() for k,v in sorted(material.items())})
    target=destination/digest
    target.mkdir(parents=True,exist_ok=True)
    for name,body in material.items():
        path=target/name
        if path.exists():
            if path.read_bytes()!=body: raise ValueError('BROWSER_PACKAGE_CANDIDATE_CHANGED')
        else:
            with path.open('xb') as out: out.write(body)
    return target
