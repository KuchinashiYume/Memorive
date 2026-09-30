"""Bounded official public materials. No search provider, credentials or background work."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json, urllib.request, urllib.parse, re
import xml.etree.ElementTree as ET
from .common import read, write, sealed, sha, now

SOURCES = {
    'openai_news': {'label':'OpenAI 公告', 'category':'announcement', 'format':'rss',
        'endpoint':'https://openai.com/news/rss.xml', 'hosts':['openai.com']},
    'deepmind_news': {'label':'Google DeepMind 公告', 'category':'announcement', 'format':'rss',
        'endpoint':'https://deepmind.google/blog/rss.xml', 'hosts':['deepmind.google']},
    'claude_code_releases': {'label':'Claude Code 发布记录', 'category':'tool', 'format':'github',
        'endpoint':'https://api.github.com/repos/anthropics/claude-code/releases?per_page=10',
        'hosts':['github.com'], 'path_prefix':'/anthropics/claude-code/releases/tag/'},
}
NORMALIZER='MemoAIPublicMaterials-v1'
BODY_LIMIT=2000000

class Text(HTMLParser):
    def __init__(self, main_only=False):
        super().__init__(); self.parts=[]; self.skip=0; self.depth=0; self.main_only=main_only
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','nav','footer','header'}: self.skip+=1
        if tag in {'main','article'}:self.depth+=1
    def handle_endtag(self,tag):
        if tag in {'script','style','nav','footer','header'}:self.skip=max(0,self.skip-1)
        if tag in {'main','article'}:self.depth=max(0,self.depth-1)
    def handle_data(self,data):
        if not self.skip and (not self.main_only or self.depth):self.parts.append(data)
    def value(self):return re.sub(r'\s+',' ',' '.join(self.parts)).strip()

def plain(value, main_only=False):
    p=Text(main_only);p.feed(value or '');return p.value()

def source_url(source,url):
    if not isinstance(url,str):raise ValueError('AI_SOURCE_URL_INVALID')
    u=urllib.parse.urlsplit(url);spec=SOURCES[source]
    if u.scheme!='https' or u.hostname not in spec['hosts'] or u.username or u.password or u.port not in {None,443}:
        raise ValueError('AI_SOURCE_URL_INVALID')
    if not u.path.startswith(spec.get('path_prefix','/')):raise ValueError('AI_SOURCE_URL_INVALID')
    return urllib.parse.urlunsplit((u.scheme,u.netloc,u.path,u.query,''))

def date_value(value):
    if not value:return None
    try:
        try:stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError:stamp=parsedate_to_datetime(value)
        if stamp.tzinfo is None:return None
        return stamp.astimezone(timezone.utc).isoformat()
    except (TypeError,ValueError,OverflowError):return None

def normalize(source,body,retrieved_at):
    spec=SOURCES[source];raw=[]
    if spec['format']=='rss':
        root=ET.fromstring(body)
        for item in root.findall('.//item'):
            raw.append((item.findtext('title'),item.findtext('link'),item.findtext('pubDate'),
                        plain(item.findtext('description')),'feed_summary'))
    else:
        values=json.loads(body)
        if not isinstance(values,list):raise ValueError('AI_SOURCE_FORMAT_INVALID')
        for item in values:
            if item.get('draft') or item.get('prerelease'):continue
            raw.append((item.get('name') or item.get('tag_name'),item.get('html_url'),item.get('published_at'),
                        item.get('body') or '', 'release_notes'))
    rows=[];seen=set()
    for title,url,published,content,extent in raw:
        try:url=source_url(source,url)
        except (ValueError,TypeError):continue
        if not isinstance(title,str) or not title.strip():continue
        stamp=date_value(published);content=str(content).strip()
        original_length=len(content)
        if original_length>8000:content=content[:8000];extent+='_excerpt'
        if not content:extent='title_only'
        version=sha({'url':url,'title':title,'published_at':stamp,'content':content,'normalizer':NORMALIZER})
        if version in seen:continue
        seen.add(version)
        rows.append({'source_id':'ai-source-'+version[:24],'source':source,'source_name':spec['label'],
            'category':spec['category'],'title':title.strip()[:500],'url':url,'published_at':stamp,
            'event_at':None,'retrieved_at':retrieved_at,'body':content,'body_extent':extent,
            'original_body_chars':original_length,'version':version})
    return rows

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None

class PublicSources:
    def __init__(self,root):
        self.root=root/'ai_sources';self.root.mkdir(parents=True,exist_ok=True)
    def cached(self):
        values=[]
        for name,spec in SOURCES.items():
            path=self.root/(name+'.json');c=read(path) if path.exists() else {}
            values.append(dict(id=name,**spec,fetched_at=c.get('fetched_at'),checked_at=c.get('checked_at'),
                item_count=len(c.get('items',[])),status=c.get('status','NOT_FETCHED'),error_code=c.get('error_code')))
        return values
    def _fetch(self,url,run_path,checkpoint):
        checkpoint()
        ledger=run_path/'http_attempts.jsonl'
        rows=ledger.read_text('utf8').splitlines() if ledger.exists() else []
        if len(rows)>=7:raise ValueError('AI_SOURCE_REQUEST_LIMIT')
        event={'url':url,'started_at':now(),'attempt':len(rows)+1}
        with ledger.open('a',encoding='utf8') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n');f.flush()
        request=urllib.request.Request(url,headers={'User-Agent':'Memorive/1.01 (AI public briefing)',
            'Accept':'application/rss+xml, application/json, text/xml, text/html'})
        with urllib.request.build_opener(NoRedirect).open(request,timeout=25) as response:
            if response.status!=200:raise ValueError('AI_SOURCE_HTTP_FAILED')
            body=response.read(BODY_LIMIT+1)
        if len(body)>BODY_LIMIT:raise ValueError('AI_SOURCE_BODY_LIMIT')
        checkpoint()
        (run_path/('public-'+sha(body)+'.body')).write_bytes(body)
        return body
    def collect(self,enabled,start,end,run_path,checkpoint,refresh=False,only=None):
        materials=[];failures=[];unknown=[];statuses=[]
        from memorive_workflow.node_progress import scope
        progress=scope('SOURCE_BRANCHES',enabled,basis='PROCESSED_ITEMS')
        for source in enabled:
            checkpoint();path=self.root/(source+'.json');old=read(path) if path.exists() else None
            cache=old
            fresh=bool(old and old.get('status')=='READY' and
                (datetime.now(timezone.utc)-datetime.fromisoformat(old['checked_at'])).total_seconds()<21600)
            do_fetch=(refresh or not fresh) and (only is None or source in only)
            try:
                if do_fetch:
                    body=self._fetch(SOURCES[source]['endpoint'],run_path,checkpoint)
                    at=now();items=normalize(source,body,at)
                    # Feed-only content remains explicitly labelled; never invent an abstract.
                    relevant=[r for r in items if r['published_at'] and start<=r['published_at']<end]
                    for item in relevant[:2]:
                        if item['body_extent']=='title_only':
                            try:
                                article=self._fetch(item['url'],run_path,checkpoint)
                                content=plain(article.decode('utf8'),True)
                                if content:
                                    item.update(body=content[:8000],body_extent='article_excerpt' if len(content)>8000 else 'article_main_text',original_body_chars=len(content))
                                    item['version']=sha({k:item[k] for k in ('url','title','published_at','body','body_extent')})
                                    item['source_id']='ai-source-'+item['version'][:24]
                            except Exception:
                                checkpoint() # Cancellation must not become partial-source success.
                    prior={r['version']:r for r in (old or {}).get('items',[])}
                    for item in items:
                        if item['version'] in prior:item['retrieved_at']=prior[item['version']]['retrieved_at']
                    version=sha([r['version'] for r in items])
                    cache=sealed({'source':source,'items':items,'material_version':version,'status':'READY',
                        'fetched_at':old['fetched_at'] if old and old.get('material_version')==version else at,'checked_at':at})
                    write(path,cache)
                if not cache or cache.get('status')!='READY':raise ValueError('AI_SOURCE_CACHE_UNAVAILABLE')
                unknown.extend(r for r in cache['items'] if not r['published_at'])
                candidates=[r for r in cache['items'] if r['published_at'] and start<=r['published_at']<end]
                candidates.sort(key=lambda x:x['published_at'],reverse=True)
                materials.extend(candidates[:6])
                statuses.append({'source':source,'status':'READY','fetched_at':cache['fetched_at'],'checked_at':cache['checked_at'],
                    'range_count':len(candidates),'material_version':cache['material_version']})
            except Exception as exc:
                checkpoint()
                code=str(exc) if re.fullmatch('[A-Z_]{1,80}',str(exc)) else 'AI_SOURCE_FETCH_FAILED'
                failures.append({'source':source,'error_code':code})
                statuses.append({'source':source,'status':'FAILED','error_code':code,'previous_cache_retained':bool(old)})
            progress.complete(source,limited=any(f['source']==source for f in failures))
        progress.close()
        # Same URL+version is one material; distinct publications and changed details survive.
        unique={r['source_id']:r for r in materials}
        return {'materials':list(unique.values()),'failures':failures,'sources':statuses,
            'unknown_date_count':len(unknown),'normalizer':NORMALIZER,'collected_at':now()}
