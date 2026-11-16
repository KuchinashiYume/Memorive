from memorive_settings.provider_catalog_aliases import matches_result_model
"""Report workflow configuration and the source-bound model narrative bridge."""
from .common import read,write,sealed,sha
from .task_projection import REPORT_STEPS
PERIODS=('daily','weekly','monthly')

def config(root):
    path=root/'report_workflow.json'
    if not path.exists():
        return {'revision':0,'config':{p:None for p in PERIODS}}
    value=read(path)
    if (value.get('schema_version')!='DesktopReportWorkflow-v1' or type(value.get('revision')) is not int
        or value['revision']<1 or not isinstance(value.get('config'),dict)
        or set(value['config'])!=set(PERIODS)
        or any(ref is not None and (not isinstance(ref,str) or not ref) for ref in value['config'].values())):
        raise ValueError('REPORT_WORKFLOW_INVALID')
    return value

def profile_options(settings_state, local_models, capability='CHAT'):
    """The model profiles of one capability (CHAT, EMBEDDING ...) that research can choose, from the
    saved settings (as settings.get_state returns them) and the recognized local models. A profile is
    eligible when it is connected and its credential is stored; a local model must also be eligible to
    run and have an exact identity."""
    from memorive_current_task.workflow_mapping import build_core_execution_snapshot
    snapshot = build_core_execution_snapshot(settings_state['settings'], settings_revision=settings_state['revision'],
                                             local_model_profiles=local_models)
    options = []
    for row in snapshot['profile_catalog']:
        if row.get('capability') != capability:
            continue
        service = row['execution_profile']['service']
        eligible = (row['connection_status'] == 'AVAILABLE' and row['credential_reference_stored']
                    and (row['profile_kind'] != 'LOCAL'
                         or (service.get('execution_eligible') is True and service.get('exact_identity_available') is True)))
        options.append(dict(profile_ref=row['profile_ref'], model=row['model'], profile_kind=row['profile_kind'],
                            model_name=row['execution_profile']['model']['model_name'], display_name=row['model'],
                            binding_hash=sha(row['execution_profile']), adapter_id=service.get('adapter_id'), eligible=eligible))
    return options


def profiles_offered(service, local_models, capability='CHAT'):
    """profile_options read from the settings service and the local model registry (None when there
    is none). The research workspace, the window-less process and the research runtime all use it."""
    local = local_models.call('local_models.list', {}).get('recognized_models', []) if local_models else []
    return profile_options(service.call('settings.get_state', {}), local, capability)


def catalog(runtime, capability='CHAT'):
    """profiles_offered for the desktop research runtime, from the desktop host it holds."""
    return profiles_offered(runtime.api._service, getattr(runtime.api, '_local_models', None), capability)

def projection(runtime):
    value=config(runtime.root)
    return dict(value,model_options=catalog(runtime),steps=[{'key':key,'name':name} for key,name in REPORT_STEPS])

def save(runtime,*,config:dict,expected_revision):
    if not isinstance(config,dict) or set(config)!=set(PERIODS):
        raise ValueError('REPORT_WORKFLOW_INVALID')
    options={r['profile_ref']:r for r in catalog(runtime)}
    for ref in config.values():
        if ref is not None and (not isinstance(ref,str) or ref not in options or not options[ref]['eligible']):
            raise ValueError('REPORT_MODEL_UNAVAILABLE')
    with runtime.lock:
        if runtime.closed:
            raise ValueError('RESEARCH_CLOSED')
        current=globals()['config'](runtime.root)
        if type(expected_revision) is not int or expected_revision!=current['revision']:
            raise ValueError('REPORT_WORKFLOW_REVISION_CONFLICT')
        value=sealed({'schema_version':'DesktopReportWorkflow-v1','revision':current['revision']+1,'config':dict(config)})
        write(runtime.root/'report_workflow.json',value)
    return projection(runtime)

def freeze(runtime,run_id,period):
    value=config(runtime.root)
    ref=value['config'][period]
    if not ref:
        raise ValueError('REPORT_MODEL_REQUIRED')
    frozen=runtime.api._service.call('settings.report_profile_freeze',{'run_id':run_id,'period':period,'profile_ref':ref})
    frozen['workflow_revision']=value['revision']
    return frozen

def compose(runtime,run_id,params,result):
    from research_reports.api_briefing import BriefingApiError,prepare_mapped_briefing,validate_mapped_narrative
    from research_reports.renderer import render_markdown,render_markdown_with_narrative
    frozen=params['report_model']
    digest=result['digest']
    prompt,schema=prepare_mapped_briefing(digest,language=params.get('language_context'))
    language=params['report_language']
    # Language requirements are already frozen inside the original prompt contract.
    if runtime.stop.is_set():
        raise ValueError('RESEARCH_CANCELLED')
    claim=sealed({'snapshot_id':frozen['snapshot_id'],'profile_ref':frozen['profile_ref'],
                  'profile_kind':frozen['profile_kind'],'model':frozen['model_name'],
                  'behavior_sha256':sha({'prompt':prompt,'schema':schema})})
    write(runtime._run_path(run_id)/'model_request.json',claim)
    response=runtime.api._service.call('settings.report_profile_execute',{'snapshot_id':frozen['snapshot_id'],'prompt':prompt,'response_schema':schema})
    write(runtime._run_path(run_id)/'model_result.json',sealed(response))
    receipt=response.get('execution_receipt') or {}
    attempted = bool(receipt.get('physical_call_ids')) or receipt.get('status') == 'PASS'
    call_count = max(int(receipt.get('external_model_calls') or 0), int(attempted))
    paid_count = max(int(receipt.get('provider_calls') or 0), int(attempted)) if frozen['profile_kind']=='API' else 0
    runtime._state(run_id,model_calls=call_count,
        paid_model_calls=paid_count,
        execution_status=receipt.get('status','NOT_ASSESSED'))
    if response.get('status')!='PASS' or receipt.get('status')!='PASS':
        raise ValueError('REPORT_MODEL_EXECUTION_FAILED')
    if response.get('requested_model')!=frozen['model_name'] or not matches_result_model(response, frozen['model_name']):
        raise ValueError('REPORT_MODEL_IDENTITY_MISMATCH')
    if not __import__('memorive_settings.cli_templates',fromlist=['receipt_evidence_complete']).receipt_evidence_complete(receipt):
        raise ValueError('REPORT_MODEL_RECEIPT_INCOMPLETE')
    try:
        narrative=validate_mapped_narrative(response.get('response'),digest)
    except BriefingApiError as error:
        # The fact report is already complete and source-validated. Keep it
        # available when its optional prose cannot pass reference validation.
        # The original model response and precise failure remain in the run.
        code=str(error)
        notice={'zh-CN':'辅助摘要未通过来源核对，已保留完整资料变更清单。',
                'en-US':'The optional summary failed source checks. The complete change record is preserved.',
                'ja-JP':'補助要約の出典を確認できなかったため、変更記録の全文を保存しました。'}.get(language,'辅助摘要未通过来源核对，已保留完整资料变更清单。')
        heading,_,body=render_markdown(digest,language=params.get('language_context')).partition('\n')
        result.update(markdown=heading+'\n\n'+notice+'\n'+body,narrative=None,
            narrative_status='OMITTED_INVALID',narrative_error_code=code,
            report_model=frozen,model_calls=call_count,paid_model_calls=paid_count,
            execution_receipt=receipt,engine='Desktop source-bound deterministic report')
        runtime._state(run_id,narrative_status='OMITTED_INVALID',narrative_error_code=code)
        return result
    usage=receipt.get('token_usage') or {}
    metadata={'slot':'report_'+params['period'],'provider':frozen['profile_kind'],'model':frozen['model_name'],
              'response_sha256':sha(response['response']),'input_tokens':usage.get('prompt_tokens',usage.get('input_tokens')),
              'output_tokens':usage.get('completion_tokens',usage.get('output_tokens')),'reasoning_tokens':usage.get('reasoning_tokens')}
    result.update(markdown=render_markdown_with_narrative(digest,narrative,metadata,language=params.get('language_context')),narrative=narrative,
                  report_model=frozen,model_calls=call_count,execution_receipt=receipt,
                  paid_model_calls=paid_count,narrative_status='VALIDATED',engine='Desktop source-bound RESEARCH_REPORTS model report')
    return result
