"""CLI verification only: observable, cancellable waiting and safe result parsing.

The network preference is a slow-response notice, never a model deadline.
Existing structured/exam/Core process execution is deliberately unchanged.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

_context=ContextVar('cli_verification_context',default=(lambda value:None,lambda:False))

def resume_owned_process(process):
    """Resume only the newly created suspended child after job ownership is set."""
    if os.name!='nt':return
    import ctypes
    from ctypes import wintypes
    class ThreadEntry(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('usage',wintypes.DWORD),('tid',wintypes.DWORD),
                  ('pid',wintypes.DWORD),('priority',wintypes.LONG),('delta',wintypes.LONG),('flags',wintypes.DWORD)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    for name in ('Thread32First','Thread32Next'):
        method=getattr(kernel,name)
        method.argtypes=[wintypes.HANDLE,ctypes.POINTER(ThreadEntry)]
        method.restype=wintypes.BOOL
    kernel.OpenThread.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenThread.restype=wintypes.HANDLE
    kernel.ResumeThread.argtypes=[wintypes.HANDLE]
    kernel.ResumeThread.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.CloseHandle.restype=wintypes.BOOL
    snapshot=kernel.CreateToolhelp32Snapshot(4,0)
    if snapshot==wintypes.HANDLE(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    ids=[]
    try:
        entry=ThreadEntry();entry.size=ctypes.sizeof(entry)
        more=kernel.Thread32First(snapshot,ctypes.byref(entry))
        while more:
            if entry.pid==process.pid:ids.append(entry.tid)
            entry.size=ctypes.sizeof(entry)
            more=kernel.Thread32Next(snapshot,ctypes.byref(entry))
    finally:kernel.CloseHandle(snapshot)
    # Before primary-thread execution, the new process has exactly one thread.
    if len(ids)!=1:raise OSError('CLI_SUSPENDED_PRIMARY_THREAD_NOT_UNIQUE')
    thread=kernel.OpenThread(2,False,ids[0])
    if not thread:raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.ResumeThread(thread)==0xffffffff:raise ctypes.WinError(ctypes.get_last_error())
    finally:kernel.CloseHandle(thread)

def stop_owned_tree(tree, process):
    """Wait for this Windows job's actual process exit, not a guessed grace delay."""
    if os.name != 'nt' or tree._job_handle is None:
        tree.terminate(process)
        process.wait()
        return
    import ctypes
    from ctypes import wintypes
    class Accounting(ctypes.Structure):
        _fields_=[(name,ctypes.c_int64) for name in ('user','kernel','period_user','period_kernel')]+[
            (name,wintypes.DWORD) for name in ('faults','total','active','terminated')]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
    kernel.TerminateJobObject.restype=wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p]
    kernel.QueryInformationJobObject.restype=wintypes.BOOL
    if not kernel.TerminateJobObject(tree._job_handle,1):
        raise ctypes.WinError(ctypes.get_last_error())
    process.wait()
    while True:
        info=Accounting()
        if not kernel.QueryInformationJobObject(tree._job_handle,1,ctypes.byref(info),ctypes.sizeof(info),None):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.active==0:return
        time.sleep(.01)

def wait_output_release(paths, progress):
    """Windows can report zero job processes before inherited file handles close."""
    if os.name!='nt':return
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    for path in paths:
        while path.exists():
            # Request DELETE permission without deleting: a readiness check on our
            # own spool file, not a process-delay guess or a wider cleanup scan.
            handle=kernel.CreateFileW(str(path),0x10000,7,None,3,0,None)
            if handle!=wintypes.HANDLE(-1).value:
                kernel.CloseHandle(handle)
                break
            error=ctypes.get_last_error()
            if error not in (32,33):raise ctypes.WinError(error)
            progress()
            time.sleep(.01)

@contextmanager
def verification_context(progress, cancelled):
    token=_context.set((progress,cancelled))
    try:yield
    finally:_context.reset(token)

def events(data):
    for line in data.splitlines():
        try:value=json.loads(line)
        except (ValueError,UnicodeDecodeError):continue
        if isinstance(value,dict):yield value

def failure_reason(data):
    # Never return provider output, URLs, environment values, or credentials.
    text=data.decode('utf8','replace').lower() if isinstance(data,bytes) else str(data).lower()
    if 'input_too_large' in text or 'input exceeds the maximum length' in text:return 'CLI_INPUT_TOO_LARGE'
    if any(s in text for s in ('401','unauthorized','not logged in','authentication required','invalid authentication','token expired')):return 'CLI_AUTH_REQUIRED'
    if any(s in text for s in ('429','usage limit','rate limit','quota exceeded','insufficient_quota')):return 'CLI_RATE_LIMITED'
    if 'model' in text and any(s in text for s in ('not found','does not exist','not supported','unsupported','not available','access to')):return 'CLI_MODEL_UNAVAILABLE'
    if any(s in text for s in ('unexpected argument','unrecognized','unknown variant','invalid value','invalid config')):return 'CLI_COMMAND_UNSUPPORTED'
    if any(s in text for s in ('connection refused','connection reset','dns','certificate','network','failed to connect','error sending request','transport error','stream disconnected')):return 'CLI_NETWORK_FAILED'
    return 'CLI_AUTH_OR_MODEL_REQUEST_FAILED'

def result_reason(process):
    rows=list(events(process.stdout));terminal=False;message=False;transient_error=None
    for row in rows:
        kind=row.get('type')
        if kind=='turn.failed' or row.get('is_error') is True:
            return failure_reason(json.dumps(row,ensure_ascii=False))
        if kind=='error':transient_error=failure_reason(json.dumps(row,ensure_ascii=False))
        if kind=='turn.completed':terminal=True
        item=row.get('item')
        if isinstance(item,dict) and kind=='item.completed' and item.get('type')=='agent_message':
            message=message or bool(str(item.get('text','')).strip())
        # Supported older/generic CLI terminal result envelope.
        if kind=='result' and row.get('status') not in {'error','failed'}:
            value=row.get('result',row.get('response'))
            if isinstance(value,str) and value.strip():terminal=True;message=True
        if kind=='message' and row.get('role')=='assistant' and str(row.get('content','')).strip():message=True
        if kind=='result' and row.get('status')=='success':terminal=True
    if process.returncode!=0:return failure_reason(process.stderr or process.stdout)
    if not process.stdout.strip():return 'CLI_RESPONSE_EMPTY'
    if not terminal or not message:return transient_error or 'CLI_RESPONSE_INCOMPLETE'
    return None

def run_verification(transport,*,argv,cwd,environment,stdin_bytes,timeout_seconds,shell,
                     template_mode=False,result_paths=()):
    from .model_validation import ProcessResult
    from model_gateway.execution_core.runner import _TreeController
    if shell is not False:raise ValueError('CLI_VALIDATION_SHELL_FORBIDDEN')
    progress,cancelled=_context.get();workspace=Path(cwd)
    stdout_path=workspace/'stdout.bin';stderr_path=workspace/'stderr.bin'
    start=time.monotonic();last_activity=start;last_publish=0.;stage='STARTING';pending=b''
    observed_types=set();process=None;stopped=False;quota_hit=False;timed_out=False;tree=_TreeController();writer=None
    diagnostic={'hard_timeout_seconds':timeout_seconds if template_mode else None,'cancelled':False,'last_stage':stage,'event_types':[]}
    attempt_path=workspace.parent/('cli-verification-'+uuid.uuid4().hex+'.json')
    stamp=lambda:datetime.now(timezone.utc).isoformat()
    command_hash=hashlib.sha256(json.dumps(list(argv),ensure_ascii=False).encode()).hexdigest().upper()
    requested=argv[argv.index('--model')+1] if not template_mode and '--model' in argv else None
    evidence={'schema_version':'CliVerificationAttempt-v1','attempt_id':attempt_path.stem,'status':'PREPARED',
        'started_at':stamp(),'requested_model':requested,'returned_model':None,
        'command_sha256':command_hash,'behavior_sha256':hashlib.sha256(stdin_bytes or b'').hexdigest().upper(),
        'executable_sha256':hashlib.sha256(Path(argv[0]).read_bytes()).hexdigest().upper(),
        'process_transport':'local_process','access_mode':'CLI_MANAGED_AUTH','billing_mode':'CLI_MANAGED_UNKNOWN',
        'route':'CLI_PROVIDER_MANAGED','region':'NOT_EXPOSED','egress':'CUSTOM_PROXY' if environment.get('HTTPS_PROXY') else 'SYSTEM_OR_DIRECT',
        'actual_cost':None,'token_usage':None,'hard_timeout_seconds':diagnostic['hard_timeout_seconds'],'acceptance_verdict':'NOT_ASSESSED'}
    with attempt_path.open('x',encoding='utf8') as out:json.dump(evidence,out,indent=2)
    diagnostic['attempt_id']=evidence['attempt_id']
    def publish(force=False):
        nonlocal last_publish
        now=time.monotonic()
        if force or now-last_publish>=1:
            last_publish=now
            progress({'stage':stage,'elapsed_ms':round((now-start)*1000),'silent_ms':round((now-last_activity)*1000),
                'slow_response':now-last_activity>=timeout_seconds,'attempt_id':evidence['attempt_id']})
    try:
        publish(True)
        if template_mode:
            from .call_ledger import execution_checkpoint
            execution_checkpoint()
        if cancelled():
            stopped=True
            stage='STOPPING'
            raise OSError('CLI_VERIFICATION_CANCELLED_BEFORE_START')
        with stdout_path.open('xb') as stdout,stderr_path.open('xb') as stderr:
            from .windows_cli import native_process_path
            process=subprocess.Popen(list(argv),cwd=native_process_path(cwd),env=dict(environment),stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                stdout=stdout,stderr=stderr,shell=False,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW|4) if os.name=='nt' else 0,
                start_new_session=os.name!='nt')
            tree.attach(process)
            resume_owned_process(process)
            if stdin_bytes is not None:
                def send_input():
                    try:process.stdin.write(stdin_bytes);process.stdin.close()
                    except (BrokenPipeError,OSError,ValueError):pass
                writer=threading.Thread(target=send_input,daemon=True);writer.start()
            stage='WAITING_RESPONSE';publish(True)
            with stdout_path.open('rb') as reader:
                stderr_size=0
                while process.poll() is None:
                    if template_mode:
                        from .call_ledger import execution_interrupt_callback
                        interrupt=execution_interrupt_callback()
                        if interrupt is not None:interrupt()
                        if time.monotonic()-start>=timeout_seconds:
                            timed_out=True;stage='TIMED_OUT';stop_owned_tree(tree,process);break
                    chunk=reader.read()
                    new_stderr_size=stderr_path.stat().st_size
                    if chunk or new_stderr_size!=stderr_size:last_activity=time.monotonic()
                    stderr_size=new_stderr_size
                    if chunk:
                        pending+=chunk;parts=pending.split(b'\n');pending=parts.pop()
                        for row in events(b'\n'.join(parts)):
                            kind=row.get('type')
                            if template_mode:
                                stage='RECEIVING';continue
                            if kind in {'thread.started','turn.started','turn.completed','turn.failed','item.started','item.completed','error'}:
                                observed_types.add(kind)
                            if kind=='item.completed':stage='RECEIVING'
                            if kind=='error':stage='RECONNECTING'
                    if cancelled():
                        stopped=True;stage='STOPPING';publish(True);stop_owned_tree(tree,process);break
                    quota=transport.capture_quota_bytes
                    if quota is not None and (stdout_path.stat().st_size>quota or stderr_size>quota or any(p.exists() and p.stat().st_size>quota for p in result_paths)):
                        quota_hit=True;stop_owned_tree(tree,process);break
                    publish();time.sleep(.1)
                process.wait()
                if template_mode:stop_owned_tree(tree,process)
        stdout,out_truncated,out_size=transport._read_bounded(stdout_path)
        stderr,err_truncated,err_size=transport._read_bounded(stderr_path)
        result=ProcessResult(started=True,returncode=process.returncode,stdout=stdout,stderr=stderr,
            timed_out=timed_out,output_truncated=quota_hit or out_truncated or err_truncated,duration_ms=round((time.monotonic()-start)*1000),
            stdout_total_bytes=out_size,stderr_total_bytes=err_size,capture_quota_bytes=transport.capture_quota_bytes)
    except (FileNotFoundError,PermissionError,OSError):
        result=ProcessResult(started=process is not None,returncode=process.poll() if process else None,stdout=b'',stderr=b'',duration_ms=round((time.monotonic()-start)*1000))
    finally:
        if process is not None:stop_owned_tree(tree,process)
        if writer is not None:writer.join(timeout=5)
        tree.close(process)
        wait_output_release((stdout_path,stderr_path),publish)
    diagnostic.update(cancelled=stopped,last_stage=stage,event_types=sorted(observed_types))
    observed_types.update(str(row['type']) for row in ([] if template_mode else events(result.stdout))
                          if row.get('type') in {'thread.started','turn.started','turn.completed','turn.failed','item.started','item.completed','error'})
    diagnostic['event_types']=sorted(observed_types)
    from .model_validation import _reported_models, _normalise_token_usage, _usage_from_cli_output
    reported=set() if template_mode else _reported_models(result.stdout)
    evidence.update(status='CANCELLED' if stopped else 'FINISHED',finished_at=stamp(),duration_ms=result.duration_ms,
        returncode=result.returncode,returned_model=requested if requested in reported else None,
        stdout_sha256=hashlib.sha256(result.stdout).hexdigest().upper(),stderr_sha256=hashlib.sha256(result.stderr).hexdigest().upper(),
        token_usage=None if template_mode else _normalise_token_usage(_usage_from_cli_output(result.stdout) or {}),event_types=sorted(observed_types))
    # A sibling terminal record preserves the pre-send binding unchanged.
    with attempt_path.with_suffix('.result.json').open('x',encoding='utf8') as out:json.dump(evidence,out,indent=2)
    return result,diagnostic
