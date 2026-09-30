"""Resumable bounded-byte projection of oversized legacy JSON objects.

SQLite's incremental BLOB reader also supports TEXT columns. Source bytes are
read in 64 KiB pieces, with UTF-8 and JSON token state persisted atomically with
the projection pieces. No canonical body is modified. Publication rechecks the
source revision; a concurrent source write wins over the staged snapshot.
"""
import codecs,json,re,time
from . import conversation_storage as cs

CHUNK=65536
MARK=re.compile(r'["\\]')

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS conversation_build(
      id TEXT PRIMARY KEY,revision INTEGER NOT NULL,offset INTEGER NOT NULL,state TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS conversation_pieces(
      id TEXT NOT NULL,ordinal INTEGER NOT NULL,piece INTEGER NOT NULL,text TEXT NOT NULL,folded TEXT NOT NULL,
      PRIMARY KEY(id,ordinal,piece))''')

class Tokens:
    def __init__(self,state,emit):self.s=state;self.emit=emit
    def path(self):
        stack=self.s['stack']
        if not stack:return []
        top=stack[-1];return top['path']+[top['key'] if top['kind']=='object' else top['index']]
    def done(self):
        if self.s['stack']:
            top=self.s['stack'][-1];top['expect']='comma'
            if top['kind']=='array':top['index']+=1
    def feed(self,text,final=False):
        s=self.s;text=s.pop('pending','')+text;i=0
        while i<len(text):
            mode=s.get('mode')
            if mode=='string':
                match=MARK.search(text,i);end=match.start() if match else len(text)
                if end>i:self.part(text[i:end])
                i=end
                if i==len(text):break
                if text[i]=='"':
                    if s['key_string']:s['stack'][-1].update(key=s.pop('key_buffer'),expect='colon')
                    else:self.emit(s['path'],'',True);self.done()
                    s['mode']=None;i+=1;continue
                need=6 if text[i:i+2]=='\\u' else 2
                if len(text)-i<need:s['pending']=text[i:];break
                if need==6 and 0xD800<=int(text[i+2:i+6],16)<=0xDBFF:
                    if len(text)-i<12:s['pending']=text[i:];break
                    need=12
                self.part(json.loads('"'+text[i:i+need]+'"'));i+=need;continue
            if mode=='primitive':
                end=i
                while end<len(text) and text[end] not in ',]} \t\r\n':end+=1
                s['primitive']+=text[i:end];i=end
                if i==len(text) and not final:break
                self.emit(s['path'],json.loads(s.pop('primitive')),True);s['mode']=None;self.done();continue
            char=text[i]
            if char.isspace():i+=1;continue
            top=s['stack'][-1] if s['stack'] else None
            if char==',':top['expect']='key' if top['kind']=='object' else 'value';i+=1;continue
            if char==':':top['expect']='value';i+=1;continue
            if char in ']}':s['stack'].pop();self.done();i+=1;continue
            path=self.path()
            if char in '[{':
                if len(path)==2 and path[0]=='messages' and isinstance(path[1],int):self.emit(path,{},True)
                s['stack'].append(dict(kind='object' if char=='{' else 'array',path=path,index=0,key=None,expect='key' if char=='{' else 'value'));i+=1;continue
            if char=='"':
                key=bool(top and top['kind']=='object' and top['expect']=='key')
                s.update(mode='string',path=path,key_string=key,key_buffer='');i+=1;continue
            s.update(mode='primitive',path=path,primitive='')
        if final and (s.get('mode') or s['stack'] or s.get('pending')):raise ValueError('CONVERSATION_MIGRATION_JSON_INCOMPLETE')
    def part(self,value):
        if self.s['key_string']:
            self.s['key_buffer']+=value
            if len(self.s['key_buffer'])>65536:raise ValueError('CONVERSATION_MIGRATION_KEY_TOO_LONG')
        else:self.emit(self.s['path'],value,False)

def step(lifecycle,row,budget,deadline):
    store=lifecycle.store;identity=row['id'];used=0;complete=False
    # One chunk transaction owns both parser progress and text pieces. A crash
    # before COMMIT replays the same chunk; there is no duplicate text append.
    while used+CHUNK<=budget and time.monotonic()<deadline:
        with store.tx() as db:
            schema(db)
            fresh=db.execute("SELECT rowid,revision FROM objects WHERE kind='thread' AND id=?",(identity,)).fetchone()
            if fresh is None:return used,True
            indexed=db.execute('SELECT 1 FROM conversation_catalog c JOIN conversation_search s ON s.id=c.id WHERE c.id=? AND c.body_revision=? AND s.source_revision=?',(identity,fresh['revision'],fresh['revision'])).fetchone()
            if indexed:
                # A foreground read/write may have completed this projection
                # while a legacy stream was paused. Its source binding wins.
                db.execute('DELETE FROM conversation_build WHERE id=?',(identity,));db.execute('DELETE FROM conversation_pieces WHERE id=?',(identity,))
                return used,True
            prior=db.execute('SELECT * FROM conversation_build WHERE id=?',(identity,)).fetchone()
            if prior and prior['revision']!=fresh['revision']:
                db.execute('DELETE FROM conversation_build WHERE id=?',(identity,));db.execute('DELETE FROM conversation_pieces WHERE id=?',(identity,));prior=None
            state=json.loads(prior['state']) if prior else dict(stack=[],body={'messages':[]},utf8='',piece=0)
            offset=prior['offset'] if prior else 0
            pieces=[]
            def emit(path,value,final):
                body=state['body']
                if len(path)==2 and path[0]=='messages' and isinstance(path[1],int):
                    while len(body['messages'])<=path[1]:body['messages'].append({'text':''})
                    return
                if len(path)==1 and path[0] in {'project','title','pinned','archived','temporary','created_at','updated_at'}:
                    if isinstance(value,str):body[path[0]]=body.get(path[0],'')+value
                    else:body[path[0]]=value
                    if path[0]=='title' and isinstance(value,str) and value:pieces.append((-1,value))
                if len(path)==3 and path[0]=='messages' and isinstance(path[1],int):
                    message=body['messages'][path[1]];key=path[2]
                    if key=='text':
                        if value:pieces.append((path[1],value))
                    elif key in {'id','role','created_at'}:
                        message[key]=message.get(key,'')+value if isinstance(value,str) else value
            with db.blobopen('objects','body',fresh['rowid'],readonly=True) as blob:
                blob.seek(offset);raw=blob.read(min(CHUNK,budget-used));complete=blob.tell()==len(blob)
            decoder=codecs.getincrementaldecoder('utf-8')();decoder.setstate((bytes.fromhex(state['utf8']),0))
            text=decoder.decode(raw,final=complete);state['utf8']=decoder.getstate()[0].hex()
            Tokens(state,emit).feed(text,final=complete)
            grouped={}
            for ordinal,value in pieces:grouped.setdefault(ordinal,[]).append(value)
            for ordinal,values in grouped.items():
                value=''.join(values)
                db.execute('INSERT INTO conversation_pieces VALUES(?,?,?,?,?)',(identity,ordinal,state['piece'],value,value.casefold()));state['piece']+=1
            used+=len(raw);offset+=len(raw)
            if complete:
                body=state['body'];cs.sync(db,'thread',identity,row['project'],body,fresh['revision'],row['bytes'],new=False,clock=lifecycle.clock())
                # Preserve separators even for empty messages and assemble the
                # search projection within SQLite, without full JSON decoding.
                for ordinal in range(-1,len(body['messages'])):
                    db.execute('INSERT INTO conversation_pieces VALUES(?,?,?,?,?)',(identity,ordinal,-1,'',''))
                db.execute('''WITH texts AS (SELECT ordinal,group_concat(text,'') AS text,group_concat(folded,'') AS folded
                  FROM (SELECT * FROM conversation_pieces WHERE id=? ORDER BY ordinal,piece) GROUP BY ordinal),
                  alltext AS (SELECT group_concat(text,char(10)) AS text,group_concat(folded,char(10)) AS folded FROM (SELECT * FROM texts ORDER BY ordinal)),
                  answers AS (SELECT json_group_array(json_array(json_extract(m.value,'$.id'),coalesce(t.text,''))) AS value
                    FROM json_each(?,'$.messages') m LEFT JOIN texts t ON t.ordinal=CAST(m.key AS INTEGER) WHERE json_extract(m.value,'$.role')='assistant')
                  UPDATE conversation_search SET full_text=(SELECT text FROM alltext),folded=(SELECT folded FROM alltext),answer_texts=(SELECT value FROM answers) WHERE id=?''',
                  (identity,json.dumps(body,ensure_ascii=False),identity))
                db.execute('DELETE FROM conversation_build WHERE id=?',(identity,));db.execute('DELETE FROM conversation_pieces WHERE id=?',(identity,))
            else:db.execute('INSERT INTO conversation_build VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,offset=excluded.offset,state=excluded.state',(identity,fresh['revision'],offset,json.dumps(state,ensure_ascii=False)))
        if complete:break
    return used,complete
