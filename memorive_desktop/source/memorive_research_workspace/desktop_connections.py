"""Use the existing inbox/Core lifecycle; only the desktop executes model jobs."""
import hashlib,json,threading
from pathlib import Path
from .store import digest,now

def bind(workspace,api):
    def submit(document,request_id):
        existing=document.get('processing') or {}
        if existing.get('item_id'):return existing
        identity='stage_'+digest([document['document_id'],document['file_hash']])[:28]
        staged=workspace.store.get('connector_stage',identity)
        if not staged:
            result=api.stage_inbox_paths([document['path']])
            staged=workspace.store.put('connector_stage',identity,document['project'],result)
        batch=api.call('inbox.import_paths',{'paths':staged['paths'],'request_id':identity,'source_kind':'picker'})
        item=batch['items'][0]['item']
        if item['state']=='DEDUPLICATED':item=api.call('inbox.item_detail',{'item_id':item['duplicate_of']})
        if item.get('state')=='ERROR':raise ValueError('INBOX_IMPORT_FAILED:'+str(item.get('error'))[:120])
        if not item.get('job_id') and item.get('state')=='QUEUED':
            api.call('inbox.dispatch',{'item_id':item['item_id'],'idempotency_key':identity})
        return {'item_id':item['item_id'],'job_id':item.get('job_id'),'state':item['state'],'submitted_at':now(),
            'route':'inbox','execution':'existing_mainline_with_saved_settings'}

    def status(document):
        previous=document.get('processing')
        if not previous:return None
        item=api.call('inbox.item_detail',{'item_id':previous['item_id']});job_id=item.get('job_id');rows=[]
        if job_id:
            api._library._sync_core_artifacts()
            for identity,entry in api._library._core_entries_by_artifact_id.items():
                if entry.get('job_id')!=job_id or entry['artifact_kind'] not in {'CORE_CARD','CORE_ANALYSIS'}:continue
                p=api._library.resolve_core_private_artifact(identity)
                raw=Path(p).read_bytes()
                if hashlib.sha256(raw).hexdigest().upper()!=entry['content_sha256'].upper():raise ValueError('RESULT_SOURCE_CHANGED')
                text=raw.decode('utf-8-sig')
                if Path(p).suffix.lower()=='.json':text='```json\n'+json.dumps(json.loads(text),ensure_ascii=False,indent=2)+'\n```'
                rows.append({'id':identity,'title':entry['row']['display_name'],'kind':entry['artifact_kind'],'markdown':text,
                    'source_path':str(p),'source_hash':digest(raw)})
        control_state=None
        if job_id:
            job=api._service.call('get_job',{'job_id':job_id})
            control_state=job.get('control_state',job.get('job_control_state'))
        return {'processing':dict(previous,job_id=job_id,state=item['state'],control_state=control_state,error=item.get('error'),last_observed_at=now()),'results':rows}

    def retry(document,request_id):
        current=status(document)
        previous=(current or {}).get('processing') or {}
        if not previous.get('job_id'):raise ValueError('CURRENT_TASK_REQUIRED')
        api.call('current_task.retry',{'job_id':previous['job_id'],'request_id':request_id})
        return dict(previous,state='RETRY_REQUESTED',retry_scope='failed_node',retry_requested_at=now())

    workspace.connections.processing_submit=submit;workspace.connections.processing_status=status;workspace.connections.processing_retry=retry
    with workspace.store.tx() as db:
        for a in workspace.store.list('connector_action',db=db):
            if a['status']=='RUNNING':workspace.store.put('connector_action',a['id'],a['project'],dict(a,status='INTERRUPTED',error='Desktop restarted; review current task before retry'),db=db)
    def pump():
        while not workspace.connector_stop.is_set():
            try:
                workspace.conversations.maintain()
                workspace.connections.pump()
                for d in workspace.store.list('connector_document'):
                    if d.get('processing'):
                        value=status(d)
                        if value:workspace.store.put('connector_document',d['id'],d['project'],dict(d,processing=value['processing'],result_items=value['results']))
            except (ValueError,OSError,RuntimeError) as exc:
                workspace.store.put('connector_host','desktop','',{'heartbeat':0,'error':str(exc)[:160]})
            workspace.connector_stop.wait(1)
    workspace.connector_thread=threading.Thread(target=pump,name='memo-companion-pump',daemon=True)
    workspace.connector_thread.start()
