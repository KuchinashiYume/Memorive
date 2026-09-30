"""Bounded local Marker sessions; no Marker dependencies in the desktop process."""
from __future__ import annotations
from pathlib import Path
import hashlib, json, os, queue, subprocess, sys, threading, time, uuid

def worker_resource(name):
    base=Path(getattr(sys,'_MEIPASS',Path(__file__).parents[2]))/'source/document_processing/document_structure' if getattr(sys,'frozen',False) else Path(__file__).parent
    path=base/name
    if not path.is_file():raise ValueError('MARKER_WORKER_RESOURCE_MISSING:'+name)
    return path

def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def inspect_runtime(root, *, verify=True):
    root=Path(root).resolve()
    lock=root/'RUNTIME_LOCK.json'
    if not lock.is_file(): raise ValueError('MARKER_RUNTIME_LOCK_MISSING')
    value=json.loads(lock.read_text('utf-8'))
    if value.get('schema')!='MemoriveMarkerRuntime-v1': raise ValueError('MARKER_RUNTIME_LOCK_INVALID')
    if verify:
        for entry in value['files']:
            path=(root/entry['path']).resolve()
            if not path.is_relative_to(root) or not path.is_file() or file_sha(path)!=entry['sha256']:
                raise ValueError('MARKER_RUNTIME_FILE_MISMATCH:'+entry['path'])
    python=root/'venv/Scripts/python.exe'
    if not python.is_file(): raise ValueError('MARKER_RUNTIME_PYTHON_MISSING')
    return {'root':str(root),'python':str(python),'lock_sha256':file_sha(lock),
            'marker':'2.0.0','profiles':['fast_no_ocr','fast_ocr','balanced_ocr'],
            'license':'APACHE_CODE_SEPARATE_MODEL_LICENSE','verified':verify}

class MarkerSession:
    def __init__(self, runtime, work, profile='fast_no_ocr', *, timeout=600, interrupt=None):
        if profile not in {'fast_no_ocr','fast_ocr','balanced_ocr'}: raise ValueError('MARKER_PROFILE_INVALID')
        if not 1<=timeout<=3600: raise ValueError('MARKER_TIMEOUT_INVALID')
        self.runtime=Path(runtime).resolve(); self.work=Path(work).resolve()
        self.profile=profile; self.timeout=timeout; self.interrupt=interrupt
        self.process=None; self.tree=None; self.log=None; self.lock=None
        self.queue=queue.Queue(); self.identity=None; self.ready=None

    def __enter__(self):
        from model_gateway.execution_core.runner import _TreeController
        from memorive_settings.cli_verification_transport import resume_owned_process
        try:
            self.identity=inspect_runtime(self.runtime)
            self.work.mkdir(parents=True,exist_ok=False)
            self.lock=(self.runtime/'execution.lock').open('a+b')
            if self.lock.seek(0,2)==0:self.lock.write(b'0');self.lock.flush()
            self.lock.seek(0)
            if os.name=='nt':
                import msvcrt
                try: msvcrt.locking(self.lock.fileno(),msvcrt.LK_NBLCK,1)
                except OSError as error: raise RuntimeError('MARKER_RUNTIME_BUSY') from error
            else:
                import fcntl
                fcntl.flock(self.lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            keep={'SYSTEMROOT','WINDIR','COMSPEC','TEMP','TMP','PATHEXT','PATH','LOCALAPPDATA'}
            env={k:v for k,v in os.environ.items() if k.upper() in keep}
            guard=worker_resource('worker_guard/sitecustomize.py').parent
            env.update(PYTHONPATH=str(guard),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',
                       E10_LOCAL_WORKER='1',E10_NETWORK_DENIAL_LOG=str(self.work/'network-denials.log'),
                       HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',
                       DO_NOT_TRACK='1',NO_PROXY='*',HTTP_PROXY='',HTTPS_PROXY='',ALL_PROXY='')
            self.log=(self.work/'worker.log').open('ab')
            self.tree=_TreeController()
            self.process=subprocess.Popen([self.identity['python'],'-B',str(worker_resource('marker_worker.py')),
                    str(self.runtime),str(self.work),self.profile],env=env,
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,
                    text=True,encoding='utf-8',bufsize=1,shell=False,
                    creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW|4) if os.name=='nt' else 0,
                    start_new_session=os.name!='nt')
            self.tree.attach(self.process); resume_owned_process(self.process)
            self.reader=threading.Thread(target=self._read,daemon=True); self.reader.start()
            self.ready=self._wait(self.timeout)
            if self.ready.get('event')!='ready': raise RuntimeError('MARKER_STARTUP_FAILED:'+str(self.ready))
            return self
        except BaseException:
            self.close(); raise

    def _read(self):
        try:
            while True:
                line=self.process.stdout.readline(65537)
                if not line: break
                if len(line)>65536: raise ValueError('WORKER_PROTOCOL_TOO_LARGE')
                self.queue.put(json.loads(line))
        except Exception as error: self.queue.put({'event':'fatal','error':str(error)})
        finally: self.queue.put({'event':'closed'})

    def _wait(self, timeout):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.interrupt: self.interrupt()
            try: return self.queue.get(timeout=.1)
            except queue.Empty: pass
        raise TimeoutError('MARKER_WORKER_TIMEOUT')

    def convert(self, source, destination):
        source=Path(source).resolve(strict=True); destination=Path(destination).resolve()
        if source.stat().st_size>512*1024*1024: raise ValueError('MARKER_INPUT_TOO_LARGE')
        if source.suffix.lower()=='.pdf':
            import pymupdf
            with pymupdf.open(source) as doc:
                if not 1<=len(doc)<=2000 or doc.needs_pass: raise ValueError('MARKER_PDF_UNSUPPORTED')
        request_id=uuid.uuid4().hex
        payload={'request_id':request_id,'source':str(source),'source_sha256':file_sha(source),'destination':str(destination)}
        self.process.stdin.write(json.dumps(payload,ensure_ascii=False)+'\n'); self.process.stdin.flush()
        try: result=self._wait(self.timeout)
        except BaseException: self.close(); raise
        if result.get('request_id')!=request_id or result.get('status')!='PASS':
            raise RuntimeError('MARKER_CONVERSION_FAILED:'+str(result))
        if result['source_sha256']!=file_sha(source): raise ValueError('MARKER_SOURCE_CHANGED')
        for name,key in [('chunks.json','candidate_sha256'),('candidate.md','markdown_sha256')]:
            if file_sha(destination/name)!=result[key]: raise ValueError('MARKER_OUTPUT_HASH_MISMATCH')
        result['runtime']=self.identity
        result['worker_sha256']=file_sha(worker_resource('marker_worker.py'))
        result['guard_sha256']=file_sha(worker_resource('worker_guard/sitecustomize.py'))
        (destination/'execution.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        return result

    def close(self):
        from memorive_settings.cli_verification_transport import stop_owned_tree
        if self.process is not None:
            if self.process.poll() is None:
                try:
                    self.process.stdin.write('{"operation":"shutdown"}\n'); self.process.stdin.flush()
                    self.process.wait(timeout=3)
                except (OSError,subprocess.TimeoutExpired): stop_owned_tree(self.tree,self.process)
            if self.process.stdin: self.process.stdin.close()
            if self.process.stdout: self.process.stdout.close()
        if self.tree: self.tree.close(); self.tree=None
        if self.log: self.log.close(); self.log=None
        if self.lock: self.lock.close(); self.lock=None

    def __exit__(self,*args): self.close()
