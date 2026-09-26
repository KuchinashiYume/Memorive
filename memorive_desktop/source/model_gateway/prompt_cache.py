"""Stable prompt layout; no response cache, TTL guarantee, or provider wire flags."""
from __future__ import annotations
import copy
import json
from pathlib import Path

LAYOUT_REVISION = 'MEMORIVE_MAINLINE_PREFIX_V2'

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def source_first(template: str, marker: str, source: str) -> str:
    if template.count(marker) != 1 or not source.strip():
        raise ValueError('CACHE_LAYOUT_SOURCE_MARKER_INVALID')
    policy=(Path(__file__).parent/'prompts/prompt_cache/shared_prefix_v2.md').read_text(encoding='utf-8').strip()
    return (policy+'\n\n<SOURCE_CONTEXT>\n'+source+'\n</SOURCE_CONTEXT>\n\n<TASK>\n'
        +template.replace(marker,'[Use the exact SOURCE_CONTEXT above.]')+'\n</TASK>')

def policy_first(template: str, marker: str, policy: str) -> str:
    if template.count(marker) != 1 or not policy.strip():
        raise ValueError('CACHE_LAYOUT_POLICY_MARKER_INVALID')
    return policy+'\n\n'+template.replace(marker,'[Apply the complete policy above.]')

def split_repair_sources(bundle: dict) -> tuple[str, dict]:
    """Deduplicate only already-authorized candidate sources; never widen scope."""
    dynamic=copy.deepcopy(bundle); sources={}
    for target in dynamic['targets']:
        ids=[]
        for row in target.pop('candidate_sources'):
            cid,text=row['chunk_id'],row['text']
            if cid in sources and sources[cid]!=text:
                raise ValueError('CACHE_LAYOUT_SOURCE_CONFLICT')
            sources[cid]=text;ids.append(cid)
        target['candidate_source_ids']=ids
    return canonical([{'chunk_id':cid,'text':sources[cid]} for cid in sorted(sources)]),dynamic

def cache_usage(value: dict) -> dict:
    """Provider evidence only. Missing metrics stay unknown; zeros remain zeros."""
    def count(v):
        return v if isinstance(v,int) and not isinstance(v,bool) and v>=0 else None
    readings={}
    for key in ('cached_input_tokens','prompt_cache_hit_tokens','cache_read_input_tokens','cachedContentTokenCount'):
        v=count(value.get(key))
        if v is not None: readings[key]=v
    for key in ('prompt_tokens_details','input_tokens_details'):
        nested=value.get(key)
        if isinstance(nested,dict):
            v=count(nested.get('cached_tokens'))
            if v is not None: readings[key+'.cached_tokens']=v
    hits=set(readings.values())
    read=next(iter(hits)) if len(hits)==1 else None
    writes=count(value.get('cache_write_input_tokens'))
    if writes is None: writes=count(value.get('cache_creation_input_tokens'))
    total=count(value.get('prompt_tokens'))
    if total is None: total=count(value.get('input_tokens'))
    inconsistent=len(hits)>1 or (read is not None and total is not None and read>total
        and 'cache_read_input_tokens' not in value)
    return {'read_tokens':None if inconsistent else read,'write_tokens':writes,
        'miss_tokens':count(value.get('prompt_cache_miss_tokens')),
        'status':'INCONSISTENT' if inconsistent else ('HIT' if read and read>0 else 'MISS' if read==0 else 'UNKNOWN'),
        'evidence_fields':readings,'retention_status':'PROVIDER_MANAGED_UNKNOWN',
        'response_reused':False}
