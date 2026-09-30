"""Paged navigation, explicit display receipts and bounded archive maintenance."""
import base64,binascii,json,math,re,time,uuid
from . import conversation_storage as cs
from .store import packed,digest

class ConversationLifecycle:
    def init_lifecycle(self):
        self.owner=uuid.uuid4().hex;self.ready=False;self.foreground_until=0.
        self.clock=self.store.lifecycle_clock;self.before_archive_commit=None

    def _clock_ok(self,db,at):
        state=db.execute('SELECT last_clock,clock_blocked FROM conversation_migration WHERE id=1').fetchone()
        if not math.isfinite(at) or at<state['last_clock']:
            if not state['clock_blocked']:
                db.execute('UPDATE conversation_migration SET clock_blocked=1 WHERE id=1')
                self.store.event('CONVERSATION_CLOCK_UNCERTAIN','lifecycle',{'reason':'CLOCK_MOVED_BACKWARD'},db)
            return False
        db.execute('UPDATE conversation_migration SET last_clock=?,clock_blocked=0 WHERE id=1',(at,))
        return True

    def _ensure(self,db,identity):
        row=db.execute('SELECT * FROM conversation_catalog WHERE id=?',(identity,)).fetchone()
        raw=db.execute("SELECT project,body,revision FROM objects WHERE kind='thread' AND id=?",(identity,)).fetchone() if row is None else None
        if raw is not None:
            cs.sync(db,'thread',identity,raw['project'],json.loads(raw['body']),raw['revision'],len(raw['body'].encode()),new=False,clock=self.clock())
            row=db.execute('SELECT * FROM conversation_catalog WHERE id=?',(identity,)).fetchone()
        if row is None:raise ValueError('THREAD_NOT_FOUND')
        return row

    def busy(self,db,identity):
        result=db.execute("SELECT kind FROM conversation_jobs WHERE thread_id=? AND status IN ('QUEUED','RUNNING') LIMIT 1",(identity,)).fetchone()
        if result is None and not db.execute('SELECT complete FROM conversation_migration WHERE id=1').fetchone()['complete']:
            result=db.execute("SELECT kind FROM objects WHERE kind IN ('job','attachment_job') AND json_extract(body,'$.thread_id')=? AND json_extract(body,'$.status') IN ('QUEUED','RUNNING') LIMIT 1",(identity,)).fetchone()
        return result

    def view(self,client_id,sequence,action,thread_id=None):
        if not isinstance(client_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',client_id):raise ValueError('THREAD_VIEW_INVALID')
        if type(sequence) is not int or sequence<1 or sequence>2**53-1 or action not in {'ready','opening','visible','displayed','heartbeat','closed'}:raise ValueError('THREAD_VIEW_INVALID')
        if action not in {'closed','ready'} and not isinstance(thread_id,str):raise ValueError('THREAD_VIEW_INVALID')
        at=self.clock();mono=time.monotonic()
        with self.store.tx() as db:
            old=db.execute('SELECT * FROM conversation_views WHERE owner=? AND client=?',(self.owner,client_id)).fetchone()
            if old and old['sequence']>=sequence:return {'status':'IGNORED_STALE_VIEW'}
            if action not in {'closed','ready'}:
                if not db.execute("SELECT 1 FROM objects WHERE kind='thread' AND id=?",(thread_id,)).fetchone():raise ValueError('THREAD_NOT_FOUND')
            if action=='displayed':self._ensure(db,thread_id)
            active=thread_id if action not in {'closed','ready'} else None
            db.execute('INSERT INTO conversation_views VALUES(?,?,?,?,?) ON CONFLICT(owner,client) DO UPDATE SET thread_id=excluded.thread_id,sequence=excluded.sequence,expires_monotonic=excluded.expires_monotonic',
                       (self.owner,client_id,active,sequence,mono+90))
            opened=action=='displayed' and self._clock_ok(db,at)
            if opened:
                db.execute("UPDATE conversation_access SET eligible_at=?,last_opened_at=?,access_revision=access_revision+1,origin='OPENED',invalid=0 WHERE id=?",(at,at,thread_id))
            # A display receipt deliberately leaves objects.revision, answer
            # versions and the serialized conversation body untouched.
            nav_epoch=db.execute('SELECT nav_epoch FROM conversation_migration WHERE id=1').fetchone()[0]
        self.ready=True
        return {'status':'RECORDED','thread_id':active,'opened':opened,'nav_epoch':nav_epoch}

    def close_views(self):
        with self.store.tx() as db:db.execute('DELETE FROM conversation_views WHERE owner=?',(self.owner,))

    def set_archive(self,db,identity,archived,*,expected=None,source='MANUAL',candidate=None):
        row=self._ensure(db,identity)
        access=db.execute('SELECT * FROM conversation_access WHERE id=?',(identity,)).fetchone()
        if expected is not None and row['source_revision']!=expected:raise ValueError('REVISION_CONFLICT')
        busy=self.busy(db,identity)
        if busy:raise ValueError('ATTACHMENT_BUSY' if busy['kind']=='attachment_job' else 'THREAD_BUSY')
        at=self.clock()
        if source=='AUTO':
            if candidate is None or row['source_revision']!=candidate['source_revision'] or access['access_revision']!=candidate['access_revision']:return False
            if row['archived'] or row['temporary'] or row['pinned'] or access['invalid']:return False
            if not isinstance(access['eligible_at'],(int,float)) or not math.isfinite(access['eligible_at']) or access['eligible_at']>at:return False
            over_count=row['message_count']>max(cs.MESSAGE_LIMIT,access['restored_message_count'])
            overdue=at-access['eligible_at']>cs.DAYS_30
            if not (over_count or overdue):return False
            reason='MESSAGE_COUNT' if over_count else 'INACTIVITY'
            if not self._clock_ok(db,at):return False
            mono=time.monotonic()
            if db.execute('SELECT 1 FROM conversation_views WHERE thread_id=? AND expires_monotonic>? AND expires_monotonic<=? LIMIT 1',(identity,mono,mono+91)).fetchone():return False
        if bool(row['archived'])==archived:return False
        # Keep the public optimistic revision in the small lifecycle row.
        # Updating even a numeric column beside a large SQLite TEXT body can
        # rewrite its overflow pages; archive must never touch that row.
        db.execute('UPDATE conversation_access SET state_revision=? WHERE id=?',(row['source_revision']+1,identity))
        db.execute('UPDATE conversation_catalog SET archived=?,source_revision=source_revision+1 WHERE id=?',(archived,identity))
        db.execute('UPDATE conversation_access SET archive_source=?,archived_at=?,archived_override=? WHERE id=?',(source,at if archived else None,archived,identity))
        db.execute('UPDATE conversation_migration SET nav_epoch=nav_epoch+1 WHERE id=1')
        if not archived:
            valid=self._clock_ok(db,at)
            db.execute("UPDATE conversation_access SET eligible_at=?,access_revision=access_revision+1,origin='RESTORED',invalid=?,restored_message_count=? WHERE id=?",(at,not valid,max(cs.MESSAGE_LIMIT,row['message_count']),identity))
        self.store.event('THREAD_ARCHIVED' if archived else 'THREAD_RESTORED',identity,{'source':source,'at':at,'reason':reason if source=='AUTO' else 'USER','message_count':row['message_count']},db)
        return True

    def _cursor(self,mode,query,archived,project,key):
        value=dict(v=1,mode=mode,q=digest([query,archived,project]),key=key)
        return base64.urlsafe_b64encode(packed(value).encode()).decode().rstrip('=')

    def page(self,query='',archived=False,cursor=None,limit=50,project=None):
        if not isinstance(query,str) or len(query)>500 or type(archived) is not bool or type(limit) is not int or not 1<=limit<=50:raise ValueError('SEARCH_INVALID')
        if project is not None and not isinstance(project,str):raise ValueError('SEARCH_INVALID')
        token=None
        if cursor is not None:
            try:
                if not isinstance(cursor,str) or len(cursor)>4096:raise ValueError()
                token=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
                if token.get('v')!=1 or token.get('q')!=digest([query,archived,project]) or token.get('mode') not in {'legacy','indexed'}:raise ValueError()
                if not isinstance(token.get('key'),list):raise ValueError()
            except (ValueError,TypeError,KeyError,AttributeError,binascii.Error):raise ValueError('CONVERSATION_CURSOR_INVALID') from None
        terms=query.casefold().split()
        with self.store.read() as db:
            complete=bool(db.execute('SELECT complete FROM conversation_migration WHERE id=1').fetchone()['complete'])
            mode=token['mode'] if token else 'indexed' if complete else 'legacy'
            if mode=='legacy':rows,next_key=self._legacy_page(db,terms,archived,limit,project,token and token['key'])
            else:rows,next_key=self._indexed_page(db,terms,archived,limit,project,token and token['key'])
            projects=self.store.list('project',db=db)
            nav_epoch=db.execute('SELECT nav_epoch FROM conversation_migration WHERE id=1').fetchone()[0]
        return dict(projects=projects,threads=rows,query=query,archived=archived,
                    next_cursor=self._cursor(mode,query,archived,project,next_key) if next_key else None,
                    nav_epoch=nav_epoch,migration_pending=not complete,ordering='pinned_updated_id' if mode=='indexed' else 'legacy_updated_id',page_size=limit)

    def _indexed_page(self,db,terms,archived,limit,project,key):
        args=[archived];where=['c.archived=?']
        if self.workspace.owns_desktop_state:
            owned=sorted(self.workspace.temporary_threads)
            where.append('(c.temporary=0'+(' OR c.id IN ('+','.join('?' for _ in owned)+')' if owned else '')+')');args+=owned
        if project is not None:where.append('c.project=?');args.append(project)
        if key is not None:
            if len(key)!=3 or type(key[0]) is not int or key[0] not in (0,1) or not all(isinstance(x,str) for x in key[1:]):raise ValueError('CONVERSATION_CURSOR_INVALID')
            where.append('(c.pinned,c.sort_at,c.id)<(?,?,?)');args+=key
        fields='c.*';join=''
        if terms:
            fields+=',s.full_text,s.answer_texts';join=' JOIN conversation_search s ON s.id=c.id AND s.source_revision=c.body_revision'
            for term in terms:where.append('instr(s.folded,?)>0');args.append(term)
        raw=db.execute('SELECT '+fields+' FROM conversation_catalog c'+join+' WHERE '+' AND '.join(where)+' ORDER BY c.pinned DESC,c.sort_at DESC,c.id DESC LIMIT ?',args+[limit+1]).fetchall()
        rows=[]
        for r in raw[:limit]:
            value=cs.metadata(r)
            if terms:self._search_fields(value,r['full_text'],json.loads(r['answer_texts']),terms)
            rows.append(value)
        last=raw[limit-1] if len(raw)>limit else None
        return rows,[last['pinned'],last['sort_at'],last['id']] if last else None

    def _search_fields(self,value,text,answers,terms):
        at=text.casefold().find(terms[0]);value['excerpt']=text[max(0,at-24):at+100].replace('\n',' ')
        value['matching_answer_ids']=[identity for identity,body in answers if any(term in body.casefold() for term in terms)]

    def _legacy_page(self,db,terms,archived,limit,project,key):
        # The transition uses existing objects ordering and a bounded scan, not
        # an all-body startup rebuild. Its cursor stays in this ordering even
        # if background indexing completes between pages.
        where=["kind='thread'"];args=[]
        if project is not None:where.append('project=?');args.append(project)
        if key is not None:
            if len(key)!=2 or not all(isinstance(x,str) for x in key):raise ValueError('CONVERSATION_CURSOR_INVALID')
            where.append('(updated,id)<(?,?)');args+=key
        page_sql='SELECT id,project,body,revision,updated FROM objects WHERE '+' AND '.join(where)+' ORDER BY updated DESC,id DESC LIMIT ?'
        if terms:raw=db.execute(page_sql,args+[limit+1]).fetchall()
        else:
            # JSON1 sees only the bounded compatibility page. No full thread
            # body crosses the database boundary or is decoded by Python.
            raw=db.execute('''WITH page AS ('''+page_sql+''') SELECT id,project,revision,updated,
              json_extract(body,'$.title','$.pinned','$.archived','$.temporary','$.created_at','$.updated_at','$.messages[#-1].created_at') AS fields,
              json_array_length(body,'$.messages') AS message_count,
              (SELECT count(*) FROM json_each(page.body,'$.messages') WHERE json_extract(value,'$.role')='user') AS question_count
              FROM page''',args+[limit+1]).fetchall()
        rows=[];last=None
        for r in raw[:limit]:
            last=[r['updated'],r['id']]
            if terms:body=json.loads(r['body'])
            else:
                values=json.loads(r['fields']);body=dict(zip(('title','pinned','archived','temporary','created_at','updated_at','last_message_at'),values))
            cs.overlay(db,r['id'],body)
            if bool(body.get('archived'))!=archived:continue
            if body.get('temporary') and self.workspace.owns_desktop_state and r['id'] not in self.workspace.temporary_threads:continue
            messages=body.get('messages',[])
            value={k:body.get(k) for k in ('title','temporary','created_at')}
            value.update(id=r['id'],project=r['project'],revision=max(r['revision'],body.get('revision',0)),pinned=bool(body.get('pinned')),archived=bool(body.get('archived')),
                         updated_at=body.get('updated_at') or body.get('last_message_at') or (messages[-1].get('created_at') if messages else None) or body.get('created_at'),message_count=len(messages) if terms else r['message_count'],question_count=sum(m.get('role')=='user' for m in messages) if terms else r['question_count'],excerpt='',matching_answer_ids=[])
            if terms:
                text='\n'.join([body.get('title','')]+[m.get('text','') for m in messages])
                if not all(term in text.casefold() for term in terms):continue
                self._search_fields(value,text,[[m['id'],m.get('text','')] for m in messages if m.get('role')=='assistant'],terms)
            rows.append(value)
        return rows,last if len(raw)>limit else None

    def maintain(self,*,force=False):
        if self.workspace.closed:return {'status':'CLOSED','migration_complete':False}
        if not force and (not self.ready or time.monotonic()<self.foreground_until):return {'status':'YIELDED','migration_complete':False}
        started=time.monotonic();count=0;read_bytes=0;rewritten=0;archived=0
        with self.store.read() as db:
            state=db.execute('SELECT * FROM conversation_migration WHERE id=1').fetchone()
            pending=[] if state['complete'] else db.execute("SELECT rowid,id,kind,project,revision,length(CAST(body AS BLOB)) AS bytes FROM objects WHERE kind IN ('thread','job','attachment_job') AND rowid>? AND rowid<=? ORDER BY rowid LIMIT ?",(state['cursor'],state['boundary'],cs.BATCH_SIZE)).fetchall()
        for r in pending:
            if self.workspace.closed or (not force and time.monotonic()<self.foreground_until):break
            if count>=cs.BATCH_SIZE or (count and time.monotonic()-started>=cs.TIME_BUDGET):break
            if r['kind']=='thread' and r['bytes']>cs.BYTE_BUDGET:
                from .conversation_backfill import step
                consumed,done=step(self,r,cs.BYTE_BUDGET-read_bytes,started+cs.TIME_BUDGET);read_bytes+=consumed;count+=1
                if done:
                    with self.store.tx() as db:db.execute('UPDATE conversation_migration SET cursor=max(cursor,?) WHERE id=1',(r['rowid'],))
                break
            if read_bytes+r['bytes']>cs.BYTE_BUDGET:break
            with self.store.read() as db:
                # A newer normal writer already published its own projection in
                # the same transaction. Do not read its larger body against the
                # old byte estimate or reapply an old project snapshot.
                current=db.execute('SELECT body,revision FROM objects WHERE kind=? AND id=? AND revision=?',(r['kind'],r['id'],r['revision'])).fetchone()
            if current:
                body=json.loads(current['body']);read_bytes+=len(current['body'].encode())
                with self.store.tx() as db:
                    fresh=db.execute('SELECT revision FROM objects WHERE kind=? AND id=?',(r['kind'],r['id'])).fetchone()
                    if fresh and fresh['revision']==current['revision']:
                        cs.sync(db,r['kind'],r['id'],r['project'],body,current['revision'],r['bytes'],new=False,clock=self.clock())
                    db.execute('UPDATE conversation_migration SET cursor=max(cursor,?) WHERE id=1',(r['rowid'],))
            else:
                with self.store.tx() as db:db.execute('UPDATE conversation_migration SET cursor=max(cursor,?) WHERE id=1',(r['rowid'],))
            count+=1
        with self.store.tx() as db:
            complete=not db.execute("SELECT 1 FROM objects WHERE kind IN ('thread','job','attachment_job') AND rowid>(SELECT cursor FROM conversation_migration WHERE id=1) AND rowid<=(SELECT boundary FROM conversation_migration WHERE id=1) LIMIT 1").fetchone()
            db.execute('UPDATE conversation_migration SET nav_epoch=nav_epoch+CASE WHEN complete!=? THEN 1 ELSE 0 END,complete=? WHERE id=1',(complete,complete))
            clock_ok=self._clock_ok(db,self.clock())
            # Retain sequence tombstones, so a delayed old receipt cannot revive a
            # newer closed view. Expiry makes crashed owners harmless.
            mono=time.monotonic();db.execute('UPDATE conversation_views SET thread_id=NULL WHERE thread_id IS NOT NULL AND (expires_monotonic<? OR expires_monotonic>?)',(mono,mono+91))
            if clock_ok:
                invalid=db.execute("SELECT id FROM conversation_access WHERE invalid=0 AND (typeof(eligible_at) NOT IN ('integer','real') OR eligible_at>?) LIMIT ?",(self.clock(),cs.BATCH_SIZE-count)).fetchall()
                for item in invalid:
                    db.execute('UPDATE conversation_access SET invalid=1 WHERE id=?',(item['id'],))
                    self.store.event('CONVERSATION_TIME_INVALID',item['id'],{'origin':'ACCESS_RECORD'},db)
                    count+=1
        if self.workspace.owns_desktop_state and count<cs.BATCH_SIZE and time.monotonic()-started<cs.TIME_BUDGET:
            with self.store.read() as db:stale=db.execute('SELECT id,source_revision FROM conversation_catalog WHERE temporary=1 LIMIT ?',(cs.BATCH_SIZE-count,)).fetchall()
            for item in stale:
                if item['id'] in self.workspace.temporary_threads:continue
                if time.monotonic()-started>=cs.TIME_BUDGET:break
                try:self.workspace.thread_forget(item['id'],item['source_revision'])
                except ValueError as exc:
                    if str(exc) not in {'THREAD_BUSY','ATTACHMENT_BUSY','REVISION_CONFLICT'}:raise
                count+=1
        if complete and clock_ok and count<cs.BATCH_SIZE and time.monotonic()-started<cs.TIME_BUDGET:
            with self.store.read() as db:
                candidates=db.execute('''WITH due AS (
                  SELECT id FROM conversation_access INDEXED BY conversation_due WHERE invalid=0 AND eligible_at<?
                  UNION SELECT id FROM conversation_catalog INDEXED BY conversation_count WHERE archived=0 AND pinned=0 AND temporary=0 AND message_count>100
                 ) SELECT c.id,c.source_revision,a.access_revision FROM due d
                 JOIN conversation_catalog c ON c.id=d.id JOIN conversation_access a ON a.id=c.id
                 WHERE a.invalid=0 AND a.eligible_at<=? AND c.archived=0 AND c.pinned=0 AND c.temporary=0
                 AND (a.eligible_at<? OR c.message_count>max(100,a.restored_message_count))
                 AND NOT EXISTS(SELECT 1 FROM conversation_jobs j WHERE j.thread_id=c.id AND j.status IN ('QUEUED','RUNNING'))
                 AND NOT EXISTS(SELECT 1 FROM conversation_views v WHERE v.thread_id=c.id AND v.expires_monotonic>? AND v.expires_monotonic<=?)
                 ORDER BY a.eligible_at,c.id LIMIT ?''',(self.clock()-cs.DAYS_30,self.clock(),self.clock()-cs.DAYS_30,mono,mono+91,cs.BATCH_SIZE-count)).fetchall()
            for candidate in candidates:
                if self.workspace.closed or time.monotonic()-started>=cs.TIME_BUDGET or (not force and time.monotonic()<self.foreground_until):break
                if self.before_archive_commit:self.before_archive_commit(dict(candidate))
                try:
                    with self.store.tx() as db:archived+=bool(self.set_archive(db,candidate['id'],True,source='AUTO',candidate=candidate))
                except ValueError as exc:
                    if str(exc) not in {'THREAD_BUSY','ATTACHMENT_BUSY','THREAD_NOT_FOUND'}:raise
                count+=1
        return dict(status='MAINTAINED',migration_complete=complete,items=count,archived=archived,body_bytes_read=read_bytes,body_bytes_rewritten=rewritten,
                    milliseconds=(time.monotonic()-started)*1000,streaming_oversized=bool(pending and pending[0]['bytes']>cs.BYTE_BUDGET))
