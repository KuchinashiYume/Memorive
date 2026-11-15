"""The evidence index: documents become numbered excerpts that answers cite.

Material comes in two ways:
  add_library_file   a file chosen from the library. The workspace keeps its own copy, named by the
                     content's hash, under library_snapshots/, and cites that copy.
  refresh            the files of a project's own folder (its vault), read where they are.
excerpts_of() cuts a document into excerpts of whole lines; an answer cites an excerpt by its id.

read() returns an excerpt only while the files it rests on still have their recorded hashes: the
document or its snapshot, the original it was made from, the text extracted from it, and the
document of a connected app it came through. Inside checking_each_file_once() each of those files
is hashed once, however many of its excerpts are read; outside it every read checks again.
Answering gathers evidence inside one such block and checks the evidence again in a new block
before saving, so a file changed while the model was answering is still noticed.

safe_path() decides which files may be read at all: only below the given root, not through links,
not inside private folders (.git, .obsidian, node_modules ...).
"""
import collections, contextlib, contextvars, math, os, re, stat
from pathlib import Path
from urllib.parse import quote
from .store import digest,packed,now
from memorive_folder_management.policy import windows_io_path

DENY_DIRS={'.git','.obsidian','.codex','.claude','node_modules','__pycache__','.venv','venv'}
EXTENSIONS={'.md','.txt','.json','.pdf'}
LAYERS={'original','cleaned','card','analysis','unknown'}
EXCERPT_CHARS=1800  # an excerpt ends with the first line that brings it to this many characters
# The files already hashed in the current checking_each_file_once() block: (path, hash) -> unchanged.
_checked=contextvars.ContextVar('evidence_files_checked',default=None)

def material_layer(artifact):
    layer=artifact.get('material_layer')
    if layer in {'original','cleaned','card','analysis','confirmed_knowledge'}:return layer
    if artifact.get('kind')=='preprocessed_full_test':return 'original'
    if artifact.get('kind') in {'source','attachment'} and Path(artifact.get('path','')).suffix.lower()=='.pdf':return 'original'
    if artifact.get('kind')=='derived':return 'confirmed_knowledge'
    return 'unknown'

def source_document_hash(artifact):
    """Use recorded lineage, never infer parentage from a title or shared words."""
    value=artifact.get('source_content_hash')
    if not value and material_layer(artifact)=='original' and Path(artifact.get('path','')).suffix.lower()=='.pdf':value=artifact.get('content_hash')
    # Earlier library bridge versions encoded the verified source hash here.
    if not value and artifact.get('kind')=='library':
        match=re.fullmatch(r'doc_([a-fA-F0-9]{64})',artifact.get('document_id',''))
        if match:value=match.group(1)
    return value.lower() if isinstance(value,str) and re.fullmatch(r'[a-fA-F0-9]{64}',value) else None
def safe_path(root,path):
    root=Path(root).resolve(strict=False);path=Path(path)
    if not windows_io_path(root).is_dir():raise ValueError('SOURCE_ROOT_UNAVAILABLE')
    try:rel=path.absolute().relative_to(root)
    except ValueError:raise ValueError('SOURCE_OUTSIDE_VAULT')
    cur=root
    for part in rel.parts:
        if part.startswith('.') or part in DENY_DIRS:raise ValueError('SOURCE_PRIVATE_DIRECTORY')
        cur/=part
        cur_io=windows_io_path(cur);info=cur_io.lstat()
        if cur.is_symlink() or cur_io.is_symlink() or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400):raise ValueError('SOURCE_LINK_REJECTED')
    resolved=path.resolve(strict=False)
    if not resolved.is_relative_to(root) or not windows_io_path(resolved).is_file():raise ValueError('SOURCE_OUTSIDE_VAULT')
    return resolved

def tokens(text):
    words=re.findall(r'[a-z0-9_]{2,}|[㐀-鿿]+',text.lower())
    return [p for w in words for p in ([w] if not re.match('[㐀-鿿]',w) else ([w] if len(w)==1 else [w[i:i+2] for i in range(len(w)-1)]))]

def read_document(path):
    path_io=windows_io_path(path)
    if path_io.stat().st_size>24*1024*1024:raise ValueError('SOURCE_TOO_LARGE')
    raw=path_io.read_bytes()
    if path.suffix.lower()=='.pdf':
        try:
            import fitz
            with fitz.open(stream=raw,filetype='pdf') as doc:
                pages=[(p.number+1,p.get_text()) for p in doc]
        except ImportError:
            import pypdf,io
            doc=pypdf.PdfReader(io.BytesIO(raw));pages=[(i+1,p.extract_text() or '') for i,p in enumerate(doc.pages)]
        if not any(t.strip() for _,t in pages):raise ValueError('SOURCE_REQUIRES_EXISTING_OCR')
    else:pages=[(None,raw.decode('utf-8-sig'))]
    return raw,pages


def excerpts_of(identity, project, raw, pages):
    """Cut a document into excerpts of whole lines, page by page. Each is a row of the chunks table:
    (excerpt id, artifact id, project, text, first line, last line, page, hash of the text)."""
    document_hash = digest(raw)
    rows = []
    for page, text in pages:
        lines = text.splitlines()
        taken, size, start = [], 0, 1
        for number, line in enumerate(lines, 1):
            taken.append(line)
            size += len(line) + 1
            if size >= EXCERPT_CHARS or number == len(lines):
                content = '\n'.join(taken)
                excerpt_id = 'ev_' + digest([identity, document_hash, page, start, number])[:32]
                rows.append((excerpt_id, identity, project, content, start, number, page, digest(content)))
                taken, size, start = [], 0, number + 1
    return rows


def replace_excerpts(db, identity, rows):
    db.execute('DELETE FROM chunks WHERE artifact_id=?', (identity,))
    db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)', rows)


@contextlib.contextmanager
def checking_each_file_once():
    """Within this block a file that excerpts rest on is hashed once, not once per excerpt read."""
    token = _checked.set({})
    try:
        yield
    finally:
        _checked.reset(token)


def file_unchanged(path, expected_hash):
    """Whether the file still has the hash recorded for it (hashed once per checking block)."""
    checked = _checked.get()
    key = (str(path), expected_hash)
    if checked is not None and key in checked:
        return checked[key]
    unchanged = digest(windows_io_path(path).read_bytes()) == expected_hash
    if checked is not None:
        checked[key] = unchanged
    return unchanged


def page_layout(folder):
    """The page layout (structure bundle) in folder, loaded and verified once per checking block."""
    checked = _checked.get()
    key = ('page layout', str(folder))
    if checked is not None and key in checked:
        return checked[key]
    from document_processing.document_structure.bundle import load
    _receipt, structure = load(folder)
    if checked is not None:
        checked[key] = structure
    return structure


class EvidenceIndex:
    def __init__(self,store):self.store=store;self.rank_model=None;self.embed_model=None;self.profile_identity=None

    def add_library_file(self, project, *, path, root, external_id, expected_hash, title, document_id=None,
                         material_layer='unknown', source_content_hash=None, structure_bundle=None):
        """Index a file chosen from the library. It must have expected_hash; the workspace keeps a copy
        of it and cites the copy. structure_bundle: the folder of its page layout, if it has one."""
        if not self.store.get('project', project):
            raise ValueError('PROJECT_NOT_FOUND')
        if material_layer not in LAYERS:
            raise ValueError('LIBRARY_MATERIAL_LAYER_INVALID')
        if source_content_hash is not None and (not isinstance(source_content_hash, str)
                                                or not re.fullmatch(r'[a-fA-F0-9]{64}', source_content_hash)):
            raise ValueError('LIBRARY_SOURCE_LINEAGE_INVALID')
        path = safe_path(root, path)
        raw, pages = read_document(path)
        raw_hash = digest(raw)
        if raw_hash.lower() != expected_hash.lower():
            raise ValueError('LIBRARY_SOURCE_HASH_MISMATCH')
        structure_binding = None
        if structure_bundle is not None:
            structure_binding = self._page_layout_binding(root, structure_bundle, expected_hash, source_content_hash)
        snapshot = self._snapshot(raw, raw_hash, path.suffix.lower())
        identity = 'art_' + digest([project, 'library', external_id])[:32]
        rows = excerpts_of(identity, project, raw, pages)
        artifact = {
            'project': project, 'title': title, 'path': str(snapshot), 'root': str(snapshot.parent.resolve()),
            'content_hash': raw_hash, 'state': 'active', 'kind': 'library', 'indexed_at': now(), 'chunks': len(rows),
            'external_id': external_id, 'document_id': document_id or 'doc_' + raw_hash, 'material_layer': material_layer,
            'source_content_hash': source_content_hash.lower() if source_content_hash else None,
            'selection': 'EXPLICIT_DESKTOP_USER', **({'structure_binding': structure_binding} if structure_binding else {})}
        with self.store.tx() as db:
            row = self.store.put('artifact', identity, project, artifact, db=db)
            replace_excerpts(db, identity, rows)
        return row

    def _page_layout_binding(self, root, structure_bundle, expected_hash, source_content_hash):
        """The page layout must be the one made from this text and its source document."""
        from document_processing.document_structure.bundle import load
        folder = safe_path(root, Path(structure_bundle) / 'bundle.json').parent
        _receipt, structure = load(folder)
        if (structure['rawmd_sha256'].lower() != expected_hash.lower()
                or structure['source_sha256'].lower() != str(source_content_hash).lower()):
            raise ValueError('LIBRARY_STRUCTURE_BINDING_MISMATCH')
        return {'path': str(folder), 'root': str(Path(root).resolve()), 'structure_id': structure['structure_id'],
                'source_sha256': structure['source_sha256'], 'rawmd_sha256': structure['rawmd_sha256']}

    def _snapshot(self, raw, raw_hash, suffix):
        """The workspace's copy of a library file, named by its content's hash. A copy already on disk
        (the same content was added before) must still hold that content."""
        folder = self.store.root / 'library_snapshots'
        windows_io_path(folder).mkdir(exist_ok=True)
        snapshot = folder / (raw_hash + suffix)
        snapshot_io = windows_io_path(snapshot)
        if snapshot_io.exists():
            if digest(snapshot_io.read_bytes()) != raw_hash:
                raise ValueError('LIBRARY_SOURCE_HASH_MISMATCH')
        else:
            with snapshot_io.open('xb') as output:
                output.write(raw)
        return snapshot

    def refresh(self, project):
        """Index the project folder's notes again: new and changed files are read, files no longer
        there become unavailable. Settings files and private folders are never read."""
        scope = self.store.get('project', project)
        if not scope:
            raise ValueError('PROJECT_NOT_FOUND')
        if not scope.get('vault'):
            raise ValueError('VAULT_REQUIRED')
        root = Path(scope['vault']).resolve(strict=True)
        failures, indexed, seen = [], [], set()
        for parent, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in DENY_DIRS and not Path(parent, d).is_symlink())
            for name in sorted(files):
                path = Path(parent, name)
                if path.suffix.lower() not in EXTENSIONS or name.startswith('.'):
                    continue
                # A JSON file is read only when it is a card; others may hold app settings or credentials.
                if path.suffix.lower() == '.json' and not (name.lower().endswith('.card.json') or name.lower() == 'card.json'):
                    continue
                try:
                    path = safe_path(root, path)
                    raw, pages = read_document(path)
                    identity = 'art_' + digest([project, str(path.relative_to(root)).casefold()])[:32]
                    seen.add(identity)
                    old = self.store.get('artifact', identity)
                    if old and old['content_hash'] == digest(raw) and old['state'] == 'active':
                        indexed.append(identity)
                        continue
                    rows = excerpts_of(identity, project, raw, pages)
                    artifact = {'project': project, 'title': path.stem, 'path': str(path), 'root': str(root),
                                'content_hash': digest(raw), 'state': 'active', 'kind': 'source', 'indexed_at': now(),
                                'chunks': len(rows)}
                    with self.store.tx() as db:
                        self.store.put('artifact', identity, project, artifact, db=db)
                        replace_excerpts(db, identity, rows)
                    indexed.append(identity)
                except (ValueError, OSError, UnicodeError) as exc:
                    failures.append({'name': name, 'code': str(exc)[:100]})
        with self.store.tx() as db:
            for item in self.store.list('artifact', project, db=db):
                if item['kind'] == 'source' and item['id'] not in seen:
                    self.store.put('artifact', item['id'], project, dict(item, state='unavailable'), db=db)
            # Version and source deletion invalidate query caches and derived knowledge on every refresh.
            scope = self.store.get('project', project, db=db)
            self.store.put('project', project, project, dict(scope, index_epoch=scope['index_epoch'] + 1), db=db)
        return {'status': 'PARTIAL' if failures else 'PASS', 'indexed': len(indexed), 'failures': failures, 'project': project}

    def read(self, evidence_id, project, *, expected_hash=None, db=None, seen=None):
        """One excerpt with the details of its document, while the files it rests on are unchanged.
        expected_hash: the document version a citation was made from. seen: excerpts already on the
        way here, when a derived note's sources are read."""
        if db is None:
            with self.store.tx() as conn:
                return self.read(evidence_id, project, expected_hash=expected_hash, db=conn, seen=seen)
        seen = set() if seen is None else set(seen)
        if evidence_id in seen:
            raise ValueError('EVIDENCE_CYCLE')
        seen.add(evidence_id)
        row = db.execute('SELECT * FROM chunks WHERE id=? AND project=?', (evidence_id, project)).fetchone()
        if not row:
            raise ValueError('EVIDENCE_NOT_FOUND')
        artifact = self.store.get('artifact', row['artifact_id'], db=db)
        if artifact['state'] != 'active':
            raise ValueError('EVIDENCE_INACTIVE')
        binding = artifact.get('source_binding') or {}
        self._check_files(artifact, binding, project, db, seen)
        if expected_hash and expected_hash != artifact['content_hash']:
            raise ValueError('EVIDENCE_VERSION_CONFLICT')
        uri = None
        if binding.get('provider') == 'zotero' and binding.get('attachment_key'):
            uri = 'zotero://open-pdf/library/items/' + binding['attachment_key'] + ('?page=' + str(row['page']) if row['page'] else '')
        elif binding.get('provider') == 'obsidian' and binding.get('relative_path'):
            uri = ('obsidian://open?vault=' + quote(binding['vault_name'], safe='')
                   + '&file=' + quote(binding['relative_path'].replace('\\', '/'), safe=''))
        # A card field shown as its own excerpt must belong to this version of the card.
        field = self.store.get('research_field', evidence_id, db=db)
        if field and (field['content_hash'] != artifact['content_hash'] or field['text_hash'] != row['hash']):
            raise ValueError('EVIDENCE_STALE')
        review = {}
        if artifact['kind'] == 'derived' and artifact.get('feedback_id'):
            proposal = self.store.get('feedback', artifact['feedback_id'], db=db)
            if proposal and proposal.get('artifact_id') == artifact['id']:
                review = {'knowledge_review': {
                    'feedback_id': proposal['id'], 'admission_state': proposal['state'],
                    'human_confirmed': proposal['human_confirmed'], 'revision': proposal['revision'],
                    'notice': 'Current admission metadata. Draft wording inside the saved title or text is historical content. Admission does not establish scientific correctness.'}}
        structure_refs = self._places_in_page_layout(artifact, row['text'])
        return {'id':evidence_id,'artifact_id':artifact['id'],'title':artifact['title']+(' · '+field['field_label'] if field else ''),'text':row['text'],'card_field':field,
            'content_hash':artifact['content_hash'],'chunk_hash':row['hash'],'path':artifact['path'],
            'line_start':row['line_start'],'line_end':row['line_end'],'page':row['page'],'kind':artifact['kind'],'project':project,
            'document_id':binding.get('document_id',artifact.get('document_id',artifact['id'])),'source_uri':uri,'source_binding':binding,
            'material_layer':material_layer(artifact),'source_content_hash':source_document_hash(artifact),**review,
            **({'line_basis':'rawmd_global','source_warnings':artifact['skill_source']['warnings']} if artifact.get('skill_source') else {}),
            **({'structure_refs':structure_refs} if structure_refs else {})}

    def _check_files(self, artifact, binding, project, db, seen):
        """Raise unless every file the excerpt rests on still has its recorded hash."""
        if binding:  # it came through a connected app (Zotero, Obsidian ...)
            connection = self.store.get('connection', binding['connection_id'], db=db)
            document = self.store.get('connector_document', binding['source_id'], db=db)
            if not connection or not connection['enabled'] or not document or document['state'] != 'active':
                raise ValueError('EVIDENCE_INACTIVE')
            if document.get('path'):
                original = safe_path(connection['root'], document['path'])
                if not file_unchanged(original, document['file_hash']):
                    raise ValueError('EVIDENCE_STALE')
        if artifact['kind'] in {'source', 'library'}:
            path = safe_path(artifact['root'], artifact['path'])
        else:  # made inside the workspace: a derived note, an attachment ...
            path = Path(artifact['path']).resolve(strict=False)
            if not path.is_relative_to(self.store.root) or not windows_io_path(path).is_file():
                raise ValueError('EVIDENCE_OUTSIDE_WORKSPACE')
            # A derived note holds only while the excerpts it was made from hold.
            for parent in artifact.get('parents', []):
                self.read(parent['id'], project, expected_hash=parent['content_hash'], db=db, seen=seen)
        if not file_unchanged(path, artifact['content_hash']):
            raise ValueError('EVIDENCE_STALE')
        if artifact.get('source_original_path'):
            original = safe_path(artifact['root'], artifact['source_original_path'])
            if not file_unchanged(original, artifact['source_content_hash']):
                raise ValueError('EVIDENCE_STALE')
        if artifact.get('extraction_path'):
            extraction = Path(artifact['extraction_path']).resolve(strict=False)
            if (not extraction.is_relative_to(self.store.root) or not windows_io_path(extraction).is_file()
                    or not file_unchanged(extraction, artifact['extraction_hash'])):
                raise ValueError('EVIDENCE_STALE')

    def _places_in_page_layout(self, artifact, text):
        """Where in the page layout this excerpt's text occurs exactly once (at most 16 places)."""
        link = artifact.get('structure_binding')
        if not link:
            return []
        folder = safe_path(link['root'], Path(link['path']) / 'bundle.json').parent
        structure = page_layout(folder)
        if structure['structure_id'] != link['structure_id']:
            raise ValueError('EVIDENCE_STRUCTURE_STALE')
        places = []
        for block in structure['blocks']:
            passage = block['text']
            if 12 <= len(passage) <= len(text) and text.count(passage) == 1:
                places.append({'structure_id': structure['structure_id'], 'block_id': block['block_id'],
                               'source_sha256': structure['source_sha256'], 'source_location': block.get('source_location'),
                               'character_start': text.index(passage), 'character_count': len(passage),
                               'status': 'EXACT_RAWMD_TEXT_LOCATION_CANDIDATE'})
                if len(places) == 16:
                    break
        return places

    def unavailable_derived(self,project,artifact_ids=None,*,db=None):
        """Report unusable knowledge without changing its human review state."""
        if db is None:
            with self.store.tx() as conn:return self.unavailable_derived(project,artifact_ids,db=conn)
        unavailable={}
        for artifact in self.store.list('artifact',project,db=db):
            if artifact['kind']!='derived' or artifact['state']!='active':continue
            if artifact_ids is not None and artifact['id'] not in artifact_ids:continue
            try:
                first=db.execute('SELECT id FROM chunks WHERE artifact_id=? LIMIT 1',(artifact['id'],)).fetchone()
                if first is None:raise ValueError('EVIDENCE_NOT_FOUND')
                self.read(first['id'],project,expected_hash=artifact['content_hash'],db=db)
            except ValueError as error:
                if str(error) not in {'EVIDENCE_NOT_FOUND','EVIDENCE_INACTIVE','EVIDENCE_STALE','EVIDENCE_VERSION_CONFLICT','SOURCE_ROOT_UNAVAILABLE'}:raise
                unavailable[artifact['id']]={'content_hash':artifact['content_hash'],'reason':str(error)}
            except FileNotFoundError:
                unavailable[artifact['id']]={'content_hash':artifact['content_hash'],'reason':'SOURCE_UNAVAILABLE'}
        return unavailable

    def search(self,query,project,limit=8,artifact_ids=None,policy_config=None,record=True):
        from retrieval_weighting.research_ranking import search
        return search(self,query,project,limit,artifact_ids,policy_config,record)

    def legacy_lexical_search(self,query,project,limit=8,artifact_ids=None):
        if not isinstance(query,str) or not query.strip() or len(query)>8000:raise ValueError('QUERY_INVALID')
        terms=set(tokens(query))
        if not terms:return {'results':[],'engine':'local lexical retrieval','stale_excluded':0}
        limit=max(1,min(int(limit),24));selected=None if artifact_ids is None else set(artifact_ids)
        with self.store.tx() as db:
            chunks=[dict(r) for r in db.execute('SELECT * FROM chunks WHERE project=?',(project,)) if selected is None or r['artifact_id'] in selected]
            temporary={a['id'] for a in self.store.list('artifact',project,db=db) if a.get('temporary_thread')}
            chunks=[c for c in chunks if c['artifact_id'] not in temporary or selected is not None and c['artifact_id'] in selected]
            counts=[collections.Counter(tokens(c['text'])) for c in chunks]
            frequency={w:sum(w in c for c in counts) for w in terms}
            n=len(chunks);avg=sum(sum(c.values()) for c in counts)/max(n,1)
            scored=[]
            for c,words in zip(chunks,counts):
                length=sum(words.values());score=sum(math.log(1+(n-frequency[w]+.5)/(frequency[w]+.5))*words[w]*2.2/(words[w]+1.2*(.25+.75*length/max(avg,1))) for w in terms if words[w])
                if score>0:scored.append((score,c))
            result=[];stale=0
            for score,c in sorted(scored,key=lambda x:(-x[0],x[1]['id'])):
                try:
                    ref=self.read(c['id'],project,db=db)
                    ref['score']=round(score*(.7 if ref['kind']=='derived' else 1),5);result.append(ref)
                except (OSError,ValueError):stale+=1;continue
                if len(result)>=limit*2:break
        return {'results':sorted(result,key=lambda r:-r['score'])[:limit],'engine':'local lexical retrieval','stale_excluded':stale}
