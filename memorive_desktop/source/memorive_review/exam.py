"""Independent M14 reference category using existing exam jobs and transports."""
from copy import deepcopy
import json
from pathlib import Path
import uuid
from .engine import digest
from .store import atomic
from .logic import obj, STRING
from memorive_settings.exam_tiers import nested_selection, tier_manifest, validate_tier
from memorive_settings.call_ledger import execution_checkpoint, call_scope, ExecutionControlSignal

CATEGORY='DATA_ANOMALY_FALSE_POSITIVE_CONTROL'
PROMPT_VERSION='DataAnomalyExamPrompt-v2-explicit-severity'
SCHEMA=obj({'answers':{'type':'array','items':obj({'case_id':STRING,
    'assessment':{'type':'string','enum':['COMPATIBLE','CONDITIONAL_INCONSISTENT','INSUFFICIENT_INFORMATION','NOT_APPLICABLE']},
    'severity':{'type':'string','enum':['NONE','MINOR','CLARIFY','PRIORITY']},'source_quote':STRING,
    'explanation':STRING,'alternative_explanation':STRING,'missing_fields':{'type':'array','items':STRING},
    'motive_judgment':{'const':'NOT_ASSESSED'}})}})

def assets():
    root=Path(__file__).with_name('assets');manifest=json.loads((root/'manifest_v3.json').read_text(encoding='utf-8'))
    for name,sha in manifest['members'].items():
        if Path(name).name!=name or digest((root/name).read_bytes())!=sha:raise ValueError('E2_EXAM_ASSET_HASH_MISMATCH')
    read=lambda n:json.loads((root/n).read_text(encoding='utf-8'))
    return manifest,read('reference_pack_v1.json'),read('answer_key_v3.json'),read('scoring_protocol_v3.json')

def score_answers(cases,answers,key):
    import jsonschema
    errors=[]
    try:jsonschema.validate(answers,SCHEMA)
    except jsonschema.ValidationError:
        return {'score':0,'case_results':[],'repair_ids':[c['case_id'] for c in cases],'schema_valid':False,'reason':'OUTPUT_CONTRACT_INVALID'}
    selected={c['case_id'] for c in cases};rows=answers['answers'];by_id={r['case_id']:r for r in rows}
    if len(by_id)!=len(rows) or set(by_id)!=selected:
        return {'score':0,'case_results':[],'repair_ids':[c['case_id'] for c in cases],'schema_valid':False,'reason':'EXACT_CASE_SET_REQUIRED'}
    gold={r['case_id']:r for r in key['answers']};results=[];tp=fp=fn=tn=0
    for case in cases:
        row=by_id[case['case_id']];expected=gold[case['case_id']]
        anomaly=expected['assessment']=='CONDITIONAL_INCONSISTENT';predicted=row['assessment']=='CONDITIONAL_INCONSISTENT'
        tp+=int(anomaly and predicted);fn+=int(anomaly and not predicted);fp+=int(not anomaly and predicted);tn+=int(not anomaly and not predicted)
        variants=[expected]+key.get('accepted_variants',{}).get(case['case_id'],[])
        matching=[v for v in variants if v['assessment']==row['assessment']]
        def missing_matches(v):
            required=set(v['missing_fields']);optional=set(v.get('optional_missing_fields',[]));actual=set(row['missing_fields'])
            return required<=actual<=required|optional
        axes={'assessment':bool(matching),
            'false_positive_control':predicted==anomaly,
            'source_alignment':bool(row['source_quote'].strip()) and row['source_quote'] in case['text'] and bool(row['explanation'].strip()) and bool(row['alternative_explanation'].strip()),
            'scope_and_severity':any(row['severity']==v['severity'] for v in matching or [expected]),
            'missing_parameters':any(missing_matches(v) for v in matching or [expected])}
        weights={'assessment':40,'false_positive_control':25,'source_alignment':15,'scope_and_severity':10,'missing_parameters':10}
        value=sum(weights[k] for k,passed in axes.items() if passed)
        results.append({'case_id':case['case_id'],'score':value,'axes':axes})
        if not all(axes.values()):errors.append(case['case_id'])
    return {'score':sum(r['score'] for r in results)/len(results),'case_results':results,'repair_ids':errors,'schema_valid':True,
        'confusion_matrix':dict(true_positive=tp,false_positive=fp,false_negative=fn,true_negative=tn),
        'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,
        'false_positive_rate':fp/(fp+tn) if fp+tn else None,'semantic_justification_automatically_proven':False}

class DataAnomalyExamExecutor:
    native_coverage_plan=True
    def __init__(self,scratch_root):
        self._scratch_root=Path(scratch_root)
        manifest,*_=assets()
        self.EXECUTOR_REF='E2_DataAnomaly_v3_'+digest({'manifest':manifest,'scorer':digest(Path(__file__).read_bytes()),'prompt':PROMPT_VERSION,'schema':SCHEMA})[:20]
    def plan(self,node,target,context):
        if node.get('node_id')!='logic_review':return {'status':'NOT_AVAILABLE','reason':'E2_EXAM_NODE_UNSUPPORTED'}
        if target.get('kind') not in {'API','CLI'} or target.get('connection_status')!='AVAILABLE':
            return {'status':'NOT_AVAILABLE','reason':'LOGIC_CLOUD_PROFILE_REQUIRED'}
        from memorive_settings.cli_templates import is_custom,exam_eligible
        if is_custom(target) and not exam_eligible(target):return {'status':'NOT_AVAILABLE','reason':'CLI_TEMPLATE_CAPABILITY_NOT_VERIFIED'}
        manifest,pack,key,protocol=assets();tier=validate_tier(context.get('workflow_exam_tier','FULL'))
        cases=nested_selection(pack['cases'],tier=tier,id_key='case_id',required_ids=manifest['required_controls'],strata=('family','difficulty'))
        coverage=tier_manifest(role=CATEGORY,tier=tier,full_ids=[c['case_id'] for c in pack['cases']],selected_ids=[c['case_id'] for c in cases],
            required_ids=manifest['required_controls'],source_binding=digest(manifest),unit='CASE',floor_reason='NEGATIVE_AND_BOUNDARY_CONTROLS_REQUIRED')
        coverage.update(repair_policy='E2_CATEGORY_REFERENCE_NONLINEAR_V1',semantic_repair_round_limit=2)
        coverage['manifest_sha256']=digest({k:v for k,v in coverage.items() if k!='manifest_sha256'})
        plan={'schema_version':'WorkflowModelExamPlan-v2','status':'READY','mode':'E2_REFERENCE_EXAM','reason':'E2_REFERENCE_REGRESSION_READY',
            'node_id':'logic_review','exam_category_id':CATEGORY,'exam_category_name':'异常数据检出与误报控制',
            'category_revision':protocol['category_revision'],'reference_pack_revision':pack['schema_version'],
            'reference_pack_sha256':digest(pack),'scoring_protocol_revision':protocol['schema_version'],'scoring_protocol_sha256':digest(protocol),
            'answer_key_revision':key['schema_version'],'answer_key_sha256':digest(key),'prompt_version':PROMPT_VERSION,
            'executor_ref':self.EXECUTOR_REF,'profile_kind':target['kind'],'profile_ref':target.get('profile_ref',target.get('config_id')),
            'model_name':target['model_name'],'target_sha256':digest(target),'exam_tier':coverage,'maximum_model_calls':3,
            'minimum_model_calls':1,'semantic_repair_round_limit':2,'budget_cap_cny':5.0 if target['kind']=='API' else None,
            'cost_semantics':'API_COST_CAP' if target['kind']=='API' else 'CLI_MANAGED_UNKNOWN' if target.get('adapter_id')=='command_template' else 'SUBSCRIPTION_CLI_NO_PER_CALL_PRICE',
            'authorization_schema_version':'WorkflowModelExamAuthorization-v1','authorization_required':True,
            'reference_regression_only':True,'qualification_eligible':False,'formal_qualification_eligible':False,
            'horizontal_comparison_eligible':False,'external_model_calls':0,'provider_calls':0,'external_network_calls':0}
        plan['plan_sha256']=digest(plan);return plan
    def execute(self,node,target,context,*,authorization,structured_chat):
        from memorive_settings.workflow_exam import QualityCardReviewerExamExecutor
        from model_gateway.resource_limits import resolve_call_limits
        plan=self.plan(node,target,context)
        if plan.get('status')!='READY':return {'status':'NOT_RUN','reason':plan.get('reason'),'score':None,'external_model_calls':0}
        reason=QualityCardReviewerExamExecutor._authorization_reason(authorization,plan)
        if reason:return {'status':'NOT_RUN','reason':reason,'score':None,'external_model_calls':0}
        manifest,pack,key,protocol=assets();ids=set(plan['exam_tier']['selected_ids']);cases=[c for c in pack['cases'] if c['case_id'] in ids]
        run_id='workflow-exam-'+uuid.uuid4().hex;root=self._scratch_root/'workflow_exams'/run_id;root.mkdir(parents=True,exist_ok=False)
        atomic(root/'plan.json',plan);atomic(root/'authorization_receipt.json',authorization)
        service=deepcopy(dict(target));service['exam_budget_cap_cny']=plan['budget_cap_cny']
        model=deepcopy(dict(target))  # Preserve exact CLI template/model verification binding.
        limits=resolve_call_limits(profile_kind=target['kind'],service=service,model=model,requested_output_tokens=None)
        instruction=('Evaluate each synthetic case only under its explicitly stated conditions. Do not infer author intent or authenticity. '
            'A missing derivative Card field, small sample size, fractional Welch df, weighted or adjusted value, clustered error, '
            'or repeated last digit is not alone an anomaly. Separate arithmetic compatibility, conditional inconsistency, '
            'insufficient information and an inapplicable rule. COMPATIBLE means a licensed comparison is compatible; NOT_APPLICABLE means the proposed rule does not apply under the stated design. '
            'Severity NONE applies to a compatible result, an inapplicable rule, or missing/ambiguous material alone. '
            'MINOR requires an actual supported inconsistency with explicitly established detail or secondary impact. '
            'CLARIFY applies to an actual supported inconsistency whose detail/secondary or primary/core impact is not established. '
            'PRIORITY requires an evidenced threat to the primary causal conclusion with reasonable alternatives checked. '
            'Use exact source quotes and explain a reasonable alternative. List missing field names using: weights, step, '
            'experimental_unit, model, source_methods, df, adjustment, source_image. Do not list irrelevant missing fields. '
            'Return exactly one answer per supplied case. No external tools.\n')
        base=instruction+json.dumps({'prompt_version':PROMPT_VERSION,'cases':cases},ensure_ascii=False)
        feedback='';calls=0;attempts=0;outcome_pending=False;spent=0.0;cost_complete=target['kind']=='API';receipts=[];scores=[];burdens=[];result=None
        try:
            for round_index in range(3):
                execution_checkpoint()
                source_prompt=base+feedback;call_id=f'round-{round_index}'
                atomic(root/(call_id+'.json'),{'request_id':run_id+':'+call_id,'job_id':run_id,'profile_ref':plan['profile_ref'],'prompt_sha256':digest(source_prompt.encode('utf-8')),'answer_key_sent':False,'plan_sha256':plan['plan_sha256']})
                if plan['budget_cap_cny'] is not None:
                    if not cost_complete:raise ValueError('E2_EXAM_REMAINING_COST_UNKNOWN_NO_AUTOMATIC_REPAIR')
                    remaining=plan['budget_cap_cny']-spent
                    if remaining<=0:raise ValueError('E2_EXAM_COST_CAP_REACHED')
                    service['exam_budget_cap_cny']=remaining
                attempts+=1;outcome_pending=True
                with call_scope(job_id=run_id,node_id='logic_review',profile_ref=plan['profile_ref'],task_type='workflow_model_exam'):
                    result=dict(structured_chat(profile_kind=target['kind'],service=service,model=model,prompt=source_prompt,response_schema=SCHEMA,
                        purpose='workflow_model_exam',max_output_tokens=limits['max_output_tokens'],timeout_seconds=limits['timeout_seconds']))
                outcome_pending=False
                atomic(root/(call_id+'.result.json'),result);calls+=int(result.get('external_model_calls') or 0)
                receipt=QualityCardReviewerExamExecutor._validate_call_result(result,requested_model=target['model_name'],profile_kind=target['kind'])
                receipts.append(receipt)
                cost=receipt.get('estimated_cost_cny')
                if target['kind']=='API':
                    import math
                    if isinstance(cost,(int,float)) and not isinstance(cost,bool) and math.isfinite(cost) and cost>=0:spent+=cost
                    else:cost_complete=False
                scored=score_answers(cases,result['response'],key);scores.append(scored);atomic(root/(call_id+'-score.json'),scored)
                if not scored['repair_ids'] or round_index==2:break
                burdens.append(len(scored['repair_ids'])/len(cases))
                feedback='\nReview and replace your prior response. The local checker found applicability, missing-input, source-alignment or severity problems in case IDs: '+', '.join(scored['repair_ids'])+'. The answer key is not supplied. Return the complete case set.\nPrior response:\n'+json.dumps(result['response'],ensure_ascii=False)
            raw=scores[-1]['score'];penalty=sum(coefficient*fraction**1.4 for coefficient,fraction in zip((4,8),burdens));adjusted=max(0,raw-penalty)
            value={'status':'PASS','score':round(adjusted),'score_exact':adjusted,'post_repair_capability_score':raw,
                'repair_adjusted_exam_score':adjusted,'directed_repairs_used':len(burdens),'repair_burden_fractions':burdens,
                'cumulative_category_repair_penalty':{'category_id':CATEGORY,'revision':protocol['schema_version'],'cumulative_penalty':penalty},
                'repair_penalty_applied_to_role_ability':True,'criterion_status':'REFERENCE_SCORE_NO_FORMAL_PASS_CUTOFF','scorability':'SCOREABLE',
                'execution_outcome':'COMPLETED','model_fail_established':False,'scorer_result':scores[-1],
                'stable_score_cache_eligible':plan['exam_tier']['tier']=='FULL','cache_reuse_status':'STABLE_REUSE' if plan['exam_tier']['tier']=='FULL' else 'TIER_LOCAL_ONLY'}
        except ExecutionControlSignal:raise
        except Exception as error:
            value={'status':'NOT_RUN','score':None,'reason':type(error).__name__+':'+str(error)[:160],
                'execution_outcome':'INCOMPLETE','scorability':'NOT_SCOREABLE','stable_score_cache_eligible':False}
        value.update(run_id=run_id,executor_ref=self.EXECUTOR_REF,exam_category_id=CATEGORY,category_revision=protocol['category_revision'],
            requested_model=target['model_name'],returned_model=(result or {}).get('returned_model'),plan_sha256=plan['plan_sha256'],
            exam_tier=plan['exam_tier'],external_model_calls=calls,request_attempts=attempts,external_model_calls_complete=not outcome_pending,request_outcome_uncertain=outcome_pending,estimated_cost_cny=spent if cost_complete else None,cost_estimate_complete=cost_complete,
            provider_received_answer_key=False,provider_received_gold=False,formal_qualification_eligible=False,
            horizontal_comparison_eligible=False,receipt_sha256s=[digest(r) for r in receipts])
        atomic(root/'exam_result.json',value);return value
