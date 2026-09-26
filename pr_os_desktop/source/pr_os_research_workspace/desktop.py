"""Trusted desktop binding; external agents receive the read/propose surface."""
from .service import ResearchWorkspace

ANSWER_SCHEMA={'type':'object','properties':{'answer':{'type':'string'},'citations':{'type':'array','items':{'type':'string'}},'needs_agent':{'type':'boolean'}},'required':['answer','citations','needs_agent'],'additionalProperties':False}

ANSWER_SCHEMA['properties']['knowledge_draft']={'anyOf':[{'type':'null'},{'type':'object','properties':{
    'title':{'type':'string'},'claim':{'type':'string'},'scope':{'type':'string'},'limitations':{'type':'string'},
    'evidence_ids':{'type':'array','items':{'type':'string'}}},'required':['title','claim','scope','limitations','evidence_ids'],'additionalProperties':False}]}

def create(api):
    def interest_get():
        return api._service.call('settings.research_get',{})['config']['use_recent_interests']
    def interest_set(enabled):
        state=api._service.call('settings.research_get',{})
        api._service.call('settings.research_save',{'config':dict(state['config'],use_recent_interests=enabled),'expected_revision':state['revision']})
    def catalog():
        from pr_os_research_runtime.report_models import catalog as report_catalog
        return [r for r in report_catalog(api._research) if r['profile_kind'] in {'API','LOCAL','CLI'}]
    def execute(*,profile_ref,prompt,job_id,response_schema=ANSWER_SCHEMA):
        snap=api._service.call('settings.research_chat_freeze',{'run_id':job_id,'profile_ref':profile_ref})
        # Route and exact model are frozen server-side; the browser never supplies credentials.
        result=api._service.call('settings.research_chat_execute',{'snapshot_id':snap['snapshot_id'],'prompt':prompt,'response_schema':response_schema})
        from .model_receipts import verified_result
        return verified_result(result,snap)
    def model(**kwargs):return execute(**kwargs)
    def vision(*,profile_ref,image_bytes,mime_type,job_id):
        import base64
        snap=api._service.call('settings.research_chat_freeze',{'run_id':job_id,'profile_ref':profile_ref})
        return api._service.call('settings.research_image_execute',{'snapshot_id':snap['snapshot_id'],
            'image_base64':base64.b64encode(image_bytes).decode('ascii'),'mime_type':mime_type})
    workspace=ResearchWorkspace(api._profile_path('WORKSPACE')/'research_workspace',model=model,catalog=catalog,recover=True,
        interest_get=interest_get,interest_set=interest_set,vision=vision)
    def embedding_catalog():
        from pr_os_research_runtime.report_models import catalog as report_catalog
        return [r for r in report_catalog(api._research,'EMBEDDING') if r['profile_kind'] in {'API','LOCAL'}]
    def embedding(*,profile_ref,inputs,job_id):
        snap=api._service.call('settings.research_embedding_freeze',{'run_id':job_id,'profile_ref':profile_ref})
        return api._service.call('settings.research_embedding_execute',{'snapshot_id':snap['snapshot_id'],'inputs':inputs})
    from m7_weighting.research_rule import bind as bind_rules
    bind_rules(workspace,api._service)
    workspace.agent_model=execute
    workspace.index.rank_model=execute
    workspace.index.embed_model=embedding
    workspace.embedding_catalog=embedding_catalog
    workspace.index.profile_identity=lambda ref: next((r['binding_hash'] for r in catalog()+embedding_catalog() if r['profile_ref']==ref and r['eligible']),None)
    import json
    (workspace.store.root/'desktop_binding.json').write_text(json.dumps({'profile_root':str(api._profile_root)},ensure_ascii=False),encoding='utf-8')
    from .library_bridge import bind
    bind(workspace,api._library)
    from .p08_t00_cross_desktop_connections import bind as bind_connections
    bind_connections(workspace,api)
    return workspace
