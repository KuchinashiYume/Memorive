"""Declarative command execution on the existing owned-process transport and ledger."""
from copy import deepcopy
import hashlib
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from collections.abc import Mapping

from .cli_templates import (PARSER, TOKEN, configuration_identity, digest, parse_output,
                            validate_local_fields)
from .call_ledger import execution_checkpoint
from .windows_cli import resolve_template_entry, template_command, template_launch_files, native_process_path

def _write(path,value):
    with path.open('x',encoding='utf8') as stream:
        json.dump(value,stream,ensure_ascii=False,allow_nan=False,indent=2)
        stream.flush();os.fsync(stream.fileno())

def execute(runner,service,model,prompt,*,schema=None,purpose='cli_verification',timeout=None,image=None):
    service=deepcopy(dict(service));model=deepcopy(dict(model))
    started=time.monotonic();workspace=None;launched=0;parsed={};reason=None;diagnostic={};binding={};usage=None
    requested=model.get('model_name');wire=prompt.encode('utf8');response=None
    configuration=configuration_identity(service,model)
    receipt=dict(schema_version='SettingsStructuredChatExecutionReceipt-v2',profile_kind='CLI',purpose=purpose,
        route='DECLARATIVE_COMMAND_TEMPLATE',region='PROVIDER_MANAGED_UNDISCLOSED',egress='CLI_PROVIDER_MANAGED',
        requested_model=requested,returned_model=None,behavior_sha256=hashlib.sha256(wire).hexdigest().upper(),
        actual_cost=None,estimated_cost_cny=None,cost_evidence='CLI_MANAGED_UNKNOWN',token_usage=None,
        model_identity_evidence='REQUESTED_ONLY_RETURNED_UNKNOWN',configuration_sha256=configuration,
        parser_version=PARSER,usage_status='PROVIDER_NOT_EXPOSED',native_session_reused=None,native_session_resume_requested=False,
        permissions_enforcement='CLI_CONFIGURATION_NOT_OS_SANDBOX')
    try:
        local=validate_local_fields(service);template=local['template']
        if purpose!='cli_verification' and (model.get('verification') or {}).get('configuration_sha256')!=configuration:raise ValueError('CLI_TEMPLATE_QUALIFICATION_INVALIDATED')
        receipt.update(template_sha256=digest(template),template_revision=template['revision'])
        if not isinstance(requested,str) or not requested:raise ValueError('CLI_MODEL_NOT_CONFIGURED')
        if len(wire)>template['max_input_bytes']:raise ValueError('CLI_INPUT_TOO_LARGE')
        if schema is not None and not template['capabilities']['structured_output']:raise ValueError('CLI_TEMPLATE_STRUCTURED_OUTPUT_UNSUPPORTED')
        if image is not None and not template['capabilities']['images']:raise ValueError('CLI_TEMPLATE_IMAGES_UNSUPPORTED')
        if purpose!='cli_verification' and not all(template['permissions'].values()):raise ValueError('CLI_TEMPLATE_NODE_PERMISSION_UNSATISFIED')
        executable=resolve_template_entry(service['executable'])
        if not executable:raise ValueError('CLI_EXECUTABLE_NOT_FOUND')
        runner._scratch_root.mkdir(parents=True,exist_ok=True)
        workspace=Path(tempfile.mkdtemp(prefix='command-',dir=runner._scratch_root)).resolve()
        prompt_file=workspace/'prompt.txt';response_file=workspace/'response.txt';schema_file=workspace/'schema.json';image_file=workspace/'image.png'
        if response_file.exists():raise ValueError('CLI_TEMPLATE_STALE_RESULT_FILE')
        if template['input']=='file':prompt_file.write_bytes(wire)
        if schema is not None:_write(schema_file,dict(schema))
        if image is not None:image_file.write_bytes(image)
        context=dict(prompt=prompt,prompt_file=native_process_path(prompt_file) if prompt_file.exists() else str(prompt_file),
            response_file=str(response_file),schema_file=native_process_path(schema_file) if schema_file.exists() else str(schema_file),
            image_file=native_process_path(image_file) if image_file.exists() else str(image_file),workdir=native_process_path(workspace),
            model=template['model_map'].get(requested,requested),effort=template['effort_map'].get(model.get('thinking_mode',''),model.get('thinking_mode','')))
        if 'schema_file' in set(TOKEN.findall(' '.join(template['argv']))) and schema is None:_write(schema_file,{})
        arguments=[TOKEN.sub(lambda m:context[m[1]],arg) for arg in template['argv']]
        argv=template_command(executable,arguments,interpreter=local['interpreter'],interpreter_arguments=template['interpreter_argv'])
        if os.name=='nt' and len(subprocess.list2cmdline(argv))>30000:raise ValueError('CLI_TEMPLATE_ARGV_TOO_LONG_USE_STDIN_OR_FILE')
        proxy_mode,proxy_address,_=runner._preferences()
        environment=runner._safe_environment('command_template',workspace,proxy_mode,proxy_address)
        environment.update(PYTHONIOENCODING='utf-8',PYTHONUTF8='1')
        for name,ref in local['environment'].items():
            if ref.startswith('ENV:'):
                value=os.environ.get(ref[4:])
                if value is None:raise ValueError('CLI_TEMPLATE_ENVIRONMENT_UNBOUND:'+name)
            else:
                value=runner._credential_resolver(ref).decode('utf8',errors='strict')
            if '\x00' in value:raise ValueError('CLI_TEMPLATE_ENVIRONMENT_INVALID:'+name)
            environment[name]=value
        # Recheck binaries after queue wait, before committing the attempt.
        execution_checkpoint()
        if configuration_identity(service,model)!=configuration:raise ValueError('CLI_TEMPLATE_EXECUTABLE_CHANGED')
        identities=[]
        for path in dict.fromkeys([executable,*template_launch_files(executable,template['argv'],interpreter=local['interpreter'],interpreter_arguments=template['interpreter_argv'])]):
            identities.append(dict(path=path,sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()))
        command_hash=digest(argv)
        bound_model=context['model'];model_bound=any('{{model}}' in arg for arg in template['argv'])
        binding=dict(configuration_sha256=configuration,template_sha256=digest(template),parser_version=PARSER,
            command_sha256=command_hash,argv=[('<prompt sha256='+receipt['behavior_sha256']+'>') if arg==prompt else arg for arg in argv],
            executable_identities=identities,environment_names=sorted(environment),request_model=bound_model,
            request_model_explicit=model_bound,working_directory=str(workspace),response_file=str(response_file),
            behavior_sha256=receipt['behavior_sha256'],input_mode=template['input'],output_mode=template['output']['mode'],
            image_sha256=hashlib.sha256(image).hexdigest().upper() if image is not None else None,
            schema_sha256=digest(schema) if schema is not None else None,timeout_seconds=min(timeout or template['timeout_seconds'],template['timeout_seconds']))
        _write(workspace/'command.pre.json',binding)
        receipt.update(request_model_binding_sha256=digest(binding),request_model_binding_status='DECLARED_ARGV_AND_BINARY' if model_bound else 'CLI_DEFAULT_MODEL_UNVERIFIED',
            cli_binary_sha256=identities[0]['sha256'],executable_identities=identities,template_attempt_path=str(workspace))
        process,diagnostic=runner._process.run_template(argv=argv,cwd=str(workspace),environment=environment,
            stdin_bytes=wire if template['input']=='stdin' else None,timeout_seconds=binding['timeout_seconds'],shell=False,
            capture_quota_bytes=template['max_output_bytes'],result_paths=(response_file,))
        launched=int(process.started)
        receipt['process_started']=process.started
        if diagnostic.get('cancelled'):raise ValueError('CLI_VERIFICATION_CANCELLED')
        if not process.started:raise ValueError('CLI_PROCESS_START_FAILED')
        if process.timed_out:raise ValueError('CLI_TEMPLATE_TIMEOUT')
        if process.output_truncated:raise ValueError('CLI_OUTPUT_LIMIT_EXCEEDED')
        if process.returncode!=0:raise ValueError('CLI_TEMPLATE_EXIT_NONZERO')
        if response_file.exists() and response_file.resolve().parent!=workspace:raise ValueError('CLI_TEMPLATE_RESULT_FILE_NOT_OWNED')
        parsed=parse_output(template,process.stdout,result_file=response_file)
        returned=parsed['returned_model'];usage=parsed['token_usage']
        if returned is not None and returned!=bound_model:raise ValueError('CLI_RETURNED_MODEL_MISMATCH')
        receipt.update(returned_model=returned,requested_model_mapping=bound_model,token_usage=usage,
            usage_status='REPORTED' if usage else 'PROVIDER_NOT_EXPOSED',model_identity_evidence='CLI_REPORTED_MODEL' if returned else 'DECLARATIVE_EXACT_REQUEST_MODEL' if model_bound else 'REQUESTED_ONLY_RETURNED_UNKNOWN')
        if schema is not None:
            try:response=json.loads(parsed['text'])
            except ValueError:raise ValueError('STRUCTURED_CHAT_RESPONSE_INVALID') from None
            import jsonschema
            try:jsonschema.validate(response,dict(schema))
            except jsonschema.ValidationError:raise ValueError('STRUCTURED_CHAT_SCHEMA_INVALID') from None
            if not isinstance(response,Mapping):raise ValueError('STRUCTURED_CHAT_RESPONSE_INVALID')
        else:response=parsed['text']
        execution_checkpoint()  # late stop never commits a completed answer
    except (ValueError,OSError,KeyError,TypeError) as error:
        reason=str(error) if isinstance(error,ValueError) else 'CLI_TEMPLATE_CONFIGURATION_OR_IO_ERROR'
    # Cooperative control signals intentionally escape this result path.
    duration=max(0,round((time.monotonic()-started)*1000));status='FAILED' if reason else 'PASS'
    receipt.update(status=status,duration_ms=duration,failure_reason=reason,
        response_sha256=digest(response) if response is not None and not reason else None,
        transport_diagnostic={**diagnostic,'generic_template_path':True,'parser_version':PARSER,
            'native_session_reused':None,'native_session_resume_requested':False,'permission_declarations_only':True,'terminal_observed':parsed.get('terminal_observed'),
            'physical_start_count':launched,'command_sha256':binding.get('command_sha256')})
    result=dict(schema_version='SettingsStructuredChatRunnerResult-v1',status=status,reason=reason or 'STRUCTURED_CHAT_COMPLETED',
        requested_model=requested,returned_model=receipt['returned_model'],duration_ms=duration,
        external_network_calls=0,provider_calls=0,external_model_calls=launched,external_process_launches=launched,
        execution_receipt=receipt)
    if not reason:result['response']=response
    if workspace is not None:_write(workspace/'command.post.json',dict(status=status,reason=result['reason'],receipt=receipt))
    return result

def verify(runner,service,model):
    template=service['template'];structured=template['capabilities']['structured_output']
    schema={'type':'object','properties':{'reply':{'type':'string'}},'required':['reply'],'additionalProperties':False} if structured else None
    prompt='Reply with MEMORIVE_READY.'
    if schema:
        from .model_validation import _structured_prompt
        prompt=_structured_prompt(prompt,schema).decode('utf8')
    image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aBzgAAAAASUVORK5CYII=') if template['capabilities']['images'] else None
    outcome=execute(runner,service,model,prompt,schema=schema,image=image)
    result=runner._base('CLI',status='AVAILABLE' if outcome['status']=='PASS' else 'INVALID',reason='CLI_TEMPLATE_CONNECTION_VERIFIED' if outcome['status']=='PASS' else outcome['reason'])
    for key in ('requested_model','returned_model','duration_ms','external_network_calls','provider_calls','external_model_calls','external_process_launches','execution_receipt'):result[key]=outcome[key]
    result['cli_diagnostic']=outcome['execution_receipt']['transport_diagnostic']
    if result['status']=='AVAILABLE':
        result['resolved_executable']=resolve_template_entry(service['executable'])
        result['token_usage']=outcome['execution_receipt']['token_usage'] or {}
        result['verified_capabilities']=['structured_output'] if structured else []
        if outcome['execution_receipt']['token_usage']:result['verified_capabilities'].append('usage')
    return result
