"""Native-only report execution: server-owned frozen profile, one request per run."""
import copy,json,re,threading,os
from .contracts import canonical_sha256,scan_sensitive
from .model_capabilities import infer_api_model_capability

PERIODS=('daily','weekly','monthly')

class ReportProfileExecution:
    periods=PERIODS
    purpose_prefix="report_"
    directory_name="report_profiles"
    output_tokens=None
    capability='CHAT'
    def __init__(self,controller):
        self.controller=controller
        self.root=controller.store.profile_root/self.directory_name
        self.lock=threading.RLock()

    def freeze(self,*,run_id,period,profile_ref):
        if not isinstance(run_id,str) or not re.fullmatch(r'research-[a-f0-9]{24}',run_id) or period not in self.periods:
            raise ValueError('REPORT_PROFILE_REQUEST_INVALID')
        current=self.controller.store.load(recover_corruption=False)
        settings=current['settings'];kind='API'
        service=next((s for s in settings['model_services'] if s['config_id']==profile_ref),None)
        model=service
        if service is None:
            kind='CLI'
            for item in settings['cli_services']:
                if not item['enabled']:continue
                match=next((m for m in item['models'] if m['profile_ref']==profile_ref),None)
                if match:service=item;model=match;break
        if service is None and isinstance(profile_ref,str) and profile_ref.startswith('local:'):
            kind='LOCAL'
            resolver=self.controller.local_profile_resolver
            service=resolver(profile_ref) if callable(resolver) else None
            model=service
            if service and (service.get('execution_eligible') is not True or service.get('exact_identity_available') is not True or service.get('capability')!=self.capability):
                raise ValueError('REPORT_MODEL_UNAVAILABLE')
        if not service or not model or model.get('connection_status')!='AVAILABLE':
            raise ValueError('REPORT_MODEL_UNAVAILABLE')
        if kind=='API' and infer_api_model_capability(service['provider'],model['model_name'])!=self.capability:
            raise ValueError('REPORT_MODEL_CAPABILITY_INVALID')
        binding={'run_id':run_id,'period':period,'profile_ref':profile_ref,'profile_kind':kind,
                 'service':copy.deepcopy(service),'model':copy.deepcopy(model),
                 'settings_revision':current['revision'],'settings_sha256':current['settings_sha256']}
        scan_sensitive(binding)
        identity=canonical_sha256(binding)
        with self.lock:
            self.root.mkdir(parents=True,exist_ok=True)
            path=self.root/(identity+'.json')
            if not path.exists():self.controller.store._atomic_write(path,binding)
        display_name=('-'.join(str(model.get(k) or '').strip() for k in ('model_name','tier','thinking_mode') if str(model.get(k) or '').strip())
            if kind=='API' else model.get('display_name') or model['model_name'])
        return {'snapshot_id':identity,'profile_ref':profile_ref,'profile_kind':kind,'model_name':model['model_name'],
                'display_name':display_name,
                'settings_revision':current['revision'],'settings_sha256':current['settings_sha256']}

    def execute(self,*,snapshot_id,prompt,response_schema):
        if not isinstance(snapshot_id,str) or not re.fullmatch(r'[A-Fa-f0-9]{64}',snapshot_id):
            raise ValueError('REPORT_PROFILE_SNAPSHOT_INVALID')
        if not isinstance(prompt,str) or not prompt.strip() or not isinstance(response_schema,dict):
            raise ValueError('REPORT_MODEL_INPUT_INVALID')
        with self.lock:
            binding=json.loads((self.root/(snapshot_id+'.json')).read_text(encoding='utf8'))
            if canonical_sha256(binding)!=snapshot_id:raise ValueError('REPORT_PROFILE_SNAPSHOT_INVALID')
            claim=self.root/(snapshot_id+'.attempt.json')
            if claim.exists():raise ValueError('REPORT_MODEL_ALREADY_SENT')
            # Exclusive claim survives cancellation, service death and uncertain provider outcomes.
            with claim.open('x',encoding='utf8') as f:
                json.dump({'status':'REQUEST_CLAIMED','snapshot_id':snapshot_id,
                    'behavior_sha256':canonical_sha256({'prompt':prompt,'schema':response_schema})},f)
                f.flush();os.fsync(f.fileno())
        if self.purpose_prefix=='research_':
            from model_gateway.research_prompt_cache import model_binding
            binding['model']=model_binding(binding['model'],prompt,response_schema,allow_session=binding['profile_kind']=='CLI' and binding['service'].get('adapter_id')=='codex_cli')
        output_tokens=self.output_tokens
        from .call_ledger import call_scope
        with call_scope(job_id=binding["run_id"], profile_ref=binding["profile_ref"], task_type=self.purpose_prefix+binding["period"]):
            result = self._execute_model(binding, snapshot_id, prompt, response_schema, output_tokens)
        scan_sensitive(result)
        result['report_snapshot_id']=snapshot_id
        if self.purpose_prefix=='research_':
            from model_gateway.research_prompt_cache import observation
            result.setdefault('execution_receipt',{})['research_cache']=observation(binding['model'],result.get('execution_receipt',{}))
        self.controller.store._atomic_write(self.root/(snapshot_id+'.result.json'),result)
        self.controller._record_validation_effects(result)
        return result

    def _execute_model(self, binding, snapshot_id, prompt, response_schema, output_tokens):
        return dict(self.controller.model_validation_runner.execute_structured_chat(
            profile_kind=binding['profile_kind'], service=binding['service'], model=binding['model'],
            prompt=prompt, response_schema=response_schema, purpose=self.purpose_prefix+binding['period'],
            **({'max_output_tokens':output_tokens} if output_tokens else {})))
