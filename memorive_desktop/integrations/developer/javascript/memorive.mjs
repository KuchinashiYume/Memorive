/** Memorive API 1.x. Node built-ins only; shell execution is never used. */
import { spawn } from 'node:child_process';
export class MemoClient {
 constructor(command,workspace,clientId,{timeout=120000}={}) {
  if(!Array.isArray(command)||!command.length)throw Error('command must be an argument list');
  this.command=[...command,'--workspace',workspace,'--client',clientId];this.timeout=timeout;
 }
 call(method,params={}) {
  return new Promise((resolve,reject)=>{
   const child=spawn(this.command[0],[...this.command.slice(1),'--call',method],{shell:false,windowsHide:true,stdio:['pipe','pipe','pipe']});
   let output='',error='',bytes=0,settled=false;
   const finish=(err,value)=>{if(settled)return;settled=true;clearTimeout(timer);err?reject(err):resolve(value);};
   const timer=setTimeout(()=>{child.kill();finish(Error('MEMO_REQUEST_TIMEOUT'));},this.timeout);
   child.stdout.setEncoding('utf8');child.stderr.setEncoding('utf8');
   child.stdout.on('data',s=>{bytes+=Buffer.byteLength(s);if(bytes>16*1024*1024){child.kill();finish(Error('MEMO_RESPONSE_TOO_LARGE'));}else output+=s;});
   child.stderr.on('data',s=>{error=(error+s).slice(-2000);});
   child.on('error',e=>finish(e));child.stdin.on('error',e=>finish(e));
   child.on('close',code=>{if(code!==0)return finish(Error(error.trim().slice(-300)||'MEMO_PROCESS_FAILED'));try{finish(null,JSON.parse(output));}catch{finish(Error('MEMO_INVALID_RESPONSE'));}});
   child.stdin.end(JSON.stringify(params));
  });
 }
 async capabilities(){const c=await this.call('memo.capabilities');if(!c.api_version?.startsWith('1.'))throw Error('MEMO_API_VERSION_UNSUPPORTED');return c;}
 events(cursor=0,limit=100){return this.call('memo.events',{cursor,limit});}
 async wait(jobId,{timeout=300000,interval=1000}={}){
  const end=Date.now()+timeout;
  while(Date.now()<end){const j=await this.call('memo.job_status',{job_id:jobId});if(['COMPLETE','ERROR','CANCELLED','INTERRUPTED','UNAVAILABLE'].includes(j.status))return j;await new Promise(r=>setTimeout(r,interval));}
  throw Error('MEMO_JOB_PENDING:'+jobId);
 }
}
