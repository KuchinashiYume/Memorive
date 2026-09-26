"""Desktop conversation management. External agents keep the read/propose surface."""
import copy
from .store import now,uid

METHODS=frozenset({'memo.conversation_index','memo.thread_update','memo.thread_duplicate','memo.thread_export'})

class Conversations:
    def __init__(self,workspace):self.workspace=workspace;self.store=workspace.store

    def _get(self,identity,revision,db):
        row=self.store.get('thread',identity,db=db)
        if not row:raise ValueError('THREAD_NOT_FOUND')
        if row['revision']!=revision:raise ValueError('REVISION_CONFLICT')
        if any(j.get('thread_id')==identity and j['status'] in {'RUNNING','QUEUED'} for j in self.store.list('job',row['project'],db=db)):
            raise ValueError('THREAD_BUSY')
        if any(j.get('thread_id')==identity and j['status'] in {'RUNNING','QUEUED'} for j in self.store.list('attachment_job',row['project'],db=db)):
            raise ValueError('ATTACHMENT_BUSY')
        return row

    def index(self,query='',archived=False):
        if not isinstance(query,str) or len(query)>500 or type(archived) is not bool:raise ValueError('SEARCH_INVALID')
        terms=query.casefold().split();rows=[]
        with self.store.tx() as db:
            projects=self.store.list('project',db=db)
            for row in self.store.list('thread',db=db):
                if bool(row.get('archived'))!=archived:continue
                text='\n'.join([row['title']]+[m.get('text','') for m in row['messages']])
                if terms and not all(term in text.casefold() for term in terms):continue
                excerpt=''
                if terms:
                    at=text.casefold().find(terms[0]);excerpt=text[max(0,at-24):at+100].replace('\n',' ')
                rows.append({k:row.get(k) for k in ('id','project','title','temporary','created_at','revision')}|
                    {'pinned':bool(row.get('pinned')),'archived':bool(row.get('archived')),
                     'updated_at':row.get('updated_at') or (row['messages'][-1].get('created_at') if row['messages'] else row['created_at']),
                     'message_count':len(row['messages']),'excerpt':excerpt})
        rows.sort(key=lambda r:(r['pinned'],r['updated_at'],r['id']),reverse=True)
        return {'projects':projects,'threads':rows,'query':query,'archived':archived}

    def update(self,thread_id,expected_revision,changes):
        if not isinstance(changes,dict) or not changes or set(changes)-{'title','pinned','archived'}:raise ValueError('THREAD_FIELDS_INVALID')
        if 'title' in changes and (not isinstance(changes['title'],str) or not changes['title'].strip() or len(changes['title'])>120):raise ValueError('THREAD_TITLE_INVALID')
        if any(type(changes[k]) is not bool for k in ('pinned','archived') if k in changes):raise ValueError('THREAD_FIELDS_INVALID')
        with self.store.tx() as db:
            row=self._get(thread_id,expected_revision,db)
            row.update(changes)
            if 'title' in changes:row.update(title=changes['title'].strip(),title_custom=True)
            result=self.store.put('thread',thread_id,row['project'],row,expected=expected_revision,db=db)
            self.store.event('THREAD_UPDATED',thread_id,{'fields':sorted(changes)},db)
            return result

    def duplicate(self,thread_id,expected_revision):
        with self.store.tx() as db:
            row=copy.deepcopy(self._get(thread_id,expected_revision,db));identity=uid('chat_')
            row.update(title=(row['title'][:110]+' · 副本'),title_custom=True,pinned=False,archived=False,created_at=now(),updated_at=now())
            # Keep the same project and exact citations; never copy project scope or admission.
            result=self.store.put('thread',identity,row['project'],row,expected=0,db=db)
            self.store.event('THREAD_DUPLICATED',identity,{'source_thread':thread_id},db)
            if row['temporary']:self.workspace.temporary_threads.add(identity)
            return result

    def export(self,thread_id):
        row=self.workspace.thread_get(thread_id)
        text='# '+row['title']+'\n\n'
        for m in row['messages']:
            text+='## '+('你' if m['role']=='user' else 'Memorive')+'\n\n'+m['text']+'\n\n'
            for ref in m.get('citations',[]):text+='- '+ref['title']+' · '+ref['id']+'\n'
        return {'text':text,'thread_id':thread_id}

    def call(self,method,params):
        return {'memo.conversation_index':self.index,'memo.thread_update':self.update,
            'memo.thread_duplicate':self.duplicate,'memo.thread_export':self.export}[method](**params)
