"""Neutral completed-analysis evidence. Never mines historical conversations."""
from pathlib import Path
from threading import RLock
from .common import sha,now,read,write,sealed
from research_recommendations import recommendation_history_research_recommendations_exposure_feedback as discovery
_LOCK=RLock()

def record_analysis(root,config,context,analysis):
    if not config.get('use_recent_interests'):return {'status':'DISABLED_BY_USER','events_written':0}
    # This is the automatic PDF workflow, not an unsolicited user question.
    # It is observable but cannot masquerade as a positive interest signal.
    events=[]
    for claim in analysis.literature_support:
        source=claim.source_id or {}
        event={'schema_version':'discovery.verification.research-interaction-evidence.0.1','object_type':'ResearchInteractionEvidence',
            'query_hash':sha(analysis.question).upper(),'session_hash':sha(context.job_id).upper(),
            'event_time':now(),'field_dimension':source.get('field') or 'unmapped','cited_work_id':source.get('paper_id'),
            'completion_state':'COMPLETED','origin':'SYSTEM_CLARIFICATION','exposure_id':None}
        discovery.validate_research_interaction_event(event)
        identity=sha({k:v for k,v in event.items() if k!='event_time'})
        events.append({'event_id':identity,'event':event,'producer':'RESEARCH_ANALYSIS_COMPLETED_AUTOMATIC_DOCUMENT_ANALYSIS',
            'source_hash':context.source_sha256,'direction_mapping':'UNMAPPED','ranking_eligible':False})
    path=Path(root)/'analysis_interactions.json'
    with _LOCK:
        state=read(path) if path.exists() else {'events':[]}
        known={r['event_id'] for r in state['events']};added=[e for e in events if e['event_id'] not in known]
        unique={r['event_id']:r for r in added}
        state['events'].extend(unique.values())
        if unique:write(path,sealed(state))
    return {'status':'OBSERVATIONAL_ONLY','events_written':len(unique),'raw_query_persisted':False,'raw_session_read':False,
        'automatic_analysis_is_not_user_interest':True}

def current_digest(runtime):
    config=runtime.api._service.call('settings.research_get',{})['config']
    if not config['use_recent_interests']:
        return {'status':'DISABLED_BY_USER','display_status':'近期互动记录已关闭','ranking_consumed':False,'events':[]}
    path=runtime.root/'analysis_interactions.json'
    events=read(path)['events'] if path.exists() else []
    from .recent_interest_adapter import frozen
    interests=frozen(runtime,config)
    return {'status':'EXPLICIT_QUERY_PRODUCER_READY','display_status':f'近期兴趣已开启 · {len(interests["events"])} 条有效提问',
        'ranking_consumed':False,'ranking_consumption_scope':'PER_DISCOVERY_RUN_RECEIPT',
        'events':events,'explicit_queries':interests,'raw_query_persisted':False,'historical_conversation_mining':False,
        'eligible_channels':['CORE','ADJACENT'],'long_term_profile_mutated':False}
