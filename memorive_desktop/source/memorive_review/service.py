"""Review work owned by the existing Core scheduler and facade.

Waiting inputs and failed requests hold no worker slot. A durable SENDING marker
is never silently replayed after a lost process or an ambiguous provider result.
"""
from __future__ import annotations
from memorive_language.source import context as source_language_context, describe as source_language_evidence
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
from memorive_settings.call_ledger import execution_control, execution_checkpoint, ExecutionControlSignal
from memorive_settings.task_scheduling import scheduled
from memorive_workflow.contracts import DATA_NODE, LOGIC_NODE, CoreArtifact
from .engine import analyze, digest, MAX_BYTES, ENGINE_VERSION, RULE_VERSION
from .store import ReviewStore, atomic, TERMINAL
from .logic import GLOBAL_INPUT_CONTRACT, cloud_profile, segments, global_segment, response_schema, prompt, invoke, validate_response, build_report

from document_processing.document_structure.desktop import StructureDesktop, METHODS as STRUCTURE_METHODS
REVIEW_METHODS=('review.job','review.report','review.disposition','review.control','review.list')+STRUCTURE_METHODS
ATTENTION={'FAILED','UNCERTAIN','PUBLICATION_FAILED'}
FINISHED={'COMPLETED','COMPLETED_LIMITED','SKIPPED','CANCELLED'}

class ReviewService:
    def __init__(self,worker,validation_runner):
        self.worker=worker;self.runner=validation_runner
        self.store=ReviewStore(worker.run_root);self.lock=threading.RLock();self.active=set()
        self.structure=StructureDesktop(worker.run_root)
        for job in self.store.jobs():
            if job['logic_state']=='SENDING':
                saved=Path(job.get('response_path',''))
                expected=(self.worker.run_root/job['job_id']/'logic_responses').resolve()
                recovered=saved.is_file() and saved.resolve().parent==expected
                self.store.update(job['job_id'],'SAVED_RESPONSE_RECOVERED' if recovered else 'PROCESS_INTERRUPTED',
                    {'logic_state':'QUEUED' if recovered else 'UNCERTAIN',
                     'reason_code':'RESUMING_SAVED_RESPONSE' if recovered else 'LOGIC_OUTCOME_UNCERTAIN_NO_AUTOMATIC_REPLAY'})
            elif job['logic_state']=='RUNNING':
                self.store.update(job['job_id'],'RESUME_SAVED_SEGMENTS',{'logic_state':'QUEUED'})
        self.reconcile_terminal_owners()

    def reconcile_terminal_owners(self):
        """Stop orphaned pending work; retain reports and uncertain call outcomes."""
        pending={'WAITING_INPUT','QUEUED','RUNNING','SENDING','PUBLISHING'}
        with self.lock:
            for state in self.store.jobs():
                job_id=state['job_id'];owner=self.worker.facade.get_job(job_id)['control_state']
                if owner not in {'CANCELLED','FAILED'}:continue
                reason='MAIN_TASK_'+owner;changes={}
                if state['data_state'] in {'WAITING_INPUT','RUNNING'}:
                    changes.update(data_state='CANCELLED',data_reason_code=reason)
                if state['logic_state'] in pending:
                    changes.update(paused=False,cancel_requested=True)
                    if job_id not in self.active:
                        changes.update(logic_state='CANCELLED',reason_code=reason)
                changes={k:v for k,v in changes.items() if state.get(k)!=v}
                if changes:
                    try:self.store.update(job_id,'OWNER_TERMINAL_RECONCILED',changes,expected_revision=state['revision'])
                    except ValueError as error:
                        # A concurrent completion owns its saved result. Re-read next tick.
                        if str(error)!='REVIEW_VERSION_CONFLICT':raise
    def register(self,context):
        config=deepcopy(dict(context.workflow_config));choice=config.get('literature_review',{})
        return self.store.create(context.job_id,item_id=context.item_id,source_sha256=context.source_sha256,
            config=config,logic_selected=choice.get('logic_selected',False),source_name=context.source_name)
    def checkpoint(self,job_id):
        from memorive_workflow import node_progress
        while True:
            node_progress.hold(self.store.get(job_id).get('paused',False))
            self.worker._execution_checkpoint(job_id)
            state=self.store.get(job_id)
            if state.get('cancel_requested'):raise ExecutionControlSignal('CANCELLED')
            if not state.get('paused'):return
            time.sleep(.1)
    def prepare_raw(self,context,raw_path):
        state=self.register(context);raw=Path(raw_path).read_bytes()
        if len(raw)>MAX_BYTES:raise ValueError('REVIEW_RAW_MD_SIZE_BUDGET_EXCEEDED')
        text=raw.decode('utf-8-sig');sha=digest(text.encode('utf-8'))
        if state.get('raw_sha256') and state['raw_sha256']!=sha:raise ValueError('REVIEW_RAW_VERSION_CHANGED_REQUIRES_NEW_JOB')
        saved=context.run_root/'review_input.md';atomic(saved,text.encode('utf-8'))
        changes={'raw_path':str(saved),'raw_sha256':sha,'raw_artifact_sha256':digest(raw),'language_context':source_language_context(text),'source_language_evidence':source_language_evidence(text)}
        if state['logic_selected'] and state['logic_state']=='WAITING_INPUT':
            try:
                node=cloud_profile(state['config']['nodes'].get(LOGIC_NODE))
                if not node.get('enabled'):raise ValueError('LOGIC_WORKFLOW_DISABLED')
                capacity=node.get('capacity',{}).get('effective_capacity',{})
                known=[v for v in (capacity.get('input_tokens'),capacity.get('shared_context_tokens')) if isinstance(v,int) and v>0]
                # UTF-8 byte budgeting is conservative for multibyte languages.
                # Existing runner still enforces provider capacity and output.
                chars=min(160000,min(known)-8192) if known else 28000
                if chars<6000:raise ValueError('LOGIC_INPUT_CAPACITY_TOO_SMALL_FOR_REVIEW_CONTRACT')
                parts=segments(text,max_chars=int(chars*.60),full_document_budget=chars-2400)
                if len(parts)>1:parts.append({'segment_id':'global-relations','mode':'GLOBAL_RELATIONS','pending_inventory':True})
                changes.update(logic_state='QUEUED',segment_count=len(parts),completed_segments=0,segments=parts,responses=[],receipts=[],input_capacity_chars=chars)
            except (ValueError,KeyError,TypeError) as error:
                changes.update(logic_state='FAILED',reason_code=str(error))
        return self.store.update(context.job_id,'RAW_READY',changes)
    def data(self,context,*,enabled=True):
        state=self.store.get(context.job_id)
        if state['data_state'] in FINISHED:return state
        if not enabled:return self.store.update(context.job_id,'DATA_SKIPPED',{'data_state':'SKIPPED'})
        self.store.update(context.job_id,'DATA_STARTED',{'data_state':'RUNNING'})
        saved=context.run_root/'data_review_pending.json'
        try:
            if saved.exists():report=json.loads(saved.read_text(encoding='utf-8'))
            else:
                text=Path(state['raw_path']).read_bytes().decode('utf-8')
                rules=state['config'].get('literature_review',{}).get('data_bindings',[])
                key=digest({'raw':state['raw_sha256'],'rules':rules,'engine':ENGINE_VERSION,'rules_version':RULE_VERSION})
                report=self.store.cache(key)
                if report is None:
                    report=analyze(text,source_id='raw-'+state['raw_sha256'],source_sha256=state['raw_sha256'],rules=rules,checkpoint=execution_checkpoint)
                    self.store.cache(key,report)
                report=deepcopy(report);report['source_id']=context.job_id;report['computation_cache_key']=key
                report['language_context']=state.get('language_context') or source_language_context(text)
                report['original_document_sha256']=context.source_sha256
                report['raw_artifact_sha256']=state.get('raw_artifact_sha256',state['raw_sha256'])
                report.pop('report_sha256',None);report.pop('report_id',None);report['report_id']='data-'+digest(report)[:32]
                report['report_sha256']=digest({k:v for k,v in report.items() if k!='report_sha256'})
                atomic(saved,report)
            return self.store.publish(context.job_id,report,binder=lambda artifact:self.worker._bind_artifact(context,artifact))
        except ExecutionControlSignal:raise
        except Exception as error:
            self.store.update(context.job_id,'DATA_FAILED',{'data_state':'FAILED','data_reason_code':type(error).__name__+':'+str(error)[:200]})
            raise
    def claim(self):
        with self.lock:
            self.reconcile_terminal_owners()
            for state in self.store.jobs():
                job_id=state['job_id']
                if state['logic_state'] not in {'QUEUED','PUBLISHING'} or state.get('paused') or job_id in self.active:continue
                if state['data_state'] not in FINISHED:continue
                job=self.worker.facade.get_job(job_id)
                if job['control_state']!='RUNNING':continue
                self.active.add(job_id);return job_id
        return None
    def card_ready(self,context,card_path,*,source_rows=None):
        """Relate actual tombstones to missing source-bound numeric inputs automatically."""
        import re,yaml
        from .pdf_assist import recover_gap
        state=self.store.get(context.job_id)
        if not state or state['data_state']=='SKIPPED' or not state.get('data_report_id'):return
        card_bytes=Path(card_path).read_bytes();card_text=card_bytes.decode('utf-8-sig')
        fences=list(re.finditer(r'(?m)^---[ \t]*\r?$',card_text))
        if len(fences)<2 or fences[0].start()!=0:return
        metadata=yaml.safe_load(card_text[fences[0].end():fences[1].start()])
        omissions=metadata.get('omissions',[]) if isinstance(metadata,dict) else []
        if not omissions:return
        old=self.store.report(state['data_report_id'])
        rules=deepcopy(old.get('rule_bindings',state['config'].get('literature_review',{}).get('data_bindings',[])))
        receipt_key=digest({'card_sha256':digest(card_bytes),'raw_sha256':state['raw_sha256'],'engine':ENGINE_VERSION,'source_rows':source_rows or []})
        if state.get('card_assist_key')==receipt_key:return
        checks={c['rule_id']:c for c in old['checks']}
        raw=Path(state['raw_path']).read_bytes().decode('utf-8');records=[];attempts=[]
        anchor_raw=raw.replace('\r\n','\n').replace('\r','\n')
        from .gaps import automatic_bindings
        associations=automatic_bindings(rules,checks,omissions,raw,source_rows)
        for rule in rules:
            if checks.get(rule.get('rule_id'),{}).get('status')!='INSUFFICIENT_INFORMATION':continue
            for binding in rule.get('card_gap_bindings',[]):
                parameter=binding.get('parameter')
                if 'inputs.'+str(parameter) not in checks[rule['rule_id']].get('missing',[]):continue
                matches=[o for o in omissions if o.get('location')==binding.get('card_location') and o.get('status')=='removed_from_final_card'
                    and (not binding.get('card_omission_id') or digest(o)==binding['card_omission_id'])]
                if len(matches)!=1:continue
                omission=matches[0];gap={'rule_id':rule['rule_id'],'parameter':parameter,'card_omission_id':digest(omission),
                    'reason':'REMOVED','pages':binding.get('pages',[]),'label':binding.get('label',''),
                    'source_sha256':context.source_sha256,'access_excluded':binding.get('access_excluded',False),
                    'automatic':binding.get('automatic',False),'source_context':binding.get('source_context'),
                    'association':binding.get('association'),'chunk_sha256s':binding.get('chunk_sha256s',[])}
                result=None
                # A unique, exact original quote is the first and cheaper source.
                anchors=omission.get('related_anchors',[])
                if binding.get('automatic'):anchors=[a for i,a in enumerate(anchors) if i in binding.get('anchor_indices',[])]
                for anchor in ([] if gap['access_excluded'] else anchors):
                    quote=(anchor.get('quote') or '').replace('\r\n','\n').replace('\r','\n')
                    if not quote or anchor_raw.count(quote)!=1 or not gap['label'] or len(gap['label'])>80:continue
                    found=list(re.finditer(r'(?<!\w)'+re.escape(gap['label'])+r'\s*[:=]\s*([+−-]?(?:\d+(?:\.\d+)?|\.\d+))(?!\w|\.\d)',quote))
                    if len(found)==1:
                        m=found[0];line=anchor_raw[:anchor_raw.index(quote)+m.start(1)].count('\n')+1
                        record={'record_id':f'CARDGAP:{gap["card_omission_id"][:12]}:{parameter}','raw':m.group(1),'value':m.group(1),'relation':'=',
                            'source':{'kind':'RawMD_CARD_ANCHOR','sha256':state['raw_sha256'],'line':line,'quote':quote,'comparison_normalization':'Line endings only; original RawMD hash and bytes retained.'}}
                        result={'status':'RESOLVED','reason':'EXACT_ORIGINAL_ANCHOR','records':[record],'card_written':False,'external_model_calls':0};break
                if result is None:
                    result=recover_gap(context.source_path,source_sha256=context.source_sha256,gap=gap,cache=self.store.cache,checkpoint=execution_checkpoint)
                attempts.append({'gap':gap,'result':result})
                if result['status']=='RESOLVED':
                    record=result['records'][0]
                    record['decimal_places']=max(0,-__import__('decimal').Decimal(record['value']).as_tuple().exponent)
                    if not any(r['record_id']==record['record_id'] for r in records):records.append(record)
                    rule.setdefault('inputs',{})[parameter]=record['record_id']
        if not attempts:return
        report=analyze(raw,source_id=context.job_id,source_sha256=state['raw_sha256'],rules=rules,supplemental_records=records,checkpoint=execution_checkpoint)
        report['language_context']=state.get('language_context') or source_language_context(raw)
        report.update(predecessor_report_id=old['report_id'],card_sha256=digest(card_bytes),card_assistance=attempts,
            automatic_card_associations=associations,original_document_sha256=context.source_sha256,
            raw_artifact_sha256=state.get('raw_artifact_sha256',state['raw_sha256']))
        report.pop('report_sha256',None);report.pop('report_id',None);report['report_id']='data-'+digest(report)[:32];report['report_sha256']=digest(report)
        self.store.publish(context.job_id,report,binder=lambda artifact:self.worker._bind_artifact(context,artifact))
        self.store.update(context.job_id,'CARD_GAP_ASSISTED',{'card_assist_key':receipt_key})
    def blocks_main_claim(self,job_id):
        state=self.store.get(job_id)
        return bool(state and state['mainline_complete'] and state['logic_state'] not in FINISHED and self.worker.facade.get_job(job_id)['control_state'] not in {'CANCELLED','FAILED'})
    def mainline_done(self,context,result):
        state=self.store.get(context.job_id)
        if not state:return True
        if not state['mainline_complete']:
            self.store.update(context.job_id,'MAINLINE_COMPLETED',{'mainline_complete':True,
                'mainline_receipt':{'artifacts':[dict(artifact_id=a.artifact_id,kind=a.kind,path=str(a.path),sha256=a.sha256,display_name=a.display_name,classification=a.classification) for a in result.artifacts],
                    'metrics':dict(result.metrics)}})
        return self.store.get(context.job_id)['logic_state'] in FINISHED
    def restore_result(self,job_id):
        from memorive_workflow.contracts import CoreExecutionResult
        state=self.store.get(job_id)
        if not state or not state['mainline_complete']:return None
        receipt=state['mainline_receipt']
        return CoreExecutionResult(tuple(CoreArtifact(**{**a,'path':Path(a['path'])}) for a in receipt['artifacts']),receipt['metrics'])
    def context(self,job_id):
        from memorive_workflow.contracts import CoreExecutionContext
        job=self.worker.facade.get_job(job_id);state=self.store.get(job_id)
        item=self.worker.inbox.store.read_item(state['item_id']);root=self.worker.run_root/job_id
        return CoreExecutionContext(job_id,job['attempt_id'],state['item_id'],self.worker._source_path(item),item['source_name'],state['source_sha256'],root,root/'artifacts',
            job['snapshots']['workflow_definition']['content'],job['snapshots']['workflow_config']['content'])
    @scheduled('LITERATURE',pipeline=True)
    def run_claimed(self,job_id):
        from memorive_workflow.scheduling import PIPELINE_LANES
        from memorive_workflow.node_progress import session_scope, facade_session, activity, scope
        from memorive_settings.task_scheduling import activity_scope
        lane=None
        try:
            with execution_control(lambda:self.checkpoint(job_id),interrupt=lambda:self.worker._execution_interrupt(job_id)), session_scope(facade_session(self.worker.facade,job_id,self.worker.facade.get_job(job_id)['attempt_id'])), activity_scope(lambda state,node=None:activity(state,LOGIC_NODE)):
                lane=PIPELINE_LANES.enter(LOGIC_NODE)
                state=self.store.get(job_id);context=self.context(job_id)
                activity('RUNNING',LOGIC_NODE)
                report_path=context.run_root/'logic_review_pending.json'
                if report_path.exists():report=json.loads(report_path.read_text(encoding='utf-8'))
                else:
                    node=cloud_profile(state['config']['nodes'][LOGIC_NODE]);text=Path(state['raw_path']).read_bytes().decode('utf-8')
                    if digest(text.encode('utf-8'))!=state['raw_sha256']:raise ValueError('LOGIC_SOURCE_HASH_MISMATCH')
                    self.store.update(job_id,'LOGIC_STARTED',{'logic_state':'RUNNING'})
                    responses=state.get('responses',[]);receipts=state.get('receipts',[])
                    parts=deepcopy(state['segments'])
                    local_units=[p['segment_id'] for p in parts if p.get('mode')=='LOCAL_SEGMENT' and not p.get('not_run_reason')]
                    with scope('LOCAL_LOGIC', local_units) as node_units:
                        for saved_part,saved_response,saved_receipt in zip(parts,responses,receipts):
                            if saved_part.get('mode')!='LOCAL_SEGMENT' or saved_part.get('not_run_reason'):continue
                            try:
                                validate_response(saved_response,text=text,segment=saved_part)
                                from memorive_settings.provider_catalog_aliases import matches_result_model
                                valid=(saved_receipt.get('status')=='PASS' and saved_receipt.get('purpose')=='core_document_processing' and saved_receipt.get('profile_kind')==node['profile_kind'] and __import__('memorive_settings.cli_templates',fromlist=['receipt_evidence_complete']).receipt_evidence_complete(saved_receipt) and matches_result_model(saved_receipt,node['execution_profile']['model']['model_name']))
                            except Exception:valid=False  # Optional observation only; business validation still owns the outcome.
                            if valid:node_units.complete(saved_part['segment_id'])
                        for index in range(len(responses),len(parts)):
                            part=parts[index]
                            if part.get('mode')!='LOCAL_SEGMENT':node_units.close()
                            execution_checkpoint()
                            if part.get('pending_inventory') or (part.get('mode')=='GLOBAL_RELATIONS' and part.get('input_contract')!=GLOBAL_INPUT_CONTRACT):
                                part=global_segment(text,parts[:index],responses,max_chars=state.get('input_capacity_chars',28000)-3000)
                                parts[index]=part
                                self.store.update(job_id,'GLOBAL_SOURCE_PACKET_PREPARED',{'segments':parts})
                            if part.get('not_run_reason'):
                                responses.append({'not_run_reason':part['not_run_reason']})
                                self.store.update(job_id,'GLOBAL_REVIEW_NOT_RUN',{'logic_state':'RUNNING','responses':responses,'completed_segments':len(responses),'reason_code':part['not_run_reason']})
                                continue
                            data=self.store.report(state['data_report_id']) if state.get('data_report_id') else None
                            source_prompt=prompt(part,data_report=data,source_sha256=state['raw_sha256'],max_chars=state.get('input_capacity_chars',28000),language_context=state.get('language_context') or source_language_context(text))
                            if len(source_prompt.encode('utf-8'))>state.get('input_capacity_chars',28000):raise ValueError('LOGIC_FULL_PROMPT_EXCEEDS_PLANNED_CAPACITY')
                            retry_limit=max(0,min(8,int(node.get('retry_count',0))))
                            for safe_retry in range(retry_limit+1):
                                request_id=f'{job_id}:logic:{state["logic_attempt"]}:{part["segment_id"]}:{safe_retry}'
                                response_path=context.run_root/'logic_responses'/f'{state["logic_attempt"]}-{part["segment_id"]}-{safe_retry}.json'
                                if response_path.is_file():
                                    result=json.loads(response_path.read_text(encoding='utf-8'))
                                else:
                                    execution_checkpoint()
                                    self.store.update(job_id,'REQUEST_PREPARED',{'logic_state':'SENDING','request_id':request_id,
                                        'response_path':str(response_path),'prompt_sha256':digest(source_prompt.encode('utf-8'))})
                                    result=invoke(node,validation_runner=self.runner,evidence_root=context.run_root/'logic_calls',job_id=job_id,request_id=request_id,source_prompt=source_prompt,response_schema=response_schema(part))
                                    # The complete outcome is durable before validation or any later call.
                                    atomic(response_path,result)
                                if result.get('status')=='PASS':
                                    self.store.update(job_id,'RESPONSE_RECEIVED',{'logic_state':'RUNNING','reason_code':None})
                                    break
                                known_zero=result.get('external_model_calls')==0 and result.get('external_process_launches',0)==0
                                if known_zero and safe_retry<retry_limit:
                                    self.store.update(job_id,'SAFE_PRE_SEND_RETRY',{'logic_state':'RUNNING','safe_retry':safe_retry+1})
                                    continue
                                self.store.update(job_id,'REQUEST_FAILED',{'logic_state':'FAILED' if known_zero else 'UNCERTAIN','reason_code':result.get('reason','LOGIC_REQUEST_FAILED')})
                                return self.store.public(job_id)
                            response=result.get('response');validate_response(response,text=text,segment=part)
                            receipt=result.get('execution_receipt')
                            if not isinstance(receipt,dict):raise ValueError('LOGIC_EXECUTION_RECEIPT_REQUIRED')
                            from memorive_settings.provider_catalog_aliases import matches_result_model
                            requested=node['execution_profile']['model']['model_name']
                            if (receipt.get('status')!='PASS' or receipt.get('profile_kind')!=node['profile_kind']
                                    or receipt.get('purpose')!='core_document_processing'
                                    or not matches_result_model(result,requested) or not matches_result_model(receipt,requested)
                                    or not __import__('memorive_settings.cli_templates',fromlist=['receipt_evidence_complete']).receipt_evidence_complete(receipt)):
                                raise ValueError('LOGIC_EXECUTION_IDENTITY_OR_USAGE_INVALID')
                            responses.append(response);receipts.append(receipt)
                            self.store.update(job_id,'SEGMENT_SAVED',{'logic_state':'RUNNING','responses':responses,'receipts':receipts,'completed_segments':len(responses)})
                            if part.get('mode')=='LOCAL_SEGMENT':node_units.complete(part['segment_id'])
                    report=build_report(source_id=job_id,source_sha256=state['raw_sha256'],text=text,parts=parts,responses=responses,node=node,data_report_id=state.get('data_report_id'))
                    report.update(language_context=state.get('language_context') or source_language_context(text),original_document_sha256=state['source_sha256'],execution_receipt_sha256s=[digest(r) for r in receipts])
                    report['report_sha256']=digest({k:v for k,v in report.items() if k!='report_sha256'});atomic(report_path,report)
                self.store.update(job_id,'REPORT_SAVED',{'logic_state':'PUBLISHING'})
                execution_checkpoint()
                self.store.publish(job_id,report,binder=lambda artifact:self.worker._bind_artifact(context,artifact))
                return self.store.public(job_id)
        except ExecutionControlSignal as signal:
            state=self.store.get(job_id)
            target='CANCELLED' if signal.state=='CANCELLED' else 'UNCERTAIN' if state['logic_state']=='SENDING' else 'QUEUED'
            self.store.update(job_id,'CONTROL_INTERRUPTED',{'logic_state':target,'reason_code':'EXECUTION_'+signal.state})
            return self.store.public(job_id)
        except Exception as error:
            state=self.store.get(job_id)
            target='UNCERTAIN' if state['logic_state']=='SENDING' else 'PUBLICATION_FAILED' if state['logic_state']=='PUBLISHING' else 'FAILED'
            self.store.update(job_id,'LOGIC_FAILED',{'logic_state':target,'reason_code':type(error).__name__+':'+str(error)[:200]})
            return self.store.public(job_id)
        finally:
            if lane is not None:PIPELINE_LANES.leave(LOGIC_NODE,lane)
            with self.lock:
                self.active.discard(job_id)
                self.reconcile_terminal_owners()
    def control(self,job_id,action,expected_revision,acknowledge_uncertain=False):
        with self.lock:
            state=self.store.get(job_id)
            if not state:raise ValueError('REVIEW_JOB_NOT_FOUND')
            if self.worker.facade.get_job(job_id)['control_state'] not in {'RUNNING','PAUSED'}:raise ValueError('REVIEW_TASK_TERMINAL')
            status=state['logic_state'];changes={}
            if action=='pause' and status in {'WAITING_INPUT','QUEUED','RUNNING','SENDING','PUBLISHING'}:changes={'paused':True}
            elif action=='resume' and state['paused']:changes={'paused':False}
            elif action=='skip' and status in {'WAITING_INPUT','QUEUED','FAILED'} and job_id not in self.active:changes={'logic_state':'SKIPPED','paused':False}
            elif action=='cancel' and status not in FINISHED:
                changes={'cancel_requested':True,'paused':False}
                if job_id not in self.active:changes['logic_state']='CANCELLED'
            elif action=='retry' and status in ATTENTION and job_id not in self.active:
                if status=='UNCERTAIN' and acknowledge_uncertain is not True:raise ValueError('LOGIC_UNCERTAIN_RETRY_ACKNOWLEDGEMENT_REQUIRED')
                if status=='PUBLICATION_FAILED':changes={'logic_state':'PUBLISHING','paused':False,'cancel_requested':False}
                elif state.get('raw_path') and state.get('segments'):
                    changes={'logic_state':'QUEUED','logic_attempt':state['logic_attempt']+1,'paused':False,'cancel_requested':False}
                else:raise ValueError('LOGIC_FROZEN_CONFIGURATION_NOT_RUNNABLE_REQUIRES_NEW_JOB')
            else:raise ValueError('REVIEW_ACTION_NOT_ELIGIBLE')
            self.store.update(job_id,'USER_'+action.upper(),changes,expected_revision=expected_revision)
            return self.store.public(job_id)
    def call(self,method,params):
        if method in STRUCTURE_METHODS:return self.structure.call(method,params)
        if method=='review.job':return self.store.public(**params)
        if method=='review.list':return [self.store.public(r['job_id']) for r in self.store.jobs()]
        if method=='review.report':
            report=self.store.report(**params)
            return {'report':report,'dispositions':self.store.dispositions(report['report_id']),'source_status':self.store.source_status(report)}
        if method=='review.disposition':return self.store.disposition(**params)
        if method=='review.control':return self.control(**params)
        raise ValueError('REVIEW_METHOD_NOT_ALLOWLISTED')
