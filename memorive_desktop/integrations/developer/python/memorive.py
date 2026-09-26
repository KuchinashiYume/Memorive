"""Memorive API 1.x subprocess client (Python standard library only)."""
import json,os,subprocess,time

class MemoError(RuntimeError):pass

class MemoClient:
    def __init__(self,command,workspace,client_id,timeout=120):
        if not isinstance(command,list) or not command:raise ValueError('command must be an argument list')
        self.command=[*command,'--workspace',str(workspace),'--client',client_id]
        self.timeout=timeout
    def call(self,method,**params):
        result=subprocess.run([*self.command,'--call',method],input=json.dumps(params,ensure_ascii=False),
            capture_output=True,text=True,encoding='utf-8',timeout=self.timeout,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise MemoError(result.stderr.strip()[-300:] or 'MEMO_PROCESS_FAILED')
        try:return json.loads(result.stdout)
        except json.JSONDecodeError:raise MemoError('MEMO_INVALID_RESPONSE') from None
    def capabilities(self):
        value=self.call('memo.capabilities')
        if not value.get('api_version','').startswith('1.'):raise MemoError('MEMO_API_VERSION_UNSUPPORTED')
        return value
    def events(self,cursor=0,limit=100):return self.call('memo.events',cursor=cursor,limit=limit)
    def wait(self,job_id,timeout=300,interval=1):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            job=self.call('memo.job_status',job_id=job_id)
            if job['status'] in {'COMPLETE','ERROR','CANCELLED','INTERRUPTED','UNAVAILABLE'}:return job
            time.sleep(interval)
        raise TimeoutError('MEMO_JOB_PENDING:'+job_id)
