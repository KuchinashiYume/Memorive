"""Manual public metadata acquisition, with a durable binding before every attempt."""
import threading, time, os, subprocess, re, json
from pathlib import Path
from .common import sha, now, write, read, sealed
from m16_external_radar.p04_t02_m16_live_metadata_transport import build_url, TransportPolicy
from m16_external_radar.p04_t02_m16_connector_framework import ArxivOfflineConnector, CrossrefOfflineConnector
from pr_os_settings.model_validation import UrllibHttpTransport
from pr_os_settings.metadata_transport import metadata_error_code, PublicMetadataHttpTransport, MetadataResponse
from urllib.parse import urlsplit, urlencode
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError

TERMS={
 'ARXIV':{'url':'https://info.arxiv.org/help/api/tou.html','reviewed_at':'2026-09-07','policy':'CC0 descriptive metadata; single connection, >=3 seconds; no PDF acquisition'},
 'CROSSREF':{'url':'https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/','reviewed_at':'2026-09-07','policy':'Public metadata API; no credentials; single connection; cache results; fail on 429'},
 'WEB-OF-SCIENCE':{'url':'https://developer.clarivate.com/apis/wos-starter','reviewed_at':'2026-09-07','policy':'Starter API subscription/key required; local private metadata only; no full text or redistribution'},
}
_lock=threading.Lock()
_last={}
# Provider page sizes, not total-recommendation limits. Verified 2026-09-07:
# arXiv user manual 3.1.1.2; Crossref REST tips/cursors; Clarivate Starter client.
PAGE_LIMITS={'ARXIV':2000,'CROSSREF':1000,'WEB-OF-SCIENCE':50}

class BoundedMetadataTransport:
    def request(self,**k):
        url=k['url'];parts=urlsplit(url)
        openalex=parts.netloc=='api.openalex.org' and (parts.path=='/works' or re.fullmatch(r'/(?:sources/S|authors/A)[0-9]+',parts.path))
        if parts.scheme!='https' or not (openalex or (parts.netloc,parts.path) in {('export.arxiv.org','/api/query'),('api.crossref.org','/works'),('api.clarivate.com','/apis/wos-starter/v1/documents')}):
            raise ValueError('METADATA_ENDPOINT_NOT_ALLOWED')
        required={'Accept','User-Agent'} | ({'X-ApiKey'} if parts.hostname=='api.clarivate.com' else set())
        if openalex and 'Authorization' in k['headers']:required.add('Authorization')
        if openalex and ('api_key' in parts.query or parts.fragment):raise ValueError('METADATA_CREDENTIAL_MUST_USE_HEADER')
        if k['method']!='GET' or k['body'] is not None or set(k['headers'])!=required:raise ValueError('METADATA_GET_REQUIRED')
        if any('\r' in v or '\n' in v for v in k['headers'].values()):raise ValueError('METADATA_HEADER_INVALID')
        limit=min(k['max_response_bytes'],2000000)
        if os.name=='nt':
            proxy=PublicMetadataHttpTransport._proxy(k['proxy_mode'],k['proxy_address'],parts.hostname)
            args=[str(PublicMetadataHttpTransport._curl_path()),'--disable','--silent','--show-error','--proto','=https','--max-redirs','0','--ca-native','--connect-timeout','10','--max-time','30','--max-filesize',str(limit),'--proxy',proxy,'--noproxy','']
            # Pass credentialed headers over stdin, never a process command line.
            args.extend(['--header','@-'])
            header_input='\n'.join(key+': '+value for key,value in k['headers'].items()).encode('utf8')
            args.extend(['--write-out','\n%{http_code}|%{ssl_verify_result}',url])
            result=subprocess.run(args,input=header_input,capture_output=True,timeout=33,creationflags=subprocess.CREATE_NO_WINDOW,shell=False,env={n:v for n,v in os.environ.items() if n.upper() not in {'CURL_CA_BUNDLE','SSL_CERT_FILE','SSL_CERT_DIR'}})
            if result.returncode:raise ValueError({28:'EXTERNAL_METADATA_TIMEOUT',60:'EXTERNAL_METADATA_TLS_CERTIFICATE',63:'EXTERNAL_RESPONSE_ENVELOPE_EXCEEDED'}.get(result.returncode,'EXTERNAL_METADATA_FETCH_FAILED'))
            body,_,suffix=result.stdout.rpartition(b'\n');status,verified=map(int,suffix.split(b'|'))
            if verified:raise ValueError('EXTERNAL_METADATA_TLS_CERTIFICATE')
        else:
            class NoRedirect(HTTPRedirectHandler):
                def redirect_request(self,*args,**kwargs):return None
            proxy={} if k['proxy_mode']=='NONE' else {'https':k['proxy_address']} if k['proxy_mode']=='CUSTOM' else None
            opener=build_opener(ProxyHandler(proxy),NoRedirect())
            try:response=opener.open(Request(url,headers=k['headers']),timeout=30)
            except HTTPError as exc:response=exc
            with response:status=response.code;body=response.read(limit+1)
        if len(body)>limit:raise ValueError('EXTERNAL_RESPONSE_ENVELOPE_EXCEEDED')
        return MetadataResponse(status,body,{'tls_verified':True,'redirects_followed':False})

class MetadataClient:
    def __init__(self,root,preferences,transport=None,source_config=None):
        self.root=Path(root);self.preferences=preferences
        self.transport=transport or BoundedMetadataTransport()
        self.source_config=source_config

    def fetch(self,provider,topic,count,run_id,call_id,stop,*,cursor=None,source_snapshot=None,exact_doi=None):
        from pr_os_settings.literature_sources import PRESETS
        from . import wos
        if provider.lower() not in PRESETS:raise ValueError('LITERATURE_PROVIDER_UNSUPPORTED')
        source=source_snapshot or (next((s for s in self.source_config() if s['provider']==provider.lower()),None) if self.source_config else None)
        endpoint=source['endpoint'] if source else PRESETS[provider.lower()][1]
        if endpoint!=PRESETS[provider.lower()][1]:raise ValueError('METADATA_ENDPOINT_NOT_ALLOWED')
        credential_env=(source['credential_env'] if source else 'WOS_API_KEY') if provider=='WEB-OF-SCIENCE' else ''
        credential=os.environ.get(credential_env,'').strip() if credential_env else ''
        if provider=='WEB-OF-SCIENCE' and not credential:raise ValueError('LITERATURE_CREDENTIAL_REQUIRED')
        if '\r' in credential or '\n' in credential:raise ValueError('LITERATURE_CREDENTIAL_INVALID')
        if type(count) is not int or not 1<=count<=PAGE_LIMITS[provider]:raise ValueError('SOURCE_PAGE_SIZE_INVALID')
        policy=TransportPolicy(max_records=PAGE_LIMITS[provider],user_agent='PR-OS/0.8.103 (desktop scholarly metadata)')
        query=({'search_query':'all:'+topic,'start':int(cursor or 0),'max_results':count,'sortBy':'relevance','sortOrder':'descending'} if provider=='ARXIV' else {'query':topic,'rows':count,'cursor':cursor or '*'})
        if exact_doi is not None:
            if provider!='CROSSREF' or count!=1 or cursor is not None or not isinstance(exact_doi,str) or not re.fullmatch(r'10\.\d{4,9}/[^\s<>]+',exact_doi):raise ValueError('EXACT_DOI_REQUEST_INVALID')
            query={'filter':'doi:'+exact_doi,'rows':1}
            url=endpoint+'?'+urlencode(query)
        elif provider=='WEB-OF-SCIENCE':
            query={'q':'TS=('+topic+')','db':'WOS','limit':count,'page':int(cursor or 1)}
            url=endpoint+'?'+urlencode(query)
        else:url=build_url(provider,query,policy)
        headers={'Accept':'application/atom+xml' if provider=='ARXIV' else 'application/json','User-Agent':policy.user_agent}
        if credential:headers['X-ApiKey']=credential
        pref=self.preferences()
        profile={'id':'P08_DESKTOP_PUBLIC_METADATA_V2','version':'2','provider':provider,'terms':TERMS[provider], 'max_records':count,'provider_page_limit':PAGE_LIMITS[provider],'max_bytes':2000000,'timeout':30,'retry_limit':0}
        route={'id':'settings-network','proxy_mode':pref.get('proxy_mode','SYSTEM'),'proxy_address_sha256':sha(pref.get('proxy_address','')),'provider_region':None,'egress_region':None,'observation_status':'NOT_EXPOSED_BY_TRANSPORT'}
        profile.update(endpoint=endpoint,credential_env=credential_env)
        key=sha({'provider':provider,'query':query,'endpoint':endpoint,'credential_env':credential_env})
        cache=self.root/'cache'/(key+'.json')
        if cache.exists():
            cached=read(cache)
            if time.time()-cached['stored_epoch']<86400:
                return dict(cached['result'],cache_hit=True,external_call_performed=False)
        request_dir=self.root/'runs'/run_id/'requests';request_dir.mkdir(parents=True,exist_ok=True)
        pre=sealed({'schema_version':'P08DesktopMetadataAttempt-v1','phase':'P08','task':'T00','module':'CROSS','run_id':run_id,'attempt_id':run_id,'call_id':call_id,'request_attempt_id':call_id,'recorded_at':now(),'stage':'PRE_SEND','profile':profile,'profile_sha256':sha(profile),'route':route,'route_sha256':sha(route),'role':'NON_MODEL_SCHOLARLY_METADATA_RETRIEVAL','evidence_class':'P08_DESKTOP_USER_REQUEST','requested_model':'N/A_NON_MODEL_API','returned_model':None,'input_sha256':sha(query),'source_sha256':sha(Path(__file__).read_bytes()),'runner_sha256':sha(Path(__file__).with_name('runtime.py').read_bytes()),'output_schema_sha256':sha('P08DesktopMetadataResult-v1'),'request':{'method':'GET','url':url,'query_sha256':sha(query)},'authorization':'EXPLICIT_MANUAL_DESKTOP_REQUEST','budget':{'request_limit':'SOURCE_PAGINATION_AND_USER_READING_TARGET','automatic_retries':0,'paid_model_calls':0},'receipt_path':str((request_dir/(call_id+'.result.json')).relative_to(self.root)),'status':'NOT_STARTED','external_call_performed':False})
        # Binding, including its on-disk checksum, must exist before any I/O.
        run_request = self.root/'runs'/run_id/'request.json'
        if run_request.exists() and read(run_request).get('trigger') == 'PASSIVE_OPT_IN':
            pre = sealed(dict(pre,authorization='EXPLICIT_OPT_IN_PASSIVE_RADAR'))
        with _lock:
            if stop.is_set():raise ValueError('RESEARCH_CANCELLED')
            delay=max(0,3.1-(time.monotonic()-_last.get(provider,0)))
            if delay and stop.wait(delay):raise ValueError('RESEARCH_CANCELLED')
            write(request_dir/(call_id+'.presend.json'),pre)
            if read(request_dir/(call_id+'.presend.json'))!=pre:raise ValueError('PRE_SEND_EVIDENCE_BINDING_BLOCKED')
            started=time.monotonic();terminal=dict(pre,stage='TERMINAL',started_at=now(),external_call_performed=True)
            try:
                response=self.transport.request(method='GET',url=url,headers=headers,body=None,timeout_seconds=30,proxy_mode=pref.get('proxy_mode','SYSTEM'),proxy_address=pref.get('proxy_address',''),max_response_bytes=2000000)
                terminal.update(http_status=response.status_code,response_sha256=sha(response.body),response_bytes=len(response.body))
                if response.status_code!=200:raise ValueError('SOURCE_HTTP_'+str(response.status_code))
                if stop.is_set():raise ValueError('RESEARCH_CANCELLED')
                parsed=wos.parse(response.body) if provider=='WEB-OF-SCIENCE' else (ArxivOfflineConnector if provider=='ARXIV' else CrossrefOfflineConnector)({})._parse(response.body)
                if len(parsed['records'])>count:raise ValueError('SOURCE_RECORD_ENVELOPE_EXCEEDED')
                rawpath=request_dir/(call_id+'.response');rawpath.write_bytes(response.body)
                result={'schema_version':'P08DesktopMetadataResult-v1','provider':provider,'captured_at':now(),'records':parsed['records'],'skipped_records':parsed.get('skipped_records',[]),'response_sha256':sha(response.body),'request_id':call_id,'cache_hit':False,'external_call_performed':True,'source_schema':'ARXIV_ATOM_API' if provider=='ARXIV' else 'CROSSREF_REST_WORKS_JSON'}
                terminal.update(status='SUCCEEDED',record_count=len(parsed['records']))
                if provider=='WEB-OF-SCIENCE':result['source_schema']='WOS_STARTER_DOCUMENTS_JSON'
                if provider=='CROSSREF':
                    document=json.loads(response.body);message=document.get('message',{})
                    result['next_cursor']=message.get('next-cursor') if len(parsed['records'])>=count else None
                    result['total_results']=message.get('total-results')
                elif provider=='ARXIV':
                    from defusedxml import ElementTree
                    feed=ElementTree.fromstring(response.body)
                    total=feed.findtext('{http://a9.com/-/spec/opensearch/1.1/}totalResults')
                    result['total_results']=int(total) if total else None
                    following=int(cursor or 0)+count
                    result['next_cursor']=following if len(parsed['records'])>=count and (not total or following<int(total)) else None
                else:
                    document=json.loads(response.body);total=document.get('metadata',{}).get('total')
                    result['total_results']=total;following=int(cursor or 1)+1
                    result['next_cursor']=following if len(parsed['records'])>=count and (total is None or (following-1)*count<total) else None
                write(cache,sealed({'stored_epoch':time.time(),'result':result}))
                return result
            except Exception as exc:
                code=getattr(exc,'code',None) or (str(exc) if re.fullmatch(r'[A-Z][A-Z0-9_]{1,100}',str(exc)) else metadata_error_code(exc))
                terminal.update(status='CANCELLED' if stop.is_set() else 'FAILED',error_code=code)
                raise ValueError(code) from exc
            finally:
                _last[provider]=time.monotonic()
                terminal.update(finished_at=now(),latency_ms=round((time.monotonic()-started)*1000),input_tokens=0,output_tokens=0,cost=0,currency='USD',accounting_status='NON_MODEL_PUBLIC_API',production_qualification='NOT_GRANTED')
                if provider=='WEB-OF-SCIENCE':terminal.update(cost=None,accounting_status='NON_MODEL_SUBSCRIPTION_USAGE_NOT_EXPOSED')
                write(request_dir/(call_id+'.result.json'),sealed(terminal))
