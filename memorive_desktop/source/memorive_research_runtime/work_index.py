"""EVO E6 workspace identity index, independent of UI filtering and exposure.

SQLite serializes identity/file/publication binding. Backfill consumes bounded
metadata batches; discovery never opens a PDF or Card to answer identity queries.
"""
from contextlib import closing
from pathlib import Path
import json, re, sqlite3, threading
from .common import sha, now
from .relations import identity_keys, record_identifiers, canonical_doi, normalize_arxiv_id, ConnectorError
from .metadata_v3 import plain

INACTIVE = {'TRASHED','DELETED','DEDUPLICATED'}

def encode(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def title_key(value): return re.sub(r'[^\w]+',' ',str(value or '').casefold()).strip()

def manifestation(record):
    ids=record_identifiers(record)
    if ids.get('arxiv_id'):
        base,version=normalize_arxiv_id(str(ids['arxiv_id']).strip())
        try:
            observed_base,observed_version=normalize_arxiv_id(str(ids.get('source_record_id') or '').strip())
            if observed_base==base:version=observed_version
        except (ValueError,TypeError,ConnectorError):pass
        return 'arxiv:'+version
    if ids.get('doi'): return 'doi:'+canonical_doi(ids['doi'])
    return 'source:'+str(ids.get('source_record_id') or record.get('source_record_id') or sha(record))

def canonical_manifestation(value):
    """Read old index keys compatibly without rewriting immutable history."""
    token=str(value).strip()
    try:
        if token.lower().startswith('doi:'):return 'doi:'+canonical_doi(token[4:])
        if token.lower().startswith('arxiv:'):return 'arxiv:'+normalize_arxiv_id(token[6:].strip())[1]
    except (ValueError,TypeError,ConnectorError):pass
    return token

def own_file_metadata(path):
    """Read explicit PDF metadata only; reference-list/body DOI is never identity."""
    result={'schema_version':'E6FileIdentity-v1','identifiers':{},'title':None,
        'identity_evidence':'INSUFFICIENT','reference_text_scanned':False}
    if Path(path).suffix.casefold()!='.pdf': return result
    try:
        import pymupdf
        from defusedxml import ElementTree
        with pymupdf.open(path) as doc:
            metadata=doc.metadata or {};result['title']=plain(metadata.get('title'))
            values=[]
            for key,value in metadata.items():
                if key.casefold() in {'doi','prism:doi'}: values.append(value)
            xml=doc.get_xml_metadata()
            if xml and len(xml)<200000:
                tree=ElementTree.fromstring(xml)
                for node in tree.iter():
                    local=node.tag.rsplit('}',1)[-1].casefold()
                    if local=='doi' and node.text: values.append(node.text)
                    for key,value in node.attrib.items():
                        if key.rsplit('}',1)[-1].casefold()=='doi': values.append(value)
            normalized=set()
            for value in values:
                candidate=re.sub(r'^(?:https?://(?:dx\.)?doi.org/|doi:\s*)','',value.strip(),flags=re.I).lower()
                if re.fullmatch(r'10\.\d{4,9}/[^\s<>]+',candidate): normalized.add(candidate)
            if len(normalized)==1:
                result['identifiers']['doi']=normalized.pop();result['identity_evidence']='EXPLICIT_PDF_DOI_METADATA'
            elif len(normalized)>1: result['identity_evidence']='CONFLICTING_PDF_DOI_METADATA'
    except Exception: result['identity_evidence']='METADATA_UNAVAILABLE'
    return result

class WorkIndex:
    def __init__(self,root):
        self.path=Path(root)/'work_identity.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        with closing(self.connect()) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS works(work_id TEXT PRIMARY KEY,metadata TEXT NOT NULL,title_key TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS work_titles ON works(title_key);
                CREATE TABLE IF NOT EXISTS aliases(alias TEXT PRIMARY KEY,work_id TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS alias_works ON aliases(work_id);
                CREATE TABLE IF NOT EXISTS files(item_id TEXT PRIMARY KEY,work_id TEXT NOT NULL,manifestation TEXT NOT NULL,content_sha TEXT,state TEXT NOT NULL,metadata TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS work_files ON files(work_id,manifestation,state);
                CREATE INDEX IF NOT EXISTS file_bytes ON files(content_sha,state);
                CREATE TABLE IF NOT EXISTS delivered(work_id TEXT NOT NULL,manifestation TEXT NOT NULL,direction_id TEXT NOT NULL,run_id TEXT NOT NULL,metadata TEXT NOT NULL,PRIMARY KEY(work_id,manifestation,direction_id));
                CREATE TABLE IF NOT EXISTS batches(run_id TEXT PRIMARY KEY,result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cursors(name TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS indexed_inputs(input_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback_clusters(work_id TEXT NOT NULL,cluster_id TEXT NOT NULL,PRIMARY KEY(work_id,cluster_id));
            ''')
            if 'source_version' not in {r[1] for r in db.execute('PRAGMA table_info(files)')}:
                db.execute('ALTER TABLE files ADD COLUMN source_version INTEGER NOT NULL DEFAULT -1')
            db.commit()

    def connect(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row;return db

    def _keys(self,record):
        ids=record_identifiers(record);keys=identity_keys(ids)
        raw=record.get('content_sha256')
        if raw: keys.add('bytes:'+str(raw).lower())
        rid=ids.get('source_record_id') or record.get('source_record_id')
        if not keys and rid: keys.add('source:'+str(rid))
        return sorted(keys)

    def _ensure(self,db,record):
        keys=self._keys(record)
        if not keys: return None
        found={r[0] for key in keys for r in db.execute('SELECT work_id FROM aliases WHERE alias=?',(key,))}
        wid=min(found) if found else 'work-'+sha(keys[0])[:32]
        title=record.get('title') or (record.get('bibliographic') or {}).get('title')
        db.execute('INSERT OR IGNORE INTO works VALUES(?,?,?,?)',(wid,encode(record),title_key(title),now()))
        # Co-observed exact identifiers unify aliases, never merely similar titles.
        for old in found-{wid}:
            db.execute('UPDATE aliases SET work_id=? WHERE work_id=?',(wid,old))
            db.execute('INSERT OR IGNORE INTO feedback_clusters SELECT ?,cluster_id FROM feedback_clusters WHERE work_id=?',(wid,old))
            db.execute('DELETE FROM feedback_clusters WHERE work_id=?',(old,))
            db.execute('INSERT OR IGNORE INTO feedback_clusters VALUES(?,?)',(wid,old))
            db.execute('UPDATE files SET work_id=? WHERE work_id=?',(wid,old))
            db.execute('INSERT OR IGNORE INTO delivered SELECT ?,manifestation,direction_id,run_id,metadata FROM delivered WHERE work_id=?',(wid,old))
            db.execute('DELETE FROM delivered WHERE work_id=?',(old,));db.execute('DELETE FROM works WHERE work_id=?',(old,))
        for key in keys: db.execute('INSERT OR REPLACE INTO aliases VALUES(?,?)',(key,wid))
        db.execute('INSERT OR IGNORE INTO feedback_clusters VALUES(?,?)',(wid,wid))
        if record.get('work_cluster_id'):db.execute('INSERT OR IGNORE INTO feedback_clusters VALUES(?,?)',(wid,record['work_cluster_id']))
        old=json.loads(db.execute('SELECT metadata FROM works WHERE work_id=?',(wid,)).fetchone()[0])
        if len(encode(record))>len(encode(old)) or not old.get('bibliographic'):
            db.execute('UPDATE works SET metadata=?,title_key=? WHERE work_id=?',(encode(record),title_key(title),wid))
        return wid

    def register(self,record):
        with self.lock,closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE');wid=self._ensure(db,record);db.commit();return wid

    def lookup(self,record):
        keys=self._keys(record);title=record.get('title') or (record.get('bibliographic') or {}).get('title')
        with closing(self.connect()) as db:
            ids={r[0] for key in keys for r in db.execute('SELECT work_id FROM aliases WHERE alias=?',(key,))}
            exact=sorted(ids);files=[];delivered=[]
            for wid in exact:
                files += [dict(row) for row in db.execute('SELECT item_id,state,manifestation,content_sha FROM files WHERE work_id=?',(wid,))]
                delivered += [dict(row) for row in db.execute('SELECT direction_id,manifestation,run_id FROM delivered WHERE work_id=?',(wid,))]
            for row in files+delivered:
                row['stored_manifestation']=row['manifestation']
                row['manifestation']=canonical_manifestation(row['manifestation'])
            possible=[r[0] for r in db.execute('SELECT work_id FROM works WHERE title_key=?',(title_key(title),)) if r[0] not in ids] if title else []
        return {'work_ids':exact,'files':files,'delivered':delivered,'possible_duplicates':possible,
            'identity_status':'EXACT' if exact else 'POSSIBLE_DUPLICATE' if possible else 'UNRESOLVED' if not keys else 'NEW_TO_INDEX',
            'has_fulltext':any(r['state'] not in INACTIVE and r['manifestation']==manifestation(record) for r in files)}

    def feedback_clusters(self,wid):
        with closing(self.connect()) as db:
            return sorted({wid}|{row[0] for row in db.execute('SELECT cluster_id FROM feedback_clusters WHERE work_id=?',(wid,))})

    def import_history(self,run_id,rows,fingerprint):
        with self.lock,closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            for row in rows:
                wid=self._ensure(db,row)
                if wid is None or not row.get('primary_direction_id'):continue
                if any(canonical_manifestation(r[0])==manifestation(row) for r in db.execute('SELECT manifestation FROM delivered WHERE work_id=? AND direction_id=?',(wid,row['primary_direction_id']))):continue
                db.execute('INSERT OR IGNORE INTO delivered VALUES(?,?,?,?,?)',
                    (wid,manifestation(row),row['primary_direction_id'],run_id,encode(row)))
            db.execute('INSERT OR REPLACE INTO indexed_inputs VALUES(?,?)',('history:'+run_id,fingerprint));db.commit()

    def seed(self,value):
        token=value.strip();ids={'doi':token}
        if 'arxiv' in token.lower() or re.fullmatch(r'\d{4}\.\d{4,5}(v\d+)?',token): ids={'arxiv_id':token}
        keys=self._keys({'identifiers':ids})
        with closing(self.connect()) as db:
            rows=[r for key in keys for r in db.execute('SELECT w.metadata FROM works w JOIN aliases a ON w.work_id=a.work_id WHERE a.alias=?',(key,))]
            if not rows: rows=list(db.execute('SELECT metadata FROM files WHERE item_id=?',(token,)))
            return json.loads(rows[0][0]) if rows else None

    def synchronize_file(self,item,metadata):
        return self.bind_file(item,metadata,check_duplicate=False)

    def bind_file(self,item,metadata,*,check_duplicate=True,target_resolver=None):
        record=dict(metadata,content_sha256=item.get('content_sha256'),item_id=item['item_id'])
        with self.lock,closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE');wid=self._ensure(db,record)
            if not wid: db.commit();return {'status':'UNRESOLVED','work_id':None,'duplicate_item_id':None}
            version=int(item.get('version',-1))
            previous=db.execute('SELECT source_version FROM files WHERE item_id=?',(item['item_id'],)).fetchone()
            if not check_duplicate and previous and version>=0 and previous[0]>=version:
                db.commit();return {'status':'STALE_OR_REPLAY_IGNORED','work_id':wid,'duplicate_item_id':None}
            variant=manifestation(record)
            # Unknown manifestation is byte-specific, never an approximate title merge.
            if not record_identifiers(record): variant='bytes:'+str(item.get('content_sha256','')).lower()
            prior=list(db.execute('SELECT item_id,manifestation,content_sha FROM files WHERE work_id=? AND item_id!=? AND state NOT IN (\'TRASHED\',\'DELETED\',\'DEDUPLICATED\') ORDER BY item_id',
                (wid,item['item_id']))) if check_duplicate else []
            duplicate=next((row['item_id'] for row in prior
                if (canonical_manifestation(row['manifestation'])==variant or row['content_sha'] and row['content_sha']==item.get('content_sha256'))
                and (target_resolver is None or target_resolver(row['item_id']))),None)
            state='DEDUPLICATED' if duplicate and item.get('state') not in INACTIVE else item.get('state','QUEUED')
            db.execute('INSERT OR REPLACE INTO files(item_id,work_id,manifestation,content_sha,state,metadata,source_version) VALUES(?,?,?,?,?,?,?)',(item['item_id'],wid,variant,item.get('content_sha256'),state,encode(record),version))
            # SQL column count is fixed by schema; all persistent records retain lineage.
            db.commit()
        return {'status':'DUPLICATE_REUSED' if duplicate else 'BOUND','work_id':wid,'duplicate_item_id':duplicate,
            'manifestation':variant,'identity_evidence':metadata.get('identity_evidence','STRUCTURED_METADATA')}

    def commit_batch(self,run_id,rows):
        """Atomic final identity binding, before the immutable result and its message."""
        with self.lock,closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT result FROM batches WHERE run_id=?',(run_id,)).fetchone()
            if prior: db.commit();return json.loads(prior[0])
            accepted=[];duplicates=[];new_works=0;new_versions=0
            for row in rows:
                wid=self._ensure(db,row);variant=manifestation(row);direction=row['primary_direction_id']
                if wid is None: duplicates.append({'candidate_id':row['candidate_id'],'reason':'IDENTITY_INSUFFICIENT'});continue
                previous=[dict(r,manifestation=canonical_manifestation(r['manifestation'])) for r in db.execute('SELECT manifestation,direction_id FROM delivered WHERE work_id=?',(wid,))]
                fulltext=any(canonical_manifestation(r['manifestation'])==variant for r in db.execute('SELECT manifestation FROM files WHERE work_id=? AND state NOT IN (\'TRASHED\',\'DELETED\',\'DEDUPLICATED\')',(wid,)))
                exists=any(r['manifestation']==variant and r['direction_id']==direction for r in previous)
                if fulltext or (exists and not row.get('allow_redisplay')):
                    duplicates.append({'candidate_id':row['candidate_id'],'work_id':wid,'reason':'FULLTEXT_EXISTS' if fulltext else 'DIRECTION_ALREADY_DELIVERED'});continue
                kind='REDISPLAY' if exists else 'NEW_WORK' if not previous else 'NEW_VERSION' if not any(r['manifestation']==variant for r in previous) else 'NEW_DIRECTION_RELATION'
                if kind=='NEW_WORK': new_works+=1
                if kind=='NEW_VERSION': new_versions+=1
                item=dict(row,stable_work_id=wid,manifestation_key=variant,delivery_kind=kind,position=len(accepted)+1)
                accepted.append(item)
                db.execute('INSERT OR IGNORE INTO delivered VALUES(?,?,?,?,?)',(wid,variant,direction,run_id,encode(item)))
            result={'recommendations':accepted,'duplicate_bindings':duplicates,'new_work_count':new_works,
                'new_version_count':new_versions,'new_relation_count':sum(r['delivery_kind']=='NEW_DIRECTION_RELATION' for r in accepted)}
            db.execute('INSERT INTO batches VALUES(?,?)',(run_id,encode(result)));db.commit();return result

    def cursor(self,name,default=None):
        with closing(self.connect()) as db:
            row=db.execute('SELECT value FROM cursors WHERE name=?',(name,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_cursor(self,name,value):
        with closing(self.connect()) as db:
            db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?)',(name,encode(value)));db.commit()

    def backfill_inbox(self,inbox,limit=64):
        store=inbox.store;cursor=self.cursor('inbox_event',0)
        with closing(store._connect()) as source:
            events=source.execute('SELECT sequence,item_id FROM events WHERE sequence>? ORDER BY sequence LIMIT ?',(cursor,limit)).fetchall()
            last=source.execute('SELECT COALESCE(MAX(sequence),0) FROM events').fetchone()[0]
            ids=list(dict.fromkeys(r['item_id'] for r in events if r['item_id']))
            items=[json.loads(r[0]) for item_id in ids for r in source.execute('SELECT record_json FROM items WHERE item_id=?',(item_id,))]
        for item in items:
            if not item.get('content_sha256'): continue
            metadata=item.get('bibliographic_identity')
            if not metadata:
                path=inbox._path(item['bound_locator']) if item.get('bound_locator') else None
                metadata=own_file_metadata(path) if path and path.is_file() else {'identifiers':{},'identity_evidence':'SOURCE_UNAVAILABLE'}
                item=store.update_item(item['item_id'],'E6_IDENTITY_INDEXED',lambda row,m=metadata:row.update(bibliographic_identity=m))
            self.synchronize_file(item,metadata)
        if events: self.set_cursor('inbox_event',events[-1]['sequence'])
        status={'status':'COMPLETE' if not events or events[-1]['sequence']>=last else 'BACKFILLING',
            'processed_event_cursor':events[-1]['sequence'] if events else cursor,'observed_event_count':last,
            'records_this_batch':len(items),'maximum_records_per_batch':limit,'scope':'ALL_INBOX_STATES_INCLUDING_COMPLETED_AND_TRASHED'}
        self.set_cursor('inbox_coverage',status);return status

    def coverage(self):
        with closing(self.connect()) as db:
            counts={table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('works','files','delivered')}
            unknown=db.execute("SELECT COUNT(*) FROM works w WHERE NOT EXISTS (SELECT 1 FROM aliases a WHERE a.work_id=w.work_id AND (a.alias LIKE 'doi:%' OR a.alias LIKE 'arxiv_id:%' OR a.alias LIKE 'wos_uid:%'))").fetchone()[0]
        return dict(counts,unidentified_works=unknown,inbox=self.cursor('inbox_coverage',{'status':'PENDING'}),
            legacy=self.cursor('legacy_coverage',{'status':'PENDING'}),history=self.cursor('history_coverage',{'status':'PENDING'}),ui_filter_independent=True,pdf_reads_during_lookup=0)
