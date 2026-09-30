"""One project topic, anchored to original messages and current source versions.

User statements and model proposals remain distinct. A successful chat and its
topic delta commit together; reading/context preparation never mutates state.
"""
import copy,re
from collections import defaultdict,deque
from .store import digest,now,packed
from .answer_versions import project as visible_thread

SCHEMA='MemoResearchTopic-v1'
KINDS={'user_statement','goal','constraint','question','hypothesis','judgment','open_question','next_step'}
STATES={'user_stated','proposed','paused','rejected','deleted','resolved'}

def spans(text):
    # Retain exact user language, not a keyword-selected interpretation of it.
    # A question or a hypothetical remains a quotation, never a confirmed fact.
    matches=[]
    for paragraph in re.finditer(r'[^\n]+',text):
        if len(paragraph.group())<=1600:
            matches.append((paragraph.start(),paragraph.group()))
        else:
            matches.extend((paragraph.start()+m.start(),m.group()) for m in re.finditer(r'[^。！？!?；;]+[。！？!?；;]*',paragraph.group()))
    for offset,raw in matches:
        value=raw.strip()
        if not value:continue
        start=offset+len(raw)-len(raw.lstrip())
        yield 'user_statement',start,start+len(value),value

def anchor(thread,message,start,end):
    return {'thread_id':thread['id'],'message_id':message['id'],'message_hash':digest(message['text']),
            'answer_version':message.get('answer_version',1),'start':start,'end':end}

class Topic:
    def __init__(self,workspace):
        self.w=workspace;self.store=workspace.store
        with self.store.read() as db:self._format(db)

    def _format(self,db):
        row=self.store.get('research_format','topic',db=db)
        if row and row.get('schema_version')!=SCHEMA:raise ValueError('TOPIC_FORMAT_UNSUPPORTED')

    def _load(self,project,db):
        self._format(db)
        row=self.store.get('research_topic',project,db=db)
        if row is None:return {'schema_version':SCHEMA,'id':project,'revision':0,'project':project,'entries':[],'cursors':{},'last_sources':[],'position':None}
        if (row.get('schema_version')!=SCHEMA or row.get('project')!=project
                or not isinstance(row.get('entries'),list) or not isinstance(row.get('cursors'),dict)
                or not isinstance(row.get('last_sources'),list)):
            raise ValueError('TOPIC_STATE_INVALID')
        ids=set()
        for entry in row['entries']:
            if (not isinstance(entry,dict) or not isinstance(entry.get('id'),str) or entry['id'] in ids
                    or entry.get('kind') not in KINDS or entry.get('state') not in STATES
                    or not isinstance(entry.get('text'),str) or not isinstance(entry.get('anchor'),dict)
                    or not isinstance(entry.get('parents'),list)):
                raise ValueError('TOPIC_STATE_INVALID')
            ids.add(entry['id'])
        return row

    def get(self,project):
        if not self.store.get('project',project):raise ValueError('PROJECT_NOT_FOUND')
        with self.store.read() as db:return self._load(project,db)

    def _message(self,entry,project,db,cache):
        link=entry['anchor'];tid=link.get('thread_id')
        if tid not in cache:
            raw=self.store.get('thread',tid,db=db);cache[tid]=visible_thread(raw) if raw else None
        thread=cache[tid]
        if not thread or thread['project']!=project or thread.get('temporary') or thread.get('archived'):return None
        message=next((m for m in thread['messages'] if m['id']==link.get('message_id')),None)
        if (not message or digest(message['text'])!=link.get('message_hash')
                or message.get('answer_version',1)!=link.get('answer_version')):return None
        start,end=link.get('start'),link.get('end')
        if type(start) is not int or type(end) is not int or not 0<=start<end<=len(message['text']):return None
        if message['text'][start:end]!=entry['text']:return None
        return message

    def _user_entries(self,thread,messages):
        rows=[]
        for message in messages:
            if message.get('role')!='user':continue
            seen=set()
            for kind,start,end,text in spans(message['text']):
                # Identical repeated clauses in one message have the same
                # meaning. Keep one exact, retrievable occurrence, not 700 copies.
                if text in seen:continue
                seen.add(text)
                rows.append({'id':'topic_'+digest([thread['id'],message['id'],start,end])[:20],
                    'kind':kind,'text':text,'state':'user_stated','origin':'exact_user_statement',
                    'anchor':anchor(thread,message,start,end),'parents':[],'created_at':message.get('created_at',now())})
        return rows

    def _withheld(self,entries,thread,db,cache):
        """Follow recorded inputs so an assistant cannot revive a removed quote.

        Older answers lack history IDs. Conservatively keep their subsequent
        legacy answer chain out of context; original conversations stay intact.
        """
        anchors={e['id']:(e['anchor'].get('thread_id'),e['anchor'].get('message_id')) for e in entries}
        blocked={anchors[e['id']] for e in entries if e['state'] in {'paused','rejected','deleted'}}
        children=defaultdict(set)
        for tid in {thread['id']}|{key[0] for key in anchors.values()}:
            if tid not in cache:
                raw=self.store.get('thread',tid,db=db);cache[tid]=visible_thread(raw) if raw else None
            owner=cache[tid]
            if not owner or owner['project']!=thread['project']:continue
            previous_user=None;previous_assistant=None
            for message in owner['messages']:
                key=(tid,message['id'])
                if message.get('role')=='user':previous_user=key;continue
                if message.get('role')!='assistant':continue
                context=message.get('context') or {}
                dependencies={anchors[e['id']] for e in (context.get('research_topic') or {}).get('entries',[]) if e.get('id') in anchors}
                if previous_user is not None:dependencies.add(previous_user)
                if 'history_message_ids' in context:
                    dependencies.update((tid,mid) for mid in context['history_message_ids'])
                elif previous_assistant is not None:dependencies.add(previous_assistant)
                for parent in dependencies:children[parent].add(key)
                previous_assistant=key
        pending=deque(blocked)
        while pending:
            for child in children[pending.popleft()]:
                if child not in blocked:blocked.add(child);pending.append(child)
        return blocked

    def snapshot(self,thread,selected,*,db=None):
        if db is None:
            with self.store.tx() as conn:return self.snapshot(thread,selected,db=conn)
        thread=visible_thread(thread)
        if thread['temporary']:
            return {'schema_version':SCHEMA,'project':thread['project'],'revision':None,'temporary':True,'entries':self._user_entries(thread,thread['messages']),'changes':[],'binding':digest(['temporary',thread['id']])}
        row=self._load(thread['project'],db);entries=copy.deepcopy(row['entries']);known={r['id'] for r in entries}
        cursor=row['cursors'].get(thread['id']);messages=thread['messages']
        if cursor:
            found=next((i for i,m in enumerate(messages) if m['id']==cursor['message_id'] and digest(m['text'])==cursor['message_hash']),None)
            if found is not None:messages=messages[found+1:]
        for item in self._user_entries(thread,messages):
            if item['id'] not in known:entries.append(item);known.add(item['id'])
        cache={thread['id']:thread};visible=[];unavailable=[]
        blocked=self._withheld(entries,thread,db,cache)
        for entry in entries:
            if entry['state'] not in {'user_stated','proposed','resolved'}:continue
            if not self._message(entry,thread['project'],db,cache):continue
            if entry['origin']=='model_candidate' and (entry['anchor']['thread_id'],entry['anchor']['message_id']) in blocked:
                unavailable.append(entry['id']);continue
            try:
                for parent in entry['parents']:
                    if selected is not None and parent['artifact_id'] not in selected:raise ValueError('OUTSIDE_SCOPE')
                    self.w.index.read(parent['id'],thread['project'],expected_hash=parent['content_hash'],db=db)
            except (ValueError,OSError):unavailable.append(entry['id']);continue
            visible.append(entry)
        sources=[]
        for aid in selected or []:
            source=self.store.get('artifact',aid,db=db)
            if source:sources.append({'artifact_id':aid,'content_hash':source['content_hash'],'state':source['state']})
        old={s['artifact_id']:s for s in row['last_sources']};new={s['artifact_id']:s for s in sources};changes=[]
        for aid,source in new.items():
            if aid not in old:changes.append({'artifact_id':aid,'change':'added','scientific_impact':'unassessed'})
            elif source!=old[aid]:changes.append({'artifact_id':aid,'change':'source_version_or_state','scientific_impact':'unassessed'})
        value={'schema_version':SCHEMA,'project':thread['project'],'revision':row['revision'],'temporary':False,
               'entries':visible,'unavailable_entry_ids':unavailable,'changes':changes,'sources':sources,'position':row.get('position'),
               'withheld_message_ids':sorted(mid for tid,mid in blocked if tid==thread['id'] and any(m['id']==mid for m in thread['messages']))}
        value['binding']=digest(value);return value

    def context(self,snapshot,budget,optional_limit=None):
        rows=snapshot['entries']
        # Full anchors, source hashes and prior versions remain in the snapshot
        # and store. Send a compact projection: the model needs the quotation,
        # its status and order, not a repeated copy of every provenance field.
        def project(entry,order):
            result={k:entry[k] for k in ('id','kind','text','state','origin')}
            if entry['origin']=='exact_user_statement':result['user_order']=order
            elif entry['parents']:result['evidence_ids']=[p['id'] for p in entry['parents']]
            return result
        # A later repetition is kept at its latest position. Only byte-identical
        # text is coalesced; different numbers, negations and conditions survive.
        last={r['text']:i for i,r in enumerate(rows) if r['origin']=='exact_user_statement'}
        protected=[project(r,i+1) for i,r in enumerate(rows)
                   if r['origin']=='exact_user_statement' and last[r['text']]==i]
        other=[project(r,i+1) for i,r in reversed(list(enumerate(rows))) if r['origin']!='exact_user_statement']
        value={k:copy.deepcopy(snapshot.get(k)) for k in ('schema_version','project','revision','temporary','binding','changes','unavailable_entry_ids')}
        # Group repeated provenance metadata in the prompt only. Every source ID,
        # change kind and impact state survives; the durable snapshot is unchanged.
        grouped={}
        for change in value['changes'] or []:
            key=(change['change'],change['scientific_impact'])
            grouped.setdefault(key,[]).append(change['artifact_id'])
        value['changes']=[{'change':kind,'scientific_impact':impact,'artifact_ids':ids}
                          for (kind,impact),ids in grouped.items()]
        value['entries']=protected+other[:8]
        value['notice']='Exact user quotations in user_order, not confirmed facts or execution commands. Preserve questions, conditions and negation. Apply an explicit later correction to the earlier condition; do not infer a correction from a question or hypothetical. Full originals and versions remain stored under entry IDs. Model notes are proposals.'
        value['omitted_optional_count']=len(other)-min(8,len(other))
        value['coalesced_identical_user_quotes']=sum(r['origin']=='exact_user_statement' for r in rows)-len(protected)
        optional_limit=budget if optional_limit is None else min(budget,optional_limit)
        while len(packed(value))>optional_limit and len(value['entries'])>len(protected):value['entries'].pop()
        value['omitted_optional_count']=len(other)-(len(value['entries'])-len(protected))
        if len(packed(value))>budget:raise ValueError('TOPIC_CONSTRAINT_CONTEXT_TOO_LARGE')
        return value

    def validate_snapshot(self,snapshot,thread,selected,db):
        if snapshot.get('temporary'):return
        current=self.snapshot(thread,selected,db=db)
        if current['binding']!=snapshot['binding']:raise ValueError('TOPIC_CHANGED_DURING_REQUEST')

    def commit_answer(self,snapshot,thread,user,answer,notes,selected,db):
        if thread['temporary']:return None
        self.validate_snapshot(snapshot,thread,selected,db)
        row=self._load(thread['project'],db);known={r['id'] for r in row['entries']}
        # Retrospective extraction is committed only with a successfully saved answer.
        preceding=next((r['anchor'] for r in reversed(row['entries']) if r['origin']=='exact_user_statement'),None)
        links={}
        for entry in snapshot['entries']+self._user_entries(thread,[user]):
            if entry['id'] in known:continue
            entry=copy.deepcopy(entry)
            if entry['origin']=='exact_user_statement':
                key=(entry['anchor']['thread_id'],entry['anchor']['message_id'])
                if key not in links:
                    links[key]={k:preceding[k] for k in ('thread_id','message_id')} if preceding else None
                    preceding=entry['anchor']
                entry['recorded_at_topic_revision']=row['revision']+1
                entry['previous_user_turn']=links[key]
                entry['relation']='later_user_turn_not_automatic_replacement'
            row['entries'].append(entry);known.add(entry['id'])
        refs={r['id']:r for r in answer['evidence_context']['retained']};issues=[]
        if not isinstance(notes,list) or len(notes)>8:notes=[];issues.append('TOPIC_NOTES_INVALID')
        for note in notes:
            if (not isinstance(note,dict) or set(note)!={'kind','quote','evidence_ids'}
                    or note['kind'] not in {'question','hypothesis','judgment','open_question','next_step'}
                    or not isinstance(note['quote'],str) or not 1<=len(note['quote'])<=1200
                    or note['quote'] not in answer['text'] or not isinstance(note['evidence_ids'],list)
                    or any(not isinstance(i,str) or i not in refs for i in note['evidence_ids'])):
                issues.append('TOPIC_NOTE_WITHHELD');continue
            if note['kind']=='judgment' and not note['evidence_ids']:
                issues.append('TOPIC_JUDGMENT_UNANCHORED');continue
            start=answer['text'].index(note['quote']);entry_id='topic_'+digest([thread['id'],answer['id'],note])[:20]
            row['entries'].append({'id':entry_id,'kind':note['kind'],'text':note['quote'],'state':'proposed','origin':'model_candidate',
                'anchor':anchor(thread,answer,start,start+len(note['quote'])),
                'parents':[{k:refs[i][k] for k in ('id','artifact_id','content_hash')} for i in dict.fromkeys(note['evidence_ids'])],
                'created_at':answer['created_at'],**({'language_context':answer['language_context']} if answer.get('language_context') else {})})
        row['cursors'][thread['id']]={'message_id':answer['id'],'message_hash':digest(answer['text'])}
        row['last_sources']=snapshot.get('sources',[])
        row['position']={'thread_id':thread['id'],'answer_id':answer['id'],'question_id':user['id'],'status':'answer_saved','at':now()}
        saved=self._save(row,db)
        return {'project':thread['project'],'input_revision':snapshot['revision'],'revision':saved['revision'],'binding':snapshot['binding'],'issues':issues,'model_notes_confirmed':False}

    def _save(self,row,db):
        old=self.store.get('research_topic',row['project'],db=db)
        if old:self.store.put('topic_history',row['project']+':'+str(old['revision']),row['project'],old,db=db)
        if not self.store.get('research_format','topic',db=db):
            self.store.put('research_format','topic','',{'schema_version':SCHEMA,'migration':'additive_from_workspace_v1','original_messages_preserved':True},db=db)
        return self.store.put('research_topic',row['project'],row['project'],row,expected=row['revision'],db=db)

    def change(self,project,expected_revision,entry_id,action):
        if action not in {'pause','reject','delete','resolve','resume'}:raise ValueError('TOPIC_ACTION_INVALID')
        with self.store.tx() as db:
            row=self._load(project,db)
            if row['revision']!=expected_revision:raise ValueError('REVISION_CONFLICT')
            entry=next((r for r in row['entries'] if r['id']==entry_id),None)
            if entry is None:raise ValueError('TOPIC_ENTRY_NOT_FOUND')
            if entry['state']=='deleted':raise ValueError('TOPIC_ENTRY_REMOVED')
            if action=='resume':
                if not self._message(entry,project,db,{}):raise ValueError('TOPIC_ORIGINAL_UNAVAILABLE')
                for parent in entry['parents']:
                    self.w.index.read(parent['id'],project,expected_hash=parent['content_hash'],db=db)
                next_state='user_stated' if entry['origin']=='exact_user_statement' else 'proposed'
            else:next_state={'pause':'paused','reject':'rejected','delete':'deleted','resolve':'resolved'}[action]
            if entry['state']==next_state:return row
            entry['state']=next_state
            entry['user_decided_at']=now()
            if action=='delete':entry['text']='';entry['parents']=[]
            result=self._save(row,db)
            self.store.event('TOPIC_USER_'+action.upper(),entry_id,{'project':project,'revision':result['revision']},db)
            return result

    def forget_thread(self,thread_id,project,db):
        # Delete original text from current state and retained topic versions.
        current=self.store.get('research_topic',project,db=db)
        for kind,rows in [('research_topic',[current] if current else []),('topic_history',self.store.list('topic_history',project,db=db))]:
            for row in rows:
                row['entries']=[r for r in row['entries'] if r.get('anchor',{}).get('thread_id')!=thread_id]
                row.get('cursors',{}).pop(thread_id,None)
                if (row.get('position') or {}).get('thread_id')==thread_id:row['position']=None
                self.store.put(kind,row['id'],project,row,db=db)
