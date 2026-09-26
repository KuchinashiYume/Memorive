"""Explicit-root indexing and source-bound search with fail-closed freshness."""
import collections, math, os, re, stat
from pathlib import Path
from urllib.parse import quote
from .store import digest,packed,now
from memorive_folder_management.policy import windows_io_path

DENY_DIRS={'.git','.obsidian','.codex','.claude','node_modules','__pycache__','.venv','venv'}
EXTENSIONS={'.md','.txt','.json','.pdf'}
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
    words=re.findall(r'[a-z0-9_]{2,}|[\u3400-\u9fff]+',text.lower())
    return [p for w in words for p in ([w] if not re.match('[\u3400-\u9fff]',w) else ([w] if len(w)==1 else [w[i:i+2] for i in range(len(w)-1)]))]

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

class EvidenceIndex:
    def __init__(self,store):self.store=store;self.rank_model=None;self.embed_model=None;self.profile_identity=None

    def add_library_file(self,project,*,path,root,external_id,expected_hash,title,document_id=None):
        if not self.store.get('project',project):raise ValueError('PROJECT_NOT_FOUND')
        path=safe_path(root,path);raw,pages=read_document(path)
        if digest(raw).lower()!=expected_hash.lower():raise ValueError('LIBRARY_SOURCE_HASH_MISMATCH')
        snapshot_root=self.store.root/'library_snapshots';windows_io_path(snapshot_root).mkdir(exist_ok=True)
        snapshot=snapshot_root/(digest(raw)+path.suffix.lower());snapshot_io=windows_io_path(snapshot)
        if not snapshot_io.exists():
            with snapshot_io.open('xb') as output:output.write(raw)
        if digest(snapshot_io.read_bytes())!=digest(raw):raise ValueError('LIBRARY_SOURCE_HASH_MISMATCH')
        path=snapshot;root=snapshot_root
        identity='art_'+digest([project,'library',external_id])[:32];chunks=[]
        for page,text in pages:
            buf=[];size=0;start=1;lines=text.splitlines()
            for number,line in enumerate(lines,1):
                buf.append(line);size+=len(line)+1
                if size>=1800 or number==len(lines):
                    content='\n'.join(buf);cid='ev_'+digest([identity,digest(raw),page,start,number])[:32]
                    chunks.append((cid,identity,project,content,start,number,page,digest(content)))
                    buf=[];size=0;start=number+1
        with self.store.tx() as db:
            row=self.store.put('artifact',identity,project,{'project':project,'title':title,'path':str(path),
                'root':str(Path(root).resolve()),'content_hash':digest(raw),'state':'active','kind':'library',
                'indexed_at':now(),'chunks':len(chunks),'external_id':external_id,'document_id':document_id or 'doc_'+digest(raw),'selection':'EXPLICIT_DESKTOP_USER'},db=db)
            db.execute('DELETE FROM chunks WHERE artifact_id=?',(identity,))
            db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)',chunks)
        return row

    def refresh(self,project):
        scope=self.store.get('project',project)
        if not scope:raise ValueError('PROJECT_NOT_FOUND')
        if not scope.get('vault'):raise ValueError('VAULT_REQUIRED')
        root=Path(scope['vault']).resolve(strict=True)
        failures=[];rows=[];seen=set()
        for parent,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=sorted(d for d in dirs if not d.startswith('.') and d not in DENY_DIRS and not Path(parent,d).is_symlink())
            for name in sorted(files):
                path=Path(parent,name)
                if path.suffix.lower() not in EXTENSIONS or name.startswith('.'):continue
                # A Vault attachment scan never reads app settings/credentials, even if renamed JSON.
                if path.suffix.lower()=='.json' and not (name.lower().endswith('.card.json') or name.lower()=='card.json'):continue
                try:
                    path=safe_path(root,path);raw,pages=read_document(path)
                    identity='art_'+digest([project,str(path.relative_to(root)).casefold()])[:32];seen.add(identity)
                    old=self.store.get('artifact',identity)
                    if old and old['content_hash']==digest(raw) and old['state']=='active':rows.append(identity);continue
                    chunks=[]
                    for page,text in pages:
                        lines=text.splitlines();buf=[];start=1
                        for i,line in enumerate(lines,1):
                            buf.append(line)
                            if sum(len(s)+1 for s in buf)>=1800 or i==len(lines):
                                content='\n'.join(buf);chunk_id='ev_'+digest([identity,digest(raw),page,start,i])[:32]
                                chunks.append((chunk_id,identity,project,content,start,i,page,digest(content)));buf=[];start=i+1
                    with self.store.tx() as db:
                        self.store.put('artifact',identity,project,{'project':project,'title':path.stem,'path':str(path),'root':str(root),
                            'content_hash':digest(raw),'state':'active','kind':'source','indexed_at':now(),'chunks':len(chunks)},db=db)
                        db.execute('DELETE FROM chunks WHERE artifact_id=?',(identity,))
                        db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)',chunks)
                    rows.append(identity)
                except (ValueError,OSError,UnicodeError) as exc:failures.append({'name':name,'code':str(exc)[:100]})
        with self.store.tx() as db:
            for item in self.store.list('artifact',project,db=db):
                if item['kind']=='source' and item['id'] not in seen:
                    self.store.put('artifact',item['id'],project,dict(item,state='unavailable'),db=db)
            # Version and source deletion invalidate query caches and derived knowledge on every refresh.
            scope=self.store.get('project',project,db=db)
            self.store.put('project',project,project,dict(scope,index_epoch=scope['index_epoch']+1),db=db)
        return {'status':'PARTIAL' if failures else 'PASS','indexed':len(rows),'failures':failures,'project':project}

    def read(self,evidence_id,project,*,expected_hash=None,db=None,seen=None):
        if db is None:
            with self.store.tx() as conn:return self.read(evidence_id,project,expected_hash=expected_hash,db=conn,seen=seen)
        seen=set() if seen is None else set(seen)
        if evidence_id in seen:raise ValueError('EVIDENCE_CYCLE')
        seen.add(evidence_id)
        row=db.execute('SELECT * FROM chunks WHERE id=? AND project=?',(evidence_id,project)).fetchone()
        if not row:raise ValueError('EVIDENCE_NOT_FOUND')
        artifact=self.store.get('artifact',row['artifact_id'],db=db)
        if artifact['state']!='active':raise ValueError('EVIDENCE_INACTIVE')
        binding=artifact.get('source_binding') or {}
        if binding:
            connection=self.store.get('connection',binding['connection_id'],db=db)
            document=self.store.get('connector_document',binding['source_id'],db=db)
            if not connection or not connection['enabled'] or not document or document['state']!='active':raise ValueError('EVIDENCE_INACTIVE')
            if document.get('path'):
                original=safe_path(connection['root'],document['path'])
                if digest(windows_io_path(original).read_bytes())!=document['file_hash']:raise ValueError('EVIDENCE_STALE')
        if artifact['kind'] in {'source','library'}:path=safe_path(artifact['root'],artifact['path'])
        else:
            path=Path(artifact['path']).resolve(strict=False)
            if not path.is_relative_to(self.store.root) or not windows_io_path(path).is_file():raise ValueError('EVIDENCE_OUTSIDE_WORKSPACE')
            for ref in artifact.get('parents',[]):self.read(ref['id'],project,expected_hash=ref['content_hash'],db=db,seen=seen)
        if digest(windows_io_path(path).read_bytes())!=artifact['content_hash']:raise ValueError('EVIDENCE_STALE')
        if artifact.get('extraction_path'):
            extraction=Path(artifact['extraction_path']).resolve(strict=False)
            if not extraction.is_relative_to(self.store.root) or not windows_io_path(extraction).is_file() or digest(windows_io_path(extraction).read_bytes())!=artifact['extraction_hash']:raise ValueError('EVIDENCE_STALE')
        if digest(row['text'])!=row['hash']:raise ValueError('EVIDENCE_STALE')
        if expected_hash and expected_hash!=artifact['content_hash']:raise ValueError('EVIDENCE_VERSION_CONFLICT')
        uri=None
        if binding.get('provider')=='zotero' and binding.get('attachment_key'):
            uri='zotero://open-pdf/library/items/'+binding['attachment_key']+('?page='+str(row['page']) if row['page'] else '')
        elif binding.get('provider')=='obsidian' and binding.get('relative_path'):
            uri='obsidian://open?vault='+quote(binding['vault_name'],safe='')+'&file='+quote(binding['relative_path'].replace('\\','/'),safe='')
        field=self.store.get('research_field',evidence_id,db=db)
        if field and (field['content_hash']!=artifact['content_hash'] or field['text_hash']!=row['hash']):raise ValueError('EVIDENCE_STALE')
        return {'id':evidence_id,'artifact_id':artifact['id'],'title':artifact['title']+(' · '+field['field_label'] if field else ''),'text':row['text'],'card_field':field,
            'content_hash':artifact['content_hash'],'chunk_hash':row['hash'],'path':artifact['path'],
            'line_start':row['line_start'],'line_end':row['line_end'],'page':row['page'],'kind':artifact['kind'],'project':project,
            'document_id':binding.get('document_id',artifact.get('document_id',artifact['id'])),'source_uri':uri,'source_binding':binding}

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
