"""Transactional local history; model providers never own the canonical history."""
import json, sqlite3, hashlib, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

def now(): return datetime.now(timezone.utc).isoformat()
def uid(prefix): return prefix+uuid.uuid4().hex
def packed(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value): return hashlib.sha256((value if isinstance(value,bytes) else packed(value).encode('utf-8'))).hexdigest()

class Store:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'research_workspace_v1.sqlite3'
        with self.tx() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS objects(kind TEXT NOT NULL,id TEXT NOT NULL,project TEXT NOT NULL,
                body TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,updated TEXT NOT NULL,PRIMARY KEY(kind,id));
              CREATE INDEX IF NOT EXISTS objects_scope ON objects(kind,project,updated);
              CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event TEXT NOT NULL,
                object_id TEXT NOT NULL,at TEXT NOT NULL,body TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,artifact_id TEXT NOT NULL,project TEXT NOT NULL,
                text TEXT NOT NULL,line_start INTEGER,line_end INTEGER,page INTEGER,hash TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS chunks_scope ON chunks(project,artifact_id);
            ''')
            # executescript commits the prior transaction; reacquire before defaults.
            db.execute('BEGIN IMMEDIATE')
            if self.get('settings','default',db=db) is None:
                self.put('settings','default','',{'profile_ref':None,'memory_enabled':True,'use_recent_interests':False,
                    'context_chars':24000,'recent_messages':8,'agent':'codex','default_project':'default',
                    'bridge_enabled':False,'bridge_port':0},db=db)
            if self.get('project','default',db=db) is None:
                self.put('project','default','default',{'name':'默认研究项目','vault':None,'index_epoch':0},db=db)

    @contextmanager
    def tx(self):
        db=sqlite3.connect(self.path,timeout=15,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA busy_timeout=15000');db.execute('PRAGMA journal_mode=WAL')
        db.execute('BEGIN IMMEDIATE')
        try:yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()

    def get(self,kind,identity,*,db=None):
        if db is None:
            with self.tx() as conn:return self.get(kind,identity,db=conn)
        row=db.execute('SELECT body,revision FROM objects WHERE kind=? AND id=?',(kind,identity)).fetchone()
        return dict(json.loads(row['body']),id=identity,revision=row['revision']) if row else None

    def list(self,kind,project=None,*,db=None):
        if db is None:
            with self.tx() as conn:return self.list(kind,project,db=conn)
        query='SELECT id,body,revision FROM objects WHERE kind=?';args=[kind]
        if project is not None:query+=' AND project=?';args.append(project)
        return [dict(json.loads(r['body']),id=r['id'],revision=r['revision']) for r in db.execute(query+' ORDER BY updated DESC,id',args)]

    def put(self,kind,identity,project,value,*,expected=None,db=None):
        if db is None:
            with self.tx() as conn:return self.put(kind,identity,project,value,expected=expected,db=conn)
        old=self.get(kind,identity,db=db)
        if expected is not None and (old or {}).get('revision',0)!=expected:raise ValueError('REVISION_CONFLICT')
        rev=(old or {}).get('revision',0)+1
        body={k:v for k,v in value.items() if k not in {'id','revision'}}
        db.execute('INSERT INTO objects VALUES(?,?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body,revision=excluded.revision,updated=excluded.updated,project=excluded.project',
            (kind,identity,project,packed(body),rev,now()))
        if kind in {'artifact','feedback','connector_document','connector_job','connector_action','job'}:
            fields=('state','status','content_hash','fingerprint','result')
            before={k:(old or {}).get(k) for k in fields}
            after={k:body.get(k) for k in fields}
            before['job_state']=((old or {}).get('processing') or {}).get('control_state')
            after['job_state']=(body.get('processing') or {}).get('control_state')
            if old is None or before!=after:
                self.event('MEMO_PUBLIC_'+kind.upper(),identity,{'project':project,'kind':kind,
                    'state':body.get('state') or body.get('status'),'job_state':after['job_state'],'revision':rev},db)
        return dict(body,id=identity,revision=rev)

    def event(self,event,identity,body,db):
        # Never record full chat prompts, credentials or forgotten memory text in audit events.
        db.execute('INSERT INTO events(event,object_id,at,body) VALUES(?,?,?,?)',(event,identity,now(),packed(body)))
