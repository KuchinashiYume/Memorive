"""Connected apps (Zotero, Obsidian, a folder, a bibliography file) and the companion that runs in them.

A connection is a folder of one project that an app keeps its papers in (save). Bringing papers in
takes two steps: preview() reads what the app sends (items) or what is picked (paths, a bibliography)
and shows what is new, updated or unchanged; commit() indexes the chosen ones in a resumable job
(_run, _ingest). Each paper becomes a connector_document; its file and a note of its bibliography and
annotations become material of the project.

The companion in the app calls companion(method, params) with connector.* methods. Everything it
sends is checked here (_item, the scope checks in companion), since it comes from outside. It can
read results for a paper (outputs), write a managed block into a note in the app (writeback_prepare,
writeback_ack), and queue actions for the desktop (queue): ask, hand to an agent, or process the PDF.
pump() runs those actions on the desktop's background thread (desktop_connections.py).
"""
import copy,html,os,re,time
from memorive_language import freeze
from memorive_language.text import choose
from pathlib import Path
from .store import digest,uid,now
from .evidence import safe_path
from .bibliography import parse

PROVIDERS={'obsidian','zotero','folder','bibliography'}
FORMATS={'.pdf','.md','.txt'}
DESKTOP=frozenset('memo.connections_state memo.connection_save memo.connection_preview memo.connection_commit memo.connection_retry memo.connection_refresh memo.connection_export memo.connection_queue memo.connection_outputs memo.connection_action_retry'.split())
COMPANION=frozenset('connector.connections connector.state connector.preview connector.commit connector.retry connector.refresh connector.search connector.read connector.outputs connector.writeback_prepare connector.writeback_ack connector.queue connector.chat connector.handoff connector.draft connector.source_changed connector.action_retry'.split())
ITEM_FIELDS={'source_key','title','authors','year','doi','abstract','path','item_key','attachment_key','library_id','version',
             'annotations','warnings','attachment_suggestions'}
MAX_FILE_BYTES=24*1024*1024
MAX_NOTE_CHARS=2*1024*1024


def limited(value, maximum=1000):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError('CONNECTOR_TEXT_INVALID')
    return value


def immutable_file(path, text):
    """Write text to path once; a file already there must hold the same text."""
    data = text.encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as stream:
            stream.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError('IMMUTABLE_SOURCE_CONFLICT')
    return path


def process_identity(pid):
    """Read-only process identity; never signal or terminate another process."""
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return str(pid)
        except ProcessLookupError:
            return None
        except PermissionError:
            return 'unknown'
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.c_void_p] * 4
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return 'unknown' if ctypes.get_last_error() == 5 else None
    try:
        values = [ctypes.c_uint64() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(v) for v in values]):
            return 'unknown'
        return str(values[0].value)
    finally:
        kernel.CloseHandle(handle)


def managed_block(source_id):
    """The markers around the block Memorive keeps in a note of the app."""
    return '<!-- memo:' + source_id + ':start -->', '<!-- memo:' + source_id + ':end -->'


class Connections:
    def __init__(self, workspace):
        self.ws = workspace
        self.store = workspace.store
        # Set by desktop_connections.py: processing a PDF runs only in the desktop app.
        self.processing_submit = None
        self.processing_status = None
        self.processing_retry = None

    def connection(self, connection_id, enabled=True, db=None):
        row = self.store.get('connection', connection_id, db=db)
        if not row:
            raise ValueError('CONNECTION_NOT_FOUND')
        if enabled and not row['enabled']:
            raise ValueError('CONNECTION_DISABLED')
        return row

    def save(self, project, name, provider, root, connection_id=None, expected_revision=0, enabled=True, writeback=False):
        """Create or change a connection. Its folder, project and app cannot change; make a new one."""
        if not self.store.get('project', project):
            raise ValueError('PROJECT_NOT_FOUND')
        if provider not in PROVIDERS or type(enabled) is not bool or type(writeback) is not bool:
            raise ValueError('CONNECTION_INVALID')
        folder = Path(root).resolve(strict=True)
        if not folder.is_dir() or folder.parent == folder or folder.name.startswith('.'):
            raise ValueError('CONNECTION_ROOT_INVALID')
        name = limited(name, 160).strip()
        if not name:
            raise ValueError('CONNECTION_NAME_REQUIRED')
        identity = connection_id or uid('conn_')
        old = self.store.get('connection', identity)
        if old and (old['root'] != str(folder) or old['project'] != project or old['provider'] != provider):
            raise ValueError('CONNECTION_SCOPE_IMMUTABLE_CREATE_NEW')
        capabilities = {'selected_files': True, 'bibliography': True, 'annotations': provider == 'zotero',
                        'managed_note_writeback': provider in {'obsidian', 'zotero'}, 'file_export': True,
                        'change_watch': 'explicit_selected_sources', 'short_chat': 'memo_desktop_queue',
                        'scientific_approval': False}
        return self.store.put('connection', identity, project, {
            'project': project, 'name': name, 'provider': provider, 'root': str(folder), 'enabled': enabled,
            'writeback': writeback, 'created_at': (old or {}).get('created_at', now()), 'capabilities': capabilities},
            expected=expected_revision)

    def state(self, project='default', connection_id=None):
        """Connections, papers, jobs, actions and write-backs of a project (or of one connection)."""
        if not self.store.get('project', project):
            raise ValueError('PROJECT_NOT_FOUND')
        connections = self.store.list('connection', project)
        if connection_id:
            connection = self.connection(connection_id, False)
            if connection['project'] != project:
                raise ValueError('PROJECT_SCOPE_MISMATCH')
            connections = [connection]
        accepted = {c['id'] for c in connections}
        # A job still RUNNING whose process is gone was interrupted.
        for job in self.store.list('connector_job', project):
            if job['connection_id'] not in accepted or job['status'] != 'RUNNING' or not job.get('owner_pid'):
                continue
            identity = process_identity(job['owner_pid'])
            if identity != 'unknown' and identity != job.get('owner_identity'):
                with self.store.tx() as db:
                    current = self.store.get('connector_job', job['id'], db=db)
                    if current['status'] == 'RUNNING' and current.get('owner_identity') == job.get('owner_identity'):
                        self.store.put('connector_job', job['id'], project, dict(current, status='INTERRUPTED'), db=db)
        heartbeat = float((self.store.get('connector_host', 'desktop') or {}).get('heartbeat', 0))
        return {'connections': connections,
                'documents': [r for r in self.store.list('connector_document', project) if r['connection_id'] in accepted],
                'jobs': [r for r in self.store.list('connector_job', project) if r['connection_id'] in accepted][:60],
                'actions': [r for r in self.store.list('connector_action', project) if r['connection_id'] in accepted][:40],
                'desktop_online': time.time() - heartbeat < 8,
                'writebacks': [r for r in self.store.list('connector_writeback', project) if r['connection_id'] in accepted]}

    def _path(self, connection, path):
        path = Path(path)
        if not path.is_absolute():
            path = Path(connection['root']) / path
        return safe_path(connection['root'], path)

    def _item(self, connection, value):
        """One paper as the app or a pick describes it, checked and in the stored form."""
        if not isinstance(value, dict):
            raise ValueError('CONNECTOR_ITEM_INVALID')
        if set(value) - ITEM_FIELDS:
            raise ValueError('CONNECTOR_ITEM_FIELDS_INVALID')
        row = {k: copy.deepcopy(v) for k, v in value.items() if k in ITEM_FIELDS}
        row['title'] = limited(row.get('title', ''), 1000).strip()
        if not row['title']:
            raise ValueError('CONNECTOR_TITLE_REQUIRED')
        row['source_key'] = limited(str(row.get('source_key') or row.get('item_key') or digest(row)[:24]), 200)
        for field, maximum in [('abstract', 40000), ('doi', 300), ('year', 40)]:
            row[field] = limited(row.get(field, ''), maximum)
        if not isinstance(row.get('authors', []), list) or len(row.get('authors', [])) > 100:
            raise ValueError('CONNECTOR_AUTHORS_INVALID')
        row['authors'] = [limited(v, 500) for v in row.get('authors', [])]
        if connection['provider'] == 'zotero':
            for key in ('item_key', 'attachment_key'):
                if row.get(key) and not re.fullmatch('[A-Z0-9]{8}', row[key]):
                    raise ValueError('ZOTERO_KEY_INVALID')
            row['library_id'] = str(row.get('library_id', '1'))
            if not row['library_id'].isdigit():
                raise ValueError('ZOTERO_LIBRARY_INVALID')
        annotations = row.get('annotations', [])
        if not isinstance(annotations, list) or len(annotations) > 500:
            raise ValueError('ANNOTATIONS_INVALID')
        row['annotations'] = []
        for annotation in annotations:
            if not isinstance(annotation, dict) or set(annotation) - {'id', 'text', 'comment', 'page'}:
                raise ValueError('ANNOTATION_INVALID')
            page = annotation.get('page')
            if page is not None and (type(page) is not int or not 1 <= page <= 50000):
                raise ValueError('ANNOTATION_PAGE_INVALID')
            row['annotations'].append({'id': limited(annotation.get('id', ''), 200), 'text': limited(annotation.get('text', ''), 8000),
                                       'comment': limited(annotation.get('comment', ''), 8000), 'page': page})
        row['file_hash'] = None
        row['path'] = row.get('path') or None
        if row['path']:
            path = self._path(connection, row['path'])
            if path.suffix.lower() not in FORMATS:
                raise ValueError('CONNECTOR_FILE_FORMAT_UNSUPPORTED')
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError('SOURCE_TOO_LARGE')
            row['path'] = str(path)
            row['file_hash'] = digest(path.read_bytes())
        row['warnings'] = list(row.get('warnings', []))[:20]
        if not row['path']:
            row['warnings'].append('METADATA_ONLY_ATTACH_PDF_TO_PROCESS')
        row['source_id'] = 'src_' + digest([connection['id'], row['source_key']])[:28]
        row['fingerprint'] = digest({k: v for k, v in row.items() if k not in {'warnings', 'fingerprint'}})
        previous = self.store.get('connector_document', row['source_id'])
        if previous and previous['fingerprint'] == row['fingerprint'] and previous['state'] == 'active':
            row['change'] = 'unchanged'
        else:
            row['change'] = 'updated' if previous else 'new'
        return row

    def preview(self, connection_id, paths=None, items=None, bibliography=None, attachment_map=None):
        """What bringing these papers in would do; nothing is indexed yet."""
        connection = self.connection(connection_id)
        values, bindings = [], []
        if bibliography:
            path = self._path(connection, bibliography)
            values = parse(path)
            bindings.append({'path': str(path), 'hash': digest(path.read_bytes())})
            mappings = attachment_map or {}
            for value in values:
                if value['source_key'] in mappings:
                    value['path'] = mappings[value['source_key']]
        if paths:
            if not isinstance(paths, list):
                raise ValueError('CONNECTOR_PATHS_INVALID')
            for value in paths:
                path = self._path(connection, value)
                values.append({'title': path.stem, 'source_key': str(path.relative_to(connection['root'])), 'path': str(path)})
        if items:
            if not isinstance(items, list):
                raise ValueError('CONNECTOR_ITEMS_INVALID')
            values.extend(items)
        if not 1 <= len(values) <= 2000:
            raise ValueError('CONNECTOR_SELECTION_REQUIRED')
        rows = [self._item(connection, v) for v in values]
        if len({v['source_id'] for v in rows}) != len(rows):
            raise ValueError('CONNECTOR_DUPLICATE_SELECTION')
        return self.store.put('connector_preview', uid('preview_'), connection['project'], {
            'connection_id': connection['id'], 'project': connection['project'], 'connection_revision': connection['revision'],
            'items': rows, 'bindings': bindings, 'created_at': now(), 'preview_hash': digest(rows),
            'counts': {kind: sum(r['change'] == kind for r in rows) for kind in ('new', 'updated', 'unchanged')},
            'external_calls': 0})

    def commit(self, preview_id, preview_hash, selected_ids=None):
        """Bring in the chosen papers of a preview (all by default) as one job."""
        preview = self.store.get('connector_preview', preview_id)
        if not preview or preview_hash != preview['preview_hash']:
            raise ValueError('PREVIEW_CONFLICT')
        connection = self.connection(preview['connection_id'])
        if connection['revision'] != preview['connection_revision']:
            raise ValueError('CONNECTION_CHANGED_REPREVIEW')
        offered = {r['source_id'] for r in preview['items']}
        selected = set(selected_ids) if selected_ids is not None else offered
        if not selected or selected - offered:
            raise ValueError('PREVIEW_SELECTION_INVALID')
        identity = 'intake_' + digest([preview_id, sorted(selected)])[:28]
        with self.store.tx() as db:
            existing = self.store.get('connector_job', identity, db=db)
            if existing:
                return existing
            for binding in preview['bindings']:  # the bibliography file must be the one previewed
                if digest(self._path(connection, binding['path']).read_bytes()) != binding['hash']:
                    raise ValueError('PREVIEW_SOURCE_CHANGED')
            steps = [{'source_id': r['source_id'], 'status': 'QUEUED', 'attempts': 0} for r in preview['items'] if r['source_id'] in selected]
            job = self.store.put('connector_job', identity, connection['project'], {
                'connection_id': connection['id'], 'project': connection['project'], 'preview_id': preview_id,
                'created_at': now(), 'status': 'QUEUED', 'external_calls': 0, 'api_cost': 0, 'currency': 'USD', 'steps': steps}, db=db)
        return self._run(job, connection, preview)

    def _ingest(self, connection, row):
        """Index one paper: its file (when it has one) and a note of its bibliography and annotations.
        add_library_file checks the file against the hash taken at the preview."""
        old = self.store.get('connector_document', row['source_id'])
        if old and old['fingerprint'] == row['fingerprint'] and old['state'] == 'active':
            return old
        related = next((d for d in self.store.list('connector_document', connection['project'])
                        if row['file_hash'] and d.get('file_hash') == row['file_hash']), None)
        document_id = (old or related or {}).get('document_id') or uid('doc_')
        text = '# ' + row['title'] + '\n\n' + '; '.join(row['authors']) + '\n' + row['year'] + '\nDOI: ' + row['doi'] + '\n\n' + row['abstract']
        for annotation in row['annotations']:
            text += '\n\n## 批注 · ' + str(annotation['page'] or '页码未知') + '\n' + annotation['text'] + '\n个人批注：' + annotation['comment']
        note = immutable_file(self.store.root / 'connector_sources' / row['source_id'] / (row['fingerprint'] + '.md'), text)
        ids = []
        needs_ocr = False
        if row['path']:
            try:
                artifact = self.ws.index.add_library_file(connection['project'], path=row['path'], root=connection['root'],
                                                          external_id=document_id + ':' + connection['id'],
                                                          expected_hash=row['file_hash'], title=row['title'])
                ids.append(artifact['id'])
            except ValueError as exc:
                if str(exc) != 'SOURCE_REQUIRES_EXISTING_OCR':
                    raise
                needs_ocr = True
        artifact = self.ws.index.add_library_file(connection['project'], path=note, root=self.store.root,
                                                  external_id=row['source_id'] + ':metadata',
                                                  expected_hash=digest(note.read_bytes()), title=row['title'] + ' · 书目与批注')
        ids.append(artifact['id'])
        binding = {k: row.get(k) for k in ('item_key', 'attachment_key', 'library_id', 'version')}
        binding.update(provider=connection['provider'], connection_id=connection['id'], document_id=document_id,
                       source_id=row['source_id'], vault_name=Path(connection['root']).name,
                       relative_path=str(Path(row['path']).relative_to(connection['root'])) if row['path'] else None)
        with self.store.tx() as db:
            for identity in ids:
                material = self.store.get('artifact', identity, db=db)
                self.store.put('artifact', identity, connection['project'], dict(material, source_binding=binding), db=db)
            processing = (old or {}).get('processing') if old and old.get('file_hash') == row['file_hash'] else None
            value = dict(row, connection_id=connection['id'], project=connection['project'], document_id=document_id,
                         state='active', artifact_ids=ids, needs_ocr=needs_ocr, processing=processing, updated_at=now())
            return self.store.put('connector_document', row['source_id'], connection['project'], value, db=db)

    def _run(self, job, connection, preview):
        """Run the job's steps that are not complete; a step that fails is kept for retry()."""
        started = time.monotonic()
        rows = {r['source_id']: r for r in preview['items']}
        with self.store.tx() as db:
            fresh = self.store.get('connector_job', job['id'], db=db)
            if fresh['status'] == 'RUNNING':
                raise ValueError('CONNECTOR_JOB_BUSY')
            job = self.store.put('connector_job', job['id'], job['project'], dict(
                fresh, status='RUNNING', owner_pid=os.getpid(), owner_identity=process_identity(os.getpid())), db=db)
        for step in job['steps']:
            if step['status'] == 'COMPLETE':
                continue
            step['attempts'] += 1
            step['started_at'] = now()
            try:
                value = self._ingest(connection, rows[step['source_id']])
                step.update(status='COMPLETE', document_id=value['document_id'], error=None)
            except (ValueError, OSError, UnicodeError) as exc:
                step.update(status='ERROR', error=str(exc)[:200])
            job = self.store.put('connector_job', job['id'], job['project'], job)
        errors = sum(s['status'] == 'ERROR' for s in job['steps'])
        job.update(status='PARTIAL' if 0 < errors < len(job['steps']) else ('ERROR' if errors else 'COMPLETE'),
                   elapsed_ms=job.get('elapsed_ms', 0) + round((time.monotonic() - started) * 1000), completed_at=now())
        return self.store.put('connector_job', job['id'], job['project'], job)

    def retry(self, job_id):
        job = self.store.get('connector_job', job_id)
        if not job:
            raise ValueError('CONNECTOR_JOB_NOT_FOUND')
        if job['status'] not in {'ERROR', 'PARTIAL', 'INTERRUPTED'}:
            raise ValueError('CONNECTOR_RETRY_STATE_INVALID')
        return self._run(job, self.connection(job['connection_id']), self.store.get('connector_preview', job['preview_id']))

    def refresh(self, connection_id):
        """Look whether the connection's papers changed on disk; changed or missing ones become unavailable."""
        connection = self.connection(connection_id)
        changed, missing = [], []
        for document in self.store.list('connector_document', connection['project']):
            if document['connection_id'] != connection['id'] or not document['path']:
                continue
            try:
                current = digest(self._path(connection, document['path']).read_bytes())
            except (OSError, ValueError):
                current = None
            if current != document['file_hash']:
                self._mark_unavailable(connection, document, 'changed' if current else 'unavailable')
                (changed if current else missing).append(document['id'])
        return {'status': 'REFRESHED', 'changed': changed, 'missing': missing, 'action': 'select_changed_sources_to_preview',
                'external_calls': 0}

    def _mark_unavailable(self, connection, document, state):
        with self.store.tx() as db:
            self.store.put('connector_document', document['id'], connection['project'], dict(document, state=state), db=db)
            for identity in document['artifact_ids']:
                material = self.store.get('artifact', identity, db=db)
                if material:
                    self.store.put('artifact', identity, connection['project'], dict(material, state='unavailable'), db=db)

    def document(self, connection, source_id):
        document = self.store.get('connector_document', source_id)
        if not document or document['connection_id'] != connection['id']:
            raise ValueError('DOCUMENT_SCOPE_MISMATCH')
        return document

    def outputs(self, connection_id, source_id):
        """What Memorive has for a paper: its source note, processing results, knowledge drafts on it.
        The paper's file and the results' files must still be the ones indexed."""
        connection = self.connection(connection_id)
        document = self.document(connection, source_id)
        if document['state'] != 'active':
            raise ValueError('DOCUMENT_SOURCE_CHANGED')
        if document['path'] and digest(self._path(connection, document['path']).read_bytes()) != document['file_hash']:
            raise ValueError('DOCUMENT_SOURCE_CHANGED')
        language = freeze(self.ws.language_settings_get())
        summary = ('# ' + document['title'] + '\n\n' + document['abstract'] + '\n\nDOI: ' + document['doi'] + '\n\n'
                   + choose(language, '资料已索引；此条为来源说明。', 'The material is indexed; this entry describes the source.',
                            '資料は索引に登録済みです。この項目は出典の説明です。'))
        items = [{'id': 'source-summary', 'title': choose(language, '书目与来源', 'Bibliography and source', '書誌と出典'),
                  'kind': 'SOURCE_SUMMARY', 'markdown': summary}]
        if self.processing_status:
            status = self.processing_status(document)
            if status:
                document = self.store.put('connector_document', document['id'], connection['project'], dict(
                    document, processing=status.get('processing', document.get('processing')),
                    result_items=status.get('results', document.get('result_items', []))))
        items.extend(document.get('result_items', []))
        for item in items:
            if item.get('source_path') and digest(Path(item['source_path']).read_bytes()) != item['source_hash']:
                raise ValueError('RESULT_SOURCE_CHANGED')
        for draft in self.store.list('feedback', connection['project']):
            if draft.get('state') in {'retracted', 'RETRACTED'}:
                continue
            if any(ref.get('artifact_id') in document['artifact_ids'] for ref in draft.get('parents', [])):
                draft_language = draft.get('language_context') or language
                markdown = (draft.get('claim', '') + '\n\n' + choose(draft_language, '适用范围：', 'Scope: ', '適用範囲：') + draft.get('scope', '')
                            + '\n\n' + choose(draft_language, '局限：', 'Limitations: ', '限界：') + draft.get('limitations', ''))
                items.append({'id': draft['id'], 'title': draft.get('title', '研究草稿'), 'kind': 'KNOWLEDGE_' + str(draft.get('state', 'DRAFT')),
                              'markdown': markdown})
        return {'document': document, 'items': items, 'language_context': language}

    def _body(self, connection, source_id, output_ids):
        """The chosen outputs of a paper as one markdown text for a note."""
        result = self.outputs(connection['id'], source_id)
        document = result['document']
        wanted = set(output_ids or ['source-summary'])
        items = [i for i in result['items'] if i['id'] in wanted]
        if not items or wanted - {i['id'] for i in items}:
            raise ValueError('OUTPUT_SELECTION_INVALID')
        language = result['language_context']
        body = '\n\n'.join('## ' + i['title'] + ' · ' + i['kind'] + '\n\n' + i['markdown'] for i in items)
        body += '\n\n' + choose(language, '来源版本：', 'Source version: ', '出典のバージョン：') + str(document['file_hash'] or document['fingerprint']) + '\n'
        if connection['provider'] == 'zotero' and document.get('attachment_key'):
            body += '\n[' + choose(language, '打开原文', 'Open source', '原文を開く') + '](zotero://open-pdf/library/items/' + document['attachment_key'] + ')\n'
        # Text that looks like a block marker must not end the managed block early.
        body = re.sub(r'<!--\s*memo:', '<!-- quoted-memo:', body, flags=re.I)
        return document, body

    def writeback_prepare(self, connection_id, source_id, external_note_id, existing_text='', output_ids=None, format='markdown'):
        """The note text with Memorive's block put in or replaced; the app writes it, then calls
        writeback_ack. A block the user edited or removed is a conflict, not overwritten."""
        connection = self.connection(connection_id)
        if not connection['writeback']:
            raise ValueError('WRITEBACK_DISABLED')
        existing_text = limited(existing_text, MAX_NOTE_CHARS)
        external_note_id = limited(external_note_id, 1000)
        document, body = self._body(connection, source_id, output_ids)
        if format not in {'markdown', 'html'} or (format == 'html' and connection['provider'] != 'zotero'):
            raise ValueError('WRITEBACK_FORMAT_INVALID')
        if format == 'html':
            body = '<div><pre>' + html.escape(body) + '</pre></div>'
        begin, end = managed_block(source_id)
        identity = 'wb_' + digest([connection['id'], source_id, external_note_id])[:28]
        old = self.store.get('connector_writeback', identity)
        if existing_text.count(begin) != existing_text.count(end) or existing_text.count(begin) > 1:
            raise ValueError('MANAGED_BLOCK_INVALID')
        match = re.search(re.escape(begin) + r'(.*?)' + re.escape(end), existing_text, re.S)
        # A plan already made for this exact note text: the app is retrying the same write.
        recovered = next((p for p in self.store.list('connector_writeback_plan', connection['project'])
                          if p['writeback_id'] == identity and p['text_hash'] == digest(existing_text)), None)
        if match and (not old or digest(match.group(1)) != old['managed_hash']) and not recovered:
            return {'status': 'CONFLICT', 'reason': 'USER_EDITED_MANAGED_BLOCK', 'proposed_markdown': body,
                    'existing_text': existing_text, 'suggested_action': 'export_new_note'}
        if old and not match:
            return {'status': 'CONFLICT', 'reason': 'MANAGED_BLOCK_REMOVED', 'proposed_markdown': body,
                    'suggested_action': 'export_new_note'}
        block = begin + '\n' + body + '\n' + end
        if match:
            merged = existing_text[:match.start()] + block + existing_text[match.end():]
        else:
            merged = existing_text + ('\n\n' if existing_text else '') + block + '\n'
        value = {'connection_id': connection['id'], 'project': connection['project'], 'source_id': source_id,
                 'external_note_id': external_note_id, 'writeback_id': identity, 'expected_text_hash': digest(existing_text),
                 'text': merged, 'managed_hash': digest('\n' + body + '\n'), 'text_hash': digest(merged),
                 'output_ids': output_ids or ['source-summary'], 'source_fingerprint': document['fingerprint'],
                 'created_at': now(), 'status': 'PREPARED', 'format': format}
        return self.store.put('connector_writeback_plan', uid('wbplan_'), connection['project'], value)

    def writeback_ack(self, connection_id, plan_id, text_hash, host_text=None, host_note_id=None):
        """The app wrote a planned note. Zotero may store the HTML note differently (host_text): the
        block in it must still be the planned one."""
        connection = self.connection(connection_id)
        plan = self.store.get('connector_writeback_plan', plan_id)
        if not plan or plan['connection_id'] != connection['id'] or plan['text_hash'] != text_hash:
            raise ValueError('WRITEBACK_RECEIPT_INVALID')
        document = self.document(connection, plan['source_id'])
        self.outputs(connection['id'], document['id'])  # the paper's files must still be the indexed ones
        if document['fingerprint'] != plan['source_fingerprint']:
            raise ValueError('WRITEBACK_SOURCE_CHANGED')
        if host_text is not None:
            if connection['provider'] != 'zotero' or plan.get('format') != 'html' or not re.fullmatch('[A-Z0-9]{8}', host_note_id or ''):
                raise ValueError('WRITEBACK_HOST_INVALID')
            limited(host_text, MAX_NOTE_CHARS)
            begin, end = managed_block(document['id'])
            match = re.search(re.escape(begin) + r'(.*?)' + re.escape(end), host_text, re.S)
            if not match or digest(match.group(1)) != plan['managed_hash']:
                raise ValueError('WRITEBACK_HOST_NORMALIZATION_CONFLICT')
            plan = dict(plan, external_note_id=host_note_id, writeback_id='wb_' + digest([connection['id'], document['id'], host_note_id])[:28],
                        text_hash=digest(host_text))
        record = {k: v for k, v in dict(plan, status='SYNCED', completed_at=now()).items() if k not in {'text', 'expected_text_hash'}}
        return self.store.put('connector_writeback', plan['writeback_id'], connection['project'], record)

    def export(self, connection_id, source_id, output_ids=None):
        """Write a paper's outputs as a new markdown file; the app's own notes are not touched."""
        connection = self.connection(connection_id)
        _document, body = self._body(connection, source_id, output_ids)
        path = self.store.root / 'connector_exports' / connection['id'] / (source_id + '-' + digest(body)[:12] + '.md')
        immutable_file(path, body)
        return {'status': 'EXPORTED', 'path': str(path), 'sha256': digest(body.encode('utf-8')), 'original_notes_modified': False}

    def queue(self, connection_id, kind, source_id=None, question='', thread_id=None, request_id=None, client_id=None, grant_revision=None):
        """Queue an action for the desktop: ask, agent, process (the PDF) or process_retry. The same
        request id gives back the same action."""
        connection = self.connection(connection_id)
        if kind not in {'ask', 'agent', 'process', 'process_retry'}:
            raise ValueError('CONNECTOR_ACTION_INVALID')
        if kind in {'ask', 'agent'} and not limited(question, 8000).strip():
            raise ValueError('QUESTION_INVALID')
        document = self.document(connection, source_id) if source_id else None
        if kind in {'process', 'process_retry'} and (not document or not document['path']):
            raise ValueError('ATTACHMENT_REQUIRED')
        if thread_id and self.ws.thread_get(thread_id)['project'] != connection['project']:
            raise ValueError('PROJECT_SCOPE_MISMATCH')
        if client_id:
            self.ws.developer.authorize_action(client_id, connection['id'], kind, grant_revision)
        key = limited(request_id or uid('request_'), 120)
        identity = 'action_' + digest([connection['id'], key, client_id] if client_id else [connection['id'], key])[:28]
        payload = {'connection_id': connection['id'], 'project': connection['project'], 'kind': kind, 'source_id': source_id,
                   'question': question, 'thread_id': thread_id}
        if client_id:
            payload.update(client_id=client_id, grant_revision=grant_revision)
        with self.store.tx() as db:
            old = self.store.get('connector_action', identity, db=db)
            if old:
                if old['request_hash'] != digest(payload):
                    raise ValueError('IDEMPOTENCY_CONFLICT')
                return old
            if client_id:  # an automation client may queue only so many actions
                settings = self.store.get('developer_settings', 'automation', db=db) or {}
                count = sum(1 for a in self.store.list('connector_action', db=db) if a.get('client_id') == client_id and a['status'] in {'QUEUED', 'RUNNING'})
                if count >= settings.get('max_queued', 20):
                    raise ValueError('AUTOMATION_QUEUE_FULL')
            return self.store.put('connector_action', identity, connection['project'],
                                  dict(payload, request_hash=digest(payload), status='QUEUED', created_at=now()), db=db)

    def action_retry(self, action_id):
        with self.store.tx() as db:
            action = self.store.get('connector_action', action_id, db=db)
            if not action or action['status'] not in {'ERROR', 'INTERRUPTED'}:
                raise ValueError('CONNECTOR_RETRY_STATE_INVALID')
            self.connection(action['connection_id'], db=db)
            return self.store.put('connector_action', action['id'], action['project'], dict(action, status='QUEUED', error=None, retry_at=now()), db=db)

    def source_changed(self, connection_id, source_ids):
        """The app reports papers it changed; they become unavailable until previewed again."""
        connection = self.connection(connection_id)
        if not isinstance(source_ids, list) or len(source_ids) > 2000:
            raise ValueError('SOURCE_IDS_INVALID')
        documents = [self.document(connection, s) for s in source_ids]
        with self.store.tx() as db:
            for document in documents:
                self.store.put('connector_document', document['id'], connection['project'], dict(document, state='changed'), db=db)
                for identity in document['artifact_ids']:
                    material = self.store.get('artifact', identity, db=db)
                    if material:
                        self.store.put('artifact', identity, connection['project'], dict(material, state='unavailable'), db=db)
        return {'status': 'CHANGED', 'source_ids': source_ids}

    def pump(self):
        """Desktop background step: say the desktop is online and dispatch queued actions."""
        self.store.put('connector_host', 'desktop', '', {'heartbeat': time.time()})
        for action in self.store.list('connector_action'):
            if action['status'] != 'QUEUED':
                continue
            with self.store.tx() as db:
                fresh = self.store.get('connector_action', action['id'], db=db)
                if fresh['status'] != 'QUEUED':
                    continue
                self.store.put('connector_action', action['id'], action['project'], dict(fresh, status='RUNNING'), db=db)
            try:
                result = self._dispatch(action)
                self.store.put('connector_action', action['id'], action['project'], dict(action, status='DISPATCHED', result=result, dispatched_at=now()))
            except (ValueError, OSError, RuntimeError) as exc:
                self.store.put('connector_action', action['id'], action['project'], dict(action, status='ERROR', error=str(exc)[:200]))

    def _dispatch(self, action):
        if action.get('client_id'):
            self.ws.developer.authorize_action(action['client_id'], action['connection_id'], action['kind'], action.get('grant_revision'))
        connection = self.connection(action['connection_id'])
        document = self.document(connection, action['source_id']) if action['source_id'] else None
        if action['kind'] in {'ask', 'agent'}:
            thread_id = action['thread_id']
            if not thread_id:
                thread = self.ws.thread_create(connection['project'])
                thread_id = thread['id']
                # The companion's new chat explicitly searches its selected project.
                scope = {'artifact_ids': document['artifact_ids']} if document else {'include_project': True}
                self.ws.attachments.configure(thread_id, thread['revision'], scope)
            if action['kind'] == 'agent':
                job = self.ws.agent_dispatch(thread_id, action['question'], action['id'])
            else:
                job = self.ws.ask(thread_id, action['question'], action['id'], artifact_ids=document['artifact_ids'] if document else None)
            return {'thread_id': thread_id, 'job_id': job['id']}
        if not self.processing_submit:
            raise ValueError('MEMO_DESKTOP_PROCESSOR_REQUIRED')
        self.outputs(connection['id'], document['id'])  # the paper's files must still be the indexed ones
        if action['kind'] == 'process_retry':
            if not self.processing_retry:
                raise ValueError('MEMO_DESKTOP_PROCESSOR_REQUIRED')
            result = self.processing_retry(document, action['id'])
        else:
            result = self.processing_submit(document, action['id'])
        self.store.put('connector_document', document['id'], document['project'], dict(document, processing=result))
        return result

    def companion(self, method, params):
        """A connector.* call from the companion. Anything it names (preview, job, action, conversation)
        must belong to its connection."""
        if method not in COMPANION:
            raise ValueError('CONNECTOR_METHOD_NOT_ALLOWED')
        p = dict(params)
        if method == 'connector.connections':
            return {'connections': self.store.list('connection', p.get('project', 'default'))}
        connection = self.connection(p.pop('connection_id'))
        if method == 'connector.state':
            return self.state(connection['project'], connection['id'])
        if method == 'connector.preview':
            return self.preview(connection['id'], **p)
        if method == 'connector.commit':
            preview = self.store.get('connector_preview', p.get('preview_id', ''))
            if not preview or preview['connection_id'] != connection['id']:
                raise ValueError('PREVIEW_SCOPE_MISMATCH')
            return self.commit(**p)
        if method == 'connector.retry':
            job = self.store.get('connector_job', p.get('job_id', ''))
            if not job or job['connection_id'] != connection['id']:
                raise ValueError('JOB_SCOPE_MISMATCH')
            return self.retry(**p)
        if method == 'connector.refresh':
            return self.refresh(connection['id'])
        if method == 'connector.source_changed':
            return self.source_changed(connection['id'], **p)
        if method == 'connector.action_retry':
            action = self.store.get('connector_action', p.get('action_id', ''))
            if not action or action['connection_id'] != connection['id']:
                raise ValueError('JOB_SCOPE_MISMATCH')
            return self.action_retry(**p)
        if method == 'connector.search':
            return self.ws.index.search(project=connection['project'], **p)
        if method == 'connector.read':
            return self.ws.index.read(project=connection['project'], **p)
        if method == 'connector.outputs':
            return self.outputs(connection['id'], **p)
        if method == 'connector.writeback_prepare':
            return self.writeback_prepare(connection['id'], **p)
        if method == 'connector.writeback_ack':
            return self.writeback_ack(connection['id'], **p)
        if method == 'connector.queue':
            return self.queue(connection['id'], **p)
        if method == 'connector.chat':
            if 'thread_id' in p:
                thread = self.ws.thread_get(p['thread_id'])
                if thread['project'] != connection['project']:
                    raise ValueError('PROJECT_SCOPE_MISMATCH')
                return thread
            return {'threads': self.store.list('thread', connection['project'])}
        if method == 'connector.handoff':
            if self.ws.thread_get(p['thread_id'])['project'] != connection['project']:
                raise ValueError('PROJECT_SCOPE_MISMATCH')
            value = self.ws.handoff(**p)
            return dict(value, export=self.ws.export_handoff(value['id']))
        if method == 'connector.draft':
            return self.ws.submit_draft(project=connection['project'], **p)

    def desktop(self, method, p):
        methods = {'memo.connections_state': self.state, 'memo.connection_save': self.save, 'memo.connection_preview': self.preview,
                   'memo.connection_commit': self.commit, 'memo.connection_retry': self.retry, 'memo.connection_refresh': self.refresh,
                   'memo.connection_export': self.export, 'memo.connection_queue': self.queue, 'memo.connection_outputs': self.outputs,
                   'memo.connection_action_retry': self.action_retry}
        return methods[method](**p)
