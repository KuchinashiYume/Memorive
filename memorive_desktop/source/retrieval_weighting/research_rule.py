"""Observe exact DOI metadata through the existing configured metadata client.

No title guess, no invented impact factor or h-index, no paper upload.
Missing fields stay unknown; bibliometrics are a ranking signal, not quality.
"""
import re,math,threading
from datetime import datetime,timezone
from memorive_research_workspace.store import digest,now,uid

def bind(workspace,service):
    from memorive_research_runtime.transport import MetadataClient
    from .research_bibliometrics import BibliometricsClient
    client=MetadataClient(workspace.store.root/'rule_metadata',lambda:service.call('settings.get_state',{})['settings']['preferences'])
    metrics=BibliometricsClient(workspace.store.root/'bibliometrics',client.preferences,client.transport)
    workspace.index.refresh_rule_metadata=lambda ids,policy_config=None:refresh(workspace,ids,client,service.call('settings.external_sources_get',{})['literature']['slots'],metrics,policy_config)

def score(observation):
    parts=components(observation)
    weights={'recency':.2,'cited_by':.2,'doc_type':.2,'journal_metric':.25,'author_h_index':.15}
    return sum(weights[k]*v for k,v in parts.items())/sum(weights[k] for k in parts) if parts else None

def components(observation):
    fields=observation.get('fields',{});parts={};year=fields.get('year');count=fields.get('cited_by')
    if type(year) is int and 1600<=year<=datetime.now(timezone.utc).year:
        parts['recency']=2**(-(datetime.now(timezone.utc).year-year)/20)
    if type(count) is int and count>=0:parts['cited_by']=min(1,math.log1p(count)/math.log(1001))
    types={'journal-article':.65,'proceedings-article':.55,'book':.6,'book-chapter':.55,'dissertation':.5,'posted-content':.45,'report':.5,'dataset':.55}
    if fields.get('doc_type') in types:parts['doc_type']=types[fields['doc_type']]
    metric=fields.get('journal_metric') or {}
    if metric.get('kind') in {'jcr_jif','openalex_2yr_mean_citedness'} and type(metric.get('value')) in (int,float) and math.isfinite(metric['value']) and metric['value']>=0:
        parts['journal_metric']=min(1,math.log1p(metric['value'])/math.log(31))
    authors=(fields.get('author_metrics') or {}).get('authors',[])
    values=[min(1,math.log1p(a['h_index'])/math.log(101)) for a in authors if type(a.get('h_index')) is int and a['h_index']>=0 and a.get('id')]
    if values:parts['author_h_index']=sum(values)/len(values)
    return parts

def find_doi(workspace,artifact):
    binding=artifact.get('source_binding') or {}
    if binding:
        doc=workspace.store.get('connector_document',binding.get('source_id','')) or {}
        doi=doc.get('doi','').lower().removeprefix('https://doi.org/').strip()
        if re.fullmatch(r'10\.\d{4,9}/[^\s<>]+',doi):return doi
    with workspace.store.tx() as db:
        rows=db.execute('SELECT id FROM chunks WHERE artifact_id=? ORDER BY page,line_start LIMIT 4',(artifact['id'],)).fetchall()
        text='\n'.join(workspace.index.read(r['id'],artifact['project'],db=db)['text'] for r in rows)
    found=list(dict.fromkeys(x.rstrip('.,;)').lower() for x in re.findall(r'10\.\d{4,9}/[-._;()/:A-Z0-9]+',text,re.I)))
    return found[0] if len(found)==1 else None

def refresh(workspace,artifact_ids,client,sources,metrics=None,policy_config=None):
    configured=next((s for s in sources if s.get('provider')=='crossref' and s.get('enabled')),None)
    if not configured:return
    for aid in artifact_ids:
        a=workspace.store.get('artifact',aid)
        if not a or a['state']!='active' or a['kind']=='derived' or a.get('temporary_thread'):continue
        doc=a.get('document_id',aid);key=a['project']+':'+doc
        old=workspace.store.get('research_rule',key)
        config=policy_config or workspace.weights.settings(a['project'])['config']
        config_hash=digest(['CROSSREF_EXACT_DOI_V1',config.get('rule_enrichment',True),config.get('rule_credential_env','OPENALEX_API_KEY')])
        if old and old.get('content_hash')==a['content_hash'] and old.get('configuration_hash')==config_hash:
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(old['observed_at'])).total_seconds()
            if 0<=age<(30*86400 if old['status']=='observed' else 86400):continue
        observation={'document_id':doc,'content_hash':a['content_hash'],'observed_at':now(),'status':'unknown','fields':{},'normalization':'recency_cited_type_journal_author_v2','configuration_hash':config_hash}
        try:
            doi=find_doi(workspace,a)
            if not doi:observation['reason']='DOI_MISSING_OR_AMBIGUOUS'
            else:
                run=uid('rule_');r=client.fetch('CROSSREF',doi,1,run,run,threading.Event(),source_snapshot=configured,exact_doi=doi)
                exact=next((v for v in r['records'] if v.get('identifiers',{}).get('doi','').lower()==doi),None)
                if not exact:raise ValueError('DOI_RECORD_NOT_CONFIRMED')
                fields={'doi':doi,'doc_type':exact['bibliographic'].get('record_type')}
                date=exact['bibliographic'].get('published_date')
                if date and re.match(r'^\d{4}',date):fields['year']=int(date[:4])
                import json
                # Only the exact request locator owned by this client, including a cache hit.
                cid=r['request_id'];raw=client.root/'runs'/cid/'requests'/(cid+'.response')
                if raw.exists() and digest(raw.read_bytes()).upper()==r['response_sha256'].upper():
                    record=next((x for x in json.loads(raw.read_bytes())['message']['items'] if str(x.get('DOI','')).lower()==doi),{})
                    if type(record.get('is-referenced-by-count')) is int:fields['cited_by']=record['is-referenced-by-count']
                    if record.get('container-title'):fields['journal']=record['container-title'][0]
                observation.update(status='observed',fields=fields,source='Crossref',source_hash=r['response_sha256'],request_id=cid)
                if metrics and config.get('rule_enrichment',True):
                    enrichment=metrics.enrich(doi,config.get('rule_credential_env','OPENALEX_API_KEY'))
                    observation['enrichment']=enrichment
                    if enrichment['status']=='OBSERVED':fields.update(enrichment['fields'])
                observation['components']=components(observation)
        except (ValueError,OSError,KeyError,TypeError) as exc:observation['reason']=str(exc)[:120]
        with workspace.store.tx() as db:
            workspace.store.put('research_rule',key,a['project'],observation,db=db)
            workspace.store.put('research_rule_history',uid('rule_observation_'),a['project'],observation,expected=0,db=db)
            workspace.store.event('DECISION_LOG_RULE_METADATA_OBSERVED',doc,{'status':observation['status'],'source_hash':observation.get('source_hash')},db)
