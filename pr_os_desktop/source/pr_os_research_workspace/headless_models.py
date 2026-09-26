"""Bind the same saved profiles to the integrated stdio process without opening a window."""
import json
from pathlib import Path
from types import SimpleNamespace

def bind(workspace):
    path=workspace.store.root/'desktop_binding.json'
    if not path.exists():return
    binding=json.loads(path.read_text(encoding='utf-8'));profile=Path(binding['profile_root']).resolve(strict=True)
    from pr_os_folder_management.policy import ProfileFolderManager
    folders=ProfileFolderManager(profile)
    if (folders.path_for('WORKSPACE')/'research_workspace').resolve()!=workspace.store.root:raise ValueError('MEMO_PROFILE_BINDING_MISMATCH')
    if not folders.path_for('SETTINGS').is_dir():raise ValueError('MEMO_PROFILE_NOT_INITIALIZED')
    from pr_os_desktop_service.service_main import build_settings_controller,LocalModelProductController
    from pr_os_settings.service_adapter import SettingsServiceAdapter
    from pr_os_research_runtime.report_models import catalog
    controller=build_settings_controller(profile);service=SettingsServiceAdapter(controller)
    api=SimpleNamespace(_service=service,_local_models=LocalModelProductController(profile.parent/'local_models'))
    runtime=SimpleNamespace(api=api)
    def execute(*,profile_ref,prompt,job_id,response_schema):
        snap=service.call('settings.research_chat_freeze',{'run_id':job_id,'profile_ref':profile_ref})
        result=service.call('settings.research_chat_execute',{'snapshot_id':snap['snapshot_id'],'prompt':prompt,'response_schema':response_schema})
        from .model_receipts import verified_result
        return verified_result(result,snap)
    def embed(*,profile_ref,inputs,job_id):
        snap=service.call('settings.research_embedding_freeze',{'run_id':job_id,'profile_ref':profile_ref})
        return service.call('settings.research_embedding_execute',{'snapshot_id':snap['snapshot_id'],'inputs':inputs})
    workspace.catalog=lambda:catalog(runtime)
    workspace.embedding_catalog=lambda:catalog(runtime,'EMBEDDING')
    from m7_weighting.research_rule import bind as bind_rules
    bind_rules(workspace,service)
    workspace.index.rank_model=execute;workspace.index.embed_model=embed
    workspace.index.profile_identity=lambda ref:next((r['binding_hash'] for r in workspace.catalog()+workspace.embedding_catalog() if r['profile_ref']==ref and r['eligible']),None)
