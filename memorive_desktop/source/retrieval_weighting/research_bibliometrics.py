"""DOI-bound OpenAlex metadata, through the existing bounded transport.

OpenAlex mean citedness is never labelled JIF; author identity comes from an
exact DOI work and an exact author ID, never name matching.
"""
import os,re,json,time,threading
from pathlib import Path
from urllib.parse import urlencode
from memorive_research_workspace.store import digest,now,uid
from memorive_research_runtime.common import sealed,write,read
from memorive_research_runtime.transport import BoundedMetadataTransport

class BibliometricsClient:
    def __init__(self,root,preferences,transport=None):
        self.root=Path(root);self.preferences=preferences;self.transport=transport or BoundedMetadataTransport();self.lock=threading.Lock()
    def get(self,entity,identity,credential_env='OPENALEX_API_KEY'):
        if entity not in {'works','sources','authors'}:raise ValueError('METRIC_ENTITY_INVALID')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',credential_env):raise ValueError('METRIC_CREDENTIAL_REFERENCE_INVALID')
        if entity=='works':
            if not re.fullmatch(r'10\.\d{4,9}/[^\s<>]+',identity):raise ValueError('METRIC_DOI_INVALID')
            endpoint='https://api.openalex.org/works';query={'filter':'doi:https://doi.org/'+identity,'per-page':2}
        else:
            if not re.fullmatch('S[0-9]+' if entity=='sources' else 'A[0-9]+',identity):raise ValueError('METRIC_ID_INVALID')
            endpoint='https://api.openalex.org/'+entity+'/'+identity;query={}
        pref=self.preferences();key=digest([endpoint,query,credential_env,pref.get('proxy_mode'),digest(pref.get('proxy_address',''))])
        cache=self.root/'cache'/(key+'.json')
        with self.lock:
            if cache.exists():
                cached=read(cache);age=time.time()-cached['stored_epoch']
                if 0<=age<(30*86400 if cached['status']=='OBSERVED' else 86400):return dict(cached,cache_hit=True)
            call=uid('metric_');folder=self.root/'attempts'/call;folder.mkdir(parents=True,exist_ok=False)
            request={'schema_version':'MemoBibliometricsAttempt-v1','call_id':call,'profile':'OPENALEX_EXACT_IDENTIFIER_V1',
                'role':'NON_MODEL_METADATA','requested_model':'N/A_NON_MODEL_API','endpoint':endpoint,
                'query':query,'credential_env':credential_env,'behavior_sha256':digest([endpoint,query]),
                'route':{'provider':'OpenAlex','proxy_mode':pref.get('proxy_mode','SYSTEM'),'proxy_address_sha256':digest(pref.get('proxy_address',''))},
                'region':'NOT_EXPOSED','egress':'SETTINGS_NETWORK','input_tokens':0,'output_tokens':0,'actual_cost':None,
                'accounting':'NON_MODEL_API_PROVIDER_QUOTA','started_at':now(),'status':'PRE_SEND'}
            write(folder/'presend.json',sealed(request))
            if read(folder/'presend.json')!=sealed(request):raise ValueError('PRE_SEND_EVIDENCE_BINDING_BLOCKED')
            token=os.environ.get(credential_env,'').strip();wire=dict(query)
            headers={'Accept':'application/json','User-Agent':'Memorive/0.8.116 scholarly metadata'}
            started=time.monotonic();result={'status':'UNKNOWN','stored_epoch':time.time(),'request_id':call,'observed_at':now(),'cache_hit':False}
            try:
                if token:
                    if any(c in token for c in '\r\n'):raise ValueError('METRIC_CREDENTIAL_INVALID')
                    headers['Authorization']='Bearer '+token
                response=self.transport.request(method='GET',url=endpoint+('?' +urlencode(wire) if wire else ''),
                    headers=headers,body=None,timeout_seconds=20,
                    proxy_mode=pref.get('proxy_mode','SYSTEM'),proxy_address=pref.get('proxy_address',''),max_response_bytes=2000000)
                request['http_status']=response.status_code
                if response.status_code!=200:raise ValueError('METRIC_HTTP_'+str(response.status_code))
                value=json.loads(response.body)
                if not isinstance(value,dict):raise ValueError('METRIC_RESPONSE_INVALID')
                result.update(status='OBSERVED',value=value,response_sha256=digest(response.body))
                reported=(value.get('meta') or {}).get('cost_usd')
                if type(reported) in (int,float) and 0<=reported<100:result['provider_quota_cost_usd']=reported
                (folder/'response.json').write_bytes(response.body)
            except (OSError,ValueError,TypeError) as e:
                reason=str(e);result['reason']=reason if re.fullmatch(r'[A-Z][A-Z0-9_]{1,100}',reason) else 'METRIC_TRANSPORT_UNAVAILABLE'
            finally:
                wire.clear();headers.clear();token=''
                request.update(status=result['status'],returned_model='N/A_NON_MODEL_API',duration_ms=round((time.monotonic()-started)*1000),finished_at=now(),response_sha256=result.get('response_sha256'),reason=result.get('reason'),provider_quota_cost_usd=result.get('provider_quota_cost_usd'))
                write(folder/'terminal.json',sealed(request))
            write(cache,sealed(result));return result

    def enrich(self,doi,credential_env='OPENALEX_API_KEY'):
        out={'status':'UNKNOWN','provider':'OpenAlex','observed_at':now(),'fields':{},'requests':[]}
        observed={}
        def get(entity,identity):
            r=self.get(entity,identity,credential_env);observed[entity+':'+identity]=r.get('observed_at');out['requests'].append({k:r.get(k) for k in ('request_id','status','response_sha256','cache_hit','reason','provider_quota_cost_usd')});return r.get('value',{})
        work=get('works',doi)
        exact=[r for r in work.get('results',[]) if str(r.get('doi','')).lower().removeprefix('https://doi.org/')==doi.lower()]
        if len(exact)!=1:out['reason']='EXACT_DOI_NOT_CONFIRMED';return out
        work=exact[0];fields=out['fields'];fields['work_id']=work['id'];fields['doi']=doi
        source=(work.get('primary_location') or {}).get('source') or {};sid=str(source.get('id','')).rsplit('/',1)[-1]
        if re.fullmatch('S[0-9]+',sid):
            journal=get('sources',sid)
            if str(journal.get('id','')).rsplit('/',1)[-1]==sid:
                metric=(journal.get('summary_stats') or {}).get('2yr_mean_citedness')
                if type(metric) in (int,float) and 0<=metric<10000:
                    fields.update(journal_metric={'value':metric,'kind':'openalex_2yr_mean_citedness','source_id':journal['id'],'observed_at':observed['sources:'+sid],'source_updated':journal.get('updated_date')})
                if journal.get('display_name'):fields['journal']=journal['display_name']
                fields['source_type']=journal.get('type')
        selected=[a for a in work.get('authorships',[]) if a.get('author_position')=='first' or a.get('is_corresponding') is True][:3]
        authors=[]
        for row in selected:
            identity=str((row.get('author') or {}).get('id','')).rsplit('/',1)[-1]
            if not re.fullmatch('A[0-9]+',identity):continue
            author=get('authors',identity);h=(author.get('summary_stats') or {}).get('h_index')
            if str(author.get('id','')).rsplit('/',1)[-1]!=identity or type(h) is not int or h<0:continue
            authors.append({'id':author['id'],'name':author.get('display_name'),'h_index':h,'orcid':author.get('orcid'),
                'role':'corresponding' if row.get('is_corresponding') else 'first','observed_at':observed['authors:'+identity]})
        if authors:fields['author_metrics']={'provider':'OpenAlex','authors':authors,'coverage':'first_and_corresponding_up_to_three','matched_by':'EXACT_DOI_AUTHOR_ID'}
        out['status']='OBSERVED';return out
