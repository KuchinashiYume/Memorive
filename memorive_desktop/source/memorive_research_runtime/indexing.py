"""EVO E6 bounded background backfill for legacy workspace metadata."""
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import json
from .common import sha,sealed,read
from .relations import record_identifiers

def history_backfill(runtime):
    index=runtime.work_index;processed=0;pending=0;failed=[]
    for path in sorted((runtime.root/'runs').glob('*/result.json')):
        if path.stat().st_size>8000000:
            failed.append({'run_id':path.parent.name,'code':'RESULT_SIZE_BOUND'});continue
        stat=path.stat();fingerprint=str(stat.st_mtime_ns)+':'+str(stat.st_size)
        with closing(index.connect()) as db:
            old=db.execute('SELECT fingerprint FROM indexed_inputs WHERE input_id=?',('history:'+path.parent.name,)).fetchone()
        if old and old[0]==fingerprint:continue
        if processed>=16:pending+=1;continue
        try:result=read(path)
        except (ValueError,OSError):
            failed.append({'run_id':path.parent.name,'code':'RESULT_HASH_MISMATCH'});processed+=1;continue
        index.import_history(path.parent.name,result.get('recommendations',[]),fingerprint);processed+=1
    index.set_cursor('history_coverage',{'status':'PARTIAL' if failed else 'BACKFILLING' if pending else 'COMPLETE',
        'runs_this_batch':processed,'remaining_observed_runs':pending,'maximum_runs_per_batch':16,
        'failures':failed,'scope':'ALL_HASH_BOUND_DISCOVERY_RESULTS_INDEPENDENT_OF_UI'})

def backfill(runtime):
    from memorive_inbox.store import InboxStore
    root=runtime.api._profile_path('INBOX')
    database=root/'inbox_store.sqlite3'
    if database.exists():
        def bound(locator):
            path=(root/locator).resolve()
            if not path.is_relative_to(root.resolve()): raise ValueError('E6_INBOX_PATH_ESCAPE')
            return path
        runtime.work_index.backfill_inbox(SimpleNamespace(store=InboxStore(database),_path=bound),limit=32)
    else: runtime.work_index.set_cursor('inbox_coverage',{'status':'COMPLETE','records':0})
    index=runtime.work_index
    history_backfill(runtime)
    jobs=runtime.api._service.call('list_jobs',{}).get('jobs',[])
    processed=0;pending=0
    for status in jobs:
        jid=status.get('job_id')
        if not jid or status.get('job_type')!='memorive-core': continue
        fingerprint=sha(status)
        with closing(index.connect()) as db:
            prior=db.execute('SELECT fingerprint FROM indexed_inputs WHERE input_id=?',('job:'+jid,)).fetchone()
        if prior and prior[0]==fingerprint: continue
        if processed>=16: pending+=1;continue
        job=runtime.api._service.call('get_job',{'job_id':jid})
        data=job.get('snapshots',{}).get('resolved_task_input',{}).get('content',{})
        record={'identifiers':record_identifiers(data),'title':data.get('display_name') or status.get('display_name'),
            'content_sha256':data.get('content_sha256') or job.get('source_sha256'),
            'identity_evidence':'LEGACY_STRUCTURED_JOB_INPUT','source_job_id':jid}
        # Read only exact metadata JSON bindings, one bounded file at a time.
        base=(runtime.api._profile_path('Core_JOBS')/jid/'artifacts').resolve()
        for binding in job.get('artifact_bindings',[]):
            locator=str(binding.get('locator',''));prefix='core-artifact:'+jid+':'
            if not locator.startswith(prefix): continue
            rel=locator[len(prefix):];path=(base/rel).resolve()
            if not path.is_relative_to(base) or path.suffix.lower()!='.json' or 'card' not in rel.lower(): continue
            if not path.is_file() or path.stat().st_size>2000000: continue
            raw=path.read_bytes()
            if binding.get('sha256') and sha(raw).upper()!=binding['sha256'].upper(): continue
            try: body=json.loads(raw)
            except ValueError: continue
            if not isinstance(body,dict): continue
            # Top-level bibliographic metadata only; no recursive reference scan.
            for box in (body,body.get('paper_info',{}),body.get('bibliographic',{}),body.get('metadata',{})):
                if not isinstance(box,dict): continue
                record['identifiers'].update(record_identifiers(box))
                if box.get('title'): record['title']=box['title']
            break
        if record['content_sha256'] or record['identifiers']:
            index.bind_file({'item_id':'job:'+jid,'content_sha256':record['content_sha256'],
                'state':str(job.get('control_state') or 'COMPLETED')},record)
        with closing(index.connect()) as db:
            db.execute('INSERT OR REPLACE INTO indexed_inputs VALUES(?,?)',('job:'+jid,fingerprint));db.commit()
        processed+=1
    index.set_cursor('legacy_coverage',{'status':'BACKFILLING' if pending else 'COMPLETE',
        'jobs_this_batch':processed,'remaining_observed_jobs':pending,'maximum_jobs_per_batch':16,
        'scope':'ALL_DURABLE_SERVICE_JOBS_INDEPENDENT_OF_UI','arbitrary_pdf_scans':0})

def loop(runtime):
    while not runtime.index_stop.is_set():
        try: backfill(runtime)
        except Exception as exc:
            runtime.work_index.set_cursor('legacy_coverage',{'status':'PARTIAL','error_code':type(exc).__name__})
        if runtime.index_stop.wait(2): break
