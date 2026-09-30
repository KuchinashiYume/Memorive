"""Desktop adapter for Discovery identity, relation, slate and feedback modules."""
from datetime import datetime,timezone
import re
from inspect import signature,Parameter
from .common import CHANNELS,sha,now,read,write,sealed
from .directions import effective_directions,channel_targets
from .relations import metadata_comparison
from .transport import PAGE_LIMITS
from memorive_workflow.node_progress import scope

def run(runtime,run_id,params):
    from . import identity,slate
    from literature_discovery.source_connectors_literature_discovery_connector_framework import sha256_json
    config=params['config'];directions=effective_directions(config);targets=channel_targets(config,directions)
    from .acquisition import collect,resolve
    from .query_plan import assess
    from .metadata_v3 import links,date_bounds
    from .work_index import manifestation
    options=params['discovery_options']
    if options.get('direction_id'):
        directions=[d for d in directions if d['id']==options['direction_id']]
        targets=channel_targets(config,directions)
    fetched,failures,observations,record_by_id,memberships,seed_map,seed_evidence=collect(runtime,run_id,params,directions,targets)
    runtime._state(run_id,stage='RESOLVING_IDENTITIES')
    registry={oid:{'content_hash':o['content_hash'],'source_snapshot_id':o['source_snapshot_id'],
        **{k:o['source_evidence'][k] for k in ('snapshot_sha256','normalized_record_sha256')}} for oid,o in observations.items()}
    resolved=resolve(list(observations.values()),registry)
    library=runtime.work_index.coverage()
    feedback=runtime.feedback.snapshot()
    seen={(e['work_cluster_id'],e['direction_id']) for e in feedback['events'] if e['event_type']=='EXPOSURE_RECORDED'}
    caps={d:config['exposure_limits'].get(d) or config['count'] for d in slate.CAP_ORDER}
    if config['exposure_limits']['direction_share'] is not None:
        caps['direction']=min(caps['direction'],int(config['count']*config['exposure_limits']['direction_share']))
    policy={'object_type':'ResearchExposurePolicy','human_confirmed':True,'no_backfill':True,'channel_targets':targets,
        'concentration_caps':caps,'batch_size':config['count'],'whitespace_slots':0,
        'max_direction_share':config['exposure_limits']['direction_share'] or 1}
    context=slate.seal_object({'schema_version':'desktop.desktop.research-context.1','is_synthetic':False,
        'directions':[{'direction_id':d['id'],'label':d['name'],'revision':d['revision'],'human_confirmed':True} for d in directions],
        'profile':{'object_type':'ResearchInterestProfile','explicit_user_source':True,'behavior_direct_write_forbidden':True,
            'direction_refs':[d['id'] for d in directions]},'policy':policy,
        'horizon_anchors':[{'object_type':'HorizonAnchor','anchor_id':'HORIZON:'+d['id'],'direction_id':d['id'],
            'description':d['query'],'independent_of_local_similarity':True,'independent_of_behavior':True,
            'human_confirmed':True} for d in directions if 'HORIZON' in d['channels']],
        'coverage_gaps':[{'gap_id':'GAP:'+d['id'],'direction_id':d['id'],'reason':d['query'],'frozen':True}
            for d in directions if 'COVERAGE_REPAIR' in d['channels']],
        'session':{'source':'DESKTOP_EXPLICIT_USER_REQUEST','long_term_profile_mutation':False,'long_term_policy_mutation':False}})
    plan=slate.seal_object({'schema_version':'desktop.desktop.discovery-plan.1','is_synthetic':False,'plan_id':run_id,
        **{k:policy[k] for k in ('batch_size','channel_targets','whitespace_slots','concentration_caps')},
        'context_hash':context['content_hash'],'primary_channel_priority':list(slate.PRIMARY_CHANNEL_PRIORITY),
        'tie_break':'CANONICAL_IDENTITY_ASC','no_backfill':'CORE_SILENT_BACKFILL_FORBIDDEN',
        'history_windows_days':[30,90],'frozen_as_of':now()})
    from .recent_interest_adapter import frozen as freeze_interests,ranking as interest_ranking
    interest_snapshot=freeze_interests(runtime,config)
    facts=[];display={};comparisons=[];suppressed=[]
    with scope('CANDIDATE_RELATIONS', [r['candidate_id'] for r in resolved['candidate_identity_projections']], 'PROCESSED_ITEMS') as candidate_units:
        for base_candidate in resolved['candidate_identity_projections']:
            # Evaluate each actual manifestation before choosing one row per work.
            # Metadata and sources remain bound to the selected version.
            groups={}
            for ref in sorted(base_candidate['identity_evidence_refs']):
                groups.setdefault(manifestation(record_by_id[ref]),[]).append(ref)
            variant_options=[]
            for variant_refs in groups.values():
                candidate=dict(base_candidate)
                refs=variant_refs;record=max((record_by_id[r] for r in refs),key=lambda r:(bool(r['bibliographic'].get('abstract')),len(r['bibliographic'].get('authors',[])),sha(r)));bib=record['bibliographic'];ids=record['identifiers']
                matches={key:value for ref in refs for key,value in memberships[ref].items()}
                evidence=[observations[x]['source_evidence']['normalized_record_sha256'] for x in refs]
                key=ids.get('doi') or ids.get('arxiv_id') or ids.get('wos_uid') or record['source_record_id']
                candidate['original_work_cluster_id']=candidate['work_cluster_id']
                candidate['work_cluster_id']=runtime.work_index.register(dict(record,work_cluster_id=candidate['work_cluster_id'])) or candidate['work_cluster_id']
                feedback_aliases=runtime.work_index.feedback_clusters(candidate['work_cluster_id'])
                indexed=runtime.work_index.lookup(record)
                comparison={'relations':[], 'library_new':'FALSE' if indexed['has_fulltext'] else 'UNKNOWN' if indexed['possible_duplicates'] or library['unidentified_works'] or library['history'].get('status')!='COMPLETE' or library['inbox'].get('status')!='COMPLETE' or library['legacy'].get('status')!='COMPLETE' else 'TRUE',
                    'identifier_scope':'WORKSPACE_INCREMENTAL_INDEX_ALL_LIFECYCLE_STATES','unidentified_local_records':library['unidentified_works'],
                    'indexed_match':indexed,'topic_similarity':'METADATA_TERM_EVIDENCE','method_similarity':'NOT_ASSESSED',
                    'object_similarity':'NOT_ASSESSED','citation_relation':'NOT_ASSESSED'}
                comparisons.append({'candidate_id':candidate['candidate_id'],**comparison})
                eligible=[]
                relation_assessments={}
                for match in matches.values():
                    direction=match['direction']
                    relation=assess(record,direction,seed_map.get(direction['id'],[]),options)
                    relation_assessments[direction['id']]=relation
                    if not relation['supported'] or not relation['publication_eligible']:
                        suppressed.append({'candidate_id':candidate['candidate_id'],'direction_id':direction['id'],'reason':'RELATION_EVIDENCE_INSUFFICIENT' if not relation['supported'] else 'PUBLICATION_OUTSIDE_EXACT_WINDOW','assessment':relation});continue
                    item={'candidate_id':candidate['candidate_id'],'work_cluster_id':candidate['work_cluster_id'],
                        'primary_direction_id':direction['id'],'direction_revision':str(direction['revision']),
                        'manifestation_id':manifestation(record)}
                    outcome=runtime.feedback.eligibility(item,feedback,work_cluster_ids=feedback_aliases)
                    prior=(candidate['work_cluster_id'],direction['id']) in seen
                    # A revoked decision explicitly restores visibility. Passive no-click is not rejection.
                    relevant=[e for e in feedback['events'] if e['work_cluster_id'] in feedback_aliases and e['direction_id']==direction['id']]
                    restored=next((e['event_type'] in {'DECISION_REVOKED','MANUAL_RESTORE'} for e in reversed(relevant)
                        if e['event_type'] in {'DECISION_REVOKED','MANUAL_RESTORE','EXPOSURE_RECORDED'}),False)
                    relation['allow_redisplay']=bool(config['include_seen'] or restored)
                    already_delivered=any(r['direction_id']==direction['id'] and r['manifestation']==manifestation(record) for r in indexed['delivered'])
                    if outcome['eligibility']=='ELIGIBLE' and not indexed['has_fulltext'] and (config['include_seen'] or not already_delivered or restored):
                        eligible.append((match,item,outcome))
                    else:suppressed.append(dict(outcome,manifestation_id=item['manifestation_id'],already_delivered=already_delivered,has_fulltext=indexed['has_fulltext']))
                if not eligible:continue
                eligible.sort(key=lambda row:(min(slate.PRIMARY_CHANNEL_PRIORITY.index(c) for c in row[0]['channels']),row[1]['primary_direction_id']))
                match,item,outcome=eligible[0];direction=match['direction']
                query_evidence=[{'channel':c,'direction_id':d[0]['direction']['id'],'query_sha256':d[0]['query_sha256'],
                    'evidence_ref':evidence[0]} for d in eligible for c in d[0]['channels']]
                metadata={'source_id':observations[refs[0]]['source_connector'],'source_available':True,
                    'authors':[x['display_name'] for x in bib['authors']],'institutions':bib.get('institutions',[]),'journal_id':bib.get('journal'),
                    'primary_direction_id':direction['id'],'direction_signals':[],'bridge_direction_refs':[],
                    'horizon_anchor_refs':[],'coverage_gap_refs':[],'query_recall_evidence':query_evidence,'feedback_eligible':True,
                    'evidence_completeness':int(bool(ids.get('doi')))+int(bool(bib.get('abstract')))+int(bool(bib.get('published_date'))),
                    'manifestation_fitness':1,'timeliness':0,'access_posture':'METADATA_ONLY','evidence_refs':evidence}
                metadata['e6_relations']=relation_assessments
                metadata['e6_objective']=options['objective']
                metadata['e6_relation_rank']=relation_assessments[direction['id']]['ranking_value']
                metadata['source_record_verified']=candidate.get('source_record_verified',False)
                metadata['recent_interest']=interest_ranking(interest_snapshot,bib['title'],bib.get('abstract'))
                pub=bib.get('published_date')
                if pub:
                    try:metadata['timeliness']=date_bounds(pub)[0].toordinal()
                    except ValueError:pass
                fact=slate.seal_object({'candidate_id':candidate['candidate_id'],'work_cluster_id':candidate['work_cluster_id'],
                    'canonical_identity':key,'state_axes':{'identity_status':candidate['identity_status']},
                    'novelty_axes':{'library_new':comparison['library_new'],'first_seen_new':'TRUE'},'metadata_candidate_only':True,
                    'relation_types':[r['relation_type'] for r in comparison['relations']],'relation_refs':evidence,'ranking_metadata':metadata})
                display_row={'identity_key':key,'title':bib['title'],'authors':metadata['authors'],
                    'published_date':pub,'abstract':bib.get('abstract'),'identifiers':ids,
                    'journal':bib.get('journal'),'platform':bib.get('platform'),'institutions':bib.get('institutions',[]),
                    'record_type':bib.get('record_type'),'date_precision':bib.get('date_precision'),'updated_date':bib.get('updated_date'),
                    'publication_date_source':bib.get('publication_date_source'),'abstract_status':bib.get('abstract_status'),
                    'identity_evidence_level':record.get('identity_evidence_level'),'partial_fields':record.get('partial_fields',[]),
                    'source_record_id':record['source_record_id'],'links':links(ids,record['location_claims']),
                    'relation_evidence':relation_assessments[direction['id']],'recommendation_reason':relation_assessments[direction['id']]['recommendation_reason'],
                    'allow_redisplay':relation_assessments[direction['id']]['allow_redisplay'],
                    'sources':sorted({observations[x]['source_connector'] for x in refs}),'locations':record['location_claims'],
                    'direction_revision':str(direction['revision']),'direction_name':direction['name'],'manifestation_id':item['manifestation_id'],
                    'recall_evidence':query_evidence,'relation_comparison':comparison,'feedback_eligibility':outcome,
                    'matching_assessment':'METADATA_RELATION_ONLY_NOT_CALIBRATED_SEMANTIC_SCORE','in_current_library':comparison['library_new']=='FALSE',
                    'previously_recommended':any((wid,direction['id']) in seen for wid in feedback_aliases)}
                version=re.search(r"v(\d+)$",manifestation(record)) if ids.get("arxiv_id") else None
                rank=(int(version.group(1)) if version else 0,bool(bib.get("abstract")),len(bib.get("authors",[])),manifestation(record),sha(record))
                variant_options.append((rank,fact,display_row))
            if variant_options:
                _,fact,row=max(variant_options,key=lambda value:value[0])
                facts.append(fact);display[fact['candidate_id']]=row
            candidate_units.complete(base_candidate['candidate_id'])
    runtime._state(run_id,stage='COMPOSING')
    history=runtime.feedback.history([d['id'] for d in directions])
    result=slate.compose_projection(facts,context,plan,history)
    recommendations=[dict(display[x['candidate_id']],**x) for x in result['slate']['selected']]
    from .readable import discovery_markdown
    from memorive_language.discovery import reason
    for row in recommendations:
        row['recommendation_reason']=reason(row,params.get('language_context'))
        row['relation_evidence']=dict(row['relation_evidence'],recommendation_reason=row['recommendation_reason'])
    markdown=discovery_markdown(config['topic'] or ' / '.join(d['name'] for d in directions),recommendations,failures,language=params.get('language_context'))
    for name,body in {'identity':resolved,'observations':{'observations':list(observations.values())},
        'recent_interest':interest_snapshot,
        'slate':result,'context':{'context':context,'plan':plan,'facts':facts,'library_sha256':sha(library),
        'feedback_sha256':sha(feedback),'comparisons':comparisons,'suppressions':suppressed}}.items():
        write(runtime._run_path(run_id)/(name+'.json'),sealed(body))
    calls=sum(bool(x.get('external_call_performed')) for x in fetched+failures)
    return {'schema_version':'DesktopDiscoveryResult-v3','language_context':params.get('language_context'),'discovery_options':options,'seed_evidence':seed_evidence,'index_coverage':library,'status':'PARTIAL' if failures else 'SUCCEEDED',
        'recommendations':recommendations,'source_results':fetched,'source_failures':failures,
        'channel_allocations':result['slate']['channel_allocations'],'identity_statistics':dict(resolved['statistics'],external_api_calls=calls,network_calls=calls),
        'identity_stage_statistics':resolved['statistics'],'coverage_report':result['coverage_report'],
        'history_evidence':history,'diversity_unknowns':[name for name,field in [('journal','journal'),('institution','institutions')] if any(not r.get(field) for r in recommendations)],'feedback_suppressions':suppressed,
        'slate_sha256':result['slate']['content_hash'],'markdown':markdown,'live_fetch_performed':calls>0,
        'recent_interests':{'snapshot_hash':interest_snapshot.get('snapshot_hash'),
            'eligible_event_count':len(interest_snapshot.get('events',[])),
            'ranking_consumed':any(f['ranking_metadata']['recent_interest']['score']>0 for f in facts),
            'eligible_channels':['CORE','ADJACENT'],'long_term_profile_mutated':False},
        'cache_hits':sum(x['cache_hit'] for x in fetched),'paid_model_calls':0,'fulltext_downloads':0,'formal_library_writes':0}
