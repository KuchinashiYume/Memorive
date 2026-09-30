"""Desktop conversation management. External agents keep the read/propose surface."""
import copy
from .store import now,uid
from . import answer_versions
from .conversation_lifecycle import ConversationLifecycle

METHODS=frozenset({'memo.conversation_index','memo.thread_update','memo.thread_duplicate','memo.thread_export','memo.thread_view'})

class Conversations(ConversationLifecycle):
    def __init__(self,workspace):self.workspace=workspace;self.store=workspace.store;self.init_lifecycle()

    def _get(self,identity,revision,db):
        row=self.store.get('thread',identity,db=db)
        if not row:raise ValueError('THREAD_NOT_FOUND')
        if row['revision']!=revision:raise ValueError('REVISION_CONFLICT')
        busy=self.busy(db,identity)
        if busy:raise ValueError('ATTACHMENT_BUSY' if busy['kind']=='attachment_job' else 'THREAD_BUSY')
        return row

    def index(self,query='',archived=False,cursor=None,limit=50,project=None):
        return self.page(query,archived,cursor,limit,project)

    def update(self,thread_id,expected_revision,changes):
        if not isinstance(changes,dict) or not changes or set(changes)-{'title','pinned','archived'}:raise ValueError('THREAD_FIELDS_INVALID')
        if 'title' in changes and (not isinstance(changes['title'],str) or not changes['title'].strip() or len(changes['title'])>120):raise ValueError('THREAD_TITLE_INVALID')
        if any(type(changes[k]) is not bool for k in ('pinned','archived') if k in changes):raise ValueError('THREAD_FIELDS_INVALID')
        with self.store.tx() as db:
            if set(changes)=={'archived'}:
                self.set_archive(db,thread_id,changes['archived'],expected=expected_revision)
                return answer_versions.project(self.store.get('thread',thread_id,db=db))
            row=self._get(thread_id,expected_revision,db)
            row.update({k:v for k,v in changes.items() if k!='archived'})
            if 'title' in changes:row.update(title=changes['title'].strip(),title_custom=True)
            result=self.store.put('thread',thread_id,row['project'],row,expected=expected_revision,db=db)
            if 'archived' in changes:
                self.set_archive(db,thread_id,changes['archived'],expected=result['revision'])
                result=self.store.get('thread',thread_id,db=db)
            self.store.event('THREAD_UPDATED',thread_id,{'fields':sorted(changes)},db)
            return answer_versions.project(result)

    def duplicate(self,thread_id,expected_revision):
        with self.store.tx() as db:
            row=copy.deepcopy(self._get(thread_id,expected_revision,db));identity=uid('chat_')
            answer_versions.normalize(row)
            mapping={m['id']:uid('msg_') for m in row['messages']}
            for message in row['messages']:
                message['copied_from_message_id']=message['id'];message['id']=mapping[message['id']]
                for key in ['question_id','parent_answer_id','e1_target_answer_id']:
                    if message.get(key) in mapping:message[key]=mapping[message[key]]
                # A copied E1 review keeps its evidence but its original binding
                # cannot certify the new thread/message identity.
                if message.get('review_binding'):message['review_binding']=None
            row['selected_answers']={mapping[q]:mapping[a] for q,a in row['selected_answers'].items()}
            row['legacy_message_parents']={mapping[mid]:mapping.get(parent) for mid,parent in row['legacy_message_parents'].items()}
            row['selection_revision']=0;row['summary']=[];row['summary_ids']=[]
            row.update(title=(row['title'][:110]+' · 副本'),title_custom=True,pinned=False,archived=False,created_at=now(),updated_at=now())
            # Keep the same project and exact citations; never copy project scope or admission.
            result=self.store.put('thread',identity,row['project'],row,expected=0,db=db)
            self.store.event('THREAD_DUPLICATED',identity,{'source_thread':thread_id},db)
            if row['temporary']:self.workspace.temporary_threads.add(identity)
            return answer_versions.project(result)

    def export(self,thread_id):
        row=self.workspace.thread_get(thread_id)
        from memorive_language import freeze
        from memorive_language.text import choose
        language=freeze(self.workspace.language_settings_get())
        text='# '+row['title']+'\n\n'
        for m in row['messages']:
            text+='## '+(choose(language,'你','You','あなた') if m['role']=='user' else 'Memorive')+'\n\n'+m['text']+'\n\n'
            for ref in m.get('citations',[]):text+='- '+ref['title']+' · '+ref['id']+'\n'
        return {'text':text,'thread_id':thread_id}

    def call(self,method,params):
        return {'memo.conversation_index':self.index,'memo.thread_update':self.update,
            'memo.thread_duplicate':self.duplicate,'memo.thread_export':self.export,'memo.thread_view':self.view}[method](**params)
