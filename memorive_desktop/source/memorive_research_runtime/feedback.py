"""Single-writer desktop persistence around the unchanged RECOMMENDATION-HISTORY event engine."""
from pathlib import Path
from threading import RLock
from research_recommendations import recommendation_history_research_recommendations_exposure_feedback as engine
from .common import read, write, sealed, sha, now

class FeedbackStore:
    def __init__(self,root):
        self.path=Path(root)/'feedback.json';self.lock=RLock()

    def snapshot(self):
        with self.lock:
            return read(self.path) if self.path.exists() else {'events':[],'requests':{},'created_at':None}

    def _append(self,key,binding,factory):
        if not isinstance(key,str) or not key:raise ValueError('RESEARCH_EVENT_REQUEST_REQUIRED')
        with self.lock:
            state=self.snapshot();old=state['requests'].get(key)
            if old:
                if old['binding']!=binding:raise ValueError('RESEARCH_EVENT_IDEMPOTENCY_CONFLICT')
                return next(e for e in state['events'] if e['event_id']==old['event_id'])
            event=factory(state)
            accepted=state['events']+[event]
            engine.build_exposure_ledger(accepted)
            state['events']=accepted;state['requests'][key]={'binding':binding,'event_id':event['event_id']}
            state['created_at']=state['created_at'] or event['event_time']
            write(self.path,sealed(state));return event

    def expose(self,run_id,slate_hash,item,request_id):
        binding=sha({'run':run_id,'slate':slate_hash,'item':item})
        def make(state):
            stamp=now();identity={'run_id':run_id,'candidate_id':item['candidate_id'],'direction_id':item['primary_direction_id'],'request_id':request_id}
            return engine.seal_object({'schema_version':'discovery.verification.exposure-event.0.1','object_type':'ExposureLedgerEvent',
                'event_type':'EXPOSURE_RECORDED','event_id':engine.stable_id('EVENT:',identity),
                'exposure_id':engine.stable_id('EXPOSURE:',identity),'event_time':stamp,'slate_id':run_id,
                'slate_revision_or_hash':slate_hash.upper(),'position':item['position'],'candidate_id':item['candidate_id'],
                'work_cluster_id':item['work_cluster_id'],'channel':item['channel'],'direction_id':item['primary_direction_id'],
                'direction_revision':str(item['direction_revision']),
                'source_contract_hash':sha({'contract':'DesktopActualExposure-v1','source':'VISIBLE_CANDIDATE_OR_EXPLICIT_ACTION'}).upper(),
                'evidence_refs':sorted(set(item['evidence_refs']))})
        return self._append(request_id,binding,make)

    def decide(self,exposure_event_id,decision_type,request_id,**options):
        def make(state):
            exposure=next((e for e in state['events'] if e['event_id']==exposure_event_id and e['event_type']=='EXPOSURE_RECORDED'),None)
            if exposure is None:raise ValueError('RESEARCH_EXPOSURE_NOT_FOUND')
            if decision_type=='GLOBALLY_USELESS' and options.get('global_confirmation') is not True:raise ValueError('RESEARCH_GLOBAL_CONFIRMATION_REQUIRED')
            return engine.make_feedback_event(exposure,decision_type,len(state['events'])+1,now(),**options)
        return self._append(request_id,sha({'exposure':exposure_event_id,'decision':decision_type,'options':options}),make)

    def revoke(self,decision_event_id,request_id):
        def make(state):
            decision=next((e for e in state['events'] if e['event_id']==decision_event_id and e['event_type']=='DECISION_RECORDED'),None)
            if decision is None:raise ValueError('RESEARCH_DECISION_NOT_FOUND')
            exposure=next(e for e in state['events'] if e['event_id']==decision['source_exposure_event_id'])
            return engine.make_control_event(exposure,'DECISION_REVOKED',len(state['events'])+1,now(),target_event_id=decision_event_id)
        return self._append(request_id,sha({'revoke':decision_event_id}),make)

    def eligibility(self,item,state=None,as_of=None):
        state=state or self.snapshot();stamp=as_of or now();ledger=engine.build_exposure_ledger(state['events'])
        return engine.evaluate_candidate({'candidate_id':item['candidate_id'],'work_cluster_id':item['work_cluster_id'],
            'direction_id':item['primary_direction_id'],'direction_revision':str(item['direction_revision']),
            'manifestation_id':item['manifestation_id']},ledger,engine.derive_scoped_suppressions(ledger,stamp),stamp)

    def history(self,direction_ids):
        state=self.snapshot();events=[{'event_id':e['event_id'],'occurred_at':e['event_time'],
            'direction_id':e['direction_id'],'channel':e['channel']} for e in state['events']
            if e['event_type']=='EXPOSURE_RECORDED' and e['direction_id'] in direction_ids]
        from datetime import datetime,timezone
        started=datetime.fromisoformat(state['created_at']) if state['created_at'] else None
        complete=[days for days in (30,90) if started and (datetime.now(timezone.utc)-started).total_seconds()>=days*86400]
        return {'history_id':'history-'+sha(events)[:24],'complete_windows_days':complete,
            'events':events,'observation_started_at':state['created_at'],'evidence_scope':'ACTUAL_VISIBLE_CANDIDATES'}
