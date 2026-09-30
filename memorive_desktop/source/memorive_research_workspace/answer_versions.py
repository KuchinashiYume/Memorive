"""Answer branches in the existing thread object, independent of E1 revisions."""
import copy,re
from .store import digest,uid,now,packed
from memorive_settings import answer_styles
from memorive_language import freeze as freeze_language

TREE_VERSION='MemoAnswerTree-v1'
METHODS=frozenset({'memo.answer_retry','memo.answer_select','memo.answer_draft_get','memo.answer_draft_save'})

def normalize(thread):
    """Map legacy linear messages without changing their IDs, text or reviews."""
    previous=None;legacy=thread.setdefault('legacy_message_parents',{})
    thread.setdefault('selected_answers',{});thread.setdefault('selection_revision',0)
    for message in thread['messages']:
        key='parent_answer_id' if message['role']=='user' else 'question_id'
        if key not in message:legacy.setdefault(message['id'],previous)
        previous=message['id']
    # A legacy root assistant has no parent selection slot. Earlier writes
    # could persist that None key as "null"; retain actual message-ID keys.
    by_id={m['id']:m for m in thread['messages']}
    for key,identity in list(thread['selected_answers'].items()):
        message=by_id.get(identity)
        if key in (None,'null') and key not in by_id and message and message['role']=='assistant' and parent_id(thread,message) is None:
            del thread['selected_answers'][key]
    thread['answer_tree_version']=TREE_VERSION
    return thread

def parent_id(thread,message):
    key='parent_answer_id' if message['role']=='user' else 'question_id'
    return message[key] if key in message else thread.get('legacy_message_parents',{}).get(message['id'])

def ancestry(thread,answer_id):
    by_id={m['id']:m for m in thread['messages']};path=[];seen=set();identity=answer_id
    while identity is not None:
        if identity in seen or identity not in by_id:raise ValueError('ANSWER_PATH_INVALID')
        seen.add(identity);message=by_id[identity];path.append(message)
        identity=parent_id(thread,message)
    return list(reversed(path))

def visible(thread):
    normalize(thread);path=[];parent=None;seen=set()
    while True:
        children=[m for m in thread['messages'] if parent_id(thread,m)==parent]
        if not children:break
        chosen=thread['selected_answers'].get(parent)
        message=next((m for m in children if m['id']==chosen),children[0])
        if message['id'] in seen:raise ValueError('ANSWER_PATH_INVALID')
        seen.add(message['id']);path.append(message);parent=message['id']
    return path

def path_token(messages):return digest([[m['id'],m.get('answer_version',1),digest(m['text'])] for m in messages])

def _select_path(thread,path):
    for message in path:
        parent=parent_id(thread,message)
        if message['role']=='assistant' and parent is not None:
            thread['selected_answers'][parent]=message['id']

def retry_style_override(thread,answer):
    """Recover legacy retry priority from its recorded origin, without editing it."""
    by_id={m['id']:m for m in thread['messages']}
    copied={m['copied_from_message_id']:m for m in thread['messages'] if m.get('copied_from_message_id')}
    original=answer['generation']['style']['style_id'];question=answer.get('question_id');seen=set()
    while True:
        if answer['id'] in seen:raise ValueError('RETRY_STYLE_HISTORY_UNAVAILABLE')
        seen.add(answer['id']);generation=answer.get('generation') or {};style=generation.get('style') or {}
        if answer.get('question_id')!=question or style.get('style_id')!=original:raise ValueError('RETRY_STYLE_HISTORY_UNAVAILABLE')
        override=answer_styles.effective_override(style)
        if 'overrides_question_style' in style or style.get('source')!='RETRY_ORIGINAL':return override
        # Before priority-v2, ordinary retries recorded the operation but lost
        # the explicit selection flag. Follow the existing retry_of evidence.
        parent=generation.get('retry_of');answer=by_id.get(parent) or copied.get(parent)
        if not answer:raise ValueError('RETRY_STYLE_HISTORY_UNAVAILABLE')

def project(thread):
    row=copy.deepcopy(thread);normalize(row);all_messages=row['messages'];messages=visible(row)
    for message in messages:
        if message['role']!='assistant':continue
        versions=[m for m in all_messages if m['role']=='assistant' and parent_id(row,m)==parent_id(row,message)]
        message['answer_variants']=[dict(id=m['id'],style_id=(m.get('generation') or {}).get('style',{}).get('style_id',answer_styles.DEFAULT_STYLE)) for m in versions]
        message['variant_number']=next(i for i,m in enumerate(versions,1) if m['id']==message['id'])
        message['retry_available']=bool(message.get('generation')) and message.get('engine')!='evidence_only' and not message.get('agent_result') and not message.get('e1_action')
    row.update(messages=messages,path_token=path_token(messages),all_message_count=len(all_messages),
               branch_key=messages[-1]['id'] if messages else 'root')
    row['summary']=[];row['summary_ids']=[]  # Future context derives from this path.
    return row

def append(thread,user,answer,*,selection_revision):
    normalize(thread)
    existing=next((m for m in thread['messages'] if m['id']==user['id']),None)
    if existing is None:thread['messages'].append(user)
    elif existing['role']!='user' or existing['text']!=user['text']:raise ValueError('QUESTION_IDENTITY_CONFLICT')
    answer['question_id']=user['id'];thread['messages'].append(answer)
    if thread['selection_revision']==selection_revision:
        # Select ancestors too, but only if the user has not changed the view.
        _select_path(thread,ancestry(thread,answer['id']))
        thread['selection_revision']+=1
    thread['updated_at']=now()

class AnswerVersions:
    def __init__(self,workspace):self.ws=workspace;self.store=workspace.store

    def select(self,thread_id,answer_id,expected_revision):
        with self.store.tx() as db:
            thread=self.store.get('thread',thread_id,db=db)
            if not thread:raise ValueError('THREAD_NOT_FOUND')
            if thread['revision']!=expected_revision:raise ValueError('REVISION_CONFLICT')
            normalize(thread);path=ancestry(thread,answer_id)
            if not path or path[-1]['role']!='assistant':raise ValueError('ANSWER_NOT_FOUND')
            _select_path(thread,path)
            thread['selection_revision']+=1
            result=self.store.put('thread',thread_id,thread['project'],thread,expected=expected_revision,db=db)
            self.store.event('ANSWER_VERSION_SELECTED',answer_id,{'thread_id':thread_id},db)
            return project(result)

    def retry(self,thread_id,answer_id,request_id,style_id=None,output_language=None):
        if style_id is not None:answer_styles.validate_style(style_id)
        thread=self.store.get('thread',thread_id)
        if not thread:raise ValueError('THREAD_NOT_FOUND')
        normalize(thread);path=ancestry(thread,answer_id)
        if not path:raise ValueError('ANSWER_NOT_FOUND')
        answer=path[-1]
        if answer['role']!='assistant':raise ValueError('ANSWER_NOT_FOUND')
        if answer.get('e1_action') or answer.get('agent_result'):raise ValueError('RETRY_SIDE_EFFECT_NOT_ALLOWED')
        if answer.get('engine')=='evidence_only':raise ValueError('RETRY_MODEL_REQUIRED')
        generation=answer.get('generation')
        if not generation:raise ValueError('RETRY_CONTEXT_UNAVAILABLE')
        original=generation['style'];style=answer_styles.snapshot(style_id or original['style_id'],
            preference_revision=original['preference_revision'],source='RETRY_SELECTION' if style_id is not None else 'RETRY_ORIGINAL',
            overrides_question_style=True if style_id is not None else retry_style_override(thread,answer))
        question=path[-2]
        if question['role']!='user':raise ValueError('ANSWER_PATH_INVALID')
        return self.enqueue(thread_id,question['text'],request_id,output_language=output_language,retry=dict(answer_id=answer_id,question=question,
            ancestors=path[:-2],generation=copy.deepcopy(generation),style=style,style_override=style_id))

    def enqueue(self,thread_id,question,request_id,artifact_ids=None,agent_profile_ref=None,path_token_expected=None,retry=None,output_language=None):
        if not isinstance(question,str) or not question.strip() or len(question)>8000:raise ValueError('QUESTION_INVALID')
        if not isinstance(request_id,str) or not re.fullmatch(r'[\w-]{8,96}',request_id):raise ValueError('REQUEST_ID_INVALID')
        request_hash=digest(dict(question=question,artifact_ids=artifact_ids,agent_profile_ref=agent_profile_ref,path=path_token_expected,
            retry_answer=retry['answer_id'] if retry else None,style=retry['style_override'] if retry else None))
        if output_language is not None:request_hash=digest([request_hash,output_language])
        identity='research-'+digest([thread_id,request_id])[:24]
        with self.ws.lock:
            if self.ws.closed:raise ValueError('WORKSPACE_CLOSED')
            defaults=self.ws.settings()
            preview=self.store.get('thread',thread_id)
            if not preview:raise ValueError('THREAD_NOT_FOUND')
            saved_policy=self.ws.weights.settings(preview['project'])
            with self.store.tx() as db:
                existing=self.store.get('job',identity,db=db)
                if existing:
                    if existing.get('request_hash')!=request_hash:raise ValueError('IDEMPOTENCY_CONFLICT')
                    return existing
                language_context=freeze_language(self.ws.language_settings_get(),current_user_text='' if retry else question,explicit_locale=output_language)
                thread=self.store.get('thread',thread_id,db=db)
                if not thread:raise ValueError('THREAD_NOT_FOUND')
                normalize(thread)
                if thread.get('archived'):raise ValueError('THREAD_ARCHIVED')
                ancestors=retry['ancestors'] if retry else visible(thread)
                if path_token_expected is not None and path_token(ancestors)!=path_token_expected:raise ValueError('ANSWER_PATH_CHANGED')
                if any(j.get('thread_id')==thread_id and j['status'] in {'QUEUED','RUNNING'} for j in self.store.list('job',thread['project'],db=db)):raise ValueError('THREAD_BUSY')
                if any(not future.done() and (self.store.get('job',jid,db=db) or {}).get('thread_id')==thread_id for jid,future in self.ws.futures.items()):raise ValueError('THREAD_BUSY')
                if retry:
                    generation=retry['generation'];selected=generation['artifact_ids'];settings=copy.deepcopy(generation['model_settings']);style=retry['style']
                    binding=generation['model_binding'];memory_snapshot=copy.deepcopy(generation['context']['confirmed_memory'])
                    weight_policy=copy.deepcopy(generation['weight_policy'])
                else:
                    selected=artifact_ids if artifact_ids is not None else self.ws.attachments.scope_ids(thread,db=db)
                    settings=dict(defaults,profile_ref=thread.get('profile_ref',defaults['profile_ref']))
                    if agent_profile_ref:settings['profile_ref']=agent_profile_ref
                    preferences=self.ws.user_preferences_get();style=answer_styles.snapshot(preferences['config']['answer_style'],preference_revision=preferences['revision'])
                    binding=next((copy.deepcopy(o) for o in self.ws.catalog() if o['profile_ref']==settings['profile_ref'] and o.get('eligible')),None)
                    if settings['profile_ref'] and not binding:raise ValueError('CHAT_MODEL_UNAVAILABLE')
                    memory_snapshot=[] if thread['temporary'] or not settings['memory_enabled'] else [dict(id=m['id'],revision=m['revision'],text=m['text']) for m in self.store.list('memory',db=db) if m['active'] and (m['scope']=='user' or m['project']==thread['project'])][:12]
                    weight_policy=saved_policy
                if selected is not None:
                    if not isinstance(selected,list) or any(not isinstance(i,str) for i in selected) or (artifact_ids is not None and len(selected)>32):raise ValueError('MATERIAL_SELECTION_INVALID')
                    for i in selected:
                        a=self.store.get('artifact',i,db=db)
                        if not a or a['project']!=thread['project']:raise ValueError('MATERIAL_OUTSIDE_PROJECT')
                        if a['state']!='active':raise ValueError('MATERIAL_NOT_READY')
                user=copy.deepcopy(retry['question']) if retry else dict(id=uid('msg_'),role='user',text=question,created_at=now(),parent_answer_id=ancestors[-1]['id'] if ancestors else None,artifact_ids=selected or [])
                generation_thread=copy.deepcopy(thread);generation_thread['messages']=copy.deepcopy(ancestors)
                topic_snapshot=self.ws.topic.snapshot(generation_thread,selected,db=db)
                job=self.store.put('job',identity,thread['project'],dict(project=thread['project'],thread_id=thread_id,status='QUEUED',
                    kind='agent' if agent_profile_ref else 'chat',question_hash=digest(question),request_hash=request_hash,request_id=request_id,
                    artifact_ids=selected,model_settings=settings,model_binding=binding,weight_policy=weight_policy,answer_style=style,language_context=language_context,
                    question_message=user,parent_path=copy.deepcopy(ancestors),parent_path_hash=path_token(ancestors),memory_snapshot=memory_snapshot,
                    topic_snapshot=topic_snapshot,selection_revision=thread['selection_revision'],retry_of=retry['answer_id'] if retry else None,
                    retry_snapshot=retry['generation'] if retry else None,created_at=now(),cancel_requested=False),db=db)
            self.ws.futures[identity]=self.ws.pool.submit(self.ws._answer,identity,question,selected)
        return job

    def generation_thread(self,job,*,db=None):
        thread=self.store.get('thread',job['thread_id'],db=db)
        if not thread:raise ValueError('THREAD_NOT_FOUND')
        if thread.get('archived'):raise ValueError('THREAD_ARCHIVED')
        normalize(thread);saved=job.get('parent_path')
        if saved is None:return project(thread)  # already queued legacy jobs
        current={m['id']:m for m in thread['messages']}
        if any(m['id'] not in current or digest(m['text'])!=digest(current[m['id']]['text']) or m.get('answer_version',1)!=current[m['id']].get('answer_version',1) for m in saved):raise ValueError('ANSWER_ANCESTOR_CHANGED')
        thread['messages']=copy.deepcopy(saved);return thread

    def validate_context(self,job,context,refs,*,db=None):
        thread=self.generation_thread(job,db=db)
        if job.get('topic_snapshot'):
            if db is None:
                with self.store.tx() as conn:self.ws.topic.validate_snapshot(job['topic_snapshot'],thread,job.get('artifact_ids'),conn)
            else:self.ws.topic.validate_snapshot(job['topic_snapshot'],thread,job.get('artifact_ids'),db)
        for m in context['confirmed_memory']:
            fresh=self.store.get('memory',m['id'],db=db)
            if not fresh or not fresh['active'] or fresh['revision']!=m['revision']:raise ValueError('MEMORY_CHANGED_DURING_REQUEST')
        for r in refs:self.ws.index.read(r['id'],job['project'],expected_hash=r['content_hash'],db=db)
        binding=job.get('model_binding')
        if binding:
            current=next((r for r in self.ws.catalog() if r['profile_ref']==binding['profile_ref'] and r.get('eligible')),None)
            if not current or current.get('binding_hash')!=binding.get('binding_hash') or current.get('model_name')!=binding.get('model_name'):raise ValueError('CHAT_MODEL_CONFIGURATION_CHANGED')
        if job.get('retry_of'):
            thread=self.store.get('thread',job['thread_id'],db=db);scope=self.ws.attachments.scope_ids(thread,db=db)
            selected=job.get('artifact_ids')
            if scope is not None and (selected is None or any(i not in scope for i in selected)):raise ValueError('RETRY_MATERIAL_SCOPE_CHANGED')

    def validate_retry_topic(self,job,prior,thread,context):
        """Keep frozen retries, but do not revive changed/deleted topic inputs.

        A commit advances the topic revision/position even if no input changed.
        Compare branch-visible semantic inputs instead of incidental revisions.
        Pre-E3 generations have no topic snapshot: add its current projection
        over the same frozen ancestors without another retrieval/model call.
        """
        current=job['topic_snapshot'];saved=prior.get('topic_snapshot')
        def semantic(snapshot):
            return {**{k:snapshot.get(k,[]) for k in ('sources','unavailable_entry_ids','withheld_message_ids')},
                    'entries':[{k:e.get(k) for k in ('id','kind','text','state','origin','anchor','parents')} for e in snapshot['entries']]}
        if saved:
            before=semantic(saved);after=semantic(current)
            current_entries={e['id']:e for e in after['entries']}
            if any(current_entries.get(e['id'])!=e for e in before['entries']):raise ValueError('RETRY_TOPIC_CONTEXT_CHANGED')
            current_sources={s['artifact_id']:s for s in after['sources']}
            if any(current_sources.get(s['artifact_id'])!=s for s in before['sources']):raise ValueError('RETRY_TOPIC_CONTEXT_CHANGED')
            used_history={m['message_id'] for name in ('recent_messages','extractive_summary') for m in context[name]}
            if used_history.intersection(after['withheld_message_ids']):raise ValueError('RETRY_TOPIC_CONTEXT_CHANGED')
            # Later entries in another branch/thread are not part of the
            # original generation. Neither admit them nor invalidate a replay
            # merely because the project now contains additional work.
            # Frozen text remains identical. Commit validation uses the newly
            # captured topic binding, while generation retains its input snapshot.
            return
        budget=job['model_settings']['context_chars']
        blocked=set(current.get('withheld_message_ids',[]))
        for field in ('recent_messages','extractive_summary'):
            context[field]=[m for m in context[field] if m['message_id'] not in blocked]
        context['withheld_history_ids']=sorted(blocked)
        available=budget-len(packed(dict(context,research_topic={})))+2
        context['research_topic']=self.ws.topic.context(current,max(0,available),optional_limit=budget//3)
        if len(packed(context))>budget:raise ValueError('QUESTION_EXCEEDS_CONTEXT_BUDGET')

    def _draft_owner(self,thread_id,branch_key,db=None):
        thread=self.store.get('thread',thread_id,db=db)
        if not thread:raise ValueError('THREAD_NOT_FOUND')
        if branch_key!='root' and not any(m['id']==branch_key for m in thread['messages']):raise ValueError('ANSWER_NOT_FOUND')
        return thread,'draft_'+digest([thread_id,branch_key])[:32]

    def draft_get(self,thread_id,branch_key):
        _,identity=self._draft_owner(thread_id,branch_key)
        return self.store.get('answer_draft',identity) or dict(id=identity,revision=0,text='',thread_id=thread_id,branch_key=branch_key)

    def draft_save(self,thread_id,branch_key,text,expected_revision):
        if not isinstance(text,str) or len(text)>8000:raise ValueError('QUESTION_INVALID')
        with self.store.tx() as db:
            thread,identity=self._draft_owner(thread_id,branch_key,db)
            return self.store.put('answer_draft',identity,thread['project'],dict(thread_id=thread_id,branch_key=branch_key,text=text),expected=expected_revision,db=db)

    def call(self,method,params):return {'memo.answer_retry':self.retry,'memo.answer_select':self.select,'memo.answer_draft_get':self.draft_get,'memo.answer_draft_save':self.draft_save}[method](**params)
