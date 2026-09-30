"""EVO E6 bounded acquisition; all seeds/pages share one frozen request budget."""
from inspect import signature,Parameter
import re,time
from .common import sha,read,write,sealed
from .query_plan import plans,source_query
from .metadata_v3 import links
from .relations import identity_keys

def collect(runtime,run_id,params,directions,targets):
    from literature_discovery.source_connectors_literature_discovery_connector_framework import sha256_json
    from memorive_workflow.node_progress import scope
    options=params['discovery_options'];limits=options['limits'];started=time.monotonic()
    fetched=[];failures=[];observations={};records={};memberships={};seed_map={};seed_evidence=[];plan_rows=[]
    snapshots={s['provider']:s for s in params.get('source_snapshot',[])};request_index=0;seed_requests=0
    def fetch(source,query,count,**kwargs):
        nonlocal request_index
        if runtime.stop.is_set(): raise ValueError('RESEARCH_CANCELLED')
        if request_index>=limits['max_requests']: raise ValueError('SOURCE_REQUEST_LIMIT_REACHED')
        if time.monotonic()-started>=limits['max_elapsed_seconds']-34: raise ValueError('SOURCE_TIME_LIMIT_REACHED')
        request_index+=1;call_id='metadata-'+str(request_index).zfill(4)
        runtime._state(run_id,stage='COLLECTING',source=source,request_index=request_index)
        try:
            parameters=signature(runtime.client.fetch).parameters
            paged=all(k in parameters for k in ('cursor','source_snapshot')) or any(p.kind==Parameter.VAR_KEYWORD for p in parameters.values())
            if not paged: raise ValueError('SOURCE_PAGING_UNSUPPORTED')
            batch=runtime.client.fetch(source.upper(),query,count,run_id,call_id,runtime.stop,source_snapshot=snapshots.get(source),**kwargs)
            fetched.append({k:v for k,v in batch.items() if k!='records'});return batch
        except Exception as exc:
            path=runtime._run_path(run_id)/'requests'/(call_id+'.result.json');receipt=read(path) if path.exists() else {}
            failures.append({'source':source,'error_code':str(exc),'request_id':call_id,
                'external_call_performed':receipt.get('external_call_performed',False)})
            raise
    for direction in directions:
        seed_map[direction['id']]=[]
        for seed_id in direction.get('seed_ids',[]):
            record=runtime.work_index.seed(seed_id)
            status='LOCAL_METADATA' if record else 'UNRESOLVED'
            if not record and seed_requests<limits['max_seed_requests']:
                doi=re.sub(r'^(?:https?://(?:dx\.)?doi.org/|doi:\s*)','',seed_id.strip(),flags=re.I).lower()
                arxiv=re.sub(r'^(?:https?://arxiv.org/abs/|arxiv:)','',seed_id.strip(),flags=re.I)
                source='crossref' if re.fullmatch(r'10\.\d{4,9}/[^\s<>]+',doi) else 'arxiv' if re.fullmatch(r'(?:[a-z.-]+/)?\d{4}[.\d]+(?:v\d+)?',arxiv) else None
                if source and source in params['sources']:
                    seed_requests+=1
                    try:
                        batch=fetch(source,'',1,**({'exact_doi':doi} if source=='crossref' else {'exact_arxiv':arxiv}))
                        rows=[r for r in batch['records'] if (('doi:'+doi) in identity_keys(r['identifiers']) if source=='crossref' else r['identifiers'].get('arxiv_id')==re.sub(r'v\d+$','',arxiv))]
                        record=rows[0] if rows else None;status='RESOLVED_SOURCE' if record else 'SOURCE_ID_NOT_RESOLVED'
                    except ValueError: status='SOURCE_RESOLUTION_FAILED'
            if record:
                record=dict(record,seed_id=seed_id);seed_map[direction['id']].append(record)
            seed_evidence.append({'direction_id':direction['id'],'seed_id':seed_id,'status':status,'metadata':record})
        plan_rows.extend(plans(direction,seed_map[direction['id']],options))
    write(runtime._run_path(run_id)/'query_plan.json',sealed({'options':options,'directions':directions,
        'queries':plan_rows,'seeds':seed_evidence,'source_snapshots':params.get('source_snapshot',[])}))
    search_succeeded=0
    branches=[d['id']+':'+s for d in directions for s in params['sources'] if any(targets[c]>0 for c in d['channels'])]
    with scope('SOURCE_BRANCHES',branches,'PROCESSED_ITEMS') as branch_units:
        for direction in directions:
            channels=[c for c in direction['channels'] if targets[c]>0]
            if not channels: continue
            for source in params['sources']:
                for plan in [p for p in plan_rows if p['direction_id']==direction['id']]:
                    cursor=None;visited=set();seen_cursors=set();stop_reason='PAGE_LIMIT'
                    for page in range(limits['max_pages_per_query']):
                        try: batch=fetch(source,source_query(source,plan,options),limits['page_size'],cursor=cursor)
                        except ValueError as exc:
                            stop_reason=str(exc)
                            if stop_reason in {'SOURCE_REQUEST_LIMIT_REACHED','SOURCE_TIME_LIMIT_REACHED'}:
                                failures.append({'source':source,'direction_id':direction['id'],'error_code':stop_reason,'external_call_performed':False})
                            break
                        search_succeeded+=1
                        fingerprint=batch['response_sha256']
                        if fingerprint in visited:
                            failures.append({'source':source,'direction_id':direction['id'],'error_code':'SOURCE_PAGE_REPEATED','external_call_performed':False});stop_reason='REPEATED_PAGE';break
                        visited.add(fingerprint)
                        for record in batch['records']:
                            oid='source_observation_'+sha({'source':source,'record':record})[:24]
                            proof={'snapshot_sha256':batch['response_sha256'],'normalized_record_sha256':sha256_json(record),
                                'fixture_classification':'DESKTOP_METADATA_SNAPSHOT','network_used':bool(batch['external_call_performed']),
                                'acquisition_request_id':batch['request_id'],'cache_hit':bool(batch['cache_hit'])}
                            observation={'schema_version':'desktop.source-observation.e6.1','object_type':'SourceObservation','object_id':oid,
                                'revision':1,'producer':'Desktop_DESKTOP_METADATA','single_writer':'Desktop_DESKTOP_METADATA','run_id':run_id,
                                'source_connector':source.upper(),'source_snapshot_id':'desktop_snapshot_'+batch['response_sha256'][:24],
                                'discovery_origin':'KEYWORD_SEARCH','observed_at':batch['captured_at'],'identifiers':record['identifiers'],
                                'bibliographic':record['bibliographic'],'location_claims':record['location_claims'],'identity_status':'VERIFIED' if identity_keys(record['identifiers']) else 'UNRESOLVED',
                                'access_status':'METADATA_ONLY','decision_status':'PENDING','candidate_card_status':'NOT_REQUESTED',
                                'promotion_status':'NOT_ELIGIBLE','source_evidence':proof,'partial_fields':record['partial_fields'],'errors':[]}
                            observation['content_hash']=sha256_json(observation)
                            observations.setdefault(oid,observation);records[oid]=record
                            memberships.setdefault(oid,{}).setdefault(direction['id'],{'direction':direction,'channels':channels,
                                'query_sha256':plan['query_sha256']})
                        following=batch.get('next_cursor')
                        write(runtime._run_path(run_id)/'acquisition_checkpoint.json',sealed({'source':source,'direction_id':direction['id'],
                            'next_cursor':following,'observations':list(observations.values()),'source_results':fetched,'source_failures':failures,
                            'request_count':request_index,'limits':limits}))
                        if following is None or not batch.get('raw_count',len(batch['records'])): stop_reason='SOURCE_EXHAUSTED';break
                        if str(following) in seen_cursors:
                            failures.append({'source':source,'error_code':'SOURCE_CURSOR_REPEATED','external_call_performed':False});stop_reason='REPEATED_CURSOR';break
                        seen_cursors.add(str(following));cursor=following
                    plan.setdefault('source_outcomes',[]).append({'source':source,'stop_reason':stop_reason,'next_cursor':cursor})
                    if stop_reason in {'SOURCE_REQUEST_LIMIT_REACHED','SOURCE_TIME_LIMIT_REACHED'}: break
                branch_units.complete(direction['id']+':'+source)
    if not search_succeeded and failures:
        write(runtime._run_path(run_id)/'source_failures.json',sealed({'failures':failures,'external_api_calls':sum(bool(x.get('external_call_performed')) for x in failures)}))
        raise ValueError('ALL_LITERATURE_SOURCES_FAILED')
    write(runtime._run_path(run_id)/'query_outcomes.json',sealed({'plans':plan_rows,'request_count':request_index,'seed_requests':seed_requests,'elapsed_seconds':time.monotonic()-started}))
    return fetched,failures,observations,records,memberships,seed_map,seed_evidence

def resolve(observations,registry):
    from . import identity
    exact=[r for r in observations if identity_keys(r['identifiers'])]
    result=identity.resolve_identity_batch(exact,registry)
    # A source URL is a metadata identity, never an assertion of a verified DOI.
    for row in observations:
        if row in exact: continue
        oid=row['object_id'];key=row['identifiers'].get('source_record_id')
        result['candidate_identity_projections'].append({'candidate_id':'CANDIDATE:'+sha(key)[:24].upper(),
            'work_cluster_id':'WORK:'+sha(key)[:24].upper(),'identity_evidence_refs':[oid],
            'identity_status':'UNRESOLVED','source_record_verified':True,'manifestation_ids':[key]})
    result['statistics']['partial_source_records']=len(observations)-len(exact)
    return result
