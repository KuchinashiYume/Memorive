"""Question-bound research skill bridge; source reuse is not knowledge admission."""
import json
import re
import sys
from pathlib import Path

from .store import digest, now, packed, uid
from .evidence import safe_path
from . import answer_coverage, answer_versions


METHOD_RULES = '''Use the memo-research reading method within this answer, without extra model rounds.
Read the relevant methods, results, conditions and counterevidence; organize a provisional
answer, then check consequential claims against the supplied text in one concentrated pass.
Correct or remove unsupported factual claims; renaming them inference does not supply evidence.
For newly introduced material explain its bearing on this question and any change to earlier
judgments. Reuse source text, but reason afresh about this question. State actual reading gaps.
Expand only useful concepts, mechanisms or assumptions; separate source statements, background,
inference and the user's hypotheses. Do not proactively recommend similar papers, titles,
authors or DOIs. Original, extracted text and analyses of one paper are one source lineage.
Give the direct answer, explanation with nearby citations, and material limits. Card generation
and knowledge admission are optional separate operations. Do not reveal internal drafts.
'''


def transport(workspace):
    command = ([sys.executable, '--memo-agent'] if getattr(sys, 'frozen', False) else
               [sys.executable, str(Path(__file__).with_name('agent_entry.py'))])
    return {'command': command + ['--workspace', str(workspace.store.root)]}


def _snapshot(path, data):
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('SKILL_SNAPSHOT_CONFLICT')
    else:
        with path.open('xb') as stream:
            stream.write(data)


def load_bundle(manifest):
    """Validate all bytes and ranges before mutating a conversation or its index."""
    path = Path(manifest).absolute()
    path = safe_path(path.parent, path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('SKILL_MANIFEST_TOO_LARGE')
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if value['schema'] != 'memo-source/1':
            raise ValueError('SKILL_SOURCE_SCHEMA_UNSUPPORTED')
        files = {}
        for key in ('original', 'raw'):
            relative = value[key]['path']
            if not isinstance(relative, str) or Path(relative).is_absolute():
                raise ValueError('SKILL_BUNDLE_PATH_INVALID')
            file = safe_path(path.parent, path.parent / relative)
            if file.stat().st_size > 64 * 1024 * 1024:
                raise ValueError('SOURCE_TOO_LARGE')
            data = file.read_bytes()
            if digest(data) != value[key]['sha256']:
                raise ValueError('SKILL_SOURCE_HASH_MISMATCH')
            files[key] = (file, data)
        if value['source_sha256'] != value['original']['sha256']:
            raise ValueError('SKILL_SOURCE_IDENTITY_MISMATCH')
        suffix = files['original'][0].suffix.lower()
        if suffix not in {'.pdf', '.md', '.txt'} or files['raw'][0].suffix.lower() != '.md':
            raise ValueError('SKILL_SOURCE_FORMAT_INVALID')
        if not isinstance(value['converter'], dict) or not value['converter']:
            raise ValueError('SKILL_CONVERTER_REQUIRED')
        if not isinstance(value['warnings'], list) or any(not isinstance(s, str) for s in value['warnings']):
            raise ValueError('SKILL_WARNINGS_INVALID')
        lines = files['raw'][1].decode('utf-8-sig').splitlines()
        pages = value['pages']
        if not isinstance(pages, list) or not pages or len(pages) > 20000:
            raise ValueError('SKILL_PAGES_INVALID')
        last = 0
        for number, page in enumerate(pages, 1):
            start, end = page['line_start'], page['line_end']
            if type(start) is not int or type(end) is not int or not last < start <= end <= len(lines):
                raise ValueError('SKILL_LINES_INVALID')
            expected = number if suffix == '.pdf' else None
            if page['page'] != expected or (expected is not None and type(page['page']) is not int):
                raise ValueError('SKILL_PAGE_INVALID')
            last = end
        if suffix != '.pdf' and len(pages) != 1:
            raise ValueError('SKILL_PAGE_INVALID')
        if suffix == '.pdf':
            import fitz
            try:
                with fitz.open(stream=files['original'][1], filetype='pdf') as document:
                    page_count = len(document)
            except (RuntimeError, ValueError) as error:
                raise ValueError('SKILL_ORIGINAL_PDF_INVALID') from error
            if page_count != len(pages):
                raise ValueError('SKILL_PAGE_COUNT_MISMATCH')
        if not any(line.strip() for p in pages for line in lines[p['line_start']-1:p['line_end']]):
            raise ValueError('ATTACHMENT_TEXT_EMPTY')
        return value, files, lines
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('SKILL_MANIFEST_INVALID') from error


class ResearchSkill:
    def __init__(self, workspace):
        self.w = workspace
        self.store = workspace.store

    def _select(self, thread, identity, db):
        if not thread or thread.get('archived'):
            raise ValueError('THREAD_NOT_AVAILABLE')
        ids = list(dict.fromkeys(thread.get('artifact_ids', []) + [identity]))
        if len(ids) > 32:
            raise ValueError('ATTACHMENT_SCOPE_LIMIT')
        if ids != thread.get('artifact_ids', []) or identity in thread.get('excluded_artifact_ids', []):
            self.store.put('thread', thread['id'], thread['project'], dict(thread, artifact_ids=ids,
                excluded_artifact_ids=[i for i in thread.get('excluded_artifact_ids', []) if i != identity]), db=db)

    def import_source(self, thread_id, manifest):
        value, files, lines = load_bundle(manifest)
        source_hash = value['source_sha256']
        key = digest([source_hash, value['raw']['sha256'], value['converter']])
        folder = self.store.root / 'skill_sources' / key
        folder.mkdir(parents=True, exist_ok=True)
        original = folder / ('original' + files['original'][0].suffix.lower())
        raw = folder / 'raw.md'
        _snapshot(original, files['original'][1])
        _snapshot(raw, files['raw'][1])
        with self.store.tx() as db:
            thread = self.store.get('thread', thread_id, db=db)
            if not thread or thread.get('archived'):
                raise ValueError('THREAD_NOT_AVAILABLE')
            identity = 'art_' + digest([thread['project'], 'skill', key, thread_id if thread['temporary'] else ''])[:32]
            old = self.store.get('artifact', identity, db=db)
            chunks = []
            for page in value['pages']:
                start = page['line_start']; buf = []; size = 0
                for end in range(start, page['line_end'] + 1):
                    buf.append(lines[end-1]); size += len(lines[end-1]) + 1
                    if size >= 1800 or end == page['line_end']:
                        text = '\n'.join(buf)
                        if text.strip():
                            eid = 'ev_' + digest([identity, page['page'], start, end, digest(text)])[:32]
                            chunks.append((eid, identity, thread['project'], text, start, end, page['page'], digest(text)))
                        buf = []; size = 0; start = end + 1
            self._select(thread, identity, db)
            if not old:
                title = Path(value.get('input_path') or files['original'][0].name).name
                self.store.put('artifact', identity, thread['project'], {
                    'project': thread['project'], 'title': title, 'path': str(raw), 'root': str(folder),
                    'content_hash': value['raw']['sha256'], 'kind': 'attachment', 'state': 'active',
                    'material_layer': 'cleaned', 'document_id': 'doc_' + source_hash,
                    'source_content_hash': source_hash, 'source_original_path': str(original),
                    'skill_source': {'converter': value['converter'], 'pages': value['pages'],
                        'warnings': value['warnings'], 'source_id': value.get('source_id'),
                        'line_basis': 'rawmd_global', 'semantic_fidelity': 'NOT_ASSESSED'},
                    'temporary_thread': thread_id if thread['temporary'] else None,
                    'created_at': now(), 'chunks': len(chunks), 'page_count': len(value['pages'])}, db=db)
                db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)', chunks)
            elif old['state'] != 'active':
                raise ValueError('SKILL_SOURCE_INACTIVE')
            # Actual Memo IDs, never the local S-* identity, form the mapping.
            refs = [self.w.index.read(c[0], thread['project'], db=db) for c in chunks]
            return {'id': identity, 'status': 'READY', 'reused': bool(old), 'source_sha256': source_hash,
                    'evidence': refs, 'conversion_called': False, 'warnings': value['warnings']}

    def reuse_original(self, thread_id, source_hash):
        with self.store.tx() as db:
            thread = self.store.get('thread', thread_id, db=db)
            if not thread or thread.get('archived'):
                raise ValueError('THREAD_NOT_AVAILABLE')
            for a in self.store.list('artifact', thread['project'], db=db):
                if (a.get('skill_source') and a['state'] == 'active' and a.get('source_content_hash') == source_hash
                        and a.get('temporary_thread') in (None, thread_id)):
                    row = db.execute('SELECT id FROM chunks WHERE artifact_id=? LIMIT 1', (a['id'],)).fetchone()
                    if row is None:
                        continue
                    self.w.index.read(row['id'], thread['project'], db=db)
                    self._select(thread, a['id'], db)
                    return {'id': a['id'], 'status': 'READY', 'reused': True, 'conversion_called': False}
        return None

    def _pack(self, project, handoff_id, handoff_hash, db):
        pack = self.w.handoffs.get(handoff_id, db)
        if pack['project'] != project or pack['content_hash'] != handoff_hash:
            raise ValueError('HANDOFF_BINDING_MISMATCH')
        thread = self.w.handoffs.validate(pack, db)
        return pack, thread

    def context(self, project, handoff_id, handoff_hash, artifact_id=None, cursor=0, limit=12):
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 24:
            raise ValueError('SKILL_PAGE_REQUEST_INVALID')
        with self.store.tx() as db:
            pack, _ = self._pack(project, handoff_id, handoff_hash, db)
            admitted = [s['id'] for s in pack['source_versions']]
            if artifact_id is not None and artifact_id not in admitted:
                raise ValueError('HANDOFF_EVIDENCE_OUTSIDE_SCOPE')
            sources = []
            for identity in admitted:
                a = self.store.get('artifact', identity, db=db)
                sources.append({k: a.get(k) for k in ('id', 'title', 'document_id', 'content_hash',
                    'source_content_hash', 'material_layer', 'chunks', 'page_count', 'skill_source')})
            refs = []; total = 0
            selected = [artifact_id] if artifact_id else admitted
            for identity in selected:
                rows = db.execute('SELECT id FROM chunks WHERE artifact_id=? ORDER BY page,line_start,id', (identity,)).fetchall()
                for row in rows:
                    if cursor <= total < cursor + limit:
                        refs.append(self.w.index.read(row['id'], project, db=db))
                    total += 1
            return {'schema': 'memo-research-context/1', 'project': project, 'thread_id': pack['thread_id'],
                    'handoff_id': handoff_id, 'handoff_hash': handoff_hash, 'question': pack['question'],
                    'sources': sources, 'evidence': refs, 'total_chunks': total,
                    'next_cursor': cursor + len(refs) if cursor + len(refs) < total else None,
                    'coverage_notice': 'Only returned excerpts were delivered; semantic reading is not assessed.',
                    'return_method': 'memo.skill_answer', 'knowledge_admitted': False}

    def answer(self, project, handoff_id, handoff_hash, request_id, answer, claims, coverage):
        from .skill_contract import validate_answer
        validate_answer(dict(project=project, handoff_id=handoff_id, handoff_hash=handoff_hash,
            request_id=request_id, answer=answer, claims=claims, coverage=coverage))
        if not re.fullmatch(r'[\w-]{8,96}', request_id):
            raise ValueError('REQUEST_ID_INVALID')
        identity = 'skill_return_' + digest([handoff_id, request_id])[:32]
        fingerprint = digest([handoff_hash, answer, claims, coverage])
        with self.store.tx() as db:
            pack = self.w.handoffs.get(handoff_id, db)
            if pack['project'] != project or pack['content_hash'] != handoff_hash:
                raise ValueError('HANDOFF_BINDING_MISMATCH')
            prior = self.store.get('skill_return', identity, db=db)
            if prior:
                if prior['request_hash'] != fingerprint:
                    raise ValueError('IDEMPOTENCY_CONFLICT')
                return prior['receipt']
            _, thread = self._pack(project, handoff_id, handoff_hash, db)
            if any(j.get('thread_id') == thread['id'] and j['status'] in {'QUEUED', 'RUNNING'}
                   for j in self.store.list('job', project, db=db)):
                raise ValueError('THREAD_BUSY')
            admitted = {s['id'] for s in pack['source_versions']}
            if len({r['artifact_id'] for r in coverage}) != len(coverage) or {r['artifact_id'] for r in coverage} != admitted:
                raise ValueError('SKILL_COVERAGE_SCOPE_MISMATCH')
            if any(r['status'] in {'partial', 'unread'} and not r['gaps'].strip() for r in coverage):
                raise ValueError('SKILL_COVERAGE_GAP_REQUIRED')
            statuses = {r['artifact_id']: r['status'] for r in coverage}
            refs = {}; verified = []
            for claim in claims:
                if claim['text'] not in answer:
                    raise ValueError('SKILL_CLAIM_NOT_IN_ANSWER')
                if claim['kind'] in {'source_statement', 'inference'} and not claim['supports']:
                    raise ValueError('SKILL_CLAIM_SUPPORT_REQUIRED')
                supports = []
                for support in claim['supports']:
                    ref = self.w.index.read(support['evidence_id'], project, expected_hash=support['content_hash'], db=db)
                    if ref['artifact_id'] not in admitted or statuses[ref['artifact_id']] == 'unread':
                        raise ValueError('HANDOFF_EVIDENCE_OUTSIDE_SCOPE')
                    if ' '.join(support['quote'].split()) not in ' '.join(ref['text'].split()):
                        raise ValueError('SKILL_QUOTE_NOT_IN_SOURCE')
                    refs[ref['id']] = ref
                    supports.append(dict(support, page=ref['page'], line_start=ref['line_start'], line_end=ref['line_end'],
                        artifact_id=ref['artifact_id'], chunk_hash=ref['chunk_hash'], traceability='CHECKED', semantic_support='NOT_ASSESSED'))
                verified.append(dict(claim, supports=supports))
            refs = list(refs.values())
            # Citation numbers in prose address the deduplicated first-use support order.
            if any(int(n) < 1 or int(n) > len(refs) for n in re.findall(r'\[(\d+)\]', answer)):
                raise ValueError('SKILL_CITATION_MARK_INVALID')
            path = answer_versions.visible(thread)
            user = {'id': uid('msg_'), 'role': 'user', 'text': pack['question'], 'created_at': now(),
                    'artifact_ids': sorted(admitted), 'parent_answer_id': path[-1]['id'] if path else None}
            plan = answer_coverage.plan(pack['question'], sorted(admitted))
            cov = answer_coverage.snapshot(self.store, project, plan, refs, refs, refs)
            message = {'id': uid('msg_'), 'role': 'assistant', 'text': answer, 'created_at': now(),
                'citations': refs, 'engine': 'memo-research-skill', 'answer_version': 1,
                'language_context': pack.get('language_context'), 'artifact_ids': sorted(admitted),
                'agent_result': True, 'needs_agent': False, 'usage': {}, 'elapsed_ms': 0,
                'evidence_context': {'question': pack['question'], 'coverage': cov, 'read': refs, 'retained': refs},
                'claim_reviews': [], 'review_binding': None,
                'skill_result': {'handoff_id': handoff_id, 'handoff_hash': handoff_hash,
                    'claims': verified, 'reported_coverage': coverage, 'semantic_support': 'NOT_ASSESSED'},
                'context': {'memory_ids': [], 'summary_message_ids': [], 'chars': len(answer)}}
            answer_versions.append(thread, user, message, selection_revision=thread['selection_revision'])
            if not thread.get('title_custom') and len(thread['messages']) == 2:
                thread['title'] = pack['question'][:70]
            self.store.put('thread', thread['id'], project, thread, db=db)
            receipt = {'status': 'RETURNED', 'thread_id': thread['id'], 'answer_id': message['id'],
                       'knowledge_admitted': False, 'citation_count': len(refs)}
            self.store.put('skill_return', identity, project, {'thread_id': thread['id'],
                'handoff_id': handoff_id, 'request_hash': fingerprint, 'receipt': receipt, 'created_at': now()}, db=db)
            return receipt
