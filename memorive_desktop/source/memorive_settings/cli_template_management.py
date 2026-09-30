"""Non-executing template management and reference protection."""
from copy import deepcopy
import json
import sqlite3
from pathlib import Path
from .cli_templates import import_service,export_service,new_service,is_custom,configuration_identity,capability_projection

def projection(settings):
    settings=deepcopy(settings)
    for service in settings['cli_services']:
        if not is_custom(service):continue
        for model in service['models']:
            binding=model.get('verification')
            if model['connection_status']=='AVAILABLE' and (not binding or binding.get('configuration_sha256')!=configuration_identity(service,model)):
                model['connection_status']='INVALID'
        service['connection_status']='AVAILABLE' if any(m['connection_status']=='AVAILABLE' for m in service['models']) else 'INVALID' if any(m['connection_status']=='INVALID' for m in service['models']) else 'UNVERIFIED'
    return settings

def contains(value,refs):
    if isinstance(value,dict):return any(contains(v,refs) for v in value.values())
    if isinstance(value,list):return any(contains(v,refs) for v in value)
    return isinstance(value,str) and value in refs

def protect_references(controller,before,after):
    old={m['profile_ref'] for s in before['cli_services'] for m in s['models']}
    new={m['profile_ref'] for s in after['cli_services'] for m in s['models']}
    removed=old-new
    if not removed:return
    if contains({k:v for k,v in after.items() if k!='cli_services'},removed):raise ValueError('CLI_PROFILE_STILL_REFERENCED:settings')
    workspace=Path(before['directories']['workspace_root'])
    report=workspace/'research/report_workflow.json'
    if report.is_file():
        value=json.loads(report.read_text(encoding='utf8'))
        if contains(value,removed):raise ValueError('CLI_PROFILE_STILL_REFERENCED:report_workflow')
    database=workspace/'research_workspace/research_workspace_v1.sqlite3'
    if database.is_file():
        with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5) as db:
            for kind,body in db.execute("SELECT kind,body FROM objects WHERE kind IN ('settings','thread','job')"):
                value=json.loads(body)
                refs=value if kind=='settings' else {'profile_ref':value.get('profile_ref')} if kind=='thread' and not value.get('archived') else value if kind=='job' and value.get('status') in {'QUEUED','RUNNING','CANCELLING'} else {}
                if contains(refs,removed):raise ValueError('CLI_PROFILE_STILL_REFERENCED:research_'+kind)
    root=controller.store.profile_root
    # Only configuration and immutable execution bindings, never user documents.
    for path in root.glob('*.json'):
        if path.name.startswith(('research','user_preferences')):
            try:value=json.loads(path.read_text(encoding='utf8'))
            except (OSError,ValueError):continue
            if contains(value,removed):raise ValueError('CLI_PROFILE_STILL_REFERENCED:'+path.name)
    for name in ('report_profiles','research_chat_profiles','research_profiles','research_image_profiles'):
        folder=root/name
        for path in folder.glob('*.json'):
            if '.' in path.stem or (folder/(path.stem+'.result.json')).exists():continue
            try:value=json.loads(path.read_text(encoding='utf8'))
            except (OSError,ValueError):continue
            if contains(value,removed):raise ValueError('CLI_PROFILE_QUEUED_OR_ACTIVE:'+name)
    for name in ('workflow_exam_jobs','cli_verification_jobs'):
        for path in (root/name).glob('*.json'):
            try:value=json.loads(path.read_text(encoding='utf8'))
            except (OSError,ValueError):continue
            if value.get('status') in {'QUEUED','RUNNING','CANCELLING'} and contains(value,removed):raise ValueError('CLI_PROFILE_QUEUED_OR_ACTIVE:'+name)

def prepare(controller,*,action,payload=None,config_id=None):
    """Return a draft. Save and inference are separate explicit calls."""
    settings=controller.store.load(recover_corruption=False)['settings']
    if action=='new':service=new_service()
    elif action=='import':service=import_service(payload)
    elif action in {'copy','export'}:
        source=deepcopy(payload) if isinstance(payload,dict) else next((s for s in settings['cli_services'] if s['config_id']==config_id and is_custom(s)),None)
        if source is not None:
            from .contracts import validate_settings
            checked=validate_settings({**settings,'cli_services':[s for s in settings['cli_services'] if s['config_id']!=source.get('config_id')]+[source]})
            source=next(s for s in checked['cli_services'] if s['config_id']==source['config_id'])
        if source is None:raise ValueError('CLI_TEMPLATE_CUSTOM_SERVICE_REQUIRED')
        portable=export_service(source)
        if action=='export':return dict(payload=portable,persistent_mutation=False,external_model_calls=0)
        service=import_service(portable);service['display_name']=service['display_name'][:76]+' 副本'
        for key in ('template','executable','interpreter','environment','concurrency_group'):service[key]=deepcopy(source[key])
    else:raise ValueError('CLI_TEMPLATE_ACTION_INVALID')
    return dict(service=service,preview=dict(name=service['display_name'],model_count=len(service['models']),input=service['template']['input'],output=service['template']['output']['mode']),persistent_mutation=False,external_model_calls=0)
