"""Processing documents sent from a connected device, and the desktop's background loop.

A document that arrives through a connection (connections.py) is processed like a file dropped
into the inbox; only the desktop runs model jobs:
  submit(document)   import the file into the inbox and start its processing job, once per document
  status(document)   follow the job; when it has produced a card and an analysis, read them from
                     the library (job_results)
  retry(document)    run the failed step of the job again
pump() runs once a second on a background thread: it keeps conversations tidy, moves connection
work along and refreshes the status of documents being processed.
"""
import json
import threading
from pathlib import Path

from .store import digest, now

# The products a connected device is shown when its document has been processed.
RESULT_KINDS = {'CORE_CARD', 'CORE_ANALYSIS'}


def job_results(library, job_id):
    """The card and analysis a processing job produced, as markdown for the connected device."""
    results = []
    for artifact_id, record in library.core_artifacts().items():
        if record['job_id'] != job_id or record['artifact_kind'] not in RESULT_KINDS:
            continue
        # Read once; the library checks these bytes against the hash the job recorded.
        path, raw = library.read_core_artifact(artifact_id)
        text = raw.decode('utf-8-sig')
        if path.suffix.lower() == '.json':
            text = '```json\n' + json.dumps(json.loads(text), ensure_ascii=False, indent=2) + '\n```'
        results.append({'id': artifact_id, 'title': record['row']['display_name'], 'kind': record['artifact_kind'],
                        'markdown': text, 'source_path': str(path), 'source_hash': digest(raw)})
    return results


def bind(workspace, api):
    def submit(document, request_id):
        existing = document.get('processing') or {}
        if existing.get('item_id'):
            return existing
        identity = 'stage_' + digest([document['document_id'], document['file_hash']])[:28]
        staged = workspace.store.get('connector_stage', identity)
        if not staged:
            result = api.stage_inbox_paths([document['path']])
            staged = workspace.store.put('connector_stage', identity, document['project'], result)
        batch = api.call('inbox.import_paths', {'paths': staged['paths'], 'request_id': identity, 'source_kind': 'picker'})
        item = batch['items'][0]['item']
        if item['state'] == 'DEDUPLICATED':
            # The same file is already in the inbox: follow that item.
            item = api.call('inbox.item_detail', {'item_id': item['duplicate_of']})
        if item.get('state') == 'ERROR':
            raise ValueError('INBOX_IMPORT_FAILED:' + str(item.get('error'))[:120])
        if not item.get('job_id') and item.get('state') == 'QUEUED':
            api.call('inbox.dispatch', {'item_id': item['item_id'], 'idempotency_key': identity})
        return {'item_id': item['item_id'], 'job_id': item.get('job_id'), 'state': item['state'], 'submitted_at': now(),
                'route': 'inbox', 'execution': 'existing_mainline_with_saved_settings'}

    def status(document):
        previous = document.get('processing')
        if not previous:
            return None
        item = api.call('inbox.item_detail', {'item_id': previous['item_id']})
        job_id = item.get('job_id')
        results, control_state = [], None
        if job_id:
            results = job_results(api._library, job_id)
            job = api._service.call('get_job', {'job_id': job_id})
            control_state = job.get('control_state', job.get('job_control_state'))
        processing = dict(previous, job_id=job_id, state=item['state'], control_state=control_state,
                          error=item.get('error'), last_observed_at=now())
        return {'processing': processing, 'results': results}

    def retry(document, request_id):
        previous = (status(document) or {}).get('processing') or {}
        if not previous.get('job_id'):
            raise ValueError('CURRENT_TASK_REQUIRED')
        api.call('current_task.retry', {'job_id': previous['job_id'], 'request_id': request_id})
        return dict(previous, state='RETRY_REQUESTED', retry_scope='failed_node', retry_requested_at=now())

    workspace.connections.processing_submit = submit
    workspace.connections.processing_status = status
    workspace.connections.processing_retry = retry

    # Actions still running when the desktop stopped did not finish; the user checks before retrying.
    with workspace.store.tx() as db:
        for action in workspace.store.list('connector_action', db=db):
            if action['status'] == 'RUNNING':
                interrupted = dict(action, status='INTERRUPTED', error='Desktop restarted; review current task before retry')
                workspace.store.put('connector_action', action['id'], action['project'], interrupted, db=db)

    def pump():
        while not workspace.connector_stop.is_set():
            try:
                workspace.conversations.maintain()
                workspace.connections.pump()
                for document in workspace.store.list('connector_document'):
                    if document.get('processing'):
                        value = status(document)
                        updated = dict(document, processing=value['processing'], result_items=value['results'])
                        workspace.store.put('connector_document', document['id'], document['project'], updated)
            except (ValueError, OSError, RuntimeError) as exc:
                # Kept as the desktop's last error; the loop goes on.
                workspace.store.put('connector_host', 'desktop', '', {'heartbeat': 0, 'error': str(exc)[:160]})
            workspace.connector_stop.wait(1)

    workspace.connector_thread = threading.Thread(target=pump, name='memo-companion-pump', daemon=True)
    workspace.connector_thread.start()
