"""Bridge Memo's user-question producer to the existing five-channel ranker."""
from memorive.research.ranking.research_interests import snapshot,signal

def frozen(runtime,config):
    if not config.get('use_recent_interests'):
        return {'enabled':False,'events':[]}
    workspace=runtime.host.research_workspace()
    if workspace is None:
        path=runtime.host.profile_path('WORKSPACE')/'research_workspace'
        if not (path/'research_workspace_v1.sqlite3').exists():
            return {'enabled':True,'events':[]}
        from memorive.research.workspace.store import Store
        store=Store(path)
    else:
        store=workspace.store
    from memorive.research.ranking.research_policy import ResearchPolicy
    policy=ResearchPolicy(store).settings()['config']
    return snapshot(store,enabled=True,window_days=policy['interest_days'],half_life=policy['interest_half_life'])

def ranking(snapshot,title,abstract):
    return {'snapshot_hash':snapshot.get('snapshot_hash'),'score':signal(snapshot,title+' '+(abstract or ''),channel='CORE'),
        'eligible_channels':['CORE','ADJACENT'],'event_count':len(snapshot.get('events',[]))}
