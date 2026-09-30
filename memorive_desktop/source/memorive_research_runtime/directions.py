"""Stable user-confirmed direction identities, independent of delivery channels."""
from .common import sha,CHANNELS

def effective_directions(config):
    if config.get('directions'):return config['directions']
    # Read-only legacy adapter: no settings rewrite, no channel as a direction ID.
    rows={}
    for channel in CHANNELS:
        query=config['queries'][channel].strip() or (config['topic'].strip() if channel=='CORE' else '')
        if not query:continue
        key='direction-'+sha({'legacy_explicit_query':query})[:24]
        row=rows.setdefault(key,{'id':key,'revision':1,'name':query,'query':query,'channels':[],
            'keywords':[],'seed_ids':[],'origin':'LEGACY_EXPLICIT_QUERY_READ_ONLY'})
        row['channels'].append(channel)
    return list(rows.values())

def channel_targets(config,directions):
    active={c for d in directions for c in d['channels'] if config['weights'][c]>0}
    if not active:raise ValueError('RESEARCH_QUERY_REQUIRED')
    total=sum(config['weights'][c] for c in active)
    raw={c:config['count']*config['weights'][c]/total for c in active}
    targets={c:int(raw.get(c,0)) for c in CHANNELS}
    ordered=sorted(active,key=lambda c:(-(raw[c]-targets[c]),CHANNELS.index(c)))
    for c in ordered[:config['count']-sum(targets.values())]:targets[c]+=1
    return targets
