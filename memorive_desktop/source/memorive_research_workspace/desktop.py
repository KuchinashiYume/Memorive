"""Trusted desktop binding; external agents receive the read/propose surface."""
from .service import ResearchWorkspace

ANSWER_SCHEMA={'type':'object','properties':{'answer':{'type':'string'},'citations':{'type':'array','items':{'type':'string'}},'needs_agent':{'type':'boolean'}},'required':['answer','citations','needs_agent'],'additionalProperties':False}

ANSWER_SCHEMA['properties']['knowledge_draft']={'anyOf':[{'type':'null'},{'type':'object','properties':{
    'title':{'type':'string'},'claim':{'type':'string'},'scope':{'type':'string'},'limitations':{'type':'string'},
    'evidence_ids':{'type':'array','items':{'type':'string'}}},'required':['title','claim','scope','limitations','evidence_ids'],'additionalProperties':False}]}
ANSWER_SCHEMA['properties']['research_notes']={'type':'array','maxItems':8,'items':{'type':'object','properties':{
    'kind':{'type':'string','enum':['question','hypothesis','judgment','open_question','next_step']},
    'quote':{'type':'string'},'evidence_ids':{'type':'array','items':{'type':'string'}}},
    'required':['kind','quote','evidence_ids'],'additionalProperties':False}}

def create(*, service, library, local_models, profile_root, workspace_root, desktop_call, stage_inbox_paths):
    """The desktop's research workspace, wired to the parts of the desktop it uses:

    service            the settings service client: settings, model profiles, model calls
    library            the library controller (memorive_library): processed papers added to a project
    local_models       the local model registry (memorive_local_models), or None
    profile_root       the user profile folder, recorded for the window-less process (headless_models.py)
    workspace_root     the folder of the workspace's own store
    desktop_call       ProductApi.call: the inbox and current task methods a connected app's files go through
    stage_inbox_paths  ProductApi.stage_inbox_paths: copies a connected app's file for the inbox
    """
    from memorive_research_runtime.report_models import profiles_offered
    def interest_get():
        return service.call('settings.research_get',{})['config']['use_recent_interests']
    def interest_set(enabled):
        state=service.call('settings.research_get',{})
        service.call('settings.research_save',{'config':dict(state['config'],use_recent_interests=enabled),'expected_revision':state['revision']})
    def catalog():
        return [r for r in profiles_offered(service,local_models) if r['profile_kind'] in {'API','LOCAL','CLI'}]
    def execute(*,profile_ref,prompt,job_id,response_schema=ANSWER_SCHEMA):
        snap=service.call('settings.research_chat_freeze',{'run_id':job_id,'profile_ref':profile_ref})
        # Kept on the job, so that a result that arrives just before a crash can still be saved after it.
        workspace.note_model_snapshot(job_id,snap)
        # Route and exact model are frozen server-side; the browser never supplies credentials.
        result=service.call('settings.research_chat_execute',{'snapshot_id':snap['snapshot_id'],'prompt':prompt,'response_schema':response_schema})
        from .model_receipts import verified_result
        return verified_result(result,snap)
    def model(**kwargs):return execute(**kwargs)
    def vision(*,profile_ref,image_bytes,mime_type,job_id):
        import base64
        snap=service.call('settings.research_chat_freeze',{'run_id':job_id,'profile_ref':profile_ref})
        return service.call('settings.research_image_execute',{'snapshot_id':snap['snapshot_id'],
            'image_base64':base64.b64encode(image_bytes).decode('ascii'),'mime_type':mime_type})
    workspace=ResearchWorkspace(workspace_root,model=model,catalog=catalog,recover=True,
        interest_get=interest_get,interest_set=interest_set,vision=vision,
        user_preferences_get=lambda:service.call('settings.user_get',{}),
        language_settings_get=lambda:service.call('settings.get_state',{}))
    def embedding_catalog():
        return [r for r in profiles_offered(service,local_models,'EMBEDDING') if r['profile_kind'] in {'API','LOCAL'}]
    def embedding(*,profile_ref,inputs,job_id):
        snap=service.call('settings.research_embedding_freeze',{'run_id':job_id,'profile_ref':profile_ref})
        return service.call('settings.research_embedding_execute',{'snapshot_id':snap['snapshot_id'],'inputs':inputs})
    from retrieval_weighting.research_rule import bind as bind_rules
    bind_rules(workspace,service)
    workspace.agent_model=execute
    workspace.index.rank_model=execute
    workspace.index.embed_model=embedding
    workspace.embedding_catalog=embedding_catalog
    workspace.index.profile_identity=lambda ref: next((r['binding_hash'] for r in catalog()+embedding_catalog() if r['profile_ref']==ref and r['eligible']),None)
    import json
    (workspace.store.root/'desktop_binding.json').write_text(json.dumps({'profile_root':str(profile_root)},ensure_ascii=False),encoding='utf-8')
    from .library_bridge import bind
    bind(workspace,library)
    from .desktop_connections import bind as bind_connections
    bind_connections(workspace,library=library,service=service,desktop_call=desktop_call,stage_inbox_paths=stage_inbox_paths)
    # Answers whose model call finished before the app was closed are saved now, from the kept result.
    workspace.finish_answers_received_before_restart(
        lambda snap:service.call('settings.research_chat_result',{'snapshot_id':snap['snapshot_id']}))
    return workspace
