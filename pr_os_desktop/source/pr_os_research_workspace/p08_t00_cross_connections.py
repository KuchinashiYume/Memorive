"""Project-scoped desktop companions, resumable intake and managed note exchange."""
import copy,html,json,os,re,time
from pathlib import Path
from urllib.parse import quote,urlparse,unquote
from .store import digest,uid,now,packed
from .evidence import safe_path,read_document,DENY_DIRS
from .p08_t00_cross_bibliography import parse

PROVIDERS={'obsidian','zotero','folder','bibliography'}
FORMATS={'.pdf','.md','.txt'}
DESKTOP=frozenset('memo.connections_state memo.connection_save memo.connection_preview memo.connection_commit memo.connection_retry memo.connection_refresh memo.connection_export memo.connection_queue memo.connection_outputs memo.connection_action_retry'.split())
COMPANION=frozenset('connector.connections connector.state connector.preview connector.commit connector.retry connector.refresh connector.search connector.read connector.outputs connector.writeback_prepare connector.writeback_ack connector.queue connector.chat connector.handoff connector.draft connector.source_changed connector.action_retry'.split())

def limited(value,maximum=1000):
    if not isinstance(value,str) or len(value)>maximum:raise ValueError('CONNECTOR_TEXT_INVALID')
    return value

def immutable_file(path,text):
    data=text.encode('utf-8');path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open('xb') as stream:stream.write(data)
    except FileExistsError:
        if path.read_bytes()!=data:raise ValueError('IMMUTABLE_SOURCE_CONFLICT')
    return path

def process_identity(pid):
    """Read-only process identity; never signal or terminate another process."""
    if os.name!='nt':
        try:os.kill(pid,0);return str(pid)
        except ProcessLookupError:return None
        except PermissionError:return 'unknown'
    import ctypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.GetProcessTimes.argtypes=[ctypes.c_void_p]+[ctypes.c_void_p]*4
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return 'unknown' if ctypes.get_last_error()==5 else None
    try:
        values=[ctypes.c_uint64() for _ in range(4)]
        if not kernel.GetProcessTimes(handle,*[ctypes.byref(v) for v in values]):return 'unknown'
        return str(values[0].value)
    finally:kernel.CloseHandle(handle)

class Connections:
    def __init__(self,workspace):self.ws=workspace;self.store=workspace.store;self.processing_submit=None;self.processing_status=None;self.processing_retry=None

    def connection(self,connection_id,enabled=True,db=None):
        row=self.store.get('connection',connection_id,db=db)
        if not row:raise ValueError('CONNECTION_NOT_FOUND')
        if enabled and not row['enabled']:raise ValueError('CONNECTION_DISABLED')
        return row

    def save(self,project,name,provider,root,connection_id=None,expected_revision=0,enabled=True,writeback=False):
        if not self.store.get('project',project):raise ValueError('PROJECT_NOT_FOUND')
        if provider not in PROVIDERS or type(enabled) is not bool or type(writeback) is not bool:raise ValueError('CONNECTION_INVALID')
        p=Path(root).resolve(strict=True)
        if not p.is_dir() or p.parent==p or p.name.startswith('.'):raise ValueError('CONNECTION_ROOT_INVALID')
        name=limited(name,160).strip()
        if not name:raise ValueError('CONNECTION_NAME_REQUIRED')
        identity=connection_id or uid('conn_');old=self.store.get('connection',identity)
        if old and (old['root']!=str(p) or old['project']!=project or old['provider']!=provider):raise ValueError('CONNECTION_SCOPE_IMMUTABLE_CREATE_NEW')
        return self.store.put('connection',identity,project,{'project':project,'name':name,'provider':provider,'root':str(p),
            'enabled':enabled,'writeback':writeback,'created_at':(old or {}).get('created_at',now()),
            'capabilities':{'selected_files':True,'bibliography':True,'annotations':provider=='zotero',
                'managed_note_writeback':provider in {'obsidian','zotero'},'file_export':True,'change_watch':'explicit_selected_sources',
                'short_chat':'memo_desktop_queue','scientific_approval':False}},expected=expected_revision)

    def state(self,project='default',connection_id=None):
        if not self.store.get('project',project):raise ValueError('PROJECT_NOT_FOUND')
        connections=self.store.list('connection',project)
        if connection_id:
            c=self.connection(connection_id,False)
            if c['project']!=project:raise ValueError('PROJECT_SCOPE_MISMATCH')
            connections=[c]
        accepted={c['id'] for c in connections}
        for j in self.store.list('connector_job',project):
            if j['connection_id'] not in accepted or j['status']!='RUNNING' or not j.get('owner_pid'):continue
            identity=process_identity(j['owner_pid'])
            if identity!='unknown' and identity!=j.get('owner_identity'):
                with self.store.tx() as db:
                    current=self.store.get('connector_job',j['id'],db=db)
                    if current['status']=='RUNNING' and current.get('owner_identity')==j.get('owner_identity'):
                        self.store.put('connector_job',j['id'],project,dict(current,status='INTERRUPTED'),db=db)
        return {'connections':connections,'documents':[r for r in self.store.list('connector_document',project) if r['connection_id'] in accepted],
            'jobs':[r for r in self.store.list('connector_job',project) if r['connection_id'] in accepted][:60],
            'actions':[r for r in self.store.list('connector_action',project) if r['connection_id'] in accepted][:40],
            'desktop_online':time.time()-float((self.store.get('connector_host','desktop') or {}).get('heartbeat',0))<8,
            'writebacks':[r for r in self.store.list('connector_writeback',project) if r['connection_id'] in accepted]}

    def _path(self,c,path):
        p=Path(path)
        if not p.is_absolute():p=Path(c['root'])/p
        return safe_path(c['root'],p)

    def _item(self,c,value):
        if not isinstance(value,dict):raise ValueError('CONNECTOR_ITEM_INVALID')
        allowed={'source_key','title','authors','year','doi','abstract','path','item_key','attachment_key','library_id','version','annotations','warnings','attachment_suggestions'}
        if set(value)-allowed:raise ValueError('CONNECTOR_ITEM_FIELDS_INVALID')
        row={k:copy.deepcopy(v) for k,v in value.items() if k in allowed}
        row['title']=limited(row.get('title',''),1000).strip()
        if not row['title']:raise ValueError('CONNECTOR_TITLE_REQUIRED')
        row['source_key']=limited(str(row.get('source_key') or row.get('item_key') or digest(row)[:24]),200)
        for field,maxlen in [('abstract',40000),('doi',300),('year',40)]:row[field]=limited(row.get(field,''),maxlen)
        if not isinstance(row.get('authors',[]),list) or len(row.get('authors',[]))>100:raise ValueError('CONNECTOR_AUTHORS_INVALID')
        row['authors']=[limited(v,500) for v in row.get('authors',[])]
        if c['provider']=='zotero':
            for key in ('item_key','attachment_key'):
                if row.get(key) and not re.fullmatch('[A-Z0-9]{8}',row[key]):raise ValueError('ZOTERO_KEY_INVALID')
            row['library_id']=str(row.get('library_id','1'))
            if not row['library_id'].isdigit():raise ValueError('ZOTERO_LIBRARY_INVALID')
        annotations=row.get('annotations',[])
        if not isinstance(annotations,list) or len(annotations)>500:raise ValueError('ANNOTATIONS_INVALID')
        row['annotations']=[]
        for a in annotations:
            if not isinstance(a,dict) or set(a)-{'id','text','comment','page'}:raise ValueError('ANNOTATION_INVALID')
            page=a.get('page')
            if page is not None and (type(page) is not int or not 1<=page<=50000):raise ValueError('ANNOTATION_PAGE_INVALID')
            row['annotations'].append({'id':limited(a.get('id',''),200),'text':limited(a.get('text',''),8000),'comment':limited(a.get('comment',''),8000),'page':page})
        row['file_hash']=None;row['path']=row.get('path') or None
        if row['path']:
            p=self._path(c,row['path'])
            if p.suffix.lower() not in FORMATS:raise ValueError('CONNECTOR_FILE_FORMAT_UNSUPPORTED')
            if p.stat().st_size>24*1024*1024:raise ValueError('SOURCE_TOO_LARGE')
            row['path']=str(p);row['file_hash']=digest(p.read_bytes())
        row['warnings']=list(row.get('warnings',[]))[:20]
        if not row['path']:row['warnings'].append('METADATA_ONLY_ATTACH_PDF_TO_PROCESS')
        row['source_id']='src_'+digest([c['id'],row['source_key']])[:28]
        row['fingerprint']=digest({k:v for k,v in row.items() if k not in {'warnings','fingerprint'}})
        previous=self.store.get('connector_document',row['source_id'])
        row['change']='unchanged' if previous and previous['fingerprint']==row['fingerprint'] and previous['state']=='active' else ('updated' if previous else 'new')
        return row

    def preview(self,connection_id,paths=None,items=None,bibliography=None,attachment_map=None):
        c=self.connection(connection_id);values=[];bindings=[]
        if bibliography:
            p=self._path(c,bibliography);values=parse(p);bindings.append({'path':str(p),'hash':digest(p.read_bytes())})
            mappings=attachment_map or {}
            for v in values:
                if v['source_key'] in mappings:v['path']=mappings[v['source_key']]
        if paths:
            if not isinstance(paths,list):raise ValueError('CONNECTOR_PATHS_INVALID')
            for value in paths:
                p=self._path(c,value);values.append({'title':p.stem,'source_key':str(p.relative_to(c['root'])),'path':str(p)})
        if items:
            if not isinstance(items,list):raise ValueError('CONNECTOR_ITEMS_INVALID')
            values.extend(items)
        if not 1<=len(values)<=2000:raise ValueError('CONNECTOR_SELECTION_REQUIRED')
        rows=[self._item(c,v) for v in values]
        if len({v['source_id'] for v in rows})!=len(rows):raise ValueError('CONNECTOR_DUPLICATE_SELECTION')
        return self.store.put('connector_preview',uid('preview_'),c['project'],{'connection_id':c['id'],'project':c['project'],
            'connection_revision':c['revision'],'items':rows,'bindings':bindings,'created_at':now(),'preview_hash':digest(rows),
            'counts':{kind:sum(r['change']==kind for r in rows) for kind in ('new','updated','unchanged')},'external_calls':0})

    def commit(self,preview_id,preview_hash,selected_ids=None):
        preview=self.store.get('connector_preview',preview_id)
        if not preview or preview_hash!=preview['preview_hash']:raise ValueError('PREVIEW_CONFLICT')
        c=self.connection(preview['connection_id'])
        if c['revision']!=preview['connection_revision']:raise ValueError('CONNECTION_CHANGED_REPREVIEW')
        selected=set(selected_ids) if selected_ids is not None else {r['source_id'] for r in preview['items']}
        if not selected or selected-{r['source_id'] for r in preview['items']}:raise ValueError('PREVIEW_SELECTION_INVALID')
        identity='intake_'+digest([preview_id,sorted(selected)])[:28]
        with self.store.tx() as db:
            existing=self.store.get('connector_job',identity,db=db)
            if existing:return existing
            for b in preview['bindings']:
                if digest(self._path(c,b['path']).read_bytes())!=b['hash']:raise ValueError('PREVIEW_SOURCE_CHANGED')
            job=self.store.put('connector_job',identity,c['project'],{'connection_id':c['id'],'project':c['project'],'preview_id':preview_id,
                'created_at':now(),'status':'QUEUED','external_calls':0,'api_cost':0,'currency':'USD',
                'steps':[{'source_id':r['source_id'],'status':'QUEUED','attempts':0} for r in preview['items'] if r['source_id'] in selected]},db=db)
        return self._run(job,c,preview)

    def _ingest(self,c,row):
        if row['path']:
            p=self._path(c,row['path'])
            if digest(p.read_bytes())!=row['file_hash']:raise ValueError('PREVIEW_SOURCE_CHANGED')
        old=self.store.get('connector_document',row['source_id'])
        if old and old['fingerprint']==row['fingerprint'] and old['state']=='active':return old
        related=next((d for d in self.store.list('connector_document',c['project']) if row['file_hash'] and d.get('file_hash')==row['file_hash']),None)
        document_id=(old or related or {}).get('document_id') or uid('doc_')
        text='# '+row['title']+'\n\n'+'; '.join(row['authors'])+'\n'+row['year']+'\nDOI: '+row['doi']+'\n\n'+row['abstract']
        for a in row['annotations']:text+='\n\n## 批注 · '+str(a['page'] or '页码未知')+'\n'+a['text']+'\n个人批注：'+a['comment']
        meta=immutable_file(self.store.root/'connector_sources'/row['source_id']/(row['fingerprint']+'.md'),text)
        ids=[];needs_ocr=False
        if row['path']:
            try:
                artifact=self.ws.index.add_library_file(c['project'],path=row['path'],root=c['root'],external_id=document_id+':'+c['id'],expected_hash=row['file_hash'],title=row['title'])
                ids.append(artifact['id'])
            except ValueError as exc:
                if str(exc)!='SOURCE_REQUIRES_EXISTING_OCR':raise
                needs_ocr=True
        artifact=self.ws.index.add_library_file(c['project'],path=meta,root=self.store.root,external_id=row['source_id']+':metadata',expected_hash=digest(meta.read_bytes()),title=row['title']+' · 书目与批注')
        ids.append(artifact['id'])
        binding={k:row.get(k) for k in ('item_key','attachment_key','library_id','version')}
        binding.update(provider=c['provider'],connection_id=c['id'],document_id=document_id,source_id=row['source_id'],vault_name=Path(c['root']).name,
            relative_path=str(Path(row['path']).relative_to(c['root'])) if row['path'] else None)
        with self.store.tx() as db:
            for identity in ids:
                a=self.store.get('artifact',identity,db=db)
                self.store.put('artifact',identity,c['project'],dict(a,source_binding=binding),db=db)
            value=dict(row,connection_id=c['id'],project=c['project'],document_id=document_id,state='active',artifact_ids=ids,
                needs_ocr=needs_ocr,processing=(old or {}).get('processing') if old and old.get('file_hash')==row['file_hash'] else None,updated_at=now())
            return self.store.put('connector_document',row['source_id'],c['project'],value,db=db)

    def _run(self,job,c,preview):
        started=time.monotonic();rows={r['source_id']:r for r in preview['items']}
        with self.store.tx() as db:
            fresh=self.store.get('connector_job',job['id'],db=db)
            if fresh['status']=='RUNNING':raise ValueError('CONNECTOR_JOB_BUSY')
            job=self.store.put('connector_job',job['id'],job['project'],dict(fresh,status='RUNNING',owner_pid=os.getpid(),owner_identity=process_identity(os.getpid())),db=db)
        for step in job['steps']:
            if step['status']=='COMPLETE':continue
            step['attempts']+=1;step['started_at']=now()
            try:
                value=self._ingest(c,rows[step['source_id']]);step.update(status='COMPLETE',document_id=value['document_id'],error=None)
            except (ValueError,OSError,UnicodeError) as exc:step.update(status='ERROR',error=str(exc)[:200])
            job=self.store.put('connector_job',job['id'],job['project'],job)
        errors=sum(s['status']=='ERROR' for s in job['steps'])
        job.update(status='PARTIAL' if 0<errors<len(job['steps']) else ('ERROR' if errors else 'COMPLETE'),
            elapsed_ms=job.get('elapsed_ms',0)+round((time.monotonic()-started)*1000),completed_at=now())
        return self.store.put('connector_job',job['id'],job['project'],job)

    def retry(self,job_id):
        job=self.store.get('connector_job',job_id)
        if not job:raise ValueError('CONNECTOR_JOB_NOT_FOUND')
        if job['status'] not in {'ERROR','PARTIAL','INTERRUPTED'}:raise ValueError('CONNECTOR_RETRY_STATE_INVALID')
        return self._run(job,self.connection(job['connection_id']),self.store.get('connector_preview',job['preview_id']))

    def refresh(self,connection_id):
        c=self.connection(connection_id);changed=[];missing=[]
        for d in self.store.list('connector_document',c['project']):
            if d['connection_id']!=c['id'] or not d['path']:continue
            try:current=digest(self._path(c,d['path']).read_bytes())
            except (OSError,ValueError):current=None
            if current!=d['file_hash']:
                with self.store.tx() as db:
                    self.store.put('connector_document',d['id'],c['project'],dict(d,state='changed' if current else 'unavailable'),db=db)
                    for identity in d['artifact_ids']:
                        a=self.store.get('artifact',identity,db=db)
                        if a:self.store.put('artifact',identity,c['project'],dict(a,state='unavailable'),db=db)
                (changed if current else missing).append(d['id'])
        return {'status':'REFRESHED','changed':changed,'missing':missing,'action':'select_changed_sources_to_preview','external_calls':0}

    def document(self,c,source_id):
        d=self.store.get('connector_document',source_id)
        if not d or d['connection_id']!=c['id']:raise ValueError('DOCUMENT_SCOPE_MISMATCH')
        return d

    def outputs(self,connection_id,source_id):
        c=self.connection(connection_id);d=self.document(c,source_id)
        if d['state']!='active':raise ValueError('DOCUMENT_SOURCE_CHANGED')
        if d['path'] and digest(self._path(c,d['path']).read_bytes())!=d['file_hash']:raise ValueError('DOCUMENT_SOURCE_CHANGED')
        items=[{'id':'source-summary','title':'书目与来源','kind':'SOURCE_SUMMARY','markdown':'# '+d['title']+'\n\n'+d['abstract']+'\n\nDOI: '+d['doi']+'\n\n资料已索引；此条为来源说明。'}]
        if self.processing_status:
            status=self.processing_status(d)
            if status:
                d=self.store.put('connector_document',d['id'],c['project'],dict(d,processing=status.get('processing',d.get('processing')),
                    result_items=status.get('results',d.get('result_items',[]))))
        items.extend(d.get('result_items',[]))
        for item in items:
            if item.get('source_path') and digest(Path(item['source_path']).read_bytes())!=item['source_hash']:raise ValueError('RESULT_SOURCE_CHANGED')
        for r in self.store.list('feedback',c['project']):
            if r.get('state') in {'retracted','RETRACTED'}:continue
            refs=r.get('parents',[])
            if any(ref.get('artifact_id') in d['artifact_ids'] for ref in refs):
                items.append({'id':r['id'],'title':r.get('title','研究草稿'),'kind':'KNOWLEDGE_'+str(r.get('state','DRAFT')),'markdown':r.get('claim','')+'\n\n适用范围：'+r.get('scope','')+'\n\n局限：'+r.get('limitations','')})
        return {'document':d,'items':items}

    def _body(self,c,source_id,output_ids):
        result=self.outputs(c['id'],source_id);d=result['document'];wanted=set(output_ids or ['source-summary'])
        items=[i for i in result['items'] if i['id'] in wanted]
        if not items or wanted-{i['id'] for i in items}:raise ValueError('OUTPUT_SELECTION_INVALID')
        body='\n\n'.join('## '+i['title']+' · '+i['kind']+'\n\n'+i['markdown'] for i in items)
        body+='\n\n来源版本：'+str(d['file_hash'] or d['fingerprint'])+'\n'
        if c['provider']=='zotero' and d.get('attachment_key'):body+='\n[打开原文](zotero://open-pdf/library/items/'+d['attachment_key']+')\n'
        body=re.sub(r'<!--\s*memo:', '<!-- quoted-memo:',body,flags=re.I)
        return d,body

    def writeback_prepare(self,connection_id,source_id,external_note_id,existing_text='',output_ids=None,format='markdown'):
        c=self.connection(connection_id)
        if not c['writeback']:raise ValueError('WRITEBACK_DISABLED')
        existing_text=limited(existing_text,2*1024*1024);external_note_id=limited(external_note_id,1000)
        d,body=self._body(c,source_id,output_ids)
        if format not in {'markdown','html'} or (format=='html' and c['provider']!='zotero'):raise ValueError('WRITEBACK_FORMAT_INVALID')
        if format=='html':body='<div><pre>'+html.escape(body)+'</pre></div>'
        begin='<!-- memo:'+source_id+':start -->';end='<!-- memo:'+source_id+':end -->'
        identity='wb_'+digest([c['id'],source_id,external_note_id])[:28];old=self.store.get('connector_writeback',identity)
        if existing_text.count(begin)!=existing_text.count(end) or existing_text.count(begin)>1:raise ValueError('MANAGED_BLOCK_INVALID')
        match=re.search(re.escape(begin)+r'(.*?)'+re.escape(end),existing_text,re.S)
        recovered=next((p for p in self.store.list('connector_writeback_plan',c['project']) if p['writeback_id']==identity and p['text_hash']==digest(existing_text)),None)
        if match and (not old or digest(match.group(1))!=old['managed_hash']) and not recovered:
            return {'status':'CONFLICT','reason':'USER_EDITED_MANAGED_BLOCK','proposed_markdown':body,'existing_text':existing_text,'suggested_action':'export_new_note'}
        if old and not match:return {'status':'CONFLICT','reason':'MANAGED_BLOCK_REMOVED','proposed_markdown':body,'suggested_action':'export_new_note'}
        block=begin+'\n'+body+'\n'+end
        merged=existing_text[:match.start()]+block+existing_text[match.end():] if match else existing_text+('\n\n' if existing_text else '')+block+'\n'
        value={'connection_id':c['id'],'project':c['project'],'source_id':source_id,'external_note_id':external_note_id,'writeback_id':identity,
            'expected_text_hash':digest(existing_text),'text':merged,'managed_hash':digest('\n'+body+'\n'),'text_hash':digest(merged),
            'output_ids':output_ids or ['source-summary'],'source_fingerprint':d['fingerprint'],'created_at':now(),'status':'PREPARED','format':format}
        return self.store.put('connector_writeback_plan',uid('wbplan_'),c['project'],value)

    def writeback_ack(self,connection_id,plan_id,text_hash,host_text=None,host_note_id=None):
        c=self.connection(connection_id);p=self.store.get('connector_writeback_plan',plan_id)
        if not p or p['connection_id']!=c['id'] or p['text_hash']!=text_hash:raise ValueError('WRITEBACK_RECEIPT_INVALID')
        d=self.document(c,p['source_id'])
        self.outputs(c['id'],d['id'])
        if d['fingerprint']!=p['source_fingerprint']:raise ValueError('WRITEBACK_SOURCE_CHANGED')
        if host_text is not None:
            if c['provider']!='zotero' or p.get('format')!='html' or not re.fullmatch('[A-Z0-9]{8}',host_note_id or ''):raise ValueError('WRITEBACK_HOST_INVALID')
            limited(host_text,2*1024*1024)
            begin='<!-- memo:'+d['id']+':start -->';end='<!-- memo:'+d['id']+':end -->'
            match=re.search(re.escape(begin)+r'(.*?)'+re.escape(end),host_text,re.S)
            if not match or digest(match.group(1))!=p['managed_hash']:raise ValueError('WRITEBACK_HOST_NORMALIZATION_CONFLICT')
            p=dict(p,external_note_id=host_note_id,writeback_id='wb_'+digest([c['id'],d['id'],host_note_id])[:28],text_hash=digest(host_text))
        return self.store.put('connector_writeback',p['writeback_id'],c['project'],{k:v for k,v in dict(p,status='SYNCED',completed_at=now()).items() if k not in {'text','expected_text_hash'}})

    def export(self,connection_id,source_id,output_ids=None):
        c=self.connection(connection_id);d,body=self._body(c,source_id,output_ids)
        path=self.store.root/'connector_exports'/c['id']/(source_id+'-'+digest(body)[:12]+'.md')
        immutable_file(path,body)
        return {'status':'EXPORTED','path':str(path),'sha256':digest(path.read_bytes()),'original_notes_modified':False}

    def queue(self,connection_id,kind,source_id=None,question='',thread_id=None,request_id=None,client_id=None,grant_revision=None):
        c=self.connection(connection_id)
        if kind not in {'ask','agent','process','process_retry'}:raise ValueError('CONNECTOR_ACTION_INVALID')
        if kind in {'ask','agent'} and not limited(question,8000).strip():raise ValueError('QUESTION_INVALID')
        d=self.document(c,source_id) if source_id else None
        if kind in {'process','process_retry'} and (not d or not d['path']):raise ValueError('ATTACHMENT_REQUIRED')
        if thread_id and self.ws.thread_get(thread_id)['project']!=c['project']:raise ValueError('PROJECT_SCOPE_MISMATCH')
        if client_id:self.ws.developer.authorize_action(client_id,c['id'],kind,grant_revision)
        key=limited(request_id or uid('request_'),120);identity='action_'+digest([c['id'],key,client_id] if client_id else [c['id'],key])[:28]
        payload={'connection_id':c['id'],'project':c['project'],'kind':kind,'source_id':source_id,'question':question,'thread_id':thread_id}
        if client_id:payload.update(client_id=client_id,grant_revision=grant_revision)
        with self.store.tx() as db:
            old=self.store.get('connector_action',identity,db=db)
            if old:
                if old['request_hash']!=digest(payload):raise ValueError('IDEMPOTENCY_CONFLICT')
                return old
            if client_id:
                settings=self.store.get('developer_settings','automation',db=db) or {}
                count=sum(1 for a in self.store.list('connector_action',db=db) if a.get('client_id')==client_id and a['status'] in {'QUEUED','RUNNING'})
                if count>=settings.get('max_queued',20):raise ValueError('AUTOMATION_QUEUE_FULL')
            return self.store.put('connector_action',identity,c['project'],dict(payload,request_hash=digest(payload),status='QUEUED',created_at=now()),db=db)

    def action_retry(self,action_id):
        with self.store.tx() as db:
            a=self.store.get('connector_action',action_id,db=db)
            if not a or a['status'] not in {'ERROR','INTERRUPTED'}:raise ValueError('CONNECTOR_RETRY_STATE_INVALID')
            self.connection(a['connection_id'],db=db)
            return self.store.put('connector_action',a['id'],a['project'],dict(a,status='QUEUED',error=None,retry_at=now()),db=db)

    def source_changed(self,connection_id,source_ids):
        c=self.connection(connection_id)
        if not isinstance(source_ids,list) or len(source_ids)>2000:raise ValueError('SOURCE_IDS_INVALID')
        docs=[self.document(c,s) for s in source_ids]
        with self.store.tx() as db:
            for d in docs:
                self.store.put('connector_document',d['id'],c['project'],dict(d,state='changed'),db=db)
                for identity in d['artifact_ids']:
                    a=self.store.get('artifact',identity,db=db)
                    if a:self.store.put('artifact',identity,c['project'],dict(a,state='unavailable'),db=db)
        return {'status':'CHANGED','source_ids':source_ids}

    def pump(self):
        self.store.put('connector_host','desktop','',{'heartbeat':time.time()})
        for action in self.store.list('connector_action'):
            if action['status']!='QUEUED':continue
            with self.store.tx() as db:
                fresh=self.store.get('connector_action',action['id'],db=db)
                if fresh['status']!='QUEUED':continue
                self.store.put('connector_action',action['id'],action['project'],dict(fresh,status='RUNNING'),db=db)
            try:
                if action.get('client_id'):self.ws.developer.authorize_action(action['client_id'],action['connection_id'],action['kind'],action.get('grant_revision'))
                c=self.connection(action['connection_id']);d=self.document(c,action['source_id']) if action['source_id'] else None
                if action['kind'] in {'ask','agent'}:
                    thread_id=action['thread_id']
                    if not thread_id:
                        thread=self.ws.thread_create(c['project']);thread_id=thread['id']
                        # The companion's new chat explicitly searches its selected project.
                        self.ws.attachments.configure(thread_id,thread['revision'],{'artifact_ids':d['artifact_ids']} if d else {'include_project':True})
                    if action['kind']=='agent':job=self.ws.agent_dispatch(thread_id,action['question'],action['id'])
                    else:job=self.ws.ask(thread_id,action['question'],action['id'],artifact_ids=d['artifact_ids'] if d else None)
                    result={'thread_id':thread_id,'job_id':job['id']}
                else:
                    if not self.processing_submit:raise ValueError('MEMO_DESKTOP_PROCESSOR_REQUIRED')
                    self.outputs(c['id'],d['id'])
                    if action['kind']=='process_retry':
                        if not self.processing_retry:raise ValueError('MEMO_DESKTOP_PROCESSOR_REQUIRED')
                        result=self.processing_retry(d,action['id'])
                    else:result=self.processing_submit(d,action['id'])
                    self.store.put('connector_document',d['id'],d['project'],dict(d,processing=result))
                self.store.put('connector_action',action['id'],action['project'],dict(action,status='DISPATCHED',result=result,dispatched_at=now()))
            except (ValueError,OSError,RuntimeError) as exc:self.store.put('connector_action',action['id'],action['project'],dict(action,status='ERROR',error=str(exc)[:200]))

    def companion(self,method,params):
        if method not in COMPANION:raise ValueError('CONNECTOR_METHOD_NOT_ALLOWED')
        p=dict(params)
        if method=='connector.connections':return {'connections':self.store.list('connection',p.get('project','default'))}
        c=self.connection(p.pop('connection_id'))
        if method=='connector.state':return self.state(c['project'],c['id'])
        if method=='connector.preview':return self.preview(c['id'],**p)
        if method=='connector.commit':
            preview=self.store.get('connector_preview',p.get('preview_id',''))
            if not preview or preview['connection_id']!=c['id']:raise ValueError('PREVIEW_SCOPE_MISMATCH')
            return self.commit(**p)
        if method=='connector.retry':
            job=self.store.get('connector_job',p.get('job_id',''))
            if not job or job['connection_id']!=c['id']:raise ValueError('JOB_SCOPE_MISMATCH')
            return self.retry(**p)
        if method=='connector.refresh':return self.refresh(c['id'])
        if method=='connector.source_changed':return self.source_changed(c['id'],**p)
        if method=='connector.action_retry':
            a=self.store.get('connector_action',p.get('action_id',''))
            if not a or a['connection_id']!=c['id']:raise ValueError('JOB_SCOPE_MISMATCH')
            return self.action_retry(**p)
        if method=='connector.search':return self.ws.index.search(project=c['project'],**p)
        if method=='connector.read':return self.ws.index.read(project=c['project'],**p)
        if method=='connector.outputs':return self.outputs(c['id'],**p)
        if method=='connector.writeback_prepare':return self.writeback_prepare(c['id'],**p)
        if method=='connector.writeback_ack':return self.writeback_ack(c['id'],**p)
        if method=='connector.queue':return self.queue(c['id'],**p)
        if method=='connector.chat':
            if 'thread_id' in p:
                t=self.ws.thread_get(p['thread_id'])
                if t['project']!=c['project']:raise ValueError('PROJECT_SCOPE_MISMATCH')
                return t
            return {'threads':self.store.list('thread',c['project'])}
        if method=='connector.handoff':
            if self.ws.thread_get(p['thread_id'])['project']!=c['project']:raise ValueError('PROJECT_SCOPE_MISMATCH')
            value=self.ws.handoff(**p);return dict(value,export=self.ws.export_handoff(value['id']))
        if method=='connector.draft':return self.ws.submit_draft(project=c['project'],**p)

    def desktop(self,method,p):
        methods={'memo.connections_state':self.state,'memo.connection_save':self.save,'memo.connection_preview':self.preview,
            'memo.connection_commit':self.commit,'memo.connection_retry':self.retry,'memo.connection_refresh':self.refresh,
            'memo.connection_export':self.export,'memo.connection_queue':self.queue,'memo.connection_outputs':self.outputs,
            'memo.connection_action_retry':self.action_retry}
        return methods[method](**p)
