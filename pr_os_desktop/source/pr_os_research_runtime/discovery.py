"""Desktop adapter for P04 identity, relation, slate and feedback modules."""
from datetime import datetime,timezone
from inspect import signature,Parameter
from .common import CHANNELS,sha,now,read,write,sealed
from .directions import effective_directions,channel_targets
from .relations import metadata_comparison
from .transport import PAGE_LIMITS

def run(runtime,run_id,params):
    from . import identity,slate
    from m16_external_radar.p04_t02_m16_connector_framework import sha256_json
    config=params['config'];directions=effective_directions(config);targets=channel_targets(config,directions)
    fetched=[];failures=[];observations={};record_by_id={};memberships={};request_index=0
    snapshots={s['provider']:s for s in params.get('source_snapshot',[])}
    for direction in directions:
        channels=[c for c in direction['channels'] if targets[c]>0]
        if not channels:continue
        for source in params['sources']:
            cursor=None;visited=set();received=0
            page_size=min(config['count'],PAGE_LIMITS[source.upper()])
            while received<config['count']:
                if runtime.stop.is_set():raise ValueError('RESEARCH_CANCELLED')
                request_index+=1;call_id='metadata-'+str(request_index).zfill(4)
                runtime._state(run_id,stage='COLLECTING',source=source,request_index=request_index)
                try:
                    # Compatibility is decided before sending, never by
                    # catching TypeError and sending the same request again.
                    parameters=signature(runtime.client.fetch).parameters
                    paged=all(k in parameters for k in ('cursor','source_snapshot')) or any(p.kind==Parameter.VAR_KEYWORD for p in parameters.values())
                    kwargs={'cursor':cursor,'source_snapshot':snapshots.get(source)} if paged else {}
                    batch=runtime.client.fetch(source.upper(),direction['query'],page_size,run_id,call_id,runtime.stop,**kwargs)
                except Exception as exc:
                    receipt_path=runtime._run_path(run_id)/'requests'/(call_id+'.result.json')
                    transport=read(receipt_path) if receipt_path.exists() else {}
                    failures.append({'source':source,'direction_id':direction['id'],'error_code':str(exc),
                        'request_id':call_id,'external_call_performed':transport.get('external_call_performed',False)})
                    break
                fetched.append({k:v for k,v in batch.items() if k!='records'})
                received+=len(batch['records'])
                for record in batch['records']:
                    oid='source_observation_'+sha({'source':source,'record':record})[:24]
                    proof={'snapshot_sha256':batch['response_sha256'],'normalized_record_sha256':sha256_json(record),
                        'fixture_classification':'DESKTOP_METADATA_SNAPSHOT','network_used':bool(batch['external_call_performed']),
                        'acquisition_request_id':batch['request_id'],'cache_hit':bool(batch['cache_hit'])}
                    observation={'schema_version':'p08.desktop.source-observation.1','object_type':'SourceObservation','object_id':oid,
                        'revision':1,'producer':'P08_DESKTOP_METADATA','single_writer':'P08_DESKTOP_METADATA','run_id':run_id,
                        'source_connector':source.upper(),'source_snapshot_id':'desktop_snapshot_'+batch['response_sha256'][:24],
                        'discovery_origin':'KEYWORD_SEARCH','observed_at':batch['captured_at'],'identifiers':record['identifiers'],
                        'bibliographic':record['bibliographic'],'location_claims':record['location_claims'],'identity_status':'VERIFIED',
                        'access_status':'METADATA_ONLY','decision_status':'PENDING','candidate_card_status':'NOT_REQUESTED',
                        'promotion_status':'NOT_ELIGIBLE','source_evidence':proof,'partial_fields':record['partial_fields'],'errors':[]}
                    observation['content_hash']=sha256_json(observation)
                    observations.setdefault(oid,observation);record_by_id[oid]=record
                    memberships.setdefault(oid,{}).setdefault(direction['id'],{'direction':direction,'channels':channels,
                        'query_sha256':sha(direction['query']).upper()})
                following=batch.get('next_cursor')
                write(runtime._run_path(run_id)/'acquisition_checkpoint.json',sealed({
                    'source':source,'direction_id':direction['id'],'next_cursor':following,
                    'observations':list(observations.values()),'source_results':fetched,'source_failures':failures,
                    'stop_boundary':'USER_READING_TARGET_OR_SOURCE_EXHAUSTION_OR_CANCEL_OR_SOURCE_CONSTRAINT'}))
                if not following or not batch['records']:break
                if not paged:
                    failures.append({'source':source,'error_code':'SOURCE_PAGING_UNSUPPORTED','external_call_performed':False});break
                # A repeated page is a partial-source failure, not an endless retry.
                fingerprint=sha(batch['records'])
                if fingerprint in visited:
                    failures.append({'source':source,'error_code':'SOURCE_PAGE_REPEATED','external_call_performed':False});break
                visited.add(fingerprint);cursor=following
    if not fetched:
        calls=sum(bool(f.get('external_call_performed')) for f in failures)
        write(runtime._run_path(run_id)/'source_failures.json',sealed({'failures':failures,'external_api_calls':calls,'network_calls':calls}))
        raise ValueError('ALL_LITERATURE_SOURCES_FAILED')
    runtime._state(run_id,stage='RESOLVING_IDENTITIES')
    registry={oid:{'content_hash':o['content_hash'],'source_snapshot_id':o['source_snapshot_id'],
        **{k:o['source_evidence'][k] for k in ('snapshot_sha256','normalized_record_sha256')}} for oid,o in observations.items()}
    resolved=identity.resolve_identity_batch(list(observations.values()),registry)
    library=[r for r in runtime.api.call('library.bootstrap',{}).get('records',[]) if not str(r.get('artifact_id','')).startswith('research-')]
    feedback=runtime.feedback.snapshot()
    seen={(e['work_cluster_id'],e['direction_id']) for e in feedback['events'] if e['event_type']=='EXPOSURE_RECORDED'}
    caps={d:config['exposure_limits'].get(d) or config['count'] for d in slate.CAP_ORDER}
    if config['exposure_limits']['direction_share'] is not None:
        caps['direction']=min(caps['direction'],int(config['count']*config['exposure_limits']['direction_share']))
    policy={'object_type':'ResearchExposurePolicy','human_confirmed':True,'no_backfill':True,'channel_targets':targets,
        'concentration_caps':caps,'batch_size':config['count'],'whitespace_slots':0,
        'max_direction_share':config['exposure_limits']['direction_share'] or 1}
    context=slate.seal_object({'schema_version':'p08.desktop.research-context.1','is_synthetic':False,
        'directions':[{'direction_id':d['id'],'label':d['name'],'revision':d['revision'],'human_confirmed':True} for d in directions],
        'profile':{'object_type':'ResearchInterestProfile','explicit_user_source':True,'behavior_direct_write_forbidden':True,
            'direction_refs':[d['id'] for d in directions]},'policy':policy,
        'horizon_anchors':[{'object_type':'HorizonAnchor','anchor_id':'HORIZON:'+d['id'],'direction_id':d['id'],
            'description':d['query'],'independent_of_local_similarity':True,'independent_of_behavior':True,
            'human_confirmed':True} for d in directions if 'HORIZON' in d['channels']],
        'coverage_gaps':[{'gap_id':'GAP:'+d['id'],'direction_id':d['id'],'reason':d['query'],'frozen':True}
            for d in directions if 'COVERAGE_REPAIR' in d['channels']],
        'session':{'source':'DESKTOP_EXPLICIT_USER_REQUEST','long_term_profile_mutation':False,'long_term_policy_mutation':False}})
    plan=slate.seal_object({'schema_version':'p08.desktop.discovery-plan.1','is_synthetic':False,'plan_id':run_id,
        **{k:policy[k] for k in ('batch_size','channel_targets','whitespace_slots','concentration_caps')},
        'context_hash':context['content_hash'],'primary_channel_priority':list(slate.PRIMARY_CHANNEL_PRIORITY),
        'tie_break':'CANONICAL_IDENTITY_ASC','no_backfill':'CORE_SILENT_BACKFILL_FORBIDDEN',
        'history_windows_days':[30,90],'frozen_as_of':now()})
    from .recent_interest_adapter import frozen as freeze_interests,ranking as interest_ranking
    interest_snapshot=freeze_interests(runtime,config)
    facts=[];display={};comparisons=[];suppressed=[]
    for candidate in resolved['candidate_identity_projections']:
        refs=candidate['identity_evidence_refs'];record=record_by_id[refs[0]];bib=record['bibliographic'];ids=record['identifiers']
        matches={key:value for ref in refs for key,value in memberships[ref].items()}
        evidence=[observations[x]['source_evidence']['normalized_record_sha256'] for x in refs]
        key=ids.get('doi') or ids.get('arxiv_id') or ids.get('wos_uid') or record['source_record_id']
        comparison=metadata_comparison({'candidate_id':candidate['candidate_id'],'identifiers':ids,'title':bib['title']},library)
        comparisons.append({'candidate_id':candidate['candidate_id'],**comparison})
        eligible=[]
        for match in matches.values():
            direction=match['direction']
            item={'candidate_id':candidate['candidate_id'],'work_cluster_id':candidate['work_cluster_id'],
                'primary_direction_id':direction['id'],'direction_revision':str(direction['revision']),
                'manifestation_id':candidate['candidate_id']}
            outcome=runtime.feedback.eligibility(item,feedback)
            prior=(candidate['work_cluster_id'],direction['id']) in seen
            # A revoked decision explicitly restores visibility. Passive no-click is not rejection.
            relevant=[e for e in feedback['events'] if e['work_cluster_id']==candidate['work_cluster_id'] and e['direction_id']==direction['id']]
            restored=next((e['event_type'] in {'DECISION_REVOKED','MANUAL_RESTORE'} for e in reversed(relevant)
                if e['event_type'] in {'DECISION_REVOKED','MANUAL_RESTORE','EXPOSURE_RECORDED'}),False)
            if outcome['eligibility']=='ELIGIBLE' and (config['include_seen'] or not prior or restored):
                eligible.append((match,item,outcome))
            else:suppressed.append(outcome)
        if not eligible:continue
        eligible.sort(key=lambda row:(min(slate.PRIMARY_CHANNEL_PRIORITY.index(c) for c in row[0]['channels']),row[1]['primary_direction_id']))
        match,item,outcome=eligible[0];direction=match['direction']
        query_evidence=[{'channel':c,'direction_id':d[0]['direction']['id'],'query_sha256':d[0]['query_sha256'],
            'evidence_ref':evidence[0]} for d in eligible for c in d[0]['channels']]
        metadata={'source_id':observations[refs[0]]['source_connector'],'source_available':True,
            'authors':[x['display_name'] for x in bib['authors']],'institutions':[],'journal_id':None,
            'primary_direction_id':direction['id'],'direction_signals':[],'bridge_direction_refs':[],
            'horizon_anchor_refs':[],'coverage_gap_refs':[],'query_recall_evidence':query_evidence,'feedback_eligible':True,
            'evidence_completeness':int(bool(ids.get('doi')))+int(bool(bib.get('abstract')))+int(bool(bib.get('published_date'))),
            'manifestation_fitness':1,'timeliness':0,'access_posture':'METADATA_ONLY','evidence_refs':evidence}
        metadata['recent_interest']=interest_ranking(interest_snapshot,bib['title'],bib.get('abstract'))
        pub=bib.get('published_date')
        if pub:
            try:metadata['timeliness']=datetime.fromisoformat(pub.replace('Z','+00:00')).date().toordinal()
            except ValueError:pass
        fact=slate.seal_object({'candidate_id':candidate['candidate_id'],'work_cluster_id':candidate['work_cluster_id'],
            'canonical_identity':key,'state_axes':{'identity_status':candidate['identity_status']},
            'novelty_axes':{'library_new':comparison['library_new'],'first_seen_new':'TRUE'},'metadata_candidate_only':True,
            'relation_types':[r['relation_type'] for r in comparison['relations']],'relation_refs':evidence,'ranking_metadata':metadata})
        facts.append(fact)
        display[candidate['candidate_id']]={'identity_key':key,'title':bib['title'],'authors':metadata['authors'],
            'published_date':pub,'abstract':bib.get('abstract'),'identifiers':ids,
            'sources':sorted({observations[x]['source_connector'] for x in refs}),'locations':record['location_claims'],
            'direction_revision':str(direction['revision']),'direction_name':direction['name'],'manifestation_id':item['manifestation_id'],
            'recall_evidence':query_evidence,'relation_comparison':comparison,'feedback_eligibility':outcome,
            'matching_assessment':'QUERY_RECALL_ONLY_NOT_SEMANTIC_SCORE','in_current_library':comparison['library_new']=='FALSE',
            'previously_recommended':(candidate['work_cluster_id'],direction['id']) in seen}
    runtime._state(run_id,stage='COMPOSING')
    history=runtime.feedback.history([d['id'] for d in directions])
    result=slate.compose_projection(facts,context,plan,history)
    recommendations=[dict(display[x['candidate_id']],**x) for x in result['slate']['selected']]
    from .readable import radar_markdown
    markdown=radar_markdown(config['topic'] or ' / '.join(d['name'] for d in directions),recommendations,failures)
    for name,body in {'identity':resolved,'observations':{'observations':list(observations.values())},
        'recent_interest':interest_snapshot,
        'slate':result,'context':{'context':context,'plan':plan,'facts':facts,'library_sha256':sha(library),
        'feedback_sha256':sha(feedback),'comparisons':comparisons,'suppressions':suppressed}}.items():
        write(runtime._run_path(run_id)/(name+'.json'),sealed(body))
    calls=sum(bool(x.get('external_call_performed')) for x in fetched+failures)
    return {'schema_version':'DesktopRadarResult-v2','status':'PARTIAL' if failures else 'SUCCEEDED',
        'recommendations':recommendations,'source_results':fetched,'source_failures':failures,
        'channel_allocations':result['slate']['channel_allocations'],'identity_statistics':dict(resolved['statistics'],external_api_calls=calls,network_calls=calls),
        'identity_stage_statistics':resolved['statistics'],'coverage_report':result['coverage_report'],
        'history_evidence':history,'diversity_unknowns':['journal','institution'],'feedback_suppressions':suppressed,
        'slate_sha256':result['slate']['content_hash'],'markdown':markdown,'live_fetch_performed':calls>0,
        'recent_interests':{'snapshot_hash':interest_snapshot.get('snapshot_hash'),
            'eligible_event_count':len(interest_snapshot.get('events',[])),
            'ranking_consumed':any(f['ranking_metadata']['recent_interest']['score']>0 for f in facts),
            'eligible_channels':['CORE','ADJACENT'],'long_term_profile_mutated':False},
        'cache_hits':sum(x['cache_hit'] for x in fetched),'paid_model_calls':0,'fulltext_downloads':0,'formal_library_writes':0}
