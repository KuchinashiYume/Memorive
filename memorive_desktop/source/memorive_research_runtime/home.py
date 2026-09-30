"""Home summaries only: indexed metadata, cached library rows, no chat/PDF bodies."""
import sqlite3

METHODS=frozenset({'home.summary','home.resolve'})

def summaries(api):
    result={'thread':None,'material':None,'inbox_count':0,'directions':[],'literature_available':False}
    workspace=api._profile_path('WORKSPACE')/'research_workspace/research_workspace_v1.sqlite3'
    if workspace.is_file():
        with sqlite3.connect(workspace.as_uri()+'?mode=ro',uri=True) as db:
            row=db.execute("SELECT id,project,json_extract(body,'$.title'),updated FROM objects WHERE kind='thread' AND coalesce(json_extract(body,'$.temporary'),0)=0 AND coalesce(json_extract(body,'$.archived'),0)=0 ORDER BY updated DESC,id LIMIT 1").fetchone()
            if row:result['thread']={'id':row[0],'project':row[1],'title':row[2],'updated_at':row[3]}
    # The cached projection is already owned by the existing library. Do not refresh its index here.
    rows=getattr(api._library,'_rows_by_id',{})
    candidates=[r for r in rows.values() if r.get('file_exists') is not False and r.get('artifact_id')]
    if candidates:
        row=max(candidates,key=lambda r:str(r.get('updated_at','')))
        result['material']={k:row.get(k) for k in ('artifact_id','display_name','updated_at','mode','research_run_id')}
    inbox=api._profile_path('INBOX')/'inbox_store.sqlite3'
    if inbox.is_file():
        with sqlite3.connect(inbox.as_uri()+'?mode=ro',uri=True) as db:
            result['inbox_count']=db.execute("SELECT count(*) FROM items WHERE state IN ('QUEUED','ERROR','IMPORTING')").fetchone()[0]
    preferences=api._service.call('settings.research_get',{})
    config=preferences['config']
    result['directions']=[{'id':d['id'],'name':d['name']} for d in config.get('directions',[])]
    if not result['directions'] and config.get('topic','').strip():result['directions']=[{'id':None,'name':config['topic']}]
    by_id={d['id']:d for d in result['directions']}
    result['latest_direction']=next((by_id[key] for key in preferences.get('direction_recency',[]) if key in by_id),result['directions'][-1] if result['directions'] else None)
    result['literature_available']=bool(result['directions'] and api._service.call('settings.external_sources_get',{})['literature']['selected'])
    return result

def call(api,method,params):
    if method=='home.summary':
        if params:raise ValueError('HOME_PARAMS_INVALID')
        return summaries(api)
    if set(params)!={'kind','target'}:raise ValueError('HOME_PARAMS_INVALID')
    kind=params['kind'];target=params['target']
    if kind=='thread':
        path=api._profile_path('WORKSPACE')/'research_workspace/research_workspace_v1.sqlite3'
        row=None
        if path.is_file():
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
                row=db.execute("SELECT id,project FROM objects WHERE kind='thread' AND id=? AND coalesce(json_extract(body,'$.temporary'),0)=0 AND coalesce(json_extract(body,'$.archived'),0)=0",(target,)).fetchone()
        return {'available':bool(row),'route':'chat','thread_id':row[0] if row else None}
    if kind=='material':
        try:
            row=api._library._get(target)
            if not row.get('file_exists'):raise ValueError('UNAVAILABLE')
            return {'available':True,'route':'library',**{k:row.get(k) for k in ('artifact_id','mode','research_run_id')}}
        except (ValueError,KeyError):return {'available':False,'route':'library'}
    if kind=='inbox':return {'available':summaries(api)['inbox_count']>0,'route':'inbox'}
    raise ValueError('HOME_PARAMS_INVALID')
