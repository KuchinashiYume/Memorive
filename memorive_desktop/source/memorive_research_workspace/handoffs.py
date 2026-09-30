"""Version-bound local handoffs and idempotent, human-reviewed returns."""
import re
from .store import digest,now,uid
from .answer_versions import project as visible_thread

class Handoffs:
    def __init__(self,workspace):self.w=workspace;self.store=workspace.store

    def binding(self,thread,scope,db):
        thread=visible_thread(thread)
        topic=self.w.topic.snapshot(thread,scope,db=db)
        unavailable=self.w.index.unavailable_derived(thread['project'],scope,db=db)
        sources=[{'id':a['id'],'content_hash':a['content_hash'],'state':a['state']}
                 for a in self.store.list('artifact',thread['project'],db=db)
                 if a['id'] not in unavailable and ((scope is None and a['state']=='active') or (scope is not None and a['id'] in scope))]
        memories=[] if thread.get('temporary') else [{'id':m['id'],'revision':m['revision'],'hash':digest(m['text'])}
                 for m in self.store.list('memory',db=db) if m['active'] and (m['scope']=='user' or m['project']==thread['project'])]
        value={'thread_revision':thread['revision'],'selected_path_hash':thread['path_token'],'topic_snapshot':topic,'artifact_ids':scope,
               'source_versions':sorted(sources,key=lambda r:r['id']),'memory_versions':sorted(memories,key=lambda r:r['id'])}
        if unavailable:value['unavailable_sources']=[dict(artifact_id=aid,**row) for aid,row in sorted(unavailable.items())]
        value['version_binding']=digest(value);return value

    def messages(self,thread,scope,topic,db):
        thread=visible_thread(thread)
        withheld=set(topic.get('withheld_message_ids',[]));out=[]
        for m in thread['messages']:
            if m['id'] in withheld:continue
            try:
                for ref in m.get('citations',[]):
                    if scope is not None and ref['artifact_id'] not in scope:raise ValueError('OUTSIDE_SCOPE')
                    self.w.index.read(ref['id'],thread['project'],expected_hash=ref['content_hash'],db=db)
            except (ValueError,OSError):continue
            out.append(m)
        return out

    def create(self,thread_id,question='',evidence_ids=None,agent=None):
        from memorive_language import freeze
        from memorive_language.handoff import instructions
        language_context=freeze(self.w.language_settings_get(),current_user_text=question)
        selected_agent=agent or self.w.settings()['agent']
        with self.store.tx() as db:
            thread=self.store.get('thread',thread_id,db=db)
            if not thread:raise ValueError('THREAD_NOT_FOUND')
            if thread.get('archived'):raise ValueError('THREAD_ARCHIVED')
            scope=self.w.attachments.scope_ids(thread,db=db)
            binding=self.binding(thread,scope,db)
            if binding.get('unavailable_sources') and not binding['source_versions']:raise ValueError('HANDOFF_NO_CURRENT_SOURCE')
            messages=self.messages(thread,scope,binding['topic_snapshot'],db)
            question=question or next((m['text'] for m in reversed(messages) if m['role']=='user'),'')
            if not isinstance(question,str) or not question.strip() or len(question)>8000:raise ValueError('QUESTION_INVALID')
        if evidence_ids is not None:
            if not isinstance(evidence_ids,list) or len(evidence_ids)>24:raise ValueError('HANDOFF_EVIDENCE_INVALID')
            refs=[self.w.index.read(i,thread['project']) for i in dict.fromkeys(evidence_ids)]
        else:
            # Local pack preparation never starts a configured ranking model.
            refs=self.w.index.legacy_lexical_search(question,thread['project'],limit=8,artifact_ids=scope)['results']
        with self.store.tx() as db:
            self.validate(dict(project=thread['project'],thread_id=thread_id,**binding),db)
            refs=[self.w.index.read(r['id'],thread['project'],expected_hash=r['content_hash'],db=db) for r in refs]
            if scope is not None and any(r['artifact_id'] not in scope for r in refs):raise ValueError('EVIDENCE_OUTSIDE_THREAD_SCOPE')
            identity=uid('handoff_')
            value={'schema_version':'MemoAgentHandoff-v3','project':thread['project'],'thread_id':thread_id,
                'question':question,'agent':selected_agent,'evidence':refs,'language_context':language_context,
                'conversation_summary':[{k:m[k] for k in ('id','role','text','created_at','citations','engine','knowledge_draft_issue') if k in m} for m in messages],
                **binding,'created_at':now(),'external_transmission':False,'model_called':False,
                'expression_preference':self.w.user_preferences_get()['config']['answer_style'],
                'expression_scope':'User preference only; receiving agent capability and instructions remain authoritative',
                'instructions':instructions(language_context)}
            value['content_hash']=digest(value)
            return self.store.put('handoff',identity,thread['project'],value,db=db)

    def validate(self,pack,db):
        thread=self.store.get('thread',pack['thread_id'],db=db)
        if not thread or thread['project']!=pack['project'] or thread.get('archived'):raise ValueError('HANDOFF_THREAD_CHANGED')
        scope=self.w.attachments.scope_ids(thread,db=db)
        if self.binding(thread,scope,db)['version_binding']!=pack.get('version_binding'):raise ValueError('HANDOFF_VERSION_CHANGED')
        for source in pack['source_versions']:
            first=db.execute('SELECT id FROM chunks WHERE artifact_id=? LIMIT 1',(source['id'],)).fetchone()
            if first is None:raise ValueError('HANDOFF_SOURCE_CHANGED')
            try:self.w.index.read(first['id'],pack['project'],expected_hash=source['content_hash'],db=db)
            except (ValueError,OSError) as error:raise ValueError('HANDOFF_SOURCE_CHANGED') from error
        return thread

    def get(self,identity,db):
        value=self.store.get('handoff',identity,db=db)
        if value is None:raise ValueError('HANDOFF_NOT_FOUND')
        if value.get('schema_version')!='MemoAgentHandoff-v3':raise ValueError('HANDOFF_LEGACY_RECREATE_REQUIRED')
        return value

    def submit(self,fields,*,handoff_id,handoff_hash,request_id):
        if not isinstance(request_id,str) or not re.fullmatch(r'[\w-]{8,96}',request_id):raise ValueError('REQUEST_ID_INVALID')
        if not isinstance(handoff_id,str) or not isinstance(handoff_hash,str):raise ValueError('HANDOFF_BINDING_REQUIRED')
        identity='return_'+digest([handoff_id,request_id])[:32];fingerprint=digest([handoff_hash,fields])
        with self.store.tx() as db:
            pack=self.get(handoff_id,db)
            if fields['project']!=pack['project'] or handoff_hash!=pack['content_hash']:raise ValueError('HANDOFF_BINDING_MISMATCH')
            prior=self.store.get('agent_return',identity,db=db)
            if prior:
                if prior['request_hash']!=fingerprint:raise ValueError('IDEMPOTENCY_CONFLICT')
                # An exact transport replay returns its existing receipt even
                # after later work; it never admits or rewrites knowledge.
                return self.store.get('feedback',prior['feedback_id'],db=db)
            self.validate(pack,db)
            source_ids={s['id'] for s in pack['source_versions']}
            for eid in fields['evidence_ids']:
                if self.w.index.read(eid,pack['project'],db=db)['artifact_id'] not in source_ids:raise ValueError('HANDOFF_EVIDENCE_OUTSIDE_SCOPE')
            provenance={'handoff_id':handoff_id,'handoff_hash':handoff_hash,'version_binding':pack['version_binding']}
            result=self.w.feedback.propose(**fields,origin='external_agent',handoff_binding=provenance,language_context=pack.get('language_context'),db=db)
            self.store.put('agent_return',identity,pack['project'],dict(thread_id=pack['thread_id'],handoff_id=handoff_id,
                request_hash=fingerprint,feedback_id=result['id'],created_at=now()),db=db)
            return result

    def validate_review(self,proposal,db):
        bound=proposal.get('handoff_binding')
        if not bound:return
        pack=self.get(bound['handoff_id'],db)
        if pack['content_hash']!=bound['handoff_hash']:raise ValueError('HANDOFF_BINDING_MISMATCH')
        self.validate(pack,db)

    def validate_desktop(self,row):
        from pathlib import Path
        with self.store.tx() as db:
            pack=self.get(row.get('handoff_id',''),db)
            if pack['content_hash']!=row.get('handoff_hash'):raise ValueError('HANDOFF_BINDING_MISMATCH')
            self.validate(pack,db)
            folder=Path(row['path']).resolve();manifest=folder/'manifest.json'
            try:
                if digest(manifest.read_bytes())!=row['manifest_hash']:raise ValueError('HANDOFF_CHANGED')
                for name,meta in row['files'].items():
                    path=(folder/name).resolve()
                    if not path.is_relative_to(folder) or digest(path.read_bytes())!=meta['sha256']:raise ValueError('HANDOFF_CHANGED')
            except OSError as error:raise ValueError('HANDOFF_CHANGED') from error
            self.validate(pack,db)
            return {'status':'READY','handoff_id':pack['id'],'handoff_hash':pack['content_hash'],'model_called':False}
