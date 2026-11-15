"""The material of a conversation, and files added to it in the window.

A conversation draws on material of its project. Its scope is kept on the thread: artifact_ids (chosen
or attached for it), excluded_artifact_ids (taken out of it), and include_project / scope (whether
the rest of the project's material counts too). scope_ids() gives the result; configure() and
remove() change it. material() is the one check that an id names material of the project.

attach() copies each added file under attachments/, named by its content's hash, and queues its
preparation; _prepare() then, in the worker pool, extracts its text (pictures and scanned pages
through the vision model) and cuts it into excerpts. A memo-source package or a returned agent
answer (JSON with a memo-* schema) is handed to the skill bridge instead.
"""
import io,json,stat,threading
from pathlib import Path
from memorive_folder_management.policy import windows_io_path
from .store import digest,uid,now,packed

METHODS=frozenset({'memo.attach_files','memo.attachment_status','memo.thread_configure','memo.material_preview','memo.material_remove'})
SUPPORTED={'.pdf','.md','.txt','.json','.docx','.png','.jpg','.jpeg','.webp'}
IMAGES={'.png','.jpg','.jpeg','.webp'}
MAX_FILES=32
MAX_FILE_BYTES=64*1024*1024
EXCERPT_CHARS=1800


def excerpts_of_attachment(artifact, pages):
    """Cut an attachment's text into excerpts of whole lines, as evidence.excerpts_of does for library
    and folder files, with two differences kept from earlier versions: an excerpt id also covers the
    excerpt's text, and blank excerpts are left out."""
    rows = []
    for page, text in pages:
        lines = text.splitlines()
        taken, size, start = [], 0, 1
        for end, line in enumerate(lines, 1):
            taken.append(line)
            size += len(line) + 1
            if size >= EXCERPT_CHARS or end == len(lines):
                content = '\n'.join(taken)
                if content.strip():
                    text_hash = digest(content)
                    excerpt_id = 'ev_' + digest([artifact['id'], artifact['content_hash'], page, start, end, text_hash])[:32]
                    rows.append((excerpt_id, artifact['id'], artifact['project'], content, start, end, page, text_hash))
                taken, size, start = [], 0, end + 1
    return rows


class Attachments:
    def __init__(self, workspace):
        self.w = workspace
        self.store = workspace.store
        self.root = self.store.root / 'attachments'
        windows_io_path(self.root).mkdir(exist_ok=True)
        self._locks = {}
        self._locks_guard = threading.Lock()

    def profile(self, thread):
        return thread.get('profile_ref', self.w.settings()['profile_ref'])

    def material(self, project, artifact_id, *, db=None):
        """The material artifact_id names, which must belong to project."""
        artifact = self.store.get('artifact', artifact_id, db=db)
        if not artifact or artifact['project'] != project:
            raise ValueError('MATERIAL_OUTSIDE_PROJECT')
        return artifact

    def configure(self, thread_id, expected_revision, changes):
        """Change a conversation's model and material scope (memo.thread_configure)."""
        if not isinstance(changes, dict) or not changes or set(changes) - {'profile_ref', 'artifact_ids', 'scope', 'include_project'}:
            raise ValueError('THREAD_CONFIG_INVALID')
        with self.store.tx() as db:
            thread = self.w.conversations._get(thread_id, expected_revision, db)
            if thread.get('archived'):
                raise ValueError('THREAD_ARCHIVED')
            if 'profile_ref' in changes:
                ref = changes['profile_ref']
                if ref is not None and not any(o['profile_ref'] == ref and o['eligible'] for o in self.w.catalog()):
                    raise ValueError('CHAT_MODEL_UNAVAILABLE')
            if 'artifact_ids' in changes:
                ids = changes['artifact_ids']
                if not isinstance(ids, list) or len(ids) > MAX_FILES or any(not isinstance(i, str) for i in ids):
                    raise ValueError('MATERIAL_SELECTION_INVALID')
                for artifact_id in ids:
                    self.material(thread['project'], artifact_id, db=db)
                # Material dropped from the selection stays out of the conversation's scope.
                removed = set(thread.get('artifact_ids', [])) - set(ids)
                excluded = (set(thread.get('excluded_artifact_ids', [])) | removed) - set(ids)
                changes = dict(changes, artifact_ids=list(dict.fromkeys(ids)), excluded_artifact_ids=sorted(excluded))
            if 'include_project' in changes:
                if type(changes['include_project']) is not bool:
                    raise ValueError('MATERIAL_SCOPE_INVALID')
                changes = dict(changes, scope='project' if changes['include_project'] else 'conversation')
            if 'scope' in changes:
                changes = dict(changes, include_project=changes['scope'] == 'project')
            if changes.get('scope', thread.get('scope', 'project')) not in {'project', 'conversation'}:
                raise ValueError('MATERIAL_SCOPE_INVALID')
            return self.store.put('thread', thread_id, thread['project'], dict(thread, **changes), expected=expected_revision, db=db)

    def attach(self, thread_id, paths):
        """Add files chosen in the window to a conversation (memo.attach_files). Each file gets its own
        result; their preparation runs as one attachment job."""
        thread = self.w.thread_get(thread_id)
        # Refused while the conversation is busy (an attachment job or a question running).
        with self.store.tx() as db:
            self.w.conversations._get(thread_id, thread['revision'], db)
        if thread.get('archived'):
            raise ValueError('THREAD_ARCHIVED')
        if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FILES:
            raise ValueError('ATTACHMENT_FILES_REQUIRED')
        job_id = uid('attach_')
        ref = self.profile(thread)
        items = []
        for value in paths:
            try:
                items.append(self._attach_one(thread, thread_id, value, ref))
            except (ValueError, OSError, TypeError, UnicodeError) as exc:
                items.append({'name': Path(str(value)).name, 'status': 'ERROR', 'error': str(exc)[:140]})
        job = self.store.put('attachment_job', job_id, thread['project'], {
            'thread_id': thread_id, 'project': thread['project'], 'profile_ref': ref, 'status': 'QUEUED',
            'items': items, 'created_at': now()})
        with self.w.lock:
            self.w.futures[job_id] = self.w.pool.submit(self._prepare, job_id)
        return job

    def _attach_one(self, thread, thread_id, value, ref):
        if not isinstance(value, str):
            raise ValueError('ATTACHMENT_PATH_INVALID')
        path = Path(value)
        io_path = windows_io_path(path)
        if not path.is_absolute() or not io_path.is_file() or io_path.is_symlink():
            raise ValueError('ATTACHMENT_PATH_INVALID')
        if getattr(io_path.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise ValueError('SOURCE_LINK_REJECTED')
        if path.suffix.lower() not in SUPPORTED:
            raise ValueError('ATTACHMENT_FORMAT_UNSUPPORTED')
        if io_path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('SOURCE_TOO_LARGE')
        raw = io_path.read_bytes()
        if not raw:
            raise ValueError('ATTACHMENT_EMPTY')
        if path.suffix.lower() == '.json':
            handed_on = self._memo_package(thread_id, path, raw)
            if handed_on is not None:
                return handed_on
        content_hash = digest(raw)
        identity = 'art_' + digest([thread['project'], 'attachment', thread_id if thread['temporary'] else '', content_hash])[:32]
        reused = self.w.skill.reuse_original(thread_id, content_hash)
        if reused:
            return dict(reused, name=path.name)
        dest = self.root / (content_hash + path.suffix.lower())
        io_dest = windows_io_path(dest)
        if io_dest.exists():  # added before: the copy on disk must still be that content
            if digest(io_dest.read_bytes()) != content_hash:
                raise ValueError('ATTACHMENT_SNAPSHOT_CONFLICT')
        else:
            with io_dest.open('xb') as output:
                output.write(raw)
        with self.store.tx() as db:
            old = self.store.get('artifact', identity, db=db)
            reusable = bool(old and old['state'] == 'active')
            pending = bool(old and old['state'] == 'preparing')
            if not reusable and not pending:
                self.store.put('artifact', identity, thread['project'], {
                    'project': thread['project'], 'title': (old or {}).get('title', path.name), 'path': str(dest),
                    'root': str(self.root), 'content_hash': content_hash, 'kind': 'attachment', 'state': 'preparing',
                    'document_id': 'doc_' + content_hash, 'temporary_thread': thread_id if thread['temporary'] else None,
                    'original_name': (old or {}).get('original_name', path.name), 'created_at': now(), 'chunks': 0,
                    'profile_ref': ref}, db=db)
            fresh = self.store.get('thread', thread_id, db=db)
            if not fresh or fresh.get('archived'):
                raise ValueError('THREAD_ARCHIVED')
            ids = list(dict.fromkeys(fresh.get('artifact_ids', []) + [identity]))
            if len(ids) > MAX_FILES:
                raise ValueError('ATTACHMENT_SCOPE_LIMIT')
            excluded = [i for i in fresh.get('excluded_artifact_ids', []) if i != identity]
            self.store.put('thread', thread_id, thread['project'], dict(fresh, artifact_ids=ids, excluded_artifact_ids=excluded), db=db)
        return {'id': identity, 'name': path.name, 'status': 'READY' if reusable else 'QUEUED', 'reused': reusable}

    def _memo_package(self, thread_id, path, raw):
        """A memo-source package or a returned agent answer goes to the skill bridge; other JSON files
        are ordinary attachments (None)."""
        candidate = json.loads(raw.decode('utf-8-sig'))
        schema = candidate.get('schema', '') if isinstance(candidate, dict) else ''
        if isinstance(schema, str) and schema.startswith('memo-source/'):
            result = self.w.skill.import_source(thread_id, path)
            fields = ('id', 'status', 'reused', 'source_sha256', 'conversion_called', 'warnings')
            return dict({k: result[k] for k in fields}, name=path.name)
        if isinstance(schema, str) and schema.startswith('memo-research-answer/'):
            if schema != 'memo-research-answer/1':
                raise ValueError('SKILL_ANSWER_SCHEMA_UNSUPPORTED')
            binding = self.store.get('handoff', candidate.get('handoff_id', ''))
            if not binding or binding['thread_id'] != thread_id:
                raise ValueError('HANDOFF_THREAD_MISMATCH')
            result = self.w.skill.answer(**{k: v for k, v in candidate.items() if k != 'schema'})
            return dict(name=path.name, status='READY', answer_return=result)
        return None

    def status(self, job_id):
        job = self.store.get('attachment_job', job_id)
        if not job:
            raise ValueError('ATTACHMENT_JOB_NOT_FOUND')
        return job

    def _prepare(self, job_id):
        """Worker: extract and index each queued file of the job, one at a time per file."""
        job = self.status(job_id)
        job['status'] = 'RUNNING'
        self.store.put('attachment_job', job_id, job['project'], job)
        for item in job['items']:
            if item['status'] != 'QUEUED':
                continue
            with self._locks_guard:
                lock = self._locks.setdefault(item['id'], threading.Lock())
            lock.acquire()
            artifact = self.store.get('artifact', item['id'])
            try:
                if artifact['state'] == 'active':  # another job prepared the same file meanwhile
                    item['status'] = 'READY'
                    continue
                self._index(artifact, job['profile_ref'], job_id)
                item['status'] = 'READY'
            except Exception as exc:
                item.update(status='ERROR', error=str(exc)[:180])
                self.store.put('artifact', artifact['id'], artifact['project'], dict(artifact, state='error', error=item['error']))
            finally:
                lock.release()
            self.store.put('attachment_job', job_id, job['project'], job)
        job['status'] = 'COMPLETE' if all(x['status'] == 'READY' for x in job['items']) else 'PARTIAL'
        self.store.put('attachment_job', job_id, job['project'], job)

    def _index(self, artifact, profile_ref, job_id):
        """Extract the file's text, keep the extraction next to the copy, and write its excerpts."""
        pages, receipts = self._pages(artifact, profile_ref, job_id)
        if not any(text.strip() for _, text in pages):
            raise ValueError('ATTACHMENT_TEXT_EMPTY')
        rows = excerpts_of_attachment(artifact, pages)
        extraction = self.root / (artifact['content_hash'] + '.' + digest(pages)[:16] + '.pages.json')
        encoded = packed(pages).encode('utf-8')
        io_extraction = windows_io_path(extraction)
        if not io_extraction.exists():
            with io_extraction.open('xb') as output:
                output.write(encoded)
        with self.store.tx() as db:
            self.store.put('artifact', artifact['id'], artifact['project'], dict(
                artifact, state='active', indexed_at=now(), chunks=len(rows), extraction_path=str(extraction),
                extraction_hash=digest(encoded), page_count=len(pages), vision_receipts=receipts, error=None), db=db)
            db.execute('DELETE FROM chunks WHERE artifact_id=?', (artifact['id'],))
            db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)', rows)

    def _pages(self, artifact, profile_ref, job_id):
        """The file's text by page. Pictures, and PDF pages without a text layer, are read by the
        vision model of the conversation; each call's receipt is kept."""
        path = Path(artifact['path'])
        raw = windows_io_path(path).read_bytes()
        suffix = path.suffix.lower()
        receipts = []

        def vision(data, mime, page):
            if not profile_ref:
                raise ValueError('ATTACHMENT_VISION_MODEL_REQUIRED')
            if not self.w.vision:
                raise ValueError('ATTACHMENT_VISION_UNAVAILABLE')
            result = self.w.vision(profile_ref=profile_ref, image_bytes=data, mime_type=mime,
                                   job_id='research-' + digest([job_id, artifact['id'], page])[:24])
            receipt = result.get('execution_receipt') or {}
            if result.get('status') != 'PASS' or receipt.get('status') != 'PASS':
                raise ValueError(result.get('reason') or 'ATTACHMENT_VISION_FAILED')
            from memorive_settings.cli_templates import receipt_evidence_complete
            if not receipt_evidence_complete(receipt):
                raise ValueError('ATTACHMENT_VISION_RECEIPT_REQUIRED')
            receipts.append(receipt)
            return result['text']

        if suffix == '.pdf':
            import fitz
            with fitz.open(stream=raw, filetype='pdf') as doc:
                if doc.needs_pass:
                    raise ValueError('ATTACHMENT_PASSWORD_REQUIRED')
                pages = []
                for page in doc:
                    text = page.get_text()
                    if not text.strip() or (len(text.strip()) < 32 and page.get_images()):
                        pixels = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
                        text = vision(pixels.tobytes('png'), 'image/png', page.number + 1)
                    pages.append((page.number + 1, text))
                return pages, receipts
        if suffix in IMAGES:
            from PIL import Image, ImageOps
            with Image.open(io.BytesIO(raw)) as image:
                image = ImageOps.exif_transpose(image).convert('RGB')
                image.thumbnail((2400, 2400))
                output = io.BytesIO()
                image.save(output, format='PNG')
            return [(1, vision(output.getvalue(), 'image/png', 1))], receipts
        if suffix == '.docx':
            from docx import Document
            doc = Document(io.BytesIO(raw))
            paragraphs = [p.text for p in doc.paragraphs]
            table_rows = [' | '.join(c.text for c in row.cells) for table in doc.tables for row in table.rows]
            return [(None, '\n'.join(paragraphs + table_rows))], receipts
        text = raw.decode('utf-8-sig')
        if suffix == '.json':
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        return [(None, text)], receipts

    def preview(self, project, artifact_id):
        """The first excerpts of a material (memo.material_preview)."""
        artifact = self.material(project, artifact_id)
        with self.store.tx() as db:
            rows = db.execute('SELECT id FROM chunks WHERE artifact_id=? ORDER BY page,line_start LIMIT 6', (artifact_id,))
            refs = [self.w.index.read(row[0], project, db=db) for row in rows]
        return {'artifact': artifact, 'evidence': refs}

    def remove(self, thread_id, expected_revision, artifact_ids):
        """Take material out of a conversation (memo.material_remove)."""
        self.w.thread_get(thread_id)  # an expired temporary conversation is forgotten here
        if not isinstance(artifact_ids, list) or not artifact_ids or any(not isinstance(i, str) for i in artifact_ids):
            raise ValueError('MATERIAL_SELECTION_INVALID')
        with self.store.tx() as db:
            thread = self.w.conversations._get(thread_id, expected_revision, db)
            if thread.get('archived'):
                raise ValueError('THREAD_ARCHIVED')
            for artifact_id in artifact_ids:
                self.material(thread['project'], artifact_id, db=db)
            # Removing from a conversation never destroys the original or another conversation's evidence.
            value = dict(thread, artifact_ids=[i for i in thread.get('artifact_ids', []) if i not in artifact_ids],
                         excluded_artifact_ids=sorted(set(thread.get('excluded_artifact_ids', [])) | set(artifact_ids)))
            self.store.event('MATERIALS_REMOVED_FROM_THREAD', thread_id, {'artifact_ids': artifact_ids}, db)
            return self.store.put('thread', thread_id, thread['project'], value, expected=expected_revision, db=db)

    def scope_ids(self, thread, *, db=None):
        """The material a conversation draws on: its own, plus the project's when it includes the project,
        minus what was taken out of it."""
        selected = list(thread.get('artifact_ids', []))
        if thread.get('include_project', thread.get('scope', 'project') == 'project'):
            selected += [a['id'] for a in self.store.list('artifact', thread['project'], db=db)
                         if a['state'] == 'active' and not a.get('temporary_thread')]
        excluded = thread.get('excluded_artifact_ids', [])
        return list(dict.fromkeys(i for i in selected if i not in excluded))

    def call(self, method, params):
        handlers = {'memo.attach_files': self.attach, 'memo.attachment_status': self.status,
                    'memo.thread_configure': self.configure, 'memo.material_preview': self.preview,
                    'memo.material_remove': self.remove}
        return handlers[method](**params)
