from pathlib import Path
import sys,os,json,uuid,unittest,types,dataclasses
import tempfile, hashlib
_TEMP = tempfile.TemporaryDirectory(prefix='memorive-cache-regression-')
ROOT=Path(_TEMP.name)
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'source'))
os.environ['MEMORIVE_RUNTIME_LOG_LOG_DIR']=str(ROOT/'tests/runtime/ops/runtime_log')
from memorive_settings.cache_session import Continuation,REVISION,IDLE_TTL_SECONDS
from memorive_settings.cache_workspace import structured_chat_workspace
from memorive.models.validation import LiveModelValidationRunner,ProcessResult
S={'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False}
T={'type':'object','properties':{'payload':S},'required':['payload'],'additionalProperties':False}
MODEL={'model_name':'gpt-5.6-sol','thinking_mode':'high','prompt_cache_scope':'fixed-test-scope','prompt_cache_session':REVISION,'prompt_cache_transport_schema':T}
PROMPT=('policy\n<SOURCE_CONTEXT>\n'+('source-only fact; '*500)+'\n</SOURCE_CONTEXT>\n<TASK>first</TASK>').encode()
ARGV=('codex','exec','--ephemeral','--model','gpt-5.6-sol','-')
def folder():
 p=ROOT/'tests/runtime'/uuid.uuid4().hex;p.mkdir(parents=True);return p
def result(thread_id,payload):
 events=[{'type':'thread.started','thread_id':thread_id},{'type':'turn.started'},
  {'type':'item.completed','item':{'id':'item_0','type':'agent_message','text':json.dumps({'payload':payload})}},
  {'type':'turn.completed','usage':{'input_tokens':2048,'cached_input_tokens':1024,'output_tokens':50}}]
 return ProcessResult(True,0,'\n'.join(json.dumps(e) for e in events).encode(),b'')
class SessionTests(unittest.TestCase):
 def seeded(self):
  p=folder();c=Continuation(p,MODEL,PROMPT,T,clock=lambda:100);c.prepare_argv(ARGV)
  tid=str(uuid.uuid4());c.complete(result(tid,{'value':'one'}),{'payload':{'value':'one'}},S)
  return p,tid
 def test_resume_keeps_source_once_and_sends_delta(self):
  p,tid=self.seeded();c=Continuation(p,MODEL,PROMPT.replace(b'first',b'second'),T,clock=lambda:101)
  a=c.prepare_argv(ARGV);self.assertEqual(c.resume_id,tid);self.assertIn('resume',a)
  self.assertNotIn('--last',a);self.assertNotIn('--ephemeral',a)
  self.assertNotIn(b'source-only fact;',c.prompt_bytes);self.assertIn(b'second',c.prompt_bytes)
 def test_expiry_reloads_full_source(self):
  p,tid=self.seeded();c=Continuation(p,MODEL,PROMPT,T,clock=lambda:100+IDLE_TTL_SECONDS)
  self.assertIsNone(c.resume_id);self.assertIn(b'source-only fact;',c.prompt_bytes)
 def test_unfinished_dispatch_never_resumes(self):
  p,tid=self.seeded();c=Continuation(p,MODEL,PROMPT,T,clock=lambda:101);c.prepare_argv(ARGV)
  d=Continuation(p,MODEL,PROMPT,T,clock=lambda:102);self.assertIsNone(d.resume_id)
 def test_changed_source_or_role_or_contract_rejected(self):
  p,tid=self.seeded()
  for model,prompt,schema in [(MODEL,PROMPT.replace(b'fact',b'new'),T),({**MODEL,'model_name':'gpt-5.6-luna'},PROMPT,T),(MODEL,PROMPT,S)]:
   with self.assertRaisesRegex(ValueError,'BINDING_MISMATCH'):Continuation(p,model,prompt,schema,clock=lambda:101)
 def test_state_tamper_rejected(self):
  p,tid=self.seeded();f=next(p.glob('*.complete.json'));f.write_bytes(f.read_bytes().replace(tid.encode(),str(uuid.uuid4()).encode()))
  with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):Continuation(p,MODEL,PROMPT,T,clock=lambda:101)
 def test_original_schema_failure_poisons_session(self):
  p=folder();c=Continuation(p,MODEL,PROMPT,T,clock=lambda:100);c.prepare_argv(ARGV)
  c.complete(result(str(uuid.uuid4()),{'value':2}),{'payload':{'value':2}},S)
  self.assertEqual(c.diagnostic['session_record_status'],'FAILED')
  self.assertIsNone(Continuation(p,MODEL,PROMPT,T,clock=lambda:101).resume_id)
 def test_workspace_lock_and_role_isolation(self):
  p=folder();service={'adapter_id':'codex_cli'}
  with structured_chat_workspace(p,profile_kind='CLI',service=service,model=MODEL,purpose='core_document_processing') as a:
   with self.assertRaises(FileExistsError):
    with structured_chat_workspace(p,profile_kind='CLI',service=service,model=MODEL,purpose='core_document_processing'):pass
   with structured_chat_workspace(p,profile_kind='CLI',service=service,model={**MODEL,'prompt_cache_scope':'other-role'},purpose='core_document_processing') as b:self.assertNotEqual(a,b)
 def test_real_runner_dispatches_twice_and_validates_payload(self):
  h0={'cli_executable':sys.executable,'cli_sha256':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest().upper(),'cli_version':'codex-cli test-fixture'}
  service={'adapter_id':'codex_cli','provider':'openai','executable':h0['cli_executable'],'cli_binary_sha256':h0['cli_sha256'],'cli_version':h0['cli_version'],'cli_identity_authority_ref':'sandbox:H0'}
  class Fake:
   def __init__(self):self.calls=[];self.tid=str(uuid.uuid4())
   def run(self,**kwargs):
    self.calls.append(kwargs)
    p=result(self.tid,{'value':str(len(self.calls))})
    events=[json.loads(line) for line in p.stdout.splitlines()]
    events[-1]['usage']={k:v*len(self.calls) for k,v in events[-1]['usage'].items()}
    return dataclasses.replace(p,stdout='\n'.join(json.dumps(e) for e in events).encode())
  fake=Fake();r=LiveModelValidationRunner(credential_resolver=lambda _:b'',preferences_loader=lambda:{'proxy_mode':'SYSTEM'},scratch_root=folder(),process_transport=fake)
  model={**MODEL,'require_exact_cli_identity_binding':True,'max_output_tokens_mode':'PROVIDER_DEFAULT'}
  values=[]
  for suffix in ('first','second'):
   v=r.execute_structured_chat(profile_kind='CLI',service=service,model=model,prompt=PROMPT.decode().replace('first',suffix),response_schema=S,purpose='core_document_processing')
   self.assertEqual(v['status'],'PASS',v.get('reason'));values.append(v)
  self.assertEqual(len(fake.calls),2);self.assertNotIn('resume',fake.calls[0]['argv']);self.assertIn('resume',fake.calls[1]['argv'])
  a,b=fake.calls;self.assertEqual(a['argv'][a['argv'].index('--output-schema')+1],b['argv'][b['argv'].index('--output-schema')+1])
  self.assertNotEqual(values[0]['response'],values[1]['response'])
  self.assertEqual(values[1]['execution_receipt']['token_usage']['prompt_tokens'],2048)
  self.assertEqual(values[1]['execution_receipt']['token_usage']['completion_tokens'],50)
 def test_cumulative_counter_regression_poisons_session(self):
  p,tid=self.seeded();c=Continuation(p,MODEL,PROMPT,T,clock=lambda:101);c.prepare_argv(ARGV)
  native=result(tid,{'value':'two'});events=[json.loads(line) for line in native.stdout.splitlines()];events[-1]['usage']['input_tokens']=1
  native=dataclasses.replace(native,stdout='\n'.join(json.dumps(e) for e in events).encode())
  c.complete(native,{'payload':{'value':'two'}},S)
  self.assertEqual(c.diagnostic['session_record_status'],'FAILED');self.assertIsNone(c.attempt_usage)
 def test_wrong_returned_session_rejects_business_result(self):
  h0={'cli_executable':sys.executable,'cli_sha256':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest().upper(),'cli_version':'codex-cli test-fixture'}
  service={'adapter_id':'codex_cli','provider':'openai','executable':h0['cli_executable'],'cli_binary_sha256':h0['cli_sha256'],'cli_version':h0['cli_version'],'cli_identity_authority_ref':'sandbox:H0'}
  class Fake:
   def __init__(self):self.n=0
   def run(self,**kwargs):
    self.n+=1;p=result(str(uuid.uuid4()),{'value':str(self.n)})
    e=[json.loads(line) for line in p.stdout.splitlines()];e[-1]['usage']={k:v*self.n for k,v in e[-1]['usage'].items()}
    return dataclasses.replace(p,stdout='\n'.join(json.dumps(v) for v in e).encode())
  f=Fake();r=LiveModelValidationRunner(credential_resolver=lambda _:b'',preferences_loader=lambda:{'proxy_mode':'SYSTEM'},scratch_root=folder(),process_transport=f)
  model={**MODEL,'require_exact_cli_identity_binding':True};values=[]
  for _ in range(2):values.append(r.execute_structured_chat(profile_kind='CLI',service=service,model=model,prompt=PROMPT.decode(),response_schema=S,purpose='core_document_processing'))
  self.assertEqual(values[0]['status'],'PASS');self.assertNotEqual(values[1]['status'],'PASS')
  self.assertEqual(values[1]['reason'],'CODEX_CONTINUATION_VALIDATION_FAILED')
if __name__=='__main__':unittest.main(verbosity=2)
