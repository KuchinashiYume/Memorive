"""Versioned declarative CLI contracts. No brand inference, evaluation or execution."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import uuid
from collections.abc import Mapping

ADAPTER = 'command_template'
SCHEMA = 'CLIConnectionTemplate-v1'
PORTABLE = 'CLIConnectionExport-v1'
PARSER = 'DeclarativeCLIParser-v1'
PLACEHOLDERS = {'prompt','prompt_file','response_file','model','effort','workdir','schema_file','image_file'}
TOKEN = re.compile(r'\{\{([^{}]+)\}\}')
PATH = re.compile(r'(?:[A-Za-z_][A-Za-z0-9_-]*|[0-9]+)(?:\.(?:[A-Za-z_][A-Za-z0-9_-]*|[0-9]+))*\Z')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')
ENV = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}\Z')
CAPABILITIES = {'structured_output','images','usage'}
LOCAL_FIELDS = {'template','interpreter','environment','concurrency_group'}

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')).hexdigest().upper()

def is_custom(service):
    return service.get('adapter_id') == ADAPTER

def _object(value, required, optional, field):
    if not isinstance(value,Mapping) or not required <= set(value) or set(value)-required-optional:
        raise ValueError('CLI_TEMPLATE_FIELDS_INVALID:'+field)

def _string(value,field,*,empty=False,limit=8192):
    if not isinstance(value,str) or (not empty and not value.strip()) or len(value)>limit or '\x00' in value:
        raise ValueError('CLI_TEMPLATE_STRING_INVALID:'+field)
    return value

def _path(value,field,*,empty=False):
    _string(value,field,empty=empty,limit=256)
    if value and PATH.fullmatch(value) is None:raise ValueError('CLI_TEMPLATE_PATH_INVALID:'+field)

def select(value,path):
    if path == '':return value
    for part in path.split('.'):
        if isinstance(value,Mapping):value=value.get(part)
        elif isinstance(value,list) and part.isdecimal() and int(part)<len(value):value=value[int(part)]
        else:return None
    return value

def _condition(value,field):
    if not isinstance(value,Mapping) or not 1<=len(value)<=8:raise ValueError('CLI_TEMPLATE_MATCH_INVALID:'+field)
    for path,expected in value.items():
        _path(path,field)
        if not (expected is None or type(expected) in {str,int,bool}):raise ValueError('CLI_TEMPLATE_MATCH_VALUE_INVALID:'+field)

def matches(value,condition):
    return isinstance(value,Mapping) and all(type(select(value,k)) is type(v) and select(value,k)==v for k,v in condition.items())

def default_template():
    return dict(schema_version=SCHEMA,revision=1,argv=['{{prompt}}'],interpreter_argv=[],input='argument',
        output=dict(mode='text'),termination=dict(success=[],failure=[]),
        capabilities=dict(structured_output=False,images=False,usage=False),
        permissions=dict(read_only=True,tools_disabled=True),working_directory='attempt',
        timeout_seconds=300,max_output_bytes=16*1024*1024,max_input_bytes=8*1024*1024,
        model_map={},effort_map={})

def validate_template(value):
    required=set(default_template())
    _object(value,required,{'metadata'},'template')
    if value['schema_version']!=SCHEMA:raise ValueError('CLI_TEMPLATE_SCHEMA_UNSUPPORTED')
    if type(value['revision']) is not int or value['revision']<1:raise ValueError('CLI_TEMPLATE_REVISION_INVALID')
    if value['working_directory']!='attempt':raise ValueError('CLI_TEMPLATE_WORKING_DIRECTORY_INVALID')
    if value['input'] not in {'stdin','file','argument'}:raise ValueError('CLI_TEMPLATE_INPUT_INVALID')
    seen=set()
    for field in ('argv','interpreter_argv'):
        args=value[field]
        if not isinstance(args,list) or len(args)>128:raise ValueError('CLI_TEMPLATE_ARGV_INVALID:'+field)
        for arg in args:
            _string(arg,field,empty=True)
            if re.match(r'(?i)^--?(api[-_]?key|access[-_]?token|password|secret)(?:=|$)',arg):raise ValueError('CLI_TEMPLATE_SECRET_ARGUMENT_FORBIDDEN')
            names=set(TOKEN.findall(arg));seen.update(names)
            if names-PLACEHOLDERS or '{{' in TOKEN.sub('',arg) or '}}' in TOKEN.sub('',arg):raise ValueError('CLI_TEMPLATE_PLACEHOLDER_INVALID:'+field)
            if field=='interpreter_argv' and names:raise ValueError('CLI_TEMPLATE_INTERPRETER_ARGUMENT_DYNAMIC')
            if '{{prompt}}' in arg and arg!='{{prompt}}':raise ValueError('CLI_TEMPLATE_PROMPT_MUST_BE_SINGLE_ARGUMENT')
    required_input={'stdin':None,'file':'prompt_file','argument':'prompt'}[value['input']]
    if required_input and required_input not in seen:raise ValueError('CLI_TEMPLATE_INPUT_SLOT_MISSING')
    if value['input']!='argument' and 'prompt' in seen:raise ValueError('CLI_TEMPLATE_PROMPT_SLOT_CONFLICT')
    for field in ('model_map','effort_map'):
        if not isinstance(value[field],Mapping) or len(value[field])>64:raise ValueError('CLI_TEMPLATE_MAPPING_INVALID:'+field)
        for k,v in value[field].items():
            _string(k,field,limit=128);_string(v,field,empty=field=='effort_map',limit=128)
            if TOKEN.search(v):raise ValueError('CLI_TEMPLATE_RECURSIVE_MAPPING')
    for field,low,high in (('timeout_seconds',1,86400),('max_output_bytes',1,1024*1024*1024),('max_input_bytes',1,64*1024*1024)):
        if type(value[field]) is not int or not low<=value[field]<=high:raise ValueError('CLI_TEMPLATE_LIMIT_INVALID:'+field)
    _object(value['capabilities'],CAPABILITIES,set(),'capabilities')
    if any(type(v) is not bool for v in value['capabilities'].values()):raise ValueError('CLI_TEMPLATE_CAPABILITY_INVALID')
    if ('image_file' in seen) != value['capabilities']['images']:raise ValueError('CLI_TEMPLATE_IMAGE_SLOT_CONFLICT')
    _object(value['permissions'],{'read_only','tools_disabled'},set(),'permissions')
    if any(type(v) is not bool for v in value['permissions'].values()):raise ValueError('CLI_TEMPLATE_PERMISSION_INVALID')
    termination=value['termination'];_object(termination,{'success','failure'},set(),'termination')
    for key in ('success','failure'):
        if not isinstance(termination[key],list) or len(termination[key])>16:raise ValueError('CLI_TEMPLATE_TERMINATION_INVALID')
        for cond in termination[key]:_condition(cond,'termination.'+key)
    output=value['output'];_object(output,{'mode'},{'path','events','file_format','ignore_non_json_lines','dedupe_path'},'output')
    if output['mode'] not in {'text','json','jsonl','file'}:raise ValueError('CLI_TEMPLATE_OUTPUT_INVALID')
    allowed_output={
        'text':{'mode'},
        'json':{'mode','path','dedupe_path'},
        'jsonl':{'mode','events','ignore_non_json_lines','dedupe_path'},
        'file':{'mode','file_format','path','ignore_non_json_lines','dedupe_path'},
    }[output['mode']]
    if set(output)-allowed_output:raise ValueError('CLI_TEMPLATE_OUTPUT_FIELDS_UNSUPPORTED:'+output['mode'])
    if output['mode']=='text' and (any(termination.values()) or value.get('metadata')):
        raise ValueError('CLI_TEMPLATE_TEXT_RULES_UNSUPPORTED')
    if output['mode']=='file' and output.get('file_format')=='text' and 'path' in output:
        raise ValueError('CLI_TEMPLATE_OUTPUT_FIELDS_UNSUPPORTED:file.path')

    if output['mode']=='file':
        if 'response_file' not in seen or output.get('file_format') not in {'text','json'}:raise ValueError('CLI_TEMPLATE_RESULT_FILE_INVALID')
    elif 'file_format' in output:raise ValueError('CLI_TEMPLATE_OUTPUT_FIELDS_INVALID:file_format')
    if 'path' in output:_path(output['path'],'output.path',empty=True)
    if 'ignore_non_json_lines' in output and type(output['ignore_non_json_lines']) is not bool:raise ValueError('CLI_TEMPLATE_OUTPUT_BOOLEAN_INVALID')
    if 'dedupe_path' in output:_path(output['dedupe_path'],'output.dedupe_path')
    events=output.get('events',[])
    if not isinstance(events,list) or len(events)>32:raise ValueError('CLI_TEMPLATE_EVENTS_INVALID')
    for row in events:
        _object(row,{'match','path','operation'},set(),'output.events')
        _condition(row['match'],'output.events.match');_path(row['path'],'output.events.path')
        if row['operation'] not in {'append','replace'}:raise ValueError('CLI_TEMPLATE_EVENT_OPERATION_INVALID')
    if output['mode']=='jsonl' and (not events or not termination['success']):raise ValueError('CLI_TEMPLATE_JSONL_FINAL_RULE_REQUIRED')
    metadata=value.get('metadata',{})
    _object(metadata,set(),{'model_path','usage_paths'},'metadata')
    if 'model_path' in metadata:_path(metadata['model_path'],'metadata.model_path')
    usage=metadata.get('usage_paths',{})
    _object(usage,set(),{'prompt_tokens','completion_tokens','total_tokens','cached_input_tokens'},'metadata.usage_paths')
    for p in usage.values():_path(p,'metadata.usage_paths')
    if bool(usage)!=value['capabilities']['usage']:raise ValueError('CLI_TEMPLATE_USAGE_DECLARATION_CONFLICT')
    return deepcopy(dict(value))

def validate_local_fields(service):
    template=validate_template(service['template'])
    interpreter=service.get('interpreter','');_string(interpreter,'interpreter',empty=True,limit=260)
    environment=service.get('environment',{})
    if not isinstance(environment,Mapping) or len(environment)>32:raise ValueError('CLI_TEMPLATE_ENVIRONMENT_INVALID')
    for name,ref in environment.items():
        if not isinstance(name,str) or ENV.fullmatch(name) is None:raise ValueError('CLI_TEMPLATE_ENV_NAME_INVALID')
        if not isinstance(ref,str) or not (ref.startswith('ENV:') and ENV.fullmatch(ref[4:]) or re.fullmatch(r'REF:windows:[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+',ref)):
            raise ValueError('CLI_TEMPLATE_ENV_REFERENCE_REQUIRED:'+name)
    group=service.get('concurrency_group','')
    if not isinstance(group,str) or (group and ID.fullmatch(group) is None):raise ValueError('CLI_TEMPLATE_CONCURRENCY_GROUP_INVALID')
    from .windows_cli import resolve_template_entry,template_launch_files
    executable=resolve_template_entry(service.get('executable',''))
    if executable:template_launch_files(executable,template['argv'],interpreter=interpreter,interpreter_arguments=template['interpreter_argv'])
    return dict(template=template,interpreter=interpreter,environment=dict(environment),concurrency_group=group)

def new_service():
    return dict(config_id='custom-'+uuid.uuid4().hex[:16],adapter_id=ADAPTER,display_name='自定义 CLI',executable='',enabled=False,
        connection_status='UNVERIFIED',models=[],template=default_template(),interpreter='',environment={},concurrency_group='')


def _portable_local_argument(arg):
    # Peel named option/config assignments, not '=' inside a URL or arbitrary text.
    value=arg
    while True:
        if len(value)>=2 and value[0] in {'\"', "'"} and value[-1]==value[0]:
            value=value[1:-1]
            continue
        match=re.match(r'^(?:--?)?[A-Za-z_][A-Za-z0-9_.-]*=',value)
        if match is None:break
        value=value[match.end():]
    if re.match(r'^[A-Za-z]:[\\/]',value):return True
    uri=re.match(r'^([A-Za-z][A-Za-z0-9+.-]*):/{2}',value)
    if uri:return uri[1].casefold()=='file'
    return value.startswith(('\\\\','//','/Users/','/home/','/tmp/'))


def export_service(service):
    """Shareable command data only. No local paths, credential refs or grades."""
    if not is_custom(service):raise ValueError('CLI_TEMPLATE_CUSTOM_SERVICE_REQUIRED')
    template=validate_template(service['template'])
    # Fixed argument paths can also contain personal names. Require re-entry.
    local_slots=[]
    for field in ('argv','interpreter_argv'):
        for i,arg in enumerate(template[field]):
            if _portable_local_argument(arg):
                template[field][i]='';local_slots.append(field+'.'+str(i))
    return dict(schema_version=PORTABLE,display_name=service['display_name'],template=template,
        models=[{k:row[k] for k in ('display_name','model_name','thinking_mode')} for row in service['models']],
        local_configuration_required=['executable','interpreter','environment','concurrency_group',*local_slots])

def import_service(value):
    _object(value,{'schema_version','display_name','template','models','local_configuration_required'},set(),'import')
    if value['schema_version']!=PORTABLE:raise ValueError('CLI_TEMPLATE_IMPORT_SCHEMA_UNSUPPORTED')
    _string(value['display_name'],'display_name',limit=80)
    if not isinstance(value['local_configuration_required'],list) or any(not isinstance(v,str) for v in value['local_configuration_required']):raise ValueError('CLI_TEMPLATE_IMPORT_LOCAL_FIELDS_INVALID')
    service=new_service();service.update(display_name=value['display_name'],template=validate_template(value['template']))
    if not isinstance(value['models'],list) or len(value['models'])>32:raise ValueError('CLI_MODELS_INVALID')
    for row in value['models']:
        _object(row,{'display_name','model_name','thinking_mode'},set(),'import.models')
        for field in ('display_name','model_name','thinking_mode'):_string(row[field],field,empty=field=='thinking_mode',limit=128)
        service['models'].append(dict(row,profile_ref='cli:'+service['config_id']+':'+uuid.uuid4().hex[:12],connection_status='UNVERIFIED'))
    return service

def configuration_identity(service,model=None):
    """Stable across labels and enable toggles; exact across execution changes."""
    fields=('config_id','adapter_id','executable','interpreter','environment','concurrency_group','template')
    data={k:service.get(k) for k in fields}
    if model is not None:data['model']={k:model.get(k) for k in ('profile_ref','model_name','thinking_mode')}
    identities={}
    for key in ('executable','interpreter'):
        raw=service.get(key)
        if raw:
            from .windows_cli import resolve_template_entry
            try:
                path=resolve_template_entry(raw)
                identities[key]=dict(path=path,sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()) if path else None
            except OSError:identities[key]=None
    data['executables']=identities
    try:
        from .windows_cli import resolve_template_entry,template_launch_files
        executable=resolve_template_entry(service.get('executable',''))
        if executable:
            files=template_launch_files(executable,service.get('template',{}).get('argv',[]),interpreter=service.get('interpreter',''),interpreter_arguments=service.get('template',{}).get('interpreter_argv',[]))
            data['launch_files']=[dict(path=p,sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest().upper()) for p in files]
    except (OSError,ValueError):data['launch_files']=None
    return digest(data)

def exam_eligible(target,capability='structured_output'):
    if not is_custom(target) or target.get('connection_status')!='AVAILABLE' or target.get('enabled') is not True:return False
    try:template=validate_template(target['template'])
    except (ValueError,KeyError):return False
    binding=target.get('verification') or {}
    current=configuration_identity(target,target)
    return bool(template['capabilities'].get(capability) and any('{{model}}' in a for a in template['argv']) and all(template['permissions'].values())
                and target.get('configuration_sha256',current)==current and binding.get('configuration_sha256')==current
                and (capability=='images' or capability in binding.get('capabilities',[])))

def capability_projection(service,model):
    if not is_custom(service):return None
    binding=model.get('verification') or {}
    valid=binding.get('configuration_sha256')==configuration_identity(service,model)
    return {key:('UNSUPPORTED' if not declared else 'INVALIDATED' if binding and not valid else 'VERIFIED' if valid and key in binding.get('capabilities',[]) else 'DECLARED_UNTESTED')
            for key,declared in service['template']['capabilities'].items()}

def receipt_model_bound(receipt,requested):
    if not isinstance(receipt,Mapping) or receipt.get('route')!='DECLARATIVE_COMMAND_TEMPLATE' or receipt.get('profile_kind')!='CLI':return False
    if receipt.get('status')!='PASS' or receipt.get('requested_model')!=requested or receipt.get('request_model_binding_status')!='DECLARED_ARGV_AND_BINARY':return False
    if not all(re.fullmatch(r'[A-F0-9]{64}',str(receipt.get(k,''))) for k in ('configuration_sha256','template_sha256','request_model_binding_sha256','cli_binary_sha256')):return False
    return receipt.get('returned_model') in (None,receipt.get('requested_model_mapping',requested))

def accumulate_usage(state,usage):
    for field in ('prompt_tokens','completion_tokens'):
        value=usage.get(field) if isinstance(usage,Mapping) else None
        current=state['token_usage'].get(field)
        state['token_usage'][field]=current+value if type(current) is int and type(value) is int else None

def parse_output(template,stdout,*,result_file=None):
    """Parse complete bytes only after owned process completion; stderr is excluded."""
    template=validate_template(template)
    out=template['output'];termination=template['termination'];metadata=template.get('metadata',{})
    text='';seen_ids={};seen_events=set();terminal=False;returned=None;usage={};final_text=False
    try:decoded=stdout.decode('utf8',errors='strict')
    except UnicodeDecodeError:raise ValueError('CLI_TEMPLATE_UTF8_INVALID') from None
    if not decoded.strip() and out['mode']!='file':raise ValueError('CLI_RESPONSE_EMPTY')
    rows=[]
    if out['mode']=='json':
        try:rows=[json.loads(decoded)]
        except ValueError:raise ValueError('CLI_TEMPLATE_JSON_INVALID') from None
    elif out['mode'] in {'jsonl','file'} and (out['mode']=='jsonl' or termination['success'] or termination['failure'] or metadata):
        for line in decoded.splitlines():
            if not line.strip():continue
            try:rows.append(json.loads(line))
            except ValueError:
                if not out.get('ignore_non_json_lines',False):raise ValueError('CLI_TEMPLATE_JSONL_INVALID') from None
    for row in rows:
        if any(matches(row,c) for c in termination['failure']):raise ValueError('CLI_TEMPLATE_ERROR_EVENT')
        if any(matches(row,c) for c in termination['success']):terminal=True
        identity=select(row,out['dedupe_path']) if out.get('dedupe_path') else None
        stamp=digest(row)
        if identity is not None:
            key=digest(identity)
            if key in seen_ids:
                if seen_ids[key]!=stamp:raise ValueError('CLI_TEMPLATE_EVENT_ID_CONFLICT')
                continue
            seen_ids[key]=stamp
        seen_events.add(stamp)
        if metadata.get('model_path'):
            actual=select(row,metadata['model_path'])
            if actual is not None:
                if not isinstance(actual,str) or not actual or returned is not None and returned!=actual:raise ValueError('CLI_TEMPLATE_MODEL_CONFLICT')
                returned=actual
        for field,path in metadata.get('usage_paths',{}).items():
            number=select(row,path)
            if number is not None:
                if type(number) is not int or number<0:raise ValueError('CLI_TEMPLATE_USAGE_INVALID')
                usage[field]=number
        if out['mode']=='jsonl':
            rules=[rule for rule in out['events'] if matches(row,rule['match'])]
            if len(rules)>1:raise ValueError('CLI_TEMPLATE_EVENT_RULE_CONFLICT')
            for rule in rules:
                value=select(row,rule['path'])
                if not isinstance(value,str):raise ValueError('CLI_TEMPLATE_TEXT_FIELD_MISSING')
                if rule['operation']=='append' and final_text:raise ValueError('CLI_TEMPLATE_DELTA_AFTER_FINAL')
                if rule['operation']=='replace':final_text=True
                text=value if rule['operation']=='replace' else text+value
    if termination['success'] and not terminal:raise ValueError('CLI_TEMPLATE_TERMINAL_EVENT_MISSING')
    if out['mode']=='text':text=decoded
    elif out['mode']=='json':text=select(rows[0],out.get('path',''))
    elif out['mode']=='file':
        if result_file is None or not result_file.is_file():raise ValueError('CLI_TEMPLATE_RESULT_FILE_MISSING')
        if result_file.is_symlink() or result_file.stat().st_nlink!=1:raise ValueError('CLI_TEMPLATE_RESULT_FILE_NOT_OWNED')
        if result_file.stat().st_size>template['max_output_bytes']:raise ValueError('CLI_OUTPUT_LIMIT_EXCEEDED')
        try:text=result_file.read_text(encoding='utf8',errors='strict')
        except UnicodeError:raise ValueError('CLI_TEMPLATE_UTF8_INVALID') from None
        if out['file_format']=='json':
            try:text=select(json.loads(text),out.get('path',''))
            except ValueError:raise ValueError('CLI_TEMPLATE_JSON_INVALID') from None
    if isinstance(text,Mapping):text=json.dumps(text,ensure_ascii=False,allow_nan=False)
    if not isinstance(text,str) or not text.strip():raise ValueError('CLI_RESPONSE_EMPTY')
    return dict(text=text,returned_model=returned,token_usage=usage or None,terminal_observed=terminal,event_count=len(seen_events))


def receipt_evidence_complete(receipt):
    if not isinstance(receipt,Mapping) or not receipt.get('behavior_sha256'):return False
    return bool(receipt.get('token_usage')) or receipt_model_bound(receipt,receipt.get('requested_model'))
