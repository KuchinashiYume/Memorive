"""Local research chat, project memory and shared evidence service.

The model is optional. Evidence-only mode returns quotations explicitly; it
never masquerades as a generated answer or launches an external agent.
"""
import copy,json,re,threading,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from .store import Store,digest,uid,now,packed
from .evidence import EvidenceIndex,material_layer,source_document_hash
from .attachments import Attachments,METHODS as ATTACHMENT_METHODS
from .conversations import Conversations,METHODS as CONVERSATION_METHODS
from .shared_cross_connections import Connections,DESKTOP as CONNECTION_METHODS
from .answer_evidence import AnswerEvidence,METHODS as ANSWER_EVIDENCE_METHODS
from . import answer_coverage
from . import answer_versions
from memorive_language import freeze as freeze_language, locale as output_locale
from memorive_language.text import choose

PUBLIC_METHODS=frozenset({'memo.capabilities','memo.search','memo.read_evidence','memo.prepare_tension',
    'memo.propose_feedback','memo.submit_draft','memo.job_status'})
DESKTOP_METHODS=frozenset({'memo.developer_state','memo.developer_save','memo.automation_save','memo.developer_config'})|PUBLIC_METHODS|frozenset({'memo.state','memo.settings_save','memo.project_save','memo.index_refresh',
    'memo.thread_create','memo.thread_get','memo.thread_forget','memo.ask','memo.cancel','memo.memory_save','memo.memory_forget','memo.topic_change',
    'memo.agent_dispatch','memo.knowledge_health','memo.feedback_revise','memo.interests','memo.interest_remove','memo.weight_settings','memo.weight_save','memo.weight_preview','memo.material_metadata','memo.material_metadata_save','memo.feedback_review','memo.feedback_retract','memo.handoff','memo.export_handoff','memo.library_list','memo.library_add'})|CONNECTION_METHODS|CONVERSATION_METHODS|ATTACHMENT_METHODS|ANSWER_EVIDENCE_METHODS

def agent_matches(option,agent):
    adapter=option.get('adapter_id','')
    known={'codex':{'codex_cli'},'claude-code':{'claude_code','claude_code_cli'}}
    if agent.startswith('custom-'):return option.get('profile_kind')=='CLI'
    return adapter in known[agent] if agent in known else adapter not in known['codex']|known['claude-code']


DESKTOP_METHODS=DESKTOP_METHODS|answer_versions.METHODS

class ResearchWorkspace:
    def __init__(self,root,*,model=None,catalog=None,recover=False,interest_get=None,interest_set=None,vision=None,user_preferences_get=None,language_settings_get=None,lifecycle_clock=None):
        self.store=Store(root,lifecycle_clock=lifecycle_clock);self.index=EvidenceIndex(self.store);self.model=model;self.catalog=catalog or (lambda:[]);self.embedding_catalog=lambda:[]
        self.interest_get=interest_get;self.interest_set=interest_set;self.vision=vision;self.agent_model=None
        from knowledge_feedback.research_ingest import ResearchFeedback
        self.feedback=ResearchFeedback(self.store,self.index)
        from .topic import Topic
        self.topic=Topic(self)
        from .handoffs import Handoffs
        self.handoffs=Handoffs(self)
        self.feedback.handoff_validate=self.handoffs.validate_review
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='memo-research');self.closed=False
        self.lock=threading.RLock();self.futures={};self.owns_desktop_state=recover;self.temporary_threads=set()
        from retrieval_weighting.research_policy import ResearchPolicy
        self.weights=ResearchPolicy(self.store)
        self.attachments=Attachments(self);self.conversations=Conversations(self);self.connections=Connections(self);self.connector_stop=threading.Event();self.connector_thread=None
        self.answer_evidence=AnswerEvidence(self)
        self.versions=answer_versions.AnswerVersions(self)
        self.language_settings_get=language_settings_get or (lambda:dict(revision=0,settings=dict(preferences=dict(language='zh-CN'))))
        self.user_preferences_get=user_preferences_get or (lambda:dict(revision=0,config=dict(answer_style='professional')))
        # Legacy answer trees are normalized on individual reads and persisted
        # with their next actual content edit, never by a startup body scan.
        self._developer=None
        if recover:
            with self.store.tx() as db:
                for job in self.store.recoverable_jobs('attachment_job',db):
                    if job['status'] in {'QUEUED','RUNNING'}:
                        for item in job['items']:
                            if item['status']=='QUEUED':
                                item.update(status='ERROR',error='ATTACHMENT_INTERRUPTED')
                                a=self.store.get('artifact',item['id'],db=db)
                                if a and a['state']=='preparing':self.store.put('artifact',a['id'],a['project'],dict(a,state='error',error='ATTACHMENT_INTERRUPTED'),db=db)
                        self.store.put('attachment_job',job['id'],job['project'],dict(job,status='PARTIAL'),db=db)
                for job in self.store.recoverable_jobs('job',db):
                    if job['status'] in {'QUEUED','RUNNING'}:
                        self.store.put('job',job['id'],job['project'],dict(job,status='INTERRUPTED',error='Restarted; retry explicitly'),db=db)
            # A crash cannot run close(). Temporary branches and drafts from
            # that process must not reappear in the next desktop session.
            with self.store.read() as db:
                temporary=[(r['id'],r['source_revision']) for r in db.execute('SELECT id,source_revision FROM conversation_catalog WHERE temporary=1')]
            for identity,revision in temporary:self.thread_forget(identity,revision)

    @property
    def developer(self):
        # Opening a conversation does not need the separate developer console
        # schemas. Initialize that API at its first actual consumer, under the
        # workspace lock so concurrent external callers share one instance.
        with self.lock:
            if self._developer is None:
                from .developer_api import DeveloperAPI
                self._developer=DeveloperAPI(self)
            return self._developer

    def settings(self):
        value=self.store.get('settings','default')
        if self.interest_get is not None:value['use_recent_interests']=self.interest_get()
        return value

    def state(self,project='default',view_thread_id=None):
        if not self.store.get('project',project):raise ValueError('PROJECT_NOT_FOUND')
        if view_thread_id is not None and not isinstance(view_thread_id,str):raise ValueError('THREAD_VIEW_INVALID')
        # The desktop navigation has its own metadata index. Refreshing the
        # open conversation must not transfer every other conversation body
        # or completed job's retained context on each update. Omitted scope
        # preserves the complete state response for existing callers.
        if view_thread_id is None:
            threads=self.store.list('thread',project)
            jobs=self.store.list('job',project)[:40]
        else:
            selected=self.store.get('thread',view_thread_id) if view_thread_id else None
            threads=[selected] if selected and selected['project']==project else []
            jobs=self.store.active_jobs(view_thread_id,project) if threads else []
        if self.owns_desktop_state:threads=[t for t in threads if not t['temporary'] or t['id'] in self.temporary_threads]
        return {'settings':self.settings(),'projects':self.store.list('project'),'threads':[answer_versions.project(t) for t in threads],
            'memories':[r for r in self.store.list('memory') if r['active'] and (r['scope']=='user' or r['project']==project)],
            'artifacts':[dict(a,material_layer=material_layer(a),source_content_hash=source_document_hash(a)) for a in self.store.list('artifact',project)],'feedback':self.store.list('feedback',project),
            'jobs':jobs,'model_options':self.catalog(),'embedding_options':self.embedding_catalog(),'research_topic':self.topic.get(project)}

    def settings_save(self,config,expected_revision,thread_id=None,thread_revision=None):
        allowed={'profile_ref','memory_enabled','use_recent_interests','context_chars','recent_messages','agent','agent_profile_ref','default_project','custom_agents','desktop_agents'}
        if not isinstance(config,dict) or set(config)-allowed:raise ValueError('SETTINGS_FIELDS_INVALID')
        current=self.settings();value={**current,**config}
        if type(value['memory_enabled']) is not bool or type(value['use_recent_interests']) is not bool:raise ValueError('SETTINGS_INVALID')
        if type(value['context_chars']) is not int or not 8000<=value['context_chars']<=64000:raise ValueError('CONTEXT_BUDGET_INVALID')
        if type(value['recent_messages']) is not int or not 2<=value['recent_messages']<=24:raise ValueError('RECENT_MESSAGES_INVALID')
        validate_agent_settings(value)
        agent_ref=value.get('agent_profile_ref')
        if not desktop_agent(value) and agent_ref and not any(o['profile_ref']==agent_ref and o['eligible'] and o['profile_kind']=='CLI' and agent_matches(o,value['agent']) for o in self.catalog()):raise ValueError('AGENT_MODEL_UNAVAILABLE')
        ref=value['profile_ref']
        if ref is not None and not any(o['profile_ref']==ref and o['eligible'] and o['profile_kind'] in {'API','LOCAL','CLI'} for o in self.catalog()):raise ValueError('CHAT_MODEL_UNAVAILABLE')
        with self.store.tx() as db:
            if self.store.get('settings','default',db=db)['revision']!=expected_revision:raise ValueError('REVISION_CONFLICT')
            thread=None
            if thread_id is not None:
                if 'profile_ref' not in config:raise ValueError('THREAD_CONFIG_INVALID')
                thread=self.conversations._get(thread_id,thread_revision,db)
                if thread.get('archived'):raise ValueError('THREAD_ARCHIVED')
            if self.interest_set is not None and value['use_recent_interests']!=current['use_recent_interests']:
                self.interest_set(value['use_recent_interests'])
            if thread is not None:
                self.store.put('thread',thread_id,thread['project'],dict(thread,profile_ref=ref),expected=thread_revision,db=db)
            return self.store.put('settings','default','',value,expected=expected_revision,db=db)

    def project_save(self,name,vault=None,project=None,expected_revision=0):
        if not isinstance(name,str) or not name.strip() or len(name)>160:raise ValueError('PROJECT_NAME_INVALID')
        identity=project or uid('project_')
        old=self.store.get('project',identity)
        if vault:
            p=Path(vault).resolve(strict=True)
            if not p.is_dir() or p.parent==p:raise ValueError('VAULT_INVALID')
            vault=str(p)
        value={'name':name.strip(),'vault':vault,'index_epoch':(old or {}).get('index_epoch',0)}
        with self.store.tx() as db:
            result=self.store.put('project',identity,identity,value,expected=expected_revision,db=db)
            if old and old.get('vault')!=vault:
                for a in self.store.list('artifact',identity,db=db):
                    if a['kind']=='source':self.store.put('artifact',a['id'],identity,dict(a,state='unavailable'),db=db)
            return result

    def thread_create(self,project='default',title='',temporary=False):
        if not self.store.get('project',project):raise ValueError('PROJECT_NOT_FOUND')
        if type(temporary) is not bool:raise ValueError('THREAD_INVALID')
        result=self.store.put('thread',uid('chat_'),project,{'project':project,'title':str(title)[:120] or choose(freeze_language(self.language_settings_get()),'新对话','New conversation','新しい会話'),'temporary':temporary,
            'created_at':now(),'messages':[],'summary':[],'summary_ids':[],'artifact_ids':[],'scope':'conversation','include_project':False,'excluded_artifact_ids':[]})
        if temporary:self.temporary_threads.add(result['id'])
        return answer_versions.project(result)

    def thread_get(self,thread_id):
        row=self.store.get('thread',thread_id)
        if not row:raise ValueError('THREAD_NOT_FOUND')
        if row['temporary'] and self.owns_desktop_state and thread_id not in self.temporary_threads:
            self.thread_forget(thread_id,row['revision']);raise ValueError('THREAD_NOT_FOUND')
        return answer_versions.project(row)

    def thread_forget(self,thread_id,expected_revision):
        with self.store.tx() as db:
            row=self.store.get('thread',thread_id,db=db)
            if not row or row['revision']!=expected_revision:raise ValueError('REVISION_CONFLICT')
            self.conversations._get(thread_id,expected_revision,db)
            for job in self.store.list('job',row['project'],db=db):
                if job.get('thread_id')==thread_id:
                    db.execute("DELETE FROM objects WHERE kind='job' AND id=?",(job['id'],))
            for a in self.store.list('artifact',row['project'],db=db):
                if a.get('temporary_thread')==thread_id:
                    db.execute('DELETE FROM chunks WHERE artifact_id=?',(a['id'],))
                    self.store.put('artifact',a['id'],row['project'],dict(a,state='unavailable'),db=db)
            db.execute("DELETE FROM objects WHERE kind='thread' AND id=?",(thread_id,))
            self.topic.forget_thread(thread_id,row['project'],db)
            for kind in ('answer_history','answer_review','coverage_probe','answer_feedback','answer_draft','handoff','desktop_handoff','agent_return'):
                for record in self.store.list(kind,row['project'],db=db):
                    if record.get('thread_id')==thread_id:db.execute('DELETE FROM objects WHERE kind=? AND id=?',(kind,record['id']))
            self.store.event('THREAD_FORGOTTEN',thread_id,{},db)
        return {'status':'FORGOTTEN','removed_from_future_context':True}

    def memory_save(self,text,scope='project',project='default',memory_id=None,expected_revision=0):
        if scope not in {'user','project'} or not isinstance(text,str) or not text.strip() or len(text)>4000:raise ValueError('MEMORY_INVALID')
        if not self.store.get('project',project):raise ValueError('PROJECT_NOT_FOUND')
        return self.store.put('memory',memory_id or uid('mem_'),project,{'text':text.strip(),'scope':scope,'project':project,
            'active':True,'confirmed_by':'user','kind':'preference_or_project_context','created_at':now()},expected=expected_revision)

    def memory_forget(self,memory_id,expected_revision):
        with self.store.tx() as db:
            m=self.store.get('memory',memory_id,db=db)
            if not m or m['revision']!=expected_revision:raise ValueError('REVISION_CONFLICT')
            # Tombstones contain no previous content; worker context is rechecked before publication.
            self.store.put('memory',memory_id,m['project'],dict(m,text='',active=False),expected=expected_revision,db=db)
            self.store.event('MEMORY_FORGOTTEN',memory_id,{},db)
        return {'status':'FORGOTTEN'}

    def interests(self,project):
        from retrieval_weighting.research_interests import snapshot
        config=self.weights.settings(project)['config']
        value=snapshot(self.store,enabled=self.settings()['use_recent_interests'],project=project,window_days=config['interest_days'],half_life=config['interest_half_life'])
        items=[]
        with self.store.tx() as db:
            for event in value['events']:
                thread=self.store.get('thread',event['thread_id'],db=db)
                text=next((m['text'] for m in (thread or {}).get('messages',[]) if m['role']=='user' and digest(m['text'])==event['query_hash']),'')
                if text:items.append({'id':str(event['event_id']),'label':text[:90],'decay':event['decay']})
        return {'items':items,'enabled':value['enabled']}

    def interest_remove(self,project,identity):
        with self.store.tx() as db:
            row=db.execute("SELECT body FROM events WHERE seq=? AND event='EXPLICIT_RESEARCH_INTEREST'",(identity,)).fetchone()
            if not row or json.loads(row['body'])['project']!=project:raise ValueError('INTEREST_NOT_FOUND')
            self.store.put('interest_exclusion',str(identity),project,{'project':project},db=db)
        return {'status':'REMOVED'}

    def knowledge_health(self,project):
        from .knowledge_health import health
        return health(self,project)

    def feedback_revise(self,identity,expected_revision,changes,evidence_ids=None):
        from .knowledge_health import revise
        return revise(self,identity,expected_revision,changes,evidence_ids)

    def agent_dispatch(self,thread_id,question,request_id,profile_ref=None):
        settings=self.settings();ref=profile_ref or settings.get('agent_profile_ref')
        options=[o for o in self.catalog() if o['profile_kind']=='CLI' and o['eligible']]
        if not ref:
            aliases={'codex':{'codex_cli'},'claude-code':{'claude_code','claude_code_cli'}}.get(settings['agent'],set())
            ref=next((o['profile_ref'] for o in options if o.get('adapter_id') in aliases),None)
        if not ref or not any(o['profile_ref']==ref and agent_matches(o,settings['agent']) for o in options):raise ValueError('AGENT_MODEL_UNAVAILABLE')
        if not self.agent_model:raise ValueError('AGENT_RUNNER_UNAVAILABLE')
        return self.ask(thread_id,question,request_id,agent_profile_ref=ref)

    def weight_preview(self,project,query,config):
        from retrieval_weighting.research_policy import validate
        proposed=validate(config)
        # Preview never invokes a model or changes the saved policy.
        from .evidence import EvidenceIndex
        index=EvidenceIndex(self.store);index.profile_identity=self.index.profile_identity
        before=index.search(query,project,limit=8,record=False)
        after=index.search(query,project,limit=8,policy_config=proposed,record=False)
        return {'before':before,'after':after,'saved':False}

    def prepare_tension(self,project,evidence_ids):
        if not isinstance(evidence_ids,list) or not 2<=len(set(evidence_ids))<=12:raise ValueError('TENSION_NEEDS_TWO_SOURCES')
        refs=[self.index.read(i,project) for i in evidence_ids]
        if len({answer_coverage.document_key(r) for r in refs})<2:raise ValueError('TENSION_NEEDS_TWO_PAPERS')
        from research_opportunities.core import CONCEPT_ORDER
        return {'schema_version':'DesktopAgentTensionPack-v1','project':project,'evidence':refs,
            'comparison_facets':list(CONCEPT_ORDER),'classification':'NOT_ASSESSED',
            'instructions':'Compare each facet with quoted evidence. Distinguish incomparable conditions from conflicting findings. Return a proposed interpretation with limitations; never declare novelty from missing search results.',
            'source_snapshot_hash':digest([{k:r[k] for k in ('id','artifact_id','content_hash','chunk_hash')} for r in refs])}

    def _context(self,thread,question,refs,settings,coverage_plan=None,topic_snapshot=None,*,request_context=None):
        memories=[] if thread['temporary'] or not settings['memory_enabled'] else [m for m in self.store.list('memory')
            if m['active'] and (m['scope']=='user' or m['project']==thread['project'])]
        if '_memory_snapshot' in settings:memories=settings['_memory_snapshot']
        selected=(coverage_plan or {}).get('scope_ids',self.attachments.scope_ids(thread))
        topic_snapshot=topic_snapshot or self.topic.snapshot(thread,selected)
        messages=[];withheld_history=[]
        for message in thread['messages']:
            if message['id'] in topic_snapshot.get('withheld_message_ids',[]):withheld_history.append(message['id']);continue
            try:
                for citation in message.get('citations',[]):
                    if selected is not None and citation['artifact_id'] not in selected:raise ValueError('HISTORY_OUTSIDE_SCOPE')
                    self.index.read(citation['id'],thread['project'],expected_hash=citation['content_hash'])
            except (ValueError,OSError):withheld_history.append(message['id']);continue
            messages.append(message)
        keep=settings['recent_messages']
        older=messages[:-keep] if len(messages)>keep else []
        summary=[{'message_id':m['id'],'role':m['role'],'excerpt':m['text'][:300]} for m in older[-16:]]
        payload={'_thread_scope':digest(thread['id']),'_temporary':thread['temporary'],'confirmed_memory':[{'id':m['id'],'revision':m['revision'],'text':m['text']} for m in memories[:12]],
            'extractive_summary':summary,'recent_messages':[{'message_id':m['id'],'role':m['role'],'text':m['text']} for m in messages[-keep:]],
            'evidence':[{**{k:r.get(k) for k in ('id','artifact_id','document_id','title','text','content_hash','page','line_start','line_end')},
                         **{k:r[k] for k in ('material_layer','source_content_hash','knowledge_review','structure_refs') if k in r}} for r in refs],'question':question,
            'withheld_history_ids':withheld_history}
        # Count the whole request envelope before topic allocation and optional trimming.
        payload.update(copy.deepcopy(request_context or {}))
        budget=settings['context_chars']
        if coverage_plan is not None:
            payload['source_scope']={'artifact_ids':coverage_plan['scope_ids'],'facets':[f['label'] for f in coverage_plan['facets']],
                'notice':'Only retained excerpts below were supplied. Missing excerpts or Card fields are not absence in the source. Facet sufficiency remains unassessed.'}
            if coverage_plan.get('unavailable_sources'):
                payload['source_scope']['unavailable_sources']=coverage_plan['unavailable_sources']
                payload['source_scope']['notice']+=' Excluded knowledge has unavailable source versions; its saved review state is unchanged. Do not treat it as current evidence or infer scientific rejection.'
        # The total budget is unchanged. User quotations share its actual
        # remaining capacity rather than failing at an arbitrary one-third
        # partition. Optional model notes still have a conservative soft limit.
        structural=dict(payload,evidence=[],confirmed_memory=[],extractive_summary=[],recent_messages=[],research_topic={})
        if coverage_plan is not None:
            structural['source_scope']=dict(payload['source_scope'],retained_artifact_ids=[],missing_artifact_ids=coverage_plan['scope_ids'])
        topic_budget=max(0,budget-len(packed(structural))+2)
        payload['research_topic']=self.topic.context(topic_snapshot,topic_budget,optional_limit=budget//3)
        def size():
            if coverage_plan is not None:
                retained=list(dict.fromkeys(r['artifact_id'] for r in payload['evidence']))
                payload['source_scope'].update(retained_artifact_ids=retained,
                    missing_artifact_ids=[a for a in coverage_plan['scope_ids'] if a not in retained] if coverage_plan['scope_ids'] is not None else None)
            return len(packed(payload))
        # Remove low-priority historic context first; never slice the current question or invent a summary.
        while size()>budget and payload['recent_messages']:payload['recent_messages'].pop(0)
        while size()>budget and payload['extractive_summary']:payload['extractive_summary'].pop(0)
        while size()>budget and payload['confirmed_memory']:payload['confirmed_memory'].pop()
        if size()>budget and payload['evidence']:
            from .context_selection import select
            by_id={r['id']:r for r in payload['evidence']}
            def fits(selected_refs):
                payload['evidence']=[by_id[r['id']] for r in selected_refs]
                return size()<=budget
            selected_refs=select(refs,question,fits)
            payload['evidence']=[by_id[r['id']] for r in selected_refs]
        if size()>budget:raise ValueError('QUESTION_EXCEEDS_CONTEXT_BUDGET')
        return payload,summary

    def ask(self,thread_id,question,request_id,artifact_ids=None,agent_profile_ref=None,path_token=None,output_language=None):
        return self.versions.enqueue(thread_id,question,request_id,artifact_ids,agent_profile_ref,path_token_expected=path_token,output_language=output_language)

    def _answer(self,identity,question,artifact_ids):
        started=time.monotonic();usage={};search={}
        try:
            from .answer_conversation import run_if_requested
            initial=self.job_status(identity)
            if not initial.get('retry_of') and run_if_requested(self,identity,question):return
            from memorive_settings.task_scheduling import TASK_CATEGORIES
            def checkpoint():
                row = self.store.get('job', identity)
                if self.closed or not row or row.get('cancel_requested'):
                    raise ValueError('RESEARCH_CANCELLED')
            with TASK_CATEGORIES.enter('RESEARCH_CHAT', checkpoint=checkpoint):
                job=self.store.get('job',identity)
                if not job:return
                with self.store.tx() as db:
                    job=self.store.get('job',identity,db=db)
                    if self.closed or not job or job['status'] not in {'QUEUED','RUNNING'} or job.get('cancel_requested'):return
                    job=self.store.put('job',identity,job['project'],dict(job,status='RUNNING'),db=db)
                thread=self.versions.generation_thread(job);settings=dict(job.get('model_settings') or self.settings())
                request_context={'language_context':copy.deepcopy(job.get('language_context') or freeze_language()),'answer_style':copy.deepcopy(job['answer_style']),'_thread_scope':digest(thread['id']),
                    '_answer_path':{'ids':[m['id'] for m in job.get('parent_path',[])],'hash':job.get('parent_path_hash')}}
                if 'memory_snapshot' in job:settings['_memory_snapshot']=job['memory_snapshot']
                if job.get('retry_snapshot'):
                    prior=job['retry_snapshot'];context=copy.deepcopy(prior['context']);summary=copy.deepcopy(prior['summary'])
                    refs=copy.deepcopy(prior['refs']);search=copy.deepcopy(prior['search']);intent=prior['intent']
                    coverage_snapshot=copy.deepcopy(prior['coverage_snapshot']);evidence_context=copy.deepcopy(prior['evidence_context'])
                    admitted={r['id'] for r in refs}
                    self.versions.validate_retry_topic(job,prior,thread,context)
                    context.update(request_context,task_intent=intent)
                else:
                    coverage_plan=answer_coverage.plan(question,artifact_ids)
                    unavailable=self.index.unavailable_derived(thread['project'],artifact_ids)
                    if unavailable:coverage_plan['unavailable_sources']=[dict(artifact_id=aid,**value) for aid,value in sorted(unavailable.items())]
                    coverage_plan['project']=thread['project'];coverage_plan['hash']=digest({k:v for k,v in coverage_plan.items() if k!='hash'})
                    search=self.index.search(question,thread['project'],limit=8,artifact_ids=artifact_ids,policy_config=(job.get('weight_policy') or {}).get('config'));refs=search['results']
                    refs=answer_coverage.local_candidates(self.index,question,artifact_ids,refs,coverage_plan)
                    retrieved_refs=list(refs)
                    # Short follow-ups may omit the original topic. Reuse only fresh citations
                    # from this thread, within any explicit document selection.
                    if not refs:
                        previous=next((m for m in reversed(thread['messages']) if m['role']=='assistant' and m.get('citations')),None)
                        for citation in (previous or {}).get('citations',[])[:8]:
                            if artifact_ids is not None and citation['artifact_id'] not in artifact_ids:continue
                            try:
                                ref=self.index.read(citation['id'],thread['project'],expected_hash=citation['content_hash'])
                                refs.append(dict(ref,selection='previous_turn'))
                            except (ValueError,OSError):continue
                    # Explicit files stay usable for Chinese questions about English PDFs.
                    # Balance the evidence across selected documents before adding ranked hits.
                    read_refs=list(refs)
                    if artifact_ids:
                        balanced=[];per_document=[]
                        with self.store.tx() as db:
                            for aid in artifact_ids:
                                if aid in unavailable:continue
                                matches=[r for r in refs if r['artifact_id']==aid]
                                # Explicit selection must cover body pages even when a
                                # Chinese query has few lexical matches in an English PDF.
                                rows=db.execute('SELECT id,page FROM chunks WHERE artifact_id=? ORDER BY page,line_start,id',(aid,)).fetchall()
                                chosen=matches[:2];seen={r['id'] for r in chosen};pages={r.get('page') for r in chosen}
                                for row in rows:
                                    if row['id'] not in seen and row['page'] not in pages:
                                        chosen.append(dict(self.index.read(row['id'],thread['project'],db=db),selection='selected_document_page'))
                                        seen.add(row['id']);pages.add(row['page'])
                                for row in rows:
                                    if row['id'] not in seen:
                                        chosen.append(dict(self.index.read(row['id'],thread['project'],db=db),selection='selected_document_context'))
                                        seen.add(row['id'])
                                per_document.append(chosen)
                        # Round-robin order preserves both papers if the context budget
                        # later removes its tail. Never reuse another paper's identity.
                        for index in range(max((len(rows) for rows in per_document),default=0)):
                            for rows in per_document:
                                if index<len(rows):balanced.append(rows[index])
                        read_refs=balanced
                        # Already-read, authorized excerpts stay eligible until
                        # the context's actual character budget has been applied.
                        refs=answer_coverage.pack_order(balanced,question)
                    if unavailable and not refs:
                        coverage=answer_coverage.snapshot(self.store,thread['project'],coverage_plan,retrieved_refs,read_refs,refs)
                        self.store.put('job',identity,job['project'],dict(self.job_status(identity),evidence_coverage=coverage))
                        raise ValueError('EVIDENCE_NO_CURRENT_SOURCE')
                    comparison=bool(re.search(r'比较|对比|异同|差异|张力|矛盾|冲突|compare|comparison|differ|tension|conflict',question,re.I))
                    # Comparing methods, results or passages inside one selected
                    # paper is a valid single-document follow-up.  Require two
                    # papers only when the wording or selection explicitly asks
                    # for a cross-document comparison.
                    cross_document=bool(re.search(r'跨篇|跨文献|不同文献|两篇|多篇|这些研究|这些文献|这些论文|between\s+(?:the\s+)?(?:papers|documents|studies)|across\s+(?:papers|documents|studies)',question,re.I))
                    intent='comparison' if comparison else ('knowledge' if re.search(r'回流|保存.*知识|整理.*结论|save.*knowledge',question,re.I) else 'question')
                    if cross_document and len({answer_coverage.document_key(r) for r in refs})<2:raise ValueError('TENSION_NEEDS_TWO_PAPERS')
                    context,summary=self._context(thread,question,refs,settings,coverage_plan,job.get('topic_snapshot'),
                        request_context=dict(request_context,task_intent='agent' if job.get('kind')=='agent' else intent))
                    admitted={e['id'] for e in context['evidence']};refs=[r for r in refs if r['id'] in admitted]
                    coverage_snapshot=answer_coverage.snapshot(self.store,thread['project'],coverage_plan,retrieved_refs,read_refs,refs)
                    evidence_context={'question':question,'coverage':coverage_snapshot,'retained':refs,'read':read_refs}
                    checkpoint()
                    if cross_document and len({answer_coverage.document_key(r) for r in refs})<2:raise ValueError('COMPARISON_CONTEXT_TOO_SMALL')
                response_schema=None;response_contract=None
                if settings['profile_ref']:
                    from .desktop import ANSWER_SCHEMA
                    from .response_contract import bind_evidence,REVISION as response_revision
                    base_schema=ANSWER_SCHEMA
                    if job.get('kind')=='agent':
                        from research_opportunities.research_opportunities import schema
                        base_schema=schema()
                    response_schema=bind_evidence(base_schema,[e['id'] for e in context['evidence']])
                    response_contract={'revision':response_revision,'schema_sha256':digest(response_schema),
                                       'evidence_ids':[e['id'] for e in context['evidence']]}
                self.store.put('job',identity,job['project'],dict(self.job_status(identity),evidence_coverage=coverage_snapshot,response_contract=response_contract))
                # Frozen retry evidence is never silently retrimmed after a style change.
                if len(packed(context))>settings['context_chars']:
                    raise ValueError('RETRY_CONTEXT_EXCEEDS_CONTEXT_BUDGET' if job.get('retry_of') else 'QUESTION_EXCEEDS_CONTEXT_BUDGET')
                self.versions.validate_context(job,context,refs)
                checkpoint()
                from model_gateway.research_prompt_cache import research_prompt,CHAT_RULES
                prompt=research_prompt(CHAT_RULES,context)
                value={}
                usage={};engine='evidence_only';citations=[r['id'] for r in refs]
                # Discussion depth is not an execution requirement. Only a
                # concrete request for external/long-running work suggests handoff.
                handoff=bool(re.search(r'^(?:请|帮我)?(?:交给|转交).{0,16}(?:Agent|智能体)|^(?:请|帮我)?(?:持续|自动|跨应用|跨仓库).{0,24}(?:执行|运行|抓取|监控)|^(?:please\s+)?(?:hand off|delegate).{0,24}agent|^(?:please\s+)?(?:continuously|autonomously).{0,24}(?:run|execute|monitor)',question,re.I))
                if settings['profile_ref']:
                    if not (self.agent_model if job.get('kind')=='agent' else self.model):raise ValueError('CHAT_MODEL_RUNNER_UNAVAILABLE')
                    if job.get('kind')=='agent':
                        from research_opportunities.research_opportunities import schema,instructions
                        prompt=research_prompt(instructions(),context)
                        pack={'schema_version':'MemoAgentHandoff-v2','project':thread['project'],'thread_id':thread['id'],'question':question,
                            'agent':settings['agent'],'evidence':refs,'conversation_summary':[{'role':m['role'],'text':m.get('text',m.get('excerpt',''))} for m in context['extractive_summary']+context['recent_messages']],
                            'instructions':instructions(),'language_context':copy.deepcopy(request_context['language_context']),'expression_preference':job['answer_style'],'expression_scope':'User preference only; does not replace receiving agent permissions or task instructions','created_at':now(),'external_transmission':True,'artifact_ids':artifact_ids,'profile_ref':settings['profile_ref']}
                        pack['content_hash']=digest(pack)
                        self.store.put('handoff','handoff_'+identity,thread['project'],pack)
                        self.store.put('job',identity,job['project'],dict(self.job_status(identity),handoff_id='handoff_'+identity,progress='AGENT_RUNNING'))
                        result=self.agent_model(profile_ref=settings['profile_ref'],prompt=prompt,job_id=identity,response_schema=response_schema)
                    else:result=self.model(profile_ref=settings['profile_ref'],prompt=prompt,job_id=identity,response_schema=response_schema)
                    value=result['response'];usage=result.get('execution_receipt',{});engine=result.get('model_name','configured_model')
                    if not isinstance(value,dict) or not isinstance(value.get('answer'),str) or not isinstance(value.get('citations'),list):raise ValueError('CHAT_RESPONSE_INVALID')
                    if any(not isinstance(c,str) or c not in admitted for c in value['citations']):raise ValueError('CHAT_CITATION_NOT_IN_CONTEXT')
                    text=value['answer'];citations=value['citations']
                    if not text.strip():raise ValueError('CHAT_RESPONSE_EMPTY')
                else:
                    text=(choose(request_context['language_context'],'已找到以下资料片段。当前为本地检索模式，可在输入框选择模型继续提问。\n\n','The excerpts below were retrieved locally. Select a model in the input area to continue.\n\n','以下の断片をローカル検索で取得しました。入力欄でモデルを選択して質問を続けられます。\n\n')+
                        '\n\n'.join(f'[{i+1}] {r["title"]}\n{r["text"][:550]}' for i,r in enumerate(refs))) if refs else choose(request_context['language_context'],'当前项目中没有找到足够相关的证据。请检查资料范围、刷新索引，或准备材料交给 Agent。','No sufficiently relevant evidence was found in this project. Check the material scope, refresh the index, or prepare materials for an agent.','このプロジェクトでは十分に関連する証拠が見つかりませんでした。資料の範囲を確認し、索引を更新するか、エージェントに渡す資料を用意してください。')
                    citations=[r['id'] for r in refs]
                with self.store.tx() as db:
                    latest=self.store.get('job',identity,db=db)
                    if not latest:return
                    if self.closed and latest['status'] in {'QUEUED','RUNNING'}:
                        self.store.put('job',identity,job['project'],dict(latest,status='INTERRUPTED',usage=usage,error='Workspace closed; result discarded',elapsed_ms=round((time.monotonic()-started)*1000)),db=db)
                        return
                    if latest['status'] not in {'QUEUED','RUNNING'}:
                        if usage:self.store.put('job',identity,job['project'],dict(latest,late_usage=usage,late_result='DISCARDED',elapsed_ms=round((time.monotonic()-started)*1000)),db=db)
                        return
                    if latest['cancel_requested']:
                        self.store.put('job',identity,job['project'],dict(latest,status='CANCELLED',usage=usage,elapsed_ms=round((time.monotonic()-started)*1000)),db=db);return
                    if latest['status'] not in {'QUEUED','RUNNING'}:return
                    current=self.store.get('thread',thread['id'],db=db)
                    if not current:return
                    if current.get('archived'):raise ValueError('THREAD_ARCHIVED')
                    self.versions.validate_context(job,context,refs,db=db)
                    user=copy.deepcopy(job['question_message'])
                    by_evidence_id={r['id']:r for r in refs}
                    answer={'id':uid('msg_'),'role':'assistant','text':text,'created_at':now(),'citations':[by_evidence_id[i] for i in citations],
                        'answer_version':1,'language_context':copy.deepcopy(request_context['language_context']),'evidence_context':evidence_context,'claim_reviews':[],'review_binding':None,
                        'engine':engine,'usage':usage,'ranking_policy_hash':search.get('policy_hash'),'ranking_receipts':search.get('ranking_receipts',[]),'ranking_warnings':search.get('warnings',[]),'intent':intent,'artifact_ids':artifact_ids or [],'elapsed_ms':round((time.monotonic()-started)*1000),'needs_agent':handoff,
                        'context':{'memory_ids':[m['id'] for m in context['confirmed_memory']],'summary_message_ids':[m['message_id'] for m in context['extractive_summary']],
                            'chars':len(packed(context)),'estimated_input_tokens':(len(prompt)+2)//3,'token_estimate_only':True}}
                    answer['generation']={'schema_version':'MemoAnswerGeneration-v1','job_id':identity,'request_id':job['request_id'],
                        'topic_snapshot':copy.deepcopy((job.get('retry_snapshot') or {}).get('topic_snapshot') or job['topic_snapshot']),
                        'language_context':copy.deepcopy(request_context['language_context']),
                        'topic_commit_binding':job['topic_snapshot']['binding'],'style':copy.deepcopy(job['answer_style']),'model_binding':copy.deepcopy(job.get('model_binding')),
                        'model_settings':copy.deepcopy(job['model_settings']),'weight_policy':copy.deepcopy(job['weight_policy']),
                        'artifact_ids':artifact_ids,'context':copy.deepcopy(context),'summary':summary,'refs':refs,'search':search,
                        'intent':intent,'coverage_snapshot':coverage_snapshot,'evidence_context':evidence_context,
                        'prompt_sha256':digest(prompt.encode()),'actual_instruction_sha256':digest(prompt.split('<SOURCE_CONTEXT>',1)[0].encode()),
                        'parent_path_hash':job['parent_path_hash'],'retry_of':job.get('retry_of')}
                    if job.get('kind')=='agent':
                        from research_opportunities.research_opportunities import validate
                        answer['opportunities']=validate(value.get('opportunities',[]),admitted);answer['agent_result']=True;answer['needs_agent']=False
                    draft=value.get('knowledge_draft')
                    if (intent in {'comparison','knowledge'} or job.get('kind')=='agent') and draft is not None:
                        issue=None
                        if not isinstance(draft,dict) or set(draft)!={'title','claim','scope','limitations','evidence_ids'}:
                            issue='KNOWLEDGE_DRAFT_INVALID'
                        elif any(not isinstance(draft[k],str) or not draft[k].strip() for k in ('title','claim','scope','limitations')):
                            issue='KNOWLEDGE_DRAFT_INVALID'
                        elif not isinstance(draft['evidence_ids'],list) or not draft['evidence_ids'] or any(not isinstance(i,str) or i not in admitted for i in draft['evidence_ids']):
                            issue='KNOWLEDGE_DRAFT_CITATION_INVALID'
                        if issue:
                            answer['knowledge_draft_issue']=issue
                            self.store.event('KNOWLEDGE_DRAFT_WITHHELD',identity,{'reason':issue,'draft_hash':digest(draft),'answer_preserved':True},db)
                        else:
                            proposal=self.feedback.propose(project=thread['project'],**draft,origin='desktop',language_context=request_context['language_context'],db=db)
                            answer['feedback_id']=proposal['id']
                            self.store.put('answer_feedback',answer['id'],thread['project'],dict(thread_id=thread['id'],answer_id=answer['id'],feedback_id=proposal['id'],job_id=identity),db=db)
                    user['artifact_ids']=artifact_ids or []
                    topic_result=self.topic.commit_answer(job['topic_snapshot'],self.versions.generation_thread(job,db=db),user,answer,value.get('research_notes',[]),artifact_ids,db)
                    answer['research_progress']=topic_result
                    answer['context']['research_topic']=context['research_topic']
                    answer['context']['history_message_ids']=[m['message_id'] for m in context['extractive_summary']+context['recent_messages']]
                    answer['context']['withheld_history_ids']=context['withheld_history_ids']
                    answer_versions.append(current,user,answer,selection_revision=job['selection_revision'])
                    current['summary']=[];current['summary_ids']=[]
                    if len(current['messages'])==2 and not current.get('title_custom'):current['title']=question[:70]
                    current['updated_at']=now()
                    self.store.put('thread',thread['id'],thread['project'],current,db=db)
                    self.store.put('job',identity,job['project'],dict(latest,status='COMPLETE',answer_id=answer['id'],elapsed_ms=answer['elapsed_ms'],usage=usage),db=db)
                    if settings['use_recent_interests'] and not thread['temporary'] and not job.get('retry_of'):
                        from retrieval_weighting.research_interests import term_hashes
                        self.store.event('EXPLICIT_RESEARCH_INTEREST',thread['id'],{'query_hash':digest(question),'term_hashes':term_hashes(question),'evidence_ids':citations,'project':thread['project']},db)
        except BaseException as exc:
            usage=getattr(exc,'execution_receipt',None) or usage
            with self.store.tx() as db:
                job=self.store.get('job',identity,db=db)
                # A late worker exception cannot undo an already committed
                # success or a restart's terminal recovery decision.
                if job and job['status'] in {'QUEUED','RUNNING','CANCELLED'}:
                    self.store.put('job',identity,job['project'],dict(job,status='CANCELLED' if job.get('cancel_requested') else 'ERROR',error=str(exc)[:180],usage=usage,ranking_receipts=search.get('ranking_receipts',[]),elapsed_ms=round((time.monotonic()-started)*1000)),db=db)

    def job_status(self,job_id):
        row=self.store.get('job',job_id)
        if not row:raise ValueError('JOB_NOT_FOUND')
        return row

    def cancel(self,job_id):
        with self.store.tx() as db:
            job=self.store.get('job',job_id,db=db)
            if not job:raise ValueError('JOB_NOT_FOUND')
            if job['status'] not in {'QUEUED','RUNNING'}:return job
            return self.store.put('job',job_id,job['project'],dict(job,cancel_requested=True,status="CANCELLED",result_will_be_discarded=True),db=db)

    def handoff(self,thread_id,question='',evidence_ids=None,agent=None):
        return self.handoffs.create(thread_id,question,evidence_ids,agent)

    def export_handoff(self,handoff_id):
        with self.store.tx() as db:
            value=self.handoffs.get(handoff_id,db)
            self.handoffs.validate(value,db)
            root=self.store.root/'handoffs'/handoff_id;root.mkdir(parents=True,exist_ok=True)
            text='# '+choose(value.get('language_context'),'Memo 研究交接','Memo research handoff','Memo 研究の引き継ぎ')+'\n\n'+value['question']+'\n\n'+value['instructions']+'\n\n'
            text+=choose(value.get('language_context'),'回传绑定：','Return binding: ','返却時の識別情報：')+'handoff_id='+value['id']+'; handoff_hash='+value['content_hash']+'\n\n'
            for r in value['evidence']:text+='## '+r['id']+' · '+r['title']+'\n\n'+r['text']+'\n\n'+choose(value.get('language_context'),'来源哈希：','Source hash: ','出典のハッシュ：')+r['content_hash']+'\n\n'
            text+='## '+choose(value.get('language_context'),'会话上下文','Conversation context','会話のコンテキスト')+'\n\n'+ '\n\n'.join(m['role']+': '+m['text'] for m in value['conversation_summary'])
            for name,data in [('handoff.json',packed(value)),('START_HERE.md',text)]:
                path=root/name
                if path.exists() and path.read_text(encoding='utf-8')!=data:raise ValueError('HANDOFF_EXPORT_CONFLICT')
                if not path.exists():path.write_text(data,encoding='utf-8')
            self.handoffs.validate(value,db)
        return {'status':'EXPORTED','path':str(root/'START_HERE.md'),'external_transmission':False}

    def submit_draft(self,project,title,claim,scope,limitations,evidence_ids,handoff_id=None,handoff_hash=None,request_id=None):
        fields=dict(project=project,title=title,claim=claim,scope=scope,limitations=limitations,evidence_ids=evidence_ids)
        if any(v is not None for v in (handoff_id,handoff_hash,request_id)):
            if not all(v is not None for v in (handoff_id,handoff_hash,request_id)):raise ValueError('HANDOFF_BINDING_REQUIRED')
            return self.handoffs.submit(fields,handoff_id=handoff_id,handoff_hash=handoff_hash,request_id=request_id)
        return self.feedback.propose(**fields,origin='external_agent')

    def call(self,method,params=None,*,desktop=False):
        if method not in (DESKTOP_METHODS if desktop else PUBLIC_METHODS):raise ValueError('METHOD_NOT_ALLOWED')
        if desktop and method!='memo.thread_view':self.conversations.foreground_until=time.monotonic()+.5
        p=dict(params or {})
        if method in answer_versions.METHODS:return self.versions.call(method,p)
        if method in ANSWER_EVIDENCE_METHODS:return self.answer_evidence.call(method,p)
        if method in {'memo.developer_state','memo.developer_save','memo.automation_save','memo.developer_config'}:return self.developer.desktop(method,p)
        if method in CONVERSATION_METHODS:return self.conversations.call(method,p)
        if method in ATTACHMENT_METHODS:return self.attachments.call(method,p)
        if method in CONNECTION_METHODS:return self.connections.desktop(method,p)
        routes={'memo.agent_dispatch':self.agent_dispatch,'memo.knowledge_health':self.knowledge_health,'memo.feedback_revise':self.feedback_revise,'memo.interests':self.interests,'memo.interest_remove':self.interest_remove,'memo.weight_settings':self.weights.settings,'memo.weight_save':self.weights.save,'memo.weight_preview':self.weight_preview,'memo.material_metadata':self.weights.metadata,'memo.material_metadata_save':self.weights.metadata_save,'memo.state':self.state,'memo.settings_save':self.settings_save,'memo.project_save':self.project_save,
            'memo.topic_change':self.topic.change,
            'memo.index_refresh':self.index.refresh,'memo.thread_create':self.thread_create,'memo.thread_get':self.thread_get,
            'memo.thread_forget':self.thread_forget,'memo.ask':self.ask,'memo.cancel':self.cancel,'memo.memory_save':self.memory_save,
            'memo.memory_forget':self.memory_forget,'memo.search':self.index.search,'memo.read_evidence':self.index.read,
            'memo.prepare_tension':self.prepare_tension,'memo.propose_feedback':self.submit_draft,'memo.submit_draft':self.submit_draft,
            'memo.feedback_review':self.feedback.review,'memo.feedback_retract':self.feedback.retract,'memo.job_status':self.job_status,
            'memo.handoff':self.handoff,'memo.export_handoff':self.export_handoff}
        if method in {'memo.library_list','memo.library_add'}:
            handler=getattr(self,method.split('.')[1],None)
            if handler is None:raise ValueError('DESKTOP_LIBRARY_REQUIRED')
            return handler(**p)
        if method=='memo.capabilities':return {'schema_version':'MemoCapabilities-v1','tools':sorted(PUBLIC_METHODS),
            'search':'shared weighted source-bound; configured model calls and metadata lookup may occur','approval':'trusted desktop only','agent_transmission':'explicit handoff',
            'projects':[{'id':r['id'],'name':r['name']} for r in self.store.list('project')]}
        if method=='memo.propose_feedback' and desktop:return self.feedback.propose(**p,origin='desktop')
        return routes[method](**p)

    def close(self):
        self.connector_stop.set()
        with self.lock:self.closed=True
        self.pool.shutdown(wait=False,cancel_futures=True)
        self.conversations.close_views()
        # Temporary threads are persisted only while the app is running; remove at close.
        with self.store.read() as db:
            temporary=[(r['id'],r['source_revision']) for r in db.execute('SELECT id,source_revision FROM conversation_catalog WHERE temporary=1')]
        for identity,revision in temporary:
            if self.owns_desktop_state or identity in self.temporary_threads:
                try:self.thread_forget(identity,revision)
                except ValueError:pass


# Desktop handoff is local preparation only; it never executes a model or imports results.
def desktop_agent(settings):
    agent=settings.get('agent','codex')
    return agent in {'codex-desktop','claude-desktop'} or any(a['id']==agent and a['kind']=='desktop' for a in settings.get('custom_agents',[]))

def validate_agent_settings(value):
    custom=value.get('custom_agents',[]);configs=value.get('desktop_agents',{})
    if not isinstance(custom,list) or len(custom)>20 or not isinstance(configs,dict):raise ValueError('AGENT_CONFIG_INVALID')
    ids={'codex','claude-code','other','codex-desktop','claude-desktop'}
    for a in custom:
        if not isinstance(a,dict) or set(a)!={'id','name','kind'}:raise ValueError('AGENT_CONFIG_INVALID')
        if not isinstance(a['id'],str) or not re.fullmatch(r'custom-[a-z0-9-]{8,48}',a['id']) or a['id'] in ids:raise ValueError('AGENT_CONFIG_INVALID')
        if not isinstance(a['name'],str) or not a['name'].strip() or len(a['name'])>60 or a['kind'] not in {'desktop','cli'}:raise ValueError('AGENT_CONFIG_INVALID')
        ids.add(a['id'])
    if value.get('agent') not in ids:raise ValueError('AGENT_INVALID')
    for key,item in configs.items():
        if key not in ids or not isinstance(item,dict) or set(item)-{'model','path'}:raise ValueError('AGENT_CONFIG_INVALID')
        if any(not isinstance(v,str) or len(v)>1024 or '\x00' in v or '\n' in v or '\r' in v for v in item.values()):raise ValueError('AGENT_CONFIG_INVALID')
        if len(item.get('model',''))>160:raise ValueError('AGENT_CONFIG_INVALID')
        path=item.get('path','')
        if path and (not Path(path).is_absolute() or Path(path).suffix.lower()!='.exe' or path.startswith(('\\\\','//'))):raise ValueError('AGENT_APP_PATH_INVALID')

def desktop_handoff(workspace,thread_id,question,root):
    import shutil
    from .evidence import safe_path
    from memorive_folder_management.policy import windows_io_path
    settings=workspace.settings();validate_agent_settings(settings)
    if not desktop_agent(settings):raise ValueError('AGENT_DESKTOP_REQUIRED')
    target=settings['agent'];config=settings.get('desktop_agents',{}).get(target,{})
    model=config.get('model','').strip()
    if not model:raise ValueError('AGENT_DESKTOP_MODEL_REQUIRED')
    thread=workspace.thread_get(thread_id)
    if any(j.get('thread_id')==thread_id and j['status'] in {'RUNNING','QUEUED'} for j in workspace.store.list('job',thread['project'])):raise ValueError('THREAD_BUSY')
    pack=workspace.handoffs.create(thread_id,question.strip() if isinstance(question,str) else '',evidence_ids=[],agent=target)
    question=pack['question'];admitted={a['id'] for a in pack['source_versions']}
    artifacts=[a for a in workspace.store.list('artifact',thread['project']) if a['id'] in admitted]
    ident=uid('')[:12];folder=Path(root)/ident;folder.mkdir(parents=True,exist_ok=False)
    material=folder/'materials';material.mkdir();warnings=list(pack.get('unavailable_sources',[]));sources=[];messages=pack['conversation_summary']
    (folder/'handoff.json').write_text(packed(pack),encoding='utf8')
    # Reuse the source- and topic-bound history, including withheld-message rules.
    cited={r['id']:r for m in messages for r in m.get('citations',[]) if isinstance(r,dict) and r.get('id')}
    admitted={a['id'] for a in artifacts}
    for eid,ref in cited.items():
        if ref.get('artifact_id') not in admitted:warnings.append({'evidence_id':eid,'reason':'OUTSIDE_CURRENT_SCOPE'})
    for n,a in enumerate(sorted(artifacts,key=lambda a:a['id']),1):
        row={'id':a['id'],'title':a['title'],'state':a['state'],'source_hash':a['content_hash'],'files':[]}
        try:
            source=safe_path(workspace.store.root if a['kind']=='derived' else a['root'],a['path']);io=windows_io_path(source)
            if digest(io.read_bytes())!=a['content_hash']:raise ValueError('EVIDENCE_STALE')
            target_file=material/(f'{n:03d}'+source.suffix.lower())
            shutil.copyfile(io,windows_io_path(target_file));row['files'].append(target_file.relative_to(folder).as_posix())
            if digest(target_file.read_bytes())!=a['content_hash']:raise ValueError('EVIDENCE_STALE')
            with workspace.store.tx() as db:
                chunks=[dict(r) for r in db.execute('SELECT * FROM chunks WHERE artifact_id=? ORDER BY page,line_start,id',(a['id'],))]
            text='\n\n'.join(f"[{c['id']}] page={c['page']} lines={c['line_start']}-{c['line_end']}\n{c['text']}" for c in chunks)
            extracted=material/f'{n:03d}-text.md';extracted.write_text(text,encoding='utf8')
            row['files'].append(extracted.relative_to(folder).as_posix());row['chunks']=len(chunks)
        except (OSError,ValueError) as error:
            row['issue']=str(error) if isinstance(error,ValueError) else 'SOURCE_UNAVAILABLE'
            warnings.append({'artifact_id':a['id'],'reason':row['issue']})
        sources.append(row)
    lines=[]
    for i,m in enumerate(messages,1):
        lines.append(f"## {i}. {m['role']} · {m.get('created_at','')}\n\n{m.get('text','')}\n")
        for ref in m.get('citations',[]):
            lines.append(f"Citation: {ref.get('id','')} · {ref.get('title','')} · page {ref.get('page','')} · source {ref.get('artifact_id','')}\n")
    # Split only between complete messages; preserve the exact eligible history in JSON too.
    parts=[];buf=''
    for line in lines:
        if len(buf)+len(line)>80000 and buf:parts.append(buf);buf=''
        buf+=line+'\n'
    if buf or not parts:parts.append(buf)
    history=[]
    for i,text in enumerate(parts,1):
        name='conversation.md' if len(parts)==1 else f'conversation-{i:03d}.md'
        (folder/name).write_text(text,encoding='utf8');history.append(name)
    (folder/'conversation.json').write_text(packed(messages),encoding='utf8')
    source_text='# Sources\n\n'+'\n\n'.join(f"## {a['title']}\nID: {a['id']}\nSHA256: {a['source_hash']}\nFiles: {', '.join(a['files'])}\nStatus: {a.get('issue','READY')}" for a in sources)
    (folder/'sources.md').write_text(source_text,encoding='utf8')
    name={'codex-desktop':'Codex Desktop','claude-desktop':'Claude Code Desktop'}.get(target,next((a['name'] for a in settings.get('custom_agents',[]) if a['id']==target),target))
    intro=__import__('memorive_language.handoff',fromlist=['desktop_intro']).desktop_intro(pack,name,model,history,sources,packed(warnings))
    (folder/'START_HERE.md').write_text(intro,encoding='utf8')
    files={p.relative_to(folder).as_posix():{'sha256':digest(p.read_bytes()),'bytes':p.stat().st_size} for p in folder.rglob('*') if p.is_file()}
    receipt={'schema_version':'MemoDesktopHandoff-v2','language_context':pack.get('language_context'),'id':ident,'project':thread['project'],'thread_id':thread_id,'thread_revision':pack['thread_revision'],'handoff_id':pack['id'],'handoff_hash':pack['content_hash'],'version_binding':pack['version_binding'],'agent':target,'agent_name':name,'model':model,'app_path':config.get('path',''),'question':question,'message_count':len(messages),'source_count':len(sources),'warnings':warnings,'created_at':now(),'files':files,'external_transmission':False,'model_called':False}
    (folder/'manifest.json').write_text(packed(receipt),encoding='utf8')
    with workspace.store.tx() as db:
        workspace.handoffs.validate(pack,db)
        workspace.store.put('desktop_handoff',ident,thread['project'],dict(receipt,path=str(folder),manifest_hash=digest((folder/'manifest.json').read_bytes())),db=db)
    prompt=__import__('memorive_language.handoff',fromlist=['launch_prompt']).launch_prompt(pack,folder/'START_HERE.md',model)
    return {'status':'PREPARED','id':ident,'path':str(folder),'prompt':prompt,'message_count':len(messages),'source_count':len(sources),'warnings':warnings,'agent_name':name}
