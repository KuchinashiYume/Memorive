"""Desktop-owned configuration, immutable candidates, and human review state."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime,timezone
import json,os,threading,uuid
from pathlib import Path
from .bundle import build_bundle,normalize_config,load,preview,context_pack
from .contract import canonical,digest,file_sha

METHODS=('structure.settings','structure.configure','structure.probe','structure.status','structure.list','structure.inspect',
         'structure.preview','structure.original','structure.confirm','structure.context','structure.numeric_review')

class StructureDesktop:
    def __init__(self,root):
        self.root=Path(root)/'structure-control';self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'state.json';self.lock=threading.RLock();self.active_jobs=set()
    def _read(self):
        if not self.path.exists():return {'revision':0,'config':normalize_config({}),'jobs':{}}
        return json.loads(self.path.read_text('utf-8'))
    def _write(self,state):
        temp=self.path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        with temp.open('xb') as f:f.write(canonical(state));f.flush();os.fsync(f.fileno())
        os.replace(temp,self.path)
    def settings(self):
        with self.lock:
            state=self._read();return {'revision':state['revision'],'config':state['config'],
                'quality_policy':{'automatic_rawmd_replacement':False,'automatic_fact_admission':False,
                                  'marker_output':'REVIEW_REQUIRED','ocr_qualification':'NOT_QUALIFIED_FOR_AUTOMATIC_TRANSCRIPTION'}}
    def snapshot(self):return deepcopy(self.settings()['config'])
    def probe(self,config):
        accepted=normalize_config(config)
        return {'status':'READY','config':accepted,'downloads':0,'model_calls':0}
    def status(self,job_id):
        if not isinstance(job_id,str) or not job_id:raise ValueError('STRUCTURE_JOB_ID_INVALID')
        with self.lock:
            entry=self._read()['jobs'].get(job_id)
            return {'status':'FOUND','job':self._public(entry)} if entry else {'status':'NOT_FOUND'}
    def configure(self,config,expected_revision):
        accepted=normalize_config(config)
        with self.lock:
            state=self._read()
            if state['revision']!=expected_revision:raise ValueError('STRUCTURE_SETTINGS_REVISION_CONFLICT')
            state['config']=accepted;state['revision']+=1;self._write(state)
            return {'status':'SAVED','revision':state['revision'],'config':accepted,'applies_to':'NEW_TASKS_ONLY'}
    def prepare(self,context,folder):
        config=normalize_config(context.workflow_config.get('document_structure',{}),verify_runtime=False)
        if config.get('mode','off')=='off':return {'status':'DISABLED'}
        from memorive_settings.call_ledger import execution_checkpoint,ExecutionControlSignal
        folder=Path(folder);sources=[p for p in folder.iterdir() if p.name.startswith(('[PDF]','[Original]')) and p.is_file()]
        raws=[p for p in folder.iterdir() if p.name.startswith('[RawMD]') and p.suffix=='.md']
        if len(sources)!=1 or len(raws)!=1:raise ValueError('STRUCTURE_MAINLINE_ARTIFACT_CARDINALITY')
        source,raw=sources[0],raws[0];destination=Path(context.run_root)/'structure-candidate'
        attempt_id=getattr(context,'attempt_id',None)
        # The production CoreExecutionContext already owns attempt identity.
        # Legacy saved entries/fixtures also bind their run directory.
        attempt_key=digest(canonical([attempt_id,str(Path(context.run_root).resolve())]))
        entry={'job_id':context.job_id,'title':context.source_name,'source_path':str(source),'rawmd_path':str(raw),
               'source_sha256':file_sha(source),'rawmd_sha256':file_sha(raw),'bundle_path':str(destination),
               'status':'PENDING','config':deepcopy(config),'viewed':{},'confirmations':{},
               'attempt_id':attempt_id,'attempt_key':attempt_key,'attempt_history':[]}
        with self.lock:
            state=self._read();old=state['jobs'].get(context.job_id)
            if context.job_id in self.active_jobs:raise RuntimeError('STRUCTURE_ATTEMPT_BUSY')
            if old:
                if old['source_sha256']!=entry['source_sha256'] or old['rawmd_sha256']!=entry['rawmd_sha256'] or old['config']!=entry['config']:
                    raise ValueError('STRUCTURE_JOB_BINDING_CHANGED')
                if old['status'] in {'CANDIDATE_NOT_ADMITTED','PARTIALLY_CONFIRMED'}:
                    self._entry(state,context.job_id);return old
                previous_key=old.get('attempt_key') or digest(canonical([old.get('attempt_id'),str(Path(old['bundle_path']).parent.resolve())]))
                if old['status']=='FAILED' and previous_key==attempt_key:return old
                entry['attempt_history']=[*deepcopy(old.get('attempt_history',[])),
                    deepcopy({k:v for k,v in old.items() if k!='attempt_history'})]
            state['jobs'][context.job_id]=deepcopy(entry);self._write(state)
            self.active_jobs.add(context.job_id)
        from memorive_workflow.node_progress import scope
        stages=None
        def progress(stage,details):
            if stage in {'parse','validate','review'}:stages.complete({'parse':'probe','validate':'parse','review':'validate'}[stage])
            with self.lock:
                state=self._read();entry.update(stage=stage,progress=details);state['jobs'][context.job_id]=deepcopy(entry);self._write(state)
        try:
            stages=scope('DOCUMENT_STRUCTURE',['probe','parse','validate'])
            receipt=build_bundle(source,raw,destination,config,interrupt=execution_checkpoint,progress=progress)
            _,value=load(destination)
            entry.update(status='CANDIDATE_NOT_ADMITTED',structure_id=receipt['structure_id'],
                         block_count=len(value['blocks']),table_count=sum(len(b['tables']) for b in value['blocks']))
        except BaseException as error:
            entry.update(status='FAILED',error_type=type(error).__name__,error=str(error)[:500],rawmd_preserved=True)
            if isinstance(error,ExecutionControlSignal):entry['control_state']=error.state
            if not isinstance(error,Exception):raise
        finally:
            if stages is not None:stages.close()
            with self.lock:
                try:
                    state=self._read();state['jobs'][context.job_id]=deepcopy(entry);self._write(state)
                finally:self.active_jobs.discard(context.job_id)
        return entry
    def _entry(self,state,job_id):
        entry=state['jobs'].get(job_id)
        if not entry:raise ValueError('STRUCTURE_JOB_NOT_FOUND')
        for path,sha in [('source_path','source_sha256'),('rawmd_path','rawmd_sha256')]:
            p=Path(entry[path])
            if not p.is_file() or file_sha(p)!=entry[sha]:raise ValueError('STRUCTURE_SOURCE_STALE')
        receipt,value=load(entry['bundle_path'])
        if receipt['structure_id']!=entry['structure_id']:raise ValueError('STRUCTURE_REVISION_CHANGED')
        return entry,receipt,value
    @staticmethod
    def _public(entry):
        return {k:v for k,v in entry.items() if k not in {'source_path','rawmd_path','bundle_path','viewed','attempt_history'}}
    def inspect(self,job_id,offset=0,limit=200):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=200:raise ValueError("STRUCTURE_BLOCK_WINDOW_INVALID")
        with self.lock:
            entry,receipt,value=self._entry(self._read(),job_id)
            return {'job':self._public(entry),'block_total':len(value['blocks']),'offset':offset,'limit':limit,'warnings':value['warnings'],'units':value.get('units',value.get('pages',[])),
                    'blocks':[{k:b.get(k) for k in ['block_id','block_type','physical_page','source_location','reading_order']}
                              | {'preview':b['text'][:240],'table_count':len(b['tables'])} for b in value['blocks'][offset:offset+limit]]}
    def original(self,job_id):
        with self.lock:
            entry,_,value=self._entry(self._read(),job_id)
            return {'path':entry['source_path'],'source_sha256':value['source_sha256']}
    def show(self,job_id,block_id):
        with self.lock:
            state=self._read();entry,_,value=self._entry(state,job_id)
            result=preview(entry['bundle_path'],block_id)
            entry['viewed'][block_id]=value['structure_id'];self._write(state)
            return result
    def confirm(self,job_id,block_id,structure_id,note,operation_id):
        if not isinstance(note,str) or not 1<=len(note.strip())<=1000:raise ValueError('STRUCTURE_REVIEW_NOTE_REQUIRED')
        if not isinstance(operation_id,str) or not 1<=len(operation_id)<=100:raise ValueError('STRUCTURE_OPERATION_ID_INVALID')
        with self.lock:
            state=self._read();entry,_,value=self._entry(state,job_id)
            if structure_id!=value['structure_id'] or entry['viewed'].get(block_id)!=structure_id:
                raise ValueError('STRUCTURE_OPEN_CURRENT_SOURCE_REQUIRED')
            receipt={'structure_id':structure_id,'block_id':block_id,'note':note.strip(),'operation_id':operation_id,
                     'source_sha256':value['source_sha256'],'rawmd_sha256':value['rawmd_sha256']}
            previous=entry['confirmations'].get(block_id)
            if previous and previous['operation_id']==operation_id:
                if any(previous[k]!=v for k,v in receipt.items()):raise ValueError('STRUCTURE_OPERATION_CONFLICT')
                return previous
            receipt['confirmed_at']=datetime.now(timezone.utc).isoformat();entry['confirmations'][block_id]=receipt
            entry['status']='PARTIALLY_CONFIRMED';self._write(state);return receipt
    def pack(self,job_id,budget):
        with self.lock:
            entry,_,_=self._entry(self._read(),job_id)
            return context_pack(entry['bundle_path'],list(entry['confirmations']),budget=budget)
    def numeric_review(self,job_id,rules=None):
        from memorive_review.engine import analyze,extract,NUMBER,UNITS
        with self.lock:
            entry,_,value=self._entry(self._read(),job_id)
            records=[]
            for block in value['blocks']:
                if block['block_id'] not in entry['confirmations']:continue
                for table in block['tables']:
                    for index,cell in enumerate(table['cells']):
                        if cell.get('numeric_review_eligible') is False:continue
                        if cell.get('formula') is not None:continue # cached formula results are not independently verified values
                        literal=cell['text'].strip();match=NUMBER.match(literal)
                        if match is None:continue
                        remainder=literal[match.end():].strip()
                        if remainder and UNITS.fullmatch(remainder) is None:continue
                        parsed=extract(cell['text'],source_sha256=digest(cell['text'].encode()).upper(),checkpoint=lambda:None)
                        # Ambiguous, compound or nonnumeric cells cannot become an atomic numeric input.
                        if len(parsed['records'])!=1:continue
                        record=deepcopy(parsed['records'][0]);record['record_id']='STRUCTURE:'+block['block_id']+':'+str(table['table_index'])+':'+str(index)
                        record['source']={'kind':'CONFIRMED_STRUCTURE_CELL','sha256':value['source_sha256'],
                                          'structure_id':value['structure_id'],'block_id':block['block_id'],
                                          'table':table['table_index'],'row':cell['row'],'column':cell['column'],
                                          'quote':cell['text'],'locator':cell.get('source_location',block.get('source_location')),
                                          'review_receipt':entry['confirmations'][block['block_id']]}
                        records.append(record)
            raw=Path(entry['rawmd_path']).read_text('utf-8')
            report=analyze(raw,source_id=job_id,rules=rules or [],supplemental_records=records)
            return {'report':report,'confirmed_structure_records':records,'structure_id':value['structure_id'],
                    'admission':'EXPLICIT_RULE_BINDINGS_REQUIRED','cloud_calls':0}
    def call(self,method,params):
        if method=='structure.settings':return self.settings()
        if method in {'structure.configure','structure.probe'}:
            # ValueError is deliberately redacted by the general IPC boundary.
            # Return only a bounded domain code so this settings form can explain
            # a missing runtime or stale revision without leaking private paths.
            try:return (self.configure if method=='structure.configure' else self.probe)(**params)
            except ValueError as error:
                import re
                code=str(error).split(':',1)[0]
                return {'status':'BLOCKED','code':code if re.fullmatch(r'[A-Z][A-Z0-9_]{1,100}',code) else 'STRUCTURE_CONFIG_INVALID'}
        if method=='structure.status':return self.status(**params)
        if method=='structure.list':
            with self.lock:return [self._public(v) for v in self._read()['jobs'].values()]
        if method=='structure.inspect':return self.inspect(**params)
        if method=='structure.preview':return self.show(**params)
        if method=='structure.original':return self.original(**params)
        if method=='structure.confirm':return self.confirm(**params)
        if method=='structure.context':return self.pack(**params)
        if method=='structure.numeric_review':return self.numeric_review(**params)
        raise ValueError('STRUCTURE_METHOD_NOT_ALLOWED')
