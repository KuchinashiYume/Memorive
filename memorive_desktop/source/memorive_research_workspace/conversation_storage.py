"""Rebuildable conversation metadata and separately owned lifecycle state.

Thread bodies remain canonical for messages, branches, citations and knowledge.
Lifecycle changes never rewrite them. New projections commit with source writes;
legacy projections are built later in bounded batches by the desktop scheduler.
"""
import json,time
from datetime import datetime,timezone

VERSION=2
DAYS_30=30*24*60*60
PAGE_SIZE=50
MESSAGE_LIMIT=100
BATCH_SIZE=20
BYTE_BUDGET=16*1024*1024
TIME_BUDGET=.030

def timestamp(value):
    try:
        if not isinstance(value,str):return None
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:return None
        number=parsed.astimezone(timezone.utc).timestamp()
        return number if number>=0 else None
    except (ValueError,OverflowError,OSError):return None

def bootstrap(db,clock):
    statements=[
      '''CREATE TABLE IF NOT EXISTS conversation_catalog(
       id TEXT PRIMARY KEY,project TEXT NOT NULL,title TEXT NOT NULL,pinned INTEGER NOT NULL,
       archived INTEGER NOT NULL,temporary INTEGER NOT NULL,created_at TEXT,sort_at TEXT NOT NULL,
       message_count INTEGER NOT NULL,question_count INTEGER NOT NULL,source_revision INTEGER NOT NULL,
       body_bytes INTEGER NOT NULL,body_revision INTEGER NOT NULL)''',
      'CREATE INDEX IF NOT EXISTS conversation_page ON conversation_catalog(archived,pinned DESC,sort_at DESC,id DESC)',
      'CREATE INDEX IF NOT EXISTS conversation_project_page ON conversation_catalog(project,archived,pinned DESC,sort_at DESC,id DESC)',
      '''CREATE TABLE IF NOT EXISTS conversation_access(
       id TEXT PRIMARY KEY,eligible_at REAL,last_opened_at REAL,access_revision INTEGER NOT NULL DEFAULT 0,
       origin TEXT NOT NULL,invalid INTEGER NOT NULL DEFAULT 0,archive_source TEXT,archived_at REAL,
       archived_override INTEGER,restored_message_count INTEGER NOT NULL DEFAULT 100,state_revision INTEGER NOT NULL DEFAULT 0)''',
      'CREATE INDEX IF NOT EXISTS conversation_count ON conversation_catalog(archived,pinned,temporary,message_count,id)',
      'CREATE INDEX IF NOT EXISTS conversation_due ON conversation_access(invalid,eligible_at,id)',
      '''CREATE TABLE IF NOT EXISTS conversation_search(
       id TEXT PRIMARY KEY,source_revision INTEGER NOT NULL,full_text TEXT NOT NULL,folded TEXT NOT NULL,
       answer_texts TEXT NOT NULL)''',
      '''CREATE TABLE IF NOT EXISTS conversation_jobs(
       kind TEXT NOT NULL,id TEXT NOT NULL,thread_id TEXT,status TEXT NOT NULL,project TEXT NOT NULL,
       PRIMARY KEY(kind,id))''',
      'CREATE INDEX IF NOT EXISTS conversation_jobs_thread ON conversation_jobs(thread_id,status)',
      'CREATE INDEX IF NOT EXISTS conversation_jobs_status ON conversation_jobs(status,kind)',
      '''CREATE TABLE IF NOT EXISTS conversation_views(
       owner TEXT NOT NULL,client TEXT NOT NULL,thread_id TEXT,sequence INTEGER NOT NULL,
       expires_monotonic REAL NOT NULL,PRIMARY KEY(owner,client))''',
      'CREATE INDEX IF NOT EXISTS conversation_views_thread ON conversation_views(thread_id,expires_monotonic)',
      '''CREATE TABLE IF NOT EXISTS conversation_migration(
       id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,cursor INTEGER NOT NULL,boundary INTEGER NOT NULL,
       complete INTEGER NOT NULL,started_at REAL NOT NULL,last_clock REAL NOT NULL,
       clock_blocked INTEGER NOT NULL DEFAULT 0,nav_epoch INTEGER NOT NULL DEFAULT 0)''',
      '''CREATE TRIGGER IF NOT EXISTS conversation_delete AFTER DELETE ON objects
       BEGIN
        DELETE FROM conversation_catalog WHERE OLD.kind='thread' AND id=OLD.id;
        DELETE FROM conversation_access WHERE OLD.kind='thread' AND id=OLD.id;
        DELETE FROM conversation_search WHERE OLD.kind='thread' AND id=OLD.id;
        DELETE FROM conversation_build WHERE OLD.kind='thread' AND id=OLD.id;
        DELETE FROM conversation_pieces WHERE OLD.kind='thread' AND id=OLD.id;
        DELETE FROM conversation_views WHERE OLD.kind='thread' AND thread_id=OLD.id;
        DELETE FROM conversation_jobs WHERE kind=OLD.kind AND id=OLD.id;
       END''',
    ]
    for sql in statements:db.execute(sql)
    from .conversation_backfill import schema
    schema(db)
    previous=db.execute('SELECT version FROM conversation_migration WHERE id=1').fetchone()
    if previous and previous['version']!=VERSION:raise ValueError('CONVERSATION_FORMAT_UNSUPPORTED')
    boundary=db.execute('SELECT coalesce(max(rowid),0) FROM objects').fetchone()[0]
    complete=not db.execute("SELECT 1 FROM objects WHERE kind IN ('thread','job','attachment_job') LIMIT 1").fetchone()
    db.execute('INSERT OR IGNORE INTO conversation_migration(id,version,cursor,boundary,complete,started_at,last_clock) VALUES(1,?,0,?,?,?,?)',(VERSION,boundary,complete,clock,clock))

def overlay(db,identity,value):
    row=db.execute('SELECT archived_override,state_revision FROM conversation_access WHERE id=?',(identity,)).fetchone()
    if row is not None:
        if row['archived_override'] is not None:value['archived']=bool(row['archived_override'])
        if row['state_revision']:value['revision']=max(value.get('revision',0),row['state_revision'])
    return value

def sync(db,kind,identity,project,body,revision,body_bytes,*,new,clock):
    if kind in {'job','attachment_job'}:
        db.execute('INSERT INTO conversation_jobs VALUES(?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET thread_id=excluded.thread_id,status=excluded.status,project=excluded.project',
                   (kind,identity,body.get('thread_id'),body.get('status',''),project))
        return
    if kind!='thread':return
    old=db.execute('SELECT archived,body_revision FROM conversation_catalog WHERE id=?',(identity,)).fetchone()
    if old is not None and old['body_revision']>revision:return
    messages=body.get('messages',[]);effective=overlay(db,identity,dict(archived=body.get('archived',False)));archived=bool(effective['archived'])
    state=db.execute('SELECT state_revision FROM conversation_access WHERE id=?',(identity,)).fetchone()
    public_revision=max(revision,state['state_revision'] if state else 0)
    sort_at=body.get('updated_at') or (messages[-1].get('created_at') if messages else None) or body.get('created_at') or ''
    db.execute('''INSERT INTO conversation_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET project=excluded.project,title=excluded.title,pinned=excluded.pinned,
      archived=excluded.archived,temporary=excluded.temporary,created_at=excluded.created_at,sort_at=excluded.sort_at,
      message_count=excluded.message_count,question_count=excluded.question_count,source_revision=excluded.source_revision,body_bytes=excluded.body_bytes,body_revision=excluded.body_revision
      WHERE excluded.body_revision>=conversation_catalog.body_revision''',
      (identity,project,body.get('title',''),bool(body.get('pinned')),archived,bool(body.get('temporary')),body.get('created_at'),sort_at,
       len(messages),sum(m.get('role')=='user' for m in messages),public_revision,body_bytes,revision))
    start=db.execute('SELECT started_at FROM conversation_migration WHERE id=1').fetchone()['started_at']
    initial=timestamp(body.get('created_at')) if new else start
    db.execute('INSERT OR IGNORE INTO conversation_access(id,eligible_at,origin,invalid) VALUES(?,?,?,?)',
               (identity,initial,'CREATED' if new else 'LEGACY_GRACE',initial is None or initial>clock))
    if initial is None or initial>clock:
        db.execute("INSERT INTO events(event,object_id,at,body) VALUES('CONVERSATION_TIME_INVALID',?,datetime('now'),?)",(identity,json.dumps({'origin':'CREATED' if new else 'LEGACY_GRACE'})))
    text='\n'.join([body.get('title','')]+[m.get('text','') for m in messages])
    answers=json.dumps([[m['id'],m.get('text','')] for m in messages if m.get('role')=='assistant'],ensure_ascii=False,separators=(',',':'))
    db.execute('INSERT INTO conversation_search VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET source_revision=excluded.source_revision,full_text=excluded.full_text,folded=excluded.folded,answer_texts=excluded.answer_texts WHERE excluded.source_revision>=conversation_search.source_revision',
               (identity,revision,text,text.casefold(),answers))

def metadata(row):
    return dict(id=row['id'],project=row['project'],title=row['title'],pinned=bool(row['pinned']),archived=bool(row['archived']),
                temporary=bool(row['temporary']),created_at=row['created_at'],updated_at=row['sort_at'],revision=row['source_revision'],
                message_count=row['message_count'],question_count=row['question_count'],excerpt='',matching_answer_ids=[])
